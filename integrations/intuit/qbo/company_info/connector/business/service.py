# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import (
    build_duplicate_qbo_identity_conflict_desc,
    record_duplicate_identity_conflict,
)
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.business.model import QboCompanyInfo as QboCompanyInfoModel
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.company.business.service import CompanyService
from entities.company.business.model import Company

logger = logging.getLogger(__name__)


class CompanyInfoCompanyConnector:
    """
    Connector service for synchronization between QboCompanyInfo and Company modules.

    U-350: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.CompanyInfoCompany` mapping-table read/write of any kind (the
    pattern-setter for the U-349 program, mirrors U-300a's
    `AttachableAttachmentConnector` / U-310's `CustomerCustomerConnector` /
    U-313's `VendorVendorConnector`, per Wave 5's "trust dbo alone" plan,
    `docs/design/u349-qbo-mapping-table-retirement.md`).
    `dbo.Company.QboId`/`RealmId` (U-238a/U-277) is the sole identity store;
    dbo.Company's own filtered unique index (`UQ_Company_QboId_RealmId`) +
    `SetCompanyQboIdentity`'s theft-clear UPDATE guarantee at most one row
    holds a given identity at any instant, so a direct hit needs no
    cross-check and the old heal/adopt/dedup branch structure (driven by a
    second, independently-writable mapping table) no longer has anything to
    drift from. The dead `sync_from_company_to_qbo` push path (zero callers,
    confirmed at Gate-1) was removed alongside it.
    """

    def __init__(
        self,
        company_service: Optional[CompanyService] = None,
        qbo_company_info_service: Optional[QboCompanyInfoService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the CompanyInfoCompanyConnector."""
        self.company_service = company_service or CompanyService()
        self.qbo_company_info_service = qbo_company_info_service or QboCompanyInfoService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_to_company(self, qbo_company_info_id: int, realm_id: str) -> Company:
        """
        Sync data from QboCompanyInfo to Company module, via the dbo-only
        identity fast path (U-350).

        Args:
            qbo_company_info_id: Database ID of QboCompanyInfo record
            realm_id: QBO realm ID fallback, used only when
                `qbo_company_info.realm_id` itself is falsy (U-277's own
                fallback, preserved here — no sibling connector takes this
                extra parameter since none of them have needed it).

        Returns:
            Company: The synced Company record
        """
        qbo_company_info_repo = self.qbo_company_info_service.repo
        qbo_company_info = qbo_company_info_repo.read_by_id(qbo_company_info_id)

        if not qbo_company_info:
            raise ValueError(f"QboCompanyInfo with ID {qbo_company_info_id} not found")

        # Company.Name maps to CompanyInfo.LegalName
        company_name = qbo_company_info.legal_name
        company_website = qbo_company_info.web_addr
        effective_realm_id = qbo_company_info.realm_id or realm_id

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_company_info.qbo_id,
            realm_id=effective_realm_id,
            entity_label="Company",
            external_label="QboCompanyInfo",
            lock_resource_label="Company",
            read_direct_by_qbo_identity=self.company_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_company_fields_and_sync(
                entity, name=company_name, website=company_website,
            ),
            resolve_candidate=lambda: self._resolve_company_candidate(
                qbo_company_info, name=company_name, website=company_website,
                realm_id=effective_realm_id,
            ),
            stamp_identity=lambda candidate: self._stamp_company_identity(
                candidate, qbo_company_info, realm_id=effective_realm_id,
            ),
        )
        if outcome.entity is None:
            # No longer race-reachable in practice (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a directly
            # invoked falsy qbo_company_info.qbo_id, mirroring every sibling
            # connector's identical guard (U-310/U-313/U-311).
            raise RuntimeError(
                f"Failed to resolve Company for QboCompanyInfo {qbo_company_info.id} "
                f"(qbo_id={qbo_company_info.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _apply_company_fields_and_sync(
        self, entity: Company, *, name: str, website: str,
    ) -> Optional[Company]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (U-350): write
        the QboCompanyInfo-derived fields onto an existing dbo-identity-matched
        Company and persist. QBO is source of truth — always overwrites, same
        as the pre-U-350 legacy path (its own ModifiedDatetime comparison
        never gated the write, only its log message; dropped as dead weight
        here along with `_parse_datetime`, which had no other caller).

        Returns None on a ROWVERSION-race/concurrent-delete `update_by_id`
        miss (U-291) — `run_identity_fastpath_dbo_only`'s own `_apply()`
        raises `raise_concurrent_write_race` unconditionally whenever
        `apply_fields` returns None, so this method staying silent on a miss
        is what keeps that single raise as the ONE place the guarantee lives
        (mirrors every sibling connector's identical HIT-branch shape).
        """
        entity.name = name
        entity.website = website
        return self.company_service.repo.update_by_id(entity)

    def _resolve_company_candidate(
        self, qbo_company_info: QboCompanyInfoModel, *, name: str, website: str, realm_id: str,
    ) -> Company:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-350):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.Company currently holds this
        identity, including the re-read under lock). Adopts an existing
        Company by NAME match first — the pre-U-350 legacy path's own Step 2
        by-name dedup safety net, preserved WITHOUT its mapping read/repair —
        before falling through to a fresh create. Mirrors
        `CustomerCustomerConnector._resolve_customer_candidate` (U-310).
        """
        existing = self.company_service.read_by_name(name) if name else None
        if existing is None:
            logger.info(
                f"No existing Company found. Creating new Company from QboCompanyInfo {qbo_company_info.id}"
            )
            return self.company_service.create(name=name or "", website=website or "")

        # The name-matched row must be re-checked for an existing, DIFFERENT
        # (QboId, RealmId) before being returned as the candidate -- the
        # dbo-only equivalent of the old mapping-table duplicate check.
        # Shared with `_stamp_company_identity`'s own pre-stamp re-read via
        # `_check_no_conflicting_company_identity`, so the two guards can't
        # drift out of sync with each other. Mirrors
        # `CustomerCustomerConnector._resolve_customer_candidate`'s
        # Decision-2-style guard (U-310).
        self._check_no_conflicting_company_identity(existing, qbo_company_info, realm_id=realm_id)

        logger.info(
            f"Binding existing local Company {existing.id} ({name}) to QboCompanyInfo "
            f"{qbo_company_info.id} by name match"
        )
        # Field write deliberately deferred to _stamp_company_identity, which
        # applies it atomically with the identity stamp under the candidate's
        # own lock (mirrors CustomerCustomerConnector's Codex round-2 fix, U-310).
        return existing

    def _stamp_company_identity(
        self, candidate: Company, qbo_company_info: QboCompanyInfoModel, *, realm_id: str,
    ) -> Optional[Company]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-350),
        delegating the row-scoped lock + theft-guard + write sequence to the
        shared `stamp_dbo_identity_with_lock` (U-328/U-331 —
        `docs/design/stamp-lock-helper.md`) — see that function's own
        docstring for why a SECOND lock, keyed on the CANDIDATE's company_id,
        is needed here: `_resolve_company_candidate` binds by NAME (a
        side-channel business key), so two different QboCompanyInfo syncs
        (unlikely in practice — one Company per realm — but not structurally
        impossible, e.g. during a realm migration) could name-match onto the
        SAME local Company concurrently. Mirrors
        `CustomerCustomerConnector._stamp_customer_identity` (U-310).

        `apply_fields` writes name/website unconditionally (QBO is source of
        truth, matching the pre-U-350 legacy path's own behavior) and feeds
        `update_by_id`'s return value into the shared helper's own
        None-guard. Sanitizes a blank/None `legal_name` to `""` — matching
        `_resolve_company_candidate`'s own `.create(name=name or "", ...)` —
        because unlike the HIT branch (an already-mapped Company whose
        `[Name] NOT NULL` risk on a blank QBO LegalName is a pre-existing,
        unchanged-by-U-350 legacy gap), this closure ALSO runs right after a
        genuine-miss `.create()` call, and a raw `None` here would immediately
        overwrite that call's already-sanitized name with a NULL, failing
        `UpdateCompanyById`'s `NOT NULL` column (Codex xhigh round-1 P1).
        `write_identity` delegates to `create_mapping`, which now only stamps
        dbo identity — there is no mapping row left to write. `on_conflict`
        keeps only the reconciliation-recording half of the former
        `_check_no_conflicting_company_identity` call — the raise itself now
        lives in the shared helper.
        """
        def _apply_fields(c: Company) -> Optional[Company]:
            c.name = qbo_company_info.legal_name or ""
            c.website = qbo_company_info.web_addr or ""
            return self.company_service.repo.update_by_id(c)

        candidate_id = coerce_id(candidate.id)
        return stamp_dbo_identity_with_lock(
            candidate_id=candidate_id,
            entity_label="Company",
            qbo_id=qbo_company_info.qbo_id,
            realm_id=realm_id,
            read_by_id=self.company_service.read_by_id,
            apply_fields=_apply_fields,
            write_identity=lambda c: self.create_mapping(
                company_id=c.id,
                qbo_company_info_id=qbo_company_info.id,
                qbo_id=qbo_company_info.qbo_id,
                realm_id=realm_id,
            ),
            on_conflict=lambda c: self._record_duplicate_qbo_company_issue(
                qbo_company_info=qbo_company_info, local_company=c, existing_qbo_id=c.qbo_id,
                realm_id=realm_id,
            ),
        )

    def _check_no_conflicting_company_identity(
        self, local_company: Company, qbo_company_info: QboCompanyInfoModel, *, realm_id: str,
    ) -> None:
        """
        Shared guard for `_resolve_company_candidate`'s name-matched
        candidate and `_stamp_company_identity`'s pre-stamp re-read (U-350)
        -- ONE implementation instead of two hand-kept-in-sync copies, since
        `_stamp_company_identity`'s SetCompanyQboIdentity theft-clear only
        protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not
        `local_company`'s PRIOR identity; it would not stop a silent
        re-point on its own. Mirrors
        `CustomerCustomerConnector._check_no_conflicting_identity` (U-310).

        No-op when `local_company` has no QBO identity yet, or already
        carries this EXACT (qbo_id, realm_id) pair (a benign re-resolve).
        Otherwise records a `company_identity_conflict` reconciliation issue
        (reusing the DriftType the now-deleted mapping-table-era
        `_record_identity_mapping_conflict_issue` used to emit) and raises.
        Checking QboId alone would miss a same-QboId-different-realm
        collision (QBO ids are only unique WITHIN a realm) — both fields
        must match.
        """
        existing_qbo_id = getattr(local_company, "qbo_id", None)
        if not existing_qbo_id or (
            existing_qbo_id == qbo_company_info.qbo_id
            and (getattr(local_company, "realm_id", None) or "") == (realm_id or "")
        ):
            return
        self._record_duplicate_qbo_company_issue(
            qbo_company_info=qbo_company_info, local_company=local_company, existing_qbo_id=existing_qbo_id,
            realm_id=realm_id,
        )
        raise ValueError(
            f"Company {local_company.id} already carries a DIFFERENT identity "
            f"(QboId={existing_qbo_id}, RealmId={getattr(local_company, 'realm_id', None)}) than "
            f"incoming QboCompanyInfo {qbo_company_info.qbo_id} (realm_id={realm_id}) — "
            f"refusing to overwrite it."
        )

    def _record_duplicate_qbo_company_issue(
        self,
        *,
        qbo_company_info: QboCompanyInfoModel,
        local_company: Company,
        existing_qbo_id: str,
        realm_id: str,
    ) -> None:
        """
        Name-match-vs-different-existing-identity duplicate (U-350). Reuses
        `company_identity_conflict` (this family's own category, previously
        emitted by the deleted mapping-table `_record_identity_mapping_conflict_issue`).
        Mirrors `CustomerCustomerConnector._record_duplicate_qbo_customer_issue` (U-310).

        `realm_id` is the caller's EFFECTIVE realm (`qbo_company_info.realm_id
        or` the connector-level fallback — U-277's own fallback, unique to
        this family) — the same value the conflict check itself compared
        against, not a re-derivation from `qbo_company_info.realm_id` alone.
        Using the raw staging value here would misreport the incoming realm
        (and file the issue under `realm_id=""`) whenever staging carries a
        null RealmId and the caller's fallback supplied the real one (Codex
        xhigh round-1 P2).
        """
        existing_realm_id = getattr(local_company, "realm_id", None)
        conflict_desc = build_duplicate_qbo_identity_conflict_desc(
            existing_qbo_id=existing_qbo_id,
            incoming_qbo_id=qbo_company_info.qbo_id,
            existing_realm_id=existing_realm_id,
            incoming_realm_id=realm_id,
        )
        details = (
            f"Duplicate QBO company detected. QboCompanyInfo {qbo_company_info.id} "
            f"(Name='{qbo_company_info.legal_name}') name-matches local Company "
            f"{local_company.id} which already carries {conflict_desc}. "
            f"Resolve by merging or renaming one of the QBO companies."
        )
        record_duplicate_identity_conflict(
            self.reconciliation_repo,
            drift_type="company_identity_conflict",
            entity_type="Company",
            entity_public_id=str(local_company.public_id) if local_company.public_id else None,
            qbo_id=str(qbo_company_info.qbo_id) if qbo_company_info.qbo_id else None,
            realm_id=realm_id or "",
            details=details,
        )

    def create_mapping(
        self,
        company_id: int,
        qbo_company_info_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> None:
        """
        Bind a Company to its QBO identity by stamping dbo.Company.QboId/RealmId
        (U-350).

        `dbo.Company.QboId`/`RealmId` is the SOLE identity store — this no
        longer reads or writes a `qbo.CompanyInfoCompany` mapping row (that
        table is retired). `qbo_company_info_id` stays in the signature for
        the caller's symmetry but is no longer persisted anywhere.

        The sole caller is `_stamp_company_identity`, which reaches this only
        under `stamp_dbo_identity_with_lock`'s own theft-guard — already
        refusing to overwrite a DIFFERENT existing identity — so the former
        mapping-table 1:1 validations are redundant and were removed with the
        mapping write. Mirrors `CustomerProjectConnector.create_mapping`
        (U-311/U-314-prereq).
        """
        self.company_service.repo.set_qbo_identity(
            id=company_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
