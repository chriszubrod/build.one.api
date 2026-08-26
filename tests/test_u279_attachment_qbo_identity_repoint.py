"""Pure-logic tests for U-279 (Phase-5 enablement) + U-300b (dbo-only pull
repoint): the attachment identity-key reads off qbo.Attachable's internal
staging PK onto dbo.Attachment's native QboId/RealmId (U-238c), then (U-300b)
retire the qbo.Attachable/qbo.AttachableAttachment staging WRITES on the pull
path entirely in favor of `run_identity_fastpath_dbo_only`.

Covers:
  1. AttachmentRepository.read_by_qbo_identity (sproc call shape) + AttachmentService
     .read_by_qbo_identity (bare passthrough — Attachment has no row-level RBAC,
     mirrors Customer's template rather than BillCredit's access-checked one).
  2. AttachableAttachmentConnector.sync_from_qbo_attachable's dbo-only fast path
     (U-300b): a direct or race-discovered hit verifies/heals its blob and writes
     nothing else; a genuine miss (re-confirmed under the create lock) hash-dedupes
     or downloads+creates, then stamps identity — never writes qbo.Attachable or
     qbo.AttachableAttachment. There is no more "conflict" state or legacy
     mapping-table fallback (Wave-5 "trust dbo alone" — no second store left to
     drift from); a hash-matched Attachment already carrying a DIFFERENT identity
     raises instead of being silently rebound (the dbo-only equivalent of the old
     qbo.AttachableAttachment 1:1 guard).
  3. AttachableAttachmentConnector.sync_attachment_to_qbo's push-side fast path
     (U-285, untouched by U-300b): a dbo-native qbo_id (matching realm) skips the
     mapping-table hop and re-upload.
  4. The three live line-item-linking call sites (sync_qbo_bill.py,
     sync_qbo_vendorcredit.py, purchase/connector/expense/business/service.py)
     try the dbo-native lookup only. U-315 removed the qbo.AttachableAttachment
     mapping-table fallback U-279 had added here — confirmed dead post-U-300b:
     the transient pull-side QboAttachable is never DB-backed, so `.id` is
     always None for every attachable these sites ever see, and the fallback
     lookup it fed could never return a row. A direct-identity miss now just
     skips the attachable.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

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


LOCK_PATCH_TARGET = "integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock"
# _stamp_pulled_identity acquires ITS OWN app lock directly (not through
# run_identity_fastpath_dbo_only's own create lock) — a separate import in
# the connector module, so it needs its own, separate patch target.
STAMP_LOCK_PATCH_TARGET = f"{ATT_CONNECTOR_MODULE}.qbo_app_lock"


def _granted_lock(*_args, **_kwargs):
    from contextlib import contextmanager

    @contextmanager
    def _cm(*_a, **_k):
        yield True

    return _cm()


def _recording_lock_factory(recorded):
    """A qbo_app_lock stand-in that always grants and appends the requested
    resource_name to `recorded` — shared by every test that pins the exact
    lock-key shape, instead of each hand-rolling its own closure."""
    from contextlib import contextmanager

    def _recording_lock(resource_name, timeout_ms=15000):
        recorded.append(resource_name)

        @contextmanager
        def _cm():
            yield True

        return _cm()

    return _recording_lock


def test_direct_hit_healthy_blob_returns_unchanged_no_writes():
    """A direct dbo hit with a healthy blob must not touch set_qbo_identity,
    the mapping table, or the create path at all — U-300b's whole point is
    that a hit needs no cross-check."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = direct_hit

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert result is direct_hit
    attachment_service.repo.set_qbo_identity.assert_not_called()
    attachment_service.create.assert_not_called()
    mapping_repo.create.assert_not_called()
    mapping_repo.read_by_attachment_id.assert_not_called()
    mapping_repo.read_by_qbo_attachable_id.assert_not_called()


