"""U-468 — Expense gets the terminal lock + safe delete cascade Bill got in
U-446b/U-446c.

A completed Expense's AP has already reached QBO, SharePoint, Excel and Box.
Until this unit nothing stopped you editing it afterwards. The contract is
HTTP 422 with `error_code: "status_locked"`, never 409.

TWO layers, both required: Python `assert_editable` is an RCSI snapshot and
is raceable on its own; every mutation sproc re-checks the parent under
`UPDLOCK, HOLDLOCK` inside its own writing transaction.

The QBO purchase HIT path updates COMPLETED Expenses by design — that is
why `update_by_public_id` carries `_via_completion_pipeline` / `is_exempt`,
matching Bill. `tests/test_ls01d_pull_does_not_own_the_lifecycle.py` only
parametrized the Bill connectors; this file proves the purchase HIT still
lands.

Bill's `BillLineItemService.read_all` is sproc-scoped (not a per-row
`assert_can_access_*`). Expense matches that shape. `ExpenseLineItemService.
read_all` and `ExpenseLineItemAttachmentService` were unscoped; they are
not, because those reads chain into a destructive delete.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.access import EntityNotAccessibleError
from shared.authz import clear_authz_context, set_authz_context
from shared.lifecycle.terminal_lock import (
    STATUS_LOCKED_PREFIX,
    StatusLockedError,
)


@pytest.fixture(autouse=True)
def _clean_authz():
    clear_authz_context()
    yield
    clear_authz_context()


from tests.sproc_text import strip_sql_comments as _executable


def _expense(status="completed", is_draft=False, **over):
    base = dict(
        id=99,
        public_id="exp-1",
        row_version="AAAA",
        vendor_id=7,
        expense_date="2026-09-01",
        reference_number="R-1",
        total_amount=None,
        memo=None,
        is_draft=is_draft,
        is_credit=False,
        status=status,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _eli(expense_id=99, public_id="eli-1"):
    return SimpleNamespace(
        id=3,
        public_id=public_id,
        expense_id=expense_id,
        row_version="AAAA",
        sub_cost_code_id=None,
        project_id=None,
        description="d",
        quantity=1,
        rate=None,
        amount=None,
        is_billable=True,
        is_billed=False,
        markup=None,
        price=None,
        is_draft=True,
        row_version_bytes=b"\x00" * 8,
    )


def _expense_service(existing=None):
    from entities.expense.business.service import ExpenseService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=existing)
    return svc


def _eli_service(existing=None):
    from entities.expense_line_item.business.service import ExpenseLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=existing)
    return svc


# ---------------------------------------------------------------------------
# 1. Terminal lock — Python layer (422, each locked path)
# ---------------------------------------------------------------------------


def test_the_error_surfaces_as_422_status_locked_not_409():
    from shared.api.errors import ApiError, ErrorCode
    from shared.api.responses import raise_workflow_error

    err = StatusLockedError("its header cannot be changed")
    assert str(err).startswith(STATUS_LOCKED_PREFIX)

    with pytest.raises(ApiError) as exc:
        raise_workflow_error(str(err), "Failed to update expense")
    assert exc.value.status_code == 422
    assert exc.value.status_code != 409
    assert exc.value.error_code == ErrorCode.STATUS_LOCKED


def test_expense_header_update_is_refused_on_a_completed_expense():
    from entities.expense.business.service import ExpenseService

    svc = _expense_service(_expense())
    with pytest.raises(StatusLockedError, match="header"):
        svc.update_by_public_id(public_id="exp-1", row_version="AAAA", memo="tampered")
    svc.repo.update_by_id.assert_not_called()


def test_an_ordinary_user_cannot_delete_a_completed_expense():
    svc = _expense_service(_expense())
    with pytest.raises(StatusLockedError, match="cannot be deleted"):
        svc.delete_by_public_id("exp-1")
    svc.repo.delete_by_id.assert_not_called()
    svc.repo.read_citing_invoice_for_expense_id.assert_not_called()


def test_creating_a_line_on_a_completed_expense_is_refused_and_writes_NOTHING():
    svc = _eli_service()
    with patch("entities.expense.business.service.ExpenseService") as MockExp:
        MockExp.return_value.read_by_public_id.return_value = _expense()
        with pytest.raises(StatusLockedError, match="added to it"):
            svc.create(expense_public_id="exp-1", description="sneaking one in")
    svc.repo.create.assert_not_called()


def test_updating_a_line_on_a_completed_expense_is_refused_and_writes_NOTHING():
    svc = _eli_service(existing=_eli())
    with patch("entities.expense.business.service.ExpenseService") as MockExp:
        MockExp.return_value.read_by_id.return_value = _expense()
        with pytest.raises(StatusLockedError, match="line items cannot be changed"):
            svc.update_by_public_id("eli-1", row_version="AAAA", amount=999)
    svc.repo.update_by_id.assert_not_called()


def test_deleting_a_line_from_a_completed_expense_is_refused_and_writes_NOTHING():
    svc = _eli_service(existing=_eli())
    with patch("entities.expense.business.service.ExpenseService") as MockExp:
        MockExp.return_value.read_by_id.return_value = _expense()
        with pytest.raises(StatusLockedError, match="deleted"):
            svc.delete_by_public_id("eli-1")
    svc.repo.delete_by_id.assert_not_called()


def test_a_line_cannot_be_MOVED_ONTO_a_completed_expense():
    svc = _eli_service(existing=_eli(expense_id=1))
    with patch("entities.expense.business.service.ExpenseService") as MockExp:
        MockExp.return_value.read_by_id.return_value = _expense(
            status="draft", is_draft=True, id=1
        )
        MockExp.return_value.read_by_public_id.return_value = _expense()
        with pytest.raises(StatusLockedError, match="moved onto it"):
            svc.update_by_public_id(
                "eli-1", row_version="AAAA", expense_public_id="exp-1"
            )
    svc.repo.update_by_id.assert_not_called()


def test_a_draft_expense_is_still_freely_editable():
    repo = MagicMock()
    repo.update_by_id.return_value = _expense(status="draft", is_draft=True)
    svc = _expense_service(_expense(status="in_review", is_draft=True))
    svc.repo = repo
    svc.update_by_public_id(public_id="exp-1", row_version="AAAA", memo="fine")
    repo.update_by_id.assert_called_once()
    assert repo.update_by_id.call_args.kwargs.get("allow_terminal_parent") is False


def test_the_completion_pipeline_may_still_update_the_header():
    """`_via_completion_pipeline` is internal-only — the router's payload
    never includes it, so no HTTP caller can acquire it."""
    repo = MagicMock()
    repo.update_by_id.return_value = _expense()
    svc = _expense_service(_expense())
    svc.repo = repo
    svc.update_by_public_id(
        public_id="exp-1",
        row_version="AAAA",
        memo="from qbo",
        _via_completion_pipeline=True,
    )
    repo.update_by_id.assert_called_once()
    assert repo.update_by_id.call_args.kwargs.get("allow_terminal_parent") is True


def test_completion_forwards_the_exemption_to_its_OWN_line_finalize():
    """`complete_expense` sets the header to completed in Step 1, then marks
    each draft line in Step 2 — against a parent that now reads terminal."""
    from entities.expense.business.service import ExpenseService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.finalize_by_id.return_value = _expense()
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(
        return_value=_expense(status="in_review", is_draft=True)
    )
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="vendor-1")
    svc.project_service = MagicMock()
    svc._expense_line_item_service = MagicMock()
    svc._expense_line_item_service.read_by_expense_id.return_value = [_eli()]

    try:
        svc.complete_expense(public_id="exp-1")
    except Exception:
        pass

    svc.expense_line_item_service.update_by_public_id.assert_called_once()
    kwargs = svc.expense_line_item_service.update_by_public_id.call_args.kwargs
    assert kwargs.get("_via_internal_pipeline") is True, (
        "completion must exempt its own line finalize. Got: "
        f"{sorted(kwargs)}"
    )


# ---------------------------------------------------------------------------
# 1b. SQL layer — UPDLOCK/HOLDLOCK inside the writing transaction
# ---------------------------------------------------------------------------


_GUARDED_SPROCS = [
    ("entities/expense/sql/dbo.expense.sql", "UpdateExpenseById"),
    ("entities/expense/sql/dbo.expense.sql", "DeleteExpenseById"),
    ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "CreateExpenseLineItem"),
    ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "UpdateExpenseLineItemById"),
    ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "DeleteExpenseLineItemById"),
    (
        "entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
        "CreateExpenseLineItemAttachment",
    ),
    (
        "entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
        "DeleteExpenseLineItemAttachmentById",
    ),
]


@pytest.mark.parametrize("sql_rel,proc", _GUARDED_SPROCS)
def test_every_expense_mutation_sproc_checks_the_parent_under_UPDLOCK(sql_rel, proc):
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert "UPDLOCK" in body, f"{proc} reads the parent without locking it"
    assert "HOLDLOCK" in body, f"{proc} must hold the lock to the commit"
    assert "WITH (UPDLOCK, HOLDLOCK)" in body
    assert "'completed'" in body, f"{proc} does not test the terminal state"
    assert "@AllowTerminalParent = 0" in body, f"{proc} cannot be exempted"
    assert "STATUS_LOCKED:" in body
    assert "SET NOCOUNT ON" in body
    assert "ROLLBACK" not in body.upper()
    assert body.index("COMMIT TRANSACTION") < body.index("RAISERROR")


@pytest.mark.parametrize("sql_rel,proc", _GUARDED_SPROCS)
def test_the_new_param_is_optional_and_fails_CLOSED(sql_rel, proc):
    from tests.sproc_text import REPO_ROOT, sproc_params

    params = sproc_params(REPO_ROOT / sql_rel, proc)
    assert "@AllowTerminalParent BIT = 0" in params
    assert "@AllowTerminalParent BIT = 1" not in params


def test_a_sproc_refusal_reaches_python_as_a_typed_error():
    from entities.expense.persistence.repo import ExpenseRepository
    import entities.expense.persistence.repo as mod

    repo = ExpenseRepository()

    def _raise(*a, **k):
        raise Exception(
            "[42000] [SQL Server]STATUS_LOCKED: a completed Expense cannot "
            "be deleted. (50000)"
        )

    with patch.object(mod, "call_procedure", _raise), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError, match="cannot be deleted"):
            repo.delete_by_id(1, allow_terminal_parent=False)


# ---------------------------------------------------------------------------
# 1c. QBO purchase HIT still updates a completed Expense
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_the_qbo_purchase_hit_path_still_updates_a_completed_expense():
    """The exemption is the load-bearing one: pulled purchases land
    status='completed', and the unattended 15-minute job writes what
    QuickBooks already holds. Without `_via_completion_pipeline` this
    raises StatusLockedError on every HIT."""
    from entities.expense.business.service import ExpenseService
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        PurchaseExpenseConnector,
    )

    completed = _expense()
    repo = MagicMock()
    repo.update_by_id.return_value = completed
    expense_svc = ExpenseService(repo=repo)
    expense_svc.read_by_public_id = MagicMock(return_value=completed)
    expense_svc.read_by_qbo_identity = MagicMock(return_value=completed)

    line_path = (
        "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
        ".PurchaseLineExpenseLineItemConnector"
    )
    with patch(line_path, return_value=MagicMock()):
        connector = PurchaseExpenseConnector(expense_service=expense_svc)
    connector._get_vendor_public_id = MagicMock(return_value="vendor-pub-1")
    connector._sync_line_items = MagicMock()

    qbo_purchase = SimpleNamespace(
        id=4,
        qbo_id="PURCH-99",
        realm_id="realm-1",
        entity_ref_value="V-1",
        doc_number="INV-100",
        txn_date="2026-08-01",
        private_note="memo",
        total_amt=100,
        credit=False,
        sync_token="3",
    )
    with patch("entities.expense.business.service.VendorService") as MockVendor, patch(
        "integrations.intuit.qbo.purchase.connector.expense.business.service"
        ".guard_lines_present"
    ):
        MockVendor.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        result = connector.sync_from_qbo_purchase(qbo_purchase, [SimpleNamespace(id=1)])

    assert result is completed
    repo.update_by_id.assert_called_once()
    assert repo.update_by_id.call_args.kwargs.get("allow_terminal_parent") is True


def test_the_qbo_purchase_line_mutations_are_exempted():
    """Same gap as Bill: POST /sync/qbo-purchases runs under a human JWT.
    Bound per-CALL so dropping the kwarg from one of the three stays red."""
    import ast

    from tests.sproc_text import REPO_ROOT

    path = (
        REPO_ROOT
        / "integrations/intuit/qbo/purchase/connector/expense_line_item/business/service.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded = {"create", "update_by_public_id", "delete_by_public_id"}
    found: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in guarded:
            continue
        target = node.func.value
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "expense_line_item_service"
        ):
            continue
        exempted = any(
            kw.arg == "_via_internal_pipeline"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        found[node.func.attr] = found.get(node.func.attr, True) and exempted
    assert set(found) == guarded, (
        f"expected the QBO pull to mutate lines via all of {sorted(guarded)}; "
        f"found {sorted(found)}"
    )
    unexempted = sorted(name for name, ok in found.items() if not ok)
    assert not unexempted, (
        f"the QBO pull's {unexempted} call(s) would be refused on a completed "
        "Expense when triggered from POST /sync/qbo-purchases"
    )


# ---------------------------------------------------------------------------
# 2. Atomic cascade — one transaction under the header lock (U-468 rebuild)
# ---------------------------------------------------------------------------

EXPENSE_SQL = "entities/expense/sql/dbo.expense.sql"
CASCADE = "DeleteExpenseCascadeById"


def _cascade_body():
    from tests.sproc_text import REPO_ROOT, sproc_body

    return _executable(sproc_body(REPO_ROOT / EXPENSE_SQL, CASCADE))


def test_the_lock_free_precheck_and_review_sprocs_are_gone():
    from tests.sproc_text import REPO_ROOT, sproc_body

    with pytest.raises(AssertionError, match="ReadInvoiceCitingExpenseById"):
        sproc_body(REPO_ROOT / EXPENSE_SQL, "ReadInvoiceCitingExpenseById")
    with pytest.raises(AssertionError, match="DeleteReviewsByExpenseId"):
        sproc_body(
            REPO_ROOT / "entities/review/sql/dbo.review.sql",
            "DeleteReviewsByExpenseId",
        )
    from entities.review.persistence.repo import ReviewRepository
    from entities.expense.persistence.repo import ExpenseRepository

    assert not hasattr(ReviewRepository, "delete_by_expense_id")
    assert not hasattr(ExpenseRepository, "read_citing_invoice_for_expense_id")


def test_deleting_an_invoiced_expense_refuses_and_destroys_nothing():
    """The citation check lives in the sproc, under the header lock. Python
    must not destroy blobs/links first — the old TOCTOU did exactly that."""
    from entities.expense.business.service import ExpenseService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    existing = _expense(status="draft", is_draft=True)
    repo = MagicMock()
    repo.delete_cascade_by_id.side_effect = ValueError(
        "Cannot delete this expense: invoice INV-9 still cites one of its line items."
    )
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as MockELI, patch(
        "entities.expense_line_item_attachment.business.service.ExpenseLineItemAttachmentService"
    ) as MockELIA, patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as MockELIARepo, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAtt, patch(
        "entities.review.persistence.repo.ReviewRepository"
    ) as MockRev, patch(
        "shared.storage.AzureBlobStorage"
    ) as MockStorage:
        with pytest.raises(ValueError, match="invoice INV-9"):
            svc.delete_by_public_id("exp-1")

    repo.delete_cascade_by_id.assert_called_once_with(99, allow_terminal_parent=False)
    repo.delete_by_id.assert_not_called()
    MockELI.assert_not_called()
    MockELIA.assert_not_called()
    MockELIARepo.assert_not_called()
    MockAtt.assert_not_called()
    MockStorage.assert_not_called()
    MockRev.assert_not_called()
    MockStorage.return_value.delete_file.assert_not_called()
    MockELIARepo.return_value.delete_by_id.assert_not_called()
    MockAtt.return_value.delete_by_public_id.assert_not_called()

    import inspect as _inspect
    src = _inspect.getsource(ExpenseService.delete_by_public_id)
    assert "AzureBlobStorage" not in src
    assert "delete_file" not in src
    assert "_assert_not_cited" not in src


def test_the_invoiced_check_is_inside_the_transaction_under_the_header_lock():
    """F1: the check must share the header lock with the deletes, not run on
    its own connection. Expense REFUSES; it does not delete invoice lines."""
    body = _cascade_body()
    lock_at = body.index("WITH (UPDLOCK, HOLDLOCK)")
    cite_at = body.index("IF @CitingInvoicePublicId IS NOT NULL")
    cite_msg_at = body.index("still cites one of its line items")
    first_delete = body.index("DELETE")
    assert lock_at < cite_at < first_delete, (
        "the citation check must run after the header lock and before any DELETE"
    )
    assert cite_msg_at < first_delete, (
        "the invoiced RAISERROR must fire before any child is destroyed"
    )
    assert "IF @CitingInvoicePublicId IS NOT NULL AND" not in body, (
        "the citation check must not be disabled in place"
    )
    assert "InvoiceLineItem" in body
    assert "eli.[ExpenseId] = @Id" in body
    assert "RAISERROR" in body
    assert "still cites one of its line items" in body
    assert "DELETE FROM dbo.[InvoiceLineItem]" not in body
    assert "DELETE FROM dbo.[InvoiceLineItemAttachment]" not in body
    assert "DELETE FROM dbo.[InvoiceLineItemSourceProvenance]" not in body
    assert body.index("COMMIT TRANSACTION") < body.index("RAISERROR")
    assert "CURSOR" not in body.upper()
    assert "WHILE" not in body.upper()


def test_the_expense_service_makes_ONE_repo_call():
    """The Python cascade is gone: no blob delete, no attachment delete, no
    link delete, no ReviewRepository call. One sproc, one transaction."""
    import inspect

    from entities.expense.business.service import ExpenseService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    existing = _expense(status="draft", is_draft=True)
    repo = MagicMock()
    repo.delete_cascade_by_id.return_value = existing
    svc = ExpenseService(repo=repo)
    svc.read_by_public_id = MagicMock(return_value=existing)

    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as MockELI, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAtt, patch(
        "entities.review.persistence.repo.ReviewRepository"
    ) as MockRev, patch(
        "shared.storage.AzureBlobStorage"
    ) as MockStorage, patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as MockELIARepo:
        assert svc.delete_by_public_id("exp-1") is existing

    repo.delete_cascade_by_id.assert_called_once_with(99, allow_terminal_parent=False)
    repo.delete_by_id.assert_not_called()
    MockELI.assert_not_called()
    MockAtt.assert_not_called()
    MockRev.assert_not_called()
    MockStorage.assert_not_called()
    MockELIARepo.assert_not_called()
    MockStorage.return_value.delete_file.assert_not_called()
    MockAtt.return_value.delete_by_public_id.assert_not_called()
    MockELIARepo.return_value.delete_by_id.assert_not_called()

    src = inspect.getsource(ExpenseService.delete_by_public_id)
    for gone in (
        "AzureBlobStorage",
        "ExpenseLineItemService",
        "AttachmentService",
        "ReviewRepository",
        "_assert_not_cited",
        "repo.delete_by_id",
    ):
        assert gone not in src, f"{gone} still runs in Python on the delete path"
    assert "delete_cascade_by_id" in src


def test_the_parent_is_locked_before_any_child_is_touched():
    body = _cascade_body()
    lock_at = body.index("WITH (UPDLOCK, HOLDLOCK)")
    first_delete = body.index("DELETE")
    assert lock_at < first_delete, "the cascade writes before it locks the parent"
    assert "HOLDLOCK" in body
    assert "SET NOCOUNT ON" in body
    assert "ROLLBACK" not in body.upper()
    assert body.count("BEGIN TRANSACTION") == 1


def test_the_cascade_contains_no_review_entry_reference():
    """Deploy-blocker: SQL Server binds column refs at CREATE PROCEDURE time
    when the table exists. prod ReviewEntry is Bill-only (no ExpenseId), so
    a ReviewEntry.ExpenseId reference fails Msg 207 and the rest of the
    batch never applies. COL_LENGTH is a RUNTIME guard and cannot save it.
    Pin the executable body, not a comment."""
    body = _cascade_body()
    assert "ReviewEntry" not in body


def test_a_completed_expense_is_refused_unless_allow_terminal_parent():
    body = _cascade_body()
    assert "@AllowTerminalParent = 0 AND @Status = 'completed'" in body
    assert "a completed Expense cannot be deleted" in body
    assert "@AllowTerminalParent BIT = 0" in body
    assert "@AllowTerminalParent BIT = 1" not in body

    from entities.expense.persistence.repo import ExpenseRepository
    import entities.expense.persistence.repo as mod

    repo = ExpenseRepository()

    def _raise(*a, **k):
        raise Exception(
            "[42000] [SQL Server]STATUS_LOCKED: a completed Expense cannot "
            "be deleted. (50000)"
        )

    with patch.object(mod, "call_procedure", _raise), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError, match="cannot be deleted"):
            repo.delete_cascade_by_id(1, allow_terminal_parent=False)

    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        captured["name"] = name
        raise Exception(
            "[42000] [SQL Server]STATUS_LOCKED: a completed Expense cannot "
            "be deleted. (50000)"
        )

    with patch.object(mod, "call_procedure", _capture), patch.object(
        mod, "get_connection"
    ):
        with pytest.raises(StatusLockedError):
            repo.delete_cascade_by_id(1, allow_terminal_parent=True)
    assert captured["name"] == "DeleteExpenseCascadeById"
    assert captured.get("AllowTerminalParent") == 1


def test_a_missing_expense_yields_none_not_a_pyodbc_no_results_raise():
    """A bare RETURN on a missing row produces NO result set, and pyodbc's
    fetchone() raises 'No results. Previous SQL was not a query'. The
    cascade must fall through to the OUTPUT DELETE, which yields empty."""
    body = _cascade_body()
    assert body.count("RETURN;") == body.count("RAISERROR")
    assert "IF @Status IS NULL" not in body

    from entities.expense.persistence.repo import ExpenseRepository
    import entities.expense.persistence.repo as mod

    repo = ExpenseRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    with patch.object(mod, "call_procedure"), patch.object(
        mod, "get_connection", return_value=conn
    ):
        assert repo.delete_cascade_by_id(1, allow_terminal_parent=False) is None


def test_the_invoiced_refusal_surfaces_as_value_error_not_swallowed():
    from entities.expense.persistence.repo import ExpenseRepository
    import entities.expense.persistence.repo as mod

    repo = ExpenseRepository()

    def _raise(*a, **k):
        raise Exception(
            "[42000] [SQL Server]Cannot delete this expense: invoice INV-9 "
            "still cites one of its line items. (50000)"
        )

    with patch.object(mod, "call_procedure", _raise), patch.object(mod, "get_connection"):
        with pytest.raises(ValueError, match="invoice INV-9"):
            repo.delete_cascade_by_id(1, allow_terminal_parent=False)


def test_attachment_rows_and_blobs_survive_the_cascade():
    body = _cascade_body()
    assert "DELETE FROM dbo.[Attachment]" not in body
    assert "DELETE FROM dbo.[ExpenseLineItemAttachment]" in body
    assert body.index("DELETE FROM dbo.[ExpenseLineItemAttachment]") < body.index(
        "DELETE FROM dbo.[ExpenseLineItem]"
    )
    assert body.index("DELETE FROM dbo.[ExpenseLineItem]") < body.index(
        "DELETE FROM dbo.[Review]"
    )
    assert "DELETED.[QboId]" in body and "DELETED.[RealmId]" in body
    assert "DELETED.[Status]" in body


def test_the_admin_decision_reaches_the_cascade_transaction():
    from shared.authz import system_authz
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService(repo=MagicMock())
    existing = _expense()
    svc.read_by_public_id = MagicMock(return_value=existing)
    svc.repo.delete_cascade_by_id.return_value = existing

    with system_authz():
        svc.delete_by_public_id("exp-1")
    svc.repo.delete_cascade_by_id.assert_called_once_with(
        99, allow_terminal_parent=True
    )


# ---------------------------------------------------------------------------
# 4. Child-entity row scoping
# ---------------------------------------------------------------------------


def test_expense_line_item_read_all_passes_the_actor_to_the_sproc():
    """Bill's read_all is likewise sproc-scoped (not per-row
    assert_can_access_*). Expense matches that, and was previously unscoped."""
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    path = REPO_ROOT / "entities/expense_line_item/sql/dbo.expense_line_item.sql"
    params = sproc_params(path, "ReadExpenseLineItems")
    body = sproc_body(path, "ReadExpenseLineItems")
    assert "@ActorUserId" in params and "@ActorIsSystemAdmin" in params
    assert "UserCanAccessExpense" in body

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.read_all.return_value = []
    ExpenseLineItemService(repo=repo).read_all()
    repo.read_all.assert_called_once_with(
        actor_user_id=20, actor_is_system_admin=False
    )


def test_expense_line_item_read_by_id_refuses_an_out_of_scope_actor():
    from entities.expense_line_item.business.service import ExpenseLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.read_by_id.return_value = _eli()
    svc = ExpenseLineItemService(repo=repo)
    with patch(
        "entities.expense_line_item.business.service.assert_can_access_expense",
        side_effect=EntityNotAccessibleError("Expense", 99),
    ):
        with pytest.raises(EntityNotAccessibleError):
            svc.read_by_id(3)


def test_expense_line_item_attachment_read_all_passes_the_actor_to_the_sproc():
    from entities.expense_line_item_attachment.business.service import (
        ExpenseLineItemAttachmentService,
    )
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    path = (
        REPO_ROOT
        / "entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql"
    )
    params = sproc_params(path, "ReadExpenseLineItemAttachments")
    body = sproc_body(path, "ReadExpenseLineItemAttachments")
    assert "@ActorUserId" in params and "@ActorIsSystemAdmin" in params
    assert "UserCanAccessExpense" in body

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.read_all.return_value = []
    ExpenseLineItemAttachmentService(repo=repo).read_all()
    repo.read_all.assert_called_once_with(
        actor_user_id=20, actor_is_system_admin=False
    )


def test_expense_line_item_attachment_read_by_id_refuses_an_out_of_scope_actor():
    from entities.expense_line_item_attachment.business.service import (
        ExpenseLineItemAttachmentService,
    )

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    repo = MagicMock()
    repo.read_by_id.return_value = SimpleNamespace(
        id=2, expense_line_item_id=3, attachment_id=9
    )
    svc = ExpenseLineItemAttachmentService(repo=repo)
    with patch(
        "entities.expense_line_item_attachment.business.service.ExpenseLineItemService"
    ) as MockELI:
        MockELI.return_value.read_by_id.side_effect = EntityNotAccessibleError(
            "Expense", 99
        )
        with pytest.raises(EntityNotAccessibleError):
            svc.read_by_id(2)


# ---------------------------------------------------------------------------
# P1 — the Attachment FILE, not just the link row
# ---------------------------------------------------------------------------


def _attachment_file_service(*, completed_bills=0, completed_expenses=0):
    from entities.attachment.business.service import AttachmentService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = AttachmentService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=9, public_id="att-9", row_version="AAAA", blob_url="https://b/x.pdf",
            filename="x.pdf", is_archived=False,
        )
    )
    patchers = [
        patch(
            "entities.bill_line_item_attachment.persistence.repo.BillLineItemAttachmentRepository"
        ),
        patch(
            "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
        ),
    ]
    MockBillRepo = patchers[0].start()
    MockExpRepo = patchers[1].start()
    MockBillRepo.return_value.count_completed_bills_by_attachment_id.return_value = (
        completed_bills
    )
    MockExpRepo.return_value.count_completed_expenses_by_attachment_id.return_value = (
        completed_expenses
    )

    class _Stop:
        def stop(self):
            for p in patchers:
                p.stop()

    return svc, _Stop()


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("update_by_public_id", dict(public_id="att-9", row_version="AAAA", blob_url="https://evil/swap.pdf")),
        ("archive", dict(public_id="att-9")),
        ("unarchive", dict(public_id="att-9")),
        ("delete_by_public_id", dict(public_id="att-9")),
    ],
)
def test_a_completed_expenses_evidence_file_cannot_be_touched(method, kwargs):
    """The link row was guarded; the FILE it points at was not.

    A caller holding plain ATTACHMENTS permissions could repoint `blob_url` at
    different bytes, rename it, or archive it — for the exact PDF the AP was
    approved from — because the Bill-only COUNT returned zero.
    """
    svc, patcher = _attachment_file_service(completed_expenses=1)
    try:
        with pytest.raises(StatusLockedError, match="attachments"):
            getattr(svc, method)(**kwargs)
    finally:
        patcher.stop()
    svc.repo.update_by_id.assert_not_called()
    svc.repo.delete_by_id.assert_not_called()


def test_the_file_guard_consults_the_expense_count_not_just_bills():
    """Dropping the expense COUNT and keeping the bill COUNT leaves every
    completed-Expense receipt editable. Bound on the call, not the source."""
    svc, patcher = _attachment_file_service(completed_bills=0, completed_expenses=1)
    try:
        with pytest.raises(StatusLockedError):
            svc.update_by_public_id(
                public_id="att-9", row_version="AAAA", filename="swapped.pdf"
            )
    finally:
        patcher.stop()


def test_count_completed_expenses_by_attachment_id_is_the_bill_mirror():
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(
        sproc_body(
            REPO_ROOT
            / "entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
            "CountCompletedExpensesByAttachmentId",
        )
    )
    assert "ExpenseLineItemAttachment" in body
    assert "INNER JOIN dbo.[Expense]" in body
    assert "[Status] = 'completed'" in body
    from entities.expense_line_item_attachment.persistence.repo import (
        ExpenseLineItemAttachmentRepository,
    )
    assert hasattr(
        ExpenseLineItemAttachmentRepository, "count_completed_expenses_by_attachment_id"
    )


# ---------------------------------------------------------------------------
# P2 — reraise_if_sproc_status_locked at every repo call site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_mod,repo_cls,method,args,kwargs,build",
    [
        ("entities.expense.persistence.repo", "ExpenseRepository",
         "update_by_id", (), {}, "expense"),
        ("entities.expense.persistence.repo", "ExpenseRepository",
         "delete_by_id", (1,), {}, None),
        ("entities.expense.persistence.repo", "ExpenseRepository",
         "delete_cascade_by_id", (1,), {}, None),
        ("entities.expense_line_item.persistence.repo", "ExpenseLineItemRepository",
         "create", (), {"expense_id": 99, "description": "d"}, None),
        ("entities.expense_line_item.persistence.repo", "ExpenseLineItemRepository",
         "update_by_id", (), {}, "eli"),
        ("entities.expense_line_item.persistence.repo", "ExpenseLineItemRepository",
         "delete_by_id", (1,), {}, None),
        ("entities.expense_line_item_attachment.persistence.repo",
         "ExpenseLineItemAttachmentRepository",
         "create", (), {"expense_line_item_id": 3, "attachment_id": 9}, None),
        ("entities.expense_line_item_attachment.persistence.repo",
         "ExpenseLineItemAttachmentRepository",
         "delete_by_id", (1,), {}, None),
    ],
)
def test_these_repos_send_the_flag_and_map_the_sentinel(
    repo_mod, repo_cls, method, args, kwargs, build
):
    """Two properties at once: the param has to reach the sproc (the REPO
    default is permissive, so an omission is silent), and the sproc's
    RAISERROR has to come back as a typed error rather than a generic 500.

    Eight call sites; drop any one and a sproc refusal becomes a 500 with
    nothing red.
    """
    import importlib

    mod = importlib.import_module(repo_mod)
    repo = getattr(mod, repo_cls)()
    if build == "expense":
        args = (SimpleNamespace(
            id=1, row_version_bytes=b"\x00" * 8, vendor_id=7,
            expense_date="2026-09-01", reference_number="R-1",
            total_amount=None, memo=None, is_draft=False, is_credit=False,
        ),)
    elif build == "eli":
        args = (SimpleNamespace(
            id=3, row_version_bytes=b"\x00" * 8, expense_id=99,
            sub_cost_code_id=None, project_id=None, description="d",
            quantity=1, rate=None, amount=None, is_billable=True,
            is_billed=False, markup=None, price=None, is_draft=True,
        ),)

    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise Exception("[42000] STATUS_LOCKED: refused by the in-transaction guard.")

    with patch.object(mod, "call_procedure", _capture), patch.object(mod, "get_connection"):
        with pytest.raises(StatusLockedError):
            getattr(repo, method)(*args, allow_terminal_parent=False, **kwargs)

    assert captured.get("AllowTerminalParent") == 0, (
        f"{repo_cls}.{method} did not send the flag — the repo defaults "
        "permissive, so this drops the guard with nothing to notice"
    )


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("create", dict(expense_public_id="exp-1", description="d")),
        ("update_by_public_id", dict(public_id="eli-1", row_version="AAAA")),
        ("delete_by_public_id", dict(public_id="eli-1")),
    ],
)
def test_the_repo_always_sends_the_flag_and_sends_0_for_a_human(method, kwargs):
    from entities.expense_line_item.business.service import ExpenseLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemService()
    svc.read_by_public_id = MagicMock(return_value=_eli())
    captured = {}

    def _capture(*, cursor, name, params):
        captured.update(params)
        raise RuntimeError("stop after the call")

    with patch("entities.expense_line_item.persistence.repo.call_procedure", _capture), \
         patch("entities.expense_line_item.persistence.repo.get_connection"), \
         patch("entities.expense.business.service.ExpenseService") as MockExp, \
         patch("entities.expense_line_item.business.service.ExpenseService") as MockExp2, \
         patch(
             "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
         ) as MockLink:
        draft = _expense(status="in_review", is_draft=True)
        for m in (MockExp, MockExp2):
            m.return_value.read_by_id.return_value = draft
            m.return_value.read_by_public_id.return_value = draft
        MockLink.return_value.read_by_expense_line_item_id.return_value = None
        try:
            getattr(svc, method)(**kwargs)
        except Exception:
            pass

    assert "AllowTerminalParent" in captured, (
        f"{method} did not send the flag — the repo defaults permissive, so "
        "this drops the in-transaction guard with no error anywhere"
    )
    assert captured["AllowTerminalParent"] == 0


def test_the_attachment_link_service_also_sends_the_flag_for_a_human():
    from entities.expense_line_item_attachment.business.service import (
        ExpenseLineItemAttachmentService,
    )

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemAttachmentService(repo=MagicMock())
    svc.repo.read_by_public_id.return_value = SimpleNamespace(
        id=5, expense_line_item_id=11
    )
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(id=5))

    with patch("entities.expense_line_item.business.service.ExpenseLineItemService") as MockEli, \
         patch("entities.expense_line_item_attachment.business.service.ExpenseLineItemService") as MockEli2:
        for m in (MockEli, MockEli2):
            m.return_value.read_by_id.return_value = _eli()
        svc.repo.delete_by_id = MagicMock()
        svc.delete_by_public_id(public_id="elia-5")

    assert svc.repo.delete_by_id.call_args.kwargs.get("allow_terminal_parent") is False, (
        "an ordinary user delete must leave the in-transaction guard ON"
    )


# ---------------------------------------------------------------------------
# P2 — ELIA guards and the three _reassert_after_a_lost_write sites
# ---------------------------------------------------------------------------


def test_attachment_mutators_carry_the_guard():
    import inspect

    from entities.expense_line_item_attachment.business.service import (
        ExpenseLineItemAttachmentService,
    )

    for name in ("create", "delete_by_public_id"):
        src = inspect.getsource(getattr(ExpenseLineItemAttachmentService, name))
        assert "_assert_parent_editable" in src, f"attachment {name} is unguarded"


def test_a_lost_reparent_race_surfaces_as_422_not_409_or_404():
    """The in-transaction guards make a wrong write impossible by matching ZERO
    rows — so the loser of the race saw "not found", a row-version 409, or a
    bare repo failure. 409 is the worst: iOS routes it to reload-and-retry."""
    from entities.expense_line_item.business.service import ExpenseLineItemService

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(return_value=_eli())
    svc.repo.delete_by_id.return_value = None

    with patch("entities.expense.business.service.ExpenseService") as MockExp, \
         patch(
             "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
         ) as MockLink, \
         patch("entities.attachment.business.service.AttachmentService"), \
         patch("shared.storage.AzureBlobStorage"):
        MockLink.return_value.read_by_expense_line_item_id.return_value = None
        draft, done = _expense(status="in_review", is_draft=True), _expense()
        MockExp.return_value.read_by_id.side_effect = [draft, done]
        with pytest.raises(StatusLockedError, match="deleted"):
            svc.delete_by_public_id("eli-1")


def test_the_reassert_does_not_relabel_a_transient_fault():
    """Re-asserting on ANY exception meant a deadlock victim or a dropped
    connection came back as `status_locked` whenever the expense happened to
    complete in the meantime — a transient fault reported as a permanent
    refusal, which is the opposite of what the client should act on."""
    import inspect

    from entities.expense_line_item.business.service import ExpenseLineItemService

    src = inspect.getsource(ExpenseLineItemService.update_by_public_id)
    assert "except DatabaseConcurrencyError:" in src, (
        "the reassert must be narrowed to the outcome a lost terminal race "
        "actually produces"
    )
    assert "except Exception:" not in src.split("_reassert_after_a_lost_write")[0][-400:], (
        "a bare `except Exception` around the reassert masks real errors"
    )


def test_the_attachment_link_delete_raises_on_a_lost_race_for_real():
    """The source-shape test could not see the guard turned into a dead branch."""
    import functools

    from entities.expense_line_item.business.service import (
        ExpenseLineItemService as RealELI,
    )
    from entities.expense_line_item_attachment.business.service import (
        ExpenseLineItemAttachmentService,
    )

    set_authz_context(user_id=20, company_id=1, is_system_admin=False)
    svc = ExpenseLineItemAttachmentService(repo=MagicMock())
    svc.repo.read_by_public_id.return_value = SimpleNamespace(
        id=5, expense_line_item_id=11
    )
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(id=5))
    svc.repo.delete_by_id.return_value = None

    with patch("entities.expense_line_item.business.service.ExpenseLineItemService") as MockEli, \
         patch("entities.expense_line_item_attachment.business.service.ExpenseLineItemService") as MockEli2, \
         patch("entities.expense.business.service.ExpenseService") as MockExp:
        for m in (MockEli, MockEli2):
            m.return_value.read_by_id.return_value = _eli()
            m.return_value._assert_parent_editable = functools.partial(
                RealELI._assert_parent_editable, m.return_value
            )
        MockExp.return_value.read_by_id.side_effect = [
            _expense(status="in_review", is_draft=True),
            _expense(),
        ]
        with pytest.raises(StatusLockedError, match="attachments"):
            svc.delete_by_public_id(public_id="elia-5")


# ---------------------------------------------------------------------------
# P3 — SQL pins that assert semantics, not substrings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql_rel,proc,must_contain",
    [
        ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "CreateExpenseLineItem",
         "WHERE [Id] = @ExpenseId AND [Status] = 'completed'"),
        ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "UpdateExpenseLineItemById",
         "SELECT @CurrentExpenseId = [ExpenseId] FROM dbo.[ExpenseLineItem] WHERE [Id] = @Id"),
        ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "DeleteExpenseLineItemById",
         "SELECT @ParentExpenseId = [ExpenseId] FROM dbo.[ExpenseLineItem] WHERE [Id] = @Id"),
        ("entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
         "CreateExpenseLineItemAttachment", "WHERE li.[Id] = @ExpenseLineItemId"),
        ("entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
         "DeleteExpenseLineItemAttachmentById", "WHERE elia.[Id] = @Id"),
    ],
)
def test_each_guard_resolves_the_parent_from_the_right_key(sql_rel, proc, must_contain):
    """The generic UPDLOCK/'completed' assertions stay green when the DELETE
    sproc's parent lookup is deleted outright, leaving its guard comparing
    against an undeclared variable. Each sproc has to be pinned to the key it
    actually resolves its parent through."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert must_contain in body, f"{proc} no longer resolves its parent correctly"


