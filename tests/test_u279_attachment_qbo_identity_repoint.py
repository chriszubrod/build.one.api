"""Pure-logic tests for U-279 (Phase-5 enablement): repoint the attachment
identity-key reads off qbo.Attachable's internal staging PK onto
dbo.Attachment's native QboId/RealmId (U-238c).

Covers:
  1. AttachmentRepository.read_by_qbo_identity (sproc call shape) + AttachmentService
     .read_by_qbo_identity (bare passthrough — Attachment has no row-level RBAC,
     mirrors Customer's template rather than BillCredit's access-checked one).
  2. AttachableAttachmentConnector.sync_from_qbo_attachable's new dbo-native fast
     path: hit updates without the mapping-table hop + self-heals a missing mapping
     row; a detected conflict RAISES and writes nothing (never falls through — the
     U-276 hotfix lesson); a miss falls through to the pre-existing mapping-table
     path unchanged. Mirrors test_u276_customer_project_qbo_identity_repoint.py's
     Section 2 shape.
  3. AttachableAttachmentConnector.sync_attachment_to_qbo's push-side fast path:
     a dbo-native qbo_id (matching realm) skips the mapping-table hop and re-upload.
  4. The three live line-item-linking call sites (sync_qbo_bill.py,
     sync_qbo_vendorcredit.py, purchase/connector/expense/business/service.py) try
     the dbo-native lookup first, falling back to the qbo.AttachableAttachment
     mapping table on a miss — read-only, no write/identity-theft risk.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
    AttachableAttachmentConnector,
)

ATT_CONNECTOR_MODULE = "integrations.intuit.qbo.attachable.connector.attachment.business.service"


def _make_qbo_attachable(**overrides):
    defaults = dict(
        id=30,
        qbo_id="QBO-ATT-99",
        realm_id="realm-1",
        file_name="invoice.pdf",
        note="note",
        category="qbo_import",
        content_type="application/pdf",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_direct_attachment(**overrides):
    defaults = dict(
        id=55,
        public_id="att-pub-55",
        blob_url="https://blob/att55.pdf",
        row_version="rv55",
        qbo_id="QBO-ATT-99",
        realm_id="realm-1",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --- Section 1: repo-level sproc call shape ---


def test_attachment_repo_read_by_qbo_identity_calls_sproc():
    from entities.attachment.persistence.repo import AttachmentRepository

    repo = AttachmentRepository()
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    with patch("entities.attachment.persistence.repo.get_connection") as mock_conn_ctx, patch(
        "entities.attachment.persistence.repo.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.read_by_qbo_identity("QBO-ATT-99", "realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == "ReadAttachmentByQboIdAndRealmId"
    assert mock_call.call_args.kwargs["params"] == {"QboId": "QBO-ATT-99", "RealmId": "realm-1"}


def _attachment_db_row(**overrides):
    """A fake pyodbc row exercising AttachmentRepository._from_db's REAL mapping
    logic — mirrors tests/test_u255_system_authz_consolidation.py's
    _vendor_row/test_repo_from_db_qbo_active_mapping pattern (the established
    idiom in this codebase for guarding a new getattr-mapped optional column)."""
    defaults = dict(
        Id=55,
        PublicId="00000000-0000-0000-0000-000000000055",
        RowVersion=b"\x00" * 8,
        CreatedDatetime="2026-01-01 00:00:00",
        ModifiedDatetime="2026-01-01 00:00:00",
        Filename="invoice.pdf",
        OriginalFilename="invoice.pdf",
        FileExtension="pdf",
        ContentType="application/pdf",
        FileSize=1024,
        FileHash="hash123",
        BlobUrl="https://blob/att55.pdf",
        Description=None,
        Category="qbo_import",
        Tags=None,
        IsArchived=False,
        Status="active",
        DownloadCount=0,
        LastDownloadedDatetime=None,
        ExpirationDate=None,
        StorageTier="Hot",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_attachment_repo_from_db_maps_qbo_identity():
    """Guards the exact regression class this call site is fragile to: a typo'd
    getattr key here (vs. the SELECT alias in ReadAttachmentByQboIdAndRealmId /
    ReadAttachmentById / ReadAttachmentByPublicId) would make read_by_qbo_identity's
    Attachment.qbo_id always come back None — the whole U-279 fast path (all 3 call
    sites + push side) would silently and permanently fall back to the legacy
    mapping-table path in production, with the rest of this suite staying green
    (it only exercises _from_db via SimpleNamespace fakes, never a real DB row)."""
    from entities.attachment.persistence.repo import AttachmentRepository

    repo = AttachmentRepository()

    without = repo._from_db(_attachment_db_row())
    assert without.qbo_id is None
    assert without.realm_id is None

    with_identity = repo._from_db(_attachment_db_row(QboId="QBO-ATT-99", RealmId="realm-1"))
    assert with_identity.qbo_id == "QBO-ATT-99"
    assert with_identity.realm_id == "realm-1"


def test_attachment_service_read_by_qbo_identity_is_bare_passthrough():
    """Attachment has no row-level RBAC (unlike BillCredit's read_by_id/
    read_by_public_id, which gate on assert_can_access_bill_credit) — the new
    method must be a bare passthrough, mirroring Customer's template."""
    from entities.attachment.business.service import AttachmentService

    repo = Mock()
    repo.read_by_qbo_identity.return_value = SimpleNamespace(id=55)
    service = AttachmentService(repo=repo)

    result = service.read_by_qbo_identity("QBO-ATT-99", "realm-1")

    repo.read_by_qbo_identity.assert_called_once_with("QBO-ATT-99", "realm-1")
    assert result.id == 55


