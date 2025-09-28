"""Legacy aliases for the module module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_module", "bus_module", "api_module", "web_module"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_module = import_alias(
    module_name,
    "pers_module",
    (
        ("pers_module",),
        ("persistence", "module"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_module = import_alias(
    module_name,
    "bus_module",
    (
        ("bus_module",),
        ("business", "module"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_module = import_alias(
    module_name,
    "api_module",
    (
        ("api_module",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_module = import_alias(
    module_name,
    "web_module",
    (
        ("web_module",),
        ("web", "routes"),
    ),
)

