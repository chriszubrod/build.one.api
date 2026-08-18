"""Pure-logic tests for U-264 invoice line duplicate repair classification."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from scripts.repair_invoice_line_duplicates import (
    LocalInvoiceLineRow,
    MappedQboLineContent,
    QboInvoiceLineRow,
    RepairEntry,
    _check_expected_count,
    apply_repairs,
    classify_invoice_line_items,
    filter_repair_entries_by_manifest,
    normalize_fingerprint,
)


def _local(
    *,
    id: int,
    invoice_id: int = 1,
    description: str = "Work",
    amount=Decimal("100"),
    is_mapped: bool = False,
    source_type: str = "Manual",
    mapped_qbo_description: str | None = None,
    mapped_qbo_amount=None,
) -> LocalInvoiceLineRow:
    mapped_qbo_line = None
    if is_mapped:
        qbo_desc = mapped_qbo_description if mapped_qbo_description is not None else description
        qbo_amt = mapped_qbo_amount if mapped_qbo_amount is not None else amount
        mapped_qbo_line = MappedQboLineContent(
            description=qbo_desc,
            amount=qbo_amt if qbo_amt is None else Decimal(str(qbo_amt)),
        )
    return LocalInvoiceLineRow(
        id=id,
        public_id=f"pid-{id}",
        invoice_id=invoice_id,
        invoice_number=f"INV-{invoice_id}",
        source_type=source_type,
        description=description,
        amount=amount if amount is None else Decimal(str(amount)),
        is_mapped=is_mapped,
        mapped_qbo_line=mapped_qbo_line,
    )


def _qbo(description: str = "Work", amount=Decimal("100")) -> QboInvoiceLineRow:
    return QboInvoiceLineRow(
        description=description,
        amount=amount if amount is None else Decimal(str(amount)),
    )


def _repair_entry(*, row_id: int, public_id: str, **overrides) -> RepairEntry:
    defaults = {
        "invoice_id": 1,
        "invoice_number": "INV-1",
        "description": "d",
        "amount": Decimal("1"),
        "mapped_sibling_id": 10,
        "qbo_line_count": 1,
        "local_mapped_count": 1,
    }
    return RepairEntry(
        row_id=row_id,
        public_id=public_id,
        **{**defaults, **overrides},
    )


@pytest.fixture
def apply_repairs_mocks():
    with (
        patch("scripts.repair_invoice_line_duplicates.fetch_local_rows") as mock_fetch_local,
        patch("scripts.repair_invoice_line_duplicates.fetch_qbo_lines_by_invoice") as mock_fetch_qbo,
        patch("scripts.repair_invoice_line_duplicates.load_classification") as mock_load_classification,
        patch("scripts.repair_invoice_line_duplicates.InvoiceLineItemService") as mock_service_cls,
    ):
        mock_load_classification.return_value = MagicMock(repair=(), held_back=())
        mock_service = MagicMock()
        mock_service_cls.return_value = mock_service
        yield {
            "fetch_local": mock_fetch_local,
            "fetch_qbo": mock_fetch_qbo,
            "load_classification": mock_load_classification,
            "service_cls": mock_service_cls,
            "service": mock_service,
        }


def test_normalize_fingerprint_none_description():
    assert normalize_fingerprint(None, Decimal("10")) == ("", "10")


def test_normalize_fingerprint_none_amount():
    assert normalize_fingerprint("  abc  ", None) == ("abc", "")


def test_normalize_fingerprint_negative_amount():
    assert normalize_fingerprint("x", Decimal("-12.50")) == ("x", "-12.5")


@pytest.mark.parametrize(
    "left,right",
    [
        (Decimal("100.00"), Decimal("100")),
        (Decimal("100.0"), Decimal("100")),
        ("100.00", "100"),
    ],
)
def test_normalize_fingerprint_trailing_zero_decimal_equivalence(left, right):
    assert normalize_fingerprint("d", left) == normalize_fingerprint("d", right)


def test_classify_duplicate_with_mapped_sibling_is_repair():
    local = [
        _local(id=10, is_mapped=True, description="Site prep", amount=Decimal("250")),
        _local(id=20, is_mapped=False, description="Site prep", amount=Decimal("250")),
    ]
    qbo = {1: [_qbo("Site prep", Decimal("250"))]}

    result = classify_invoice_line_items(local, qbo)

    assert len(result.repair) == 1
    assert result.repair[0].row_id == 20
    assert result.repair[0].mapped_sibling_id == 10
    assert result.repair[0].qbo_line_count == 1
    assert result.repair[0].local_mapped_count == 1
    assert result.held_back == ()
    assert result.total_unmapped_manual == 1


def test_classify_no_sibling_legacy_held_back():
    local = [_local(id=30, is_mapped=False, description="Unique legacy", amount=Decimal("99"))]
    qbo = {1: []}

    result = classify_invoice_line_items(local, qbo)

    assert result.repair == ()
    assert len(result.held_back) == 1
    assert result.held_back[0].row_id == 30
    assert result.held_back[0].reason == "no_sibling"


def test_classify_unclaimed_qbo_line_held_back():
    local = [
        _local(id=40, is_mapped=True, description="Dup fp", amount=Decimal("50")),
        _local(id=41, is_mapped=False, description="Dup fp", amount=Decimal("50")),
    ]
    qbo = {
        1: [
            _qbo("Dup fp", Decimal("50")),
            _qbo("Dup fp", Decimal("50")),
        ]
    }

    result = classify_invoice_line_items(local, qbo)

    assert result.repair == ()
    assert len(result.held_back) == 1
    assert result.held_back[0].row_id == 41
    assert result.held_back[0].reason == "unclaimed_qbo_line"


def test_classify_unique_row_no_fingerprint_collision_held_back():
    local = [
        _local(id=50, is_mapped=True, description="Mapped A", amount=Decimal("10")),
        _local(id=51, is_mapped=False, description="Unique B", amount=Decimal("20")),
    ]
    qbo = {1: [_qbo("Mapped A", Decimal("10"))]}

    result = classify_invoice_line_items(local, qbo)

    assert result.repair == ()
    assert len(result.held_back) == 1
    assert result.held_back[0].row_id == 51
    assert result.held_back[0].reason == "no_sibling"
    assert result.total_unmapped_manual == 1


def test_classify_two_unmapped_duplicates_both_repair_when_qbo_fully_covered():
    local = [
        _local(id=60, is_mapped=True, description="X", amount=Decimal("1")),
        _local(id=61, is_mapped=False, description="X", amount=Decimal("1")),
        _local(id=62, is_mapped=False, description="X", amount=Decimal("1")),
    ]
    qbo = {1: [_qbo("X", Decimal("1"))]}

    result = classify_invoice_line_items(local, qbo)

    assert {e.row_id for e in result.repair} == {61, 62}
    assert result.held_back == ()


def test_classify_ignores_mapped_and_non_manual_rows():
    local = [
        _local(id=70, source_type="BillLineItem", is_mapped=False),
        _local(id=71, source_type="Manual", is_mapped=True),
    ]

    result = classify_invoice_line_items(local, {})

    assert result.repair == ()
    assert result.held_back == ()
    assert result.total_unmapped_manual == 0


def test_classify_mapped_sibling_qbo_drift_does_not_count():
    """Local content matches fingerprint but mapped QBO line content does not."""
    local = [
        _local(
            id=80,
            is_mapped=True,
            description="Site prep",
            amount=Decimal("250"),
            mapped_qbo_description="Different QBO text",
            mapped_qbo_amount=Decimal("250"),
        ),
        _local(id=81, is_mapped=False, description="Site prep", amount=Decimal("250")),
    ]
    qbo = {1: [_qbo("Site prep", Decimal("250"))]}

    result = classify_invoice_line_items(local, qbo)

    assert result.repair == ()
    assert len(result.held_back) == 1
    assert result.held_back[0].row_id == 81
    assert result.held_back[0].reason == "no_sibling"


def test_classify_three_verified_mapped_siblings_qbo_three_all_unmapped_repairable():
    fp_desc, fp_amt = "Shared", Decimal("10")
    local = [
        _local(id=90, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(id=91, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(id=92, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(id=93, is_mapped=False, description=fp_desc, amount=fp_amt),
        _local(id=94, is_mapped=False, description=fp_desc, amount=fp_amt),
        _local(id=95, is_mapped=False, description=fp_desc, amount=fp_amt),
    ]
    qbo = {
        1: [
            _qbo(fp_desc, fp_amt),
            _qbo(fp_desc, fp_amt),
            _qbo(fp_desc, fp_amt),
        ]
    }

    result = classify_invoice_line_items(local, qbo)

    assert {e.row_id for e in result.repair} == {93, 94, 95}
    assert result.held_back == ()


def test_classify_three_local_mapped_only_two_qbo_identity_verified_qbo_three_held_back():
    fp_desc, fp_amt = "Shared", Decimal("10")
    local = [
        _local(id=100, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(id=101, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(
            id=102,
            is_mapped=True,
            description=fp_desc,
            amount=fp_amt,
            mapped_qbo_description="Drifted",
            mapped_qbo_amount=fp_amt,
        ),
        _local(id=103, is_mapped=False, description=fp_desc, amount=fp_amt),
        _local(id=104, is_mapped=False, description=fp_desc, amount=fp_amt),
    ]
    qbo = {
        1: [
            _qbo(fp_desc, fp_amt),
            _qbo(fp_desc, fp_amt),
            _qbo(fp_desc, fp_amt),
        ]
    }

    result = classify_invoice_line_items(local, qbo)

    assert result.repair == ()
    assert {h.row_id for h in result.held_back} == {103, 104}
    assert all(h.reason == "unclaimed_qbo_line" for h in result.held_back)


def test_classify_two_verified_mapped_qbo_two_three_unmapped_all_repairable():
    fp_desc, fp_amt = "Shared", Decimal("10")
    local = [
        _local(id=110, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(id=111, is_mapped=True, description=fp_desc, amount=fp_amt),
        _local(id=112, is_mapped=False, description=fp_desc, amount=fp_amt),
        _local(id=113, is_mapped=False, description=fp_desc, amount=fp_amt),
        _local(id=114, is_mapped=False, description=fp_desc, amount=fp_amt),
    ]
    qbo = {1: [_qbo(fp_desc, fp_amt), _qbo(fp_desc, fp_amt)]}

    result = classify_invoice_line_items(local, qbo)

    assert {e.row_id for e in result.repair} == {112, 113, 114}
    assert result.held_back == ()


def test_classify_two_distinct_fingerprint_groups_no_cross_contamination():
    local = [
        _local(id=120, is_mapped=True, description="GroupA", amount=Decimal("1")),
        _local(id=121, is_mapped=False, description="GroupA", amount=Decimal("1")),
        _local(id=122, is_mapped=True, description="GroupB", amount=Decimal("2")),
        _local(id=123, is_mapped=False, description="GroupB", amount=Decimal("2")),
    ]
    qbo = {
        1: [
            _qbo("GroupA", Decimal("1")),
            _qbo("GroupB", Decimal("2")),
            _qbo("GroupB", Decimal("2")),
        ]
    }

    result = classify_invoice_line_items(local, qbo)

    assert len(result.repair) == 1
    assert result.repair[0].row_id == 121
    held = {h.row_id: h.reason for h in result.held_back}
    assert held[123] == "unclaimed_qbo_line"


def test_classify_negative_amount_end_to_end():
    local = [
        _local(id=130, is_mapped=True, description="Credit", amount=Decimal("-12.50")),
        _local(id=131, is_mapped=False, description="Credit", amount=Decimal("-12.5")),
    ]
    qbo = {1: [_qbo("Credit", Decimal("-12.50"))]}

    result = classify_invoice_line_items(local, qbo)

    assert len(result.repair) == 1
    assert result.repair[0].row_id == 131


def test_classify_held_back_unmapped_never_counts_as_mapped_sibling():
    local = [
        _local(id=140, is_mapped=False, description="Lonely", amount=Decimal("5")),
        _local(id=141, is_mapped=True, description="Work", amount=Decimal("100")),
        _local(id=142, is_mapped=False, description="Work", amount=Decimal("100")),
    ]
    qbo = {1: [_qbo("Work", Decimal("100"))]}

    result = classify_invoice_line_items(local, qbo)

    assert len(result.repair) == 1
    assert result.repair[0].row_id == 142
    held = [h for h in result.held_back if h.row_id == 140]
    assert len(held) == 1
    assert held[0].reason == "no_sibling"


@pytest.mark.parametrize(
    "actual,expected,apply,expected_ok",
    [
        (5, None, False, True),
        (5, 5, False, True),
        (5, 5, True, True),
        (5, 4, False, True),
        (5, 4, True, False),
    ],
)
def test_check_expected_count(actual, expected, apply, expected_ok):
    assert _check_expected_count(actual, expected, apply=apply) is expected_ok


def test_filter_repair_entries_by_manifest():
    entries = (
        _repair_entry(row_id=1, public_id="aaa"),
        _repair_entry(row_id=2, public_id="bbb"),
    )

    filtered = filter_repair_entries_by_manifest(entries, ("aaa",))

    assert len(filtered) == 1
    assert filtered[0].public_id == "aaa"


def test_apply_repairs_skips_entries_not_in_manifest(apply_repairs_mocks):
    mocks = apply_repairs_mocks
    repairable_a = _repair_entry(row_id=1, public_id="in-manifest")
    repairable_b = _repair_entry(row_id=2, public_id="not-in-manifest")
    local_rows = [
        _local(id=10, is_mapped=True),
        _local(id=1, is_mapped=False),
        _local(id=2, is_mapped=False),
    ]
    mocks["fetch_local"].return_value = local_rows
    mocks["fetch_qbo"].return_value = {1: [_qbo()]}

    deleted, skipped, all_ok = apply_repairs(
        MagicMock(),
        (repairable_a, repairable_b),
        batch_size=10,
        manifest_public_ids=frozenset({"in-manifest"}),
    )

    assert deleted == 1
    assert skipped == 0
    assert all_ok is True
    mocks["service"].delete_by_public_id.assert_called_once_with(public_id="in-manifest")


def test_apply_repairs_prints_partial_summary_on_delete_failure(apply_repairs_mocks, capsys):
    mocks = apply_repairs_mocks
    entry = _repair_entry(row_id=1, public_id="pid-1")
    mocks["fetch_local"].return_value = [
        _local(id=10, is_mapped=True),
        _local(id=1, is_mapped=False),
    ]
    mocks["fetch_qbo"].return_value = {1: [_qbo()]}
    mocks["service"].delete_by_public_id.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        apply_repairs(
            MagicMock(),
            (entry,),
            batch_size=10,
            manifest_public_ids=frozenset({"pid-1"}),
        )

    captured = capsys.readouterr()
    assert "APPLY partial summary (interrupted)" in captured.out
    assert "deleted=0" in captured.out
    assert "not_yet_attempted=0" in captured.out
