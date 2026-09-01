"""
Verify a dbo-native QBO identity against the qbo.* mapping table before an
outbound push trusts it as a QBO reference (U-276, round-4 review finding).

Background: as families are repointed off qbo.* staging (Phase 4), push
helpers start reading dbo-native QboId columns (e.g. dbo.Project.QboId)
directly instead of hopping through the qbo.*Customer mapping table. dbo
identity alone only guarantees dbo-internal uniqueness — Set*QboIdentity's
own theft-detection UPDATE enforces that no two local rows share an external
id at any moment — but it does NOT guarantee the mapping table has caught up
to the latest holder. The pull-side fast path (base/identity_fastpath.py)
treats a disagreement between the two as a "conflict" and HARD-STOPS —
recording a reconciliation issue and raising, never guessing which side is
right and never proceeding on either. An outbound push needs the same
discipline, or a stale/"stolen" dbo identity can push a live financial
document (Bill/Expense/Invoice) under the WRONG QBO customer's books.

NB (U-287): this module's original text said the pull side "falls back to the
mapping table as authoritative". That WAS true when this was written, and it
was the 2026-08-20 live-prod P0 — falling through let the legacy path
set_qbo_identity on a different row (theft-clearing the conflicted row's
identity) or mint a duplicate. Do not "restore symmetry" by softening the
push side back toward a fall-back; the discipline being mirrored is the hard
stop. Refusing to resolve (returning None here) is the push-side analogue.

NB (U-284v/U-297): two of the three wrappers were also used pull-side at the
time, as the verify step of a dbo-first *reference resolver*
(`BillLineItemConnector._get_project_public_id`,
`CustomerProjectConnector._resolve_parent_customer_id`). There a None was
advisory, not a veto: it meant "don't trust the dbo-native shortcut", and the
caller fell through to the legacy qbo.* hop it was trying to skip — it did NOT
mean "stop". The hard stop above is the rule wherever there is a WRITE to
protect (the push helpers, the header identity fast path); a read-only
resolver has nothing to corrupt by taking the slower, already-trusted path.
(U-310/U-311 later repointed both cited call sites onto `verify_identity_dbo_only`
below instead, once their families retired the legacy hop entirely — see NB
(U-309).)

NB (U-306, superseded U-355 — see the NB below): the verify engine used to run
2 reads per call (mapping-by-local-id, then a second round trip for the
mapped external row) and, when a family had no mapping row at all, TRUSTED
the dbo-stamped QboId unconditionally — LOCAL-SIDE ONLY, blind to the mapping
table already binding that same external id to a DIFFERENT local row (booked
as U-297's H1). Both were closed by one change: each family's
`read_identity_check` callable was a single JOIN'd sproc
(`integrations/intuit/qbo/base/sql/identity_consistency_reads.sql`, deleted
U-355 along with its one remaining caller) that returned the forward
comparison AND the reverse-direction lookup in one round trip. The JOIN'd
read made the reverse check free (it already touches the staging
table), which is what makes closing H1 no longer "self-defeating" the way a
second round trip would have been.

NB (U-309): the `verify_*_qbo_identity` wrappers above read a family's qbo.*
mapping table — the right check while that table is still an
independently-writable second store this module exists to guard against
drifting from (see the top of this docstring). A family that has RETIRED its
mapping table (Wave 5's "trust dbo alone" plan, memory
`project_qbo_trust_dbo_identity_alone`, `docs/design/wave5.md`) has no second
store left to read, so it needs a structurally different check —
`verify_identity_dbo_only` below. See that function's own docstring for its
contract, why it needs no lock, and its current wiring status.

NB (U-314): this NB's own prediction played out — `verify_project_qbo_identity`,
`verify_vendor_qbo_identity`, and `verify_customer_qbo_identity` are deleted
(their families' `qbo.CustomerProject`/`VendorVendor`/`CustomerCustomer`
mapping tables dropped in the same unit). `verify_bill_qbo_identity` was the
last concrete wrapper over `_verify_dbo_qbo_identity`.

NB (U-355): `verify_bill_qbo_identity` and its shared engine
`_verify_dbo_qbo_identity` (plus the `IdentityCheckResult` dataclass and
`BillBillRepository.read_identity_check`, its one caller) are deleted —
`qbo.BillBill` is retired, so Bill has no second store left to cross-check
against either. `outbox/business/worker.py::_refresh_bill` (the one caller)
is repointed onto `verify_identity_dbo_only` below, same as every other
post-Wave-5 family. If a future family needs the two-independent-stores
verify shape `_verify_dbo_qbo_identity` provided, reintroduce it deliberately
rather than reverting this deletion — every family reachable from today's
registry has now retired its second store.
"""
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def verify_identity_dbo_only(
    entity,
    *,
    read_direct_by_qbo_identity: Callable[[Optional[str], Optional[str]], Any],
) -> Optional[str]:
    """
    Verify an already-resolved dbo-native QBO identity for a family that has
    NO qbo.* mapping table left to cross-check against (U-309 — the Wave-5
    "trust dbo alone" verify-side counterpart to
    `base/identity_fastpath.py::run_identity_fastpath_dbo_only`).

    Read `dbo.<Entity>` fresh by `(entity.qbo_id, entity.realm_id)` and return
    `entity.qbo_id` iff the fresh read's `.id` still equals `entity.id`, else
    `None`. This is exactly `run_identity_fastpath_dbo_only`'s unlocked direct
    read (`direct = read_direct_by_qbo_identity(...)`), extracted and given an
    id-match comparison so it answers a VERIFY question — "is this entity I
    already resolved still the current holder of its own identity?" — instead
    of that function's CREATE question, "who currently holds this identity,
    if anyone?"

    No lock. `run_identity_fastpath_dbo_only` takes one because a MISS there
    can lead to two concurrent callers both deciding to MINT a new row for the
    same identity — a real critical section to serialize. This function never
    mints anything, so there is no critical section to protect: the one race
    it could hit (the identity gets reassigned to a different row between the
    caller's original read and this verify call) is caught by the `.id`
    comparison itself, not by serializing around it — there's nothing to
    serialize, only a fact to re-check, exactly as `docs/design/wave5.md` §2
    lays out for Option A.

    `read_direct_by_qbo_identity` is the family's own direct-by-identity
    read (e.g. `<entity>_service.read_by_qbo_identity`), called as
    `read_direct_by_qbo_identity(entity.qbo_id, entity.realm_id)` — the same
    callable shape and parameter name `run_identity_fastpath_dbo_only`
    accepts, so a family that already has one wired for its create path can
    hand it straight to this one too.

    WIRED since U-310/U-311/U-312, into the 12 verify/reference-resolver call
    sites `docs/design/wave5.md` §4 enumerates (Customer/Project/Vendor), each
    replacing that family's `qbo.*`-mapping-table-reading `verify_*_qbo_identity`
    wrapper once the family's own mapping table was retired (those three
    wrappers are gone as of U-314; `verify_bill_qbo_identity` — the last one —
    is gone as of U-355, once Bill retired its own mapping table too; see the
    module docstring's NB (U-314) and NB (U-355)). Bill's push-side callers
    (BillBillConnector, outbox/business/worker.py::_refresh_bill) now call
    THIS function directly, same as every other post-Wave-5 family.
    """
    if not entity or not entity.qbo_id:
        return None

    direct = read_direct_by_qbo_identity(entity.qbo_id, entity.realm_id)
    if direct is not None and direct.id == entity.id:
        return entity.qbo_id

    logger.error(
        f"{type(entity).__name__} {getattr(entity, 'id', None)}'s dbo QboId "
        f"({entity.qbo_id}) no longer resolves back to it on a fresh "
        f"dbo-only read (found local id: {getattr(direct, 'id', None)}) — "
        f"refusing to trust it."
    )
    return None
