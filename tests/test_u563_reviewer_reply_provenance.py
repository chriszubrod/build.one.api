"""U-563 — emailed reviewer decisions must bind to a real inbound reply."""

from __future__ import annotations

import inspect
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.recipient_model import ResolvedRecipient
from shared.api.errors import ErrorCode
from shared.authz.context import clear_authz_context, system_authz
from tests.test_u459_reviewer_impersonation import CALL_SITES

from shared.authz.provenance import (
    bind_reviewer_reply_to_thread,
    ReviewerProvenanceRefused,
    bind_reviewer_reply,
)


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


@pytest.fixture(autouse=True)
def _default_empty_outbox_and_conversation_poll():
    """Bill/Expense apply paths read completed outbox rows before bind."""
    with patch(
        "integrations.ms.outbox.persistence.repo.MsOutboxRepository",
        return_value=MagicMock(read_completed_by_entity=MagicMock(return_value=[])),
    ):
        with patch(
            "entities.email_message.persistence.repo.EmailMessageRepository",
            return_value=MagicMock(read_by_conversation_id=MagicMock(return_value=[])),
        ):
            yield


@pytest.fixture(autouse=True)
def _u564_reviewer_decision_transaction_conn():
    """U-564: bill/expense apply_reviewer_decision uses one get_connection()."""
    bill_conn = MagicMock(name="bill_review_conn")
    expense_conn = MagicMock(name="expense_review_conn")
    with patch("entities.bill.business.service.get_connection") as bill_gc:
        with patch("entities.expense.business.service.get_connection") as exp_gc:
            bill_gc.return_value.__enter__ = MagicMock(return_value=bill_conn)
            bill_gc.return_value.__exit__ = MagicMock(return_value=False)
            exp_gc.return_value.__enter__ = MagicMock(return_value=expense_conn)
            exp_gc.return_value.__exit__ = MagicMock(return_value=False)
            yield


CONV = "AAQkADExampleThreadIdCaseSensitive"
OTHER_CONV = "AAQkADTotallyDifferentNotificationThread"
REPLY_PUB = "reply-em-pub-1"
REVIEWER_EMAIL = "pm@example.com"
CL_OUTBOUND_SUBJECT = "Contract Labor - TB3 - 2026-08-24"
BILL_NOTIF_SUBJECT = "Review notification subject bill"
EXP_NOTIF_SUBJECT = "Review notification subject expense"


def _inbound_em(**overrides):
    base = dict(
        id=777,
        public_id=REPLY_PUB,
        folder="inbox",
        from_address=REVIEWER_EMAIL,
        conversation_id=CONV,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _outbound_notification_em(*, conversation_id: str):
    return SimpleNamespace(
        id=50,
        public_id="outbound-em-pub",
        folder="sentitems",
        processing_status="outbound",
        subject=CL_OUTBOUND_SUBJECT,
        conversation_id=conversation_id,
    )


def _pm_recipient(email: str = REVIEWER_EMAIL) -> ResolvedRecipient:
    return ResolvedRecipient(
        user_id=20,
        firstname="Pat",
        lastname="M",
        email=email,
        role_name="Project Manager",
        project_id=1,
    )


def _bill_svc(*, source_email_message_id=50, is_draft=True):
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=5,
            public_id="bill-pub-5",
            is_draft=is_draft,
            status="draft",
            source_email_message_id=source_email_message_id,
        )
    )
    return svc


def _expense_svc(*, source_email_message_id=50, is_draft=True):
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=101,
            public_id="exp-pub-1",
            is_draft=is_draft,
            source_email_message_id=source_email_message_id,
        )
    )
    return svc


def _patch_reply_em(em):
    return patch("entities.email_message.business.service.EmailMessageService")


def _cl_outbound_repo_patch(*, reply_conv: str, outbound_conv: str):
    """Stub outbound notification lookup for CL thread anchor."""
    return patch(
        "entities.email_message.persistence.repo.EmailMessageRepository",
        return_value=MagicMock(
            read_by_conversation_id=MagicMock(
                return_value=[_outbound_notification_em(conversation_id=outbound_conv)],
            ),
        ),
    )


def _be_outbound_em(*, conversation_id: str, subject: str):
    return SimpleNamespace(
        id=51,
        folder="sentitems",
        processing_status="outbound",
        subject=subject,
        conversation_id=conversation_id,
    )


