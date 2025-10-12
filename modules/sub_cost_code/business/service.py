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
        number: str,
        name: str,
        description: Optional[str] = None,
        cost_code_id: str,
    ) -> SubCostCode:
        """
        Create a new sub cost code.
        """
        return self.repo.create(
            number=number,
            name=name,
            description=description,
            cost_code_id=cost_code_id
        )

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

    def update_by_public_id(self, public_id: str, sub_cost_code) -> Optional[SubCostCode]:
        """
        Update a sub cost code by ID.
        """
        _sub_cost_code = self.read_by_public_id(public_id=public_id)
        if _sub_cost_code:
            _sub_cost_code.row_version = sub_cost_code.row_version
            _sub_cost_code.number = sub_cost_code.number
            _sub_cost_code.name = sub_cost_code.name
            _sub_cost_code.description = sub_cost_code.description
            _sub_cost_code.cost_code_id = sub_cost_code.cost_code_id
        return self.repo.update_by_id(_sub_cost_code)

    def delete_by_public_id(self, public_id: str) -> Optional[SubCostCode]:
        """
        Soft delete a sub cost code by public ID.
        """
        _sub_cost_code = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_sub_cost_code.id)
