"""
U-671 — the QBO vendor-credit incremental pull must send its watermark WITH a UTC offset.

The vendor-credit client used to carry a private `_format_datetime_for_qbo_query` that
emitted `YYYY-MM-DDTHH:MM:SS` with no offset. `dbo.Sync` watermarks are UTC, but QBO reads
an offset-less timestamp in company-local time (Pacific), so every incremental tick asked
for credits "updated after now + 7h" and fetched nothing (proven against live QBO on
2026-10-14: VC 77338 was excluded by `> '2026-10-06T17:00:00'` and returned by
`> '2026-10-06T17:00:00+00:00'`). Credits only arrived through manual date-window runs.

These tests pin (1) the offset on the real query the client builds, for the watermark shapes
the pull actually produces, and (2) that no QBO entity client defines its own formatter again
— U-254 collapsed nine copies into `base/client.py`; this tenth one diverged and was missed.
"""
import ast
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from integrations.intuit.qbo.base.watermark import _normalize_watermark_value
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient

REPO_ROOT = Path(__file__).resolve().parents[1]
QBO_ROOT = REPO_ROOT / "integrations" / "intuit" / "qbo"
SHARED_HOME = QBO_ROOT / "base" / "client.py"


def _captured_query(last_updated_time) -> str:
    http = MagicMock()
    http.get.return_value = {"QueryResponse": {}}
    client = QboVendorCreditClient(realm_id="123", http_client=http)
    assert client.query_vendor_credits(last_updated_time=last_updated_time) == []
    return http.get.call_args.kwargs["params"]["query"]


@pytest.mark.parametrize(
    "watermark",
    [
        # WatermarkRun.watermark_value / _normalize_watermark_value(datetime) — the scheduler path
        "2026-10-06T17:00:00Z",
        _normalize_watermark_value(datetime(2026, 10, 6, 17, 0, 0)),
        # _normalize_last_sync of a '+00:00' string read back from dbo.Sync
        _normalize_watermark_value("2026-10-06T17:00:00+00:00"),
    ],
)
def test_incremental_query_carries_utc_offset(watermark):
    query = _captured_query(watermark)
    assert "MetaData.LastUpdatedTime > '2026-10-06T17:00:00+00:00'" in query


def test_offsetless_boundary_is_never_emitted():
    query = _captured_query("2026-10-06T17:00:00Z")
    assert "> '2026-10-06T17:00:00'" not in query


def test_date_window_query_is_unchanged():
    http = MagicMock()
    http.get.return_value = {"QueryResponse": {}}
    QboVendorCreditClient(realm_id="123", http_client=http).query_vendor_credits(
        start_date="2026-01-01", end_date="2026-10-15"
    )
    query = http.get.call_args.kwargs["params"]["query"]
    assert "TxnDate >= '2026-01-01'" in query and "TxnDate <= '2026-10-15'" in query
    assert "LastUpdatedTime" not in query


def _entity_clients():
    return sorted(QBO_ROOT.glob("*/external/client.py"))


def test_guard_scans_every_entity_client():
    # A guard that matches nothing passes vacuously — prove it sees the clients it protects.
    names = {p.parent.parent.name for p in _entity_clients()}
    assert {"vendorcredit", "bill", "purchase", "invoice"} <= names
    assert len(names) >= 10


@pytest.mark.parametrize("path", _entity_clients(), ids=lambda p: p.parent.parent.name)
def test_no_entity_client_defines_its_own_query_formatter(path):
    tree = ast.parse(path.read_text())
    own = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_format_datetime_for_qbo_query"
    ]
    assert not own, (
        f"{path.relative_to(REPO_ROOT)} defines its own _format_datetime_for_qbo_query at line(s) {own}; "
        f"use the shared helper in {SHARED_HOME.relative_to(REPO_ROOT)} (U-254, U-671)."
    )


@pytest.mark.parametrize("path", _entity_clients(), ids=lambda p: p.parent.parent.name)
def test_incremental_clients_import_the_shared_formatter(path):
    source = path.read_text()
    if "LastUpdatedTime >" not in source:
        pytest.skip("no incremental LastUpdatedTime filter")
    tree = ast.parse(source)
    imported = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "integrations.intuit.qbo.base.client"
        and any(a.name == "_format_datetime_for_qbo_query" for a in n.names)
        for n in ast.walk(tree)
    )
    assert imported, f"{path.relative_to(REPO_ROOT)} filters on LastUpdatedTime without the shared formatter"
