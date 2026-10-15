"""
U-671 — the vendor-credit incremental pull sends its watermark WITH a UTC offset.

QBO reads an offset-less timestamp in company-local time, so the old private formatter's
`> '2026-10-06T17:00:00'` silently meant 7 hours later and every tick fetched nothing.
The no-per-client-formatter guard lives with the shared helper's tests
(tests/test_qbo_format_datetime_for_qbo_query.py).
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from integrations.intuit.qbo.base.watermark import _normalize_watermark_value
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient


def _captured_query(**query_kwargs) -> str:
    http = MagicMock()
    http.get.return_value = {"QueryResponse": {}}
    client = QboVendorCreditClient(realm_id="123", http_client=http)
    assert client.query_vendor_credits(**query_kwargs) == []
    return http.get.call_args.kwargs["params"]["query"]


@pytest.mark.parametrize(
    "watermark",
    [
        # dbo.Sync read back as a datetime (ReadSyncs) — the scheduler path
        _normalize_watermark_value(datetime(2026, 10, 6, 17, 0, 0)),
        # dbo.Sync read back as VARCHAR (CreateSync/UpdateSync OUTPUT)
        _normalize_watermark_value("2026-10-06T17:00:00+00:00"),
    ],
)
def test_incremental_query_carries_utc_offset(watermark):
    query = _captured_query(last_updated_time=watermark)
    assert "MetaData.LastUpdatedTime > '2026-10-06T17:00:00+00:00'" in query
    assert "> '2026-10-06T17:00:00'" not in query


def test_date_window_query_is_unchanged():
    query = _captured_query(start_date="2026-01-01", end_date="2026-10-15")
    assert "TxnDate >= '2026-01-01'" in query and "TxnDate <= '2026-10-15'" in query
    assert "LastUpdatedTime" not in query
