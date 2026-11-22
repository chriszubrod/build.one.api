"""U-797c2a: record-before-enqueue, the payload key, never-raise try_* wrappers, the
worker's three-way stamp routing, and the composer's shared pieces. All fakes."""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business import cl_notification_service as cl_mod
from entities.review.business import review_notification_service as rns
from entities.review.business.notification_service import ReviewNotificationService

# Captured before any test patches the module attribute.
_REAL_RECORD_SERVICE = rns.ReviewNotificationRecordService
from entities.review.business.cl_notification_service import contract_labor_ask
from entities.review.business.notification_service import (
    BILL_EXPENSE_ARCHIVE_NAME,
    BILL_EXPENSE_ASK,
)
from entities.review.business.review_email_composer import ReviewEmailComposer
from integrations.ms.outbox.business import service as outbox_service_mod
from integrations.ms.outbox.business import worker as worker_mod
from tests.test_u754_review_notification_characterization import (
    ALL_RECIPIENTS,
    LINKS_WITH_PDF,
    MULTI_LINES,
    RECORD_SERVICE,
    _bill,
    _harness,
    _review,
)


# ---------------------------------------------------------------------------
# Bill / Expense: record before enqueue, key in payload, attach after
# ---------------------------------------------------------------------------


def _bill_run(*, record=SimpleNamespace(Id=77), enqueue_result=SimpleNamespace(id=90, public_id="ob"),
              repo_failure=None):
    """Drive the real ReviewNotificationService._do_enqueue through the U-754 harness,
    with the enqueue and the record write observed in one ordered log."""
    stack, _calls = _harness(recipients=ALL_RECIPIENTS, line_items=MULTI_LINES, links=LINKS_WITH_PDF)
    order: list = []
    with stack:
        rec = RECORD_SERVICE["instance"]
        if repo_failure is not None:
            class _Failing:
                def create(self, **kw):
                    raise repo_failure

                def delete(self, **kw):
                    raise repo_failure

                def add_target(self, **kw):
                    raise repo_failure

                def set_outbox(self, **kw):
                    raise repo_failure

            stack.enter_context(patch(
                "entities.review.business.review_notification_service.ReviewNotificationRecordService",
                side_effect=lambda: _REAL_RECORD_SERVICE(repo=_Failing()),
            ))
        else:
            rec.try_record_for_review.side_effect = lambda **kw: (
                order.append(("record", kw)), record)[1]
            rec.try_attach_outbox.side_effect = lambda id, outbox_id, **kw: (
                order.append(("attach", (id, outbox_id))), True)[1]
            rec.try_release.side_effect = lambda id, **kw: (order.append(("release", id)), True)[1]
        outbox_cls = stack.enter_context(patch("integrations.ms.outbox.business.service.MsOutboxService"))
        outbox_cls.return_value.enqueue_send_mail.side_effect = lambda **kw: (
            order.append(("enqueue", kw)), enqueue_result)[1]
        ReviewNotificationService().enqueue_for_bill(bill=_bill(), review=_review(), exclude_user_id=27)
    return order


def test_bill_record_is_written_before_the_enqueue_and_attached_after():
    order = _bill_run()
    assert [name for name, _ in order] == ["record", "enqueue", "attach"]
    assert order[0][1]["kind"] == "bill"
    assert order[0][1]["targets"] == [("bill", 55)]
    assert order[1][1]["review_notification_id"] == 77
    assert order[2][1] == (77, 90)


def test_bill_record_failure_enqueues_the_identical_email_without_the_key():
    with_record = _bill_run()
    without_record = _bill_run(record=None)
    enqueue_with = [kw for name, kw in with_record if name == "enqueue"][0]
    enqueue_without = [kw for name, kw in without_record if name == "enqueue"][0]
    assert enqueue_with.pop("review_notification_id") == 77
    assert "review_notification_id" not in enqueue_without
    assert enqueue_with == enqueue_without
    assert [name for name, _ in without_record] == ["record", "enqueue"]  # no attach without a record


