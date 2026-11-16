# U-797a — Contract Labor coding suggestion: shadow prediction + live scorecard

Status: **Gate 1 APPROVED 2026-11-16 (Chris)** — ship bar for U-797b confirmed: ≥ 40 scored crew-days and exact ≥ 64 % on suggested days. Parent U-797. Next: U-797b (email shows the suggestion,
"Approved" applies it), U-797c (one reply-to-review binding across review types).

## Decisions already made (Chris, 2026-11-16)

- Bring the Bill coding process to Contract Labor: prediction, then the Bill-style email, then "Approved"
  applies the suggestion.
- **Bar = bill-level**, not the 2026-11-06 "≥ 80 % exact" rule. U-776 is superseded.
- Days that look like several tasks are **shown with a "may need splitting" flag**, not hidden.
- **Shadow first**: this unit stores predictions and measures them; nothing a person sees changes.

## Goal

For every submitted Contract Labor crew-member day, store one suggested SubCostCode (or an abstain) and a
split flag, then measure live how often the PM's final code matches. The numbers decide when U-797b ships.

## Non-goals

No change to the Contract Labor email, the reply path, the edit page or any line's SubCostCode. No QBO
write. No prefill.

## Unit of prediction

One **crew-member day per project** = `(ContractLaborId, ProjectId)`. Since migration 009 there is one
ContractLabor per TimeEntry, so this pair is unique. It is keyed on the pair rather than on
`ContractLaborLineItemId` because a PM splitting a day in `PUT /{id}/bill` deletes and recreates lines.

## The ladder (CL v1)

Mirrors the bill ladder's shape (history if strong → model → blank), reusing measured parts only.

1. **History rung (no LLM).** `ReadContractLaborSubCostCodeCounts` (the U-749 picker's sproc: labelled
   statuses only, holding codes excluded, 2-day lag, own entry excluded). It is **strong** when the vendor ×
   project has ≥ 3 priors and the top code holds ≥ 60 % of them. That is the bill ladder's
   `HISTORY_STRONG_RAW_SCORE`, here as a share. In that case the history code is the suggestion and no
   model is called. The threshold is not yet measured for labor; the scorecard reports this rung separately.
2. **Model rung: Sonnet with the U-750 prompt.**
   - Input: the day's TimeLog notes, rebuilt exactly as `AggregateTimeEntryOnSubmit` and U-673 do.
   - Context: up to 30 precedent crew-days on the same project within ±10 workdays, already coded before
     submit time.
   - Candidate codes: the codes in that precedent **plus the vendor's top 3 (U-749)**. The vendor top 3 is
     new, and is the only unmeasured change: without it, a new project like OL-PH has an empty catalog and
     always abstains. The shadow measures it.
   - Output (unchanged from U-750): `single` / `split` / `abstain`, with codes and hours.
   - The suggestion is the single code, or the code with the most hours on a split.
3. **Split flag (U-751 rule).** Flag when Sonnet answered `split`, OR the arm-A note matcher resolves ≥ 2
   distinct codes, using its (τ, m) frozen from the U-751 calibration run.
4. **Blank** when history is weak and the model abstains, is unavailable or returns an invalid code.

**Example (Weston Parker, OL-PH, 11/11, 5.0 h):** OL-PH has no history for his crew, and his vendor-wide
history is 65.02 Misc Labor on 18 of 33 lines (55 %). Both are weak, so the model rung runs on "Helped
the crew with crates of pavers. Ran to HD", with his top 3 (65.02 / 62.00 / 8.11) as the only candidates.

## Mechanics (bill pattern)

- **Table `dbo.ContractLaborCodingSuggestion`** (new; `dbo.CodingSuggestion` is per document, drives the
  live coding queue and has a hard-coded TargetKind CHECK, so shadow rows stay out of it).
  - Keys: `ContractLaborId`, `ProjectId`, unique together; `WorkDate`; `VendorId`.
  - Status and versioning: `Status` (pending/done/failed/skipped), `ModelVersion` (`u797a-cl-v1`).
  - The suggestion: `DecisionSource` (history/model/none), `SubCostCodeId`, `Confidence`, `MaySplit`,
    `ModelProposal` (single/split/abstain), `ModelSplitCodes` (ids only).
  - Rung inputs: `HistoryTopSubCostCodeId`, `HistoryShare`, `HistoryPriorCount`, `ArmACodeCount`.
  - Call record: `Model`, `FailureReason`, `CallDatetime`, plus Created/Modified.
  - **No note text or reply text is stored**: ids, codes and numbers only.
- **Enqueue:** in `ReviewService.create`'s Contract Labor branch, right after `enqueue_drafts`, insert one
  `pending` row per (CL, project). This is failure-isolated and never raises, so the email cannot be
  delayed or altered. Behind `CL_CODING_SHADOW_ENABLED` (unset = off).
- **Tick:** `POST /admin/contract-labor/coding/tick` (X-Drain-Secret, limit 1), driven by a scheduler timer
  every 2 minutes (`0 */2 * * * *`, one row per POST, like `bill_coding_text_tick`). It claims pending rows,
  runs the ladder and completes them. A row whose own ContractLabor is already labelled is completed as
  `skipped` (`own_cl_coded`) with no model call. Claim order: never-claimed rows first (`ClaimedDatetime IS
  NULL`), then oldest `CreatedDatetime`, so a row released after a failure cannot starve the queue. The
  15-minute lease and the 24 h stale sweep are unchanged.
