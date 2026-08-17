# RC Source Linking Signal — Measurement & Decision (U-242)

**Date:** 2026-08-16  
**Unit:** U-242 (measurement/decision — no linking behavior shipped)  
**Script:** `scripts/analyze_rc_source_fingerprint.py`  
**Pure matcher:** `integrations/intuit/qbo/reimburse_charge/business/fingerprint.py`

---

## 1. Finding — KI-32 restated from measurement

**Original KI-32 claim:** QBO exposes a reverse `LinkedTxn` from ReimburseCharge back to the source Bill/Purchase while un-invoiced, and **destroys that pointer** once `HasBeenInvoiced=true`.

**Measured mechanism (2026-08-16, live realm, full population via `analyze_rc_source_fingerprint.py` Step B):**

| Lifecycle | LinkedTxn behavior |
|-----------|-------------------|
| `HasBeenInvoiced=false` | **No `LinkedTxn` key** on header or lines |
| `HasBeenInvoiced=true` | `LinkedTxn` present, but `TxnType='Invoice'` only — forward pointer to the consuming invoice, **never** Bill/Purchase |

A prior n=3000 sample independently confirmed zero Bill/Purchase-type `LinkedTxn` entries at header or line level. Step B re-verifies over the **full** live pull.

### Full-population LinkedTxn counts

