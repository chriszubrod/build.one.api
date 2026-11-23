# U-797b1 — Contract Labor suggestions in the review email, with a wait and a snapshot

Status: built on `unit/u-797b1`, pending the review pipeline and Gate 2 for the flag. Parent U-797. Follows
`docs/design/u797a-cl-coding-shadow.md` (the shadow prediction, Gate 1 approved 2026-11-16). Next: U-797b2, where a
bare "Approved" applies the suggestion.

## What b1 does

All of it rides `CL_SUGGESTIONS_IN_REVIEW_ENABLED` (own flag, default off). The shadow flag is already on in prod and only produces the rows. Flag off is trunk's email.

1. **The email shows the suggestion.** The crew email (one per project and date) becomes one table: Worker, Hours,
   Notes/Description, Suggested code, Note. The suggested cell is `NN.NN Name` only for a completed row with a
   history or model source, a SubCostCodeId, and not a holding code. A split shows `(split?)` and lists the
   candidate codes in Note. Everything else shows `no suggestion`, with a Note saying why (holding, failed,
   skipped, pending at send time).
2. **The email waits for the suggestion.** With `CL_CODING_SHADOW_ENABLED` on, the (project, date) email is held
   while any suggestion row for that project is `pending` and the crew's latest submit is under
   `CL_SUGGESTION_WAIT_MINUTES` (30). The flag off, or no rows, means no wait, which is today's behaviour.
3. **The claim records what was shown.** Every crew member's target row stores a snapshot of exactly what their
   email row showed, plus a fingerprint of their lines at compose time. b2 reads it back to decide what a bare
   "Approved" may apply.
4. **The ask changes.** `Reply "Approved" to apply the suggested codes, or reply with a sub cost code to apply it
   to the whole crew.` The subject is unchanged. Nothing applies a code in this unit.

## Order and timing

- **Order.** `ReviewService.create` enqueues the shadow rows before `enqueue_drafts`. The wait only sees rows that
  exist, so the enqueue has to come first. The shadow enqueue is still failure-isolated and outside the drafts
  try, so it can never delay or alter the email.
- **Hold marker.** Only when `CL_SUGGESTIONS_IN_REVIEW_ENABLED` is on and the email is actually held is a
  `dbo.ContractLaborReviewHold` row written for the (project, date). Holding the day again resets it (new wait,
  0 attempts, not given up). A successful claim deletes it; `PruneContractLaborReviewHolds` removes markers whose
  day already has a claim.
- **Re-fire.** When the shadow tick settles a row, and the crew's counted members (submitted, with a submitted line
  on the project) have no pending suggestion, a MARKED (project, date) is fired once (`enqueue_drafts` for the
  work date). Unmarked days are never fired. The claim de-dups, so a double fire sends once.
- **Sweep.** Every tick, whatever the flags, `ReadContractLaborReviewHoldsDue` returns markers held within 7 days
  that have not given up. A marker is due when it has been held longer than the 30-minute wait or its crew has
  settled, and never while any ContractLabor on the date is still pending_review (that fire could not send, so it
  is neither attempted nor counted). Each fire counts an attempt; the fifth that leaves the marker in place gives
  up and logs an error (ids only). Turning the flag off releases every marked hold on the next tick.
- **Timeout.** A row still pending at the timeout goes out as `suggestion pending when sent`, with no code.

## The snapshot

Columns on `dbo.ReviewNotificationTarget` (nullable, one guarded ALTER): `SuggestedSubCostCodeId`, `SuggestionId`,
`SuggestionSource` (NVARCHAR(50), widened in round 2), `SuggestionMaySplit`, `LinesFingerprint`.

| Email showed | SuggestedSubCostCodeId | SuggestionId | SuggestionSource | SuggestionMaySplit |
|---|---|---|---|---|
| clean code | code | row id | history / model | 0 |
| split, with a top code | top code | row id | history / model | 1 |
| split, no code | NULL | NULL | NULL | 1 |
| no suggestion (abstain) | NULL | NULL | NULL | 0 |
| holding code | NULL | NULL | NULL | 0 |
| failed, skipped, pending, no row | NULL | NULL | NULL | NULL |

`LinesFingerprint` is `cl_lines_fingerprint(pairs)`: SHA-256 hex over the sorted `lineItemId:hours` strings, joined
by newlines. Hours are a Decimal string, and a missing value counts as `0`. It is order-independent, and it is the
one definition b2 reuses to detect lines that changed after the email.

