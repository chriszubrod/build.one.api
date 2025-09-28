"""Legacy aliases for the certificate type module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_certificate_type", "bus_certificate_type", "api_certificate_type", "web_certificate_type"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_certificate_type = import_alias(
    module_name,
    "pers_certificate_type",
    (
        ("pers_certificate_type",),
        ("persistence", "certificate_type"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_certificate_type = import_alias(
    module_name,
    "bus_certificate_type",
    (
        ("bus_certificate_type",),
        ("business", "certificate_type"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_certificate_type = import_alias(
    module_name,
    "api_certificate_type",
    (
        ("api_certificate_type",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_certificate_type = import_alias(
    module_name,
    "web_certificate_type",
    (
        ("web_certificate_type",),
        ("web", "routes"),
    ),
)

