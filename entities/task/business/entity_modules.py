# Local Imports
from shared.rbac import has_module_permission
from shared.rbac_constants import Modules

# U-551b-f6 — the one mapping from an inbox entity type to the module that gates
# that entity's own detail routes. Keys must match the UNION arms of `dbo.ReadInboxTasks`.
ENTITY_MODULE_BY_TYPE: dict[str, str] = {
    "Bill": Modules.BILLS,
    "Expense": Modules.EXPENSES,
    "BillCredit": Modules.BILL_CREDITS,
    "Invoice": Modules.INVOICES,
}


def readable_entity_types(current_user: dict) -> list[str]:
    """Entity types whose module the caller has can_read on (system admins pass via the check)."""
    return [
        entity_type
        for entity_type, module in ENTITY_MODULE_BY_TYPE.items()
        if has_module_permission(current_user, module, "can_read")
    ]
