"""U-549 Phase C2 slice C — ramp chaser config + admin digest trigger."""

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import shared.api.admin as admin
from app import app
from config import Settings


def _ramp_chaser_draft_enabled(mode: str | None) -> bool:
    """Fail-closed draft gate — must match RampChaserDigestService.run_for_week.

    Unlike the time-entry digest, only the exact string ``draft`` enables the
    sweep (no ``.lower()`` — ``DRAFT`` is treated as off).
    """
    return (mode or "off") == "draft"


@pytest.mark.parametrize(
    "mode,expected_draft",
    [
        ("off", False),
        ("", False),
        ("DRAFT", False),
        ("drafts", False),
        ("send", False),
        ("draf", False),
        ("draft", True),
    ],
)
def test_ramp_chaser_mode_fail_closed_only_exact_draft(mode, expected_draft):
    assert _ramp_chaser_draft_enabled(mode) is expected_draft


def test_ramp_chaser_mode_fail_closed_from_settings(monkeypatch):
    monkeypatch.setenv("RAMP_CHASER_MODE", "DRAFT")
    settings = Settings()
    assert _ramp_chaser_draft_enabled(settings.ramp_chaser_mode) is False


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
