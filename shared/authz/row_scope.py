"""
Row-scope rule registry — Phase 0 skeleton, NOT enforced yet.

Each scoped entity declares the rule that determines which rows the
caller can see. Phase 3 wires this into the read sprocs (sprocs gain
an `@AccessibleProjectIds NVARCHAR(MAX)` param; service layer resolves
the list per request).

Default rule for any entity not in the table is `RowScope.COMPANY_ONLY`.

Rules:
- COMPANY_ONLY: filter only by `WHERE CompanyId = @CompanyId`. The bulk
  of entities (vendors, customers, cost codes, masters, etc.).
- PROJECT_MEMBERSHIP: additionally filter by `ProjectId IN @AccessibleProjectIds`.
  Bills / Expenses / Invoices / ContractLabor / TimeEntry — where users
  with no Modules.PROJECTS read grant only see rows for projects they're
  explicitly assigned to via UserProject.
- NONE: only system admins can read. Reserved for future system tables.
"""
from __future__ import annotations

from enum import Enum


class RowScope(str, Enum):
    COMPANY_ONLY = "company_only"
    PROJECT_MEMBERSHIP = "project_membership"
    NONE = "none"


# Keys are the entity directory name (matches `entities/{name}/`).
ROW_SCOPE_RULES: dict[str, RowScope] = {
    "bill": RowScope.PROJECT_MEMBERSHIP,
    "bill_credit": RowScope.PROJECT_MEMBERSHIP,
    "expense": RowScope.PROJECT_MEMBERSHIP,
    "invoice": RowScope.PROJECT_MEMBERSHIP,
    "contract_labor": RowScope.PROJECT_MEMBERSHIP,
    "time_entry": RowScope.PROJECT_MEMBERSHIP,
}


def row_scope_for(entity_name: str) -> RowScope:
    """Lookup helper. Defaults to COMPANY_ONLY for any unregistered entity."""
    return ROW_SCOPE_RULES.get(entity_name, RowScope.COMPANY_ONLY)
