"""U-508 — the CompanyInfo pull's match-by-address-fields fallback is deleted.

`QboCompanyInfoService._sync_physical_address` used to carry a "# Migration:"
block: on a qbo_id MISS it called `QboPhysicalAddressRepository.read_all()`
(957 rows), linear-scanned in Python for a row with the same
(line1, city, postal_code), and REWROTE that row's `qbo_id` to CompanyInfo's.
It was scoped by neither realm nor owner.

Why it originally shipped WITH U-514, and why it now ships ALONE
(U-514 was split out on 2026-09-23 and is NOT deployed):

    Realm-scoping the read makes the three CompanyInfo-owned
    `qbo.PhysicalAddress` rows MISS -- they carry `RealmId = NULL`. That miss
    fed the fallback. `read_all()` is `ORDER BY [QboId] ASC`, and '1246_bill'
    sorts before '1612', so the first row matching
    ('PO Box 594', 'Brentwood', '37024') is Vendor 1246 (Rogers Build Inc.)'s
    BILLING address. The pull would have silently re-keyed it to
    CompanyInfo's id. Verified against live data.

Why the block was already spent, and therefore safe to delete rather than fix:
commit e3f2f068 (07:34:07 UTC) introduced it 14 minutes AFTER the three rows it
was written to heal were created (07:20:32 UTC). It re-keyed them once in
January 2026 and has matched nothing since -- zero rows carry the synthetic
shape it healed. It has never fired destructively.

Its TRIGGER, though, is still live and deliberately kept: `sync_from_qbo` still
synthesises `f"{realm_id}-company"` (and -legal / -customer-communication) when
QBO omits an address `Id`, so the miss branch remains permanently reachable.
`test_synthetic_id_fallback_still_creates_cleanly` pins that, which is what
stops the three tests above it from passing vacuously against a pull that can
no longer miss at all.
"""
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo as QboCompanyInfoExternal,
)
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress

REALM = "9130353016965726"

SERVICE_MODULE = "integrations.intuit.qbo.company_info.business.service"
CLIENT_TARGET = f"{SERVICE_MODULE}.QboCompanyInfoClient"
REPO_TARGET = f"{SERVICE_MODULE}.QboPhysicalAddressRepository"

# The live collision, verbatim: Vendor 1246 (Rogers Build Inc.)'s billing
# address shares all three matched fields with the company address.
SHARED_LINE1 = "PO Box 594"
SHARED_CITY = "Brentwood"
SHARED_POSTAL = "37024"


def _row(*, id, qbo_id, realm_id, line1=None, city=None, postal_code=None, **rest):
    """A real QboPhysicalAddress, not a SimpleNamespace -- a renamed field must
    break these tests rather than silently diverge from the shipped dataclass."""
    return QboPhysicalAddress(
        id=id,
        public_id=f"pub-{id}",
        # base64 of b"rowver01"; `row_version_bytes` is what the update path reads.
        row_version="cm93dmVyMDE=",
        created_datetime="2026-01-01 00:00:00",
        modified_datetime="2026-01-01 00:00:00",
        qbo_id=qbo_id,
        realm_id=realm_id,
        line1=line1,
        line2=rest.get("line2"),
        city=city,
        country=rest.get("country"),
        country_sub_division_code=rest.get("country_sub_division_code"),
        postal_code=postal_code,
    )


class _FakeAddressRepo:
    """In-memory QboPhysicalAddressRepository.

    `read_by_qbo_id` reproduces the U-514 sproc's realm scoping (exact qbo_id
    AND realm, with NULL matching only NULL). `read_all` RETURNS ROWS rather
    than raising: a fake that blew up would make the "read_all is never called"
    mutation fail for the wrong reason and would hide whether the reinstated
    fallback actually adopts the wrong row.
    """

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.read_all_calls = 0
        self.create_calls = []
        self.update_calls = []
        self._next_id = max((r.id for r in self.rows), default=900) + 1

    # -- reads ------------------------------------------------------------
    def read_by_qbo_id(self, *, qbo_id):
        # qbo_id ONLY -- matching HEAD. Realm scoping is U-514's, split out of
        # this unit on 2026-09-23. The miss this test needs is the SYNTHETIC-id
        # miss (`{realm}-company`), which exists with or without realm scoping;
        # U-514 later adds a second way to miss (realm mismatch), which is why
        # the deleted fallback must never come back.
        for row in self.rows:
            if row.qbo_id == qbo_id:
                return row
        return None

    def read_all(self):
        self.read_all_calls += 1
        # ORDER BY [QboId] ASC -- the ordering that makes '1246_bill' win.
        return sorted(self.rows, key=lambda r: (r.qbo_id or ""))

    # -- writes -----------------------------------------------------------
    def create(self, **kwargs):
        # **kwargs, not an explicit signature: the "drop realm_id" mutation
        # must reach the assertion, not die on a TypeError here.
        self.create_calls.append(kwargs)
        row = _row(
            id=self._next_id,
            qbo_id=kwargs.get("qbo_id"),
            realm_id=kwargs.get("realm_id"),
            line1=kwargs.get("line1"),
            city=kwargs.get("city"),
            postal_code=kwargs.get("postal_code"),
        )
        self._next_id += 1
        self.rows.append(row)
        return row

    def update_by_id(self, **kwargs):
        self.update_calls.append(kwargs)
        for row in self.rows:
            if row.id == kwargs.get("id"):
                row.qbo_id = kwargs.get("qbo_id", row.qbo_id)
                if "realm_id" in kwargs:
                    row.realm_id = kwargs["realm_id"]
                row.line1 = kwargs.get("line1", row.line1)
                row.city = kwargs.get("city", row.city)
                row.postal_code = kwargs.get("postal_code", row.postal_code)
                return row
        return None


