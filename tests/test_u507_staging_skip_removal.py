"""U-507: the QBO staging tier has NO skip — every fetched record syncs or holds.

Evidence this unit acted on: of the 11 watermarked QBO pull families, TEN held
on a missing QBO Id and exactly ONE — reimburse_charge — skipped and advanced
its watermark past the record. Its rationale ("can never persist or be retried,
so it does NOT hold the watermark (would stall forever)") was written
2026-08-01; the bounded hold it denies the existence of
(QBO_WATERMARK_HOLD_BOUND_SECONDS, default 7200s) landed 2026-08-12. The
comment described a codebase that no longer exists.

What these tests pin:
  * reimburse_charge records a STAGING FAILURE (holds) on a no-Id row, keeps the
    good rows of the same page, and never hands a falsy qbo_id to the repo.
  * that outcome does not advance the watermark at WatermarkRun.commit.
  * the record_staging_skip verb and its FAILURE_REASON_STAGING_SKIP namespace
    are gone from the shared vocabulary, so there is no staging skip to reach for.
  * TALLY GUARD: all 11 families hold, by one of three mechanisms. This turns the
    11:0 tally into an executable fact so family #12 cannot quietly reintroduce a
    staging skip.

FIX ROUND 1 — arm A of that tally guard was VACUOUS as shipped. It trusted
`model_fields['id'].is_required()` for the six schema-only families (bill,
purchase, invoice, vendorcredit, account, term), which is not the invariant:
pydantic's required rule rejects an ABSENT or NULL `Id` but ACCEPTS `""`, and
`str_strip_whitespace` turned `" "` into `""` on five of the six. A QBO row with
`"Id": ""` therefore validated, staged with QBO id `""`, recorded SUCCESS and
ADVANCED the watermark — inside the very arm that declared those six safe. The
six now bind `integrations.intuit.qbo.base.id_validation.require_non_blank_qbo_id`,
and arm A proves rejection BY CONSTRUCTION (section 4b) instead of by
`is_required()`.

NEGATIVE CONTROL at the bottom: broadening `should_hold` to count skipped_ids
must leave every assertion above green. If broadening it flipped anything, these
tests would be pinning "should_hold happens to be True" rather than the actual
mechanism (staging_failed_ids non-empty, skipped_ids empty).
"""
from __future__ import annotations

import re

