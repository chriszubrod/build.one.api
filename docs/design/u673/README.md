# U-673 Phase A — cost-code suggestion for contract labor, from phrase precedent

Prod, read-only, **2026-10-29**. Harness: `scripts/u673_labor_coding_backtest.py`, with tests in
`tests/test_u673_labor_coding_backtest.py`. Dry-run is the default and prints population counts only;
`--run --out-dir docs/design/u673` scores and writes `u673_report_<stamp>.{md,json}`. Both outputs carry
ids, codes, counts and scores only, and both pass `assert_outputs_privacy_safe` (JSON strings must be
id/code tokens, and no note, description or reply text of 8 or more characters may appear anywhere in
either file) before the run reports success. Both files are written under temporary names, checked by the
guard, and renamed only after both pass. On any failure, including an interrupt, every temporary and
final file is removed.

| run | files (stamp) | flags | notes |
|---|---|---|---|
| R1 | `…20261029T182004Z` | `--run --out-dir docs/design/u673` | $0, no LLM; 1,785 labelled lines |

## Design (Gate 1, 2026-10-29)

- **Label:** `ContractLaborLineItem.SubCostCodeId` (not NULL, not a holding code) on a ContractLabor with
  status approved/completed. The legacy names ready/billed are also accepted, in case any row escaped the
  2026-07-02 rename.
- **Input text = rebuilt TimeLog notes.** The line's TimeEntry is
  `COALESCE(ContractLaborLineItem.SourceTimeEntryId, ContractLabor.SourceTimeEntryId)`. Lines a PM
  splits off in `PUT /{id}/bill` have no line-level `SourceTimeEntryId`, because
  `CreateContractLaborLineItem` has no such parameter, but their ContractLabor does: since migration 009
  there is one CL per TimeEntry. Take that TimeEntry's work-type `TimeLog.Note` rows, keep those on the
  line's `ProjectId` (notes are always scoped to the project; a split-off line on a project the worker
  never clocked into gets no text rather than another project's narrative), order by `ClockIn`, trim them, drop empty
  ones and join with `'; '`. This is the same rule `AggregateTimeEntryOnSubmit` uses. The stored
  `ContractLaborLineItem.Description` is **never** used as input, because `apply_reviewer_decision`
  overwrites it with reviewer text that can carry the code. The only place it is read is the leakage
  probe.
- **Excel-era lines** are lines where neither the line nor its ContractLabor has a `SourceTimeEntryId`.
  They have no input text. They form the `excel` segment and are
  left out of the headline.
- **As-of rule (lag 2 days):** a prior line counts only if its `LineDate <= query LineDate − 2`. When
  both lines also have a coded-at time, the prior must have been coded strictly before the query line.
  Coded-at is the first Review on the ContractLabor whose ReviewStatus is final and not declined. If
  either line has no Review row, only the date rule applies (the population table reports coverage).
  A line on the same ContractLabor never counts as a prior.
- **S0:** the vendor's dominant SCC (needs ≥ 3 priors), and a vendor × project variant that falls back
  to vendor-wide.
- **S1 (windows 3 / 7 / 14 workdays):** IDF-weighted Jaccard between the line's tokens and earlier coded
  lines on the **same project** inside the window, weighted by 1 / (1 + workday distance) and summed per
  SCC. Confidence is the top SCC's share of the summed score. If there is no evidence, S1 abstains to
  S0 vendor × project (source `fallback:*`). Tokens come from the U-691 bill tokenizer minus a local
  labor stop-list. The shared tokenizer is unchanged.
- **S2:** split the note on `;`, newline, `,` and the word `and`, then run S1 on each segment. If two or
  more segments resolve to different SCCs, predict an N-way split, with hours in proportion to segment
  counts. A split counts as correct for a line only if the line's code is in the split **and** every
  code in the split appears in the actual group. The actual group is every line of a TimeLog-era
  ContractLabor that shares (ContractLabor, Project, LineDate), split-off lines included. `line_correct`
  and `split_metrics` read the same group truth, which `run_backtest` computes once.
