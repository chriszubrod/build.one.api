"""U-475 — the user read routes must answer 404/422, never 500.

Three defects, all of which surfaced in prod as a bare
`500 Internal Server Error` from `GET /api/v1/get/user/{public_id}`:

1. **Absent row.** `read_by_public_id` returns None for an id that does not
   resolve; the route called `user.to_dict()` on it unguarded ->
   `AttributeError` -> 500. Sibling read routes guard with `raise_not_found`
   (see `test_u370_address_crud_guards.py`, the same defect fixed on Address);
   this one did not.
2. **Malformed id.** `@PublicId` is declared UNIQUEIDENTIFIER, so a non-UUID
   path segment (`/get/user/18`) fails in the DRIVER (SQL 8114) after opening
   a connection. `parse_public_id` already exists to reject that shape at the
   boundary -- this route just never called it.
3. **`raise_not_found` arity.** The helper takes ONE argument. `/get/user/me`
   called `raise_not_found("User", "me")`, so the not-found branch raised
   `TypeError` -> 500 instead of the 404 it was written to return. This is the
   iOS bootstrap path (2026-06-09 onboarding lockout), where an opaque 500 is
   exactly the failure mode that is hardest to diagnose on-device.

Each test asserts the TRANSPORT outcome, so reverting any one fix turns the
matching test red rather than merely changing an internal call shape.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

import pytest

from conftest import REPO_ROOT, iter_prod_python_sources
from entities.user.api.router import (
    get_my_user_router,
    get_user_by_public_id_router,
)
from shared.api.errors import ApiError, ErrorCode
from shared.api.responses import raise_not_found
from shared.authz import current_user_id

VALID_GUID = "0C2A4C4E-7D23-4356-9BCD-A326689B8967"


def test_absent_user_is_404_not_500():
    """A well-formed id that resolves to no row -> 404, not AttributeError."""
    with patch("entities.user.api.router.UserService") as service_cls:
        service_cls.return_value.read_by_public_id.return_value = None
        with pytest.raises(ApiError) as exc_info:
            get_user_by_public_id_router(public_id=VALID_GUID, current_user={})

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "User not found"
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND
    service_cls.return_value.read_by_public_id.assert_called_once()


def test_malformed_public_id_is_422_and_never_touches_the_database():
    """`/get/user/18` is rejected at the boundary -- no UNIQUEIDENTIFIER bind.

    The second assertion is the one that matters: a 422 produced by letting the
    driver fail would still be a connection opened and a SQL 8114 raised. The
    service is never even constructed.
    """
    with patch("entities.user.api.router.UserService") as service_cls:
        with pytest.raises(ApiError) as exc_info:
            get_user_by_public_id_router(public_id="18", current_user={})

    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
    service_cls.assert_not_called()


def test_me_route_is_404_when_the_row_is_missing():
    """The arity bug: this branch used to raise TypeError -> 500."""
    token = current_user_id.set(4242)
    try:
        with patch("entities.user.api.router.UserService") as service_cls:
            service_cls.return_value.read_by_id.return_value = None
            with pytest.raises(ApiError) as exc_info:
                get_my_user_router(current_user={})
    finally:
        current_user_id.reset(token)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "User not found"
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


def test_me_route_is_404_when_there_is_no_caller_id():
    """Same arity bug on the other branch of the same route."""
    token = current_user_id.set(None)
    try:
        with pytest.raises(ApiError) as exc_info:
            get_my_user_router(current_user={})
    finally:
        current_user_id.reset(token)

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


def _raise_not_found_calls(tree: ast.AST):
    """Yield every `raise_not_found(...)` Call node, plain or attribute-style."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name == "raise_not_found":
            yield node


def test_no_call_site_overruns_raise_not_found_signature():
    """Repo-wide guard for the defect class, not just the two sites fixed here.

    The ceiling is DERIVED from the helper's own signature rather than pinned at
    a literal, so a deliberate, backward-compatible change (adding an optional
    `identifier`) makes this test pass on its own instead of reporting a safe
    refactor as a regression. What it still catches is the real failure: a call
    site passing more arguments than the helper accepts, which raises TypeError
    only when the not-found branch actually fires -- i.e. in prod, surfacing as
    the same opaque 500 this unit exists to remove.

    Scanned across every PRODUCTION source (`iter_prod_python_sources` excludes
    `tests/`), because `raise_not_found` is importable from anywhere and a
    router is not the only place that can call it -- the prod call sites are the
    ones that turn into a 500.
    """
    max_args = len(inspect.signature(raise_not_found).parameters)

    offenders: list[str] = []
    for path in sorted(iter_prod_python_sources()):
        source = path.read_text(encoding="utf-8")
        if "raise_not_found(" not in source:
            continue
        for node in _raise_not_found_calls(ast.parse(source)):
            count = len(node.args) + len(node.keywords)
            if count > max_args:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> "
                    f"{ast.unparse(node)}  [{count} args, helper accepts {max_args}]"
                )

    assert not offenders, (
        "raise_not_found() called with more arguments than it accepts -- these "
        "raise TypeError at runtime, which surfaces as a 500 from whichever "
        "route hits the not-found branch:\n" + "\n".join(offenders)
    )
