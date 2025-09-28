"""Legacy aliases for the user module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_user", "bus_user", "api_user", "web_user"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_user = import_alias(
    module_name,
    "pers_user",
    (
        ("pers_user",),
        ("persistence", "user"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_user = import_alias(
    module_name,
    "bus_user",
    (
        ("bus_user",),
        ("business", "user"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_user = import_alias(
    module_name,
    "api_user",
    (
        ("api_user",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_user = import_alias(
    module_name,
    "web_user",
    (
        ("web_user",),
        ("web", "routes"),
    ),
)

