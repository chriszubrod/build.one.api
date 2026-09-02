"""U-356 — retire the qbo.InvoiceInvoice mapping second-store (U-349 program,
family 7, the first header family with a RECONCILIATION consumer).

Covers, all pure-logic (mocked repos / no live DB):
  1. The identity-stamp rollback race fix on the fresh-CREATE path (U-354/U-355
     pattern): a stamp or line-sync failure deletes the just-minted header and
     re-raises; a failed rollback records `orphan_invoice_header`.
  2. The ADOPT path (side-channel-keyed candidate) is stamped under the shared
     `stamp_dbo_identity_with_lock` theft-guard: a candidate that already
     carries a DIFFERENT identity by stamp time records
     `invoice_identity_conflict` and raises — and is NEVER rolled back.
  3. The dbo-native reconciliation re-expression: `INVOICE_DRAW_ROWS_SQL`
     returns the SAME rows as the retired mapping-hop query on a shared SQLite
     fixture (characterization / equivalence), plus the two documented,
     intended divergences on rows the mapping table could not represent
     consistently.
  4. The outbox worker's `_refresh_invoice` repoint (dbo-only verify,
     hard-refuse with a severed `__context__` on a genuine conflict).
  5. `QboInvoiceService.cost_coded_lines_for_invoice` has no mapping fallback.
"""
import sqlite3
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
    InvoiceInvoiceConnector,
)
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.reconciliation.business.service import (
    INVOICE_DRAW_ROWS_SQL,
    ReconciliationService,
)
pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")

ILI_SERVICE = "entities.invoice_line_item.business.service.InvoiceLineItemService"


def _make_qbo_invoice(**overrides):
    defaults = dict(
        id=901,
        qbo_id="INV-77",
        realm_id="realm-1",
        customer_ref_value="qbo-customer-1",
        doc_number="5001",
        txn_date="2026-08-01",
        due_date="2026-08-15",
        private_note="draw",
        total_amt=100,
        sync_token="3",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_ONE_LINE = [SimpleNamespace(id=1)]


def _build_connector():
    invoice_service = Mock()
    invoice_service.repo = Mock()
    project_service = Mock()
    reconciliation_repo = Mock()
    connector = InvoiceInvoiceConnector(
        line_mapping_repo=Mock(),
        invoice_service=invoice_service,
        project_service=project_service,
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    connector._get_project_public_id = Mock(return_value="project-pub-1")
    connector._sync_line_items = Mock()
    return connector


def _wire_fresh_create_miss(connector, created):
    """Fast path misses (no dbo.Invoice holds this identity), no adopt candidate
    by number or fingerprint -> the plain CREATE branch."""
    connector.invoice_service.read_by_qbo_identity.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=42)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=None)
    connector.invoice_service.create.return_value = created
    refreshed = SimpleNamespace(
        id=created.id, public_id=created.public_id, qbo_id="INV-77", realm_id="realm-1"
    )
    connector.invoice_service.read_by_id.return_value = refreshed
    return refreshed


# ---------------------------------------------------------------------------
# 1. fresh-CREATE path: identity-stamp rollback race fix
# ---------------------------------------------------------------------------


def test_create_path_stamps_identity_syncs_lines_and_returns_refreshed_row():
    connector = _build_connector()
    created = SimpleNamespace(id=77, public_id="pub-77")
    refreshed = _wire_fresh_create_miss(connector, created)

    result = connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    assert result is refreshed  # re-read after the stamp, not the pre-stamp row
    connector.invoice_service.create.assert_called_once()
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="INV-77", realm_id="realm-1", sync_token="3"
    )
    connector._sync_line_items.assert_called_once_with(77, "pub-77", _ONE_LINE, "realm-1")
    connector.invoice_service.delete_by_public_id.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


