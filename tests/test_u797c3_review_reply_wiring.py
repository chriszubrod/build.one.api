"""U-797c3: sproc static checks, repo method, config flag, admin endpoints, scheduler timers."""
from __future__ import annotations

import ast
import inspect
import os
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.api import admin as admin_mod
from tests.sproc_text import REPO_ROOT, strip_sql_comments
from entities.review.persistence import review_notification_repo as repo_mod
from entities.review.persistence.review_notification_repo import ReviewNotificationRepository

SQL = REPO_ROOT / "entities/review/sql/dbo.review_reply_ingest.sql"


def _norm_code() -> str:
    return re.sub(r"\s+", " ", strip_sql_comments(SQL.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- sproc


def test_sproc_file_has_contract_header():
    head = SQL.read_text(encoding="utf-8").splitlines()[:5]
    assert any(line.startswith("-- Contract:") for line in head)


def test_sproc_name_and_window_default():
    code = _norm_code()
    assert "CREATE OR ALTER PROCEDURE dbo.ReadOpenReviewReplyConversations" in code
    assert "@SinceDays INT = 45" in code
    assert "DATEADD(day, -@SinceDays, SYSUTCDATETIME())" in code


def test_sproc_nocount_go_and_no_rollback():
    raw = SQL.read_text(encoding="utf-8")
    code = strip_sql_comments(raw)
    assert "SET NOCOUNT ON" in code
    assert raw.rstrip().endswith("GO")
    assert "ROLLBACK" not in code.upper()


def test_sproc_distinct_conversation_id_is_bin_and_not_null():
    code = _norm_code()
    assert re.search(r"SELECT DISTINCT n\.\[ConversationId\] COLLATE Latin1_General_BIN", code)
    assert "n.[ConversationId] IS NOT NULL" in code


def test_sproc_open_target_rule_per_kind():
    code = _norm_code()
    assert "n.[Kind] = N'bill'" in code
    assert "b.[Status] IN (N'submitted', N'in_review')" in code
    assert "n.[Kind] = N'expense'" in code
    assert "e.[Status] IN (N'submitted', N'in_review')" in code
    assert "e.[IsDraft]" not in code
    assert "n.[Kind] = N'contract_labor'" in code
    assert "c.[Status] IN (N'pending_review', N'submitted')" in code
    # U-797c4a: bill_credit now has an open rule (IsDraft = 1 and the latest review's kind),
    # pinned in tests/test_u797c4a_sql.py. This U-797c3 assertion used to forbid it.
    assert "n.[Kind] = N'bill_credit'" in code
    assert "bc.[IsDraft] = 1" in code


# ---------------------------------------------------------------- repo


def test_repo_method_binds_since_days_and_returns_strings(monkeypatch):
    calls = []

    def fake_call(*, cursor, name, params):
        calls.append((name, params))

    class Cur:
        def fetchall(self):
            return [("AAQkA1",), ("AAQkA2",)]

    @contextmanager
    def fake_conn(*_a, **_k):
        yield SimpleNamespace(cursor=lambda: Cur(), commit=lambda: None)

    monkeypatch.setattr(repo_mod, "call_procedure", fake_call)
    monkeypatch.setattr(repo_mod, "get_connection", fake_conn)
    out = ReviewNotificationRepository().read_open_reply_conversations()
    assert calls == [("ReadOpenReviewReplyConversations", {"SinceDays": 45})]
    assert out == ["AAQkA1", "AAQkA2"]


# ---------------------------------------------------------------- config


def test_config_flag_defaults_off():
    import config

    fields = getattr(config.Settings, "model_fields", None) or config.Settings.__fields__
    assert "review_reply_ingest_enabled" in fields
    assert fields["review_reply_ingest_enabled"].default is False


# ---------------------------------------------------------------- endpoints


def _route(suffix: str):
    for r in admin_mod.router.routes:
        if getattr(r, "path", "").endswith(suffix):
            return r
    return None


def _has_drain_dep(route) -> bool:
    return any(d.call is admin_mod._require_drain_secret for d in route.dependant.dependencies)


def test_ingest_endpoint_is_drain_gated_post():
    route = _route("/review-replies/ingest")
    assert route is not None
    assert "POST" in route.methods
    assert _has_drain_dep(route)
    assert "lookback_hours" in [p.name for p in route.dependant.query_params]
    assert "ge=1, le=24" in inspect.getsource(route.endpoint).replace("  ", " ") or \
        "ge=1" in inspect.getsource(route.endpoint)


def test_catch_up_endpoint_is_drain_gated_post():
    route = _route("/review-replies/catch-up")
    assert route is not None
    assert "POST" in route.methods
    assert _has_drain_dep(route)


# ---------------------------------------------------------------- scheduler

SCHED_ENV = "SCHEDULER_FUNCTION_APP"


def _scheduler_file() -> Path:
    raw = os.environ.get(SCHED_ENV)
    if not raw or not Path(raw).is_file():
        pytest.skip(f"set {SCHED_ENV} to the scheduler function_app.py to run")
    return Path(raw)


def _fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _schedule(fn):
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "timer_trigger":
            for kw in dec.keywords:
                if kw.arg == "schedule":
                    return ast.literal_eval(kw.value)
    return None


def _post_paths(fn):
    out = []
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_post" and node.args
                and isinstance(node.args[0], ast.Constant)):
            out.append(node.args[0].value)
    return out


def test_scheduler_timers_present_with_schedules_and_paths():
    tree = ast.parse(_scheduler_file().read_text(encoding="utf-8"))
    ingest = _fn(tree, "review_reply_ingest")
    catch_up = _fn(tree, "review_reply_catch_up")
    assert ingest is not None and catch_up is not None
    assert _schedule(ingest) == "0 */10 * * * *"
    assert _post_paths(ingest) == ["/api/v1/admin/review-replies/ingest"]
    assert _schedule(catch_up) == "0 5 7 * * *"
    assert _post_paths(catch_up) == ["/api/v1/admin/review-replies/catch-up"]


def _logs_nonzero_errors(fn) -> bool:
    """True when the body has `if ...errors...:` whose block logs at warning or error."""
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and 'errors' in ast.unparse(node.test):
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                        and inner.func.attr in ("warning", "error")):
                    return True
    return False


