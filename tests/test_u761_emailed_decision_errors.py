"""U-761 — the emailed reviewer-decision path (Path A) refuses like Path B.

Both entities, engine and route:
  * a line added between the fast path and the lock -> ValueError / 400, no write;
  * an SCC row whose id is None -> "not found" ValueError / 400, no write;
  * a stale line ROWVERSION from the coding update -> 409 line_row_version_stale;
  * a terminal-lock refusal from the sprocs -> 422 status_locked;
  * any other exception is unchanged (raises raw / 500).
"""

from __future__ import annotations

import dataclasses
import inspect
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.bill.business.service import BillService
from entities.expense.business.service import ExpenseService
from entities.review.business import reviewer_decision
from entities.review.business.decision_errors import ReviewDecisionApplyError
from shared.database import DatabaseConcurrencyError
from shared.lifecycle.terminal_lock import StatusLockedError

ENTITIES = {
    "bill": dict(svc=BillService, router="entities.bill.api.router", base="/api/v1/bill"),
    "expense": dict(svc=ExpenseService, router="entities.expense.api.router", base="/api/v1/expense"),
}
APPROVED = NS(id=2, is_final=True, is_declined=False, name="Approved")


class _World:
    """A spec whose hooks are fakes, plus the lazily imported collaborators."""

    def __init__(self, entity, *, count=1, scc_id=7, update=None, create=None):
        self.entity = entity
        self.line = NS(id=1, row_version="RV1")
        self.line_service = MagicMock()
        self.line_service.update_coding_in_transaction.side_effect = update
        self.repo = MagicMock()
        if create is not None:
            self.repo.create.side_effect = create
        else:
            self.repo.create.return_value = NS(review_status_id=APPROVED.id)
        self.conn_entered = 0
        base = ENTITIES[entity]["svc"](repo=MagicMock())._reviewer_decision_spec()

        @contextmanager
        def get_connection():
            self.conn_entered += 1
            yield "conn"

        scc_service = MagicMock()
        scc_service.read_by_public_id.return_value = NS(id=scc_id, is_holding_code=False)
        self.spec = dataclasses.replace(
            base,
            read_parent=lambda public_id: NS(id=10, is_draft=True),
            resolve_recipients=lambda svc, pid: {"to": [NS(user_id=20, email="pm@example.com")], "cc": []},
            line_service_factory=lambda: self.line_service,
            read_lines=lambda svc, pid: [self.line],
            count_lines_for_update=lambda svc, pid, conn: count,
            lock_current_review=lambda repo, pid, conn: None,
            get_connection=get_connection,
            sub_cost_code_service=lambda: scc_service,
            assert_may_act_as=lambda **kw: True,
        )

    def patches(self, st: ExitStack):
        statuses = MagicMock()
        statuses.read_all.return_value = [APPROVED]
        statuses.read_by_id.return_value = APPROVED
        st.enter_context(patch("entities.review_status.business.service.ReviewStatusService", return_value=statuses))
        st.enter_context(patch("entities.review.persistence.repo.ReviewRepository", return_value=self.repo))
        st.enter_context(patch("entities.review.business.recipient_service.ReviewRecipientService"))
        st.enter_context(patch("shared.authz.provenance.bind_reviewer_reply_to_review_thread", return_value=NS(id=555)))

    def engine(self):
        with ExitStack() as st:
            self.patches(st)
            return reviewer_decision.apply_emailed(
                self.spec,
                public_id="PID-1",
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id="SCC-PID",
                reviewer_email_message_public_id="MSG-PID",
            )

    def route(self):
        import importlib

        cfg = ENTITIES[self.entity]
        router_mod = importlib.import_module(cfg["router"])
        dep = inspect.signature(router_mod.apply_reviewer_decision_router).parameters["current_user"].default.dependency
        app.dependency_overrides[dep] = lambda: {"sub": "test-sub", "id": 77}
        try:
            with ExitStack() as st:
                self.patches(st)
                st.enter_context(patch.object(cfg["svc"], "_reviewer_decision_spec", lambda _self: self.spec))
                return TestClient(app, raise_server_exceptions=False).post(
                    f"{cfg['base']}/PID-1/apply-reviewer-decision",
                    json={
                        "decision": "approved",
                        "reviewer_email": "pm@example.com",
                        "sub_cost_code_public_id": "SCC-PID",
                        "reviewer_email_message_public_id": "MSG-PID",
                    },
                )
        finally:
            app.dependency_overrides.pop(dep, None)

    def assert_no_write(self):
        self.line_service.update_coding_in_transaction.assert_not_called()
        self.repo.create.assert_not_called()


both = pytest.mark.parametrize("entity", list(ENTITIES))


@both
def test_happy_path_still_codes_and_reviews(entity):
    w = _World(entity)
    assert w.engine()["review_status"] == "Approved"
    w.line_service.update_coding_in_transaction.assert_called_once()
    w.repo.create.assert_called_once()
    assert _World(entity).route().status_code == 200


