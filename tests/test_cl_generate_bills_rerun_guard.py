"""Pure-logic tests for contract-labor generate-bills re-run guards (U-135)."""

from decimal import Decimal
import types
import uuid

import pytest

from entities.contract_labor.business.bill_service import ContractLaborBillService
from entities.contract_labor.business.service import ContractLaborService


def _make_svc():
    svc = ContractLaborBillService()
    return svc


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
    return types.SimpleNamespace(id=scc_id, description="Framing")


def _cl(
    cl_id,
    vendor_id=1,
    status="ready",
    work_date="2026-06-10",
    bill_line_item_id=None,
    employee_name="Bob",
):
    return types.SimpleNamespace(
        id=cl_id,
        vendor_id=vendor_id,
        status=status,
        work_date=work_date,
        bill_line_item_id=bill_line_item_id,
        employee_name=employee_name,
        billing_period_start=None,
        public_id=str(uuid.uuid4()),
    )


def _li(
    li_id,
    cl_id,
    project_id=10,
    scc_id=100,
    hours=8,
    rate=50,
    line_date="2026-06-10",
    bill_line_item_id=None,
):
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
        bill_line_item_id=bill_line_item_id,
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


def _bli(bli_id=600, public_id=None):
    return types.SimpleNamespace(id=bli_id, public_id=public_id or str(uuid.uuid4()))


def _wire_minimal(svc, vendor, projects, sccs, ready_entries, line_items_by_cl):
    svc.vendor_service = types.SimpleNamespace(read_all=lambda: [vendor])
    svc.project_service = types.SimpleNamespace(read_all=lambda: projects)
    svc.scc_service = types.SimpleNamespace(read_all=lambda: sccs)
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


def _patch_pdf_pipeline(monkeypatch):
    monkeypatch.setattr(
        ContractLaborBillService, "_generate_pdf", lambda *a, **k: b"pdf"
    )
    import entities.contract_labor.business.bill_service as bill_mod

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


def _wire_refusal_path(
    svc,
    *,
    vendor,
    project,
    scc,
    ready,
    line_items_by_cl,
    existing_bill,
    existing_bli,
    billed_cls,
    update_calls,
    read_by_id,
    delete_bli_calls=None,
    read_lines=None,
    bli_create_fail="should not create bli",
):
    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    if read_lines is not None:
        svc.line_item_repo.read_by_contract_labor_id = read_lines
    if delete_bli_calls is None:
        delete_by_id = lambda bli_id: None
    else:
        delete_by_id = lambda bli_id: delete_bli_calls.append(bli_id)
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda bill_number, vendor_id: existing_bill,
            update_by_id=lambda b: (update_calls.append(b), b)[1],
        ),
        create=lambda **kwargs: pytest.fail("should not create"),
    )
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [existing_bli],
        create=lambda **kwargs: pytest.fail(bli_create_fail),
        repo=types.SimpleNamespace(delete_by_id=delete_by_id),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: billed_cls,
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=read_by_id,
        update_by_id=lambda entry: entry,
    )


def test_all_feeders_billed_ready_empty_no_op():
    svc = _make_svc()
    vendor = _vendor()
    _wire_minimal(svc, vendor, [_project()], [_scc()], [], {})
    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["bills_created"] == 0
    assert result["bills_refused"] == 0
    assert any("No ready entries" in e for e in result["errors"])


def test_partial_resume_parent_level_billed_feeder_refused():
    svc = _make_svc()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1, status="ready")]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bli = _bli(600)
    existing_bill = _bill(invoice="2026.06.15.TB3", is_draft=True)
    billed_cl = _cl(99, status="billed", bill_line_item_id=600)

    update_calls = []
    delete_bli_calls = []

    _wire_refusal_path(
        svc,
        vendor=vendor,
        project=project,
        scc=scc,
        ready=ready,
        line_items_by_cl=line_items_by_cl,
        existing_bill=existing_bill,
        existing_bli=existing_bli,
        billed_cls=[billed_cl],
        update_calls=update_calls,
        delete_bli_calls=delete_bli_calls,
        read_by_id=lambda id: ready[0] if id == 1 else None,
    )

    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert not update_calls
    assert not delete_bli_calls
    assert any("reset ALL its period feeders" in e for e in result["errors"])


