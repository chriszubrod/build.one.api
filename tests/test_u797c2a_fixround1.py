"""U-797c2a fix round 1: regression tests, written before the fixes.

1. worker stamp step: import and call failures never escape _handle_send_mail.
2. lazy record service: a constructor failure still enqueues the identical email.
3. writes off: no record is created or released; the email goes without the key.
4. log context: try_* logs carry the identifier and the exception class name only.
5. by-id stamp is owned: only fills rows that are unattached or attached to this outbox row.
6. fallback: a failed by-id stamp falls back to the by-outbox-id stamp.
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business import cl_notification_service as cl_mod
from entities.review.business import review_notification_service as rns
from entities.review.business.notification_service import ReviewNotificationService
from entities.review.business.review_email_composer import ReviewEmailComposer
from integrations.ms.outbox.business import service as outbox_service_mod
from integrations.ms.outbox.business import worker as worker_mod
from tests.test_u754_review_notification_characterization import (
    ALL_RECIPIENTS,
    LINKS_WITH_PDF,
    MULTI_LINES,
    _bill,
    _harness,
    _review,
)


class _BoomRepo:
    def __getattr__(self, name):
        def _raise(**kw):
            raise RuntimeError("secret sql text")
        return _raise


# ---------------------------------------------------------------------------
# 1. worker guard
# ---------------------------------------------------------------------------

def _worker_send(monkeypatch, row, payload):
    from integrations.ms.mail.external import client as mail_client

    draft = {"draft": {"message_id": "G1", "conversation_id": "C1", "internet_message_id": "<I1>"}}
    monkeypatch.setattr(mail_client, "create_draft", lambda **kw: draft)
    w = worker_mod.MsOutboxWorker.__new__(worker_mod.MsOutboxWorker)
    monkeypatch.setattr(w, "_raise_if_external_error", lambda row, result: None)
    monkeypatch.setattr(w, "_persist_payload", lambda row, payload: True)
    w._handle_send_mail(row, payload)


_REVIEW_ROW = SimpleNamespace(id=7, public_id="P", entity_type="Bill")
_REVIEW_PAYLOAD = {"to_addresses": [], "subject": "S", "body": "b", "mode": "draft",
                   "review_notification_id": 77}


def test_worker_stamp_call_failure_never_escapes_the_send_mail_handler(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("stamp exploded")

    monkeypatch.setattr(rns, "stamp_review_notification", _boom)
    _worker_send(monkeypatch, _REVIEW_ROW, dict(_REVIEW_PAYLOAD))


def test_worker_stamp_import_failure_never_escapes_the_send_mail_handler(monkeypatch):
    # A failing import of the stamp module must not fail the row.
    monkeypatch.setitem(sys.modules, "entities.review.business.review_notification_service", None)
    _worker_send(monkeypatch, _REVIEW_ROW, dict(_REVIEW_PAYLOAD))


# ---------------------------------------------------------------------------
# 2. lazy record service: constructor failure still enqueues the identical email
# ---------------------------------------------------------------------------

def _bill_enqueue(record_constructor=None):
    stack, _calls = _harness(recipients=ALL_RECIPIENTS, line_items=MULTI_LINES, links=LINKS_WITH_PDF)
    sent = []
    with stack:
        if record_constructor is not None:
            stack.enter_context(patch.object(rns, "ReviewNotificationRecordService",
                                             side_effect=record_constructor))
        outbox_cls = stack.enter_context(patch("integrations.ms.outbox.business.service.MsOutboxService"))
        outbox_cls.return_value.enqueue_send_mail.side_effect = lambda **kw: (
            sent.append(kw), SimpleNamespace(id=90, public_id="ob"))[1]
        ReviewNotificationService().enqueue_for_bill(bill=_bill(), review=_review(), exclude_user_id=27)
    return sent


def test_bill_record_constructor_failure_still_enqueues_the_identical_email():
    with_record = _bill_enqueue()
    failing = _bill_enqueue(record_constructor=ImportError("no record service"))
    assert len(failing) == 1
    assert "review_notification_id" not in failing[0]
    expected = {k: v for k, v in with_record[0].items() if k != "review_notification_id"}
    assert failing[0] == expected


def _cl_enqueue(record_constructor=None):
    rows = [SimpleNamespace(ProjectId=1, ContractLaborId=7, ProjectName="P", ProjectAbbreviation="PJ",
                            EmployeeName="A", Hours=1, IsBillable=True, IsOverhead=False, Description="d")]
    sent = []
    svc = cl_mod.ContractLaborReviewNotificationService()
    contract_labor = SimpleNamespace(public_id="cp", work_date=date(2026, 11, 19))
    outbox = MagicMock()
    outbox.enqueue_send_mail.side_effect = lambda **kw: (sent.append(kw), SimpleNamespace(id=90))[1]
    record_patch = patch.object(rns, "ReviewNotificationRecordService",
                                side_effect=record_constructor or (lambda *a, **k: MagicMock()))
    with patch.object(cl_mod.ContractLaborReviewNotificationService, "_count_pending_for_date", return_value=0), \
         patch.object(cl_mod.ContractLaborReviewNotificationService, "_read_submitted_lines_for_date",
                      return_value=rows), \
         patch.object(cl_mod.ContractLaborReviewNotificationService, "_claim_notification", return_value=True), \
         patch.object(cl_mod.ContractLaborReviewNotificationService, "_fetch_recipients",
                      return_value={1: {"name": "", "abbreviation": "PJ", "pms": [], "owners": []}}), \
         patch.object(cl_mod, "MsOutboxService", return_value=outbox), \
         patch("config.Settings", return_value=MagicMock(invoice_inbox_email="")), \
         record_patch:
        svc._do_enqueue(contract_labor=contract_labor)
    return sent


def test_cl_record_constructor_failure_still_enqueues_without_the_key():
    failing = _cl_enqueue(record_constructor=ImportError("no record service"))
    assert len(failing) == 1
    assert "review_notification_id" not in failing[0]


# ---------------------------------------------------------------------------
# 3. writes off: no record create or release
# ---------------------------------------------------------------------------

def test_writes_off_creates_no_record_and_sends_without_the_key():
    factory = MagicMock(name="records_factory")
    outbox = MagicMock()
    outbox.enqueue_send_mail.return_value = None
    mail = dict(entity_type="Bill", entity_public_id="bp", to_addresses=[], cc_addresses=[],
                bcc_addresses=[], subject="S", body="b", body_type="HTML", attachment=None,
                mode="draft", review_id=5, bill_id=31)
    with patch.object(outbox_service_mod, "writes_allowed", return_value=False):
        row = ReviewEmailComposer.record_then_send(
            outbox=outbox, records_factory=factory,
            record_kwargs=dict(kind="bill", review_id=5, targets=[("bill", 31)], subject="S"),
            mail=mail, context="kind=bill bill_public_id=bp")
    factory.assert_not_called()
    outbox.enqueue_send_mail.assert_called_once_with(**mail)
    assert row is None


# ---------------------------------------------------------------------------
# 4. log context
# ---------------------------------------------------------------------------

def _log_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == rns.logger.name]


def test_try_record_failure_log_carries_the_document_context_and_class_only(caplog):
    svc = rns.ReviewNotificationRecordService(repo=_BoomRepo())
    with caplog.at_level(logging.ERROR, logger=rns.logger.name):
        svc.try_record_for_review(kind="bill", review_id=1, targets=[("bill", 2)], subject="s",
                                  context="kind=bill bill_public_id=bp-1")
    message = _log_messages(caplog)[-1]
    assert "context=kind=bill bill_public_id=bp-1" in message
    assert "error=RuntimeError" in message
    assert "secret" not in message


def test_try_claim_failure_log_carries_project_and_work_date(caplog):
    svc = rns.ReviewNotificationRecordService(repo=_BoomRepo())
    with caplog.at_level(logging.ERROR, logger=rns.logger.name):
        svc.try_claim_for_crew(project_id=3, work_date=date(2026, 11, 19), subject="s",
                               contract_labor_ids=[7], context="project_id=3 work_date=2026-11-19")
    message = _log_messages(caplog)[-1]
    assert "project_id=3 work_date=2026-11-19" in message
    assert "error=RuntimeError" in message


def test_try_attach_and_release_failures_carry_context(caplog):
    svc = rns.ReviewNotificationRecordService(repo=_BoomRepo())
    with caplog.at_level(logging.ERROR, logger=rns.logger.name):
        svc.try_attach_outbox(1, 2, context="kind=expense expense_public_id=ep-9")
        svc.try_release(1, context="kind=expense expense_public_id=ep-9")
    messages = _log_messages(caplog)
    assert sum("context=kind=expense expense_public_id=ep-9" in m for m in messages) == 2


# ---------------------------------------------------------------------------
# 5. by-id stamp ownership (repo param + sproc static)
# ---------------------------------------------------------------------------

def test_repo_stamp_by_id_passes_the_outbox_id_for_the_ownership_check():
    from entities.review.persistence import review_notification_repo as repo_mod

    calls = []

    class _Conn:
        def cursor(self):
            return self

        def commit(self):
            pass

    from contextlib import contextmanager

    @contextmanager
    def fake_connection():
        yield _Conn()

    with patch.object(repo_mod, "get_connection", fake_connection), \
         patch.object(repo_mod, "call_procedure",
                      side_effect=lambda cursor, name, params: calls.append((name, params))):
        repo_mod.ReviewNotificationRepository().stamp_by_id(
            id=77, outbox_id=8, conversation_id="C", internet_message_id=None, draft_graph_message_id="G")
    assert calls[0][1]["OutboxId"] == 8


def test_stamp_by_id_sproc_only_updates_unattached_or_own_outbox_rows():
    import re
    from tests.sproc_text import REPO_ROOT

    text = (REPO_ROOT / "entities" / "review" / "sql" / "dbo.review_notification.sql").read_text(encoding="utf-8")
    batch = [b for b in re.split(r"^\s*GO\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
             if "CREATE OR ALTER PROCEDURE dbo.StampReviewNotificationById" in b][0]
    assert "@OutboxId" in batch
    assert "[OutboxId] IS NULL OR [OutboxId] = @OutboxId" in batch


# ---------------------------------------------------------------------------
# 6. fallback from a failed by-id stamp to the by-outbox-id stamp
# ---------------------------------------------------------------------------

def _stamp_with_repo(monkeypatch, repo, review_notification_id=77):
    svc = rns.ReviewNotificationRecordService(repo=repo)
    monkeypatch.setattr(rns, "ReviewNotificationRecordService", lambda *a, **k: svc)
    return rns.stamp_review_notification(
        outbox_id=8, review_notification_id=review_notification_id, conversation_id="C",
        internet_message_id=None, draft_graph_message_id="G")


def test_by_id_failure_falls_back_to_the_outbox_stamp(monkeypatch):
    repo = MagicMock()
    repo.stamp_by_id.side_effect = RuntimeError("db")
    assert _stamp_with_repo(monkeypatch, repo) is True
    repo.stamp_from_outbox.assert_called_once()
    assert repo.stamp_from_outbox.call_args.kwargs["outbox_id"] == 8


def test_by_id_success_does_not_fall_back_and_passes_the_outbox_id(monkeypatch):
    repo = MagicMock()
    assert _stamp_with_repo(monkeypatch, repo) is True
    repo.stamp_from_outbox.assert_not_called()
    assert repo.stamp_by_id.call_args.kwargs["outbox_id"] == 8


def test_both_stamps_failing_returns_false(monkeypatch):
    repo = MagicMock()
    repo.stamp_by_id.side_effect = RuntimeError("db")
    repo.stamp_from_outbox.side_effect = RuntimeError("db")
    assert _stamp_with_repo(monkeypatch, repo) is False
