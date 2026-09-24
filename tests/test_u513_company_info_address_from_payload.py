"""U-513 — CompanyInfo's addresses project from the INLINE payload, not from
`qbo.PhysicalAddress`.

The pull used to write the three addresses into the staging table and hand the
row ids to the caller, which read each row straight back out via
`PhysicalAddressAddressConnector.sync_from_qbo_to_address`. Every column staging
held is present inline on the CompanyInfo response, so that round trip bought
nothing but a dependency on a table being sunset.

Phase 1 (this file) removed the READ and kept the WRITE, so the customer and
vendor packages could be converted in parallel against a table that still
existed. Phase 3a removed the write too, once they were; the assertions here
that pinned the surviving write moved to
`test_u513_ph3_company_info_no_staging_write.py`, which pins its absence.
Everything else in this file is unchanged and is what phase 3a had to preserve.

⚠️ THE IDENTITY, verified against the code rather than assumed — the unit brief
   guessed the staging row was keyed on the realm id (`record_id = realm_id`).
   It is NOT. `sync_from_qbo` keys each slot on
   `address_ref.id or f"{realm_id}-{suffix}"` — the address's own QBO Id when
   QBO supplies one, and a per-slot synthetic (`-company` / `-legal` /
   `-customer-communication`) when it does not. `dbo.Address.QboId` carries
   whichever was in force, so projecting under a bare realm id would mint a
   duplicate for every slot instead of updating the existing row. Section 1
   pins both branches.

⚠️ THE REALM is None, and that is deliberate — `_sync_physical_address` has
   never passed `realm_id` to the staging repo, so this family's
   `qbo.PhysicalAddress` rows (and therefore the `dbo.Address` rows stamped from
   them) carry `RealmId = NULL`, and `ReadAddressByQboIdAndRealmId` matches NULL
   only against NULL. Section 4 pins it, with the full trace of what passing the
   live realm would cost.
"""
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.company_info.business.service import (
    ADDRESS_SLOTS,
    QboCompanyInfoService,
)
from integrations.intuit.qbo.company_info.connector.address.business.service import (
    CompanyInfoAddressConnector,
)
from integrations.intuit.qbo.company_info.external.schemas import (
    QboCompanyInfo as QboCompanyInfoExternal,
)

REALM = "9130353016965726"

SERVICE_MODULE = "integrations.intuit.qbo.company_info.business.service"
CLIENT_TARGET = f"{SERVICE_MODULE}.QboCompanyInfoClient"
ADDRESS_CONNECTOR_TARGET = f"{SERVICE_MODULE}.CompanyInfoAddressConnector"

LINE1 = "PO Box 594"
CITY = "Brentwood"
POSTAL = "37024"


def _external(**overrides):
    """A real external-schema CompanyInfo, not a SimpleNamespace -- a renamed
    field must break these tests rather than silently diverge from what
    `sync_from_qbo` actually reads."""
    payload = {
        "Id": "CI-1",
        "CompanyName": "ROGERS BUILD INC",
        "LegalName": "Rogers Build, Inc.",
        "Country": "US",
        "FiscalYearStartMonth": 1,
    }
    payload.update(overrides)
    return QboCompanyInfoExternal(**payload)