# --- Section 2: AttachableAttachmentConnector.sync_from_qbo_attachable fast path ---


def _build_connector():
    mapping_repo = Mock()
    attachment_service = Mock()
    attachment_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = AttachableAttachmentConnector(
        mapping_repo=mapping_repo,
        attachment_service=attachment_service,
        auth_service=Mock(),
        reconciliation_repo=reconciliation_repo,
    )
    return connector, mapping_repo, attachment_service, reconciliation_repo


def test_resolve_mapping_state_consistent():
    connector, mapping_repo, _, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30)
    mapping_repo.read_by_attachment_id.return_value = SimpleNamespace(id=1, qbo_attachable_id=30)

    state, _, _ = connector._resolve_mapping_state(attachment_id=55, qbo_attachable=qbo_attachable)

    assert state == "consistent"
    mapping_repo.read_by_qbo_attachable_id.assert_not_called()  # settled by attachment-side alone


def test_resolve_mapping_state_missing():
    connector, mapping_repo, _, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30)
    mapping_repo.read_by_attachment_id.return_value = None
    mapping_repo.read_by_qbo_attachable_id.return_value = None

    state, _, _ = connector._resolve_mapping_state(attachment_id=55, qbo_attachable=qbo_attachable)

    assert state == "missing"


def test_resolve_mapping_state_qbo_side_conflict():
    connector, mapping_repo, _, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30)
    mapping_repo.read_by_attachment_id.return_value = None
    mapping_repo.read_by_qbo_attachable_id.return_value = SimpleNamespace(id=2, attachment_id=9)

    state, by_attachment, by_qbo_attachable = connector._resolve_mapping_state(
        attachment_id=55, qbo_attachable=qbo_attachable
    )

    assert state == "conflict"
    assert by_attachment is None
    assert by_qbo_attachable.attachment_id == 9


def test_resolve_mapping_state_local_side_conflict():
    connector, mapping_repo, _, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30)
    mapping_repo.read_by_attachment_id.return_value = SimpleNamespace(id=3, qbo_attachable_id=5)

    state, by_attachment, by_qbo_attachable = connector._resolve_mapping_state(
        attachment_id=55, qbo_attachable=qbo_attachable
    )

    assert state == "conflict"
    assert by_attachment.qbo_attachable_id == 5


def test_raise_identity_mapping_conflict_issue_names_both_sides():
    connector, _, _, reconciliation_repo = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    qbo_side = SimpleNamespace(id=2, attachment_id=9, qbo_attachable_id=30)
    local_side = SimpleNamespace(id=3, attachment_id=55, qbo_attachable_id=5)

    connector._raise_identity_mapping_conflict_issue(
        qbo_attachable=qbo_attachable, dbo_attachment_id=55,
        local_side_mapping=local_side, qbo_side_mapping=qbo_side,
        realm_id="realm-1",
    )

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "attachment_identity_conflict"
    assert "55" in kwargs["details"]
    assert "9" in kwargs["details"]
    assert "5" in kwargs["details"]


