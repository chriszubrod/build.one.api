"""Legacy aliases for the company module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_company", "bus_company", "api_company", "web_company"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_company = import_alias(
    module_name,
    "pers_company",
    (
        ("pers_company",),
        ("persistence", "company"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_company = import_alias(
    module_name,
    "bus_company",
    (
        ("bus_company",),
        ("business", "company"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_company = import_alias(
    module_name,
    "api_company",
    (
        ("api_company",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_company = import_alias(
    module_name,
    "web_company",
    (
        ("web_company",),
        ("web", "routes"),
    ),
)

