# Python Standard Library Imports
from typing import Optional, List

# Third-party Imports

# Local Imports
from services.cost_code.persistence.repo import CostCodeRepository
from services.cost_code.business.model import CostCode


class CostCodeService:
    """
    Service for CostCode entity business operations.
    """

    def __init__(self, repo: Optional[CostCodeRepository] = None):
        """Initialize the CostCodeService."""
        self.repo = repo or CostCodeRepository()

    def create(self, *, tenant_id: int = 1, number: str, name: str, description: Optional[str] = None) -> CostCode:
        """
        Create a new cost code.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            number: Cost code number
            name: Cost code name
            description: Cost code description (optional)
        """
        return self.repo.create(tenant_id=tenant_id, number=number, name=name, description=description)

    def read_all(self) -> List[CostCode]:
        """
        Read all cost codes.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[CostCode]:
        """
        Read a cost code by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[CostCode]:
        """
        Read a cost code by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_number(self, number: str) -> Optional[CostCode]:
        """
        Read a cost code by number.
        """
        return self.repo.read_by_number(number)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        number: str = None,
        name: str = None,
        description: str = None,
    ) -> Optional[CostCode]:
        """
        Update a cost code by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        _cost_code = self.read_by_public_id(public_id=public_id)
        if _cost_code:
            _cost_code.row_version = row_version
            if number is not None:
                _cost_code.number = number
            if name is not None:
                _cost_code.name = name
            if description is not None:
                _cost_code.description = description
        return self.repo.update_by_id(_cost_code)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[CostCode]:
        """
        Soft delete a cost code by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        _cost_code = self.read_by_public_id(public_id=public_id)
        if not _cost_code:
            return None
        return self.repo.delete_by_id(_cost_code.id)
