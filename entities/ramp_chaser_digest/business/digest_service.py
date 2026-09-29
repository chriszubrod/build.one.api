# Python Standard Library Imports
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ENTITY_TYPE = "RampChaserDigest"
_OUTBOX_KIND_SEND_MAIL = "send_mail"


class RampChaserDigestService:
    """
    Weekly per-cardholder Ramp memo/receipt chaser — draft-only via MS outbox (U-549 §6).

    One digest per (cardholder, week_of Tuesday in business_timezone). Never patches
    open drafts; send detection uses conversation_id + SentItems/DeletedItems only.
    """

    def run_for_week(self, week_of: Optional[str] = None) -> dict:
        try:
            return self._run_for_week(week_of)
        except Exception as error:
            logger.exception("ramp_chaser_digest.sweep_failed: %s", error)
            from config import Settings

            settings = Settings()
            mode = self._normalized_mode(settings)
            resolved_week = week_of
            if not resolved_week:
                try:
                    resolved_week = self._default_week_of(settings)
                except Exception:
                    resolved_week = None
            return self._empty_summary(
                status="error", mode=mode, week_of=resolved_week, failed=1
            )

    def _run_for_week(self, week_of: Optional[str]) -> dict:
        from config import Settings

        settings = Settings()
        mode = self._normalized_mode(settings)
        tz = self._business_tz(settings)
        now = datetime.now(tz)

        if mode != "draft":
            logger.info("ramp_chaser_digest.disabled mode=%s", mode)
            return self._empty_summary(status="disabled", mode=mode, week_of=week_of)

        if week_of:
            week_of = self.canonicalize_week_of(str(week_of).strip())
        else:
            week_of = self._default_week_of(settings, now=now)

        from entities.ramp_chaser_digest.persistence.repo import RampChaserDigestRepository
        from entities.ramp_transaction_follow_up.persistence.repo import (
            RampTransactionFollowUpRepository,
        )
        from integrations.ms.outbox.persistence.repo import MsOutboxRepository
        from integrations.ramp.auth.business.service import RampAuthService
        from integrations.ramp.base.client import RampHttpClient
        from integrations.ramp.user.business.service import RampUserService
        from integrations.ramp.user.external.client import RampUserExternalClient

        digest_repo = RampChaserDigestRepository()
        follow_up_repo = RampTransactionFollowUpRepository()
        outbox_repo = MsOutboxRepository()
        # RampUserExternalClient REQUIRES an http client — it has no default. The
        # roster is the only Ramp call this sweep makes, so the client is built
        # here, used once, and closed immediately rather than held for the whole
        # sweep. Mirrors RampTransactionService, which also closes in a finally.
        _ramp_base = (settings.ramp_api_base_url or "https://api.ramp.com").rstrip("/")
        _ramp_http = RampHttpClient(
            api_base=_ramp_base,
            auth_service=RampAuthService(settings),
        )

        self._reconcile_uncaptured_drafts(
            digest_repo=digest_repo,
            outbox_repo=outbox_repo,
        )

        sent_observed = 0
        discarded_unsent = 0
        unsent_carryover = 0
        observe = self._observe_outstanding_sends(
            digest_repo=digest_repo,
            current_week_of=week_of,
        )
        sent_observed = observe["sent_observed"]
        discarded_unsent = observe["discarded_unsent"]
        unsent_carryover = observe["unsent_carryover"]

        unresolved = follow_up_repo.read_unresolved()
        actionable = [
            row
            for row in unresolved
            if self._item_needs_chase(row)
        ]
        groups = self._group_by_card_holder(actionable)

        try:
            roster = RampUserService(RampUserExternalClient(_ramp_http)).build_roster()
        finally:
            _ramp_http.close()

        drafted = 0
        already_drafted = 0
        skipped_inactive = 0
        unroutable = 0
        refused = 0
        outbox_dead_letter = 0
        failed = 0
        recipient_changed = 0
        recipient_unverified = 0

        for card_holder_id, items in groups.items():
            try:
                outcome, holder_advisory = self._process_cardholder(
                    card_holder_id=card_holder_id,
                    items=items,
                    week_of=week_of,
                    now=now,
                    tz=tz,
                    settings=settings,
                    roster=roster,
                    digest_repo=digest_repo,
                    outbox_repo=outbox_repo,
                )
                if outcome == "drafted":
                    drafted += 1
                elif outcome == "already_drafted":
                    already_drafted += 1
                elif outcome == "skipped_inactive":
                    skipped_inactive += 1
                elif outcome == "unroutable":
                    unroutable += 1
                elif outcome == "refused":
                    refused += 1
                elif outcome == "outbox_dead_letter":
                    outbox_dead_letter += 1
                if holder_advisory == "changed":
                    recipient_changed += 1
                elif holder_advisory == "unverified":
                    recipient_unverified += 1
            except Exception as error:
                failed += 1
                logger.exception(
                    "ramp_chaser_digest.cardholder_failed week_of=%s card_holder=%s: %s",
                    week_of,
                    card_holder_id,
                    error,
                )

        summary = {
            "status": "ok",
            "mode": mode,
            "week_of": week_of,
            "cardholders_total": len(groups),
            "drafted": drafted,
            "already_drafted": already_drafted,
            "sent_observed": sent_observed,
            "discarded_unsent": discarded_unsent,
            "unsent_carryover": unsent_carryover,
            "skipped_inactive": skipped_inactive,
            "unroutable": unroutable,
            "refused_ms_writes_gate": refused,
            "outbox_dead_letter": outbox_dead_letter,
            "failed": failed,
            # NB these count DETECTIONS, not advisories a human saw: an
            # already-drafted cardholder short-circuits before render_digest and
            # still increments here. Fine for monitoring drift; do not read them
            # as "N reviewers were warned".
            "recipient_changed": recipient_changed,
            "recipient_unverified": recipient_unverified,
        }
        logger.info("ramp_chaser_digest.sweep_complete %s", summary)
        return summary

    def _reconcile_uncaptured_drafts(self, *, digest_repo, outbox_repo) -> None:
        """Capture Graph draft ids for every digest row still missing one (§6)."""
        for row in digest_repo.read_uncaptured():
            try:
                self._try_capture_draft_from_outbox(
                    digest=row,
                    digest_repo=digest_repo,
                    outbox_repo=outbox_repo,
                )
            except Exception as error:
                logger.warning(
                    "ramp_chaser_digest.capture_failed digest=%s: %s",
                    row.public_id,
                    error,
                )

    def _observe_outstanding_sends(
        self,
        *,
        digest_repo,
        current_week_of: str,
    ) -> dict:
        from integrations.ms.mail.external.client import get_message, list_messages

        sent_observed = 0
        discarded_unsent = 0
        unsent_carryover = 0

        for row in digest_repo.read_outstanding():
            if not row.draft_message_id:
                continue
            try:
                get_result = get_message(
                    message_id=row.draft_message_id,
                    include_body=False,
                )
            except Exception as error:
                logger.warning(
                    "ramp_chaser_digest.observe_get_failed digest=%s: %s",
                    row.public_id,
                    error,
                )
                continue

            if self._is_transient_graph_result(get_result):
                logger.warning(
                    "ramp_chaser_digest.observe_get_transient digest=%s status=%s",
                    row.public_id,
                    get_result.get("status_code"),
                )
                continue

            email = get_result.get("email")
            if email and get_result.get("status_code") == 200:
                if email.get("is_draft"):
                    if self._is_previous_week(row.week_of, current_week_of):
                        unsent_carryover += 1
                        self._stamp_outcome_only(
                            digest_repo,
                            row,
                            "unsent_carryover",
                        )
                    continue
                digest_repo.stamp_notified(
                    card_holder_ramp_user_id=row.card_holder_ramp_user_id,
                    week_of=row.week_of,
                    outcome="sent",
                )
                sent_observed += 1
                continue

            conversation_id = row.conversation_id
            if not conversation_id:
                logger.warning(
                    "ramp_chaser_digest.observe_vanished_no_conversation digest=%s",
                    row.public_id,
                )
                continue

            try:
                sent_hit = self._conversation_in_folder(
                    list_messages,
                    folder="sentitems",
                    conversation_id=conversation_id,
                )
                if sent_hit is True:
                    digest_repo.stamp_notified(
                        card_holder_ramp_user_id=row.card_holder_ramp_user_id,
                        week_of=row.week_of,
                        outcome="sent",
                    )
                    sent_observed += 1
                    continue
                if sent_hit is None:
                    continue

                deleted_hit = self._conversation_in_folder(
                    list_messages,
                    folder="deleteditems",
                    conversation_id=conversation_id,
                )
                if deleted_hit is True:
                    discarded_unsent += 1
                    self._stamp_outcome_only(
                        digest_repo,
                        row,
                        "discarded_unsent",
                    )
                    continue
                if deleted_hit is None:
                    continue

                logger.info(
                    "ramp_chaser_digest.observe_vanished_unknown digest=%s week_of=%s",
                    row.public_id,
                    row.week_of,
                )
            except Exception as error:
                logger.warning(
                    "ramp_chaser_digest.observe_folder_failed digest=%s: %s",
                    row.public_id,
                    error,
                )

        return {
            "sent_observed": sent_observed,
            "discarded_unsent": discarded_unsent,
            "unsent_carryover": unsent_carryover,
        }

    def _process_cardholder(
        self,
        *,
        card_holder_id: Optional[str],
        items: list,
        week_of: str,
        now: datetime,
        tz: ZoneInfo,
        settings,
        roster: dict,
        digest_repo,
        outbox_repo,
    ) -> tuple[str, bool]:
        if not card_holder_id:
            logger.warning(
                "ramp_chaser_digest.unroutable week_of=%s reason=null_card_holder",
                week_of,
            )
            return "unroutable", False

        entry = roster.get(str(card_holder_id))
        if entry is None:
            logger.warning(
                "ramp_chaser_digest.unroutable week_of=%s card_holder=%s reason=missing_roster",
                week_of,
                card_holder_id,
            )
            return "unroutable", False

        if not entry.is_active:
            return "skipped_inactive", False

        email = (entry.email or "").strip()
        if not email:
            logger.warning(
                "ramp_chaser_digest.unroutable week_of=%s card_holder=%s reason=no_email",
                week_of,
                card_holder_id,
            )
            return "unroutable", False

        from entities.ramp_chaser_digest.business.body import (
            RECIPIENT_ADVISORY_CHANGED,
            RECIPIENT_ADVISORY_UNVERIFIED,
        )
        from shared.encryption import blind_index

        current = blind_index(email.strip().lower())
        previous = digest_repo.read_latest_recipient_hash(
            card_holder_ramp_user_id=str(card_holder_id),
            week_of=week_of,
        )
        # ⛔ THREE FACTS THAT ONLY MAKE SENSE TOGETHER — none is obvious alone.
        #
        # 1. WARN-ONCE. The upsert below stamps `current`, so THIS week's row is
        #    next week's baseline. A change therefore advises exactly once, not
        #    every week after. Accepted: the advisory lands in the draft the
        #    reviewer is about to send, which is the moment the control exists for.
        # 2. WHY A PRE-EXISTING ROW READS AS FIRST SIGHT. The lookup sproc carries
        #    `AND RecipientHash IS NOT NULL`, so rows written before this column
        #    existed are skipped rather than compared against NULL. That is what
        #    stops the first post-deploy sweep raising a CHANGED advisory for
        #    everyone — they get UNVERIFIED instead, which is the honest signal.
        # 3. WHY A NULL PARAMETER CANNOT ERASE THE BASELINE. The upsert preserves
        #    with `CASE WHEN @RecipientHash IS NOT NULL`, so a caller that omits it
        #    leaves the stored hash intact. Without that, any non-hash-aware upsert
        #    would silently reset a cardholder to unbaselined.
        if previous and current and previous != current:
            holder_advisory = RECIPIENT_ADVISORY_CHANGED
        elif not previous and current:
            # No stored fingerprint: nothing to compare, so an address altered before
            # the first digest would be adopted silently. Flag it once (step 4c).
            holder_advisory = RECIPIENT_ADVISORY_UNVERIFIED
        else:
            holder_advisory = None
        if holder_advisory:
            logger.warning(
                "ramp_chaser_digest.recipient_advisory week_of=%s card_holder=%s advisory=%s",
                week_of,
                card_holder_id,
                holder_advisory,
            )

        digest = digest_repo.upsert(
            card_holder_ramp_user_id=str(card_holder_id),
            week_of=week_of,
            recipient_hash=current,
        )
        if digest is None:
            raise RuntimeError("upsert returned no digest row")

        if digest.draft_message_id:
            return "already_drafted", holder_advisory

        capture_state = self._try_capture_draft_from_outbox(
            digest=digest,
            digest_repo=digest_repo,
            outbox_repo=outbox_repo,
        )
        if capture_state == "captured":
            return "already_drafted", holder_advisory
        if capture_state == "enqueued":
            return "already_drafted", holder_advisory
        if capture_state == "dead_letter":
            return "outbox_dead_letter", holder_advisory

        refreshed = digest_repo.read_by_card_holder_and_week(
            card_holder_ramp_user_id=str(card_holder_id),
            week_of=week_of,
        )
        if refreshed and refreshed.draft_message_id:
            return "already_drafted", holder_advisory

        from entities.ramp_chaser_digest.business.body import (
            RAMP_CHASER_DIGEST_BODY_TYPE,
            render_digest,
        )
        from integrations.ms.outbox.business.service import MsOutboxService

        card_holder_name = self._card_holder_display_name(items)
        first_name = self._first_name_from_display(card_holder_name)
        body_items = [self._follow_up_to_body_item(row) for row in items]
        subject, body = render_digest(
            first_name=first_name,
            card_holder=card_holder_name,
            items=body_items,
            now=now,
            tz=tz,
            recipient_advisory=holder_advisory,
        )

        cc = self._resolve_cc(settings, cardholder_email=email)

        result = MsOutboxService().enqueue_send_mail(
            entity_type=_ENTITY_TYPE,
            entity_public_id=str(digest.public_id),
            to_addresses=[{"email": email, "name": card_holder_name}],
            cc_addresses=cc,
            subject=subject,
            body=body,
            body_type=RAMP_CHASER_DIGEST_BODY_TYPE,
            mode="draft",
        )
        if result is None:
            logger.info(
                "ramp_chaser_digest.enqueue_refused week_of=%s card_holder=%s",
                week_of,
                card_holder_id,
            )
            return "refused", holder_advisory

        return "drafted", holder_advisory

    def _try_capture_draft_from_outbox(
        self,
        *,
        digest,
        digest_repo,
        outbox_repo,
    ) -> str:
        """
        Reconcile outbox → digest draft id.

        Returns: none | captured | enqueued | dead_letter
        """
        from integrations.ms.mail.external.client import get_message

        entity_id = str(digest.public_id)
        completed = outbox_repo.read_completed_by_entity(
            _ENTITY_TYPE,
            entity_id,
            _OUTBOX_KIND_SEND_MAIL,
        )
        if completed:
            graph_id = self._graph_message_id_from_outbox_rows(completed)
            if graph_id:
                get_result = get_message(message_id=graph_id, include_body=False)
                if self._is_transient_graph_result(get_result):
                    return "enqueued"
                if get_result.get("status_code") == 200 and get_result.get("email"):
                    email = get_result["email"]
                    digest_repo.stamp_drafted(
                        card_holder_ramp_user_id=digest.card_holder_ramp_user_id,
                        week_of=digest.week_of,
                        draft_message_id=graph_id,
                        conversation_id=email.get("conversation_id"),
                        internet_message_id=email.get("internet_message_id"),
                    )
                    return "captured"

        if outbox_repo.count_by_entity_and_kind(
            _ENTITY_TYPE,
            entity_id,
            _OUTBOX_KIND_SEND_MAIL,
        ) > 0:
            pending = outbox_repo.read_pending_by_entity(
                _ENTITY_TYPE,
                entity_id,
                _OUTBOX_KIND_SEND_MAIL,
            )
            if pending:
                return "enqueued"
            if completed:
                return "enqueued"
            logger.warning(
                "ramp_chaser_digest.outbox_dead_letter digest=%s",
                digest.public_id,
            )
            return "dead_letter"
        return "none"

    @staticmethod
    def _graph_message_id_from_outbox_rows(rows: list) -> Optional[str]:
        for row in rows:
            payload_raw = getattr(row, "payload", None)
            if not payload_raw:
                continue
            try:
                payload = json.loads(payload_raw)
            except (TypeError, json.JSONDecodeError):
                continue
            graph_id = payload.get("graph_message_id")
            if graph_id:
                return str(graph_id)
        return None

    @staticmethod
    def _item_needs_chase(row) -> bool:
        needs_memo = bool(getattr(row, "needs_memo", None))
        needs_receipt = bool(getattr(row, "needs_receipt", None))
        return needs_memo or needs_receipt

    @staticmethod
    def _group_by_card_holder(rows: list) -> Dict[Optional[str], list]:
        groups: Dict[Optional[str], list] = {}
        for row in rows:
            key = getattr(row, "card_holder_ramp_user_id", None)
            if key is not None:
                key = str(key)
            groups.setdefault(key, []).append(row)
        return groups

    @staticmethod
    def _follow_up_to_body_item(row) -> dict:
        return {
            "merchant_name": getattr(row, "merchant_name", None),
            "amount": getattr(row, "amount", None),
            "transaction_date": getattr(row, "transaction_date", None),
            "needs_memo": getattr(row, "needs_memo", None),
            "needs_receipt": getattr(row, "needs_receipt", None),
            "first_seen_at": getattr(row, "first_seen_at", None),
        }

    @staticmethod
    def _card_holder_display_name(items: list) -> str:
        for row in items:
            name = (getattr(row, "card_holder_name", None) or "").strip()
            if name:
                return name
        return "Cardholder"

    @staticmethod
    def _first_name_from_display(display_name: str) -> str:
        part = (display_name or "").strip().split()
        return part[0] if part else "there"

    @staticmethod
    def _resolve_cc(settings, *, cardholder_email: str) -> list:
        cc_addr = (getattr(settings, "ramp_chaser_cc_email", None) or "").strip()
        if not cc_addr:
            return []
        if cc_addr.lower() == cardholder_email.strip().lower():
            return []
        return [{"email": cc_addr, "name": "Owner"}]

    @staticmethod
    def _stamp_outcome_only(digest_repo, digest_row, outcome: str) -> None:
        digest_repo.stamp_outcome(
            card_holder_ramp_user_id=digest_row.card_holder_ramp_user_id,
            week_of=digest_row.week_of,
            outcome=outcome,
        )

    @staticmethod
    def _is_previous_week(row_week_of: Optional[str], current_week_of: str) -> bool:
        if not row_week_of:
            return False
        try:
            row_d = date.fromisoformat(str(row_week_of)[:10])
            cur_d = date.fromisoformat(str(current_week_of)[:10])
        except ValueError:
            return False
        return row_d < cur_d

    @staticmethod
    def _conversation_in_folder(
        list_messages_fn,
        *,
        folder: str,
        conversation_id: str,
    ) -> Optional[bool]:
        """True if a message exists in the folder; False if clean miss; None if inconclusive."""
        safe_id = conversation_id.replace("'", "''")
        result = list_messages_fn(
            folder=folder,
            top=5,
            filter_query=f"conversationId eq '{safe_id}'",
        )
        status = result.get("status_code")
        if status != 200:
            if status and status >= 500:
                raise RuntimeError(f"graph list_messages failed: {status}")
            logger.warning(
                "ramp_chaser_digest.folder_read_inconclusive folder=%s status=%s",
                folder,
                status,
            )
            return None
        messages = result.get("messages") or []
        return len(messages) > 0

    @staticmethod
    def _is_transient_graph_result(result: dict) -> bool:
        if not isinstance(result, dict):
            return True
        code = result.get("status_code")
        if code in (429, 500, 502, 503, 504):
            return True
        if result.get("is_retryable"):
            return True
        return False

    @classmethod
    def canonicalize_week_of(cls, week_of: str) -> str:
        """Map any calendar day to the Tuesday anchor for that business week."""
        parsed = date.fromisoformat(str(week_of)[:10])
        return cls._tuesday_of_week(parsed).isoformat()

    def _default_week_of(self, settings, now: Optional[datetime] = None) -> str:
        tz = self._business_tz(settings)
        if now is None:
            now = datetime.now(tz)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)
        return self._tuesday_of_week(now.date()).isoformat()

    @staticmethod
    def _tuesday_of_week(today: date) -> date:
        """Tuesday anchor for the digest week (business calendar, not ISO Monday week)."""
        dow = today.weekday()  # Monday=0, Tuesday=1, ...
        if dow == 0:
            return today + timedelta(days=1)
        if dow == 1:
            return today
        return today - timedelta(days=dow - 1)

    @staticmethod
    def _empty_summary(
        *,
        status: str,
        mode: str,
        week_of: Optional[str],
        failed: int = 0,
    ) -> dict:
        return {
            "status": status,
            "mode": mode,
            "week_of": week_of,
            "cardholders_total": 0,
            "drafted": 0,
            "already_drafted": 0,
            "sent_observed": 0,
            "discarded_unsent": 0,
            "unsent_carryover": 0,
            "skipped_inactive": 0,
            "unroutable": 0,
            "refused_ms_writes_gate": 0,
            "outbox_dead_letter": 0,
            "failed": failed,
            "recipient_changed": 0,
            "recipient_unverified": 0,
        }

    @staticmethod
    def _normalized_mode(settings) -> str:
        """The ONE spelling of the fail-closed gate.

        Never inline a second copy of this, and never mirror it in a test. Two
        slices of this unit once normalised differently and the suite stayed
        green, because each asserted a property of its own copy.
        """
        return (settings.ramp_chaser_mode or "off").strip().lower()

    @staticmethod
    def _business_tz(settings) -> ZoneInfo:
        name = (getattr(settings, "business_timezone", None) or "America/Chicago").strip()
        try:
            return ZoneInfo(name)
        except Exception:
            logger.warning(
                "ramp_chaser_digest.bad_timezone name=%s — falling back to America/Chicago",
                name,
            )
            return ZoneInfo("America/Chicago")
