"""Legacy aliases for the customer module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_customer", "bus_customer", "api_customer", "web_customer"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_customer = import_alias(
    module_name,
    "pers_customer",
    (
        ("pers_customer",),
        ("persistence", "customer"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_customer = import_alias(
    module_name,
    "bus_customer",
    (
        ("bus_customer",),
        ("business", "customer"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_customer = import_alias(
    module_name,
    "api_customer",
    (
        ("api_customer",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_customer = import_alias(
    module_name,
    "web_customer",
    (
        ("web_customer",),
        ("web", "routes"),
    ),
)

