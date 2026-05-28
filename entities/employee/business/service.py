# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Third-party Imports

# Local Imports
from entities.employee.business.model import Employee
from entities.employee.persistence.repo import EmployeeRepository
from shared.authz import current_user_id


class EmployeeService:
    """Service for Employee entity business operations."""

    def __init__(self, repo: Optional[EmployeeRepository] = None):
        self.repo = repo or EmployeeRepository()

    @staticmethod
    def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
        """Always route financial values through Decimal(str(...)) — never float().

        Per memory: float round-trips silently corrupt currency amounts.
        """
        if value is None or value == "":
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Invalid decimal value: {value!r}") from e

    def create(
        self,
        *,
        tenant_id: int = 1,
        firstname: str,
        lastname: str,
        email: Optional[str] = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        is_active: bool = True,
        notes: Optional[str] = None,
    ) -> Employee:
        if not firstname or not firstname.strip():
            raise ValueError("Employee firstname is required.")
        if not lastname or not lastname.strip():
            raise ValueError("Employee lastname is required.")
        firstname = firstname.strip()
        lastname = lastname.strip()

        existing = self.repo.read_by_name(firstname=firstname, lastname=lastname)
        if existing:
            raise ValueError(f"Employee '{firstname} {lastname}' already exists.")

        return self.repo.create(
            firstname=firstname,
            lastname=lastname,
            email=email,
            hourly_rate=self._coerce_decimal(hourly_rate),
            markup=self._coerce_decimal(markup),
            is_active=is_active,
            notes=notes,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[Employee]:
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Employee]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Employee]:
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, firstname: str, lastname: str) -> Optional[Employee]:
        return self.repo.read_by_name(firstname=firstname, lastname=lastname)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        firstname: str = None,
        lastname: str = None,
        email: Optional[str] = None,
        hourly_rate: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        is_active: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> Optional[Employee]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if row_version is not None:
            existing.row_version = row_version

        # Duplicate-name check only when name is being changed
        if (firstname is not None or lastname is not None):
            new_first = firstname if firstname is not None else existing.firstname
            new_last = lastname if lastname is not None else existing.lastname
            if new_first != existing.firstname or new_last != existing.lastname:
                dup = self.read_by_name(firstname=new_first, lastname=new_last)
                if dup and dup.public_id != public_id:
                    raise ValueError(f"Employee '{new_first} {new_last}' already exists.")
            existing.firstname = new_first
            existing.lastname = new_last

        # Email: None means "not sent" → preserve; "" means user cleared the field → store NULL
        if email is not None:
            existing.email = email if email != "" else None

        if hourly_rate is not None:
            existing.hourly_rate = self._coerce_decimal(hourly_rate)
        if markup is not None:
            existing.markup = self._coerce_decimal(markup)
        if is_active is not None:
            existing.is_active = is_active
        if notes is not None:
            existing.notes = notes if notes != "" else None

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Employee]:
        logger = logging.getLogger(__name__)
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        logger.info(f"Soft deleting employee {public_id} (id={existing.id})")
        return self.repo.soft_delete_by_public_id(public_id=public_id)
