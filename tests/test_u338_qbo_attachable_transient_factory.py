"""Characterization tests for U-338: `QboAttachable.transient(...)` collapses 3
hand-copied transient (never-persisted) QboAttachable constructions into one
factory on the dataclass — `QboAttachableService._upsert_attachable`,
`AttachableAttachmentConnector._transient_attachable_from_response`, and
`._transient_attachable_from_dbo`. Behavior-preserving: these tests pin the
exact field set each call site produced BEFORE the refactor, driven through
the real (now-delegating) methods so a future edit to any of the three or to
the factory itself that silently changes a mapped field goes RED here.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
    AttachableAttachmentConnector,
)

REALM_ID = "realm-338"


def _make_qbo_att_external(**overrides):
    """A stand-in for `QboAttachableExternalSchema` (the pull-response shape)."""
    ref = SimpleNamespace(entity_ref_type="Bill", entity_ref_value="qbo-bill-1")
    defaults = dict(
        id="qbo-att-1",
        sync_token="st-1",
        file_name="invoice.pdf",
        note="a note",
        category="qbo_import",
        content_type="application/pdf",
        size=1024,
        file_access_uri="https://qbo/access/1",
        temp_download_uri="https://qbo/temp/1",
        attachable_ref=[ref],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_upload_response(**overrides):
    """A stand-in for the QBO upload response object (the push-fresh shape)."""
    defaults = dict(
        id="qbo-att-2",
        sync_token="st-2",
        file_name="receipt.pdf",
        note="upload note",
        category="qbo_import",
        content_type="application/pdf",
        size=2048,
        file_access_uri="https://qbo/access/2",
        temp_download_uri="https://qbo/temp/2",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_attachment(**overrides):
    """A stand-in for `entities.attachment.business.model.Attachment` (the
    push-idempotency-fast-path shape)."""
    defaults = dict(
        qbo_id="qbo-att-3",
        original_filename="original.pdf",
        filename="stored.pdf",
        description="dbo description",
        category="qbo_import",
        content_type="application/pdf",
        file_size=4096,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


ALWAYS_NONE_FIELDS = ["id", "public_id", "row_version", "created_datetime", "modified_datetime"]


def _assert_always_none(att: QboAttachable):
    for field in ALWAYS_NONE_FIELDS:
        assert getattr(att, field) is None, f"{field} must be None on a transient QboAttachable"


class TestTransientFactory:
    def test_defaults_are_none_and_kwargs_pass_through(self):
        att = QboAttachable.transient(qbo_id="q1", realm_id="r1")
        _assert_always_none(att)
        assert att.qbo_id == "q1"
        assert att.realm_id == "r1"
        assert att.sync_token is None
        assert att.file_name is None
        assert att.note is None
        assert att.category is None
        assert att.content_type is None
        assert att.size is None
        assert att.file_access_uri is None
        assert att.temp_download_uri is None
        assert att.entity_ref_type is None
        assert att.entity_ref_value is None


class TestUpsertAttachable:
    """Site 1: QboAttachableService._upsert_attachable (business/service.py)."""

    def test_produces_expected_fields(self):
        service = QboAttachableService(auth_service=MagicMock())
        qbo_att = _make_qbo_att_external()

        result = service._upsert_attachable(REALM_ID, qbo_att)

        _assert_always_none(result)
        assert result.qbo_id == "qbo-att-1"
        assert result.sync_token == "st-1"
        assert result.realm_id == REALM_ID
        assert result.file_name == "invoice.pdf"
        assert result.note == "a note"
        assert result.category == "qbo_import"
        assert result.content_type == "application/pdf"
        assert result.size == 1024
        assert result.file_access_uri == "https://qbo/access/1"
        assert result.temp_download_uri == "https://qbo/temp/1"
        assert result.entity_ref_type == "Bill"
        assert result.entity_ref_value == "qbo-bill-1"

    def test_no_attachable_ref_leaves_entity_ref_none(self):
        service = QboAttachableService(auth_service=MagicMock())
        qbo_att = _make_qbo_att_external(attachable_ref=[])

        result = service._upsert_attachable(REALM_ID, qbo_att)

        assert result.entity_ref_type is None
        assert result.entity_ref_value is None


class TestTransientAttachableFromResponse:
    """Site 2: AttachableAttachmentConnector._transient_attachable_from_response."""

    def test_produces_expected_fields(self):
        connector = AttachableAttachmentConnector(auth_service=MagicMock())
        response = _make_upload_response()

        result = connector._transient_attachable_from_response(
            response=response,
            realm_id=REALM_ID,
            entity_type="Bill",
            entity_id="qbo-bill-9",
        )

        _assert_always_none(result)
        assert result.qbo_id == "qbo-att-2"
        assert result.sync_token == "st-2"
        assert result.realm_id == REALM_ID
        assert result.file_name == "receipt.pdf"
        assert result.note == "upload note"
        assert result.category == "qbo_import"
        assert result.content_type == "application/pdf"
        assert result.size == 2048
        assert result.file_access_uri == "https://qbo/access/2"
        assert result.temp_download_uri == "https://qbo/temp/2"
        assert result.entity_ref_type == "Bill"
        assert result.entity_ref_value == "qbo-bill-9"


class TestTransientAttachableFromDbo:
    """Site 3: AttachableAttachmentConnector._transient_attachable_from_dbo."""

    def test_produces_expected_fields(self):
        connector = AttachableAttachmentConnector(auth_service=MagicMock())
        attachment = _make_attachment()

        result = connector._transient_attachable_from_dbo(
            attachment, REALM_ID, entity_type="Purchase", entity_id="qbo-purchase-5",
        )

        _assert_always_none(result)
        assert result.qbo_id == "qbo-att-3"
        assert result.sync_token is None
        assert result.realm_id == REALM_ID
        assert result.file_name == "original.pdf"  # original_filename preferred
        assert result.note == "dbo description"
        assert result.category == "qbo_import"
        assert result.content_type == "application/pdf"
        assert result.size == 4096
        assert result.file_access_uri is None
        assert result.temp_download_uri is None
        assert result.entity_ref_type == "Purchase"
        assert result.entity_ref_value == "qbo-purchase-5"

    def test_file_name_falls_back_to_filename_when_no_original(self):
        connector = AttachableAttachmentConnector(auth_service=MagicMock())
        attachment = _make_attachment(original_filename=None)

        result = connector._transient_attachable_from_dbo(attachment, REALM_ID)

        assert result.file_name == "stored.pdf"
        assert result.entity_ref_type is None
        assert result.entity_ref_value is None
