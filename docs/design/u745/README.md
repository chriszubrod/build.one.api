# U-745: M0 decision model wired into the bill-coding ladder V2

## What it does

- **Shadow on every V2 decision.** On every V2 decision (the PDF drop and the background tick),
  the U-698 **M0** model scores the candidates. M0 is pure Python with no LLM. It stores its pick
  and its calibrated probability next to the filtered similar-bills vote, on both rows:
  `BillIntakePrediction.M0SubCostCodeId / M0Confidence` and
  `BillTextCodingSuggestion.M0SubCostCodeId / M0Confidence`. The columns mirror the Similar* vote
  columns (`BIGINT`, `DECIMAL(5,4)`). A tick that reuses the drop's row copies the drop's M0 pick.
  The shadow changes no behaviour.
- **The switch.** `bill_coding_m0_rung_enabled` (env `BILL_CODING_M0_RUNG_ENABLED`, default `False`)
  controls the last rung of `choose_final`.
  - When it is on and M0 answered, M0's pick replaces the vote at that rung. Its `DecisionSource`
    / `CodingSource` is `"m0"`.
  - When it is on and M0 is silent, the vote applies as before.
  - When it is off, the ladder is unchanged.
  - History, Haiku, Sonnet and Foundry are untouched either way.
  - M0 never sets a project.
  - An `"m0"` prefill is presented like the vote ("Suggested from similar past bills").
  - Both CHECK constraints now allow `'m0'`. A re-run of the SQL upgrades an older definition.
- **Scorecard.** The scorecard has a new rung, `m0`, scored on every V2 row. A NULL pick counts as
  an abstain.
  - V2 rows written before U-745 carry no pick, so they score as abstains.
  - **Coverage note:** the drop never waits for M0. A drop-row shadow is therefore also NULL when
    the M0 reads lose the race, mostly on the Mode A history short-circuit (no cascade to overlap
    with) and on cold label counts.
  - Those rows count as m0 abstains, so the drop-row rate understates M0. The intake row's
    `M0SubCostCodeId` is NULL on them while the tick row may hold a pick.
  - Compare `m0` with `similar_vote` on the rows where M0 actually ran before reading too much
    into abstains.

## Owner rules (Gate 1)

- M0 abstains whenever the filtered vote has no neighbours. It never prefills from weak history
  alone.
- There is no confidence bar. The stored confidence is the isotonic-calibrated probability, while
  candidates are ranked on the raw probability (`rank_candidates`).
- Any M0 failure means no pick, plus a WARNING that logs the exception type only. Failures include:
  - a model file that is missing, corrupt or has a mismatched feature list (fails closed, logged
    once);
  - a read error or a read timeout (5 s);
  - a `LeakageError`;
  - the reads not being ready in time.

  In every case the decision is the same as without M0.
- **M0 is off the critical path.** Its two reads start as a task beside the decision's other reads.
  Nothing waits for them before the cascade. They are consulted only at `choose_final`, after the
  cascade or the Mode A history short-circuit.
  - **The drop never waits** (by design). It uses M0 only if the reads
    have already finished. Otherwise M0 is absent for that decision, logged at DEBUG, so the drop
    can never miss its coding deadline because of M0.
  - **The tick** waits up to `M0_WAIT_TICK_SECONDS` = `serve.M0_READ_BOUND_SECONDS` = 5 s (the same bound sets the read timeout and the label-count follower wait).
  - A read that is late is shielded, not cancelled: it finishes and warms the cache. If it is
    late, the shadow is NULL, and with the switch on the vote stands.
- **The company label counts load once at a time.** The load is single-flight per company.
  Concurrent callers wait, for at most 5 s, for the one in-flight scan instead of starting their
  own.

## Train/serve parity

Live serving reuses the backtest's feature code (`build_context`, `m0_features`, `LogisticModel`,
`rank_candidates`) through `decision_model/serve.py`. The shipped weights are
`decision_model/m0_model.json`, copied from `docs/design/u698/u698_model_20261029T185458Z.json`.
The parity test is `tests/test_u745_m0_parity.py`. It checks that features, candidates, raw
probabilities and the pick are identical. Its population is seeded into SQL Server by
`tests_sqlserver/test_u745_decision_model_priors_sql.py`.

