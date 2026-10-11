# Budget Tracker file migration — ledger (started 2026-10-02)

Scope (Chris, 2026-10-02): move every tracker out of `225 - Budget Trackers` into its project's budget subfolder on BOTH Box and
SharePoint; keep the parallel SharePoint sync; re-template/convert where decided per tracker; normalize names only where it costs
nothing. Worked manually, ONE tracker (or family) at a time, decisions asked per tracker.

Conventions used on every move: id-preserving (Box `PUT /files/{id}` parent under `box_app_lock` + WOPI human-lock check;
SP `PATCH parentReference` under `ms_excel_write:{item}` applock). Verified after each move: Box sha1 + file_version unchanged;
SP version count + top version + lastModifiedDateTime unchanged AND a fresh download equals the top version's size (Graph's
size/hash fields on items in `225` were STALE — not a usable fence). Cached rows refreshed: `box.File.BoxFolderId`,
`ms.DriveItem.ParentItemId`. Dead-letter replays: targeted per-row reset (never the all-rows script), staggered, then
verified by downloading the previous Box version and diffing col-Z keys (outbox `done` is necessary, not sufficient).

## SHT — project 28 — 2026-10-02 — COMPLETE
- Box 2272193694652 → `SHT 2012 Sunset Hills Terrace-2021--04/12 - Budgets` (388371463051); SP 017ZKYN572IY…2JJW → same path
  (017ZKYN56MUT5CMUF2DJCKGXMJQAEDYF6A). Filename already `<Project.Name> - Budget Tracker.xlsx`; sheet DETAILS both.
- Formulas: Box fully immune incl. tail. SP tail positional — LEFT by decision.
- Logged, not fixed: DETAILS F2714 "SHT-Initial" `=F2528-F2510` (Box) vs `=F2567-F2549` (SP); duplicate col-Z key CE61AD7E…
  at Box r397/398 (pre-existing).
- Dead letters 7596/8073/8075 replayed → all done att 1; diff v2775495095615→v2775535518646: exactly ONE row added
  (r150 VaVia QBO-71946 $715 code 3); the other two were col-Z no-ops.
- OPEN: ms.Outbox 8652 `insert_excel_row` abandoned in_progress since 2026-09-29 (row_index 2016) — needs delivery check + close.

## BR-MAIN — project 64 — 2026-10-02 — COMPLETE
- Box 2272197075681 → `BR-MAIN 7550C Buffalo Rd Main House--2021--32/12 - Budgets` (388301200434); SP → same
  (017ZKYN575S4NQB4XERRB2C5PBHHNR3J7T). Filename LEFT as "7550" by decision (DB/folders say 7550C).
- Both copies identical, fully immune; 331/331 distinct keys both sides — the 17 SP duplicates from the 2026-09-09 TODO are GONE.
- box.Outbox 9515 (abandoned in_progress 2026-09-29, bill insert): key verified present → closed done + CompletedAt.
- box.Outbox 5687 (stamp_draw_request BR-MAIN-28, WOPI dead letter 08-18): all 13 rows already H=BR-MAIN-28 → replay no-op.

## Buffalo Road dormant set — 2026-10-02 — COMPLETE (move-only by decision; all unmapped, 0 col-Z keys)
- BR-BARN (62), BR-BRIDGE (63), BR-SW (66) → their own `12 - Budgets`; BR-RENTAL (65) → BR-MAIN's `12 - Budgets` (no own
  budget folder, none created); BR-GARAGE (no dbo.Project) → BR-BARN's `12 - Budgets`. All on old `Tracking Budget DETAILS`
  layout (BR-SW/BR-RENTAL older still); not sync-capable without a rebuild.
- BR-BARN Box copy is one version OLDER than SP (May 2025) — dormant, harmless.
- dbo.Project names 62/63/65 recased to BR-BARN / BR-BRIDGE / BR-RENTAL by Chris in SSMS (classifier blocked my UPDATE);
  `preserve_human_edited_name` keeps them on QBO re-pull.

## OHR2 family — 2026-10-02 — COMPLETE
- Folders CREATED (approved exception to adopt-only): Box `OHR2-CHAPEL …/12 - Budgets` = 423692682062; SP = 017ZKYN52PQFIMZR5HIZDL4OLXM5M6IIJU.
- OHR2 (30) and OHR2-GUEST (32) → OHR2's `12 - Budgets` (Box 388363155897 / SP 017ZKYN53JG4QJ72SI45D267U3PIYSS3UC);
  OHR2-CHAPEL (31) → new CHAPEL/12. Filenames unchanged by decision (CHAPEL file 1539 vs DB 1543; GUEST "Road" vs DB "Rd").
- OHR2 tail block converted to the immune idiom on BOTH copies (7 cells: Draw F = SUMIF(SUBTOTAL,F)−SUMIFS(F,B=90), X,
  current-draw, fee, total, label echo, COUNTIFS). Fee category = 90 "BUILDER'S FEE". Box v2775525724615 → v2775640252633
  (rollback = 2775525724615), off-target 0, push log written. SP: 7 Graph formula PATCHes.
