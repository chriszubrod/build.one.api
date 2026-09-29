"""U-549 Phase C2 — RampChaserDigestService sweep (mocked Graph + DB)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService
from entities.ramp_chaser_digest.business.model import RampChaserDigest
from entities.ramp_transaction_follow_up.business.model import RampTransactionFollowUp
from integrations.ramp.user.business.service import RampUserRosterEntry
from shared.encryption import blind_index

_CHI = ZoneInfo("America/Chicago")
_ENTITY_TYPE = "RampChaserDigest"


def _follow_up(
    *,
    card_holder_ramp_user_id="user-a",
    card_holder_name="Alex Active",
    needs_memo=True,
    needs_receipt=False,
    merchant="Store",
):
    return RampTransactionFollowUp(
        id=1,
        public_id="f-up-1",
        row_version=None,
        ramp_transaction_id="tx-1",
        card_holder_ramp_user_id=card_holder_ramp_user_id,
        card_holder_name=card_holder_name,
        merchant_name=merchant,
        amount=Decimal("10.00"),
        transaction_date="2026-09-20",
        needs_memo=needs_memo,
        needs_receipt=needs_receipt,
        first_seen_at="2026-09-20T12:00:00+00:00",
        last_drafted_at=None,
        draft_message_id=None,
        last_notified_at=None,
        notify_count=0,
        escalated_at=None,
        resolved_at=None,
        created_at=None,
        updated_at=None,
    )


def _digest_row(
    *,
    public_id="digest-pub-1",
    card_holder_ramp_user_id="user-a",
    week_of="2026-09-29",
    draft_message_id=None,
    conversation_id=None,
):
    return RampChaserDigest(
        id=1,
        public_id=public_id,
        row_version=None,
        card_holder_ramp_user_id=card_holder_ramp_user_id,
        week_of=week_of,
        draft_message_id=draft_message_id,
        conversation_id=conversation_id,
        internet_message_id=None,
        last_drafted_at=None,
        last_notified_at=None,
        notify_count=0,
        outcome=None,
        recipient_hash=None,
        created_at=None,
        updated_at=None,
    )


@pytest.fixture
def draft_mode(monkeypatch):
    monkeypatch.setenv("RAMP_CHASER_MODE", "draft")


@pytest.fixture
def mocks():
    digest_repo = MagicMock()
    digest_repo.read_uncaptured.return_value = []
    digest_repo.read_outstanding.return_value = []
    digest_repo.read_latest_recipient_hash.return_value = None
    digest_repo.upsert.side_effect = lambda **kw: _digest_row(
        card_holder_ramp_user_id=kw["card_holder_ramp_user_id"],
        week_of=kw["week_of"],
    )
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
        "user-b": RampUserRosterEntry(
            ramp_user_id="user-b",
            email="bob@example.com",
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
            "entities.ramp_transaction_follow_up.persistence.repo.RampTransactionFollowUpRepository",
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
        "enqueue_update_draft": patch(
            "integrations.ms.outbox.business.service.MsOutboxService.enqueue_update_draft",
        ),
        "update_draft": patch("integrations.ms.mail.external.client.update_draft"),
    }

    started = {k: p.start() for k, p in patches.items()}
    yield SimpleNamespace(
        digest_repo=digest_repo,
        follow_up_repo=follow_up_repo,
        outbox_repo=outbox_repo,
        user_service=user_service,
        ms_outbox_svc=ms_outbox_svc,
        enqueue_update_draft=started["enqueue_update_draft"],
        update_draft=started["update_draft"],
    )
    for p in patches.values():
        p.stop()


def test_mode_off_does_nothing(monkeypatch):
    monkeypatch.setenv("RAMP_CHASER_MODE", "off")
    with patch(
        "entities.ramp_chaser_digest.persistence.repo.RampChaserDigestRepository"
    ) as digest_cls, patch(
        "entities.ramp_transaction_follow_up.persistence.repo.RampTransactionFollowUpRepository"
    ) as follow_cls, patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_cls, patch(
        "integrations.ms.mail.external.client.get_message"
    ) as get_msg:
        result = RampChaserDigestService().run_for_week()
    assert result["status"] == "disabled"
    digest_cls.assert_not_called()
    follow_cls.assert_not_called()
    ms_cls.assert_not_called()
    get_msg.assert_not_called()


def test_week_of_tuesday_in_business_timezone_monday_late(mocks, draft_mode):
    monday_late = datetime(2026, 9, 28, 23, 0, 0, tzinfo=_CHI)
    with patch(
        "entities.ramp_chaser_digest.business.digest_service.datetime"
    ) as dt_mod:
        dt_mod.now.return_value = monday_late
        dt_mod.side_effect = lambda *a, **k: datetime(*a, **k)
        result = RampChaserDigestService().run_for_week()
    assert result["week_of"] == "2026-09-29"


def test_neither_flag_excluded_from_digest(mocks, draft_mode):
    mocks.follow_up_repo.read_unresolved.return_value = [
        _follow_up(needs_memo=False, needs_receipt=False),
        _follow_up(card_holder_ramp_user_id="user-b", needs_memo=True, needs_receipt=False),
    ]
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["cardholders_total"] == 1
    assert result["drafted"] == 1
    mocks.ms_outbox_svc.enqueue_send_mail.assert_called_once()


def test_roster_fetched_once_for_multiple_cardholders(mocks, draft_mode):
    mocks.follow_up_repo.read_unresolved.return_value = [
        _follow_up(card_holder_ramp_user_id="user-a"),
        _follow_up(card_holder_ramp_user_id="user-b", needs_memo=False, needs_receipt=True),
    ]
    RampChaserDigestService().run_for_week("2026-09-29")
    mocks.user_service.build_roster.assert_called_once()


def test_inactive_cardholder_skipped(mocks, draft_mode):
    mocks.user_service.build_roster.return_value = {
        "user-a": RampUserRosterEntry(
            ramp_user_id="user-a",
            email="alex@example.com",
            status="inactive",
            is_active=False,
        ),
    }
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["skipped_inactive"] == 1
    assert result["drafted"] == 0
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()


def test_missing_roster_unroutable(mocks, draft_mode):
    mocks.user_service.build_roster.return_value = {}
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["unroutable"] == 1
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()


def test_no_email_unroutable(mocks, draft_mode):
    mocks.user_service.build_roster.return_value = {
        "user-a": RampUserRosterEntry(
            ramp_user_id="user-a",
            email=None,
            status="USER_ACTIVE",
            is_active=True,
        ),
    }
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["unroutable"] == 1


def test_no_enqueue_update_draft_or_patch(mocks, draft_mode):
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    RampChaserDigestService().run_for_week("2026-09-29")
    mocks.enqueue_update_draft.assert_not_called()
    mocks.update_draft.assert_not_called()


def test_outstanding_draft_still_open_not_patched(mocks, draft_mode):
    outstanding = _digest_row(
        week_of="2026-09-29",
        draft_message_id="draft-1",
        conversation_id="conv-1",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]
    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={
            "status_code": 200,
            "email": {"is_draft": True, "message_id": "draft-1"},
        },
    ), patch("integrations.ms.mail.external.client.list_messages") as list_msg:
        result = RampChaserDigestService().run_for_week("2026-09-29")
    list_msg.assert_not_called()
    mocks.digest_repo.stamp_notified.assert_not_called()
    mocks.digest_repo.stamp_outcome.assert_not_called()
    assert result["unsent_carryover"] == 0


def test_previous_week_open_draft_counts_unsent_carryover(mocks, draft_mode):
    outstanding = _digest_row(
        week_of="2026-09-22",
        draft_message_id="draft-old",
        conversation_id="conv-old",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]
    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={
            "status_code": 200,
            "email": {"is_draft": True, "message_id": "draft-old"},
        },
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["unsent_carryover"] == 1
    mocks.digest_repo.stamp_outcome.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-22",
        outcome="unsent_carryover",
    )


def test_vanished_id_in_sentitems_stamps_notified(mocks, draft_mode):
    outstanding = _digest_row(
        week_of="2026-09-22",
        draft_message_id="gone",
        conversation_id="conv-sent",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]

    def list_side_effect(folder, **kwargs):
        if folder == "sentitems":
            return {"status_code": 200, "messages": [{"message_id": "sent-1"}]}
        return {"status_code": 200, "messages": []}

    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={"status_code": 404, "email": None},
    ), patch(
        "integrations.ms.mail.external.client.list_messages",
        side_effect=list_side_effect,
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["sent_observed"] == 1
    mocks.digest_repo.stamp_notified.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-22",
        outcome="sent",
    )


def test_vanished_id_in_deleteditems_does_not_stamp_notified(mocks, draft_mode):
    outstanding = _digest_row(
        week_of="2026-09-22",
        draft_message_id="gone",
        conversation_id="conv-del",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]

    def list_side_effect(folder, **kwargs):
        if folder == "deleteditems":
            return {"status_code": 200, "messages": [{"message_id": "del-1"}]}
        return {"status_code": 200, "messages": []}

    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={"status_code": 404, "email": None},
    ), patch(
        "integrations.ms.mail.external.client.list_messages",
        side_effect=list_side_effect,
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["discarded_unsent"] == 1
    mocks.digest_repo.stamp_notified.assert_not_called()
    mocks.digest_repo.stamp_outcome.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-22",
        outcome="discarded_unsent",
    )


def test_vanished_id_neither_folder_stamps_nothing(mocks, draft_mode):
    outstanding = _digest_row(
        week_of="2026-09-22",
        draft_message_id="gone",
        conversation_id="conv-unk",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]
    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={"status_code": 404, "email": None},
    ), patch(
        "integrations.ms.mail.external.client.list_messages",
        return_value={"status_code": 200, "messages": []},
    ):
        RampChaserDigestService().run_for_week("2026-09-29")
    mocks.digest_repo.stamp_notified.assert_not_called()
    mocks.digest_repo.stamp_outcome.assert_not_called()


def test_transient_get_does_not_stamp(mocks, draft_mode):
    outstanding = _digest_row(
        draft_message_id="draft-1",
        conversation_id="conv-1",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]
    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={"status_code": 503, "email": None, "is_retryable": True},
    ):
        RampChaserDigestService().run_for_week("2026-09-29")
    mocks.digest_repo.stamp_notified.assert_not_called()
    mocks.digest_repo.stamp_outcome.assert_not_called()


def test_capture_draft_id_from_outbox_payload(mocks, draft_mode):
    digest = _digest_row(public_id="digest-capture")
    mocks.digest_repo.upsert.return_value = digest
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]

    outbox_row = SimpleNamespace(
        payload=json.dumps({"graph_message_id": "graph-draft-99"}),
    )
    mocks.outbox_repo.read_completed_by_entity.return_value = [outbox_row]

    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={
            "status_code": 200,
            "email": {
                "conversation_id": "conv-new",
                "internet_message_id": "imid-new",
            },
        },
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")

    mocks.digest_repo.stamp_drafted.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-29",
        draft_message_id="graph-draft-99",
        conversation_id="conv-new",
        internet_message_id="imid-new",
    )
    assert result["already_drafted"] == 1
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()


def test_enqueue_none_counted_refused(mocks, draft_mode):
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    mocks.ms_outbox_svc.enqueue_send_mail.return_value = None
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["refused_ms_writes_gate"] == 1
    assert result["drafted"] == 0


def test_second_run_same_week_already_drafted(mocks, draft_mode):
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    drafted = _digest_row(draft_message_id="existing-draft")
    mocks.digest_repo.upsert.side_effect = None
    mocks.digest_repo.upsert.return_value = drafted
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["already_drafted"] == 1
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()


def test_one_cardholder_failure_does_not_sink_batch(mocks, draft_mode):
    mocks.follow_up_repo.read_unresolved.return_value = [
        _follow_up(card_holder_ramp_user_id="user-a"),
        _follow_up(card_holder_ramp_user_id="user-b", needs_memo=False, needs_receipt=True),
    ]

    def upsert_side_effect(**kw):
        if kw["card_holder_ramp_user_id"] == "user-a":
            raise RuntimeError("boom")
        return _digest_row(
            card_holder_ramp_user_id=kw["card_holder_ramp_user_id"],
            week_of=kw["week_of"],
        )

    mocks.digest_repo.upsert.side_effect = upsert_side_effect
    result = RampChaserDigestService().run_for_week("2026-09-29")
    assert result["failed"] == 1
    assert result["drafted"] == 1
    mocks.ms_outbox_svc.enqueue_send_mail.assert_called_once()


# The fail-closed mode gate is pinned ONCE, in tests/test_u549_c2_config_admin.py
# (test_ramp_chaser_mode_fail_closed_only_exact_draft) — it covers one more mode
# string and asserts strictly more: mode == "draft", read_uncaptured called, and
# that NOTHING at all is constructed for every inert mode. A second
# parametrisation here duplicated it. Do not re-add one, and do not factor the
# gate into a shared test helper: a helper that re-states the gate is the mirror
# that already hid a real divergence between two slices of this unit.


def test_uncaptured_digest_captured_with_zero_open_items(mocks, draft_mode):
    """P0-a: reconcile by digest row, not this week's cardholder groups."""
    uncaptured = _digest_row(
        public_id="digest-stranded",
        week_of="2026-09-29",
        draft_message_id=None,
    )
    mocks.digest_repo.read_uncaptured.return_value = [uncaptured]
    mocks.follow_up_repo.read_unresolved.return_value = []

    outbox_row = SimpleNamespace(
        payload=json.dumps({"graph_message_id": "graph-stranded"}),
    )
    mocks.outbox_repo.read_completed_by_entity.return_value = [outbox_row]
    mocks.outbox_repo.count_by_entity_and_kind.return_value = 1

    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={
            "status_code": 200,
            "email": {
                "conversation_id": "conv-stranded",
                "internet_message_id": "imid-stranded",
            },
        },
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")

    mocks.digest_repo.stamp_drafted.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-29",
        draft_message_id="graph-stranded",
        conversation_id="conv-stranded",
        internet_message_id="imid-stranded",
    )
    assert result["cardholders_total"] == 0
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()


