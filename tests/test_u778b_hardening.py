"""U-778b Pass 3 hardening: fail-closed repo arg check (H1) and system-caller gate on the service read (H2)."""
import pytest

import entities.invoice_line_item.persistence.repo as repo_mod
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository
from shared.access import EntityNotAccessibleError
from shared.authz.context import system_authz


def _forbid_connection(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("get_connection must not be entered")

    monkeypatch.setattr(repo_mod, "get_connection", _boom)


@pytest.mark.parametrize("kwargs", [
    {},
    {"bill_line_item_id": 1, "expense_line_item_id": 2},
    {"bill_line_item_id": 1, "expense_line_item_id": 2, "bill_credit_line_item_id": 3},
])
def test_repo_refuses_zero_or_several_source_ids_before_connecting(monkeypatch, kwargs):
    _forbid_connection(monkeypatch)
    with pytest.raises(ValueError):
        InvoiceLineItemRepository().read_ids_by_source_line_item(**kwargs)


class _SpyRepo:
    def __init__(self):
        self.calls = []

    def read_ids_by_source_line_item(self, **kw):
        self.calls.append(kw)
        return [(1, 2)]


def test_service_read_refused_without_system_context_and_never_touches_repo():
    spy = _SpyRepo()
    with pytest.raises(EntityNotAccessibleError):
        InvoiceLineItemService(repo=spy).read_ids_by_source_line_item("BillLineItem", 5)
    assert spy.calls == []


def test_service_read_allowed_inside_system_authz():
    spy = _SpyRepo()
    with system_authz():
        assert InvoiceLineItemService(repo=spy).read_ids_by_source_line_item("BillLineItem", 5) == [(1, 2)]
    assert spy.calls == [{"bill_line_item_id": 5}]