- OHR2-CHAPEL SP: 454 positional budget lookups → `=SUMIFS(Budget!F:F,Budget!C:C,INDEX(DETAILS!$D:$D,ROW()))` (Box's own
  immune text). 454/454, off-target 0, errors 0. OHR2-GUEST SP: 454 (450 positional + 4 #REF!) → same; 454/454 after three
  resumes (mobile IP rotation cut the DB mid-run), off-target 0, errors 0, #REF! 0.
- OHR2 13 dead letters replayed (75s stagger) → all done att 1; version UNCHANGED (12 already delivered; row 4280's only line
  is coded to project 32 and is already in GUEST's sheet r1582 — correct no-op). Aug-11 "Access denied" rows replayed cleanly.
- Dup keys logged: OHR2 Box D78B9C94 r147/149, 3BBBBFC4 r2535/2548; SP adds 90ACEC03 r2932/2933.
- Canaries: Box 25 files/133 folders/0 missing; SP list_worksheets by id = 200 for 30/31/32.

## Estate counters
- Box outbox dead letters: 135 → 118. Box in_progress stuck claims from 2026-09-29: both closed (9515 here; the other was SHT's twin
  on the ms side = 8652, still OPEN).
- Lessons: Graph size/hash stale in `225`; sessionless Graph writes have read-after-write lag (use a workbook session for bulk
  formula writes); the session scratchpad is NOT durable — this ledger now lives in the repo.

## Hillsboro — HP (18) + HP2 (13) — 2026-10-07 — COMPLETE
- HP: Box 2272199906165 → `HP 6135 Hillsboro Pike--2022--63/12 - Budgets` (388326956594); SP 017ZKYN5Z4BD6SNX3SORALRZT24OKYU5CF → same
  (017ZKYN5ZRMBGX3OJTURGKA7HAW24BXHLE). Both verified. Filename LEFT as "Hillsboro Road" by decision (DB/folders say Pike).
- HP2: Box half was already in `HP2 4406 Harding Place/12 - Budgets` (Chris, 2026-10-02); SP 017ZKYN52K3RDZQTECCZA3K6L2V7C7XNSM →
  `12 - Budgets` (017ZKYN5YBSX4HGEEXDRALFD7FP33JEI64), verified. Filename + DB name LEFT ("Harding Place" vs DB "Harding Pike") by decision.
- HP Box "Draw" cell F3016 `=F2971-F2949` → `=SUMIF($E:$E,"SUBTOTAL",F:F)-SUMIFS(F:F,$B:$B,90,$E:$E,"<>SUBTOTAL")` (fee category 90
  confirmed by Chris; B=90 SUBTOTAL at r2990). Box v2765935646208 → v2786962840702 (rollback = 2765935646208), off-target 0.
- SharePoint copies LEFT positional by decision (454 lookups / 211 range subtotals / 3 comma GTs each, ZERO drift — Graph is formula-aware).
- Duplicate col-Z keys LOGGED + BOOKED (api TODO.md): HP 3 keys ×2 at r1972–1977 on BOTH copies; HP2 Box 4 keys ×2 (r1125–1128 /
  r1150–1153); HP2 SP 33 keys duplicated (3 tripled), file edited 2026-10-07 — same signature as the BR-MAIN 2026-09-09 incident.
- HP2 13 Box dead letters reset (75s stagger) — pre-replay v2786958193518; expected: bill 33E7E852 (2 keys absent) inserts, bill
  5678B230 has 0 line items (no-op), the other 11 already delivered. RESULT: all 13 done att 1; diff v2786958193518 → v2786968520808 added EXACTLY
  the two expected rows (Selvin Cordova 2026.08.31.HP2: r1403 code 40 $2,025 and r2136 code 65 $337.50), 0 removed, 0 new dups.
- Canaries: SP list_worksheets(18) and (13) = 200.

## Overton Lea — OL (23), OL-PH (132), OL2 (5) — 2026-10-07 — COMPLETE
- OL + OL-PH: Box halves already in `OL 925 Overton Lea--2024--06/12 - Budgets` (388380598860, Chris 2026-10-02). SP halves
  017ZKYN53NOBJ5TRRJDNCLOGBUUX25EP3T / 017ZKYN56OVGFC7QJD2VGZIUV6RYWFGHEP → OL's SP `12 - Budgets` (017ZKYN57UOBPIZYVVLZHYKL6BKXLF23O4), verified.
- OL2 (dormant, 0 keys): `12 - Budgets` CREATED under `OL2 945 Overton Lea` by decision — Box 425239624094, SP 017ZKYN55R6IQTJBS4PNEL5P3BUZNFMZFW;
  Box 2272199282008 + SP 017ZKYN524XKWUQO74EJF3VBCX5XSBHMVL moved in, verified. Positional, zero drift, left as-is.
- OL Box tail converted (fee category 90 by Chris): F2186 Draw `=#REF!-F1966` (was an ERROR) → SUMIF(SUBTOTAL)−SUMIFS(B=90);
  N2200 draw `=SUMIFS(N:N,H:H,M2200)` → INDEX/ROW; N2202 fee `=N2166*M2168` (drifted) → INDEX/ROW; N2204 total `=SUM(N2166:N2169)`
  (drifted) → INDEX/ROW. Exactly 4 cells changed; 0 #REF! left. v2781695384594 → v2786994268211 (rollback = 2781695384594).
- SharePoint copies LEFT positional by decision (zero drift). OL-PH Box was already fully immune.
- OL dead letters 7897 (expense insert, key already present) + 10044 (stamp_draw_request OL-16, WOPI exhaustion 2026-10-02) →
  both done att 1, version unchanged; OL-16 column H verified correct on 36/36 rows.
- Duplicate keys BOOKED: OL SP 85E4F35E r65/66, B7A677E8 r1181/1182 (Box clean).
- Canaries: Box 25/133/0 missing; SP list_worksheets(23), (132) = 200. Box dead letters estate-wide now 104.

## Kirkwood — KA (52), KA2 (55) — 2026-10-07 — COMPLETE (move-only)
- KA: Box 2272205509293 → `KA 825B Kirkwood Ave.--2024--10/12 - Budgets` (388318024525); SP 017ZKYN56WJVCA5LBFKJD36QYROTVRHE3O →
  same (017ZKYN5YI4RG3L7Q4HBALFUCJVVQVQS6Q). Verified both.
- KA2: Box 2272205193018 → `KA2 - 827 Kirkwood Ave/12 - Budgets` (388317001555); SP 017ZKYN57E7BYMQNUOPNE3M5B4LWOHXP3S → same
  (017ZKYN5ZJ4YBUBBZOMRHJLRULAO6YGLWJ). Verified both. Filename LEFT ("827 Kirkwood" vs DB "… Ave") by decision.
- Box copies fully immune (KA's 5 audit-flagged drifted lookups had already been fixed). SP copies positional, zero drift, LEFT.
- KA fee cell N2074 `=200000/8` is a deliberate flat fee ($25k/draw) — LEFT by decision. KA tags draws with DATES (current draw
  keyed 2025-12-19) → third date-serial project after HE12 and EVR.
- No dead letters, no duplicate keys. Canaries: Box 25/0 missing; SP list_worksheets(52),(55) = 200.
- CORRECTION to earlier notes: Box outbox 9469 (WVA, update_box_excel) is STILL in_progress since 2026-09-29 18:11 — only BR-MAIN's
  9515 was closed. Handle at WVA.

## Hidden River — HRL (9), HRL-BARN (10), HRL-POOL (11) — 2026-10-07 — COMPLETE (move-only)
- HRL: Box 2272193336420 → `HRL 2483 Hidden River Lane--2021--28/12 - Budgets` (388334589242); SP 017ZKYN52F7S5KTDDUDNC2VPVSJKQL3UL7 → same
  (017ZKYN5ZQWTXCIF2PABBY6N52TCJYVM3I). Verified both. Sheet `Tracking Budget DETAILS`; Box subtotals/totals immune; only 2 col-Z keys
  (overwhelmingly hand-entered — treat as HE12-style duplication risk on any back-fill).
- HRL-BARN / HRL-POOL (dormant, 0 keys, old range layout): Chris's rule = own `12 - Budgets` if the project parent exists, else HRL's.
  Parents existed on both platforms → CREATED: BARN Box 425243716258 / SP 017ZKYN5YUKEW7W55TX5F2OBOJNRCUK6LI; POOL Box 425247211051 /
  SP 017ZKYN52S5IU7YJB3HFGKDSHCBNE5UZJA. Files moved in, verified both platforms.
- SharePoint copies LEFT positional (zero drift). No dead letters, no duplicate keys.
- Canaries: Box 25/0 missing; SP list_worksheets(9) = 200.

## Harlan Estates — HE12 (76), HE7 (48), HE10 (111), HE1-Bare (83), HE1-RBP (106), HE2 (51), HE4 (2) — 2026-10-07 — COMPLETE
- All seven moved into existing `12 - Budgets` folders on both platforms (targets pre-checked by name + parent prefix); every move
  verified (Box sha1/version unchanged; SP versions/lastModified unchanged, download == top version size).
  Box/SP targets: HE12 388338015838 / 017ZKYN54WD3QO6AGLRNDINSSZWR6D7ZEO · HE7 388342890009 / 017ZKYN53BKFYRZ7RBM5FYURJDFAKMVY3S ·
  HE10 388337053372 / 017ZKYN547TDV3FDBVRJAK2EG5EUTNQ5K6 · HE1-Bare + HE1-RBP → HE1 388326253339 / 017ZKYN5ZEQYZBATK7HRFYNM77TFBVUVPA
  (both into HE1's folder by decision) · HE2 388326625246 / 017ZKYN5ZTGEG44MFZGJGYS7W6YTBUDG4G · HE4 388318410008 / 017ZKYN56DNMURAKKNVBGI36U545M45B7K.
- Filenames LEFT by decision ("Estates" vs DB "Estate", "Ct" vs "Court", lot-name vs address forms).
- HE12: Box immune; SP positional zero drift, LEFT. Dead letter 6125 (2 Metro Water expense lines, WOPI 08-21) pre-classified
  (HE12 is mostly hand-entered): both lines already keyed on the sheet (r481, r483) → replay done att 1 as a no-op, version unchanged.
- HE7: was SharePoint-mapped only. Chris clarified (2026-10-07) he wanted the Box mapping RESOLVED, not deferred. Done:
  1) Box 2272194258248 refreshed from SP (SP lastModified 05:35:55Z held unchanged through the run): v2515269537230 → v2787142926811,
     byte-identical to SP (rollback = 2515269537230). 2) Converted 144 cells: 68 range subtotals → SUMIFS(N:N,$B:$B,cat,$E:$E,"<>SUBTOTAL")
     (all 68 structurally clean); 68 subtotal-row budget lookups `'Schedule Of Values'!…C{own row}` → INDEX($C:$C,ROW()); F/N grand
     totals (listed all 68 subtotals → value-preserving) → SUMIF(SUBTOTAL); tail X/Payments/Balance/draw/fee/total → immune idiom
     (Payments copied verbatim from BR-MAIN). Exactly 144 changed; v2787142926811 → v2787150406887 (pre-conversion rollback = 2787142926811).
     Left: two human scratch cells S33 `=F33-N33`, Q889 `=N902+7667.5+N904` (not read by any total). 3) Mapped via
     BoxProjectWorkbookService.map_workbook (project 48, sheet `Tracking Budget DETAILS`) + draw_requests → `15 - Draw Requests &
     Change Orders` (388345962007) via BoxProjectFolderService.map_project. NOTE: HE7 is date-tagged (current draw keyed 2025-10-17)
     and has only 1 synced key → forward pushes only, never back-fill.
- Dormant (0 keys): HE10, HE2 (modern positions, range subtotals); HE1-Bare, HE1-RBP, HE4 (oldest B=sub/C=code layout). Move-only.
- No duplicate keys anywhere in the group. Canaries: Box 25/0 missing; SP list_worksheets(76),(48) = 200.

## Stanford Ct — SSC2 (79), SSC (49), SSC-POOL (50) — 2026-10-07 — COMPLETE (move-only)
- SSC2 Box 2272205888486 → `SSC2 - 5620 S Stanford Ct (Marvin)/12 - Budgets` (388358495186); SP → same (017ZKYN5ZQLZSGOAXJKVH2VE3BV76LWCXP).
  SSC → own `12 - Budgets` (Box 388372647816 / SP 017ZKYN5ZMPN5AEXINVRE3NWZ3YD5G4NAC). SSC-POOL → own (Box 388357573523 / SP
  017ZKYN5YOVVH3DO7BGZEYRV2CBSMETGPD). All verified. Filenames and SP positional LEFT by decision.
- SSC2 "10 SP-only keys / $41,354.42 Box gap" — FALSE ALARM, back-fill NOT run. Chris approved a back-fill on my key-only comparison;
  the pre-write classification found 9 of the 10 already on Box as hand-entered keyless rows tagged SSC2-04 (r47, r48, r83, r741,
  r1403–1407) and the Ferguson credit CM106788 at r1380. Draw totals match: SSC2-04 Box = SP = $84,473.39; SSC2-03 = $84,650.10 both.
  A back-fill would have duplicated ~$41k of cost rows. LESSON: compare CONTENT (vendor+amount+draw), never keys alone.
- New, booked: SSC2-05 Box $32,610.11 vs SP $29,116.17 — Box r1666 is an extra keyless row (no vendor, col K 'SSC2-05', $3,493.94)
  present only on Box. Box dup key 738484B4 sits on r1379 (Ferguson 8567505-1 $2,667.89) AND r1380 (credit CM106788 −$2,667.90) —
  the credit row carries the bill's key (hand-copied), totals unaffected.

## Franklin Pike — FP (35), FP-POOL (36) — 2026-10-09 — COMPLETE
- FINDING: FP's SharePoint mapping pointed at the 2022 ORIGINAL (`FP/12 - Budgets`, item 017ZKYN5ZDEW4FCYPRARFKLHDQDT5CXMVY, 130KB,
  3 tabs, old layout one column left). The sync wrote 6 bills into it since March 2026 ($3,011.88), appended at r1136–1141 one column
  off → invisible to its totals. The real SP tracker (`225` copy 017ZKYN56BAQMIUCTAPFFIMAICHTQUSQ5J, modern DETAILS) never got a synced
  row and was last edited 2026-01-12. Box (2272198734238) was the live copy: modern, fully immune (incl. the Payments/Balance cells the
  U-439 audit flagged — already fixed), same 6 keys correctly placed.
