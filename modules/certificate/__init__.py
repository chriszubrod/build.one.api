"""Legacy aliases for the certificate module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_certificate", "bus_certificate", "api_certificate", "web_certificate"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_certificate = import_alias(
    module_name,
    "pers_certificate",
    (
        ("pers_certificate",),
        ("persistence", "certificate"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_certificate = import_alias(
    module_name,
    "bus_certificate",
    (
        ("bus_certificate",),
        ("business", "certificate"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_certificate = import_alias(
    module_name,
    "api_certificate",
    (
        ("api_certificate",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_certificate = import_alias(
    module_name,
    "web_certificate",
    (
        ("web_certificate",),
        ("web", "routes"),
    ),
)

