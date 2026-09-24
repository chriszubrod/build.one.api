"""U-524 — ExpenseService.apply_reviewer_decision mirrors Bill with a single-line gate."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.recipient_model import ResolvedRecipient
from shared.authz.context import clear_authz_context, system_authz


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


EXPENSE_PID = "exp-pub-1"
REVIEWER_USER_ID = 20
CALLER_USER_ID = 33
SCC_PID = "scc-pub-9"


def _pm_recipient() -> ResolvedRecipient:
    return ResolvedRecipient(
        user_id=REVIEWER_USER_ID,
        firstname="Pat",
        lastname="M",
        email="pm@example.com",
        role_name="Project Manager",
        project_id=1,
    )


def _expense_service_stub(*, is_draft: bool = True):
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=101,
            public_id=EXPENSE_PID,
            is_draft=is_draft,
        )
    )
    return svc


def _line_item(public_id: str = "eli-pub-1"):
    return SimpleNamespace(
        public_id=public_id,
        row_version="rv1",
        sub_cost_code_id=None,
    )


def _approved_statuses():
    """Declined first, IsFinal=false — matches prod seed (scripts/.../seed_review_statuses.sql)."""
    declined = SimpleNamespace(id=100, name="Declined", is_final=False, is_declined=True)
    approved = SimpleNamespace(id=30, name="Approved", is_final=True, is_declined=False)
    return [declined, approved]


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.expense.business.service.SubCostCodeService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_approved_happy_path_stamps_scc_and_attributes_review_to_reviewer(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [_line_item()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=30)

    with system_authz():
        result = svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            description="Job supplies",
        )

    EliSvc.return_value.update_by_public_id.assert_called_once_with(
        public_id="eli-pub-1",
        row_version="rv1",
        sub_cost_code_id=9001,
        description="Job supplies",
    )
    assert review_create.call_args.kwargs["user_id"] == REVIEWER_USER_ID
    assert review_create.call_args.kwargs["created_by_user_id"] == REVIEWER_USER_ID
    assert review_create.call_args.kwargs["expense_id"] == 101
    assert review_create.call_args.kwargs["bill_id"] is None
    assert review_create.call_args.kwargs["review_status_id"] == 30
    assert result["reviewer_user_id"] == REVIEWER_USER_ID
    assert result["decision_applied"] == "approved"
    assert result["is_draft"] is True


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.expense.business.service.SubCostCodeService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_approved_omitted_description_passes_none_not_blank(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    """Would fail if description were coerced to '' (blanking ExpenseLineItem.Description)."""
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [_line_item()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
    ReviewRepo.return_value.create.return_value = SimpleNamespace(review_status_id=30)

    with system_authz():
        svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            reviewer_email="pm@example.com",
            sub_cost_code_public_id=SCC_PID,
            description=None,
        )

    _, kwargs = EliSvc.return_value.update_by_public_id.call_args
    assert kwargs.get("description") is None


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.expense.business.service.SubCostCodeService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_reviewer_email_normalized_strip_and_lower(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    """Would fail if .strip() or .lower() were dropped on reviewer_email matching."""
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [_line_item()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=30)

    with system_authz():
        result = svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="approved",
            reviewer_email="  PM@Example.COM  ",
            sub_cost_code_public_id=SCC_PID,
        )

    assert review_create.call_args.kwargs["user_id"] == REVIEWER_USER_ID
    assert result["reviewer_user_id"] == REVIEWER_USER_ID
    assert result["decision_applied"] == "approved"


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_rejected_multi_line_expense_succeeds_without_line_item_update(
    RecipSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    """Rejection must not hit the single-line gate; would fail if gate were hoisted outside approval."""
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [
        _line_item("eli-a"),
        _line_item("eli-b"),
    ]
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    with system_authz():
        result = svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
        )

    EliSvc.return_value.update_by_public_id.assert_not_called()
    assert review_create.call_args.kwargs["review_status_id"] == 100
    assert review_create.call_args.kwargs["user_id"] == REVIEWER_USER_ID
    assert result["decision_applied"] == "rejected"


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_rejected_zero_line_items_succeeds_without_line_item_update(
    RecipSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = []
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    with system_authz():
        result = svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
        )

    EliSvc.return_value.update_by_public_id.assert_not_called()
    assert review_create.call_args.kwargs["review_status_id"] == 100
    assert result["decision_applied"] == "rejected"


@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_refuses_when_not_draft(RecipSvc):
    svc = _expense_service_stub(is_draft=False)
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    with system_authz():
        with pytest.raises(ValueError, match="no longer a draft"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
            )


@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_refuses_unauthorized_reviewer_email(RecipSvc):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    with system_authz():
        with pytest.raises(ValueError, match="not an authorized reviewer"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="stranger@example.com",
                sub_cost_code_public_id=SCC_PID,
            )


@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_refuses_reviewer_email_not_on_empty_recipient_envelope(RecipSvc):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {"to": [], "cc": []}
    with system_authz():
        with pytest.raises(ValueError, match="not an authorized reviewer"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
            )


@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_refuses_zero_line_items(RecipSvc, EliSvc):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = []
    with system_authz():
        with pytest.raises(ValueError, match="no line items"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
            )


@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_refuses_multi_line_expense(RecipSvc, EliSvc):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [
        _line_item("a"),
        _line_item("b"),
    ]
    with system_authz():
        with pytest.raises(ValueError, match="multi-line"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
                sub_cost_code_public_id=SCC_PID,
            )


@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_approved_without_sub_cost_code_refused(RecipSvc, EliSvc):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [_line_item()]
    with system_authz():
        with pytest.raises(ValueError, match="sub_cost_code_public_id is required"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="approved",
                reviewer_email="pm@example.com",
            )


@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_invalid_decision_refused(RecipSvc):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    with system_authz():
        with pytest.raises(ValueError, match="decision must be 'approved' or 'rejected'"):
            svc.apply_reviewer_decision(
                expense_public_id=EXPENSE_PID,
                decision="declined",
                reviewer_email="pm@example.com",
            )


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_raw_reply_text_none_leaves_comments_null(
    RecipSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [_line_item()]
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    with system_authz():
        svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
            raw_reply_text=None,
        )

    assert review_create.call_args.kwargs["comments"] is None
    assert review_create.call_args.kwargs["review_status_id"] == 100


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_raw_reply_text_whitespace_becomes_null_comments(
    RecipSvc,
    RsSvc,
    ReviewRepo,
):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    with system_authz():
        svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
            raw_reply_text="   ",
        )

    assert review_create.call_args.kwargs["comments"] is None


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_raw_reply_text_is_stored_stripped(
    RecipSvc,
    RsSvc,
    ReviewRepo,
):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    with system_authz():
        svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
            raw_reply_text="  Please recode  ",
        )

    assert review_create.call_args.kwargs["comments"] == "Please recode"


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.email_message.business.service.EmailMessageService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_reviewer_email_message_public_id_threads_to_review_row(
    RecipSvc,
    EmailSvc,
    RsSvc,
    ReviewRepo,
):
    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EmailSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=777)
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Declined")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=100)

    with system_authz():
        svc.apply_reviewer_decision(
            expense_public_id=EXPENSE_PID,
            decision="rejected",
            reviewer_email="pm@example.com",
            reviewer_email_message_public_id="em-pub-42",
        )

    EmailSvc.return_value.read_by_public_id.assert_called_once_with(
        public_id="em-pub-42"
    )
    assert review_create.call_args.kwargs["email_message_id"] == 777


@patch("entities.review.persistence.repo.ReviewRepository")
@patch("entities.review_status.business.service.ReviewStatusService")
@patch("entities.expense_line_item.business.service.ExpenseLineItemService")
@patch("entities.expense.business.service.SubCostCodeService")
@patch("entities.review.business.recipient_service.ReviewRecipientService")
def test_review_user_id_is_reviewer_not_caller(
    RecipSvc,
    SccSvc,
    EliSvc,
    RsSvc,
    ReviewRepo,
):
    """Would fail if UserId were taken from the authenticated caller (33)."""
    from shared.authz.context import set_authz_context

    svc = _expense_service_stub()
    RecipSvc.return_value.resolve_for_expense.return_value = {
        "to": [_pm_recipient()],
        "cc": [],
    }
    EliSvc.return_value.read_by_expense_id.return_value = [_line_item()]
    SccSvc.return_value.read_by_public_id.return_value = SimpleNamespace(id=9001)
    RsSvc.return_value.read_all.return_value = _approved_statuses()
    RsSvc.return_value.read_by_id.return_value = SimpleNamespace(name="Approved")
    review_create = ReviewRepo.return_value.create
    review_create.return_value = SimpleNamespace(review_status_id=30)

    set_authz_context(user_id=CALLER_USER_ID, company_id=1, is_system_admin=True)

    svc.apply_reviewer_decision(
        expense_public_id=EXPENSE_PID,
        decision="approved",
        reviewer_email="pm@example.com",
        sub_cost_code_public_id=SCC_PID,
    )

    assert review_create.call_args.kwargs["user_id"] == REVIEWER_USER_ID
    assert review_create.call_args.kwargs["user_id"] != CALLER_USER_ID