def test_create_path_stamp_failure_rolls_back_orphan_header_and_reraises():
    """THE race fix (U-354/U-355 pattern, RED before this unit): a transient
    set_qbo_identity failure after the header insert used to strand an
    UNSTAMPED orphan Invoice that read_by_qbo_identity can never find again,
    so the next pull tick minted a genuine duplicate. Now the just-created
    header is deleted and the original error re-raised (watermark holds)."""
    connector = _build_connector()
    created = SimpleNamespace(id=77, public_id="pub-77")
    _wire_fresh_create_miss(connector, created)
    connector.invoice_service.repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")

    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    connector.invoice_service.delete_by_public_id.assert_called_once_with("pub-77")
    connector._sync_line_items.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


def test_create_path_line_sync_failure_rolls_back_orphan_header_and_reraises():
    """Both steps share ONE try/except: a raising line sync gets the identical
    cleanup (closes the U-006 landmine note — `_sync_line_items` swallows
    today, but the rollback stands regardless)."""
    connector = _build_connector()
    created = SimpleNamespace(id=77, public_id="pub-77")
    _wire_fresh_create_miss(connector, created)
    connector._sync_line_items.side_effect = RuntimeError("line sync blew up")

    with pytest.raises(RuntimeError, match="line sync blew up"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    connector.invoice_service.repo.set_qbo_identity.assert_called_once()
    connector.invoice_service.delete_by_public_id.assert_called_once_with("pub-77")


def test_create_path_rollback_failure_records_orphan_header_issue():
    connector = _build_connector()
    created = SimpleNamespace(id=77, public_id="pub-77")
    _wire_fresh_create_miss(connector, created)
    connector.invoice_service.repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")
    connector.invoice_service.delete_by_public_id.side_effect = RuntimeError("delete failed")

    with pytest.raises(RuntimeError, match="stamp failed"):  # ORIGINAL error, not the rollback's
        connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    connector.reconciliation_repo.create.assert_called_once()
    kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "orphan_invoice_header"
    assert kwargs["entity_type"] == "Invoice"
    assert kwargs["entity_public_id"] == "pub-77"
    assert kwargs["qbo_id"] == "INV-77"
    assert kwargs["realm_id"] == "realm-1"
    assert "delete failed" in kwargs["details"]


def test_create_path_all_suffixes_taken_raises_value_error_not_unbound_local():
    connector = _build_connector()
    _wire_fresh_create_miss(connector, SimpleNamespace(id=77, public_id="pub-77"))
    connector.invoice_service.create.side_effect = ValueError("Invoice number already exists")

    with pytest.raises(ValueError, match="all of its suffix variants already exist"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    assert connector.invoice_service.create.call_count == 10
    connector.invoice_service.repo.set_qbo_identity.assert_not_called()
    # Durable record, or the un-projected QBO invoice is invisible to the daily
    # reconcile (which only scans dbo.Invoice rows WITH identity).
    kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "duplicate_qbo_invoice_number"
    assert kwargs["qbo_id"] == "INV-77"
    assert kwargs["realm_id"] == "realm-1"
    assert "5001-11" in kwargs["details"]


def test_race_resolved_hit_under_lock_adopts_racer_never_creates():
    """Two syncs of the SAME QboInvoice: the unlocked read misses, the re-read
    under run_identity_fastpath_dbo_only's create lock finds the racer's row —
    apply fields to it, never mint a second header."""
    connector = _build_connector()
    racer = SimpleNamespace(id=55, public_id="pub-55", row_version="rv", invoice_number="5001")
    connector.invoice_service.read_by_qbo_identity.side_effect = [None, racer]
    updated = SimpleNamespace(id=55, public_id="pub-55", invoice_number="5001")
    connector.invoice_service.update_by_public_id.return_value = updated

    result = connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    assert result is updated
    connector.invoice_service.create.assert_not_called()
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="INV-77", realm_id="realm-1", sync_token="3"
    )


# ---------------------------------------------------------------------------
# 2. ADOPT path: stamped under the candidate lock + theft-guard
# ---------------------------------------------------------------------------


def _wire_number_match_adopt(connector, existing_by_id):
    connector.invoice_service.read_by_qbo_identity.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=42)
    lookup_row = SimpleNamespace(id=existing_by_id.id, public_id=existing_by_id.public_id)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = lookup_row
    connector.invoice_service.read_by_id.return_value = existing_by_id
    connector._header_fingerprint_matches = Mock(return_value=True)
    connector._has_qbo_line_provenance = Mock(return_value=True)
    connector.invoice_service.update_by_public_id.return_value = existing_by_id