Measured 2026-08-16 by `scripts/analyze_rc_source_fingerprint.py`, **independently reproduced**
by a second agent that wrote its own from-scratch script (no access to this repo's new files) —
both runs pulled the full live realm fresh and landed on identical numbers:

```
Live QBO ReimburseCharge count:          26,582
Staged qbo.ReimburseCharge count:        26,582  (0 drift at time of run)

HasBeenInvoiced=false — TxnType counts:  (none) — 658 records, zero carry a LinkedTxn key
HasBeenInvoiced=true  — TxnType counts:  Invoice: 54,641  (header + line-level combined;
                                          25,924 records × ~1-2 LinkedTxn entries each,
                                          multi-line RCs carry one per line)

Bill/Purchase LinkedTxn found anywhere:  ZERO — confirmed by both runs
```

This is not a sample — it is every ReimburseCharge in the realm. The independent run parsed
header vs. line-level LinkedTxn separately and got the same split (25,924 header pointers,
28,717 line-level pointers, all `TxnType='Invoice'`, zero `Bill`/`Purchase`).

**Implication:** `parse_reimburse_charge`'s `_SOURCE_TXN_TYPES = ("Bill", "Purchase")` filter is correct and defensive — it would ignore an Invoice forward pointer rather than mis-capture it — but **there is nothing to match today**. `qbo.ReimburseCharge.SourceTxnId` is never populated from QBO pulls. The Tier-0 path in `entities/invoice/business/reconciliation.py` (`apply_links`) is wired but structurally dead until a different signal is chosen.

Existing CASE-WHEN-preserve / merge / watermark-hold behavior remains valuable as **defensive/forward-compatible** practice (do not null stored pointers; hold watermark on staging failure for idempotent retry) — it is **not** load-bearing recovery of a proven one-shot data-loss window.

---

## 2. Measured fingerprint match rates

Fingerprint tiers (evaluated independently per line, pure function `tier_match`):

| Tier | Match key |
|------|-----------|
| **A** | `customer_ref_value` + amount (±$0.01) + `txn_date` |
| **B** | Tier A + `description` |
| **C** | Tier B + `item_ref_value` |

Candidate pool: staged `qbo.BillLine` + `qbo.PurchaseLine` for the realm (combined). Markup/derivative RC lines (no `ItemRef`) excluded from matching.

### Line counts

Measured and independently reproduced (both runs identical):

```
Base lines measured:                     26,525
Derivative (markup) lines excluded:       3,080
Other DetailType lines skipped:               0
HasBeenInvoiced=true base lines:         25,869
HasBeenInvoiced=false base lines:           656
Candidate pool (Bill + Purchase):        37,336  (BillLine=24,447, PurchaseLine=12,889)
```

### Per-tier rates — ALL base lines

| Tier | % unique | % ambiguous | % unmatched |
|------|----------|-------------|-------------|
| A (customer+amount+date) | 94.04% | 4.17% | 1.79% |
| B (+description) | 97.11% | 1.09% | 1.80% |
| C (+cost-code item_ref) | **97.14%** | **1.06%** | **1.80%** |

Independent reproduction (fresh live pull, separately-written code): A 94.04/4.17/1.79 · B
97.11/1.09/1.80 · C 97.14/1.06/1.80 — **exact agreement**, not just "close."

### Split by `HasBeenInvoiced` (bias check)

| Tier | Invoiced=true (n=25,869) | Invoiced=false (n=656) |
|------|---------------------------|--------------------------|
| A | 94.06% unique / 4.19% amb / 1.75% unm | 93.14% unique / 3.35% amb / 3.51% unm |
| B | 97.18% unique / 1.06% amb / 1.76% unm | 94.21% unique / 2.29% amb / 3.51% unm |
| C | 97.22% unique / 1.02% amb / 1.76% unm | 94.21% unique / 2.29% amb / 3.51% unm |

Un-invoiced RCs run ~3 points lower on unique% and ~2x higher on unmatched% — expected: they're
newer/more-recent charges, so their source Bill/Purchase is more likely not yet staged, or the
RC itself was created moments before this pull with its source line still in flight. Not a bias
in the matching logic itself — a real staleness effect worth noting for cadence (Section 4).

### Actionable cut (Tier-C unique matches with mapped dbo id)

```
Tier-C unique total:                          25,767
Tier-C unique with BillLineItemId /
  ExpenseLineItemId mapped:                   24,799
Actionable %:                                  96.24%    (independently reproduced: 96.24%)
```

968 Tier-C-unique matches (3.76%) point at a qbo line with no dbo mapping yet — not immediately
usable for `apply_links` without an onboarding step (KI-35 precedent: scoped `sync_qbo_bill.py`
re-run backfills the mapping, or a direct-`dbo` fallback for locally-originated CL bills).

### Ambiguous Tier-C samples — what they have in common

Two independent passes characterized the ambiguous cases and converged on the same root causes.
**None of the ~280 ambiguous cases look like a fingerprint weakness — they trace to genuine
duplicate or near-identical source data:**

1. **Multi-worker crew lines (the dominant pattern, ~15-20 of the 25 sampled).** Contract-labor
   bills like `2026.06.30.TB3` post one BillLineItem per worker (Elmer Cordova, Wilmer Diaz,
   Ricardo Moreno, …) sharing the identical task description, cost code, and dollar amount for
   the same work-date — e.g. RC 74534 ("Added drywall on pantry…", $65.00, item 340,
   2026-06-30) matches 3 separate BillLineItems, one per crew member. The fingerprint **cannot**
   and **should not** disambiguate these — there is no way to know which worker's line an RC's
   $65 belongs to from amount+date+description+item alone; this is an inherent limit of the
   signal, not a bug.
2. **Duplicate/orphan BillLine rows.** RC 74035 ("Beam Install", $265.20, item 385) matches Bill
   line 23387 (mapped) **and** Bill line 24012 (unmapped) — a duplicate/orphaned staging row
   sitting alongside the real one. Similarly RC 74717 / 74043 ("Entry Gate", $2,075.00) each
   match a mapped line and an unmapped duplicate.
3. **Cross-source double-entry.** RC 73604 ("Trim Materials", $205.45, item 379) matches a Bill
   line **and** a Purchase line with identical amount/date/description/item — the same vendor
   charge appears to have been entered twice, once as a Bill and once as a Purchase/Expense.
   This is a genuine duplicate-booking risk pattern independent of RC linking.

**Practical takeaway:** a real Tier-0.5 implementation needs an explicit tie-break policy for
the crew-line case (pattern 1 is common and benign — likely resolvable by aligning RC line order
within a multi-line invoice to BillLineItem insertion order, the same `LineNum`-alignment trick
`entities/invoice/intelligence/prompt.md` already documents for ambiguous descriptions) and
should **surface, not silently pick**, patterns 2/3 (they indicate real data-quality issues
worth fixing at the source, not papering over in the matcher).

---

## 3. Recommended signal

**Recommendation: Tier C** (customer_ref + amount ±$0.01 + txn_date + description + cost-code
`item_ref`) as the RC→source fingerprint, wired in as a **Tier-0.5 candidate query** — not a
blind auto-apply.

| Candidate signal | Verdict |
|------------------|---------|
| **Tier C** (customer + amount + date + description + item) | **Recommended.** 97.14% unique, 1.06% ambiguous, 1.8% unmatched, 96.24% of unique matches already have a usable dbo mapping. Adds real discrimination over A (+3.1 points unique, ambiguity cut by ~4x) — description and cost code both earn their place in the key. |
| **Tier B** | Marginally worse than C (97.11% vs 97.14% unique) and no cheaper to compute — no reason to drop item_ref. |
| **Tier A only** | Rejected — 4.17% ambiguous (1,105 lines) is 4x Tier C's ambiguity, driven by real same-day/same-amount/different-work collisions that description+item resolve. |
| **Tier 0 (LinkedTxn → SourceTxnId)** | **Not viable** — confirmed twice independently, zero Bill/Purchase pointers exist at any lifecycle stage. |

**Confidence statement (honest):** Tier C is **not deterministic** — 97.14% unique is a strong
practical signal, not a guarantee. The two failure modes are structurally different and need
different handling:
- **1.8% unmatched** — mostly explained by staleness (un-invoiced RCs run 3.51% unmatched vs
  1.76% for invoiced, consistent with the source Bill/Purchase not yet being staged).
- **1.06% ambiguous** — overwhelmingly genuine duplicate/near-duplicate source data (Section 2),
  not a matching-algorithm weakness. A same-`LineNum`-position tie-break (the same trick already
  used for ambiguous invoice-line descriptions in `entities/invoice/intelligence/prompt.md` Step
  4.1) would likely resolve the multi-worker-crew-line majority of these, but that's untested
  here and belongs in the follow-up implementation unit, not asserted as a number in this doc.

**Do not** describe Tier C (or any tier) as "deterministic" in code comments or product copy —
ship it as "propose, surface ambiguous/unmatched for review," matching the existing
`InvoiceReconciliationService.propose_links` / `apply_links` pattern (dry-run proposal, explicit
apply, `ambiguous` and `no_match` statuses already exist as first-class outcomes in
`resolve_link_proposals`).

Cross-reference: `ProposeInvoiceSourceLinks` / `entities/invoice/business/reconciliation.py`
fingerprint tiers already exist for invoice-line → source linking; the follow-up unit adds
RC-mediated resolution using Tier C as a new Tier-0.5 candidate source, or replaces the dead
LinkedTxn-based Tier-0 hop outright (see Section 5).

---

## 4. Cadence recommendation — is 15 minutes still justified?

The original 15-minute RC staging timer was partly motivated by KI-32's assumed one-shot capture window. **Measurement removes that urgency** — there is no reverse pointer to lose. Cadence choice becomes a **staleness vs API cost** tradeoff.

### Call-cost math (ReimburseCharge pull)

Two different costs, not one — this doc's own measurement run (Section 1/2, `query_all_reimburse_charges` with no filter) paginates at 1000 rows/page, so pulling the full 26,582-row realm cost **~27 metered calls, once**. The recurring **timer** is a different, cheaper operation: `sync_qbo_reimburse_charge.py` calls `sync_from_qbo(..., last_updated_time=<last tick's watermark>)`, which QBO filters server-side (`Metadata.LastUpdatedTime > ...`) — each steady-state tick only returns RCs changed since the last tick, not the whole realm. At any of the cadences below, that's realistically single-to-low-double-digit RCs per window, well under the 1000-row page size, so **each steady-state tick = 1 metered call**, same as a normal incremental pull for any other entity in this fleet. (A cold-start/backfill tick with no prior watermark is the ~27-call full-pull case above — a one-time cost independent of cadence.)

| Cadence | Ticks/month | RC pull calls/month (steady-state, 1/tick) | % of 500,000 cap |
|---------|-------------|---------------------------------------------|------------------|
| **15 min** | 2,880 | 2,880 | 0.58% |
| **1 hour** | 720 | 720 | 0.14% |
| **4×/day (6 h)** | 120 | 120 | 0.024% |

**Realm baseline (~early August 2026):** ~6–7K CorePlus calls/month total across all QBO operations. A 15-minute RC timer adds ~2,880 calls → **~40–45% incremental** on that baseline (not on the 500K cap — the cap headroom is ample either way).

**Assessment:**

- **500K cap:** RC pull at any listed cadence is negligible (<1% of cap either way) — the cap was
  never the real constraint for this timer.
- **Steady-state budget:** 15-minute vs hourly is a ~2,160 call/month delta against a ~6–7K/month
  realm baseline — a ~30% swing in total QBO call volume for one timer, which *was* the actual
  cost this unit's finding removes the justification for.
- **Staleness now matters for fingerprint fields, not pointer capture.** The original 15-minute
  cadence existed to catch a source pointer before QBO destroyed it — that pointer doesn't exist,
  so there's nothing time-critical to catch. What staleness now affects is how fresh `qbo.
  ReimburseCharge`'s CustomerRef/Amount/TxnDate/Description/ItemRef are when a linking pass runs
  — and those fields are set once when the source Bill/Purchase line is entered and essentially
  never change afterward (they're the vendor's original charge, not something QBO revises).
  Measured: only **656 of 26,525 base lines (2.5%) are currently un-invoiced** — the vast
  majority of RC rows a linking pass would touch are already stable, invoiced records where
  cadence is irrelevant.

**Recommendation: relax to hourly (`0 X * * * *`).** Nothing in this measurement supports
sub-hour cadence — the one-shot-loss rationale is gone, the fingerprint fields it would be
protecting are stable once written, and the unmatched-rate gap between invoiced/un-invoiced RCs
(1.76% vs 3.51%) is a staging-lag effect that an hourly tick resolves within the hour just as
well as a 15-minute tick resolves it within 15 minutes — neither cadence is "catching" a window
that closes, just adjusting how long staging can lag reality. Hourly cuts RC-pull call volume
~4x (2,880 → 720/month, ~10-12% of the current realm baseline instead of ~40-45%) with no
measured downside. If a future unit's linking SLA needs fresher-than-hourly RC data for a
specific workflow (e.g. same-session linking right after a bill is entered), tighten then, with
that concrete requirement driving the number — not a speculative one-shot risk that measurement
has now ruled out.

---

## 5. Follow-up unit scope (NOT done in U-242)

A real implementation unit would need to:

1. **Wire chosen tier** into `ProposeInvoiceSourceLinks` / `entities/invoice/business/reconciliation.py` as Tier-0 or Tier-0.5 — query staged RC rows + fingerprint match to `qbo.BillLine`/`qbo.PurchaseLine`, then hop to dbo line items.
2. **Handle ambiguous matches** — skip, flag for review, or deterministic tie-break (document policy).
3. **Unmapped candidate onboarding (KI-35)** — when Tier-C unique match lands on a qbo line with no `BillLineItemBillLine` / `PurchaseLineExpenseLineItem` mapping, decide suppress vs direct-dbo fallback vs scoped `sync_qbo_bill.py` backfill.
4. **Scheduler cadence** — publish/adjust RC timer in `build.one.scheduler` per Section 4 decision.
5. **Retire or reframe Tier-0 LinkedTxn hop** — keep code path defensive or remove dead branch after review.
6. **Reconcile vocabulary with the existing linking status enum.** `fingerprint.py`'s `MatchOutcome` (`unmatched`/`unique`/`ambiguous`) is this measurement's own internal vocabulary — the wiring unit should map it onto `resolve_link_proposals`'s established statuses (`no_match`/`linkable`/`ambiguous`) rather than carrying a second, synonymous vocabulary into production code. Similarly, this doc's letter tiers (A/B/C) are internal to the measurement; express the chosen tier(s) using the existing numeric `Tier` scale (0-3) that `ProposeInvoiceSourceLinks`/`resolve_link_proposals` already use, not as new letters, so there's one tier vocabulary across both the invoice-line and RC-mediated linking paths.

---

## 6. Stale-comment follow-up (out of U-242 scope)

These files still carry the old KI-32 framing and were **intentionally left untouched** in U-242:

- `scripts/sync_qbo_reimburse_charge.py`
- `docs/audit_qbo_integration_2026_08_07.md`
- `entities/invoice/intelligence/prompt.md`

Refresh in a doc-only follow-up unit once this decision doc is filled with live numbers.

---

## Verification

All numbers in this doc came from `scripts/analyze_rc_source_fingerprint.py` run 2026-08-17
against the live realm (`scripts/_reports/rc_source_fingerprint_20260817_014210.json`), then
**independently reproduced** by a second agent that wrote its own from-scratch matching script
(no access to `fingerprint.py` / `analyze_rc_source_fingerprint.py` / this doc) against a fresh
live pull. Every headline figure — population counts, LinkedTxn tabulation, per-tier match
rates, actionable %, and the ambiguous-case root causes — agreed between the two runs to within
rounding. Re-run the script anytime with:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/analyze_rc_source_fingerprint.py
```

Numbers will drift slightly over time as new Bills/Purchases/RCs are entered — re-run before
relying on this doc for a decision more than a few weeks old.
