"""Unit tests for U-312 — repoint the 3 non-identity Project direct-reads
(draw-audit's `missing_qbo_mapping` gap class, `reconcile_project.py`'s and
`sync_qbo_invoice.py`'s project -> QBO-customer-ref resolvers) off the
`qbo.CustomerProject` mapping-table hop onto `dbo.Project.QboId`/`.RealmId`
directly, plus the P1 Codex's xhigh review found beyond the original 3:
`InvoiceRepository.read_duplicate_projects_by_project_id`'s `QboMappings`
column (feeds the SEPARATE `duplicate_project` halt-class) was the design
doc's own consumer sweep missing a 4th live `qbo.CustomerProject` reference.
No harness existed for any of these 4 call sites before this unit.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.invoice.business.audit import InvoiceDrawAuditService
from entities.invoice.persistence.repo import InvoiceRepository
from scripts.reconcile_project import _get_project_qbo_customer_ref
from scripts.sync_qbo_invoice import _resolve_project_to_customer_ref


def _make_project(*, id=1, name="Main Street", qbo_id=None, realm_id=None):
    return SimpleNamespace(id=id, name=name, qbo_id=qbo_id, realm_id=realm_id)


def _make_qbo_customer(*, qbo_id="191", realm_id="realm-1", display_name="Acme"):
    return SimpleNamespace(qbo_id=qbo_id, realm_id=realm_id, display_name=display_name)


def _patch_project_service(monkeypatch, project_service):
    monkeypatch.setattr(
        "scripts.sync_qbo_invoice.ProjectService", lambda: project_service
    )


def _mock_repo_connection(rows):
    """Mock get_connection()/cursor for a raw-SQL repo method; returns (conn, cursor)."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    return conn, cursor


# ── entities/invoice/business/audit.py::InvoiceDrawAuditService._read_qbo_mapping ──


def _audit_service(qbo_customer_repo):
    return InvoiceDrawAuditService(
        invoice_service=MagicMock(),
        invoice_repo=MagicMock(),
        reconciliation_service=MagicMock(),
        project_service=MagicMock(),
        qbo_customer_repo=qbo_customer_repo,
        sync_repo=MagicMock(),
        box_workbook_repo=MagicMock(),
        box_folder_repo=MagicMock(),
    )


def test_read_qbo_mapping_none_project_is_absent():
    qbo_customer_repo = MagicMock()
    svc = _audit_service(qbo_customer_repo)

    result = svc._read_qbo_mapping(None)

    assert result == {"present": False, "mapping": None, "customer": None}
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_read_qbo_mapping_project_without_qbo_id_is_absent():
    qbo_customer_repo = MagicMock()
    svc = _audit_service(qbo_customer_repo)
    project = _make_project(qbo_id=None)

    result = svc._read_qbo_mapping(project)

    assert result == {"present": False, "mapping": None, "customer": None}
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_not_called()


def test_read_qbo_mapping_present_reads_directly_by_project_qbo_identity_no_mapping_hop():
    qbo_customer_repo = MagicMock()
    customer = _make_qbo_customer(qbo_id="191", realm_id="realm-1", display_name="Acme Co")
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = customer
    svc = _audit_service(qbo_customer_repo)
    project = _make_project(id=42, qbo_id="191", realm_id="realm-1")

    result = svc._read_qbo_mapping(project)

    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("191", "realm-1")
    assert result["present"] is True
    assert result["mapping"] == {"project_id": 42}
    assert result["customer"] == {"qbo_id": "191", "realm_id": "realm-1", "display_name": "Acme Co"}


def test_read_qbo_mapping_present_but_no_raw_customer_mirror_row():
    qbo_customer_repo = MagicMock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = None
    svc = _audit_service(qbo_customer_repo)
    project = _make_project(id=42, qbo_id="191", realm_id="realm-1")

    result = svc._read_qbo_mapping(project)

    assert result["present"] is True
    assert result["customer"] is None


def test_audit_service_no_longer_has_customer_project_repo():
    # Wave-5 U-312: the qbo.CustomerProject mapping hop is fully retired from
    # this service — a regression that re-adds it would silently reintroduce
    # the mapping-table dependency this unit removed.
    svc = _audit_service(MagicMock())
    assert not hasattr(svc, "customer_project_repo")


# ── scripts/reconcile_project.py::_get_project_qbo_customer_ref ──


def test_get_project_qbo_customer_ref_reads_dbo_project_qbo_id_directly():
    project_service = MagicMock()
    project_service.read_by_id.return_value = _make_project(id=7, qbo_id="55")

    result = _get_project_qbo_customer_ref(7, project_service)

    project_service.read_by_id.assert_called_once_with(7)
    assert result == "55"


def test_get_project_qbo_customer_ref_no_project_returns_none():
    project_service = MagicMock()
    project_service.read_by_id.return_value = None

    result = _get_project_qbo_customer_ref(999, project_service)

    assert result is None


