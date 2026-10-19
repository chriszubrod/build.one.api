# U-676 — Bill drop-to-prefill

**Status:** design approved by Chris 2026-10-16. Build order: U-676a (measure) → U-676b (API) → U-676c (web) → U-676d (cleanup).
**Extends:** U-639 (cost-code prediction), U-653 (history pre-fill), U-654/U-654b (text tier), U-674 (untouched pre-fills block submit/complete). It does **not** add a second suggester.

## Problem

The working process is: the person entering a bill picks `00.02 Suspense` as a placeholder, submits it for review, and the reviewer replies with the correct SubCostCode. The U-654b text tier does predict a code from the attachment, but it can never be used:

- a suggestion only fills an **empty** line, and a Suspense line is not empty;
- suggestions only render in BillEdit, while reviewers code in the iOS queue or by email reply;
- on `/bill/create`, a dropped file is only previewed — nothing is uploaded or extracted until Save/Submit/Complete.

Measured 2026-10-16: 14 of 14 manual bills with a text prediction were coded `00.02 Suspense`; the predictions were plausible for the vendor (confidence 0.85–0.98) and never shown.

## Goal

When a PDF is dropped on `/bill/create`, pre-fill **Vendor, Bill Date, Sub Cost Code (predicted), Description and Amount** (and Bill Number) from the file. The predicted code replaces the Suspense placeholder, so the reviewer receives a real code to confirm or correct through the existing review flow.

## Decisions (Chris, 2026-10-16)

1. **Measure first** (U-639 D5): no build of the read path until a backtest picks the extraction method.
2. **Header fields need explicit acceptance**, like the cost code: vendor, bill date, amount (and bill number, description) filled from the file are suggestions until accepted or edited; Submit and Complete wait for that.
3. **Invoice text may go to an LLM** under the U-535g masking (the same masking U-654b already relies on), with its known gaps documented — this extends U-658's W-9-only scope for LLM field extraction to bill invoices.
4. **Waiting on drop is fine:** the form shows "Reading invoice…" for roughly 5–20 s and fills fields when the result returns.

## Flow

1. **Drop:** upload the PDF immediately (`POST /api/v1/upload/attachment`, existing).
2. **Read:** a new API call reads that attachment synchronously:
   - text from the PDF's own text layer when present, else Document Intelligence;
   - mask TINs and persist the extraction blob through the existing writer (`_persist_extraction_blob`), so the later text tick and any re-extraction reuse it rather than paying again;
   - extract header fields: vendor (matched to `dbo.Vendor`), bill number, bill date, amount, description;
   - predict the cost code (and project when none is chosen): history first, the U-654 text cascade where history is weak/cold or there is no project — the same rule the tick uses.
3. **Web:** show "Reading invoice…" without blocking input; when the result arrives, fill only fields that are still empty or untouched; mark each filled field "from file" (the code keeps its AI badge); Submit and Complete stay disabled until every file-derived field is accepted or edited (Accept-all covers them).
4. **Server:** the cost code is enforced server-side by U-674 (`CodingPrefillState`). Header-field acceptance is client-side in v1 (no new columns); server-side recording is a follow-up.
5. **Suspense** stops being the default. With no prediction the code stays empty and the user picks one (Suspense included) as today.

## U-676a — the measurement (first)

A read-only offline bake-off on ~200 completed bills that have a PDF, ground truth from the finished bill:

| Field | Truth | Score |
|---|---|---|
| Vendor | `Bill.VendorId` | exact vendor match after resolution |
| Bill number | `Bill.BillNumber` (exclude `QBO-%` placeholders) | normalized exact |
| Bill date | `Bill.BillDate` | exact date |
| Amount | `Bill.TotalAmount` | within $0.01 |
| Description | first line's description | reported, not gated (free text) |

**Arms:**
- **A — rules:** Document Intelligence layout output through the existing `BillExtractionMapper` (`entities/bill/business/extraction_mapper.py`, orphaned since U-656, never measured).
- **B — LLM:** the extraction text (masked, U-535g) through a structured-output cascade task returning the same fields, with catalog-only vendor resolution (model output mapped to `dbo.Vendor` by the same matcher, never trusted as an id).

