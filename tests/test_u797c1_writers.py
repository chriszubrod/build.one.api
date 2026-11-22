"""U-797c1: stage-1 writers (Bill/Expense record, CL claim + release) and the worker stamp."""
from __future__ import annotations

import inspect
from datetime import date
from types import SimpleNamespace

import pytest

from entities.review.business import cl_notification_service as cl_mod
from entities.review.business import notification_service as ns_mod
from entities.review.business import review_notification_service as rns
from entities.review.business.review_email_composer import ReviewEmailComposer
from entities.contract_labor.business import service as cl_service_mod
from integrations.ms.outbox.business import worker as worker_mod


class FakeRecordService:
    """Stands in for ReviewNotificationRecordService in every writer."""

    log: list = []
    fail_on: set = set()
    claim_result = SimpleNamespace(Id=9)

    def __init__(self, repo=None):
        pass

    def _rec(self, name, *args, **kw):
        FakeRecordService.log.append((name, args, kw))
        if name in FakeRecordService.fail_on:
            raise RuntimeError(f"{name} failed")

    def record_for_review(self, **kw):
        self._rec("record_for_review", **kw)
        return SimpleNamespace(Id=12)

    def claim_for_crew(self, **kw):
        self._rec("claim_for_crew", **kw)
        return FakeRecordService.claim_result

    def attach_outbox(self, id, outbox_id):
        self._rec("attach_outbox", id, outbox_id)

    def release(self, id):
        self._rec("release", id)

    def stamp_from_outbox(self, **kw):
        self._rec("stamp_from_outbox", **kw)

    # U-797c2a: the writers call the never-raise try_* wrappers; these mirror the real ones.
    def try_record_for_review(self, context="", **kw):
        try:
            return self.record_for_review(**kw)
        except Exception:
            return None

    def try_claim_for_crew(self, context="", **kw):
        try:
            return self.claim_for_crew(**kw)
        except Exception:
            return None

    def try_attach_outbox(self, id, outbox_id, context=""):
        try:
            self.attach_outbox(id, outbox_id)
            return True
        except Exception:
            return False

    def try_release(self, id, context=""):
        try:
            self.release(id)
            return True
        except Exception:
            return False


def _reset(monkeypatch, fail_on=(), claim_result=SimpleNamespace(Id=9)):
    monkeypatch.setenv("ALLOW_MS_WRITES", "true")  # the record is written only when writes are on
    FakeRecordService.log = []
    FakeRecordService.fail_on = set(fail_on)
    FakeRecordService.claim_result = claim_result
    monkeypatch.setattr(rns, "ReviewNotificationRecordService", FakeRecordService)
    return FakeRecordService


def _names(log):
    return [entry[0] for entry in log]


# ---------------------------------------------------------------------------
# Bill / Expense writer
# ---------------------------------------------------------------------------
class _Outbox:
    """Records enqueues into the shared FakeRecordService.log so order is observable.
    `result` is the row returned (None = refused). `raises` makes the enqueue raise."""

    def __init__(self, result=SimpleNamespace(id=90), raises=None):
        self.calls = []
        self.result = result
        self.raises = raises

    def enqueue_send_mail(self, **kw):
        self.calls.append(kw)
        FakeRecordService.log.append(("enqueue", (), kw))
        if self.raises is not None:
            raise self.raises
        return self.result


def _bill_mail(**over):
    mail = dict(entity_type="Bill", entity_public_id="bp", to_addresses=[], cc_addresses=[],
                bcc_addresses=[], subject="[Review] V — Bill #1", body="<p>b</p>",
                body_type="HTML", attachment=None, mode="draft", review_id=5, bill_id=31)
    mail.update(over)
    return mail


_BILL_RECORD = dict(kind="bill", review_id=5, targets=[("bill", 31)], subject="[Review] V — Bill #1")


def test_bill_expense_record_is_written_before_the_enqueue_and_attached_after(monkeypatch):
    _reset(monkeypatch)
    outbox = _Outbox()
    row = ReviewEmailComposer.record_then_send(
        outbox=outbox, records_factory=FakeRecordService, record_kwargs=_BILL_RECORD, mail=_bill_mail())
    assert row is outbox.result
    assert _names(FakeRecordService.log) == ["record_for_review", "enqueue", "attach_outbox"]
    assert FakeRecordService.log[0][2] == _BILL_RECORD
    assert outbox.calls[0]["review_notification_id"] == 12
    assert FakeRecordService.log[2][1] == (12, 90)


def test_expense_record_uses_expense_kind(monkeypatch):
    _reset(monkeypatch)
    record = dict(kind="expense", review_id=6, targets=[("expense", 32)], subject="S")
    ReviewEmailComposer.record_then_send(
        outbox=_Outbox(), records_factory=FakeRecordService, record_kwargs=record,
        mail=_bill_mail(entity_type="Expense", bill_id=None))
    assert FakeRecordService.log[0][2]["kind"] == "expense"
    assert FakeRecordService.log[0][2]["targets"] == [("expense", 32)]