def test_partial_resume_line_level_billed_feeder_refused():
    svc = _make_svc()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1, status="ready")]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bli = _bli(600)
    existing_bill = _bill(invoice="2026.06.15.TB3", is_draft=True)
    billed_cl = _cl(99, status="billed", bill_line_item_id=None)
    billed_li = _li(201, 99, bill_line_item_id=600)

    update_calls = []
    delete_bli_calls = []

    def read_lines(contract_labor_id):
        if contract_labor_id == 1:
            return line_items_by_cl[1]
        if contract_labor_id == 99:
            return [billed_li]
        return []

    _wire_refusal_path(
        svc,
        vendor=vendor,
        project=project,
        scc=scc,
        ready=ready,
        line_items_by_cl=line_items_by_cl,
        existing_bill=existing_bill,
        existing_bli=existing_bli,
        billed_cls=[billed_cl],
        update_calls=update_calls,
        delete_bli_calls=delete_bli_calls,
        read_lines=read_lines,
        read_by_id=lambda id: ready[0] if id == 1 else None,
    )

    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert not update_calls
    assert not delete_bli_calls


def test_multi_project_billed_parent_other_bli_child_this_bill_refused():
    """Sequential multi-project billing: parent BLI may point at the last
    billed project while an earlier project's line item still references
    this bill's BLI — Guard B must inspect child lines regardless."""
    svc = _make_svc()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1, status="ready")]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bli = _bli(600)
    existing_bill = _bill(invoice="2026.06.15.TB3", is_draft=True)
    other_bill_bli_id = 700
    billed_cl = _cl(
        99, status="billed", bill_line_item_id=other_bill_bli_id
    )
    billed_li_this_bill = _li(201, 99, bill_line_item_id=600)
    billed_li_other_bill = _li(202, 99, project_id=20, bill_line_item_id=700)

    update_calls = []
    delete_bli_calls = []

    def read_lines(contract_labor_id):
        if contract_labor_id == 1:
            return line_items_by_cl[1]
        if contract_labor_id == 99:
            return [billed_li_this_bill, billed_li_other_bill]
        return []

    _wire_refusal_path(
        svc,
        vendor=vendor,
        project=project,
        scc=scc,
        ready=ready,
        line_items_by_cl=line_items_by_cl,
        existing_bill=existing_bill,
        existing_bli=existing_bli,
        billed_cls=[billed_cl],
        update_calls=update_calls,
        delete_bli_calls=delete_bli_calls,
        read_lines=read_lines,
        read_by_id=lambda id: ready[0] if id == 1 else None,
    )

    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert not update_calls
    assert not delete_bli_calls


def test_mixed_vendor_one_create_one_refused(monkeypatch):
    svc = _make_svc()
    vendor = _vendor()
    project_a = _project(project_id=10, abbr="TB3")
    project_b = _project(project_id=20, abbr="HP2")
    scc = _scc()
    ready = [_cl(1), _cl(2)]
    line_items_by_cl = {
        1: [_li(101, 1, project_id=10, line_date="2026-06-10")],
        2: [_li(102, 2, project_id=20, line_date="2026-06-10")],
    }
    existing_bli = _bli(700)
    existing_bill_b = _bill(bill_id=501, invoice="2026.06.15.HP2", is_draft=True)
    billed_cl = _cl(99, status="billed", bill_line_item_id=700)

    created_bills = []
    update_calls = []

    def lookup_bill(bill_number, vendor_id):
        if bill_number == "2026.06.15.HP2":
            return existing_bill_b
        return None

    new_bill = _bill(bill_id=502, invoice="2026.06.15.TB3")

    _wire_minimal(svc, vendor, [project_a, project_b], [scc], ready, line_items_by_cl)
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lookup_bill,
            update_by_id=lambda b: (update_calls.append(b), b)[1],
        ),
        create=lambda **kwargs: (created_bills.append(kwargs), new_bill)[1],
        delete_by_public_id=lambda pid: None,
    )
    new_bli = _bli(800)
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [existing_bli] if bill_id == 501 else [],
        create=lambda **kwargs: new_bli,
        repo=types.SimpleNamespace(delete_by_id=lambda bli_id: None),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [billed_cl],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: next((e for e in ready if e.id == id), None),
        update_by_id=lambda entry: entry,
    )

    _patch_pdf_pipeline(monkeypatch)
    result = svc.generate_bills_for_vendor(vendor_id=1)

    assert result["bills_created"] == 1
    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert len(created_bills) == 1
    assert not update_calls


