# AGENTS.md — build.one.api

Guidance for coding agents operating in this repo (Cursor Composer executor, Codex reviewer, Claude Code orchestrator). The **Review guidelines** section below is what the Codex review stage is pointed at.

## Review guidelines

This is the rubric the **Codex** reviewer is pointed at (installed into each repo as
`AGENTS.md`, or referenced directly). It encodes build.one's real, recurring failure
modes so the review is grounded in this codebase, not generic lint. Keep it in sync with
the umbrella `CLAUDE.md` "Common Bug Patterns" section.

## Priorities (in order)
1. **Correctness / financial safety** — anything touching money, GL coding, QBO, billing,
   invoices, remittance, or bill/expense math is P0 until proven safe.
2. **Data loss / multi-user state bleed** — deletes without cascade handling; per-user or
   per-Company scoping mistakes; auth context not set.
3. **Concurrency / races** — optimistic concurrency, auto-save/complete/delete ordering.
4. **Test adequacy** — does the change carry the tests its risk warrants?

## build.one financial-safety checklist (P0 if violated)
- **Money is `Decimal(str(...))`, never `float`.** Any float in money math is P0.
- **Sproc `UPDATE` NULL-overwrite:** an unconditional `SET col = @col` clobbers optional
  None fields. Optional columns need a `CASE WHEN @col IS NULL THEN col ELSE @col END`
  guard (as `IsDraft` has). Flag unguarded optional updates.
- **QBO mapping integrity:** `sync_to_qbo_bill` must write `BillLineItemBillLine` mappings
  or `sync_from_qbo` duplicates lines. `qbo.*.Id` ≠ `dbo.*.Id` — never alias a QBO PK as a
  plain `BillId`. Customer invoices are created in **QBO first**.
- **FK has no CASCADE DELETE:** children must be nullified/deleted first (e.g.
  `InvoiceLineItem.BillLineItemId` before `BillLineItem`; blob → Attachment → link → line).
- **Optimistic concurrency** = ROWVERSION base64; a write that ignores the concurrency
  token is a lost-update bug.

## Correctness / multi-user (P0–P1)
- Auth context: outbox workers and CLI/sync paths must `set_authz_context(is_system_admin=True)`.
- Per-Company / per-User scoping: list paths must isolate by `UserProject`/`CompanyId`;
  `IsSystemAdmin` bypasses module+Company checks — make sure that bypass is intended.
- Auto-save races: Complete must `await autoSave()` flush; Delete must set `isSaving=true`
  before canceling the timer.
- No variable shadowing in bill generation (`scc_amount`); always unpack `(result, reason)`.

## API / contract
- API responses use the `{"data": …}` envelope.
- Router → service → repo(stored procs) → SQL Server layering; business logic doesn't leak
  into routers.

## What NOT to flag
- Pre-existing issues outside this diff's lines (note them at most as P3 context).
- Style/formatting with no behavioral or safety consequence.
- Missing tests for pure refactors that a behavior-preserving pass made.

## Severity contract (must match the schema)
- **P0** must fix — correctness or financial safety. Blocks.
- **P1** should fix before merge — realistic malfunction.
- **P2** follow-up. **P3** nit.
The orchestrator's bounded fix loop acts on **P0 and P1** only; be deliberate about that line.