def _external(**overrides):
    payload = {
        "Id": "CI-1",
        "CompanyName": "ROGERS BUILD INC",
        "LegalName": "Rogers Build, Inc.",
        "Country": "US",
        "FiscalYearStartMonth": 1,
    }
    payload.update(overrides)
    return QboCompanyInfoExternal(**payload)


def _company_addr(**overrides):
    payload = {
        "Line1": SHARED_LINE1,
        "City": SHARED_CITY,
        "PostalCode": SHARED_POSTAL,
        "CountrySubDivisionCode": "TN",
    }
    payload.update(overrides)
    return payload


def _patched_client(response):
    client = MagicMock()
    client.get_company_info.return_value = response
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


def _run_pull(repo, response):
    """Drive the real `sync_from_qbo`, not `_sync_physical_address` directly --
    the synthetic-id derivation (`... or f"{realm_id}-company"`) lives in the
    caller, and it is what makes the miss branch reachable at all."""
    svc = QboCompanyInfoService()
    with patch(CLIENT_TARGET, return_value=_patched_client(response)), patch(
        REPO_TARGET, return_value=repo
    ):
        return svc.sync_from_qbo(realm_id=REALM)


# --------------------------------------------------------------------------
# 1. A qbo_id miss CREATES. It never adopts a row by address fields.
# --------------------------------------------------------------------------


def test_qbo_id_miss_creates_never_adopts():
    """A pre-existing row with the identical (line1, city, postal_code) but a
    different qbo_id must be left completely alone."""
    twin = _row(
        id=910,
        qbo_id="1246_bill",
        realm_id=REALM,
        line1=SHARED_LINE1,
        city=SHARED_CITY,
        postal_code=SHARED_POSTAL,
    )
    repo = _FakeAddressRepo([twin])

    _run_pull(repo, _external(CompanyAddr=_company_addr(Id="1612")))

    assert len(repo.create_calls) == 1, (
        "a qbo_id miss must CREATE; adopting an address-field twin is the U-508 defect"
    )
    assert repo.create_calls[0]["qbo_id"] == "1612"
    assert repo.update_calls == [], "nothing existing may be updated on a miss"


# --------------------------------------------------------------------------
# 2. read_all() is never called during address sync.
# --------------------------------------------------------------------------


def test_read_all_is_never_called_during_address_sync():
    """The 957-row scan is gone, not merely narrowed. Pinned separately from the
    behavioural tests because a future "optimised" variant of the same fallback
    (scan fewer rows, still by address fields) would keep those green while
    reintroducing exactly this cross-owner reach."""
    repo = _FakeAddressRepo(
        [
            _row(
                id=910,
                qbo_id="1246_bill",
                realm_id=REALM,
                line1=SHARED_LINE1,
                city=SHARED_CITY,
                postal_code=SHARED_POSTAL,
            )
        ]
    )

    _run_pull(
        repo,
        _external(
            CompanyAddr=_company_addr(),
            LegalAddr=_company_addr(Line1="1 Legal Way", City="Nashville", PostalCode="37201"),
            CustomerCommunicationAddr=_company_addr(),
        ),
    )

    assert repo.read_all_calls == 0, (
        "address sync must never enumerate the whole qbo.PhysicalAddress table; "
        f"read_all() was called {repo.read_all_calls}x"
    )


# --------------------------------------------------------------------------
# 3. THE P0, as a test.
# --------------------------------------------------------------------------


