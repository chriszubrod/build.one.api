"""U-679 — contract-labor bill paths must not mutate inaccessible bills (P0)."""

from __future__ import annotations

import types
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

import entities.contract_labor.business.bill_service as bill_mod

from entities.contract_labor.business.bill_service import ContractLaborBillService
from shared.access import EntityNotAccessibleError
from shared.authz import clear_authz_context, system_authz


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _vendor(vendor_id=1, name="Acme Labor"):
    return types.SimpleNamespace(id=vendor_id, public_id=str(uuid.uuid4()), name=name)


def _project(project_id=10, abbr="TB3"):
    return types.SimpleNamespace(
        id=project_id,
        abbreviation=abbr,
        name=f"{abbr} Project",
        public_id=str(uuid.uuid4()),
    )


def _scc(scc_id=100):
    return types.SimpleNamespace(id=scc_id, description="Framing", number="100")


def _cl(cl_id, vendor_id=1, status="ready", work_date="2026-06-10"):
    return types.SimpleNamespace(
        id=cl_id,
        vendor_id=vendor_id,
        status=status,
        work_date=work_date,
        bill_line_item_id=None,
        employee_name="Bob",
        billing_period_start=None,
        public_id=str(uuid.uuid4()),
        bill_number=None,
    )


def _li(li_id, cl_id, project_id=10, scc_id=100, hours=8, rate=50, line_date="2026-06-10"):
    price = Decimal(str(hours)) * Decimal(str(rate))
    return types.SimpleNamespace(
        id=li_id,
        project_id=project_id,
        sub_cost_code_id=scc_id,
        hours=Decimal(str(hours)),
        rate=Decimal(str(rate)),
        price=price,
        line_date=line_date,
        is_billable=True,
        is_overhead=False,
        bill_line_item_id=None,
        row_version_bytes=b"v1",
        description="work",
        markup=Decimal("0"),
    )


def _bill(bill_id=500, invoice="2026.06.15.TB3", is_draft=True, vendor_id=1):
    return types.SimpleNamespace(
        id=bill_id,
        public_id=str(uuid.uuid4()),
        bill_number=invoice,
        is_draft=is_draft,
        vendor_id=vendor_id,
        bill_date="2026-06-15",
        due_date="2026-06-30",
        total_amount=Decimal("400"),
        memo="Contract Labor",
    )


def _raise_entity_not_accessible():
    raise EntityNotAccessibleError("Bill", 777)


def _patch_pdf_and_attachment_stack(monkeypatch, *, pdf: bytes = b"pdf") -> None:
    monkeypatch.setattr(
        ContractLaborBillService, "_generate_pdf", lambda *a, **k: pdf
    )
    monkeypatch.setattr(
        bill_mod,
        "AzureBlobStorage",
        lambda: types.SimpleNamespace(
            upload_file=lambda **kw: "https://blob/example.pdf"
        ),
    )
    monkeypatch.setattr(
        bill_mod,
        "AttachmentService",
        lambda: types.SimpleNamespace(
            create=lambda **kw: types.SimpleNamespace(
                public_id=str(uuid.uuid4()), id=1
            ),
            read_by_id=lambda id: None,
            delete_by_public_id=lambda public_id: None,
        ),
    )
    monkeypatch.setattr(
        bill_mod,
        "BillLineItemAttachmentService",
        lambda: types.SimpleNamespace(
            read_by_bill_line_item_id=lambda **kw: None,
            create=lambda **kw: None,
            delete_by_public_id=lambda **kw: None,
        ),
    )


def _wire_draft_bill_edit_path(
    svc,
    *,
    existing_bill,
    existing_bli,
    ready,
    update_calls,
    delete_bli_calls,
    create_bli_calls,
    new_bli_id: int = 900,
):
    new_bli = types.SimpleNamespace(id=new_bli_id, public_id=str(uuid.uuid4()))
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda bill_number, vendor_id: existing_bill,
            update_by_id=lambda b, **_: (update_calls.append(b), b)[1],
        ),
        create=lambda **kwargs: pytest.fail("should not create"),
    )
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [existing_bli],
        create=lambda **kwargs: (create_bli_calls.append(kwargs), new_bli)[1],
        repo=types.SimpleNamespace(
            delete_by_id=lambda bli_id, **_: delete_bli_calls.append(bli_id)
        ),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: ready[0],
        update_by_id=lambda entry: entry,
    )
    return new_bli


def _wire_minimal(svc, vendor, projects, sccs, ready_entries, line_items_by_cl):
    svc.vendor_service = types.SimpleNamespace(read_all=lambda: [vendor])
    svc.project_service = types.SimpleNamespace(read_all=lambda: projects)
    svc.scc_service = types.SimpleNamespace(
        read_all=lambda: sccs,
        is_holding_code=lambda _sid: False,
    )
    svc.cl_service = types.SimpleNamespace(
        read_by_status=lambda status, billing_period_start=None: ready_entries
    )
    svc.line_item_repo = types.SimpleNamespace(
        read_by_contract_labor_id=lambda contract_labor_id: line_items_by_cl.get(
            contract_labor_id, []
        ),
        read_by_id=lambda id: next(
            (
                li
                for lis in line_items_by_cl.values()
                for li in lis
                if li.id == id
            ),
            None,
        ),
        update_by_id=lambda **kwargs: types.SimpleNamespace(id=kwargs["id"]),
    )


