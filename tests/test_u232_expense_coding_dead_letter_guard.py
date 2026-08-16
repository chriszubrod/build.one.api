"""U-232 follow-up — the expense-coding confirm route maps the shared QBO
outbox dead-letter guard to a clean 409 instead of falling through
raise_database_error's `raise error` fallback into a bare unhandled 500
(caught in code review: entities/expense_coding_item/business/service.py's
confirm() already commits Status='confirmed' via record_confirmation()
before calling QboOutboxService().enqueue(), so an unmapped exception here
left the item looking confirmed while never reaching QBO — the exact
failure mode U-058a fixed)."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from entities.expense_coding_item.api import router as router_mod
from entities.expense_coding_item.api.schemas import ConfirmExpenseCodingItemRequest
from integrations.intuit.qbo.outbox.business.service import QboOutboxDeadLetterExistsError
from shared.authz import current_user_id


def test_confirm_expense_coding_router_refuses_with_409_when_dead_letter_exists():
    existing = MagicMock(claimed_by_user_id=17)
    project = MagicMock(id=1)
    sub_cost_code = MagicMock(id=2)

    dead_letter_error = QboOutboxDeadLetterExistsError(
        entity_type="ExpenseCodingItem",
        entity_public_id="pub-1",
        kind="recode_purchase_line",
        dead_letter_public_id="dead-letter-outbox-9",
    )

    body = ConfirmExpenseCodingItemRequest(
        project_public_id="proj-pub-1",
        sub_cost_code_public_id="scc-pub-1",
        description="desc",
        was_overridden=False,
    )

    token = current_user_id.set(17)
    try:
        with patch.object(router_mod, "ExpenseCodingItemService") as service_cls, patch(
            "entities.project.business.service.ProjectService"
        ) as project_svc_cls, patch(
            "entities.sub_cost_code.business.service.SubCostCodeService"
        ) as scc_svc_cls:
            service_cls.return_value.read_by_public_id.return_value = existing
            service_cls.return_value.confirm.side_effect = dead_letter_error
            project_svc_cls.return_value.read_by_public_id.return_value = project
            scc_svc_cls.return_value.read_by_public_id.return_value = sub_cost_code

            with pytest.raises(HTTPException) as exc_info:
                router_mod.confirm_expense_coding_item_router(
                    public_id="pub-1",
                    body=body,
                    _={"sub": "user"},
                )
    finally:
        current_user_id.reset(token)

    assert exc_info.value.status_code == 409
    assert "retry_qbo_outbox_dead_letters.py" in exc_info.value.detail
    assert "dead-letter-outbox-9" in exc_info.value.detail