def _addr(**overrides):
    payload = {
        "Line1": LINE1,
        "City": CITY,
        "PostalCode": POSTAL,
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


def _run_pull(response, *, address_connector=None):
    """Drive the REAL `sync_from_qbo`. The synthetic-id derivation and the
    blankness gate both live in it, so exercising `project_address` directly
    would prove nothing about what production passes.

    No staging repo is patched: since phase 3a the pull constructs none, and
    patching a name the module no longer carries would be an AttributeError
    rather than a guard. Its absence is pinned in
    `test_u513_ph3_company_info_no_staging_write.py`."""
    svc = QboCompanyInfoService()
    connector_cls = MagicMock(return_value=address_connector or MagicMock())
    with patch(CLIENT_TARGET, return_value=_patched_client(response)), patch(
        ADDRESS_CONNECTOR_TARGET, connector_cls
    ):
        outcome = svc.sync_from_qbo(realm_id=REALM)
    return outcome


# --------------------------------------------------------------------------
# 1. The identity. A wrong one mints a duplicate instead of updating.
# --------------------------------------------------------------------------


def test_company_address_projects_under_the_qbo_address_id_when_qbo_supplies_one():
    """QBO's own `CompanyAddr.Id` wins. Not the CompanyInfo `Id`, not the realm
    id -- `dbo.Address.QboId` carries this exact string today."""
    connector = MagicMock()

    _run_pull(_external(CompanyAddr=_addr(Id="1612")), address_connector=connector)

    connector.project_address.assert_called_once()
    _args, kwargs = connector.project_address.call_args
    assert kwargs["qbo_id"] == "1612", (
        "the address must project under QBO's own address Id; any other value "
        "MINTS A DUPLICATE dbo.Address rather than updating the existing row"
    )
    assert kwargs["qbo_id"] != REALM, "the realm id is not the address identity"


@pytest.mark.parametrize(
    "field,suffix",
    [
        ("CompanyAddr", "company"),
        ("LegalAddr", "legal"),
        ("CustomerCommunicationAddr", "customer-communication"),
    ],
)
def test_id_less_address_projects_under_the_per_slot_synthetic_id(field, suffix):
    """When QBO omits the address `Id` the pull synthesises `{realm}-{suffix}`,
    and that is what `dbo.Address.QboId` holds. The three suffixes are distinct:
    collapsing them (or using a bare realm id) would make all three slots
    collide on ONE dbo.Address row."""
    connector = MagicMock()

    _run_pull(_external(**{field: _addr()}), address_connector=connector)

    _args, kwargs = connector.project_address.call_args
    assert kwargs["qbo_id"] == f"{REALM}-{suffix}"


def test_all_three_slots_project_under_distinct_identities():
    """Anti-collision, as one assertion over a whole response."""
    connector = MagicMock()

    _run_pull(
        _external(
            CompanyAddr=_addr(),
            LegalAddr=_addr(Line1="1 Legal Way", City="Nashville", PostalCode="37201"),
            CustomerCommunicationAddr=_addr(Id="1612"),
        ),
        address_connector=connector,
    )

    projected = [c.kwargs["qbo_id"] for c in connector.project_address.call_args_list]
    assert projected == [f"{REALM}-company", f"{REALM}-legal", "1612"]
    assert len(set(projected)) == 3, "two slots resolved to the same dbo.Address"


def test_the_inline_payload_is_the_source_of_the_projected_fields():
    """Not the staging row. The whole point of the unit: every column staging
    held is already on the response."""
    connector = MagicMock()

    _run_pull(
        _external(
            CompanyAddr=_addr(Line1="123 Main St", Line2="Suite 4", City="Franklin",
                              CountrySubDivisionCode="TN", PostalCode="37064")
        ),
        address_connector=connector,
    )

    args, _kwargs = connector.project_address.call_args
    address_ref = args[0]
    assert address_ref.line1 == "123 Main St"
    assert address_ref.line2 == "Suite 4"
    assert address_ref.city == "Franklin"
    assert address_ref.country_sub_division_code == "TN"
    assert address_ref.postal_code == "37064"


def test_projection_never_reads_a_staging_row_back():
    """The dependency being removed, pinned directly: the pull must not hand a
    `qbo.PhysicalAddress` row id to anything. A read-back would keep the sunset
    blocked no matter how the fields were sourced."""
    connector = MagicMock()

    _run_pull(_external(CompanyAddr=_addr()), address_connector=connector)

    assert connector.sync_from_qbo_to_address.call_count == 0, (
        "the staging-id projection entry point must not be called any more"
    )


# --------------------------------------------------------------------------
# 2. Blank means ABSENT. Nothing is minted.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="all-empty"),
        pytest.param({"Id": "1612"}, id="id-only"),
        pytest.param({"Line1": "   ", "City": "  ", "PostalCode": " "}, id="whitespace-only"),
        pytest.param({"Country": "US"}, id="country-only"),
        pytest.param({"Line2": "Suite 4"}, id="line2-only"),
        pytest.param({"CountrySubDivisionCode": "TN"}, id="state-only"),
    ],
)
def test_blank_inline_address_mints_nothing(payload):
    """`line1`, `city` and `postal_code` all empty after `.strip()` is QBO's
    placeholder shape (U-506 P1's predicate, re-expressed against the payload).
    191 blank `dbo.Address` rows already exist because two of the three staging
    writers had no guard at all; this one must not add more."""
    connector = MagicMock()

    _run_pull(_external(CompanyAddr=payload), address_connector=connector)

    assert connector.project_address.call_count == 0, (
        f"a blank address ({payload!r}) minted a dbo.Address"
    )


def test_an_omitted_address_slot_mints_nothing():
    connector = MagicMock()
    _run_pull(_external(), address_connector=connector)
    assert connector.project_address.call_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"Line1": LINE1}, id="line1-only"),
        pytest.param({"City": CITY}, id="city-only"),
        pytest.param({"PostalCode": POSTAL}, id="postal-only"),
    ],
)
def test_any_one_of_the_three_matched_fields_makes_an_address_present(payload):
    """Anti-vacuity for the blankness tests: the gate is ALL-three-empty, not
    any-empty. Tighten it to `all present` and the blank tests above still pass
    while real addresses stop projecting."""
    connector = MagicMock()

    _run_pull(_external(CompanyAddr=payload), address_connector=connector)

    assert connector.project_address.call_count == 1