def test_direct_hit_missing_blob_heals_by_redownload_and_reupload():
    connector, _, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = direct_hit
    refreshed = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1", blob_url="https://blob/new.pdf")
    attachment_service.read_by_id.return_value = refreshed

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls, patch.object(
        connector, "_download_from_qbo", return_value=b"file-bytes"
    ), patch.object(connector, "_upload_to_blob", return_value="https://blob/new.pdf"):
        mock_blob_cls.return_value.exists.return_value = False
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert result is refreshed
    attachment_service.update_by_public_id.assert_called_once()
    # No identity re-stamp on a hit — a dbo-only hit is, by construction,
    # already the exact (qbo_id, realm_id) pair (the pre-U-300b mismatch
    # branch this dropped could never actually fire).
    attachment_service.repo.set_qbo_identity.assert_not_called()


def test_direct_hit_missing_blob_and_redownload_failure_raises():
    connector, _, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    direct_hit = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = direct_hit

    with patch(f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage") as mock_blob_cls, patch.object(
        connector, "_download_from_qbo", return_value=None
    ):
        mock_blob_cls.return_value.exists.return_value = False
        with pytest.raises(RuntimeError, match="blob missing and re-download"):
            connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")


def test_genuine_miss_creates_new_attachment_and_stamps_identity():
    """No direct hit (checked twice — the outer read AND the re-read under
    the create lock) and no hash match: downloads, uploads, creates, then
    stamps dbo identity via the wrapped stamp_identity — never touches
    qbo.Attachable or qbo.AttachableAttachment."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = None
    attachment_service.read_by_hash.return_value = None
    created = _make_direct_attachment(id=77, qbo_id=None, realm_id=None)
    attachment_service.create.return_value = created
    stamped = _make_direct_attachment(id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")
    # _stamp_pulled_identity reads twice: once to pre-check the candidate's
    # CURRENT (unmapped) identity, once to return the post-stamp refresh.
    attachment_service.read_by_id.side_effect = [created, stamped]

    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock), patch(
        STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock
    ), patch.object(connector, "_download_from_qbo", return_value=b"file-bytes"), patch.object(
        connector, "_upload_to_blob", return_value="https://blob/new.pdf"
    ):
        attachment_service.calculate_hash.return_value = "hash123"
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert result is stamped
    attachment_service.create.assert_called_once()
    attachment_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="QBO-ATT-99", realm_id="realm-1"
    )
    mapping_repo.create.assert_not_called()


def test_genuine_miss_hash_dedupe_reuses_unmapped_attachment_and_stamps():
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = None
    # ReadAttachmentByHash's real projection never carries QboId/RealmId (see
    # _stamp_pulled_identity's docstring) — model that faithfully here rather
    # than handing the mock a qbo_id this read could never actually return.
    existing_by_hash = _make_direct_attachment(id=88, qbo_id=None, realm_id=None, blob_url="https://blob/existing.pdf")
    attachment_service.read_by_hash.return_value = existing_by_hash
    stamped = _make_direct_attachment(id=88, qbo_id="QBO-ATT-99", realm_id="realm-1")
    # _stamp_pulled_identity's own read_by_id pre-check is what actually
    # confirms this candidate is unmapped (its call_count is asserted below).
    attachment_service.read_by_id.side_effect = [existing_by_hash, stamped]

    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock), patch(
        STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock
    ), patch.object(connector, "_download_from_qbo", return_value=b"file-bytes"), patch(
        f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage"
    ) as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        attachment_service.calculate_hash.return_value = "hash123"
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert result is stamped
    attachment_service.create.assert_not_called()
    attachment_service.update_by_public_id.assert_not_called()  # blob already healthy
    attachment_service.repo.set_qbo_identity.assert_called_once_with(
        id=88, qbo_id="QBO-ATT-99", realm_id="realm-1"
    )
    assert attachment_service.read_by_id.call_args_list == [call(88), call(88)]
    mapping_repo.create.assert_not_called()


def test_genuine_miss_hash_dedupe_already_bound_raises_instead_of_stealing():
    """The real identity-theft guard for a hash-deduped candidate: ReadAttachment
    ByHash's projection never carries QboId/RealmId (confirmed against
    entities/attachment/sql/dbo.attachment.sql — only ReadAttachmentById/
    ByPublicId/ByQboIdAndRealmId select those columns), so a naive check on
    read_by_hash's own result can NEVER detect a real conflict — it is
    _stamp_pulled_identity's read_by_id pre-check (which DOES carry them) that
    must catch this. Mutation target: deleting that pre-check makes this
    silently steal the identity instead of raising."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = None
    # read_by_hash returns qbo_id=None (its real, incomplete projection) even
    # though this Attachment genuinely already carries a DIFFERENT identity —
    # read_by_id (the pre-stamp re-check) is what reveals the truth.
    existing_by_hash = _make_direct_attachment(id=88, qbo_id=None, realm_id=None)
    attachment_service.read_by_hash.return_value = existing_by_hash
    truth = _make_direct_attachment(id=88, qbo_id="QBO-ATT-OTHER", realm_id="realm-1")
    attachment_service.read_by_id.return_value = truth

    # existing_by_hash carries a real blob_url (_make_direct_attachment's
    # default), so the hash-dedupe branch probes AzureBlobStorage().exists()
    # before ever reaching _stamp_pulled_identity — patch it healthy so this
    # test deterministically exercises the pre-stamp guard instead of
    # non-deterministically wandering into the unmocked blob-heal path
    # (Codex round-2 finding).
    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock), patch(
        STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock
    ), patch.object(connector, "_download_from_qbo", return_value=b"file-bytes"), patch(
        f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage"
    ) as mock_blob_cls:
        mock_blob_cls.return_value.exists.return_value = True
        attachment_service.calculate_hash.return_value = "hash123"
        with pytest.raises(ValueError, match="already carries"):
            connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    attachment_service.repo.set_qbo_identity.assert_not_called()
    attachment_service.create.assert_not_called()
    mapping_repo.create.assert_not_called()


