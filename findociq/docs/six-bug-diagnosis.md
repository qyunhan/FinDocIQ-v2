# Six KPH binding misses — diagnosis

Read-only investigation, 2026-08-04, branch `mapping/close-logged-items`. No
writes. No fixes applied.

## Headline: the framing does not survive contact with the data

**All six authored anchors resolve PASS.** `resolve_anchors.py` reports
`PASS=72 PENDING_EXTRACTION=3 FAIL=0` — none of the six is a resolution failure.
The misses are downstream, and they split into three unrelated causes, only one
of which is a real pipeline bug:

| # | Reported | Actual |
|---|---|---|
| 1 | DBS Customer deposits = 4,579 | **Real bug.** Wrong row wins a 5-way conflict. |
| 2 | DBS Total equity blank | **Not a bug.** Renders 68,916 on the `as_at` basis. |
| 3 | UOB Gross customer loans blank | **Config mismatch.** Dashboard asks for `_net`, anchor is `_gross`. |
| 4 | OCBC Total liabilities blank | **Not a bug.** Renders 612,118 on the `as_at` basis. |
| 5 | OCBC CET1 = blank/15.1, want 16.9 | **Real bug**, and an instance of a 29-row class. |
| 6 | OCBC NAV/ord share blank | **Not a bug.** Renders 13.38 on the `as_at` basis. |

Three of the six (2, 4, 6) were compared against the **Full-year** column. They
are balance-sheet stock items carrying `period_span='as_at'`, and
`filter_by_basis('fy')` correctly excludes them. On the `as_at` basis at
31-Dec-25 they read exactly the expected figures:

```
          Total equity   Total liabilities   NAV/share
  DBS         68,916            828,572         (n/a)
  OCBC        63,570            612,118          13.38
  UOB         51,493            520,568          29.36
```

Bug 2 wanted ~68,916 → **68,916**. Bug 4 wanted OCBC total liabilities →
**612,118**. Bug 6 wanted 13.38 → **13.38**. Nothing to fix in the data.

---

## The class-of-bugs finding (bug 5, and 28 more rows)

`stamp_human_anchors()` (`migrate_serving_views.py:159`) projects
`human_confirmed` `bank_line_map` bindings onto `row_dim`. Its docstring is
explicit: *"Additive: never overwrites resolve_deterministic's
`row_dim.concept_key`, only fills the `_human` columns."* It writes
**`concept_key_human`**.

`build_fact_metric.py` contains **zero references to `concept_key_human`**:

```
$ grep -c concept_key_human pipeline/concept/build_fact_metric.py
0
```

So a human-authored anchor landing on a row whose deterministic `concept_key`
is NULL produces **no fact**. The binding exists, is `human_confirmed`, is
correctly projected — and is invisible to the serving layer.

Corpus-wide:

```sql
SELECT COUNT(*) FROM row_dim
 WHERE identity_source='human_anchor' AND concept_key IS NULL;          -- 29
SELECT COUNT(*) FROM row_dim
 WHERE identity_source='human_anchor' AND concept_key IS NULL
   AND concept_key_human IS NOT NULL;                                   -- 29
```

**All 29** carry a `concept_key_human` that no downstream consumer reads:

| Bank | concept | rows |
|---|---|---|
| DBS | `pnl.eps.basic`, `pnl.eps.diluted` | 4 + 4 |
| OCBC | `pnl.eps.basic`/`diluted` 2+2, `bs.nav_per_share` 1, `pnl.profit.net_attributable` 1, **`reg.capital.cet1_ratio` 1** | 7 |
| UOB | `pnl.eps.basic`/`diluted` 3+3, `bs.assets.customer_loans_gross` 1, `bs.assets.total` 1, `bs.equity.shareholders` 1, `bs.liabilities.customer_deposits` 2, `bs.nav_per_share` 1, `pnl.income.total` 1, `reg.capital.cet1_ratio` 1 | 14 |

This is one fix, not 29.

### Compounding factor — the runbook omits the re-stamp

`stamp_human_anchors` is reached only via `migrate_serving_views.migrate()`
(`:266`), which runs inside `concept/run.py` — **run_doc STEP 4a**. The runbook
in `docs/ingest-inventory.md` §6 goes `load_anchors` → `build_fact_metric`,
skipping STEP 4a entirely. So today's re-hydration wrote 15 bindings into
`bank_line_map` that were never re-projected onto `row_dim` at all. Even with
the `concept_key_human` fix, the runbook would need the re-stamp step.

---

## Per-bug detail

### Bug 1 — DBS Customer deposits = 4,579 (want ~610,023)

**resolve_anchors: PASS.**
`table_id=overview_selected_balance_sheet_items_m_2025-12-31`,
`row_lineage_id=239`, `matched=(None,'Customer deposits')` — the correct row.

