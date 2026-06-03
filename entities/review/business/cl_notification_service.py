"""
Per-project draft email for ContractLabor review submissions.

When a ContractLabor enters review (first Submit transition), enqueue ONE
MS Graph `create_draft` per distinct project on the labor's line items.
Each draft is addressed to that project's PM(s) and asks for the
SubCostCode(s) for the lines on that project. Drafts are never auto-sent
— they land in the shared mailbox's Drafts folder and the user manually
addresses + sends them after reviewing.

Empty TO is allowed: projects with no `UserProject(Role='Project Manager')`
get a draft with empty TO so the user has a placeholder in Drafts to
manually address. Per Chris' product call.

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
        from entities.contract_labor.persistence.line_item_repo import (
            ContractLaborLineItemRepository,
        )

        cl_id = contract_labor.id
        cl_public_id = contract_labor.public_id
        worker_name = contract_labor.employee_name or "Worker"
        work_date = contract_labor.work_date or "—"

        line_items = ContractLaborLineItemRepository().read_by_contract_labor_id(
            contract_labor_id=cl_id,
        )
        lines_by_project: dict[int, list] = {}
        for li in line_items:
            if li.project_id is None:
                continue  # overhead lines have no project anchor
            lines_by_project.setdefault(li.project_id, []).append(li)

        recipients_by_project = self._fetch_recipients(cl_id)

        # Union of project ids from BOTH sources — recipients sproc
        # surfaces every distinct project even when no PM exists, AND
        # we ALWAYS create a draft per project that has at least one
        # line item (even if no PM is configured for that project).
        project_ids = set(lines_by_project.keys()) | set(recipients_by_project.keys())

        outbox = MsOutboxService()
        enqueued = 0
        empty_bucket = {"name": "", "abbreviation": "", "pms": []}
        for project_id in sorted(project_ids):
            lines = lines_by_project.get(project_id, [])
            if not lines:
                # Project surfaced by sproc but no current line items —
                # nothing to ask about. Skip.
                continue
            bucket = recipients_by_project.get(project_id, empty_bucket)
            pms = bucket.get("pms", [])
            project_label = self._format_project_label(bucket, project_id)

            to_addresses = self._build_to_addresses(pms)
            subject = f"Review: {worker_name} – {project_label} – {work_date}"
            body = self._build_body(
                worker_name=worker_name,
                work_date=str(work_date),
                project_label=project_label,
                lines=lines,
                pms=pms,
            )

            try:
                outbox.enqueue_send_mail(
                    entity_type="ContractLabor",
                    entity_public_id=cl_public_id,
                    to_addresses=to_addresses,
                    subject=subject,
                    body=body,
                    body_type="HTML",
                    mode="draft",
                )
                enqueued += 1
            except Exception as error:
                logger.exception(
                    "cl_review_notification.enqueue_project_failed "
                    "cl_public_id=%s project_id=%s: %s",
                    cl_public_id,
                    project_id,
                    error,
                )

        logger.info(
            "cl_review_notification.enqueued cl_public_id=%s projects=%d drafts=%d",
            cl_public_id,
            len(project_ids),
            enqueued,
        )

    # =========================================================================
    # Internals
    # =========================================================================

    def _fetch_recipients(self, contract_labor_id: int) -> dict[int, dict]:
        """Return {project_id: {'name': str, 'abbreviation': Optional[str],
        'pms': [{user_id, firstname, lastname, email}, ...]}}.

        Every project on the CL's line items appears, even when no PM is
        configured — `'pms'` is an empty list in that case. Project name
        and abbreviation come from the sproc's LEFT JOIN on dbo.Project
        so this avoids the access-guarded service path (which would
        return None in a no-actor context like the outbox worker)."""
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
                },
            )
            # Each sproc row corresponds to one PM (or to one NULL-PM
            # placeholder row when the project has no PM). Skip NULL-PM
            # rows — the bucket already exists.
            if r.UserId is None:
                continue
            bucket["pms"].append(
                {
                    "user_id": r.UserId,
                    "firstname": r.Firstname or "",
                    "lastname": r.Lastname or "",
                    "email": r.Email,
                }
            )
        return out

    def _build_to_addresses(self, pms: list[dict]) -> list[dict]:
        """Convert PM dicts into the MS-outbox recipient shape. Email-less
        rows are dropped. Empty list is valid per the relaxed draft-mode
        guard in the outbox worker."""
        addrs: list[dict] = []
        for r in pms:
            email = (r.get("email") or "").strip()
            if not email:
                continue
            name = f"{r.get('firstname', '')} {r.get('lastname', '')}".strip() or None
            addrs.append({"email": email, "name": name})
        return addrs

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
        worker_name: str,
        work_date: str,
        project_label: str,
        lines: list,
        pms: list[dict],
    ) -> str:
        """HTML body matching Chris' template (2026-06-03):

            {name(s)},

            The following Contract Labor record has been submitted for
            review. When you have a moment, please review and reply with
            an approval with sub cost code and description or not
            approved.

            Date: {date}
            Project: {project}
            Hours: {hours}
            Is Billable: {billable}
            Is Overhead: {overhead}
            Description: {description}

        When the project has multiple line items, repeat the
        Date/Project/Hours/Billable/Overhead/Description block per line
        separated by a blank line. When no PM is resolved, the salutation
        falls back to 'Hi,'."""
        names = self._greeting_names(pms)
        greeting = (
            f"<p>{html.escape(names)},</p>" if names else "<p>Hi,</p>"
        )

        ask = (
            "<p>The following Contract Labor record has been submitted "
            "for review. When you have a moment, please review and reply "
            "with an approval with sub cost code and description or not "
            "approved.</p>"
        )

        parts: list[str] = [greeting, ask]
        for li in lines:
            parts.append(self._format_line_block(
                work_date=work_date,
                project_label=project_label,
                line=li,
            ))
        return "".join(parts)

    def _format_line_block(self, *, work_date: str, project_label: str, line) -> str:
        hours = self._fmt_hours(line.hours)
        billable = self._fmt_yes_no(line.is_billable, default_true=True)
        overhead = self._fmt_yes_no(line.is_overhead, default_true=False)
        desc = (line.description or "").strip() or "(no description)"
        # Use <br> within a <p> so the block renders as one paragraph
        # with line breaks — matches the plain-text feel of the template
        # while staying HTML-valid.
        return (
            "<p>"
            f"Date: {html.escape(work_date)}<br>"
            f"Project: {html.escape(project_label)}<br>"
            f"Hours: {html.escape(hours)}<br>"
            f"Is Billable: {html.escape(billable)}<br>"
            f"Is Overhead: {html.escape(overhead)}<br>"
            f"Description: {html.escape(desc)}"
            "</p>"
        )

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
