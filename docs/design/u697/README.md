# U-697 — vector-embedding neighbours vs word neighbours for similar bills

Prod, read-only, 2026-10-29. Harness: `scripts/u697_vector_similar_bills_backtest.py` (this commit), built on
the U-691/U-694 harness (`scripts/u691_similar_bills_backtest.py`, `--text-source pdf`) so **only the
similarity measure changes**. Same bills, same windows (cal 2025-10-11..2026-04-10, eval
2026-04-11..2026-10-10), same amount band (±25 % full weight, zero at 4×), K = 10, same vote, same
calibration-only variant selection, same "history if strong (≥ 0.60), else vote" combined rule.
Embeddings: `text-embedding-3-small` on the Foundry resource `buildonefoundry`, 1,536 dimensions, PDF
text head-truncated to 24,000 characters. Spend metered by the harness itself (not the Claude ledger).

| run | files (stamp) | flags | embedding cost |
|---|---|---|---|
| R1 | `…20261029T130009Z` | `--run --max-cost-usd 1.00` | $0.0326 (two invocations; see below) |

The first invocation stopped after 321 bills on an HTTP 400 the harness did not recognise as
"too many tokens" (Azure's wording: `Invalid 'input[0]': maximum input length is 8192 tokens.`, with a
null `error.code`). The context-length pattern was widened, a regression test added (mutation-proved), and
the run resumed from the cache: $0.0029 + $0.0297.

Outputs committed: `u697_comparison_20261029T130009Z.{md,json}` plus the u691-format report and summary
per arm (`u691_{report,summary}_20261029T130009Z_u697_{W,V,H}.*`). The per-bill predictions CSVs were
written locally (ids and scores only) but are not committed. Every file passed
`assert_outputs_privacy_safe`. Embedding cache: `.cache/u697/embeddings.jsonl` (gitignored, kept for
U-698 outside the repo; bill id + text SHA-256 + vector, no text).

Arms: **W** words (IDF-weighted Jaccard × band; the U-694 path, unchanged) · **V** vectors (cosine ×
band) · **H** reciprocal-rank fusion of W and V per variant (1/(60 + rank), top 100 of each, fused-score
vote). **V′** (shifted cosine) was not run: V lost clearly enough on rank order — not only on the score
scale — that rescaling the scores could not close it.

## Population

| | this run | U-694 | Δ |
|---|---|---|---|
| bills with usable PDF text | 3,813 | 3,757 | +56 |
| eval window | 2,325 | 2,374 | −49 |
| cal window | 1,327 | 1,338 | −11 |

The small drift is bills coded or re-coded since U-694 (2026-10-21). Word-arm parity check (generic
scorer vs `u691.score_all_rolling`): 0 mismatches. Bills without a vector: 0.

## Results — top-1 over all eval bills (abstain = wrong), bill / dollar

| method | W | V | H | U-694 W |
|---|---|---|---|---|
| same vendor | **60.2** / 65.3 | 59.2 / 61.4 | 59.9 / 62.5 | 60.5 |
| any vendor | **60.6** / 64.9 | 48.8 / 42.6 | 50.5 / 47.5 | 61.0 |
| other vendor | **26.8** / 9.9 | 23.4 / 8.6 | 25.2 / 9.3 | 27.6 |
| any vendor, same project (Mode A only) | **50.7** / 49.1 | 33.0 / 26.7 | 35.2 / 27.6 | 51.2 |
| combined A (history if strong, else vote) | **62.3** / 64.6 | 61.5 / 61.7 | 61.9 / 62.7 | 62.6 |
| combined B | **60.7** / 65.4 | 60.0 / 62.3 | 60.6 / 63.5 | 61.1 |
| history A / B (same in every arm) | 58.1 / 60.1 · 45.2 / 51.0 | | | |

W reproduces the U-694 baseline within 0.5 point on every row. Lift of combined over history: A 4.3 /
3.5 / 3.8 points (W / V / H), B 15.6 / 14.8 / 15.4.

## Calibration selection

| arm | Mode A variant | Mode B variant | cal top-1 (selected), bill / dollar |
|---|---|---|---|
| W | any vendor | any vendor | 47.4 / 46.0 |
| V | same vendor | same vendor | 45.1 / 45.1 |
| H | same vendor | same vendor | 45.7 / 44.9 |

On calibration V's any-vendor variant scored 29.7 (W: 47.4), so selection fell back to same vendor —
vectors could not even win the variant they were meant to improve.

## Cold start

| arm | history abstained (n = 87): any / other | no-prior bucket (n = 29): any | no earlier same-vendor PDF bill (n = 60): any / other |
|---|---|---|---|
| U-694 W | 32.2 / 21.8 | 13.8 | — |
| W | 35.6 / 25.3 | 13.8 | 13.3 / 13.3 |
| V | 23.0 / 19.5 | 17.2 | 10.0 / 10.0 |
| H | 23.0 / 20.7 | 20.7 | 15.0 / 15.0 |

The third stratum is new here: bills whose vendor has **no strictly-earlier bill in the PDF population**,
so a same-vendor neighbour is impossible and any hit must come from another vendor's bill. Vectors are
worse on the 87 history-abstained bills and on the 60 new-vendor bills; the only gain is the 29-bill
no-prior bucket (+1 bill for V, +2 for H), which is noise at that size.

## Vendor re-identification

Share of the any-vendor top-10 neighbours that come from the bill's own vendor (pooled; mean per bill
identical to one decimal).

| arm | same-vendor share |
|---|---|
| W | 65.6 % |
| V | 50.2 % |
| H | 52.7 % |