**Smoking gun — five rows carry this concept for DBS @2025-12-31:**

```
FS_NII_DETAIL        'Customer deposits'  net_interest_income_average_balance_sheet_2nd_half_2025_…
FS_NII_DETAIL        'Customer deposits'  net_interest_income_average_balance_sheet_year_2025_year_2024_…
FS_NII_DETAIL        'Customer deposits'  net_interest_income_volume_and_rate_analysis_m_2nd_half_…
FS_NII_DETAIL        'Customer deposits'  net_interest_income_volume_and_rate_analysis_m_year_2025_…
FS_BALANCE_SELECTED  'Customer deposits'  overview_selected_balance_sheet_items_m_2025-12-31   <- authored
```

Four of the five are **average-balance-sheet / volume-and-rate** rows — interest
expense analysis, not balance-sheet stock. `fact_metric` resolution:

| span | value | `n_candidates` | `resolved_by` | source table |
|---|---|---|---|---|
| 2H | **4,579** | 3 | `conflict` | `net_interest_income_average_balance_sheet_2nd_half_2025…` |
| FY | **9,774** | 3 | `conflict` | `net_interest_income_average_balance_sheet_year_2025_year_2024` |
| 4Q | 610,023 | 1 | `single` | `selected_balance_sheet_items_m_…_2026-03-31` (the 1Q26 doc) |

The correct 610,023 exists only at `4Q`, sourced from the **1Q26** document's
comparative column. The 4Q25 document's own `FS_BALANCE_SELECTED` row is stamped
but produces no `as_at`/FY/2H fact — DBS Customer deposits is blank on the
`as_at` basis while OCBC (428,286) and UOB (425,938) both render.

**Root cause: D — section/table-level ambiguity.** Five rows in three different
table types share one `concept_key`; the conflict resolver has no rule
preferring a stock table over an average-balance table.

**Recommended fix (not applied):** row-level data fix — narrow the concept stamp
so `FS_NII_DETAIL` average-balance/volume-rate rows do not carry
`bs.liabilities.customer_deposits`. A `prefer_table` rule favouring
`FS_BALANCE_SELECTED` for stock concepts would also work and generalises better.
Residual to confirm: why the 4Q25 `FS_BALANCE_SELECTED` row yields no fact.

### Bug 2 — DBS Total equity "blank"

**resolve_anchors: PASS.** `audited_balance_sheets_…_2025-12-31`,
`row_lineage_id=323`, with `[WARN: table_type_id not in table_catalog for this
bank/section]`.

`fact_metric` has two rows, correctly split by legal entity:
`CONSOLIDATED 68,916` and `PARENT_COMPANY 17,643`. `v_fact_metric_serving`
returns exactly one: **68,916 CONSOLIDATED**, `period_span='as_at'`.

**Root cause: F — not a defect.** Blank only under the FY basis toggle. The
`WARN` is cosmetic (a `table_catalog` coverage gap for the statutory balance
sheet), not the cause.

**Recommended fix:** none. Optionally add `FS_BALANCE_STATUTORY` to
`table_registry_seed.csv` for DBS to silence the warning.

### Bug 3 — UOB Gross customer loans blank (want 352,180)

**resolve_anchors: PASS.** `FS_HIGHLIGHTS_COMBINED`, `row_lineage_id=2217`,
`matched=(None,'Gross customer loans')`. `fact_metric` holds **352,180** at
`as_at`, FY and 2H.

**The value is present and correct — the dashboard never asks for it.**

```
highlights.yaml:157   label: "Net customer loans"
                      concept: bs.assets.customer_loans_net
lineage_identity_map  UOB anchor concept_key: bs.assets.customer_loans_gross
```

The CSV's own `review_flag` records the decision: *"BASIS MISMATCH: canonical
says NET, anchor is GROSS customer loans | concept_key renamed net->gross per
policy confirmation."* That rename was never propagated to `highlights.yaml`,
which still requests `_net`. UOB shows 347,877 on the `as_at` basis — a
different, `_net`-bound row — and blank on FY.

**Root cause: F — config drift between two authored files.**

**Recommended fix:** `highlights.yaml` edit — decide gross-vs-net as a
dashboard-level policy and make the two files agree. Note this is a *semantic*
choice (352,180 gross vs 347,877 net), not a formatting one.

### Bug 4 — OCBC Total liabilities "blank"

**resolve_anchors: PASS.** `row_lineage_id=269`.
`fact_metric`: `CONSOLIDATED 612,118` (`n_candidates=2`, `conflict`) and
`BANK_SOLO 391,664` (`twin_collapse`). Serving view returns **612,118**,
`period_span='as_at'`.

**Root cause: F — not a defect.** Same basis-toggle artifact as bug 2.
**Recommended fix:** none.

### Bug 5 — OCBC CET1 = 15.1 (want 16.9)

