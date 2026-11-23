"""
Consolidated per-(project, date) crew draft email for ContractLabor reviews.

CONSOLIDATION (U-210, 2026-08-05): the review-notification is now ONE
draft per (project, work_date) that COMBINES every laborer on that
project+date — not one draft per (worker, project). It also gates on the
whole date: a draft only releases once EVERY ContractLabor for the
work_date has left 'pending_review' (all "Submit for Review"). While any
record for the date is still pending, the enqueue holds; the trigger
re-fires on each subsequent submit and the last one clears the gate.

Each draft is addressed to that project's PM(s) (TO) + Owner(s) (CC), BCCs
the office archive, and asks for the SubCostCode for the crew's labor on
that project+date. A PM reply applies that SCC to the whole crew (see
ContractLaborService.apply_reviewer_decision). Drafts are never auto-sent
— they land in the shared mailbox's Drafts folder for manual send.

Dedup + straggler handling: a (project, date) draft is claimed exactly
once via an atomic conditional insert into ContractLaborNotification.
A laborer added AFTER the date already released does NOT re-send the crew
email (the claim already exists) — it's a manual follow-up per Chris' call.

Empty TO is allowed: projects with no `UserProject(Role='Project Manager')`
get a draft with empty TO so the user has a placeholder in Drafts to
manually address.

Failure isolation: never raises. Enqueue failures log and continue; the
Review row is never rolled back.

SUGGESTIONS (U-797b1). Rollout and rollback are governed by CL_SUGGESTIONS_IN_REVIEW_ENABLED (Settings
cl_suggestions_in_review_enabled, default off, strict). Flag off is trunk's email. Flag on: the email shows each
crew member's suggested code in a table, and a (project, date) email is held while a counted member's suggestion
is pending and the crew's latest submit is under CL_SUGGESTION_WAIT_MINUTES. A held day is marked in
dbo.ContractLaborReviewHold, which is the only thing the sweep and the completion re-fire act on. The marker is
deleted when the claim succeeds. A fire that does not delete the marker counts an attempt, and after five the
sweep gives up on it and logs ids only. The shadow flag (CL_CODING_SHADOW_ENABLED) only produces the suggestion
rows. It does not change the email. The claim stores a snapshot of what each crew member's row showed.
"""

import html
import logging
from datetime import datetime, timezone
from typing import Optional

from shared.env_flags import is_truthy

from entities.review.business import review_notification_service as _rns
from entities.review.business.review_email_composer import (
    SUBJECT_PREFIX,
    ReviewEmailComposer,
)
from integrations.ms.outbox.business.service import MsOutboxService
from shared.database import call_procedure, get_connection, map_database_error

# The consolidated CL review email subject. Two accepted forms (U-798):
#   new (built):   "[Review] Contract Labor - {ProjectLabel} - {YYYY-MM-DD}"
#   legacy (sent before U-798): "Contract Labor - {ProjectLabel} - {YYYY-MM-DD}"
# The reply binding (ContractLaborService._expected_cl_reviewer_reply_conversation_id)
# and the FindContractLaborForReviewerReply sproc match on both forms.
CL_NOTIFICATION_SUBJECT_PREFIX = f"{SUBJECT_PREFIX}Contract Labor - "
CL_LEGACY_NOTIFICATION_SUBJECT_PREFIX = "Contract Labor - "


def is_cl_notification_subject(subject: Optional[str]) -> bool:
    """True iff an OUTBOUND subject is a CL review notification (new or legacy form).

    Outbound subjects only: "Re:"/"RE:" prefixes are not stripped.
    """
    if not subject:
        return False
    return subject.startswith(CL_NOTIFICATION_SUBJECT_PREFIX) or subject.startswith(
        CL_LEGACY_NOTIFICATION_SUBJECT_PREFIX
    )


logger = logging.getLogger(__name__)