**Also measured:** time per document (text-layer vs DI path, cascade latency p50/p90), cost per document, and how often the text layer is enough to skip DI.

**Sample:** recent completed bills (e.g. last 6 months), stratified by intake (manual / email / QBO), one PDF per bill, privacy-asserted outputs (ids and scores only, no names or values).

**Run gates:** dry-run default; `--run` against prod is a Gate 2 with `--max-cost-usd` (expected < $10); interruptible and resumable (`timeout -s INT`, per-call timeout, `--resume-from`, per the long-run rules).

**Verdict:** per field, pick the arm with higher top-1 accuracy; a hybrid (e.g. rules for amount/date, LLM for vendor/description) is allowed. A field below a usable bar (proposed: 80% exact) is not pre-filled in v1.

## Later units

- **U-676b (API):** the synchronous read endpoint on the winning method, Bills-module RBAC, per-call timeout, daily call cap shared with the text tier, TIN-masked, no field values in logs.
- **U-676c (web):** upload on drop, the reading state, fill-only-empty/untouched, "from file" markers, acceptance gate on Submit/Complete, error and timeout fallbacks (fall back to manual entry silently).
- **U-676d (cleanup):** remove attachments uploaded on drop but never linked to a bill (blob + row), after a grace period.

## Out of scope

