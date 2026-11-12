# api TODO — closed sections, cut from `TODO.md`

Sections whose work is **fully closed**, cut out of `TODO.md` so the live backlog is
only live work. Nothing here needs action. History: `grep -n "U-<n>" docs/archive/`.

Cut 2026-11-11 (U-788). **Each section below was archived by reading its body**, not by
a checkbox count — "zero open checkboxes" turned out to mean "written as prose" far more
often than "done" (54 sections had zero open boxes; only these 9 were actually closed).

| cut | why it is closed |
|---|---|
| `U-549 C1` | RESOLVED 2026-09-29 — guard sproc is live; kept as an incident record |
| `U-503d/e residuals` | all three doors closed by U-078 |
| `CLOSED by U-528` | blob_url exfil path closed, verified by OpenAPI probe |
| `U-314 follow-up` | closed by U-425; stale prompt recipes rewritten |
| `U-238a migration not applied` | RESOLVED 2026-08-16; migration is applied, kept for the incident record |
| `U-120` | coi_parser live-ACORD calibration DONE |
| `U-081` | closed 2026-10-08, superseded by U-440a |
| `RESOLVED 2026-07-10` | master reconciled; feat/model-cascade since retired (branch verified gone) |
| `U-337 follow-up` | closed by U-340 |

---

## ~~U-549 C1 — `update_draft` guard sproc never applied~~ — RESOLVED 2026-09-29

`dbo.CountMsOutboxByEntityAndKind` **is live**, verified against `sys.objects` at U-567's Step 0. The
`update_draft` Kind is reachable; `enqueue_update_draft` no longer fails closed on a missing guard.

Kept as a record because it was rediscovered the hard way twice: the symptom is
`ms.outbox.update_draft.idempotency_guard_failed_enqueue_refused` + `return None`, which looks like a bug
and is actually a correct fail-closed on an unverifiable idempotency guard. If it ever recurs, the fix is
to apply `integrations/ms/outbox/sql/ms.outbox.sql` — purely additive, `CREATE OR ALTER`, idempotent.

---

## U-503d/e residuals — the quantity widening stopped short of three doors (booked 2026-09-25)

Shipped: `34a4ccfa` `06108842` `27c3da22` `281e956e` (bill) and `b2a6d018` (expense). The
column, sprocs, read path and primary REST endpoint now agree on `Decimal` for BOTH
entities. These three did NOT move, so a fractional quantity still 422s on them — and
now that everything around them agrees, they LOOK intentional. That is why they are here.

- [x] 🟡 **`line_quantity: Optional[int]` on both parents.** `entities/bill/api/schemas.py:70` — closed by U-078
  + `business/service.py:209`, and `entities/expense/api/schemas.py:67` + `business/service.py:152`.
  These are the one-shot "create the parent with an inline line" paths; they reject 5.25
  before the fixed line-item create is ever reached. Verified live on expense:
  `ExpenseCreate(..., line_quantity=Decimal("5.25"))` still raises.
- [x] 🟡 **Agent tool surfaces still `int`.** `entities/bill/intelligence/tools.py:290,557,659` — closed by U-078
  and `entities/expense/intelligence/tools.py:257,472,552`. They POST to the endpoints that
  were just widened, so **an agent still cannot write a fractional quantity on either
  entity.** Fails loudly (Pydantic v2 rejects 2.5→int rather than truncating), so it is a
  capability gap, not data loss. Per `feedback_agent_tools_are_a_consumer_surface`, a
  web+mcp grep would have missed these entirely.
- [x] 🟢 **Truthy guard on the QBO bill push.** `integrations/intuit/qbo/bill/connector/bill/business/service.py:1152` — closed by U-078
  — `qty = Decimal(str(line_item.quantity)) if line_item.quantity else None` drops a
  legitimate `Decimal("0")` to None. **Metadata only, NOT money**: `amount` is sent
  explicitly and the `qty*unit_price` fallback would have produced 0 anyway. Confirmed the
  only remaining truthy quantity guard in non-test code. Fold into the next touch of that file.

---

## ✅ CLOSED by U-528 (2026-09-24) — client-supplied `blob_url` exfil path