def _be_thread_anchor_patch(
    *,
    entity_type: str,
    entity_public_id: str,
    reply_conv: str,
    notification_subject: str,
    anchored: bool,
):
    """Stub completed send_mail outbox + sent-items poll for Bill/Expense thread bind."""
    import json

    outbox_rows = (
        [
            SimpleNamespace(
                payload=json.dumps(
                    {
                        "subject": notification_subject,
                        "conversation_id": reply_conv,
                    }
                ),
            )
        ]
        if anchored
        else []
    )
    conv_msgs = (
        [_be_outbound_em(conversation_id=reply_conv, subject=notification_subject)]
        if anchored
        else []
    )
    return (
        patch(
            "integrations.ms.outbox.persistence.repo.MsOutboxRepository",
            return_value=MagicMock(
                read_completed_by_entity=MagicMock(return_value=outbox_rows),
            ),
        ),
        patch(
            "entities.email_message.persistence.repo.EmailMessageRepository",
            return_value=MagicMock(
                read_by_conversation_id=MagicMock(return_value=conv_msgs),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# bind_reviewer_reply — unit
# ---------------------------------------------------------------------------


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_happy_path_returns_message(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em()
    em = bind_reviewer_reply_to_thread(
        reviewer_email=REVIEWER_EMAIL,
        reviewer_email_message_public_id=REPLY_PUB,
        expected_conversation_id=CONV,
        what="test",
    )
    assert em.id == 777


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_skips_thread_checks_when_thread_not_bound(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
        conversation_id=OTHER_CONV,
    )
    em = bind_reviewer_reply(
        reviewer_email=REVIEWER_EMAIL,
        reviewer_email_message_public_id=REPLY_PUB,
        what="test",
    )
    assert em.id == 777


@pytest.mark.parametrize("bad_id", [None, "", "   "])
def test_bind_refuses_missing_or_blank_public_id(bad_id):
    with pytest.raises(ReviewerProvenanceRefused) as exc:
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=bad_id,
            expected_conversation_id=CONV,
            what="test",
        )
    assert exc.value.status_code == 403
    assert exc.value.error_code == ErrorCode.REVIEWER_PROVENANCE_REFUSED


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_unresolvable_public_id(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = None
    with pytest.raises(ReviewerProvenanceRefused, match="does not resolve"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id=CONV,
            what="test",
        )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_non_inbox_folder(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(folder="sentitems")
    with pytest.raises(ReviewerProvenanceRefused, match="inbox"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id=CONV,
            what="test",
        )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_from_address_mismatch(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
        from_address="other@example.com"
    )
    with pytest.raises(ReviewerProvenanceRefused, match="sender"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id=CONV,
            what="test",
        )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_accepts_from_address_case_and_space_insensitive(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
        from_address="  PM@Example.COM "
    )
    em = bind_reviewer_reply_to_thread(
        reviewer_email="pm@example.com",
        reviewer_email_message_public_id=REPLY_PUB,
        expected_conversation_id=CONV,
        what="test",
    )
    assert em.id == 777


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_when_document_has_no_thread(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em()
    with pytest.raises(ReviewerProvenanceRefused, match="no linked email thread"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id=None,
            what="test",
        )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_a_blank_thread_even_when_the_reply_has_no_conversation(EmailSvc):
    # Without the blank-thread guard, "" == "" would bind a thread-less reply to a
    # document that has no thread at all.
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(conversation_id="")
    with pytest.raises(ReviewerProvenanceRefused, match="no linked email thread"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id="   ",
            what="test",
        )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_conversation_mismatch(EmailSvc):
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
        conversation_id="other-thread"
    )
    with pytest.raises(ReviewerProvenanceRefused, match="thread"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id=CONV,
            what="test",
        )


@patch("entities.email_message.business.service.EmailMessageService")
def test_bind_refuses_conversation_case_mismatch(EmailSvc):
    """Graph conversation ids are case-sensitive."""
    EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
        conversation_id=CONV.lower()
    )
    with pytest.raises(ReviewerProvenanceRefused, match="thread"):
        bind_reviewer_reply_to_thread(
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
            expected_conversation_id=CONV,
            what="test",
        )


# ---------------------------------------------------------------------------
# u459-style registry — bind_reviewer_reply wired at call sites
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,cls,method", CALL_SITES)
def test_the_service_calls_bind_reviewer_reply(module, cls, method):
    import importlib

    svc_cls = getattr(importlib.import_module(module), cls)
    src = inspect.getsource(getattr(svc_cls, method))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert re.search(r"bind_reviewer_reply(_to_thread|_to_review_thread)?\(", executable), (
        f"{cls}.{method} does not bind reviewer reply provenance (U-563)"
    )


# ---------------------------------------------------------------------------
# Bill — service integration
# ---------------------------------------------------------------------------


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.bill_line_item.business.service.BillLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_bill_happy_path_review_row_carries_bound_message_id(
    RecipSvc, BliSvc, ReviewRepo
):
    svc = _bill_svc()
    RecipSvc.return_value.resolve_for_bill.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    BliSvc.return_value.read_by_bill_id.return_value = [
        SimpleNamespace(public_id="bli-1", row_version="rv")
    ]
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=30)

    ob_patch, em_patch = _be_thread_anchor_patch(
        entity_type="Bill",
        entity_public_id="bill-pub-5",
        reply_conv=CONV,
        notification_subject=BILL_NOTIF_SUBJECT,
        anchored=True,
    )
    with (
        system_authz(),
        _patch_reply_em(_inbound_em()) as EmailSvc,
        patch("entities.bill.business.service.SubCostCodeService") as Scc,
        patch("entities.review_status.business.service.ReviewStatusService") as Rs,
        ob_patch,
        em_patch,
    ):
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        Rs.return_value.read_all.return_value = [
            SimpleNamespace(id=30, is_final=True, is_declined=False),
        ]
        Rs.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            conversation_id=CONV,
        )
        result = svc.apply_reviewer_decision(
            bill_public_id="bill-pub-5",
            decision="approved",
            reviewer_email=REVIEWER_EMAIL,
            sub_cost_code_public_id="scc-pub",
            reviewer_email_message_public_id=REPLY_PUB,
        )

    assert review_create.call_args.kwargs["email_message_id"] == 777
    assert result["decision_applied"] == "approved"


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.bill_line_item.business.service.BillLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_bill_refuses_reply_on_unrelated_conversation(
    RecipSvc, BliSvc, ReviewRepo,
):
    svc = _bill_svc(source_email_message_id=99)
    RecipSvc.return_value.resolve_for_bill.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    BliSvc.return_value.read_by_bill_id.return_value = [
        SimpleNamespace(public_id="bli-1", row_version="rv")
    ]
    review_create = ReviewRepo.return_value.create

    ob_patch, em_patch = _be_thread_anchor_patch(
        entity_type="Bill",
        entity_public_id="bill-pub-5",
        reply_conv=OTHER_CONV,
        notification_subject=BILL_NOTIF_SUBJECT,
        anchored=False,
    )
    with (
        system_authz(),
        _patch_reply_em(_inbound_em()) as EmailSvc,
        patch("entities.bill.business.service.SubCostCodeService") as Scc,
        ob_patch,
        em_patch,
    ):
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            conversation_id=OTHER_CONV,
        )
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                bill_public_id="bill-pub-5",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=REPLY_PUB,
            )

    BliSvc.return_value.update_coding_in_transaction.assert_not_called()
    review_create.assert_not_called()


