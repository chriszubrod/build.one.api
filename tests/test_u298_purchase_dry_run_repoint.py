"""Pure-logic tests for U-298 (Wave-1) — scripts/sync_qbo_purchase.py's dry-run
preview repointed off qbo.Purchase staging-row existence onto dbo.Expense's own
native QboId identity (U-283b): the SAME identity PurchaseExpenseConnector
actually resolves by. qbo.Purchase stays a read-only audit mirror that is
written on every pull regardless of whether the Expense side landed, so basing
the create/update classification on it could read backwards (e.g. a staging
row surviving an Expense create that failed/rolled back on a prior tick).

Covers:
  1. ExpenseRepository.read_qbo_ids_by_realm_id (sproc call shape).
  2. ExpenseService.read_qbo_ids_by_realm_id (RBAC actor threading).
  3. sync_qbo_purchase._dry_run_preview classifies create/update against that
     bulk dbo.Expense identity set, not qbo.Purchase staging existence.

Mocks stand in for the DB layer / QBO client; no DB/QBO I/O.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch


# --- Section 1: ExpenseRepository.read_qbo_ids_by_realm_id ---


def test_expense_repo_read_qbo_ids_by_realm_id_calls_sproc():
    from entities.expense.persistence.repo import ExpenseRepository

    repo = ExpenseRepository()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        SimpleNamespace(Id=1, QboId="PURCH-1"),
        SimpleNamespace(Id=2, QboId="PURCH-2"),
        SimpleNamespace(Id=3, QboId=None),  # defensive: sproc filters QboId IS NOT NULL, guard anyway
    ]

    with patch("entities.expense.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.expense.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        result = repo.read_qbo_ids_by_realm_id("realm-1", actor_user_id=17, actor_is_system_admin=True)

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadExpenseQboIdsByRealmId"
    assert mock_call.call_args.kwargs["params"] == {
        "RealmId": "realm-1",
        "ActorUserId": 17,
        "ActorIsSystemAdmin": 1,
    }
    assert result == {"PURCH-1", "PURCH-2"}


# --- Section 2: ExpenseService.read_qbo_ids_by_realm_id ---


def test_expense_service_read_qbo_ids_by_realm_id_threads_actor_scope():
    """Mirrors BillService/ExpenseService's other read_by_qbo_identity actor-
    scope test — must NOT bypass RBAC scoping."""
    from entities.expense.business.service import ExpenseService
    from shared.authz import current_is_system_admin, current_user_id

    repo = Mock()
    repo.read_qbo_ids_by_realm_id.return_value = {"PURCH-1"}
    service = ExpenseService(repo=repo)

    tok_u = current_user_id.set(7)
    tok_a = current_is_system_admin.set(True)
    try:
        result = service.read_qbo_ids_by_realm_id("realm-1")
    finally:
        current_user_id.reset(tok_u)
        current_is_system_admin.reset(tok_a)

    repo.read_qbo_ids_by_realm_id.assert_called_once_with(
        "realm-1", actor_user_id=7, actor_is_system_admin=True
    )
    assert result == {"PURCH-1"}


# --- Section 3: sync_qbo_purchase._dry_run_preview classification ---


def test_dry_run_preview_classifies_by_dbo_expense_identity_not_staging():
    from scripts.sync_qbo_purchase import _dry_run_preview

    qbo_purchases = [
        SimpleNamespace(id="Q-1", doc_number="D1", entity_ref=None, txn_date="2026-08-01", total_amt=100),
        SimpleNamespace(id="Q-2", doc_number="D2", entity_ref=None, txn_date="2026-08-02", total_amt=200),
    ]
    mock_client = MagicMock()
    mock_client.query_all_purchases.return_value = qbo_purchases

    mock_expense_service = Mock()
    # Q-1 already carries dbo.Expense native identity; Q-2 does not.
    mock_expense_service.read_qbo_ids_by_realm_id.return_value = {"Q-1"}

    with patch("scripts.sync_qbo_purchase.QboPurchaseClient") as mock_client_cls, patch(
        "scripts.sync_qbo_purchase.ExpenseService", return_value=mock_expense_service
    ):
        mock_client_cls.return_value.__enter__.return_value = mock_client
        result = _dry_run_preview(realm_id="realm-1")

    mock_expense_service.read_qbo_ids_by_realm_id.assert_called_once_with("realm-1")
    assert result["expense_identity"]["would_update"] == 1  # Q-1
    assert result["expense_identity"]["would_create"] == 1  # Q-2
    assert result["local_expenses_existing"] == 1


def test_dry_run_preview_no_qbo_staging_read_left_behind():
    """Regression guard: the repoint must actually stop reading qbo.Purchase
    staging-row existence for this classification — a QboPurchaseRepository
    call here would mean the repoint silently reverted."""
    from scripts.sync_qbo_purchase import _dry_run_preview

    mock_client = MagicMock()
    mock_client.query_all_purchases.return_value = []
    mock_expense_service = Mock()
    mock_expense_service.read_qbo_ids_by_realm_id.return_value = set()

    with patch("scripts.sync_qbo_purchase.QboPurchaseClient") as mock_client_cls, patch(
        "scripts.sync_qbo_purchase.ExpenseService", return_value=mock_expense_service
    ), patch("integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository") as mock_staging_repo:
        mock_client_cls.return_value.__enter__.return_value = mock_client
        _dry_run_preview(realm_id="realm-1")

    mock_staging_repo.assert_not_called()
