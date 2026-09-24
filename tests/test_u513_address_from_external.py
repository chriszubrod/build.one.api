"""U-513: project a QBO address onto `dbo.Address` DIRECTLY from the external
payload, with no `qbo.PhysicalAddress` write-then-read-back hop.

`qbo.PhysicalAddress` is a pure staging cache: the QBO pull writes the address
into it, then `PhysicalAddressAddressConnector.sync_from_qbo_to_address` reads
that row straight back and projects it. The content was already inline on the
originating payload the whole time, so the round trip buys nothing — and the
table is being sunset. `sync_address_from_external` is the same projection,
reachable without it.

What this file pins
-------------------
  1. The PUBLIC CONTRACT of `sync_address_from_external` (its exact keyword-only
     signature). Three sibling packages are being repointed onto it in parallel;
     a silent signature drift here breaks all of them at once, which is exactly
     the class of failure a signature assertion is cheap enough to prevent.
  2. The new path's own behaviour: create-on-miss with the caller's SYNTHETIC
     qbo_id, update-on-hit, blank sanitisation, caller-supplied realm with no
     fallback, tombstone refusal (U-370 C1), street/city soft-dedup, and the
     single `raise_concurrent_write_race` on a ROWVERSION miss.
  3. That `sync_from_qbo_to_address` STILL WORKS, unchanged — including its
     missing-row `ValueError`. `customer/connector/project`,
     `vendor/connector/vendor` and `scripts/sync_qbo_company_info.py` all still
     call it during the sunset; this is the regression that would break them.
  4. That both entry points produce the SAME projection for the same content —
     the property that makes (3) safe, since they now share one implementation
     rather than two hand-kept-in-sync copies.

Pure logic: every service/repo is a Mock and the app lock is granted by the
shared `grant_qbo_app_lock` fixture, so no database connection is ever
attempted. Mirrors tests/test_u277_company_address_qbo_identity_repoint.py's
Section 3 shape.
"""
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from integrations.intuit.qbo.physical_address.connector.business.service import (
    PhysicalAddressAddressConnector,
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


# The address content, once, so "same content through both entry points" is a
# real equality rather than two hand-typed literals that happen to agree today.
PAYLOAD = dict(
    line1="123 Main",
    line2="Suite 2",
    city="Austin",
    country_sub_division_code="TX",
    postal_code="78701",
)


def _build_connector():
    """A connector whose every collaborator is a Mock. `read_deleted_by_qbo_identity`
    defaults to the no-tombstone answer; the tombstone test overrides it."""
    address_service = Mock()
    address_service.repo = Mock()
    address_service.read_deleted_by_qbo_identity.return_value = None
    qbo_physical_address_service = Mock()
    qbo_physical_address_service.repo = Mock()
    reconciliation_repo = Mock()
    connector = PhysicalAddressAddressConnector(
        address_service=address_service,
        qbo_physical_address_service=qbo_physical_address_service,
        reconciliation_repo=reconciliation_repo,
    )
    return connector, address_service, qbo_physical_address_service, reconciliation_repo


def _staging_row(**overrides):
    """A `qbo.PhysicalAddress` row shaped as the repo returns it."""
    defaults = dict(id=100, qbo_id="1246_bill", realm_id="realm-1", **PAYLOAD)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _arm_genuine_miss(address_service, *, created_id=300):
    """Wire the mocks for a genuine miss: no identity holder, no street/city
    match, create mints `created_id`, then the stamp re-reads it twice."""
    created = SimpleNamespace(id=created_id, qbo_id=None, realm_id=None)
    stamped = SimpleNamespace(id=created_id, qbo_id="1246_bill", realm_id="realm-1")
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_by_street_one_and_city.return_value = None
    address_service.create.return_value = created
    address_service.repo.update_by_id.side_effect = lambda a: a
    address_service.read_by_id.side_effect = [created, stamped]
    return created, stamped


# --- 1. The contract three parallel consumers code against ---


def test_sync_address_from_external_signature_is_the_u513_contract():
    """The exact public signature, pinned. Three packages are being repointed
    onto this in parallel against this spelling; renaming a parameter, making
    one positional, or dropping `source_ref`'s default breaks all of them at
    once with no local test failure to warn whoever did it."""
    sig = inspect.signature(PhysicalAddressAddressConnector.sync_address_from_external)
    params = list(sig.parameters.values())

    assert params[0].name == "self"
    assert [p.name for p in params[1:]] == [
        "qbo_id",
        "realm_id",
        "line1",
        "line2",
        "city",
        "country_sub_division_code",
        "postal_code",
        "source_ref",
    ]
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[1:]
    ), "every caller-facing parameter must be keyword-only"
    assert [p.name for p in params[1:] if p.default is not inspect.Parameter.empty] == [
        "source_ref"
    ], "source_ref is the ONLY optional parameter — everything else must be supplied"
    assert sig.parameters["source_ref"].default is None


