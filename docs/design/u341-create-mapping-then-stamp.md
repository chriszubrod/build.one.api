# U-341 — shared `create_mapping_then_stamp` helper for QBO line connectors (DESIGN)

**Status:** Phase-1 design, awaiting `/em` Gate-2. No build code in this unit — approval dispatches the build (U-341) separately.
**Class:** behavior-preserving structural hardening, **design-gated** (extracts a shared `base/` primitive used by 4 line connectors, and the failure policies differ per connector — a decision surface).
**Origin:** U-339 follow-up (booked 2026-08-31). U-339 fixed the live `bill_line_item` stamp-after-swallowed-mapping bug (5 stamped-but-unmapped rows found live at U-293 Gate-1); this unit closes the *class* so a 5th connector — or an edit to an existing one — can't reintroduce it.

---

## 1. The invariant + why it needs structural enforcement

**Invariant:** a QBO line's dbo identity (`stamp_line_identity_or_warn`) must be stamped **only when the mapping-create actually succeeded**. Stamping onto a line whose mapping create failed produces a "stamped-but-unmapped anomaly row" that neither the fast path (won't blind-overwrite) nor Shape-B's fingerprint (won't match on drift) can recover → a duplicate line on the next pull.

Today the invariant is upheld by **convention, per connector** — there is no shared mechanism enforcing it by construction. It already shipped broken once (`bill_line_item`, U-339). A future connector, or an edit to one of the two "warn" connectors, has no structural guard.

## 2. Ground truth — the 4 line connectors have THREE distinct failure policies (verified in-code)

| Connector | On `create_mapping` failure | Stamp reached on failure? |
|---|---|---|
| `bill/…/bill_line_item` (U-339-fixed) | `except ValueError:` → warn + **skip stamp** (`else:`); `DatabaseConstraintError` **propagates** (caller rolls back / holds watermark) | No (fixed) |
| `vendorcredit/…/bill_credit_line_item` | `except Exception:` → warn + **skip stamp** (`else:`) — swallows *all* incl. DatabaseConstraintError, keeps the line (fingerprint re-adopts next pull) | No |
| `invoice/…/invoice_line_item` | `except (ValueError, DatabaseConstraintError):` → **compensating-delete + re-raise** | No (unreachable by construction) |
| `purchase/…/expense_line_item` | `except Exception:` → **compensating-delete + re-raise** (`raise ValueError(...) from e`) | No (unreachable by construction) |

**Two axes vary:** (a) *what happens to the line on failure* — keep-and-warn vs delete-and-raise; (b) *which exceptions each catches* — `ValueError` only (bill) vs `Exception` (the other three). All four AGREE the stamp must not run on failure — that's the shared invariant to enforce.

## 3. Proposed design — one helper, invariant by construction, policy by callback

Add to `integrations/intuit/qbo/base/` (new module `line_identity_stamp.py`, or into `identity_drift.py` beside `stamp_line_identity_or_warn`):

```python
def create_mapping_then_stamp(
    *,
    create_mapping: Callable[[], Any],       # attempts the mapping create; raises on failure
    stamp_identity: Callable[[], None],      # stamps dbo line identity; ONLY called after success
    on_mapping_failure: Callable[[Exception], None],  # per-connector policy; may return (skip) or raise
    catch: tuple[type[Exception], ...] = (ValueError, DatabaseConstraintError),
) -> Any:
    """Enforce the invariant BY CONSTRUCTION: stamp_identity is unreachable unless
    create_mapping returned without raising. On failure, delegate to the connector's
    on_mapping_failure policy (warn-and-return to skip the stamp, or compensating-
    delete-and-raise to abort the line) — the helper itself never stamps on failure."""
    try:
        mapping = create_mapping()
    except catch as exc:
        on_mapping_failure(exc)   # policy: warn (returns) OR delete+raise (raises)
        return None               # reached only if policy chose warn-and-skip
    stamp_identity()              # structurally unreachable on failure
    return mapping
```

Each connector collapses its hand-rolled `try/except/else` into a call passing its own 3 closures. The invariant lives in ONE place; a 5th connector gets it for free and **cannot** reintroduce the bug (there is no code path from a failed `create_mapping` to `stamp_identity`).

- The `catch` parameter preserves the per-connector exception scope (bill: `(ValueError,)` so DatabaseConstraintError still propagates; others: default incl. DatabaseConstraintError).
- `on_mapping_failure` carries the keep-and-warn vs delete-and-raise policy — each connector passes its existing behavior verbatim.

## 4. The one decision for /em (Gate-2)

**Preserve the 3 policies as-is (recommended), or unify them?** This design **preserves** each connector's current failure behavior (behavior-preserving refactor — lowest risk, and each policy has a documented rationale: bill_credit deliberately keeps the line vs purchase deliberately rolls back). The alternative — converging all 4 on ONE policy — is a **behavior change** (e.g. would bill_credit start rolling back lines it currently keeps?) and should NOT ride this structural extraction. Recommendation: preserve now; if a unified policy is wanted, book it separately with its own before/after analysis. Flagging because "extract a shared helper" can tempt an over-eager unification.

## 5. Blast radius / testing

- Files: new `base/line_identity_stamp.py` (+ `create_mapping_then_stamp`), 4 line connectors repointed, their tests. `stamp_line_identity_or_warn` unchanged. No SQL.
- **Behavior-preserving** — each connector's emitted behavior (warn-and-skip / delete-and-raise / propagate) identical before/after; the existing per-connector tests must stay green unchanged.
- Add: a helper-level test proving `stamp_identity` is NOT called when `create_mapping` raises, for BOTH policy shapes (warn callback that returns; callback that raises). **Mutation-proof**: make the helper stamp unconditionally → the test goes RED.
- Add: a structural/AST guard is optional but ideal — assert no line connector calls `stamp_line_identity_or_warn` outside a `create_mapping_then_stamp` call (so a 6th connector can't hand-roll the buggy shape). Nice-to-have; the build unit decides.

## 6. Dispatch

Single build unit (U-341), Composer-writes / Codex-reviews. Sequence: add the helper + its tests → repoint the 4 connectors one at a time (existing tests green after each) → optional AST guard. Pure Python, no SQL, normal deploy after Gate-2.
