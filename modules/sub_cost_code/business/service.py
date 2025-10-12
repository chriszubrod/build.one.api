# Python Standard Library Imports
from typing import List, Optional

# Third-party Imports

# Local Imports
from modules.sub_cost_code.persistence.repo import SubCostCodeRepository
from modules.sub_cost_code.business.model import SubCostCode


class SubCostCodeService:
    """
    Service for SubCostCode entity business operations.
    """

    def __init__(self, repo: Optional[SubCostCodeRepository] = None):
        """Initialize the SubCostCodeService."""
        self.repo = repo or SubCostCodeRepository()

    def create(
        self,
        *,
        cost_code_public_id: str,
        number: str,
        name: str,
        description: Optional[str] = None,
    ) -> SubCostCode:
        """
        Create a new sub cost code.
        """
        return self.repo.create(
            cost_code_public_id=cost_code_public_id,
            number=number,
            name=name,
            description=description,
        )

    def read_all(self, cost_code_public_id: Optional[str] = None) -> List[SubCostCode]:
        """
        Read all sub cost codes.
        """
        if cost_code_public_id:
            return self.repo.read_by_cost_code_public_id(cost_code_public_id)
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

    def read_by_number(self, number: str, cost_code_public_id: str) -> Optional[SubCostCode]:
        """
        Read a sub cost code by number within a parent cost code.
        """
        return self.repo.read_by_number(number=number, cost_code_public_id=cost_code_public_id)

    def update_by_public_id(self, public_id: str, sub_cost_code) -> Optional[SubCostCode]:
        """
        Update a sub cost code by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = sub_cost_code.row_version
        existing.cost_code_public_id = sub_cost_code.cost_code_public_id
        existing.number = sub_cost_code.number
        existing.name = sub_cost_code.name
        existing.description = sub_cost_code.description
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[SubCostCode]:
        """
        Soft delete a sub cost code by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        return self.repo.delete_by_id(existing.id)