- Decision (a), executed in order: (1) SP `225` copy refreshed from Box — SP v306 → v307 (rollback = 306). Bytes differ by design
  (SharePoint injects customXml/docProps on upload); content verified identical: all sheets, 60,239 populated cells, 0 differences.
  (2) 2022 original renamed in place to `FP - 4005 Franklin Pike - Budget Tracker (2022 original).xlsx` on BOTH platforms (Box
  2272642503986 — its migrated mirror collided by name in Box FP/12; same file, same decision applied). 6 misaligned rows left as-is.
  (3) refreshed copy moved into SP `FP/12 - Budgets` (017ZKYN5ZV4USD7R4BFRCL3LAWDZVIRYTM). (4) SP mapping re-pointed via
  DriveItemProjectExcelConnector unlink + link_excel_to_project → item 017ZKYN56BAQ…, sheet `DETAILS`; list_worksheets(35)=200.
  (5) Box FP moved into Box `FP/12 - Budgets` (388321557152), unchanged.
- FP-POOL (dormant, Box == SP byte-identical): moved into its own `12 - Budgets` on both (Box 388320892737 / SP 017ZKYN56SAP66ONT2ZFGIDBTGEFY5FDGM).
- BOOKED: FP Box r3950/3951 (cost code 98 / 98.01, Wilmer Diaz + Selvin Cordova labor) landed below TOTAL EXPENSES — no 98 section
  in this tracker (the U-437 "SubCostCode not in template" class). Counted by draw totals, not section totals.
- Canaries: Box 25/0 missing; SP list_worksheets(35) DETAILS present.

