"""U-535b b1: scripts/attachment_backfill_candidates.py (spy service, no DB / Box / DI)."""
from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from entities.attachment_backfill.business.matcher import BillRow, InventoryFile
from entities.attachment_backfill.business.project_map import ProjectRef

PROJECTS = [ProjectRef(id=10, name="ABC1 - 1200 Elm Street")]
INVENTORY = [InventoryFile("a", "ABC1 - Acme - 42 - misc - $5.00 - 1-2-2024.pdf", "f1", "ABC1 - 1200 Elm Street")]


def _script():
    return importlib.import_module("scripts.attachment_backfill_candidates")


class SpySvc:
    """Records every service call; read paths return canned data."""

    def __init__(self, run=None, open_runs=(), summary=()):
        self.calls: list[str] = []
        self.kwargs: dict[str, dict] = {}
        self.run = run
        self._open_runs = list(open_runs)
        self._summary = list(summary)
        self.repo = self

    def read_bills_for_backfill(self, *, company_id):
        return [BillRow(id=1, bill_number="42", total_amount=Decimal("5.00"), project_ids=frozenset({10}))]

    @staticmethod
    def plan_box_candidates(inventory, bills, projects):
        from entities.attachment_backfill.business.service import AttachmentBackfillService

        return AttachmentBackfillService.plan_box_candidates(inventory, bills, projects)

    def read_run(self, public_id):
        return self.run

    def open_runs(self, source):
        return self._open_runs

    def run_summary(self, run_id):
        return self._summary

    def __getattr__(self, name):
        def _call(*a, **k):
            self.calls.append(name)
            self.kwargs[name] = k
            if name == "generate_box_candidates":
                return {"pruned_stale": 4}
            if name == "verify_candidates" and self.kwargs.get("_raise"):
                raise KeyboardInterrupt
            return {"Id": 1, "PublicId": "new-run", "Status": "cancelled"}

        return _call


def _parse(*argv):
    return _script().build_parser().parse_args(list(argv))


def _generate(svc, *extra, inventory=INVENTORY):
    return _script().cmd_generate(
        _parse("generate", "--root-folder-id", "1", *extra), svc,
        list_inventory=lambda root: inventory, load_projects=lambda: PROJECTS,
    )


def test_parser_defaults_are_dry_run():
    assert _parse("generate", "--root-folder-id", "1").apply is False
    args = _parse("verify", "--run", "00000000-0000-0000-0000-000000000000")
    assert args.apply is False and args.workers == 4


@pytest.mark.parametrize("argv", [
    ["verify", "--run", "p", "--batch", "0"],
    ["verify", "--run", "p", "--workers", "0"],
    ["verify", "--run", "p", "--limit", "-1"],
])
def test_cli_rejects_non_positive_numbers(argv):
    with pytest.raises(SystemExit):
        _parse(*argv)


def test_generate_dry_run_never_writes():
    svc = SpySvc()
    assert _generate(svc) == 0 and svc.calls == []


def test_generate_apply_refuses_while_a_run_is_open(capsys):
    svc = SpySvc(open_runs=[{"Id": 9, "PublicId": "open-run-uuid", "Status": "ready", "Source": "box"}])
    listed = []
    rc = _script().cmd_generate(
        _parse("generate", "--root-folder-id", "1", "--apply"), svc,
        list_inventory=lambda root: listed.append(root) or INVENTORY, load_projects=lambda: PROJECTS,
    )
    assert rc != 0 and svc.calls == [] and listed == []
    out = capsys.readouterr().out
    assert "open-run-uuid" in out and "--force-new-run" in out


def test_generate_force_new_run_opens_a_second_run(capsys):
    svc = SpySvc(open_runs=[{"Id": 9, "PublicId": "open-run-uuid", "Status": "generating", "Source": "box"}])
    assert _generate(svc, "--apply", "--force-new-run") == 0
    assert svc.calls == ["create_run", "generate_box_candidates", "mark_run_ready"]
    assert svc.kwargs["generate_box_candidates"]["prune_stale"] is False


def test_generate_resume_requires_generating_run():
    svc = SpySvc(run={"Id": 3, "PublicId": "p", "Status": "ready", "Source": "box"})
    assert _generate(svc, "--resume-run", "p", "--apply", inventory=[]) != 0
    assert "generate_box_candidates" not in svc.calls and "create_run" not in svc.calls


def test_generate_resume_inserts_into_existing_run_prunes_and_prints_actual_status(capsys):
    svc = SpySvc(run={"Id": 3, "PublicId": "p", "Status": "generating", "Source": "box"})
    assert _generate(svc, "--resume-run", "p", "--apply") == 0
    assert svc.calls == ["generate_box_candidates", "mark_run_ready"]
    assert svc.kwargs["generate_box_candidates"]["prune_stale"] is True
    out = capsys.readouterr().out
    assert "pruned_stale: 4" in out
    assert "run status: cancelled" in out  # the status the sproc returned, not a constant


def test_verify_refuses_run_not_ready(capsys):
    svc = SpySvc(run={"Id": 3, "PublicId": "p", "Status": "generating", "Source": "box"})
    assert _script().cmd_verify(_parse("verify", "--run", "p", "--apply"), svc) != 0
    assert "verify_candidates" not in svc.calls
    assert "ready" in capsys.readouterr().out


def test_verify_dry_run_downloads_nothing(capsys):
    svc = SpySvc(
        run={"Id": 3, "Source": "box", "Status": "ready"},
        summary=[
            {"MatchTier": "A", "ReviewStatus": "pending", "CandidateCount": 15, "Unchecked": 4, "Errored": 2},
            {"MatchTier": "A", "ReviewStatus": "rejected", "CandidateCount": 3, "Unchecked": 3, "Errored": 0},
        ],
    )
    assert _script().cmd_verify(_parse("verify", "--run", "x"), svc) == 0
    assert "would check: 4" in capsys.readouterr().out and svc.calls == []
    assert _script().cmd_verify(_parse("verify", "--run", "x", "--include-errors"), svc) == 0
    assert "would check: 6" in capsys.readouterr().out


def test_verify_keyboard_interrupt_exits_promptly(monkeypatch, capsys):
    import integrations.box.base.client as client_mod

    class NoBox:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(client_mod, "BoxHttpClient", lambda *a, **k: NoBox())
    svc = SpySvc(run={"Id": 3, "Source": "box", "Status": "ready"})
    svc.kwargs["_raise"] = True
    assert _script().cmd_verify(_parse("verify", "--run", "p", "--apply"), svc) == 130
    assert "unchecked" in capsys.readouterr().out


def test_cancel_dry_run_and_apply_prints_actual_status(capsys):
    svc = SpySvc(run={"Id": 3, "PublicId": "p", "Status": "ready", "Source": "box"})
    assert _script().cmd_cancel(_parse("cancel", "--run", "p"), svc) == 0
    assert svc.calls == []
    assert _script().cmd_cancel(_parse("cancel", "--run", "p", "--apply"), svc) == 0
    assert svc.calls == ["cancel_run"]
    assert "cancelled" in capsys.readouterr().out
