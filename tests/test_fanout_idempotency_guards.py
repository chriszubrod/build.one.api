"""U-221 — fan-out idempotency guards (Box push + SharePoint enqueue), pure logic."""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from integrations.box.file.business.model import BoxFile
from integrations.box.file.business.service import BoxFileService
from integrations.box.outbox.business.model import BoxOutbox
from integrations.box.outbox.business.service import BoxOutboxService
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_UPLOAD_SHAREPOINT_FILE,
    MsOutboxService,
)
from shared.fanout_guard import idempotency_guards_disabled, same_attachment_id

_BLOB_BYTES = b"fan-out-idempotency-test-bytes"
_BLOB_SHA1 = hashlib.sha1(_BLOB_BYTES).hexdigest()
_ENTITY_TYPE = "Bill"
_ENTITY_PUBLIC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_BOX_FOLDER = "12345"
_FILENAME = "invoice.pdf"


@pytest.fixture(autouse=True)
def _clear_disable_fanout_guards_env(monkeypatch):
    monkeypatch.delenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", raising=False)


def _box_payload(**overrides):
    base = {
        "blob_path": "container/path/blob",
        "filename": _FILENAME,
        "box_folder_id": _BOX_FOLDER,
        "content_type": "application/pdf",
        "doc_kind": "document",
        "entity_type": _ENTITY_TYPE,
        "entity_public_id": _ENTITY_PUBLIC_ID,
        "attachment_id": 42,
    }
    base.update(overrides)
    return base


def _registry_row(**overrides):
    row = BoxFile(
        box_file_id="box-file-99",
        box_folder_id=_BOX_FOLDER,
        name=_FILENAME,
        sha1=_BLOB_SHA1,
        etag="etag-1",
        file_version_id="ver-1",
        attachment_id=42,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _upload_response(sha1=_BLOB_SHA1):
    return {
        "entries": [
            {
                "id": "new-box-id",
                "name": _FILENAME,
                "sha1": sha1,
                "etag": "new-etag",
                "file_version": {"id": "new-ver"},
            }
        ]
    }


@pytest.fixture
def box_service():
    repo = MagicMock()
    push_log_repo = MagicMock()
    svc = BoxFileService(repo=repo, push_log_repo=push_log_repo)
    client = MagicMock()
    client.upload_file.return_value = _upload_response()
    with patch.object(BoxFileService, "_fetch_blob", return_value=_BLOB_BYTES):
        yield svc, repo, push_log_repo, client


def _run_box_push(svc, client, payload):
    return svc.push_blob_to_box(
        client=client,
        payload=payload,
        outbox_id=7,
        request_id="req-1",
        actor_user_id=17,
    )


# --- shared.fanout_guard unit tests ---


@pytest.mark.parametrize(
    "env_value",
    ["true", "TRUE", " true ", "True"],
)
def test_idempotency_guards_disabled_true_values(monkeypatch, env_value):
    monkeypatch.setenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", env_value)
    assert idempotency_guards_disabled() is True


@pytest.mark.parametrize(
    "env_value",
    ["false", "0", "1", "yes", ""],
)
def test_idempotency_guards_disabled_false_values(monkeypatch, env_value):
    monkeypatch.setenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", env_value)
    assert idempotency_guards_disabled() is False


def test_idempotency_guards_disabled_unset(monkeypatch):
    monkeypatch.delenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", raising=False)
    assert idempotency_guards_disabled() is False


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (None, None, True),
        (None, 1, False),
        (1, None, False),
        (1, 1, True),
        ("1", 1, True),
    ],
)
def test_same_attachment_id(a, b, expected):
    assert same_attachment_id(a, b) is expected


def test_same_attachment_id_raises_on_non_numeric():
    with pytest.raises(ValueError):
        same_attachment_id("x", 1)


# --- Box guard ---


def test_box_skips_on_exact_registry_match(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row()]

    result = _run_box_push(svc, client, _box_payload())

    assert result == {
        "id": "box-file-99",
        "name": _FILENAME,
        "sha1": _BLOB_SHA1,
        "etag": "etag-1",
        "file_version": {"id": "ver-1"},
    }
    repo.read_by_entity.assert_called_once_with(
        _ENTITY_TYPE, _ENTITY_PUBLIC_ID, box_folder_id=_BOX_FOLDER, name=_FILENAME
    )
    client.upload_file.assert_not_called()
    repo.upsert.assert_not_called()
    push_log_repo.create.assert_not_called()