def contract_labor_ask(project_label: str, work_date: str, *, suggestions: bool = False) -> str:
    """The reviewer ask for a consolidated crew draft (one project, one date).

    suggestions=False is trunk's ask, byte for byte (flag off). suggestions=True (U-797b1, flag on) asks for the
    suggested codes and keeps the decline guidance."""
    if suggestions:
        return (
            f"<p>The following Contract Labor for {html.escape(project_label)} "
            f"on {html.escape(work_date)} has been submitted for review. "
            'Reply "Approved" to apply the suggested codes, or reply with a sub cost code '
            "to apply it to the whole crew. To decline, reply \"Not approved\" with the reason.</p>"
        )
    return (
        f"<p>The following Contract Labor for {html.escape(project_label)} "
        f"on {html.escape(work_date)} has been submitted for review. When "
        "you have a moment, please review and reply with an approval with "
        "sub cost code and description or not approved. The sub cost code "
        "you provide will be applied to the full crew listed below.</p>"
    )


# The per-row note for a suggestion that is not a shown code (U-797b1). Clean and no-suggestion rows have none.
_STATE_NOTES = {
    "holding": "holding code, not suggested",
    "failed": "suggestion failed",
    "skipped": "suggestion skipped",
    "pending": "suggestion pending when sent",
}


def _suggested_cell(display) -> str:
    if display.code_label:
        return display.code_label + (" (split?)" if display.split_shown else "")
    return "no suggestion"


def _note_cell(display) -> str:
    parts: list[str] = []
    if display.split_shown:
        parts.append(
            "possible split" + (": " + ", ".join(display.candidate_labels) if display.candidate_labels else "")
        )
    note = _STATE_NOTES.get(display.state)
    if note:
        parts.append(note)
    return "; ".join(parts)


def _crew_snapshots(lines: list, displays: dict) -> dict:
    """One CrewTargetSnapshot per ContractLabor on the crew, from what its email row showed (U-797b1).

    The fingerprint covers that member's lines on this project at compose time (cl_lines_fingerprint)."""
    from entities.review.business import review_notification_service as rns

    pairs_by_cl: dict = {}
    for r in lines:
        pairs_by_cl.setdefault(r.ContractLaborId, []).append(
            (getattr(r, "LineItemId", None), getattr(r, "Hours", None))
        )
    out: dict = {}
    for cl_id, pairs in pairs_by_cl.items():
        display = displays[cl_id]
        out[cl_id] = rns.CrewTargetSnapshot(
            contract_labor_id=cl_id,
            suggested_sub_cost_code_id=display.code_id,
            suggestion_id=display.suggestion_id,
            suggestion_source=display.source,
            suggestion_may_split=display.split_shown,
            lines_fingerprint=rns.cl_lines_fingerprint(pairs),
        )
    return out