def test_get_project_qbo_customer_ref_project_without_qbo_id_returns_none():
    project_service = MagicMock()
    project_service.read_by_id.return_value = _make_project(id=7, qbo_id=None)

    result = _get_project_qbo_customer_ref(7, project_service)

    assert result is None


# ── scripts/sync_qbo_invoice.py::_resolve_project_to_customer_ref ──


def test_resolve_project_to_customer_ref_reads_dbo_project_qbo_id_directly(monkeypatch):
    # read_all's sproc (ReadProjects) doesn't select QboId/RealmId at all (base-file
    # gap, TODO.md) — the name-search row deliberately carries no qbo_id here to prove
    # the function re-fetches by id (ReadProjectById, which does select it) rather than
    # trusting whatever read_all happened to return.
    search_hit = _make_project(id=3, name="Highland Ave", qbo_id=None)
    refetched = _make_project(id=3, name="Highland Ave", qbo_id="88")
    project_service = MagicMock()
    project_service.read_all.return_value = [search_hit]
    project_service.read_by_id.return_value = refetched
    _patch_project_service(monkeypatch, project_service)

    result = _resolve_project_to_customer_ref("Highland")

    project_service.read_by_id.assert_called_once_with(3)
    assert result == "88"


def test_resolve_project_to_customer_ref_no_match_raises(monkeypatch):
    project_service = MagicMock()
    project_service.read_all.return_value = [_make_project(id=1, name="Main St")]
    _patch_project_service(monkeypatch, project_service)

    with pytest.raises(ValueError, match="No project found"):
        _resolve_project_to_customer_ref("Nonexistent")
    project_service.read_by_id.assert_not_called()


def test_resolve_project_to_customer_ref_ambiguous_match_raises(monkeypatch):
    project_service = MagicMock()
    project_service.read_all.return_value = [
        _make_project(id=1, name="Main Street A"),
        _make_project(id=2, name="Main Street B"),
    ]
    _patch_project_service(monkeypatch, project_service)

    with pytest.raises(ValueError, match="Multiple projects match"):
        _resolve_project_to_customer_ref("Main Street")
    project_service.read_by_id.assert_not_called()


def test_resolve_project_to_customer_ref_no_qbo_id_raises(monkeypatch):
    project_service = MagicMock()
    project_service.read_all.return_value = [
        _make_project(id=5, name="Riverside", qbo_id=None)
    ]
    project_service.read_by_id.return_value = _make_project(id=5, name="Riverside", qbo_id=None)
    _patch_project_service(monkeypatch, project_service)

    with pytest.raises(ValueError, match="No QBO identity"):
        _resolve_project_to_customer_ref("Riverside")


def test_resolve_project_to_customer_ref_refetch_finds_nothing_raises(monkeypatch):
    # Edge case: project deleted between the read_all search and the read_by_id
    # re-fetch (or an RBAC-scoped actor loses visibility) — must raise, not crash
    # on None.qbo_id.
    project_service = MagicMock()
    project_service.read_all.return_value = [_make_project(id=9, name="Gone Project")]
    project_service.read_by_id.return_value = None
    _patch_project_service(monkeypatch, project_service)

    with pytest.raises(ValueError, match="No QBO identity"):
        _resolve_project_to_customer_ref("Gone")


# ── entities/invoice/persistence/repo.py::InvoiceRepository.read_duplicate_projects_by_project_id ──
# (Codex xhigh P1: a 4th live qbo.CustomerProject reference wave5.md's own consumer
# sweep missed — feeds the separate duplicate_project halt-class, not missing_qbo_mapping.)


def test_read_duplicate_projects_sql_no_longer_references_qbo_customer_project():
    conn, cursor = _mock_repo_connection([])

    with patch("entities.invoice.persistence.repo.get_connection", return_value=conn):
        InvoiceRepository().read_duplicate_projects_by_project_id(42)

    sql_sent = cursor.execute.call_args[0][0]
    assert "qbo.CustomerProject" not in sql_sent
    assert "qbo.[CustomerProject]" not in sql_sent
    assert "[QboId]" in sql_sent


def test_read_duplicate_projects_qbo_mappings_reflects_dbo_project_qbo_id():
    row_with_identity = SimpleNamespace(
        Id=99, Name="HP", Abbreviation=None, CreatedDatetime="2026-01-01", QboMappings=1
    )
    row_without_identity = SimpleNamespace(
        Id=100, Name="HP", Abbreviation=None, CreatedDatetime="2026-01-01", QboMappings=0
    )
    conn, cursor = _mock_repo_connection([row_with_identity, row_without_identity])

    with patch("entities.invoice.persistence.repo.get_connection", return_value=conn):
        rows = InvoiceRepository().read_duplicate_projects_by_project_id(42)

    assert [r.QboMappings for r in rows] == [1, 0]
