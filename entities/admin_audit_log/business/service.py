# Python Standard Library Imports
import json
import logging
from typing import Any, Optional

# Third-party Imports

# Local Imports
from entities.admin_audit_log.business.model import AdminAuditLog
from entities.admin_audit_log.persistence.repo import AdminAuditLogRepository
from shared.authz import current_is_system_admin, current_user_id

logger = logging.getLogger(__name__)

REDACTED_KEYS = frozenset({
    "password",
    "password_hash",
    "new_password",
    "current_password",
    "token",
    "refresh_token",
    "access_token",
    "secret",
})


def sanitize_detail(detail: Optional[dict]) -> Optional[dict]:
    """Recursively drop sensitive keys before persisting audit detail JSON."""
    if detail is None:
        return None

    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                key_lower = str(key).lower()
                if key_lower in REDACTED_KEYS or "password" in key_lower:
                    continue
                cleaned[key] = _clean(item)
            return cleaned
        if isinstance(value, list):
            return [_clean(item) for item in value]
        return value

    # Contract: a non-dict `detail` (a caller passing a list by mistake)
    # persists as SQL NULL, never as JSON — keep the dict-only guard.
    cleaned = _clean(detail)
    return cleaned if isinstance(cleaned, dict) else None


class AdminAuditLogService:
    """Service for recording and reading admin audit log entries.

    `read_recent` has no API consumer yet — the global admin audit view is a
    booked follow-up — so the method is staged for it, not dead code.
    """

    def __init__(self, repo: Optional[AdminAuditLogRepository] = None):
        self.repo = repo or AdminAuditLogRepository()

    def record_admin_action(
        self,
        *,
        action: str,
        target_user_id: int | None,
        detail: dict | None = None,
    ) -> AdminAuditLog:
        actor_user_id = current_user_id.get()
        actor_isa = current_is_system_admin.get()
        sanitized = sanitize_detail(detail)
        detail_json = json.dumps(sanitized, default=str) if sanitized is not None else None
        try:
            return self.repo.create(
                actor_user_id=actor_user_id,
                actor_is_system_admin=bool(actor_isa),
                action=action,
                target_user_id=target_user_id,
                detail=detail_json,
            )
        except Exception as error:
            # Fail LOUD, and leave the full (already-sanitized) record in the
            # app log: the audit write runs AFTER the mutation it describes has
            # committed (separate connection), so on the rare same-DB transient
            # between the two statements this line is the only trail. Search
            # for the AUDIT-FALLBACK marker when reconciling. (U-585 Pass-1 P1,
            # accepted-by-design; same-transaction audit is booked as a follow-up.)
            logger.error(
                "AUDIT-FALLBACK: failed to record admin audit action %s "
                "actor_user_id=%s actor_is_system_admin=%s target_user_id=%s detail=%s: %s",
                action,
                actor_user_id,
                bool(actor_isa),
                target_user_id,
                detail_json,
                error,
            )
            raise

    def read_for_user(self, user_id: int, limit: int = 50) -> list[AdminAuditLog]:
        return self.repo.read_by_target_user_id(user_id, limit)

    def read_recent(self, limit: int = 100) -> list[AdminAuditLog]:
        return self.repo.read_recent(limit)


def record_admin_action(
    *,
    action: str,
    target_user_id: int | None,
    detail: dict | None = None,
) -> AdminAuditLog:
    """Record one admin action.

    Resolves `AdminAuditLogService` from this module's globals at CALL time, so
    consumers can import this module at module level (no function-local import
    to dodge a cycle) while tests keep patching the class on this module.
    """
    return AdminAuditLogService().record_admin_action(
        action=action,
        target_user_id=target_user_id,
        detail=detail,
    )