- iOS / email reviewer surfaces showing the suggestion (the prediction now reaches reviewers as the line's code).
- Expense and BillCredit drop-to-prefill.
- Server-side recording of header-field acceptance.

## U-676f — Claude-first cascade policy (2026-10-18)

Production routing for bill text coding and bill header extraction:

- **Claude first** on promotional credit (Haiku 5.5 first rung for both tasks).
- **Azure Foundry is fallback-only** (`fallback_only` rungs): cash models run only when Claude did not return a valid result (error, timeout, unparseable JSON, or — since U-676n — parsed JSON that fails the task validator, e.g. a code outside `candidate_sccs`). A valid answer below the confidence bar does **not** escalate to Foundry.
- **Daily Claude spend cap** (default `$200/31` ≈ `$6.45` UTC day, `dbo.LlmDailySpend`). When cap is reached, Claude rungs skip and **Foundry fallback rungs do not run** (no cash spend for the rest of the UTC day). Legacy ladders without `fallback_only` (e.g. `DEFAULT_LADDER` for agents) still run non-Claude rungs.
- **Out-of-credit breaker**: HTTP 402 / `billing_error` / credit-balance 400 opens a 60-minute process-local block; Claude rungs skip with `claude_unavailable:credit` and Foundry fallback may run. One warning per block, no response bodies in logs.
- **Rollback**: `CASCADE_LADDER_BILL_CODING_SUGGESTION=foundry:DeepSeek-V4-Flash,foundry:gpt-5.4-nano,foundry:gpt-5.4-mini` and `CASCADE_THRESHOLD_BILL_CODING_SUGGESTION=0.85` restores pre-U-676f bill text behaviour.
- **Process-local** breaker and 30s spend-read cache (each gunicorn worker is independent).
- **Fix round 1 (2026-10-18)**: the budget gate and spend metering live in `AnthropicTransport.stream` (every Claude caller, including `CascadeTransport` and direct agent pins). `run_ladder` only enforces `fallback_only` (skip Foundry when an earlier attempt returned `claude_unavailable:daily_cap`). Spend reads fail closed after 10 minutes without a good DB row; writes retry via an in-process unsaved buffer. The bill text tick returns `capped` without claiming or finalizing when the daily cap stops work.
- **Fix round 2 (2026-10-18)**: HTTP 402 / credit-balance 400 and partial SSE usage always run through `post_call_metering` (one `finally` per HTTP attempt; retries do not double-meter). Claude routed via `ANTHROPIC_FOUNDRY_BASE_URL` is still gated and counted against the same Claude daily cap (conservative; not configured in prod). Spend writes hold a process lock and flush per-day unsaved buffers; an ambiguous write (committed then connection dropped) can double-count spend in DB (over-count is the safe direction). Unknown Anthropic model ids are costed at Sonnet 5.5 rates. `CASCADE_*` and `CLAUDE_DAILY_SPEND_CAP_USD` env values are parsed as strings so invalid values cannot crash `Settings()`.

## U-676b — as built (2026-10-18)

- **Endpoint:** `POST /api/v1/bill/read-attachment` (`entities/bill/api/read_attachment_router.py`), registered in `app.py`.
- **Flag:** `BILL_DROP_PREFILL_ENABLED` (default off). Off → envelope `status: disabled`, no extraction or model calls.
- **RBAC:** `Modules.BILLS` / `can_create`. Fresh uploads only — linked attachments return generic 404.
- **Link gate:** `dbo.ReadAttachmentLinkState` (`entities/attachment/sql/dbo.attachment_link_state.sql`) via `AttachmentRepository.read_link_state` — one row `(IsLinked, IsTaxpayer)` across all attachment link tables (incl. `ms.DriveItemAttachment`). DB errors fail closed: treat as linked (404) and taxpayer (aggressive masking).
- **Timeout:** `BILL_DROP_PREFILL_TIMEOUT_SECONDS` (default 25) via `asyncio.wait_for`; blocking I/O on `asyncio.to_thread`.
- **Text:** `AttachmentExtractionService.extract_for_bill_drop_prefill` — invoice masking unless taxpayer-linked; persists U-535g extraction JSON; reuses current completed blob. If background extraction holds `processing`, the service polls the row every 1 s (off the event loop) until completion or the same timeout budget → `timeout`.
- **Header:** `run_bill_header_extraction` on masked text; vendor match via `vendor_name_resolver` (60s vendor-list cache); per-field confidence from cascade (vendor = min(cascade, match score)).
- **Coding:** History-first when request includes `project_public_id` and history raw ≥ 0.60; else `run_bill_coding_suggestion` Mode A/B. Response `coding` matches `POST /bill/coding/suggest` (`_suggestion_to_api_payload`).
- **Budget:** Claude daily cap → `ai_unavailable` (no Foundry spend); no separate text-tier daily call counter on this path.
- **Orchestration:** `entities/bill/business/read_attachment_service.py` (`run_bill_read_attachment_with_timeout`, `AttachmentNotReadable` → 404).
- **Background extraction:** `_extract` / `_fail` match origin/master; bill-drop race recovery and masking-aware reuse live only in `extract_for_bill_drop_prefill` / `_bill_drop_*` helpers.
- **Ownership:** `ReadAttachmentLinkState` filters by `@ActorUserId`, `@CompanyId`, `@IsSystemAdmin`. `dbo.Attachment.CompanyId` defaults to `1` and `CreateAttachment` does not pass it today — multi-company uploads would need that threaded (no code change in this round).
- **Text coding:** shared `predict_text_coding` + `bill_suggestion_from_text_cascade`; API payload via `presenters.suggestion_to_api_payload`.

## U-676h — as built (2026-10-18)

- **LLM header schema:** `short_description` (≤60, truncated at a word boundary), `long_description` (≤200, same truncation), and `reference` (≤60, PO/Job/work-order — not the invoice number), plus existing vendor/bill number/date/amount/confidence. The cascade prompt asks for the new keys; the validator still accepts legacy `description` and treats it as `long_description` when `long_description` is absent (stored U-676a backtest rows keep working).
- **Short line description:** `entities/bill/business/description_format.py::format_short_description` — collapse whitespace, strip trailing punctuation, keep the first five space-delimited words, Title Case (first character of each word upper-cased, remainder unchanged so tokens like `3/4"`, `PVC`, `2x4`, `LLC`, `O'Neil` survive).
- **Read-attachment `fields`:** seven keys always present — `description` (formatted short line), `long_description`, `reference`, plus vendor/bill_number/bill_date/total_amount. Short text comes from `short_description`, else the formatter is applied to `long_description`. Per-field confidence matches the other header scalars. Bill memo composition (short + long + reference) is owned by the web unit (U-676c).
- **U-676a bake-off:** Arm B/C description scoring reads `long_description`, falling back to legacy `description`.

## U-676k — as built (2026-10-19, fix r1 2026-10-19)

- **Short line description — no filler tail:** The header cascade prompt tells the model to name the thing bought or work done (e.g. "Poplar S4S Lumber", "Biweekly Mowing") and not append generic Purchase, Order, Invoice, Bill, Payment, Charge, Fee, and similar generic tails unless that word is the item (e.g. "Delivery Fee"). `format_short_description` backstops **only** a trailing `purchase` / `purchases` (case-insensitive) before the five-word cut and **again after** the 60-character word-boundary cut (so a filler cannot survive as the last token post-truncation). Never strips a one-word input (e.g. `"Purchase"` stays). Real item names such as "Change Order", "Electric Bill", and "Down Payment" are not altered by the backstop.
- **Drop path cost code — measured combined rule (true-project history):** On `run_bill_read_attachment` when the request has **no** `project_public_id`, run the text cascade (Mode B) **first** to obtain an accessible **project P** (`retain_accessible_project` keeps P when the model’s SubCostCode is unresolved or a holding code). Then **one** `BillCodingSuggester.suggest` keyed on **project P** (not the vendor’s dominant project). If that history row is strong (history source, SubCostCode present, raw **≥ 0.60**), the **SubCostCode** comes from history on P (`source`, confidence, and reason match P); **project** is P. If history on P is weak, the model’s SubCostCode wins with project P. If the model yields no accessible project, fall back to **no-project** vendor history: use it only when strong, else the model’s SubCostCode alone with no project. When the coding model is daily-capped or errors, header fields are kept; strong no-project history still returns `coding` with `status: partial`, otherwise no coding. With a **chosen** project, behaviour is unchanged: one pre-model `suggest` on that project; strong history skips the model. The background text tick (`U-654b`) is unchanged.
- **Why combined on drop:** U-654a Haiku backtest — text-only top-1 **41.2%** vs history **54.2%**; combined **55.0%** (+0.8 over history) when history is evaluated on the **true** project. Haiku project prediction **94.8%** (Foundry 94.2%), so text-first project + history-on-P for SubCostCode matches measured production behaviour.

## U-676l — as built (2026-10-19)

- **Timing:** one INFO line per pipeline completion that reaches `_finish` — `bill_read_attachment.timing` with integer ms for `attachment`, `link_state`, `extract`, `header_llm`, `vendor_match`, `history`, `catalog`, `coding_llm`, `payload`, `total`, plus `status`. Only the attachment internal id is logged (no vendor names, bill text, or public ids).
- **Step order:** attachment read → link gate → start catalog prefetch + optional chosen-project access task → extraction → await project task (fail-fast before header LLM) → header Haiku → vendor match (sync, timed) → coding branch when vendor id known. Mode A (chosen project): history suggest + eligibility; strong history skips the text model and **cancels** the catalog prefetch; weak history awaits catalog then `predict_text_coding`. Mode B (no chosen project): await catalog, text model, then U-676k history merge. Text-tier daily call cap is checked only inside `predict_text_coding` (once per request when the model runs).
- **Fail-fast project access:** when `project_public_id` is present, resolve + `assert_can_access_project` run after extraction succeeds and **before** `run_bill_header_extraction` (inaccessible → 404, zero model calls).
- **Concurrency & cleanup:** after the link gate passes, catalog load and (when requested) chosen-project resolution run in parallel with extraction and the header Haiku call. A `try`/`finally` on the drop path always cancels and drains unfinished prefetch tasks (catalog, project) so early exit, outer `wait_for` timeout, header/coding exceptions, and non-`AttachmentNotReadable` project failures do not leak pending tasks. `_await_or_cancel_tasks` re-raises `CancelledError` when the current task is being cancelled (outer timeout).
- **De-duplication:** one `BillCodingSuggester.suggest` per request on the drop path (after Mode B when no project is chosen, keyed on the model’s project P; on the chosen-project path, before the model as today) — passed into `history_eligible_for_text` as `precomputed_suggestion` when a project was chosen (tick unchanged). Vendor internal id + name come from `VendorResolveMatch` (vendor list cache carries id); no post-match `VendorService.read_by_public_id` or `_read_vendor_name` on the drop path when id is known. Prefetched catalog is passed into `predict_text_coding`.
- **Catalog cache (drop path only):** 60 s in-process TTL on `load_catalog_from_db`, keyed by `as_of` truncated to the minute, at most **one** cached entry (replaced on each miss). Thread-safe. The background text tick does **not** use this cache — it calls `_load_catalog_from_db` directly via its loader hook.

### Follow-ups (deferred)

- `ExtractionPolicy` object; vendor-resolver / mapper consolidation + fuzzy matcher to `shared/`; `_VendorPairCache`; vendor cache keyed by company.

### Round 3 (2026-10-18) — metering order and operator notes

- **Metering happens before the terminal event.** `AnthropicTransport.stream` records spend and opens the
  credit breaker *before* it yields `Done` or a `TransportError`, exactly once per request, so a consumer that
  stops reading on that event (e.g. `CascadeTransport`) already sees the breaker open for its next rung. Paths
  with no terminal event (truncated stream, exception, consumer close) meter what was seen in `finally`.
  A metering failure is logged and never replaces the answer — a finished Claude reply never falls back to Foundry.
- **Spend writes never hold the lock across the DB call.** Amounts move unsaved → in-flight under the lock, are
  written without it, then settled; readers take the same lock, so a gate check never misses an amount in
  motion (in-flight spend counts toward the cap until its write settles). Unsaved spend
  from an earlier UTC day is retried under its own `SpendDate`.
- **Settings** `CLAUDE_CREDIT_BREAKER_MINUTES` (1–10080, default 60) and `CLAUDE_SPEND_CACHE_SECONDS`
  (≥ 0, default 30; 0 = read every call) are parsed leniently: a bad value logs one warning and uses the default.
- **Operator note — lowering `LlmDailySpend` by hand.** Each worker keeps the *largest* total it has seen for the
  day (totals only ever grow), so a manual downward correction does not take effect in running workers until the
  UTC day rolls over or the API restarts.
- **Known over-count:** a write that commits but whose reply is lost is retried and counted twice (safe direction).

## U-676m — persist intake predictions (2026-10-19)

**Goal:** When `POST /api/v1/bill/read-attachment` runs, store header + coding predictions on `dbo.BillIntakePrediction` (one row per attachment, re-read overwrites). Bind to the bill on create; snapshot **final** header + attachment-line coding on the draft→completed flip; reuse matching predictions in the U-654b text tick; expose predictions on the edit page; report accuracy offline.

### Data model

- Table `dbo.BillIntakePrediction` — predicted fields, cascade metadata (models, confidences, tokens, cost, timing JSON), optional `Final*` columns after complete. `RejectReason` is a placeholder (nothing writes it in U-676m).
- Sprocs: `UpsertBillIntakePrediction` (MERGE on `AttachmentId`), `BindBillIntakePredictionToBill`, `RecordBillIntakePredictionFinal` (once guard on `FinalizedDatetime`), `ReadBillIntakePredictionByBillId` / `ByAttachmentId`, `ReadBillIntakePredictionsForAccuracy`.

### Write paths

1. **Read (failure-isolated):** After the read payload is assembled, a single upsert runs; exceptions are logged without driver text and do not change the HTTP body. Persist for every non-`disabled` status once the attachment passed the link gate (including `timeout` / `unreadable` / `ai_unavailable`). Optional request field `intake_source` (`pdf_drop` default; `pdf_upload` | `list_drop`).
2. **Create (failure-isolated):** After `BillLineItemAttachment` is linked, `BindBillIntakePredictionToBill` sets `BillId` where still null.
3. **Complete (failure-isolated, draft→completed only):** Alongside `record_bill_coding_suggestion_on_save`, `RecordBillIntakePredictionFinal` captures vendor, bill number/date/total, and SubCostCode/Project from the line that carries the PDF (else first line).

### Text tick reuse

Before blob download, `_process_one_claim` loads the intake row by `attachment_id`. Reuse (complete with `record_call=False`, no model) when `ExtractedDatetime`, text `ModelVersion`, status `ok`/`partial`, `PredSubCostCodeId`, and coding mode/chosen project match the claim.

### API

- `GET /api/v1/bill/{public_id}/intake-prediction` — `Modules.BILLS` `can_read` + bill access guard; returns predicted fields only (no cost/tokens/timing/final). 404 when inaccessible or absent.

### Accuracy

- `scripts/u676m_prediction_accuracy.py` — read-only markdown report from `ReadBillIntakePredictionsForAccuracy` (per-field match rates, overall, top vendors, mean cost/latency). Apply SQL base file to prod before first report.
