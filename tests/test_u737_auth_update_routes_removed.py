"""U-737: the self-service Auth update/relink routes are gone (privilege escalation)."""
from __future__ import annotations

from fastapi.routing import APIRoute

from entities.auth.api import router as auth_router_module
from entities.auth.business.service import AuthService


def _paths():
    return {(m, r.path) for r in auth_router_module.router.routes if isinstance(r, APIRoute) for m in r.methods}


def test_update_auth_routes_are_not_registered():
    paths = _paths()
    assert not any("/update/auth/" in p for _, p in paths), paths


def test_relink_service_methods_are_gone():
    assert not hasattr(AuthService, "update_by_public_id")
    assert not hasattr(AuthService, "update_user_id_by_public_id")