def test_sync_from_qbo_to_address_signature_is_unchanged():
    """`scripts/sync_qbo_company_info.py` calls this by KEYWORD
    (`qbo_physical_address_id=`), so the parameter name is load-bearing, not
    just its position."""
    sig = inspect.signature(PhysicalAddressAddressConnector.sync_from_qbo_to_address)
    assert [p.name for p in sig.parameters.values()] == ["self", "qbo_physical_address_id"]


# --- 2. The new direct-from-payload path ---


def test_external_miss_creates_address_with_the_synthetic_qbo_id_and_realm():
    connector, address_service, qbo_service, _ = _build_connector()
    _, stamped = _arm_genuine_miss(address_service)

    result = connector.sync_address_from_external(
        qbo_id="1246_bill", realm_id="realm-1", source_ref="Customer 1246 BillAddr", **PAYLOAD
    )

    assert result is stamped
    address_service.create.assert_called_once_with(
        street_one="123 Main", street_two="Suite 2", city="Austin", state="TX", zip="78701",
    )
    # The SYNTHETIC identity the caller minted is what lands on dbo.Address --
    # it is not a QBO record id and nothing here tries to derive one.
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="1246_bill", realm_id="realm-1",
    )
    # The whole point of U-513: no staging round trip.
    assert qbo_service.mock_calls == [], "the external path must not touch qbo.PhysicalAddress"


def test_external_hit_updates_the_existing_address_and_sanitizes_blanks():
    """HIT branch: the identity already resolves, so no create and no re-stamp
    — just the field overwrite (QBO is source of truth). `None` must sanitize
    to `""`; street_one/street_two/city/state/zip are all NOT NULL columns.
    Mutation target: dropping an `or ""` writes a NULL the sproc rejects."""
    connector, address_service, qbo_service, _ = _build_connector()
    existing = SimpleNamespace(
        id=55, street_one="Old St", street_two="Old Ste", city="Old City", state="OK", zip="00000",
    )
    address_service.read_by_qbo_identity.return_value = existing
    address_service.repo.update_by_id.side_effect = lambda a: a

    result = connector.sync_address_from_external(
        qbo_id="1246_ship", realm_id="realm-1", line1=None, line2=None, city=None,
        country_sub_division_code=None, postal_code=None,
    )

    assert result is existing
    assert (existing.street_one, existing.street_two, existing.city, existing.state, existing.zip) == (
        "", "", "", "", "",
    )
    address_service.read_by_qbo_identity.assert_called_once_with("1246_ship", "realm-1")
    address_service.repo.update_by_id.assert_called_once_with(existing)
    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    address_service.read_by_street_one_and_city.assert_not_called()
    assert qbo_service.mock_calls == []


def test_external_hit_overwrites_non_blank_fields_too():
    """The blank case above must not be the only thing proving the write
    happened — a no-op apply would also leave `""` on an already-blank row."""
    connector, address_service, _, _ = _build_connector()
    existing = SimpleNamespace(
        id=55, street_one="Curated", street_two="", city="Old City", state="OK", zip="00000",
    )
    address_service.read_by_qbo_identity.return_value = existing
    address_service.repo.update_by_id.side_effect = lambda a: a

    result = connector.sync_address_from_external(qbo_id="1246_bill", realm_id="realm-1", **PAYLOAD)

    assert (result.street_one, result.street_two, result.city, result.state, result.zip) == (
        "123 Main", "Suite 2", "Austin", "TX", "78701",
    )