## Chickering — CC (41), CC-RENO (114) — 2026-10-09/10 — COMPLETE
- CC: Box 2272206215641 → `CC 6136 Chickering Ct.--2021--45/12 - Budgets` (388324195051); SP 017ZKYN537JWZ6AJOPRJC3MGOJ2OF7MGEJ → same
  (017ZKYN5YYTPMG4JMOXFBYG2WRLKACND57). CC-RENO (dormant, modern positions, Box == SP): Box 2272194645564 → own `12 - Budgets`
  (388318840406); SP 017ZKYN53YQVSQF23UHVFZBX5BQBBNTQTD → own (017ZKYN55U7M3RNH5PFFAZMMC2YJBZ4WKT). All verified; canaries green.
- OPEN (asked 2026-10-09, not yet answered): CC is on the OLD template (one column left). Box U-559 guard refuses every CC push
  (dead letters 9481/9485/10472 — all bill 20261004 G&M Plumbing $215, key B7536BB5). SP has no guard: r1032 ($525, key AE57DCC1 —
  the 2026-09-29 repair was Box-only) and r1033 ($215) sit one column off, invisible to SP totals. Options put to Chris:
  (a) unmap CC on both platforms + hand entry (recommended; ~2 bills/yr); (b) leave; (c) rebuild onto modern template.
  And: hand-repair SP r1032/r1033 + add the $215 row on Box + close the 3 dead letters, or book it.
- DECIDED 2026-10-10 (Chris): (1) LEAVE the CC mappings as-is (old template stays; Box will keep refusing CC pushes and SP will keep
  writing them one column right — each future CC bill needs the same hand repair). (2) Repair the two bills BY HAND — done:
  SP r1032 ($525, AE57DCC1) + r1033 ($215, B7536BB5) shifted one column left via Graph (A/B numeric, C/G/N cleared, keys kept);
  SP cat-24 subtotal M1085 $30,250 → $30,990 (+$740, Graph-computed). Box: $215 filled IN PLACE into blank spare r1035 (inside
  M1084 `=SUM(M1031:M1082)`), style copied from repaired r1032, key B7536BB5 in Z; exactly 7 cells changed; v2766614380284 →
  v2793715202078 (rollback = 2766614380284). Dead letters 9481/9485/10472 → `cancelled` with "resolved by hand" note.
- NOTE: Box CC subtotal r1084 range stops at M1082 (drifted by old inserts; r1083 is a blank 24.14 spare, so no money impact today).

## WVA — project 46 — 2026-10-10 — COMPLETE
- box.Outbox 9469 (bill 8E957FA0 insert, abandoned in_progress since 2026-09-29 18:11 — the second stuck claim): key verified present
  → closed `done` + CompletedAt. Both 2026-09-29 stuck Box claims are now closed (9515 BR-MAIN, 9469 WVA).
- Box tail converted (fee category 90 = "BUILDER'S FEE" on this sheet): F2633 Draw `=F2510-F2498` → SUMIF(SUBTOTAL)−SUMIFS(B=90);
  N2646 WVA-19 `=SUMIFS(N:N,H:H,M2638)` (wrong row) → INDEX/ROW; N2648 fee `=N2638*M2640` → INDEX/ROW; N2650 total `=SUM(N2637:N2641)`
  → INDEX/ROW. Exactly 4 changed. v2771640733483 → v2794418874318 (rollback = 2771640733483). Left: O123 `=SUM(N119:N124)` human helper.