@pytest.mark.parametrize(
    "sql_rel,proc,must_contain,why",
    [
        ("entities/expense_line_item/sql/dbo.expense_line_item.sql", "DeleteExpenseLineItemById",
         "AND (@AllowTerminalParent = 1 OR [ExpenseId] = @ParentExpenseId)",
         "the DELETE must be bound to the parent it locked, with an exempt-caller "
         "escape so a QBO orphan rollback still deletes after a reparent"),
        ("entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
         "CreateExpenseLineItemAttachment", "li.[ExpenseId] = @ParentExpenseId",
         "the INSERT must be bound to the parent it locked"),
        ("entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql",
         "DeleteExpenseLineItemAttachmentById", "li.[ExpenseId] = @ParentExpenseId",
         "the DELETE must be bound to the parent it locked"),
        ("entities/expense/sql/dbo.expense.sql", "UpdateExpenseById",
         "RAISERROR('STATUS_LOCKED: a completed Expense cannot be edited.', 16, 1)",
         "a completion winning the header race must yield 422 status_locked, not 409"),
        ("entities/attachment/sql/dbo.attachment.sql", "UpdateAttachmentById",
         "STATUS_LOCKED: this file is evidence for a completed Expense.",
         "the Python check runs in a separate transaction; only this one is "
         "serialized against Expense completion"),
        ("entities/attachment/sql/dbo.attachment.sql", "DeleteAttachmentById",
         "STATUS_LOCKED: this file is evidence for a completed Expense.",
         "same, for the destructive direction"),
        ("entities/attachment/sql/dbo.attachment.sql", "UpdateAttachmentById",
         "WHERE elia.[AttachmentId] = @Id AND li.[ExpenseId] > @PrevExpenseId",
         "the Expense walk must resolve parents from the link row, ascending"),
        ("entities/attachment/sql/dbo.attachment.sql", "DeleteAttachmentById",
         "WHERE elia.[AttachmentId] = @Id AND li.[ExpenseId] > @PrevExpenseId",
         "same, for the destructive direction"),
    ],
)
def test_the_write_is_bound_to_what_the_guard_actually_checked(sql_rel, proc, must_contain, why):
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(sproc_body(REPO_ROOT / sql_rel, proc))
    assert must_contain in body, f"{proc}: {why}"


def test_the_two_parent_locks_are_taken_in_a_total_order():
    """A bare `IN (@a, @b)` guarantees no acquisition order, so A->B and B->A
    moves could take the two U locks in opposing order and deadlock. Ascending
    id is a total order every writer agrees on."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = _executable(
        sproc_body(
            REPO_ROOT / "entities/expense_line_item/sql/dbo.expense_line_item.sql",
            "UpdateExpenseLineItemById",
        )
    )
    assert "WHERE [Id] = @LoExpenseId" in body and "WHERE [Id] = @HiExpenseId" in body, (
        "each parent must be locked by its own single-id seek, low id first"
    )
    assert "IN (@LoExpenseId, @HiExpenseId)" not in body and "IN (@CurrentExpenseId" not in body, (
        "a set-based IN (...) seek reintroduces the undefined acquisition order"
    )
    assert body.index("@LoExpenseId AND [Status]") < body.index("@HiExpenseId AND [Id] <>"), (
        "low must be locked before high"
    )
