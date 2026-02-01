"""Entity config for workflow UI labels and module routes (by classified entity type)."""
from dataclasses import dataclass
@dataclass
class EntityConfig:
    label: str
    details_label: str
    module: str  # route prefix e.g. /bills, /expenses


_ENTITY_CONFIGS: dict[str, EntityConfig] = {
    "bill": EntityConfig(label="Bill", details_label="Bill", module="/bills"),
    "expense": EntityConfig(label="Expense", details_label="Expense", module="/expenses"),
    "invoice": EntityConfig(label="Invoice", details_label="Invoice", module="/bills"),
    "contract": EntityConfig(label="Contract", details_label="Contract", module="/contracts"),
    "change_order": EntityConfig(label="Change Order", details_label="Change Order", module="/change-orders"),
    "other": EntityConfig(label="Other", details_label="Other", module="/tasks"),
}


def get_entity_config(entity_type: str) -> EntityConfig:
    """Return config for a classified entity type; fallback for unknown types."""
    return _ENTITY_CONFIGS.get(entity_type.lower(), _ENTITY_CONFIGS["other"])