The hypothesis was that vectors would stop re-finding the vendor and find look-alike work from other
vendors instead. They did stop re-finding the vendor — but the neighbours they found instead do not share
the cost code (other-vendor accuracy fell 26.8 → 23.4). Embedding similarity tracks what the document
looks like (layout, supplier type, boilerplate), not what the work was.

## Paired tests (same eval bills)

Fixed = W wrong and the other arm right; broke = the reverse; two-sided exact sign test.

| comparison | method | fixed | broke | p |
|---|---|---|---|---|
| W vs V | same vendor | 36 | 59 | 0.024 |
| W vs V | any vendor | 58 | 333 | < 0.0001 |
| W vs V | other vendor | 99 | 179 | < 0.0001 |
| W vs V | combined A | 49 | 67 | 0.114 |
| W vs V | combined B | 57 | 75 | 0.139 |
| W vs H | any vendor | 45 | 282 | < 0.0001 |
| W vs H | other vendor | 98 | 135 | 0.018 |
| W vs H | combined A | 50 | 60 | 0.391 |
| W vs H | combined B | 59 | 62 | 0.856 |

Every vote variant is significantly worse with vectors. The combined rule hides most of it because
history answers ~58 % of bills before the vote is consulted; even there, no arm beats W.

## U-701b 500-bill sample — history if strong, else vote

Bills from `docs/design/u701b/u654a_predictions_claude-haiku_20261028T150518Z.csv` that are in the
**evaluation window and the PDF population**: 500 of 501 ids (1 outside both windows). U-701b reference
on these 500: word vote 60.0 / 59.2, Haiku → Sonnet ladder 64.2 / 64.4.

| arm | Mode A | Mode B |
|---|---|---|
| W | **60.0** | **59.2** |
| V | 57.8 | 57.2 |
| H | 58.0 | 57.2 |

W reproduces U-701b's word-vote figure exactly. A vector vote would lower the ladder's last rung by ~2
points, not raise it.

## Truncation, spend and runtime

| item | value |
|---|---|
| requests (incl. one-input smoke call), resumed run | 62 (+6 in the first invocation) |
| prompt tokens | 1,485,785 (+142,882) — ≈ 427 per bill |
| cost (at $0.02 / 1M tokens) / cap | $0.0297 (+$0.0029) / $1.00 |
| cache hits (resumed run) | 321 |
| inputs truncated at 24,000 chars | 3 (+1) |
| inputs re-truncated after a too-many-tokens 400 | 1 |
| sends lost to a transport error (counted as spent, worst case) | 0 |
| runtime: load / embed / word arm / vector pass / total (s) | 72.6 / 314.6 / 34.9 / 106.7 / 528.9 |

Cosines are computed in pure Python (`array('f')`, `sum(map(operator.mul, …))`); numpy is not in the
venv, so there is no vectorised path. 107 s for a full rolling pass over 3,813 bills.

## Score scale caveat

Embedding cosines between invoices cluster around 0.2–0.8 and are almost never ≤ 0, so V finds a
neighbour for nearly every bill (any-vendor coverage 99.6 %) and its similarity-weighted vote is close to
a plain majority of the top 10. Word scores are sparse and spread from 0 to 1. The vote-share
"confidence" is therefore **not comparable across arms**. This does not explain the result: the
comparison above is top-1 over all bills (abstain = wrong), which does not depend on any confidence bar,
and H — which uses only ranks — loses too.

## Digits

The word arm's tokenizer keeps letters only, so it never sees part numbers, SKUs, quantities, unit
prices, addresses or invoice numbers. The embedding sees all of it. On this data that hurt more than it
helped: the re-identification and other-vendor rows show vectors pulling neighbours on surface features
that say nothing about the cost code.

## Azure SQL VECTOR probe

Read-only expressions on the prod database (no table created), 2026-10-29:

| probe | result |
|---|---|
| edition / service objective | SQL Azure (engine edition 5), **Basic** |
| database compatibility level | 150 |
| `CAST('[1,0,0]' AS VECTOR(3))` | works |
| `VECTOR_DISTANCE('cosine', …)` | works (0.40 for the test pair) |
| 1,536-dim `VECTOR` | works, 6,152 bytes per value |
| `sys.vector_indexes` catalog view | present |
| `GENERATE_SERIES` | not available at compatibility level 150 (needs 160) |

So the native type and exact distance are available on our current tier today. Storage for every coded
PDF bill would be ≈ 6 KB × 19k ≈ 120 MB. Approximate vector indexes (DiskANN) were not exercised and are
not needed at this size — an exact scan of a few thousand candidates is cheap.

## Conclusions

- **U-698 (vector neighbours in the live suggester): do not build.** Vectors lose on every vote variant
  (any vendor −11.8 points, other vendor −3.4, both p < 0.0001), on cold start, and on the U-701b sample
  (−2.2). Fusion (H) recovers some of the gap but never beats words. Close U-698 as measured-negative,
  or re-scope it to the one untested idea below.
- **Live ladder (U-701b):** no change. The filtered word vote stays the last rung before blank, and the
  similar-bills block fed to Haiku stays word-based.
- **Infrastructure:** none needed. If embeddings are revisited, Azure SQL already supports `VECTOR(1536)`
  and `VECTOR_DISTANCE` on Basic, so there is no platform blocker — the blocker is accuracy. Cost is not
  a factor either: ≈ 427 tokens per bill ≈ $0.009 per 1,000 bills.
- **Untested (if anyone wants a follow-up):** embedding the line-item descriptions only (not whole-PDF
  text, which is dominated by layout and boilerplate), or embedding the bill's coded line text as the
  index side. Both target the "what was the work" signal that whole-document vectors miss. Not
  recommended ahead of other ladder work.
