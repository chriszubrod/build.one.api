"""U-551b-f6 — the task inbox returns only the entity types the caller's modules grant."""

import inspect
import logging
import re
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from entities.review.business.task_feed import ReviewTaskFeed
from entities.review.persistence.inbox_repo import ReviewInboxRepository
from entities.task.api.router import get_tasks_inbox, get_tasks_inbox_counts
from entities.task.business import feed_registry
from entities.task.business.aggregator import TaskAggregator
from entities.task.business.entity_modules import (
    ENTITY_MODULE_BY_TYPE,
    readable_entity_types,
)
from entities.task.business.feed import TaskCount, TaskFeed, TaskRow
from shared.rbac_constants import Modules

INBOX = "/api/v1/get/tasks/inbox"
COUNTS = "/api/v1/get/tasks/inbox/counts"
SQL_PATH = Path(__file__).resolve().parent.parent / "entities/review/sql/dbo.inbox_tasks.sql"
ROUTER = "entities.task.api.router"


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _rbac_override():
    for fn in (get_tasks_inbox, get_tasks_inbox_counts):
        dep = inspect.signature(fn).parameters["current_user"].default.dependency
        app.dependency_overrides[dep] = lambda: {"sub": "test-sub", "user_id": 5}
    # resolve_user_id would open a DB connection — fake it.
    with patch(f"{ROUTER}.resolve_user_id", return_value=5):
        yield
    app.dependency_overrides.clear()


def _grants(types):
    return patch(f"{ROUTER}.readable_entity_types", return_value=list(types))


RBAC = "entities.task.business.entity_modules.has_module_permission"


def test_moduleMap_isPinnedLiterally():
    assert ENTITY_MODULE_BY_TYPE == {
        "Bill": Modules.BILLS,
        "Expense": Modules.EXPENSES,
        "BillCredit": Modules.BILL_CREDITS,
        "Invoice": Modules.INVOICES,
    }


@pytest.mark.parametrize("granted_type", list(ENTITY_MODULE_BY_TYPE))
def test_readable_entity_types_oneModuleGrantsExactlyItsOwnType(granted_type):
    only = ENTITY_MODULE_BY_TYPE[granted_type]
    calls = []

    def fake(user, module, perm):
        calls.append((module, perm))
        return module == only

    # Order pin: results follow the mapping order.
    with patch(RBAC, side_effect=fake):
        assert readable_entity_types({"sub": "x"}) == [granted_type]
    assert sorted(calls) == sorted((m, "can_read") for m in ENTITY_MODULE_BY_TYPE.values())
    assert list(ENTITY_MODULE_BY_TYPE) == ["Bill", "Expense", "BillCredit", "Invoice"]


def test_readable_entity_types_noGrants_isEmpty():
    with patch(RBAC, return_value=False):
        assert readable_entity_types({"sub": "x"}) == []


def test_readable_entity_types_systemAdmin_getsEveryType():
    # Real has_module_permission: the admin branch returns SYSTEM_ADMIN_GRANT without a DB read
    # (_resolve_permissions_from_db short-circuits on is_system_admin), so nothing is patched.
    admin = {"sub": "admin-sub", "is_system_admin": True}
    assert readable_entity_types(admin) == list(ENTITY_MODULE_BY_TYPE)


@pytest.mark.parametrize("granted, params", [(["Bill"], {"entity_type": "Expense"}), ([], {})])
def test_inbox_emptyAnswer_doesNotQuery(client, granted, params):
    with _grants(granted), patch.object(TaskAggregator, "read_inbox") as agg:
        r = client.get(INBOX, params=params)
    assert r.status_code == 200
    assert r.json() == {"data": [], "count": 0}
    agg.assert_not_called()


@pytest.mark.parametrize("granted, requested", [(["Bill"], "Bill"), (["Bill", "Invoice"], None)])
def test_inbox_passesTheAllowedList(client, granted, requested):
    params = {"entity_type": requested} if requested else {}
    with _grants(granted), patch.object(TaskAggregator, "read_inbox", return_value=[]) as agg:
        r = client.get(INBOX, params=params)
    assert r.status_code == 200
    kw = agg.call_args.kwargs
    assert kw["entity_type"] == requested
    assert kw["allowed_entity_types"] == granted


def test_inbox_unknownEntityType_isTreatedAsNoFilter(client):
    with _grants(["Bill", "Invoice"]), patch.object(TaskAggregator, "read_inbox", return_value=[]) as agg:
        r = client.get(INBOX, params={"entity_type": "Foo"})
    assert r.status_code == 200
    kw = agg.call_args.kwargs
    assert kw["entity_type"] is None
    assert kw["allowed_entity_types"] == ["Bill", "Invoice"]


def test_counts_filteredToGrantedTypes(client):
    counts = [TaskCount("Review", t, False, 1, 2, 3) for t in ("Bill", "Expense", "BillCredit", "Invoice")]
    with _grants(["Bill", "Expense"]), patch.object(TaskAggregator, "read_counts", return_value=counts):
        r = client.get(COUNTS)
    assert r.status_code == 200
    assert [c["entity_type"] for c in r.json()["data"]] == ["Bill", "Expense"]


