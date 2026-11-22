"""U-797c1: repo param mapping and the record service (claim / attach / release / stamp / lookup)."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from entities.review.business import review_notification_service as rns
from entities.review.persistence import review_notification_repo as repo_mod
from entities.review.persistence.review_notification_repo import ReviewNotificationRepository


# ---------------------------------------------------------------------------
# Fake pyodbc connection / cursor. call_procedure executes "EXEC dbo.Name @K=?,..."
# with the values in insertion order, so capturing execute() gives the exact mapping.
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, sets=None):
        self.executed: list[tuple[str, list]] = []
        self._sets = list(sets or [])
        self._current = self._sets.pop(0) if self._sets else []

    def execute(self, sql, params=None):
        self.executed.append((sql, list(params or [])))
        return self

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchall(self):
        return list(self._current)

    def nextset(self):
        self._current = self._sets.pop(0) if self._sets else []
        return bool(self._current)


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def _patch_conn(monkeypatch, cursor):
    conn = FakeConn(cursor)

    @contextmanager
    def fake_get_connection(*_a, **_k):
        yield conn

    monkeypatch.setattr(repo_mod, "get_connection", fake_get_connection)
    return conn


def _sql_of(cursor) -> str:
    return cursor.executed[-1][0]


def _params_of(cursor) -> list:
    return cursor.executed[-1][1]


# ---------------------------------------------------------------------------
# Repo: param mapping
# ---------------------------------------------------------------------------
def test_repo_create_maps_params_in_declared_order(monkeypatch):
    row = SimpleNamespace(Id=4)
    cursor = FakeCursor([[row]])
    _patch_conn(monkeypatch, cursor)
    got = ReviewNotificationRepository().create(
        kind="bill", review_id=5, subject="Subj", project_id=None, work_date=None,
    )
    assert got is row
    assert _sql_of(cursor).startswith(
        "EXEC dbo.CreateReviewNotification @Kind=?, @ReviewId=?, @Subject=?, @ProjectId=?, @WorkDate=?"
    )
    assert _params_of(cursor) == ["bill", 5, "Subj", None, None]


def test_repo_create_returns_none_on_empty_claim(monkeypatch):
    cursor = FakeCursor([[]])
    _patch_conn(monkeypatch, cursor)
    got = ReviewNotificationRepository().create(
        kind="contract_labor", review_id=None, subject="S", project_id=3, work_date="2026-11-19",
    )
    assert got is None


def test_repo_add_target_maps_one_document_column(monkeypatch):
    cursor = FakeCursor([[SimpleNamespace(Id=1)]])
    _patch_conn(monkeypatch, cursor)
    ReviewNotificationRepository().add_target(
        review_notification_id=9, bill_id=None, expense_id=None,
        bill_credit_id=None, contract_labor_id=77,
    )
    assert _sql_of(cursor).startswith(
        "EXEC dbo.AddReviewNotificationTarget @ReviewNotificationId=?, @BillId=?, "
        "@ExpenseId=?, @BillCreditId=?, @ContractLaborId=?"
    )
    assert _params_of(cursor) == [9, None, None, None, 77]


def test_repo_set_outbox_stamp_and_delete_param_mapping(monkeypatch):
    cursor = FakeCursor([[], [], []])
    conn = _patch_conn(monkeypatch, cursor)
    repo = ReviewNotificationRepository()
    repo.set_outbox(id=3, outbox_id=44)
    assert _sql_of(cursor).startswith("EXEC dbo.SetReviewNotificationOutbox @Id=?, @OutboxId=?")
    assert _params_of(cursor) == [3, 44]

    repo.stamp_from_outbox(outbox_id=44, conversation_id="C", internet_message_id="<I>", draft_graph_message_id="G")
    assert _sql_of(cursor).startswith(
        "EXEC dbo.StampReviewNotificationFromOutbox @OutboxId=?, @ConversationId=?, "
        "@InternetMessageId=?, @DraftGraphMessageId=?"
    )
    assert _params_of(cursor) == [44, "C", "<I>", "G"]

    repo.delete(id=3)
    assert _sql_of(cursor).startswith("EXEC dbo.DeleteReviewNotification @Id=?")
    assert _params_of(cursor) == [3]
    assert conn.commits >= 1


def test_repo_find_for_reply_reads_both_result_sets(monkeypatch):
    notif = SimpleNamespace(Id=1, Kind="bill")
    target = SimpleNamespace(Id=8, ReviewNotificationId=1, BillId=5, ExpenseId=None,
                             BillCreditId=None, ContractLaborId=None)
    cursor = FakeCursor([[notif], [target]])
    _patch_conn(monkeypatch, cursor)
    notifs, targets = ReviewNotificationRepository().find_for_reply(conversation_id="Conv-A")
    assert notifs == [notif]
    assert targets == [target]
    assert _sql_of(cursor).startswith("EXEC dbo.FindReviewNotificationForReply @ConversationId=?")
    assert _params_of(cursor) == ["Conv-A"]


# ---------------------------------------------------------------------------
# Record service, with a fake repo
# ---------------------------------------------------------------------------
class FakeRepo:
    def __init__(self, create_row=None, raise_on=None):
        self.create_row = create_row
        self.raise_on = raise_on or {}
        self.calls: list[tuple[str, dict]] = []
        self.lookup_rows = ([], [])

    def _maybe_raise(self, name):
        if name in self.raise_on:
            raise self.raise_on[name]

    def create(self, **kw):
        self.calls.append(("create", kw))
        self._maybe_raise("create")
        return self.create_row

    def add_target(self, **kw):
        self.calls.append(("add_target", kw))
        self._maybe_raise("add_target")
        return SimpleNamespace(Id=1)

    def set_outbox(self, **kw):
        self.calls.append(("set_outbox", kw))

    def stamp_from_outbox(self, **kw):
        self.calls.append(("stamp", kw))
        self._maybe_raise("stamp")

    def delete(self, **kw):
        self.calls.append(("delete", kw))

    def find_for_reply(self, **kw):
        self.calls.append(("find", kw))
        return self.lookup_rows


def test_record_for_review_creates_then_adds_target():
    repo = FakeRepo(create_row=SimpleNamespace(Id=12))
    svc = rns.ReviewNotificationRecordService(repo=repo)
    row = svc.record_for_review(kind="expense", review_id=5, targets=[("expense", 31)], subject="S")
    assert row.Id == 12
    assert repo.calls[0] == ("create", {"kind": "expense", "review_id": 5, "subject": "S",
                                        "project_id": None, "work_date": None})
    assert repo.calls[1] == ("add_target", {"review_notification_id": 12, "bill_id": None,
                                            "expense_id": 31, "bill_credit_id": None,
                                            "contract_labor_id": None})


def test_attach_outbox_and_release_delegate_to_repo():
    repo = FakeRepo()
    svc = rns.ReviewNotificationRecordService(repo=repo)
    svc.attach_outbox(12, 90)
    svc.release(12)
    assert repo.calls == [("set_outbox", {"id": 12, "outbox_id": 90}), ("delete", {"id": 12})]


def test_claim_for_crew_adds_one_target_per_crew_member():
    repo = FakeRepo(create_row=SimpleNamespace(Id=20))
    svc = rns.ReviewNotificationRecordService(repo=repo)
    row = svc.claim_for_crew(project_id=2, work_date="2026-11-19", subject="S", contract_labor_ids=[7, 8])
    assert row.Id == 20
    assert repo.calls[0] == ("create", {"kind": "contract_labor", "review_id": None, "subject": "S",
                                        "project_id": 2, "work_date": "2026-11-19"})
    targets = [kw for name, kw in repo.calls if name == "add_target"]
    assert [t["contract_labor_id"] for t in targets] == [7, 8]
    assert all(t["bill_id"] is None and t["expense_id"] is None for t in targets)


def test_claim_for_crew_returns_none_and_adds_nothing_when_already_claimed():
    repo = FakeRepo(create_row=None)
    svc = rns.ReviewNotificationRecordService(repo=repo)
    assert svc.claim_for_crew(project_id=2, work_date="2026-11-19", subject="S",
                              contract_labor_ids=[7]) is None
    assert not [c for c in repo.calls if c[0] == "add_target"]


def test_claim_for_crew_releases_the_claim_if_a_target_write_fails():
    repo = FakeRepo(create_row=SimpleNamespace(Id=20), raise_on={"add_target": RuntimeError("boom")})
    svc = rns.ReviewNotificationRecordService(repo=repo)
    with pytest.raises(RuntimeError):
        svc.claim_for_crew(project_id=2, work_date="2026-11-19", subject="S", contract_labor_ids=[7])
    assert ("delete", {"id": 20}) in repo.calls


def test_stamp_from_outbox_never_raises():
    repo = FakeRepo(raise_on={"stamp": RuntimeError("db down")})
    svc = rns.ReviewNotificationRecordService(repo=repo)
    # U-797c2a: stamps report success as a bool; a failure is False and never raises.
    assert svc.stamp_from_outbox(outbox_id=44, conversation_id="C", internet_message_id=None,
                                 draft_graph_message_id="G") is False


def test_stamp_from_outbox_passes_values_through():
    repo = FakeRepo()
    svc = rns.ReviewNotificationRecordService(repo=repo)
    svc.stamp_from_outbox(outbox_id=44, conversation_id="C", internet_message_id="<I>",
                          draft_graph_message_id="G")
    assert repo.calls == [("stamp", {"outbox_id": 44, "conversation_id": "C",
                                     "internet_message_id": "<I>", "draft_graph_message_id": "G"})]


def test_lookup_for_reply_groups_targets_by_notification():
    notif_a = SimpleNamespace(Id=1, Kind="bill")
    notif_b = SimpleNamespace(Id=2, Kind="contract_labor")
    targets = [
        SimpleNamespace(ReviewNotificationId=1, BillId=5, ExpenseId=None, BillCreditId=None, ContractLaborId=None),
        SimpleNamespace(ReviewNotificationId=2, BillId=None, ExpenseId=None, BillCreditId=None, ContractLaborId=7),
        SimpleNamespace(ReviewNotificationId=2, BillId=None, ExpenseId=None, BillCreditId=None, ContractLaborId=8),
    ]
    repo = FakeRepo()
    repo.lookup_rows = ([notif_a, notif_b], targets)
    svc = rns.ReviewNotificationRecordService(repo=repo)
    got = svc.lookup_for_reply("Conv-A")
    assert got == [(notif_a, {("bill", 5)}), (notif_b, {("contract_labor", 7), ("contract_labor", 8)})]


def test_lookup_for_reply_blank_conversation_skips_the_database():
    repo = FakeRepo()
    svc = rns.ReviewNotificationRecordService(repo=repo)
    assert svc.lookup_for_reply(None) == []
    assert svc.lookup_for_reply("   ") == []
    assert repo.calls == []


def test_targets_as_pairs_helper():
    t = SimpleNamespace(ReviewNotificationId=3, BillId=None, ExpenseId=9, BillCreditId=None, ContractLaborId=None)
    assert rns.target_pairs([t], 3) == {("expense", 9)}
    assert rns.target_pairs([t], 4) == set()


class OutboxModelRepo:
    """In-memory model of the fill rules in SetReviewNotificationOutbox and
    StampReviewNotificationFromOutbox (dbo.ReviewNotification). Models the
    sprocs' semantics; the SQL itself is checked in test_u797c1_sql.py."""

    def __init__(self, payload):
        self.payload = payload  # ms.Outbox.Payload as the worker leaves it
        self.row = {"OutboxId": None, "ConversationId": None,
                    "InternetMessageId": None, "DraftGraphMessageId": None}

    def _fill(self, values):
        for key, value in values.items():
            if self.row[key] is None and value is not None:
                self.row[key] = value

    def set_outbox(self, *, id, outbox_id):
        self.row["OutboxId"] = outbox_id
        self._fill({
            "ConversationId": self.payload.get("conversation_id"),
            "InternetMessageId": self.payload.get("internet_message_id"),
            "DraftGraphMessageId": self.payload.get("graph_message_id"),
        })

    def stamp_from_outbox(self, *, outbox_id, conversation_id, internet_message_id, draft_graph_message_id):
        if self.row["OutboxId"] != outbox_id:
            return  # no row carries this OutboxId yet: a no-op
        self._fill({
            "ConversationId": conversation_id,
            "InternetMessageId": internet_message_id,
            "DraftGraphMessageId": draft_graph_message_id,
        })

    def delete(self, **kw):
        pass


