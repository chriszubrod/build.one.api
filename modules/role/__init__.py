"""Legacy aliases for the role module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_role", "bus_role", "api_role", "web_role"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_role = import_alias(
    module_name,
    "pers_role",
    (
        ("pers_role",),
        ("persistence", "role"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_role = import_alias(
    module_name,
    "bus_role",
    (
        ("bus_role",),
        ("business", "role"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_role = import_alias(
    module_name,
    "api_role",
    (
        ("api_role",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_role = import_alias(
    module_name,
    "web_role",
    (
        ("web_role",),
        ("web", "routes"),
    ),
)

