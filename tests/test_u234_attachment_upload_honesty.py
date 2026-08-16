"""U-234 — durable ReconciliationIssue when attachment upload to QBO fails."""
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.attachment.business.model import Attachment
from integrations.intuit.qbo.attachable.business.model import QboAttachable as LocalQboAttachable
from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
    AttachableAttachmentConnector,
)
from integrations.intuit.qbo.attachable.external.schemas import QboAttachable as QboAttachableResponse
from integrations.intuit.qbo.base.errors import (
    QboBudgetExceededError,
    QboServerError,
    QboValidationError,
    QboWriteRefusedError,
)

REALM_ID = "realm-u234"
ENTITY_TYPE = "Bill"
ENTITY_ID = "qbo-bill-42"
ATTACHMENT_PUBLIC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _attachment() -> Attachment:
    return Attachment(
        id=99,
        public_id=ATTACHMENT_PUBLIC_ID,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        filename="invoice.pdf",
        original_filename="invoice.pdf",
        file_extension=".pdf",
        content_type="application/pdf",
        file_size=1024,
        file_hash=None,
        blob_url="https://blob.example/invoice.pdf",
        description="Vendor invoice",
        category=None,
        tags=None,
        is_archived=False,
        status="active",
        download_count=None,
        last_downloaded_datetime=None,
        expiration_date=None,
        storage_tier="Hot",
    )


def _upload_response(qbo_id: str = "qbo-att-777") -> QboAttachableResponse:
    return QboAttachableResponse(
        Id=qbo_id,
        SyncToken="0",
        FileName="invoice.pdf",
        ContentType="application/pdf",
    )


def _local_qbo_attachable(qbo_id: str = "qbo-att-777") -> LocalQboAttachable:
    return LocalQboAttachable(
        id=501,
        public_id=None,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        qbo_id=qbo_id,
        sync_token="0",
        realm_id=REALM_ID,
        file_name="invoice.pdf",
        note=None,
        category=None,
        content_type="application/pdf",
        size=1024,
        file_access_uri=None,
        temp_download_uri=None,
        entity_ref_type=ENTITY_TYPE,
        entity_ref_value=ENTITY_ID,
    )


def _connector(reconciliation_repo=None) -> AttachableAttachmentConnector:
    connector = AttachableAttachmentConnector(
        mapping_repo=Mock(),
        attachment_service=Mock(),
        auth_service=Mock(),
        reconciliation_repo=reconciliation_repo or Mock(),
    )
    connector.mapping_repo.read_by_attachment_id.return_value = None
    connector.mapping_repo.read_by_qbo_attachable_id.return_value = None
    connector.auth_service.ensure_valid_token.return_value = MagicMock(access_token="tok")
    return connector


def _blob_download_patch():
    storage_instance = MagicMock()
    storage_instance.download_file.return_value = (b"%PDF-1.4 payload", {"content_type": "application/pdf"})
    return patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.AzureBlobStorage",
        return_value=storage_instance,
    )


def _client_upload_patch(side_effect=None, return_value=None):
    client = MagicMock()
    if side_effect is not None:
        client.upload_attachable.side_effect = side_effect
    else:
        client.upload_attachable.return_value = return_value or _upload_response()
    return patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.QboAttachableClient",
        return_value=MagicMock(__enter__=Mock(return_value=client), __exit__=Mock(return_value=False)),
    ), client


def test_upload_failure_records_reconciliation_issue_and_reraises():
    connector = _connector()
    upload_error = QboServerError("QBO 500", http_status=500)
    client_upload_patch, client = _client_upload_patch(side_effect=upload_error)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        with pytest.raises(QboServerError) as exc_info:
            connector.sync_attachment_to_qbo(
                attachment=_attachment(),
                realm_id=REALM_ID,
                entity_type=ENTITY_TYPE,
                entity_id=ENTITY_ID,
            )

    assert exc_info.value is upload_error
    client.upload_attachable.assert_called_once()
    record_issue.assert_called_once()
    assert record_issue.call_args.args[0] is connector.reconciliation_repo
    kwargs = record_issue.call_args.kwargs
    assert kwargs["drift_type"] == "attachment_upload_failed"
    assert kwargs["entity_type"] == "Attachment"
    assert kwargs["entity_public_id"] == ATTACHMENT_PUBLIC_ID
    assert kwargs["qbo_id"] is None
    assert kwargs["realm_id"] == REALM_ID
    assert kwargs["severity"] == "critical"
    assert "POST-COMMIT-AMBIGUOUS" in kwargs["details"]


def test_post_upload_local_failure_records_qbo_id_and_reraises():
    connector = _connector()
    upload_resp = _upload_response("qbo-att-post-commit")
    local_create_error = RuntimeError("local QboAttachable insert failed")
    client_upload_patch, _client = _client_upload_patch(return_value=upload_resp)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.QboAttachableRepository"
    ) as repo_cls, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        repo_cls.return_value.create.side_effect = local_create_error

        with pytest.raises(RuntimeError) as exc_info:
            connector.sync_attachment_to_qbo(
                attachment=_attachment(),
                realm_id=REALM_ID,
                entity_type=ENTITY_TYPE,
                entity_id=ENTITY_ID,
            )

    assert exc_info.value is local_create_error
    record_issue.assert_called_once()
    kwargs = record_issue.call_args.kwargs
    assert kwargs["qbo_id"] == "qbo-att-post-commit"
    assert "must NOT be blindly re-uploaded" in kwargs["details"]