def test_box_uploads_when_sha1_differs(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(sha1="deadbeef" * 5)]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()
    repo.upsert.assert_called_once()


def test_box_uploads_when_name_differs(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(name="other.pdf")]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_uploads_when_folder_differs(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(box_folder_id="99999")]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_uploads_when_attachment_id_differs(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(attachment_id=99)]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_uploads_when_registry_sha1_is_none(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(sha1=None)]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_uploads_when_two_rows_match_ambiguous(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(), _registry_row(box_file_id="box-file-100")]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_uploads_when_entity_public_id_missing(box_service):
    svc, repo, push_log_repo, client = box_service
    payload = _box_payload(entity_public_id=None)

    _run_box_push(svc, client, payload)

    repo.read_by_entity.assert_not_called()
    client.upload_file.assert_called_once()


def test_box_uploads_when_read_by_entity_raises(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.side_effect = RuntimeError("db down")

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_uploads_when_registry_attachment_id_not_int_coercible(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(attachment_id="not-an-int")]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()


def test_box_skips_when_registry_sha1_is_uppercase(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(sha1=_BLOB_SHA1.upper())]

    result = _run_box_push(svc, client, _box_payload())

    assert result["id"] == "box-file-99"
    client.upload_file.assert_not_called()


def test_box_uploads_when_registry_attachment_id_none_payload_has_id(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(attachment_id=None)]

    _run_box_push(svc, client, _box_payload(attachment_id=42))

    client.upload_file.assert_called_once()


def test_box_uploads_when_registry_attachment_id_set_payload_none(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(attachment_id=42)]

    _run_box_push(svc, client, _box_payload(attachment_id=None))

    client.upload_file.assert_called_once()


def test_box_uploads_when_registry_row_is_deleted(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row(is_deleted=True)]

    _run_box_push(svc, client, _box_payload())

    client.upload_file.assert_called_once()
    repo.upsert.assert_called_once()


def test_box_skips_after_upsert_reactivates_deleted_row(box_service):
    """UpsertBoxFile clears IsDeleted; guard must skip again on the next drain."""
    svc, repo, push_log_repo, client = box_service

    repo.read_by_entity.return_value = [_registry_row(is_deleted=True)]
    _run_box_push(svc, client, _box_payload())
    client.upload_file.assert_called_once()
    repo.upsert.assert_called_once()
    client.upload_file.reset_mock()
    repo.upsert.reset_mock()

    repo.read_by_entity.return_value = [_registry_row(is_deleted=False)]

    result = _run_box_push(svc, client, _box_payload())

    assert result["id"] == "box-file-99"
    client.upload_file.assert_not_called()
    repo.upsert.assert_not_called()


def test_box_force_true_uploads_on_exact_registry_match(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row()]

    _run_box_push(svc, client, _box_payload(force=True))

    client.upload_file.assert_called_once()
    repo.upsert.assert_called_once()


def test_box_read_by_entity_called_with_payload_entity_keys(box_service):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = []

    _run_box_push(svc, client, _box_payload())

    repo.read_by_entity.assert_called_once_with(
        _ENTITY_TYPE, _ENTITY_PUBLIC_ID, box_folder_id=_BOX_FOLDER, name=_FILENAME
    )


@pytest.mark.parametrize(
    "env_value,should_upload",
    [
        ("true", True),
        ("false", False),
    ],
)
def test_box_guard_respects_disable_fanout_env(box_service, monkeypatch, env_value, should_upload):
    svc, repo, push_log_repo, client = box_service
    repo.read_by_entity.return_value = [_registry_row()]
    monkeypatch.setenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", env_value)

    _run_box_push(svc, client, _box_payload())

    if should_upload:
        client.upload_file.assert_called_once()
        repo.upsert.assert_called_once()
    else:
        client.upload_file.assert_not_called()


# --- Box outbox coalesce (force sticky) ---


def _box_outbox_base_payload(**overrides):
    base = {
        "blob_path": "container/path/blob",
        "filename": _FILENAME,
        "content_type": "application/pdf",
        "box_folder_id": _BOX_FOLDER,
        "attachment_id": 42,
        "doc_kind": "attachment",
        "project_id": 1,
    }
    base.update(overrides)
    return base


def _box_outbox_existing_row(payload_dict):
    return BoxOutbox(
        id=1,
        public_id="existing-outbox-uuid",
        row_version="rv1",
        kind="upload_box_file",
        entity_type="bill",
        entity_public_id=_ENTITY_PUBLIC_ID,
        status="pending",
        payload=json.dumps(payload_dict),
    )


@pytest.fixture
def box_outbox_service():
    repo = MagicMock()
    svc = BoxOutboxService(repo=repo)
    updated_row = BoxOutbox(
        id=1,
        public_id="existing-outbox-uuid",
        row_version="rv2",
        status="pending",
    )
    repo.update_ready_after.return_value = updated_row
    with patch(
        "integrations.box.outbox.business.service._writes_allowed",
        return_value=True,
    ), patch(
        "integrations.box.file.business.naming.sanitize_filename",
        side_effect=lambda filename, identity_source: filename,
    ):
        yield svc, repo, updated_row


def _run_box_enqueue(svc, repo, *, force=False, box_folder_id=_BOX_FOLDER, existing_rows=None):
    if existing_rows is not None:
        repo.read_pending_by_entity.return_value = existing_rows
    return svc.enqueue_box_upload(
        entity_type="bill",
        entity_public_id=_ENTITY_PUBLIC_ID,
        doc_kind="attachment",
        blob_path="container/path/blob",
        filename=_FILENAME,
        content_type="application/pdf",
        box_folder_id=box_folder_id,
        attachment_id=42,
        project_id=1,
        force=force,
    )


def test_box_coalesce_preserves_force_when_later_enqueue_not_forced(box_outbox_service):
    svc, repo, updated_row = box_outbox_service
    existing_payload = _box_outbox_base_payload(force=True, box_folder_id="old-folder")
    existing = _box_outbox_existing_row(existing_payload)
    repo.update_payload.return_value = updated_row

    result = _run_box_enqueue(
        svc, repo, force=False, box_folder_id="new-folder", existing_rows=[existing]
    )

    assert result is updated_row
    repo.update_payload.assert_called_once()
    written_payload = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert written_payload["force"] is True
    assert written_payload["box_folder_id"] == "new-folder"


def test_box_coalesce_sets_force_when_forced_enqueue_coalesces_non_forced_row(box_outbox_service):
    svc, repo, updated_row = box_outbox_service
    existing_payload = _box_outbox_base_payload(box_folder_id="old-folder")
    existing = _box_outbox_existing_row(existing_payload)
    repo.update_payload.return_value = updated_row

    result = _run_box_enqueue(
        svc, repo, force=True, box_folder_id="new-folder", existing_rows=[existing]
    )

    assert result is updated_row
    repo.update_payload.assert_called_once()
    written_payload = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert written_payload["force"] is True
    assert written_payload["box_folder_id"] == "new-folder"


def test_box_coalesce_force_refresh_failed_logs_warning_still_returns_row(box_outbox_service, caplog):
    svc, repo, updated_row = box_outbox_service
    existing_payload = _box_outbox_base_payload(force=True)
    existing = _box_outbox_existing_row(existing_payload)
    repo.update_payload.return_value = None

    with caplog.at_level("WARNING"):
        result = _run_box_enqueue(
            svc, repo, force=True, box_folder_id="new-folder", existing_rows=[existing]
        )

    assert result is updated_row
    assert any(
        record.getMessage() == "box.outbox.force_refresh_failed"
        for record in caplog.records
    )
    warning_record = next(
        r for r in caplog.records if r.getMessage() == "box.outbox.force_refresh_failed"
    )
    assert warning_record.outbox_public_id == "existing-outbox-uuid"
    assert warning_record.entity_type == "bill"
    assert warning_record.entity_public_id == _ENTITY_PUBLIC_ID
    assert warning_record.attachment_id == 42


# --- SharePoint guard ---


def _sp_identity_payload(**overrides):
    base = {
        "drive_id": "drive-1",
        "parent_item_id": "parent-1",
        "filename": _FILENAME,
        "blob_path": "https://acct.blob.core.windows.net/c/x.pdf",
        "attachment_id": 42,
        "content_type": "application/pdf",
    }
    base.update(overrides)
    return base


def _completed_outbox(payload_dict):
    return MsOutbox(
        id=100,
        public_id="outbox-prior-uuid",
        kind=KIND_UPLOAD_SHAREPOINT_FILE,
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        status="done",
        payload=json.dumps(payload_dict),
    )


@pytest.fixture
def ms_service():
    repo = MagicMock()
    svc = MsOutboxService(repo=repo)
    enqueued = MsOutbox(id=200, public_id="new-outbox-uuid", status="pending")
    with patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-abc",
    ), patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=True,
    ), patch.object(svc, "enqueue", return_value=enqueued) as enqueue_mock:
        yield svc, repo, enqueue_mock, enqueued


