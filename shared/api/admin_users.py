# Python Standard Library Imports
from typing import Any, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Query

# Local Imports
from entities.admin_audit_log.business.service import AdminAuditLogService
from entities.auth.business.service import AuthService
from entities.contact.business.service import ContactService
from entities.role.business.service import RoleService
from entities.user.business.model import User
from entities.user.business.service import UserService
from entities.user_role.business.service import UserRoleService
from shared.api.responses import (
    item_response,
    list_response,
    parse_public_id,
    raise_not_found,
)
from shared.rbac import require_system_admin

router = APIRouter(prefix="/api/v1/admin", tags=["api", "admin-users"])


def filter_and_page_users(
    summaries: list[dict],
    search: Optional[str],
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """Case-insensitive substring search over name/username/email; sort lastname, firstname."""
    needle = (search or "").strip().lower()

    def _sort_key(row: dict) -> tuple:
        last = row.get("lastname") or ""
        first = row.get("firstname") or ""
        return (last.lower(), first.lower(), row.get("id") or 0)

    if needle:
        filtered = []
        for row in summaries:
            haystacks = (
                row.get("firstname"),
                row.get("lastname"),
                row.get("username"),
                row.get("email"),
            )
            if any(needle in (h or "").lower() for h in haystacks):
                filtered.append(row)
    else:
        filtered = list(summaries)

    filtered.sort(key=_sort_key)
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return page, total


def _first_email(contacts) -> Optional[str]:
    for contact in contacts:
        if contact.email:
            return contact.email
    return None


def _role_map() -> dict[int, Any]:
    """Every Role keyed by id — one read, shared by the whole page."""
    return {role.id: role for role in RoleService().read_all()}


def _roles_for_user(
    user_id: int, role_map: dict[int, Any], user_role_service: UserRoleService
) -> list[dict]:
    user_roles = user_role_service.read_all_by_user_id(user_id)
    roles = []
    for ur in user_roles:
        role = role_map.get(ur.role_id)
        roles.append(
            {
                "user_role_public_id": str(ur.public_id),
                "role_public_id": str(role.public_id) if role else None,
                "role_id": ur.role_id,
                "role_name": role.name if role else None,
                "company_id": ur.company_id,
            }
        )
    return roles


def _build_user_summary(user: User) -> dict:
    """The User-row half of a summary row.

    `username` / `has_auth` / `email` / `roles` are filled in by the enrichment
    passes below; their keys are created here so the response key order never
    depends on which pass ran.
    """
    return {
        "public_id": str(user.public_id),
        "id": user.id,
        "firstname": user.firstname,
        "lastname": user.lastname,
        "is_system_admin": bool(user.is_system_admin),
        "is_agent": bool(user.is_agent),
        "username": None,
        "has_auth": False,
        "email": None,
        "roles": [],
        "created_datetime": user.created_datetime,
    }


def _enrich_identity(rows: list[dict]) -> None:
    """Fill username / has_auth / email on the given rows, in place.

    Contact has a bulk read (ReadContacts) but Auth has no bulk repo method, so
    this stays one lookup per row — which is why callers hand it the page slice
    rather than the whole table wherever the filter allows it.
    """
    if not rows:
        return
    auth_service = AuthService()
    contact_service = ContactService()
    for row in rows:
        user_id = row["id"]
        auth = auth_service.read_by_user_id(user_id=user_id)
        row["username"] = auth.username if auth else None
        row["has_auth"] = auth is not None
        row["email"] = _first_email(contact_service.read_by_user_id(user_id=user_id))


def _enrich_roles(rows: list[dict]) -> None:
    """Fill roles on the given rows, in place — one Role read for all of them."""
    if not rows:
        return
    role_map = _role_map()
    user_role_service = UserRoleService()
    for row in rows:
        row["roles"] = _roles_for_user(row["id"], role_map, user_role_service)


def _summaries_for_all_users(
    *, include_agents: bool, search: Optional[str], limit: int, offset: int
) -> list[dict]:
    """Every user as a summary row, with the per-user lookups kept to the page.

    A search term matches `username` / `email`, so on that path every row needs
    its auth + contact lookup before the filter can be correct. With no search
    term the filter reads User-row fields only, so the page is resolved first —
    via `filter_and_page_users` itself, so the sort key can never drift from the
    caller's — and only those rows pay for a lookup. Rows outside the page are
    still returned (the caller's `count` is the full total) but keep their
    placeholder values; the caller's own slice drops them.
    """
    users = UserService().read_all(include_agents=include_agents)
    summaries = [_build_user_summary(user) for user in users]
    searching = bool((search or "").strip())
    if searching:
        _enrich_identity(summaries)
    page, _total = filter_and_page_users(summaries, search, limit, offset)
    if not searching:
        _enrich_identity(page)
    _enrich_roles(page)
    return summaries


def _summary_for_public_id(user_public_id: str) -> Optional[dict]:
    user = UserService().read_by_public_id(public_id=user_public_id)
    if not user:
        return None
    summary = _build_user_summary(user)
    _enrich_identity([summary])
    _enrich_roles([summary])
    return summary


# Plain `def` on purpose: these handlers call synchronous pyodbc services, so
# they must run on Starlette's threadpool like every entity router does. An
# `async def` here would block the event loop for the whole per-user fan-out.
@router.get("/users")
def list_admin_users(
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_agents: bool = Query(default=False),
    current_user: dict = Depends(require_system_admin()),
):
    summaries = _summaries_for_all_users(
        include_agents=include_agents, search=search, limit=limit, offset=offset
    )
    page, total = filter_and_page_users(summaries, search, limit, offset)
    return list_response(page, count=total)


@router.get("/user/{user_public_id}")
def get_admin_user(
    user_public_id: str,
    current_user: dict = Depends(require_system_admin()),
):
    user_public_id = parse_public_id(user_public_id, "user_public_id")
    detail = _summary_for_public_id(user_public_id)
    if not detail:
        raise_not_found("User")
    return item_response(detail)


@router.get("/user/{user_public_id}/audit")
def get_admin_user_audit(
    user_public_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(require_system_admin()),
):
    user_public_id = parse_public_id(user_public_id, "user_public_id")
    user_service = UserService()
    user = user_service.read_by_public_id(public_id=user_public_id)
    if not user:
        raise_not_found("User")

    entries = AdminAuditLogService().read_for_user(user.id, limit=limit)
    actor_ids = {e.actor_user_id for e in entries if e.actor_user_id is not None}
    actor_names: dict[int, Optional[str]] = {}
    for actor_id in actor_ids:
        actor = user_service.read_by_id(actor_id)
        if actor and (actor.firstname or actor.lastname):
            actor_names[actor_id] = " ".join(
                p for p in (actor.firstname, actor.lastname) if p
            ).strip()

    payload = []
    for entry in entries:
        row = entry.to_dict()
        row["actor_name"] = (
            actor_names.get(entry.actor_user_id) if entry.actor_user_id else None
        )
        payload.append(row)

    return list_response(payload)