def test_bill_expense_record_failure_never_raises_and_enqueues_without_the_key(monkeypatch):
    _reset(monkeypatch, fail_on={"record_for_review"})
    outbox = _Outbox()
    # Must not raise, and the email enqueues exactly as before U-797c2a: no key, no attach.
    row = ReviewEmailComposer.record_then_send(
        outbox=outbox, records_factory=FakeRecordService, record_kwargs=_BILL_RECORD, mail=_bill_mail())
    assert row is outbox.result
    assert "review_notification_id" not in outbox.calls[0]
    assert outbox.calls[0] == _bill_mail()
    assert "attach_outbox" not in _names(FakeRecordService.log)
    assert "release" not in _names(FakeRecordService.log)


def test_refused_enqueue_releases_the_record(monkeypatch):
    _reset(monkeypatch)
    row = ReviewEmailComposer.record_then_send(
        outbox=_Outbox(result=None), records_factory=FakeRecordService,
        record_kwargs=_BILL_RECORD, mail=_bill_mail())
    assert row is None
    assert ("release", (12,), {}) in FakeRecordService.log


def test_raised_enqueue_releases_the_record_and_reraises(monkeypatch):
    _reset(monkeypatch)
    with pytest.raises(RuntimeError):
        ReviewEmailComposer.record_then_send(
            outbox=_Outbox(raises=RuntimeError("enqueue boom")), records_factory=FakeRecordService,
            record_kwargs=_BILL_RECORD, mail=_bill_mail())
    assert ("release", (12,), {}) in FakeRecordService.log


def test_bill_expense_record_is_written_before_the_enqueue_in_the_composer_source():
    src = inspect.getsource(ReviewEmailComposer.record_then_send)
    assert src.index("try_record_for_review(") < src.index("send_with_record(")


# ---------------------------------------------------------------------------
# Contract Labor writer: keeps the legacy claim, adds the crew claim
# ---------------------------------------------------------------------------
def _cl_service(monkeypatch, *, legacy_claim=True, enqueue_result="row", record_log=None):
    svc = cl_mod.ContractLaborReviewNotificationService()
    lines = [
        SimpleNamespace(ProjectId=1, ContractLaborId=7, ProjectName="Proj", ProjectAbbreviation="PJ"),
        SimpleNamespace(ProjectId=1, ContractLaborId=8, ProjectName="Proj", ProjectAbbreviation="PJ"),
    ]
    calls = record_log if record_log is not None else []
    monkeypatch.setattr(svc, "_count_pending_for_date", lambda work_date: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda work_date: lines)
    monkeypatch.setattr(svc, "_claim_notification",
                        lambda **kw: (calls.append(("legacy_claim", kw)) or legacy_claim))
    monkeypatch.setattr(svc, "_release_notification_claim",
                        lambda **kw: calls.append(("legacy_release", kw)))
    monkeypatch.setattr(svc, "_fetch_recipients", lambda cl_id: {1: {"pms": [], "owners": []}})
    monkeypatch.setattr(svc, "_build_recipient_addresses", lambda people: [])
    monkeypatch.setattr(svc, "_build_body", lambda **kw: "<p>b</p>")
    monkeypatch.setattr(svc, "_build_bcc_addresses", lambda: [])
    monkeypatch.setattr(svc, "_format_project_label", lambda meta, pid: "PJ")

    class FakeOutbox:
        def enqueue_send_mail(self, **kw):
            calls.append(("enqueue", kw))
            return SimpleNamespace(id=90) if enqueue_result == "row" else None

    monkeypatch.setattr(cl_mod, "MsOutboxService", FakeOutbox)
    return svc, calls


CL = SimpleNamespace(public_id="cp", work_date=date(2026, 11, 19))


def test_cl_success_keeps_legacy_claim_adds_crew_claim_and_attaches(monkeypatch):
    _reset(monkeypatch)
    svc, calls = _cl_service(monkeypatch)
    svc.enqueue_drafts(contract_labor=CL)
    kinds = [c[0] for c in calls]
    assert kinds == ["legacy_claim", "enqueue"]  # legacy claim kept, no release on success
    claim = [e for e in FakeRecordService.log if e[0] == "claim_for_crew"][0][2]
    assert claim["project_id"] == 1
    assert claim["work_date"] == date(2026, 11, 19)
    assert claim["contract_labor_ids"] == [7, 8]
    attach = [e for e in FakeRecordService.log if e[0] == "attach_outbox"][0]
    assert attach[1] == (9, 90)
    assert "release" not in _names(FakeRecordService.log)


def test_cl_enqueue_failure_releases_both_claims(monkeypatch):
    _reset(monkeypatch)
    svc, calls = _cl_service(monkeypatch, enqueue_result=None)
    svc.enqueue_drafts(contract_labor=CL)
    assert "legacy_release" in [c[0] for c in calls]
    assert ("release", (9,), {}) in FakeRecordService.log


