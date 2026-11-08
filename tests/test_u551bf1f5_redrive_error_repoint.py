"""U-551b-f1-f5 (+f3a): the system-admin redrive of an `error` repoint.

Every test names the mutant it kills. Repo, lock and outbox are mocked; nothing opens a connection."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.auth.business.service import get_current_user_api

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
OUTBOX = "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
TARGETS = [{"qbo_line_id": "L1", "sub_cost_code_id": 5, "project_id": 2, "description": "d",
            "repoint_from_item_ref_value": "ITEM-A"}]
RAW = json.dumps(TARGETS)
NONCANONICAL = json.dumps(TARGETS, indent=1)  # != RAW: a re-serialising mutant cannot reproduce it


def _item(status="error", source=REPOINT_CONFIRMATION_SOURCE, seq=6, targets=TARGETS, raw=RAW):
    return SimpleNamespace(
        public_id=PUBLIC_ID, status=status, confirmation_source=source, confirmation_seq=seq,
        realm_id="realm-1", repoint_targets=targets, repoint_targets_json=raw, written_sync_token="8",
        confirmed_project_id=202, confirmed_sub_cost_code_id=101, confirmed_description="desc",
    )


def _svc(item, recorded=SimpleNamespace(confirmation_seq=7)):
    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=item)
    svc.record_confirmation = MagicMock(return_value=recorded)
    svc._enqueue_recode_and_mark = MagicMock(return_value=True)
    return svc


def _assert_no_writes(svc):
    svc.record_confirmation.assert_not_called()
    svc._enqueue_recode_and_mark.assert_not_called()


@patch(GATE, return_value="writes off")
def test_gate_off_is_writes_disabled_with_no_read_or_write(_gate):
    # kills: the redrive bypassing the recode write gate
    svc = _svc(_item())
    assert svc.redrive_error_repoint(PUBLIC_ID) == {"status": "writes_disabled", "reason": "writes off"}
    svc.read_by_public_id.assert_not_called()
    _assert_no_writes(svc)


@patch(GATE, return_value=None)
@pytest.mark.parametrize("item,expected", [
    (None, "not_found"),                                                                  # kills: None deref / wrong status
    (_item(status="written"), "not_error"),                                               # kills: acting on a non-error row
    (_item(status="confirmed"), "not_error"),
    (_item(source="confirm_endpoint"), "not_a_repoint"),                                  # kills: redriving a first-write error (cockpit's job)
    (_item(source=None), "not_a_repoint"),
    (_item(targets=None, raw=None), "no_repoint_targets"),                                # kills: recording a NULL revision
    (_item(targets=[], raw="[]"), "no_repoint_targets"),                                  # kills: `is None` instead of falsy
])
def test_each_refusal_has_exact_status_and_no_record_or_enqueue(_gate, item, expected):
    # kills: any refusal that still records / enqueues, or a reordered/renamed refusal
    svc = _svc(item)
    with patch(OUTBOX) as outbox:
        assert svc.redrive_error_repoint(PUBLIC_ID) == {"status": expected}
    _assert_no_writes(svc)
    outbox.return_value.repo.read_dead_letter_by_entity.assert_not_called()


@patch(GATE, return_value=None)
def test_dead_letter_blocks_before_any_write_and_is_looked_up_by_the_enqueue_identity(_gate):
    # kills: skipping the dead-letter check (re-write behind a possibly-landed dead letter) or looking it
    # up under a different kind/entity type than confirm enqueues with
    svc = _svc(_item())
    with patch(OUTBOX) as outbox:
        outbox.return_value.repo.read_dead_letter_by_entity.return_value = SimpleNamespace(public_id="x")
        assert svc.redrive_error_repoint(PUBLIC_ID) == {"status": "dead_letter_pending"}
    outbox.return_value.repo.read_dead_letter_by_entity.assert_called_once_with(
        kind="recode_purchase_line", entity_type="CodingSuggestion", entity_public_id=PUBLIC_ID)
    _assert_no_writes(svc)


@patch(GATE, return_value=None)
def test_happy_path_records_same_revision_fenced_then_enqueues_with_the_new_seq(_gate):
    # kills: a wrong/absent fence seq, the wrong source, re-serialised/dropped targets, or enqueueing
    # with the OLD seq (the mark would then fence itself out)
    assert NONCANONICAL != RAW
    svc = _svc(_item(seq=6, raw=NONCANONICAL))
    with patch(OUTBOX) as outbox:
        outbox.return_value.repo.read_dead_letter_by_entity.return_value = None
        out = svc.redrive_error_repoint(PUBLIC_ID)
    assert out == {"status": "enqueued", "confirmation_seq": 7}
    kw = svc.record_confirmation.call_args.kwargs
    assert kw["expected_confirmation_seq"] == 6
    assert kw["confirmation_source"] == REPOINT_CONFIRMATION_SOURCE == "complete_repoint"
    assert kw["repoint_targets_json"] == NONCANONICAL  # raw passthrough, byte for byte
    assert kw["status"] == "confirmed" and kw["public_id"] == PUBLIC_ID
    assert (kw["confirmed_project_id"], kw["confirmed_sub_cost_code_id"]) == (202, 101)
    svc._enqueue_recode_and_mark.assert_called_once_with(PUBLIC_ID, "realm-1", 7)


@patch(GATE, return_value=None)
def test_fenced_out_record_is_moved_with_no_enqueue(_gate):
    # kills: enqueueing after record_confirmation returned None (the row moved under us)
    svc = _svc(_item(), recorded=None)
    with patch(OUTBOX) as outbox:
        outbox.return_value.repo.read_dead_letter_by_entity.return_value = None
        assert svc.redrive_error_repoint(PUBLIC_ID) == {"status": "moved"}
    svc._enqueue_recode_and_mark.assert_not_called()


@patch(GATE, return_value=None)
def test_lock_busy_is_busy_with_no_read(_gate):
    # kills: running the redrive without the suggestion lock
    from tests.conftest import mock_qbo_app_lock_denied
    svc = _svc(_item())
    with patch("entities.coding_suggestion.business.service.qbo_app_lock", mock_qbo_app_lock_denied):
        assert svc.redrive_error_repoint(PUBLIC_ID) == {"status": "busy"}
    svc.read_by_public_id.assert_not_called()
    _assert_no_writes(svc)


# ---- route -----------------------------------------------------------------------------------------

URL = f"/api/v1/admin/expense-coding/suggestions/{PUBLIC_ID}/redrive-repoint"


@pytest.fixture
def client():
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


def _as(user):
    app.dependency_overrides[get_current_user_api] = lambda: user


ADMIN = {"sub": "a", "is_system_admin": True}


def test_non_admin_is_403_and_service_not_called(client):
    # kills: require_system_admin() dropped from the route
    _as({"sub": "u", "is_system_admin": False})
    with patch.object(CodingSuggestionService, "redrive_error_repoint") as redrive:
        assert client.post(URL).status_code == 403
    redrive.assert_not_called()


def test_drain_secret_alone_is_not_enough(client, monkeypatch):
    # kills: the route being (mis)wired to the timer's drain secret instead of a JWT admin
    monkeypatch.setenv("DRAIN_SECRET", "s")
    with patch.object(CodingSuggestionService, "redrive_error_repoint") as redrive:
        assert client.post(URL, headers={"X-Drain-Secret": "s"}).status_code in (401, 403)
    redrive.assert_not_called()


def test_non_uuid_is_422_before_the_service(client):
    # kills: an unvalidated id reaching the lock/sproc
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "redrive_error_repoint") as redrive:
        assert client.post("/api/v1/admin/expense-coding/suggestions/not-a-uuid/redrive-repoint").status_code == 422
    redrive.assert_not_called()


def test_enqueued_is_200_data_envelope(client):
    # kills: envelope shape changed / success mapped to a non-200
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "redrive_error_repoint",
                      return_value={"status": "enqueued", "confirmation_seq": 7}) as redrive:
        r = client.post(URL)
    assert r.status_code == 200 and r.json() == {"data": {"status": "enqueued", "confirmation_seq": 7}}
    redrive.assert_called_once_with(PUBLIC_ID)


def test_not_found_is_404(client):
    # kills: not_found folded into 409
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "redrive_error_repoint", return_value={"status": "not_found"}):
        r = client.post(URL)
    assert r.status_code == 404


@pytest.mark.parametrize("status", ["not_error", "not_a_repoint", "no_repoint_targets", "dead_letter_pending",
                                    "moved", "busy", "writes_disabled"])
def test_refusals_are_409_with_status_detail(client, status):
    # kills: a refusal returned as a silent 200, or a lost status in `detail`
    _as(ADMIN)
    with patch.object(CodingSuggestionService, "redrive_error_repoint", return_value={"status": status}):
        r = client.post(URL)
    assert r.status_code == 409 and r.json()["detail"] == status
