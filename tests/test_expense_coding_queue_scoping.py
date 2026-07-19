"""U-059 pure-logic tests: the expense-coding queue/metrics service threads the
authenticated actor (current_user_id / current_is_system_admin ContextVars) down
into the repository call so the sproc can UserProject-scope the rows, plus an
anti-drift guard that the scoping predicate stays in all three SQL scans. No DB."""

from contextlib import contextmanager
from pathlib import Path

from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from shared.authz import current_user_id, current_is_system_admin


_SQL_FILE = (
    Path(__file__).resolve().parent.parent
    / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
)


@contextmanager
def _actor(user_id, is_system_admin):
    """Set the auth ContextVars for the block, restoring them afterwards."""
    t1 = current_user_id.set(user_id)
    t2 = current_is_system_admin.set(is_system_admin)
    try:
        yield
    finally:
        current_user_id.reset(t1)
        current_is_system_admin.reset(t2)


class _FakeLineRepo:
    def __init__(self):
        self.queue_calls = []
        self.metrics_calls = []

    def read_expense_coding_queue(self, realm_id=None, actor_user_id=None, actor_is_system_admin=None):
        self.queue_calls.append((realm_id, actor_user_id, actor_is_system_admin))
        return []  # empty => service skips the reseed branch

    def read_expense_coding_metrics(self, realm_id=None, since_days=None, actor_user_id=None, actor_is_system_admin=None):
        self.metrics_calls.append((realm_id, since_days, actor_user_id, actor_is_system_admin))
        return {}


def test_queue_threads_actor_into_repo():
    fake = _FakeLineRepo()
    svc = QboPurchaseService(line_repo=fake)
    with _actor(42, False):
        svc.get_expense_coding_queue(realm_id="R1")
    assert fake.queue_calls == [("R1", 42, False)]


def test_metrics_threads_actor_into_repo():
    fake = _FakeLineRepo()
    svc = QboPurchaseService(line_repo=fake)
    with _actor(7, True):
        svc.get_expense_coding_metrics(realm_id="R1", since_days=30)
    assert fake.metrics_calls == [("R1", 30, 7, True)]


def test_admin_actor_forwarded_true():
    fake = _FakeLineRepo()
    svc = QboPurchaseService(line_repo=fake)
    with _actor(17, True):
        svc.get_expense_coding_queue(realm_id=None)
    assert fake.queue_calls == [(None, 17, True)]


def test_all_three_scans_retain_userproject_scoping():
    """Anti-drift guard. The UserProject scoping predicate must stay in ALL THREE
    scans of this file — ReadExpenseCodingQueue, the metrics TotalTargetLines
    subquery, and the metrics per-status aggregation. This catches the real
    failure mode: a later edit silently dropping scoping from one scan (e.g.
    metrics leaking every realm's counts while the queue stays scoped, or vice
    versa). Same discipline as the U-057/U-062 copy-paste-drift guards."""
    sql = _SQL_FILE.read_text()
    assert sql.count("dbo.UserCanAccessProject(") == 3
    assert sql.count("COALESCE(eci.[ConfirmedProjectId], eci.[SuggestedProjectId]) IS NULL") == 3
    assert sql.count("@ActorIsSystemAdmin = 1") == 3
