"""Legacy aliases for the address module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_address", "bus_address", "api_address", "web_address"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_address = import_alias(
    module_name,
    "pers_address",
    (
        ("pers_address",),
        ("persistence", "address"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_address = import_alias(
    module_name,
    "bus_address",
    (
        ("bus_address",),
        ("business", "address"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_address = import_alias(
    module_name,
    "api_address",
    (
        ("api_address",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_address = import_alias(
    module_name,
    "web_address",
    (
        ("web_address",),
        ("web", "routes"),
    ),
)

