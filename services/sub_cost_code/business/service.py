# Python Standard Library Imports
from typing import List, Optional

# Third-party Imports

# Local Imports
from services.sub_cost_code.persistence.repo import SubCostCodeRepository
from services.sub_cost_code.business.model import SubCostCode


class SubCostCodeService:
    """
    Service for SubCostCode entity business operations.
    """

    def __init__(self, repo: Optional[SubCostCodeRepository] = None):
        """Initialize the SubCostCodeService."""
        self.repo = repo or SubCostCodeRepository()

    def create(self, *, tenant_id: int = 1, number: str, name: str, description: Optional[str] = None, cost_code_id: int) -> SubCostCode:
        """
        Create a new sub cost code.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            number: Sub cost code number
            name: Sub cost code name
            description: Sub cost code description (optional)
            cost_code_id: Parent cost code ID
        """
        return self.repo.create(tenant_id=tenant_id, number=number, name=name, description=description, cost_code_id=cost_code_id)

    def read_all(self) -> List[SubCostCode]:
        """
        Read all sub cost codes.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[SubCostCode]:
        """
        Read a sub cost code by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[SubCostCode]:
        """
        Read a sub cost code by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_number(self, number: str) -> Optional[SubCostCode]:
        """
        Read a sub cost code by number within a parent cost code.
        """
        return self.repo.read_by_number(number=number)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        number: str = None,
        name: str = None,
        description: str = None,
        cost_code_id: int = None,
    ) -> Optional[SubCostCode]:
        """
        Update a sub cost code by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        _sub_cost_code = self.read_by_public_id(public_id=public_id)
        if _sub_cost_code:
            _sub_cost_code.row_version = row_version
            if number is not None:
                _sub_cost_code.number = number
            if name is not None:
                _sub_cost_code.name = name
            if description is not None:
                _sub_cost_code.description = description
            if cost_code_id is not None:
                _sub_cost_code.cost_code_id = cost_code_id
        return self.repo.update_by_id(_sub_cost_code)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[SubCostCode]:
        """
        Soft delete a sub cost code by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        _sub_cost_code = self.read_by_public_id(public_id=public_id)
        if not _sub_cost_code:
            return None
        return self.repo.delete_by_id(_sub_cost_code.id)
