# U-658 — Field extraction bake-off (Layout + parsers vs Read + LLM)

## Question

Does **Document Intelligence prebuilt-read** text (TIN-masked) plus an **LLM** extract fields as well as **prebuilt-layout** plus today’s rule parsers?

## Arms

| Arm | DI model | Extraction |
|-----|----------|------------|
| A | prebuilt-layout (`extract_invoice`) | Existing parsers (`parse_w9_fields`, COI/BL/CL parsers, `parse_vendor_invoice_number`) |
| B | prebuilt-read (`extract_text`) | DeepSeek-V4-Flash via `extract_with_llm` (one-rung Foundry cascade) |
| C | prebuilt-read | claude-haiku-5-5 via `extract_with_llm` (one-rung cascade; **requires** `ANTHROPIC_FOUNDRY_BASE_URL`) |

Bite 1 shipped the harness, scoring, and arm A. Bite 2 implements `extract_with_llm` (structured JSON + validators, TIN-masked Read text truncated at 24k chars, spend cap with hard `--max-cost-usd` ceiling of **10**). Arm C refuses `--run` unless Haiku resolves to a **non-direct** Anthropic Messages URL (Foundry routing via `_resolve_messages_route`; exit 2).

**Scope decision (Chris, 2026-10-16): the LLM arms (B/C) run on W-9s only.** For COI, licenses and bills only arm A (Layout + parser) runs and their B/C verdicts read NOT-EVALUATED; `extract_with_llm` also refuses any non-W-9 doc before a call. W-9 Read text is aggressively masked (every digit) and checked by a strict collapsed-digit guard, so no TIN digit leaves the process. The non-W-9 masking below is kept (and tested) for a possible future round, but is not used for any LLM call: six review rounds kept finding labelled-TIN variants (spaced/hyphenated/full-width labels, dot leaders) it missed.

**LLM payload safety (non-W-9):** Read text uses `mask_tins(aggressive=False)`, then two rules (round 4). **Shape rule:** only the shapes a TIN is written in — SSN 3-2-4, EIN 2-7 (and 2-3-4), nine digits in a row, or nine single-digit comb boxes — with any run of whitespace/punctuation (up to 12 chars) between the groups, keeping the last 4 (`12 / 3456789`, `123  45  6789`, `123.45.6789`, `(12) 3456789`, `１２３…`, superscripts). **Label rule:** every digit within 40 characters after a TIN label (SSN, EIN, FEIN, Fed ID, Tax ID, TIN, Taxpayer ID) is masked whatever its grouping. Groupings that merely total 9 digits (a 7-digit policy number beside a date, a SKU + qty + price, limit columns, phones, ZIP+4) are left alone. An **independent guard** (its own digit normalisation and regex, gaps up to 40) refuses anything TIN-shaped the mask missed (`payload_refused`; booked Read DI cost; run stays complete). W-9 keeps aggressive digit masking and the strict collapsed 9+ digit guard. **Fairness:** a document whose B/C payload was extra-masked or refused is excluded from the A-vs-B/C comparison for **all** arms; `masking_excluded_docs` in the summary counts them per doc type — read it next to `sample_counts`. Before any masking, invisible characters (Unicode Cf/Mn/Me: zero-width space/joiner, soft hyphen, BOM, word joiner, combining marks) are stripped, so one inside a TIN cannot split its digit groups (round 5); the guard strips them independently and treats a lone `*` as a separator. The bare words `TIN` and `Taxpayer` only count as labels when an ID marker or a digit follows, so "TIN FLASHING" on a bill does not fire. **Residuals (accepted round 4):** an UNLABELLED TIN written in a non-TIN grouping (e.g. `12-34567 89`); TIN groups more than 40 characters apart or split by words; OCR letters for digits (`12-345678O`).

## Truth decisions

- **W-9** — Parser-derived truth on `dbo.Taxpayer` (via `dbo.TaxpayerAttachment`). **Arm A** scores **entity_name** (normalized) and **TIN last-4**; **arms B/C** score **entity_name** only (Read text is aggressively digit-masked; the LLM schema does not ask for TIN). **entity_name** switch metrics (all arms) skip documents whose truth name contains a digit (e.g. “A1 Plumbing”, “84 Lumber”) so aggressive Read masking does not bias B/C vs A; `w9_entity_name_digit_excluded` in the summary JSON counts those attachments once. Classification is scored only against optional hand labels (`--w9-labels` CSV: `attachment_public_id`, `classification`). Without labels, classification is reported as unscored. A labelling sheet (no names) is emitted for Chris to fill.
- **COI** — Verified rows only (`CertificateOfInsurance.VerificationStatus = 'Verified'`). Truth: header on `dbo.CertificateOfInsurance` + policies on `dbo.VendorInsurancePolicy`.
- **Business / contractors license** — Verified only. Truth on `dbo.BusinessLicense` / `dbo.ContractorsLicense` via attachment link tables.
- **Bill invoice number** — Truth: `dbo.Bill.BillNumber` via BillLineItem → BillLineItemAttachment → Attachment. **QBO-sourced only** (`QboId` + `RealmId` set), excluding `BillNumber` like `QBO-%`, email intake (`SourceEmailMessageId` set), and contract-labor bills (any `ContractLabor` or `ContractLaborLineItem` linked to the bill’s line items). Never `Attachment.VendorInvoiceNumber`.

## How to run

From repo root (dry-run default — populations + cost estimate, no DI/LLM):

```bash
./.venv/bin/python -m scripts.u658_extraction_bakeoff
./.venv/bin/python -m scripts.u658_extraction_bakeoff --doc-types w9,coi,bill --seed 658
```

Arms against prod (EM / approved gate; read-only DB, `--max-cost-usd` cap):

```bash
./.venv/bin/python -m scripts.u658_extraction_bakeoff --run --arms A --max-cost-usd 10 --out-dir docs/design/u658
./.venv/bin/python -m scripts.u658_extraction_bakeoff --run --arms A,B --max-cost-usd 10 --out-dir docs/design/u658
# Arm C only when ANTHROPIC_FOUNDRY_BASE_URL is set (Foundry-routed Haiku):
./.venv/bin/python -m scripts.u658_extraction_bakeoff --run --arms A,B,C --max-cost-usd 10 --out-dir docs/design/u658
```

Verdicts per doc type: **NOT-EVALUATED** when arm A or a requested LLM arm did not run; **INSUFFICIENT-SAMPLE** when either arm has fewer than 20 scored documents; **SWITCH-CANDIDATE** only when every requested challenger (B and/or C) is ≥ arm A on all comparison fields and strictly better on at least one; otherwise **KEEP-LAYOUT**. W-9 switch comparison uses entity_name (+ classification when labels are loaded), not TIN last-4.

Optional W-9 classification labels:

```bash
./.venv/bin/python -m scripts.u658_extraction_bakeoff --run --arms A --w9-labels path/to/w9_labels.csv
```

Outputs: `u658_report_*.md`, `u658_per_doc_*.csv`, `u658_summary_*.json`, plus `u658_w9_labelling_sheet_*.csv`. All pass privacy assertions (no names, TINs, or extracted values).
