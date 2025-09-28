"""Legacy aliases for the bill module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = [
    "persistence",
    "business",
    "api",
    "web",
    "pers_bill",
    "pers_bill_line_item",
    "pers_bill_line_item_attachment",
    "bus_bill",
    "bus_bill_line_item",
    "bus_bill_line_item_attachment",
    "api_bill",
    "web_bill",
]

module_name = __name__

# Persistence aliases must be initialised first so business imports can use them.
persistence = ensure_package(f"{module_name}.persistence")
pers_bill = import_alias(
    module_name,
    "pers_bill",
    (
        ("pers_bill",),
        ("persistence", "bill"),
    ),
)
pers_bill_line_item = import_alias(
    module_name,
    "pers_bill_line_item",
    (
        ("pers_bill_line_item",),
        ("persistence", "bill_line_item"),
    ),
)
pers_bill_line_item_attachment = import_alias(
    module_name,
    "pers_bill_line_item_attachment",
    (
        ("pers_bill_line_item_attachment",),
        ("persistence", "bill_line_item_attachment"),
    ),
)

# Business layer aliases depend on persistence.
business = ensure_package(f"{module_name}.business")
bus_bill = import_alias(
    module_name,
    "bus_bill",
    (
        ("bus_bill",),
        ("business", "bill"),
    ),
)
bus_bill_line_item = import_alias(
    module_name,
    "bus_bill_line_item",
    (
        ("bus_bill_line_item",),
        ("business", "bill_line_item"),
    ),
)
bus_bill_line_item_attachment = import_alias(
    module_name,
    "bus_bill_line_item_attachment",
    (
        ("bus_bill_line_item_attachment",),
        ("business", "bill_line_item_attachment"),
    ),
)

# Finally expose the API and web routes which import the business layer.
api = ensure_package(f"{module_name}.api")
api_bill = import_alias(
    module_name,
    "api_bill",
    (
        ("api_bill",),
        ("api", "routes"),
    ),
)

web = ensure_package(f"{module_name}.web")
web_bill = import_alias(
    module_name,
    "web_bill",
    (
        ("web_bill",),
        ("web", "routes"),
    ),
)