- 9 dead letters (Aug 12–13 WOPI exhaustions) replayed, 60s stagger → all done att 1, version unchanged (all delivered already).
- Box 2272199156819 → `WVA 424 Westview Ave/12 - Budgets` (388383517412), verified.
- SharePoint REFRESHED FROM BOX by decision (a): SP had fallen behind — WVA-18 $82,400.08 vs Box $84,450.02 (the 2026-07-06 Box-only
  reconcile: +3 utility rows, −$17.47 Home Depot) and WVA-19 $29,599.15 vs Box $33,079.09 (Box hand-entered $3,805.56 row r2607 near
  the fee section; Lowe's credit QBO-69755 −$162.81 on Box vs +$162.81 on SP). SP v653 → v654 (rollback = 653); 42,413 populated cells
  compared, 0 differ. DROPPED from SP by this choice (accepted): 7 Jack VanOrman 2026.05.31 labor rows at $0 below TOTAL EXPENSES.
  SP now carries Box's immune formulas. Then moved into `WVA 424 Westview Ave/12 - Budgets` (017ZKYN53IYYJ7YIAXBZC37BJL6A5WWTD7).
- Canaries: Box 25/0 missing; SP list_worksheets(46) = 200. Note: SP usedRange now ends at column AA (row 1825's inert AA residue,
  documented in the misalignment memory) — harmless.

## TB3 — project 73 — 2026-10-10 — COMPLETE
- Box already fully immune (incl. tail; U-439 phase e done earlier). Nothing converted. Still noted: fee budget F2967 is a typed
  $751,816.47 (U-439); r3005 sub-cost-code '00' expense $413.61 appended after the draw block (booked).
- 9 dead letters (Aug 14, Sep 9; one "Access denied") replayed 60s stagger → all done, version unchanged (7 delivered; FA6DB3A4's
  lines not coded to 73 → no-op).
- Box 2272200621454 → `TB3 917 Tyne Blvd--2023--79/12 - Budgets` (388372496739), verified.
- SharePoint REFRESHED FROM BOX by decision: SP was behind — TB3-19/20/21 lacked Box's hand-entered 15% fee rows r2955–2957
  ($95,679.61 / $35,468.55 / $46,218.33); SP lacked 7 Bluebird Roofing credit rows (−$3,000); SP's only unique rows were 2 Brayan
  labor rows whose keys (11A255B7, B1624756) resolve to NO line item (stale, deleted lines); SP had 6 extra dup keys; SP tail stuck
  at TB3-18. SP v898 → v899 (rollback = 898); 48,950 cells compared, 0 differ. Moved into TB3/12 (017ZKYN57ZWUUUQ2DVN5DKJRMLJGEIEAIZ).
- Booked: key 2EBCDE9B on 7 rows (1013–1019) on Box (and SP before refresh).
- Canaries: Box 25/0 missing; SP list_worksheets(73) = 200.

## STANDARD BATCH (blanket review) — 2026-10-10 — 36 trackers COMPLETE
Chris asked (2026-10-10) for a blanket review of everything left in `225`, applying settled decisions and surfacing exceptions.
Standard = project folder exists (own `12 - Budgets`, or created under the HRL rule), no name collision, no unresolved sync issue.
Each move verified (Box sha1+version unchanged; SP version count/top id + lastModified unchanged). All dormant except EVR (mapped;
both copies agree on all 21 draw tags, 0 key differences, no outbox rows, Box fully immune). Log: docs/budget-tracker-migration-batch-log-2026-10-10.txt.
  - BMB: Box → 200 - Rogers Build Projects/BMB 533 Belle Meade Blvd--2021--22/12 - Budgets | SP → /General/200 - Rogers Build Projects/BMB 533 Belle Meade Blvd--2021--22/12 - Budgets
  - CR: Box → 200 - Rogers Build Projects/CR 1025 Choate Rd--2023--87/12 - Budgets | SP → /General/200 - Rogers Build Projects/CR 1025 Choate Rd--2023--87/12 - Budgets
  - DFR: Box → 200 - Rogers Build Projects/DFR 8242 Duffield Road/12 - Budgets | SP → /General/200 - Rogers Build Projects/DFR 8242 Duffield Road/12 - Budgets | Box 12 - Budgets CREATED 426275746939; SP 12 - Budgets 201 017ZKYN54A7DLGRNL5TJBYV5QQL54ZQVIE
  - EAS: Box → 250 - Completed Rogers Build Projects/EAS 2109 18th Ave South--2021--14/12 - Budgets | SP → /General/250 - Completed Rogers Build Projects/EAS 2109 18th Ave South--2021--14/12 - Budgets
  - EASH: Box → 200 - Rogers Build Projects/EASH - 5866 E Ashland Dr/12 - Budgets | SP → — (Box-only) | Box 12 - Budgets CREATED 426276778986
  - ED: Box → 200 - Rogers Build Projects/ED 2005 Earlington Dr--2021--35/12 - Budgets | SP → /General/200 - Rogers Build Projects/ED 2005 Earlington Dr--2021--35/12 - Budgets
  - EVR: Box → 200 - Rogers Build Projects/EVR 6315 E Valley Rd--2023--86/12 - Budgets | SP → /General/200 - Rogers Build Projects/EVR 6315 E Valley Rd--2023--86/12 - Budgets
  - FAR: Box → 200 - Rogers Build Projects/FAR 0 Hwy. 100 & Int. 840--2023--81/12 - Budgets | SP → /General/200 - Rogers Build Projects/FAR 0 Hwy. 100 & Int. 840--2023--81/12 - Budgets
  - GBC: Box → 200 - Rogers Build Projects/GBC 4031 Graybar Ct--2022-68/12 - Budgets | SP → /General/200 - Rogers Build Projects/GBC 4031 Graybar Ct--2022-68/12 - Budgets
  - GTD: Box → 200 - Rogers Build Projects/GTD 709 Georgetown Dr--2021--27/12 - Budgets | SP → /General/200 - Rogers Build Projects/GTD 709 Georgetown Dr--2021--27/12 - Budgets
  - HW: Box → 200 - Rogers Build Projects/HW 128 Hardingwoods Dr--2021--85/12 - Budgets | SP → /General/200 - Rogers Build Projects/HW 128 Hardingwoods Dr--2021--85/12 - Budgets
  - KD: Box → 200 - Rogers Build Projects/KD 1901 Kingsbury Dr--2022--53/12 - Budgets | SP → /General/200 - Rogers Build Projects/KD 1901 Kingsbury Dr--2022--53/12 - Budgets
  - KD-GARAGE: Box → 200 - Rogers Build Projects/KD-GARAGE 1901 Kingsbury Dr. - 2024/12 - Budgets | SP → /General/200 - Rogers Build Projects/KD-GARAGE 1901 Kingsbury Dr. - 2024/12 - Budgets
  - MGL: Box → 200 - Rogers Build Projects/MGL 6018 Martingale Lane--2021--15/12 - Budgets | SP → /General/200 - Rogers Build Projects/MGL 6018 Martingale Lane--2021--15/12 - Budgets
  - ML2: Box → 200 - Rogers Build Projects/ML2 4512 Millrace Ln--2022--75/12 - Budgets | SP → /General/200 - Rogers Build Projects/ML2 4512 Millrace Ln--2022--75/12 - Budgets
  - NR: Box → 200 - Rogers Build Projects/NR 1101 Natchez Road--2021--44/12 - Budgets | SP → /General/200 - Rogers Build Projects/NR 1101 Natchez Road--2021--44/12 - Budgets
  - NR2: Box → 200 - Rogers Build Projects/NR2 1105 Natchez Rd--2022--73/12 - Budgets | SP → /General/200 - Rogers Build Projects/NR2 1105 Natchez Rd--2022--73/12 - Budgets
  - OC3: Box → 200 - Rogers Build Projects/OC3 1335 Otter Creek Rd--2025--/12 - Budgets | SP → /General/200 - Rogers Build Projects/OC3 1335 Otter Creek Rd--2025--/12 - Budgets | Box 12 - Budgets CREATED 426276592554; SP 12 - Budgets 201 017ZKYN53XA6XTS77COVHK6OFYYRP6UYQB
  - OHB: Box → 200 - Rogers Build Projects/OHB 1809 Old Hickory Blvd--2021--39/12 - Budgets | SP → /General/200 - Rogers Build Projects/OHB 1809 Old Hickory Blvd--2021--39/12 - Budgets
  - OHB2: Box → 200 - Rogers Build Projects/OHB2 1917 Old Hickory Blvd--2022--60/12 - Budgets | SP → /General/200 - Rogers Build Projects/OHB2 1917 Old Hickory Blvd--2022--60/12 - Budgets
  - ONT-1: Box → 200 - Rogers Build Projects/ONT-1 2474 Old Natchez Trace (Cabin 1)--2022--50/12 - Budgets | SP → /General/200 - Rogers Build Projects/ONT-1 2474 Old Natchez Trace (Cabin 1)--2022--50/12 - Budgets
  - ONT-2: Box → 200 - Rogers Build Projects/ONT-2 2468 Old Natchez Trace (Cabin 2)--2022--51/12 - Budgets | SP → /General/200 - Rogers Build Projects/ONT-2 2468 Old Natchez Trace (Cabin 2)--2022--51/12 - Budgets
  - OVH: Box → 200 - Rogers Build Projects/OVH - 2031 Overhill Dr/12 - Budgets | SP → — (Box-only) | Box 12 - Budgets CREATED 426276143810
  - PD: Box → 200 - Rogers Build Projects/PD 903 Pasedena Drive--2021--11/12 - Budgets | SP → /General/200 - Rogers Build Projects/PD 903 Pasedena Drive--2021--11/12 - Budgets
  - RBFO: Box → 200 - Rogers Build Projects/RBFO 0 Fairview Blvd. - Office--2022--78/12 - Budgets | SP → /General/200 - Rogers Build Projects/RBFO 0 Fairview Blvd. - Office--2022--78/12 - Budgets
  - SD2: Box → 200 - Rogers Build Projects/SD2 1902 Shamrock Dr--2022--84/12 - Budgets | SP → /General/200 - Rogers Build Projects/SD2 1902 Shamrock Dr--2022--84/12 - Budgets
  - SEC: Box → 200 - Rogers Build Projects/SEC - 410 Saint Edmonds Ct/12 - Budgets | SP → /General/200 - Rogers Build Projects/SEC - 410 Saint Edmonds Ct/12 - Budgets
  - SHR2: Box → 200 - Rogers Build Projects/SHR2 - 1601 S. Harpeth Road -- 2023 -- 89/12 - Budgets | SP → /General/200 - Rogers Build Projects/SHR2 - 1601 S. Harpeth Road -- 2023 -- 89/12 - Budgets
  - TGGT: Box → 200 - Rogers Build Projects/TGGT 1216 Taggartwood Road--2021--02/12 - Budgets | SP → /General/200 - Rogers Build Projects/TGGT 1216 Taggartwood Road--2021--02/12 - Budgets
  - THC: Box → 200 - Rogers Build Projects/THC 699 Thurrock Circle/12 - Budgets | SP → /General/200 - Rogers Build Projects/THC 699 Thurrock Circle/12 - Budgets | Box 12 - Budgets CREATED 426276208610; SP 12 - Budgets 201 017ZKYN5Y7XFU2UQYKMJDLQQ3AP7WJ52JO
  - WCD: Box → 200 - Rogers Build Projects/WCD 6432 Worchester Drive--2021--40/12 - Budgets | SP → /General/200 - Rogers Build Projects/WCD 6432 Worchester Drive--2021--40/12 - Budgets
  - WD2: Box → 200 - Rogers Build Projects/WD2 5883 Willshire Dr--2022--76/12 - Budgets | SP → /General/200 - Rogers Build Projects/WD2 5883 Willshire Dr--2022--76/12 - Budgets
  - WD3: Box → 200 - Rogers Build Projects/WD3 5898 Willshire Dr--2023--83/12 - Budgets | SP → /General/200 - Rogers Build Projects/WD3 5898 Willshire Dr--2023--83/12 - Budgets
  - WL2: Box → 200 - Rogers Build Projects/WL2 4128 Wallace Ln--2023--82/12 - Budgets | SP → /General/200 - Rogers Build Projects/WL2 4128 Wallace Ln--2023--82/12 - Budgets
  - WRR2: Box → 200 - Rogers Build Projects/WRR2 1075 Webb Ridge Rd. Addition--2022--61/12 - Budgets | SP → /General/200 - Rogers Build Projects/WRR2 1075 Webb Ridge Rd. Addition--2022--61/12 - Budgets
  - WRR3: Box → 200 - Rogers Build Projects/WRR3 - 0 Webb Ridge Road Barndo/12 - Budgets | SP → /General/200 - Rogers Build Projects/WRR3 - 0 Webb Ridge Road Barndo/12 - Budgets
- Canaries after batch: Box 25/0 missing; SP list_worksheets(42) EVR = 200.
- EXCEPTIONS carried to Chris (not executed): no project folder (ONT-NEW/OLD/SW, GWT-STORM, CCR-BARN, CCR-STABLES, HSR, TB-POST,
  Joe Dirrte budget folder); `25 - Budgets` naming (GWT, ML, OHR); prior-version collisions in target (MPR 2022 copy, 1,428 cells
  differ; RHC 2023 copy, 206 differ); old-template mapped (ML, CBT, BPT); HA SharePoint half (WVA/TB3 pattern; 16 delivered dead
  letters; Box Draw cell); mapping gaps (WR, MCD, OVH, CBT-MC, LR location).

## `25 - Budgets` group — GWT (14), OHR (82), ML (74) — 2026-10-10 — MOVED
- Decision (Chris): use the existing `25 - Budgets` folders as they are (no rename to `12`).
- GWT → Box 388322331806 / SP 017ZKYN56PEL7HX4URZZFKC4WPKE2J3UXE; OHR → Box 388360228706 / SP 017ZKYN5ZR2K6LU3IC5NE24R6BHCK2WE6Z;
  ML → Box 388356035859 / SP 017ZKYN5YCKAHPY2BI5RAITOTVJLN7IWFJ. All verified (Box sha1+version, SP versions+lastModified unchanged).
- ML location only. ML's old-template mapping question (exception group 4, with CBT and BPT) is still OPEN.
- Canaries: Box 25/0 missing; SP list_worksheets(74) = 200. GWT-STORM's routing (into GWT's `25 - Budgets`?) is in group 1, still open.

