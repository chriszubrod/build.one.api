"""U-256 Part F — BoxExcelUpdateService freshness short-circuit (pure logic)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from integrations.box.excel.business.model import BoxWorkbookEntityPush
from integrations.box.excel.business.service import (
    BoxExcelUpdateService,
    STAMP_LIKE_ENTITY_TYPE,
    _content_hash,
)
from integrations.box.excel.business.workbook_editor import DEFAULT_KEY_COL_INDEX


_BOX_FILE_ID = "workbook-123"
_ENTITY_TYPE = "bill"
_ENTITY_PUBLIC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ENTITY_PUBLIC_ID_2 = "bbbbbbbb-cccc-dddd-eeee-ffff-000000000001"
_INVOICE_PUBLIC_ID = "cccccccc-dddd-eeee-ffff-000000000001"
_STAMP_PAIRS = [("source-line-uuid", "DR-001")]


def _details_row(key: str):
    row = [None] * 26
    row[DEFAULT_KEY_COL_INDEX] = key
    return row


_SAMPLE_ROWS = [_details_row("line-1")]


def _outbox_row(**overrides):
    row = MagicMock()
    row.id = 99
    row.entity_type = _ENTITY_TYPE
    row.entity_public_id = _ENTITY_PUBLIC_ID
    row.request_id = "req-uuid"
    row.created_by_user_id = 17
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _insert_payload(**overrides):
    base = {
        "box_file_id": _BOX_FILE_ID,
        "worksheet_name": "DETAILS",
        "entity_type": _ENTITY_TYPE,
        "entity_public_id": _ENTITY_PUBLIC_ID,
        "project_id": 1,
        "operation": "insert",
    }
    base.update(overrides)
    return base


def _stamp_payload(**overrides):
    base = {
        "box_file_id": _BOX_FILE_ID,
        "worksheet_name": "DETAILS",
        "entity_type": "invoice",
        "entity_public_id": _INVOICE_PUBLIC_ID,
        "operation": "stamp_draw_request",
    }
    base.update(overrides)
    return base


@contextmanager
def _lock_always_acquired():
    @contextmanager
    def _fake_lock(_key):
        yield True

    with patch(
        "integrations.box.excel.business.service.box_app_lock",
        side_effect=_fake_lock,
    ):
        yield


@pytest.fixture
def excel_service():
    svc = BoxExcelUpdateService()
    push_repo = MagicMock()
    return svc, push_repo


def test_freshness_skips_when_content_hash_matches(excel_service):
    svc, push_repo = excel_service
    content_hash = _content_hash(_SAMPLE_ROWS)
    push_repo.read_all_for_file.return_value = [
        BoxWorkbookEntityPush(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            content_hash=content_hash,
        )
    ]

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_details_rows",
        return_value=_SAMPLE_ROWS,
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http, _lock_always_acquired():
        svc.handle(_outbox_row(), _insert_payload())

    mock_http.assert_not_called()
    push_repo.upsert.assert_not_called()


def test_freshness_proceeds_and_upserts_when_hash_differs(excel_service):
    svc, push_repo = excel_service
    push_repo.read_all_for_file.return_value = [
        BoxWorkbookEntityPush(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            content_hash="old-hash",
        )
    ]

    client = MagicMock()
    client.get.return_value = {"etag": "etag0", "lock": None, "name": "book.xlsx"}
    client.put.return_value = {"etag": "etag1", "name": "book.xlsx"}
    client.download_file.return_value = b"xlsx-bytes"
    client.upload_file_version.return_value = {
        "entries": [{"id": _BOX_FILE_ID, "name": "book.xlsx", "sha1": "abc", "etag": "e2"}]
    }

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_details_rows",
        return_value=_SAMPLE_ROWS,
    ), patch(
        "integrations.box.excel.business.workbook_editor.apply_rows_to_details",
        return_value={"bytes": b"new-xlsx", "applied": 1, "skipped": 0},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, patch.object(
        BoxExcelUpdateService, "_record_push"
    ), _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        svc.handle(_outbox_row(), _insert_payload())

    client.download_file.assert_called_once()
    push_repo.upsert.assert_called_once()
    upsert_kwargs = push_repo.upsert.call_args.kwargs
    assert upsert_kwargs["box_file_id"] == _BOX_FILE_ID
    assert upsert_kwargs["entity_type"] == _ENTITY_TYPE
    assert upsert_kwargs["entity_public_id"] == _ENTITY_PUBLIC_ID
    assert upsert_kwargs["content_hash"] == _content_hash(_SAMPLE_ROWS)


def test_batch_carries_only_fresh_entities_into_locked_path(excel_service):
    svc, push_repo = excel_service
    rows_a = [_details_row("a")]
    rows_b = [_details_row("b")]
    hash_a = _content_hash(rows_a)
    hash_b = _content_hash(rows_b)

    push_repo.read_all_for_file.return_value = [
        BoxWorkbookEntityPush(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            content_hash=hash_a,
        )
    ]

    captured_insert_rows = []

    def _capture_run_locked(self, **kwargs):
        captured_insert_rows.extend(kwargs.get("insert_rows") or [])
        return True

    client = MagicMock()
    client.get.return_value = {"etag": "etag0", "lock": None, "name": "book.xlsx"}
    client.put.return_value = {"etag": "etag1", "name": "book.xlsx"}
    client.download_file.return_value = b"xlsx-bytes"

    def _build_rows(entity_type, entity_public_id, project_id=None):
        if entity_public_id == _ENTITY_PUBLIC_ID:
            return rows_a
        if entity_public_id == _ENTITY_PUBLIC_ID_2:
            return rows_b
        return []

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_details_rows",
        side_effect=_build_rows,
    ), patch(
        "integrations.box.excel.business.workbook_editor.apply_rows_to_details",
        return_value={"bytes": None, "applied": 0, "skipped": 1},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, patch.object(
        BoxExcelUpdateService, "_run_locked", _capture_run_locked
    ), _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        payload = _insert_payload(
            entities=[
                {"entity_type": _ENTITY_TYPE, "entity_public_id": _ENTITY_PUBLIC_ID},
                {"entity_type": _ENTITY_TYPE, "entity_public_id": _ENTITY_PUBLIC_ID_2},
            ],
        )
        svc.handle(_outbox_row(), payload)

    assert captured_insert_rows == rows_b
    push_repo.upsert.assert_called_once()
    assert push_repo.upsert.call_args.kwargs["entity_public_id"] == _ENTITY_PUBLIC_ID_2
    assert push_repo.upsert.call_args.kwargs["content_hash"] == hash_b


def _stamp_client_mocks():
    client = MagicMock()
    client.get.return_value = {"etag": "etag0", "lock": None, "name": "book.xlsx"}
    client.put.return_value = {"etag": "etag1", "name": "book.xlsx"}
    client.download_file.return_value = b"xlsx-bytes"
    return client


def test_stamp_lost_no_match_does_not_record_freshness(excel_service):
    svc, push_repo = excel_service
    push_repo.read_one.return_value = None
    client = _stamp_client_mocks()

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_invoice_draw_stamp_pairs",
        return_value=_STAMP_PAIRS,
    ), patch(
        "integrations.box.excel.business.workbook_editor.stamp_columns_by_key",
        return_value={"bytes": None, "applied": 0, "skipped": 1, "matched": 0},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        svc.handle(
            _outbox_row(entity_type="invoice", entity_public_id=_INVOICE_PUBLIC_ID),
            _stamp_payload(),
        )

    push_repo.upsert.assert_not_called()


def test_stamp_partial_loss_bytes_none_does_not_record_freshness(excel_service):
    """matched>0 with skipped>0 and bytes=None: some keys landed, others not."""
    svc, push_repo = excel_service
    push_repo.read_one.return_value = None
    client = _stamp_client_mocks()

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_invoice_draw_stamp_pairs",
        return_value=_STAMP_PAIRS,
    ), patch(
        "integrations.box.excel.business.workbook_editor.stamp_columns_by_key",
        return_value={"bytes": None, "applied": 0, "skipped": 1, "matched": 1},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        svc.handle(
            _outbox_row(entity_type="invoice", entity_public_id=_INVOICE_PUBLIC_ID),
            _stamp_payload(),
        )

    push_repo.upsert.assert_not_called()


def test_stamp_partial_success_with_bytes_does_not_record_freshness(excel_service):
    """matched>0 with skipped>0 and applied>0: partial write must not cache full plan."""
    svc, push_repo = excel_service
    push_repo.read_one.return_value = None
    client = _stamp_client_mocks()
    client.upload_file_version.return_value = {
        "entries": [{"id": _BOX_FILE_ID, "name": "book.xlsx", "sha1": "abc", "etag": "e2"}]
    }

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_invoice_draw_stamp_pairs",
        return_value=_STAMP_PAIRS,
    ), patch(
        "integrations.box.excel.business.workbook_editor.stamp_columns_by_key",
        return_value={"bytes": b"new-xlsx", "applied": 1, "skipped": 1, "matched": 1},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, patch.object(
        BoxExcelUpdateService, "_record_push"
    ), _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        svc.handle(
            _outbox_row(entity_type="invoice", entity_public_id=_INVOICE_PUBLIC_ID),
            _stamp_payload(),
        )

    client.upload_file_version.assert_called_once()
    push_repo.upsert.assert_not_called()


def test_stamp_already_present_still_records_freshness(excel_service):
    svc, push_repo = excel_service
    push_repo.read_one.return_value = None
    from integrations.box.excel.business.row_builder import DRAW_REQUEST_COL_INDEX

    stamp_updates = [
        (source_pid, {DRAW_REQUEST_COL_INDEX: draw_value})
        for source_pid, draw_value in _STAMP_PAIRS
    ]
    stamp_hash = _content_hash(stamp_updates)
    client = _stamp_client_mocks()

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_invoice_draw_stamp_pairs",
        return_value=_STAMP_PAIRS,
    ), patch(
        "integrations.box.excel.business.workbook_editor.stamp_columns_by_key",
        return_value={"bytes": None, "applied": 0, "skipped": 0, "matched": 1},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        svc.handle(
            _outbox_row(entity_type="invoice", entity_public_id=_INVOICE_PUBLIC_ID),
            _stamp_payload(),
        )

    push_repo.upsert.assert_called_once_with(
        box_file_id=_BOX_FILE_ID,
        entity_type=STAMP_LIKE_ENTITY_TYPE,
        entity_public_id=_INVOICE_PUBLIC_ID,
        content_hash=stamp_hash,
    )


def test_freshness_upsert_failure_does_not_raise(excel_service):
    svc, push_repo = excel_service
    push_repo.read_all_for_file.return_value = [
        BoxWorkbookEntityPush(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            content_hash="old-hash",
        )
    ]
    push_repo.upsert.side_effect = RuntimeError("db down")

    client = MagicMock()
    client.get.return_value = {"etag": "etag0", "lock": None, "name": "book.xlsx"}
    client.put.return_value = {"etag": "etag1", "name": "book.xlsx"}
    client.download_file.return_value = b"xlsx-bytes"
    client.upload_file_version.return_value = {
        "entries": [{"id": _BOX_FILE_ID, "name": "book.xlsx", "sha1": "abc", "etag": "e2"}]
    }

    with patch(
        "integrations.box.excel.persistence.repo.BoxWorkbookEntityPushRepository",
        return_value=push_repo,
    ), patch(
        "integrations.box.excel.business.row_builder.build_details_rows",
        return_value=_SAMPLE_ROWS,
    ), patch(
        "integrations.box.excel.business.workbook_editor.apply_rows_to_details",
        return_value={"bytes": b"new-xlsx", "applied": 1, "skipped": 0},
    ), patch(
        "integrations.box.base.client.BoxHttpClient",
    ) as mock_http_cls, patch.object(
        BoxExcelUpdateService, "_record_push"
    ), _lock_always_acquired():
        mock_http_cls.return_value.__enter__.return_value = client
        svc.handle(_outbox_row(), _insert_payload())

    push_repo.upsert.assert_called_once()


def test_filter_fresh_insert_entities_hash_is_order_independent():
    """`_filter_fresh_insert_entities` itself must canonicalize row order
    before hashing — not just `_content_hash` in isolation — since
    `build_details_rows`'s underlying feeder queries don't guarantee stable
    row order call-to-call."""
    push_repo = MagicMock()
    push_repo.read_all_for_file.return_value = []
    rows_order_a = [_details_row("b"), _details_row("a")]
    rows_order_b = [_details_row("a"), _details_row("b")]
    entity_list = [{"entity_type": _ENTITY_TYPE, "entity_public_id": _ENTITY_PUBLIC_ID}]

    with patch(
        "integrations.box.excel.business.row_builder.build_details_rows",
        return_value=rows_order_a,
    ):
        fresh_a, _ = BoxExcelUpdateService._filter_fresh_insert_entities(
            push_cache_repo=push_repo,
            box_file_id=_BOX_FILE_ID,
            entity_list=entity_list,
            project_id=1,
        )
    with patch(
        "integrations.box.excel.business.row_builder.build_details_rows",
        return_value=rows_order_b,
    ):
        fresh_b, _ = BoxExcelUpdateService._filter_fresh_insert_entities(
            push_cache_repo=push_repo,
            box_file_id=_BOX_FILE_ID,
            entity_list=entity_list,
            project_id=1,
        )

    assert fresh_a[0]["content_hash"] == fresh_b[0]["content_hash"]


def test_build_stamp_plan_hash_is_order_independent():
    """`_build_stamp_plan` itself must canonicalize pair order before
    hashing — `build_invoice_draw_stamp_pairs`'s query has no ORDER BY at
    all, so row order is not guaranteed call-to-call."""
    pairs_order_a = [("b", "DR-2"), ("a", "DR-1")]
    pairs_order_b = [("a", "DR-1"), ("b", "DR-2")]

    with patch(
        "integrations.box.excel.business.row_builder.build_invoice_draw_stamp_pairs",
        return_value=pairs_order_a,
    ):
        _, hash_a = BoxExcelUpdateService._build_stamp_plan(
            operation="stamp_draw_request",
            entity_public_id=_INVOICE_PUBLIC_ID,
            source_public_ids=None,
        )
    with patch(
        "integrations.box.excel.business.row_builder.build_invoice_draw_stamp_pairs",
        return_value=pairs_order_b,
    ):
        _, hash_b = BoxExcelUpdateService._build_stamp_plan(
            operation="stamp_draw_request",
            entity_public_id=_INVOICE_PUBLIC_ID,
            source_public_ids=None,
        )

    assert hash_a == hash_b
