"""Legacy aliases for the auth module hierarchy."""

from __future__ import annotations

from modules._legacy_alias import ensure_package, import_alias

__all__ = ["business", "api", "web", "bus_auth", "api_auth", "web_auth"]

module_name = __name__

# Business layer for authentication services.
business = ensure_package(f"{module_name}.business")
bus_auth = import_alias(
    module_name,
    "bus_auth",
    (
        ("bus_auth",),
        ("business", "auth"),
    ),
)

# API routes rely on the business services.
api = ensure_package(f"{module_name}.api")
api_auth = import_alias(
    module_name,
    "api_auth",
    (
        ("api_auth",),
        ("api", "routes"),
    ),
)

# Web routes also rely on the business services.
web = ensure_package(f"{module_name}.web")
web_auth = import_alias(
    module_name,
    "web_auth",
    (
        ("web_auth",),
        ("web", "routes"),
    ),
)