def test_completed_bill_refused_even_without_billed_feeders():
    svc = _make_svc()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bill = _bill(is_draft=False)

    update_calls = []

    _wire_refusal_path(
        svc,
        vendor=vendor,
        project=project,
        scc=scc,
        ready=ready,
        line_items_by_cl=line_items_by_cl,
        existing_bill=existing_bill,
        existing_bli=_bli(600),
        billed_cls=[],
        update_calls=update_calls,
        read_by_id=lambda id: ready[0],
        bli_create_fail="no bli",
    )

    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["bills_refused"] == 1
    assert result["bills_updated"] == 0
    assert not update_calls
    assert any("completed bills are never regenerated" in e for e in result["errors"])


def test_repair_flow_edit_path_rebuilds_when_no_billed_references(monkeypatch):
    svc = _make_svc()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1)]
    line_items_by_cl = {1: [_li(101, 1, line_date="2026-06-10")]}
    existing_bli = _bli(600)
    existing_bill = _bill(is_draft=True)

    update_calls = []
    delete_bli_calls = []
    create_bli_calls = []

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lambda bill_number, vendor_id: existing_bill,
            update_by_id=lambda b: (update_calls.append(b), b)[1],
        ),
        create=lambda **kwargs: pytest.fail("should not create"),
    )
    new_bli = _bli(900)
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [existing_bli],
        create=lambda **kwargs: (create_bli_calls.append(kwargs), new_bli)[1],
        repo=types.SimpleNamespace(
            delete_by_id=lambda bli_id: delete_bli_calls.append(bli_id)
        ),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: ready[0],
        update_by_id=lambda entry: entry,
    )

    _patch_pdf_pipeline(monkeypatch)

    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["bills_updated"] == 1
    assert result["bills_refused"] == 0
    assert update_calls
    assert delete_bli_calls == [600]
    assert create_bli_calls


def test_billing_period_skips_off_period_group(monkeypatch):
    svc = _make_svc()
    vendor = _vendor()
    project = _project()
    scc = _scc()
    ready = [_cl(1), _cl(2)]
    line_items_by_cl = {
        1: [_li(101, 1, line_date="2026-06-10")],
        2: [_li(102, 2, line_date="2026-06-20")],
    }

    lookup_calls = []
    created = []

    def lookup_bill(bill_number, vendor_id):
        lookup_calls.append(bill_number)
        return None

    new_bill = _bill(bill_id=503, invoice="2026.06.15.TB3")

    _wire_minimal(svc, vendor, [project], [scc], ready, line_items_by_cl)
    svc.bill_service = types.SimpleNamespace(
        repo=types.SimpleNamespace(
            read_by_bill_number_and_vendor_id=lookup_bill,
            update_by_id=lambda b: b,
        ),
        create=lambda **kwargs: (created.append(kwargs), new_bill)[1],
        delete_by_public_id=lambda pid: None,
    )
    new_bli = _bli(901)
    svc.bill_line_item_service = types.SimpleNamespace(
        read_by_bill_id=lambda bill_id: [],
        create=lambda **kwargs: new_bli,
        repo=types.SimpleNamespace(delete_by_id=lambda bli_id: None),
    )
    svc.cl_repo = types.SimpleNamespace(
        read_by_vendor_id=lambda vid: [],
        read_by_bill_line_item_id=lambda bli_id: [],
        read_by_id=lambda id: next((e for e in ready if e.id == id), None),
        update_by_id=lambda entry: entry,
    )

    _patch_pdf_pipeline(monkeypatch)
    result = svc.generate_bills_for_vendor(
        vendor_id=1, billing_period_start="2026-06-01"
    )

    assert result["bills_created"] == 1
    assert any("off-period group" in e for e in result["errors"])
    assert "2026.06.30.TB3" not in lookup_calls
    assert "2026.06.15.TB3" in lookup_calls


def test_delete_by_public_id_billed_raises_and_skips_repo_delete():
    svc = ContractLaborService()
    billed = _cl(1, status="billed")
    delete_calls = []
    svc.repo = types.SimpleNamespace(
        delete_by_id=lambda cl_id: delete_calls.append(cl_id),
        delete_by_public_id=lambda public_id: True,
    )
    svc.read_by_public_id = lambda public_id: billed

    with pytest.raises(ValueError, match="Cannot delete billed entries"):
        svc.delete_by_public_id(public_id=billed.public_id)
    assert not delete_calls


def test_delete_by_public_id_pending_review_deletes():
    svc = ContractLaborService()
    pending = _cl(1, status="pending_review")
    delete_calls = []
    svc.repo = types.SimpleNamespace(
        delete_by_id=lambda cl_id: (delete_calls.append(cl_id), pending)[1],
    )
    svc.read_by_public_id = lambda public_id: pending

    result = svc.delete_by_public_id(public_id=pending.public_id)
    assert result is pending
    assert delete_calls == [1]
