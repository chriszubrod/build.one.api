"""Legacy aliases for the contact module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_contact", "bus_contact", "api_contact", "web_contact"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_contact = import_alias(
    module_name,
    "pers_contact",
    (
        ("pers_contact",),
        ("persistence", "contact"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_contact = import_alias(
    module_name,
    "bus_contact",
    (
        ("bus_contact",),
        ("business", "contact"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_contact = import_alias(
    module_name,
    "api_contact",
    (
        ("api_contact",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_contact = import_alias(
    module_name,
    "web_contact",
    (
        ("web_contact",),
        ("web", "routes"),
    ),
)