@pytest.mark.parametrize(
    "setup_reply",
    [
        pytest.param({"public_id": None}, id="missing_id"),
        pytest.param({"resolve_none": True}, id="unresolvable"),
        pytest.param({"folder": "sentitems"}, id="sentitems"),
        pytest.param({"from_address": "wrong@example.com"}, id="sender_mismatch"),
    ],
)
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.bill_line_item.business.service.BillLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_bill_approval_refusal_writes_nothing(
    RecipSvc, BliSvc, ReviewRepo, setup_reply,
):
    svc = _bill_svc()
    RecipSvc.return_value.resolve_for_bill.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    BliSvc.return_value.read_by_bill_id.return_value = [
        SimpleNamespace(public_id="bli-1", row_version="rv")
    ]
    review_create = ReviewRepo.return_value.create
    reply_pub = setup_reply.get("public_id", REPLY_PUB)

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        patch("entities.bill.business.service.SubCostCodeService") as Scc,
    ):
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        if setup_reply.get("resolve_none"):
            EmailSvc.return_value.read_by_public_id.return_value = None
        else:
            EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
                folder=setup_reply.get("folder", "inbox"),
                from_address=setup_reply.get("from_address", REVIEWER_EMAIL),
            )
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                bill_public_id="bill-pub-5",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=reply_pub,
            )

    BliSvc.return_value.update_coding_in_transaction.assert_not_called()
    review_create.assert_not_called()