def test_observe_send_with_zero_open_items(mocks, draft_mode):
    outstanding = _digest_row(
        week_of="2026-09-29",
        draft_message_id="draft-sent",
        conversation_id="conv-sent",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]
    mocks.follow_up_repo.read_unresolved.return_value = []

    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={"status_code": 404, "email": None},
    ), patch(
        "integrations.ms.mail.external.client.list_messages",
        return_value={"status_code": 200, "messages": [{"message_id": "m1"}]},
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")

    assert result["sent_observed"] == 1
    assert result["cardholders_total"] == 0
    mocks.digest_repo.stamp_notified.assert_called_once()


def test_rerun_before_drain_does_not_double_enqueue(mocks, draft_mode):
    """P0-b: pending outbox row means already enqueued — no second send_mail."""
    digest = _digest_row(public_id="digest-pending")
    mocks.digest_repo.upsert.return_value = digest
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]
    mocks.outbox_repo.read_completed_by_entity.return_value = []
    mocks.outbox_repo.count_by_entity_and_kind.return_value = 1
    mocks.outbox_repo.read_pending_by_entity.return_value = [
        SimpleNamespace(public_id="ob-pending"),
    ]

    result = RampChaserDigestService().run_for_week("2026-09-29")

    assert result["already_drafted"] == 1
    assert result["drafted"] == 0
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()


