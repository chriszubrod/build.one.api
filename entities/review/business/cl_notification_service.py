"""
Consolidated per-(project, date) crew draft email for ContractLabor reviews.

CONSOLIDATION (U-XXX, 2026-08-05): the review-notification is now ONE
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
"""

import html
import logging
from typing import Optional

from integrations.ms.outbox.business.service import MsOutboxService
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


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

        outbox = MsOutboxService()
        enqueued = 0
        for project_id in sorted(lines_by_project.keys()):
            lines = lines_by_project[project_id]
            meta = project_meta[project_id]
            representative_cl_id = meta["representative_cl_id"]
            project_label = self._format_project_label(meta, project_id)
            subject = f"Contract Labor - {project_label} - {work_date_str}"

            # ── CLAIM (dedup + straggler-manual + race guard) ────────
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
            )

            try:
                outbox.enqueue_send_mail(
                    entity_type="ContractLabor",
                    entity_public_id=str(cl_public_id or ""),
                    to_addresses=to_addresses,
                    cc_addresses=cc_addresses,
                    bcc_addresses=bcc_addresses,
                    subject=subject,
                    body=body,
                    body_type="HTML",
                    mode="draft",
                )
                enqueued += 1
            except Exception as error:
                logger.exception(
                    "cl_review_notification.enqueue_project_failed "
                    "project_id=%s work_date=%s: %s",
                    project_id, work_date_str, error,
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
            addrs.append({"email": email, "name": name})
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
                return [{"email": inbox, "name": None}]
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

    def _greeting_names(self, pms: list[dict]) -> str:
        """Slash-join first names: ['Cassidy', 'Zach'] → 'Cassidy/Zach'.
        Returns empty string when no recipients — caller renders 'Hi,'."""
        firsts: list[str] = []
        for r in pms:
            fn = (r.get("firstname") or "").strip()
            if fn and fn not in firsts:
                firsts.append(fn)
        return "/".join(firsts)

    def _build_body(
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
        names = self._greeting_names(pms)
        greeting = f"<p>{html.escape(names)},</p>" if names else ""

        ask = (
            f"<p>The following Contract Labor for {html.escape(project_label)} "
            f"on {html.escape(work_date)} has been submitted for review. When "
            "you have a moment, please review and reply with an approval with "
            "sub cost code and description or not approved. The sub cost code "
            "you provide will be applied to the full crew listed below.</p>"
        )

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
    def _fmt_hours(value) -> str:
        if value is None:
            return "0.00"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _fmt_yes_no(value, *, default_true: bool) -> str:
        """ContractLaborLineItem.IsBillable / IsOverhead are bool with
        None defaults — treat None as default_true. Mirrors the Jinja
        template's `is_billable is not False` convention."""
        if value is None:
            return "Yes" if default_true else "No"
        return "Yes" if value else "No"