def test_fast_path_conflict_qbo_side_raises_and_writes_nothing():
    """On a detected qbo-side conflict, sync_from_qbo_attachable must record the
    issue and RAISE — never fall through to the legacy mapping-table path.
    Falling through would re-map the CONFLICTING Attachment (9) and call
    set_qbo_identity on it — SetAttachmentQboIdentity's own theft-detection UPDATE
    applies against ANY row already carrying that (QboId, RealmId), which is
    exactly `direct` (55): the same fall-through identity-theft bug class the
    U-276 hotfix closed for Customer/Project. This test locks in that it can't
    recur here."""
    connector, mapping_repo, attachment_service, reconciliation_repo = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55)
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_attachment_id.return_value = None
    conflicting = SimpleNamespace(id=2, attachment_id=9, qbo_attachable_id=30)
    mapping_repo.read_by_qbo_attachable_id.return_value = conflicting

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    reconciliation_repo.create.assert_called_once()
    attachment_service.update_by_public_id.assert_not_called()
    attachment_service.create.assert_not_called()
    attachment_service.repo.set_qbo_identity.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_fast_path_conflict_local_side_only_raises_no_duplicate_create():
    """A 'local-side-only' conflict must ALSO raise, not fall through to the
    download-and-create branch — that would mint a duplicate Attachment for a
    QboAttachable `direct` already represents."""
    connector, mapping_repo, attachment_service, reconciliation_repo = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55)
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_attachment_id.return_value = SimpleNamespace(id=3, qbo_attachable_id=5)

    with pytest.raises(ValueError, match="identity conflict"):
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    reconciliation_repo.create.assert_called_once()
    attachment_service.create.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_fast_path_hit_self_heals_missing_mapping():
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55)
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_attachment_id.return_value = None
    mapping_repo.read_by_qbo_attachable_id.return_value = None

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    mapping_repo.create.assert_called_once_with(attachment_id=55, qbo_attachable_id=30)


def test_fast_path_self_heal_race_escalates_to_recorded_conflict():
    """A concurrent sync can turn 'missing' into 'conflict' between the pre-check
    and the create() call (no sp_getapplock serializes this — same known gap as
    U-276). The create() failure must not just be a bare warning — re-check and
    record a real conflict issue when that's what actually happened."""
    connector, mapping_repo, attachment_service, reconciliation_repo = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55)
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_attachment_id.side_effect = [None, None]
    mapping_repo.read_by_qbo_attachable_id.side_effect = [
        None, SimpleNamespace(id=9, attachment_id=3, qbo_attachable_id=qbo_attachable.id)
    ]
    mapping_repo.create.side_effect = Exception("UNIQUE constraint violation")

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    reconciliation_repo.create.assert_called_once()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "attachment_identity_conflict"


def test_fast_path_hit_consistent_skips_mapping_write_and_identity_restamp():
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_attachment_id.return_value = SimpleNamespace(id=1, qbo_attachable_id=30)

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert result is direct_hit
    mapping_repo.create.assert_not_called()
    # Identity already agrees by construction on the fast path — must not
    # re-stamp (wasted round trip on the steady-state path this exists to
    # keep cheap — mirrors U-276/278's identical assertion).
    attachment_service.repo.set_qbo_identity.assert_not_called()


