"""Legacy aliases for the sub cost code module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_sub_cost_code", "bus_sub_cost_code", "api_sub_cost_code", "web_sub_cost_code"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_sub_cost_code = import_alias(
    module_name,
    "pers_sub_cost_code",
    (
        ("pers_sub_cost_code",),
        ("persistence", "sub_cost_code"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_sub_cost_code = import_alias(
    module_name,
    "bus_sub_cost_code",
    (
        ("bus_sub_cost_code",),
        ("business", "sub_cost_code"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_sub_cost_code = import_alias(
    module_name,
    "api_sub_cost_code",
    (
        ("api_sub_cost_code",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_sub_cost_code = import_alias(
    module_name,
    "web_sub_cost_code",
    (
        ("web_sub_cost_code",),
        ("web", "routes"),
    ),
)