> **FIXED.** `POST /create/attachment` deleted (zero callers, verified across api incl. `entities/*/intelligence/`, web, mcp, ios, scheduler); `AttachmentCreate` removed; `blob_url` dropped from `AttachmentUpdate` and no longer forwarded by `PUT /update/attachment/{public_id}`. **`AttachmentService` deliberately unchanged** — `/upload/attachment`, both folder processors, the QBO attachable connector, the Box vendor service, the email bridge and `BillService._rename_invoice_blob_on_complete` all pass SERVER-DERIVED values and would break if the parameter were removed. The trust boundary is the request-body binding, not the parameter ([[feedback_trust_boundary_is_the_body_binding]]).
>
> **Closure verified by probe, not grep:** the live OpenAPI spec was built and every `$ref`/`allOf`/`anyOf` in every request body resolved; the only request-side blob field left in the entire API is a boolean (`upload_to_blob` on the MS linked-mail route). All nine sibling `*AttachmentCreate` schemas refute — they are link-table schemas carrying public_ids, none declares `blob_url`. Every remaining writer of `Attachment.blob_url` traces to `storage.upload_file(...)`.

---

## U-314 follow-up (qbo.CustomerProject retirement) — surfaced running OVH-02, 2026-09-04

- [x] **CLOSED by U-425 (2026-09-09) — the 2026-09-08 rewrite removed every retired-table recipe and U-425 verified the replacement text against code.** ~~`entities/invoice/intelligence/prompt.md` (InvoiceAgent playbook) Step 1 still queries the retired
  `qbo.CustomerProject` mapping table.** Its project-resolution SQL (`JOIN qbo.CustomerProject cp ON
  cp.ProjectId = ?` → `qbo.Customer`) and the Step 1 heal recipe (`CustomerProjectConnector().create_mapping(...)`)
  are stale post-U-314: the table is dropped, `dbo.Project.QboId`/`.RealmId` is the sole identity store now
  (stamped via `SetProjectQboIdentity`). Confirmed live-broken running the OVH-02 playbook session
  (2026-09-04): the literal Step 1 query raised `Invalid object name 'qbo.CustomerProject'`; worked around
  ad hoc by reading `dbo.Project.QboId`/`.RealmId` directly. Rewrite Step 1's project-resolution query as a
  plain `SELECT Id, PublicId, Name, Abbreviation, QboId, RealmId FROM dbo.Project WHERE ...` and its heal
  recipe as a direct `SetProjectQboIdentity`-style stamp (mirroring the U-355/U-361 entries' treatment of
  the analogous Bill/VendorCredit mapping-table retirements). Same "large, actively-used agent prompt,
  disproportionate content edit" reasoning as those entries — worth doing as one consolidated pass across
  all four stale sections (this one + the U-355 Bill-recovery entry + the U-361 VendorCredit entry + the
  U-363 Bill-fingerprint entry below) rather than four separate edits.

---

## ~~U-238a migration not applied~~ — **RESOLVED 2026-08-16, do not act on this**

> ⚠️ **This entry is HISTORICAL. The migration IS applied.** Verified directly against prod
> on 2026-08-16: `dbo.Bill.QboId`, `dbo.Invoice.QboId`, `dbo.BillLineItem.QboId` and
> `SetInvoiceQboIdentity` all exist. U-238b and U-238c SQL have since been applied on top,
> and the U-238c reference backfill stamped 7,145 rows with a clean 17-topology drift check.
> **The blanket "do not run local pull/connector scripts" warning below no longer applies.**
> Kept for the incident record only.