def test_lock_resource_key_matches_dbo_only_namespace():
    """Wiring-level check that this connector's lock_resource_label="Attachment"
    produces the exact resource-key shape run_identity_fastpath_dbo_only
    documents (disjoint from create_race_lock's qbo_mapping_create:* prefix)."""
    connector, _, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.return_value = None
    attachment_service.read_by_hash.return_value = None
    attachment_service.create.return_value = _make_direct_attachment(id=77)
    attachment_service.read_by_id.return_value = _make_direct_attachment(id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")

    recorded = []

    with patch(LOCK_PATCH_TARGET, side_effect=_recording_lock_factory(recorded)), patch(
        STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock
    ), patch.object(connector, "_download_from_qbo", return_value=b"file-bytes"), patch.object(
        connector, "_upload_to_blob", return_value="https://blob/new.pdf"
    ):
        attachment_service.calculate_hash.return_value = "hash123"
        connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert recorded == ["qbo_dbo_identity_create:Attachment:QBO-ATT-99:realm-1"]


def test_race_discovered_under_lock_adopts_racer_without_touching_create_path():
    """The race this whole design closes: a concurrent pull wins the create
    lock first and binds the identity between this call's outer miss-check
    and its re-read under the lock. The connector must ADOPT that row (via
    the same blob-verify apply_fields path as a direct hit) and never invoke
    its own create-path helpers (_download_from_qbo/create/set_qbo_identity)."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
    racer_row = _make_direct_attachment(id=90, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_qbo_identity.side_effect = [None, racer_row]

    with patch(LOCK_PATCH_TARGET, side_effect=_granted_lock), patch(
        f"{ATT_CONNECTOR_MODULE}.AzureBlobStorage"
    ) as mock_blob_cls, patch.object(connector, "_download_from_qbo") as mock_download:
        mock_blob_cls.return_value.exists.return_value = True
        result = connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    assert result is racer_row
    mock_download.assert_not_called()
    attachment_service.create.assert_not_called()
    attachment_service.repo.set_qbo_identity.assert_not_called()
    mapping_repo.create.assert_not_called()
    assert attachment_service.read_by_qbo_identity.call_args_list == [
        call("QBO-ATT-99", "realm-1"),
        call("QBO-ATT-99", "realm-1"),
    ]


def test_stamp_pulled_identity_wraps_none_return_with_refreshed_row():
    """AttachmentRepository.set_qbo_identity returns None (Codex's U-300a
    review flagged this) — _stamp_pulled_identity must wrap it into the
    refreshed row run_identity_fastpath_dbo_only expects back. Reads twice:
    the pre-stamp theft-guard check, then the post-stamp refresh."""
    connector, _, attachment_service, _ = _build_connector()
    attachment_service.repo.set_qbo_identity.return_value = None
    unmapped = _make_direct_attachment(id=77, qbo_id=None, realm_id=None)
    refreshed = _make_direct_attachment(id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_id.side_effect = [unmapped, refreshed]

    with patch(STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock):
        result = connector._stamp_pulled_identity(attachment_id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")

    attachment_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="QBO-ATT-99", realm_id="realm-1"
    )
    assert attachment_service.read_by_id.call_args_list == [call(77), call(77)]
    assert result is refreshed


def test_stamp_pulled_identity_refuses_to_overwrite_a_different_existing_identity():
    """The theft-guard itself, exercised directly (not just through the
    hash-dedupe integration test above): a pre-stamp read_by_id showing a
    DIFFERENT qbo_id already on the row must raise, never call
    set_qbo_identity. An exact-match pre-existing identity (the coincidental
    already-correct case) is allowed through, not raised."""
    connector, _, attachment_service, _ = _build_connector()
    attachment_service.read_by_id.return_value = _make_direct_attachment(
        id=77, qbo_id="QBO-ATT-OTHER", realm_id="realm-1"
    )

    with patch(STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock):
        with pytest.raises(ValueError, match="already carries QBO identity QBO-ATT-OTHER"):
            connector._stamp_pulled_identity(attachment_id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")

    attachment_service.repo.set_qbo_identity.assert_not_called()


def test_stamp_pulled_identity_allows_exact_match_through():
    connector, _, attachment_service, _ = _build_connector()
    same = _make_direct_attachment(id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")
    attachment_service.read_by_id.side_effect = [same, same]

    with patch(STAMP_LOCK_PATCH_TARGET, side_effect=_granted_lock):
        result = connector._stamp_pulled_identity(attachment_id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")

    attachment_service.repo.set_qbo_identity.assert_called_once_with(
        id=77, qbo_id="QBO-ATT-99", realm_id="realm-1"
    )
    assert result is same


def test_stamp_pulled_identity_lock_key_is_scoped_to_the_candidate_row():
    """Disjoint namespace + key check: the stamp lock must be keyed on the
    CANDIDATE's own attachment_id (qbo_dbo_identity_stamp:Attachment:<id>),
    not on the incoming (qbo_id, realm_id) — that's what closes the
    hash-collision race run_identity_fastpath_dbo_only's own create lock
    can't (two different QboAttachables hash-deduping onto the SAME
    Attachment acquire two DIFFERENT qbo_id-keyed locks upstream, so without
    this second lock keyed on the shared candidate row they'd never
    contend)."""
    connector, _, attachment_service, _ = _build_connector()
    attachment_service.read_by_id.return_value = _make_direct_attachment(
        id=77, qbo_id=None, realm_id=None
    )
    recorded = []

    with patch(STAMP_LOCK_PATCH_TARGET, side_effect=_recording_lock_factory(recorded)):
        connector._stamp_pulled_identity(attachment_id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")

    assert recorded == ["qbo_dbo_identity_stamp:Attachment:77"]


def test_stamp_pulled_identity_fails_closed_on_lock_timeout():
    connector, _, attachment_service, _ = _build_connector()

    from contextlib import contextmanager

    @contextmanager
    def _denied_lock(*_a, **_k):
        yield False

    with patch(STAMP_LOCK_PATCH_TARGET, side_effect=_denied_lock):
        with pytest.raises(RuntimeError, match="Could not acquire identity-stamp lock"):
            connector._stamp_pulled_identity(attachment_id=77, qbo_id="QBO-ATT-99", realm_id="realm-1")

    attachment_service.read_by_id.assert_not_called()
    attachment_service.repo.set_qbo_identity.assert_not_called()


def test_two_racers_hash_deduping_onto_the_same_row_serialize_and_the_loser_raises():
    """The actual race the new stamp lock closes, reproduced with REAL threads
    (not sequential calls dressed up as a race): two genuinely concurrent
    calls to _stamp_pulled_identity for the SAME attachment_id but DIFFERENT
    qbo_ids (as two different QboAttachables that hash-deduped onto the same
    local row would produce). A real threading.Lock stands in for
    sp_getapplock's cross-connection mutual exclusion.

    Codex round-3 (correctly) flagged the prior version of this test for
    proving mutual exclusion only via a sleep-widened race window plus the
    OUTCOME (one winner, one loser) — plausible but not a direct proof that
    exclusion actually held. This version adds a deterministic invariant
    probe (`_enter_critical_section`/`_exit_critical_section`): if the lock
    ever let both racers inside at once, `max_concurrent_occupants` would
    observe 2 — the assertion on it below is a direct measurement of mutual
    exclusion, not an inference from the final state. The lock also now
    validates BOTH racers request the identical resource name (round-3's
    other note — the prior fake lock ignored it entirely).
    Mutation target: this is exactly what breaks if the lock is removed,
    keyed wrong, or the critical section is narrower than read+write."""
    import threading
    import time
    from contextlib import contextmanager

    connector, _, attachment_service, _ = _build_connector()

    # A tiny in-memory identity store standing in for dbo.Attachment's row,
    # shared across both racer threads exactly like a real concurrent DB
    # connection pool would be. Guarded by its own lock purely so the TEST's
    # own bookkeeping isn't itself racy — this is not the lock under test.
    state_lock = threading.Lock()
    state = {"qbo_id": None, "realm_id": None}

    # Deterministic mutual-exclusion probe: not thread-safe BY DESIGN (no
    # lock of its own) — its whole purpose is to visibly corrupt if two
    # threads are ever inside the critical section concurrently, which is
    # exactly the condition under test.
    occupancy = {"current": 0, "max": 0}

    def _enter_critical_section():
        occupancy["current"] += 1
        occupancy["max"] = max(occupancy["max"], occupancy["current"])

    def _exit_critical_section():
        occupancy["current"] -= 1

    def _read_by_id(attachment_id):
        with state_lock:
            return _make_direct_attachment(id=77, qbo_id=state["qbo_id"], realm_id=state["realm_id"])

    def _set_qbo_identity(*, id, qbo_id, realm_id):
        _enter_critical_section()
        try:
            # Widen the window so a racer NOT excluded by the lock would
            # reliably be scheduled into the (would-be) overlap, rather than
            # merely possibly so — the occupancy probe above is what actually
            # PROVES exclusion, this sleep just makes a broken lock's failure
            # deterministic instead of flaky.
            time.sleep(0.05)
            with state_lock:
                state["qbo_id"] = qbo_id
                state["realm_id"] = realm_id
        finally:
            _exit_critical_section()

    attachment_service.read_by_id.side_effect = _read_by_id
    attachment_service.repo.set_qbo_identity.side_effect = _set_qbo_identity

    # A real (non-mocked) reentrant-free lock stand-in: a plain non-reentrant
    # lock, shared across both racer threads — exactly what sp_getapplock
    # provides across two real DB connections. Asserts both racers request
    # the SAME resource name (they must, to actually contend at all).
    real_lock = threading.Lock()
    requested_resources = set()
    resources_seen_lock = threading.Lock()

    @contextmanager
    def _real_lock(resource_name, timeout_ms=15000):
        with resources_seen_lock:
            requested_resources.add(resource_name)
        acquired = real_lock.acquire(timeout=timeout_ms / 1000)
        try:
            yield acquired
        finally:
            if acquired:
                real_lock.release()

    outcomes = {}

    def _racer(qbo_id):
        try:
            outcomes[qbo_id] = ("won", connector._stamp_pulled_identity(
                attachment_id=77, qbo_id=qbo_id, realm_id="realm-1"
            ))
        except ValueError as e:
            outcomes[qbo_id] = ("lost", e)

    with patch(STAMP_LOCK_PATCH_TARGET, side_effect=_real_lock):
        t1 = threading.Thread(target=_racer, args=("QBO-ATT-X",))
        t2 = threading.Thread(target=_racer, args=("QBO-ATT-Y",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive(), "a racer thread hung — lock likely deadlocked"
    assert requested_resources == {"qbo_dbo_identity_stamp:Attachment:77"}, (
        "both racers must contend on the SAME lock resource to serialize at all"
    )
    assert occupancy["max"] == 1, (
        f"both racers were inside the critical section concurrently (max_occupants="
        f"{occupancy['max']}) — the lock did not actually exclude them"
    )
    kinds = sorted(kind for kind, _ in outcomes.values())
    assert kinds == ["lost", "won"], f"expected exactly one winner and one loser, got {outcomes}"
    winner_qbo_id = next(q for q, (kind, _) in outcomes.items() if kind == "won")
    assert state["qbo_id"] == winner_qbo_id  # final state matches the winner, never dual-written


def test_no_qbo_id_raises_without_ever_downloading():
    """A QboAttachable with no external qbo_id can't be resolved dbo-only —
    there is no legacy staging-PK fallback left in this design (U-300b), so
    this must raise rather than silently create an unmapped, unstamped
    Attachment the way the pre-U-300b legacy path did."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    qbo_attachable = _make_qbo_attachable(id=30, qbo_id=None)

    with patch.object(connector, "_download_from_qbo") as mock_download:
        with pytest.raises(RuntimeError, match="dbo-only identity fast path"):
            connector.sync_from_qbo_attachable(qbo_attachable, "realm-1")

    attachment_service.read_by_qbo_identity.assert_not_called()
    mock_download.assert_not_called()
    mapping_repo.create.assert_not_called()