- **S3:** parse `<number> <label>` from the reply. The number resolver mirrors the
  `FindSubCostCodeForReply` strategies in confidence order: exact number (1.00), right segment padded to
  two digits (0.95), and exact pipe-delimited alias (0.90). Two strategies are not mirrored:
  substring-alias (0.80) and substring-name (0.75). On a numeric hint they are the sproc's weakest paths,
  and matching names is the label resolver's job. The label resolver
  takes the SCC name with the best token Jaccard, if that score is ≥ 0.5 and no other name ties it. When
  the label resolver finds a match, it wins. In a labor context, a `* Materials` code maps to its
  `* Labor` sibling when one exists. The truth for a reply is the single distinct SCC on its parent:
  the ContractLabor's lines, or the Bill's line items. A parent with more than one distinct SCC is
  counted as ambiguous and not scored.
  - **Crew dedupe:** one PM reply codes every crew member (U-210), and each member gets a Review row with
    the same Comments. Labor replies are therefore deduplicated on (whitespace-normalised, lower-cased
    Comments, project, work date) before scoring. Both the raw and the deduplicated counts are reported.
  - **Two views, because the truth is circular:**
    - `all_parsed`: the parent's current SCC was usually written by the incumbent number resolver acting
      on that same reply, so the number resolver is favoured by construction.
    - `truth_differs_from_number`: replies where the current SCC is not the number resolver's pick,
      which is evidence that a human corrected it. This is the only correction view.
    - `ContractLaborLineItem.ModifiedDatetime` is **not** used as a correction signal, and is not
      selected. Bill generation (`bill_service.py`, around lines 643-656) rewrites every line of every
      billed ContractLabor through `update_by_id(..., bill_line_item_id=...)`, and
      `UpdateContractLaborLineItemById` sets `ModifiedDatetime` every time. "Modified after the review"
      would therefore mostly mean "billed". A view based on it was built in fix round 1 and then removed.
- **Windows:** evaluation runs 2026-06-01..2026-10-15. The threshold is fitted on calibration only
  (2026-06-01..2026-08-15, TimeLog-sourced rows): it is the lowest value in 0.50..0.95 whose
  confident-wrong rate is ≤ 5 % with **at least 20 calibration rows shown** at that threshold. It is then measured on the **holdout** 2026-08-16..2026-10-15, so no
  evaluation row affects the choice. 2026-09-16..30 is reported as a named slice.
- **Phase B bar:** S1 or S1+S2 must beat S0 (vendor × project) top-1 on TimeLog-sourced lines by
  ≥ 15 points, with confident-wrong ≤ 5 % at the fitted threshold. If fewer than 20 holdout rows are
  shown, the verdict is `insufficient_support`, never a pass. The number of rows shown is reported
  beside every verdict.

## Population

| | count |
|---|---|
| labelled lines (approved/completed CLs, Jan 2 – Oct 26 2026) | 1,785 |
| TimeLog-sourced / with a non-empty note | 1,763 / 1,663 |
| of which PM split-offs (TimeEntry only on the CL) | 440 |
| Excel-era | 22 |
| with a coded-at time | 574 |
| projects / vendors | 36 / 14 |
| eval-window lines (2026-06-01 .. 10-15) | 804 (782 TimeLog-sourced) |

## Results — top-1 over all eval lines (abstain = wrong), TimeLog-sourced

| signal | top-1 | top-3 | coverage | hours-weighted top-1 | 9/16–9/30 top-1 (n=107) |
|---|---|---|---|---|---|
| S0 vendor | 56.4 | 64.2 | 99.1 | 60.6 | 28.0 |
| S0 vendor × project (baseline) | 57.0 | 69.8 | 99.1 | 60.2 | 43.9 |
| S1 phrase precedent, 3-day window | 63.9 | 69.9 | 99.1 | 69.7 | 51.4 |
| **S1 phrase precedent, 7-day window (best)** | **65.2** | 73.0 | 99.1 | 71.8 | 51.4 |
| S1 phrase precedent, 14-day window | 63.9 | 74.3 | 99.1 | 71.2 | 45.8 |
| S1+S2 (split detection), 3 / 7 / 14 days | 62.5 / 62.4 / 61.5 | 69.9 / 72.9 / 73.9 | 99.1 | 67.8 / 67.5 / 66.7 | 49.5 / 49.5 / 50.5 |

Has-note lines (749): best S1 64.1 vs baseline 55.5 (+8.6). Empty-note lines (33) score 90.9 on every signal
(they fall back to vendor × project). Excel-era (22) score 100 on every signal (tiny, single-vendor).

## Project history depth (TimeLog-sourced)

| signal | depth 0 (21) | 1–9 (37) | 10–49 (150) | 50+ (574) |
|---|---|---|---|---|
| S0 vendor × project | 100.0 | 91.9 | 54.0 | 54.0 |
| best S1 (7-day) | 100.0 | 91.9 | 60.0 | 63.6 |

S1's lift comes from long-running projects (50+ prior lines), where vendor history is diluted across trades.

## Fitted threshold — holdout

