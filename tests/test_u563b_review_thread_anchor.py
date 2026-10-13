"""U-563b part 1 — Bill/Expense reviewer replies bind to the document review thread."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.authz.context import clear_authz_context, system_authz
from shared.authz.provenance import (
    ReviewerProvenanceRefused,
    expected_review_thread_conversation_ids,
)

PRE_CUTOFF_CREATED = "2026-09-29 12:00:00"
POST_CUTOFF_CREATED = "2026-10-01 08:00:00"

BILL_PID = "bill-pub-anchor-a"
BILL_B_PID = "bill-pub-anchor-b"
EXP_PID = "exp-pub-anchor"
RECORDED_CONV = "AAQkADRecordedDraftConvIdExact"
REPLY_CONV = RECORDED_CONV
OTHER_CONV = "AAQkADUnrelatedThreadId"
CONV_B = "AAQkADDocumentBNotificationThread"
NOTIF_SUBJECT = "Review: Vendor ABC — Inv 1001 — Project X — $1,234.56"
REVIEWER_EMAIL = "pm@example.com"
REPLY_PUB = "reply-em-pub-anchor"


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


def _outbox_row(
    *,
    entity_type: str,
    entity_public_id: str,
    subject: str | None = NOTIF_SUBJECT,
    conversation_id: str | None = None,
    kind: str = "send_mail",
    created_datetime: str | None = PRE_CUTOFF_CREATED,
):
    payload_obj: dict = {"mode": "draft"}
    if subject is not None:
        payload_obj["subject"] = subject
    if conversation_id is not None:
        payload_obj["conversation_id"] = conversation_id
    ns = SimpleNamespace(
        kind=kind,
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        payload=json.dumps(payload_obj),
    )
    if created_datetime is not None:
        ns.created_datetime = created_datetime
    return ns


def _outbox_row_raw_payload(payload: str):
    return SimpleNamespace(
        kind="send_mail",
        entity_type="Bill",
        entity_public_id=BILL_PID,
        payload=payload,
    )


def _sent_notification(*, conversation_id: str, subject: str = NOTIF_SUBJECT, processing_status: str = "outbound"):
    return SimpleNamespace(
        id=50,
        folder="sentitems",
        processing_status=processing_status,
        subject=subject,
        conversation_id=conversation_id,
    )


def _patch_outbox_and_conversation(*, outbox_rows, conversation_messages):
    outbox_mock = MagicMock(read_completed_by_entity=MagicMock(return_value=outbox_rows))
    em_mock = MagicMock(read_by_conversation_id=MagicMock(return_value=conversation_messages))
    return (
        patch(
            "integrations.ms.outbox.persistence.repo.MsOutboxRepository",
            return_value=outbox_mock,
        ),
        patch(
            "entities.email_message.persistence.repo.EmailMessageRepository",
            return_value=em_mock,
        ),
        outbox_mock,
        em_mock,
    )


# ---------------------------------------------------------------------------
# expected_review_thread_conversation_ids — recorded draft conversation (primary)
# ---------------------------------------------------------------------------


def test_helper_accepts_when_recorded_conversation_matches_reply():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
        )
    ]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows,
        conversation_messages=[],
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == {REPLY_CONV}
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_refuses_recorded_conversation_case_mismatch():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
        )
    ]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows,
        conversation_messages=[],
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=RECORDED_CONV.swapcase(),
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_refuses_reply_on_other_conversation_when_recorded_present():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
        )
    ]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows,
        conversation_messages=[_sent_notification(conversation_id=OTHER_CONV, subject=NOTIF_SUBJECT)],
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=OTHER_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_cross_document_reply_on_b_thread_refused_for_document_a():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
        )
    ]
    ob_patch, em_patch, outbox_mock, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows,
        conversation_messages=[_sent_notification(conversation_id=CONV_B, subject=NOTIF_SUBJECT)],
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=CONV_B,
        ) == set()
    outbox_mock.read_completed_by_entity.assert_called_once_with(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        kind="send_mail",
    )
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_matching_subject_does_not_rescue_when_recorded_conversation_mismatches():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
        )
    ]
    msgs = [_sent_notification(conversation_id=OTHER_CONV, subject=NOTIF_SUBJECT)]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=OTHER_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_mixed_legacy_and_recorded_rows_use_recorded_rule():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
        ),
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            conversation_id=RECORDED_CONV,
        ),
    ]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows,
        conversation_messages=[_sent_notification(conversation_id=OTHER_CONV, subject=NOTIF_SUBJECT)],
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=OTHER_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_read_completed_filters_send_mail_kind():
    ob_patch, em_patch, outbox_mock, _ = _patch_outbox_and_conversation(outbox_rows=[], conversation_messages=[])
    with ob_patch, em_patch:
        expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        )
    outbox_mock.read_completed_by_entity.assert_called_once_with(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        kind="send_mail",
    )


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps([]),
        json.dumps({}),
        json.dumps({"conversation_id": 12345}),
        json.dumps({"conversation_id": ""}),
        json.dumps({"conversation_id": "   "}),
    ],
)
def test_helper_skips_malformed_or_invalid_conversation_id_in_payload(payload):
    rows = [_outbox_row_raw_payload(payload)]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


# ---------------------------------------------------------------------------
# expected_review_thread_conversation_ids — legacy subject + sentitems (fallback)
# ---------------------------------------------------------------------------


def test_helper_legacy_returns_reply_conversation_when_outbound_subject_matches():
    rows = [_outbox_row(entity_type="Bill", entity_public_id=BILL_PID, subject=NOTIF_SUBJECT, conversation_id=None)]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == {REPLY_CONV}


def test_helper_legacy_empty_when_no_completed_outbox_rows():
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=[], conversation_messages=[])
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


def test_helper_legacy_empty_when_outbox_rows_are_for_another_document():
    rows: list = []
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


@pytest.mark.parametrize(
    "bad_subject",
    [
        "totally different subject",
        NOTIF_SUBJECT.lower(),
        NOTIF_SUBJECT + " ",
    ],
)
def test_helper_legacy_empty_on_subject_mismatch(bad_subject):
    rows = [_outbox_row(entity_type="Bill", entity_public_id=BILL_PID, subject=NOTIF_SUBJECT, conversation_id=None)]
    msgs = [_sent_notification(conversation_id=REPLY_CONV, subject=bad_subject)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


def test_helper_legacy_empty_when_matching_subject_is_inbound_not_outbound():
    rows = [_outbox_row(entity_type="Expense", entity_public_id=EXP_PID, subject=NOTIF_SUBJECT, conversation_id=None)]
    msgs = [
        _sent_notification(
            conversation_id=REPLY_CONV,
            processing_status="inbound",
        )
    ]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Expense",
            entity_public_id=EXP_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


def test_helper_legacy_empty_when_outbound_conversation_differs_only_by_case():
    rows = [_outbox_row(entity_type="Bill", entity_public_id=BILL_PID, subject=NOTIF_SUBJECT, conversation_id=None)]
    msgs = [_sent_notification(conversation_id=REPLY_CONV.swapcase())]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"body": "no subject key"}),
    ],
)
def test_helper_legacy_tolerates_subjectless_outbox_payload(payload):
    rows = [_outbox_row_raw_payload(payload)]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(outbox_rows=rows, conversation_messages=msgs)
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


def test_helper_post_cutoff_no_stamp_refuses_subject_fallback_even_when_sentitems_match():
    """Post-U-579 rows without conversation_id stamp must not use subject fallback."""
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=POST_CUTOFF_CREATED,
        )
    ]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=msgs
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


@pytest.mark.parametrize(
    "created,expect_accept",
    [("2026-09-29 23:59:59", True), ("2026-09-30 00:00:00", False)],
)
def test_helper_cutoff_boundary_is_strictly_before(created, expect_accept):
    """The cutoff is a UTC day boundary and strict: the cutoff instant itself is NOT legacy."""
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=created,
        )
    ]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=msgs
    )
    with ob_patch, em_patch:
        got = expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        )
    assert got == ({REPLY_CONV} if expect_accept else set())


def test_helper_mixed_rows_only_the_pre_cutoff_subject_counts():
    """A post-cutoff unstamped row must not widen the legacy subject set."""
    late_subject = "Review: Other Vendor — Inv 2002 — Project Z — $9.99"
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        ),
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=late_subject,
            conversation_id=None,
            created_datetime=POST_CUTOFF_CREATED,
        ),
    ]
    msgs = [_sent_notification(conversation_id=OTHER_CONV, subject=late_subject)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=msgs
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=OTHER_CONV,
        ) == set()


def test_helper_pre_cutoff_no_stamp_accepts_subject_fallback():
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        )
    ]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=msgs
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == {REPLY_CONV}


@pytest.mark.parametrize("bad_created", [None, "", "not-a-date", "2026/09/29"])
def test_helper_unparseable_or_missing_created_datetime_refuses_subject_fallback(bad_created):
    row = _outbox_row(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        subject=NOTIF_SUBJECT,
        conversation_id=None,
        created_datetime=PRE_CUTOFF_CREATED,
    )
    if bad_created is None:
        delattr(row, "created_datetime")
    else:
        row.created_datetime = bad_created
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=[row], conversation_messages=msgs
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_outbox_row_for_other_entity_public_id_ignored_for_recorded_and_subjects():
    other_pid = BILL_B_PID
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=other_pid,
            subject=NOTIF_SUBJECT,
            conversation_id=RECORDED_CONV,
            created_datetime=PRE_CUTOFF_CREATED,
        ),
        _outbox_row(
            entity_type="Bill",
            entity_public_id=other_pid.upper(),
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        ),
    ]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=msgs
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()


def test_helper_legacy_subject_fallback_emits_warning(caplog):
    import logging

    rows = [
        _outbox_row(
            entity_type="Expense",
            entity_public_id=EXP_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        )
    ]
    msgs = [_sent_notification(conversation_id=REPLY_CONV)]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=msgs
    )
    with caplog.at_level(logging.WARNING):
        with ob_patch, em_patch:
            assert expected_review_thread_conversation_ids(
                entity_type="Expense",
                entity_public_id=EXP_PID,
                reply_conversation_id=REPLY_CONV,
            ) == {REPLY_CONV}
    assert any(
        r.message == "review_thread.legacy_subject_fallback"
        and getattr(r, "event_name", None) == "review_thread.legacy_subject_fallback"
        for r in caplog.records
    )


def test_helper_legacy_identical_subjects_cross_bind_residual():
    """Accepted residual: pre-cutoff notifications with identical subjects can cross-bind.

    Two documents sharing the same notification subject both accept a reply on either
    thread when no conversation_id stamp exists (legacy path only).
    """
    shared_subject = "Review: Shared Vendor — Inv 9999 — Project Y — $500.00"
    conv_a = "AAQkADPreCutoffDocAThread"
    conv_b = "AAQkADPreCutoffDocBThread"
    rows_a = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=shared_subject,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        )
    ]
    rows_b = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_B_PID,
            subject=shared_subject,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        )
    ]
    sent_on_b = [_sent_notification(conversation_id=conv_b, subject=shared_subject)]
    ob_a, em_a, _, _ = _patch_outbox_and_conversation(outbox_rows=rows_a, conversation_messages=sent_on_b)
    with ob_a, em_a:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=conv_b,
        ) == {conv_b}
    sent_on_a = [_sent_notification(conversation_id=conv_a, subject=shared_subject)]
    ob_b, em_b, _, _ = _patch_outbox_and_conversation(outbox_rows=rows_b, conversation_messages=sent_on_a)
    with ob_b, em_b:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_B_PID,
            reply_conversation_id=conv_a,
        ) == {conv_a}


def test_legacy_cutoff_constant_matches_u579_deploy():
    from shared.authz import provenance as provenance_mod

    assert provenance_mod.LEGACY_THREAD_FALLBACK_CUTOFF == "2026-09-30 00:00:00"


# ---------------------------------------------------------------------------
# Bill / Expense service — thread bind via recorded conversation
# ---------------------------------------------------------------------------


def _inbound_reply(*, conversation_id: str):
    return SimpleNamespace(
        id=777,
        public_id=REPLY_PUB,
        folder="inbox",
        from_address=REVIEWER_EMAIL,
        conversation_id=conversation_id,
    )


def _bill_svc():
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=5,
            public_id=BILL_PID,
            is_draft=True,
        )
    )
    return svc


def _expense_svc():
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=101,
            public_id=EXP_PID,
            is_draft=True,
        )
    )
    return svc


def _pm_recipient():
    from entities.review.business.recipient_model import ResolvedRecipient

    return ResolvedRecipient(
        user_id=20,
        firstname="Pat",
        lastname="M",
        email=REVIEWER_EMAIL,
        role_name="Project Manager",
        project_id=1,
    )


def _recorded_anchor_fixtures(
    *,
    entity_type: str,
    entity_public_id: str,
    recorded_conv: str,
    reply_conv: str,
):
    outbox = [
        _outbox_row(
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            conversation_id=recorded_conv,
        )
    ]
    return _patch_outbox_and_conversation(
        outbox_rows=outbox,
        conversation_messages=[_sent_notification(conversation_id=reply_conv, subject=NOTIF_SUBJECT)],
    )


@patch("entities.bill.business.service.get_connection")
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.bill_line_item.business.service.BillLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_bill_refuses_right_sender_on_unrelated_conversation_zero_writes(
    RecipSvc, BliSvc, ReviewRepo, bill_gc,
):
    bill_gc.return_value.__enter__ = MagicMock(return_value=MagicMock())
    bill_gc.return_value.__exit__ = MagicMock(return_value=False)
    svc = _bill_svc()
    RecipSvc.return_value.resolve_for_bill.return_value = {"to": [_pm_recipient()], "cc": []}
    BliSvc.return_value.read_by_bill_id.return_value = [
        SimpleNamespace(public_id="bli-1", row_version="rv")
    ]
    review_create = ReviewRepo.return_value.create

    outbox_patch, em_patch, _, em_mock = _recorded_anchor_fixtures(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        recorded_conv=RECORDED_CONV,
        reply_conv=OTHER_CONV,
    )
    with (
        system_authz(),
        patch("entities.email_message.business.service.EmailMessageService") as EmailSvc,
        patch("entities.bill.business.service.SubCostCodeService") as Scc,
        outbox_patch,
        em_patch,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_reply(
            conversation_id=OTHER_CONV,
        )
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                bill_public_id=BILL_PID,
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=REPLY_PUB,
            )

    bill_gc.return_value.__enter__.assert_not_called()
    BliSvc.return_value.update_coding_in_transaction.assert_not_called()
    review_create.assert_not_called()
    em_mock.read_by_conversation_id.assert_not_called()


@patch("entities.bill.business.service.get_connection")
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.bill_line_item.business.service.BillLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_bill_accepts_reply_on_anchored_thread(
    RecipSvc, BliSvc, ReviewRepo, bill_gc,
):
    bill_gc.return_value.__enter__ = MagicMock(return_value=MagicMock())
    bill_gc.return_value.__exit__ = MagicMock(return_value=False)
    svc = _bill_svc()
    RecipSvc.return_value.resolve_for_bill.return_value = {"to": [_pm_recipient()], "cc": []}
    BliSvc.return_value.read_by_bill_id.return_value = [
        SimpleNamespace(public_id="bli-1", row_version="rv")
    ]
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=30)

    outbox_patch, em_patch, outbox_mock, em_mock = _recorded_anchor_fixtures(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        recorded_conv=RECORDED_CONV,
        reply_conv=REPLY_CONV,
    )
    with (
        system_authz(),
        patch("entities.email_message.business.service.EmailMessageService") as EmailSvc,
        patch("entities.bill.business.service.SubCostCodeService") as Scc,
        patch("entities.review_status.business.service.ReviewStatusService") as Rs,
        outbox_patch,
        em_patch,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_reply(
            conversation_id=REPLY_CONV,
        )
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        Rs.return_value.read_all.return_value = [
            SimpleNamespace(id=30, is_final=True, is_declined=False),
        ]
        Rs.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
        svc.apply_reviewer_decision(
            bill_public_id=BILL_PID,
            decision="approved",
            reviewer_email=REVIEWER_EMAIL,
            sub_cost_code_public_id="scc-pub",
            reviewer_email_message_public_id=REPLY_PUB,
        )

    review_create.assert_called_once()
    em_mock.read_by_conversation_id.assert_not_called()
    outbox_mock.read_completed_by_entity.assert_called_once_with(
        entity_type="Bill", entity_public_id=BILL_PID, kind="send_mail",
    )


@patch("entities.expense.business.service.get_connection")
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_expense_refuses_right_sender_on_unrelated_conversation_zero_writes(
    RecipSvc, ReviewRepo, expense_gc,
):
    expense_gc.return_value.__enter__ = MagicMock(return_value=MagicMock())
    expense_gc.return_value.__exit__ = MagicMock(return_value=False)
    svc = _expense_svc()
    RecipSvc.return_value.resolve_for_expense.return_value = {"to": [_pm_recipient()], "cc": []}
    review_create = ReviewRepo.return_value.create

    outbox_patch, em_patch, _, em_mock = _recorded_anchor_fixtures(
        entity_type="Expense",
        entity_public_id=EXP_PID,
        recorded_conv=RECORDED_CONV,
        reply_conv=OTHER_CONV,
    )
    with (
        system_authz(),
        patch("entities.email_message.business.service.EmailMessageService") as EmailSvc,
        outbox_patch,
        em_patch,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_reply(
            conversation_id=OTHER_CONV,
        )
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                expense_public_id=EXP_PID,
                decision="rejected",
                reviewer_email=REVIEWER_EMAIL,
                reviewer_email_message_public_id=REPLY_PUB,
            )

    expense_gc.return_value.__enter__.assert_not_called()
    review_create.assert_not_called()
    em_mock.read_by_conversation_id.assert_not_called()


@patch("entities.expense.business.service.get_connection")
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_expense_accepts_reply_on_anchored_thread(
    RecipSvc, ReviewRepo, expense_gc,
):
    expense_gc.return_value.__enter__ = MagicMock(return_value=MagicMock())
    expense_gc.return_value.__exit__ = MagicMock(return_value=False)
    svc = _expense_svc()
    RecipSvc.return_value.resolve_for_expense.return_value = {"to": [_pm_recipient()], "cc": []}
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    outbox_patch, em_patch, outbox_mock, em_mock = _recorded_anchor_fixtures(
        entity_type="Expense",
        entity_public_id=EXP_PID,
        recorded_conv=RECORDED_CONV,
        reply_conv=REPLY_CONV,
    )
    with (
        system_authz(),
        patch("entities.email_message.business.service.EmailMessageService") as EmailSvc,
        patch("entities.review_status.business.service.ReviewStatusService") as Rs,
        outbox_patch,
        em_patch,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_reply(
            conversation_id=REPLY_CONV,
        )
        Rs.return_value.read_all.return_value = [
            SimpleNamespace(id=100, is_final=False, is_declined=True),
        ]
        Rs.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
        svc.apply_reviewer_decision(
            expense_public_id=EXP_PID,
            decision="rejected",
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
        )

    review_create.assert_called_once()
    em_mock.read_by_conversation_id.assert_not_called()
    # The anchor must be read for THIS expense, not a Bill with the same id.
    outbox_mock.read_completed_by_entity.assert_called_once_with(
        entity_type="Expense", entity_public_id=EXP_PID, kind="send_mail",
    )


# ---------------------------------------------------------------------------
# bind_reviewer_reply_to_review_thread — one read, cheap checks first (Pass 2)
# ---------------------------------------------------------------------------


def _patch_reply_read(em):
    svc = MagicMock(read_by_public_id=MagicMock(return_value=em))
    return patch(
        "entities.email_message.business.service.EmailMessageService",
        return_value=svc,
    ), svc


def test_review_thread_binder_refuses_wrong_sender_before_any_outbox_read():
    from shared.authz.provenance import (
        ReviewerProvenanceRefused,
        bind_reviewer_reply_to_review_thread,
    )

    em = _inbound_reply(conversation_id=RECORDED_CONV)
    em.from_address = "someone-else@example.com"
    read_patch, _ = _patch_reply_read(em)
    ob_patch, em_patch, outbox_mock, _ = _patch_outbox_and_conversation(
        outbox_rows=[], conversation_messages=[]
    )
    with read_patch, ob_patch, em_patch:
        with pytest.raises(ReviewerProvenanceRefused, match="sender does not match"):
            bind_reviewer_reply_to_review_thread(
                entity_type="Bill",
                entity_public_id=BILL_PID,
                reviewer_email=REVIEWER_EMAIL,
                reviewer_email_message_public_id=REPLY_PUB,
                what="test",
            )
    outbox_mock.read_completed_by_entity.assert_not_called()


@pytest.mark.parametrize("reply_conv,accepted", [(RECORDED_CONV, True), (OTHER_CONV, False)])
def test_review_thread_binder_reads_reply_once_and_binds_to_recorded_thread(reply_conv, accepted):
    from shared.authz.provenance import (
        ReviewerProvenanceRefused,
        bind_reviewer_reply_to_review_thread,
    )

    read_patch, reply_svc = _patch_reply_read(_inbound_reply(conversation_id=reply_conv))
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=RECORDED_CONV,
            created_datetime=POST_CUTOFF_CREATED,
        )
    ]
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=[]
    )
    kwargs = dict(
        entity_type="Bill",
        entity_public_id=BILL_PID,
        reviewer_email=REVIEWER_EMAIL,
        reviewer_email_message_public_id=REPLY_PUB,
        what="test",
    )
    with read_patch, ob_patch, em_patch:
        if accepted:
            assert bind_reviewer_reply_to_review_thread(**kwargs).conversation_id == RECORDED_CONV
        else:
            with pytest.raises(ReviewerProvenanceRefused, match="not on this document's review email thread"):
                bind_reviewer_reply_to_review_thread(**kwargs)
    reply_svc.read_by_public_id.assert_called_once_with(public_id=REPLY_PUB)


def test_helper_legacy_ignores_matching_subject_outside_sentitems():
    """A same-subject message on the reply's conversation that is not in Sent Items
    (e.g. an inbound copy) is not our notification and must not anchor the thread."""
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject=NOTIF_SUBJECT,
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        )
    ]
    inbox_copy = _sent_notification(conversation_id=REPLY_CONV)
    inbox_copy.folder = "inbox"
    ob_patch, em_patch, _, _ = _patch_outbox_and_conversation(
        outbox_rows=rows, conversation_messages=[inbox_copy]
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()


def test_helper_legacy_blank_subject_never_anchors():
    """Forward-era notifications (2026-05) were enqueued with the placeholder subject "".
    A blank subject is not an identity: it must not match a no-subject Sent Items message."""
    rows = [
        _outbox_row(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            subject="",
            conversation_id=None,
            created_datetime=PRE_CUTOFF_CREATED,
        )
    ]
    ob_patch, em_patch, _, em_mock = _patch_outbox_and_conversation(
        outbox_rows=rows,
        conversation_messages=[_sent_notification(conversation_id=REPLY_CONV, subject=None)],
    )
    with ob_patch, em_patch:
        assert expected_review_thread_conversation_ids(
            entity_type="Bill",
            entity_public_id=BILL_PID,
            reply_conversation_id=REPLY_CONV,
        ) == set()
    em_mock.read_by_conversation_id.assert_not_called()