## MPR (121), RHC (110) — 2026-10-10 — MOVED (prior-version collisions resolved)
- Both target `12 - Budgets` folders already held an OLDER copy of the same-named tracker (MPR 2022: 1,428 cells differ; RHC 2023:
  206 differ). The `225` copies were current and identical across Box/SP. Decision (Chris): rename the older copies in place to
  `… (prior version).xlsx` on BOTH platforms (Box content unchanged by rename), then move the current trackers in. All verified.
- Both dormant (unmapped). No outbox, no formula work.

## Old-template mapped — ML (74), CBT (101), BPT (56) — 2026-10-10 — decision "same as CC"
- Mappings LEFT as-is (Box refuses pushes via U-559; SP writes one column off → hand repair each time).
- ML SP repaired: r2101, r2103–2108 shifted one column left (7 bills, $2,210 — mirrors the 2026-09-29 Box-only repair); r2248
  (duplicate Dale INV41290 $540, key 0771EBB3 → no line item) blanked keeping its key, mirroring Box r3668. Graph-computed:
  subtotal M2136 $25,438.92 → $27,648.92 and TOTAL M2199 $1,409,088.66 → $1,411,298.66 (+$2,210 each). ML file moved earlier
  (`25 - Budgets`).
- CBT SP repaired: r1120 (Engineered Comfort 43382, $5,466.09), r2398 ($62.50), r2399 ($32.50) shifted left; duplicate rows
  r2400/r2401 (repeat keys 149AFCAA/A6BF9428) cleared entirely. Graph-computed: M1149 +$5,466.09, M2440 +$95.00, TOTAL M2469
  +$5,561.09. CBT moved into `CBT 5313 Cherry Blossom Trail--2021--36/12 - Budgets` on both, verified.
- OPEN decision: CBT Box r1120 shows $0 for bill 43382 (Price NULL, Amount $5,466.09 in dbo — the frozen-$0 class); SP now shows
  $5,466.09. Price≠Amount is a decision item.
- BPT moved into `BPT 4706 Belmont Park Terrace--2022--01/12 - Budgets` on both, verified. OPEN decision: its 3 SP-synced rows
  (r1266–1268: Home Depot QBO-76012 $252.46; Elmer/Selvin 2026.09.15 labor, Price $97.50/$168.75 vs Amount $65/$125) sit BELOW the
  draw block in the modern layout; BPT's sheet is the FP-2022 layout (A CODE `65.1/65.2`, B CATEGORY, F DATE, G PAYABLE TO,
  H INVOICE #, I DESCRIPTION, J Ck, K AMOUNT PAID). Placement + Price-vs-Amount need Chris.
- CBT Box decision (Chris): set M1120 to the bill amount → $0 → $5,466.09 (only that cell); v2767892996931 → v2794551160398
  (rollback = 2767892996931). Box and SP now agree on CBT's three synced bills.
- BPT SP decision (Chris): place the rows in BPT's own layout; labor at PRICE. Home Depot QBO-76012 $252.46 → r1109 (65.1 spare);
  Elmer 2026.09.15.BPT $97.50 → r1141, Selvin $168.75 → r1142 (65.2 spares); F=date, G=vendor, H=inv#, I=desc, J=type, K=amount,
  key in Z, E (draw) blank. Stray rows r1266–1268 cleared. Graph-computed: K1156 section-65 $13,318.31 → $13,837.02 and TOTAL K1242
  $411,319.11 → $411,837.82 (+$518.71 each). BPT's Box copy (unmapped) is unchanged — BPT Box mapping is a mapping-gap item.