import ast
import importlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from integrations.intuit.qbo.base import sync_outcome as sync_outcome_module
from integrations.intuit.qbo.base.id_validation import BLANK_QBO_ID_ERROR
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.base.watermark import WatermarkRun
from integrations.intuit.qbo.base.watermark import _QBO_SYNC_ENTITY_META
from integrations.intuit.qbo.reimburse_charge.business.service import (
    QboReimburseChargeService,
)
from integrations.intuit.qbo.term.business.service import QboTermService
from integrations.intuit.qbo.term.external.client import QboTermClient
from tests.test_qbo_watermark_runner import (
    FIXED_QUERY_START,
    FakeSyncService,
    _make_sync,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
QBO_ROOT = REPO_ROOT / "integrations" / "intuit" / "qbo"
REALM_ID = "realm-test"
ENTITY = "reimburse_charge"

_NO_ID_RAW = {
    "CustomerRef": {"value": "77", "name": "Haverford"},
    "TxnDate": "2026-09-01",
    "Amount": "100.00",
    "HasBeenInvoiced": False,
}
_SECOND_NO_ID_RAW = dict(_NO_ID_RAW, Amount="250.00")
_GOOD_RAW = dict(_NO_ID_RAW, Id="900", Amount="1577.45")


def _rc_client_cm(raw_records):
    """MagicMock usable as `with QboInvoiceClient(...) as client:`."""
    client = MagicMock()
    client.query_all_reimburse_charges.return_value = raw_records
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False
    return cm


def _run_sync(raw_records):
    """Drive the real sync_from_qbo loop over `raw_records`; return (repo, outcome)."""
    repo = MagicMock()
    repo.read_by_qbo_id_and_realm_id.return_value = None
    repo.create.side_effect = lambda **kwargs: SimpleNamespace(
        id=101, qbo_id=kwargs["qbo_id"], amount=Decimal("1577.45")
    )
    service = QboReimburseChargeService(repo=repo)
    with patch(
        "integrations.intuit.qbo.reimburse_charge.business.service.QboInvoiceClient",
        return_value=_rc_client_cm(raw_records),
    ):
        outcome = service.sync_from_qbo(realm_id=REALM_ID)
    return repo, outcome


def _opened_rc_run(fake: FakeSyncService) -> WatermarkRun:
    run = WatermarkRun(fake, "qbo", "prod", ENTITY)
    run.open()
    run.query_start = FIXED_QUERY_START
    return run


# --------------------------------------------------------------------------- #
# 1. The staging loop fails-and-holds instead of skipping
# --------------------------------------------------------------------------- #


def test_no_id_row_records_a_staging_failure_and_holds_while_good_rows_still_sync():
    """A malformed RC must HOLD the window, not silently advance past it (U-507)."""
    _, outcome = _run_sync([_NO_ID_RAW, _GOOD_RAW])

    assert outcome.fetched == 2
    assert outcome.should_hold is True
    assert outcome.staging_failed_ids == ["<no-id>:0"]
    assert outcome.skipped_ids == []
    # The malformed row must not poison its page: the good RC still stages.
    assert outcome.synced_count == 1
    assert outcome.failure_reasons["staging:<no-id>:0"] == "ReimburseCharge with no Id"


def test_no_id_sentinel_is_per_row_so_n_malformed_rows_are_n_distinct_ids():
    """A bare '<no-id>' would emit N identical ReconciliationIssues at the hold bound.

    Scope is PER RESPONSE, not durable: the QBO query has no ORDER BY, so `i` can
    land differently on the next tick. Distinguishing rows within ONE run is the
    only job these sentinels have.
    """
    _, outcome = _run_sync([_NO_ID_RAW, _SECOND_NO_ID_RAW, _GOOD_RAW])

    assert outcome.staging_failed_ids == ["<no-id>:0", "<no-id>:1"]
    assert len(set(outcome.staging_failed_ids)) == 2
    assert outcome.synced_count == 1


def test_repo_is_never_called_with_a_falsy_qbo_id():
    """UQ_QboReimburseCharge_QboId_RealmId is FILTERED (WHERE QboId IS NOT NULL...).

    A NULL-QboId row therefore inserts cleanly and duplicates on EVERY pull, so the
    `continue` after the staging failure is load-bearing — recording the failure is
    not enough on its own.
    """
    repo, _ = _run_sync([_NO_ID_RAW, _GOOD_RAW])

    calls = (
        repo.read_by_qbo_id_and_realm_id.call_args_list
        + repo.create.call_args_list
        + repo.update_by_qbo_id.call_args_list
    )
    assert calls, "expected the good RC to reach the repo"
    for call in calls:
        qbo_id = call.kwargs.get("qbo_id", call.args[0] if call.args else None)
        assert qbo_id, f"repo reached with a falsy qbo_id: {call}"
    assert repo.create.call_count == 1


# --------------------------------------------------------------------------- #
# 2. The watermark actually holds on that outcome
# --------------------------------------------------------------------------- #


def test_watermark_commit_does_not_advance_on_a_no_id_outcome():
    """The whole point: a no-Id RC must leave LastSyncDatetime where it was."""
    # Strictly BEFORE FIXED_QUERY_START: otherwise _write's "never move backward" guard
    # would refuse an advance anyway and a reverted verb would masquerade as a hold.
    stored = "2026-01-01T00:00:00"
    fake = FakeSyncService([_make_sync(entity=ENTITY, last_sync_datetime=stored)])
    run = _opened_rc_run(fake)
    _, outcome = _run_sync([_NO_ID_RAW, _GOOD_RAW])

    run.commit(outcome)

    assert fake.rows[0].last_sync_datetime == stored
    assert all(update.last_sync_datetime == stored for _, update in fake.updates), (
        "commit must not write a new LastSyncDatetime while holding"
    )
    # The hold streak is anchored, which is what arms the bounded force-advance.
    assert fake.rows[0].hold_started_datetime is not None


def test_bounded_force_advance_still_releases_a_stuck_no_id_hold():
    """A hold is bounded — 'it would stall forever' is not a reason to skip."""
    # Strictly BEFORE FIXED_QUERY_START: otherwise _write's "never move backward" guard
    # would refuse an advance anyway and a reverted verb would masquerade as a hold.
    stored = "2026-01-01T00:00:00"
    long_ago = (FIXED_QUERY_START - timedelta(days=68)).strftime("%Y-%m-%dT%H:%M:%S")
    fake = FakeSyncService(
        [_make_sync(entity=ENTITY, last_sync_datetime=stored, hold_started_datetime=long_ago)]
    )
    run = _opened_rc_run(fake)
    _, outcome = _run_sync([_NO_ID_RAW, _GOOD_RAW])

    with patch.object(WatermarkRun, "_record_bound_forced_advance") as recorder:
        run.commit(outcome)

    recorder.assert_called_once()
    assert fake.rows[0].last_sync_datetime == run.watermark_value


# --------------------------------------------------------------------------- #
# 3. The verb is gone from the shared vocabulary
# --------------------------------------------------------------------------- #


def test_record_staging_skip_and_its_reason_constant_are_gone():
    """There must be no staging skip verb left to reach for."""
    assert not hasattr(SyncOutcome, "record_staging_skip")
    assert not hasattr(sync_outcome_module, "FAILURE_REASON_STAGING_SKIP")
    # ...and the verbs that replace it still exist, so this cannot pass on a typo.
    assert hasattr(SyncOutcome, "record_staging_failure")
    assert hasattr(SyncOutcome, "record_projection_error")
    assert sync_outcome_module.FAILURE_REASON_SKIP == "skip"


def test_sync_outcome_module_docstring_states_the_no_staging_skip_policy():
    """The policy lives in exactly one place; a reader must find it there."""
    doc = (sync_outcome_module.__doc__ or "").lower()
    assert "staging" in doc and "no staging skip" in doc
    assert "record_projection_error" in doc


# --------------------------------------------------------------------------- #
# 4. TALLY GUARD — all 11 watermarked pull families hold on a missing QBO Id
# --------------------------------------------------------------------------- #

# The external READ schema each pull parses QBO rows into. `None` means the family
# has no pydantic read schema (reimburse_charge parses raw dicts), so it must carry
# one of the in-service mechanisms instead.
_EXTERNAL_READ_SCHEMA_BY_ENTITY: dict[str, Optional[str]] = {
    "bill": "integrations.intuit.qbo.bill.external.schemas.QboBill",
    "purchase": "integrations.intuit.qbo.purchase.external.schemas.QboPurchase",
    "invoice": "integrations.intuit.qbo.invoice.external.schemas.QboInvoice",
    "vendorcredit": "integrations.intuit.qbo.vendorcredit.external.schemas.QboVendorCredit",
    "vendor": "integrations.intuit.qbo.vendor.external.schemas.QboVendor",
    "customer": "integrations.intuit.qbo.customer.external.schemas.QboCustomer",
    "item": "integrations.intuit.qbo.item.external.schemas.QboItem",
    "account": "integrations.intuit.qbo.account.external.schemas.QboAccount",
    "term": "integrations.intuit.qbo.term.external.schemas.QboTerm",
    "company_info": "integrations.intuit.qbo.company_info.external.schemas.QboCompanyInfo",
    "reimburse_charge": None,
}


def test_tally_registry_covers_exactly_the_watermarked_pull_families():
    """Family #12 must be classified here, not silently exempted."""
    assert set(_EXTERNAL_READ_SCHEMA_BY_ENTITY) == set(_QBO_SYNC_ENTITY_META), (
        "a QBO pull family was added to _QBO_SYNC_ENTITY_META without declaring how it "
        "holds on a missing QBO Id: "
        + ", ".join(
            sorted(set(_EXTERNAL_READ_SCHEMA_BY_ENTITY) ^ set(_QBO_SYNC_ENTITY_META))
        )
    )


# The six families whose only mechanism is arm A (schema-level rejection).
_SCHEMA_REJECTS_FALSY_ID_FAMILIES = frozenset(
    {"bill", "purchase", "invoice", "vendorcredit", "account", "term"}
)

# Every one of those six declares EXACTLY {Id, SyncToken} required, so one payload
# serves all six. `test_arm_a_registry_is_derived_from_the_schemas_themselves` fails
# loudly if that ever stops being true rather than letting the probe below go
# vacuous on a payload that no longer validates.
_MINIMAL_VALID_READ_PAYLOAD = {"Id": "900", "SyncToken": "0"}

# Every shape of "the QBO Id is effectively missing" that `id: str` REQUIRED does
# NOT reject on its own. `" "` matters twice over: five of the six inherit
# `str_strip_whitespace=True`, which silently strips it to `""`, while vendorcredit
# does NOT strip and kept it VERBATIM as a TRUTHY garbage id.
_FALSY_IDS = ("", " ", "\t", "\n  ", "    ")


def _load_schema(dotted: str):
    module_name, _, class_name = dotted.rpartition(".")
    return getattr(importlib.import_module(module_name), class_name)


def _required_aliases(schema) -> set:
    return {
        (field.alias or name)
        for name, field in schema.model_fields.items()
        if field.is_required()
    }


def _schema_rejects_a_falsy_id(dotted: Optional[str]) -> bool:
    """Mechanism A: pydantic aborts the whole page before anything can stage.

    This USED to assert `model_fields['id'].is_required()` and stop there. That is
    not the invariant and the guard passed VACUOUSLY for all six families: pydantic's
    required rule rejects an ABSENT or NULL `Id`, but it ACCEPTS `""` (and, under
    `str_strip_whitespace`, `" "` stripped down to `""`). A QBO row with `"Id": ""`
    validated, staged with QBO id `""`, recorded SUCCESS and ADVANCED the watermark —
    the exact silent loss U-507 exists to close, alive inside the arm that declared
    these six safe.

    So the arm now CONSTRUCTS the schema against an otherwise-valid payload and only
    holds if every falsy Id actually raises. `required` is kept as a precondition
    (it is what rejects absent/NULL) but is no longer sufficient on its own.
    """
    if not dotted:
        return False
    schema = _load_schema(dotted)
    field = schema.model_fields.get("id")
    if field is None or not field.is_required():
        return False
    if _required_aliases(schema) - set(_MINIMAL_VALID_READ_PAYLOAD):
        # A new required field appeared, so the payload below would fail for an
        # unrelated reason and every rejection it "proves" would be vacuous.
        return False
    payload = dict(_MINIMAL_VALID_READ_PAYLOAD)
    try:
        schema(**payload)
    except ValidationError:
        return False  # base payload invalid => the probes below prove nothing
    for falsy in _FALSY_IDS:
        try:
            schema(**{**payload, "Id": falsy})
        except ValidationError:
            continue
        return False
    return True


def _service_tree(entity: str) -> ast.AST:
    return ast.parse(
        (QBO_ROOT / entity / "business" / "service.py").read_text(encoding="utf-8")
    )


def _service_raises_missing_id_value_error(tree: ast.AST) -> bool:
    """Mechanism B: an explicit guard raising ValueError('... must have an ID')."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "ValueError":
            continue
        for arg in node.exc.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if "must have an id" in arg.value.lower():
                    return True
    return False


def _service_fails_and_continues_on_falsy_guard(tree: ast.AST) -> bool:
    """Mechanism C: `if not <id>:` → record_staging_failure(...) + `continue`.

    Both halves are required. A bare `continue` (purchase has one, for zero-id
    LINES) is a skip, not a hold; a bare record_staging_failure without the
    `continue` would fall through to the upsert and duplicate the row.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if not (isinstance(node.test, ast.UnaryOp) and isinstance(node.test.op, ast.Not)):
            continue
        records_failure = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "record_staging_failure"
            for child in ast.walk(node)
        )
        continues = any(isinstance(child, ast.Continue) for child in ast.walk(node))
        if records_failure and continues:
            return True
    return False