def test_external_realm_comes_from_the_caller_with_no_fallback():
    """Invariant: realm is the CALLER's to supply. A `None` realm must be
    threaded through verbatim — to the identity read, the tombstone read and
    the stamp — not quietly replaced by a connector-level default. Mutation
    target: any `realm_id or <something>` inside this connector."""
    connector, address_service, _, _ = _build_connector()
    _arm_genuine_miss(address_service)

    connector.sync_address_from_external(qbo_id="1246_bill", realm_id=None, **PAYLOAD)

    assert address_service.read_by_qbo_identity.call_args_list == [
        call("1246_bill", None),
        call("1246_bill", None),  # the re-read under the create lock
    ]
    address_service.read_deleted_by_qbo_identity.assert_called_once_with("1246_bill", None)
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="1246_bill", realm_id=None,
    )


def test_external_apply_returning_none_raises_concurrent_write_race():
    """A ROWVERSION race / concurrent delete on the HIT-branch `update_by_id`
    must raise, never hand back a silent `entity=None`. The raise lives in
    `run_identity_fastpath_dbo_only` alone (U-316) — `_apply_address_fields_
    and_sync` stays silent and returns None. Mutation target: adding a second
    raise here, or swallowing the None."""
    connector, address_service, _, _ = _build_connector()
    address_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=55, street_one="Old", street_two="", city="Old", state="OK", zip="0",
    )
    address_service.repo.update_by_id.return_value = None  # row gone on write

    with pytest.raises(RuntimeError, match="concurrent write race"):
        connector.sync_address_from_external(qbo_id="1246_bill", realm_id="realm-1", **PAYLOAD)

    address_service.repo.set_qbo_identity.assert_not_called()


def test_external_tombstone_holder_refuses_and_records_issue():
    """U-370 C1 must survive the re-plumbing: a soft-deleted Address still
    holding this identity reads as a miss through the live lookup, and minting
    a second live row would let SetAddressQboIdentity's theft-clear strip the
    tombstone's QboId. Refuse + record; never revive, never duplicate."""
    connector, address_service, _, reconciliation_repo = _build_connector()
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_deleted_by_qbo_identity.return_value = SimpleNamespace(
        id=77, public_id="addr-pub-77", qbo_id="1246_bill",
    )

    with pytest.raises(ValueError, match="already held by soft-deleted Address"):
        connector.sync_address_from_external(
            qbo_id="1246_bill", realm_id="realm-1", source_ref="Customer 1246 BillAddr", **PAYLOAD
        )

    address_service.read_by_street_one_and_city.assert_not_called()
    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "deleted_address_holds_identity"
    assert "77" in kwargs["details"]
    assert "Customer 1246 BillAddr" in kwargs["details"]


def test_external_adopts_an_unclaimed_address_by_street_and_city():
    """The street/city soft-dedup safety net must survive: an existing live
    Address with no QBO identity is adopted and stamped rather than duplicated.
    The field write happens in the stamp (under the candidate's own lock), not
    in candidate resolution."""
    connector, address_service, _, _ = _build_connector()
    existing = SimpleNamespace(
        id=150, qbo_id=None, realm_id=None, street_one="Old", street_two="", city="Old City",
        state="OK", zip="00000",
    )
    stamped = SimpleNamespace(id=150, qbo_id="1246_bill", realm_id="realm-1")
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_by_street_one_and_city.return_value = existing
    address_service.repo.update_by_id.side_effect = lambda a: a
    address_service.read_by_id.side_effect = [existing, stamped]

    result = connector.sync_address_from_external(qbo_id="1246_bill", realm_id="realm-1", **PAYLOAD)

    assert result is stamped
    assert (existing.street_one, existing.city) == ("123 Main", "Austin")
    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=150, qbo_id="1246_bill", realm_id="realm-1",
    )


