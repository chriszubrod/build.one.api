"""U-579 — capture the draft's conversation id at CREATE time.

Graph message ids change when a message moves. The weekly Ramp chaser used to
learn a draft's ``conversation_id`` by GETting the stored ``graph_message_id``
on a LATER sweep — a read-back that can only resolve while the draft still sits
in Drafts. Observed 2026-09-29: the owner cleared all six drafts the same
afternoon (4 sent, 2 deleted), so every stored id returned "not found" by the
next weekly sweep and ``sent_observed`` was 0 despite four confirmed sends.

The conversation id was in the create-draft response all along — the worker
just discarded it. It is now stamped onto the outbox payload at create time,
and the digest reads it from there.

Invariants these tests pin:
  * the MS outbox worker is SHARED by every MS integration — the new keys are
    strictly additive and nothing else about any kind changes;
  * the read-back GET remains the FALLBACK for rows enqueued before this change
    (six of them in production), including its INCONCLUSIVE-on-failure
    semantics — a vanished id stamps NOTHING, because 2 of the 6 vanished ids
    observed were deletions, not sends.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService
from entities.ramp_chaser_digest.business.model import RampChaserDigest
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.worker import MsOutboxWorker
from integrations.ramp.user.business.service import RampUserRosterEntry

_CREATE_DRAFT = "integrations.ms.mail.external.client.create_draft"
_GET_MESSAGE = "integrations.ms.mail.external.client.get_message"
_LIST_MESSAGES = "integrations.ms.mail.external.client.list_messages"
_UPLOAD_SMALL = "integrations.ms.sharepoint.external.client.upload_small_file"


# ---------------------------------------------------------------------------
# Worker fixtures
# ---------------------------------------------------------------------------


def _outbox_row(**overrides):
    defaults = dict(
        id=1,
        public_id="ob-1",
        row_version="rv-1",
        kind="send_mail",
        entity_type="RampChaserDigest",
        entity_public_id="digest-1",
        tenant_id="tenant-1",
        request_id="req-1",
        payload="{}",
        status="in_progress",
        attempts=0,
        ready_after=None,
        correlation_id=None,
    )
    defaults.update(overrides)
    return MsOutbox(**defaults)


def _send_payload():
    return {
        "to_addresses": [{"email": "alex@example.com", "name": "Alex"}],
        "cc_addresses": [],
        "bcc_addresses": [],
        "subject": "Ramp receipts",
        "body": "<p>body</p>",
        "body_type": "HTML",
        "attachment": None,
        "mode": "draft",
    }


def _worker_with_repo():
    repo = MagicMock()
    repo.update_payload.return_value = SimpleNamespace(row_version="rv-2")
    return MsOutboxWorker(repo=repo), repo


# ---------------------------------------------------------------------------
# 1–3: the shared MS outbox worker
# ---------------------------------------------------------------------------


def test_worker_stamps_conversation_and_internet_message_id():
    """The fix: the create-draft response already carries both ids."""
    worker, repo = _worker_with_repo()
    payload = _send_payload()

    draft_response = {
        "status_code": 201,
        "draft": {
            "message_id": "AAMkAG-draft",
            "conversation_id": "conv-captured",
            "internet_message_id": "<imid-captured@example.com>",
        },
    }

    with patch(_CREATE_DRAFT, return_value=draft_response):
        worker._handle_send_mail(_outbox_row(), payload)

    assert payload["graph_message_id"] == "AAMkAG-draft"
    assert payload["conversation_id"] == "conv-captured"
    assert payload["internet_message_id"] == "<imid-captured@example.com>"

    repo.update_payload.assert_called_once()
    persisted = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert persisted["conversation_id"] == "conv-captured"
    assert persisted["internet_message_id"] == "<imid-captured@example.com>"
    assert persisted["graph_message_id"] == "AAMkAG-draft"


def test_worker_unchanged_when_draft_lacks_the_new_fields():
    """Strictly additive: a response without the ids behaves exactly as before."""
    worker, repo = _worker_with_repo()
    payload = _send_payload()

    with patch(
        _CREATE_DRAFT,
        return_value={"status_code": 201, "draft": {"message_id": "mid-only"}},
    ) as draft:
        worker._handle_send_mail(_outbox_row(), payload)

    draft.assert_called_once()
    assert payload["graph_message_id"] == "mid-only"
    assert "conversation_id" not in payload
    assert "internet_message_id" not in payload

    repo.update_payload.assert_called_once()
    persisted = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert persisted["graph_message_id"] == "mid-only"
    assert "conversation_id" not in persisted
    assert "internet_message_id" not in persisted


@pytest.mark.parametrize(
    "draft_body",
    [
        {"message_id": "m", "conversation_id": None, "internet_message_id": None},
        {"message_id": "m", "conversation_id": "", "internet_message_id": ""},
    ],
)
def test_worker_does_not_stamp_empty_ids(draft_body):
    """Graph returning null/empty is the same as not returning it — no junk keys."""
    worker, _repo = _worker_with_repo()
    payload = _send_payload()

    with patch(_CREATE_DRAFT, return_value={"status_code": 201, "draft": draft_body}):
        worker._handle_send_mail(_outbox_row(), payload)

    assert payload["graph_message_id"] == "m"
    assert "conversation_id" not in payload
    assert "internet_message_id" not in payload


def test_other_outbox_kinds_are_untouched():
    """Blast-radius guard: this worker also drains SharePoint / Excel / review mail."""
    worker, repo = _worker_with_repo()
    payload = {
        "drive_id": "drive-1",
        "parent_item_id": "folder-1",
        "filename": "packet.pdf",
        "content_type": "application/pdf",
        "blob_path": "attachments/packet.pdf",
    }
    before = dict(payload)

    with patch.object(
        MsOutboxWorker, "_fetch_blob", return_value=b"%PDF-1.4 small"
    ) as fetch, patch(_UPLOAD_SMALL, return_value={"status_code": 201}) as upload:
        worker._handle_upload_sharepoint_file(
            _outbox_row(kind="upload_sharepoint_file"), payload
        )

    fetch.assert_called_once_with("attachments/packet.pdf")
    upload.assert_called_once()
    assert payload == before
    assert "conversation_id" not in payload
    assert "internet_message_id" not in payload
    assert "graph_message_id" not in payload
    repo.update_payload.assert_not_called()


# ---------------------------------------------------------------------------
# Digest fixtures
# ---------------------------------------------------------------------------


def _digest_row(
    *,
    public_id="digest-pub-1",
    card_holder_ramp_user_id="user-a",
    week_of="2026-09-29",
    draft_message_id=None,
    conversation_id=None,
    internet_message_id=None,
):
    return RampChaserDigest(
        id=1,
        public_id=public_id,
        row_version=None,
        card_holder_ramp_user_id=card_holder_ramp_user_id,
        week_of=week_of,
        draft_message_id=draft_message_id,
        conversation_id=conversation_id,
        internet_message_id=internet_message_id,
        last_drafted_at=None,
        last_notified_at=None,
        notify_count=0,
        outcome=None,
        recipient_hash=None,
        created_at=None,
        updated_at=None,
    )


def _outbox_payload_row(**payload):
    return SimpleNamespace(payload=json.dumps(payload))


@pytest.fixture
def draft_mode(monkeypatch):
    monkeypatch.setenv("RAMP_CHASER_MODE", "draft")


@pytest.fixture
def mocks():
    digest_repo = MagicMock()
    digest_repo.read_uncaptured.return_value = []
    digest_repo.read_outstanding.return_value = []
    digest_repo.read_latest_recipient_hash.return_value = None
    digest_repo.read_by_card_holder_and_week.return_value = None

    follow_up_repo = MagicMock()
    follow_up_repo.read_unresolved.return_value = []

    outbox_repo = MagicMock()
    outbox_repo.read_completed_by_entity.return_value = []
    outbox_repo.count_by_entity_and_kind.return_value = 0
    outbox_repo.read_pending_by_entity.return_value = []

    user_service = MagicMock()
    user_service.build_roster.return_value = {
        "user-a": RampUserRosterEntry(
            ramp_user_id="user-a",
            email="alex@example.com",
            status="USER_ACTIVE",
            is_active=True,
        ),
    }

    ms_outbox_svc = MagicMock()
    ms_outbox_svc.enqueue_send_mail.return_value = SimpleNamespace(public_id="ob-1")

    patches = {
        "digest_repo": patch(
            "entities.ramp_chaser_digest.persistence.repo.RampChaserDigestRepository",
            return_value=digest_repo,
        ),
        "follow_up_repo": patch(
            "entities.ramp_transaction_follow_up.persistence.repo."
            "RampTransactionFollowUpRepository",
            return_value=follow_up_repo,
        ),
        "outbox_repo": patch(
            "integrations.ms.outbox.persistence.repo.MsOutboxRepository",
            return_value=outbox_repo,
        ),
        "user_service": patch(
            "integrations.ramp.user.business.service.RampUserService",
            return_value=user_service,
        ),
        "user_client": patch(
            "integrations.ramp.user.external.client.RampUserExternalClient",
        ),
        "ms_outbox": patch(
            "integrations.ms.outbox.business.service.MsOutboxService",
            return_value=ms_outbox_svc,
        ),
    }
    for p in patches.values():
        p.start()
    yield SimpleNamespace(
        digest_repo=digest_repo,
        follow_up_repo=follow_up_repo,
        outbox_repo=outbox_repo,
        ms_outbox_svc=ms_outbox_svc,
    )
    for p in patches.values():
        p.stop()


# ---------------------------------------------------------------------------
# 4–6: digest capture — payload first, GET only as fallback
# ---------------------------------------------------------------------------


def test_digest_captures_from_payload_without_any_read_back(mocks, draft_mode):
    """The whole point: the id is already ours — do not ask Graph for it."""
    mocks.digest_repo.read_uncaptured.return_value = [
        _digest_row(public_id="digest-new")
    ]
    mocks.outbox_repo.read_completed_by_entity.return_value = [
        _outbox_payload_row(
            graph_message_id="graph-new",
            conversation_id="conv-at-create",
            internet_message_id="<imid-at-create@example.com>",
        )
    ]
    mocks.outbox_repo.count_by_entity_and_kind.return_value = 1

    with patch(_GET_MESSAGE) as get_msg:
        RampChaserDigestService().run_for_week("2026-09-29")

    get_msg.assert_not_called()
    mocks.digest_repo.stamp_drafted.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-29",
        draft_message_id="graph-new",
        conversation_id="conv-at-create",
        internet_message_id="<imid-at-create@example.com>",
    )


def test_digest_falls_back_to_get_for_pre_u579_payloads(mocks, draft_mode):
    """The six production rows enqueued before this change keep their old path."""
    mocks.digest_repo.read_uncaptured.return_value = [
        _digest_row(public_id="digest-legacy")
    ]
    mocks.outbox_repo.read_completed_by_entity.return_value = [
        _outbox_payload_row(graph_message_id="graph-legacy")
    ]
    mocks.outbox_repo.count_by_entity_and_kind.return_value = 1

    with patch(
        _GET_MESSAGE,
        return_value={
            "status_code": 200,
            "email": {
                "conversation_id": "conv-from-get",
                "internet_message_id": "<imid-from-get@example.com>",
            },
        },
    ) as get_msg:
        RampChaserDigestService().run_for_week("2026-09-29")

    get_msg.assert_called_once_with(message_id="graph-legacy", include_body=False)
    mocks.digest_repo.stamp_drafted.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-29",
        draft_message_id="graph-legacy",
        conversation_id="conv-from-get",
        internet_message_id="<imid-from-get@example.com>",
    )


@pytest.mark.parametrize(
    "get_result",
    [
        {"status_code": 404, "email": None},
        {"status_code": 503, "email": None, "is_retryable": True},
    ],
)
def test_failed_fallback_get_is_inconclusive_and_stamps_nothing(
    mocks, draft_mode, get_result
):
    """Fail SAFE — a non-resolving id never becomes a 'notified' record."""
    mocks.digest_repo.read_uncaptured.return_value = [
        _digest_row(public_id="digest-vanished")
    ]
    mocks.outbox_repo.read_completed_by_entity.return_value = [
        _outbox_payload_row(graph_message_id="graph-vanished")
    ]
    mocks.outbox_repo.count_by_entity_and_kind.return_value = 1

    with patch(_GET_MESSAGE, return_value=get_result):
        RampChaserDigestService().run_for_week("2026-09-29")

    mocks.digest_repo.stamp_drafted.assert_not_called()
    mocks.digest_repo.stamp_notified.assert_not_called()
    mocks.digest_repo.stamp_outcome.assert_not_called()


# ---------------------------------------------------------------------------
# 7: the observed production scenario, end to end
# ---------------------------------------------------------------------------


def test_vanished_draft_id_still_detected_as_sent_via_captured_conversation(
    mocks, draft_mode
):
    """2026-09-29 replay: id gone from the store, send still detected.

    Sweep 1 captures the conversation id straight from the outbox payload
    (no GET can succeed once the owner has sent the draft). Sweep 2 finds the
    stored draft id unresolvable — exactly the 404 that produced
    ``sent_observed: 0`` before this unit — and falls through to the
    conversation lookup, which finds the message in SentItems.
    """
    # --- Sweep 1: capture at create time ---------------------------------
    mocks.digest_repo.read_uncaptured.return_value = [
        _digest_row(public_id="digest-observed")
    ]
    mocks.outbox_repo.read_completed_by_entity.return_value = [
        _outbox_payload_row(
            graph_message_id="graph-will-vanish",
            conversation_id="conv-observed",
            internet_message_id="<imid-observed@example.com>",
        )
    ]
    mocks.outbox_repo.count_by_entity_and_kind.return_value = 1

    with patch(_GET_MESSAGE) as get_msg:
        RampChaserDigestService().run_for_week("2026-09-29")

    get_msg.assert_not_called()
    stamped = mocks.digest_repo.stamp_drafted.call_args.kwargs
    assert stamped["conversation_id"] == "conv-observed"

    # --- Sweep 2: the owner has sent it; the stored id no longer resolves --
    mocks.digest_repo.read_uncaptured.return_value = []
    mocks.digest_repo.stamp_drafted.reset_mock()
    mocks.digest_repo.read_outstanding.return_value = [
        _digest_row(
            public_id="digest-observed",
            draft_message_id=stamped["draft_message_id"],
            conversation_id=stamped["conversation_id"],
            internet_message_id=stamped["internet_message_id"],
        )
    ]

    with patch(
        _GET_MESSAGE,
        return_value={
            "status_code": 404,
            "email": None,
            "error": "The specified object was not found in the store.",
        },
    ), patch(
        _LIST_MESSAGES,
        return_value={"status_code": 200, "messages": [{"id": "sent-copy"}]},
    ) as list_msgs:
        result = RampChaserDigestService().run_for_week("2026-09-29")

    assert result["sent_observed"] == 1
    assert result["discarded_unsent"] == 0
    mocks.digest_repo.stamp_notified.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-29",
        outcome="sent",
    )
    # SentItems is consulted first and settles it — DeletedItems is not needed.
    assert list_msgs.call_count == 1
    assert list_msgs.call_args.kwargs["folder"] == "sentitems"
    assert "conv-observed" in list_msgs.call_args.kwargs["filter_query"]


def test_vanished_draft_found_in_deleted_items_is_not_a_send(mocks, draft_mode):
    """2 of the 6 vanished ids were DELETIONS — never infer a send from absence."""
    mocks.digest_repo.read_outstanding.return_value = [
        _digest_row(
            public_id="digest-deleted",
            draft_message_id="graph-deleted",
            conversation_id="conv-deleted",
        )
    ]

    def _list_side_effect(*, folder, top, filter_query):
        if folder == "sentitems":
            return {"status_code": 200, "messages": []}
        return {"status_code": 200, "messages": [{"id": "deleted-copy"}]}

    with patch(
        _GET_MESSAGE, return_value={"status_code": 404, "email": None}
    ), patch(_LIST_MESSAGES, side_effect=_list_side_effect):
        result = RampChaserDigestService().run_for_week("2026-09-29")

    assert result["sent_observed"] == 0
    assert result["discarded_unsent"] == 1
    mocks.digest_repo.stamp_notified.assert_not_called()


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param(["bad"], id="list"),
        pytest.param({"bad": "shape"}, id="dict"),
        pytest.param(12345, id="int"),
        pytest.param(True, id="bool"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("", id="empty-string"),
    ],
)
def test_malformed_conversation_id_falls_back_instead_of_stamping_junk(bad_value):
    """A non-str identity must route to the FALLBACK, never activate the fast path.

    Found by Pass 1. The original `str(value) if value else None` turned `["bad"]`
    into `"['bad']"` — id-shaped enough to satisfy the fast path, which then SKIPS
    the read-back GET that could still have fetched the real conversation id.

    Stamping a fabricated id is strictly worse than capturing nothing: every later
    SentItems/DeletedItems lookup then searches for a conversation that does not
    exist, so the row can never be reconciled and the failure is silent.
    """
    from entities.ramp_chaser_digest.business.digest_service import (
        _captured_identity,
        RampChaserDigestService,
    )

    assert _captured_identity(bad_value) is None

    row = SimpleNamespace(
        payload=json.dumps(
            {
                "graph_message_id": "m-1",
                "conversation_id": bad_value,
                "internet_message_id": bad_value,
            }
        )
    )
    identity = RampChaserDigestService._draft_identity_from_outbox_rows([row])
    assert identity["graph_message_id"] == "m-1", "the message id is still usable"
    assert identity["conversation_id"] is None, "malformed => absent => fallback"
    assert identity["internet_message_id"] is None


def test_captured_identity_accepts_a_real_id_and_trims_it():
    from entities.ramp_chaser_digest.business.digest_service import _captured_identity

    assert _captured_identity("  AAMkAGE2-conv-1  ") == "AAMkAGE2-conv-1"


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(["bad"], id="list"),
        pytest.param({"bad": "shape"}, id="dict"),
        pytest.param(12345, id="int"),
        pytest.param("   ", id="whitespace-only"),
    ],
)
def test_worker_refuses_to_persist_a_non_string_identity(bad):
    """The worker must not write a malformed identity into the payload at all.

    Belt to the digest's braces. `_captured_identity` already refuses to USE a
    non-str, but a consumer that reached for the raw payload key would still find
    something id-shaped. Refusing at the write side means the bad value never
    exists to be misread — and a downstream reader added later inherits the
    guarantee without having to know about it.

    Added because a mutation removing this guard left the suite GREEN: the digest
    tests pin the read side only.
    """
    worker, repo = _worker_with_repo()
    payload = _send_payload()

    with patch(
        _CREATE_DRAFT,
        return_value={
            "status_code": 201,
            "draft": {
                "message_id": "mid-1",
                "conversation_id": bad,
                "internet_message_id": bad,
            },
        },
    ):
        worker._handle_send_mail(_outbox_row(), payload)

    assert payload["graph_message_id"] == "mid-1", "the good field still lands"
    assert "conversation_id" not in payload, f"a {type(bad).__name__} must not be persisted"
    assert "internet_message_id" not in payload

    persisted = json.loads(repo.update_payload.call_args.kwargs["payload"])
    assert "conversation_id" not in persisted
    assert "internet_message_id" not in persisted