No threshold on the grid (0.50–0.95) kept confident-wrong ≤ 5 % with ≥ 20 calibration rows shown, for any
signal — so no confidence gate could be fitted (holdout n = 282). Confident-wrong at fixed bars for best S1
(7-day, TimeLog): 24.8 % at 0.60, 22.4 % at 0.70, 20.9 % at 0.85.

## Phase B verdict — NOT EARNED

| candidate | top-1 (TimeLog) | lift over S0 vendor × project (57.0) | confident-wrong at fitted threshold | verdict |
|---|---|---|---|---|
| S1 3 / 7 / 14 days | 63.9 / 65.2 / 63.9 | +6.9 / **+8.2** / +6.9 | no threshold fits | insufficient_support |
| S1+S2 3 / 7 / 14 days | 62.5 / 62.4 / 61.5 | +5.5 / +5.4 / +4.5 | no threshold fits | insufficient_support |

Bar (owner, Gate 1): ≥ +15 points over vendor history and ≤ 5 % confident-wrong. Best lift is +8.2, and
no signal reaches a confident-wrong rate anywhere near 5 %.

## Leakage probe (stored Description as input)

Swapping the rebuilt TimeLog notes for the stored (reviewer-overwritten) Description moves TimeLog-sourced
top-1 by +0.1 to +2.9 points (largest: S1 14-day, 63.9 → 66.9). The stored descriptions carry a little
extra signal, not the answer; the rebuild rule costs almost nothing and stays.

## S2 — split detection

Eval window: 708 (CL, project, date) groups, 50 real splits. Predicted splits were mostly wrong:
precision 27–37 %, recall 16–70 % depending on the window, set-correct on predicted splits 0–13 %,
hours-weighted correct on real splits 28–39 %. The 9/16–9/30 slice (26 real splits in 73 groups) is the
exception at the 14-day window (precision 100 %, recall 88 %, set-correct 30 %) — the period the hand
review drew its examples from. S2 lowers line top-1 by about 1–3 points everywhere; it does not earn a place.

## S3 — label over number

| corpus | view | raw / deduped | scored | number resolver | label over number | disagreements |
|---|---|---|---|---|---|---|
| labor | all_parsed (circular) | 487 / 225 (143 parsed) | 84 | 92.9 | 92.9 | 0 |
| labor | truth_differs_from_number | | 6 | 0.0 | 0.0 | 0 |
| bill | all_parsed (circular) | 261 / 261 (91 parsed) | 91 | 97.8 | 97.8 | 0 |
| bill | truth_differs_from_number | | 2 | 0.0 | 0.0 | 0 |

In 175 scored replies the label and the number never resolved to different codes, and on the 8 replies a
human later recoded, neither resolver was right. The four hand-found cases (`65.1`, `99.0`, `13.2`,
`34.00 Materials`) did not recur in stored replies. Changing the shared `FindSubCostCodeForReply` has no
measured benefit — do not build the sub-unit on this evidence.

## Conclusions

- **Phase B (live labor suggestion chips): do not build as specified.** Phrase precedent beats vendor ×
  project history by +8.2 points (65.2 vs 57.0), half the +15 bar, and no confidence threshold keeps
  confident-wrong under 5 % — so a single prefilled suggestion would be wrong about one time in four.
- **What the data does support:** top-3 accuracy of 73–75 % for S1. A "pick one of three" picker
  ordering (the right code is in the first three for three lines in four) is the only use that clears a
  practical bar; it would need its own Gate 1 as a picker-ranking change, not an auto-suggestion.
- **Split detection (S2) and label-over-number (S3):** no value on this data. Drop both.

## Limits

- IDF is computed once over every input document, including later ones. It uses no labels, but it is
  not as-of.
- Labels are each line's **current** SCC. A recode after approval is not replayed.
- S2's segmenting is deliberately crude: "doors and windows" splits in two. Hours are split by segment
  count, not by clock time.
- **S3's truth is mostly the incumbent resolver's own output.** `all_parsed` therefore overstates the
  number resolver. Only `truth_differs_from_number` measures against human-corrected codes, and it may
  be small. It also misses a correction where the human happened to pick the same code the number
  resolver would have chosen. Line `ModifiedDatetime` cannot fill that gap, because billing bumps it on
  every billed line (see Design).
- Crew dedupe needs a single project per ContractLabor. For a multi-project CL the key's project is
  NULL, so its replies dedupe only with other replies that also have a NULL project.
- S3 takes the first `<number> <label>` in the reply body, so quoted history after it is ignored. The
  labor-context Materials→Labor swap is a heuristic built from a single known case.
- Lines with no coded-at time fall back to the date-only rule, which may let a same-week prior through
  when coding happened out of order.
