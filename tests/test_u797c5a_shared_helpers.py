"""U-797c5a mutation pins (EM) for the shared Bill/BillCredit push helpers."""

import logging
from types import SimpleNamespace

from integrations.intuit.qbo.attachable.line_attachments import sync_line_attachments
from integrations.intuit.qbo.base.push_refs import resolve_verified_dbo_ref


def test_attachment_without_a_blob_is_skipped_not_pushed():
    # M14: an attachment row with no blob_url is never sent to QBO.
    pushed = []
    connector = SimpleNamespace(sync_attachment_to_qbo=lambda **kw: pushed.append(kw))
    lines = [SimpleNamespace(public_id="li-1"), SimpleNamespace(public_id="li-2")]
    links = {"li-1": SimpleNamespace(attachment_id=1), "li-2": SimpleNamespace(attachment_id=2)}
    attachments = {1: SimpleNamespace(id=1, blob_url=None), 2: SimpleNamespace(id=2, blob_url="https://blob/2")}
    sync_line_attachments(
        line_items=lines, read_link=links.get, read_attachment=attachments.get, connector=connector,
        entity_type="VendorCredit", qbo_entity_id="880", realm_id="r", logger=logging.getLogger("t"), label="BillCredit 7",
    )
    assert [kw["attachment"].id for kw in pushed] == [2]


def test_require_qbo_id_short_circuits_without_an_identity_read():
    # M17: an entity with no QboId returns None WITHOUT the identity read when require_qbo_id is set.
    reads = []
    out = resolve_verified_dbo_ref(
        5,
        read_by_id=lambda _id: SimpleNamespace(id=5, qbo_id=None, realm_id="r", name="P"),
        read_by_qbo_identity=lambda *a: reads.append(a),
        require_qbo_id=True,
    )
    assert out is None
    assert reads == []