# ---------------------------------------------------------------------------
# Expense — service integration
# ---------------------------------------------------------------------------


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_expense_happy_path_review_row_carries_bound_message_id(
    RecipSvc, RsSvc, ReviewRepo,
):
    svc = _expense_svc()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    RsSvc.return_value.read_all.return_value = [
        SimpleNamespace(id=100, is_final=False, is_declined=True),
    ]
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    ob_patch, em_patch = _be_thread_anchor_patch(
        entity_type="Expense",
        entity_public_id="exp-pub-1",
        reply_conv=CONV,
        notification_subject=EXP_NOTIF_SUBJECT,
        anchored=True,
    )
    with (
        system_authz(),
        _patch_reply_em(_inbound_em()) as EmailSvc,
        ob_patch,
        em_patch,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em()
        svc.apply_reviewer_decision(
            expense_public_id="exp-pub-1",
            decision="rejected",
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
        )

    assert review_create.call_args.kwargs["email_message_id"] == 777


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_expense_refuses_reply_on_unrelated_conversation(
    RecipSvc, EliSvc, ReviewRepo,
):
    svc = _expense_svc()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [
        SimpleNamespace(public_id="eli-1", row_version="rv")
    ]
    EliSvc.return_value.count_by_expense_id_for_update.return_value = 1
    review_create = ReviewRepo.return_value.create

    ob_patch, em_patch = _be_thread_anchor_patch(
        entity_type="Expense",
        entity_public_id="exp-pub-1",
        reply_conv=OTHER_CONV,
        notification_subject=EXP_NOTIF_SUBJECT,
        anchored=False,
    )
    with (
        system_authz(),
        _patch_reply_em(_inbound_em()) as EmailSvc,
        patch("entities.expense.business.service.SubCostCodeService") as Scc,
        ob_patch,
        em_patch,
    ):
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            conversation_id=OTHER_CONV,
        )
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                expense_public_id="exp-pub-1",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=REPLY_PUB,
            )

    EliSvc.return_value.update_coding_in_transaction.assert_not_called()
    review_create.assert_not_called()


@pytest.mark.parametrize(
    "setup_reply",
    [
        pytest.param({"public_id": None}, id="missing_id"),
        pytest.param({"resolve_none": True}, id="unresolvable"),
        pytest.param({"folder": "sentitems"}, id="sentitems"),
        pytest.param({"from_address": "wrong@example.com"}, id="sender_mismatch"),
    ],
)
@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_expense_approval_refusal_writes_nothing(
    RecipSvc, EliSvc, ReviewRepo, setup_reply,
):
    svc = _expense_svc()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [
        SimpleNamespace(public_id="eli-1", row_version="rv")
    ]
    review_create = ReviewRepo.return_value.create
    reply_pub = setup_reply.get("public_id", REPLY_PUB)

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        patch("entities.expense.business.service.SubCostCodeService") as Scc,
    ):
        Scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
        if setup_reply.get("resolve_none"):
            EmailSvc.return_value.read_by_public_id.return_value = None
        else:
            EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
                folder=setup_reply.get("folder", "inbox"),
                from_address=setup_reply.get("from_address", REVIEWER_EMAIL),
            )
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                expense_public_id="exp-pub-1",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=reply_pub,
            )

    EliSvc.return_value.update_coding_in_transaction.assert_not_called()
    review_create.assert_not_called()


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_expense_refused_from_mismatch_writes_nothing(
    RecipSvc, EliSvc, ReviewRepo,
):
    svc = _expense_svc()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            from_address="wrong@example.com"
        )
        with pytest.raises(ReviewerProvenanceRefused):
            svc.apply_reviewer_decision(
                expense_public_id="exp-pub-1",
                decision="rejected",
                reviewer_email=REVIEWER_EMAIL,
                reviewer_email_message_public_id=REPLY_PUB,
            )
    EliSvc.return_value.update_coding_in_transaction.assert_not_called()
    ReviewRepo.return_value.create.assert_not_called()