def test_budget_exceeded_does_not_record_reconciliation_issue():
    connector = _connector()
    budget_error = QboBudgetExceededError("monthly cap reached")
    client_upload_patch, _client = _client_upload_patch(side_effect=budget_error)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        with pytest.raises(QboBudgetExceededError) as exc_info:
            connector.sync_attachment_to_qbo(
                attachment=_attachment(),
                realm_id=REALM_ID,
                entity_type=ENTITY_TYPE,
                entity_id=ENTITY_ID,
            )

    assert exc_info.value is budget_error
    record_issue.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


def test_write_refused_does_not_record_reconciliation_issue():
    connector = _connector()
    write_refused = QboWriteRefusedError("ALLOW_QBO_WRITES is not true")
    client_upload_patch, _client = _client_upload_patch(side_effect=write_refused)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        with pytest.raises(QboWriteRefusedError) as exc_info:
            connector.sync_attachment_to_qbo(
                attachment=_attachment(),
                realm_id=REALM_ID,
                entity_type=ENTITY_TYPE,
                entity_id=ENTITY_ID,
            )

    assert exc_info.value is write_refused
    record_issue.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


def test_happy_path_returns_local_attachable_without_recording_issue():
    connector = _connector()
    local_row = _local_qbo_attachable()
    client_upload_patch, _client = _client_upload_patch()

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.QboAttachableRepository"
    ) as repo_cls, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        repo_cls.return_value.create.return_value = local_row

        result = connector.sync_attachment_to_qbo(
            attachment=_attachment(),
            realm_id=REALM_ID,
            entity_type=ENTITY_TYPE,
            entity_id=ENTITY_ID,
        )

    assert result is local_row
    record_issue.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()
    connector.mapping_repo.create.assert_called_once()


def test_mapping_race_records_orphaned_issue_and_returns_local_attachable():
    connector = _connector()
    local_row = _local_qbo_attachable("qbo-att-race")
    upload_resp = _upload_response("qbo-att-race")
    connector.mapping_repo.read_by_attachment_id.side_effect = [
        None,
        Mock(qbo_attachable_id=123),
    ]
    client_upload_patch, _client = _client_upload_patch(return_value=upload_resp)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.QboAttachableRepository"
    ) as repo_cls, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        repo_cls.return_value.create.return_value = local_row

        result = connector.sync_attachment_to_qbo(
            attachment=_attachment(),
            realm_id=REALM_ID,
            entity_type=ENTITY_TYPE,
            entity_id=ENTITY_ID,
        )

    assert result is local_row
    record_issue.assert_called_once()
    kwargs = record_issue.call_args.kwargs
    assert kwargs["drift_type"] == "attachment_mapping_orphaned"
    assert kwargs["qbo_id"] == "qbo-att-race"
    assert kwargs["entity_public_id"] == ATTACHMENT_PUBLIC_ID
    assert kwargs["severity"] == "critical"
    assert "must NOT be re-uploaded" in kwargs["details"]
    assert "concurrent mapping race" in kwargs["details"]
    connector.mapping_repo.create.assert_not_called()


def test_malformed_2xx_response_detail_surfaced_in_reconciliation_issue():
    connector = _connector()
    raw_snippet = '{"UnexpectedShape": true, "foo": "bar"}'
    upload_error = QboValidationError(
        "Unexpected upload response format — QBO returned a successful 2xx with "
        "a parseable body, so the Attachable was almost certainly created "
        "server-side despite the unrecognized shape",
        detail=raw_snippet,
    )
    client_upload_patch, _client = _client_upload_patch(side_effect=upload_error)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        with pytest.raises(QboValidationError) as exc_info:
            connector.sync_attachment_to_qbo(
                attachment=_attachment(),
                realm_id=REALM_ID,
                entity_type=ENTITY_TYPE,
                entity_id=ENTITY_ID,
            )

    assert exc_info.value is upload_error
    record_issue.assert_called_once()
    kwargs = record_issue.call_args.kwargs
    assert kwargs["qbo_id"] is None
    assert raw_snippet in kwargs["details"]
    assert "Response detail:" in kwargs["details"]


def test_upload_failure_with_null_public_id_records_none_entity_public_id():
    connector = _connector()
    attachment = _attachment()
    attachment.public_id = None
    upload_error = QboServerError("QBO 500", http_status=500)
    client_upload_patch, _client = _client_upload_patch(side_effect=upload_error)

    with _blob_download_patch(), client_upload_patch, patch(
        "integrations.intuit.qbo.attachable.connector.attachment.business.service.record_mapping_issue"
    ) as record_issue:
        with pytest.raises(QboServerError):
            connector.sync_attachment_to_qbo(
                attachment=attachment,
                realm_id=REALM_ID,
                entity_type=ENTITY_TYPE,
                entity_id=ENTITY_ID,
            )

    record_issue.assert_called_once()
    kwargs = record_issue.call_args.kwargs
    assert kwargs["entity_public_id"] is None