def test_fast_path_hit_stale_identity_heals_via_existing_overwrite_logic():
    """A fast-path hit whose stored qbo_id/realm_id has drifted from the incoming
    qbo_attachable (e.g. a historical dual-write gap) must still self-heal via the
    pre-existing set_qbo_identity overwrite — that logic is reused unchanged, not
    replaced, by this unit."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55, qbo_id="QBO-ATT-OLD", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    mapping_repo.read_by_attachment_id.return_value = SimpleNamespace(id=1, qbo_attachable_id=30)

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    attachment_service.repo.set_qbo_identity.assert_called_once_with(
        id=55, qbo_id="QBO-ATT-99", realm_id="realm-1"
    )


def test_fast_path_miss_falls_back_to_mapping_table_path():
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = None
    mapping_repo.read_by_qbo_attachable_id.return_value = None
    mapping_repo.read_by_attachment_id.return_value = None  # _create_mapping's 1:1 guard
    attachment_service.read_by_hash.return_value = None

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage"), patch.object(
        connector, "_download_from_qbo", return_value=b"file-bytes"
    ), patch.object(connector, "_upload_to_blob", return_value="https://blob/new.pdf"):
        attachment_service.calculate_hash.return_value = "hash123"
        created = _make_direct_attachment(id=77)
        attachment_service.create.return_value = created
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    attachment_service.read_by_qbo_identity.assert_called_once_with("QBO-ATT-99", "realm-1")
    assert result is created
    attachment_service.create.assert_called_once()


def test_fast_path_skipped_entirely_when_no_qbo_id():
    """A QboAttachable with no external qbo_id can't possibly have a dbo-native
    identity match — the fast-path lookup should not even be attempted."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id=None)
    mapping_repo.read_by_qbo_attachable_id.return_value = None
    mapping_repo.read_by_attachment_id.return_value = None  # _create_mapping's 1:1 guard
    attachment_service.read_by_hash.return_value = None

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage"), patch.object(
        connector, "_download_from_qbo", return_value=b"file-bytes"
    ), patch.object(connector, "_upload_to_blob", return_value="https://blob/new.pdf"):
        attachment_service.calculate_hash.return_value = "hash123"
        attachment_service.create.return_value = _make_direct_attachment(id=77)
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    attachment_service.read_by_qbo_identity.assert_not_called()


# --- Section 3: sync_attachment_to_qbo (push side) fast path ---


def test_push_fast_path_dbo_qbo_id_skips_mapping_lookup_and_reupload():
    connector, mapping_repo, attachment_service, _ = _build_connector()
    attachment = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1")

    existing_qbo_attachable = SimpleNamespace(id=30, qbo_id="QBO-ATT-99")
    with patch(f"{ATT_CONNECTOR_MODULE}.QboAttachableRepository") as MockQboAttRepo:
        MockQboAttRepo.return_value.read_by_qbo_id_and_realm_id.return_value = existing_qbo_attachable
        result = connector.sync_attachment_to_qbo(
            attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
        )

    assert result is existing_qbo_attachable
    mapping_repo.read_by_attachment_id.assert_not_called()
    MockQboAttRepo.return_value.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-ATT-99", "realm-1")


def test_push_fast_path_staging_row_missing_falls_back_to_mapping_table_as_safety_net():
    """dbo says pushed (qbo_id + realm match) but the qbo.Attachable staging row
    that should corroborate it is missing (staging-cache lag, not a genuine
    two-source conflict) — must fall through to the legacy mapping-table check
    as a safety net, not blindly trust the unsubstantiated qbo_id."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    attachment = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1")
    existing_mapping = SimpleNamespace(id=1, qbo_attachable_id=30)
    mapping_repo.read_by_attachment_id.return_value = existing_mapping
    existing_qbo_attachable = SimpleNamespace(id=30)

    with patch(f"{ATT_CONNECTOR_MODULE}.QboAttachableRepository") as MockQboAttRepo:
        MockQboAttRepo.return_value.read_by_qbo_id_and_realm_id.return_value = None  # staging row missing
        MockQboAttRepo.return_value.read_by_id.return_value = existing_qbo_attachable
        result = connector.sync_attachment_to_qbo(
            attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
        )

    assert result is existing_qbo_attachable
    MockQboAttRepo.return_value.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-ATT-99", "realm-1")
    mapping_repo.read_by_attachment_id.assert_called_once_with(55)


def test_push_fast_path_realm_mismatch_falls_back_to_mapping_table():
    """A dbo qbo_id stamped under a DIFFERENT realm must not short-circuit the
    push for THIS realm — fall back to the legacy mapping-table check. Route the
    fallback through the mapping-found early-return so the rest of the (unrelated)
    upload path is never reached."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    attachment = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-OTHER")
    existing_mapping = SimpleNamespace(id=1, qbo_attachable_id=30)
    mapping_repo.read_by_attachment_id.return_value = existing_mapping
    existing_qbo_attachable = SimpleNamespace(id=30)

    with patch(f"{ATT_CONNECTOR_MODULE}.QboAttachableRepository") as MockQboAttRepo:
        MockQboAttRepo.return_value.read_by_id.return_value = existing_qbo_attachable
        result = connector.sync_attachment_to_qbo(
            attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
        )

    assert result is existing_qbo_attachable
    MockQboAttRepo.return_value.read_by_qbo_id_and_realm_id.assert_not_called()
    mapping_repo.read_by_attachment_id.assert_called_once_with(55)