def test_bill_real_record_failure_is_isolated_and_the_email_still_enqueues():
    order = _bill_run(repo_failure=RuntimeError("db down"))
    enqueue = [kw for name, kw in order if name == "enqueue"]
    assert len(enqueue) == 1
    assert "review_notification_id" not in enqueue[0]


def test_bill_refused_enqueue_releases_the_record():
    order = _bill_run(enqueue_result=None)
    assert [name for name, _ in order] == ["record", "enqueue", "release"]
    assert order[2][1] == 77


# ---------------------------------------------------------------------------
# Contract Labor: the claimed record id rides in the payload
# ---------------------------------------------------------------------------


def _cl_run(*, claim):
    rows = [SimpleNamespace(ProjectId=1, ContractLaborId=7, ProjectName="P", ProjectAbbreviation="PJ",
                            EmployeeName="A", Hours=1, IsBillable=True, IsOverhead=False, Description="d")]
    order: list = []
    svc = cl_mod.ContractLaborReviewNotificationService()
    contract_labor = SimpleNamespace(public_id="cp", work_date=date(2026, 11, 19))
    outbox = MagicMock()
    outbox.enqueue_send_mail.side_effect = lambda **kw: (order.append(("enqueue", kw)),
                                                         SimpleNamespace(id=90))[1]
    with patch.object(cl_mod.ContractLaborReviewNotificationService, "_count_pending_for_date", return_value=0), \
         patch.object(cl_mod.ContractLaborReviewNotificationService, "_read_submitted_lines_for_date",
                      return_value=rows), \
         patch.object(cl_mod.ContractLaborReviewNotificationService, "_claim_notification", return_value=True), \
         patch.object(cl_mod.ContractLaborReviewNotificationService, "_fetch_recipients",
                      return_value={1: {"name": "", "abbreviation": "PJ", "pms": [], "owners": []}}), \
         patch.object(cl_mod, "MsOutboxService", return_value=outbox), \
         patch("config.Settings", return_value=MagicMock(invoice_inbox_email="")), \
         patch("entities.review.business.review_notification_service.ReviewNotificationRecordService") as rec_cls:
        rec = rec_cls.return_value
        rec.try_claim_for_crew.side_effect = lambda **kw: (order.append(("claim", kw)), claim)[1]
        rec.try_attach_outbox.side_effect = lambda id, outbox_id, **kw: (order.append(("attach", (id, outbox_id))),
                                                                         True)[1]
        rec.try_release.side_effect = lambda id, **kw: (order.append(("release", id)), True)[1]
        svc._do_enqueue(contract_labor=contract_labor)
    return order


def test_cl_claim_is_written_before_the_enqueue_and_its_id_rides_in_the_payload():
    order = _cl_run(claim=SimpleNamespace(Id=44))
    assert [name for name, _ in order] == ["claim", "enqueue", "attach"]
    assert order[1][1]["review_notification_id"] == 44
    assert order[2][1] == (44, 90)


def test_cl_without_a_claimed_record_enqueues_without_the_key():
    order = _cl_run(claim=None)
    enqueue = [kw for name, kw in order if name == "enqueue"][0]
    assert "review_notification_id" not in enqueue
    assert [name for name, _ in order] == ["claim", "enqueue"]


# ---------------------------------------------------------------------------
# enqueue_send_mail: the key is written only when given
# ---------------------------------------------------------------------------


def _enqueue_payload(**kw):
    captured = {}
    service = outbox_service_mod.MsOutboxService.__new__(outbox_service_mod.MsOutboxService)

    def _fake_enqueue(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1)

    with patch.object(outbox_service_mod, "_resolve_tenant_id", return_value="t-1"), \
         patch.object(service, "enqueue", side_effect=_fake_enqueue):
        service.enqueue_send_mail(entity_type="Bill", entity_public_id="bp", to_addresses=[],
                                  subject="s", body="b", **kw)
    return captured["payload"]