def test_external_street_city_match_carrying_a_different_identity_refuses():
    """The dbo-only equivalent of the old mapping-table duplicate check: a
    street/city-matched row that already carries a DIFFERENT (QboId, RealmId)
    must raise + record, never be silently re-pointed by the theft-clear."""
    connector, address_service, _, reconciliation_repo = _build_connector()
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_by_street_one_and_city.return_value = SimpleNamespace(
        id=150, public_id="addr-pub-150", qbo_id="1246_ship", realm_id="realm-1",
        street_one="Untouched",
    )

    with pytest.raises(ValueError, match="already carries a DIFFERENT identity"):
        connector.sync_address_from_external(
            qbo_id="1246_bill", realm_id="realm-1", source_ref="Customer 1246 BillAddr", **PAYLOAD
        )

    address_service.repo.update_by_id.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()
    kwargs = reconciliation_repo.create.call_args.kwargs
    assert kwargs["drift_type"] == "address_identity_conflict"
    assert kwargs["qbo_id"] == "1246_bill"
    assert "Customer 1246 BillAddr" in kwargs["details"]


def test_external_falsy_qbo_id_raises_rather_than_returning_none():
    """`run_identity_fastpath_dbo_only` reports `hit=False, entity=None` for a
    falsy qbo_id; the connector's backstop turns that into a raise so a caller
    can never receive `None` where an `Address` is declared."""
    connector, address_service, _, _ = _build_connector()

    with pytest.raises(RuntimeError, match="via the dbo-only identity fast path"):
        connector.sync_address_from_external(qbo_id="", realm_id="realm-1", **PAYLOAD)

    address_service.create.assert_not_called()
    address_service.repo.set_qbo_identity.assert_not_called()


@pytest.mark.parametrize("source_ref", [None, "Customer 1246 BillAddr", "Vendor 9 BillAddr"])
def test_source_ref_never_changes_the_projection(source_ref):
    """`source_ref` is log/reconciliation text ONLY. Whatever it says — or
    doesn't — the create and the stamp must be byte-identical. Mutation target:
    anything that branches on, or persists, this value."""
    connector, address_service, _, _ = _build_connector()
    _arm_genuine_miss(address_service)

    connector.sync_address_from_external(
        qbo_id="1246_bill", realm_id="realm-1", source_ref=source_ref, **PAYLOAD
    )

    address_service.create.assert_called_once_with(
        street_one="123 Main", street_two="Suite 2", city="Austin", state="TX", zip="78701",
    )
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="1246_bill", realm_id="realm-1",
    )


# --- 3. The staging entry point still works (the cross-package regression) ---


def test_sync_from_qbo_to_address_still_projects_from_the_staging_row():
    """Three packages still call this during the sunset. It must keep reading
    the staging row and projecting exactly what it always did."""
    connector, address_service, qbo_service, _ = _build_connector()
    _, stamped = _arm_genuine_miss(address_service)
    qbo_service.repo.read_by_id.return_value = _staging_row()

    result = connector.sync_from_qbo_to_address(100)

    assert result is stamped
    qbo_service.repo.read_by_id.assert_called_once_with(100)
    address_service.create.assert_called_once_with(
        street_one="123 Main", street_two="Suite 2", city="Austin", state="TX", zip="78701",
    )
    address_service.repo.set_qbo_identity.assert_called_once_with(
        id=300, qbo_id="1246_bill", realm_id="realm-1",
    )


def test_sync_from_qbo_to_address_still_raises_on_a_missing_staging_row():
    """The exact pre-U-513 message and exception type. `ProjectConnector`'s own
    blank-address guard and its tests are written against this wording."""
    connector, address_service, qbo_service, _ = _build_connector()
    qbo_service.repo.read_by_id.return_value = None

    with pytest.raises(ValueError, match=r"^QboPhysicalAddress with ID 404 not found$"):
        connector.sync_from_qbo_to_address(404)

    address_service.read_by_qbo_identity.assert_not_called()
    address_service.create.assert_not_called()