class ContractLaborReviewNotificationService:
    """Per-project draft generator for ContractLabor reviews."""

    def enqueue_drafts(self, *, contract_labor) -> None:
        """Public surface. Resolves per-project recipients + line items,
        builds one draft per project, enqueues into `[ms].[Outbox]`.

        Failures are isolated and logged — never propagate."""
        try:
            self._do_enqueue(contract_labor=contract_labor)
        except Exception as error:
            logger.exception(
                "cl_review_notification.enqueue_failed cl_public_id=%s: %s",
                getattr(contract_labor, "public_id", None),
                error,
            )

    def _do_enqueue(self, *, contract_labor):
        cl_public_id = getattr(contract_labor, "public_id", None)
        work_date = getattr(contract_labor, "work_date", None)
        if work_date is None:
            logger.info(
                "cl_review_notification.skip_no_work_date cl_public_id=%s",
                cl_public_id,
            )
            return
        work_date_str = (
            work_date.isoformat() if hasattr(work_date, "isoformat") else str(work_date)
        )

        # ── DATE GATE ────────────────────────────────────────────────
        # Release the consolidated crew drafts ONLY when every
        # ContractLabor for this WorkDate has left 'pending_review' (all
        # "Submit for Review"). While any record for the date is still
        # pending, hold — the trigger re-fires on each subsequent submit
        # and the last one clears the gate.
        pending = self._count_pending_for_date(work_date)
        if pending is None:
            return  # gate lookup failed; error already logged — don't half-send
        if pending > 0:
            logger.info(
                "cl_review_notification.held work_date=%s pending=%d "
                "(trigger cl_public_id=%s)",
                work_date_str, pending, cl_public_id,
            )
            return

        # ── GATHER: all submitted crew lines for the date, by project ─
        line_rows = self._read_submitted_lines_for_date(work_date)
        if not line_rows:
            logger.info(
                "cl_review_notification.nothing_submitted work_date=%s", work_date_str,
            )
            return

        lines_by_project: dict[int, list] = {}
        project_meta: dict[int, dict] = {}
        for r in line_rows:
            pid = r.ProjectId
            lines_by_project.setdefault(pid, []).append(r)
            if pid not in project_meta:
                project_meta[pid] = {
                    "name": getattr(r, "ProjectName", None) or "",
                    "abbreviation": getattr(r, "ProjectAbbreviation", None) or "",
                    # ANY crew CL with a line on this project resolves the
                    # per-project PM/Owner recipients + satisfies the FK.
                    "representative_cl_id": r.ContractLaborId,
                }

        # BCC the office archive (matches Bill's review-notification
        # envelope). Lazy import — Settings can fail to load in some CLI
        # contexts; never let it break the enqueue path.
        bcc_addresses = self._build_bcc_addresses()

        settings = self._load_settings()  # one Settings per enqueue
        suggestions_on = self._suggestions_in_review_enabled(settings)
        outbox = MsOutboxService()
        enqueued = 0
        for project_id in sorted(lines_by_project.keys()):
            lines = lines_by_project[project_id]
            meta = project_meta[project_id]
            representative_cl_id = meta["representative_cl_id"]
            project_label = self._format_project_label(meta, project_id)
            subject = f"{CL_NOTIFICATION_SUBJECT_PREFIX}{project_label} - {work_date_str}"

            # ── SUGGESTIONS (U-797b1, flag on only) ─────────────
            # Hold this (project, date) while a counted member's suggestion is pending and the crew's latest submit
            # is under the wait. A held day is marked, so the sweep and the completion re-fire can find it. If the
            # marker cannot be written the email goes now rather than being held with no way to release it.
            displays: dict = {}
            snapshots = None
            if suggestions_on:
                from entities.contract_labor.business.coding_shadow_service import suggestion_hold

                suggestion_rows, labels = self._read_suggestions(project_id, work_date)
                if suggestion_hold(suggestion_rows, now=datetime.now(timezone.utc)) and self._mark_held(
                    project_id, work_date
                ):
                    logger.info(
                        "cl_review_notification.held_for_suggestions project_id=%s work_date=%s",
                        project_id, work_date_str,
                    )
                    continue
                displays = self._displays_for_crew(lines, suggestion_rows, labels)
                snapshots = _crew_snapshots(lines, displays)

            # ── CLAIM (dedup + straggler-manual + race guard) ────
            # Atomically insert the (project, date) token; only the
            # inserter enqueues the draft. If the token already exists
            # (this project+date released in a prior cycle), skip — a
            # late-added laborer is a manual follow-up, never re-sends
            # the whole crew. The Subject stored here is the deterministic
            # reply-binding key (FindContractLaborForReviewerReply).
            claimed = self._claim_notification(
                contract_labor_id=representative_cl_id,
                project_id=project_id,
                work_date=work_date,
                outbound_subject=subject,
            )
            if not claimed:
                logger.info(
                    "cl_review_notification.already_sent project_id=%s work_date=%s",
                    project_id, work_date_str,
                )
                continue

            # U-797c1: the same claim, recorded on dbo.ReviewNotification with one target per
            # crew member. Self-guarded; None when it could not be written (legacy claim still holds).
            # U-797c2a: the record id rides in the enqueue payload (send_with_record).
            review_notification = self._claim_review_notification(
                project_id=project_id,
                work_date=work_date,
                subject=subject,
                contract_labor_ids=sorted({r.ContractLaborId for r in lines}),
                snapshots=snapshots,
            )

            # Claim is held from here down. Any exit without an outbox row releases it so a later
            # trigger can retry. One project's failure must never abort the remaining projects.
            # NOTE: _claim_notification is self-guarded and cannot raise; if that ever changes, move
            # it back inside the try or per-project isolation degrades to whole-run.
            try:
                recipients_by_project = self._fetch_recipients(representative_cl_id)
                bucket = recipients_by_project.get(project_id, {"pms": [], "owners": []})
                pms = bucket.get("pms", [])
                owners = bucket.get("owners", [])

                to_addresses = self._build_recipient_addresses(pms)
                cc_addresses = self._build_recipient_addresses(owners)
                body = self._build_body(
                    project_label=project_label,
                    work_date=work_date_str,
                    lines=lines,
                    pms=pms,
                    suggestions=displays,
                    suggestions_enabled=suggestions_on,
                )

                outbox_row = ReviewEmailComposer.send_with_record(
                    outbox=outbox,
                    records_factory=lambda: _rns.ReviewNotificationRecordService(),
                    record=review_notification,
                    context=f"project_id={project_id} work_date={work_date_str}",
                    mail=dict(
                        entity_type="ContractLabor",
                        entity_public_id=str(cl_public_id or ""),
                        to_addresses=to_addresses,
                        cc_addresses=cc_addresses,
                        bcc_addresses=bcc_addresses,
                        subject=subject,
                        body=body,
                        body_type="HTML",
                        mode="draft",
                    ),
                )
                if outbox_row:
                    enqueued += 1
                    if suggestions_on:
                        self._clear_hold(project_id, work_date)
                    continue
                logger.error(
                    "cl_review_notification.enqueue_project_no_row "
                    "project_id=%s work_date=%s (tenant/gate — releasing claim)",
                    project_id, work_date_str,
                )
            except Exception as error:
                logger.exception(
                    "cl_review_notification.enqueue_project_failed "
                    "project_id=%s work_date=%s: %s",
                    project_id, work_date_str, error,
                )
            # Record first, then the legacy claim: a later re-claim never meets a stuck record row.
            self._release_review_notification(review_notification)
            self._release_notification_claim(
                project_id=project_id, work_date=work_date,
            )

        logger.info(
            "cl_review_notification.enqueued work_date=%s projects=%d drafts=%d",
            work_date_str, len(lines_by_project), enqueued,
        )

    # =========================================================================
    # DB reads for the gate + consolidation
    # =========================================================================

    def _count_pending_for_date(self, work_date) -> Optional[int]:
        """Count ContractLabor still in 'pending_review' for the date.
        Returns None on lookup failure so the caller aborts rather than
        releasing a half-gated set."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                call_procedure(
                    cursor=cur,
                    name="CountPendingContractLaborByWorkDate",
                    params={"WorkDate": work_date},
                )
                row = cur.fetchone()
                return int(row.PendingCount) if row is not None else 0
        except Exception as error:
            logger.exception(
                "cl_review_notification.gate_count_failed work_date=%s: %s",
                work_date, error,
            )
            return None

    def _read_submitted_lines_for_date(self, work_date) -> list:
        """All project-anchored line items across contractors whose
        ContractLabor for the date is 'submitted' (awaiting PM coding)."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                call_procedure(
                    cursor=cur,
                    name="ReadSubmittedContractLaborLinesByWorkDate",
                    params={"WorkDate": work_date},
                )
                return cur.fetchall()
        except Exception as error:
            logger.exception(
                "cl_review_notification.read_lines_failed work_date=%s: %s",
                work_date, error,
            )
            return []

    def _claim_notification(
        self, *, contract_labor_id: int, project_id: int, work_date, outbound_subject: str,
    ) -> bool:
        """Atomically claim the (project, date) notification token.

        Returns True iff THIS call inserted the row — the caller then
        enqueues the draft. Returns False when the token already existed
        (already sent → skip) OR the write failed (logged; safer to skip
        than double-send). The conditional insert under (UPDLOCK,
        HOLDLOCK) is both the dedup guard and the concurrency guard: two
        near-simultaneous "last submit" releases can't both insert.
        """
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO dbo.[ContractLaborNotification]
                           ([ContractLaborId], [ProjectId], [WorkDate], [OutboundSubject])
                       SELECT ?, ?, ?, ?
                       WHERE NOT EXISTS (
                           SELECT 1 FROM dbo.[ContractLaborNotification] WITH (UPDLOCK, HOLDLOCK)
                           WHERE [ProjectId] = ? AND [WorkDate] = ?
                       )""",
                    (contract_labor_id, project_id, work_date, outbound_subject,
                     project_id, work_date),
                )
                inserted = cur.rowcount
                conn.commit()
                return inserted == 1
        except Exception as error:
            logger.warning(
                "cl_review_notification.claim_failed project_id=%s work_date=%s: %s",
                project_id, work_date, error,
            )
            return False

    @staticmethod
    def _load_settings():
        """The Settings for one enqueue. None when they cannot load: the email then goes as trunk's."""
        try:
            from config import Settings

            return Settings()
        except Exception:
            logger.warning("cl_review_notification.settings_load_failed — email unchanged", exc_info=True)
            return None

    @staticmethod
    def _suggestions_in_review_enabled(settings=None) -> bool:
        """CL_SUGGESTIONS_IN_REVIEW_ENABLED, read strictly: only a real True, or the string "true", turns it on.
        No Settings, a stand-in (a MagicMock), or any other value leaves it off (trunk's email)."""
        value = getattr(settings, "cl_suggestions_in_review_enabled", False) if settings is not None else False
        if value is True:
            return True
        return isinstance(value, str) and is_truthy(value)

    def _hold_repo(self):
        from entities.contract_labor.persistence.coding_suggestion_repo import (
            ContractLaborCodingSuggestionRepository,
        )

        return ContractLaborCodingSuggestionRepository()

    def _mark_held(self, project_id: int, work_date) -> bool:
        """Write the marker for a held (project, date). False when it could not be written. Never raises."""
        try:
            self._hold_repo().upsert_review_hold(project_id, work_date)
            return True
        except Exception as error:
            logger.error(
                "cl_review_notification.hold_mark_failed project_id=%s error=%s", project_id, type(error).__name__,
            )
            return False

    def _clear_hold(self, project_id: int, work_date) -> None:
        """Delete the marker once its claim has succeeded. Never raises."""
        try:
            self._hold_repo().delete_review_hold(project_id, work_date)
        except Exception as error:
            logger.error(
                "cl_review_notification.hold_clear_failed project_id=%s error=%s", project_id, type(error).__name__,
            )

    def _read_suggestions(self, project_id: int, work_date):
        """(suggestion rows, labels) for one project's day. A read failure returns no rows, so the email goes
        without suggestions rather than being held on a read that cannot answer. Never raises."""
        try:
            from entities.contract_labor.persistence.coding_suggestion_repo import (
                ContractLaborCodingSuggestionRepository,
            )

            return ContractLaborCodingSuggestionRepository().read_suggestions_for_project_day(project_id, work_date)
        except Exception as error:
            logger.error(
                "cl_review_notification.suggestions_read_failed project_id=%s error=%s",
                project_id, type(error).__name__,
            )
            return [], {}

    @staticmethod
    def _displays_for_crew(lines: list, rows: list, labels: dict) -> dict:
        from entities.contract_labor.business.coding_shadow_service import suggestion_display

        by_cl = {row.contract_labor_id: row for row in rows}
        return {
            cl_id: suggestion_display(by_cl.get(cl_id), labels)
            for cl_id in {r.ContractLaborId for r in lines}
        }

    def _claim_review_notification(self, *, project_id: int, work_date, subject: str,
                                   contract_labor_ids: list, snapshots: Optional[dict] = None):
        """U-797c1: record the crew claim on dbo.ReviewNotification. Never raises.
        Returns None when not written (already claimed, or a failure). snapshots: U-797b1."""
        context = f"project_id={project_id} work_date={work_date}"
        records = ReviewEmailComposer.records_or_none(
            lambda: _rns.ReviewNotificationRecordService(), context,
        )
        if records is None:
            return None
        return records.try_claim_for_crew(
            project_id=project_id,
            work_date=work_date,
            subject=subject,
            contract_labor_ids=contract_labor_ids,
            snapshots=snapshots,
            context=context,
        )

    def _release_review_notification(self, record) -> None:
        # A leftover record row only means the record for this (project, date) is missing
        # or stale, so the reply binds through the legacy path. It does not stop a future
        # draft from being sent.
        if record is not None:
            context = f"review_notification_id={record.Id}"
            records = ReviewEmailComposer.records_or_none(
                lambda: _rns.ReviewNotificationRecordService(), context,
            )
            if records is not None:
                records.try_release(record.Id, context=context)

    def _release_notification_claim(self, *, project_id: int, work_date) -> None:
        """Drop the (project, date) claim so a failed draft can retry.

        Failure-isolated: logs and swallows — never raises. A failed
        release leaves the pair stuck (dedup blocks forever); log loudly."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """DELETE FROM dbo.[ContractLaborNotification]
                       WHERE [ProjectId] = ? AND [WorkDate] = ?""",
                    (project_id, work_date),
                )
                conn.commit()
        except Exception as error:
            logger.error(
                "cl_review_notification.release_claim_failed project_id=%s "
                "work_date=%s: %s — claim row may be stuck; consolidated "
                "draft for this pair will not retry until manual cleanup",
                project_id, work_date, error,
            )

    # =========================================================================
    # Internals
    # =========================================================================

    def _fetch_recipients(self, contract_labor_id: int) -> dict[int, dict]:
        """Return {project_id: {'name': str, 'abbreviation': Optional[str],
        'pms': [...], 'owners': [...]}}.

        Mirrors Bill's envelope: PMs go to TO, Owners go to CC. Every
        project on the CL's line items appears, even when neither role
        is configured — `'pms'` and `'owners'` are empty lists in that
        case. Project name and abbreviation come from the sproc's LEFT
        JOIN on dbo.Project so this avoids the access-guarded service
        path (which returns None in the no-actor context the outbox
        worker runs in)."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                call_procedure(
                    cursor=cur,
                    name="ResolveContractLaborReviewRecipientsPerProject",
                    params={"ContractLaborId": contract_labor_id},
                )
                rows = cur.fetchall()
        except Exception as error:
            logger.exception(
                "cl_review_notification.recipients_lookup_failed cl_id=%s: %s",
                contract_labor_id,
                error,
            )
            raise map_database_error(error)

        out: dict[int, dict] = {}
        for r in rows:
            bucket = out.setdefault(
                r.ProjectId,
                {
                    "name": getattr(r, "ProjectName", None) or "",
                    "abbreviation": getattr(r, "ProjectAbbreviation", None) or "",
                    "pms": [],
                    "owners": [],
                },
            )
            # NULL UserId rows mark "project has no PM/Owner" — the
            # bucket already exists; skip.
            if r.UserId is None:
                continue
            recipient = {
                "user_id": r.UserId,
                "firstname": r.Firstname or "",
                "lastname": r.Lastname or "",
                "email": r.Email,
            }
            role = (getattr(r, "RoleName", None) or "").strip()
            if role == "Project Manager":
                bucket["pms"].append(recipient)
            elif role == "Owner":
                bucket["owners"].append(recipient)
            # else: unexpected role, ignore.
        return out

    def _build_recipient_addresses(self, rows: list[dict]) -> list[dict]:
        """Convert recipient dicts into the MS-outbox recipient shape.
        Email-less rows are dropped. Empty list is valid per the relaxed
        draft-mode guard in the outbox worker."""
        addrs: list[dict] = []
        for r in rows:
            email = (r.get("email") or "").strip()
            if not email:
                continue
            name = f"{r.get('firstname', '')} {r.get('lastname', '')}".strip() or None
            addrs.append(ReviewEmailComposer.address(email, name))
        return addrs

    def _build_bcc_addresses(self) -> list[dict]:
        """Office archive recipient list. Mirrors Bill's review notification
        which BCCs `Settings.invoice_inbox_email` when configured. Returns
        an empty list when not configured — never raises."""
        try:
            from config import Settings

            settings = Settings()
            inbox = (getattr(settings, "invoice_inbox_email", "") or "").strip()
            if inbox:
                return ReviewEmailComposer.archive_bcc(inbox, None)
        except Exception:
            logger.warning(
                "cl_review_notification.bcc_lookup_failed — falling back to no BCC",
                exc_info=True,
            )
        return []

    def _format_project_label(self, bucket: dict, project_id: int) -> str:
        """Prefer abbreviation when set; fall back to project name; then to
        '#<id>' so the subject is always populated. Bucket carries the
        project metadata from the recipients sproc — bypasses the
        access-guarded ProjectService path."""
        abbr = (bucket.get("abbreviation") or "").strip()
        if abbr:
            return abbr
        name = (bucket.get("name") or "").strip()
        if name:
            return name
        return f"#{project_id}"

    def _greeting_names(self, pms: list[dict]) -> list[str]:
        """Stripped, non-empty first names of the PMs, in order. The composer dedupes
        and slash-joins them ('Cassidy/Zach')."""
        names: list[str] = []
        for r in pms:
            first = (r.get("firstname") or "").strip()
            if first:
                names.append(first)
        return names

    def _build_body(
        self,
        *,
        project_label: str,
        work_date: str,
        lines: list,
        pms: list[dict],
        suggestions: Optional[dict] = None,
        suggestions_enabled: bool = False,
    ) -> str:
        """Consolidated crew HTML body for one (project, date).

        suggestions_enabled=False (flag off): trunk's body, byte for byte. True (U-797b1): the greeting, the new ask
        and one table for the crew with the suggestions. suggestions maps ContractLabor id to a SuggestionDisplay."""
        if not suggestions_enabled:
            return self._build_body_legacy(project_label=project_label, work_date=work_date, lines=lines, pms=pms)
        greeting = ReviewEmailComposer.greeting(self._greeting_names(pms), dedupe=True)
        ask = contract_labor_ask(project_label, work_date, suggestions=True)
        return greeting + ask + self._format_crew_table(lines, suggestions)

    def _format_crew_table(self, lines: list, suggestions: Optional[dict]) -> str:
        """One table for the crew, a row per line, in the sproc's (project, worker, line) order.
        Columns: Worker, Hours, Notes/Description, Suggested code, Note (the Bill email's table style)."""
        from entities.contract_labor.business.coding_shadow_service import suggestion_display

        header = (
            "<tr><th align='left'>Worker</th><th align='left'>Hours</th>"
            "<th align='left'>Is Billable</th><th align='left'>Is Overhead</th>"
            "<th align='left'>Notes/Description</th><th align='left'>Suggested code</th>"
            "<th align='left'>Note</th></tr>"
        )
        rows: list[str] = []
        for r in lines:
            display = suggestions[r.ContractLaborId]
            name = getattr(r, "EmployeeName", None) or "Worker"
            hours = self._fmt_hours(getattr(r, "Hours", None))
            desc = (getattr(r, "Description", None) or "").strip() or "(no description)"
            billable = self._fmt_yes_no(getattr(r, "IsBillable", None), default_true=True)
            overhead = self._fmt_yes_no(getattr(r, "IsOverhead", None), default_true=False)
            rows.append(
                f"<tr><td>{html.escape(name)}</td><td>{html.escape(hours)}</td>"
                f"<td>{html.escape(billable)}</td><td>{html.escape(overhead)}</td>"
                f"<td>{html.escape(desc)}</td><td>{html.escape(_suggested_cell(display))}</td>"
                f"<td>{html.escape(_note_cell(display))}</td></tr>"
            )
        return (
            "<table cellpadding='4' cellspacing='0' border='1' "
            "style='border-collapse:collapse; margin-top:6px;'>"
            f"{header}{''.join(rows)}</table>"
        )

    @staticmethod
    def _fmt_hours(value) -> str:
        if value is None:
            return "0.00"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    def _build_body_legacy(
        self,
        *,
        project_label: str,
        work_date: str,
        lines: list,
        pms: list[dict],
    ) -> str:
        """Consolidated crew HTML body for one (project, date):

            {name(s)},

            The following Contract Labor for {project} on {date} has been
            submitted for review. When you have a moment, please review
            and reply with an approval with sub cost code and description
            or not approved. The sub cost code you provide will be applied
            to the full crew listed below.

            {Worker A}
            Hours: {hours}
            Is Billable: {billable}
            Is Overhead: {overhead}
            Description: {description}

            {Worker B}
            ...

        Rows arrive project-then-worker-then-line ordered (the sproc's
        ORDER BY), so grouping preserves a stable render. A worker with
        multiple lines on the project gets each line under one bold name.
        When no PM is resolved, the salutation is omitted."""
        # Greeting only rendered when PMs resolve. No PMs → start straight
        # at the body, no salutation. (Owners and BCC still receive the
        # email; they just don't get a personalized greeting since they're
        # not the addressees in TO.)
        greeting = ReviewEmailComposer.greeting(self._greeting_names(pms), dedupe=True)
        ask = contract_labor_ask(project_label, work_date)

        # Group the flat line rows by worker (ContractLaborId), preserving
        # first-seen order so the render matches the sproc's ORDER BY.
        by_worker: dict = {}
        worker_order: list = []
        for r in lines:
            cl_id = r.ContractLaborId
            if cl_id not in by_worker:
                by_worker[cl_id] = {
                    "name": getattr(r, "EmployeeName", None) or "Worker",
                    "lines": [],
                }
                worker_order.append(cl_id)
            by_worker[cl_id]["lines"].append(r)

        parts: list[str] = [greeting, ask]
        for cl_id in worker_order:
            parts.append(self._format_worker_block(by_worker[cl_id]))
        return "".join(parts)

    def _format_worker_block(self, worker: dict) -> str:
        """One <p> per worker: bold name, then Hours/Billable/Overhead/
        Description for each of their lines on this project."""
        line_blocks: list[str] = []
        for r in worker["lines"]:
            hours = self._fmt_hours(getattr(r, "Hours", None))
            billable = self._fmt_yes_no(getattr(r, "IsBillable", None), default_true=True)
            overhead = self._fmt_yes_no(getattr(r, "IsOverhead", None), default_true=False)
            desc = (getattr(r, "Description", None) or "").strip() or "(no description)"
            line_blocks.append(
                f"Hours: {html.escape(hours)}<br>"
                f"Is Billable: {html.escape(billable)}<br>"
                f"Is Overhead: {html.escape(overhead)}<br>"
                f"Description: {html.escape(desc)}"
            )
        inner = "<br><br>".join(line_blocks)
        return f"<p><b>{html.escape(worker['name'])}</b><br>{inner}</p>"

    @staticmethod
    def _fmt_yes_no(value, *, default_true: bool) -> str:
        """ContractLaborLineItem.IsBillable / IsOverhead are bool with
        None defaults — treat None as default_true. Mirrors the Jinja
        template's `is_billable is not False` convention."""
        if value is None:
            return "Yes" if default_true else "No"
        return "Yes" if value else "No"