def test_push_no_dbo_qbo_id_falls_back_to_mapping_table():
    connector, mapping_repo, attachment_service, _ = _build_connector()
    attachment = _make_direct_attachment(id=55, qbo_id=None, realm_id=None)
    existing_mapping = SimpleNamespace(id=1, qbo_attachable_id=30)
    mapping_repo.read_by_attachment_id.return_value = existing_mapping
    existing_qbo_attachable = SimpleNamespace(id=30)

    with patch(f"{ATT_CONNECTOR_MODULE}.QboAttachableRepository") as MockQboAttRepo:
        MockQboAttRepo.return_value.read_by_id.return_value = existing_qbo_attachable
        result = connector.sync_attachment_to_qbo(
            attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
        )

    assert result is existing_qbo_attachable
    mapping_repo.read_by_attachment_id.assert_called_once_with(55)
    MockQboAttRepo.return_value.read_by_id.assert_called_once_with(30)


# --- Section 4: the three live line-item-linking call sites ---


SYNC_BILL_MODULE = "scripts.sync_qbo_bill"
SYNC_VC_MODULE = "scripts.sync_qbo_vendorcredit"
PURCHASE_EXPENSE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"


def test_bill_link_attachments_direct_hit_skips_mapping_lookup():
    from scripts.sync_qbo_bill import _link_attachments_to_bill_line_items

    bill_line_item = SimpleNamespace(id=1, public_id="bli-pub-1")
    with patch(f"{SYNC_BILL_MODULE}.BillLineItemService") as MockBLI, patch(
        f"{SYNC_BILL_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_BILL_MODULE}.BillLineItemAttachmentService") as MockBLIAttSvc, patch(
        f"{SYNC_BILL_MODULE}.AttachableAttachmentRepository"
    ) as MockMappingRepo:
        MockBLI.return_value.read_by_bill_id.return_value = [bill_line_item]
        MockBLIAttSvc.return_value.read_by_bill_line_item_ids.return_value = []
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_qbo_identity.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_line_items(bill_id=42, qbo_attachables=[qbo_attachable])

    assert links == 1
    MockAttSvc.return_value.read_by_qbo_identity.assert_called_once_with("QBO-ATT-99", "realm-1")
    MockMappingRepo.return_value.read_by_qbo_attachable_id.assert_not_called()
    MockBLIAttSvc.return_value.create.assert_called_once_with(
        bill_line_item_public_id="bli-pub-1", attachment_public_id="att-pub-55"
    )


def test_bill_link_attachments_direct_miss_falls_back_to_mapping_table():
    from scripts.sync_qbo_bill import _link_attachments_to_bill_line_items

    bill_line_item = SimpleNamespace(id=1, public_id="bli-pub-1")
    with patch(f"{SYNC_BILL_MODULE}.BillLineItemService") as MockBLI, patch(
        f"{SYNC_BILL_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_BILL_MODULE}.BillLineItemAttachmentService") as MockBLIAttSvc, patch(
        f"{SYNC_BILL_MODULE}.AttachableAttachmentRepository"
    ) as MockMappingRepo:
        MockBLI.return_value.read_by_bill_id.return_value = [bill_line_item]
        MockBLIAttSvc.return_value.read_by_bill_line_item_ids.return_value = []
        MockAttSvc.return_value.read_by_qbo_identity.return_value = None
        MockMappingRepo.return_value.read_by_qbo_attachable_id.return_value = SimpleNamespace(attachment_id=55)
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_id.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_line_items(bill_id=42, qbo_attachables=[qbo_attachable])

    assert links == 1
    MockMappingRepo.return_value.read_by_qbo_attachable_id.assert_called_once_with(30)
    MockAttSvc.return_value.read_by_id.assert_called_once_with(55)