def test_staging_path_keeps_its_historical_source_wording_in_recorded_issues():
    """The staging wrapper passes the full `"QboPhysicalAddress {id}"`
    descriptor, so every log line and ReconciliationIssue this family already
    emits stays byte-identical through the re-plumbing — an operator's saved
    searches and the U-333 recorder-consolidation fixtures both depend on it."""
    connector, address_service, qbo_service, reconciliation_repo = _build_connector()
    address_service.read_by_qbo_identity.return_value = None
    address_service.read_deleted_by_qbo_identity.return_value = SimpleNamespace(
        id=77, public_id="addr-pub-77", qbo_id="1246_bill",
    )
    qbo_service.repo.read_by_id.return_value = _staging_row(id=100)

    with pytest.raises(ValueError, match="already held by soft-deleted Address"):
        connector.sync_from_qbo_to_address(100)

    assert reconciliation_repo.create.call_args.kwargs["details"].startswith(
        "QboPhysicalAddress 100 (QboId=1246_bill, RealmId=realm-1)"
    )


# --- 4. The two entry points are one implementation ---


def _record_projection(drive, **staging_overrides):
    """Drive one entry point against a fresh connector and return everything it
    asked its collaborators to do, plus what it returned."""
    connector, address_service, qbo_service, _ = _build_connector()
    _, stamped = _arm_genuine_miss(address_service)
    qbo_service.repo.read_by_id.return_value = _staging_row(**staging_overrides)

    result = drive(connector)

    return {
        "result": result,
        "stamped": stamped,
        "identity_reads": address_service.read_by_qbo_identity.call_args_list,
        "tombstone_reads": address_service.read_deleted_by_qbo_identity.call_args_list,
        "dedup_reads": address_service.read_by_street_one_and_city.call_args_list,
        "creates": address_service.create.call_args_list,
        "stamps": address_service.repo.set_qbo_identity.call_args_list,
    }


def test_both_entry_points_produce_the_same_projection_for_the_same_content():
    """The property that makes the wrapper safe: `sync_from_qbo_to_address` is
    now nothing but a staging read in front of `sync_address_from_external`, so
    identical content must drive identical work. Mutation target: any behaviour
    that survives on only one of the two paths — i.e. a re-introduced second
    copy of the tombstone refusal, the dedup, or the field sanitisation."""
    via_staging = _record_projection(lambda c: c.sync_from_qbo_to_address(100))
    via_external = _record_projection(
        lambda c: c.sync_address_from_external(
            qbo_id="1246_bill", realm_id="realm-1", source_ref="QboPhysicalAddress 100", **PAYLOAD
        )
    )

    for key in ("identity_reads", "tombstone_reads", "dedup_reads", "creates", "stamps"):
        assert via_staging[key] == via_external[key], f"{key} diverged between the two entry points"
    assert via_staging["result"] is via_staging["stamped"]
    assert via_external["result"] is via_external["stamped"]


def test_both_entry_points_sanitize_blanks_identically():
    """Same equality, on the branch where a divergence would actually corrupt
    data: a staging row of all-`None` fields and an all-`None` payload must
    both reach `create` as `""`."""
    blank = dict(line1=None, line2=None, city=None, country_sub_division_code=None, postal_code=None)
    via_staging = _record_projection(lambda c: c.sync_from_qbo_to_address(100), **blank)
    via_external = _record_projection(
        lambda c: c.sync_address_from_external(qbo_id="1246_bill", realm_id="realm-1", **blank)
    )

    assert via_staging["creates"] == via_external["creates"]
    assert via_staging["creates"] == [call(street_one="", street_two="", city="", state="", zip="")]
    # The blank row also skips the street/city dedup lookup on BOTH paths --
    # `read_by_street_one_and_city` is only reached when both are non-empty.
    assert via_staging["dedup_reads"] == via_external["dedup_reads"] == []
