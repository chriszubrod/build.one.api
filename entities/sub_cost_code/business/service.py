# Python Standard Library Imports
from typing import List, Optional

# Third-party Imports

# Local Imports
from entities.sub_cost_code.persistence.repo import SubCostCodeRepository
from entities.sub_cost_code.business.model import SubCostCode


class SubCostCodeService:
    """
    Service for SubCostCode entity business operations.
    """

    def __init__(self, repo: Optional[SubCostCodeRepository] = None):
        """Initialize the SubCostCodeService."""
        self.repo = repo or SubCostCodeRepository()

    def create(self, *, tenant_id: int = 1, number: str, name: str, description: Optional[str] = None, cost_code_id: int, aliases: Optional[str] = None) -> SubCostCode:
        """
        Create a new sub cost code.
        """
        return self.repo.create(number=number, name=name, description=description, cost_code_id=cost_code_id, aliases=aliases)

    def read_all(self) -> List[SubCostCode]:
        """
        Read all sub cost codes.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[SubCostCode]:
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
        Read a sub cost code by number.
        """
        return self.repo.read_by_number(number=number)

    def read_by_alias(self, alias: str) -> Optional[SubCostCode]:
        """
        Read a sub cost code by alias value.
        """
        return self.repo.read_by_alias(alias=alias)

    def search_by_name(self, *, query: str, limit: int = 10) -> List[SubCostCode]:
        """
        Case-insensitive substring search against Name, Number, and Aliases.

        Results are ranked: exact-prefix matches come before substring
        matches. Pulls the full catalog via read_all() and filters in
        memory — SubCostCode is small (~500 rows) so this is cheaper than
        a dedicated LIKE sproc. Upgrade to a sproc if the table grows or
        ranked/fuzzy matching gets more complex.
        """
        q = (query or "").strip().lower()
        if not q:
            return []
        if limit <= 0:
            return []

        prefix_hits: List[SubCostCode] = []
        substring_hits: List[SubCostCode] = []

        for scc in self.repo.read_all():
            name = (scc.name or "").lower()
            number = (scc.number or "").lower()
            aliases_raw = (scc.aliases or "").lower()
            # Aliases are stored as a delimited string; split on common
            # separators so we can match each alias individually.
            alias_values = [
                a.strip()
                for delim in (",", ";", "|")
                for a in aliases_raw.split(delim)
                if a.strip()
            ] or ([aliases_raw.strip()] if aliases_raw.strip() else [])

            if (
                name.startswith(q)
                or number.startswith(q)
                or any(a.startswith(q) for a in alias_values)
            ):
                prefix_hits.append(scc)
            elif (
                q in name
                or q in number
                or q in aliases_raw
            ):
                substring_hits.append(scc)

            if len(prefix_hits) >= limit:
                break

        combined: List[SubCostCode] = (
            prefix_hits + substring_hits
        )[:limit]
        return combined

    def upsert(self, *, number: str, name: str, description: Optional[str] = None, cost_code_id: int, aliases: Optional[str] = None) -> SubCostCode:
        """
        Create or update a sub cost code by Number + CostCodeId.
        """
        return self.repo.upsert(number=number, name=name, description=description, cost_code_id=cost_code_id, aliases=aliases)

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
        aliases: str = None,
    ) -> Optional[SubCostCode]:
        """
        Update a sub cost code by public ID.
        """
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
            if aliases is not None:
                _sub_cost_code.aliases = aliases
        return self.repo.update_by_id(_sub_cost_code)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[SubCostCode]:
        """
        Delete a sub cost code by public ID.
        """
        _sub_cost_code = self.read_by_public_id(public_id=public_id)
        if not _sub_cost_code:
            return None
        return self.repo.delete_by_id(_sub_cost_code.id)
