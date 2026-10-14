"""U-669b — on-demand copy of reviewer inbox replies onto recorded review threads."""

from __future__ import annotations

import importlib
import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import app
from entities.email_message.business.model import EmailMessage
from entities.email_message.business.service import MailboxPollService
from shared.access import EntityNotAccessibleError
from shared.api.errors import ApiError, ErrorCode
from shared.api.responses import STATUS_LOCKED_PREFIX, list_response
from shared.rbac_constants import Modules
from tests.test_review_route_rbac import _assert_current_user_rbac
from tests.test_u563b_review_thread_anchor import _outbox_row, _outbox_row_raw_payload

REVIEWER_REPLY_STATUS = "reviewer_reply"

BILL_PID = "bill-pub-u669b"
EXP_PID = "exp-pub-u669b"
RECORDED_CONV = "AAQkADRecordedThreadU669b"
OTHER_ENTITY_CONV = "AAQkADOtherDocThread"
CONV_WITH_QUOTE = "AAQkADit'sEscaped"
CONV_A = "AAQkADConvA669b"
CONV_B = "AAQkADConvB669b"
MAILBOX = "invoices@example.com"
PRE_CUTOFF = "2026-09-29 12:00:00"
BILL_SYNC_PATH = f"/api/v1/bill/{BILL_PID}/review-replies/sync"
EXP_SYNC_PATH = f"/api/v1/expense/{EXP_PID}/review-replies/sync"

SYNC_ROUTE = (
    (
        "entities.bill.api.router",
        "BillService",
        "Bill",
        BILL_PID,
        "Bills",
        BILL_SYNC_PATH,
        "Bill",
        1,
    ),
    (
        "entities.expense.api.router",
        "ExpenseService",
        "Expense",
        EXP_PID,
        Modules.EXPENSES,
        EXP_SYNC_PATH,
        "Expense",
        99,
    ),
)


@pytest.fixture(autouse=True)
def _clear_pause_email_poll_env(monkeypatch):
    monkeypatch.delenv("PAUSE_EMAIL_POLL", raising=False)


@pytest.fixture
def api_client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


def _override_sync_rbac(router_module):
    dep = inspect.signature(router_module.sync_review_replies_router).parameters[
        "current_user"
    ].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub"}


def _patch_outbox(rows):
    outbox_mock = MagicMock(read_completed_by_entity=MagicMock(return_value=rows))
    return patch(
        "integrations.ms.outbox.persistence.repo.MsOutboxRepository",
        return_value=outbox_mock,
    ), outbox_mock


@contextmanager
def _patch_copy_review_thread_graph(
    svc,
    *,
    settings,
    list_mock,
    ingest_mock=None,
    message_repo=None,
):
    with patch(
        "entities.email_message.business.service.config.Settings",
        return_value=settings,
    ), patch(
        "entities.email_message.business.service.mail_client.list_messages",
        list_mock,
    ):
        ingest_patch = (
            patch.object(svc, "_ingest_messages", ingest_mock)
            if ingest_mock is not None
            else None
        )
        repo_patch = (
            patch.object(svc, "message_repo", message_repo)
            if message_repo is not None
            else None
        )
        if ingest_patch is not None:
            ingest_patch.start()
        if repo_patch is not None:
            repo_patch.start()
        try:
            yield
        finally:
            if repo_patch is not None:
                repo_patch.stop()
            if ingest_patch is not None:
                ingest_patch.stop()


def _stub_ingest_message_repos(svc):
    svc.message_repo.read_by_graph_message_id = MagicMock(return_value=None)
    svc.message_repo.upsert = MagicMock(return_value=SimpleNamespace(id=1))
    svc.attachment_repo.read_by_email_message_id = MagicMock(return_value=[])


# ---------------------------------------------------------------------------
# recorded_review_conversation_ids
# ---------------------------------------------------------------------------


def test_recorded_review_conversation_ids_returns_exact_ids_for_entity():
    from shared.authz.provenance import recorded_review_conversation_ids

    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id="AAQkADSecondThread",
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
    ]
    ob_patch, outbox_mock = _patch_outbox(rows)
    with ob_patch:
        assert recorded_review_conversation_ids("Bill", BILL_PID) == {
            RECORDED_CONV,
            "AAQkADSecondThread",
        }
    outbox_mock.read_completed_by_entity.assert_called_once_with(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        kind="send_mail",
    )