def test_adopt_path_writes_fields_under_lock_and_stamps_identity():
    connector = _build_connector()
    existing = SimpleNamespace(
        id=1057, public_id="inv-pub-1057", row_version="rv-fresh", invoice_number="CORRECTED-100",
        qbo_id=None, realm_id=None, total_amount=Decimal("100"), invoice_date="2026-08-01",
    )
    _wire_number_match_adopt(connector, existing)

    result = connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    assert result is existing
    connector.invoice_service.create.assert_not_called()
    # The field write ran against the fresh under-lock re-read (its ROWVERSION),
    # preserving the human-corrected number.
    args, kwargs = connector.invoice_service.update_by_public_id.call_args
    assert args[0] == "inv-pub-1057"
    assert kwargs["row_version"] == "rv-fresh"
    assert kwargs["invoice_number"] == "CORRECTED-100"
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=1057, qbo_id="INV-77", realm_id="realm-1", sync_token="3"
    )
    # Number-match route: ONE by-id re-read in resolve (the number lookup's row
    # carries no qbo_id) + the stamp lock's own two (step 2 + step 7).
    assert connector.invoice_service.read_by_id.call_count == 3
    # Lines are never re-projected onto an adopted invoice — the provenance gate
    # already proved it carries QBO-mapped lines (see _adopt_invoice_identity).
    connector._sync_line_items.assert_not_called()
    connector.reconciliation_repo.create.assert_not_called()


def test_fingerprint_route_adopt_does_not_reread_the_candidate_in_resolve():
    """`_find_adoptable_invoice_by_fingerprint` already returns the by-id row it
    vetted, so resolve must not spend another round trip on it — only the stamp
    lock's own two reads happen."""
    connector = _build_connector()
    existing = SimpleNamespace(
        id=1057, public_id="inv-pub-1057", row_version="rv", invoice_number="RENAMED",
        qbo_id=None, realm_id=None, total_amount=Decimal("100"), invoice_date="2026-08-01",
    )
    connector.invoice_service.read_by_qbo_identity.return_value = None
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=42)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None
    connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=existing)
    connector.invoice_service.read_by_id.return_value = existing
    connector._header_fingerprint_matches = Mock(return_value=True)
    connector._has_qbo_line_provenance = Mock(return_value=True)
    connector.invoice_service.update_by_public_id.return_value = existing

    connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    assert connector.invoice_service.read_by_id.call_count == 2
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=1057, qbo_id="INV-77", realm_id="realm-1", sync_token="3"
    )
    connector._sync_line_items.assert_not_called()