## HA — project 128 — 2026-10-10 — COMPLETE (WVA/TB3 treatment)
- SP-only key 99E4DE46 checked before refresh: a LIVE expense line, but a DUPLICATE — expense QBO-76778 ($715) has two line items in
  dbo (99E4DE46 created 2026-09-23; 00D87E0A created 2026-10-02); Box carries 00D87E0A at r49 drawn on HA-08. Refresh loses no real
  cost. The dbo duplicate is BOOKED as a P1 in api TODO.md.
- Box Draw cell F2021 `=F2012-F2000` → immune (fee 90 = BUILDER'S FEE); v2792138031856 → v2794534134853 (rollback 2792138031856).
- 16 dead letters replayed (60s stagger) → all done, version unchanged.
- SP refreshed from Box: v681 → v682 (rollback = 681); 42,591 cells compared, 0 differ (SP was behind on HA-04..HA-08 by ~14% each —
  Box's hand-entered fee rows). Moved into `HA - 206 Haverford Ave/12 - Budgets` (017ZKYN52XTBRU4V5KFRFJC5TDCKFZABA3).
- Canaries: Box 25/0 missing; SP list_worksheets(128) = 200.

## No-own-folder routing — 2026-10-10 — MOVED (dormant, move-only)
- Decision (Chris): ONT-NEW (87), ONT-OLD (88), ONT-SW (89) → ONT-MAIN's `12 - Budgets` (Box 388368289312 / SP
  017ZKYN5Y32WZYB2WJIBDK52O73AS2A2UV; same 2442 Old Natchez Trace address). GWT-STORM (15) → GWT's `25 - Budgets` (Box 388322331806 /
  SP 017ZKYN56PEL7HX4URZZFKC4WPKE2J3UXE). Targets and name collisions pre-checked; all moves verified on both platforms.
- Still in `225`, decision OPEN: CCR-BARN (123), CCR-STABLES (124), HSR (37) — project rows but no folder anywhere; TB-POST — no
  project row, no folder; Joe Dirrte — folder exists, no project row (create its `12 - Budgets`?).
- Decisions (Chris, 2026-10-10): CCR-BARN, CCR-STABLES, HSR → LEAVE in `225` (no project folders). TB-POST → TB (project 84,
  "TB - 1645 Tyne Blvd", same address) folder `250 - Completed Rogers Build Projects/2019--02--TB 1645 Tyne Blvd/12 - Budgets`
  (existing; Box 388273066956 / SP 017ZKYN56MARRZLFFUKBG3TLD2WXQOV7XB). Joe Dirrte → `12 - Budgets` CREATED (Box 426284197566 /
  SP 017ZKYN5YUDO46NFYWSRDJL4SHNM2LSEH3). Both moved + verified on both platforms.
- WSR (113): dropped from the standard batch list by mistake; moved 2026-10-10 into its own `12 - Budgets` on both platforms, verified.

## WR — project 203 — 2026-10-10 — MAPPED (Box-only project; no SharePoint folder)
- Workbook `WR - 5224 Williamsburg Rd - Budget Tracker.xlsx` (Box 2501605993330), created by Chris 2026-10-02 in
  `WR 5224 Williamsburg Rd/12 - Budgets` as a clone of the converted master: layout guard PASS, 458 immune formulas, 0 range SUMs,
  0 data rows, 0 keys.
- Mapped via BoxProjectWorkbookService.map_workbook (sheet DETAILS) and BoxProjectFolderService.map_project: invoices →
  `14 - Invoices` (401850661358), draw_requests → `15 - Draw Requests` (401849223680). Box canary: 136 folders / 25 files checked,
  0 missing. (Workbook count in the canary is the box.File registry; WR registers on its first push.)
- OPEN: back-fill WR's pre-mapping activity (2 completed bills, 5 completed expense lines, Sep 18–27) — SAFE here because the sheet
  is empty (no hand-entered rows to duplicate). And dbo.Project.Abbreviation is NULL → next invoice number would be INV-1.
- WR decisions (Chris, 2026-10-10): (1) BACK-FILL pre-mapping activity incl. PDFs to `14 - Invoices` (safe: empty sheet, no
  hand-entered rows — an explicit, decided exception to the forward-only rule). Enqueued via the completion flow's own helpers
  (BillService/ExpenseService `_enqueue_box_excel` + `_enqueue_box_uploads`, project 203 lines only) for 2 completed bills
  (G&M Plumbing 20261023 $195; Robinson Demolition 2103 $19,500) and 5 completed expenses (QBO-76214/18/19/25/26; each also has a
  project-less line, correctly skipped) → box.Outbox 10701–10714 (7 update_box_excel + 7 upload_box_file). Draft QBO-76574 left to
  push on its own completion. Expense receipt PDFs keep their raw Ramp filenames (existing behaviour of the expense upload helper).
  (2) dbo.Project 203 Abbreviation NULL → 'WR' via `scripts/run_sql.py` (guarded `WHERE Abbreviation IS NULL`), verified — first
  invoice will number WR-01.