def test_scheduler_timers_log_when_errors_nonzero():
    tree = ast.parse(_scheduler_file().read_text(encoding="utf-8"))
    assert _logs_nonzero_errors(_fn(tree, "review_reply_ingest"))
    assert _logs_nonzero_errors(_fn(tree, "review_reply_catch_up"))


def test_watermark_hazard_is_documented():
    notes = (REPO_ROOT / "docs/conventions/notifications.md").read_text(encoding="utf-8")
    assert "MAX(ReceivedDatetime)" in notes
    assert "ReadMaxReceivedDatetimeByMailbox" in notes
    poll_doc = ast.get_docstring(
        next(n for n in ast.walk(ast.parse((REPO_ROOT / "shared/api/admin.py").read_text(encoding="utf-8")))
             if isinstance(n, ast.AsyncFunctionDef) and n.name == "email_poll_router")
    )
    assert "MAX(ReceivedDatetime)" in poll_doc
    assert "reviewer_reply" in poll_doc


def _warns_when_locked(fn) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test)
            if "skipped" in test_src and "locked" in test_src:
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                            and inner.func.attr in ("warning", "error")):
                        return True
    return False


def test_scheduler_timers_warn_when_the_lock_is_held():
    tree = ast.parse(_scheduler_file().read_text(encoding="utf-8"))
    assert _warns_when_locked(_fn(tree, "review_reply_ingest"))
    assert _warns_when_locked(_fn(tree, "review_reply_catch_up"))


def test_watchdog_allowlists_the_review_reply_ingest_lock():
    # Tied to the service's own lock name, so a rename cannot silently drop it from the watchdog.
    from entities.email_message.business.review_reply_ingest_service import LOCK_NAME
    from shared.applock_watchdog import ALLOWLIST_PREFIXES

    assert LOCK_NAME.startswith(ALLOWLIST_PREFIXES)


def test_scheduler_catch_up_comment_describes_budgeted_rotated_walk():
    text = _scheduler_file().read_text(encoding="utf-8")
    block = text[text.index("Reviewer-reply catch-up (U-797c3)"):text.index("def review_reply_catch_up")]
    assert "07:05" in block
    assert "07:00" not in block
    assert "budget" in block.lower()
    assert "rotat" in block.lower()


def test_scheduler_inventory_lists_both_timers():
    inv = _scheduler_file().parent / "docs" / "timer-inventory.md"
    text = inv.read_text(encoding="utf-8")
    assert "`review_reply_ingest`" in text
    assert "`review_reply_catch_up`" in text