- [x] **(RESOLVED — see banner above) P0 for any local playbook/InvoiceAgent/script session — SQL-first deploy step from commit `00a3dec` was never run.** That commit added dual-write QBO-identity stamping (`Set<Entity>QboIdentity`) to the Bill/Expense/Invoice/Project/Company QBO connectors, on **both** create and update paths, gated on `scripts/migrations/238a_qbo_identity_headers.sql` (new `QboId`/`RealmId`/`SyncToken` columns + 5 new sprocs) landing FIRST — the commit message says so explicitly ("⛔ SQL-FIRST DEPLOY REQUIRED — do not build/deploy the container before applying..."). The migration was never run. Confirmed live: `dbo.Invoice` has no `QboId` column, `SetInvoiceQboIdentity` does not exist on prod.
  - **Blast radius:** any session running a local script from this checkout (`scripts/sync_qbo_{bill,invoice,purchase,vendorcredit}.py`, `--project` scoped pulls, direct connector calls per the InvoiceAgent playbook) against prod DB hits the missing sproc on the FIRST identity-stamp call and raises — for BOTH new-record CREATE and existing-record UPDATE paths, across all 5 entities.
  - **Deployed API/scheduler is currently UNAFFECTED** (confirmed: a Bill was cleanly created+mapped by the scheduler at 17:48 on 2026-08-13, after the commit landed) — the container hasn't picked up this commit yet, so periodic scheduled pulls are safe. The exposure is specifically local script sessions running against prod DB with the current checkout.
  - **Confirmed incident:** an OL-14 InvoiceAgent session's local `sync_qbo_invoice.py --project` pull hit this repeatedly, creating 3 orphaned zombie `dbo.Invoice` rows (header created, mapping+lines never reached, non-`ValueError` exception skipped the orphan-cleanup path) before being diagnosed. Cleaned up manually (deleted the 3 zombies; the invoice ultimately landed correctly via the scheduler's own unaffected pull and was renamed back to `OL-14` from an accidental `-4` suffix).
  - **Fix:** apply `scripts/migrations/238a_qbo_identity_headers.sql` (purely additive, `NOT EXISTS`-guarded — safe standalone) plus the 5 new `Set<Entity>QboIdentity` sprocs (brand-new objects in each entity's base SQL file — extract just those, don't blanket re-apply the base files, since Bill/Expense/Project/Company/Invoice aren't on the single-source-of-truth converted list and may carry other prod-drifted sprocs). Until then: **no local pull/connector script for Bill/Expense/Invoice/Project/Company should be run from this checkout against prod** — wait for the scheduler's own cadence instead (its deployed code is unaffected).

---

## 🟢 U-120 — `coi_parser` live-ACORD calibration (2026-07-22) — DONE

**DONE:** `_parse_policies_from_tables` rewritten to parse the DI **coverage-grid table by header-name column mapping** (INSR LTR / TYPE OF INSURANCE / POLICY NUMBER / POLICY EFF / POLICY EXP — indices differ per cert), grouping multi-row coverage blocks, dropping blank sections (no policy#/dates), carrier via INSR-LTR → INSURER A–F table; content-scan kept as fallback when no grid table. Sanitized DI fixtures (`tests/fixtures/acord_{gl_auto,wc}_di.json`, scrubbed from the real certs) + `tests/test_coi_parser_grid.py`; existing content-path tests unchanged. 543 tests green. **LIVE-validated on A&A's real GL+WC DI:** GL #03750825 exp 2025-12-14 + Auto #5575082500 exp 2025-12-14 (no spurious WC) and WC #UB-5W154448-25-42 exp 2026-02-24 (no spurious GL/Auto) — auto-parses exactly what previously needed hand-correction.

_Original problem (kept for provenance):_ Proven necessary during the A&A Masonry test case: on a REAL ACORD 25 (A&A's GL cert), `entities/certificate_of_insurance/business/coi_parser.py` mis-parsed the coverage table — put the **limit `1,000,000` into `policy_number`**, **missed EVERY `POLICY EXP` date** (they're plainly in the DI text as `12/14/2025`), invented a **spurious empty WC row** (matched the blank "WORKERS COMPENSATION" section header), and only half-mapped carriers. The raw DI `content` is clean — it's the **table-mapping / insurer-letter→carrier + limits-vs-policy#-column logic** that isn't calibrated. Ground-truth to calibrate against (A&A's GL cert, attachment `6A657092…`): GL insurer-A OWNERS #**03750825** eff 12/14/2024 exp **12/14/2025** $1M/$2M; Auto insurer-B Auto-Owners #**5575082500** exp 12/14/2025 $1M CSL; WC section BLANK (not a policy). Fixes: (1) read `POLICY EXP (MM/DD/YYYY)` per coverage row; (2) don't put limit amounts in `policy_number` (require a non-limit alnum token from the POLICY NUMBER column); (3) drop coverage rows with no policy#/dates/limits (blank ACORD section headers); (4) resolve carrier by INSR-LTR → INSURER A–F block. Re-test on A&A's GL + WC certs (both in the folder). Until then, COI ingests need human correction of the drafted policies.

---

## U-081 — MS Excel drain worker re-checks column-Z at drain time (2026-07-17, found during U-065 Gate-2) — closes the reclaim-overlap residual

The one side-effect NOT idempotent under overlapping completion runs. Column-Z (line-item public_id) is checked only at *enqueue*; Excel outbox rows never coalesce and the drain worker doesn't re-check, so two overlapping runs of the same completion — a reclaim overtaking a still-alive >30-min-hung task (U-065's N=1800s residual, practically unreachable but real) — can enqueue duplicate DETAILS rows. SharePoint (coalesce+replace) and QBO (mapping-check) are already overlap-idempotent; Excel is the only gap.
- [x] ~~Make the MS Excel drain worker re-check column-Z at drain time → true idempotency under overlap.~~ **CLOSED 2026-10-08 — superseded by U-440a (`d556af0a`, 2026-09-10), which shipped exactly this after the 2026-08-06 duplicate-rows incident; see `MsOutboxWorker._filter_rows_already_in_worksheet` + `tests/test_u440a_excel_drain_dedup.py`.** **Shared sensitive path** — touches every Excel sync (Box + SharePoint workbooks) and the insert row-index is position-dependent, so it needs its own verify + blast-radius pass (which is why it was kept out of U-065).

---

## ✅ RESOLVED 2026-07-10 — `master` reconciled to the prod line; it is now the canonical trunk

The former landmine (`master` was divergent + stale and would hard-regress prod) is **fixed**. On 2026-07-10 `master` was merged with `feat/model-cascade`, resolving the whole tree to the prod lineage, so `master`'s tree is now **byte-identical** to `feat/model-cascade` and the live `:latest` commit `4b13d1f` is an **ancestor of both** (`git merge-base --is-ancestor 4b13d1f master` → true). `master`/`origin/master` are converged and safe; **deploy from `master` going forward.** `feat/model-cascade` is now a redundant mirror carrying only the in-flight Path-B work below and is **slated for retirement** once that lands on `master`.

**Deploy discipline is unchanged:** `az acr build` builds the working tree, so still confirm HEAD is on the live `:latest` lineage before every deploy (`master` now is). The cascade/Foundry feature (`cc3965c`) remains an ancestor of `4b13d1f`, i.e. already live in prod.

---

## U-337 follow-up (account sync lock, 2026-08-31) — ✅ CLOSED by U-340 (2026-08-31)

- [x] **→ SHIPPED U-340 (2026-08-31): fanned out the proven U-337 lock pattern to the sibling routers.**
  Wrapped all 6 remaining per-entity API routes with live `/sync/*` handlers
  (`bill`/`purchase`/`vendor`/`customer`/`company_info`/`vendorcredit`) in
  `qbo_app_lock(qbo_entity_sync_lock_resource(entity))`, 409 on a busy lock — same shape as U-337's account
  fix, entity strings verified to match `shared/api/admin.py`'s dispatcher keys exactly. `term` had no API
  router to fix (only entry point is the already-locked admin dispatcher); `invoice`'s `/sync/qbo-invoices`
  route was left alone — it's dead code, disabled and never calling `sync_from_qbo`, so a lock there would
  wrap nothing. `scripts/sync_qbo_account.py`'s third unlocked entry point (the CLI `__main__` path) is now
  locked via a new `run_locked()` wrapper — locking only the CLI path, not `sync_qbo_account()`/
  `sync_qbo_to_local()` themselves, since those are also called by the admin dispatcher which already holds
  the same lock for that whole call; wrapping at the originally-suggested line 106 would have self-deadlocked
  every admin/scheduler-triggered account sync. `scripts/repair_invoice_line_duplicates.py`'s hardcoded
  `"qbo_sync:invoice"` literal now calls the shared helper. Codex `xhigh` round 1: PASS, 0 findings.
  `/simplify` 4-lens: reuse/efficiency clean; one simplification applied (test file's redundant `case_id`
  column collapsed into `entity_key`); altitude confirmed right depth, logged 2 new deferred follow-ups below.

---

