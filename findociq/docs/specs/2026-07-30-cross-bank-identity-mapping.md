# Cross-bank identity mapping — Key Performance Highlights (FY2025)

Status: **mapping verified against the DB; 3 semantic decisions OPEN; 6 items blocked.**
Date: 2026-07-30
Scope: EXACTLY the 26 identity items the user supplied. Other rows in each bank's
key-financial tables are deliberately out of scope and are not gaps.

Source documents (all `doc_period = 2025-12-31`):

| bank | doc_id | state |
|---|---|---|
| DBS | `DBS_4Q25_performance_summary` | 45 tables / 744 rows |
| UOB | `UOB_4Q25_condensed-financial-statements` | 44 tables / 755 rows |
| OCBC | `OCBC_4Q25_Condensed_Financial_Statements` | 41 tables / 628 rows |
| OCBC | `OCBC_4Q25_Media_Release_and_Financial_Highlights` | **53 tables / 866 rows — RE-EXTRACTED 2026-07-30, now loads** |

The Media Release previously failed to load
(`financial_highlights_continued… row 4: cell 6 has no leaf column (row width 6 >
5 columns)` — a malformed model output no deterministic step could repair). It was
re-extracted 2026-07-30 and now loads; items 18-23 are UNBLOCKED. Caveats:

- STEP 5 verify still fails on ONE table, `fy25_performance_highlights`, after 2
  auto-re-extract rounds. The other 52 tables verified. Not blocking the mapping,
  but that table's values are unconfirmed against the PDF.
- The ratio rows are DUPLICATED across ~8 table_ids (`performance_ratios_…`,
  `revenue_mix_efficiency_ratios_…`, `capital_adequacy_ratios_8_9_…`,
  `leverage_ratio_5_8_9_…`, `net_stable_funding_ratio_7_8_…`, etc.). The Financial
  Highlights block was split into many tables that each repeat the same ratio
  rows. The mapping must PIN one table_id, not label-match.
- Values verified identical across the duplicates; this doc's ratio source of
  record for the mapping is `revenue_mix_efficiency_ratios_financial_highlights_continued_2025-12-31`,
  column `2025` (`col_period` 2025-12-31).

## The mapping (FY2025 values, Group/consolidated)

| # | Identity | DBS | UOB | OCBC |
|---|---|---|---|---|
| 1 | Net interest income | **14,500** ⚠1 | 9,355 | 9,150 |
| 2 | Net fee and commission income | 4,898 | 2,569 | 2,411 |
| 3 | Other non-interest income | **3,502** ᴰ | 1,884 | **3,053** ᴰ |
| 4 | Total income | 22,900 | 13,808 | 14,614 |
| 5 | Operating expenses | 9,249 ⚠2 | 6,157 | 5,882 |
| 6 | Operating profit | 13,651 ⚠2 | 7,651 | 8,732 |
| 7 | Amortisation of intangible assets | 23 | 31 | 21 |
| 8 | Allowances for credit and other losses | 791 | 2,042 | 665 |
| 9 | Share of profits/losses of associates and JVs | 262 | 79 | 1,077 ⚠4 |
| 10 | Profit before tax | 13,099 ⚠2 | 5,657 | 9,123 |
| 11 | Net profit | 11,033 ⚠2 | 4,682 | **7,560 or 7,422** ⚠3 |
| 12 | Net Customer Loans | 445,011 | **347,877 net / 352,180 gross** ⚠5 | 336,692 |
| 13 | Customer deposits | 610,023 | 425,938 | 428,286 |
| 14 | Total assets | 897,488 | 572,061 | 675,688 |
| 15 | Total liabilities | 828,572 | 520,568 | 612,118 |
| 16 | Total equity | 68,916 | 51,493 | 63,570 |
| 17 | Shareholders' equity | 68,867 | 51,248 | **61,768** ᴰ |
| 18 | Net interest margin | 2.01 | 1.89 | 1.91 |
| 19 | Cost/income ratio | 40.4 | 44.6 | 40.2 |
| 20 | Return on assets | 1.29 | 0.86 ⚠6 | 1.37 |
| 21 | Return on equity | 16.2 | 9.6 | 12.6 |
| 22 | NPL ratio | 1.0 | 1.5 | 0.9 |
| 23 | Common Equity Tier 1 | 17.0 | 15.1 | 16.9 |
| 24 | EPS — Basic | **3.88 or 3.84** ⚠2 | 2.76 | 1.63 |
| 25 | EPS — Diluted | **3.86 or 3.82** ⚠2 | 2.75 | 1.63 |
| 26 | Net book value / NAV per share | 24.29 | 29.36 | 13.38 |