def test_monday_week_of_canonicalizes_to_tuesday(mocks, draft_mode):
    """P0-c: Monday ?week_of collapses to the same Tuesday anchor."""
    mocks.follow_up_repo.read_unresolved.return_value = [_follow_up()]

    RampChaserDigestService().run_for_week("2026-09-28")

    mocks.digest_repo.upsert.assert_called_once_with(
        card_holder_ramp_user_id="user-a",
        week_of="2026-09-29",
        recipient_hash=blind_index("alex@example.com"),
    )


def test_stamp_notified_sql_requires_null_last_notified():
    """P1-a: notified stamp is idempotent (SQL guard)."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "entities/ramp_chaser_digest/sql/dbo.ramp_chaser_digest.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE StampRampChaserDigestNotified" in sql
    idx = sql.index("CREATE OR ALTER PROCEDURE StampRampChaserDigestNotified")
    body = sql[idx : idx + 2500]
    assert "[LastNotifiedAt] IS NULL" in body


def test_sentitems_429_does_not_stamp_even_if_deleted_would_hit(mocks, draft_mode):
    """P1-b: transient folder read must not drive any outcome stamp."""
    outstanding = _digest_row(
        week_of="2026-09-22",
        draft_message_id="gone",
        conversation_id="conv-429",
    )
    mocks.digest_repo.read_outstanding.return_value = [outstanding]

    def list_side_effect(folder, **kwargs):
        if folder == "sentitems":
            return {"status_code": 429, "messages": []}
        if folder == "deleteditems":
            return {"status_code": 200, "messages": [{"message_id": "del-1"}]}
        return {"status_code": 200, "messages": []}

    with patch(
        "integrations.ms.mail.external.client.get_message",
        return_value={"status_code": 404, "email": None},
    ), patch(
        "integrations.ms.mail.external.client.list_messages",
        side_effect=list_side_effect,
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")

    assert result["discarded_unsent"] == 0
    assert result["sent_observed"] == 0
    mocks.digest_repo.stamp_notified.assert_not_called()
    mocks.digest_repo.stamp_outcome.assert_not_called()


def test_outer_exception_returns_error_summary_with_failed_one(mocks, draft_mode):
    """A sweep that dies OUTSIDE per-cardholder isolation must say so.

    run_for_week's own except block is the last line of defence: everything
    before the per-cardholder try/except (Settings, the roster fetch, the
    uncaptured read) lands here. `failed` is the only numeric signal that
    anything went wrong, so a summary of failed=0 would make a crashed sweep
    indistinguishable from a clean no-op — and the endpoint returns 200 either
    way. Pinned because a mutation of failed=1 -> failed=0 was GREEN.
    """
    with patch.object(
        RampChaserDigestService,
        "_run_for_week",
        side_effect=RuntimeError("roster fetch exploded"),
    ):
        result = RampChaserDigestService().run_for_week("2026-09-29")

    assert result["status"] == "error"
    assert result["failed"] == 1
    assert result["week_of"] == "2026-09-29"
    assert result["mode"] == "draft"
    # a failed sweep drafts nothing and stamps nothing
    assert result["drafted"] == 0
    assert result["sent_observed"] == 0
    mocks.ms_outbox_svc.enqueue_send_mail.assert_not_called()
    mocks.digest_repo.stamp_notified.assert_not_called()


def test_real_ramp_collaborators_construct_without_typeerror(monkeypatch, draft_mode):
    """The digest's Ramp client chain must CONSTRUCT for real, not just when mocked.

    Regression for a live P0: `_run_for_week` called `RampUserExternalClient()` with
    no arguments, but that constructor REQUIRES an http_client. Every digest run
    raised TypeError and was swallowed into `failed=1` by the outer handler.

    It survived Pass 1, Pass 2, step 4c and 7 mutation proofs because every other
    test in this file patches `RampUserService`/`RampUserExternalClient`, so the real
    constructor never ran — and `RAMP_CHASER_MODE=off` returned at the mode gate
    before reaching the construction, so no environment ever executed it either.

    This test therefore stubs at the HTTP boundary ONLY. Everything above it —
    RampAuthService, RampHttpClient, RampUserExternalClient, RampUserService — is
    built for real, which is the only way a wrong constructor signature can surface.
    """
    import integrations.ramp.base.client as ramp_client_mod

    calls = {"get": 0, "close": 0}

    def fake_get(self, path_or_url, **kwargs):
        calls["get"] += 1
        return {"data": [], "page": {}}

    def fake_close(self):
        calls["close"] += 1

    monkeypatch.setattr(ramp_client_mod.RampHttpClient, "get", fake_get)
    monkeypatch.setattr(ramp_client_mod.RampHttpClient, "close", fake_close)

    with patch(
        "entities.ramp_chaser_digest.persistence.repo.RampChaserDigestRepository"
    ) as digest_cls, patch(
        "entities.ramp_transaction_follow_up.persistence.repo.RampTransactionFollowUpRepository"
    ) as follow_cls, patch(
        "integrations.ms.outbox.persistence.repo.MsOutboxRepository"
    ):
        digest_cls.return_value.read_uncaptured.return_value = []
        digest_cls.return_value.read_outstanding.return_value = []
        follow_cls.return_value.read_unresolved.return_value = []
        result = RampChaserDigestService()._run_for_week("2026-09-29")

    assert result["status"] == "ok"
    assert result["failed"] == 0
    assert calls["get"] >= 1, "the real Ramp client chain was never exercised"
    # the sweep makes exactly one Ramp call and must not leak the connection
    assert calls["close"] == 1, "the Ramp http client was not closed"


@pytest.mark.parametrize(
    "configured,cardholder,expected",
    [
        pytest.param("", "pat@x.com", [], id="unset-no-cc"),
        pytest.param("austin@rogersbuild.com", "pat@x.com",
                     ["austin@rogersbuild.com"], id="single-unchanged"),
        pytest.param("austin@rogersbuild.com,invoice@rogersbuild.com", "pat@x.com",
                     ["austin@rogersbuild.com", "invoice@rogersbuild.com"], id="two-addresses"),
        pytest.param("  austin@rogersbuild.com ,  invoice@rogersbuild.com  ", "pat@x.com",
                     ["austin@rogersbuild.com", "invoice@rogersbuild.com"], id="whitespace-tolerated"),
        pytest.param("austin@rogersbuild.com,,invoice@rogersbuild.com,", "pat@x.com",
                     ["austin@rogersbuild.com", "invoice@rogersbuild.com"], id="empty-entries-dropped"),
        pytest.param("austin@rogersbuild.com,AUSTIN@rogersbuild.com", "pat@x.com",
                     ["austin@rogersbuild.com"], id="case-insensitive-dedupe"),
        # ⛔ the cardholder is never CC'd onto their OWN digest, in any position
        pytest.param("austin@rogersbuild.com,pat@x.com", "pat@x.com",
                     ["austin@rogersbuild.com"], id="cardholder-stripped-when-listed"),
        pytest.param("PAT@X.COM", "pat@x.com", [], id="cardholder-stripped-case-insensitively"),
        pytest.param("austin@rogersbuild.com,pat@x.com", " PAT@X.com ",
                     ["austin@rogersbuild.com"], id="cardholder-stripped-despite-whitespace"),
    ],
)
def test_resolve_cc_takes_a_comma_separated_list(configured, cardholder, expected):
    """The standing CC is a LIST — owner and invoice mailbox both stand on it.

    The cardholder must never appear: they are already the To:, and a duplicate
    reads as a mistake by the one person the message is trying to persuade.
    """
    settings = SimpleNamespace(ramp_chaser_cc_email=configured)
    got = RampChaserDigestService._resolve_cc(settings, cardholder_email=cardholder)
    assert [e["email"] for e in got] == expected
    assert all(e.get("name") for e in got), "every CC entry needs a display name"