def test_vendorcredit_link_attachments_direct_hit_skips_mapping_lookup():
    from scripts.sync_qbo_vendorcredit import _link_attachments_to_bill_credit_line_items

    line_item = SimpleNamespace(id=1, public_id="bcli-pub-1")
    with patch(f"{SYNC_VC_MODULE}.BillCreditLineItemService") as MockBCLI, patch(
        f"{SYNC_VC_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_VC_MODULE}.BillCreditLineItemAttachmentService") as MockBCLIAttSvc, patch(
        f"{SYNC_VC_MODULE}.AttachableAttachmentRepository"
    ) as MockMappingRepo:
        MockBCLI.return_value.read_by_bill_credit_id.return_value = [line_item]
        MockBCLIAttSvc.return_value.read_by_bill_credit_line_item_ids.return_value = []
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_qbo_identity.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_credit_line_items(
            bill_credit_id=42, qbo_attachables=[qbo_attachable]
        )

    assert links == 1
    MockAttSvc.return_value.read_by_qbo_identity.assert_called_once_with("QBO-ATT-99", "realm-1")
    MockMappingRepo.return_value.read_by_qbo_attachable_id.assert_not_called()


def test_vendorcredit_link_attachments_direct_miss_falls_back_to_mapping_table():
    from scripts.sync_qbo_vendorcredit import _link_attachments_to_bill_credit_line_items

    line_item = SimpleNamespace(id=1, public_id="bcli-pub-1")
    with patch(f"{SYNC_VC_MODULE}.BillCreditLineItemService") as MockBCLI, patch(
        f"{SYNC_VC_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_VC_MODULE}.BillCreditLineItemAttachmentService") as MockBCLIAttSvc, patch(
        f"{SYNC_VC_MODULE}.AttachableAttachmentRepository"
    ) as MockMappingRepo:
        MockBCLI.return_value.read_by_bill_credit_id.return_value = [line_item]
        MockBCLIAttSvc.return_value.read_by_bill_credit_line_item_ids.return_value = []
        MockAttSvc.return_value.read_by_qbo_identity.return_value = None
        MockMappingRepo.return_value.read_by_qbo_attachable_id.return_value = SimpleNamespace(attachment_id=55)
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_id.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_credit_line_items(
            bill_credit_id=42, qbo_attachables=[qbo_attachable]
        )

    assert links == 1
    MockMappingRepo.return_value.read_by_qbo_attachable_id.assert_called_once_with(30)
    MockAttSvc.return_value.read_by_id.assert_called_once_with(55)


def test_purchase_expense_link_attachments_direct_hit_skips_mapping_lookup():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        sync_purchase_attachments_to_expense_line_items,
    )

    line_item = SimpleNamespace(id=1, public_id="eli-pub-1")
    with patch(
        "integrations.intuit.qbo.attachable.connector.attachment.persistence.repo.AttachableAttachmentRepository"
    ) as MockMappingRepo, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAttSvc, patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as MockELI, patch(
        "entities.expense_line_item_attachment.business.service.ExpenseLineItemAttachmentService"
    ) as MockELIAttSvc:
        MockELI.return_value.read_by_expense_id.return_value = [line_item]
        MockELIAttSvc.return_value.read_by_expense_line_item_ids.return_value = []
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_qbo_identity.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = sync_purchase_attachments_to_expense_line_items(
            expense_id=42, qbo_attachables=[qbo_attachable]
        )

    assert links == 1
    MockAttSvc.return_value.read_by_qbo_identity.assert_called_once_with("QBO-ATT-99", "realm-1")
    MockMappingRepo.return_value.read_by_qbo_attachable_id.assert_not_called()


def test_purchase_expense_link_attachments_direct_miss_falls_back_to_mapping_table():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        sync_purchase_attachments_to_expense_line_items,
    )

    line_item = SimpleNamespace(id=1, public_id="eli-pub-1")
    with patch(
        "integrations.intuit.qbo.attachable.connector.attachment.persistence.repo.AttachableAttachmentRepository"
    ) as MockMappingRepo, patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAttSvc, patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as MockELI, patch(
        "entities.expense_line_item_attachment.business.service.ExpenseLineItemAttachmentService"
    ) as MockELIAttSvc:
        MockELI.return_value.read_by_expense_id.return_value = [line_item]
        MockELIAttSvc.return_value.read_by_expense_line_item_ids.return_value = []
        MockAttSvc.return_value.read_by_qbo_identity.return_value = None
        MockMappingRepo.return_value.read_by_qbo_attachable_id.return_value = SimpleNamespace(attachment_id=55)
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_id.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = sync_purchase_attachments_to_expense_line_items(
            expense_id=42, qbo_attachables=[qbo_attachable]
        )

    assert links == 1
    MockMappingRepo.return_value.read_by_qbo_attachable_id.assert_called_once_with(30)
    MockAttSvc.return_value.read_by_id.assert_called_once_with(55)
