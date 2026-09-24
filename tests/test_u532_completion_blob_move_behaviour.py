"""U-532 — behavioural coverage for `BillService._rename_invoice_blob_on_complete`.

AST call-site specs in `test_u528_attachment_blob_url_not_client_settable` and
`test_u446b_terminal_lock` prove the rename *call is written* (and that there is
exactly one such site). This module proves the function *runs*: blob move,
row repoint, ordering, dedupe, skips, and failure isolation.

Load-bearing seams (do not swap for bare MagicMocks on AttachmentService):
- `BillService.__new__(BillService)` — `__init__` eagerly builds seven services.
- `create_autospec(AttachmentService, instance=True)` — signature drift (m18/m19) fails loud.
- `_FakeStorage` borrows real `AzureBlobStorage._parse_blob_url` (no network/config).
- One test drives real `AttachmentService.update_by_public_id` with
  `is_evidence_for_a_completed_parent → True` (terminal-lock exemption).
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from entities.attachment.business.service import AttachmentService
from entities.bill.business.service import BillService
from shared.storage import AzureBlobStorage

_MODULE = "entities.bill.business.service"
_OLD = "https://acct.blob.core.windows.net/attachments/contract-labor/B-1/inv.pdf"
_NEW = "https://acct.blob.core.windows.net/attachments/inv.pdf"


class _FakeStorage:
    """Records every blob call. Borrows the REAL URL parser so a change to
    `_parse_blob_url` is still exercised; touches no network and no config."""

    instances: list = []
    container_name = "attachments"
    _parse_blob_url = AzureBlobStorage._parse_blob_url

    def __init__(self, *, download=None, upload=None, delete=None):
        self.calls: list = []
        self._download = download
        self._upload = upload
        self._delete = delete
        _FakeStorage.instances.append(self)

    def download_file(self, blob_url):
        self.calls.append(("download", blob_url))
        if self._download:
            raise self._download
        return b"%PDF-1.4 bytes", {}

    def upload_file(self, *, blob_name, file_content, content_type):
        self.calls.append(("upload", blob_name, file_content, content_type))
        if self._upload:
            raise self._upload
        return f"https://acct.blob.core.windows.net/attachments/{blob_name}"

    def delete_file(self, blob_url):
        self.calls.append(("delete", blob_url))
        if self._delete:
            raise self._delete


def _attachment(blob_url=_OLD, content_type="application/pdf", **kw):
    return SimpleNamespace(
        id=9,
        public_id="att-9",
        row_version="AAAA",
        blob_url=blob_url,
        content_type=content_type,
        filename="contract-labor/B-1/inv.pdf",
        original_filename="contract-labor/B-1/inv.pdf",
        **kw,
    )


def _service(*, attachments, links, update_side_effect=None):
    """`_rename_invoice_blob_on_complete` needs exactly two collaborators."""
    svc = BillService.__new__(BillService)
    svc.bill_line_item_attachment_service = MagicMock()
    svc.bill_line_item_attachment_service.read_by_bill_line_item_id.side_effect = (
        lambda *, bill_line_item_public_id: links.get(bill_line_item_public_id)
    )
    # autospec binds the REAL signature: dropping `blob_url` or
    # `_via_internal_pipeline` from AttachmentService makes this test error,
    # which a bare MagicMock would swallow.
    svc.attachment_service = create_autospec(AttachmentService, instance=True)
    svc.attachment_service.read_by_id.side_effect = lambda *, id: attachments.get(id)
    if update_side_effect:
        svc.attachment_service.update_by_public_id.side_effect = update_side_effect
    return svc


def _line(public_id="bli-1"):
    return SimpleNamespace(public_id=public_id)


@pytest.fixture(autouse=True)
def _reset():
    _FakeStorage.instances = []
    yield


def _run(svc, line_items, errors=None, **storage_kw):
    errors = [] if errors is None else errors
    storage = _FakeStorage(**storage_kw)
    with patch(f"{_MODULE}.AzureBlobStorage", return_value=storage):
        svc._rename_invoice_blob_on_complete(
            line_items=line_items, all_errors=errors
        )
    return storage, errors


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------


def test_a_nested_blob_is_moved_to_the_container_root_and_the_row_repointed():
    att = _attachment()
    svc = _service(
        attachments={7: att}, links={"bli-1": SimpleNamespace(attachment_id=7)}
    )
    storage, errors = _run(svc, [_line()])

    assert errors == []
    assert ("download", _OLD) in storage.calls
    upload = [c for c in storage.calls if c[0] == "upload"]
    assert len(upload) == 1
    assert upload[0][1] == "inv.pdf", "the folder prefix must be stripped"
    assert upload[0][2] == b"%PDF-1.4 bytes", "the moved bytes must be the blob's"
    assert upload[0][3] == "application/pdf"

    svc.attachment_service.update_by_public_id.assert_called_once()
    kwargs = svc.attachment_service.update_by_public_id.call_args.kwargs
    assert kwargs["public_id"] == "att-9"
    assert kwargs["row_version"] == "AAAA"
    assert kwargs["blob_url"] == _NEW, "the row must point at the NEW blob"
    assert kwargs["filename"] == "inv.pdf"
    assert kwargs["original_filename"] == "inv.pdf"
    assert kwargs["_via_internal_pipeline"] is True, (
        "step 1 of completion already made the bill terminal; without the "
        "exemption the rename is refused and the blob keeps its nested name"
    )
    assert ("delete", _OLD) in storage.calls, "the old blob must not be left behind"


def test_missing_content_type_defaults_to_pdf_on_upload():
    att = _attachment(content_type=None)
    svc = _service(
        attachments={7: att}, links={"bli-1": SimpleNamespace(attachment_id=7)}
    )
    storage, errors = _run(svc, [_line()])
    assert errors == []
    upload = [c for c in storage.calls if c[0] == "upload"]
    assert upload[0][3] == "application/pdf"


def test_the_old_blob_dies_only_after_the_row_points_at_the_new_one():
    """Delete-then-update would destroy the bytes a failed update still names."""
    att = _attachment()
    order: list = []
    svc = _service(
        attachments={7: att},
        links={"bli-1": SimpleNamespace(attachment_id=7)},
        update_side_effect=lambda **kw: order.append("update"),
    )
    storage, _ = _run(svc, [_line()])
    for call in storage.calls:
        if call[0] == "delete":
            order.append("delete")
    assert order == ["update", "delete"]


def test_a_failed_row_update_leaves_the_old_blob_intact():
    att = _attachment()
    svc = _service(
        attachments={7: att},
        links={"bli-1": SimpleNamespace(attachment_id=7)},
        update_side_effect=RuntimeError("row is locked"),
    )
    storage, errors = _run(svc, [_line()])
    assert not [c for c in storage.calls if c[0] == "delete"], (
        "the row still names the OLD blob; destroying it loses the document"
    )
    assert errors == [{"step": "rename_invoice_blob", "error": "row is locked"}]


# --------------------------------------------------------------------------
# skips
# --------------------------------------------------------------------------


def test_a_blob_already_at_the_container_root_is_left_alone():
    att = _attachment(blob_url=_NEW)
    svc = _service(
        attachments={7: att}, links={"bli-1": SimpleNamespace(attachment_id=7)}
    )
    storage, errors = _run(svc, [_line()])
    assert storage.calls == [], "a root blob must not be re-uploaded onto itself"
    svc.attachment_service.update_by_public_id.assert_not_called()
    assert errors == []


def test_two_line_items_sharing_one_attachment_move_it_once():
    att = _attachment()
    link = SimpleNamespace(attachment_id=7)
    svc = _service(
        attachments={7: att}, links={"bli-1": link, "bli-2": link}
    )
    storage, _ = _run(svc, [_line("bli-1"), _line("bli-2")])
    assert len([c for c in storage.calls if c[0] == "upload"]) == 1
    svc.attachment_service.update_by_public_id.assert_called_once()


def test_a_duplicate_line_item_link_does_not_abort_later_attachments():
    """`continue` on dedupe must not become `break` and skip remaining line items."""
    att7 = _attachment()
    att8 = _attachment(blob_url=_OLD.replace("inv.pdf", "second.pdf"))
    att8.id = 8
    att8.public_id = "att-8"
    svc = _service(
        attachments={7: att7, 8: att8},
        links={
            "bli-1": SimpleNamespace(attachment_id=7),
            "bli-2": SimpleNamespace(attachment_id=7),
            "bli-3": SimpleNamespace(attachment_id=8),
        },
    )
    storage, _ = _run(
        svc, [_line("bli-1"), _line("bli-2"), _line("bli-3")]
    )
    assert len([c for c in storage.calls if c[0] == "upload"]) == 2


def test_an_attachment_with_no_blob_url_is_skipped_not_parsed():
    att = _attachment(blob_url=None)
    svc = _service(
        attachments={7: att}, links={"bli-1": SimpleNamespace(attachment_id=7)}
    )
    storage, errors = _run(svc, [_line()])
    assert storage.calls == []
    assert errors == []


def test_no_line_items_never_even_constructs_storage():
    svc = _service(attachments={}, links={})
    errors: list = []
    with patch(f"{_MODULE}.AzureBlobStorage") as MockStorage:
        svc._rename_invoice_blob_on_complete(
            line_items=[], all_errors=errors
        )
    MockStorage.assert_not_called()


def test_a_line_item_with_no_link_is_skipped():
    svc = _service(attachments={}, links={"bli-1": None})
    storage, errors = _run(svc, [_line()])
    assert storage.calls == []
    assert errors == []


# --------------------------------------------------------------------------
# failure isolation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("failing", ["download", "upload", "delete"])
def test_a_storage_failure_is_recorded_and_never_raised(failing):
    """Completion must degrade to a 207, not a 500."""
    att = _attachment()
    svc = _service(
        attachments={7: att}, links={"bli-1": SimpleNamespace(attachment_id=7)}
    )
    _, errors = _run(svc, [_line()], **{failing: RuntimeError(f"{failing} boom")})
    assert errors == [
        {"step": "rename_invoice_blob", "error": f"{failing} boom"}
    ], "a swallowed failure makes completion report success it did not achieve"


def test_one_failing_attachment_does_not_stop_the_next_one():
    bad = _attachment()
    good = _attachment(blob_url=_OLD.replace("inv.pdf", "other.pdf"))
    good.public_id = "att-10"
    svc = _service(
        attachments={7: bad, 8: good},
        links={
            "bli-1": SimpleNamespace(attachment_id=7),
            "bli-2": SimpleNamespace(attachment_id=8),
        },
        update_side_effect=[RuntimeError("first fails"), None],
    )
    _, errors = _run(svc, [_line("bli-1"), _line("bli-2")])
    assert len(errors) == 1
    assert svc.attachment_service.update_by_public_id.call_count == 2


# --------------------------------------------------------------------------
# the exemption is load-bearing, not decorative — driven through the REAL
# AttachmentService against a terminal parent.
# --------------------------------------------------------------------------


def test_the_rename_is_not_refused_by_the_terminal_lock_on_its_own_bill():
    real = AttachmentService.__new__(AttachmentService)
    real.repo = MagicMock()
    stored = _attachment()
    real.read_by_public_id = MagicMock(return_value=stored)
    real.is_evidence_for_a_completed_parent = MagicMock(return_value=True)

    svc = BillService.__new__(BillService)
    svc.bill_line_item_attachment_service = MagicMock()
    svc.bill_line_item_attachment_service.read_by_bill_line_item_id.return_value = (
        SimpleNamespace(attachment_id=7)
    )
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_id.return_value = stored
    svc.attachment_service.update_by_public_id.side_effect = (
        lambda **kw: real.update_by_public_id(**kw)
    )

    storage, errors = _run(svc, [_line()])
    assert errors == [], f"the completion's own rename was refused: {errors}"
    real.repo.update_by_id.assert_called_once()
    assert real.repo.update_by_id.call_args.kwargs["allow_terminal_parent"] is True


def test_attachment_service_create_autospec_still_accepts_blob_url():
    """m18: `create_autospec` mirrors the real `create` signature (not a bare mock)."""
    sig = inspect.signature(AttachmentService.create)
    assert "blob_url" in sig.parameters
    stub = create_autospec(AttachmentService, instance=True)
    stub.create(
        filename="f.pdf",
        original_filename="f.pdf",
        file_extension="pdf",
        content_type="application/pdf",
        file_size=1,
        file_hash="abc",
        blob_url=_NEW,
    )
