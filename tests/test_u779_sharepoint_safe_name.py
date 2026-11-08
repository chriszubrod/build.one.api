"""
U-779: a newline in a line description produced a SharePoint filename that Graph
rejects (HTTP 400), dead-lettering the upload. `sharepoint_safe_name` removes
control chars (C0 + DEL); every filename builder uses it, and the Graph
write chokepoints sanitize again as a backstop.

All Graph / DB / blob access is mocked.
"""
import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import mock_ms_graph_client_cm

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_OLD_RULE = re.compile(r'[<>:"/\\|?*]')
_EVENT = "ms.sharepoint.name_sanitized_at_chokepoint"


def _old(text):
    return _OLD_RULE.sub("_", text)


def _safe(text):
    from integrations.ms.base.paths import sharepoint_safe_name

    return sharepoint_safe_name(text)


# ---------------------------------------------------------------------------
# sharepoint_safe_name
# ---------------------------------------------------------------------------

INVARIANT_CORPUS = [
    "",
    "plain name.pdf",
    "double  space",
    "trailing space ",
    " leading space",
    "trailing dot.",
    "a & b",
    "#7 stone",
    "100% done",
    "O'Brien",
    "Café Zoë – naïve",
    "rocket \U0001f680 emoji",
    '<>:"/\\|?*',
    "a<b>c:d\"e/f\\g|h?i*j",
    "   ",
]


@pytest.mark.parametrize("text", INVARIANT_CORPUS)
def test_invariant_identity_for_text_without_control_chars(text):
    """INVARIANT: no control char => output equals the historic re.sub exactly."""
    assert not _CONTROL.search(text)
    assert _safe(text) == _old(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a\nb", "ab"),
        ("a\r\nb", "ab"),
        ("a\tb", "ab"),
        ("a\x00b", "ab"),
        ("a\x7fb", "ab"),
        ("a\x1fb", "ab"),
        ("a \n b", "a  b"),
        ("a\r\n\tb", "ab"),
        ("a  \n\n  b", "a    b"),
        ("\nabc", "abc"),
        ("abc\n", "abc"),
        ("a\nb\nc", "abc"),
        ("Rogers Build Expense \nZoom", "Rogers Build Expense Zoom"),
        ("x\n/y", "x_y"),
        ("a  b\nc  d", "a  bc  d"),
    ],
)
def test_control_chars_are_removed(text, expected):
    assert _safe(text) == expected


# Pass-1 F3: Box derives its names from these same builders (bill
# _build_module_filename -> shared/completion/box_fanout.py) and has always
# stripped control chars itself. Box-sanitizing our output must give the SAME
# Box name as Box-sanitizing the raw text, or files already in Box get a
# duplicate on the next sync (the registry guard compares the full name).
BOX_PARITY_CORPUS = [
    "B\n1", "a\nb", "Expense \nZoom\r\n\tcall", "\nlead", "trail\n", "x\x00y\x7fz",
    "tab\there", "a \n b", "x\n/y", "plain name", "a  b", "dot.", "{brace}\n",
]


@pytest.mark.parametrize("text", BOX_PARITY_CORPUS)
def test_box_name_unchanged_by_sharepoint_sanitizer(text):
    from integrations.box.file.business.naming import sanitize_filename

    pid = "12345678-90ab-cdef-1234-567890abcdef"
    assert sanitize_filename(_safe(text) + ".pdf", pid) == sanitize_filename(text + ".pdf", pid)


# ---------------------------------------------------------------------------
# Builder sites
# ---------------------------------------------------------------------------

NL = "Expense \nZoom\r\n\tcall"


def _attachment():
    return SimpleNamespace(
        file_extension="pdf", original_filename="x.pdf", content_type="application/pdf",
        blob_url="blob://x", id=7,
    )


def _assert_clean(name):
    assert not _CONTROL.search(name), repr(name)


def _inject(svc, **deps):
    """Set collaborators on a __new__-built service; lazy properties are backed by `_name`."""
    for name, value in deps.items():
        if isinstance(getattr(type(svc), name, None), property):
            setattr(svc, f"_{name}", value)
        else:
            setattr(svc, name, value)
    return svc


def _capture_spec(service, method_name, *args, **kwargs):
    captured = {}

    def fake_upload(spec, line_items, project_id):
        captured["spec"] = spec
        return {}

    with patch("shared.completion.sharepoint_fanout.upload_to_project_module_folder", side_effect=fake_upload):
        getattr(service, method_name)(*args, **kwargs)
    return captured["spec"]


