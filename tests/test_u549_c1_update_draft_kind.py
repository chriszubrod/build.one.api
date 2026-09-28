"""U-549 Phase C1 — MS outbox `update_draft` Kind (enqueue + drain semantics)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from integrations.ms.base.errors import MsServerError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.service import (
    KIND_APPEND_EXCEL_ROW,
    KIND_INSERT_EXCEL_ROW,
    KIND_SEND_MAIL,
    KIND_UPDATE_DRAFT,
    KIND_UPLOAD_SHAREPOINT_FILE,
    UPDATE_DRAFT_OUTCOME_NOT_A_DRAFT,
    UPDATE_DRAFT_OUTCOME_NOT_FOUND,
    UPDATE_DRAFT_OUTCOME_PATCHED,
    MsOutboxService,
)
from integrations.ms.outbox.business.worker import MsOutboxWorker

_UPDATE_DRAFT = "integrations.ms.mail.external.client.update_draft"
_GET_MESSAGE = "integrations.ms.mail.external.client.get_message"

_ENTITY_TYPE = "RampTransactionFollowUp"
_ENTITY_PUBLIC_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_GRAPH_MESSAGE_ID = "AAMkAG-draft-id-001"


def _update_payload(**overrides):
    base = {
        "graph_message_id": _GRAPH_MESSAGE_ID,
        "to_addresses": [{"email": "worker@example.com", "name": "W"}],
        "cc_addresses": [],
        "bcc_addresses": [{"email": "archive@example.com", "name": None}],
        "subject": "Receipt needed",
        "body": "<p>Please attach your receipt.</p>",
        "body_type": "HTML",
    }
    base.update(overrides)
    return base


def _update_row(**overrides):
    defaults = {
        "id": 1,
        "public_id": "outbox-update-draft-1",
        "kind": KIND_UPDATE_DRAFT,
        "entity_type": _ENTITY_TYPE,
        "entity_public_id": _ENTITY_PUBLIC_ID,
        "tenant_id": "tenant-1",
        "request_id": "req-1",
        "row_version": "rv-1",
        "attempts": 0,
        "payload": json.dumps(_update_payload()),
    }
    defaults.update(overrides)
    return MsOutbox(**defaults)


def _ok_patch():
    return {
        "status_code": 200,
        "draft": {"message_id": _GRAPH_MESSAGE_ID},
    }


def _ok_get(is_draft=True, **email_overrides):
    email = {
        "message_id": _GRAPH_MESSAGE_ID,
        "is_draft": is_draft,
    }
    email.update(email_overrides)
    return {
        "status_code": 200,
        "email": email,
    }


def _not_found_response(**extra):
    base = {
        "status_code": 404,
        "message": "MsNotFoundError: resource not found",
        "is_retryable": False,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Enqueue — ALLOW_MS_WRITES gate
# ---------------------------------------------------------------------------


def test_enqueue_update_draft_refused_when_ms_writes_off():
    svc = MsOutboxService(repo=MagicMock())
    with patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=False,
    ), patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-1",
    ):
        result = svc.enqueue_update_draft(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            graph_message_id=_GRAPH_MESSAGE_ID,
            to_addresses=[{"email": "a@b.com"}],
            subject="s",
            body="b",
        )

    assert result is None
    svc.repo.create.assert_not_called()


def test_enqueue_update_draft_enqueues_when_ms_writes_on():
    repo = MagicMock()
    svc = MsOutboxService(repo=repo)
    created = MsOutbox(id=9, public_id="ob-new", status="pending")
    repo.create.return_value = created

    with patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=True,
    ), patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-abc",
    ), patch(
        "integrations.ms.outbox.business.service.idempotency_guards_disabled",
        return_value=False,
    ):
        repo.count_by_entity_and_kind.return_value = 0
        result = svc.enqueue_update_draft(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            graph_message_id=_GRAPH_MESSAGE_ID,
            to_addresses=[{"email": "worker@example.com", "name": "W"}],
            cc_addresses=[{"email": "cc@example.com"}],
            bcc_addresses=[{"email": "archive@example.com"}],
            subject="Receipt needed",
            body="<p>body</p>",
            body_type="HTML",
        )

    assert result is created
    repo.create.assert_called_once()
    call_kwargs = repo.create.call_args.kwargs
    assert call_kwargs["kind"] == KIND_UPDATE_DRAFT
    assert call_kwargs["entity_type"] == _ENTITY_TYPE
    assert call_kwargs["entity_public_id"] == _ENTITY_PUBLIC_ID
    assert call_kwargs["tenant_id"] == "tenant-abc"
    payload = json.loads(call_kwargs["payload"])
    assert payload["graph_message_id"] == _GRAPH_MESSAGE_ID
    assert payload["to_addresses"] == [{"email": "worker@example.com", "name": "W"}]
    assert payload["cc_addresses"] == [{"email": "cc@example.com"}]
    assert payload["bcc_addresses"] == [{"email": "archive@example.com"}]
    assert payload["subject"] == "Receipt needed"
    assert payload["body"] == "<p>body</p>"
    assert payload["body_type"] == "HTML"


# ---------------------------------------------------------------------------
# Handler — happy path
# ---------------------------------------------------------------------------


def test_handle_update_draft_calls_graph_with_message_id_and_fields():
    worker = MsOutboxWorker(repo=MagicMock())
    payload = _update_payload()

    with patch(_GET_MESSAGE, return_value=_ok_get()), patch(
        _UPDATE_DRAFT, return_value=_ok_patch()
    ) as graph_patch:
        worker._handle_update_draft(_update_row(), payload)

    graph_patch.assert_called_once_with(
        message_id=_GRAPH_MESSAGE_ID,
        to_recipients=payload["to_addresses"],
        subject=payload["subject"],
        body=payload["body"],
        body_type=payload["body_type"],
        cc_recipients=payload["cc_addresses"],
        bcc_recipients=payload["bcc_addresses"],
    )
    assert payload["update_draft_outcome"] == UPDATE_DRAFT_OUTCOME_PATCHED


# ---------------------------------------------------------------------------
# Factual terminal outcomes (none imply the message was sent)
# ---------------------------------------------------------------------------


def test_get_is_draft_false_is_not_a_draft_no_patch():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    with patch(_GET_MESSAGE, return_value=_ok_get(is_draft=False)), patch(
        _UPDATE_DRAFT
    ) as graph_patch:
        worker._process_inner(row)

    graph_patch.assert_not_called()
    repo.mark_done.assert_called_once()
    repo.mark_failed.assert_not_called()
    persisted = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert persisted["update_draft_outcome"] == UPDATE_DRAFT_OUTCOME_NOT_A_DRAFT


def test_get_404_is_not_found_no_patch():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row()

    get_not_found = _not_found_response(email=None)

    with patch(_GET_MESSAGE, return_value=get_not_found), patch(_UPDATE_DRAFT) as graph_patch:
        worker._process_inner(row)

    graph_patch.assert_not_called()
    repo.mark_done.assert_called_once()
    persisted = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert persisted["update_draft_outcome"] == UPDATE_DRAFT_OUTCOME_NOT_FOUND


def test_patch_404_is_not_found_after_successful_get():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    patch_not_found = _not_found_response(draft=None)

    with patch(_GET_MESSAGE, return_value=_ok_get()), patch(
        _UPDATE_DRAFT, return_value=patch_not_found
    ):
        worker._process_inner(row)

    repo.mark_done.assert_called_once()
    repo.mark_failed.assert_not_called()
    persisted = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert persisted["update_draft_outcome"] == UPDATE_DRAFT_OUTCOME_NOT_FOUND


def test_patch_400_not_a_draft_body_retries_not_terminal():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    bad_request = {
        "status_code": 400,
        "message": "The message is not a draft and cannot be updated",
        "is_retryable": False,
        "draft": None,
    }

    with patch(_GET_MESSAGE, return_value=_ok_get(is_draft=True)), patch(
        _UPDATE_DRAFT, return_value=bad_request
    ), patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    repo.mark_done.assert_not_called()
    repo.update_payload.assert_not_called()
    repo.mark_dead_letter.assert_not_called()
    repo.mark_failed.assert_called_once()


# ---------------------------------------------------------------------------
# GET inconclusive → retry (never terminal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "get_result",
    [
        {"status_code": 200, "email": None},
        {"status_code": 200},
        {"status_code": 200, "email": []},
        {"status_code": 200, "email": {"message_id": _GRAPH_MESSAGE_ID}},
        {"status_code": 200, "email": {"message_id": _GRAPH_MESSAGE_ID, "is_draft": None}},
    ],
    ids=[
        "email_none",
        "email_missing",
        "email_not_dict",
        "is_draft_absent",
        "is_draft_none",
    ],
)
def test_get_inconclusive_shapes_retry(get_result):
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    with patch(_GET_MESSAGE, return_value=get_result), patch(
        _UPDATE_DRAFT
    ) as graph_patch, patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    graph_patch.assert_not_called()
    repo.mark_done.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.update_payload.assert_not_called()


def test_get_200_empty_top_level_body_retries():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    with patch(_GET_MESSAGE, return_value={"status_code": 200}), patch(
        _UPDATE_DRAFT
    ) as graph_patch, patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    graph_patch.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_done.assert_not_called()


def test_get_message_null_bcc_recipients_is_retryable_not_success():
    """Explicit null in a collection slot is malformed — must not coerce to success."""
    from integrations.ms.mail.external.client import get_message

    mock_client = MagicMock()
    mock_client.get.return_value = {
        "isDraft": True,
        "bccRecipients": None,
    }

    with patch(
        "integrations.ms.mail.external.client.MsGraphClient"
    ) as client_cls:
        client_cls.return_value.__enter__.return_value = mock_client
        result = get_message("msg-id", include_body=False)

    assert result["status_code"] == 503
    assert result.get("is_retryable") is True
    assert result.get("email") is None


def test_get_message_null_bcc_recipients_worker_retries_not_terminal():
    """Worker must exercise raw Graph null via get_message, not a pre-coerced envelope."""
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    mock_client = MagicMock()
    mock_client.get.return_value = {
        "id": _GRAPH_MESSAGE_ID,
        "isDraft": True,
        "bccRecipients": None,
    }

    with patch(
        "integrations.ms.mail.external.client.MsGraphClient"
    ) as client_cls, patch(_UPDATE_DRAFT) as graph_patch, patch.object(
        MsOutboxWorker, "_escalate_dead_letter"
    ):
        client_cls.return_value.__enter__.return_value = mock_client
        worker._process_inner(row)

    graph_patch.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_done.assert_not_called()
    repo.mark_dead_letter.assert_not_called()
    repo.update_payload.assert_not_called()


def test_get_503_retries():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    get_transient = {
        "status_code": 503,
        "message": "Service unavailable",
        "is_retryable": True,
    }

    with patch(_GET_MESSAGE, return_value=get_transient), patch(
        _UPDATE_DRAFT
    ) as graph_patch, patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    graph_patch.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_done.assert_not_called()
    repo.update_payload.assert_not_called()


def test_patch_transient_5xx_schedules_retry_not_dead_letter():
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    transient = {
        "status_code": 503,
        "message": "Service unavailable",
        "is_retryable": True,
    }

    with patch(_GET_MESSAGE, return_value=_ok_get()), patch(
        _UPDATE_DRAFT, return_value=transient
    ), patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    repo.mark_done.assert_not_called()


def test_patch_transient_5xx_raises_retryable_from_handler():
    worker = MsOutboxWorker(repo=MagicMock())
    transient = {
        "status_code": 503,
        "message": "Service unavailable",
        "is_retryable": True,
    }

    with patch(_GET_MESSAGE, return_value=_ok_get()), patch(
        _UPDATE_DRAFT, return_value=transient
    ):
        with pytest.raises(MsServerError) as exc_info:
            worker._handle_update_draft(_update_row(), _update_payload())

    assert exc_info.value.is_retryable is True
    assert exc_info.value.http_status == 503


def test_get_message_list_body_is_retryable_not_dead_letter():
    from integrations.ms.mail.external.client import get_message

    mock_client = MagicMock()
    mock_client.get.return_value = []

    with patch(
        "integrations.ms.mail.external.client.MsGraphClient"
    ) as client_cls:
        client_cls.return_value.__enter__.return_value = mock_client
        result = get_message("msg-id", include_body=False)

    assert result["status_code"] == 503
    assert result["is_retryable"] is True
    assert result.get("email") is None


# ---------------------------------------------------------------------------
# Idempotency + durable outcome
# ---------------------------------------------------------------------------


def test_enqueue_update_draft_guard_count_failure_does_not_enqueue():
    repo = MagicMock()
    svc = MsOutboxService(repo=repo)
    repo.count_by_entity_and_kind.side_effect = OSError("db unavailable")

    with patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=True,
    ), patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-abc",
    ), patch(
        "integrations.ms.outbox.business.service.idempotency_guards_disabled",
        return_value=False,
    ):
        result = svc.enqueue_update_draft(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            graph_message_id=_GRAPH_MESSAGE_ID,
            to_addresses=[{"email": "worker@example.com"}],
            subject="s",
            body="b",
        )

    assert result is None
    repo.create.assert_not_called()


def test_second_enqueue_same_entity_does_not_create_second_row():
    repo = MagicMock()
    svc = MsOutboxService(repo=repo)
    created = MsOutbox(id=9, public_id="ob-new", status="pending")
    repo.create.return_value = created
    repo.count_by_entity_and_kind.return_value = 1

    with patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=True,
    ), patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-abc",
    ), patch(
        "integrations.ms.outbox.business.service.idempotency_guards_disabled",
        return_value=False,
    ):
        result = svc.enqueue_update_draft(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            graph_message_id=_GRAPH_MESSAGE_ID,
            to_addresses=[{"email": "worker@example.com"}],
            subject="s",
            body="b",
        )

    assert result is None
    repo.create.assert_not_called()
    repo.count_by_entity_and_kind.assert_called_once_with(
        _ENTITY_TYPE, _ENTITY_PUBLIC_ID, KIND_UPDATE_DRAFT
    )


def test_send_mail_row_does_not_suppress_update_draft_enqueue():
    repo = MagicMock()
    svc = MsOutboxService(repo=repo)
    created = MsOutbox(id=9, public_id="ob-new", status="pending")
    repo.create.return_value = created
    repo.count_by_entity_and_kind.return_value = 0

    with patch(
        "integrations.ms.outbox.business.service._writes_allowed",
        return_value=True,
    ), patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id",
        return_value="tenant-abc",
    ), patch(
        "integrations.ms.outbox.business.service.idempotency_guards_disabled",
        return_value=False,
    ):
        result = svc.enqueue_update_draft(
            entity_type=_ENTITY_TYPE,
            entity_public_id=_ENTITY_PUBLIC_ID,
            graph_message_id=_GRAPH_MESSAGE_ID,
            to_addresses=[{"email": "worker@example.com"}],
            subject="s",
            body="b",
        )

    assert result is created
    repo.create.assert_called_once()
    repo.count_by_entity_and_kind.assert_called_once_with(
        _ENTITY_TYPE, _ENTITY_PUBLIC_ID, KIND_UPDATE_DRAFT
    )


def test_outcome_persistence_failure_does_not_mark_done():
    repo = MagicMock()
    repo.update_payload.side_effect = OSError("db unavailable")
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    with patch(_GET_MESSAGE, return_value=_ok_get()), patch(
        _UPDATE_DRAFT, return_value=_ok_patch()
    ), patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    repo.mark_done.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()


def test_update_payload_none_does_not_mark_done():
    repo = MagicMock()
    repo.update_payload.return_value = None
    worker = MsOutboxWorker(repo=repo)
    row = _update_row(attempts=0)

    with patch(_GET_MESSAGE, return_value=_ok_get()), patch(
        _UPDATE_DRAFT, return_value=_ok_patch()
    ), patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    repo.mark_done.assert_not_called()
    repo.mark_failed.assert_called_once()


# ---------------------------------------------------------------------------
# Dispatch table guard
# ---------------------------------------------------------------------------


def test_dispatch_table_maps_update_draft_and_preserves_existing_kinds():
    worker = MsOutboxWorker(repo=MagicMock())
    table = worker._dispatch_table

    assert KIND_UPDATE_DRAFT in table
    assert table[KIND_UPDATE_DRAFT].__name__ == "_handle_update_draft"

    assert table[KIND_UPLOAD_SHAREPOINT_FILE].__name__ == "_handle_upload_sharepoint_file"
    assert table[KIND_APPEND_EXCEL_ROW].__name__ == "_handle_append_excel_row"
    assert table[KIND_INSERT_EXCEL_ROW].__name__ == "_handle_insert_excel_row"
    assert table[KIND_SEND_MAIL].__name__ == "_handle_send_mail"
