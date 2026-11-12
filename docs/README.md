# api docs — index

186 files live under `docs/`. This index exists so a session can find the right one
without grepping all of them, and so **historical** material is not mistaken for live
guidance. Added 2026-11-11 (U-788).

## Where things are

| path | what it is | live? |
|---|---|---|
| [`DECISIONS.md`](DECISIONS.md) | the decision log — why the architecture is the way it is | **live** |
| [`runbooks/`](runbooks/README.md) (30) | operational runbooks, one per failure mode. Has its own index | **live** |
| [`conventions/`](conventions/) (12) | **standing rules**, extracted from `CLAUDE.md` so it stays ~15 KB. Binding, not history — CLAUDE.md keeps the one-line rule and links here | **live** |
| [`design/`](design/) (47) | per-unit design docs, `u<n>-*.md`. The design that gated a unit | per unit |
| [`operations/`](operations/) (14) | pre-change prod snapshots, `u<n>_*`. **Evidence, not guidance** — never act on one | frozen |
| [`archive/2026/`](archive/2026/) (5) | historical audits + closed TODO sections. **Nothing here needs action** | historical |

## Live reference at this level

- [`qbo-field-ownership.md`](qbo-field-ownership.md) — which side owns which QBO field.
- [`rc_source_linking_signal_2026_08_16.md`](rc_source_linking_signal_2026_08_16.md) —
  ⚠️ **cited from live source**: `entities/invoice/business/reconciliation.py`, the
  reimburse-charge service + sql, the invoice agent prompt, and the scheduler's
  `function_app.py`. Dated name, but **not** archivable — it is the spec those call sites
  point at.
- [`staging_removal_phase4_5_scoping.md`](staging_removal_phase4_5_scoping.md) ·
  [`staging_removal_phase6_readiness.md`](staging_removal_phase6_readiness.md) — the
  `qbo.*` staging removal; the remaining phase is still open (U-442).
- [`integration-timetracking-contractlabor-bill.md`](integration-timetracking-contractlabor-bill.md)
  + [`…-phase-1-3-schema.md`](integration-timetracking-contractlabor-bill-phase-1-3-schema.md)
  — time-tracking → contract-labor → bill integration.
- [`markup-to-bill-stage-plan.md`](markup-to-bill-stage-plan.md) — markup staging plan.

## Procedures (run only when the matching problem is live)

- [`dedupe-project-rows.md`](dedupe-project-rows.md)
- [`repair_invoice_line_duplicates_u264.md`](repair_invoice_line_duplicates_u264.md)
- [`u249_reconciler_triage_findings.md`](u249_reconciler_triage_findings.md)
- [`budget-tracker-migration-ledger-2026-10.md`](budget-tracker-migration-ledger-2026-10.md)
  + [`…batch-log-2026-10-10.txt`](budget-tracker-migration-batch-log-2026-10-10.txt)

## Historical — read for context, do not act

`handoff-u424-em.md`, `handoff-u467-em.md`, `handoff-bill-attachment-scoping-em.md` —
per-unit `/em` handoffs, superseded by the units that consumed them.

## Conventions

- **Dated filename is not the same as archivable.** `rc_source_linking_signal_2026_08_16.md`
  above is the counter-example: live code cites it. Check for inbound references
  (`.py`/`.sql`/`prompt.md` too, not just `.md`) before moving anything.
- A doc that gated a unit belongs in `design/`; a doc that tells an operator what to do
  when something breaks belongs in `runbooks/`; a frozen pre-change reading belongs in
  `operations/`.
