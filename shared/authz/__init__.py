"""
Access control: tenant scope + row scope + ContextVar plumbing.

Phase 0 lays the foundation only — the ContextVars are populated by the
auth middleware but no enforcement reads them yet. Phase 2 wires the
permission resolver and Phase 3 wires row-scope filtering on top.
"""
from shared.authz.context import (
    current_user_id,
    current_company_id,
    current_is_system_admin,
    set_authz_context,
    clear_authz_context,
)
from shared.authz.row_scope import (
    ROW_SCOPE_RULES,
    row_scope_for,
    RowScope,
)

__all__ = [
    "current_user_id",
    "current_company_id",
    "current_is_system_admin",
    "set_authz_context",
    "clear_authz_context",
    "ROW_SCOPE_RULES",
    "row_scope_for",
    "RowScope",
]