@pytest.mark.parametrize("entity", sorted(_QBO_SYNC_ENTITY_META))
def test_every_pull_family_holds_on_a_missing_qbo_id(entity):
    """11:0 as a test. No watermarked QBO pull may skip-and-advance past a no-Id row.

    Exactly three sanctioned mechanisms, all of which HOLD:
      A. the external read schema REJECTS a falsy `Id` — pydantic raises
         ValidationError and aborts the page, so commit is never reached at all.
         Proven by construction (`""`, `" "`, …) against an otherwise-valid
         payload, NOT by `is_required()`, which accepts `""`;
      B. the staging upsert raises ValueError('... must have an ID'), recorded by
         the caller as a staging failure;
      C. the staging loop records a staging failure and `continue`s past the row.
    """
    tree = _service_tree(entity)
    schema_rejects_falsy = _schema_rejects_a_falsy_id(_EXTERNAL_READ_SCHEMA_BY_ENTITY[entity])
    raises_guard = _service_raises_missing_id_value_error(tree)
    fails_and_continues = _service_fails_and_continues_on_falsy_guard(tree)

    assert schema_rejects_falsy or raises_guard or fails_and_continues, (
        f"QBO pull family '{entity}' has no mechanism that holds the watermark on a "
        "record with no QBO Id: its external read schema still ACCEPTS a falsy `Id` "
        "(declaring it required is NOT enough — pydantic's required rule rejects an "
        "absent/NULL Id but accepts \"\"; bind "
        "`integrations.intuit.qbo.base.id_validation.require_non_blank_qbo_id`), its "
        "service raises no \"must have an ID\" ValueError, and its staging loop does "
        "not record_staging_failure + continue. A staging skip is not an option "
        "(U-507): the hold is bounded by QBO_WATERMARK_HOLD_BOUND_SECONDS and "
        "force-advances with a critical ReconciliationIssue, so 'it would stall "
        "forever' is not a valid reason to advance past a record."
    )