def test_adopt_path_theft_guard_conflict_records_issue_raises_and_never_rolls_back():
    """The candidate looked unbound at resolve time but, on the fresh re-read
    inside its own stamp lock, already carries a DIFFERENT QBO identity (a
    concurrent sync of ANOTHER QBO invoice adopted it first). Record
    invoice_identity_conflict, raise, write NOTHING — and never delete a
    pre-existing invoice the way the fresh-create rollback would."""
    connector = _build_connector()
    unbound = SimpleNamespace(
        id=1057, public_id="inv-pub-1057", row_version="rv", invoice_number="INV-100",
        qbo_id=None, realm_id=None, total_amount=Decimal("100"), invoice_date="2026-08-01",
    )
    stolen = SimpleNamespace(
        id=1057, public_id="inv-pub-1057", row_version="rv2", invoice_number="INV-100",
        qbo_id="INV-OTHER", realm_id="realm-1",
    )
    _wire_number_match_adopt(connector, unbound)
    connector.invoice_service.read_by_id.side_effect = [unbound, stolen]

    with pytest.raises(ValueError, match="already carries QBO identity"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    connector.invoice_service.update_by_public_id.assert_not_called()
    connector.invoice_service.repo.set_qbo_identity.assert_not_called()
    connector.invoice_service.delete_by_public_id.assert_not_called()
    connector.invoice_service.create.assert_not_called()
    connector.reconciliation_repo.create.assert_called_once()
    kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "invoice_identity_conflict"
    assert kwargs["entity_type"] == "Invoice"
    assert kwargs["entity_public_id"] == "inv-pub-1057"
    assert kwargs["qbo_id"] == "INV-77"
    assert kwargs["realm_id"] == "realm-1"  # the caller's effective realm (U-350 P2)
    assert "DIFFERENT QboId INV-OTHER" in kwargs["details"]


def test_number_match_bound_to_other_qbo_invoice_is_collision_not_adopt():
    """The dbo-only replacement for the retired "mapped to a DIFFERENT
    QboInvoice" check reads the candidate BY ID (the number lookup's row never
    carries QboId): a bound row records duplicate_qbo_invoice_number with the
    holder's own QboId and falls through to CREATE."""
    connector = _build_connector()
    bound = SimpleNamespace(
        id=1057, public_id="inv-pub-1057", row_version="rv", invoice_number="INV-100",
        qbo_id="INV-OTHER", realm_id="realm-1",
    )
    _wire_number_match_adopt(connector, bound)
    created = SimpleNamespace(id=1058, public_id="inv-pub-1058")
    connector.invoice_service.create.return_value = created
    connector.invoice_service.read_by_id.side_effect = [bound, SimpleNamespace(id=1058, public_id="inv-pub-1058")]

    connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    connector.invoice_service.update_by_public_id.assert_not_called()
    connector.invoice_service.create.assert_called_once()
    kwargs = connector.reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "duplicate_qbo_invoice_number"
    assert kwargs["qbo_id"] == "INV-OTHER"
    assert kwargs["realm_id"] == "realm-1"
    connector.invoice_service.repo.set_qbo_identity.assert_called_once_with(
        id=1058, qbo_id="INV-77", realm_id="realm-1", sync_token="3"
    )


def test_number_match_same_qbo_id_different_realm_is_a_collision():
    """QBO ids are only unique WITHIN a realm — same QboId under another realm
    is a different invoice (the theft-guard's own predicate)."""
    connector = _build_connector()
    other_realm = SimpleNamespace(
        id=1057, public_id="inv-pub-1057", row_version="rv", invoice_number="INV-100",
        qbo_id="INV-77", realm_id="realm-OTHER",
    )
    _wire_number_match_adopt(connector, other_realm)
    connector.invoice_service.create.return_value = SimpleNamespace(id=1058, public_id="p")

    connector.sync_from_qbo_invoice(_make_qbo_invoice(), _ONE_LINE)

    connector.invoice_service.create.assert_called_once()
    assert connector.reconciliation_repo.create.call_args.kwargs["drift_type"] == "duplicate_qbo_invoice_number"


def test_fingerprint_scan_rereads_candidate_by_id_and_skips_bound_rows():
    connector = _build_connector()
    cached = SimpleNamespace(
        id=1057, project_id=42, total_amount=Decimal("100"), invoice_date="2026-08-01",
    )
    connector._invoice_cache = {cached.id: cached}
    connector._caches_preloaded = True
    connector.invoice_service.read_by_id.return_value = SimpleNamespace(
        id=1057, qbo_id="INV-OTHER", realm_id="realm-1",
    )
    connector._has_qbo_line_provenance = Mock(return_value=True)

    assert connector._find_adoptable_invoice_by_fingerprint(
        42, 100, "2026-08-01", "INV-77", "realm-1"
    ) is None
    connector.invoice_service.read_by_id.assert_called_once_with(1057)


def test_fingerprint_scan_returns_unbound_candidate_from_the_by_id_reread():
    connector = _build_connector()
    cached = SimpleNamespace(
        id=1057, project_id=42, total_amount=Decimal("100"), invoice_date="2026-08-01",
    )
    connector._invoice_cache = {cached.id: cached}
    connector._caches_preloaded = True
    fresh = SimpleNamespace(id=1057, qbo_id=None, realm_id=None)
    connector.invoice_service.read_by_id.return_value = fresh
    connector._has_qbo_line_provenance = Mock(return_value=True)

    assert connector._find_adoptable_invoice_by_fingerprint(
        42, 100, "2026-08-01", "INV-77", "realm-1"
    ) is fresh


# ---------------------------------------------------------------------------
# 3. reconciliation re-expression: characterization / equivalence
# ---------------------------------------------------------------------------

# Frozen copy of the pre-U-356 row source (verbatim from reconcile_invoice_draws
# at 8bc77965) — the mapping-table hop this unit removes. Kept HERE, in the
# test, so the equivalence claim is against the real "before", not a paraphrase.
_LEGACY_MAPPING_HOP_SQL = """
    SELECT i.Id, CAST(i.PublicId AS NVARCHAR(50)) AS PublicId,
           i.InvoiceNumber, i.TotalAmount, i.IsDraft,
           qi.QboId, qi.TotalAmt,
           (SELECT COUNT(*) FROM dbo.InvoiceLineItem x WHERE x.InvoiceId = i.Id) AS DboLines,
           (SELECT COUNT(*) FROM qbo.InvoiceLine ql WHERE ql.QboInvoiceId = qi.Id) AS QboLines,
           (SELECT COUNT(*) FROM dbo.InvoiceLineItem x
              WHERE x.InvoiceId = i.Id AND x.SourceType = 'Manual') AS ManualLines,
           (SELECT COUNT(*) FROM dbo.InvoiceLineItem x
              LEFT JOIN dbo.BillLineItem b ON b.Id = x.BillLineItemId
              LEFT JOIN dbo.ExpenseLineItem e ON e.Id = x.ExpenseLineItemId
              LEFT JOIN dbo.BillCreditLineItem c ON c.Id = x.BillCreditLineItemId
            WHERE x.InvoiceId = i.Id
              AND x.SourceType IN ('BillLineItem','ExpenseLineItem','BillCreditLineItem')
              AND COALESCE(b.IsBilled, e.IsBilled, c.IsBilled, 0) = 0) AS UnbilledSources
    FROM qbo.InvoiceInvoice map
    JOIN dbo.Invoice i ON i.Id = map.InvoiceId
    JOIN qbo.Invoice qi ON qi.Id = map.QboInvoiceId
    WHERE qi.RealmId = ?
"""


def _fixture_db():
    """SQLite stand-in with `dbo`/`qbo` ATTACHed as schema names so the
    production SQL text runs unmodified. Representative corpus:

      inv 1  stamped (Q1,R1), mapped -> qi 101   total drift + unbilled source, completed
      inv 2  stamped (Q2,R1), mapped -> qi 102   line-count drift, draft
      inv 3  stamped (Q3,R2), mapped -> qi 103   OTHER realm -> excluded by both
      inv 4  manual: no QboId, no mapping         -> excluded by both
      inv 5  stamped (Q5,R1), mapped -> qi 105   qi.RealmId NULL -> excluded by both
      qi 106 (Q6,R1) with no dbo counterpart      -> excluded by both
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dbo")
    conn.execute("ATTACH DATABASE ':memory:' AS qbo")
    conn.executescript(
        """
        CREATE TABLE dbo.Invoice(Id INTEGER PRIMARY KEY, PublicId TEXT, InvoiceNumber TEXT,
                                 TotalAmount REAL, IsDraft INTEGER, QboId TEXT, RealmId TEXT);
        CREATE TABLE qbo.Invoice(Id INTEGER PRIMARY KEY, QboId TEXT, RealmId TEXT, TotalAmt REAL);
        CREATE TABLE qbo.InvoiceInvoice(Id INTEGER PRIMARY KEY, InvoiceId INTEGER, QboInvoiceId INTEGER);
        CREATE TABLE dbo.InvoiceLineItem(Id INTEGER PRIMARY KEY, InvoiceId INTEGER, SourceType TEXT,
                                         BillLineItemId INTEGER, ExpenseLineItemId INTEGER,
                                         BillCreditLineItemId INTEGER);
        CREATE TABLE qbo.InvoiceLine(Id INTEGER PRIMARY KEY, QboInvoiceId INTEGER);
        CREATE TABLE dbo.BillLineItem(Id INTEGER PRIMARY KEY, IsBilled INTEGER);
        CREATE TABLE dbo.ExpenseLineItem(Id INTEGER PRIMARY KEY, IsBilled INTEGER);
        CREATE TABLE dbo.BillCreditLineItem(Id INTEGER PRIMARY KEY, IsBilled INTEGER);

        INSERT INTO dbo.Invoice VALUES
          (1, 'p1', 'INV-1', 100.00, 0, 'Q1', 'R1'),
          (2, 'p2', 'INV-2', 200.00, 1, 'Q2', 'R1'),
          (3, 'p3', 'INV-3', 300.00, 0, 'Q3', 'R2'),
          (4, 'p4', 'MANUAL-4', 400.00, 0, NULL, NULL),
          (5, 'p5', 'INV-5', 500.00, 0, 'Q5', 'R1');
        INSERT INTO qbo.Invoice VALUES
          (101, 'Q1', 'R1', 110.00),
          (102, 'Q2', 'R1', 200.00),
          (103, 'Q3', 'R2', 300.00),
          (105, 'Q5', NULL, 500.00),
          (106, 'Q6', 'R1', 600.00);
        INSERT INTO qbo.InvoiceInvoice VALUES (1, 1, 101), (2, 2, 102), (3, 3, 103), (5, 5, 105);
        INSERT INTO dbo.BillLineItem VALUES (11, 0), (12, 1);
        INSERT INTO dbo.InvoiceLineItem VALUES
          (1001, 1, 'Manual', NULL, NULL, NULL),
          (1002, 1, 'BillLineItem', 11, NULL, NULL),
          (1003, 2, 'BillLineItem', 12, NULL, NULL),
          (1004, 2, 'Manual', NULL, NULL, NULL);
        INSERT INTO qbo.InvoiceLine VALUES (5001, 101), (5002, 101), (5003, 102);
        """
    )
    return conn


def _rows(conn, sql, realm_id):
    return sorted(conn.execute(sql, (realm_id,)).fetchall())


def test_dbo_native_draw_rows_match_legacy_mapping_hop_on_representative_fixture():
    """THE equivalence claim: on a corpus where every mapped invoice is also
    dbo-stamped with the SAME (QboId, RealmId) — the invariant the U-238a
    backfill established and the read-only prod check confirmed at cutover
    (986 = 986, symmetric difference 0) — the re-expressed query returns
    exactly the rows the mapping hop did, per realm."""
    conn = _fixture_db()
    for realm in ("R1", "R2", "R-NONE"):
        assert _rows(conn, INVOICE_DRAW_ROWS_SQL, realm) == _rows(conn, _LEGACY_MAPPING_HOP_SQL, realm)

    rows = _rows(conn, INVOICE_DRAW_ROWS_SQL, "R1")
    assert [r[0] for r in rows] == [1, 2]  # inv 3 (R2), 4 (manual), 5 (NULL-realm qi) excluded
    # (Id, PublicId, InvoiceNumber, TotalAmount, IsDraft, QboId, TotalAmt,
    #  DboLines, QboLines, ManualLines, UnbilledSources)
    assert rows[0] == (1, "p1", "INV-1", 100.0, 0, "Q1", 110.0, 2, 2, 1, 1)
    assert rows[1] == (2, "p2", "INV-2", 200.0, 1, "Q2", 200.0, 2, 1, 1, 0)


def test_dbo_native_draw_rows_documented_divergences_from_the_mapping_hop():
    """The two row classes the mapping table could not represent consistently,
    asserted explicitly so the divergence is a decision, not an accident:

      * stamped-but-unmapped (inv 7): INVISIBLE to the legacy detector (the
        2026-08-07 audit's "an unprojected invoice has no mapping and is
        invisible to the daily reconcile") — now reconciled. Intended.
      * mapped-but-unstamped (inv 8, `pending_backfill`): the class the U-238a
        backfill eliminated (0 live) — now excluded, since dbo identity is the
        sole store. Intended.
    """
    conn = _fixture_db()
    conn.executescript(
        """
        INSERT INTO dbo.Invoice VALUES (7, 'p7', 'INV-7', 700.00, 0, 'Q7', 'R1'),
                                       (8, 'p8', 'INV-8', 800.00, 0, NULL, 'R1');
        INSERT INTO qbo.Invoice VALUES (107, 'Q7', 'R1', 700.00), (108, 'Q8', 'R1', 800.00);
        INSERT INTO qbo.InvoiceInvoice VALUES (8, 8, 108);
        """
    )
    new_ids = {r[0] for r in _rows(conn, INVOICE_DRAW_ROWS_SQL, "R1")}
    old_ids = {r[0] for r in _rows(conn, _LEGACY_MAPPING_HOP_SQL, "R1")}
    assert new_ids - old_ids == {7}
    assert old_ids - new_ids == {8}
    assert new_ids & old_ids == {1, 2}


def test_dbo_native_draw_rows_sql_is_mapping_free_and_realm_scoped_on_dbo():
    assert "qbo.InvoiceInvoice" not in INVOICE_DRAW_ROWS_SQL
    assert "JOIN qbo.Invoice qi ON qi.QboId = i.QboId AND qi.RealmId = i.RealmId" in INVOICE_DRAW_ROWS_SQL
    assert "WHERE i.RealmId = ?" in INVOICE_DRAW_ROWS_SQL
    assert "AND i.QboId IS NOT NULL" in INVOICE_DRAW_ROWS_SQL  # filtered UQ index eligibility


def test_reconcile_invoice_draws_executes_the_module_level_row_source():
    """The constant the equivalence test exercises must be what production
    actually runs — guards against the two drifting apart."""
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value.cursor.return_value = cursor
    service = ReconciliationService(repo=Mock())

    with patch("shared.database.get_connection", return_value=conn_ctx):
        result = service.reconcile_invoice_draws("realm-1")

    cursor.execute.assert_called_once_with(INVOICE_DRAW_ROWS_SQL, "realm-1")
    assert result["errors"] == 0 and result["flagged"] == 0


# ---------------------------------------------------------------------------
# 4. outbox worker: _refresh_invoice repoint (mirrors test_u301b's Bill shape)
# ---------------------------------------------------------------------------


class _FakeIssueRepo:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)


class _FakeInvoiceService:
    """`read_by_public_id` returns a row WITHOUT identity (ReadInvoiceByPublicId
    does not select QboId/RealmId); `read_by_id` returns the identity-bearing
    row; `read_by_qbo_identity` reproduces verify_identity_dbo_only's contract."""

    def __init__(self, by_id, *, fresh_by_identity=None):
        self._by_id = by_id
        self._fresh_by_identity = fresh_by_identity
        self.read_by_id_calls = []

    def read_by_public_id(self, public_id):
        return SimpleNamespace(id=self._by_id.id, public_id=public_id) if self._by_id else None

    def read_by_id(self, id):
        self.read_by_id_calls.append(id)
        return self._by_id

    def read_by_qbo_identity(self, qbo_id, realm_id):
        return self._fresh_by_identity


class _FakeQboInvoiceClient:
    def __init__(self):
        self.get_invoice_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_invoice(self, qbo_id):
        self.get_invoice_calls.append(qbo_id)
        return SimpleNamespace(id=qbo_id)


class _FakeQboInvoiceService:
    def __init__(self):
        self.calls = []

    def upsert_from_external(self, qbo_invoice, realm_id):
        self.calls.append((qbo_invoice, realm_id))
        return SimpleNamespace(id=999, qbo_id=qbo_invoice.id), []


class _FakeConnector:
    def __init__(self):
        self.calls = []

    def sync_from_qbo_invoice(self, *, qbo_invoice, qbo_invoice_lines):
        self.calls.append((qbo_invoice, qbo_invoice_lines))


def _patch_invoice_refresh_stack(monkeypatch, *, by_id, fresh_by_identity=None):
    svc = _FakeInvoiceService(by_id, fresh_by_identity=fresh_by_identity)
    monkeypatch.setattr("entities.invoice.business.service.InvoiceService", lambda: svc)
    client = _FakeQboInvoiceClient()
    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.external.client.QboInvoiceClient", lambda realm_id: client
    )
    qbo_svc = _FakeQboInvoiceService()
    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.business.service.QboInvoiceService", lambda: qbo_svc
    )
    connector = _FakeConnector()
    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.connector.invoice.business.service.InvoiceInvoiceConnector",
        lambda: connector,
    )
    repo = _FakeIssueRepo()
    monkeypatch.setattr(
        "integrations.intuit.qbo.reconciliation.persistence.repo.ReconciliationIssueRepository",
        lambda: repo,
    )
    return svc, client, qbo_svc, connector, repo


