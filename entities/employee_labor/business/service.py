# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Local Imports
from entities.employee_labor.business.model import EmployeeLabor, VALID_STATUSES
from entities.employee_labor.persistence.repo import EmployeeLaborRepository
from shared.authz import current_user_id

# Status transitions allowed via update_status() — Phase 4 aggregation creates
# rows in 'pending_review'; admin/UI moves them to 'ready' when complete; the
# Invoice link transitions to 'invoiced' at complete_invoice time.
VALID_TRANSITIONS = {
    "pending_review": {"ready"},
    "ready": {"pending_review", "invoiced"},
    "invoiced": set(),  # terminal
}


def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid decimal value: {value!r}") from e


class EmployeeLaborService:
    def __init__(self, repo: Optional[EmployeeLaborRepository] = None):
        self.repo = repo or EmployeeLaborRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        employee_public_id: str,
        project_public_id: Optional[str] = None,
        work_date: str,
        billing_period_start: str,
        billing_period_end: str,
        total_hours: Union[str, Decimal, None] = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        total_amount: Union[str, Decimal, None] = None,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        status: str = "pending_review",
        source_time_entry_id: Optional[int] = None,
    ) -> EmployeeLabor:
        from entities.employee.business.service import EmployeeService
        from entities.project.business.service import ProjectService
        from entities.sub_cost_code.business.service import SubCostCodeService

        employee = EmployeeService().read_by_public_id(public_id=employee_public_id)
        if not employee:
            raise ValueError(f"Employee {employee_public_id!r} not found.")

        project_id = None
        if project_public_id:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project {project_public_id!r} not found.")
            project_id = int(project.id)

        sub_cost_code_id = None
        if sub_cost_code_public_id:
            scc = SubCostCodeService().read_by_public_id(public_id=sub_cost_code_public_id)
            if not scc:
                raise ValueError(f"SubCostCode {sub_cost_code_public_id!r} not found.")
            sub_cost_code_id = int(scc.id)

        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}. Must be one of {VALID_STATUSES}.")

        return self.repo.create(
            employee_id=int(employee.id),
            project_id=project_id,
            work_date=work_date,
            billing_period_start=billing_period_start,
            billing_period_end=billing_period_end,
            total_hours=_coerce_decimal(total_hours),
            hourly_rate=_coerce_decimal(hourly_rate),
            markup=_coerce_decimal(markup),
            total_amount=_coerce_decimal(total_amount),
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            status=status,
            source_time_entry_id=source_time_entry_id,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: int) -> Optional[EmployeeLabor]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[EmployeeLabor]:
        return self.repo.read_by_public_id(public_id)

    def read_by_billing_period(self, billing_period_start: str) -> list[EmployeeLabor]:
        return self.repo.read_by_billing_period(billing_period_start)

    def read_by_status(self, status: str, billing_period_start: Optional[str] = None) -> list[EmployeeLabor]:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}.")
        return self.repo.read_by_status(status, billing_period_start)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        project_public_id: Optional[str] = None,
        total_hours: Union[str, Decimal, None] = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        total_amount: Union[str, Decimal, None] = None,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        invoice_line_item_id: Optional[int] = None,
    ) -> Optional[EmployeeLabor]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if row_version is not None:
            existing.row_version = row_version

        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status {status!r}.")
            if existing.status and status != existing.status:
                allowed = VALID_TRANSITIONS.get(existing.status, set())
                if status not in allowed:
                    raise ValueError(
                        f"Cannot transition EmployeeLabor from {existing.status!r} to {status!r}."
                    )
            existing.status = status

        if project_public_id is not None:
            if project_public_id == "":
                existing.project_id = None
            else:
                from entities.project.business.service import ProjectService
                p = ProjectService().read_by_public_id(public_id=project_public_id)
                existing.project_id = int(p.id) if p else None

        if sub_cost_code_public_id is not None:
            if sub_cost_code_public_id == "":
                existing.sub_cost_code_id = None
            else:
                from entities.sub_cost_code.business.service import SubCostCodeService
                scc = SubCostCodeService().read_by_public_id(public_id=sub_cost_code_public_id)
                existing.sub_cost_code_id = int(scc.id) if scc else None

        if total_hours is not None:
            existing.total_hours = _coerce_decimal(total_hours)
        if hourly_rate is not None:
            existing.hourly_rate = _coerce_decimal(hourly_rate)
        if markup is not None:
            existing.markup = _coerce_decimal(markup)
        if total_amount is not None:
            existing.total_amount = _coerce_decimal(total_amount)
        if description is not None:
            existing.description = description if description != "" else None
        if invoice_line_item_id is not None:
            existing.invoice_line_item_id = invoice_line_item_id

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[EmployeeLabor]:
        logger = logging.getLogger(__name__)
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        logger.info(f"Deleting EmployeeLabor {public_id} (id={existing.id})")
        self.repo.delete_by_id(int(existing.id))
        return existing