FULL_PAYLOAD = {"conversation_id": "C1", "internet_message_id": "<I1>", "graph_message_id": "G1"}
EXPECTED_ROW = {"OutboxId": 44, "ConversationId": "C1", "InternetMessageId": "<I1>",
                "DraftGraphMessageId": "G1"}


def test_both_orders_converge_attach_before_stamp():
    # Worker persists the payload after create_draft, then stamps. Attach ran first, while the
    # payload was still empty: the row gets OutboxId only, and the stamp that follows fills it.
    repo = OutboxModelRepo(payload={})
    svc = rns.ReviewNotificationRecordService(repo=repo)
    svc.attach_outbox(12, 44)
    repo.payload = dict(FULL_PAYLOAD)
    svc.stamp_from_outbox(outbox_id=44, conversation_id="C1",
                          internet_message_id="<I1>", draft_graph_message_id="G1")
    assert repo.row == EXPECTED_ROW


def test_both_orders_converge_stamp_before_attach():
    # Stamp ran before attach. It finds no row for OutboxId and does nothing. Attach then reads the
    # payload the worker already persisted and fills the identity.
    repo = OutboxModelRepo(payload=dict(FULL_PAYLOAD))
    svc = rns.ReviewNotificationRecordService(repo=repo)
    svc.stamp_from_outbox(outbox_id=44, conversation_id="C1",
                          internet_message_id="<I1>", draft_graph_message_id="G1")
    assert repo.row["ConversationId"] is None  # the stamp could not land yet
    svc.attach_outbox(12, 44)
    assert repo.row == EXPECTED_ROW
