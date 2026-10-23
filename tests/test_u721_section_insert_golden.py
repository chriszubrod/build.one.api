"""
U-721a golden characterization tests — DETAILS workbook section insert/append paths.

Pins pre-refactor enqueue kwargs (insert + append) and return dicts for the five
Bill / BillCredit / Expense sync paths so U-721b refactor can prove byte-identical
outbox writes. Values captured from live code 2026-10-23; do not hand-edit.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ID = 5
BATCH_ENTITY_PUBLIC_ID = "6085baa5-a8c9-592e-9a7f-c6289f0eb5cf"
DRIVE_ID = "drv"
ITEM_ID = "itm"
WORKSHEET = "DETAILS"

BILL_PUBLIC_ID = "11111111-1111-4111-8111-111111111101"
EXPENSE_PUBLIC_ID = "22222222-2222-4222-8222-222222222202"
CREDIT_PUBLIC_ID = "33333333-3333-4333-8333-333333333303"

assert BATCH_ENTITY_PUBLIC_ID == str(
    uuid.uuid5(uuid.NAMESPACE_URL, f"ms-excel-batch:{PROJECT_ID}")
)

DEFAULT_SCC_MAP = {1: "10.01", 2: "20.01", 3: "99.99", 4: "10.01", 5: None}


def _section(scc: str) -> list:
    return ["", scc.split(".")[0], scc] + [""] * 23


def _data_row(scc: str, tag: str, *, type_col: str = "Bill", z: str | None = None) -> list:
    row = ["", scc.split(".")[0], scc] + [""] * 23
    row[8], row[9], row[12], row[25] = "2026-01-01", "Vendor", type_col, z if z is not None else tag
    return row


def _std_sheet() -> list[list]:
    header = ["", "", "SCC"] + [""] * 23
    return [
        header,
        _section("10.01"),
        _data_row("10.01", "old-a"),
        _section("20.01"),
        _data_row("20.01", "old-b"),
        _section("30.01"),
        _data_row("30.01", "old-c"),
    ]


def _tie_sheet() -> list[list]:
    header = ["", "", "SCC"] + [""] * 23
    return [header, _section("10.01"), _data_row("20.01", "old-b"), _section("30.01")]


def _scc_read_side_effect(scc_map: dict, *, force_none: bool = False):
    def _read(scc_id=None, *, id=None):
        if force_none:
            return None
        raw = id if id is not None else scc_id
        if raw is None:
            return None
        key = int(raw) if str(raw).lstrip("-").isdigit() else raw
        if key not in scc_map:
            return None
        num = scc_map[key]
        if num is None:
            return SimpleNamespace(number=None)
        return SimpleNamespace(number=num)

    return _read


def _capture_ms_outbox_calls(ms_outbox: MagicMock) -> list[dict]:
    captured = []
    for method in ("enqueue_excel_insert", "enqueue_excel_append"):
        mock = getattr(ms_outbox, method)
        for call in mock.call_args_list:
            captured.append({"method": method, "kwargs": dict(call.kwargs)})
    return captured


def _assert_golden(ms_outbox: MagicMock, result: dict, expected_calls: list, expected_result: dict) -> None:
    assert _capture_ms_outbox_calls(ms_outbox) == expected_calls
    assert result == expected_result


def _workbook_patches(mod, sheet: list[list], ms_outbox: MagicMock | None = None):
    if ms_outbox is None:
        ms_outbox = MagicMock()
        ms_outbox.enqueue_excel_insert.return_value = SimpleNamespace(public_id="ob-ins")
        ms_outbox.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    driveitem_repo = MagicMock()
    driveitem_repo.read_all.return_value = [
        SimpleNamespace(id=7, ms_drive_id=3, item_id=ITEM_ID)
    ]
    patches = (
        patch.object(mod, "MsDriveItemRepository", return_value=driveitem_repo),
        patch.object(mod, "create_workbook_session", return_value="sess"),
        patch.object(mod, "close_workbook_session"),
        patch.object(
            mod,
            "get_excel_used_range_values",
            return_value={
                "status_code": 200,
                "range": {"values": [list(r) for r in sheet]},
            },
        ),
        patch("integrations.ms.outbox.business.service.MsOutboxService", return_value=ms_outbox),
    )
    return ms_outbox, patches


def _bill_line(n: int, scc_id, **kw):
    defaults = {
        "id": n,
        "public_id": f"li-{n:04d}-aaaa-4111-8111-11111111{n:04d}",
        "sub_cost_code_id": scc_id,
        "description": f"line {n}",
        "price": Decimal("10.50"),
        "amount": Decimal("9.00"),
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _bill_service(mod, scc_map=None, *, force_scc_none: bool = False):
    svc = mod.BillService.__new__(mod.BillService)
    svc._project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value={"id": 7, "worksheet_name": WORKSHEET})
    )
    svc._drive_repo = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(drive_id=DRIVE_ID))
    )
    svc.vendor_service = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(name="Acme"))
    )
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = _scc_read_side_effect(
        scc_map or DEFAULT_SCC_MAP, force_none=force_scc_none
    )
    return svc


def _bill_header():
    return SimpleNamespace(
        public_id=BILL_PUBLIC_ID,
        vendor_id=1,
        bill_date="2026-10-01",
        bill_number="B-100",
    )


def _credit_line(n: int, scc_id, **kw):
    defaults = {
        "id": n,
        "public_id": f"cli-{n:04d}-aaaa-4111-8111-11111111{n:04d}",
        "sub_cost_code_id": scc_id,
        "description": f"credit line {n}",
        "amount": Decimal("25.75"),
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _credit_service(mod, scc_map=None):
    svc = mod.BillCreditCompleteService.__new__(mod.BillCreditCompleteService)
    svc.project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value={"id": 7, "worksheet_name": WORKSHEET})
    )
    svc.drive_repo = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(drive_id=DRIVE_ID))
    )
    svc.vendor_service = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(name="Acme"))
    )
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = _scc_read_side_effect(
        scc_map or DEFAULT_SCC_MAP
    )
    return svc


def _credit_header():
    return SimpleNamespace(
        public_id=CREDIT_PUBLIC_ID,
        vendor_id=1,
        credit_date="2026-10-02",
        credit_number="C-50",
    )


def _expense_line(n: int, scc_id, **kw):
    defaults = {
        "id": n,
        "public_id": f"eli-{n:04d}-aaaa-4111-8111-11111111{n:04d}",
        "sub_cost_code_id": scc_id,
        "project_id": PROJECT_ID,
        "description": f"exp line {n}",
        "price": Decimal("40.00"),
        "amount": Decimal("35.00"),
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _expense_service(mod, scc_map=None, *, force_scc_none: bool = False):
    svc = mod.ExpenseService.__new__(mod.ExpenseService)
    svc._project_excel_connector = MagicMock(
        get_excel_for_project=MagicMock(return_value={"id": 7, "worksheet_name": WORKSHEET})
    )
    svc._drive_repo = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(drive_id=DRIVE_ID))
    )
    svc.vendor_service = MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(name="Ramp Vendor"))
    )
    svc.sub_cost_code_service = MagicMock()
    svc.sub_cost_code_service.read_by_id.side_effect = _scc_read_side_effect(
        scc_map or DEFAULT_SCC_MAP, force_none=force_scc_none
    )
    svc.sub_cost_code_service.is_holding_code = MagicMock(return_value=False)
    return svc


def _expense_header(**kw):
    defaults = {
        "public_id": EXPENSE_PUBLIC_ID,
        "vendor_id": 1,
        "expense_date": "2026-10-03",
        "reference_number": "REF-9",
        "is_credit": False,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


GOLDEN = {
    "bill_single_baseline": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 3', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0003-aaaa-4111-8111-111111110003'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 6', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0006-aaaa-4111-8111-111111110006'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 4', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0004-aaaa-4111-8111-111111110004'],
                        ['', '', '', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 5', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0005-aaaa-4111-8111-111111110005'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 6 row(s) for Excel sync',
            "synced_count": 6,
            "errors": [],
        },
    },
    "bill_single_same_number_two_ids": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 2 row(s) for Excel sync',
            "synced_count": 2,
            "errors": [],
        },
    },
    "bill_single_col_z_skip": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "errors": [],
        },
    },
    "bill_single_tie": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 2 row(s) for Excel sync',
            "synced_count": 2,
            "errors": [],
        },
    },
    "bill_single_scc_read_none": {
        "calls": [
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '', '', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "errors": [],
        },
    },
    "bill_single_row_build_fail": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "errors": [
                {
                    "line_item_id": 2,
                    "line_item_public_id": 'li-0002-aaaa-4111-8111-111111110002',
                    "error": "Error building Excel row: float() argument must be a string or a real number, not 'object'",
                },
            ],
        },
    },
    "bill_single_insert_refuse": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "errors": [
                {
                    "sub_cost_code": '10.01',
                    "error": 'Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)',
                },
            ],
        },
    },
    "bill_single_append_refuse": {
        "calls": [
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                        ['', '', '', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Queued 0 row(s) for Excel sync',
            "synced_count": 0,
            "errors": [
                {
                    "error": 'Excel append enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)',
                },
            ],
        },
    },
    "bill_single_enqueue_raise": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Bill',
                    "entity_public_id": '11111111-1111-4111-8111-111111111101',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Error syncing to Excel: boom',
            "synced_count": 0,
            "errors": [
                {
                    "error": 'boom',
                },
            ],
        },
    },
    "bill_batch_baseline": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 3', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0003-aaaa-4111-8111-111111110003'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 6', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0006-aaaa-4111-8111-111111110006'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 4', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0004-aaaa-4111-8111-111111110004'],
                        ['', '', '', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 5', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0005-aaaa-4111-8111-111111110005'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 6 row(s) to Excel workbook',
            "synced_count": 6,
            "errors": [],
        },
    },
    "bill_batch_same_number_two_ids": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 2 row(s) to Excel workbook',
            "synced_count": 2,
            "errors": [],
        },
    },
    "bill_batch_row_build_fail": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "errors": [
                {
                    "line_item_id": 2,
                    "error": "float() argument must be a string or a real number, not 'object'",
                },
            ],
        },
    },
    "bill_batch_insert_refuse": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 2', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "errors": [
                {
                    "sub_cost_code": '10.01',
                    "error": 'Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)',
                },
            ],
        },
    },
    "bill_batch_enqueue_raise": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-01', 'Acme', 'B-100', 'line 1', 'Bill', 10.5, '', '', '', '', '', '', '', '', '', '', '', 'li-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Error syncing to Excel: boom',
            "synced_count": 0,
            "errors": [
                {
                    "error": 'boom',
                },
            ],
        },
    },
    "bc_batch_baseline": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 3', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0003-aaaa-4111-8111-111111110003'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 1', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 2', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 4', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0004-aaaa-4111-8111-111111110004'],
                        ['', '', '', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 5', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0005-aaaa-4111-8111-111111110005'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 5 row(s) to Excel workbook',
            "synced_count": 5,
            "errors": [],
        },
    },
    "bc_batch_positive_n": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 1', 'Credit', 100.0, '', '', '', '', '', '', '', '', '', '', '', 'cli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "errors": [],
        },
    },
    "bc_batch_same_number_two_ids": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 1', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 2', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 2 row(s) to Excel workbook',
            "synced_count": 2,
            "errors": [],
        },
    },
    "bc_batch_insert_refuse": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 1', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 2', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "errors": [
                {
                    "sub_cost_code": '10.01',
                    "error": 'Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)',
                },
            ],
        },
    },
    "bc_batch_enqueue_raise": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 1', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Error syncing to Excel: boom',
            "synced_count": 0,
            "errors": [
                {
                    "error": 'boom',
                },
            ],
        },
    },
    "bc_batch_row_build_fail": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'BillCreditBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-02', 'Acme', 'C-50', 'credit line 1', 'Credit', 25.75, '', '', '', '', '', '', '', '', '', '', '', 'cli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "errors": [
                {
                    "line_item_id": 2,
                    "error": "float() argument must be a string or a real number, not 'object'",
                },
            ],
        },
    },
    "exp_single_baseline": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 5', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0005-aaaa-4111-8111-111111110005'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 3', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0003-aaaa-4111-8111-111111110003'],
                        ['', '', '', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 4', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0004-aaaa-4111-8111-111111110004'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 5 row(s) for Excel sync',
            "synced_count": 5,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_single_is_credit": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-CR', 'exp line 1', 'Expense Credit', -50.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_single_same_number_two_ids": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 2 row(s) for Excel sync',
            "synced_count": 2,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_single_row_build_fail": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [
                {
                    "line_item_id": 2,
                    "line_item_public_id": 'eli-0002-aaaa-4111-8111-111111110002',
                    "error": "[<class 'decimal.ConversionSyntax'>]",
                },
            ],
        },
    },
    "exp_single_enqueue_raise": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Error syncing to Excel: boom',
            "synced_count": 0,
            "errors": [
                {
                    "error": 'boom',
                },
            ],
        },
    },
    "exp_single_scc_read_none": {
        "calls": [
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '', '', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_single_insert_refuse": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [
                {
                    "sub_cost_code": '10.01',
                    "error": 'Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)',
                },
            ],
        },
    },
    "exp_single_append_refuse": {
        "calls": [
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                        ['', '', '', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Queued 0 row(s) for Excel sync',
            "synced_count": 0,
            "deferred_holding_count": 0,
            "errors": [
                {
                    "error": 'Excel append enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)',
                },
            ],
        },
    },
    "exp_single_col_z_skip": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'Expense',
                    "entity_public_id": '22222222-2222-4222-8222-222222222202',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Queued 1 row(s) for Excel sync',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_batch_baseline": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 6,
                    "values": [
                        ['', '20', '20.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 3', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0003-aaaa-4111-8111-111111110003'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 6', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0006-aaaa-4111-8111-111111110006'],
                    ],
                    "session_id": None,
                },
            },
            {
                "method": 'enqueue_excel_append',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "values": [
                        ['', '99', '99.99', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 4', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0004-aaaa-4111-8111-111111110004'],
                        ['', '', '', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 5', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0005-aaaa-4111-8111-111111110005'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 6 row(s) to Excel workbook',
            "synced_count": 6,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_batch_same_number_two_ids": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 2', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0002-aaaa-4111-8111-111111110002'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 2 row(s) to Excel workbook',
            "synced_count": 2,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_batch_is_credit": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-CR', 'exp line 1', 'Expense Credit', -50.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [],
        },
    },
    "exp_batch_row_build_fail": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": True,
            "message": 'Synced 1 row(s) to Excel workbook',
            "synced_count": 1,
            "deferred_holding_count": 0,
            "errors": [
                {
                    "line_item_id": 2,
                    "error": "[<class 'decimal.ConversionSyntax'>]",
                },
            ],
        },
    },
    "exp_batch_enqueue_raise": {
        "calls": [
            {
                "method": 'enqueue_excel_insert',
                "kwargs": {
                    "entity_type": 'ExpenseBatch',
                    "entity_public_id": '6085baa5-a8c9-592e-9a7f-c6289f0eb5cf',
                    "drive_id": 'drv',
                    "item_id": 'itm',
                    "worksheet_name": 'DETAILS',
                    "row_index": 4,
                    "values": [
                        ['', '10', '10.01', '', '', '', '', '', '2026-10-03', 'Ramp Vendor', 'REF-9', 'exp line 1', 'Expense', 40.0, '', '', '', '', '', '', '', '', '', '', '', 'eli-0001-aaaa-4111-8111-111111110001'],
                    ],
                    "session_id": None,
                },
            },
        ],
        "result": {
            "success": False,
            "message": 'Error syncing to Excel: boom',
            "synced_count": 0,
            "errors": [
                {
                    "error": 'boom',
                },
            ],
        },
    },
}

def test_bill_single_golden_baseline():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    
    line_items = [_bill_line(1,1), _bill_line(2,1), _bill_line(3,2), _bill_line(4,3), _bill_line(5,None), _bill_line(6,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_baseline"]["calls"], GOLDEN["bill_single_baseline"]["result"])

def test_bill_single_golden_two_scc_ids_same_number():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    
    line_items = [_bill_line(1,1), _bill_line(2,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_same_number_two_ids"]["calls"], GOLDEN["bill_single_same_number_two_ids"]["result"])

def test_bill_single_golden_column_z_dedup():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    sheet.append(_data_row("10.01", "existing", z="li-0001-aaaa-4111-8111-111111110001"))
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    line_items = [_bill_line(1,1), _bill_line(2,2)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_col_z_skip"]["calls"], GOLDEN["bill_single_col_z_skip"]["result"])

def test_bill_single_golden_equal_index_tie():

    import entities.bill.business.service as mod
    sheet = _tie_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod, scc_map={1: '10.01', 2: '20.01'})
    bill = _bill_header()
    
    line_items = [_bill_line(1,2), _bill_line(2,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_tie"]["calls"], GOLDEN["bill_single_tie"]["result"])

def test_bill_single_golden_scc_read_returns_none():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod, force_scc_none=True)
    bill = _bill_header()
    
    line_items = [_bill_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_scc_read_none"]["calls"], GOLDEN["bill_single_scc_read_none"]["result"])

def test_bill_single_golden_row_build_error():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    bad = _bill_line(2,1); bad.price = object(); line_items = [_bill_line(1,1), bad]
    line_items = line_items
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_row_build_fail"]["calls"], GOLDEN["bill_single_row_build_fail"]["result"])

def test_bill_single_golden_insert_enqueue_refused():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.return_value = None
    ms.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _bill_service(mod)
    bill = _bill_header()
    line_items = [_bill_line(1,1), _bill_line(2,3)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_insert_refuse"]["calls"], GOLDEN["bill_single_insert_refuse"]["result"])

def test_bill_single_golden_append_enqueue_refused():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.return_value = SimpleNamespace(public_id="ob-ins")
    ms.enqueue_excel_append.return_value = None
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _bill_service(mod)
    bill = _bill_header()
    line_items = [_bill_line(1,3), _bill_line(2,None)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_append_refuse"]["calls"], GOLDEN["bill_single_append_refuse"]["result"])

def test_bill_single_golden_enqueue_raises():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.side_effect = RuntimeError("boom")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _bill_service(mod)
    bill = _bill_header()
    line_items = [_bill_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(bill, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_single_enqueue_raise"]["calls"], GOLDEN["bill_single_enqueue_raise"]["result"])

def test_bill_batch_golden_baseline():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    
    line_items = [_bill_line(1,1), _bill_line(2,1), _bill_line(3,2), _bill_line(4,3), _bill_line(5,None), _bill_line(6,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel([(bill, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_batch_baseline"]["calls"], GOLDEN["bill_batch_baseline"]["result"])

def test_bill_batch_golden_two_scc_ids_same_number():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    
    line_items = [_bill_line(1,1), _bill_line(2,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel([(bill, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_batch_same_number_two_ids"]["calls"], GOLDEN["bill_batch_same_number_two_ids"]["result"])

def test_bill_batch_golden_row_build_error():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _bill_service(mod)
    bill = _bill_header()
    bad = _bill_line(2,1); bad.price = object(); line_items = [_bill_line(1,1), bad]
    line_items = line_items
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel([(bill, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_batch_row_build_fail"]["calls"], GOLDEN["bill_batch_row_build_fail"]["result"])

def test_bill_batch_golden_insert_enqueue_refused():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.return_value = None
    ms.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _bill_service(mod)
    bill = _bill_header()
    line_items = [_bill_line(1,1), _bill_line(2,3)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel([(bill, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_batch_insert_refuse"]["calls"], GOLDEN["bill_batch_insert_refuse"]["result"])

def test_bill_batch_golden_enqueue_raises():

    import entities.bill.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.side_effect = RuntimeError("boom")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _bill_service(mod)
    bill = _bill_header()
    line_items = [_bill_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bills_batch_to_excel([(bill, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bill_batch_enqueue_raise"]["calls"], GOLDEN["bill_batch_enqueue_raise"]["result"])

def test_bill_credit_batch_golden_baseline():

    import entities.bill_credit.business.complete_service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _credit_service(mod)
    credit = _credit_header()
    
    line_items = [_credit_line(1,1), _credit_line(2,1), _credit_line(3,2), _credit_line(4,3), _credit_line(5,None)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel([(credit, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bc_batch_baseline"]["calls"], GOLDEN["bc_batch_baseline"]["result"])

def test_bill_credit_batch_golden_positive_amount():

    import entities.bill_credit.business.complete_service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _credit_service(mod)
    credit = _credit_header()
    
    line_items = [_credit_line(1,1, amount=Decimal('100'))]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel([(credit, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bc_batch_positive_n"]["calls"], GOLDEN["bc_batch_positive_n"]["result"])

def test_bill_credit_batch_golden_two_scc_ids_same_number():

    import entities.bill_credit.business.complete_service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _credit_service(mod)
    credit = _credit_header()
    
    line_items = [_credit_line(1,1), _credit_line(2,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel([(credit, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bc_batch_same_number_two_ids"]["calls"], GOLDEN["bc_batch_same_number_two_ids"]["result"])

def test_bill_credit_batch_golden_insert_enqueue_refused():

    import entities.bill_credit.business.complete_service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.return_value = None
    ms.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    ms.enqueue_excel_insert.return_value = None
    ms.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _credit_service(mod)
    credit = _credit_header()
    line_items = [_credit_line(1,1), _credit_line(2,3)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel([(credit, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bc_batch_insert_refuse"]["calls"], GOLDEN["bc_batch_insert_refuse"]["result"])

def test_bill_credit_batch_golden_enqueue_raises():

    import entities.bill_credit.business.complete_service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.side_effect = RuntimeError("boom")
    ms.enqueue_excel_insert.side_effect = RuntimeError("boom")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _credit_service(mod)
    credit = _credit_header()
    line_items = [_credit_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel([(credit, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bc_batch_enqueue_raise"]["calls"], GOLDEN["bc_batch_enqueue_raise"]["result"])

def test_bill_credit_batch_golden_row_build_error():

    import entities.bill_credit.business.complete_service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _credit_service(mod)
    credit = _credit_header()
    bad = _credit_line(2,1); bad.amount = object(); line_items = [_credit_line(1,1), bad]
    line_items = line_items
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_bill_credits_batch_to_excel([(credit, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["bc_batch_row_build_fail"]["calls"], GOLDEN["bc_batch_row_build_fail"]["result"])

def test_expense_single_golden_baseline():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    
    line_items = [_expense_line(1,1), _expense_line(2,2), _expense_line(3,3), _expense_line(4,None), _expense_line(5,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_baseline"]["calls"], GOLDEN["exp_single_baseline"]["result"])

def test_expense_single_golden_is_credit():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header(is_credit=True, reference_number='REF-CR')
    
    line_items = [_expense_line(1,1, price=Decimal('50'))]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_is_credit"]["calls"], GOLDEN["exp_single_is_credit"]["result"])

def test_expense_single_golden_two_scc_ids_same_number():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    
    line_items = [_expense_line(1,1), _expense_line(2,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_same_number_two_ids"]["calls"], GOLDEN["exp_single_same_number_two_ids"]["result"])

def test_expense_single_golden_row_build_error():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    bad = _expense_line(2,1); bad.price = object(); line_items = [_expense_line(1,1), bad]
    line_items = line_items
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_row_build_fail"]["calls"], GOLDEN["exp_single_row_build_fail"]["result"])

def test_expense_single_golden_enqueue_raises():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.side_effect = RuntimeError("boom")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _expense_service(mod)
    expense = _expense_header()
    line_items = [_expense_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_enqueue_raise"]["calls"], GOLDEN["exp_single_enqueue_raise"]["result"])

def test_expense_single_golden_scc_read_returns_none():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod, force_scc_none=True)
    expense = _expense_header()
    
    line_items = [_expense_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_scc_read_none"]["calls"], GOLDEN["exp_single_scc_read_none"]["result"])

def test_expense_single_golden_insert_enqueue_refused():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.return_value = None
    ms.enqueue_excel_append.return_value = SimpleNamespace(public_id="ob-app")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _expense_service(mod)
    expense = _expense_header()
    line_items = [_expense_line(1,1), _expense_line(2,3)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_insert_refuse"]["calls"], GOLDEN["exp_single_insert_refuse"]["result"])

def test_expense_single_golden_append_enqueue_refused():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.return_value = SimpleNamespace(public_id="ob-ins")
    ms.enqueue_excel_append.return_value = None
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _expense_service(mod)
    expense = _expense_header()
    line_items = [_expense_line(1,3), _expense_line(2,None)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_append_refuse"]["calls"], GOLDEN["exp_single_append_refuse"]["result"])

def test_expense_single_golden_column_z_dedup():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    sheet.append(_data_row("10.01", "existing", z="eli-0001-aaaa-4111-8111-111111110001"))
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    line_items = [_expense_line(1,1), _expense_line(2,2)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_to_excel_workbook(expense, line_items, PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_single_col_z_skip"]["calls"], GOLDEN["exp_single_col_z_skip"]["result"])

def test_expense_batch_golden_baseline():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    
    line_items = [_expense_line(1,1), _expense_line(2,1), _expense_line(3,2), _expense_line(4,3), _expense_line(5,None), _expense_line(6,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_expenses_batch_to_excel([(expense, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_batch_baseline"]["calls"], GOLDEN["exp_batch_baseline"]["result"])

def test_expense_batch_golden_two_scc_ids_same_number():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    
    line_items = [_expense_line(1,1), _expense_line(2,4)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_expenses_batch_to_excel([(expense, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_batch_same_number_two_ids"]["calls"], GOLDEN["exp_batch_same_number_two_ids"]["result"])

def test_expense_batch_golden_is_credit():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header(is_credit=True, reference_number='REF-CR')
    
    line_items = [_expense_line(1,1, price=Decimal('50'))]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_expenses_batch_to_excel([(expense, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_batch_is_credit"]["calls"], GOLDEN["exp_batch_is_credit"]["result"])

def test_expense_batch_golden_row_build_error():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms, patches = _workbook_patches(mod, sheet)
    svc = _expense_service(mod)
    expense = _expense_header()
    bad = _expense_line(2,1); bad.price = object(); line_items = [_expense_line(1,1), bad]
    line_items = line_items
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_expenses_batch_to_excel([(expense, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_batch_row_build_fail"]["calls"], GOLDEN["exp_batch_row_build_fail"]["result"])

def test_expense_batch_golden_enqueue_raises():

    import entities.expense.business.service as mod
    sheet = _std_sheet()
    ms = MagicMock()
    ms.enqueue_excel_insert.side_effect = RuntimeError("boom")
    ms, patches = _workbook_patches(mod, sheet, ms_outbox=ms)
    svc = _expense_service(mod)
    expense = _expense_header()
    line_items = [_expense_line(1,1)]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = svc.sync_expenses_batch_to_excel([(expense, line_items)], PROJECT_ID)
    _assert_golden(ms, result, GOLDEN["exp_batch_enqueue_raise"]["calls"], GOLDEN["exp_batch_enqueue_raise"]["result"])