# --------------------------------------------------------------------------- #
# 4b. ARM A AT RUNTIME — the six schema-required families REJECT a falsy Id
# --------------------------------------------------------------------------- #


def test_arm_a_registry_is_derived_from_the_schemas_themselves():
    """A 7th schema-required family must be probed, not silently unprobed.

    `_schema_rejects_a_falsy_id` returns False for any family the minimal payload
    cannot cover, which would quietly push that family onto arm B/C. This asserts
    the registry and the payload still match the schemas, so such a family fails
    HERE with a readable message instead of drifting.
    """
    declares_required = {
        entity
        for entity, dotted in _EXTERNAL_READ_SCHEMA_BY_ENTITY.items()
        if dotted
        and (_load_schema(dotted).model_fields.get("id") is not None)
        and _load_schema(dotted).model_fields["id"].is_required()
    }
    assert declares_required == set(_SCHEMA_REJECTS_FALSY_ID_FAMILIES), (
        "a QBO pull family changed which mechanism holds its watermark: "
        + ", ".join(sorted(declares_required ^ set(_SCHEMA_REJECTS_FALSY_ID_FAMILIES)))
    )
    for entity in sorted(_SCHEMA_REJECTS_FALSY_ID_FAMILIES):
        schema = _load_schema(_EXTERNAL_READ_SCHEMA_BY_ENTITY[entity])
        missing = _required_aliases(schema) - set(_MINIMAL_VALID_READ_PAYLOAD)
        assert not missing, (
            f"{entity}'s read schema gained required field(s) {sorted(missing)} — extend "
            "_MINIMAL_VALID_READ_PAYLOAD, or the falsy-Id probe validates nothing"
        )


