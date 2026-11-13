"""U-792: per-project switches for the Application for Payment's optional blocks.

Pure-logic / no-live-DB: defaults, tri-state resolution, the service's access gate
and concurrency contract, the routes, and the sproc text."""

import base64
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from entities.project_draw_packet_setting.api import router as settings_router
from entities.project_draw_packet_setting.api.schemas import DrawPacketSettingUpdate
from entities.project_draw_packet_setting.business import service as settings_service
from entities.project_draw_packet_setting.business.model import (
    DrawPacketBlocks,
    ProjectDrawPacketSetting,
)
from entities.project_draw_packet_setting.business.service import ProjectDrawPacketSettingService
from shared.api.errors import ApiError
from shared.database import RowVersionConflictError

ROOT = Path(__file__).resolve().parents[1]
TOKEN = base64.b64encode(b"\x00\x00\x00\x00\x00\x00\x07\xd1").decode()


def _setting(**switches):
    return ProjectDrawPacketSetting(id=1, public_id="p", row_version=TOKEN,
                                    created_datetime=None, modified_datetime=None,
                                    project_id=203, **switches)


# ---- defaults + resolution ------------------------------------------------------

def test_house_defaults():
    assert DrawPacketBlocks() == DrawPacketBlocks(contractor_statement=True, owner_approval=True,
                                                  architect_certificate=False, notary=True)


def test_null_switches_follow_the_defaults():
    assert _setting().blocks() == DrawPacketBlocks()


def test_a_stored_false_beats_a_true_default_and_true_beats_false():
    blocks = _setting(show_contractor_statement=False, show_owner_approval=False,
                      show_architect_certificate=True, show_notary=False).blocks()
    assert blocks == DrawPacketBlocks(contractor_statement=False, owner_approval=False,
                                      architect_certificate=True, notary=False)


# ---- service ----------------------------------------------------------------------

class _Repo:
    def __init__(self, row=None, upsert_result="echo"):
        self.row, self.upsert_result, self.calls = row, upsert_result, []

    def read_by_project_id(self, project_id):
        return self.row

    def upsert(self, **kw):
        self.calls.append(kw)
        return _setting() if self.upsert_result == "echo" else self.upsert_result


@pytest.fixture
def gate(monkeypatch):
    seen = []
    monkeypatch.setattr(settings_service, "assert_can_access_project", seen.append)
    return seen


def test_blocks_for_a_project_without_a_row_are_the_defaults(gate):
    assert ProjectDrawPacketSettingService(_Repo(None)).blocks_for_project(203) == DrawPacketBlocks()
    assert gate == [203]


def test_every_path_checks_project_access(monkeypatch):
    def deny(project_id):
        raise PermissionError(project_id)

    monkeypatch.setattr(settings_service, "assert_can_access_project", deny)
    repo = _Repo(_setting())
    svc = ProjectDrawPacketSettingService(repo)
    for call in (lambda: svc.read_by_project_id(203), lambda: svc.blocks_for_project(203),
                 lambda: svc.upsert(203, row_version=None)):
        with pytest.raises(PermissionError):
            call()
    assert repo.calls == []   # nothing written past a denied gate


def test_upsert_passes_the_decoded_token_and_every_switch(gate):
    repo = _Repo()
    ProjectDrawPacketSettingService(repo).upsert(
        203, row_version=TOKEN, show_contractor_statement=False, show_architect_certificate=True)
    call = repo.calls[0]
    assert call["row_version_bytes"] == base64.b64decode(TOKEN)
    assert call["show_contractor_statement"] is False
    assert call["show_architect_certificate"] is True
    assert call["show_owner_approval"] is None and call["show_notary"] is None  # full replace


def test_first_save_carries_no_token(gate):
    repo = _Repo()
    ProjectDrawPacketSettingService(repo).upsert(203, row_version=None)
    assert repo.calls[0]["row_version_bytes"] is None


def test_nothing_written_is_a_row_version_conflict(gate):
    with pytest.raises(RowVersionConflictError):
        ProjectDrawPacketSettingService(_Repo(upsert_result=None)).upsert(203, row_version=TOKEN)


@pytest.mark.parametrize("bad", ["not base64!!", base64.b64encode(b"short").decode()])
def test_a_malformed_token_is_rejected_before_the_database(gate, bad):
    repo = _Repo()
    with pytest.raises(ValueError):
        ProjectDrawPacketSettingService(repo).upsert(203, row_version=bad)
    assert repo.calls == []


