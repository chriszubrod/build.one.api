import inspect

from entities.bill_credit_line_item.business.service import BillCreditLineItemService


def test_create_accepts_tenant_id_for_instant_workflow_dispatcher():
    # The instant-workflow dispatcher (core/workflow/business/instant.py) injects
    # tenant_id into EVERY service call as a keyword. create() must accept it as a
    # keyword-only param with a default so both the dispatcher and direct callers work.
    params = inspect.signature(BillCreditLineItemService.create).parameters
    assert "tenant_id" in params
    tenant_id = params["tenant_id"]
    assert tenant_id.kind is inspect.Parameter.KEYWORD_ONLY
    assert tenant_id.default is None


def test_delete_by_public_id_accepts_tenant_id_for_instant_workflow_dispatcher():
    params = inspect.signature(BillCreditLineItemService.delete_by_public_id).parameters
    assert "tenant_id" in params
    tenant_id = params["tenant_id"]
    assert tenant_id.kind is inspect.Parameter.KEYWORD_ONLY
    assert tenant_id.default is None