@pytest.mark.parametrize("entity", sorted(_SCHEMA_REJECTS_FALSY_ID_FAMILIES))
def test_schema_required_family_rejects_a_blank_or_whitespace_qbo_id(entity):
    """`id: str` required is NOT "no falsy Id can get through" (U-507 fix round 1).

    Required rejects an ABSENT or NULL `Id`. It ACCEPTS `""`. And `" "` is not
    saved by a truthiness test either: five of the six strip it to `""` via
    `str_strip_whitespace`, and vendorcredit — which does not strip — kept it as a
    TRUTHY `" "` that every downstream `if not qbo_id:` guard waves through.
    """
    schema = _load_schema(_EXTERNAL_READ_SCHEMA_BY_ENTITY[entity])
    payload = dict(_MINIMAL_VALID_READ_PAYLOAD)

    # NEGATIVE CONTROL, first: the payload must differ from the probes ONLY in Id,
    # or every rejection below could be some unrelated field failing.
    assert schema(**payload).id == "900"

    for falsy in _FALSY_IDS:
        with pytest.raises(ValidationError) as excinfo:
            schema(**{**payload, "Id": falsy})
        assert BLANK_QBO_ID_ERROR in str(excinfo.value), (
            f"{entity} rejected Id={falsy!r}, but for some OTHER reason than the "
            f"non-blank guard: {excinfo.value}"
        )


# Padded ids that must all canonicalise to "900". NBSP (U+00A0) and the
# ideographic space (U+3000) are included because `str.isspace()` is True for
# both, so Python's `.strip()` removes them -- these are the shapes the deploy
# preflight (scripts/migrations/u507_preflight_padded_qbo_ids.py) scans for.
_PADDED_IDS = (" 900", "900 ", "  900  ", "\u00a0900", "900\u3000")


def test_vendorcredit_is_still_the_family_that_makes_normalization_observable():
    """ANTI-VACUITY precondition for the test below -- read this first if it fails.

    Five of the six families set `str_strip_whitespace=True`, so pydantic strips a
    padded Id for them whether or not our validator normalizes. VendorCredit does
    NOT strip, which makes it the ONLY family where "returns normalized" is
    distinguishable from "returns the raw value".

    If someone adds `str_strip_whitespace` to VendorCredit, the next test stops
    discriminating and silently becomes vacuous -- so this pins the precondition
    rather than letting that happen quietly.
    """
    vendorcredit = _load_schema(_EXTERNAL_READ_SCHEMA_BY_ENTITY["vendorcredit"])
    assert vendorcredit.model_config.get("str_strip_whitespace", False) is False, (
        "VendorCredit now strips whitespace, so test_schema_returns_the_normalized_id "
        "no longer proves anything. Point that test at a non-stripping family, or "
        "assert the validator's return value directly."
    )
    # ...and at least one sibling DOES strip, or the contrast being relied on is imaginary.
    a_stripping_family = _load_schema(_EXTERNAL_READ_SCHEMA_BY_ENTITY["bill"])
    assert a_stripping_family.model_config.get("str_strip_whitespace", False) is True