- WR receipts: Chris rejected raw Ramp filenames. The 5 raw-named uploads (10706/08/10/12/14) were cancelled while still pending
  (guarded Status='pending'); re-enqueued 10715–10719 named by the expense completion flow's own convention block
  (`_upload_attachments_to_module_folder`, verbatim), e.g. `EXP - WR - VaVia Nashville, LLC - QBO-76226 - Portable Toilet - 3.00 -
  $200.00 - 09-03-2026.pdf` (+ the Box enqueue's identity suffix, same as bills). Code fix booked in api TODO.md (P2).
- WR back-fill VERIFIED 2026-10-10: all live rows (7 update_box_excel + 2 bill uploads + 5 re-named receipt uploads) done. DETAILS
  holds exactly 7 keyed rows = the 7 completed WR line items, each in its cost-code section (2.00, 2.01 ×2, 3.00 ×2, 6.00, 7.04) with
  price matching dbo; 0 extras. `14 - Invoices` holds exactly 7 PDFs, all convention-named; no raw `receipt_…` names. WR COMPLETE.

## OVH — project 145 (Box-only) — 2026-10-10 — steps 1–4 approved by Chris
- STEP 1 folders: created `14 - Invoices` (426352465842) + `15 - Draw Requests` (426354247057) under `OVH - 2031 Overhill Dr`;
  moved draw folders `OVH-01` (17 items) + `OVH-02` (3 items) into `15`; invoices mapping re-pointed root → `14` (unmap_project +
  map_project); draw_requests mapped → `15`.
- STEP 2 tracker (2331128356630, cloned from the master on 2026-09-04 — six days BEFORE the master's conversion, hence positional;
  20 rows hand-inserted in Excel from r313, so a template cell-copy was impossible): converted BY RULE to the master's exact immune
  text — 454 lookups `=SUMIFS(Budget!F:F,Budget!C:C,INDEX($D:$D,ROW()))` (0 drifted → value-preserving), 68 subtotals × F/N/O
  (each range asserted clean: own category only, ends at r−1, swallows no SUBTOTAL), 3 grand totals (listed all 68 → value-
  preserving), tail Payments/Balance/draw/fee/total (template text; Payments range asserted to pick up no extra numbers). 666 planned
  = 666 changed, 0 off-target, 0 positional left. v2713056508305 → v2795051559992 (rollback = 2713056508305).
- STEP 3 mapped (sheet DETAILS).
- STEP 4 back-fill: content match (vendor+amount) of the 28 completed OVH lines vs the hand-entered sheet → 13 present, 15 absent,
  every source bill either fully present or fully absent (no mixed). Enqueued update_box_excel for the 15 fully-absent bills only
  (14 crew-labor bills, 65.02, $5,282.04; Nacho's Painting 457366 $4,690) + upload_box_file for ALL 28 lines' PDFs to `14 - Invoices`
  (bills via BillService._enqueue_box_uploads; expenses named by the expense completion convention block, verbatim) → box.Outbox
  10720–10762. Draw re-stamp for OVH-01/OVH-02 to follow once inserts drain.
- STEP 4 VERIFIED: 43 rows done (15 inserts + 28 uploads), then draw stamps OVH-01 (28 pairs, outbox 10763) + OVH-02 (1 pair,
  10764) done. Tracker now holds 15 keyed rows, all inside their cost-code sections above TOTAL EXPENSES (r1537), 0 dup keys, all
  tagged OVH-01 (Nacho 457366 in code 49; 14 crew-labor rows in 65.02). The 13 hand-typed rows untouched. `14 - Invoices` holds 28
  PDFs, all convention-named.
- Draw totals after: OVH-02 tracker $1,280.00 = invoice $1,280.00 ✓. OVH-01 tracker $7,857.46 → $17,849.50; invoice OVH-01 lines
  total $18,794.51 (of which $16,789.78 carry a source bill/expense), invoice total $20,348.42 (difference = fee). Residual
  $945.01 between the tracker's OVH-01 and the invoice's lines is BOOKED (api TODO.md), not guessed.
- OVH COMPLETE.

## LR — project 100 — 2026-10-10 — COMPLETE (move-only, from the project ROOT)
- Both copies sat at the project root (`LR - 1833 Laurel Ridge Drive/`), not in `225`. Box (2272615552558) fully immune (U-438
  drift since fixed); SP positional, zero drift; all 7 draw tags + 27 keys identical across copies; no outbox rows.
- Moved into existing `12 - Budgets` on both: Box 388356479483 / SP 017ZKYN52ILBZ5C6O7KVEKBWYII4E7OHZZ. Verified; cache rows refreshed.

## BPT — project 56 — 2026-10-10 — COMPLETE (steps 1–4 approved)
- STEP 1: Box tracker (2272206292552, last edited 2026-06-11) refreshed from the SharePoint copy (current; carries today's 3 placed
  rows): v2523482524211 → v2795183015197 (rollback = 2523482524211), byte-identical to SP, 5,507 cells / 0 differ. Removed six stray
  test values in DETAILS G20–G29 ("Test One"…"Test six", Box-only, from the June 11 edit).
- STEP 2: Box tracker deliberately NOT mapped — 2022 layout; the U-559 guard would refuse every push. SharePoint stays the synced
  copy (CC-style hand repairs, per Chris).
- STEP 3: Box doc folders mapped — invoices → `14 - Invoices` (388308571087), draw_requests → `15 - Draw Requests` (388300979990).
- STEP 4: no PDF back-fill — 151 historical sources; `14 - Invoices` already holds 145 migrated originals.
- Box canary: 139 folders checked, 0 missing.


## MCD — project 119 — 2026-10-10 — COMPLETE (steps 1–5 approved)
- Finding: MCD DOES have a Box tracker (2272892212528, Box `25 - Budgets`, last edited 2026-06-08) — it never sat in `225`.
  Both copies are the 2022 layout (`TRACKING DETAILS`, amount col K). U-559's guard PASSES this layout (booked P2).
- STEP 1 SP repair: the sync had appended 13 rows (3 duplicate keys) below TOTAL (r1392) at r1420–1432 in modern positions. Placed the
  10 distinct lines in MCD's own layout: 9 crew-labor bills (price) → 65.002 Misc. Labor spares r1270–1275, r1305, r1306, r1316;
  Sherwin-Williams QBO-75550 $25.74 (coded category 49) → 49.001 spare r902. Cleared r1420–1432. Graph-computed: K1346 (65)
  +$1,655.90, K913 (49) +$25.74, TOTAL K1392 +$1,681.64 — exact.
- STEP 2 Box tracker refreshed from repaired SP: v2516177264672 → v2795208623054 (rollback = 2516177264672), byte-identical.
- STEP 3 Box tracker NOT mapped (2022 layout). Guard gap + missing SP guard booked P2 in api TODO.md.
- STEP 4 Box draw_requests → `40 - Draw Requests` (388356692947) mapped; invoices → `35 - Invoices` already mapped. No PDF back-fill.
- STEP 5 dbo.Project 119 Abbreviation NULL → 'MCD' via scripts/run_sql.py (guarded), verified.

## CBT-MC (81) + CBT (101) — 2026-10-10 — COMPLETE (steps 1–4 approved)
- STEP 1: no CBT-MC tracker exists anywhere (small closed-out sub-project: 2 bills, 12 expenses, invoices CBT-MC-01/02 from 2024;
  packets already in CBT's `15 - Draw Requests/CBT-MC-0x`). No action.
- FINDING: CBT (101) — active, 612 completed bills — had its Box WORKBOOK mapped but NO Box folder mappings, so no CBT AP PDF or
  packet had been pushed to Box since go-live (2026-06-16).
- STEP 2: mapped BOTH projects (shared sub-unit pattern) → CBT's `14 - Invoices` (388328578827) + `15 - Draw Requests` (388316807471).
- STEP 3 back-fill (content-checked): CBT/CBT-MC documents sent to SharePoint since 2026-06-16 = 3 bills (Engineered Comfort 43382
  HVAC; Selvin + Ricardo 2026.08.31 labor), none present in Box 14 by convention-name match; 0 invoices created since → no packets.
  Pushed via BillService._enqueue_box_uploads → box.Outbox 10765–10767 done; Box 14 now 741 files, all 3 present.
- STEP 4: dbo.Project 101 → 'CBT', 81 → 'CBT-MC' via scripts/run_sql.py (guarded, no collisions), verified.

## MIGRATION STATUS — 2026-10-10
- `225 - Budget Trackers` on both platforms now holds only the master template + CCR-BARN, CCR-STABLES, HSR (left by Chris's decision).
- All mapping gaps closed (WR, OVH, BPT, MCD, CBT-MC/CBT, LR). Open follow-ups live in api TODO.md (duplicates, guard gap, expense
  Box filenames, HA duplicate expense line, OVH-01 residual, SSC2-05 extra row, FP/TB3 out-of-template rows, CC/ML/CBT/BPT/MCD
  recurring hand repairs).