def test_recorded_review_conversation_ids_ignores_other_entity_rows():
    from shared.authz.provenance import recorded_review_conversation_ids

    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id="other-bill-pub",
            conversation_id=OTHER_ENTITY_CONV,
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
    ]
    with _patch_outbox(rows)[0]:
        assert recorded_review_conversation_ids("Bill", BILL_PID) == {RECORDED_CONV}


def test_recorded_review_conversation_ids_skips_non_send_mail_kind_on_row():
    from shared.authz.provenance import recorded_review_conversation_ids

    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
            kind="send_mail",
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id="AAQkADWrongKind",
            kind="update_excel",
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
    ]
    with _patch_outbox(rows)[0]:
        assert recorded_review_conversation_ids("Bill", BILL_PID) == {RECORDED_CONV}


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps([]),
        json.dumps({"conversation_id": ""}),
        json.dumps({"conversation_id": "   "}),
        json.dumps({"conversation_id": 99}),
    ],
)
def test_recorded_review_conversation_ids_skips_bad_payloads(payload):
    from shared.authz.provenance import recorded_review_conversation_ids

    row = _outbox_row_raw_payload(payload)
    row.entity_public_id = BILL_PID
    row.created_datetime = PRE_CUTOFF
    with _patch_outbox([row])[0]:
        assert recorded_review_conversation_ids("Bill", BILL_PID) == set()


def test_recorded_and_expected_helpers_consistent_on_recorded_fixture():
    from shared.authz.provenance import (
        expected_review_thread_conversation_ids,
        recorded_review_conversation_ids,
    )

    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
            subject=None,
            created_datetime=PRE_CUTOFF,
        ),
    ]
    ob_patch, _ = _patch_outbox(rows)
    em_patch = patch(
        "entities.email_message.persistence.repo.EmailMessageRepository",
        return_value=MagicMock(read_by_conversation_id=MagicMock(return_value=[])),
    )
    with ob_patch, em_patch:
        recorded = recorded_review_conversation_ids("Bill", BILL_PID)
        for reply_conv in (RECORDED_CONV, RECORDED_CONV.swapcase(), "AAQkADUnrelated"):
            expected = expected_review_thread_conversation_ids(
                entity_type="Bill",
                entity_public_id=BILL_PID,
                reply_conversation_id=reply_conv,
            )
            if reply_conv in recorded:
                assert expected == {reply_conv}
            else:
                assert expected == set()


# ---------------------------------------------------------------------------
# copy_review_thread_replies
# ---------------------------------------------------------------------------


def test_copy_review_thread_replies_empty_ids_skips_graph():
    svc = MailboxPollService()
    with patch(
        "entities.email_message.business.service.mail_client.list_messages"
    ) as list_mock:
        assert svc.copy_review_thread_replies([]) == []
        list_mock.assert_not_called()


def test_copy_review_thread_replies_raises_when_mailbox_unconfigured():
    from entities.email_message.business.service import ReviewReplyCopyError

    svc = MailboxPollService()
    settings = SimpleNamespace(invoice_inbox_email=None)
    with patch("entities.email_message.business.service.config.Settings", return_value=settings):
        with pytest.raises(ReviewReplyCopyError):
            svc.copy_review_thread_replies([RECORDED_CONV])


def test_copy_review_thread_replies_filter_escaping_and_ingest():
    svc = MailboxPollService()
    settings = SimpleNamespace(invoice_inbox_email=MAILBOX)
    escaped = CONV_WITH_QUOTE.replace("'", "''")
    expected_filter = (
        f"receivedDateTime ge 2000-01-01T00:00:00Z and conversationId eq '{escaped}'"
    )

    inbox_row = EmailMessage(
        id=1,
        public_id="em-inbox-1",
        folder="inbox",
        conversation_id=CONV_WITH_QUOTE,
        from_address="pm@example.com",
        from_name="PM",
        received_datetime="2026-10-01T12:00:00Z",
        subject="Re: review",
        body_preview="approved",
    )
    sent_row = EmailMessage(
        id=2,
        public_id="em-sent-1",
        folder="sentitems",
        conversation_id=CONV_WITH_QUOTE,
    )

    list_mock = MagicMock(
        return_value={"status_code": 200, "messages": [{"message_id": "g1"}]}
    )
    ingest_mock = MagicMock(return_value=(1, 0, []))
    em_repo = MagicMock(
        read_by_conversation_id=MagicMock(return_value=[sent_row, inbox_row])
    )

    with _patch_copy_review_thread_graph(
        svc,
        settings=settings,
        list_mock=list_mock,
        ingest_mock=ingest_mock,
        message_repo=em_repo,
    ):
        result = svc.copy_review_thread_replies([CONV_WITH_QUOTE])

    list_mock.assert_called_once_with(
        folder="inbox",
        top=50,
        filter_query=expected_filter,
        order_by="receivedDateTime desc",
        mailbox=MAILBOX,
    )
    ingest_mock.assert_called_once_with(
        messages=[{"message_id": "g1"}],
        mailbox=MAILBOX,
        folder="inbox",
        default_status=REVIEWER_REPLY_STATUS,
        download_attachments=False,
    )
    em_repo.read_by_conversation_id.assert_called_once_with(
        conversation_id=CONV_WITH_QUOTE,
        max_rows=500,
    )
    assert result == [inbox_row]


