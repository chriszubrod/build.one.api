# Python Standard Library Imports
from typing import List, Optional

# Third-party Imports

# Local Imports
from entities.sub_cost_code.persistence.alias_repo import SubCostCodeAliasRepository
from entities.sub_cost_code.business.alias_model import SubCostCodeAlias


class SubCostCodeAliasService:
    """
    Service for SubCostCodeAlias entity business operations.
    """

    def __init__(self, repo: Optional[SubCostCodeAliasRepository] = None):
        """Initialize the SubCostCodeAliasService."""
        self.repo = repo or SubCostCodeAliasRepository()

    def create(self, *, sub_cost_code_id: int, alias: str, source: Optional[str] = None) -> SubCostCodeAlias:
        """
        Create a new sub cost code alias.

        Args:
            sub_cost_code_id: Parent sub cost code ID
            alias: The alias value
            source: Origin of the alias (e.g. 'manual', 'bill_agent')
        """
        return self.repo.create(sub_cost_code_id=sub_cost_code_id, alias=alias, source=source)

    def read_all(self) -> List[SubCostCodeAlias]:
        """
        Read all sub cost code aliases.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[SubCostCodeAlias]:
        """
        Read a sub cost code alias by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[SubCostCodeAlias]:
        """
        Read a sub cost code alias by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_sub_cost_code_id(self, sub_cost_code_id: int) -> List[SubCostCodeAlias]:
        """
        Read all aliases for a given sub cost code.
        """
        return self.repo.read_by_sub_cost_code_id(sub_cost_code_id)

    def read_by_alias(self, alias: str) -> Optional[SubCostCodeAlias]:
        """
        Read a sub cost code alias by alias value.
        """
        return self.repo.read_by_alias(alias=alias)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        row_version: str,
        sub_cost_code_id: int = None,
        alias: str = None,
        source: str = None,
    ) -> Optional[SubCostCodeAlias]:
        """
        Update a sub cost code alias by public ID.
        """
        _alias = self.read_by_public_id(public_id=public_id)
        if _alias:
            _alias.row_version = row_version
            if sub_cost_code_id is not None:
                _alias.sub_cost_code_id = sub_cost_code_id
            if alias is not None:
                _alias.alias = alias
            if source is not None:
                _alias.source = source
        return self.repo.update_by_id(_alias)

    def delete_by_public_id(self, public_id: str) -> Optional[SubCostCodeAlias]:
        """
        Delete a sub cost code alias by public ID.
        """
        _alias = self.read_by_public_id(public_id=public_id)
        if not _alias:
            return None
        return self.repo.delete_by_id(_alias.id)