# ---------------------------------------------------------------------------
# Contract labor — _apply_decision_to_single_cl + crew wrapper
# ---------------------------------------------------------------------------


def _cl_svc_harness(monkeypatch):
    parent = SimpleNamespace(
        id=10,
        public_id="cl-pub-1",
        status="submitted",
    )
    repo = MagicMock()
    svc = __import__(
        "entities.contract_labor.business.service",
        fromlist=["ContractLaborService"],
    ).ContractLaborService(repo=repo)
    monkeypatch.setattr(svc, "read_by_public_id", lambda **kw: parent)
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: SimpleNamespace(
            read_by_public_id=lambda **kw: SimpleNamespace(id=100, public_id="proj-a"),
        ),
    )
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        SimpleNamespace(ProjectId=100, UserId=20, Email=REVIEWER_EMAIL),
    ]
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: conn
    conn.__exit__ = lambda *a: None
    monkeypatch.setattr("entities.contract_labor.business.service.get_connection", lambda: conn)
    monkeypatch.setattr("entities.contract_labor.business.service.call_procedure", lambda **kw: None)
    approved = SimpleNamespace(id=1, name="Approved", is_final=True, is_declined=False)
    declined = SimpleNamespace(id=2, name="Declined", is_final=False, is_declined=True)
    monkeypatch.setattr(
        "entities.review_status.business.service.ReviewStatusService",
        lambda: SimpleNamespace(
            read_all=lambda: [declined, approved],
            read_by_id=lambda id: approved if id == 1 else declined,
        ),
    )
    review_create = MagicMock(
        return_value=SimpleNamespace(
            id=99,
            review_status_id=2,
            status_is_final=False,
            status_is_declined=True,
        )
    )
    monkeypatch.setattr(
        "entities.review.persistence.repo.ReviewRepository",
        lambda: SimpleNamespace(
            create=review_create,
            read_current_by_contract_labor_id_for_update=lambda *a, **kw: None,
        ),
    )
    return svc, repo, review_create, parent


def _cl_resolver_and_outbound_stubs(repo, *, outbound_conv: str | None = None):
    outbound_conv = outbound_conv if outbound_conv is not None else CONV
    repo.find_for_reviewer_reply.return_value = {
        "project_public_id": "proj-a",
        "work_date": "2026-08-24",
        "match_kind": "conversation",
    }
    repo.read_reviewable_crew_by_project_and_date.return_value = [
        {"contract_labor_public_id": "cl-pub-1", "contract_labor_id": 10},
    ]
    return _cl_outbound_repo_patch(reply_conv=CONV, outbound_conv=outbound_conv)


def test_cl_happy_path_review_row_carries_bound_message_id(monkeypatch):
    svc, repo, review_create, _parent = _cl_svc_harness(monkeypatch)

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        _cl_resolver_and_outbound_stubs(repo),
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em()
        result = svc._apply_decision_to_single_cl(
            contract_labor_public_id="cl-pub-1",
            project_public_id="proj-a",
            decision="rejected",
            reviewer_email=REVIEWER_EMAIL,
            reviewer_email_message_public_id=REPLY_PUB,
        )

    assert result["decision_applied"] == "rejected"
    assert review_create.call_args.kwargs["email_message_id"] == 777
    repo.find_for_reviewer_reply.assert_called_with(conversation_id=CONV)


def test_cl_refused_when_resolver_does_not_include_cl(monkeypatch):
    svc, repo, review_create, _parent = _cl_svc_harness(monkeypatch)
    repo.find_for_reviewer_reply.return_value = {
        "project_public_id": "proj-a",
        "work_date": "2026-08-24",
        "match_kind": "conversation",
    }
    repo.read_reviewable_crew_by_project_and_date.return_value = [
        {"contract_labor_public_id": "other-cl", "contract_labor_id": 99},
    ]

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em()
        with pytest.raises(ReviewerProvenanceRefused, match="thread"):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id="cl-pub-1",
                project_public_id="proj-a",
                decision="rejected",
                reviewer_email=REVIEWER_EMAIL,
                reviewer_email_message_public_id=REPLY_PUB,
            )
    review_create.assert_not_called()


