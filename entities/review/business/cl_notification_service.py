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
                continue  # overhead lines have no PM owner
            lines_by_project.setdefault(li.project_id, []).append(li)

        recipients_by_project = self._fetch_recipients(cl_id)

        # Union of project ids from BOTH sources — recipients sproc
        # surfaces every distinct project even when no PM exists.
        project_ids = set(lines_by_project.keys()) | set(recipients_by_project.keys())

        outbox = MsOutboxService()
        enqueued = 0
        for project_id in sorted(project_ids):
            lines = lines_by_project.get(project_id, [])
            if not lines:
                # Project surfaced by sproc but no current line items —
                # nothing to ask about. Skip.
                continue
            recip_rows = recipients_by_project.get(project_id, [])
            project_label = self._format_project_label(recip_rows, lines, project_id)

            to_addresses = self._build_to_addresses(recip_rows)
            subject = f"Review: {worker_name} – {project_label} – {work_date}"
            body = self._build_body(
                worker_name=worker_name,
                work_date=str(work_date),
                project_label=project_label,
                lines=lines,
                pm_first_name=self._first_pm_firstname(recip_rows),
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

    def _fetch_recipients(self, contract_labor_id: int) -> dict[int, list[dict]]:
        """Return {project_id: [{user_id, firstname, lastname, email}, ...]}.

        Projects with NO PM still appear with an empty list."""
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

        out: dict[int, list[dict]] = {}
        for r in rows:
            bucket = out.setdefault(r.ProjectId, [])
            # NULL user columns mark "project has no PM" — surface the
            # ProjectId by inserting an empty bucket; do NOT push a fake
            # recipient row.
            if r.UserId is None:
                continue
            bucket.append(
                {
                    "user_id": r.UserId,
                    "firstname": r.Firstname or "",
                    "lastname": r.Lastname or "",
                    "email": r.Email,
                    "project_name": getattr(r, "ProjectName", None),
                    "project_abbreviation": getattr(r, "ProjectAbbreviation", None),
                }
            )
            # Ensure the bucket exists even if no PM
            out.setdefault(r.ProjectId, bucket)
        # Make sure every ProjectId from the sproc shows up, even with no PMs
        for r in rows:
            out.setdefault(r.ProjectId, [])
        return out

    def _build_to_addresses(self, recip_rows: list[dict]) -> list[dict]:
        """Convert recipient dicts into the MS-outbox shape. Email-less
        rows are dropped. Empty list is valid per the relaxed draft-mode
        guard in the outbox worker."""
        addrs: list[dict] = []
        for r in recip_rows:
            email = (r.get("email") or "").strip()
            if not email:
                continue
            name = f"{r.get('firstname', '')} {r.get('lastname', '')}".strip() or None
            addrs.append({"email": email, "name": name})
        return addrs

    def _format_project_label(
        self,
        recip_rows: list[dict],
        lines: list,
        project_id: int,
    ) -> str:
        # Prefer abbreviation when set; fall back to project name; then to
        # "#<id>" so the subject is always populated.
        for r in recip_rows:
            abbr = (r.get("project_abbreviation") or "").strip()
            if abbr:
                return abbr
            name = (r.get("project_name") or "").strip()
            if name:
                return name
        # No recipient rows: try a Project lookup as a last resort.
        try:
            from entities.project.business.service import ProjectService

            p = ProjectService().read_by_id(id=project_id)
            if p:
                return (p.abbreviation or p.name or f"#{project_id}").strip()
        except Exception:
            pass
        return f"#{project_id}"

    def _first_pm_firstname(self, recip_rows: list[dict]) -> Optional[str]:
        for r in recip_rows:
            fn = (r.get("firstname") or "").strip()
            if fn:
                return fn
        return None

    def _build_body(
        self,
        *,
        worker_name: str,
        work_date: str,
        project_label: str,
        lines: list,
        pm_first_name: Optional[str],
    ) -> str:
        """Free-form HTML body — one paragraph per line item; ends with a
        plain request for the SubCostCode(s). Per Chris' product call:
        no structured fields, no table, just a written ask."""
        greeting = (
            f"<p>Hi {html.escape(pm_first_name)},</p>"
            if pm_first_name
            else "<p>Hi,</p>"
        )

        intro = (
            f"<p>{html.escape(worker_name)} worked on "
            f"<strong>{html.escape(project_label)}</strong> on "
            f"<strong>{html.escape(str(work_date))}</strong>. Please reply with "
            f"the SubCostCode(s) so we can mark the work ready for billing.</p>"
        )

        parts: list[str] = [greeting, intro]
        if len(lines) == 1:
            li = lines[0]
            hours = self._fmt_hours(li.hours)
            desc = (li.description or "").strip() or "(no description)"
            parts.append(
                f"<p><strong>{hours} hours</strong> — {html.escape(desc)}</p>"
            )
        else:
            for i, li in enumerate(lines, start=1):
                hours = self._fmt_hours(li.hours)
                desc = (li.description or "").strip() or "(no description)"
                parts.append(
                    f"<p><strong>Line {i} ({hours} hours)</strong> — "
                    f"{html.escape(desc)}</p>"
                )

        parts.append("<p>Thanks!</p>")
        return "".join(parts)

    @staticmethod
    def _fmt_hours(value) -> str:
        if value is None:
            return "0.00"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
