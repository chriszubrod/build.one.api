import pathlib
import sys
import types
from decimal import Decimal

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


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