def test_cl_refuses_outbound_conversation_id_case_mismatch(monkeypatch):
    svc, repo, review_create, _parent = _cl_svc_harness(monkeypatch)

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        _cl_resolver_and_outbound_stubs(repo, outbound_conv=CONV.lower()),
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em()
        with pytest.raises(ReviewerProvenanceRefused, match="thread"):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id="cl-pub-1",
                project_public_id="proj-a",
                decision="rejected",
                reviewer_email=REVIEWER_EMAIL,
                reviewer_email_message_public_id=REPLY_PUB,
            )
    review_create.assert_not_called()


def test_cl_refuses_reply_on_different_conversation_than_notification(monkeypatch):
    """CL binds to the outbound notification thread (Bill/Expense do the same since U-563b)."""
    svc, repo, review_create, _parent = _cl_svc_harness(monkeypatch)

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        _cl_resolver_and_outbound_stubs(repo, outbound_conv=OTHER_CONV),
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            conversation_id=CONV,
        )
        with pytest.raises(ReviewerProvenanceRefused, match="thread"):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id="cl-pub-1",
                project_public_id="proj-a",
                decision="rejected",
                reviewer_email=REVIEWER_EMAIL,
                reviewer_email_message_public_id=REPLY_PUB,
            )
    review_create.assert_not_called()


def test_cl_approval_refusal_writes_nothing(monkeypatch):
    svc, repo, review_create, parent = _cl_svc_harness(monkeypatch)
    li_repo = MagicMock()
    li_repo.read_by_contract_labor_id.return_value = [
        SimpleNamespace(
            id=1,
            project_id=100,
            row_version_bytes=b"\x01",
            line_date="2026-08-24",
            description="d",
            hours=8,
            rate=50,
            markup=0.1,
            price=440,
            is_billable=True,
            is_overhead=False,
            bill_line_item_id=None,
        ),
    ]
    monkeypatch.setattr(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository",
        lambda: li_repo,
    )
    monkeypatch.setattr(
        "entities.sub_cost_code.business.service.SubCostCodeService",
        lambda: SimpleNamespace(
            read_by_public_id=lambda **kw: SimpleNamespace(id=555),
        ),
    )
    recompute = MagicMock()
    monkeypatch.setattr(svc.repo, "update_aggregates", recompute)
    ready = MagicMock()
    monkeypatch.setattr(svc, "mark_as_ready_via_review_approval", ready)

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        _cl_resolver_and_outbound_stubs(repo),
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            from_address="wrong@example.com",
        )
        with pytest.raises(ReviewerProvenanceRefused):
            svc._apply_decision_to_single_cl(
                contract_labor_public_id="cl-pub-1",
                project_public_id="proj-a",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=REPLY_PUB,
            )

    li_repo.update_by_id.assert_not_called()
    review_create.assert_not_called()
    recompute.assert_not_called()
    ready.assert_not_called()
    assert parent.status == "submitted"


def test_cl_wrapper_provenance_refused_propagates_without_member_writes(monkeypatch):
    svc, repo, review_create, _parent = _cl_svc_harness(monkeypatch)
    project = SimpleNamespace(id=100, public_id="proj-a")
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: project),
    )
    repo.read_reviewable_crew_by_project_and_date.return_value = [
        {"contract_labor_public_id": "cl-pub-1", "employee_name": "A"},
        {"contract_labor_public_id": "cl-pub-2", "employee_name": "B"},
    ]

    with (
        system_authz(),
        patch(
            "entities.email_message.business.service.EmailMessageService"
        ) as EmailSvc,
        _cl_resolver_and_outbound_stubs(repo),
    ):
        EmailSvc.return_value.read_by_public_id.return_value = _inbound_em(
            from_address="wrong@example.com",
        )
        with pytest.raises(ReviewerProvenanceRefused) as exc:
            svc.apply_reviewer_decision(
                project_public_id="proj-a",
                work_date="2026-08-24",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=REPLY_PUB,
            )

    assert exc.value.status_code == 403
    assert exc.value.error_code == ErrorCode.REVIEWER_PROVENANCE_REFUSED
    review_create.assert_not_called()