def test_enqueue_send_mail_writes_the_key_only_when_given():
    assert "review_notification_id" not in _enqueue_payload()
    assert _enqueue_payload(review_notification_id=None).get("review_notification_id") is None
    assert "review_notification_id" not in _enqueue_payload(review_notification_id=None)
    assert _enqueue_payload(review_notification_id=77)["review_notification_id"] == 77


def test_enqueue_send_mail_payload_is_otherwise_unchanged():
    payload = _enqueue_payload(review_id=3, bill_id=4)
    assert set(payload) == {
        "to_addresses", "cc_addresses", "bcc_addresses", "subject", "body", "body_type",
        "attachment", "mode", "review_id", "bill_id", "forward_message_id", "comment_text",
        "html_preamble",
    }


# ---------------------------------------------------------------------------
# try_* wrappers never raise and log the class name only
# ---------------------------------------------------------------------------


class _BoomRepo:
    def create(self, **kw):
        raise RuntimeError("secret sql text")

    def add_target(self, **kw):
        raise RuntimeError("secret sql text")

    def set_outbox(self, **kw):
        raise RuntimeError("secret sql text")

    def delete(self, **kw):
        raise RuntimeError("secret sql text")

    def stamp_from_outbox(self, **kw):
        raise RuntimeError("secret sql text")

    def stamp_by_id(self, **kw):
        raise RuntimeError("secret sql text")


def test_try_wrappers_never_raise_and_return_none_or_false(caplog):
    svc = rns.ReviewNotificationRecordService(repo=_BoomRepo())
    with caplog.at_level(logging.ERROR):
        assert svc.try_record_for_review(kind="bill", review_id=1, targets=[("bill", 2)], subject="s") is None
        assert svc.try_claim_for_crew(project_id=1, work_date=date(2026, 1, 1), subject="s",
                                      contract_labor_ids=[7]) is None
        assert svc.try_attach_outbox(1, 2) is False
        assert svc.try_release(1) is False
        svc.stamp_from_outbox(outbox_id=1, conversation_id="c", internet_message_id=None,
                              draft_graph_message_id=None)
        svc.stamp_by_id(id=1, outbox_id=1, conversation_id="c", internet_message_id=None, draft_graph_message_id=None)
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in messages
    assert "secret sql text" not in messages


