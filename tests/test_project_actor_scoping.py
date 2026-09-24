"""search_by_name and read_by_customer_id must thread the caller's actor to ReadProjects.

`ReadProjects` filters on `@ActorIsSystemAdmin = 1 OR EXISTS (UserProject …)`, which
FAILS CLOSED on NULL/NULL. The pre-fix body called `self.repo.read_all()` with no
actor, so every in-memory filter saw zero rows. Measured on prod, 2026-09-21:

    read_all() with actor                         -> 139 projects
    search_by_name(query='EVR') without actor slip -> 0 hits
    read_by_customer_id(1) without actor slip      -> 0 (customer has 13)

MEASURED RED: restore `self.repo.read_all()` in search_by_name only and three tests
go red; restore it in read_by_customer_id only and two go red — empty search/filter
results plus missing actor kwargs on the repo call. The clean-contract-labor CLI test
fails if main() calls bare repo.read_all() instead of ContractLaborService.read_all()
under declared system intent (system_authz(), mirroring the script's __main__ guard).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# tests/ must be on sys.path for `from conftest import ...` — this module sorts
# alphabetically before the test_qbo_* files that would otherwise have inserted it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import actor_absent, actor_context  # noqa: E402

from entities.project.business.service import ProjectService

CUSTOMER_ID = 1
OTHER_CUSTOMER_ID = 2


class FailClosedProjectRepo:
    """Models ReadProjects: yields nothing when actor is absent (NULL/NULL path)."""

    def __init__(self, projects):
        self._projects = projects
        self.read_all_calls = []

    def read_all(self, **kwargs):
        self.read_all_calls.append(kwargs)
        if actor_absent(kwargs):
            return []
        return list(self._projects)


class FailClosedContractLaborRepo:
    """Models ReadContractLabors: yields nothing when actor is absent (NULL/NULL path)."""

    def __init__(self, entries=()):
        self._entries = entries
        self.read_all_calls = []

    def read_all(self, **kwargs):
        self.read_all_calls.append(kwargs)
        if actor_absent(kwargs):
            return []
        return list(self._entries)


def _project(**overrides):
    defaults = {
        "id": 1,
        "name": "EVR - 6315 E Valley Rd",
        "abbreviation": "EVR",
        "customer_id": CUSTOMER_ID,
        "status": "active",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _service(*projects):
    repo = FailClosedProjectRepo(projects)
    return ProjectService(repo=repo), repo


def test_search_by_name_finds_project_by_name():
    service, _ = _service(
        _project(name="Evergreen Remodel"),
        _project(id=2, name="Other Job", abbreviation="OTH", customer_id=OTHER_CUSTOMER_ID),
    )

    with actor_context(17, False):
        hits = service.search_by_name(query="ever")

    assert len(hits) == 1
    assert hits[0].name == "Evergreen Remodel"


def test_search_by_name_finds_by_abbreviation():
    service, _ = _service(
        _project(name="6315 E Valley Rd", abbreviation="EVR"),
        _project(id=2, name="Unrelated", abbreviation="ZZZ", customer_id=OTHER_CUSTOMER_ID),
    )

    with actor_context(17, False):
        hits = service.search_by_name(query="evr")

    assert len(hits) == 1
    assert hits[0].abbreviation == "EVR"


def test_search_by_name_threads_the_actor():
    service, repo = _service(_project())

    with actor_context(17, False):
        service.search_by_name(query="evr")

    assert len(repo.read_all_calls) == 1
    call = repo.read_all_calls[0]
    assert call["actor_user_id"] == 17
    assert call["actor_is_system_admin"] is False


def test_read_by_customer_id_returns_only_that_customers_projects():
    service, _ = _service(
        _project(id=1, customer_id=CUSTOMER_ID),
        _project(id=2, name="Second for customer", abbreviation="S2", customer_id=CUSTOMER_ID),
        _project(id=3, name="Other customer", abbreviation="OC", customer_id=OTHER_CUSTOMER_ID),
    )

    with actor_context(17, False):
        rows = service.read_by_customer_id(CUSTOMER_ID)

    assert len(rows) == 2
    assert {p.id for p in rows} == {1, 2}


def test_read_by_customer_id_threads_the_actor():
    service, repo = _service(_project())

    with actor_context(17, False):
        service.read_by_customer_id(CUSTOMER_ID)

    assert len(repo.read_all_calls) == 1
    call = repo.read_all_calls[0]
    assert call["actor_user_id"] == 17
    assert call["actor_is_system_admin"] is False


def _contract_labor_entry(**overrides):
    defaults = {
        "id": 1,
        "employee_name": "Alice",
        "work_date": "2026-01-15",
        "job_name": "EVR",
        "time_in": "08:00",
        "time_out": "17:00",
        "description": "Framing",
        "status": "ready",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_clean_contract_labor_duplicates_cli_threads_system_admin_actor(capsys):
    from scripts.clean_contract_labor_duplicates import main

    from shared.authz.context import (
        current_is_system_admin,
        current_is_system_context,
        system_authz,
    )

    repo = FailClosedContractLaborRepo(
        [
            _contract_labor_entry(id=1, status="ready"),
            _contract_labor_entry(id=2, status="billed"),
        ]
    )

    with system_authz():
        with patch(
            "scripts.clean_contract_labor_duplicates.ContractLaborRepository",
            return_value=repo,
        ), patch.object(sys, "argv", ["clean_contract_labor_duplicates.py", "--dry-run"]):
            main()

        assert len(repo.read_all_calls) == 1
        assert repo.read_all_calls[0].get("actor_is_system_admin") is True

    out = capsys.readouterr().out
    assert "No duplicate groups found." not in out
    assert "Found 1 duplicate groups" in out

    # system_authz() must restore on exit — a bare assert_cli_system_admin() leak
    # would leave is_system_context=True for every later test in this process.
    assert current_is_system_admin.get() is False
    assert current_is_system_context.get() is False


def test_clean_contract_labor_duplicates_entrypoint_declares_system_intent():
    """The SECOND half of the CLI fix, pinned separately.

    The behavioural test above calls main() under system_authz(), so it proves the
    read threads the actor — but it cannot see the script's own `__main__` block. A
    mutation that deletes `assert_cli_system_admin()` from that block leaves every
    other test in this file green while the real script silently reverts to a
    fail-closed read. Both halves are required (the helper sets the ContextVars, the
    service reads them), so both are pinned; this one is a source assertion because
    the entrypoint is not importable as a function.
    """
    import ast

    src = Path(__file__).resolve().parent.parent / "scripts" / "clean_contract_labor_duplicates.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    main_guards = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and any(
            isinstance(c, ast.Compare)
            and isinstance(c.left, ast.Name)
            and c.left.id == "__name__"
            for c in ast.walk(node.test)
        )
    ]
    assert main_guards, "no `if __name__ == '__main__':` block found"

    called = [
        n.func.id for n in ast.walk(main_guards[0])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "assert_cli_system_admin" in called, (
        "the __main__ block must call assert_cli_system_admin() — without it the "
        "ContextVars are never set and ContractLaborService.read_all() threads a "
        "NULL actor, so ReadContractLabors fails closed and the script reports "
        "'No duplicate groups found.' regardless of the data"
    )
    assert called.index("assert_cli_system_admin") < called.index("main"), (
        "assert_cli_system_admin() must run BEFORE main()"
    )