@pytest.mark.parametrize("entity", sorted(_SCHEMA_REJECTS_FALSY_ID_FAMILIES))
@pytest.mark.parametrize("padded", _PADDED_IDS)
def test_schema_returns_the_normalized_id_not_the_raw_one(entity, padded):
    """The validator must RETURN the canonical id, not merely accept a padded one.

    MUTATION GAP THIS CLOSES (found 2026-09-23): changing
    `require_non_blank_qbo_id`'s final `return normalized` to `return value` left
    the entire suite GREEN. Nothing asserted the normalization, even though
    `id_validation.py`'s own docstring calls it load-bearing and the deploy
    preflight exists precisely because a ' 900' row read back as '900' misses the
    exact (QboId, RealmId) lookup and mints a SECOND staging row plus a second
    downstream identity -- which deletion reconciliation will not clean up.

    VendorCredit is the family that proves it; see the anti-vacuity test above.
    """
    schema = _load_schema(_EXTERNAL_READ_SCHEMA_BY_ENTITY[entity])
    payload = dict(_MINIMAL_VALID_READ_PAYLOAD)

    # NEGATIVE CONTROL: the unpadded payload must already give the canonical id,
    # or a pass below could be measuring something other than normalization.
    assert schema(**payload).id == "900"

    parsed = schema(**{**payload, "Id": padded})
    assert parsed.id == "900", (
        f"{entity} kept a non-canonical id {parsed.id!r} from input {padded!r}. "
        f"The validator must return normalize_qbo_id(value), not the raw value."
    )


# Whitespace shapes that are TRUTHY in Python but are not identities. NBSP and the
# ideographic space are included because `str.isspace()` covers them, so `.strip()`
# removes them -- which is exactly why a bare `str()` leaves them behind.
_TRUTHY_BLANKS = (" ", "\t", "\r\n", "\u00a0", "\u3000")


@pytest.mark.parametrize("blank", _TRUTHY_BLANKS)
def test_reimburse_charge_holds_on_a_whitespace_id_not_just_an_empty_one(blank):
    """U-507 round 2 -- a REAL gap this file's other arm could not see.

    `reimburse_charge` is WATERMARKED, and its guard is `if not
    parsed.get("qbo_id")`. `parse_reimburse_charge` used a bare `_as_str`
    (`str(value)`), so a whitespace-only Id stayed TRUTHY, passed the guard,
    staged a garbage identity, recorded SUCCESS and ADVANCED the watermark past
    itself -- the precise silent-loss shape this unit exists to close.

    Round 1 closed only the EMPTY-string case and this file's reimburse_charge
    arm is AST-based (it asserts a falsy guard + `continue` exists), so it could
    never have caught it. This is a RUNTIME probe on purpose.
    """
    from integrations.intuit.qbo.reimburse_charge.business.parse import (
        parse_reimburse_charge,
    )

    # Negative control: a real id must survive, or "None" below proves nothing.
    assert parse_reimburse_charge({"Id": "900", "Amount": "1.00"})["qbo_id"] == "900"

    parsed = parse_reimburse_charge({"Id": blank, "Amount": "1.00"})
    assert parsed["qbo_id"] is None, (
        f"Id={blank!r} parsed to {parsed['qbo_id']!r}, which is TRUTHY, so "
        f"`if not parsed.get('qbo_id')` will not fire and the row will stage "
        f"and advance the watermark."
    )


def test_reimburse_charge_canonicalises_a_padded_id():
    """A padded-but-real id must normalize, not stage two identities."""
    from integrations.intuit.qbo.reimburse_charge.business.parse import (
        parse_reimburse_charge,
    )

    assert parse_reimburse_charge({"Id": " 900 ", "Amount": "1.00"})["qbo_id"] == "900"


@pytest.mark.parametrize("blank", _TRUTHY_BLANKS)
def test_raise_and_hold_families_reject_whitespace_at_runtime(blank):
    """The five `if not x.id` families depend on stripping to make a blank falsy.

    attachable's local base declared only `populate_by_name`, so it did NOT
    strip and `Id=" "` reached the upsert as a truthy identity -- stamping
    `dbo.Attachment.QboId = " "`. Asserting the CONFIG would be weaker than this:
    what matters is the observed value, whichever mechanism produces it.
    """
    from integrations.intuit.qbo.attachable.external.schemas import QboAttachable
    from integrations.intuit.qbo.customer.external.schemas import QboCustomer
    from integrations.intuit.qbo.item.external.schemas import QboItem
    from integrations.intuit.qbo.vendor.external.schemas import QboVendor

    for name, model in (
        ("attachable", QboAttachable),
        ("customer", QboCustomer),
        ("item", QboItem),
        ("vendor", QboVendor),
    ):
        # Negative control first.
        assert model(Id="900").id == "900", f"{name} mangled a real id"

        parsed = model(Id=blank).id
        assert not parsed, (
            f"{name} kept Id={blank!r} as {parsed!r} -- TRUTHY, so its "
            f"`if not x.id` guard will not fire and a blank identity reaches the DB."
        )