def test_blank_slot_does_not_suppress_its_non_blank_siblings():
    """Per-slot, not all-or-nothing."""
    connector = MagicMock()

    _run_pull(
        _external(CompanyAddr={"Id": "1612"}, LegalAddr=_addr()),
        address_connector=connector,
    )

    projected = [c.kwargs["qbo_id"] for c in connector.project_address.call_args_list]
    assert projected == [f"{REALM}-legal"]


# --------------------------------------------------------------------------
# 3. U-508a's deletion still holds: a miss CREATES, it never adopts.
# --------------------------------------------------------------------------


def test_no_field_match_lookup_reaches_the_projection():
    """U-508a deleted a `read_all()` + (line1, city, postal_code) scan that
    re-keyed whatever row it found -- unscoped by realm and by owner, it would
    have adopted Vendor 1246 (Rogers Build Inc.)'s BILLING address, which shares
    ('PO Box 594', 'Brentwood', '37024') with the company address.

    The new path must not reintroduce it in any form. What the projection
    receives is the identity and the fields, and nothing that could be used to
    go looking for a same-street row.
    """
    connector = MagicMock()

    _run_pull(_external(CompanyAddr=_addr()), address_connector=connector)

    _args, kwargs = connector.project_address.call_args
    assert kwargs["qbo_id"] == f"{REALM}-company", (
        "a qbo_id MISS must CREATE under the synthetic id, never adopt a "
        "field-matched stranger's row"
    )


def test_projection_source_carries_no_field_match_lookup():
    """A behavioural pin can be satisfied by a fallback that is merely gated
    off. This asserts the call sites are absent from the shipped source."""
    import inspect

    from integrations.intuit.qbo.company_info.connector.address.business import (
        service as connector_module,
    )

    # The whole projection path, not just one method -- a helper alongside it
    # would be just as reachable. Docstrings are stripped first: they NAME the
    # deleted calls, which is the record of why they must stay deleted.
    sources = {
        "CompanyInfoAddressConnector": inspect.getsource(connector_module),
        "_project_addresses_from_payload": inspect.getsource(
            QboCompanyInfoService._project_addresses_from_payload
        ),
    }
    for label, source in sources.items():
        body = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for doc in (
            connector_module.CompanyInfoAddressConnector.__doc__,
            CompanyInfoAddressConnector.project_address.__doc__,
            QboCompanyInfoService._project_addresses_from_payload.__doc__,
        ):
            body = body.replace(doc or "\0", "")
        for banned in ("read_all", "read_by_street_one_and_city", "read_by_id"):
            assert banned not in body, (
                f"{banned} must not appear in {label} -- see "
                "_sync_physical_address's docstring for the row it would adopt"
            )


def test_a_projection_failure_holds_the_watermark_and_does_not_abort_siblings():
    """`record_projection_error`'s classifier is unchanged: a transient error
    HOLDS so the next tick retries, and one bad slot does not swallow the
    others."""
    connector = MagicMock()
    connector.project_address.side_effect = [
        RuntimeError("transient db error"),
        MagicMock(id=77),
    ]

    outcome = _run_pull(
        _external(
            CompanyAddr=_addr(),
            LegalAddr=_addr(Line1="1 Legal Way", City="Nashville", PostalCode="37201"),
        ),
        address_connector=connector,
    )

    assert outcome.projection_failed_ids == [f"{REALM}-company"]
    assert outcome.should_hold is True
    assert connector.project_address.call_count == 2, "a failed slot aborted its sibling"


def test_a_permanent_data_error_records_a_skip_not_a_hold():
    """A plain `ValueError` is the connectors' permanent-data convention --
    `_check_no_conflicting_address_identity` raises one. It must stay a SKIP, or
    a genuinely unresolvable address would hold the CompanyInfo watermark
    forever."""
    connector = MagicMock()
    connector.project_address.side_effect = ValueError("identity already held")

    outcome = _run_pull(_external(CompanyAddr=_addr()), address_connector=connector)

    assert outcome.skipped_ids == [f"{REALM}-company"]
    assert outcome.projection_failed_ids == []
    assert outcome.should_hold is False


def test_addresses_are_not_counted_as_projected_records():
    """`projected_count` means "the Company projected". The caller never counted
    addresses into it; inflating it would change what every watermark run
    reports."""
    connector = MagicMock()

    outcome = _run_pull(
        _external(CompanyAddr=_addr(), LegalAddr=_addr(Id="1612")),
        address_connector=connector,
    )

    assert outcome.projected_count == 0


# --------------------------------------------------------------------------
# 4. The realm this family's dbo.Address identity is stamped under.
# --------------------------------------------------------------------------