Bill, Expense and BillCredit targets leave every snapshot column NULL.

## Why b2 can apply only clean rows

A bare "Approved" in b2 applies a code only when the snapshot shows a clean code (not split, not holding, not
pending), and only when the recomputed fingerprint still matches the stored one. A split or a no-suggestion row
needs the reviewer to name a code. That keeps the snapshot honest in both directions: it records what was shown,
and b2 acts only on what it can prove was shown and is still current.

## Decisions and deviations

- **Submit time** is `ContractLabor.ModifiedDatetime`. The status flip to `submitted` stamps it. There is no
  dedicated submit timestamp. A later header edit on a crew member bumps it and extends the wait, which is safe
  but worth knowing.
- **Read failure.** If reading the suggestions fails, the email goes without suggestions, with no wait. The
  alternative (hold on a read failure) would stop the email for as long as the read is broken. This is logged.
- **Billable and overhead** are table columns with the flag on (fix round 1). They are not in the flag-off email.
- **Markers (round 2).** A held (project, date) is marked in `dbo.ContractLaborReviewHold`. The mark is written only when `CL_SUGGESTIONS_IN_REVIEW_ENABLED` is on and the email is actually held (an upsert, idempotent). If the mark cannot be written, the email goes now rather than being held with no way to release it. The marker is deleted when the claim succeeds. `PruneContractLaborReviewHolds` removes markers whose day already has a claim, such as a flag-off send.
- **Sweep (round 2).** Runs on every tick whatever the flags, outside the claim loop's error path. It acts only on marked holds. A marker is due once it has been held longer than the wait, or once the crew has settled. The sweep is bounded to markers held within 7 days. It fires each work date at most once per tick. Every fire of a marker counts an attempt (`RecordContractLaborReviewHoldFire`), except a fire that deleted the marker. The fifth non-deleting fire sets `GaveUpDatetime`. The marker is then no longer fired, and an error is logged with ids only (no ReconciliationIssue recorder exists for this area, so log only).
- **Completion re-fire (round 2).** A settled crew day whose (project, date) is marked is refired once and the attempt recorded. An unmarked day is never fired.
- **One pending rule (H2).** `crew_members` (submitted, with a line on the project) and `crew_day_settled` decide the hold, the due check and the re-fire alike.
- **Rollout and rollback.** Governed by `CL_SUGGESTIONS_IN_REVIEW_ENABLED` (Settings `cl_suggestions_in_review_enabled`, default off, strict). Flag off is trunk's email. Flag off also releases any marked hold on the next tick. `CL_CODING_SHADOW_ENABLED` only produces the suggestion rows.
- **Pending at timeout** renders as `no suggestion` with a note. It is not a code, so b2 cannot apply it.
- **Deploy order.** The SQL must apply before the api deploys. The repo's AddReviewNotificationTarget call now
  passes five more params, and the new read sprocs are called from the tick and the email.

## Rollout

1. Apply the SQL: the guarded ALTER, the widened `AddReviewNotificationTarget`, and the three read sprocs
   (`ReadContractLaborCodingSuggestionsByProjectAndDate`, `ReadReviewNotificationTargetSnapshots`), and the hold table with its six sprocs (`dbo.contract_labor_review_hold.sql`).
2. Deploy api and scheduler with the flag off. The email then shows `no suggestion` everywhere, with no wait.
3. Turn `CL_SUGGESTIONS_IN_REVIEW_ENABLED` on at Gate 2. The wait and the suggestions go live together. Shadow rows come from `CL_CODING_SHADOW_ENABLED`, which is already on.
4. Rollback: unset `CL_SUGGESTIONS_IN_REVIEW_ENABLED`. The email goes back to no suggestions and no wait. Snapshot rows
   already written are inert.

## Tests

`tests/test_u797b1_pure.py` (the hold, the display states, the fingerprint), `test_u797b1_email.py` (the table, each
state, escaping, the ask), `test_u797b1_gate.py` (the wait, the timeout, flag off, the snapshot, the double fire),
`test_u797b1_tick.py` (the re-fire, the sweep, attempts, flag off), `test_u797b1_structure.py` (the order, the repo
and record service, and the SQL house rules).