def _row(entity_type):
    return TaskRow(
        task_type="Review", entity_type=entity_type, is_credit=False, parent_public_id="p",
        parent_id=1, parent_number="1", counterparty_name=None, amount=None, review_public_id=None,
        status_name="Pending", status_color=None, status_sort_order=0, status_is_final=False,
        status_is_declined=False, submitter_firstname=None, submitter_lastname=None,
        last_activity_at="2026-01-01T00:00:00", assigned_to_me=True,
    )


@contextmanager
def _only_feed(feed):
    saved = feed_registry.all_feeds()
    feed_registry.clear()
    try:
        feed_registry.register(feed)
        yield
    finally:
        feed_registry.clear()
        for f in saved:
            feed_registry.register(f)


def test_inbox_feedThatIgnoresTheGrant_isDroppedAtTheRouterEdge(client, caplog):
    class Leaky:
        task_type = "Review"

        def read_inbox(self, **kw):
            return [_row("Bill"), _row("Expense")]

    with _only_feed(Leaky()), _grants(["Bill"]), caplog.at_level(logging.ERROR, logger=ROUTER):
        r = client.get(INBOX)
    assert r.status_code == 200
    assert [x["entity_type"] for x in r.json()["data"]] == ["Bill"]
    assert any("outside the caller's grants" in m for m in caplog.messages)


def test_aggregator_threadsAllowedEntityTypesToEveryFeed():
    seen = []

    class Fake:
        task_type = "Review"

        def read_inbox(self, **kw):
            seen.append(kw)
            return []

    with _only_feed(Fake()):
        TaskAggregator().read_inbox(
            current_user_id=1, is_system_admin=False, scope="mine", task_type=None,
            entity_type=None, allowed_entity_types=["Bill"], status_public_id=None, page=0, page_size=10,
        )
    assert seen and seen[0]["allowed_entity_types"] == ["Bill"]


def test_entityTypes_hasNoDefaultAnywhere():
    for fn in (TaskFeed.read_inbox, ReviewTaskFeed.read_inbox, ReviewInboxRepository.read_inbox,
               TaskAggregator.read_inbox):
        assert inspect.signature(fn).parameters["allowed_entity_types"].default is inspect.Parameter.empty


def test_reviewFeed_forwardsEntityTypesToTheRepo():
    repo = MagicMock()
    repo.read_inbox.return_value = []
    ReviewTaskFeed(repo=repo).read_inbox(
        current_user_id=1, is_system_admin=False, scope="mine", entity_type=None,
        allowed_entity_types=["Expense", "Invoice"], status_public_id=None, page=0, page_size=10,
    )
    assert repo.read_inbox.call_args.kwargs["allowed_entity_types"] == ["Expense", "Invoice"]


def test_repo_joinsEntityTypesForTheSproc():
    kwargs = dict(current_user_id=1, is_system_admin=False, scope="mine", entity_type=None,
                  status_public_id=None, page=0, page_size=10)
    with patch("entities.review.persistence.inbox_repo.get_connection") as gc, \
         patch("entities.review.persistence.inbox_repo.call_procedure") as cp:
        gc.return_value.__enter__.return_value = MagicMock()
        ReviewInboxRepository().read_inbox(allowed_entity_types=["Bill", "Expense"], **kwargs)
        assert cp.call_args.kwargs["params"]["AllowedEntityTypes"] == "Bill,Expense"
        cp.reset_mock()
        with pytest.raises(ValueError):
            ReviewInboxRepository().read_inbox(allowed_entity_types=[], **kwargs)
        cp.assert_not_called()


def _strip_sql_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"--[^\n]*", "", text)


def test_sql_everyArmFiltersOnItsOwnEntityType():
    sql = SQL_PATH.read_text()
    counts_idx = sql.index("CREATE OR ALTER PROCEDURE dbo.ReadInboxTaskCounts")
    tasks_start = sql.index("CREATE OR ALTER PROCEDURE dbo.ReadInboxTasks")
    # Declaration is checked on the raw text (the parameter comment is not a predicate).
    assert len(re.findall(r"@AllowedEntityTypes\s+NVARCHAR\(200\)\s*=\s*NULL", sql[tasks_start:counts_idx])) == 1
    tasks = _strip_sql_comments(sql[tasks_start:counts_idx])
    counts = _strip_sql_comments(sql[counts_idx:])

    types = list(ENTITY_MODULE_BY_TYPE)
    markers = [f"WHERE (@EntityType IS NULL OR @EntityType = N'{x}')" for x in types]
    for m in markers:
        assert tasks.count(m) == 1, m
    positions = [tasks.index(m) for m in markers] + [len(tasks)]
    assert positions == sorted(positions)
    for i, x in enumerate(types):
        arm = tasks[positions[i]:positions[i + 1]]
        lines = [ln.strip() for ln in arm.splitlines()]
        own = f"AND (@AllowedEntityTypes IS NULL OR N',' + @AllowedEntityTypes + N',' LIKE N'%,{x},%')"
        assert lines.count(own) == 1, x
        assert arm.count("LIKE N'%,") == 1, x
    assert "@AllowedEntityTypes" not in counts
