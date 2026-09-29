"""U-570 — warn when a Ramp chaser digest recipient fingerprint changes."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from entities.ramp_chaser_digest.business.body import render_digest
from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService
from entities.ramp_chaser_digest.business.model import RampChaserDigest
from entities.ramp_transaction_follow_up.business.model import RampTransactionFollowUp
from integrations.ramp.user.business.service import RampUserRosterEntry
from shared.encryption import blind_index

_CHI = ZoneInfo("America/Chicago")
_WEEK = "2026-09-29"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture
def stable_encryption_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "dGVzdC1rZXktZm9yLWU1NzAtdGVzdHM=")


def _follow_up(**kwargs):
    base = dict(
        id=1,
        public_id="f-up-1",
        row_version=None,
        ramp_transaction_id="tx-1",
        card_holder_ramp_user_id="user-a",
        card_holder_name="Emison Cordova",
        merchant_name="Store",
        amount=Decimal("10.00"),
        transaction_date="2026-09-20",
        needs_memo=True,
        needs_receipt=False,
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
    base.update(kwargs)
    return RampTransactionFollowUp(**base)


def _digest(**kwargs):
    base = dict(
        id=1,
        public_id="digest-pub-1",
        row_version=None,
        card_holder_ramp_user_id="user-a",
        week_of=_WEEK,
        draft_message_id=None,
        conversation_id=None,
        internet_message_id=None,
        last_drafted_at=None,
        last_notified_at=None,
        notify_count=0,
        outcome=None,
        recipient_hash=None,
        created_at=None,
        updated_at=None,
    )
    base.update(kwargs)
    return RampChaserDigest(**base)


@pytest.fixture
def draft_mode(monkeypatch):
    monkeypatch.setenv("RAMP_CHASER_MODE", "draft")


@pytest.fixture
def sweep_mocks(stable_encryption_key):
    digest_repo = MagicMock()
    digest_repo.read_uncaptured.return_value = []
    digest_repo.read_outstanding.return_value = []
    digest_repo.read_latest_recipient_hash.return_value = None
    digest_repo.read_by_card_holder_and_week.return_value = None
    digest_repo.upsert.side_effect = lambda **kw: _digest(
        card_holder_ramp_user_id=kw["card_holder_ramp_user_id"],
        week_of=kw["week_of"],
        recipient_hash=kw.get("recipient_hash"),
    )

    follow_up_repo = MagicMock()
    follow_up_repo.read_unresolved.return_value = [_follow_up()]

    outbox_repo = MagicMock()
    outbox_repo.read_completed_by_entity.return_value = []
    outbox_repo.count_by_entity_and_kind.return_value = 0
    outbox_repo.read_pending_by_entity.return_value = []

    ms_outbox_svc = MagicMock()
    ms_outbox_svc.enqueue_send_mail.return_value = SimpleNamespace(public_id="ob-1")

    patches = [
        patch(
            "entities.ramp_chaser_digest.persistence.repo.RampChaserDigestRepository",
            return_value=digest_repo,
        ),
        patch(
            "entities.ramp_transaction_follow_up.persistence.repo.RampTransactionFollowUpRepository",
            return_value=follow_up_repo,
        ),
        patch(
            "integrations.ms.outbox.persistence.repo.MsOutboxRepository",
            return_value=outbox_repo,
        ),
        patch(
            "integrations.ms.outbox.business.service.MsOutboxService",
            return_value=ms_outbox_svc,
        ),
        patch(
            "integrations.ramp.user.external.client.RampUserExternalClient",
        ),
    ]
    started = [p.start() for p in patches]
    try:
        yield SimpleNamespace(
            digest_repo=digest_repo,
            follow_up_repo=follow_up_repo,
            ms_outbox_svc=ms_outbox_svc,
        )
    finally:
        for p in started:
            p.stop()


def _roster(email: str):
    return {
        "user-a": RampUserRosterEntry(
            ramp_user_id="user-a",
            email=email,
            status="USER_ACTIVE",
            is_active=True,
        ),
    }


def test_changed_address_warns_in_body_and_summary(sweep_mocks, draft_mode, stable_encryption_key):
    old_email = "pat@company.com"
    new_email = "attacker@evil.com"
    previous_hash = blind_index(old_email.strip().lower())
    current_hash = blind_index(new_email.strip().lower())
    assert previous_hash != current_hash

    sweep_mocks.digest_repo.read_latest_recipient_hash.return_value = previous_hash

    with patch(
        "integrations.ramp.user.business.service.RampUserService"
    ) as user_cls:
        user_svc = MagicMock()
        user_svc.build_roster.return_value = _roster(new_email)
        user_cls.return_value = user_svc

        with patch(
            "entities.ramp_chaser_digest.business.body.render_digest",
            wraps=render_digest,
        ) as render_mock:
            result = RampChaserDigestService().run_for_week(_WEEK)

    assert result["recipient_changed"] == 1
    assert result["drafted"] == 1
    render_mock.assert_called_once()
    assert render_mock.call_args.kwargs["recipient_changed"] is True
    body = sweep_mocks.ms_outbox_svc.enqueue_send_mail.call_args.kwargs["body"]
    greeting_idx = body.index("Emison,")
    warning_idx = body.index("WARNING")
    assert warning_idx < greeting_idx


def test_stable_gmail_address_does_not_warn(sweep_mocks, draft_mode, stable_encryption_key):
    gmail = "emison@gmail.com"
    digest_hash = blind_index(gmail.strip().lower())
    sweep_mocks.digest_repo.read_latest_recipient_hash.return_value = digest_hash

    with patch(
        "integrations.ramp.user.business.service.RampUserService"
    ) as user_cls:
        user_svc = MagicMock()
        user_svc.build_roster.return_value = _roster(gmail)
        user_cls.return_value = user_svc

        with patch(
            "entities.ramp_chaser_digest.business.body.render_digest",
            wraps=render_digest,
        ) as render_mock:
            result = RampChaserDigestService().run_for_week(_WEEK)

    assert result["recipient_changed"] == 0
    render_mock.assert_called_once()
    assert render_mock.call_args.kwargs["recipient_changed"] is False
    body = sweep_mocks.ms_outbox_svc.enqueue_send_mail.call_args.kwargs["body"]
    assert "WARNING" not in body


def test_first_sight_does_not_warn_but_stamps_hash(sweep_mocks, draft_mode, stable_encryption_key):
    email = "new.hire@example.com"
    sweep_mocks.digest_repo.read_latest_recipient_hash.return_value = None

    with patch(
        "integrations.ramp.user.business.service.RampUserService"
    ) as user_cls:
        user_svc = MagicMock()
        user_svc.build_roster.return_value = _roster(email)
        user_cls.return_value = user_svc

        with patch(
            "entities.ramp_chaser_digest.business.body.render_digest",
            wraps=render_digest,
        ) as render_mock:
            result = RampChaserDigestService().run_for_week(_WEEK)

    assert result["recipient_changed"] == 0
    render_mock.assert_called_once()
    assert render_mock.call_args.kwargs["recipient_changed"] is False
    expected = blind_index(email.strip().lower())
    sweep_mocks.digest_repo.upsert.assert_called_once()
    assert sweep_mocks.digest_repo.upsert.call_args.kwargs["recipient_hash"] == expected


def test_case_and_whitespace_are_not_a_change(sweep_mocks, draft_mode, stable_encryption_key):
    canonical = "pat@example.com"
    stored = blind_index(canonical)
    sweep_mocks.digest_repo.read_latest_recipient_hash.return_value = stored

    with patch(
        "integrations.ramp.user.business.service.RampUserService"
    ) as user_cls:
        user_svc = MagicMock()
        user_svc.build_roster.return_value = _roster("  Pat@Example.com  ")
        user_cls.return_value = user_svc

        with patch(
            "entities.ramp_chaser_digest.business.body.render_digest",
            wraps=render_digest,
        ) as render_mock:
            result = RampChaserDigestService().run_for_week(_WEEK)

    assert result["recipient_changed"] == 0
    assert render_mock.call_args.kwargs["recipient_changed"] is False


def test_raw_email_never_reaches_the_digest_row_or_the_logs(sweep_mocks, draft_mode, stable_encryption_key, caplog):
    """The address reaches neither dbo.RampChaserDigest nor any log line.

    ⛔ Scoped deliberately, because a broader name would be a lie. The raw address
    DOES land in `ms.Outbox.Payload` as `to_addresses`, and that is by design and
    predates this unit: the outbox worker drains asynchronously and cannot send
    without knowing the recipient. Every send_mail row in the system carries it,
    including the Bill review notifications that shipped long before Ramp.

    What the C2 Gate-2 decision actually forbade is a stored copy on the ENTITY —
    `CardHolderEmail` on dbo.RampTransactionFollowUp — because that one would
    drift out of sync with Ramp's roster. A transient work item is not that.
    """
    secret = "hidden.recipient@personal.net"
    sweep_mocks.digest_repo.read_latest_recipient_hash.return_value = blind_index("old@example.com")

    with patch(
        "integrations.ramp.user.business.service.RampUserService"
    ) as user_cls:
        user_svc = MagicMock()
        user_svc.build_roster.return_value = _roster(secret)
        user_cls.return_value = user_svc

        with caplog.at_level("WARNING"):
            RampChaserDigestService().run_for_week(_WEEK)

    upsert_kw = sweep_mocks.digest_repo.upsert.call_args.kwargs
    assert _HEX64.match(upsert_kw["recipient_hash"])
    for value in upsert_kw.values():
        assert value != secret
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_warning_renders_above_greeting_by_index(stable_encryption_key):
    now = datetime(2026, 9, 28, 15, 0, 0, tzinfo=_CHI)
    _, body = render_digest(
        first_name="Pat",
        card_holder="Pat Cardholder",
        items=[],
        now=now,
        tz=_CHI,
        recipient_changed=True,
    )
    assert body.index("WARNING") < body.index("Pat,")