| Input | Training | Live |
| --- | --- | --- |
| Prior bills | U-653b `_LOAD_SQL` (non-draft, ≥1 coded line; label = dominant code by \|amount\|, tie → lowest line id; project = that code's lowest line; amount = Σ\|amount\| of all lines), strictly earlier, never self | `ReadVendorPriorBillsForDecisionModel` (same CTE), re-guarded by `AsOfIndex` |
| `has_pdf` (prior) | the bill had PDF text | **proxy:** a BLIA attachment with an `AttachmentTextTokenHash` row of the current tokenizer, not a TaxpayerAttachment (the corpus join) |
| `base_rate(c)` | share of all strictly-earlier labelled bills | `ReadSubCostCodeLabelCountsForDecisionModel` per company, cached ~1 h in-process; **no as-of cut** |
| Query amount | Σ\|line amount\| of the coded bill | `abs(extracted total)` (drop) / `Bill.TotalAmount` (tick) |
| Query project (Mode A) | the bill's labelled project | the chosen project |
| `has_text` / `pdf_token_count` | PDF token set present / its size | query token hashes present / their count |
| Neighbours | rolling word neighbours, catalog-filtered | the same scoped, catalog-filtered list `filtered_vote` votes over |
| `choosable` | `Choosable` (as-of, no holding, no Suspense, no ambiguous number) | `serve.catalog_choosable` (the `apply_similar_evidence` rule over the as-of catalog); agreement pinned by the parity test |

## Accepted approximations

- **`has_pdf` proxy.** A prior counts as having a PDF when the corpus would hash it.
- **Base rates are not cut by as-of.** They include bills coded after the query was created. The
  live query is new, so the difference is at most the bills completed since the cache filled.
- **Query amount.** Live uses the extracted (drop) or stored (tick) total, not the coded lines'
  Σ|amount|.
- **No company in training.** Training ran over the whole population, a single company in
  practice. Live reads are filtered by `Bill.CompanyId`.
- **Neighbour token cap.** Live similar-bills neighbours use `similar_bills_max_tokens_per_bill`,
  which keeps the 50 highest-IDF tokens on both the query and the prior (see the corpus build and
  `_scored_neighbours` in `similar_bills_service.py`). Training used the full PDF token sets
  (u698 `neighbour_pass`, where `TokenSourceConfig` defaults to `max_tokens_per_bill=None`).
  - So `combined`, `match` and the top-10 set differ, and with them M0's F7–F10 inputs and its
    calibration.
  - The shadow is scored on real outcomes, so the live scorecard measures live M0 regardless.
  - **Switch-on bar:** on the same V2 rows, the live scorecard `m0` rung is at least as good as
    `similar_vote`.
  - Follow-up: re-measure M0 offline with the 50-token cap.
- **Base rate in detail.** Live base rates have no as-of cut and are company-scoped. Training
  counted strictly earlier bills with no company filter.
  - A tick that re-scores an older bill sees bills coded after it, and possibly its own label.
    The effect is tiny.
  - `tests/test_u745_m0_parity.py` pins that the uncut live counts change only `F15_base_rate`.
- **HasPdf cost.** The `HasPdf` EXISTS scans `IX_BillLineItemAttachment_AttachmentId`, because
  there is no `BillLineItemId` index. That is fine at today's size; an index is a follow-up.
- **Timed-out reads.** An M0 read that timed out still holds its worker thread and pooled
  connection until its query ends. This is bounded by the query, and the stage wait never blocks
  on it.

## SQL

- New file: `entities/coding_suggestion/sql/dbo.decision_model_priors.sql`. It holds two read-only
  sprocs and takes `@TokenizerVersion` for the `has_pdf` proxy.
- Changed files, with idempotent column adds and a CHECK upgrade:
  - `bill_text_coding_suggestion.sql`
  - `dbo.bill_intake_prediction.sql`, whose `ReadBillIntakePredictionsForAccuracy` appends
    `M0SubCostCodeId`, `M0Confidence`, `TextM0SubCostCodeId` and `TextM0Confidence`.
- **Gate 2 apply order:** `bill_text_coding_suggestion.sql`, then `dbo.bill_intake_prediction.sql`,
  then `dbo.decision_model_priors.sql`. Apply all three before the image deploys, because the new
  code always sends the `@M0*` parameters and calls the two new sprocs.
- **Pre-check before the CHECK upgrade.** Confirm that the existing values fit the new CHECK lists:
  - `SELECT DISTINCT CodingSource FROM dbo.BillIntakePrediction` ⊆
    {history, primary, fallback, similar_vote, m0, none, NULL};
  - `SELECT DISTINCT DecisionSource FROM dbo.BillTextCodingSuggestion` ⊆
    {history, primary, escalate, fallback, similar_vote, m0, reused_intake, none, NULL}.
