"""Legacy aliases for the payment term module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["persistence", "business", "api", "web", "pers_payment_term", "bus_payment_term", "api_payment_term", "web_payment_term"]

module_name = __name__

# Persistence aliases are initialised before business logic.
persistence = ensure_package(f"{module_name}.persistence")
pers_payment_term = import_alias(
    module_name,
    "pers_payment_term",
    (
        ("pers_payment_term",),
        ("persistence", "payment_term"),
    ),
)

# Business aliases depend on the persistence layer.
business = ensure_package(f"{module_name}.business")
bus_payment_term = import_alias(
    module_name,
    "bus_payment_term",
    (
        ("bus_payment_term",),
        ("business", "payment_term"),
    ),
)

# The API layer depends on business services.
api = ensure_package(f"{module_name}.api")
api_payment_term = import_alias(
    module_name,
    "api_payment_term",
    (
        ("api_payment_term",),
        ("api", "routes"),
    ),
)

# The web layer depends on business services.
web = ensure_package(f"{module_name}.web")
web_payment_term = import_alias(
    module_name,
    "web_payment_term",
    (
        ("web_payment_term",),
        ("web", "routes"),
    ),
)

