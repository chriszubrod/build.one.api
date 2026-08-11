import pathlib
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]

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