def _row(**overrides):
    defaults = dict(entity_public_id="inv-pid-1", realm_id="realm-1", public_id="outbox-pid-1")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_refresh_invoice_proceeds_when_dbo_identity_verifies(monkeypatch):
    invoice = SimpleNamespace(id=7, public_id="inv-pid-1", qbo_id="INV-QBO", realm_id="realm-1")
    svc, client, qbo_svc, connector, repo = _patch_invoice_refresh_stack(
        monkeypatch, by_id=invoice, fresh_by_identity=SimpleNamespace(id=7),
    )
    worker = QboOutboxWorker.__new__(QboOutboxWorker)

    worker._refresh_invoice(_row())

    assert svc.read_by_id_calls == [7]  # identity re-read by id, not trusted off the by-public-id row
    assert client.get_invoice_calls == ["INV-QBO"]
    assert qbo_svc.calls[0][1] == "realm-1"
    assert len(connector.calls) == 1
    assert repo.created == []


def test_refresh_invoice_no_qbo_id_is_a_noop(monkeypatch):
    invoice = SimpleNamespace(id=7, public_id="inv-pid-1", qbo_id=None, realm_id=None)
    svc, client, qbo_svc, connector, repo = _patch_invoice_refresh_stack(monkeypatch, by_id=invoice)
    worker = QboOutboxWorker.__new__(QboOutboxWorker)

    worker._refresh_invoice(_row())

    assert client.get_invoice_calls == [] and connector.calls == [] and repo.created == []