def test_a_blank_id_row_aborts_the_whole_page_at_the_real_client_parse_site():
    """Arm A's actual claim: the PAGE dies, so nothing stages and commit is unreachable.

    Driven through the real `QboTermClient.query_terms`, whose parse is
    `[QboTerm(**term) for term in terms_data]` — a list comprehension, so one bad
    row destroys the whole page including the good rows next to it. That is the
    hold: `sync_from_qbo` never returns, `WatermarkRun.commit` is never called, no
    hold marker is stamped and the bounded force-advance cannot engage.
    """
    http_client = MagicMock()
    http_client.get.return_value = {
        "QueryResponse": {
            "Term": [
                {"Id": "800", "SyncToken": "0", "Name": "Net 30"},
                {"Id": "", "SyncToken": "0", "Name": "Malformed"},
            ]
        }
    }

    with pytest.raises(ValidationError, match=BLANK_QBO_ID_ERROR):
        QboTermClient(realm_id=REALM_ID, http_client=http_client).query_terms()


def test_negative_control_a_valid_id_still_parses_and_still_stages():
    """If the guard broke the happy path it would be over-tightened, not fixed."""
    http_client = MagicMock()
    http_client.get.return_value = {
        "QueryResponse": {"Term": [{"Id": "800", "SyncToken": "0", "Name": "Net 30"}]}
    }
    terms = QboTermClient(realm_id=REALM_ID, http_client=http_client).query_terms()
    assert [term.id for term in terms] == ["800"]

    repo = MagicMock()
    repo.read_by_qbo_id_and_realm_id.return_value = None
    staged = SimpleNamespace(id=1, qbo_id="800")
    repo.create.return_value = staged
    client = MagicMock()
    client.query_all_terms.return_value = terms
    cm = MagicMock()
    cm.__enter__.return_value = client
    cm.__exit__.return_value = False

    with patch(
        "integrations.intuit.qbo.term.business.service.QboTermClient", return_value=cm
    ):
        outcome = QboTermService(repo=repo).sync_from_qbo(
            realm_id=REALM_ID, sync_to_modules=False
        )

    assert outcome.synced == [staged]
    assert outcome.staging_failed_ids == []
    assert outcome.should_hold is False
    assert repo.create.call_args.kwargs["qbo_id"] == "800"


# --------------------------------------------------------------------------- #
# 5. NEGATIVE CONTROL
# --------------------------------------------------------------------------- #


def test_negative_control_broadening_should_hold_to_count_skips_changes_nothing(monkeypatch):
    """These tests must pin the MECHANISM, not the shape of `should_hold`.

    If redefining `should_hold` to also count skipped_ids changed any assertion
    above, that assertion would be riding on `should_hold` being True rather than
    on staging_failed_ids being populated and skipped_ids being empty — and a
    reverted verb (record_staging_skip) would then sail through.
    """
    monkeypatch.setattr(
        SyncOutcome,
        "should_hold",
        property(
            lambda self: bool(
                self.staging_failed_ids or self.projection_failed_ids or self.skipped_ids
            )
        ),
    )

    _, outcome = _run_sync([_NO_ID_RAW, _GOOD_RAW])
    assert outcome.should_hold is True
    assert outcome.staging_failed_ids == ["<no-id>:0"]
    assert outcome.skipped_ids == []
    assert outcome.synced_count == 1

    # Strictly BEFORE FIXED_QUERY_START: otherwise _write's "never move backward" guard
    # would refuse an advance anyway and a reverted verb would masquerade as a hold.
    stored = "2026-01-01T00:00:00"
    fake = FakeSyncService([_make_sync(entity=ENTITY, last_sync_datetime=stored)])
    run = _opened_rc_run(fake)
    run.commit(outcome)
    assert fake.rows[0].last_sync_datetime == stored


# --- U-507 fix round: the normalization preflight ------------------------- #