ᴰ = DERIVED, not a printed row. Must go through `compute_ratios`'
`metric_kind: metric` FILL-ONLY path, NOT a `concept_map` alias.

Arithmetic verification passed independently for each bank's income-statement
waterfall — e.g. UOB 9,355 + 2,569 + 1,884 = 13,808; 13,808 − 6,157 = 7,651;
7,651 − 31 − 2,042 + 79 = 5,657. DBS: 14,494 + 6 = 14,500, and
14,500 + 4,898 + 3,502 = 22,900. OCBC route A/B for item 17 agree exactly
(61,768 = 63,570 − 676 − 1,126).

## OPEN DECISIONS (these change the numbers — do not guess)

**D1 — DBS: underlying or reported basis?** DBS's "Selected income statement
items" is the UNDERLYING basis; it excludes a 100m CSR provision and
reconciles to `Reported net profit` in the same table (rows 20-22).

| item | underlying (mapped) | reported/statutory |
|---|---|---|
| Operating expenses | 9,249 | 9,349 |
| Operating profit | 13,651 | 13,551 |
| Profit before tax | 13,099 | 12,999 |
| Net profit | 11,033 | 10,933 |
| EPS basic / diluted | 3.88 / 3.86 | 3.84 / 3.82 |

If UOB and OCBC are mapped from their statutory statements (they are), leaving
DBS on the underlying basis makes the cross-bank comparison
underlying-vs-reported. Same fork drives items 24/25.

**D2 — OCBC: which "Net profit"?** The supplied hint points at
`Profit for the period/year` = **7,560**, which INCLUDES non-controlling
interests. `Equity holders of the Bank` = **7,422** is OCBC's headline
attributable figure and already carries `pnl.profit.net_attributable`
(7,422 + 138 NCI = 7,560). DBS (11,033) and UOB (4,682) are both ATTRIBUTABLE,
so 7,560 would make OCBC the odd one out.

**D3 — UOB: net or gross customer loans?** The identity says NET; the supplied
UOB hint says `Gross customer loans`. Both rows exist and differ: 352,180 gross
vs 347,877 net (Δ 4,303 = GP 2,997 + SP 1,306). DBS (445,011) and OCBC (336,692)
are both NET, so net is the consistent choice. Note UOB's Financial Highlights
table carries ONLY gross — net must come from the Customer Loans table (p13).

## Hints that do not match the printed labels

| bank | hint | actual verbatim label |
|---|---|---|
| DBS | `Per share data` (table) | **The hint is CORRECT** — "Per share data" is a real sub-table inside DBS's key-financial-highlights exhibit. The DB has it mis-titled `DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES`: the extractor took the PAGE header as the table title. Model nondeterminism, not a document difference — the SAME exhibit in `DBS_1Q26_trading_update` extracted correctly as `Per share data ($)3`. Fixed by re-extraction, NOT by reload (the title comes from the model, not the loader) |
| DBS | `Net interest income` | THREE rows carry this label; the Group figure is `Of which: Net interest income` (row 10). Bare-label rows 3/7 are the commercial-book (14,494) and markets (6) components |
| UOB | `Total operating expenses` | in Financial Highlights it is `Less: Operating expenses`; the hint label exists only in the audited income statement |
| UOB | `Return on assets` | `Return on average total assets` (do not confuse with `Return on average risk-weighted assets`) |
| OCBC | `…associates and JVs` | `Share of results of associates, net of tax` — no JV mention |

## DATA DEFECTS found (real, in the DB, not mapping problems)

**Systematic across all three banks — same class each time:**

