"""Legacy aliases for the vendor type module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_vendor_type", "bus_vendor_type", "api_vendor_type", "web_vendor_type"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_vendor_type = import_alias(
    module_name,
    "pers_vendor_type",
    (
        ("pers_vendor_type",),
        ("persistence", "vendor_type"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_vendor_type = import_alias(
    module_name,
    "bus_vendor_type",
    (
        ("bus_vendor_type",),
        ("business", "vendor_type"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_vendor_type = import_alias(
    module_name,
    "api_vendor_type",
    (
        ("api_vendor_type",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_vendor_type = import_alias(
    module_name,
    "web_vendor_type",
    (
        ("web_vendor_type",),
        ("web", "routes"),
    ),
)

