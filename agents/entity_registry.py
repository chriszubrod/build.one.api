# Python Standard Library Imports
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class EntityField:
    """Definition of a field for an entity type."""
    key: str  # Internal key (e.g., "bill_number")
    label: str  # Display label (e.g., "Bill Number")
    source: str  # Where to get value: "classification", "vendor_match", "project_match", "draft"
    source_key: Optional[str] = None  # Key within source (defaults to key)
    format: Optional[str] = None  # Optional format hint: "currency", "date", "percent"
    
    def __post_init__(self):
        if self.source_key is None:
            self.source_key = self.key


@dataclass
class EntityConfig:
    """Configuration for an entity type."""
    type_key: str  # Internal key (e.g., "bill")
    label: str  # Display label (e.g., "Bill")
    label_plural: str  # Plural label (e.g., "Bills")
    details_label: str  # Section header (e.g., "Bill Details")
    module: str  # Module name for routing
    fields: List[EntityField] = field(default_factory=list)
    color: str = "blue"  # UI accent color
    icon: str = "📄"  # UI icon


# Entity type definitions
ENTITY_CONFIGS: Dict[str, EntityConfig] = {
    "bill": EntityConfig(
        type_key="bill",
        label="Bill",
        label_plural="Bills",
        details_label="Bill Details",
        module="bill",
        color="blue",
        icon="📄",
        fields=[
            EntityField("vendor_name", "Vendor", "vendor_match", "vendor.name"),
            EntityField("total_amount", "Amount", "draft", "total_amount", format="currency"),
            EntityField("bill_number", "Bill Number", "draft", "bill_number"),
            EntityField("bill_date", "Bill Date", "draft", "bill_date", format="date"),
            EntityField("due_date", "Due Date", "draft", "due_date", format="date"),
            EntityField("project_name", "Project", "project_match", "project.name"),
        ],
    ),
    "expense": EntityConfig(
        type_key="expense",
        label="Expense",
        label_plural="Expenses",
        details_label="Expense Details",
        module="expense",
        color="orange",
        icon="🧾",
        fields=[
            EntityField("vendor_name", "Vendor", "vendor_match", "vendor.name"),
            EntityField("total_amount", "Amount", "classification", "amount", format="currency"),
            EntityField("expense_date", "Date", "classification", "invoice_date", format="date"),
            EntityField("category", "Category", "classification", "expense_category"),
            EntityField("project_name", "Project", "project_match", "project.name"),
        ],
    ),
    "invoice": EntityConfig(
        type_key="invoice",
        label="Invoice",
        label_plural="Invoices",
        details_label="Invoice Details",
        module="invoice",
        color="green",
        icon="📋",
        fields=[
            EntityField("customer_name", "Customer", "customer_match", "customer.name"),
            EntityField("total_amount", "Amount", "classification", "amount", format="currency"),
            EntityField("invoice_number", "Invoice Number", "classification", "invoice_number"),
            EntityField("invoice_date", "Invoice Date", "classification", "invoice_date", format="date"),
            EntityField("due_date", "Due Date", "classification", "due_date", format="date"),
            EntityField("project_name", "Project", "project_match", "project.name"),
        ],
    ),
    "contract": EntityConfig(
        type_key="contract",
        label="Contract",
        label_plural="Contracts",
        details_label="Contract Details",
        module="contract",
        color="purple",
        icon="📑",
        fields=[
            EntityField("party_name", "Party", "classification", "party_name"),
            EntityField("contract_value", "Value", "classification", "amount", format="currency"),
            EntityField("contract_date", "Date", "classification", "contract_date", format="date"),
            EntityField("expiration_date", "Expires", "classification", "expiration_date", format="date"),
            EntityField("project_name", "Project", "project_match", "project.name"),
        ],
    ),
    "change_order": EntityConfig(
        type_key="change_order",
        label="Change Order",
        label_plural="Change Orders",
        details_label="Change Order Details",
        module="change_order",
        color="red",
        icon="📝",
        fields=[
            EntityField("vendor_name", "From", "vendor_match", "vendor.name"),
            EntityField("amount", "Amount", "classification", "amount", format="currency"),
            EntityField("co_number", "CO Number", "classification", "invoice_number"),
            EntityField("project_name", "Project", "project_match", "project.name"),
        ],
    ),
    "other": EntityConfig(
        type_key="other",
        label="Document",
        label_plural="Documents",
        details_label="Document Details",
        module="document",
        color="gray",
        icon="📎",
        fields=[
            EntityField("from_name", "From", "email", "from_address"),
            EntityField("subject", "Subject", "email", "subject"),
        ],
    ),
}


def get_entity_config(entity_type: str) -> EntityConfig:
    """Get entity configuration by type key."""
    return ENTITY_CONFIGS.get(entity_type, ENTITY_CONFIGS["other"])


def get_all_entity_types() -> List[str]:
    """Get list of all entity type keys."""
    return list(ENTITY_CONFIGS.keys())


def map_category_to_entity(category: str) -> str:
    """
    Map classification category to entity type.
    
    The LLM returns categories like 'bill', 'expense', etc.
    This maps them to our entity registry keys.
    """
    # Direct mappings
    category_map = {
        "bill": "bill",
        "vendor_bill": "bill",
        "vendor_invoice": "bill",
        "expense": "expense",
        "expense_report": "expense",
        "receipt": "expense",
        "invoice": "invoice",
        "customer_invoice": "invoice",
        "contract": "contract",
        "agreement": "contract",
        "change_order": "change_order",
        "co": "change_order",
    }
    
    return category_map.get(category.lower(), "other")