def test_generate_bills_inaccessible_existing_refused_zero_writes(monkeypatch):
    svc = ContractLaborBillService()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bill = _bill(invoice="2026.06.15.TB3", is_draft=True)
    existing_bli = types.SimpleNamespace(id=600, public_id=str(uuid.uuid4()))

    update_calls = []
    delete_bli_calls = []
    pdf_calls = []

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    monkeypatch.setattr(
        ContractLaborBillService, "_generate_pdf", lambda *a, **k: pdf_calls.append(1) or b"pdf"
    )
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda bill_number, vendor_id: existing_bill,
            update_by_id=lambda b, **_: (update_calls.append(b), b)[1],
        ),
        create=lambda **kwargs: pytest.fail("should not create"),
    )
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [existing_bli],
        create=lambda **kwargs: pytest.fail("should not create bli"),
        repo=types.SimpleNamespace(
            delete_by_id=lambda bli_id, **_: delete_bli_calls.append(bli_id)
        ),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: ready[0],
        update_by_id=lambda entry: entry,
    )

    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        return_value=False,
    ):
        result = svc.generate_bills_for_vendor(vendor_id=1)

    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert result["bills_created"] == 0
    assert not update_calls
    assert not delete_bli_calls
    assert not pdf_calls
    assert any("is not accessible" in e for e in result["errors"])


def test_generate_bills_mixed_accessible_edit_and_inaccessible_refused(monkeypatch):
    svc = ContractLaborBillService()
    vendor = _vendor()
    project_a = _project(project_id=10, abbr="TB3")
    project_b = _project(project_id=20, abbr="HP2")
    scc = _scc()
    ready = [_cl(1), _cl(2)]
    line_items_by_cl = {
        1: [_li(101, 1, project_id=10, line_date="2026-06-10")],
        2: [_li(102, 2, project_id=20, line_date="2026-06-10")],
    }
    existing_bill_b = _bill(bill_id=501, invoice="2026.06.15.HP2", is_draft=True)
    existing_bli = types.SimpleNamespace(id=700, public_id=str(uuid.uuid4()))
    new_bill = _bill(bill_id=502, invoice="2026.06.15.TB3")

    created_bills = []
    update_calls = []

    def lookup_bill(bill_number, vendor_id):
        if bill_number == "2026.06.15.HP2":
            return existing_bill_b
        return None

    def access(bill_id):
        return bill_id != existing_bill_b.id

    _wire_minimal(svc, vendor, [project_a, project_b], [scc], ready, line_items_by_cl)
    _patch_pdf_and_attachment_stack(monkeypatch)
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lookup_bill,
            update_by_id=lambda b, **_: (update_calls.append(b), b)[1],
        ),
        create=lambda **kwargs: (created_bills.append(kwargs), new_bill)[1],
        delete_by_public_id=lambda pid: None,
    )
    new_bli = types.SimpleNamespace(id=800, public_id=str(uuid.uuid4()))
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [existing_bli] if bill_id == 501 else [],
        create=lambda **kwargs: new_bli,
        repo=types.SimpleNamespace(delete_by_id=lambda bli_id, **_: None),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: next((e for e in ready if e.id == id), None),
        update_by_id=lambda entry: entry,
    )

    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        side_effect=access,
    ):
        result = svc.generate_bills_for_vendor(vendor_id=1)

    assert result["bills_created"] == 1
    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert len(created_bills) == 1
    assert not update_calls


def test_generate_bills_accessible_existing_still_edits(monkeypatch):
    """Accessible draft bill — edit path unchanged (can_access True)."""
    svc = ContractLaborBillService()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bli = types.SimpleNamespace(id=600, public_id=str(uuid.uuid4()))
    existing_bill = _bill(is_draft=True)

    update_calls = []
    delete_bli_calls = []
    create_bli_calls = []

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    _patch_pdf_and_attachment_stack(monkeypatch)
    _wire_draft_bill_edit_path(
        svc,
        existing_bill=existing_bill,
        existing_bli=existing_bli,
        ready=ready,
        update_calls=update_calls,
        delete_bli_calls=delete_bli_calls,
        create_bli_calls=create_bli_calls,
    )

    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        return_value=True,
    ):
        result = svc.generate_bills_for_vendor(vendor_id=1)

    assert result["bills_updated"] == 1
    assert result["bills_refused"] == 0
    assert update_calls
    assert delete_bli_calls == [600]
    assert create_bli_calls