def test_stamp_review_notification_never_raises_even_if_the_service_cannot_be_built(monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            raise ImportError("no module")

    monkeypatch.setattr(rns, "ReviewNotificationRecordService", Boom)
    assert rns.stamp_review_notification(
        outbox_id=1, review_notification_id=5, conversation_id="c",
        internet_message_id=None, draft_graph_message_id="g",
    ) is False


# ---------------------------------------------------------------------------
# Worker: stamp by id, by outbox id for legacy review rows, no DB call otherwise
# ---------------------------------------------------------------------------


class _RecordingRecService:
    log: list = []
    built: int = 0
    fail: bool = False

    def __init__(self, repo=None):
        _RecordingRecService.built += 1

    def stamp_by_id(self, **kw):
        _RecordingRecService.log.append(("by_id", kw))
        if _RecordingRecService.fail:
            raise RuntimeError("boom")

    def stamp_from_outbox(self, **kw):
        _RecordingRecService.log.append(("by_outbox", kw))
        if _RecordingRecService.fail:
            raise RuntimeError("boom")


@pytest.fixture
def rec_recorder(monkeypatch):
    _RecordingRecService.log = []
    _RecordingRecService.built = 0
    _RecordingRecService.fail = False
    monkeypatch.setattr(rns, "ReviewNotificationRecordService", _RecordingRecService)
    return _RecordingRecService


def _worker_send(monkeypatch, row, payload):
    from integrations.ms.mail.external import client as mail_client

    draft = {"draft": {"message_id": "G1", "conversation_id": "C1", "internet_message_id": "<I1>"}}
    monkeypatch.setattr(mail_client, "create_draft", lambda **kw: draft)
    w = worker_mod.MsOutboxWorker.__new__(worker_mod.MsOutboxWorker)
    monkeypatch.setattr(w, "_raise_if_external_error", lambda row, result: None)
    monkeypatch.setattr(w, "_persist_payload", lambda row, payload: True)
    w._handle_send_mail(row, payload)


_BASE = {"to_addresses": [], "subject": "S", "body": "b", "mode": "draft"}


def test_worker_stamps_by_id_when_the_payload_names_the_record(monkeypatch, rec_recorder):
    _worker_send(monkeypatch, SimpleNamespace(id=7, public_id="P", entity_type="Bill"),
                 dict(_BASE, review_notification_id=77))
    assert rec_recorder.log == [("by_id", {"id": 77, "outbox_id": 7, "conversation_id": "C1",
                                            "internet_message_id": "<I1>",
                                            "draft_graph_message_id": "G1"})]


def test_worker_stamps_legacy_review_rows_by_outbox_id(monkeypatch, rec_recorder):
    _worker_send(monkeypatch, SimpleNamespace(id=8, public_id="P", entity_type="ContractLabor"), dict(_BASE))
    assert [name for name, _ in rec_recorder.log] == ["by_outbox"]
    assert rec_recorder.log[0][1]["outbox_id"] == 8


def test_worker_makes_no_db_call_for_non_review_mail(monkeypatch, rec_recorder):
    _worker_send(monkeypatch, SimpleNamespace(id=9, public_id="P", entity_type="Conversation"), dict(_BASE))
    assert rec_recorder.log == []
    assert rec_recorder.built == 0


def test_worker_stamp_failure_never_fails_the_send_mail_row(monkeypatch, rec_recorder):
    rec_recorder.fail = True
    # No exception escapes; the row is not failed or retried by the stamp.
    _worker_send(monkeypatch, SimpleNamespace(id=7, public_id="P", entity_type="Bill"),
                 dict(_BASE, review_notification_id=77))
    # Item 6: a failed by-id stamp falls back to the by-outbox-id stamp; neither escapes.
    assert [name for name, _ in rec_recorder.log] == ["by_id", "by_outbox"]


# ---------------------------------------------------------------------------
# Composer: the shared pieces
# ---------------------------------------------------------------------------


def test_composer_subject_prefix_and_archive_names_are_preserved():
    assert ReviewEmailComposer.subject("X") == "[Review] X"
    assert BILL_EXPENSE_ARCHIVE_NAME == "Invoice Inbox (archive)"
    assert ReviewEmailComposer.archive_bcc("a@x.test", BILL_EXPENSE_ARCHIVE_NAME) == [
        {"email": "a@x.test", "name": "Invoice Inbox (archive)"}]
    assert ReviewEmailComposer.archive_bcc("a@x.test", None) == [{"email": "a@x.test", "name": None}]


def test_composer_greeting_dedupe_is_per_type():
    names = ["Cassidy", "Zach", "Cassidy", "Zoë"]
    assert ReviewEmailComposer.greeting(names, dedupe=True) == "<p>Cassidy/Zach/Zoë,</p>"
    assert ReviewEmailComposer.greeting(names, dedupe=False) == "<p>Cassidy/Zach/Cassidy/Zoë,</p>"
    assert ReviewEmailComposer.greeting([], dedupe=True) == ""
    assert ReviewEmailComposer.greeting(["<b>"], dedupe=False) == "<p>&lt;b&gt;,</p>"


def test_composer_ask_wording_is_preserved():
    assert BILL_EXPENSE_ASK == (
        "<p>When you have a moment, will you please reply for approval "
        "with Sub Cost Code and Description, or non-approval?</p>")
    ask = contract_labor_ask("A & B", "2026-11-19")
    assert ask.startswith("<p>The following Contract Labor for A &amp; B on 2026-11-19 has been submitted for review.")
    assert ask.endswith("the full crew listed below.</p>")


def test_composer_address_shape():
    assert ReviewEmailComposer.address("a@x.test", "A B") == {"email": "a@x.test", "name": "A B"}