def test_copy_review_thread_replies_two_conversations_dedupes_and_sorts(caplog):
    svc = MailboxPollService()
    settings = SimpleNamespace(invoice_inbox_email=MAILBOX)
    filter_a = (
        "receivedDateTime ge 2000-01-01T00:00:00Z and "
        f"conversationId eq '{CONV_A}'"
    )
    filter_b = (
        "receivedDateTime ge 2000-01-01T00:00:00Z and "
        f"conversationId eq '{CONV_B}'"
    )

    older = EmailMessage(
        id=10,
        public_id="em-old",
        folder="inbox",
        conversation_id=CONV_B,
        received_datetime="2026-10-01T10:00:00Z",
    )
    newer = EmailMessage(
        id=20,
        public_id="em-new",
        folder="inbox",
        conversation_id=CONV_A,
        received_datetime="2026-10-02T10:00:00Z",
    )
    duplicate = EmailMessage(
        id=10,
        public_id="em-old-dup",
        folder="inbox",
        conversation_id=CONV_B,
        received_datetime="2026-10-01T10:00:00Z",
    )
    sent_noise = EmailMessage(
        id=99,
        public_id="em-sent",
        folder="sentitems",
        conversation_id=CONV_A,
        received_datetime="2026-10-03T10:00:00Z",
    )

    list_mock = MagicMock(
        side_effect=[
            {"status_code": 200, "messages": [{"message_id": "ga"}]},
            {"status_code": 200, "messages": [{"message_id": "gb"}]},
        ]
    )
    ingest_mock = MagicMock(
        side_effect=[(1, 0, []), (0, 0, [{"error": "partial fail"}])]
    )
    em_repo = MagicMock(
        read_by_conversation_id=MagicMock(
            side_effect=[[sent_noise, newer], [older, duplicate]]
        )
    )

    with _patch_copy_review_thread_graph(
        svc,
        settings=settings,
        list_mock=list_mock,
        ingest_mock=ingest_mock,
        message_repo=em_repo,
    ), caplog.at_level("WARNING"):
        result = svc.copy_review_thread_replies([CONV_A, CONV_B])

    assert list_mock.call_count == 2
    list_mock.assert_any_call(
        folder="inbox",
        top=50,
        filter_query=filter_a,
        order_by="receivedDateTime desc",
        mailbox=MAILBOX,
    )
    list_mock.assert_any_call(
        folder="inbox",
        top=50,
        filter_query=filter_b,
        order_by="receivedDateTime desc",
        mailbox=MAILBOX,
    )
    assert ingest_mock.call_count == 2
    for call in ingest_mock.call_args_list:
        assert call.kwargs["download_attachments"] is False
    em_repo.read_by_conversation_id.assert_any_call(
        conversation_id=CONV_A, max_rows=500
    )
    em_repo.read_by_conversation_id.assert_any_call(
        conversation_id=CONV_B, max_rows=500
    )
    assert result == [older, newer]
    assert any(CONV_B in r.message and "partial fail" in r.message for r in caplog.records)


@pytest.mark.parametrize("pause_value", ["true", "1", "YES "])
def test_copy_review_thread_replies_raises_when_email_poll_paused(
    monkeypatch, pause_value
):
    from entities.email_message.business.service import ReviewReplyCopyError

    monkeypatch.setenv("PAUSE_EMAIL_POLL", pause_value)
    svc = MailboxPollService()
    settings = SimpleNamespace(invoice_inbox_email=MAILBOX)
    list_mock = MagicMock()
    ingest_mock = MagicMock()

    with _patch_copy_review_thread_graph(
        svc,
        settings=settings,
        list_mock=list_mock,
        ingest_mock=ingest_mock,
    ):
        with pytest.raises(ReviewReplyCopyError) as exc_info:
            svc.copy_review_thread_replies([RECORDED_CONV])

    assert exc_info.value.status_code == 503
    assert exc_info.value.message == "email ingestion is paused (PAUSE_EMAIL_POLL)"
    list_mock.assert_not_called()
    ingest_mock.assert_not_called()