def test_regenerate_pdf_legacy_fallback_ignores_inaccessible_bill():
    svc = ContractLaborBillService()
    entry = _cl(1, status="billed")
    entry.bill_number = "2026.06.15.TB3"
    entry.vendor_id = 1
    hidden_bill = _bill(bill_id=888)

    svc.cl_service = types.SimpleNamespace(read_by_public_id=lambda public_id: entry)
    svc.project_service = types.SimpleNamespace(read_all=lambda: [_project()])
    svc.scc_service = types.SimpleNamespace(read_all=lambda: [_scc()])
    svc.vendor_service = types.SimpleNamespace(read_all=lambda: [_vendor()])
    svc.line_item_repo = types.SimpleNamespace(read_by_contract_labor_id=lambda **kw: [])
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda **kw: hidden_bill,
        ),
        read_by_id=lambda id: pytest.fail("read_by_id should not run without accessible bill"),
    )

    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        return_value=False,
    ):
        result = svc.regenerate_pdf_for_entries([entry.public_id])

    assert "error" in result
    assert "No billed entries" in result["error"]


def test_regenerate_pdf_legacy_fallback_uses_accessible_bill():
    """Legacy bill_number lookup runs only when no BLI ids resolve a bill."""
    svc = ContractLaborBillService()
    entry = _cl(1, status="billed")
    entry.bill_number = "2026.06.15.TB3"
    entry.vendor_id = 1
    bill = _bill(bill_id=888)
    li = _li(101, 1)
    li.bill_line_item_id = None

    svc.cl_service = types.SimpleNamespace(read_by_public_id=lambda public_id: entry)
    svc.project_service = types.SimpleNamespace(read_all=lambda: [_project()])
    svc.scc_service = types.SimpleNamespace(read_all=lambda: [_scc()])
    svc.vendor_service = types.SimpleNamespace(read_all=lambda: [_vendor()])
    svc.line_item_repo = types.SimpleNamespace(
        read_by_contract_labor_id=lambda **kw: [li],
    )
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda **kw: bill,
        ),
        read_by_id=lambda id: pytest.fail("read_by_id should not run on legacy-only path"),
    )

    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        return_value=True,
    ) as mock_access:
        result = svc.regenerate_pdf_for_entries([entry.public_id])

    mock_access.assert_called_once_with(bill.id)
    assert result.get("bill_count") == 1
    assert result.get("pdf_bytes")


def test_generate_bills_system_context_bypasses_access_on_existing(monkeypatch):
    """system_authz → can_access_bill True without patching UDF."""
    svc = ContractLaborBillService()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bill = _bill(is_draft=True)
    existing_bli = types.SimpleNamespace(id=600, public_id=str(uuid.uuid4()))
    update_calls = []
    delete_bli_calls = []
    create_bli_calls = []

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    _patch_pdf_and_attachment_stack(monkeypatch)
    _wire_draft_bill_edit_path(
        svc,
        existing_bill=existing_bill,
        existing_bli=existing_bli,
        ready=ready,
        update_calls=update_calls,
        delete_bli_calls=delete_bli_calls,
        create_bli_calls=create_bli_calls,
    )

    with system_authz():
        result = svc.generate_bills_for_vendor(vendor_id=1)

    assert result["bills_updated"] == 1
    assert update_calls


def test_generate_bills_project_failure_omits_exception_text(monkeypatch):
    svc = ContractLaborBillService()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    secret = "Bill 777 is not accessible to the current actor"

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    _patch_pdf_and_attachment_stack(monkeypatch)
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda bill_number, vendor_id: None,
        ),
        create=lambda **kwargs: types.SimpleNamespace(
            public_id=str(uuid.uuid4()),
            id=501,
            is_draft=True,
        ),
        delete_by_public_id=lambda public_id: None,
    )
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [],
        create=lambda **kwargs: (_raise_entity_not_accessible()),
        repo=types.SimpleNamespace(delete_by_id=lambda bli_id, **_: None),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: ready[0],
        update_by_id=lambda entry: entry,
    )

    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        return_value=True,
    ):
        result = svc.generate_bills_for_vendor(vendor_id=1)

    assert len(result["errors"]) == 1
    assert secret not in result["errors"][0]
    assert result["errors"][0] == "Error generating bill for project 10 period 2026-06-15"


def test_generate_bills_pdf_upload_failure_omits_exception_text(monkeypatch):
    """A blob/driver failure while attaching the PDF must not echo its text in errors[] (U-679)."""
    svc = ContractLaborBillService()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bli = types.SimpleNamespace(id=600, public_id=str(uuid.uuid4()))
    existing_bill = _bill(is_draft=True)

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    _patch_pdf_and_attachment_stack(monkeypatch)
    secret = "[Microsoft][ODBC Driver 18] blob container secret-xyz"

    def _boom(**_kw):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        bill_mod,
        "AzureBlobStorage",
        lambda: types.SimpleNamespace(upload_file=_boom),
    )
    _wire_draft_bill_edit_path(
        svc,
        existing_bill=existing_bill,
        existing_bli=existing_bli,
        ready=ready,
        update_calls=[],
        delete_bli_calls=[],
        create_bli_calls=[],
    )
    with patch(
        "entities.contract_labor.business.bill_service.can_access_bill",
        return_value=True,
    ):
        result = svc.generate_bills_for_vendor(vendor_id=1)

    upload_errors = [e for e in result["errors"] if "Failed to upload PDF" in e]
    assert upload_errors, result["errors"]
    assert all(secret not in e and "secret-xyz" not in e for e in result["errors"])