def test_projection_realm_is_none_because_that_is_what_identity_was_stamped_under():
    """NOT the live realm, and this is the sharpest edge in the unit.

    `_sync_physical_address` has never passed `realm_id` to
    `QboPhysicalAddressRepository.create/update_by_id`, so every
    `qbo.PhysicalAddress` row this family owns carries `RealmId = NULL`;
    `sync_from_qbo_to_address` read realm off that row, so the `dbo.Address`
    rows it stamped carry NULL too. `ReadAddressByQboIdAndRealmId` matches NULL
    only against NULL.

    Hand it the live realm and the identity read MISSES the very row it must
    update, falls to the street/city adopt path, finds that same row, and trips
    `_check_no_conflicting_address_identity` (same QboId, different realm) -- a
    plain ValueError, classified a PERMANENT SKIP. The watermark advances and
    the company address silently stops reaching dbo.

    Flipping this is U-514's, in the same deploy as its realm backfill.
    """
    assert CompanyInfoAddressConnector.ADDRESS_IDENTITY_REALM_ID is None

    address_connector = MagicMock()
    connector = CompanyInfoAddressConnector(address_connector=address_connector)

    connector.project_address(
        MagicMock(line1=LINE1, line2=None, city=CITY,
                  country_sub_division_code="TN", postal_code=POSTAL),
        qbo_id=f"{REALM}-company",
    )

    _args, kwargs = address_connector.sync_address_from_external.call_args
    assert kwargs["realm_id"] is None, (
        "passing the live realm MISSES this family's RealmId=NULL dbo.Address "
        "rows and turns the projection into a permanent skip"
    )
    assert kwargs["qbo_id"] == f"{REALM}-company"


def test_connector_forwards_the_inline_fields_verbatim():
    """The contract's field list, pinned -- a silently dropped `line2` or
    `country_sub_division_code` would blank those columns on every sync."""
    address_connector = MagicMock()
    connector = CompanyInfoAddressConnector(address_connector=address_connector)
    address_ref = MagicMock(
        line1="123 Main St", line2="Suite 4", city="Franklin",
        country_sub_division_code="TN", postal_code="37064",
    )

    connector.project_address(address_ref, qbo_id="1612")

    _args, kwargs = address_connector.sync_address_from_external.call_args
    assert kwargs["line1"] == "123 Main St"
    assert kwargs["line2"] == "Suite 4"
    assert kwargs["city"] == "Franklin"
    assert kwargs["country_sub_division_code"] == "TN"
    assert kwargs["postal_code"] == "37064"


# --------------------------------------------------------------------------
# 5. U-505's missing-id guard is a STAGING FAILURE, still.
# --------------------------------------------------------------------------


def test_missing_qbo_id_is_still_a_staging_failure_and_holds_the_watermark():
    """Skips are excluded from `should_hold`, so downgrading this to a skip
    would advance the delta cursor past a broken row and lose the Company
    permanently. U-513 must not have disturbed the classification."""
    outcome = _run_pull(_external(Id=None, CompanyAddr=_addr()))

    assert outcome.staging_failed_ids == ["<no-id>"]
    assert outcome.skipped_ids == [], "a missing Id must never be a skip"
    assert outcome.should_hold is True
    assert not outcome.synced


def test_missing_qbo_id_projects_no_address():
    """Unchanged ordering: the caller used to project addresses off
    `outcome.synced[0]`, so a malformed response reached no projection. Moving
    the projection into the service must not have made a no-Id response start
    writing dbo rows."""
    connector = MagicMock()

    outcome = _run_pull(
        _external(Id=None, CompanyAddr=_addr()), address_connector=connector
    )

    assert outcome.staging_failed_ids == ["<no-id>"]
    assert connector.project_address.call_count == 0


def test_the_missing_id_guard_still_raises_value_error():
    """The guard itself, at its own level -- so a caller-level refactor cannot
    quietly delete it while the outcome-level assertions above are satisfied by
    something else."""
    svc = QboCompanyInfoService()
    with pytest.raises(ValueError, match="QBO CompanyInfo must have an ID"):
        svc._build_company_info(_external(Id=None), realm_id=REALM)


# --------------------------------------------------------------------------
# 6. The slot table.
# --------------------------------------------------------------------------


def test_slot_table_covers_the_three_payload_fields():
    """`ADDRESS_SLOTS` is the single derivation the projection reads. A slot
    dropped from it silently stops projecting that address, with no other test
    failing.

    Two elements per slot since phase 3a: the third carried the transient-model
    field the staging row id was threaded into, and both the id and the write
    that produced it are gone."""
    assert [attribute for attribute, _ in ADDRESS_SLOTS] == [
        "company_addr", "legal_addr", "customer_communication_addr",
    ]
    assert [suffix for _, suffix in ADDRESS_SLOTS] == [
        "company", "legal", "customer-communication",
    ]
    external = _external()
    for attribute, _ in ADDRESS_SLOTS:
        assert hasattr(external, attribute)