1. **`bs.assets.customer_loans_gross` stamped on NET values.** DBS `Customer
   loans` 445,011 (net, = audited `Loans and advances to customers`); UOB BS
   `Loans to customers` 347,877; OCBC BS `Loans to customers` 336,692 (proved
   net by note 12: 341,120 gross − 1,577 − 2,851 = 336,692). All three should
   be `bs.assets.customer_loans_net`.
2. **`bs.equity.shareholders` stamped on BOTH `Total equity` and
   `Shareholders' equity`.** UOB: rows carrying 51,493 and 51,248 share one key.
   OCBC: rows carrying 63,570 and 61,768 share one key. A concept-key lookup for
   shareholders' equity can return the wrong number. `Total equity` needs its
   own key.
3. **Unit stamped `S$m` on non-monetary cells.** UOB's Financial Highlights
   ratio rows (NIM 1.89, CIR 44.6, NPL 1.5, ROE 9.6, ROA 0.86, CET1 15.1) and
   per-share rows (EPS 2.76/2.75, NAV 29.36) all carry `cell_fact.unit='S$m'`.
   OCBC EPS (1.63) and NAV (13.38) likewise. Separately, OCBC's `per_share`
   unit is applied to exactly the wrong rows — the 6 cells stamped `per_share`
   are S$ MILLION distribution amounts in `8. Dividends/distributions`.
4. **Section-header rows carrying concept keys.** UOB BS bare headers `Equity` →
   `bs.equity.shareholders`, `Liabilities` → `bs.liabilities.total`, `Assets` →
   `bs.assets.total`. OCBC BS row 2 (header, no cells) → `bs.equity.shareholders`.
5. **UOB `Other non-interest income` (1,884) stamped `pnl.noninterest.total`.**
   1,884 is the RESIDUAL after fees; the true total is 4,453. This is exactly the
   double-count predicted in DECISIONS (2026-07-30, Highlights view) — now
   confirmed as a live mis-stamp.
6. **Row mis-parenting** (the class the geometry stage fixes, on documents that
   have not been re-loaded): UOB `Allowance for credit and other losses` is a
   CHILD of `Less: Amortisation of intangible assets` (siblings in the PDF);
   DBS audited BS `Non-controlling interests` parented to `Revenue reserves`;
   OCBC `Net asset value per ordinary share` parented to `ASSETS`.
7. **DBS_4Q25 FY columns have NULL `col_period`/`period_span`.** `Year 2025` and
   `Year 2024` both resolve to `period='2025-12-31'`, so FY2024 cells are stamped
   with the FY2025 date. **FY years cannot currently be separated by period on
   this document** — only by `col_leaf_label`/`col_id`. This is the period-grammar
   defect already FIXED in `load_v7` but not yet applied here; it clears on reload.

## Concept coverage gaps (within the 26 items only)

- `ratio.cir` and `ratio.roa` have **no OCBC rows at all, in any period or
  document**. Re-loading the Media Release is necessary but NOT sufficient —
  those two labels also need a mapping.
- OCBC has no FY2025 (`2025-12-31`) value for any of `ratio.nim` / `ratio.npl` /
  `ratio.roe` / `reg.capital.cet1_ratio`; the latest are 3Q25 / 9M25.
- `concept_key` is NULL on these mapped rows: Operating profit (all 3 banks),
  Amortisation (DBS, UOB, OCBC), Share of associates (all 3), Return on assets
  (DBS, UOB), EPS basic/diluted (all 3), NAV per share (all 3), OCBC `Profit for
  the period/year`.

## Next actions

1. Resolve D1, D2, D3 (user decisions — they change reported figures).
2. Re-extract `OCBC_4Q25_Media_Release_and_Financial_Highlights` (~$0.10) to
   unblock items 18-23. It is one of the three known `nt=0` documents.
3. Fix the mis-stamps (1, 2, 5) and the header-row stamps (4) — these are
   `concept_map` / stamping corrections, independent of the mapping.
4. The unit defects (3) need a loader look: the row-level unit on a parent
   (`Earnings per share (S$)`, `Key financial ratios (%)`) is not propagating to
   its leaves.
5. Items 3 (DBS, OCBC) and 17 (OCBC) are DERIVED — wire via `compute_ratios`
   `metric_kind: metric` FILL-ONLY, never as an alias.
6. The batch re-extraction sweep clears defect 7 and most of defect 6.