def test_padded_id_preflight_uses_the_same_normalizer_not_a_translation():
    """The first version of this preflight was T-SQL (`QboId <> LTRIM(RTRIM(QboId))`)
    and it was WRONG in the dangerous direction -- it reported clean while the
    hazard was live. Measured: it MISSED NBSP, ideographic space, and trailing
    ASCII spaces (SQL Server's comparison ignores those), catching 1 of 4 cases.

    The rule this guards is therefore not 'a preflight exists' but 'the preflight
    applies the SAME function the validator applies'. Any T-SQL restatement is a
    translation and can drift."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sql = root / "scripts/migrations/u507_preflight_padded_qbo_ids.sql"
    assert not sql.exists(), (
        "the T-SQL preflight is back. It cannot express normalize_qbo_id's "
        "whitespace set; use the Python one."
    )

    mod = root / "scripts/migrations/u507_preflight_padded_qbo_ids.py"
    assert mod.exists(), "the padded-id preflight was not staged"
    body = mod.read_text()
    assert "from integrations.intuit.qbo.base.ids import normalize_qbo_id" in body
    assert "normalize_qbo_id(value) != value" in body, (
        "preflight no longer compares against normalize_qbo_id itself"
    )
    # Scoped to CODE, not prose: the preflight's own docstring names LTRIM/RTRIM
    # to explain why it is not SQL, and a naive substring check reads that as a
    # violation. (It did, on the first run of this test.)
    import ast

    tree = ast.parse(body)
    if ast.get_docstring(tree) is not None:
        tree.body = tree.body[1:]
    code_only = ast.unparse(tree)
    for token in ("LTRIM", "RTRIM"):
        assert token not in code_only, (
            f"preflight reintroduced a SQL-side {token} translation in executable code"
        )

    validator = (root / "integrations/intuit/qbo/base/id_validation.py").read_text()
    assert "u507_preflight_padded_qbo_ids" in validator, (
        "id_validation.py does not reference the preflight its normalization depends on"
    )


def test_preflight_derives_its_table_list_instead_of_hardcoding_one():
    """The list was hand-curated three times and wrong every time: 6 tables, then
    10 after review found the dbo carriers, then review found an 11th
    (dbo.PaymentTerm). Asking the schema found 32 QboId-carrying tables -- the
    curated list was missing 22. A preflight that silently checks the wrong
    subset is the same false-assurance failure as the T-SQL predicate it
    replaced, so the list must be DERIVED."""
    import importlib.util
    from pathlib import Path

    mod_path = (Path(__file__).resolve().parents[1]
                / "scripts/migrations/u507_preflight_padded_qbo_ids.py")
    body = mod_path.read_text()
    assert "INFORMATION_SCHEMA.COLUMNS" in body, "preflight no longer derives its tables"
    assert "TABLE_TYPE  = 'BASE TABLE'" in body or "TABLE_TYPE = 'BASE TABLE'" in body, (
        "preflight must exclude views -- they are projections of tables already covered"
    )
    assert "TARGETS" not in body, "a hardcoded table list is back"


def _load_preflight():
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parents[1]
            / "scripts/migrations/u507_preflight_padded_qbo_ids.py")
    spec = importlib.util.spec_from_file_location("u507_preflight", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCursor:
    """Answers the schema query, then the per-table scans."""

    def __init__(self, tables, rows_by_table):
        self._tables = tables
        self._rows = rows_by_table
        self._pending = None

    def execute(self, sql, *args):
        if "INFORMATION_SCHEMA" in sql:
            self._pending = [(t,) for t in self._tables]
        else:
            m = re.search(r"FROM (\S+)", sql)
            self._pending = self._rows.get(m.group(1), [])

    def fetchall(self):
        return self._pending


def test_find_non_canonical_flags_exactly_what_the_validator_would_normalize():
    """Semantic, against the REAL function. The previous version of this test
    asserted a LOCAL COPY of the predicate, so it would have passed even if
    find_non_canonical() were bypassed entirely."""
    mod = _load_preflight()
    rows = {
        "qbo.Bill": [(1, "900"), (2, "\xa0900"), (3, "900 ")],
        "dbo.PaymentTerm": [(4, "\u3000TERM"), (5, "TERM")],
    }
    offenders, tables = mod.find_non_canonical(
        cursor=_FakeCursor(["qbo.Bill", "dbo.PaymentTerm"], rows)
    )

    assert tables == ["qbo.Bill", "dbo.PaymentTerm"], "derived table list not used for the scan"
    flagged = {(t, i) for t, i, _ in offenders}
    assert flagged == {("qbo.Bill", 2), ("qbo.Bill", 3), ("dbo.PaymentTerm", 4)}, (
        f"wrong rows flagged: {sorted(flagged)} -- NBSP, trailing-space and "
        "ideographic-space ids must all be caught, canonical ones must not"
    )
