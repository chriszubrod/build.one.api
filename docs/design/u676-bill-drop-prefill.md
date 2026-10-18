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
- **Azure Foundry is fallback-only** (`fallback_only` rungs): cash models run only when Claude did not return a parseable result (error, timeout, unparseable JSON). Low confidence after a successful parse does **not** escalate to Foundry.
- **Daily Claude spend cap** (default `$200/31` ≈ `$6.45` UTC day, `dbo.LlmDailySpend`). When cap is reached, Claude rungs skip and **Foundry fallback rungs do not run** (no cash spend for the rest of the UTC day). Legacy ladders without `fallback_only` (e.g. `DEFAULT_LADDER` for agents) still run non-Claude rungs.
- **Out-of-credit breaker**: HTTP 402 / `billing_error` / credit-balance 400 opens a 60-minute process-local block; Claude rungs skip with `claude_unavailable:credit` and Foundry fallback may run. One warning per block, no response bodies in logs.
- **Rollback**: `CASCADE_LADDER_BILL_CODING_SUGGESTION=foundry:DeepSeek-V4-Flash,foundry:gpt-5.4-nano,foundry:gpt-5.4-mini` and `CASCADE_THRESHOLD_BILL_CODING_SUGGESTION=0.85` restores pre-U-676f bill text behaviour.
- **Process-local** breaker and 30s spend-read cache (each gunicorn worker is independent).
- **Fix round 1 (2026-10-18)**: the budget gate and spend metering live in `AnthropicTransport.stream` (every Claude caller, including `CascadeTransport` and direct agent pins). `run_ladder` only enforces `fallback_only` (skip Foundry when an earlier attempt returned `claude_unavailable:daily_cap`). Spend reads fail closed after 10 minutes without a good DB row; writes retry via an in-process unsaved buffer. The bill text tick returns `capped` without claiming or finalizing when the daily cap stops work.
- **Fix round 2 (2026-10-18)**: HTTP 402 / credit-balance 400 and partial SSE usage always run through `post_call_metering` (one `finally` per HTTP attempt; retries do not double-meter). Claude routed via `ANTHROPIC_FOUNDRY_BASE_URL` is still gated and counted against the same Claude daily cap (conservative; not configured in prod). Spend writes hold a process lock and flush per-day unsaved buffers; an ambiguous write (committed then connection dropped) can double-count spend in DB (over-count is the safe direction). Unknown Anthropic model ids are costed at Sonnet 5.5 rates. `CASCADE_*` and `CLAUDE_DAILY_SPEND_CAP_USD` env values are parsed as strings so invalid values cannot crash `Settings()`.

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
