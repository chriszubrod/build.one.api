# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Local Imports
from entities.employee_labor_line_item.business.model import EmployeeLaborLineItem
from entities.employee_labor_line_item.persistence.repo import EmployeeLaborLineItemRepository
from shared.authz import current_user_id


def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid decimal value: {value!r}") from e


class EmployeeLaborLineItemService:
    def __init__(self, repo: Optional[EmployeeLaborLineItemRepository] = None):
        self.repo = repo or EmployeeLaborLineItemRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        employee_labor_public_id: str,
        line_date: Optional[str] = None,
        project_public_id: Optional[str] = None,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        hours: Union[str, Decimal, None] = None,
        rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        price: Union[str, Decimal, None] = None,
        is_billable: bool = True,
        is_overhead: bool = False,
        invoice_line_item_id: Optional[int] = None,
    ) -> EmployeeLaborLineItem:
        from entities.employee_labor.business.service import EmployeeLaborService
        from entities.project.business.service import ProjectService
        from entities.sub_cost_code.business.service import SubCostCodeService

        parent = EmployeeLaborService().read_by_public_id(public_id=employee_labor_public_id)
        if not parent:
            raise ValueError(f"EmployeeLabor {employee_labor_public_id!r} not found.")

        project_id = None
        if project_public_id:
            p = ProjectService().read_by_public_id(public_id=project_public_id)
            project_id = int(p.id) if p else None

        sub_cost_code_id = None
        if sub_cost_code_public_id:
            scc = SubCostCodeService().read_by_public_id(public_id=sub_cost_code_public_id)
            sub_cost_code_id = int(scc.id) if scc else None

        return self.repo.create(
            employee_labor_id=int(parent.id),
            line_date=line_date,
            project_id=project_id,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            hours=_coerce_decimal(hours),
            rate=_coerce_decimal(rate),
            markup=_coerce_decimal(markup),
            price=_coerce_decimal(price),
            is_billable=is_billable,
            is_overhead=is_overhead,
            invoice_line_item_id=invoice_line_item_id,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: int) -> Optional[EmployeeLaborLineItem]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[EmployeeLaborLineItem]:
        return self.repo.read_by_public_id(public_id)

    def read_by_employee_labor_id(self, employee_labor_id: int) -> list[EmployeeLaborLineItem]:
        return self.repo.read_by_employee_labor_id(employee_labor_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        line_date: Optional[str] = None,
        project_public_id: Optional[str] = None,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        hours: Union[str, Decimal, None] = None,
        rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        price: Union[str, Decimal, None] = None,
        is_billable: Optional[bool] = None,
        is_overhead: Optional[bool] = None,
        invoice_line_item_id: Optional[int] = None,
    ) -> Optional[EmployeeLaborLineItem]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if row_version is not None:
            existing.row_version = row_version

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

        if line_date is not None:
            existing.line_date = line_date
        if description is not None:
            existing.description = description if description != "" else None
        if hours is not None:
            existing.hours = _coerce_decimal(hours)
        if rate is not None:
            existing.rate = _coerce_decimal(rate)
        if markup is not None:
            existing.markup = _coerce_decimal(markup)
        if price is not None:
            existing.price = _coerce_decimal(price)
        if is_billable is not None:
            existing.is_billable = is_billable
        if is_overhead is not None:
            existing.is_overhead = is_overhead
        if invoice_line_item_id is not None:
            existing.invoice_line_item_id = invoice_line_item_id

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[EmployeeLaborLineItem]:
        logger = logging.getLogger(__name__)
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        logger.info(f"Deleting EmployeeLaborLineItem {public_id} (id={existing.id})")
        self.repo.delete_by_id(int(existing.id))
        return existing
