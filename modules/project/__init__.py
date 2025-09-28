"""Legacy aliases for the project module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_project", "bus_project", "api_project", "web_project"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_project = import_alias(
    module_name,
    "pers_project",
    (
        ("pers_project",),
        ("persistence", "project"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_project = import_alias(
    module_name,
    "bus_project",
    (
        ("bus_project",),
        ("business", "project"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_project = import_alias(
    module_name,
    "api_project",
    (
        ("api_project",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_project = import_alias(
    module_name,
    "web_project",
    (
        ("web_project",),
        ("web", "routes"),
    ),
)

