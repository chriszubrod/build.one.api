"""U-798 — the CL review email subject gains a "[Review] " prefix.

New outbound subject: `[Review] Contract Labor - {ProjectLabel} - {YYYY-MM-DD}`.
Threads already sent with the legacy `Contract Labor - ...` subject must still bind.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from entities.contract_labor.business.service import ContractLaborService
from tests.test_cl_review_notification_consolidation import (
    _cl_trigger,
    _notification_svc_with_patches,
    _submitted_line,
)
from tests.test_sproc_single_source import _sproc_body
from tests.test_u563_reviewer_reply_provenance import CONV, _cl_anchor, _outbound

_CONTRACT_LABOR_SQL = (
    Path(__file__).resolve().parents[1]
    / "entities" / "contract_labor" / "sql" / "dbo.contract_labor.sql"
)


# --- built subject (real enqueue path, fakes as in test_cl_review_notification_consolidation) ---


def test_built_subject_is_review_prefixed_contract_labor_project_and_date(monkeypatch):
    svc = _notification_svc_with_patches(monkeypatch)
    lines = [_submitted_line(1, "Brayan", 10, abbr="TB3")]
    captured = []

    monkeypatch.setattr(svc, "_count_pending_for_date", lambda _wd: 0)
    monkeypatch.setattr(svc, "_read_submitted_lines_for_date", lambda _wd: lines)
    monkeypatch.setattr(svc, "_claim_notification", lambda **kw: True)
    monkeypatch.setattr(
        svc, "_fetch_recipients", lambda _cl_id: {10: {"pms": [], "owners": []}},
    )

    class _Outbox:
        def enqueue_send_mail(self, **kw):
            captured.append(kw["subject"])
            return {"id": 1}

    monkeypatch.setattr(
        "entities.review.business.cl_notification_service.MsOutboxService", _Outbox,
    )

    svc.enqueue_drafts(contract_labor=_cl_trigger(work_date="2026-07-15"))

    assert captured == ["[Review] Contract Labor - TB3 - 2026-07-15"]


# --- is_cl_notification_subject (outbound subjects only) ---


def test_is_cl_notification_subject_accepts_new_and_legacy_forms():
    from entities.review.business.cl_notification_service import is_cl_notification_subject

    assert is_cl_notification_subject("[Review] Contract Labor - TB3 - 2026-11-03") is True
    assert is_cl_notification_subject("Contract Labor - TB3 - 2026-11-03") is True


@pytest.mark.parametrize(
    "subject",
    [
        None,
        "",
        "Re: Contract Labor - X - 2026-11-03",
        "RE: [Review] Contract Labor - X",
        "[Review] Vendor — Bill #1 — HP2 — $5.00",
        "Contract Labor Overhead",
        "[review] Contract Labor - X",
    ],
)
def test_is_cl_notification_subject_rejects_everything_else(subject):
    from entities.review.business.cl_notification_service import is_cl_notification_subject

    assert is_cl_notification_subject(subject) is False


# --- reply binding anchor ---


def test_anchor_binds_to_an_outbound_with_the_new_review_subject(monkeypatch):
    assert _cl_anchor(
        monkeypatch,
        [_outbound(subject="[Review] Contract Labor - proj-a - 2026-08-24")],
    ) == CONV


def test_anchor_still_binds_to_an_outbound_with_the_legacy_subject(monkeypatch):
    assert _cl_anchor(
        monkeypatch, [_outbound(subject="Contract Labor - proj-a - 2026-08-24")],
    ) == CONV


def test_anchor_refuses_a_bill_review_subject_on_the_thread(monkeypatch):
    assert _cl_anchor(
        monkeypatch, [_outbound(subject="[Review] Vendor — Bill #1 — HP2 — $5.00")],
    ) is None


# --- sproc LIKE escaping ---


def test_find_reviewer_reply_sproc_matches_both_subject_forms_with_escaped_bracket():
    body = _sproc_body(_CONTRACT_LABOR_SQL, "FindContractLaborForReviewerReply")

    assert "LIKE '[[]Review] Contract Labor - %'" in body
    assert "LIKE 'Contract Labor - %'" in body
    # Unescaped, `[Review]` is a T-SQL character class and matches nothing useful.
    assert "LIKE '[Review]" not in body
