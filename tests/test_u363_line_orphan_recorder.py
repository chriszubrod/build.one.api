"""Pure-logic tests for base/line_orphan_recorder.py — the shared orphan-line
ReconciliationIssue recorder extracted in U-363 (TODO.md's hard-prerequisite
lift of the `_record_orphan_line_issue`/`_record_readopt_stamp_failed_issue`/
`_record_create_failed_issue` shape, previously hand-copied once each in
bill_credit_line_item/U-361, invoice_line_item/U-362, and bill_line_item/U-363
itself). Connector-level wiring (which drift_type/entity_type/parent_label
each family passes) is covered by each family's own mapping-retire test file;
this file proves the shared function bodies themselves, parameter-agnostic.
"""
from types import SimpleNamespace
from unittest.mock import Mock

from integrations.intuit.qbo.base.line_orphan_recorder import (
    record_create_failed_issue,
    record_orphan_line_issue,
    record_readopt_stamp_failed_issue,
)


def _line(id_=77, public_id="pub-77"):
    return SimpleNamespace(id=id_, public_id=public_id)


def test_record_orphan_line_issue_passes_through_drift_and_entity_type():
    repo = Mock()
    record_orphan_line_issue(
        repo,
        drift_type="orphan_bli_line_item",
        entity_type="BillLineItem",
        line_item=_line(),
        qbo_line_id="LINE-1",
        parent_label="Bill",
        parent_id=19146,
        realm_id="realm-1",
        exc=RuntimeError("delete failed"),
    )
    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_bli_line_item"
    assert kwargs["entity_type"] == "BillLineItem"
    assert kwargs["entity_public_id"] == "pub-77"
    assert kwargs["qbo_id"] == "LINE-1"
    assert kwargs["realm_id"] == "realm-1"
    assert "Bill 19146" in kwargs["details"]
    assert "delete failed" in kwargs["details"]
    assert "BillLineItem 77" in kwargs["details"]


def test_record_orphan_line_issue_qbo_id_and_public_id_fall_back_to_none():
    repo = Mock()
    record_orphan_line_issue(
        repo,
        drift_type="orphan_ili_line_item",
        entity_type="InvoiceLineItem",
        line_item=SimpleNamespace(id=5, public_id=None),
        qbo_line_id=None,
        parent_label="Invoice",
        parent_id=8,
        realm_id=None,
        exc=RuntimeError("x"),
    )
    kwargs = repo.create.call_args.kwargs
    assert kwargs["entity_public_id"] is None
    assert kwargs["qbo_id"] is None
    assert kwargs["realm_id"] == ""  # realm_id or "" — never None on the wire


def test_record_readopt_stamp_failed_issue_uses_lowercased_parent_label_in_prose():
    repo = Mock()
    record_readopt_stamp_failed_issue(
        repo,
        drift_type="bcli_line_readopt_failed",
        entity_type="BillCreditLineItem",
        line_item=_line(id_=55, public_id="pub-55"),
        qbo_line_id="1",
        parent_label="BillCredit",
        parent_id=19146,
        realm_id="realm-1",
        exc=RuntimeError("readopt stamp db error"),
    )
    kwargs = repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "bcli_line_readopt_failed"
    assert "BillCredit 19146" in kwargs["details"]
    assert "billcredit will keep retrying" in kwargs["details"]
    assert "readopt stamp db error" in kwargs["details"]


def test_record_create_failed_issue_has_no_entity_public_id_by_design():
    """The fresh-create path never has a candidate row to identify — this
    recorder is detectability-only, unlike the other two."""
    repo = Mock()
    record_create_failed_issue(
        repo,
        drift_type="ili_line_create_failed",
        entity_type="InvoiceLineItem",
        qbo_line_id="9",
        parent_label="Invoice",
        parent_id=8,
        realm_id="realm-1",
        exc=RuntimeError("create failed"),
    )
    kwargs = repo.create.call_args.kwargs
    assert kwargs["entity_public_id"] is None
    assert kwargs["qbo_id"] == "9"
    assert "Invoice 8" in kwargs["details"]
    assert "create failed" in kwargs["details"]
