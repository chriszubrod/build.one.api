"""U-549 Phase C2 slice C — ramp chaser config + admin digest trigger."""

import asyncio
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import shared.api.admin as admin
from app import app
from config import Settings
from entities.ramp_chaser_digest.business.digest_service import RampChaserDigestService


@pytest.fixture
def ramp_chaser_draft_sweep_mocks():
    """Minimal mocks so run_for_week can enter draft mode without live DB/Graph."""
    digest_repo = MagicMock()
    digest_repo.read_uncaptured.return_value = []
    digest_repo.read_outstanding.return_value = []

    follow_up_repo = MagicMock()
    follow_up_repo.read_unresolved.return_value = []

    outbox_repo = MagicMock()
    outbox_repo.read_completed_by_entity.return_value = []
    outbox_repo.count_by_entity_and_kind.return_value = 0
    outbox_repo.read_pending_by_entity.return_value = []

    user_service = MagicMock()
    user_service.build_roster.return_value = {}

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
            "integrations.ramp.user.business.service.RampUserService",
            return_value=user_service,
        ),
        patch("integrations.ramp.user.external.client.RampUserExternalClient"),
        patch("integrations.ms.outbox.business.service.MsOutboxService"),
        patch("integrations.ms.mail.external.client.get_message"),
    ]
    for p in patches:
        p.start()
    try:
        yield {
            "digest_repo": digest_repo,
            "follow_up_repo": follow_up_repo,
        }
    finally:
        for p in patches:
            p.stop()


# Do NOT reintroduce a local copy of the draft-mode predicate (e.g. _ramp_chaser_draft_enabled).
# A mirrored helper here already hid a real divergence: the test asserted its own gate while
# RampChaserDigestService.run_for_week normalised with .strip().lower() — the suite stayed green.


@pytest.mark.parametrize(
    "mode,expected_draft",
    [
        ("off", False),
        ("", False),
        ("DRAFT", True),
        (" DRAFT ", True),
        ("drafts", False),
        ("send", False),
        ("drat", False),
        ("draft", True),
    ],
)
def test_ramp_chaser_mode_fail_closed_only_exact_draft(
    monkeypatch,
    mode,
    expected_draft,
    request,
):
    monkeypatch.setenv("RAMP_CHASER_MODE", mode)
    if expected_draft:
        sweep_mocks = request.getfixturevalue("ramp_chaser_draft_sweep_mocks")
        result = RampChaserDigestService().run_for_week("2026-09-29")
        assert result["status"] != "disabled"
        assert result["status"] == "ok"
        assert result["mode"] == "draft"
        sweep_mocks["digest_repo"].read_uncaptured.assert_called()
    else:
        with patch(
            "entities.ramp_chaser_digest.persistence.repo.RampChaserDigestRepository"
        ) as digest_cls, patch(
            "entities.ramp_transaction_follow_up.persistence.repo.RampTransactionFollowUpRepository"
        ) as follow_cls, patch(
            "integrations.ms.outbox.business.service.MsOutboxService"
        ) as ms_outbox_cls, patch(
            "integrations.ms.mail.external.client.get_message"
        ) as get_message:
            result = RampChaserDigestService().run_for_week("2026-09-29")
        assert result["status"] == "disabled"
        digest_cls.assert_not_called()
        follow_cls.assert_not_called()
        ms_outbox_cls.assert_not_called()
        get_message.assert_not_called()


def test_ramp_chaser_cc_email_defaults_to_none():
    settings = Settings()
    assert settings.ramp_chaser_cc_email is None


def test_settings_has_no_ramp_chaser_sender_field():
    assert "ramp_chaser_sender" not in Settings.model_fields


def test_ramp_chaser_digest_admin_route_uses_drain_secret():
    module_src = open(admin.__file__, encoding="utf-8").read()
    assert (
        '@router.post("/ramp-chaser/digest", dependencies=[Depends(_require_drain_secret)])'
        in module_src
    )


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def drain_secret_configured(monkeypatch):
    monkeypatch.setenv("DRAIN_SECRET", "unit-test-drain-secret")


def test_ramp_chaser_digest_503_when_drain_secret_unconfigured(client, monkeypatch):
    monkeypatch.setenv("DRAIN_SECRET", "")
    response = client.post(
        "/api/v1/admin/ramp-chaser/digest",
        headers={"X-Drain-Secret": "anything"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Drain secret not configured on server"


def test_ramp_chaser_digest_401_on_drain_secret_mismatch(
    client, drain_secret_configured
):
    response = client.post(
        "/api/v1/admin/ramp-chaser/digest",
        headers={"X-Drain-Secret": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing X-Drain-Secret"


def test_ramp_chaser_digest_bad_week_of_returns_400(
    client, drain_secret_configured
):
    response = client.post(
        "/api/v1/admin/ramp-chaser/digest?week_of=not-a-date",
        headers={"X-Drain-Secret": "unit-test-drain-secret"},
    )
    assert response.status_code == 400
    assert "ISO date" in response.json()["detail"]


@pytest.fixture
def fake_ramp_chaser_digest_service():
    mock_svc = MagicMock()
    mock_svc.run_for_week.return_value = {"status": "disabled", "mode": "off"}

    digest_mod = types.ModuleType(
        "entities.ramp_chaser_digest.business.digest_service"
    )

    class RampChaserDigestService:
        def __init__(self):
            pass

        @staticmethod
        def canonicalize_week_of(week_of, _settings):
            return week_of

        def run_for_week(self, week_of):
            return mock_svc.run_for_week(week_of)

    digest_mod.RampChaserDigestService = RampChaserDigestService
    for name in (
        "entities",
        "entities.ramp_chaser_digest",
        "entities.ramp_chaser_digest.business",
        "entities.ramp_chaser_digest.business.digest_service",
    ):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["entities.ramp_chaser_digest.business.digest_service"] = digest_mod
    yield mock_svc
    sys.modules.pop("entities.ramp_chaser_digest.business.digest_service", None)


def test_ramp_chaser_digest_success_envelope_mocks_service(
    client, drain_secret_configured, fake_ramp_chaser_digest_service
):
    mock_svc = fake_ramp_chaser_digest_service
    response = client.post(
        "/api/v1/admin/ramp-chaser/digest?week_of=2026-09-22",
        headers={"X-Drain-Secret": "unit-test-drain-secret"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["job"] == "ramp_chaser.digest"
    assert "duration_ms" in body
    assert body["result"] == {"status": "disabled", "mode": "off"}
    mock_svc.run_for_week.assert_called_once_with("2026-09-22")


def test_ramp_chaser_digest_router_validates_week_of_before_run():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(admin.ramp_chaser_digest_router(week_of="2026-99-99"))
    assert exc_info.value.status_code == 400


def test_ramp_chaser_admin_canonicalizes_monday_week_of(
    client, drain_secret_configured,
):
    from entities.ramp_chaser_digest.business.digest_service import (
        RampChaserDigestService,
    )

    with patch.object(
        RampChaserDigestService,
        "run_for_week",
        return_value={"status": "disabled", "mode": "off"},
    ) as run_mock:
        response = client.post(
            "/api/v1/admin/ramp-chaser/digest?week_of=2026-09-28",
            headers={"X-Drain-Secret": "unit-test-drain-secret"},
        )
    assert response.status_code == 200
    run_mock.assert_called_once_with("2026-09-29")
