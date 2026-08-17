"""Pure-logic tests for ReconciliationIssueRepository lifecycle methods (U-246)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository


def _mock_row(**kwargs):
    defaults = {
        "Id": 1,
        "PublicId": "00000000-0000-0000-0000-000000000001",
        "RowVersion": b"\x00\x01",
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-02 00:00:00",
        "DriftType": "orphaned_item_scc_mapping",
        "Severity": "critical",
        "Action": "manual_review",
        "EntityType": "SubCostCode",
        "EntityPublicId": None,
        "QboId": "QBO-1",
        "RealmId": "realm-1",
        "Details": "details",
        "Status": "acknowledged",
        "AcknowledgedAt": "2026-01-02 00:00:00",
        "ResolvedAt": None,
        "ReconcileRunId": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _setup_mock_connection(mock_get_connection):
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_get_connection.return_value.__enter__.return_value = conn
    return cursor


@patch("integrations.intuit.qbo.reconciliation.persistence.repo.get_connection")
@patch("integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure")
def test_acknowledge_calls_sproc_and_maps_row(mock_call_procedure, mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection)
    cursor.fetchone.return_value = _mock_row(Status="acknowledged")

    repo = ReconciliationIssueRepository()
    result = repo.acknowledge(42)

    mock_call_procedure.assert_called_once_with(
        cursor=cursor,
        name="AcknowledgeQboReconciliationIssue",
        params={"Id": 42},
    )
    assert result is not None
    assert result.id == 1
    assert result.status == "acknowledged"


@patch("integrations.intuit.qbo.reconciliation.persistence.repo.get_connection")
@patch("integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure")
def test_resolve_calls_sproc_and_maps_row(mock_call_procedure, mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection)
    cursor.fetchone.return_value = _mock_row(Status="resolved", ResolvedAt="2026-01-03 00:00:00")

    repo = ReconciliationIssueRepository()
    result = repo.resolve(99)

    mock_call_procedure.assert_called_once_with(
        cursor=cursor,
        name="ResolveQboReconciliationIssue",
        params={"Id": 99},
    )
    assert result is not None
    assert result.status == "resolved"
    assert result.resolved_at == "2026-01-03 00:00:00"


@patch("integrations.intuit.qbo.reconciliation.persistence.repo.get_connection")
@patch("integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure")
def test_bulk_resolve_calls_sproc_and_returns_ids(mock_call_procedure, mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection)
    cursor.fetchall.return_value = [SimpleNamespace(Id=10), SimpleNamespace(Id=11)]

    created_before = "2026-01-01 00:00:00"
    repo = ReconciliationIssueRepository()
    ids = repo.bulk_resolve(
        drift_type="pull_delete_reconcile",
        entity_type="Bill",
        created_before=created_before,
        realm_id="realm-42",
        status="open",
        max_rows=500,
    )

    mock_call_procedure.assert_called_once_with(
        cursor=cursor,
        name="BulkResolveQboReconciliationIssuesByFilter",
        params={
            "DriftType": "pull_delete_reconcile",
            "EntityType": "Bill",
            "CreatedBefore": created_before,
            "RealmId": "realm-42",
            "Status": "open",
            "MaxRows": 500,
            "DryRun": False,
        },
    )
    assert ids == [10, 11]


@patch("integrations.intuit.qbo.reconciliation.persistence.repo.get_connection")
@patch("integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure")
def test_preview_bulk_resolve_calls_sproc_with_dry_run_and_maps_rows(
    mock_call_procedure, mock_get_connection
):
    cursor = _setup_mock_connection(mock_get_connection)
    cursor.fetchall.return_value = [
        SimpleNamespace(
            Id=10,
            DriftType="pull_delete_reconcile",
            EntityType="Bill",
            QboId="QBO-10",
            CreatedDatetime="2026-01-01 00:00:00",
            TotalMatchCount=25,
        )
    ]

    created_before = "2026-01-01 00:00:00"
    repo = ReconciliationIssueRepository()
    rows = repo.preview_bulk_resolve(
        drift_type="pull_delete_reconcile",
        entity_type="Bill",
        created_before=created_before,
        realm_id="realm-42",
        status="open",
        max_rows=500,
    )

    mock_call_procedure.assert_called_once_with(
        cursor=cursor,
        name="BulkResolveQboReconciliationIssuesByFilter",
        params={
            "DriftType": "pull_delete_reconcile",
            "EntityType": "Bill",
            "CreatedBefore": created_before,
            "RealmId": "realm-42",
            "Status": "open",
            "MaxRows": 500,
            "DryRun": True,
        },
    )
    assert rows == [
        {
            "id": 10,
            "drift_type": "pull_delete_reconcile",
            "entity_type": "Bill",
            "qbo_id": "QBO-10",
            "created_datetime": "2026-01-01 00:00:00",
            "total_match_count": 25,
        }
    ]


@patch("integrations.intuit.qbo.reconciliation.persistence.repo.get_connection")
@patch("integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure")
def test_preview_bulk_resolve_returns_empty_list_when_no_rows(
    mock_call_procedure, mock_get_connection
):
    cursor = _setup_mock_connection(mock_get_connection)
    cursor.fetchall.return_value = []

    repo = ReconciliationIssueRepository()
    rows = repo.preview_bulk_resolve(drift_type="orphaned_item_scc_mapping")

    assert rows == []


@patch("integrations.intuit.qbo.reconciliation.persistence.repo.get_connection")
@patch("integrations.intuit.qbo.reconciliation.persistence.repo.call_procedure")
def test_triage_summary_calls_sproc_and_maps_rows(mock_call_procedure, mock_get_connection):
    cursor = _setup_mock_connection(mock_get_connection)
    cursor.fetchall.return_value = [
        SimpleNamespace(
            DriftType="orphaned_item_scc_mapping",
            EntityType="SubCostCode",
            Severity="critical",
            Action="manual_review",
            Status="open",
            RowCount=100,
            UniqueKeyCount=50,
            FirstSeen="2026-01-01 00:00:00",
            LastSeen="2026-01-15 00:00:00",
        )
    ]

    repo = ReconciliationIssueRepository()
    rows = repo.triage_summary()

    mock_call_procedure.assert_called_once_with(
        cursor=cursor,
        name="ReadQboReconciliationIssueTriageSummary",
        params={},
    )
    assert len(rows) == 1
    assert rows[0] == {
        "drift_type": "orphaned_item_scc_mapping",
        "entity_type": "SubCostCode",
        "severity": "critical",
        "action": "manual_review",
        "status": "open",
        "row_count": 100,
        "unique_key_count": 50,
        "first_seen": "2026-01-01 00:00:00",
        "last_seen": "2026-01-15 00:00:00",
    }
