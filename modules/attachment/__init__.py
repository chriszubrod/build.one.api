"""Legacy aliases for the attachment module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_attachment", "bus_attachment", "api_attachment", "web_attachment"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_attachment = import_alias(
    module_name,
    "pers_attachment",
    (
        ("pers_attachment",),
        ("persistence", "attachment"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_attachment = import_alias(
    module_name,
    "bus_attachment",
    (
        ("bus_attachment",),
        ("business", "attachment"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_attachment = import_alias(
    module_name,
    "api_attachment",
    (
        ("api_attachment",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_attachment = import_alias(
    module_name,
    "web_attachment",
    (
        ("web_attachment",),
        ("web", "routes"),
    ),
)