# --- 1. line added between the fast path and the lock ----------------------


@both
def test_line_added_under_lock_is_refused_engine(entity):
    w = _World(entity, count=2)
    with pytest.raises(ValueError, match="has 2 line items"):
        w.engine()
    assert w.conn_entered == 1
    w.assert_no_write()


@both
def test_line_added_under_lock_is_refused_route(entity):
    w = _World(entity, count=2)
    resp = w.route()
    assert resp.status_code == 400
    assert "has 2 line items" in resp.json()["detail"]
    w.assert_no_write()


# --- 2. SCC row with id None ------------------------------------------------


@both
def test_scc_id_none_is_not_found_engine(entity):
    w = _World(entity, scc_id=None)
    with pytest.raises(ValueError) as ei:
        w.engine()
    assert str(ei.value) == "SubCostCode with public_id 'SCC-PID' not found."
    assert w.conn_entered == 0
    w.assert_no_write()


@both
def test_scc_id_none_is_not_found_route(entity):
    w = _World(entity, scc_id=None)
    resp = w.route()
    assert resp.status_code == 400
    assert resp.json()["detail"] == "SubCostCode with public_id 'SCC-PID' not found."
    w.assert_no_write()


# --- 3a. stale line ROWVERSION from the coding update ------------------------


@both
def test_stale_rowversion_is_409_engine(entity):
    db_err = DatabaseConcurrencyError("row moved")
    w = _World(entity, update=db_err)
    with pytest.raises(ReviewDecisionApplyError) as ei:
        w.engine()
    assert (ei.value.status_code, ei.value.error_code) == (409, "line_row_version_stale")
    assert ei.value.detail == f"The {entity} line was updated by someone else; refetch and re-present."
    assert ei.value.__cause__ is db_err
    w.repo.create.assert_not_called()


@both
def test_stale_rowversion_is_409_route(entity):
    w = _World(entity, update=DatabaseConcurrencyError("row moved"))
    resp = w.route()
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "line_row_version_stale"
    w.repo.create.assert_not_called()


# --- 3b. terminal lock from the sprocs -------------------------------------


@both
@pytest.mark.parametrize("where", ["create", "update"])
def test_status_locked_is_422_engine(entity, where):
    locked = StatusLockedError("parent completed")
    w = _World(entity, **{where: locked})
    with pytest.raises(ReviewDecisionApplyError) as ei:
        w.engine()
    assert (ei.value.status_code, ei.value.error_code) == (422, "status_locked")
    assert ei.value.detail == str(locked)
    assert ei.value.__cause__ is locked


@both
@pytest.mark.parametrize("where", ["create", "update"])
def test_status_locked_is_422_route(entity, where):
    locked = StatusLockedError("parent completed")
    resp = _World(entity, **{where: locked}).route()
    assert resp.status_code == 422
    assert resp.json() == {"detail": str(locked), "error_code": "status_locked"}


# --- 4. anything else is unchanged ------------------------------------------


@both
@pytest.mark.parametrize(
    "kw",
    [
        {"create": RuntimeError("boom")},
        {"update": RuntimeError("boom")},
        # A concurrency error NOT from the coding update is not a stale line.
        {"create": DatabaseConcurrencyError("review moved")},
    ],
    ids=["create_runtime", "update_runtime", "create_concurrency"],
)
def test_unrelated_exception_is_unchanged(entity, kw):
    exc = next(iter(kw.values()))
    with pytest.raises(type(exc)) as ei:
        _World(entity, **kw).engine()
    assert ei.value is exc
    assert _World(entity, **kw).route().status_code == 500


# --- agent guidance --------------------------------------------------------


@pytest.mark.parametrize(
    "tool_path,prompt_path",
    [
        ("entities.bill.intelligence.tools:apply_reviewer_decision", "intelligence/agents/bill_specialist/prompt.md"),
        ("entities.expense.intelligence.tools:apply_expense_reviewer_decision", "intelligence/agents/expense_specialist/prompt.md"),
    ],
    ids=["bill", "expense"],
)
def test_agent_guidance_names_the_new_refusals(tool_path, prompt_path):
    import importlib
    from pathlib import Path

    mod, name = tool_path.split(":")
    desc = getattr(importlib.import_module(mod), name).description
    prompt = (Path(__file__).resolve().parent.parent / prompt_path).read_text()
    for text in (desc, prompt):
        assert "409 `line_row_version_stale`" in text or "`409 line_row_version_stale`" in text
        # Chris's decision: a 409 on the emailed path is never retried; a human reconciles.
        stale = text[text.index("line_row_version_stale"):]
        stale = stale[: stale.index("status_locked")]
        assert "do not retry" in stale.lower()
        assert "flagged_needs_review" in stale
        assert "retry once" not in text.lower()
        assert "422 `status_locked`" in text or "`422 status_locked`" in text