def test_cl_wrapper_refuses_before_any_member_write_when_a_later_member_is_off_thread(monkeypatch):
    """Member B failing the thread check must refuse the WHOLE crew before member A
    is written — not a 403 over a half-applied crew (round-2 review P1)."""
    svc, repo, review_create, _parent = _cl_svc_harness(monkeypatch)
    project = SimpleNamespace(id=100, public_id="proj-a")
    monkeypatch.setattr(
        "entities.contract_labor.business.service.ProjectService",
        lambda: SimpleNamespace(read_by_public_id=lambda **kw: project),
    )
    repo.read_reviewable_crew_by_project_and_date.return_value = [
        {"contract_labor_public_id": "cl-pub-1", "employee_name": "A"},
        {"contract_labor_public_id": "cl-pub-2", "employee_name": "B"},
    ]
    em = _inbound_em(from_address=REVIEWER_EMAIL)
    monkeypatch.setattr(
        svc,
        "_expected_cl_reviewer_reply_conversation_id",
        lambda **kw: em.conversation_id if kw["contract_labor_public_id"] == "cl-pub-1" else None,
    )
    single = MagicMock()
    monkeypatch.setattr(svc, "_apply_decision_to_single_cl", single)

    with (
        system_authz(),
        patch("entities.email_message.business.service.EmailMessageService") as EmailSvc,
    ):
        EmailSvc.return_value.read_by_public_id.return_value = em
        with pytest.raises(ReviewerProvenanceRefused) as exc:
            svc.apply_reviewer_decision(
                project_public_id="proj-a",
                work_date="2026-08-24",
                decision="approved",
                reviewer_email=REVIEWER_EMAIL,
                sub_cost_code_public_id="scc-pub",
                reviewer_email_message_public_id=REPLY_PUB,
            )

    assert exc.value.status_code == 403
    single.assert_not_called()
    review_create.assert_not_called()


# --- ContractLabor outbound anchor (the exact-thread half of the CL binding) ---


def _cl_anchor(monkeypatch, thread_messages):
    """Resolver confirms the crew; the conversation lookup returns `thread_messages`."""
    from entities.contract_labor.business.service import ContractLaborService

    svc = ContractLaborService(repo=MagicMock())
    svc.repo.find_for_reviewer_reply.return_value = {
        "match_kind": "conversation", "project_public_id": "proj-a", "work_date": "2026-08-24",
    }
    svc.repo.read_reviewable_crew_by_project_and_date.return_value = [
        {"contract_labor_public_id": "cl-pub-1"},
    ]
    monkeypatch.setattr(
        "entities.email_message.persistence.repo.EmailMessageRepository",
        lambda: SimpleNamespace(read_by_conversation_id=lambda **kw: thread_messages),
    )
    with patch("entities.email_message.business.service.EmailMessageService") as Em:
        Em.return_value.read_by_public_id.return_value = _inbound_em()
        return svc._expected_cl_reviewer_reply_conversation_id(
            reviewer_email_message_public_id=REPLY_PUB,
            contract_labor_public_id="cl-pub-1",
            project_public_id="proj-a",
            project_id=100,
        )


def _outbound(**kw):
    base = dict(processing_status="outbound", subject="Contract Labor - proj-a - 2026-08-24", conversation_id=CONV)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cl_anchor_binds_to_an_exact_case_outbound_notification(monkeypatch):
    assert _cl_anchor(monkeypatch, [_outbound()]) == CONV


def test_cl_anchor_refuses_an_outbound_whose_conversation_differs_only_by_case(monkeypatch):
    # The resolver's SQL compare is case-insensitive; this is the exact half.
    assert _cl_anchor(monkeypatch, [_outbound(conversation_id=CONV.swapcase())]) is None


def test_cl_anchor_ignores_a_non_outbound_message_on_the_thread(monkeypatch):
    # The reply itself (inbound) sits on the thread too; it must not anchor the thread.
    assert _cl_anchor(monkeypatch, [_outbound(processing_status="processed")]) is None


def test_cl_anchor_ignores_an_outbound_that_is_not_a_contract_labor_notification(monkeypatch):
    assert _cl_anchor(monkeypatch, [_outbound(subject="[Review] Vendor — Bill #9")]) is None