# ---- schema + routes ----------------------------------------------------------------

def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        DrawPacketSettingUpdate(show_notary=True, show_signature=True)


_PROJECT = SimpleNamespace(id=203, public_id="20bf258b-1c5a-4ac7-af8b-8198a32d3e95")


def _projects(monkeypatch, project):
    class _Projects:
        def read_by_public_id(self, public_id):
            return project

    monkeypatch.setattr(settings_router, "ProjectService", _Projects)


def test_get_without_a_row_reports_defaults(monkeypatch):
    _projects(monkeypatch, _PROJECT)
    monkeypatch.setattr(settings_router.service, "read_by_project_id", lambda pid: None)
    data = settings_router.get_draw_packet_settings_router(_PROJECT.public_id, current_user={})["data"]
    assert data["row_version"] is None
    assert set(data["stored"].values()) == {None}
    assert data["effective"] == DrawPacketBlocks().to_dict()


def test_get_an_inaccessible_or_missing_project_is_404(monkeypatch):
    _projects(monkeypatch, None)
    with pytest.raises(ApiError) as exc:
        settings_router.get_draw_packet_settings_router(_PROJECT.public_id, current_user={})
    assert exc.value.status_code == 404


def test_put_conflict_is_409(monkeypatch):
    _projects(monkeypatch, _PROJECT)

    def conflict(*a, **kw):
        raise RowVersionConflictError("stale")

    monkeypatch.setattr(settings_router.service, "upsert", conflict)
    with pytest.raises(ApiError) as exc:
        settings_router.update_draw_packet_settings_router(
            _PROJECT.public_id, DrawPacketSettingUpdate(row_version=TOKEN), current_user={})
    assert exc.value.status_code == 409


def test_put_bad_token_is_400(monkeypatch):
    _projects(monkeypatch, _PROJECT)

    def bad(*a, **kw):
        raise ValueError("row_version is not valid base64")

    monkeypatch.setattr(settings_router.service, "upsert", bad)
    with pytest.raises(ApiError) as exc:
        settings_router.update_draw_packet_settings_router(
            _PROJECT.public_id, DrawPacketSettingUpdate(row_version="x"), current_user={})
    assert exc.value.status_code == 400


def test_put_returns_the_saved_switches(monkeypatch):
    _projects(monkeypatch, _PROJECT)
    monkeypatch.setattr(settings_router.service, "upsert",
                        lambda pid, **kw: _setting(show_architect_certificate=True))
    data = settings_router.update_draw_packet_settings_router(
        _PROJECT.public_id, DrawPacketSettingUpdate(show_architect_certificate=True),
        current_user={})["data"]
    assert data["row_version"] == TOKEN
    assert data["stored"]["show_architect_certificate"] is True
    assert data["effective"]["architect_certificate"] is True


def test_routes_are_registered_and_gated():
    import app as app_module

    paths = {r.path for r in app_module.app.routes}
    assert "/api/v1/get/project/{public_id}/draw-packet-settings" in paths
    assert "/api/v1/update/project/{public_id}/draw-packet-settings" in paths
    for route in settings_router.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert deps, f"{route.path} has no RBAC dependency"


# ---- sproc text ---------------------------------------------------------------------

SQL = (ROOT / "entities/project_draw_packet_setting/sql/dbo.project_draw_packet_setting.sql").read_text()


def test_upsert_never_rolls_back_and_always_returns_a_result_set():
    body = SQL[SQL.index("CREATE OR ALTER PROCEDURE UpsertProjectDrawPacketSetting"):]
    assert "ROLLBACK" not in body.upper()
    assert "SET NOCOUNT ON" in body
    # the trailing SELECT runs on every path, filtered by @Written
    assert re.search(r"COMMIT TRANSACTION;\s*SELECT", body)
    assert "AND @Written = 1" in body


def test_project_id_is_unique_and_the_existence_check_locks():
    assert "CREATE UNIQUE INDEX [UX_ProjectDrawPacketSetting_ProjectId]" in SQL
    assert "WITH (UPDLOCK, HOLDLOCK)" in SQL


def test_every_batch_is_go_terminated():
    assert SQL.rstrip().endswith("GO")
    assert SQL.count("CREATE OR ALTER PROCEDURE") == 2