def test_foreign_vendor_billing_address_is_never_adopted_or_rekeyed():
    """The exact live collision the deleted block would have caused under U-514.

    Vendor 1246 (Rogers Build Inc.)'s billing address `1246_bill` shares
    ('PO Box 594', 'Brentwood', '37024') with the company address, and sorts
    FIRST under `read_all()`'s `ORDER BY [QboId] ASC`. The CompanyInfo response
    here carries no address `Id`, so the caller synthesises
    `{realm}-company` -- a guaranteed qbo_id miss, which is precisely the state
    that fed the fallback.

    Its row must come out byte-identical, and a NEW row must be created beside
    it.
    """
    foreign = _row(
        id=910,
        qbo_id="1246_bill",
        realm_id=REALM,
        line1=SHARED_LINE1,
        city=SHARED_CITY,
        postal_code=SHARED_POSTAL,
    )
    # A second, later-sorting twin: proves the assertion is about "adopt none",
    # not merely "adopt a different one".
    also_foreign = _row(
        id=920,
        qbo_id="1612_ship",
        realm_id=REALM,
        line1=SHARED_LINE1,
        city=SHARED_CITY,
        postal_code=SHARED_POSTAL,
    )
    repo = _FakeAddressRepo([foreign, also_foreign])

    outcome = _run_pull(repo, _external(CompanyAddr=_company_addr()))

    assert foreign.qbo_id == "1246_bill", (
        "Vendor 1246's billing address was re-keyed -- this is the U-508 P0"
    )
    assert also_foreign.qbo_id == "1612_ship"
    assert foreign.realm_id == REALM and also_foreign.realm_id == REALM
    touched = [call.get("id") for call in repo.update_calls]
    assert 910 not in touched and 920 not in touched, (
        f"a foreign address row was updated by the CompanyInfo pull: {repo.update_calls!r}"
    )
    assert len(repo.create_calls) == 1
    assert repo.create_calls[0]["qbo_id"] == f"{REALM}-company"
    # And the pull still produced a usable projection -- the new row's id.
    assert outcome.synced[0].company_addr_id not in (910, 920, None)


# --------------------------------------------------------------------------
# 4. The synthetic-id trigger is still live (anti-vacuity for 1-3).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,suffix",
    [
        ("CompanyAddr", "-company"),
        ("LegalAddr", "-legal"),
        ("CustomerCommunicationAddr", "-customer-communication"),
    ],
)
def test_synthetic_id_fallback_still_creates_cleanly(field, suffix):
    """When QBO omits an address `Id`, the caller synthesises one and the sync
    CREATES under it. Delete that `or f"{realm_id}-..."` branch and the three
    tests above go vacuous -- there would be no reachable miss left to guard.

    It also pins that the synthesised id is the one PERSISTED, so the
    qbo_id-only read finds the row again next tick. (U-514 later adds a
    realm-scoped read, which introduces a SECOND way to miss -- which is why
    the deleted fallback must never come back.)
    """
    repo = _FakeAddressRepo()

    outcome = _run_pull(repo, _external(**{field: _company_addr()}))

    assert len(repo.create_calls) == 1, "an Id-less address must still be persisted"
    assert repo.create_calls[0]["qbo_id"] == f"{REALM}{suffix}"
    assert repo.update_calls == []
    assert outcome.should_hold is False


# --------------------------------------------------------------------------
# 5. (Parked) realm_id threading moved to U-514 on 2026-09-23 along with the
#    realm-scoped read it depends on. This service's read is qbo_id-only and
#    neither write persists a realm; see scratchpad/u514_parked/.
# --------------------------------------------------------------------------





def test_service_source_carries_no_read_all_call():
    """A behavioural pin can be satisfied by a fallback that is merely gated off.
    This asserts the call site itself is absent from the module."""
    import inspect

    from integrations.intuit.qbo.company_info.business import service as module

    method = module.QboCompanyInfoService._sync_physical_address
    source = inspect.getsource(method)
    # The method's own docstring NAMES the deleted call (that is the record of
    # why it must stay deleted), so strip it before pinning the code.
    body = source.replace(method.__doc__ or "", "")
    assert "read_all" not in body, (
        "_sync_physical_address must not reference read_all at all -- see that "
        "method's docstring for the row it would adopt"
    )


# --------------------------------------------------------------------------
# 8. The one transition the deletion leaves unhandled is RECORDED, not silent.
# --------------------------------------------------------------------------


def test_synthetic_to_real_transition_records_its_consequence_and_defers_the_runbook():
    """What the docstring must carry, and what it must NOT.

    MUST: the traced consequence -- conflict, raise, permanent skip, watermark
    advances -- because that is what a reader needs to judge the deletion.

    MUST NOT: the operational runbook. It lived here and drifted out of true
    three times in one day (it cited a unique index that was reverted, claimed
    the rows merely 'drift' when they actually conflict-and-skip, and claimed
    any synthetic row proves a live transition when it does not). A function
    docstring cannot be kept accurate as a procedure; BOARD.md U-518 owns it.
    """
    from integrations.intuit.qbo.company_info.business.service import (
        QboCompanyInfoService,
    )

    doc = QboCompanyInfoService._sync_physical_address.__doc__

    # the consequence, stated correctly
    assert "address_identity_conflict" not in doc or "PERMANENT SKIP" in doc
    assert "_check_no_conflicting_address_identity" in doc
    assert "PERMANENT SKIP" in doc, "the docstring must state that the projection is SKIPPED"
    assert "watermark advances" in doc

    # the false claims that kept coming back
    assert "just drift" not in doc, "the rows do not drift; they conflict and skip"
    assert "Nothing errors" not in doc

    # the runbook is deferred, not inlined
    assert "U-518" in doc, "the docstring must point at the unit that owns the runbook"
    assert "MANUAL REMEDY" not in doc, "the runbook is back in the docstring"