# --- Section 3: sync_attachment_to_qbo (push side) fast path ---


def test_push_fast_path_dbo_qbo_id_returns_transient_no_legacy_reads():
    """U-300c-prereq: dbo.Attachment's own (QboId, RealmId) is the SOLE
    'already pushed' signal. When it is present and the realm matches, the push
    returns a transient QboAttachable (id=None, qbo_id echoed) and performs NO
    qbo.Attachable / qbo.AttachableAttachment read — both legacy tables are
    being retired. (Mutation guard: neutering this early return sends the method
    into the upload path and this assertion fails.)"""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    # _transient_attachable_from_dbo reads these display fields off the Attachment.
    attachment = _make_direct_attachment(
        id=55, qbo_id="QBO-ATT-99", realm_id="realm-1",
        original_filename="att55.pdf", filename="att55.pdf", description=None,
        category="qbo_import", content_type="application/pdf", file_size=123,
    )

    result = connector.sync_attachment_to_qbo(
        attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
    )

    assert result.id is None  # transient, never persisted
    assert result.qbo_id == "QBO-ATT-99"
    assert result.realm_id == "realm-1"
    mapping_repo.read_by_attachment_id.assert_not_called()


def test_push_realm_mismatch_is_not_already_pushed_falls_through_to_upload():
    """A dbo qbo_id stamped under a DIFFERENT realm must not short-circuit the
    push for THIS realm — already_dbo_pushed is False, so the method proceeds to
    the upload path (proven here by the no-blob_url guard it hits first) rather
    than taking the transient fast path. No legacy mapping-table lookup."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    attachment = _make_direct_attachment(id=55, qbo_id="QBO-ATT-99", realm_id="realm-OTHER", blob_url=None)

    with pytest.raises(ValueError, match="no blob_url"):
        connector.sync_attachment_to_qbo(
            attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
        )
    mapping_repo.read_by_attachment_id.assert_not_called()


def test_push_no_dbo_qbo_id_falls_through_to_upload():
    """No dbo identity → not already-pushed → proceeds to the upload path with
    no legacy mapping-table lookup (U-300c-prereq removed that fallback; a live
    check proved 0 mapping rows whose dbo.Attachment.QboId is NULL, so the old
    mapping-hit-while-dbo-absent branch was unreachable in prod)."""
    connector, mapping_repo, attachment_service, _ = _build_connector()
    attachment = _make_direct_attachment(id=55, qbo_id=None, realm_id=None, blob_url=None)

    with pytest.raises(ValueError, match="no blob_url"):
        connector.sync_attachment_to_qbo(
            attachment=attachment, realm_id="realm-1", entity_type="Bill", entity_id="qbo-bill-1"
        )
    mapping_repo.read_by_attachment_id.assert_not_called()


# --- Section 4: the three live line-item-linking call sites ---


SYNC_BILL_MODULE = "scripts.sync_qbo_bill"
SYNC_VC_MODULE = "scripts.sync_qbo_vendorcredit"
PURCHASE_EXPENSE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"


def test_bill_link_attachments_direct_hit_skips_mapping_lookup():
    from scripts.sync_qbo_bill import _link_attachments_to_bill_line_items

    bill_line_item = SimpleNamespace(id=1, public_id="bli-pub-1")
    with patch(f"{SYNC_BILL_MODULE}.BillLineItemService") as MockBLI, patch(
        f"{SYNC_BILL_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_BILL_MODULE}.BillLineItemAttachmentService") as MockBLIAttSvc:
        MockBLI.return_value.read_by_bill_id.return_value = [bill_line_item]
        MockBLIAttSvc.return_value.read_by_bill_line_item_ids.return_value = []
        attachment = SimpleNamespace(id=55, public_id="att-pub-55")
        MockAttSvc.return_value.read_by_qbo_identity.return_value = attachment

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_line_items(bill_id=42, qbo_attachables=[qbo_attachable])

    assert links == 1
    MockAttSvc.return_value.read_by_qbo_identity.assert_called_once_with("QBO-ATT-99", "realm-1")
    MockBLIAttSvc.return_value.create.assert_called_once_with(
        bill_line_item_public_id="bli-pub-1", attachment_public_id="att-pub-55"
    )


def test_bill_link_attachments_direct_miss_skips_cleanly_no_mapping_table_fallback():
    """U-315: the qbo.AttachableAttachment mapping-table fallback is gone — confirmed
    dead post-U-300b (qbo_attachable.id is always None for anything this loop ever
    sees). A direct-identity miss must just skip, with no further lookup attempted."""
    from scripts.sync_qbo_bill import _link_attachments_to_bill_line_items

    assert "AttachableAttachmentRepository" not in inspect.getsource(_link_attachments_to_bill_line_items)
    assert "read_by_qbo_attachable_id" not in inspect.getsource(_link_attachments_to_bill_line_items)

    bill_line_item = SimpleNamespace(id=1, public_id="bli-pub-1")
    with patch(f"{SYNC_BILL_MODULE}.BillLineItemService") as MockBLI, patch(
        f"{SYNC_BILL_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_BILL_MODULE}.BillLineItemAttachmentService") as MockBLIAttSvc:
        MockBLI.return_value.read_by_bill_id.return_value = [bill_line_item]
        MockBLIAttSvc.return_value.read_by_bill_line_item_ids.return_value = []
        MockAttSvc.return_value.read_by_qbo_identity.return_value = None

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_line_items(bill_id=42, qbo_attachables=[qbo_attachable])

    assert links == 0
    MockAttSvc.return_value.read_by_id.assert_not_called()
    MockBLIAttSvc.return_value.create.assert_not_called()


def test_vendorcredit_link_attachments_direct_hit_skips_mapping_lookup():
    from scripts.sync_qbo_vendorcredit import _link_attachments_to_bill_credit_line_items

    line_item = SimpleNamespace(id=1, public_id="bcli-pub-1")
    with patch(f"{SYNC_VC_MODULE}.BillCreditLineItemService") as MockBCLI, patch(
        f"{SYNC_VC_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_VC_MODULE}.BillCreditLineItemAttachmentService") as MockBCLIAttSvc:
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


def test_vendorcredit_link_attachments_direct_miss_skips_cleanly_no_mapping_table_fallback():
    """U-315: fallback removed — see test_bill_link_attachments_direct_miss_skips_cleanly_no_mapping_table_fallback."""
    from scripts.sync_qbo_vendorcredit import _link_attachments_to_bill_credit_line_items

    assert "AttachableAttachmentRepository" not in inspect.getsource(_link_attachments_to_bill_credit_line_items)
    assert "read_by_qbo_attachable_id" not in inspect.getsource(_link_attachments_to_bill_credit_line_items)

    line_item = SimpleNamespace(id=1, public_id="bcli-pub-1")
    with patch(f"{SYNC_VC_MODULE}.BillCreditLineItemService") as MockBCLI, patch(
        f"{SYNC_VC_MODULE}.AttachmentService"
    ) as MockAttSvc, patch(f"{SYNC_VC_MODULE}.BillCreditLineItemAttachmentService") as MockBCLIAttSvc:
        MockBCLI.return_value.read_by_bill_credit_id.return_value = [line_item]
        MockBCLIAttSvc.return_value.read_by_bill_credit_line_item_ids.return_value = []
        MockAttSvc.return_value.read_by_qbo_identity.return_value = None

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = _link_attachments_to_bill_credit_line_items(
            bill_credit_id=42, qbo_attachables=[qbo_attachable]
        )

    assert links == 0
    MockAttSvc.return_value.read_by_id.assert_not_called()
    MockBCLIAttSvc.return_value.create.assert_not_called()


def test_purchase_expense_link_attachments_direct_hit_skips_mapping_lookup():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        sync_purchase_attachments_to_expense_line_items,
    )

    line_item = SimpleNamespace(id=1, public_id="eli-pub-1")
    with patch(
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


def test_purchase_expense_link_attachments_direct_miss_skips_cleanly_no_mapping_table_fallback():
    """U-315: fallback removed — see test_bill_link_attachments_direct_miss_skips_cleanly_no_mapping_table_fallback."""
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        sync_purchase_attachments_to_expense_line_items,
    )

    assert "AttachableAttachmentRepository" not in inspect.getsource(sync_purchase_attachments_to_expense_line_items)
    assert "read_by_qbo_attachable_id" not in inspect.getsource(sync_purchase_attachments_to_expense_line_items)

    line_item = SimpleNamespace(id=1, public_id="eli-pub-1")
    with patch(
        "entities.attachment.business.service.AttachmentService"
    ) as MockAttSvc, patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as MockELI, patch(
        "entities.expense_line_item_attachment.business.service.ExpenseLineItemAttachmentService"
    ) as MockELIAttSvc:
        MockELI.return_value.read_by_expense_id.return_value = [line_item]
        MockELIAttSvc.return_value.read_by_expense_line_item_ids.return_value = []
        MockAttSvc.return_value.read_by_qbo_identity.return_value = None

        qbo_attachable = _make_qbo_attachable(id=30, qbo_id="QBO-ATT-99", realm_id="realm-1")
        links = sync_purchase_attachments_to_expense_line_items(
            expense_id=42, qbo_attachables=[qbo_attachable]
        )

    assert links == 0
    MockAttSvc.return_value.read_by_id.assert_not_called()
    MockELIAttSvc.return_value.create.assert_not_called()
