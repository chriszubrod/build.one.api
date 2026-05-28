# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Local Imports
from entities.employee_project_rate.business.model import EmployeeProjectRate
from entities.employee_project_rate.persistence.repo import EmployeeProjectRateRepository
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


def _resolve_employee_id(public_id: str) -> int:
    from entities.employee.business.service import EmployeeService
    e = EmployeeService().read_by_public_id(public_id=public_id)
    if not e:
        raise ValueError(f"Employee with public_id {public_id!r} not found.")
    return int(e.id)


def _resolve_project_id(public_id: str) -> int:
    from entities.project.business.service import ProjectService
    p = ProjectService().read_by_public_id(public_id=public_id)
    if not p:
        raise ValueError(f"Project with public_id {public_id!r} not found.")
    return int(p.id)


class EmployeeProjectRateService:
    def __init__(self, repo: Optional[EmployeeProjectRateRepository] = None):
        self.repo = repo or EmployeeProjectRateRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        employee_public_id: str,
        project_public_id: str,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        notes: Optional[str] = None,
    ) -> EmployeeProjectRate:
        employee_id = _resolve_employee_id(employee_public_id)
        project_id = _resolve_project_id(project_public_id)

        existing = [r for r in self.repo.read_by_employee_id(employee_id) if r.project_id == project_id]
        if existing:
            raise ValueError(
                f"Override row already exists for this Employee + Project pair. "
                f"Update the existing row (public_id={existing[0].public_id}) instead."
            )

        return self.repo.create(
            employee_id=employee_id,
            project_id=project_id,
            hourly_rate=_coerce_decimal(hourly_rate),
            markup=_coerce_decimal(markup),
            notes=notes,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: int) -> Optional[EmployeeProjectRate]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[EmployeeProjectRate]:
        return self.repo.read_by_public_id(public_id)

    def read_by_employee_id(self, employee_id: int) -> list[EmployeeProjectRate]:
        return self.repo.read_by_employee_id(employee_id)

    def read_by_project_id(self, project_id: int) -> list[EmployeeProjectRate]:
        return self.repo.read_by_project_id(project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        notes: Optional[str] = None,
    ) -> Optional[EmployeeProjectRate]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        if row_version is not None:
            existing.row_version = row_version
        if hourly_rate is not None:
            existing.hourly_rate = _coerce_decimal(hourly_rate)
        if markup is not None:
            existing.markup = _coerce_decimal(markup)
        if notes is not None:
            existing.notes = notes if notes != "" else None
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[EmployeeProjectRate]:
        logger = logging.getLogger(__name__)
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        logger.info(f"Soft deleting EmployeeProjectRate {public_id} (id={existing.id})")
        return self.repo.soft_delete_by_public_id(public_id=public_id)

    def read_effective_rate(self, *, employee_id: int, project_id: int) -> dict:
        return self.repo.read_effective_rate(employee_id=employee_id, project_id=project_id)
