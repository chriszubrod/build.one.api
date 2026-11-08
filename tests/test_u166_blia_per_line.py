"""U-166 — new bill lines inherit the bill's single shared attachment."""
import logging
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from entities.bill_line_item.business.service import BillLineItemService

BLIS = "entities.bill_line_item.business.service"
BLIAS = "entities.bill_line_item_attachment.business.service.BillLineItemAttachmentService"
ATTS = "entities.attachment.business.service.AttachmentService"


def _svc(siblings):
    repo = MagicMock()
    repo.create.return_value = NS(id=100, public_id="new-pub")
    repo.read_by_bill_id.return_value = [NS(id=100, public_id="new-pub")] + siblings
    return BillLineItemService(repo=repo), repo


def _create(svc, **kw):
    with patch(f"{BLIS}.BillService") as bs, patch.object(svc, "_assert_parent_editable"):
        bs.return_value.read_by_public_id.return_value = NS(id=7)
        return svc.create(bill_public_id="b-pub", **kw)


def _run(links, siblings=None, **kw):
    sibs = siblings if siblings is not None else [NS(id=1, public_id="s1"), NS(id=2, public_id="s2")]
    svc, repo = _svc(sibs)
    with patch(BLIAS) as blia, patch(ATTS) as att:
        blia.return_value.read_by_bill_line_item_ids.return_value = links
        att.return_value.read_by_id.return_value = NS(public_id="att-pub")
        out = _create(svc, **kw)
        return out, blia.return_value


def test_inherits_when_siblings_share_one_attachment():
    out, blia = _run([NS(attachment_id=5), NS(attachment_id=5)])
    assert out.id == 100
    blia.read_by_bill_line_item_ids.assert_called_once_with(["s1", "s2"])  # the new line is excluded
    blia.create.assert_called_once_with(bill_line_item_public_id="new-pub", attachment_public_id="att-pub")


def test_no_inherit_without_linked_siblings():
    _, blia = _run([])
    blia.create.assert_not_called()


def test_no_inherit_without_siblings():
    _, blia = _run([], siblings=[])
    blia.create.assert_not_called()


def test_no_inherit_with_two_distinct_attachments():
    _, blia = _run([NS(attachment_id=5), NS(attachment_id=6)])
    blia.create.assert_not_called()


def test_opt_out_skips():
    _, blia = _run([NS(attachment_id=5)], inherit_bill_attachment=False)
    blia.create.assert_not_called()
    blia.read_by_bill_line_item_ids.assert_not_called()


def test_inherit_failure_does_not_fail_create_and_logs_ids_only(caplog):
    svc, repo = _svc([NS(id=1, public_id="s1")])
    with patch(BLIAS) as blia, patch(ATTS) as att:
        blia.return_value.read_by_bill_line_item_ids.return_value = [NS(attachment_id=5)]
        att.return_value.read_by_id.return_value = NS(public_id="att-pub")
        blia.return_value.create.side_effect = RuntimeError("secret-file-name.pdf")
        with caplog.at_level(logging.WARNING, logger=BLIS):
            out = _create(svc)
    assert out.id == 100
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "u166" in msgs and "secret-file-name" not in msgs


def test_cl_call_site_opts_out():
    src = Path("entities/contract_labor/business/bill_service.py").read_text()
    i = src.index("self.bill_line_item_service.create(")
    assert "inherit_bill_attachment=False" in src[i:i + 900]


def test_qbo_pull_call_site_opts_out():
    import inspect
    from integrations.intuit.qbo.bill.connector.bill_line_item.business import service as qsvc
    src = inspect.getsource(qsvc)
    assert "inherit_bill_attachment=False" in src