@pytest.mark.parametrize("pause_value", [None, "false", ""])
def test_copy_review_thread_replies_runs_when_email_poll_not_paused(
    monkeypatch, pause_value
):
    svc = MailboxPollService()
    settings = SimpleNamespace(invoice_inbox_email=MAILBOX)
    if pause_value is None:
        monkeypatch.delenv("PAUSE_EMAIL_POLL", raising=False)
    else:
        monkeypatch.setenv("PAUSE_EMAIL_POLL", pause_value)

    inbox_row = EmailMessage(
        id=1,
        public_id="em-inbox-pause-off",
        folder="inbox",
        conversation_id=RECORDED_CONV,
        received_datetime="2026-10-01T12:00:00Z",
    )
    list_mock = MagicMock(
        return_value={"status_code": 200, "messages": [{"message_id": "g1"}]}
    )
    ingest_mock = MagicMock(return_value=(1, 0, []))
    em_repo = MagicMock(read_by_conversation_id=MagicMock(return_value=[inbox_row]))

    with _patch_copy_review_thread_graph(
        svc,
        settings=settings,
        list_mock=list_mock,
        ingest_mock=ingest_mock,
        message_repo=em_repo,
    ):
        result = svc.copy_review_thread_replies([RECORDED_CONV])

    list_mock.assert_called_once()
    ingest_mock.assert_called_once()
    assert result == [inbox_row]


def test_copy_review_thread_replies_non_200_raises():
    from entities.email_message.business.service import ReviewReplyCopyError

    svc = MailboxPollService()
    settings = SimpleNamespace(invoice_inbox_email=MAILBOX)
    list_mock = MagicMock(return_value={"status_code": 503, "message": "graph down"})

    with patch(
        "entities.email_message.business.service.config.Settings",
        return_value=settings,
    ), patch(
        "entities.email_message.business.service.mail_client.list_messages",
        list_mock,
    ):
        with pytest.raises(ReviewReplyCopyError) as exc_info:
            svc.copy_review_thread_replies([RECORDED_CONV])
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# _ingest_messages default_status
# ---------------------------------------------------------------------------


def _minimal_graph_summary(message_id: str = "graph-msg-1"):
    return {"message_id": message_id, "categories": []}


@pytest.mark.parametrize(
    "folder,default_status,expected",
    [
        ("inbox", None, "pending"),
        ("sentitems", None, "outbound"),
        ("inbox", REVIEWER_REPLY_STATUS, REVIEWER_REPLY_STATUS),
    ],
)
def test_ingest_messages_default_processing_status(folder, default_status, expected):
    svc = MailboxPollService()
    _stub_ingest_message_repos(svc)

    with patch(
        "entities.email_message.business.service.mail_client.get_message",
        return_value={
            "status_code": 200,
            "email": {
                "conversation_id": "c1",
                "from_email": "vendor@x.com" if folder == "inbox" else None,
            },
        },
    ):
        kwargs = {
            "messages": [_minimal_graph_summary()],
            "mailbox": MAILBOX,
            "folder": folder,
        }
        if default_status is not None:
            kwargs["default_status"] = default_status
        svc._ingest_messages(**kwargs)

    assert svc.message_repo.upsert.call_args.kwargs["default_processing_status"] == expected


def test_ingest_messages_download_attachments_false_skips_attachment_work():
    svc = MailboxPollService()
    _stub_ingest_message_repos(svc)

    with patch(
        "entities.email_message.business.service.mail_client.get_message",
        return_value={
            "status_code": 200,
            "email": {
                "conversation_id": "c1",
                "from_email": "pm@example.com",
                "has_attachments": True,
                "attachments": [{"id": "att-1", "name": "doc.pdf", "is_inline": False}],
            },
        },
    ), patch.object(svc, "_persist_attachment") as persist_mock:
        svc._ingest_messages(
            messages=[_minimal_graph_summary()],
            mailbox=MAILBOX,
            folder="inbox",
            default_status=REVIEWER_REPLY_STATUS,
            download_attachments=False,
        )

    persist_mock.assert_not_called()


