# Python Standard Library Imports
from typing import Optional, Tuple

# Third-party Imports

# Local Imports
from entities.project.business.model import Project
from entities.project.persistence.repo import ProjectRepository
from shared.authz import current_user_id, current_is_system_admin


def _actor_scope() -> Tuple[Optional[int], Optional[bool]]:
    """Read the current request actor from ContextVars."""
    return current_user_id.get(), current_is_system_admin.get()


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
        """
        return self.repo.create(
            tenant_id=tenant_id,
            name=name,
            description=description,
            status=status,
            customer_id=customer_id,
            abbreviation=abbreviation,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[Project]:
        """
        Read projects, scoped by UserProject for non-admin actors.
        """
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_all(
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_user_id(self, user_id: int) -> list[Project]:
        """
        Read projects the user has access to (joined through dbo.UserProject).
        """
        return self.repo.read_by_user_id(user_id=user_id)

    def read_by_id(self, id: int) -> Optional[Project]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_id(
            id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_public_id(self, public_id: str) -> Optional[Project]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_public_id(
            public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_name(self, name: str) -> Optional[Project]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_name(
            name,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def find_for_invoice(self, *, address_hint: Optional[str] = None,
                         project_name_hint: Optional[str] = None) -> list[dict]:
        """Multi-strategy ranked Project lookup for invoice classification.
        Used by the project_specialist agent when an invoice's Ship To /
        job-site address needs to be bound to an existing Project row."""
        return self.repo.find_for_invoice(
            address_hint=address_hint, project_name_hint=project_name_hint,
        )

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