def test_expense_module_folder_filename_has_no_control_chars():
    from entities.expense.business.service import ExpenseService

    svc = object.__new__(ExpenseService)
    _inject(svc, sub_cost_code_service=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(number="01-100"))))
    expense = SimpleNamespace(reference_number="R1", expense_date="2026-11-05", is_credit=False, total_amount=10)
    spec = _capture_spec(svc, "_upload_attachments_to_module_folder", expense, [], 1, 1)
    line_item = SimpleNamespace(sub_cost_code_id=1, description=NL, price=12.5)
    name = spec.build_filename(
        line_item, SimpleNamespace(abbreviation="V", name="V"),
        SimpleNamespace(abbreviation="P", name="P"), _attachment(),
    )
    _assert_clean(name)
    assert "Expense Zoomcall" in name


def test_expense_general_receipts_filename_has_no_control_chars():
    from entities.expense.business.service import ExpenseService

    svc = object.__new__(ExpenseService)
    link_svc = MagicMock()
    link_svc.read_by_expense_line_item_id.return_value = SimpleNamespace(attachment_id=7)
    att_svc = MagicMock()
    att_svc.read_by_id.return_value = _attachment()
    _inject(
        svc,
        sub_cost_code_service=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(number="01-100"))),
        project_service=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(abbreviation="P", name="P"))),
        vendor_service=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(abbreviation="V", name="V"))),
        expense_line_item_attachment_service=link_svc,
        attachment_service=att_svc,
    )
    svc._expense_has_holding_line = lambda *a, **k: False
    svc._navigate_to_folder = lambda *a, **k: "receipts"
    svc._read_or_create_subfolder = lambda *a, **k: "folder"

    expense = SimpleNamespace(
        public_id="e1", reference_number="R1", expense_date="2026-11-05", is_credit=False,
        total_amount=10, vendor_id=3,
    )
    line_item = SimpleNamespace(
        public_id="li1", id=1, project_id=2, sub_cost_code_id=1, description=NL, price=12.5,
    )
    drive = SimpleNamespace(web_url="https://x/RogersBuildLLC", name="Documents", drive_id="d", public_id="pd")
    storage = MagicMock()
    storage.download_file.return_value = (b"x", {})
    captured = {}

    def fake_enqueue(**kw):
        captured.update(kw)
        return "queued", SimpleNamespace(public_id="o1")

    with patch("entities.expense.business.service.MsDriveService") as drives, \
         patch("entities.expense.business.service.AzureBlobStorage", return_value=storage), \
         patch("integrations.ms.outbox.business.service.MsOutboxService"), \
         patch("shared.completion.sharepoint_fanout.enqueue_attachment_file", side_effect=fake_enqueue):
        drives.return_value.read_all.return_value = [drive]
        svc._upload_to_general_receipts_folder(expense, [line_item])

    assert "filename" in captured, "enqueue was never reached"
    _assert_clean(captured["filename"])
    assert "Expense Zoomcall" in captured["filename"]


def test_bill_module_filename_has_no_control_chars():
    from entities.bill.business.service import BillService

    svc = object.__new__(BillService)
    _inject(svc, sub_cost_code_service=MagicMock(
        read_by_id=MagicMock(return_value=SimpleNamespace(number="01-100", name="Lumber\nand framing"))))
    bill = SimpleNamespace(bill_number="B\n1", bill_date="2026-11-05", total_amount=10)
    name = svc._build_module_filename(
        bill=bill,
        line_item=SimpleNamespace(sub_cost_code_id=1, price=5),
        vendor=SimpleNamespace(abbreviation="V", name="V"),
        project=SimpleNamespace(abbreviation="P", name="P"),
        bill_line_items_count=1,
        attachment=_attachment(),
    )
    _assert_clean(name)
    assert "B1" in name and "Lumberand framing" in name


def test_bill_credit_module_filename_has_no_control_chars():
    from entities.bill_credit.business.complete_service import BillCreditCompleteService

    svc = object.__new__(BillCreditCompleteService)
    _inject(svc, sub_cost_code_service=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(number="01-100"))))
    credit = SimpleNamespace(credit_number="C1", credit_date="2026-11-05")
    spec = _capture_spec(svc, "_upload_attachments_to_module_folder", credit, [], 1)
    name = spec.build_filename(
        SimpleNamespace(sub_cost_code_id=1, description=NL, amount=12),
        SimpleNamespace(abbreviation="V", name="V"),
        SimpleNamespace(abbreviation="P", name="P"),
        _attachment(),
    )
    _assert_clean(name)
    assert "Expense Zoomcall" in name