def test_ingest_messages_download_attachments_true_still_downloads():
    svc = MailboxPollService()
    _stub_ingest_message_repos(svc)

    with patch(
        "entities.email_message.business.service.mail_client.get_message",
        return_value={
            "status_code": 200,
            "email": {
                "conversation_id": "c1",
                "from_email": "pm@example.com",
                "has_attachments": True,
                "attachments": [{"id": "att-1", "name": "doc.pdf", "is_inline": False}],
            },
        },
    ), patch.object(svc, "_persist_attachment", return_value=True) as persist_mock:
        svc._ingest_messages(
            messages=[_minimal_graph_summary()],
            mailbox=MAILBOX,
            folder="inbox",
        )

    persist_mock.assert_called_once()


def test_ingest_messages_self_sent_dropped_with_reviewer_reply_status():
    svc = MailboxPollService()
    svc.message_repo.read_by_graph_message_id = MagicMock(return_value=None)
    svc.message_repo.upsert = MagicMock(return_value=SimpleNamespace(id=1))

    summary = _minimal_graph_summary()
    summary["from_email"] = MAILBOX

    with patch(
        "entities.email_message.business.service.mail_client.get_message",
    ) as get_mock:
        svc._ingest_messages(
            messages=[summary],
            mailbox=MAILBOX,
            folder="inbox",
            default_status=REVIEWER_REPLY_STATUS,
        )

    get_mock.assert_not_called()
    svc.message_repo.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Bill / Expense route — sync_review_replies_router
# ---------------------------------------------------------------------------


