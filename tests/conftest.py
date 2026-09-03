import pathlib
import sys
import types
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyodbc
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]


def _blocked_live_pyodbc_connect(*args, **kwargs):
    """No-live-DB harness guard (U-295) — installed at conftest import time so it
    also catches connections from a stray background thread/task a leaked test
    might spawn. See the RuntimeError below for what to do instead."""
    raise RuntimeError(
        "BLOCKED: a test attempted a real pyodbc.connect() against the live prod "
        "database. This harness is pure-logic / no-live-DB — mock "
        "shared.database.get_connection (or the specific repo module's imported "
        "get_connection) instead of letting a repo/connector default to a real "
        "instance. See tests/conftest.py::_blocked_live_pyodbc_connect."
    )


pyodbc.connect = _blocked_live_pyodbc_connect


@contextmanager
def mock_qbo_app_lock_granted(*_args, **_kwargs):
    """Shared `qbo_app_lock` stand-in yielding True (lock acquired) — the same
    contract test_u226/test_u243 already established locally; import this instead
    of hand-rolling another copy."""
    yield True


@contextmanager
def mock_qbo_app_lock_denied(*_args, **_kwargs):
    """Shared `qbo_app_lock` stand-in yielding False (lock busy) — the
    mirror-image of `mock_qbo_app_lock_granted`; import this instead of
    hand-rolling another local `yield False` copy (U-337 simplify pass)."""
    yield False


def mock_ms_graph_client_cm(return_value=None):
    """A MsGraphClient()-as-context-manager double: `__enter__` returns the
    same mock so `with MsGraphClient() as client: client.upload(...)` calls
    land on it and can be asserted via `client.upload.call_args`/`.post`/`.get`.
    Import this instead of hand-rolling another local copy."""
    instance = MagicMock()
    instance.__enter__.return_value = instance
    instance.__exit__.return_value = False
    if return_value is not None:
        instance.upload.return_value = return_value
        instance.post.return_value = return_value
        instance.get.return_value = return_value
    return instance


@pytest.fixture
def grant_qbo_app_lock():
    """U-304: patches identity_fastpath.py's `qbo_app_lock` import to always
    grant the lock, for pure-logic tests exercising the create-race lock
    (self-heal insert + rollback-guard recheck+delete) that only care about the
    resolve-state branching, not real sp_getapplock serialization. Opt in per
    module via `pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")` —
    NOT autouse here, since most of the suite has no reason to touch this lock."""
    with patch(
        "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock",
        mock_qbo_app_lock_granted,
    ):
        yield


_SKIP_DIR_NAMES = frozenset({".venv", ".git", "__pycache__", "node_modules", ".pytest_cache"})


def iter_prod_python_sources(root: Path | None = None, *, skip_files: frozenset[str] = frozenset()):
    """Yield production Python source files under repo root, excluding tests and venv."""
    repo_root = root or REPO_ROOT
    for path in repo_root.rglob("*.py"):
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name in skip_files:
            continue
        if "tests" in path.parts:
            continue
        yield path


def stub_qbo_identity_fastpath_miss(entity_service_mock) -> None:
    """Force a QBO connector's dbo-native identity fast path (base/identity_fastpath.py)
    to miss, so a test built against the pre-fast-path legacy connector logic exercises
    that legacy path unchanged.

    A bare Mock() auto-returns a truthy Mock for any undefined attribute/method call,
    including `.read_by_qbo_identity(...)` — which would otherwise silently divert such
    a test into the new fast path instead of the branch it's actually testing.
    """
    entity_service_mock.read_by_qbo_identity.return_value = None


def pytest_configure(config):
    """Fail fast with guidance if the interpreter is missing project deps.

    The suite MUST run under the project venv (Python 3.11 with requirements
    installed): ``./.venv/bin/python -m pytest``. A bare system ``python3`` lacks
    app deps (e.g. ``transitions``, imported transitively by the workflow layer),
    so any test importing an app module errors at collection with a cryptic
    ModuleNotFoundError (and pure-logic tests give a misleading green). ``transitions``
    is used as the sentinel: present in the venv, absent from a bare system Python.
    Detect the wrong interpreter up front, before collection.
    """
    import importlib.util

    if importlib.util.find_spec("transitions") is None:
        pytest.exit(
            "Project dependencies are missing — you are likely running a system "
            "Python instead of the project venv. Run:\n"
            "    ./.venv/bin/python -m pytest\n"
            "(See CLAUDE.md 'Testing'.)",
            returncode=1,
        )


@pytest.fixture
def cl_line_item():
    """Build duck-typed contract-labor line items for numeric test batches."""

    def _make(**overrides):
        defaults = {
            "hours": Decimal(str("8.0")),
            "rate": Decimal(str("260.00")),
            "price": Decimal(str("390.00")),
            "is_billable": True,
            "sub_cost_code_id": 1,
            "description": "framing",
        }
        defaults.update(overrides)
        return types.SimpleNamespace(**defaults)

    return _make
