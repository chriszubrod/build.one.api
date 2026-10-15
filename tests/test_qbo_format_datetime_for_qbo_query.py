"""
Pure-logic tests for the shared _format_datetime_for_qbo_query (U-254).

Was duplicated byte-identically across 9 qbo/*/external/client.py files with
ZERO test coverage before this unit; now collapsed into one home in
qbo/base/client.py. These tests pin the reachable formatting behavior so a
future edit to the sole copy can't silently drift for all 9 callers at once.
"""
from datetime import datetime, timezone

from integrations.intuit.qbo.base.client import _format_datetime_for_qbo_query


def test_none_returns_none():
    assert _format_datetime_for_qbo_query(None) is None


def test_datetime_object_with_tzinfo():
    dt = datetime(2026, 8, 17, 12, 30, 0, tzinfo=timezone.utc)
    assert _format_datetime_for_qbo_query(dt) == "2026-08-17T12:30:00+00:00"


def test_iso_string_with_z_suffix():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30:00Z") == "2026-08-17T12:30:00+00:00"


def test_iso_string_with_explicit_offset():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30:00+00:00") == "2026-08-17T12:30:00+00:00"


def test_date_only_string_gets_midnight_time():
    assert _format_datetime_for_qbo_query("2026-08-17") == "2026-08-17T00:00:00+00:00"


def test_hh_mm_without_seconds_gets_seconds_appended():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30Z") == "2026-08-17T12:30:00+00:00"


def test_microseconds_are_stripped():
    assert _format_datetime_for_qbo_query("2026-08-17T12:30:00.123456Z") == "2026-08-17T12:30:00+00:00"


# --- U-671: the shared helper is the ONLY formatter ---------------------------------------
# The vendor-credit client kept a diverged private copy that dropped the UTC offset, so its
# incremental pull fetched nothing for six months. Every QBO entity client must call this
# helper and none may define its own.
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

ENTITY_CLIENTS = sorted((REPO_ROOT / "integrations" / "intuit" / "qbo").glob("*/external/client.py"))


def test_guard_sees_the_entity_clients():
    # A guard that matches nothing passes vacuously — prove it sees the clients it protects.
    assert {"vendorcredit", "bill", "purchase", "invoice"} <= {p.parent.parent.name for p in ENTITY_CLIENTS}


@pytest.mark.parametrize("path", ENTITY_CLIENTS, ids=lambda p: p.parent.parent.name)
def test_entity_client_uses_the_shared_formatter(path):
    source = path.read_text()
    tree = ast.parse(source)
    defined = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_format_datetime_for_qbo_query"]
    assert not defined, f"{path.relative_to(REPO_ROOT)} defines its own formatter at line(s) {defined}"
    # Keyed on the literal clause text: a client spelling the filter differently is not covered.
    if "LastUpdatedTime >" in source:
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "_format_datetime_for_qbo_query" for n in ast.walk(tree)), (
            f"{path.relative_to(REPO_ROOT)} filters on LastUpdatedTime without calling the shared formatter"
        )
