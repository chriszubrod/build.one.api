# U-347 — `@qbo_sync_locked` shared lock decorator (design)

**Status:** Design-gated, awaiting `/em` Gate-2. No code changes in this unit — investigation +
design only.
**Scope:** `integrations/intuit/qbo/base/locking.py` and every entity-sync entry point that
acquires the `qbo_sync:<entity>` applock. Does **not** touch the other `qbo_app_lock` domains
(mapping-create, mapping-delete, outbox drain, auth-refresh — see [Non-goals](#non-goals)).

## 1. Problem

`qbo_entity_sync_lock_resource(entity)` + `qbo_app_lock(...)` is the primitive (added in U-337)
that lets every entry point capable of triggering the same QBO entity's sync — a per-entity API
route, the admin `/sync/qbo/{entity}` dispatcher, a standalone CLI script — contend on the *same*
SQL applock instead of each hand-writing its own resource string that can silently drift apart.
U-337's own Pass-1 finding was exactly that drift: the account API route computed a
realm-suffixed key that never contended with the admin route's entity-only key.

The primitive closed that specific drift, but every site still **hand-copies the acquire/handle
ceremony** around it — three to seven lines of `lock_resource = ...; with qbo_app_lock(...) as
got_lock: if not got_lock: <handle>`, repeated at every entry point, with the `<handle>` step
varying by call shape (raise / return-dict / skip / exit-code — see §2). U-340 fanned this
ceremony out to 6 more routers by hand; U-346 is doing the same to CLI scripts **right now** (see
§2.4) — this design exists so the *next* entity, and the next entry-point kind, don't require a
9th, 10th, 11th hand-copy of the same shape, and so a hand-typed entity string can't drift again.

## 2. Current-state inventory (verified against live code, not the assignment's assumed count)

The assignment described "the per-entity API routers (U-337 account + U-340's 6)" and "the 9+1
CLI scripts (U-346)". Both undercounted — the actual surface, read from the code, is:

### 2.1 API routers — 8 live sync routes, 7 locked, **1 unlocked gap**

| Entity | Router | Locked? | Notes |
|---|---|---|---|
| account | `integrations/intuit/qbo/account/api/router.py` | ✅ U-337 | reference shape |
| bill | `.../bill/api/router.py` | ✅ U-340 | |
| purchase | `.../purchase/api/router.py` | ✅ U-340 | |
| vendor | `.../vendor/api/router.py` | ✅ U-340 | |
| customer | `.../customer/api/router.py` | ✅ U-340 | |
| company_info | `.../company_info/api/router.py` | ✅ U-340 | no `last_updated_time`, otherwise same shape |
| vendorcredit | `.../vendorcredit/api/router.py` | ✅ U-340 | |
| **item** | `.../item/api/router.py` | ❌ **unlocked** | **live** route (comment at the call site: *"POST /sync/qbo-items (the live pull) stays"*) that calls `service.sync_from_qbo(...)` with zero lock guard. U-340's own test docstring accounts for `term` (no router) and `invoice` (dead route) but never mentions `item` — it was missed, not deliberately excluded. This is the *exact* race U-337/U-340 exist to close, live in prod today. |
| invoice | `.../invoice/api/router.py` | — (n/a) | correctly unlocked: the handler never calls `sync_from_qbo`, just logs and returns `[]` ("Invoice QBO sync disabled... managed manually in QBO") |
| term | *(no router)* | — (n/a) | only reachable via admin dispatcher + CLI |

**Flagging `item` is a finding of this design unit, not a pre-existing known item** — it should be
folded into this migration's scope (§5) rather than filed as a separate follow-up, since fixing it
is now a one-line decorator application once §3 lands.

### 2.2 Admin dispatcher — 1 site, dynamic entity

`shared/api/admin.py::sync_qbo_router` (`POST /sync/qbo/{entity}`) builds `lock_resource` from a
**runtime path param**, not a literal known at def time, and uses `timeout_ms=0` (non-blocking):
on contention it returns `{"skipped": True}`, which the caller translates to **HTTP 200**
`{"status": "skipped", "reason": "lock_busy"}` — never a 409. This is a third failure-handling
shape, and it covers all 11 `VALID_QBO_ENTITIES` (the 10 above plus `reimburse_charge`, which has
no API router at all).

### 2.3 CLI scripts — 11 total, 1 locked, **10 need locking** (not 9)

`scripts/sync_qbo_*.py`, one per entity, each with an `if __name__ == "__main__":` block:

| Script | Locked? |
|---|---|
| `sync_qbo_account.py` | ✅ U-337/U-340 — has `run_locked()`, the reference shape |
| `sync_qbo_bill.py` | 🟡 **in flight, uncommitted** — see §2.4 |
| `sync_qbo_invoice.py` | 🟡 **in flight, uncommitted** — see §2.4 |
| `sync_qbo_purchase.py`, `_vendor.py`, `_customer.py`, `_company_info.py`, `_vendorcredit.py`, `_item.py`, `_term.py` | ❌ unlocked |
| `sync_qbo_reimburse_charge.py` | ❌ unlocked — **missing from U-340's TODO follow-up list entirely** (which named 9 scripts, not this one). Same gap class as `item`'s router: a live entity in `VALID_QBO_ENTITIES`, dispatched by admin.py, with a CLI entry point nobody locked. |

So: 1 done, 2 in flight, **8** genuinely untouched, for **10** total needing the fix — the
assignment's "9" undercounts by missing `reimburse_charge`.

### 2.4 Live concurrent WIP — directly relevant to sequencing (§6)

The working tree is dirty right now with **uncommitted** changes to `scripts/sync_qbo_bill.py` and
`scripts/sync_qbo_invoice.py` (plus an unrelated `entities/invoice/intelligence/prompt.md` edit —
out of scope, untouched by this unit per the standing "never touch prompt.md" rule). Both diffs
hand-add a `run_locked()` function **byte-for-byte identical in shape** to
`sync_qbo_account.py::run_locked` — same `lock_resource = qbo_entity_sync_lock_resource(entity)`,
same `with qbo_app_lock(lock_resource) as got_lock`, same busy-dict shape, same docstring citing
"see scripts/sync_qbo_account.py::run_locked for the full rationale". This is U-346 actively
fanning out by hand, mid-flight, in this exact repo, right now — the concrete proof of the
copy-paste-drift risk this design is meant to close, and a hard constraint on migration ordering
(§6.4).

### 2.5 `repair_invoice_line_duplicates.py` — a 4th, distinct shape

Not a sync entry point — it's a data-repair script that **contends against** the `invoice` sync
lock so its mutation can't race a live `sync_qbo_invoice` run. Already uses
`qbo_entity_sync_lock_resource("invoice")` + `qbo_app_lock(..., timeout_ms=30_000)` directly
(no wrapper), guards only the `apply_repairs(...)` call (not the whole `main()`), and on
contention logs and returns a shell exit code (`1`), not JSON, not an exception.

### 2.6 Summary of shapes

| # | Shape | Sites | On contention |
|---|---|---|---|
| A | FastAPI route | 8 (7 done + `item`) | `raise HTTPException(409, detail=...)` |
| B | Admin dispatcher | 1 (dynamic, all 11 entities) | return `{"skipped": True}` → HTTP 200 skip |
| C | CLI entry function | 11 (1 done, 2 in flight, 8 to build) | return `{"result": {"success": False, ...}, "status_code": 409}` |
| D | Non-sync lock consumer | 1 (`repair_invoice_line_duplicates.py`) | log + `return 1` |

Four distinct failure-handling shapes is why §3 proposes one shared primitive plus **two** thin
decorators, not one universal decorator (§7.1).

## 3. Proposed design

All additions live in `integrations/intuit/qbo/base/locking.py`, next to the existing
`qbo_entity_sync_lock_resource` / `qbo_app_lock`. `qbo_entity_sync_lock_resource` stays the sole
key source (per the assignment's constraint) — every new helper below calls it internally and
**no other module computes a `qbo_sync:<entity>` string again**.

### 3.1 Layer 0 — `qbo_sync_lock` (context manager, the actual fix for key-drift)

```python
@contextmanager
def qbo_sync_lock(entity: str, timeout_ms: int = 15000) -> Iterator[bool]:
    """
    Acquire the qbo_app_lock for a QBO entity's sync, keyed via
    qbo_entity_sync_lock_resource(entity) — the ONE place that turns an
    entity name into the qbo_sync:<entity> resource string for sync
    purposes. Every entity-sync entry point (route, CLI, admin dispatcher,
    a lock-consuming repair script) should acquire through this, not by
    re-deriving the resource string itself.
    """
    with qbo_app_lock(qbo_entity_sync_lock_resource(entity), timeout_ms=timeout_ms) as got_lock:
        yield got_lock
```

This alone is the structural fix the assignment's "can't drift" goal needs: it's usable
everywhere, including the two sites (admin dispatcher, repair script) that don't fit either
decorator below. It is a pure refactor of the existing two-line pattern — zero behavior change.

### 3.2 Layer 1a — `qbo_sync_locked_route(entity)` (FastAPI route decorator)

```python
def qbo_sync_locked_route(entity: str, timeout_ms: int = 15000):
    """
    Decorator for a POST /sync/qbo-* route handler. Wraps the call in
    qbo_sync_lock(entity); raises HTTPException(409) on contention.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with qbo_sync_lock(entity, timeout_ms=timeout_ms) as got_lock:
                if not got_lock:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"QBO {entity} sync already in progress. Try again shortly.",
                    )
                return func(*args, **kwargs)
        return wrapper
    return decorator
```

Applied as:

```python
@router.post("/sync/qbo-accounts")
@qbo_sync_locked_route("account")
def sync_qbo_accounts_router(body: QboAccountSync, current_user: dict = Depends(...)):
    result = service.sync_from_qbo(realm_id=body.realm_id, last_updated_time=body.last_updated_time)
    return list_response([account.to_dict() for account in result.synced])
```

**FastAPI/DI compatibility (the one real implementation risk):** FastAPI discovers a route's
parameters via `inspect.signature(endpoint)`, which follows `__wrapped__` — set automatically by
`functools.wraps` — so `body: QboAccountSync` and `current_user: dict = Depends(...)` still
resolve correctly through the decorator; FastAPI then calls the *wrapper* with the resolved
kwargs by name, and the wrapper forwards them via `**kwargs`. This is a well-established pattern,
but it must be **proven with a `TestClient` smoke request per repointed router in Phase 2**, not
assumed from reading `inspect`'s docs — a DI-resolution break here would surface as a runtime 422
or 500, not a decoration-time error. All 8 current per-entity routers are plain `def` (sync); the
decorator does not need `async def` support for the current site set — noted as a limitation
rather than solved speculatively.

### 3.3 Layer 1b — `qbo_sync_locked_cli(entity)` (CLI entry-function decorator)

```python
def qbo_sync_locked_cli(entity: str, timeout_ms: int = 15000):
    """
    Decorator for a sync_qbo_*.py CLI entry function returning the
    {"result": {...}, "status_code": int} shape every sync_qbo_* function
    already returns. On contention, returns a status_code=409 dict of that
    SAME shape instead of calling the wrapped function — so a caller (or
    exit_nonzero_on_sync_failure) never needs to special-case lock-busy vs.
    a real sync failure.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            resource = qbo_entity_sync_lock_resource(entity)
            with qbo_sync_lock(entity, timeout_ms=timeout_ms) as got_lock:
                if not got_lock:
                    return {
                        "result": {
                            "success": False,
                            "error": f"QBO {entity} sync already in progress (lock '{resource}' busy).",
                        },
                        "status_code": 409,
                    }
                return func(*args, **kwargs)
        return wrapper
    return decorator
```

Applied as:

```python
@qbo_sync_locked_cli("account")
def run_locked(skip_sync_record_update: bool = False, dry_run: bool = False) -> dict:
    return sync_qbo_account(skip_sync_record_update=skip_sync_record_update, dry_run=dry_run)
```

**Hard constraint, carried over from `sync_qbo_account.py::run_locked`'s own docstring:** the
decorator must wrap a **dedicated CLI-only entry function** (`run_locked`), *never* the shared
`sync_qbo_<entity>()` function itself. The admin dispatcher calls `sync_qbo_<entity>()` directly
while **already holding this exact resource** — decorating the shared function would make the
admin path's own outer lock nest a second acquire of the same resource from a second DB session
and self-deadlock (block until `timeout_ms`, then fail every admin-triggered sync). This is not a
hypothetical: it's the documented reason `run_locked()` exists as a separate function today, and
every one of the 8 remaining CLI scripts must follow the same split.

### 3.4 Admin dispatcher — folds onto Layer 0, not a decorator

```python
lock_resource = qbo_entity_sync_lock_resource(entity)  # unchanged — used only in the log line below

def _locked_sync_fn():
    with qbo_sync_lock(entity, timeout_ms=0) as got_lock:
        if not got_lock:
            return {"skipped": True}
        return sync_fn()
```

`entity` here is a **runtime path parameter**, not a string literal known at decoration time — a
decorator's `@qbo_sync_locked_route("account")` shape requires the entity to be fixed at `def`
time, so it structurally cannot express "lock whichever entity this request names." The right fit
is the Layer-0 primitive used inline, exactly as today, just collapsed from two calls to one. This
directly answers the assignment's "whether to also fold the admin dispatcher" question: **yes,
onto `qbo_sync_lock`, not onto either decorator** — and it's also the reason a single "universal"
decorator covering routes *and* the dispatcher was rejected (§7.1).

### 3.5 `repair_invoice_line_duplicates.py` — folds onto Layer 0, not a decorator

Same reasoning as §3.4 for a different cause: this isn't an entity-sync entry point at all, and
the lock only needs to guard one inner call (`apply_repairs(...)`), not the function it's called
from. It keeps its custom `timeout_ms=30_000` and its own log-and-`return 1` handling, just via
one call instead of two:

```python
with qbo_sync_lock("invoice", timeout_ms=QBO_INVOICE_SYNC_LOCK_TIMEOUT_MS) as got_lock:
    if not got_lock:
        logger.error(...)
        return 1
    deleted, skipped, all_ok = apply_repairs(...)
```

## 4. The one user-visible behavior change

Today's 7 router 409 messages interpolate the caller's own `body.realm_id`:
`f"QBO {entity} sync already in progress for realm {body.realm_id}. Try again shortly."`
`qbo_entity_sync_lock_resource`'s own docstring establishes the lock is **deliberately
entity-only, not realm-scoped** (single-realm system today) — so the realm segment in the message
was always slightly misleading (it names the realm the *caller* passed, not necessarily the realm
actually holding the lock). §3.2's decorator drops it: `f"QBO {entity} sync already in progress.
Try again shortly."` This is a real (if cosmetic) API response-text change on 7 already-shipped
endpoints — called out explicitly rather than folded in silently, and worth a one-line Gate-2
sign-off (§8) even though no test asserts the old wording.

## 5. Migration inventory

| Site | Change | Kind |
|---|---|---|
| `locking.py` | add `qbo_sync_lock`, `qbo_sync_locked_route`, `qbo_sync_locked_cli` | new, additive |
| 7 shipped routers (account, bill, purchase, vendor, customer, company_info, vendorcredit) | repoint onto `@qbo_sync_locked_route(entity)` | mechanical repoint, behavior-preserving except §4 |
| `item` router | **add** `@qbo_sync_locked_route("item")` | new locking (closes a live gap) |
| `shared/api/admin.py` dispatcher | repoint onto `qbo_sync_lock(entity, timeout_ms=0)` | mechanical repoint, no behavior change |
| `sync_qbo_account.py::run_locked` | repoint onto `@qbo_sync_locked_cli("account")` | mechanical repoint, no behavior change — becomes the canonical worked example |
| `sync_qbo_bill.py`, `sync_qbo_invoice.py` | reconcile in-flight WIP onto `@qbo_sync_locked_cli(...)` instead of hand-copied `run_locked()` | see §6.4 |
| `sync_qbo_purchase.py`, `_vendor.py`, `_customer.py`, `_company_info.py`, `_vendorcredit.py`, `_item.py`, `_term.py`, `_reimburse_charge.py` (8 scripts) | **add** `run_locked()` + `@qbo_sync_locked_cli(entity)` | new locking |
| `repair_invoice_line_duplicates.py` | repoint onto `qbo_sync_lock("invoice", timeout_ms=...)` | mechanical repoint, no behavior change |

## 6. Sequencing

1. Build + unit-test `locking.py`'s 3 new helpers in isolation first (mirrors the existing
   `qbo_app_lock`/`qbo_entity_sync_lock_resource` test style) — behavior-preserving by
   construction, lowest risk, unblocks everything else.
2. Repoint the 7 shipped routers + admin dispatcher + `sync_qbo_account.py` onto the new helpers
   in one pass — pure mechanical repoints, easy to verify via existing tests
   (`test_u337_qbo_account_sync_lock.py`, `test_u340_qbo_entity_sync_lock_fanout.py`) with the
   patch targets updated to the new module path.
3. Add the `item` router lock — same shape as step 2 but *new* coverage, so it wants its own
   contention test (mirroring `test_u337`'s three assertions), not just a repoint diff.
4. **Reconcile the in-flight `sync_qbo_bill.py` / `sync_qbo_invoice.py` WIP (§2.4) before it
   lands.** Those two diffs are sitting in the working tree uncommitted right now, hand-writing the
   exact `run_locked()` shape this unit exists to replace. If U-346 commits them first, step 5
   below becomes "repoint 2, add 6" instead of "add 8" — either order is fine functionally, but
   whichever session picks up U-346 next should build on `@qbo_sync_locked_cli` if it exists yet,
   and this design should be Gate-2'd and Phase-2-built **before** any *more* CLI scripts get the
   old hand-copied treatment, so the fan-out doesn't grow past 2 scripts on the deprecated shape.
5. Add the remaining CLI `run_locked()` wrappers (6–8 depending on step 4's timing) +
   `repair_invoice_line_duplicates.py` repoint.
6. Guard test (§7.3) lands last, once every site is migrated, so it's green from the start rather
   than landing red and getting silenced.

## 7. Open decisions for Gate-2

### 7.1 One decorator vs. two variants — **recommend two thin decorators over one shared primitive**

Rejected: a single `@qbo_sync_locked(entity, mode="route"|"cli")` polymorphic decorator. The four
failure-handling shapes in §2.6 (raise / skip-dict / busy-dict / exit-code) are genuinely
different contracts with different callers (FastAPI's exception middleware vs. a JSON-printing
CLI vs. an async dispatcher vs. a script's own log-and-exit) — a `mode` string parameter would
just move the branching from "which decorator did you import" to "which string did you pass,"
without saving a line, and loses the self-documenting call site (`@qbo_sync_locked_route(...)`
tells a reader the failure mode without opening the definition). Recommend: **one shared
primitive (`qbo_sync_lock`) + two named decorators (`_route`, `_cli`)**, with the admin dispatcher
and the repair script using the primitive directly (§3.4, §3.5) since neither fits a
decorator's static-entity-at-def-time shape.

### 7.2 Fold the admin dispatcher in? — **yes, onto the primitive, not a decorator** (§3.4)

### 7.3 Guard test — recommend an import-boundary check, not a call-site scan

Concretely: assert that no file *other than* `integrations/intuit/qbo/base/locking.py` imports
`qbo_app_lock` directly for entity-sync purposes. In practice this means the 8 router files, the
admin dispatcher, all 11 CLI scripts, and the repair script must import only
`qbo_sync_lock` / `qbo_sync_locked_route` / `qbo_sync_locked_cli` from `locking.py` — never
`qbo_app_lock` itself. A parametrized test over that explicit file list (source-scans each file
for `qbo_app_lock` in its `from ... import` line) fails the moment a new entry point — or a
reverted repoint — reaches for the raw primitive instead of the shared wrapper, which is the
concrete "new entry point gets it free" property the assignment asks for. (The *other* lock
domains — `identity_fastpath.py`, `mapping_cleanup.py`, the outbox worker, the auth-refresh lock —
correctly keep importing `qbo_app_lock` directly; they're a different resource-key scheme
entirely and are explicitly out of this guard's file list, see [Non-goals](#non-goals).)

### 7.4 Sequencing vs. U-337/U-340/U-346 — see §6; the concrete ask is: land this design's Gate-2
before any additional CLI script gets a hand-copied `run_locked()`, so at most 2 scripts
(`bill`, `invoice`, already in flight) ever exist on the old shape.

## Non-goals

Out of scope for this decorator, confirmed by code (not assumed): `identity_fastpath.py`'s
mapping-create lock (`qbo_mapping_create:<label>:<id>`), `mapping_cleanup.py`'s mapping-delete
lock (`qbo_mapping_delete:<label>:<id>`), the outbox worker's drain lock (`DRAIN_LOCK_NAME`,
module-constant), and `auth/business/service.py`'s token-refresh lock. All four use
`qbo_app_lock` with resource keys that have nothing to do with `qbo_entity_sync_lock_resource` and
serve entirely different serialization purposes — folding them into "the QBO sync lock decorator"
would conflate distinct lock domains for no benefit.

`entities/invoice/intelligence/prompt.md` — untouched, per standing rule; unrelated dirty WIP
noted only because it shares the working tree with the in-flight CLI script edits in §2.4.

---

## /em Gate-2 verdict — APPROVED (2026-08-31)

Design approved as written; it exceeds the brief (caught the self-deadlock trap, the FastAPI DI risk, the item-router live gap, and reimburse_charge). Decisions on §7:
1. **§7.1 — 1 primitive + 2 named decorators** (`qbo_sync_lock` + `_route` + `_cli`). Approved — the 4 failure shapes are genuinely distinct; reject the polymorphic `mode=` decorator.
2. **§7.2 — fold the admin dispatcher onto `qbo_sync_lock` (Layer 0), not a decorator.** Approved (runtime entity param can't be a static-entity decorator).
3. **§7.3 — import-boundary guard test.** Approved.
4. **§4 — the 7-router 409 text dropping "for realm {id}".** Approved (the lock is entity-only; the realm segment was misleading). Cosmetic, no test asserts the old wording.

**Sequencing decision (§6.4 / §7.4):** U-346 (CLI-lock fan-out) is already in flight and closes real security gaps NOW — **let it land** (it must follow the `run_locked()` split per §3.3, which its `sync_qbo_account.py` reference enforces). U-347 **Phase-2 build dispatches AFTER U-346 lands + Gate-2**, and its repoint then converts U-346's hand-copied `run_locked()`s onto `@qbo_sync_locked_cli` (behavior-preserving) — the "wasted" hand-copies are cheap and get replaced. Accepting a handful more hand-copies is the right call vs. holding the security fix for the decorator.

**2 live gaps U-347 Phase-2 MUST close (neither is in U-346's scope):**
- **`item` router `/sync/qbo-items`** — unlocked live sync route (U-337/U-340 missed it). Same race class as the others, low-probability, but real.
- **`sync_qbo_reimburse_charge.py`** — unlocked CLI, absent from U-340's 9-script list and from U-346's 9-script scope.

Phase-2 build = the full §5 migration inventory (7 router repoints + item-router new lock + admin dispatcher + all CLI repoints/adds incl. reimburse_charge + repair script + the guard test), with a TestClient smoke request per repointed router (§3.2 DI risk).