def test_contract_labor_pdf_filename_keeps_distinct_descriptions_distinct():
    """Pass-1 r1 F1: the CL name is the blob key (contract-labor/<bill>/<name>)
    and never a SharePoint name, so U-779 leaves it alone. A sanitizer here
    would merge "Units 1\\n2" and "Units 12" into one blob key and overwrite a PDF."""
    from entities.contract_labor.business.pdf_service import ContractLaborPDFService

    svc = object.__new__(ContractLaborPDFService)

    def name(desc):
        return svc._generate_filename(
            SimpleNamespace(bill_date="2026-11-05"),
            SimpleNamespace(name="Vendor"),
            SimpleNamespace(abbreviation="P", name="P"),
            SimpleNamespace(number="01-100"),
            SimpleNamespace(description=desc, price=10),
        )

    assert name("Units 1\n2") != name("Units 12")


def test_multi_line_expense_filename_carries_no_description():
    """Pass-1 r1 F2 refutation for expenses: two lines of ONE expense can never
    collide on a sanitized description, because a multi-line expense's name is
    "Multiple See Image" with no description at all."""
    from entities.expense.business.service import ExpenseService

    svc = object.__new__(ExpenseService)
    _inject(svc, sub_cost_code_service=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(number="01-100"))))
    expense = SimpleNamespace(reference_number="R1", expense_date="2026-11-05", is_credit=False, total_amount=10)
    lines = [SimpleNamespace(sub_cost_code_id=1, description=d, price=5) for d in ("Units 1\n2", "Units 12")]
    spec = _capture_spec(svc, "_upload_attachments_to_module_folder", expense, lines, 1, expense_line_items_count=2)
    names = {
        spec.build_filename(li, SimpleNamespace(abbreviation="V", name="V"),
                            SimpleNamespace(abbreviation="P", name="P"), _attachment())
        for li in lines
    }
    assert names == {"EXP - P - V - R1 - Multiple See Image - $10.00 - 11-05-2026.pdf"}


def test_invoice_subfolder_name_has_no_control_chars():
    from entities.invoice.business.service import InvoiceService

    svc = object.__new__(InvoiceService)
    driveitem = MagicMock()
    driveitem.read_or_create_folder.return_value = {"status_code": 201, "item": {"item_id": "sub"}}
    _inject(
        svc,
        module_service=MagicMock(read_by_name=MagicMock(return_value=SimpleNamespace(id=1))),
        project_module_connector=MagicMock(
            get_folder_for_module=MagicMock(return_value={"ms_drive_id": 1, "item_id": "f"})),
        drive_repo=MagicMock(read_by_id=MagicMock(return_value=SimpleNamespace(public_id="pd", drive_id="d"))),
        driveitem_service=driveitem,
        invoice_attachment_service=MagicMock(read_by_invoice_id=MagicMock(return_value=[])),
    )
    svc._collect_line_attachment_rows = lambda **kw: []
    invoice = SimpleNamespace(project_id=1, invoice_number="INV\n-001", public_id="i1", id=1)

    with patch("integrations.ms.outbox.business.service.MsOutboxService"), \
         patch("entities.attachment.business.service.AttachmentService"):
        svc._upload_to_sharepoint(invoice, [])

    folder_name = driveitem.read_or_create_folder.call_args.kwargs["folder_name"]
    _assert_clean(folder_name)
    assert folder_name == "INV-001"


def test_box_subfolder_name_matches_sharepoint_and_has_no_control_chars():
    from integrations.box.folder.business.service import BoxProjectFolderService

    svc = object.__new__(BoxProjectFolderService)
    client = MagicMock()
    client.create_folder.return_value = {"id": 99, "name": "INV-001"}
    # Same raw input on both sides (no trailing space: the Box side strips and the
    # SharePoint side does not -- a pre-existing asymmetry, out of U-779 scope).
    result = svc.read_or_create_child_folder(client=client, parent_box_folder_id="1", folder_name="INV\n-001")
    sent = client.create_folder.call_args.args[1]
    _assert_clean(sent)
    assert sent == _safe("INV\n-001")  # byte-identical with the SharePoint invoice subfolder for the same input
    assert result["name"] == "INV-001"


