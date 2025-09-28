"""Legacy aliases for the vendor module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_vendor", "bus_vendor", "api_vendor", "web_vendor"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_vendor = import_alias(
    module_name,
    "pers_vendor",
    (
        ("pers_vendor",),
        ("persistence", "vendor"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_vendor = import_alias(
    module_name,
    "bus_vendor",
    (
        ("bus_vendor",),
        ("business", "vendor"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_vendor = import_alias(
    module_name,
    "api_vendor",
    (
        ("api_vendor",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_vendor = import_alias(
    module_name,
    "web_vendor",
    (
        ("web_vendor",),
        ("web", "routes"),
    ),
)