**resolve_anchors: PASS.** `performance_ratios_financial_highlights_continued_2025-12-31`,
`row_lineage_id=1740`, `matched=('Capital Adequacy Ratios','Common Equity Tier 1')`.

**Smoking gun — the authored row carries no usable concept:**

```
row_leaf_label     = 'Common Equity Tier 1'
row_parent         = 11
concept_key        = None            <- what build_fact_metric reads
concept_key_human  = 'reg.capital.cet1_ratio'   <- what stamp_human_anchors wrote
identity_source    = 'human_anchor'
```

Only **two** OCBC rows carry `reg.capital.cet1_ratio` in `concept_key`, both in
`strong_funding_liquidity_and_capital_position` (`FS_HIGHLIGHTS_COMBINED`):
`'Transitional final Basel III reforms 1/'` and `'Fully phased-in final Basel III
reforms 2/'`. The fully-phased-in variant won (`n_candidates=2`,
`resolved_by=conflict`) → **15.1**. The authored 16.9 row was never in the race.

`bank_line_map` is correct: `[human_confirmed] FS_RATIOS_KEY
('common_equity_tier_1', 'capital_adequacy_ratios') <- dashboard_rows.yaml`,
alongside 15 `ai_proposed` rivals from `backfill:corpus`.

**Root cause: F — `concept_key_human` written but never read** (the 29-row class
above), compounded by **D** (two Basel III variants competing for the fallback).

**Recommended fix:** code change in `build_fact_metric.py` — read
`COALESCE(concept_key, concept_key_human)`, or have `stamp_human_anchors` write
`concept_key` when it is NULL. Either fixes all 29 rows. Not applied.

### Bug 6 — OCBC NAV per ordinary share "blank" (want 13.38)

**resolve_anchors: PASS.** `balance_sheets_…_2025-12-31`, `row_lineage_id=492`,
`matched=('ASSETS','Net asset value per ordinary share – S$')`.

`fact_metric`: `CONSOLIDATED 13.38` and `BANK_SOLO 9.1`, both `single`.
Serving view returns **13.38** at `as_at`. Renders correctly on that basis.

Worth noting the en-dash and the `– S$` currency suffix resolved **fine** —
`normalize_row_label` handled them, and the authored `parent_row=''` still
matched the physical parent `'ASSETS'`. The hypothesised "normalize doesn't
strip currency suffixes" failure mode did not occur.

**Root cause: F — not a defect.** **Recommended fix:** none.

---

## Cross-cutting observation

**Not six independent bugs — three groups, and the largest is one fix.**

1. **Three non-bugs (2, 4, 6).** Balance-sheet stock items at
   `period_span='as_at'`, compared against the FY column. The dashboard defaults
   to the `as_at` basis; the FY view is a deliberate filter. If the comparison
   against `1_Highlights.xlsx` was done on one basis toggle, **re-run it per
   basis before filing further misses** — that alone accounts for half this list.
   A genuine usability question sits underneath: a reader on the FY basis sees
   blank cells with no hint that the figure exists one toggle away.

2. **One config drift (3).** Two authored files disagree on gross-vs-net for
   UOB. Both are internally consistent; nothing in the pipeline detects the
   mismatch, because `highlights.yaml` and `lineage_identity_map.csv` have no
   cross-check. This is the same class as the `table_registry_seed.csv` vs
   `table_registry.yaml` split already recorded in
   `docs/specs/2026-08-04-masterlist.md` §2 — **two hand-authored files, no
   reconciliation.**

3. **Two real pipeline bugs (1, 5), both about competing `concept_key` stamps.**
   Bug 5 is the more valuable: `concept_key_human` is written by
   `stamp_human_anchors` and read by nobody, silently orphaning **29 rows** — the
   entire mechanism by which a human anchor is supposed to override machine
   resolution is disconnected from the serving layer. Bug 1 is the same shape
   without the human-anchor wrinkle: no rule prefers a stock table over an
   average-balance table.

**The hypothesised class-fix — "normalize_row_label doesn't strip currency
suffixes like `– S$`" — is not supported.** Bug 6's `'Net asset value per
ordinary share – S$'` resolved correctly, en-dash and all. Normalization is not
implicated in any of the six.

**Priority if fixing:** the `concept_key_human` read (one change, 29 rows,
includes bug 5) → the `highlights.yaml` gross/net decision (bug 3, a real
number changes) → bug 1's conflict rule. Bugs 2, 4, 6 need nothing.

---

## Scope note

Read-only. `resolve_anchors.py` was executed but is a reporting tool that writes
nothing. No file, script, config, or DB row was modified.

Related: `docs/runbook-execution-2026-08-04.md` (today's re-hydration),
`docs/m3-store-relationship.md` (the three `concept_key` writers),
`docs/ingest-inventory.md` §6 (the runbook missing STEP 4a).