# ---------------------------------------------------------------------------
# Chokepoint backstop
# ---------------------------------------------------------------------------

_SP = "integrations.ms.sharepoint.external.client"
def _events(caplog):
    return [r for r in caplog.records if getattr(r, "event_name", None) == _EVENT]


def test_upload_small_file_sanitizes_and_warns(caplog):
    from integrations.ms.sharepoint.external.client import upload_small_file

    client = mock_ms_graph_client_cm(return_value={"id": "i", "name": "x"})
    with caplog.at_level(logging.WARNING), patch(f"{_SP}.MsGraphClient", return_value=client):
        upload_small_file("d", "p", "a \nb.pdf", b"x", "application/pdf")
    assert client.upload.call_args.args[0] == "drives/d/items/p:/a%20b.pdf:/content"
    ev = _events(caplog)
    assert len(ev) == 1
    # Check the record's actual field VALUES: repr() escapes "\n", so a repr
    # search can never see a raw name (that check was vacuous, mutation M8).
    assert "a \nb" not in ev[0].getMessage()
    assert not any(isinstance(v, str) and "a \nb" in v for v in vars(ev[0]).values())
    assert ev[0].operation == "driveitem.upload_small"


def test_upload_large_file_sanitizes_url_and_json_name_and_warns(caplog):
    from integrations.ms.sharepoint.external.client import upload_large_file

    client = mock_ms_graph_client_cm()
    client.post.return_value = {"uploadUrl": None}
    with caplog.at_level(logging.WARNING), patch(f"{_SP}.MsGraphClient", return_value=client):
        upload_large_file("d", "p", "a \nb.pdf", b"x", "application/pdf")
    assert client.post.call_args.args[0] == "drives/d/items/p:/a%20b.pdf:/createUploadSession"
    assert client.post.call_args.kwargs["json"]["item"]["name"] == "a b.pdf"
    assert len(_events(caplog)) == 1


def test_create_folder_sanitizes_name_and_warns(caplog):
    from integrations.ms.sharepoint.external.client import create_folder

    client = mock_ms_graph_client_cm(return_value={"id": "i", "name": "x"})
    with caplog.at_level(logging.WARNING), patch(f"{_SP}.MsGraphClient", return_value=client):
        create_folder("d", "p", "INV\n-001")
    assert client.post.call_args.kwargs["json"]["name"] == "INV-001"
    assert len(_events(caplog)) == 1


def test_worker_large_upload_sanitizes_and_warns(caplog):
    from integrations.ms.outbox.business.worker import MsOutboxWorker

    worker = MsOutboxWorker(repo=MagicMock())
    client = mock_ms_graph_client_cm()
    client.post.return_value = {"uploadUrl": None}
    payload = {"drive_id": "d", "parent_item_id": "p", "filename": "a \nb.pdf"}
    with caplog.at_level(logging.WARNING), patch("integrations.ms.base.client.MsGraphClient", return_value=client):
        with pytest.raises(ValueError):
            worker._upload_large_file_with_resume(
                row=MagicMock(id=1, row_version="rv"), payload=payload, content=b"x",
                total_size=1, content_type="application/pdf",
            )
    assert client.post.call_args.args[0] == "drives/d/items/p:/a%20b.pdf:/createUploadSession"
    assert client.post.call_args.kwargs["json"]["item"]["name"] == "a b.pdf"
    assert len(_events(caplog)) == 1
    assert payload["filename"] == "a \nb.pdf"  # stored payload untouched


def test_clean_names_pass_through_unchanged_without_warning(caplog):
    from integrations.ms.sharepoint.external.client import create_folder, upload_large_file, upload_small_file

    client = mock_ms_graph_client_cm(return_value={"id": "i", "name": "x", "uploadUrl": None})
    with caplog.at_level(logging.WARNING), patch(f"{_SP}.MsGraphClient", return_value=client):
        upload_small_file("d", "p", "ok  name.pdf", b"x")
        upload_large_file("d", "p", "ok  name.pdf", b"x")
        create_folder("d", "p", "ok  name")
    assert client.upload.call_args.args[0] == "drives/d/items/p:/ok%20%20name.pdf:/content"
    assert client.post.call_args_list[0].kwargs["json"]["item"]["name"] == "ok  name.pdf"
    assert client.post.call_args_list[1].kwargs["json"]["name"] == "ok  name"
    assert _events(caplog) == []
