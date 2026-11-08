# U-551b-f1-f3b — adopt a written record after a first write crashed between POST and mark (DESIGN)

Repo read: `.worktrees/u-551b-f1-f5` (master + f5). Paths below are relative to it. Doc: `docs/design/u551b-f1-repoint-revision-model.md`.

## 1. The state, and telling it from a legitimate legacy row

Sequence: drain first write (`ConfirmationSource IN ('review_submit','confirm_endpoint')`) → connector POSTs the recode
(`…connector/expense/business/service.py:1046`, `update_purchase_raw`, pre-edit numbering) → process dies before `mark_written` (`worker.py:1397`). Outbox row is
reclaimed; retry hits `_resolve_recode_purchase_line_targets` (`worker.py:1102`) — staging has not been re-pulled, so the
placeholder ids still resolve → connector branch (a) (`service.py:~975-985`, pre-edit numbering) sees every spec already on the target item →
`{"status":"already_recoded","sync_token":live}` with NO `verified_lines` because `is_repoint_call` is False (`:931`,
`:1012-1016`). Worker `:1373-1397` takes `fresh_write=False`, non-repoint → `merged=None`, `sync_token=None` →
`MarkCodingSuggestionWritten` CASE-preserves both NULLs (`dbo.coding_suggestion.sql:847-848`) → **`written`, no record**.
Downstream: `_repoint_revision_for_candidate` (`entities/expense/business/service.py:1139-1147`) treats NULL as legacy
("nothing to repoint") → a later reviewer recode never reaches QBO; `_non_repoint_status_guard` (`…coding_suggestion/
business/service.py:1152`) answers `already_written`. (If a pull HAD run before the retry, staging is empty → `no_target_lines`
error — a different, already-covered state: cockpit confirm.)

| Signal | crash-gap row (f3b target) | legacy row |
|---|---|---|
| `WrittenAt` | ≥ `RECORD_REQUIRED_SINCE_UTC` (`shared/api/admin.py:871`, 2026-11-07 22:40Z) | before it |
| `ConfirmationSource` | `review_submit` / `confirm_endpoint` (never `complete_repoint`: a repoint always carries `verified_lines`) | any/NULL |
| `WrittenSyncToken`, `WrittenLinesJson` | both NULL | both NULL |
| live `SyncToken` vs `SyncTokenAtSuggest` | live = suggest+1 (exactly our one write) | anything |
| local `ExpenseLineItem.qbo_id` lines | each coded local line's live QBO line is ItemBased with ItemRef == `resolve_qbo_item_ref(local scc)` | often placeholder/other |

`ReadCodingSuggestionRecoveryCandidates` (`…sql:1351`, category `written_without_record`) already lists the first row class;
the sweeper surfaces it under `recovery.written_without_record` (`service.py:822`).

## 2. Candidate fixes

### A — PREVENT: let the first-write retry's `already_recoded` carry a record
- Connector `recode_purchase_lines` (`service.py:901`): today `verified_lines` is appended for EVERY spec that lands in branch (a)
  (`:959-965`) but only *emitted* when `is_repoint_call` (`:984-987`, `:1040-1042`). Change: when `not is_repoint_call` and
  `not pending_specs`, emit `verified_lines` + `sync_token=live` **only if** `live_sync_token == str(int(expected)+1)`
  (expected = `SyncTokenAtSuggest`, i.e. exactly one QBO update since our snapshot — ours). Otherwise return as today (no record).
  Also require, per spec with `project_id`, that the live line's `CustomerRef.value == _get_qbo_customer_ref(project_id).value`
  (`:1058`, `:1166`); a mismatch → no record (not an error: the write is not provably ours).
  Safe to compute: branch (a) already has `item_ref`, `sub_cost_code_id`, `target_qbo_line_id`; it IS the same triple the
  `written_result` builds (`:1028-1038`). No extra GET; no POST.
- Worker `:1373-1397`: non-repoint + `already_recoded` + `verified_lines` present → `merged = verified_lines` (REPLACE, not
  merge — a first write replaces, per the comment at `:1374-1377`), `sync_token=result["sync_token"]`. Absent → today's `None/None`.