- **Attempts and the three-strike rule (fix round 2).** `AttemptCount` (INT, default 0) counts failed model
  attempts. A policy deferral (cap, stopped, headroom) releases with no attempt counted. A failed attempt
  (the model returns no answer, or any exception while processing the row: inputs, IDF, gates, call, parse)
  releases the row with `AttemptCount + 1` and stops the tick. On the third strike (`MAX_ATTEMPTS = 3`) the
  row is completed as `failed` with `FailureReason` `model_unavailable` or the exception class name. The
  daily call count (`CountContractLaborCodingCallsToday`) counts a row once a day when it has a model call
  or an attempt today, so a row with two attempts counts once. Pre-model infrastructure failures count as
  attempts too.
- **Gates, before the model rung only:** history answers complete first, with no model call, even when a gate
  is closed. Before each row's model call the tick checks, per row: the Claude day is NOT stopped; today's
  spend is under 70 % of the live cap (`SPEND_HEADROOM_FRACTION`); today's calls are under the daily cap. A
  gate that is closed defers the row and the tick continues to the next claim. An unavailable model releases
  the row and stops the tick.
- **Spend:** Sonnet goes through the existing transport, so it is metered into `dbo.LlmDailySpend` and gated
  by the live $6.45 Claude cap. Shadow work has its own daily call cap (default 60) and stops at 70 % of the
  cap, so it never starves live bill coding. Expected volume is about 10–25 crew-days per working day; the
  cost is measured on the first week's rows.
- **Final capture:** no write hook. The scorecard reads `ContractLaborLineItem.SubCostCodeId` at read time
  for CLs in a labelled status (approved/completed; legacy ready/billed), the same rule as U-673. That
  covers reply-coded and web-coded lines alike.

## Scorecard

`GET /admin/intelligence/cl-coding-scorecard?since=` (system admin), with a pure builder reusing
`wilson_interval` and the U-744 report shape. Per crew-day:

- **Final code set:** the distinct line codes on that (CL, project).
- **Single-coded day:** *exact* when the suggestion equals the one final code.
- **Day the PM split:** the split flag is *right*; a single suggestion counts as not exact.

Reported: coverage (suggested ÷ finalized), exact % with a 95 % interval, confident-wrong %, split-flag
precision and recall, all broken down by `DecisionSource`. Availability failures are excluded from the
rates, as in U-744. Because nothing is prefilled, these are **independent** accuracy numbers, unlike the
bills scorecard, which measures acceptance.

**Ship bar for U-797b (confirmed by Chris 2026-11-16):** at least 40 scored crew-days, and exact ≥ 64 % on
suggested days. 64 % is the bill ladder's backtest.

## Build, review, rollout

- **Repos:** api (table + sprocs, service, tick endpoint, scorecard), scheduler (one timer). Worktrees
  `u-797a` in each.
- **Tier: non-P0.** It writes only its own table, with no coding, QBO or auth write. It does spend against
  the live Claude cap, so Pass 1 checks the cap gating explicitly.
- **Pipeline:** the full per-unit pipeline. Mutation testing on the decision function: each rung, the
  strong threshold, the split OR, and the cap skip.
- **Rollout:**
  1. Apply SQL after the drift check.
  2. Deploy api and scheduler with the flag OFF.
  3. Turn the flag ON at Gate 2.
  4. Read the scorecard after about 2 weeks; that reading decides U-797b.
- **Rollback:** unset `CL_CODING_SHADOW_ENABLED`. Rows already stored are inert.

## Builder deviations (U-797a build)

- Precedent window is +/- 14 calendar days (the sproc), not +/- 10 workdays, so the sproc needs no calendar. Equivalent in practice.
- The history rung counts the vendor x project scope only (no vendor-wide fallback), per the design wording.
- Model-rung Confidence is NULL (the U-750 prompt has no confidence field), so confident-wrong counts history-rung rows only. Flagged for Chris.
- Added `ReleaseContractLaborCodingSuggestion` (returns a capped claim to pending), beyond the brief list.
- Shadow inputs carry the full catalog (non-holding SubCostCodes), not only referenced codes.
- **Arm A IDF (review fix round 1).** The IDF is the broad labelled corpus, one document per labelled TimeLog-sourced line
  (`ReadContractLaborCodingIdfCorpus`, rebuilt in Python), built once per UTC day per worker and cached in-process. It is
  NOT computed over the precedent days. This matches the U-750 backtest. Evidence is merged per (precedent day, code),
  with 0-hour lines dropped, as `precedent_groups` does.
- **Leak guard.** The inputs sproc excludes precedent lines dated on the query WorkDate (U-750 `evidence_visible`),
  and a row whose own ContractLabor is already labelled is skipped. The 24 h stale sweep guards staleness only.
- **Scorecard.** Final code sets count only lines with Hours > 0. Availability failures (`model_unavailable`,
  `claude_unavailable`) are excluded from every rate, not counted as model answers.
- **Hook placement.** The shadow enqueue sits after, and outside, the drafts try block. A drafts failure still enqueues the shadow.