def test_refresh_invoice_deleted_between_reads_is_a_noop(monkeypatch):
    """read_by_public_id hits but the by-id re-read misses (deleted in between):
    nothing to refresh — never fall back to the identity-less row."""
    svc, client, qbo_svc, connector, repo = _patch_invoice_refresh_stack(monkeypatch, by_id=None)
    svc.read_by_public_id = lambda public_id: SimpleNamespace(id=7, public_id=public_id)
    worker = QboOutboxWorker.__new__(QboOutboxWorker)

    worker._refresh_invoice(_row())

    assert client.get_invoice_calls == [] and connector.calls == [] and repo.created == []


def test_refresh_invoice_hard_refuses_on_genuine_identity_conflict_with_severed_context(monkeypatch):
    invoice = SimpleNamespace(id=7, public_id="inv-pid-1", qbo_id="INV-QBO", realm_id="realm-1")
    svc, client, qbo_svc, connector, repo = _patch_invoice_refresh_stack(
        monkeypatch, by_id=invoice, fresh_by_identity=SimpleNamespace(id=999),  # reassigned
    )
    worker = QboOutboxWorker.__new__(QboOutboxWorker)

    try:
        raise RuntimeError("SyncToken mismatch already being handled")
    except RuntimeError:
        with pytest.raises(ValueError, match="identity conflict") as excinfo:
            worker._refresh_invoice(_row(entity_public_id="inv-pid-conflict"))

    assert excinfo.value.__context__ is None  # severed, so is_retryable_error can't misclassify it
    assert client.get_invoice_calls == [] and connector.calls == []
    assert len(repo.created) == 1
    issue = repo.created[0]
    assert issue["drift_type"] == "invoice_identity_conflict"
    assert issue["entity_type"] == "Invoice"
    assert issue["entity_public_id"] == "inv-pid-conflict"
    assert issue["qbo_id"] == "INV-QBO"
    assert issue["realm_id"] == "realm-1"
