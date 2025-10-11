# Python Standard Library Imports
from typing import Optional, List

# Third-party Imports

# Local Imports
from modules.cost_code.persistence.repo import CostCodeRepository
from modules.cost_code.business.model import CostCode


class CostCodeService:
    """
    Service for CostCode entity business operations.
    """

    def __init__(self, repo: Optional[CostCodeRepository] = None):
        """Initialize the CostCodeService."""
        self.repo = repo or CostCodeRepository()

    def create(self, *, number: str, name: str, description: Optional[str] = None) -> CostCode:
        """
        Create a new cost code.
        """
        return self.repo.create(number=number, name=name, description=description)

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

    def update_by_public_id(self, public_id: str, cost_code) -> Optional[CostCode]:
        """
        Update a cost code by public ID.
        """
        _cost_code = self.read_by_public_id(public_id=public_id)
        if _cost_code:
            _cost_code.row_version = cost_code.row_version
            _cost_code.number = cost_code.number
            _cost_code.name = cost_code.name
            _cost_code.description = cost_code.description
        return self.repo.update_by_id(_cost_code)

    def delete_by_public_id(self, public_id: str) -> Optional[CostCode]:
        """
        Soft delete a cost code by public ID.
        """
        _cost_code = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_cost_code.id)
