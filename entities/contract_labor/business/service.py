# Python Standard Library Imports
import logging
from typing import Optional, Tuple
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.contract_labor.business.model import ContractLabor
from entities.contract_labor.persistence.repo import ContractLaborRepository
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from shared.access import (
    EntityNotAccessibleError,
    assert_can_access_project,
)
from shared.authz import current_user_id, current_is_system_admin

logger = logging.getLogger(__name__)


class ContractLaborService:
    """
    Service for ContractLabor entity business operations.
    """

    def __init__(self, repo: Optional[ContractLaborRepository] = None):
        """Initialize the ContractLaborService."""
        self.repo = repo or ContractLaborRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        vendor_public_id: str,
        employee_name: str,
        work_date: str,
        total_hours: Decimal,
        project_public_id: Optional[str] = None,
        job_name: Optional[str] = None,
        time_in: Optional[str] = None,
        time_out: Optional[str] = None,
        break_time: Optional[str] = None,
        regular_hours: Optional[Decimal] = None,
        overtime_hours: Optional[Decimal] = None,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        status: str = "pending_review",
        import_batch_id: Optional[str] = None,
        source_file: Optional[str] = None,
        source_row: Optional[int] = None,
    ) -> ContractLabor:
        """
        Create a new contract labor entry.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Validate and resolve vendor
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
        vendor_id = vendor.id
        
        # Resolve project if provided
        project_id = None
        if project_public_id:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            project_id = project.id
        
        # Validate SubCostCode if provided
        if sub_cost_code_id is not None:
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
        
        # Calculate billing period
        billing_period_start = ContractLabor.calculate_billing_period_start(work_date)
        
        # Calculate total amount if rate is provided
        total_amount = None
        if hourly_rate is not None:
            base_amount = total_hours * hourly_rate
            if markup is not None:
                total_amount = base_amount * (Decimal("1") + markup)
            else:
                total_amount = base_amount
        
        return self.repo.create(
            vendor_id=vendor_id,
            project_id=project_id,
            employee_name=employee_name,
            job_name=job_name,
            work_date=work_date,
            time_in=time_in,
            time_out=time_out,
            break_time=break_time,
            regular_hours=regular_hours,
            overtime_hours=overtime_hours,
            total_hours=total_hours,
            hourly_rate=hourly_rate,
            markup=markup,
            total_amount=total_amount,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            billing_period_start=billing_period_start,
            status=status,
            import_batch_id=import_batch_id,
            source_file=source_file,
            source_row=source_row,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[ContractLabor]:
        """Read contract labor entries, scoped by UserProject for non-admins."""
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def _filter_accessible(self, rows: list[ContractLabor]) -> list[ContractLabor]:
        """Drop rows whose project is not accessible to the current actor.

        Admins / unauthenticated callers bypass via assert_can_access_project's
        own short-circuit; for non-admin users this filters per-row by project
        membership. Rows with no project_id (un-matched job column) are kept —
        they don't leak project data.
        """
        if current_is_system_admin.get() or current_user_id.get() is None:
            return rows
        accessible: list[ContractLabor] = []
        for row in rows:
            if row.project_id is None:
                accessible.append(row)
                continue
            try:
                assert_can_access_project(row.project_id)
                accessible.append(row)
            except EntityNotAccessibleError:
                continue
        return accessible

    def read_by_id(self, id: int) -> Optional[ContractLabor]:
        """
        Read a contract labor entry by ID.
        """
        cl = self.repo.read_by_id(id)
        if cl is None:
            return None
        if cl.project_id is not None:
            assert_can_access_project(cl.project_id)
        return cl

    def read_by_public_id(self, public_id: str) -> Optional[ContractLabor]:
        """
        Read a contract labor entry by public ID.
        """
        cl = self.repo.read_by_public_id(public_id)
        if cl is None:
            return None
        if cl.project_id is not None:
            assert_can_access_project(cl.project_id)
        return cl

    def read_by_vendor_id(self, vendor_id: int) -> list[ContractLabor]:
        """
        Read all contract labor entries for a specific vendor.
        """
        return self._filter_accessible(self.repo.read_by_vendor_id(vendor_id))

    def read_by_vendor_public_id(self, vendor_public_id: str) -> list[ContractLabor]:
        """
        Read all contract labor entries for a vendor by public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            return []
        return self._filter_accessible(self.repo.read_by_vendor_id(vendor_id=vendor.id))

    def read_by_billing_period(self, billing_period_start: str) -> list[ContractLabor]:
        """
        Read all contract labor entries for a specific billing period.
        """
        return self._filter_accessible(self.repo.read_by_billing_period(billing_period_start))

    def read_by_status(self, status: str, billing_period_start: Optional[str] = None) -> list[ContractLabor]:
        """
        Read all contract labor entries with a specific status, optionally filtered by billing period.
        """
        return self._filter_accessible(
            self.repo.read_by_status(status, billing_period_start=billing_period_start)
        )

    def read_by_import_batch_id(self, import_batch_id: str) -> list[ContractLabor]:
        """
        Read all contract labor entries from a specific import batch.
        """
        return self._filter_accessible(self.repo.read_by_import_batch_id(import_batch_id))

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        billing_period_start: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_by: str = "WorkDate",
        sort_direction: str = "DESC",
    ) -> list[ContractLabor]:
        """Read contract labor with pagination + filters, scoped by UserProject."""
        return self.repo.read_paginated(
            page_number=page_number,
            page_size=page_size,
            search_term=search_term,
            vendor_id=vendor_id,
            project_id=project_id,
            status=status,
            billing_period_start=billing_period_start,
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_direction=sort_direction,
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        billing_period_start: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        """Count contract labor matching filter criteria, scoped by UserProject.
        """
        return self.repo.count(
            search_term=search_term,
            vendor_id=vendor_id,
            project_id=project_id,
            status=status,
            billing_period_start=billing_period_start,
            start_date=start_date,
            end_date=end_date,
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        vendor_public_id: str = None,
        project_public_id: str = None,
        employee_name: str = None,
        work_date: str = None,
        time_in: str = None,
        time_out: str = None,
        break_time: str = None,
        regular_hours: float = None,
        overtime_hours: float = None,
        total_hours: float = None,
        hourly_rate: float = None,
        markup: float = None,
        sub_cost_code_id: int = None,
        description: str = None,
        status: str = None,
    ) -> Optional[ContractLabor]:
        """
        Update a contract labor entry by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        # Update row version
        existing.row_version = row_version
        
        # Resolve vendor if provided
        if vendor_public_id is not None:
            vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
            if not vendor:
                raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
            existing.vendor_id = vendor.id
        
        # Resolve project if provided
        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            existing.project_id = project.id
        
        # Validate SubCostCode if provided
        if sub_cost_code_id is not None:
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
            existing.sub_cost_code_id = sub_cost_code_id
        
        # Update simple fields
        if employee_name is not None:
            existing.employee_name = employee_name
        if work_date is not None:
            existing.work_date = work_date
            # Recalculate billing period if work date changed
            existing.billing_period_start = ContractLabor.calculate_billing_period_start(work_date)
        if time_in is not None:
            existing.time_in = time_in
        if time_out is not None:
            existing.time_out = time_out
        if break_time is not None:
            existing.break_time = break_time
        if regular_hours is not None:
            existing.regular_hours = Decimal(str(regular_hours))
        if overtime_hours is not None:
            existing.overtime_hours = Decimal(str(overtime_hours))
        if total_hours is not None:
            existing.total_hours = Decimal(str(total_hours))
        if hourly_rate is not None:
            existing.hourly_rate = Decimal(str(hourly_rate))
        if markup is not None:
            existing.markup = Decimal(str(markup))
        if description is not None:
            existing.description = description
        if status is not None:
            existing.status = status
        
        # Recalculate total amount
        if existing.hourly_rate is not None and existing.total_hours is not None:
            base_amount = existing.total_hours * existing.hourly_rate
            if existing.markup is not None:
                existing.total_amount = base_amount * (Decimal("1") + existing.markup)
            else:
                existing.total_amount = base_amount
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ContractLabor]:
        """
        Delete a contract labor entry by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None

    def apply_reviewer_decision(
        self,
        *,
        contract_labor_public_id: str,
        project_public_id: str,
        decision: str,
        reviewer_email: str,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        raw_reply_text: Optional[str] = None,
        reviewer_email_message_public_id: Optional[str] = None,
    ) -> dict:
        """Apply a Project Manager / Owner's emailed review decision to a CL row.

        Mirrors BillService.apply_reviewer_decision (entities/bill/
        business/service.py:1008) with `bill_id → contract_labor_id`.
        Each invocation:
          1. Validates the CL is still `pending_review` (mirrors Bill's
             draft guard; Unit 2's find sproc deliberately doesn't filter
             on status so we can surface a specific error here).
          2. Authorizes `reviewer_email` against PM/Owner UserProject
             recipients on the matched `project_public_id` (via
             dbo.ResolveContractLaborReviewRecipientsPerProject — same
             sproc cl_notification_service uses to address the outbound).
          3. On approval: updates each ContractLaborLineItem WHERE
             ProjectId = matched project with the supplied SCC +
             description (read-modify-write to preserve all other line
             fields).
          4. Always: inserts a new Review row (insert-only audit trail)
             with the target ReviewStatus + raw_reply_text as comments +
             email_message_id link.

        CL.Status stays at 'pending_review' either way (per design Q3):
        the PM-supplied SCC + description is applied, AP still has to
        enter rate/markup before mark_as_ready.

        decision ∈ {'approved', 'rejected'} — same vocab as Bill's.

        Returns: dict with decision_applied, review_status name,
        reviewer_user_id, the CL public_id, the matched project_id.
        """
        from entities.contract_labor.persistence.line_item_repo import (
            ContractLaborLineItemRepository,
        )
        from entities.review.persistence.repo import ReviewRepository
        from entities.review_status.business.service import ReviewStatusService
        from entities.sub_cost_code.business.service import SubCostCodeService
        from shared.database import call_procedure, get_connection

        if decision not in ('approved', 'rejected'):
            raise ValueError(
                f"decision must be 'approved' or 'rejected'; got '{decision}'"
            )

        cl = self.read_by_public_id(public_id=contract_labor_public_id)
        if cl is None or cl.id is None:
            raise ValueError(
                f"ContractLabor with public_id '{contract_labor_public_id}' not found."
            )

        # Status guard — surfaced here so the agent can produce a
        # specific human-readable failure. Reviewer decisions can be
        # applied while the CL is in an editable-by-reviewer state:
        # - `pending_review` (legacy pre-vocab-shim state)
        # - `submitted` (post-shim: CL flipped on initial Submit)
        # Anything past that (`ready` / `billed`) is closed to reviewer
        # replies. Mirrors Bill's draft guard at service.py:1062.
        if cl.status not in ('pending_review', 'submitted'):
            raise ValueError(
                f"ContractLabor {contract_labor_public_id} is no longer "
                f"reviewable (current status: {cl.status!r}); reviewer "
                f"decisions cannot be applied. The human must edit directly."
            )

        # Resolve target project from the supplied public_id; needed to
        # filter the line-item update + the authz check.
        project = ProjectService().read_by_public_id(public_id=project_public_id)
        if project is None or project.id is None:
            raise ValueError(
                f"Project with public_id '{project_public_id}' not found."
            )

        # ── Authz: reviewer_email must be a PM or Owner on this project ──
        # ResolveContractLaborReviewRecipientsPerProject returns one row
        # per (project, PM/Owner) tuple across ALL projects the CL spans;
        # filter to the matched project_id then case-insensitive email match.
        with get_connection() as conn:
            cur = conn.cursor()
            call_procedure(
                cursor=cur,
                name='ResolveContractLaborReviewRecipientsPerProject',
                params={'ContractLaborId': cl.id},
            )
            recipients = cur.fetchall()

        normalized_email = (reviewer_email or '').strip().lower()
        reviewer_user_id: Optional[int] = None
        for r in recipients:
            if int(r.ProjectId or 0) != int(project.id):
                continue
            row_email = (getattr(r, 'Email', None) or '').strip().lower()
            if row_email and row_email == normalized_email:
                reviewer_user_id = int(r.UserId) if r.UserId is not None else None
                break

        if reviewer_user_id is None:
            raise ValueError(
                f"Sender '{reviewer_email}' is not an authorized reviewer for "
                f"ContractLabor {contract_labor_public_id} on project "
                f"{project_public_id} (must be Project Manager or Owner)."
            )

        # ── Approval prework: resolve SCC + load line items ──────────
        # Validate the SCC + collect target line items BEFORE writing the
        # Review row. We want the SCC-not-found / no-matching-lines errors
        # to abort EARLY (no audit row written for a decision the system
        # can't apply). Once the Review row exists, partial line-item
        # failures are reported but the audit intent is preserved.
        scc_id: Optional[int] = None
        matched_lines: list = []
        if decision == 'approved':
            if not sub_cost_code_public_id:
                raise ValueError(
                    "sub_cost_code_public_id is required when decision='approved'."
                )
            scc = SubCostCodeService().read_by_public_id(public_id=sub_cost_code_public_id)
            if scc is None or scc.id is None:
                raise ValueError(
                    f"SubCostCode with public_id '{sub_cost_code_public_id}' not found."
                )
            scc_id = int(scc.id)

            li_repo = ContractLaborLineItemRepository()
            line_items = li_repo.read_by_contract_labor_id(contract_labor_id=cl.id)
            # Filter to the matched project. Overhead lines (IsOverhead=1,
            # ProjectId NULL) are silently excluded — the PM is reviewing
            # a specific project, not the worker's overhead allocation.
            # The agent's docstring covers this so the omission is auditable.
            matched_lines = [li for li in line_items if li.project_id == project.id]
            if not matched_lines:
                raise ValueError(
                    f"ContractLabor {contract_labor_public_id} has no line items "
                    f"on project {project_public_id} to apply the SCC to."
                )

        # ── Resolve target ReviewStatus + source EmailMessage ───────
        # Approved → first IsFinal AND NOT IsDeclined; rejected → first
        # IsDeclined. Mirrors Bill at service.py:1129-1148. PM approval
        # IS the approval per the multi-reviewer locked semantics.
        comments = (raw_reply_text or '').strip() or None
        review_statuses = ReviewStatusService().read_all()
        if decision == 'approved':
            target = next(
                (s for s in review_statuses if s.is_final and not s.is_declined),
                None,
            )
            if target is None:
                raise ValueError(
                    "No terminal non-declined ReviewStatus configured "
                    "(expected one with IsFinal=true AND IsDeclined=false)."
                )
        else:  # rejected
            target = next(
                (s for s in review_statuses if s.is_declined),
                None,
            )
            if target is None:
                raise ValueError(
                    "No declined ReviewStatus configured (expected one with IsDeclined=true)."
                )

        email_message_id: Optional[int] = None
        if reviewer_email_message_public_id:
            from entities.email_message.business.service import EmailMessageService
            em = EmailMessageService().read_by_public_id(
                public_id=reviewer_email_message_public_id,
            )
            if em is not None:
                email_message_id = em.id

        # ── Write Review row FIRST so audit intent is always captured ─
        # /code-review Unit 3 (bugs_correctness/errors_lifecycle): if we
        # wrote line items first and a mid-loop row-version conflict
        # raised, the audit trail would be silently empty while half the
        # lines were mutated. Reversing the order means partial line-item
        # failures still leave the Review row in place so AP can see what
        # was attempted + reconcile the split state from the React queue.
        new_review = ReviewRepository().create(
            review_status_id=target.id,
            user_id=reviewer_user_id,
            comments=comments,
            bill_id=None,
            expense_id=None,
            bill_credit_id=None,
            invoice_id=None,
            contract_labor_id=cl.id,
            email_message_id=email_message_id,
            created_by_user_id=reviewer_user_id,
        )

        # ── Approval only: update each matched line item ─────────────
        # Per-line try/except so a single row-version conflict on one
        # line doesn't silently abort the rest. Collect failures into a
        # structured list and raise at the end if any. The Review row is
        # already written (above) so the audit trail records the
        # decision regardless of how many lines actually applied.
        #
        # NOTE: when `description` is supplied on an approval that
        # matches multiple line items on the same project, every
        # matched line's description is overwritten with the same
        # string — this flattens distinct per-line descriptions. The
        # tool prompt documents this; agents pass `description` only
        # when the PM's intent is clearly project-wide.
        if decision == 'approved' and matched_lines:
            line_failures: list[str] = []
            for li in matched_lines:
                try:
                    li_repo.update_by_id(
                        id=li.id,
                        row_version=li.row_version_bytes,
                        line_date=li.line_date,
                        project_id=li.project_id,
                        sub_cost_code_id=scc_id,
                        description=description if description is not None else li.description,
                        hours=li.hours,
                        rate=li.rate,
                        markup=li.markup,
                        price=li.price,
                        # Use the project's canonical `is not False`
                        # phrasing per CLAUDE.md project_conventions
                        # (handles None → default-billable=True correctly).
                        is_billable=(li.is_billable is not False),
                        is_overhead=bool(li.is_overhead) if li.is_overhead is not None else False,
                        bill_line_item_id=li.bill_line_item_id,
                    )
                except Exception as line_error:
                    line_failures.append(
                        f"line_item_id={li.id}: {type(line_error).__name__}: {line_error}"
                    )
                    logger.warning(
                        "apply_reviewer_decision partial-update on CL %s line %s: %s",
                        contract_labor_public_id, li.id, line_error,
                    )
            if line_failures:
                raise ValueError(
                    f"ContractLabor {contract_labor_public_id} apply partial-failure: "
                    f"Review row was created (id={new_review.id}) but "
                    f"{len(line_failures)}/{len(matched_lines)} line items failed "
                    f"to update. AP must reconcile via React queue. "
                    f"Failures: {'; '.join(line_failures)}"
                )

        # ── Auto-mirror Review → ContractLabor.status ──────────────
        # When the new Review row lands at an approved final state
        # (IsFinal=true, IsDeclined=false), flip CL.Status pending_review
        # → ready so Generate Bills picks it up. Mirrors
        # ReviewService.create() lines 95-111 (the canonical hook fired
        # by the React /advance/review path); replicated here because
        # we go through ReviewRepository.create directly (Bill mirror
        # pattern for created_by_user_id=reviewer attribution).
        # Failure-isolated: log + continue; the Review row is the
        # authoritative audit and stands on its own.
        if new_review.status_is_final and not new_review.status_is_declined:
            try:
                self.mark_as_ready_via_review_approval(contract_labor_id=cl.id)
            except Exception:
                logger.exception(
                    "Failed to mirror ContractLabor.status after Review approval "
                    "via apply_reviewer_decision (cl_id=%s, review_id=%s)",
                    cl.id, new_review.id,
                )

        rs = ReviewStatusService().read_by_id(id=new_review.review_status_id) if new_review.review_status_id else None
        new_status_name = rs.name if rs else None

        return {
            'decision_applied': decision,
            'review_status': new_status_name,
            'reviewer_user_id': reviewer_user_id,
            'contract_labor_public_id': contract_labor_public_id,
            'project_public_id': project_public_id,
            'project_id': project.id,
            'contract_labor_id': cl.id,
        }

    def find_for_reviewer_reply(
        self,
        *,
        conversation_id: Optional[str] = None,
        worker_hint: Optional[str] = None,
        project_hint: Optional[str] = None,
        work_date_hint: Optional[str] = None,
    ) -> Optional[dict]:
        """Bind a PM/Owner reply email back to its (ContractLabor,
        Project) pair via the outbound notification's ConversationId.

        Used by the contract_labor_specialist agent during the email_
        specialist Step 1bx reviewer-reply branch detection. Returns
        None when no unambiguous match exists; the caller then flows
        the reply through flagged_needs_review.
        """
        return self.repo.find_for_reviewer_reply(
            conversation_id=conversation_id,
            worker_hint=worker_hint,
            project_hint=project_hint,
            work_date_hint=work_date_hint,
        )

    def read_distinct_billing_periods(self) -> list[str]:
        """Return distinct BillingPeriodStart values (YYYY-MM-DD), most-recent first."""
        return self.repo.read_distinct_billing_periods()

    def get_last_rate_for_vendor(self, vendor_public_id: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Get the last used hourly rate and markup for a vendor (for carry-forward).
        
        Args:
            vendor_public_id: Public ID of the vendor
            
        Returns:
            Tuple of (hourly_rate, markup)
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            return (None, None)
        return self.repo.get_last_rate_for_vendor(vendor_id=vendor.id)

    def mark_as_ready(self, public_id: str) -> Optional[ContractLabor]:
        """
        Mark a contract labor entry as ready for billing.
        Validates that all required fields are set.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        # Validate required fields
        if not existing.vendor_id:
            raise ValueError("Vendor is required before marking as ready.")
        if not existing.sub_cost_code_id:
            raise ValueError("SubCostCode is required before marking as ready.")
        if not existing.hourly_rate:
            raise ValueError("Hourly rate is required before marking as ready.")
        if not existing.total_hours:
            raise ValueError("Total hours is required before marking as ready.")

        existing.status = "ready"
        return self.repo.update_by_id(existing)

    def mark_as_ready_via_review_approval(self, *, contract_labor_id: int) -> Optional[ContractLabor]:
        """
        Flip status to 'ready' after Review reaches an approved (IsFinal=1,
        IsDeclined=0) state. Bypasses the legacy mark_as_ready validation —
        in the Review workflow, the reviewer's approval IS the validation.
        Parent-row fields like sub_cost_code_id/hourly_rate are no longer
        populated (line items carry those now). Looks up by internal id
        since the caller is ReviewService which has the FK at hand.
        """
        existing = self.repo.read_by_id(contract_labor_id)
        if not existing:
            return None
        if existing.status == "ready" or existing.status == "billed":
            return existing  # idempotent; no-op if already advanced
        existing.status = "ready"
        return self.repo.update_by_id(existing)

    def bulk_mark_as_ready(self, public_ids: list[str]) -> dict:
        """
        Mark multiple contract labor entries as ready for billing.
        
        Returns:
            Dict with success count, error count, and error details
        """
        success_count = 0
        errors = []
        
        for public_id in public_ids:
            try:
                result = self.mark_as_ready(public_id=public_id)
                if result:
                    success_count += 1
                else:
                    errors.append({"public_id": public_id, "error": "Entry not found"})
            except ValueError as e:
                errors.append({"public_id": public_id, "error": str(e)})
            except Exception as e:
                logger.error(f"Error marking {public_id} as ready: {e}")
                errors.append({"public_id": public_id, "error": str(e)})
        
        return {
            "success_count": success_count,
            "error_count": len(errors),
            "errors": errors,
        }

    def bulk_delete(self, public_ids: list[str]) -> dict:
        """
        Delete multiple contract labor entries.
        Only entries with status 'pending_review' can be deleted.
        
        Returns:
            Dict with success count, error count, and error details
        """
        success_count = 0
        errors = []
        
        for public_id in public_ids:
            try:
                # Get the entry first to check status
                entry = self.read_by_public_id(public_id=public_id)
                if not entry:
                    errors.append({"public_id": public_id, "error": "Entry not found"})
                    continue
                
                # Only allow deletion of pending_review entries
                if entry.status == "billed":
                    errors.append({"public_id": public_id, "error": "Cannot delete billed entries"})
                    continue
                
                # Delete the entry
                result = self.repo.delete_by_public_id(public_id=public_id)
                if result:
                    success_count += 1
                else:
                    errors.append({"public_id": public_id, "error": "Delete failed"})
            except Exception as e:
                logger.error(f"Error deleting {public_id}: {e}")
                errors.append({"public_id": public_id, "error": str(e)})
        
        return {
            "success_count": success_count,
            "error_count": len(errors),
            "errors": errors,
        }

    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
        bill_line_item_id: Optional[int] = None,
    ) -> int:
        """
        Bulk update status for multiple contract labor entries.
        
        Returns:
            Number of rows updated
        """
        return self.repo.bulk_update_status(
            ids=ids,
            status=status,
            bill_line_item_id=bill_line_item_id,
        )

    def get_ready_entries_grouped_for_billing(
        self,
        billing_period_start: str,
    ) -> dict:
        """
        Get all 'ready' entries for a billing period, grouped by vendor,
        then by SubCostCode+Project combination.
        
        This is used to prepare data for bill creation.
        
        Returns:
            Dict structure:
            {
                vendor_id: {
                    (sub_cost_code_id, project_id): [ContractLabor, ...],
                    ...
                },
                ...
            }
        """
        entries = self.repo.read_by_billing_period(billing_period_start)
        ready_entries = [e for e in entries if e.status == "ready"]
        
        grouped = {}
        for entry in ready_entries:
            vendor_id = entry.vendor_id
            if vendor_id not in grouped:
                grouped[vendor_id] = {}
            
            key = (entry.sub_cost_code_id, entry.project_id)
            if key not in grouped[vendor_id]:
                grouped[vendor_id][key] = []
            
            grouped[vendor_id][key].append(entry)
        
        return grouped