def test_cl_crew_claim_failure_keeps_legacy_flow_and_still_enqueues(monkeypatch):
    _reset(monkeypatch, fail_on={"claim_for_crew"})
    svc, calls = _cl_service(monkeypatch)
    svc.enqueue_drafts(contract_labor=CL)
    assert "enqueue" in [c[0] for c in calls]
    assert "legacy_release" not in [c[0] for c in calls]


def test_cl_already_claimed_skips_crew_claim(monkeypatch):
    _reset(monkeypatch)
    svc, calls = _cl_service(monkeypatch, legacy_claim=False)
    svc.enqueue_drafts(contract_labor=CL)
    assert "enqueue" not in [c[0] for c in calls]
    assert "claim_for_crew" not in _names(FakeRecordService.log)


def test_cl_crew_claim_none_still_enqueues_without_attach(monkeypatch):
    _reset(monkeypatch, claim_result=None)
    svc, calls = _cl_service(monkeypatch)
    svc.enqueue_drafts(contract_labor=CL)
    assert "enqueue" in [c[0] for c in calls]
    assert "attach_outbox" not in _names(FakeRecordService.log)


# ---------------------------------------------------------------------------
# Worker stamp (U-579 area): called with the outbox id, never fails the row
# ---------------------------------------------------------------------------
def _worker(monkeypatch, draft_result):
    from integrations.ms.mail.external import client as mail_client

    monkeypatch.setattr(mail_client, "create_draft", lambda **kw: draft_result)
    w = worker_mod.MsOutboxWorker.__new__(worker_mod.MsOutboxWorker)
    monkeypatch.setattr(w, "_raise_if_external_error", lambda row, result: None)
    monkeypatch.setattr(w, "_persist_payload", lambda row, payload: True)
    return w


_PAYLOAD = {
    "to_addresses": [{"email": "a@x.test", "name": "A"}],
    "subject": "S",
    "body": "<p>b</p>",
    "mode": "draft",
    "review_id": 3,
}


def test_worker_stamps_review_notification_with_outbox_id(monkeypatch):
    _reset(monkeypatch)
    draft = {"draft": {"message_id": "G1", "conversation_id": "C1", "internet_message_id": "<I1>"}}
    w = _worker(monkeypatch, draft)
    w._handle_send_mail(SimpleNamespace(id=7, public_id="P", entity_type="Bill"), dict(_PAYLOAD))
    stamp = [e for e in FakeRecordService.log if e[0] == "stamp_from_outbox"]
    assert len(stamp) == 1
    assert stamp[0][2] == {"outbox_id": 7, "conversation_id": "C1",
                           "internet_message_id": "<I1>", "draft_graph_message_id": "G1"}


def test_worker_stamp_failure_is_swallowed(monkeypatch):
    _reset(monkeypatch, fail_on={"stamp_from_outbox"})
    draft = {"draft": {"message_id": "G1", "conversation_id": "C1", "internet_message_id": "<I1>"}}
    w = _worker(monkeypatch, draft)
    # The send_mail row must still complete: no exception escapes the handler.
    w._handle_send_mail(SimpleNamespace(id=7, public_id="P", entity_type="Bill"), dict(_PAYLOAD))


def test_worker_stamp_constructor_failure_is_swallowed(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise ImportError("no module")

    monkeypatch.setattr(rns, "ReviewNotificationRecordService", Boom)
    draft = {"draft": {"message_id": "G1", "conversation_id": "C1", "internet_message_id": "<I1>"}}
    w = _worker(monkeypatch, draft)
    w._handle_send_mail(SimpleNamespace(id=7, public_id="P", entity_type="Bill"), dict(_PAYLOAD))


def test_worker_has_no_stamp_when_graph_returns_no_message_id(monkeypatch):
    _reset(monkeypatch)
    w = _worker(monkeypatch, {"draft": {}})
    w._handle_send_mail(SimpleNamespace(id=7, public_id="P", entity_type="Bill"), dict(_PAYLOAD))
    assert "stamp_from_outbox" not in _names(FakeRecordService.log)


def test_cl_release_removes_the_record_before_the_legacy_claim(monkeypatch):
    """Item 8: the new record row goes first, so a later re-claim never sees a stuck record."""
    order = []
    _reset(monkeypatch)
    monkeypatch.setattr(FakeRecordService, "release", lambda self, id: order.append("record_release"))
    svc, _calls = _cl_service(monkeypatch, enqueue_result=None)
    monkeypatch.setattr(svc, "_release_notification_claim",
                        lambda **kw: order.append("legacy_release"))
    svc.enqueue_drafts(contract_labor=CL)
    assert order == ["record_release", "legacy_release"]


def test_record_release_failure_log_does_not_claim_the_draft_will_not_retry():
    src = inspect.getsource(cl_mod.ContractLaborReviewNotificationService._release_review_notification)
    assert "will not retry" not in src
    assert "record" in src


def test_bind_cl_helper_docstring_describes_project_and_date_not_membership():
    from entities.contract_labor.business.service import ContractLaborService
    doc = ContractLaborService._bind_cl_decision_reply.__doc__ or ""
    assert "work_date" in doc
    assert "project" in doc.lower()
    assert "include this ContractLabor" not in doc

