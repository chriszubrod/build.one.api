# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.project.business.model import Project
from entities.project.persistence.repo import ProjectRepository


class ProjectService:
    """
    Service for Project entity business operations.
    """

    def __init__(self, repo: Optional[ProjectRepository] = None):
        """Initialize the ProjectService."""
        self.repo = repo or ProjectRepository()

    def create(self, *, tenant_id: int = 1, name: str, description: str, status: str, customer_id: Optional[int] = None, abbreviation: Optional[str] = None) -> Project:
        """
        Create a new project.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Project name
            description: Project description
            status: Project status
            customer_id: Optional customer ID
            abbreviation: Optional project abbreviation
        """
        return self.repo.create(tenant_id=tenant_id, name=name, description=description, status=status, customer_id=customer_id, abbreviation=abbreviation)

    def read_all(self) -> list[Project]:
        """
        Read all projects.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Project]:
        """
        Read a project by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Project]:
        """
        Read a project by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Project]:
        """
        Read a project by name.
        """
        return self.repo.read_by_name(name)

    def search_by_name(self, *, query: str, limit: int = 10):
        """
        Case-insensitive substring search against Name + Abbreviation.
        Prefix matches rank above substring matches.

        In-memory filter over `read_all()` — Project is small (~130 rows)
        so this beats a dedicated LIKE sproc. Upgrade to a sproc if the
        table grows or fuzzy matching gets more complex.
        """
        q = (query or "").strip().lower()
        if not q or limit <= 0:
            return []

        prefix_hits = []
        substring_hits = []

        for project in self.repo.read_all():
            name = (project.name or "").lower()
            abbreviation = (project.abbreviation or "").lower()

            if name.startswith(q) or abbreviation.startswith(q):
                prefix_hits.append(project)
            elif q in name or q in abbreviation:
                substring_hits.append(project)

            if len(prefix_hits) >= limit:
                break

        return (prefix_hits + substring_hits)[:limit]

    def read_by_customer_id(self, customer_id: int):
        """
        Return all projects belonging to a Customer (BIGINT FK).

        Useful for parent-child queries from the Customer specialist
        agent ("what projects does Customer X have?"). In-memory filter
        over read_all() since Project is small (~130 rows).
        """
        return [
            p for p in self.repo.read_all() if p.customer_id == customer_id
        ]

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        description: str = None,
        status: str = None,
        customer_id: int = None,
        abbreviation: str = None,
    ) -> Optional[Project]:
        """
        Update a project by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if description is not None:
                existing.description = description
            if status is not None:
                existing.status = status
            if customer_id is not None:
                existing.customer_id = customer_id
            if abbreviation is not None:
                existing.abbreviation = abbreviation
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Project]:
        """
        Delete a project by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