def _run_sp_enqueue(svc, **kwargs):
    defaults = {
        "entity_type": _ENTITY_TYPE,
        "entity_public_id": _ENTITY_PUBLIC_ID,
        "drive_id": "drive-1",
        "parent_item_id": "parent-1",
        "filename": _FILENAME,
        "content_type": "application/pdf",
        "blob_path": "https://acct.blob.core.windows.net/c/x.pdf",
        "attachment_id": 42,
    }
    defaults.update(kwargs)
    return svc.enqueue_sharepoint_upload(**defaults)


def test_sharepoint_skips_when_identity_matches(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    prior = _completed_outbox(_sp_identity_payload())
    repo.read_completed_by_entity.return_value = [prior]

    result = _run_sp_enqueue(svc)

    assert result is prior
    assert result is not None
    enqueue_mock.assert_not_called()
    repo.create.assert_not_called()
    repo.read_completed_by_entity.assert_called_once_with(
        entity_type=_ENTITY_TYPE,
        entity_public_id=_ENTITY_PUBLIC_ID,
        kind=KIND_UPLOAD_SHAREPOINT_FILE,
    )


def test_sharepoint_enqueues_when_filename_differs(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(filename="other.pdf"))
    ]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_blob_path_differs(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(blob_path="https://other/path"))
    ]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_drive_id_differs(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(drive_id="drive-OTHER"))
    ]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_parent_item_id_differs(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(parent_item_id="parent-OTHER"))
    ]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_attachment_id_differs(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(attachment_id=99))
    ]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_no_completed_rows(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = []

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_payload_unparseable(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    bad = _completed_outbox(_sp_identity_payload())
    bad.payload = "{not-json"
    repo.read_completed_by_entity.return_value = [bad]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_read_raises(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.side_effect = RuntimeError("db down")

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_writes_disabled_skips_guard_returns_none(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    enqueue_mock.return_value = None
    with patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=False,
    ):
        result = _run_sp_enqueue(svc)

    assert result is None
    repo.read_completed_by_entity.assert_not_called()
    enqueue_mock.assert_called_once()


@pytest.mark.parametrize("status", ["failed", "dead_letter"])
def test_sharepoint_enqueues_when_prior_status_not_done(ms_service, status):
    svc, repo, enqueue_mock, enqueued = ms_service
    prior = _completed_outbox(_sp_identity_payload())
    prior.status = status
    repo.read_completed_by_entity.return_value = [prior]

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_prior_attachment_id_none_payload_has_id(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(attachment_id=None))
    ]

    result = _run_sp_enqueue(svc, attachment_id=42)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_prior_attachment_id_set_payload_none(ms_service):
    svc, repo, enqueue_mock, enqueued = ms_service
    repo.read_completed_by_entity.return_value = [
        _completed_outbox(_sp_identity_payload(attachment_id=42))
    ]

    result = _run_sp_enqueue(svc, attachment_id=None)

    assert result is enqueued
    enqueue_mock.assert_called_once()


def test_sharepoint_enqueues_when_disable_fanout_guards_env_true(ms_service, monkeypatch):
    svc, repo, enqueue_mock, enqueued = ms_service
    prior = _completed_outbox(_sp_identity_payload())
    repo.read_completed_by_entity.return_value = [prior]
    monkeypatch.setenv("DISABLE_FANOUT_IDEMPOTENCY_GUARDS", "true")

    result = _run_sp_enqueue(svc)

    assert result is enqueued
    enqueue_mock.assert_called_once()
    repo.read_completed_by_entity.assert_not_called()
