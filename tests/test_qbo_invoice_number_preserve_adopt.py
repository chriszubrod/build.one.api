"""U-034 — Invoice invoice_number preserve + fingerprint re-adopt (completes the
"rule of three").

The Bill/BillCredit/Expense siblings already preserve a human-corrected document
number on QBO re-pull (U-024/U-027). Invoice was DEFERRED because its lost-mapping
gap-detect/adopt path keyed ONLY on the QBO-derived number: preserving a divergent
local number made that lookup miss and the suffix-CREATE loop mint a phantom "-N"
duplicate (the documented "46 phantom -N invoices" bug).

U-034 ships two coupled changes to InvoiceInvoiceConnector.sync_from_qbo_invoice:
  1. the plain existing-mapping UPDATE path (and the adopt-branch UPDATE) route the
     number through base.field_ownership.preserve_human_edited_ref;
  2. the adopt lookup gains a header-FINGERPRINT fallback (total + txn_date + project)
     so a mapping-lost, human-RENAMED invoice is RE-ADOPTED, not phantom-duplicated.

All collaborators are mocked — no DB/QBO I/O. Line syncing is stubbed; these tests
exercise the number decision + the adopt/create routing only.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
    InvoiceInvoiceConnector,
)

ILI_SERVICE = "entities.invoice_line_item.business.service.InvoiceLineItemService"


def _make_qbo_invoice(
    *, qbo_id="975", doc_number="INV-100", total_amt=100,
    txn_date="2026-07-01", due_date="2026-07-15",
):
    return SimpleNamespace(
        id=5001,
        qbo_id=qbo_id,
        customer_ref_value="qbo-cust-1",
        realm_id="realm-1",
        doc_number=doc_number,
        txn_date=txn_date,
        due_date=due_date,
        private_note="note",
        total_amt=total_amt,
    )


def _make_invoice(
    *, invoice_number, inv_id=1057, public_id="inv-pub-1057", project_id=200,
    total_amount=Decimal("100"), invoice_date="2026-07-01",
):
    return SimpleNamespace(
        id=inv_id,
        public_id=public_id,
        invoice_number=invoice_number,
        project_id=project_id,
        total_amount=total_amount,
        invoice_date=invoice_date,
        row_version="rowver==",
    )


def _build_connector():
    connector = InvoiceInvoiceConnector(
        mapping_repo=Mock(),
        line_mapping_repo=Mock(),
        invoice_service=Mock(),
        project_service=Mock(),
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
    )
    connector._get_project_public_id = Mock(return_value="proj-pub-1")
    connector._sync_line_items = Mock()  # isolate the number/adopt decision
    return connector


# ---------------------------------------------------------------------------
# Plain existing-mapping UPDATE path — preserve/upgrade decision
# ---------------------------------------------------------------------------

def _run_update(connector, qbo_invoice, stored_invoice):
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = SimpleNamespace(
        id=1, qbo_invoice_id=qbo_invoice.id, invoice_id=stored_invoice.id
    )
    connector.invoice_service.read_by_id.return_value = stored_invoice
    connector.invoice_service.update_by_public_id.return_value = stored_invoice

    connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.update_by_public_id.assert_called_once()
    connector.invoice_service.create.assert_not_called()
    return connector.invoice_service.update_by_public_id.call_args.kwargs["invoice_number"]


def test_update_preserves_manual_number():
    connector = _build_connector()
    passed = _run_update(
        connector,
        _make_qbo_invoice(qbo_id="975", doc_number="INV-100"),
        _make_invoice(invoice_number="CORRECTED-100"),
    )
    assert passed == "CORRECTED-100"


def test_update_upgrades_placeholder():
    connector = _build_connector()
    passed = _run_update(
        connector,
        _make_qbo_invoice(qbo_id="975", doc_number="INV-100"),
        _make_invoice(invoice_number="QBO-975"),
    )
    assert passed == "INV-100"


@pytest.mark.parametrize("stored", [None, ""])
def test_update_sets_from_doc_number_when_empty(stored):
    connector = _build_connector()
    passed = _run_update(
        connector,
        _make_qbo_invoice(qbo_id="975", doc_number="INV-100"),
        _make_invoice(invoice_number=stored),
    )
    assert passed == "INV-100"


def test_update_preserves_manual_when_doc_number_none():
    connector = _build_connector()
    passed = _run_update(
        connector,
        _make_qbo_invoice(qbo_id="975", doc_number=None),  # incoming => "QBO-975"
        _make_invoice(invoice_number="CORRECTED-100"),
    )
    assert passed == "CORRECTED-100"


# ---------------------------------------------------------------------------
# CREATE path — unchanged (no mapping, no fingerprint match)
# ---------------------------------------------------------------------------

def test_create_uses_qbo_derived_number_when_no_local_match():
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100")
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None  # no mapping => gap-detect
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector.invoice_service.read_all.return_value = []  # fingerprint finds nothing
    connector.mapping_repo.read_by_invoice_id.return_value = None
    connector.invoice_service.create.return_value = _make_invoice(
        invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058"
    )
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()
    assert connector.invoice_service.create.call_args.kwargs["invoice_number"] == "INV-100"


# ---------------------------------------------------------------------------
# THE CRITICAL ONE — mapping-lost + human-renamed => RE-ADOPT via fingerprint,
# NO phantom -N duplicate.
# ---------------------------------------------------------------------------

def test_mapping_lost_renamed_invoice_readopts_via_fingerprint_no_phantom():
    connector = _build_connector()
    # QBO still carries the ORIGINAL number (invoice push is disabled, so QBO never
    # learned of the local rename).
    qbo_invoice = _make_qbo_invoice(
        qbo_id="975", doc_number="INV-100", total_amt=100, txn_date="2026-07-01"
    )
    # Local invoice was renamed by a human and its header mapping was later lost.
    renamed = _make_invoice(
        invoice_number="CORRECTED-100", inv_id=1057, public_id="inv-pub-1057",
        project_id=200, total_amount=Decimal("100"), invoice_date="2026-07-01",
    )

    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None  # mapping lost
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    # QBO-derived-number lookup MISSES ("INV-100" != local "CORRECTED-100").
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    # Fingerprint scan operates over the preloaded cache (production batch path).
    connector._invoice_cache = {renamed.id: renamed}
    # The renamed invoice is unmapped at the HEADER (mapping-lost signature) but retains
    # QBO LINE provenance (its InvoiceLineItemInvoiceLine mapping survives), which is what
    # marks it a mapping-lost QBO invoice (not a manual one).
    connector.mapping_repo.read_by_invoice_id.return_value = None
    connector.line_mapping_repo.read_by_invoice_line_item_id.return_value = SimpleNamespace(id=1)
    connector.mapping_repo.create.return_value = SimpleNamespace(id=3)
    connector.invoice_service.update_by_public_id.return_value = renamed

    with patch(ILI_SERVICE) as ili_cls:
        # Used by both the provenance check and the had_lines check; give it a line item
        # carrying an id so _has_qbo_line_provenance can look up its line mapping.
        ili_cls.return_value.read_by_invoice_id.return_value = [SimpleNamespace(id=9001)]
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    # No phantom minted.
    connector.invoice_service.create.assert_not_called()
    # Existing renamed invoice adopted in place, number PRESERVED.
    connector.invoice_service.update_by_public_id.assert_called_once()
    kwargs = connector.invoice_service.update_by_public_id.call_args.kwargs
    assert connector.invoice_service.update_by_public_id.call_args.args[0] == "inv-pub-1057"
    assert kwargs["invoice_number"] == "CORRECTED-100"
    # Header mapping re-established: renamed invoice <-> this QboInvoice.
    connector.mapping_repo.create.assert_called_once()
    create_kwargs = connector.mapping_repo.create.call_args.kwargs
    assert create_kwargs["invoice_id"] == 1057
    assert create_kwargs["qbo_invoice_id"] == 5001


# ---------------------------------------------------------------------------
# Fingerprint guards — do NOT over-adopt.
# ---------------------------------------------------------------------------

def test_fingerprint_mismatch_does_not_adopt_creates_new():
    """A local invoice that does NOT match total+date is left alone -> CREATE."""
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100", total_amt=100)
    other = _make_invoice(
        invoice_number="SOMETHING-ELSE", inv_id=1057, project_id=200,
        total_amount=Decimal("999"), invoice_date="2026-07-01",  # different total
    )
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector._invoice_cache = {other.id: other}
    connector.mapping_repo.read_by_invoice_id.return_value = None
    connector.invoice_service.create.return_value = _make_invoice(
        invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058"
    )
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()


def test_fingerprint_candidate_mapped_to_other_qbo_is_not_adopted():
    """A fingerprint-matching invoice already bound to a DIFFERENT QboInvoice is a
    genuine separate invoice -> never stolen; falls through to CREATE."""
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100", total_amt=100)
    other = _make_invoice(
        invoice_number="OTHER-INV", inv_id=1057, project_id=200,
        total_amount=Decimal("100"), invoice_date="2026-07-01",  # fingerprint MATCHES
    )
    created = _make_invoice(invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058")

    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector._invoice_cache = {other.id: other}

    def _read_by_invoice_id(invoice_id):
        # The fingerprint candidate is bound to a DIFFERENT QboInvoice; the freshly
        # created invoice is unmapped.
        if invoice_id == other.id:
            return SimpleNamespace(id=7, invoice_id=other.id, qbo_invoice_id=9999)
        return None

    connector.mapping_repo.read_by_invoice_id.side_effect = _read_by_invoice_id
    connector.invoice_service.create.return_value = created
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()
    assert connector.invoice_service.create.call_args.kwargs["invoice_number"] == "INV-100"


def test_fingerprint_match_without_qbo_provenance_is_not_adopted():
    """A local/manual invoice that matches total+date+project but has NO QBO line-mapping
    provenance is a distinct invoice, not a mapping-lost QBO one -> never adopted; falls
    through to CREATE. Closes the Pass-1 P2 false-adopt path."""
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100", total_amt=100)
    manual = _make_invoice(
        invoice_number="MANUAL-5", inv_id=1057, project_id=200,
        total_amount=Decimal("100"), invoice_date="2026-07-01",  # fingerprint MATCHES
    )
    created = _make_invoice(invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058")

    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector._invoice_cache = {manual.id: manual}
    connector.mapping_repo.read_by_invoice_id.return_value = None  # unmapped
    # No line-mapping provenance -> manual invoice.
    connector.line_mapping_repo.read_by_invoice_line_item_id.return_value = None
    connector.invoice_service.create.return_value = created
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    with patch(ILI_SERVICE) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = [SimpleNamespace(id=9001)]
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()
    assert connector.invoice_service.create.call_args.kwargs["invoice_number"] == "INV-100"


def test_number_matched_manual_invoice_without_provenance_is_not_adopted():
    """Even an EXACT (project, number, total, date) match is NOT adopted when the local
    invoice lacks QBO line provenance — it is a distinct manual invoice, not a mapping-lost
    QBO one. Gates the number-matched adopt path (Pass-1 round-2 P2)."""
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100", total_amt=100)
    manual = _make_invoice(
        invoice_number="INV-100", inv_id=1057, project_id=200,  # SAME number as QBO-derived
        total_amount=Decimal("100"), invoice_date="2026-07-01",
    )
    created = _make_invoice(invoice_number="INV-100", inv_id=1058, public_id="inv-pub-1058")

    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = manual  # number HIT
    connector.mapping_repo.read_by_invoice_id.return_value = None  # unmapped
    connector.line_mapping_repo.read_by_invoice_line_item_id.return_value = None  # NO provenance
    connector.invoice_service.create.return_value = created
    connector.mapping_repo.create.return_value = SimpleNamespace(id=2)

    with patch(ILI_SERVICE) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = [SimpleNamespace(id=9001)]
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.update_by_public_id.assert_not_called()


def test_number_matched_qbo_invoice_with_provenance_is_adopted():
    """A mapping-lost QBO invoice found by EXACT number match (with line provenance) is
    re-adopted in place — the provenance gate does not block legitimate re-adoption."""
    connector = _build_connector()
    qbo_invoice = _make_qbo_invoice(qbo_id="975", doc_number="INV-100", total_amt=100)
    qbo_local = _make_invoice(
        invoice_number="INV-100", inv_id=1057, public_id="inv-pub-1057", project_id=200,
        total_amount=Decimal("100"), invoice_date="2026-07-01",
    )

    connector.mapping_repo.read_by_qbo_invoice_id.return_value = None  # header mapping lost
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=200)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = qbo_local  # number HIT
    connector.mapping_repo.read_by_invoice_id.return_value = None  # unmapped header
    connector.line_mapping_repo.read_by_invoice_line_item_id.return_value = SimpleNamespace(id=1)  # provenance
    connector.mapping_repo.create.return_value = SimpleNamespace(id=3)
    connector.invoice_service.update_by_public_id.return_value = qbo_local

    with patch(ILI_SERVICE) as ili_cls:
        ili_cls.return_value.read_by_invoice_id.return_value = [SimpleNamespace(id=9001)]
        connector.sync_from_qbo_invoice(qbo_invoice, [])

    connector.invoice_service.create.assert_not_called()
    connector.invoice_service.update_by_public_id.assert_called_once()
    connector.mapping_repo.create.assert_called_once()