- Flips two pinned tests: `tests/test_u551bf1_repoint_connector.py:165` and `tests/test_u551bf1_repoint_worker.py:212`
  (both assert the no-record behaviour); rewrite them to the +1 rule, keep a `live == expected+2` case asserting no record.
- Does nothing for rows already in the state (the sweeper's current list) — that is B.

### B — ADOPT: system-admin action that verifies the live purchase and writes the record
- Route: `POST /admin/expense-coding/suggestions/{public_id}/adopt-written-record`, `require_system_admin()`, UUID 422,
  200 `{"data":…}` / 404 `not_found` / 409 `detail=status` — clone of `shared/api/admin.py:903-927`.
- Service `CodingSuggestionService.adopt_written_record(public_id)` — body under `coding_suggestion_lock`
  (`service.py:78`, timeout `CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS`), gated by `recode_write_gate_reason()` like redrive (`:892`;
  a record write is a mark → I3). Steps: read → `status!='written'` → `not_written`; either half of the record set →
  `has_record`; `confirmation_source` not in (`review_submit`, `confirm_endpoint`) → `not_a_first_write`; `WrittenAt <
  RECORD_REQUIRED_SINCE_UTC` → `legacy` (pass the constant in, as the sweeper does); `ConfirmationSeq IS NULL` → `moved`
  (no fence value; never reach the sproc THROW). Read the local lines with a `qbo_id`
  (`ExpenseLineItemService.read_by_expense_id(item.expense_id)`), then GET `get_purchase_raw`
  (`…purchase/external/client.py:136`); a GET without `SyncToken` → `no_live_token`.
  **Verification rule (round 2 — the local line is NOT evidence).** The 15-min purchase pull projects the live
  ItemRef/CustomerRef onto the local line (`expense_line_item/business/service.py` ~:624-654), so after a pull
  "local coding == live" is true for ANY ItemBased line, a human's recode included. Therefore:
  - *Confirmed match (preferred, any source with a confirmed scc)* — verify against the CONFIRMED coding the drain
    wrote: item = `resolve_qbo_item_ref(item.confirmed_sub_cost_code_id, realm)` (`HoldingCodeRefused` →
    `line_holding`, None → `line_unmapped`), customer = `_get_qbo_customer_ref(item.confirmed_project_id)` when a
    project is confirmed (unresolvable → `line_not_recoded`). Exactly one of the local lines' qbo ids must be ItemBased
    on the live purchase carrying that pair → ONLY that line is recorded (`sub_cost_code_id = item.confirmed_sub_cost_code_id`);
    more than one → `ambiguous_lines`; zero → `confirm_endpoint`, or a `review_submit` with a SINGLE linked line, refuses
    (`line_not_recoded`, `line_missing` when a local qbo id is absent from the live document); only a
    `review_submit` with MULTIPLE linked lines falls to multi-line mode. A `confirm_endpoint`
    row without a confirmed scc → `no_confirmed_coding`.
  - *Multi-line `review_submit`* (no confirmed scc, or zero confirmed matches with ≥2 linked lines) — per-line coding exists only locally:
    check each local line with a coding against its own resolved item/customer (NULL-coded lines skipped; missing
    live line → `line_missing`; mismatch → `line_not_recoded`; zero verifiable → `line_not_recoded`).
  - **RESIDUAL (same sentence in the route docstring and the runbook §6):** the adopt cannot recover which lines
    were on the placeholder at write time (the drain wrote `confirmed_*` only to the placeholder PurchaseLine(s);
    the pull has since overwritten the local lines). The confirmed match is preferred because it under-claims at
    worst (a later repoint is refused `repoint_target_not_ours`, the same outcome as leaving the row), while
    multi-line mode may over-claim pre-categorized lines - the operator must verify EVERY recorded line was on the
    placeholder before our write. Leaving the row is safe (completion treats a NULL record as legacy).
  Build `written_lines = [{"qbo_line_id": str, "item_ref_value": str, "sub_cost_code_id": int}]` — exactly the keys
  `CodingSuggestion.written_lines` validates (`business/model.py:120-139`). Persist via
  `repo.adopt_written_record(public_id, live_sync_token, json, item.confirmation_seq)`.
- New sproc `AdoptCodingSuggestionWrittenRecord(@PublicId, @WrittenSyncToken NVARCHAR(50), @WrittenLinesJson NVARCHAR(MAX),
  @ExpectedConfirmationSeq INT)` in `dbo.coding_suggestion.sql` (own batch, one CREATE OR ALTER): `UPDATE … SET WrittenSyncToken=
  @WrittenSyncToken, WrittenLinesJson=@WrittenLinesJson, ModifiedDatetime=@Now WHERE PublicId=@PublicId AND Status=N'written'
  AND WrittenSyncToken IS NULL AND WrittenLinesJson IS NULL AND ConfirmationSeq=@ExpectedConfirmationSeq`; both params NOT NULL
  (THROW if NULL — never the CASE-preserve pattern); `WrittenAt`/`Status`/`ConfirmationSeq` untouched; returns the row via the
  shared SELECT block (0 rows → service `moved`). Also `COALESCE`-free: the guard IS the refusal set (`not_written`,
  `has_record`, `moved`) — the service pre-checks only to name the reason.

## 3. Invariants each must hold
- I1 (approval bound to its snapshot): neither records a confirmation; B fences on `ConfirmationSeq` so a revision recorded
  between the read and the UPDATE loses the adopt (`moved`). A: the mark is already seq-fenced (`worker.py:1310`).
- I2 (no revision under a drain): A runs inside the drain's lock; B takes the same lock, so no drain/confirm interleaves.
- I3 (gate = no mark): A inherits the gate at `worker.py:1273`; B checks `recode_write_gate_reason()` before the read.
- **I4 (`written` asserts QBO is as our record says):** the adopted token must be the token of the state that CONTAINS our
  write. A: the live token under the +1 rule is exactly post-write. B: the GET returns the current token *and* the lines it
  verifies in the same document, so `WrittenSyncToken` = the state in which the lines were checked; the seq fence + lock mean
  nothing of ours moved between GET and UPDATE. A human edit after the GET only bumps the token → the next repoint's fence
  (`service.py:932`) marks `changed_in_qbo`, exactly as for a normally recorded write. Never adopt `SyncTokenAtSuggest`.
- I5 (cannot compute ≠ nothing diverges): every unresolvable input is a named refusal, never an empty/partial record.
- I6 (only lines we wrote): A's +1 rule + ItemRef/CustomerRef equality; B's CustomerRef check + `legacy` cut-off + all-or-nothing.
  Residual risk (both): a human coded the identical item+customer in the single window — accepted, operator-confirmed in B.

## 4. Helpers to reuse
`_live_token_matches` (connector `service.py:882`) for the +1 compare (`str(int(expected)+1)`); `get_purchase_raw` (client `:136`);
`_get_qbo_item_ref` / `resolve_qbo_item_ref`, `_get_qbo_customer_ref` (`service.py:1138`, `:1166`); the line-lookup by `Id`
from `:945-949`; `model.written_lines` (`model.py:120`) as the round-trip check after building the JSON; `coding_suggestion_lock`,
`recode_write_gate_reason`, `read_by_public_id`; route shape from `redrive_error_repoint_router`. `verify_purchase_untouched`
(`:886`) is NOT reusable as-is (B has no expected token; it needs the raw document) — factor a `_get_purchase_raw(realm, id)`
out of it and call that from both. `_resolve_repoint_targets`/`_recode_target_from_local_line` are drain-side spec builders;
B only needs coding, not economics, so do not reuse them.

## 5. Tests and the mutants they kill
- A/connector (`test_u551bf1_repoint_connector.py`): live=expected+1 → `verified_lines` + live token (kills: gate stays
  `is_repoint_call`-only); live=expected+2 or ==expected → no record (kills: +1 rule dropped → adopts a human's write);
  CustomerRef mismatch with a project → no record (kills: CustomerRef check removed); mixed placeholder+target → still POSTs,
  unchanged (kills: early return before `pending_specs`).
- A/worker (`test_u551bf1_repoint_worker.py`): non-repoint `already_recoded` WITH `verified_lines` → `mark_written(sync_token=
  live, written_lines_json=verified)` REPLACING a previous record (kills: merge instead of replace; token left None); WITHOUT →
  `None/None` as today (kills: adopting an empty list / `[]` record).
- B/service (new `test_u551bf1f3b_adopt_written_record.py`, shape of `test_u551bf1f5_redrive_error_repoint.py:25-117`): gate off
  → `writes_disabled`, no read/GET; each refusal → exact status, `adopt` sproc never called, GET not called for pre-GET refusals
  (kills: refusal reordering); happy path → JSON that `CodingSuggestion(written_lines_json=…).written_lines` parses non-empty and
  token == GET token (kills: `SyncTokenAtSuggest` used; int scc as str); one of two lines on the placeholder → `line_not_recoded`
  and no write (kills: partial adoption); sproc returns None → `moved`; lock busy → `busy`.
- B/route: non-admin 403, drain-secret-alone 403, non-uuid 422, 200 envelope, 404, 409 statuses (clone `:139-190`).
- B/SQL static (`test_u551bf1_coding_suggestion_sql.py` pattern): proc declares the 4 params; WHERE has `Status = N'written'`,
  both `IS NULL`, `ConfirmationSeq = @ExpectedConfirmationSeq`, no tautology (`_update_where`, `:140`); SET is unconditional
  (no CASE); own batch (`:52`, `:74`). Kills: guard column dropped → overwrite of a real record; CASE-preserve regression.

## 6. Recommendation
Ship **A + B as ONE unit, SQL-first** (sproc → API image, as f5). B alone leaves the hole open and makes every future crash an
operator ticket; A alone strands the rows already listed under `written_without_record` and the `+1`/CustomerRef fall-throughs
it deliberately refuses — those fall-throughs are precisely B's input, so the two are one fail-closed design: A takes the
provable case automatically, B gives the operator the GET-verified rest. Both are small (connector ~15 lines, worker ~6,
service ~70, sproc ~40, route ~25) and share the verification rule (ItemRef + CustomerRef against the live document), so one
review pass covers the rule once. Gate 2 proof: run the adopt sproc's WHERE as a SELECT on prod against the sweeper's current
`written_without_record` ids before apply; A needs no backfill.

## 7. Step-0 citation re-verification and build notes (builder, 2026-11-08)
Every cited site was re-read on the f3b worktree: the behaviour matches the doc (branch (a) emits `verified_lines` only for
`is_repoint_call`; the worker's first-write `already_recoded` marks `None/None`; `MarkCodingSuggestionWritten` CASE-preserves;
`ReadCodingSuggestionRecoveryCandidates` lists `written_without_record`; the redrive route/service shape and
`RECORD_REQUIRED_SINCE_UTC` are as cited). Only line numbers had shifted (corrected above where they were off by more than a few).
Built as designed, with these specifics:
- The `+1` compare is `_token_is_exactly_one_ahead` (int-parse; unparseable expected token -> no record); `verified_lines` is emitted
  only when every spec in branch (a) also passes the CustomerRef check (a spec with a project whose mapped CustomerRef is
  unresolvable counts as a mismatch).
- Worker: only a NON-repoint `already_recoded` carrying a non-empty `verified_lines` adopts the live token and REPLACES the record.
  A repoint `already_recoded` keeps `sync_token=None` (unchanged).
- Connector gains `_get_purchase_raw` (static, read-only GET) shared by `verify_purchase_untouched` and a public `get_purchase_raw`
  used by the adopt service; the adopt path never calls `update_purchase_raw`.
- Service `adopt_written_record(public_id, *, record_required_since)`: the constant is passed in from the route (no import cycle).
  A NULL/unparseable `WrittenAt`, or a missing expense/purchase/realm id, fails closed (`legacy` / `line_not_recoded`).
  Success returns `{"status":"adopted","lines":n,"written_sync_token":t}`.