def _draft_entity(public_id: str):
    entity = MagicMock()
    entity.public_id = public_id
    entity.is_draft = True
    return entity


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id",
    SYNC_ROUTE,
    ids=["bill", "expense"],
)
def test_sync_route_rbac(
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
):
    router = importlib.import_module(router_module_name)
    _assert_current_user_rbac(
        router.sync_review_replies_router,
        module,
        "can_update",
    )


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id",
    SYNC_ROUTE,
    ids=["bill", "expense"],
)
def test_sync_route_404_not_found(
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
):
    router = importlib.import_module(router_module_name)
    with patch.object(router, service_attr) as entity_svc, patch.object(
        router, "recorded_review_conversation_ids"
    ) as recorded, patch.object(router, "MailboxPollService") as poll_svc:
        entity_svc.return_value.read_by_public_id.return_value = None
        with pytest.raises(ApiError) as exc_info:
            router.sync_review_replies_router(
                public_id=public_id,
                current_user={"sub": "u"},
            )
        assert exc_info.value.status_code == 404
        recorded.assert_not_called()
        poll_svc.assert_not_called()


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id",
    SYNC_ROUTE,
    ids=["bill", "expense"],
)
def test_sync_route_422_status_locked_when_not_draft(
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
):
    router = importlib.import_module(router_module_name)
    with patch.object(router, service_attr) as entity_svc, patch.object(
        router, "recorded_review_conversation_ids"
    ) as recorded, patch.object(router, "MailboxPollService") as poll_svc:
        entity_svc.return_value.read_by_public_id.return_value = MagicMock(
            is_draft=False, public_id=public_id
        )
        with pytest.raises(ApiError) as exc_info:
            router.sync_review_replies_router(
                public_id=public_id,
                current_user={"sub": "u"},
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.error_code == ErrorCode.STATUS_LOCKED
        assert str(exc_info.value.detail).startswith(STATUS_LOCKED_PREFIX)
        recorded.assert_not_called()
        poll_svc.assert_not_called()


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id",
    SYNC_ROUTE,
    ids=["bill", "expense"],
)
def test_sync_route_422_no_recorded_thread(
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
):
    router = importlib.import_module(router_module_name)
    with patch.object(router, service_attr) as entity_svc, patch.object(
        router, "recorded_review_conversation_ids",
        return_value=set(),
    ) as recorded, patch.object(router, "MailboxPollService") as poll_svc:
        entity_svc.return_value.read_by_public_id.return_value = _draft_entity(public_id)
        with pytest.raises(HTTPException) as exc_info:
            router.sync_review_replies_router(
                public_id=public_id,
                current_user={"sub": "u"},
            )
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == "no recorded review thread for this document"
        recorded.assert_called_once_with(entity_type, public_id)
        poll_svc.assert_not_called()


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id,"
    "copy_err_status,copy_err_msg,log_fragment",
    [
        (
            *SYNC_ROUTE[0],
            502,
            "graph",
            "502",
        ),
        (
            *SYNC_ROUTE[1],
            503,
            "fail",
            "503",
        ),
    ],
    ids=["bill", "expense"],
)
def test_sync_route_502_on_copy_error(
    caplog,
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
    copy_err_status,
    copy_err_msg,
    log_fragment,
):
    from entities.email_message.business.service import ReviewReplyCopyError

    router = importlib.import_module(router_module_name)
    with patch.object(router, service_attr) as entity_svc, patch.object(
        router, "recorded_review_conversation_ids",
        return_value={RECORDED_CONV},
    ) as recorded, patch.object(router, "MailboxPollService") as poll_svc:
        entity_svc.return_value.read_by_public_id.return_value = _draft_entity(public_id)
        poll_svc.return_value.copy_review_thread_replies.side_effect = ReviewReplyCopyError(
            copy_err_status, copy_err_msg
        )
        with caplog.at_level("WARNING"), pytest.raises(HTTPException) as exc_info:
            router.sync_review_replies_router(
                public_id=public_id,
                current_user={"sub": "u"},
            )
        assert exc_info.value.status_code == 502
        recorded.assert_called_once_with(entity_type, public_id)
        assert any(
            log_fragment in r.message and copy_err_msg in r.message
            for r in caplog.records
        )


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id,"
    "em_kwargs,expected_item",
    [
        (
            *SYNC_ROUTE[0],
            {
                "public_id": "reply-pub",
                "from_address": "pm@example.com",
                "from_name": "PM",
                "received_datetime": "2026-10-01T12:00:00Z",
                "subject": "Re: bill",
                "body_preview": "approved",
            },
            {
                "email_message_public_id": "reply-pub",
                "from_address": "pm@example.com",
                "from_name": "PM",
                "received_datetime": "2026-10-01T12:00:00Z",
                "subject": "Re: bill",
                "body_preview": "approved",
            },
        ),
        (
            *SYNC_ROUTE[1],
            {
                "public_id": "exp-reply",
                "from_address": "owner@example.com",
                "from_name": "Owner",
                "received_datetime": "2026-10-02T09:00:00Z",
                "subject": "Re: expense",
                "body_preview": "reject",
            },
            {
                "email_message_public_id": "exp-reply",
                "from_address": "owner@example.com",
                "from_name": "Owner",
                "received_datetime": "2026-10-02T09:00:00Z",
                "subject": "Re: expense",
                "body_preview": "reject",
            },
        ),
    ],
    ids=["bill", "expense"],
)
def test_sync_route_200_envelope(
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
    em_kwargs,
    expected_item,
):
    router = importlib.import_module(router_module_name)
    em = EmailMessage(**em_kwargs)
    with patch.object(router, service_attr) as entity_svc, patch.object(
        router, "recorded_review_conversation_ids",
        return_value={RECORDED_CONV},
    ) as recorded, patch.object(router, "MailboxPollService") as poll_svc:
        entity_svc.return_value.read_by_public_id.return_value = _draft_entity(public_id)
        poll_svc.return_value.copy_review_thread_replies.return_value = [em]

        body = router.sync_review_replies_router(
            public_id=public_id,
            current_user={"sub": "u"},
        )

    assert body == list_response([expected_item])
    recorded.assert_called_once_with(entity_type, public_id)
    poll_svc.return_value.copy_review_thread_replies.assert_called_once_with({RECORDED_CONV})


@pytest.mark.parametrize(
    "router_module_name,service_attr,entity_type,public_id,module,sync_path,entity_label,entity_id",
    SYNC_ROUTE,
    ids=["bill", "expense"],
)
def test_sync_route_integration_entity_not_accessible_404(
    api_client,
    clear_dependency_overrides,
    router_module_name,
    service_attr,
    entity_type,
    public_id,
    module,
    sync_path,
    entity_label,
    entity_id,
):
    router = importlib.import_module(router_module_name)
    _override_sync_rbac(router)
    with patch.object(router, service_attr) as entity_svc, patch.object(
        router, "MailboxPollService"
    ) as poll_svc:
        entity_svc.return_value.read_by_public_id.side_effect = EntityNotAccessibleError(
            entity_label, entity_id
        )
        response = api_client.post(sync_path)
    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.NOT_FOUND
    poll_svc.assert_not_called()
