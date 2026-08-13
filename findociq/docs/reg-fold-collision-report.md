# REG_* -> FS_RATIOS_KEY fold: collision report

Read-only analysis. No `bank_line_map` row, no `table_registry` row, and no migration script was modified to produce this report -- it only queries `db/compiled_fs.db` (read) and `data/derived/lineage_identity_map.csv` (read) and writes this one file.

**Scope:** the 4 legacy `table_type_id`s (`REG_LCR`, `REG_LEVERAGE`, `REG_NSFR`, `REG_KEY_METRICS`) scheduled to fold into `FS_RATIOS_KEY`, per the same duplicate-registry pattern already fixed for 6 other pairs (`pipeline/mapping/migrate_consolidate_table_type_ids.py`, see `docs/DECISIONS.md` 2026-08-04 for that precedent and why this 4-way fold was explicitly excluded from it).

## ⚠️ Scope correction — not all 4 legacy ids are actually FS

`table_t.table_type_id` naming alone does not say which document family a row came from -- checked directly against `document.doc_family`:

| table_type_id | `table_t` rows: financial_stmt | `table_t` rows: pillar3 |
|---|---|---|
| `REG_LCR` | 1 | 0 |
| `REG_NSFR` | 1 | 0 |
| `REG_LEVERAGE` | 1 | 10 |
| `REG_KEY_METRICS` | 0 | 5 |

And at the `bank_line_map` address level (recomputing each address's origin from `row_dim` with the same address logic `stamp_human_anchors` uses, since `bank_line_map` itself carries no `doc_family` column):

| table_type_id | FS-only | PILLAR3-only | unresolved |
|---|---|---|---|
| `REG_LCR` | 16 | 0 | 7 |
| `REG_LEVERAGE` | 16 | 61 | 7 |
| `REG_NSFR` | 16 | 0 | 8 |
| `REG_KEY_METRICS` | 0 | 44 | 0 |

**`REG_KEY_METRICS` is 100% Pillar 3 — it should not be part of this FS fold at all.** `REG_LEVERAGE` is mostly Pillar 3 (a real FS-scoped minority exists alongside a larger Pillar 3 majority). `REG_LCR`/`REG_NSFR` are cleanly FS.

This does **not** change section 2's 40 collisions below — every one of them traces to the FS-only slice (confirmed: `REG_KEY_METRICS` contributes zero collisions), so they were never contaminated. It **does** change section 3: the non-colliding rows include real Pillar 3 content that must NOT be folded into `FS_RATIOS_KEY`, split out explicitly below instead of lumped into one "fold cleanly" bucket as an earlier version of this report incorrectly did.

## Schema notes (read this before the per-collision tables)

- `bank_line_map` is **period-agnostic** and keyed by `(bank, table_type_id, row_label_norm, parent_label_norm)` -- there is no physical page/row/column position on this table (that lives in `row_dim`, per-document, not per-canonical-anchor). `existing_addr`/`proposed_addr` below are this LOGICAL address, not a PDF coordinate.
- `bank_line_map` has **no `reviewed_at`/`reviewer` columns**. The closest fields are `mapped_at` (timestamp) and `mapped_by` (a SOURCE string, e.g. `dashboard_rows.yaml` or `backfill:corpus` -- not a human name; only `map_status='human_confirmed'` rows genuinely had human eyes on them). Reported as `existing_reviewed_at`/`existing_reviewer` per the requested column names, but treat `mapped_by='backfill:corpus'` as machine-sourced, not reviewed.
- `bank_line_map` has **no `formula` column**. Formulas (where they exist) live in `data/derived/lineage_identity_map.csv`, keyed by `concept_key` (+ `bank`). Checked: **none of the 5 `reg.*` concepts (`reg.capital.cet1_ratio`, `reg.leverage_ratio`, `reg.liquidity.lcr_all_ccy`, `reg.liquidity.lcr_sgd`, `reg.liquidity.nsfr`) have a recorded formula** -- they're all printed/anchored values, not derived. Expect `existing_formula`/`proposed_formula` to be blank for nearly every row below; that's a correct read of the data, not a missing lookup.
- **Important asymmetry the task brief's framing doesn't fully capture**: of `FS_RATIOS_KEY`'s 143 rows, only **16 are `human_confirmed`** — the other **127 are `ai_proposed`**, the *same* review status as every REG_* row (175/175 `ai_proposed`, 0 `human_confirmed`). "Existing is reviewed, proposed isn't" only actually holds for collisions against one of those 16 rows; the rest is unreviewed-vs-unreviewed, and the verdict heuristic below treats those two cases differently.

## 1. Summary

- Total REG_* rows across all 4 legacy ids: **175**
  - `REG_LCR`: 23
  - `REG_LEVERAGE`: 84
  - `REG_NSFR`: 24
  - `REG_KEY_METRICS`: 44
- Non-colliding REG_* rows (fold cleanly, no existing `FS_RATIOS_KEY` address in the way): **106**
- Colliding REG_* rows (share an address with an existing `FS_RATIOS_KEY` row): **69**
- Verification: 106 + 69 = 175 == total REG_* rows (175): **OK**
- Distinct colliding addresses: **40** (a few addresses have more than one REG_* row landing on them, across different legacy ids or duplicate rows within one id, hence 69 rows over 40 addresses)
- `FS_RATIOS_KEY` existing rows: **143** total, of which 16 `human_confirmed`, 127 `ai_proposed`.

## 2. Per-collision detail (most divergent first)

### Collision 1/40 — OCBC / `basic_earnings` (parent: `earnings_per_share`)
- **collision_key**: bank=`OCBC`, row_label=`basic_earnings`, parent=`earnings_per_share`
- **existing_row_id**: 1697
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=earnings_per_share | row=basic_earnings
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: pnl.eps.basic
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/2]**: 1796
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=earnings_per_share | row=basic_earnings
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1819
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=earnings_per_share | row=basic_earnings
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 2/40 — OCBC / `common_equity_tier_1` (parent: `capital_adequacy_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`common_equity_tier_1`, parent=`capital_adequacy_ratios`
- **existing_row_id**: 1703
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=capital_adequacy_ratios | row=common_equity_tier_1
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: reg.capital.cet1_ratio
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/2]**: 1798
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=capital_adequacy_ratios | row=common_equity_tier_1
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1821
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=capital_adequacy_ratios | row=common_equity_tier_1
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 3/40 — OCBC / `cost_to_income` (parent: `revenue_mix_efficiency_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`cost_to_income`, parent=`revenue_mix_efficiency_ratios`
- **existing_row_id**: 1707
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=revenue_mix_efficiency_ratios | row=cost_to_income
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: ratio.cir
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/3]**: 1799
- **proposed_addr [1/3]**: OCBC | REG_LCR | parent=revenue_mix_efficiency_ratios | row=cost_to_income
- **proposed_source_table_type [1/3]**: `REG_LCR`
- **proposed_reviewed_at [1/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/3]**: (none)
- **proposed_formula [1/3]**: (none recorded)
- **proposed_note [1/3]**: no concept stamped in corpus
- **proposed_row_id [2/3]**: 1822
- **proposed_addr [2/3]**: OCBC | REG_LEVERAGE | parent=revenue_mix_efficiency_ratios | row=cost_to_income
- **proposed_source_table_type [2/3]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/3]**: (none)
- **proposed_formula [2/3]**: (none recorded)
- **proposed_note [2/3]**: no concept stamped in corpus
- **proposed_row_id [3/3]**: 1845
- **proposed_addr [3/3]**: OCBC | REG_NSFR | parent=revenue_mix_efficiency_ratios | row=cost_to_income
- **proposed_source_table_type [3/3]**: `REG_NSFR`
- **proposed_reviewed_at [3/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [3/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [3/3]**: (none)
- **proposed_formula [3/3]**: (none recorded)
- **proposed_note [3/3]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 4/40 — OCBC / `diluted_earnings` (parent: `earnings_per_share`)
- **collision_key**: bank=`OCBC`, row_label=`diluted_earnings`, parent=`earnings_per_share`
- **existing_row_id**: 1713
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=earnings_per_share | row=diluted_earnings
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: pnl.eps.diluted
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/2]**: 1800
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=earnings_per_share | row=diluted_earnings
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1823
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=earnings_per_share | row=diluted_earnings
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 5/40 — OCBC / `net_asset_value_per_share` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`net_asset_value_per_share`, parent=`(none)`
- **existing_row_id**: 1735
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=net_asset_value_per_share
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: bs.nav_per_share
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/2]**: 1806
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=net_asset_value_per_share
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1829
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=net_asset_value_per_share
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 6/40 — OCBC / `net_interest_margin` (parent: `revenue_mix_efficiency_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`net_interest_margin`, parent=`revenue_mix_efficiency_ratios`
- **existing_row_id**: 1742
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=revenue_mix_efficiency_ratios | row=net_interest_margin
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: ratio.nim
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/3]**: 1807
- **proposed_addr [1/3]**: OCBC | REG_LCR | parent=revenue_mix_efficiency_ratios | row=net_interest_margin
- **proposed_source_table_type [1/3]**: `REG_LCR`
- **proposed_reviewed_at [1/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/3]**: (none)
- **proposed_formula [1/3]**: (none recorded)
- **proposed_note [1/3]**: no concept stamped in corpus
- **proposed_row_id [2/3]**: 1830
- **proposed_addr [2/3]**: OCBC | REG_LEVERAGE | parent=revenue_mix_efficiency_ratios | row=net_interest_margin
- **proposed_source_table_type [2/3]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/3]**: (none)
- **proposed_formula [2/3]**: (none recorded)
- **proposed_note [2/3]**: no concept stamped in corpus
- **proposed_row_id [3/3]**: 1853
- **proposed_addr [3/3]**: OCBC | REG_NSFR | parent=revenue_mix_efficiency_ratios | row=net_interest_margin
- **proposed_source_table_type [3/3]**: `REG_NSFR`
- **proposed_reviewed_at [3/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [3/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [3/3]**: (none)
- **proposed_formula [3/3]**: (none recorded)
- **proposed_note [3/3]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 7/40 — OCBC / `npl_ratio` (parent: `revenue_mix_efficiency_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`npl_ratio`, parent=`revenue_mix_efficiency_ratios`
- **existing_row_id**: 1757
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=revenue_mix_efficiency_ratios | row=npl_ratio
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: ratio.npl
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/3]**: 1810
- **proposed_addr [1/3]**: OCBC | REG_LCR | parent=revenue_mix_efficiency_ratios | row=npl_ratio
- **proposed_source_table_type [1/3]**: `REG_LCR`
- **proposed_reviewed_at [1/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/3]**: (none)
- **proposed_formula [1/3]**: (none recorded)
- **proposed_note [1/3]**: no concept stamped in corpus
- **proposed_row_id [2/3]**: 1833
- **proposed_addr [2/3]**: OCBC | REG_LEVERAGE | parent=revenue_mix_efficiency_ratios | row=npl_ratio
- **proposed_source_table_type [2/3]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/3]**: (none)
- **proposed_formula [2/3]**: (none recorded)
- **proposed_note [2/3]**: no concept stamped in corpus
- **proposed_row_id [3/3]**: 1857
- **proposed_addr [3/3]**: OCBC | REG_NSFR | parent=revenue_mix_efficiency_ratios | row=npl_ratio
- **proposed_source_table_type [3/3]**: `REG_NSFR`
- **proposed_reviewed_at [3/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [3/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [3/3]**: (none)
- **proposed_formula [3/3]**: (none recorded)
- **proposed_note [3/3]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 8/40 — OCBC / `return_on_assets` (parent: `performance_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`return_on_assets`, parent=`performance_ratios`
- **existing_row_id**: 1767
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=performance_ratios | row=return_on_assets
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: ratio.roa
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/2]**: 1812
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=performance_ratios | row=return_on_assets
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1835
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=performance_ratios | row=return_on_assets
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 9/40 — OCBC / `return_on_equity` (parent: `performance_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`return_on_equity`, parent=`performance_ratios`
- **existing_row_id**: 1770
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=performance_ratios | row=return_on_equity
- **existing_reviewed_at**: 2026-08-03T02:17:56+00:00
- **existing_reviewer**: dashboard_rows.yaml (status: `human_confirmed`)
- **existing_concept_key**: ratio.roe
- **existing_formula**: (none recorded)
- **existing_note**: 
- **proposed_row_id [1/2]**: 1813
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=performance_ratios | row=return_on_equity
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1836
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=performance_ratios | row=return_on_equity
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: concept_key, confidence, map_status, mapped_by, note, period_type
- **verdict_suggestion**: `keep_existing` — Existing address is human_confirmed; a REG_* fold should never override a human decision without explicit review.

### Collision 10/40 — OCBC / `all_currency` (parent: `liquidity_coverage_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`all_currency`, parent=`liquidity_coverage_ratios`
- **existing_row_id**: 1690
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=liquidity_coverage_ratios | row=all_currency
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/2]**: 1795
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=liquidity_coverage_ratios | row=all_currency
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1818
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=liquidity_coverage_ratios | row=all_currency
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 11/40 — OCBC / `all_currency` (parent: `liquidity_coverage_ratios_6_8`)
- **collision_key**: bank=`OCBC`, row_label=`all_currency`, parent=`liquidity_coverage_ratios_6_8`
- **existing_row_id**: 1691
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=liquidity_coverage_ratios_6_8 | row=all_currency
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1841
- **proposed_addr**: OCBC | REG_NSFR | parent=liquidity_coverage_ratios_6_8 | row=all_currency
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 12/40 — OCBC / `basic_earnings` (parent: `earnings_per_share_2`)
- **collision_key**: bank=`OCBC`, row_label=`basic_earnings`, parent=`earnings_per_share_2`
- **existing_row_id**: 1698
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=earnings_per_share_2 | row=basic_earnings
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1842
- **proposed_addr**: OCBC | REG_NSFR | parent=earnings_per_share_2 | row=basic_earnings
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 13/40 — OCBC / `capital_adequacy_ratios` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`capital_adequacy_ratios`, parent=`(none)`
- **existing_row_id**: 1699
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=capital_adequacy_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id [1/2]**: 1797
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=capital_adequacy_ratios
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: structural header: every occurrence has zero cells
- **proposed_row_id [2/2]**: 1820
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=capital_adequacy_ratios
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 14/40 — OCBC / `capital_adequacy_ratios_8_9` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`capital_adequacy_ratios_8_9`, parent=`key_financial_ratios`
- **existing_row_id**: 1700
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=capital_adequacy_ratios_8_9
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id**: 1843
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=capital_adequacy_ratios_8_9
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 15/40 — OCBC / `common_equity_tier_1` (parent: `capital_adequacy_ratios_8_9`)
- **collision_key**: bank=`OCBC`, row_label=`common_equity_tier_1`, parent=`capital_adequacy_ratios_8_9`
- **existing_row_id**: 1704
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=capital_adequacy_ratios_8_9 | row=common_equity_tier_1
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1844
- **proposed_addr**: OCBC | REG_NSFR | parent=capital_adequacy_ratios_8_9 | row=common_equity_tier_1
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 16/40 — OCBC / `diluted_earnings` (parent: `earnings_per_share_2`)
- **collision_key**: bank=`OCBC`, row_label=`diluted_earnings`, parent=`earnings_per_share_2`
- **existing_row_id**: 1714
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=earnings_per_share_2 | row=diluted_earnings
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1846
- **proposed_addr**: OCBC | REG_NSFR | parent=earnings_per_share_2 | row=diluted_earnings
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 17/40 — OCBC / `earnings_per_share` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`earnings_per_share`, parent=`(none)`
- **existing_row_id**: 1716
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=earnings_per_share
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id [1/2]**: 1801
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=earnings_per_share
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: structural header: every occurrence has zero cells
- **proposed_row_id [2/2]**: 1824
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=earnings_per_share
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 18/40 — OCBC / `earnings_per_share_2` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`earnings_per_share_2`, parent=`key_financial_ratios`
- **existing_row_id**: 1718
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=earnings_per_share_2
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id**: 1847
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=earnings_per_share_2
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 19/40 — OCBC / `key_financial_ratios` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`key_financial_ratios`, parent=`(none)`
- **existing_row_id**: 1727
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=key_financial_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id [1/3]**: 1802
- **proposed_addr [1/3]**: OCBC | REG_LCR | parent=(none) | row=key_financial_ratios
- **proposed_source_table_type [1/3]**: `REG_LCR`
- **proposed_reviewed_at [1/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/3]**: (none)
- **proposed_formula [1/3]**: (none recorded)
- **proposed_note [1/3]**: structural header: every occurrence has zero cells
- **proposed_row_id [2/3]**: 1825
- **proposed_addr [2/3]**: OCBC | REG_LEVERAGE | parent=(none) | row=key_financial_ratios
- **proposed_source_table_type [2/3]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/3]**: (none)
- **proposed_formula [2/3]**: (none recorded)
- **proposed_note [2/3]**: structural header: every occurrence has zero cells
- **proposed_row_id [3/3]**: 1848
- **proposed_addr [3/3]**: OCBC | REG_NSFR | parent=(none) | row=key_financial_ratios
- **proposed_source_table_type [3/3]**: `REG_NSFR`
- **proposed_reviewed_at [3/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [3/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [3/3]**: (none)
- **proposed_formula [3/3]**: (none recorded)
- **proposed_note [3/3]**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 20/40 — OCBC / `leverage_ratio` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`leverage_ratio`, parent=`(none)`
- **existing_row_id**: 1728
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=leverage_ratio
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/2]**: 1803
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=leverage_ratio
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1826
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=leverage_ratio
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 21/40 — OCBC / `leverage_ratio_5_8_9` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`leverage_ratio_5_8_9`, parent=`key_financial_ratios`
- **existing_row_id**: 1730
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=leverage_ratio_5_8_9
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1849
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=leverage_ratio_5_8_9
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 22/40 — OCBC / `liquidity_coverage_ratios` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`liquidity_coverage_ratios`, parent=`(none)`
- **existing_row_id**: 1731
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=liquidity_coverage_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id [1/2]**: 1804
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=liquidity_coverage_ratios
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: structural header: every occurrence has zero cells
- **proposed_row_id [2/2]**: 1827
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=liquidity_coverage_ratios
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 23/40 — OCBC / `liquidity_coverage_ratios_6_8` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`liquidity_coverage_ratios_6_8`, parent=`key_financial_ratios`
- **existing_row_id**: 1732
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=liquidity_coverage_ratios_6_8
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id**: 1850
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=liquidity_coverage_ratios_6_8
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 24/40 — OCBC / `loans_to_deposits` (parent: `revenue_mix_efficiency_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`loans_to_deposits`, parent=`revenue_mix_efficiency_ratios`
- **existing_row_id**: 1734
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=revenue_mix_efficiency_ratios | row=loans_to_deposits
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/3]**: 1805
- **proposed_addr [1/3]**: OCBC | REG_LCR | parent=revenue_mix_efficiency_ratios | row=loans_to_deposits
- **proposed_source_table_type [1/3]**: `REG_LCR`
- **proposed_reviewed_at [1/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/3]**: (none)
- **proposed_formula [1/3]**: (none recorded)
- **proposed_note [1/3]**: no concept stamped in corpus
- **proposed_row_id [2/3]**: 1828
- **proposed_addr [2/3]**: OCBC | REG_LEVERAGE | parent=revenue_mix_efficiency_ratios | row=loans_to_deposits
- **proposed_source_table_type [2/3]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/3]**: (none)
- **proposed_formula [2/3]**: (none recorded)
- **proposed_note [2/3]**: no concept stamped in corpus
- **proposed_row_id [3/3]**: 1851
- **proposed_addr [3/3]**: OCBC | REG_NSFR | parent=revenue_mix_efficiency_ratios | row=loans_to_deposits
- **proposed_source_table_type [3/3]**: `REG_NSFR`
- **proposed_reviewed_at [3/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [3/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [3/3]**: (none)
- **proposed_formula [3/3]**: (none recorded)
- **proposed_note [3/3]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 25/40 — OCBC / `net_asset_value_per_share` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`net_asset_value_per_share`, parent=`key_financial_ratios`
- **existing_row_id**: 1737
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=net_asset_value_per_share
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1852
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=net_asset_value_per_share
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 26/40 — OCBC / `net_stable_funding_ratio` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`net_stable_funding_ratio`, parent=`(none)`
- **existing_row_id**: 1746
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=net_stable_funding_ratio
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/2]**: 1808
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=net_stable_funding_ratio
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1831
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=net_stable_funding_ratio
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 27/40 — OCBC / `net_stable_funding_ratio_7_8` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`net_stable_funding_ratio_7_8`, parent=`key_financial_ratios`
- **existing_row_id**: 1748
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=net_stable_funding_ratio_7_8
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1854
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=net_stable_funding_ratio_7_8
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 28/40 — OCBC / `non_interest_income_to_total_income` (parent: `revenue_mix_efficiency_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`non_interest_income_to_total_income`, parent=`revenue_mix_efficiency_ratios`
- **existing_row_id**: 1753
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=revenue_mix_efficiency_ratios | row=non_interest_income_to_total_income
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/3]**: 1809
- **proposed_addr [1/3]**: OCBC | REG_LCR | parent=revenue_mix_efficiency_ratios | row=non_interest_income_to_total_income
- **proposed_source_table_type [1/3]**: `REG_LCR`
- **proposed_reviewed_at [1/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/3]**: (none)
- **proposed_formula [1/3]**: (none recorded)
- **proposed_note [1/3]**: no concept stamped in corpus
- **proposed_row_id [2/3]**: 1832
- **proposed_addr [2/3]**: OCBC | REG_LEVERAGE | parent=revenue_mix_efficiency_ratios | row=non_interest_income_to_total_income
- **proposed_source_table_type [2/3]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/3]**: (none)
- **proposed_formula [2/3]**: (none recorded)
- **proposed_note [2/3]**: no concept stamped in corpus
- **proposed_row_id [3/3]**: 1855
- **proposed_addr [3/3]**: OCBC | REG_NSFR | parent=revenue_mix_efficiency_ratios | row=non_interest_income_to_total_income
- **proposed_source_table_type [3/3]**: `REG_NSFR`
- **proposed_reviewed_at [3/3]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [3/3]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [3/3]**: (none)
- **proposed_formula [3/3]**: (none recorded)
- **proposed_note [3/3]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 29/40 — OCBC / `performance_ratios` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`performance_ratios`, parent=`(none)`
- **existing_row_id**: 1763
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=performance_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id [1/2]**: 1811
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=performance_ratios
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: structural header: every occurrence has zero cells
- **proposed_row_id [2/2]**: 1834
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=performance_ratios
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 30/40 — OCBC / `performance_ratios` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`performance_ratios`, parent=`key_financial_ratios`
- **existing_row_id**: 1764
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=performance_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id**: 1858
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=performance_ratios
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 31/40 — OCBC / `return_on_assets_3` (parent: `performance_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`return_on_assets_3`, parent=`performance_ratios`
- **existing_row_id**: 1769
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=performance_ratios | row=return_on_assets_3
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1859
- **proposed_addr**: OCBC | REG_NSFR | parent=performance_ratios | row=return_on_assets_3
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 32/40 — OCBC / `return_on_equity_1_2` (parent: `performance_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`return_on_equity_1_2`, parent=`performance_ratios`
- **existing_row_id**: 1771
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=performance_ratios | row=return_on_equity_1_2
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1860
- **proposed_addr**: OCBC | REG_NSFR | parent=performance_ratios | row=return_on_equity_1_2
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 33/40 — OCBC / `revenue_mix_efficiency_ratios` (no parent)
- **collision_key**: bank=`OCBC`, row_label=`revenue_mix_efficiency_ratios`, parent=`(none)`
- **existing_row_id**: 1773
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=(none) | row=revenue_mix_efficiency_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id [1/2]**: 1814
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=(none) | row=revenue_mix_efficiency_ratios
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: structural header: every occurrence has zero cells
- **proposed_row_id [2/2]**: 1837
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=(none) | row=revenue_mix_efficiency_ratios
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 34/40 — OCBC / `revenue_mix_efficiency_ratios` (parent: `key_financial_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`revenue_mix_efficiency_ratios`, parent=`key_financial_ratios`
- **existing_row_id**: 1774
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=key_financial_ratios | row=revenue_mix_efficiency_ratios
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: structural header: every occurrence has zero cells
- **proposed_row_id**: 1861
- **proposed_addr**: OCBC | REG_NSFR | parent=key_financial_ratios | row=revenue_mix_efficiency_ratios
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: structural header: every occurrence has zero cells
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 35/40 — OCBC / `singapore_dollar` (parent: `liquidity_coverage_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`singapore_dollar`, parent=`liquidity_coverage_ratios`
- **existing_row_id**: 1780
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=liquidity_coverage_ratios | row=singapore_dollar
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/2]**: 1815
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=liquidity_coverage_ratios | row=singapore_dollar
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1838
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=liquidity_coverage_ratios | row=singapore_dollar
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 36/40 — OCBC / `singapore_dollar` (parent: `liquidity_coverage_ratios_6_8`)
- **collision_key**: bank=`OCBC`, row_label=`singapore_dollar`, parent=`liquidity_coverage_ratios_6_8`
- **existing_row_id**: 1781
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=liquidity_coverage_ratios_6_8 | row=singapore_dollar
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1862
- **proposed_addr**: OCBC | REG_NSFR | parent=liquidity_coverage_ratios_6_8 | row=singapore_dollar
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 37/40 — OCBC / `tier_1` (parent: `capital_adequacy_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`tier_1`, parent=`capital_adequacy_ratios`
- **existing_row_id**: 1783
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=capital_adequacy_ratios | row=tier_1
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/2]**: 1816
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=capital_adequacy_ratios | row=tier_1
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1839
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=capital_adequacy_ratios | row=tier_1
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 38/40 — OCBC / `tier_1` (parent: `capital_adequacy_ratios_8_9`)
- **collision_key**: bank=`OCBC`, row_label=`tier_1`, parent=`capital_adequacy_ratios_8_9`
- **existing_row_id**: 1784
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=capital_adequacy_ratios_8_9 | row=tier_1
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1863
- **proposed_addr**: OCBC | REG_NSFR | parent=capital_adequacy_ratios_8_9 | row=tier_1
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 39/40 — OCBC / `total` (parent: `capital_adequacy_ratios`)
- **collision_key**: bank=`OCBC`, row_label=`total`, parent=`capital_adequacy_ratios`
- **existing_row_id**: 1786
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=capital_adequacy_ratios | row=total
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id [1/2]**: 1817
- **proposed_addr [1/2]**: OCBC | REG_LCR | parent=capital_adequacy_ratios | row=total
- **proposed_source_table_type [1/2]**: `REG_LCR`
- **proposed_reviewed_at [1/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [1/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [1/2]**: (none)
- **proposed_formula [1/2]**: (none recorded)
- **proposed_note [1/2]**: no concept stamped in corpus
- **proposed_row_id [2/2]**: 1840
- **proposed_addr [2/2]**: OCBC | REG_LEVERAGE | parent=capital_adequacy_ratios | row=total
- **proposed_source_table_type [2/2]**: `REG_LEVERAGE`
- **proposed_reviewed_at [2/2]**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer [2/2]**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key [2/2]**: (none)
- **proposed_formula [2/2]**: (none recorded)
- **proposed_note [2/2]**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

### Collision 40/40 — OCBC / `total` (parent: `capital_adequacy_ratios_8_9`)
- **collision_key**: bank=`OCBC`, row_label=`total`, parent=`capital_adequacy_ratios_8_9`
- **existing_row_id**: 1787
- **existing_addr**: OCBC | FS_RATIOS_KEY | parent=capital_adequacy_ratios_8_9 | row=total
- **existing_reviewed_at**: 2026-08-03T09:40:31+00:00
- **existing_reviewer**: backfill:corpus (status: `ai_proposed`)
- **existing_concept_key**: (none)
- **existing_formula**: (none recorded)
- **existing_note**: no concept stamped in corpus
- **proposed_row_id**: 1864
- **proposed_addr**: OCBC | REG_NSFR | parent=capital_adequacy_ratios_8_9 | row=total
- **proposed_source_table_type**: `REG_NSFR`
- **proposed_reviewed_at**: 2026-08-03T09:40:31+00:00
- **proposed_reviewer**: backfill:corpus (status: `ai_proposed`)
- **proposed_concept_key**: (none)
- **proposed_formula**: (none recorded)
- **proposed_note**: no concept stamped in corpus
- **diff_summary**: (no field differences beyond address)
- **verdict_suggestion**: `keep_existing` — Neither side has a concept_key and every other field (note, status, mapped_by, ...) is identical -- the rows are otherwise indistinguishable, only the legacy table_type_id differs. Nothing is lost either way; safe to drop the REG_* duplicate.

## 3. Non-colliding REG_* rows — split by document family

106 rows across 105 addresses have no existing `FS_RATIOS_KEY` address in the way, collision-wise. But "no collision" is not the same as "safe to fold" — per the scope correction above, a real chunk of these are Pillar 3-sourced and must not become `FS_RATIOS_KEY` rows regardless of collision status. Split by the same family trace used above:

| family | row count | verdict |
|---|---|---|
| FS-only | 1 | safe to fold into `FS_RATIOS_KEY` |
| PILLAR3-only | 105 | **DO NOT fold** — genuine Pillar 3 content, out of this project's current FS scope entirely |
| BOTH | 0 | defer — same label appears in both families, needs a human call on whether it's coincidental or a genuinely shared ratio |
| unresolved | 0 | defer — no live `row_dim` instance currently attests this address (stale/superseded anchor); investigate before moving |

### 3a. FS-only — safe to fold

1 rows. Safe to move with a plain `UPDATE ... SET table_type_id='FS_RATIOS_KEY'`, the same mechanism `migrate_consolidate_table_type_ids.py` already used for the 6 clean pairs — **not done here** (out of scope for this report; follow-up task per the brief).

| bank | row_label_norm | parent_label_norm | source table_type_id | map_status | concept_key |
|---|---|---|---|---|---|
| OCBC | `notes_1_other_equity_instruments_and_non_controlling_interes` | `` | REG_NSFR | ai_proposed |  |

### 3b. PILLAR3-only — must NOT be folded into FS_RATIOS_KEY

105 rows, all currently sitting under `REG_LEVERAGE` (61) or `REG_KEY_METRICS` (44). These are genuine Pillar 3 Basel disclosure line items (leverage ratio components, G-SIB indicators, RWA detail, etc.) that happen to share the legacy `REG_*` naming with the ids being folded — they are NOT FS content and this fold must not touch them. Left under their current `table_type_id` for now; a real Pillar 3 registry (separate, explicitly out of scope for the current FS masterlist work) would be the right home for them eventually.

| bank | row_label_norm | parent_label_norm | source table_type_id | map_status | concept_key |
|---|---|---|---|---|---|
| DBS | `1_even_though_the_group_is_not_a_g_sib_it_is_required_under_` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `1_leverage_ratio_is_computed_using_quarter_end_balances` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `2_lcr_is_calculated_based_on_average_for_the_quarter_please_` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `3_prior_to_30_september_21_lvb_was_excluded_and_the_impact_w` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `3_pursuant_to_mas_notice_637_effective_1_july_2024` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `additional_cet_buffer_requirements_as_a_percentage_of_rwa` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `adjusted_effective_notional_amount_of_written_credit_derivat` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_collateral_received_under_securities_financin` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_derivative_transactions` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_fiduciary_assets_recognised_on_the_balance_sh` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_fiduciary_assets_recognised_on_the_balance_sh` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_investments_in_entities_that_are_consolidated` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_off_balance_sheet_items` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_securitised_exposures_that_meet_the_operation` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustment_for_sfts` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustments_for_calculation_of_exposure_measures_of_off_bala` | `exposure_measures_of_off_balance_sheet_i` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustments_for_eligible_cash_pooling_arrangements` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustments_for_prudent_valuation_adjustments_and_specific_a` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `adjustments_for_regular_way_purchases_and_sales_of_financial` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `applicable_leverage_buffers` | `leverage_ratio` | REG_LEVERAGE | ai_proposed |  |
| DBS | `asset_amounts_deducted_in_determining_tier_1_capital` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `asset_amounts_deducted_in_determining_tier_1_capital_and_reg` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `asset_amounts_deducted_in_determining_tier_1_capital_capital` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `available_capital_amounts` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `bank_g_sib_and_or_d_sib_additional_requirements_1` | `additional_cet_buffer_requirements_as_a_` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `capital_and_total_exposures` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `capital_conservation_buffer_requirement_2_5_from_2019` | `additional_cet_buffer_requirements_as_a_` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `ccp_leg_of_trade_exposures_excluded` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `ccp_leg_of_trade_exposures_excluded_in_respect_of_in_respect` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `cet_available_after_meeting_the_reporting_bank_s_minimum_cap` | `additional_cet_buffer_requirements_as_a_` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `cet_capital` | `available_capital_amounts` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `cet_ratio` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed | reg.capital.cet1_ratio |
| DBS | `cet_ratio_pre_floor_ratio` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed | reg.capital.cet1_ratio |
| DBS | `cet_ratio_pre_floor_ratio_3` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed | reg.capital.cet1_ratio |
| DBS | `countercyclical_buffer_requirement` | `additional_cet_buffer_requirements_as_a_` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `deductions_of_receivable_assets_for_cash_variation_margin_pr` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `deductions_of_receivables_for_the_cash_portion_of_variation_` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `derivative_exposure_measures` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `disclosure_of_mean_values` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `eligible_netting_of_cash_payables_and_cash_receivables` | `sft_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `exposure_measure` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `exposure_measures_of_off_balance_sheet_items` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `exposure_measures_of_on_balance_sheet_items` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `further_adjustments_in_effective_notional_amounts_and_deduct` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `g_sib_and_or_d_sib_additional_requirements_1` | `additional_cet_buffer_requirements_as_a_` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `gross_sft_assets_with_no_recognition_of_accounting_netting_a` | `sft_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `gross_up_for_derivative_collaterals_provided_where_deducted_` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `gross_up_for_derivatives_collateral_provided_where_deducted_` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `leverage_ratio` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `leverage_ratio` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `leverage_ratio` | `leverage_ratio` | REG_LEVERAGE | ai_proposed |  |
| DBS | `leverage_ratio_exposure_measure` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `leverage_ratio_incorporating_mean_values_for_sft_assets` | `leverage_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `leverage_ratio_incorporating_mean_values_for_sft_assets_3` | `leverage_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `leverage_ratio_incorporating_values_from_row_28` | `disclosure_of_mean_values` | REG_LEVERAGE | ai_proposed |  |
| DBS | `leverage_ratio_row_2_row_13` | `leverage_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `liquidity_coverage_ratio` | `liquidity_coverage_ratio` | REG_KEY_METRICS | ai_proposed | reg.liquidity.lcr_ratio |
| DBS | `liquidity_coverage_ratio_2` | `` | REG_KEY_METRICS | ai_proposed | reg.liquidity.lcr_ratio |
| DBS | `liquidity_coverage_ratio_2_3` | `` | REG_KEY_METRICS | ai_proposed | reg.liquidity.lcr_ratio |
| DBS | `mean_value_of_gross_sft_assets_after_adjustment_for_sale_acc` | `disclosure_of_mean_values` | REG_LEVERAGE | ai_proposed |  |
| DBS | `national_minimum_leverage_ratio_requirement` | `leverage_ratio` | REG_LEVERAGE | ai_proposed |  |
| DBS | `net_stable_funding_ratio` | `` | REG_KEY_METRICS | ai_proposed | reg.liquidity.nsfr_ratio |
| DBS | `net_stable_funding_ratio` | `net_stable_funding_ratio` | REG_KEY_METRICS | ai_proposed | reg.liquidity.nsfr_ratio |
| DBS | `net_stable_funding_ratio_3` | `` | REG_KEY_METRICS | ai_proposed | reg.liquidity.nsfr_ratio |
| DBS | `off_balance_sheet_items_at_notional_amount` | `exposure_measures_of_off_balance_sheet_i` | REG_LEVERAGE | ai_proposed |  |
| DBS | `on_balance_sheet_items_excluding_derivative_transactions_and` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `other_adjustments` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `potential_future_exposure_associated_with_all_derivative_tra` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `quarter_end_value_of_gross_sft_assets_after_adjustment_for_s` | `disclosure_of_mean_values` | REG_LEVERAGE | ai_proposed |  |
| DBS | `replacement_cost_associated_with_all_derivative_transactions` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `replacement_cost_associated_with_all_derivative_transactions` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `risk_based_capital_ratios_as_a_percentage_of_rwa` | `` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `risk_weighted_assets_amounts` | `` | REG_KEY_METRICS | ai_proposed | reg.capital.rwa |
| DBS | `sft_counterparty_exposures` | `sft_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `sft_exposure_measures` | `` | REG_LEVERAGE | ai_proposed |  |
| DBS | `sft_exposure_measures_where_a_reporting_bank_acts_as_an_agen` | `sft_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `specific_and_general_allowances_associated_with_off_balance_` | `exposure_measures_of_off_balance_sheet_i` | REG_LEVERAGE | ai_proposed |  |
| DBS | `specific_and_general_allowances_associated_with_offbalance_s` | `exposure_measures_of_off_balance_sheet_i` | REG_LEVERAGE | ai_proposed |  |
| DBS | `specific_and_general_allowances_associated_with_on_balance_s` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `specific_and_general_allowances_associated_with_onbalance_sh` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `tier_1_capital` | `available_capital_amounts` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `tier_1_capital` | `capital_and_total_exposures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `tier_1_ratio` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `tier_1_ratio_pre_floor_ratio` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `tier_1_ratio_pre_floor_ratio_3` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_available_stable_funding` | `net_stable_funding_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_capital` | `available_capital_amounts` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_capital_ratio` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_capital_ratio_pre_floor_ratio` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_capital_ratio_pre_floor_ratio_3` | `risk_based_capital_ratios_as_a_percentag` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_consolidated_assets_as_per_published_financial_stateme` | `` | REG_LEVERAGE | ai_proposed | bs.assets.total |
| DBS | `total_derivative_exposure_measures` | `derivative_exposure_measures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `total_exposure_measures_of_off_balance_sheet_items` | `exposure_measures_of_off_balance_sheet_i` | REG_LEVERAGE | ai_proposed |  |
| DBS | `total_exposure_measures_of_on_balance_sheet_items_excluding_` | `exposure_measures_of_on_balance_sheet_it` | REG_LEVERAGE | ai_proposed |  |
| DBS | `total_exposures` | `capital_and_total_exposures` | REG_LEVERAGE | ai_proposed |  |
| DBS | `total_exposures_incorporating_values_from_row_28` | `disclosure_of_mean_values` | REG_LEVERAGE | ai_proposed |  |
| DBS | `total_high_quality_liquid_assets` | `liquidity_coverage_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_leverage_ratio_exposure_measure` | `leverage_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_net_cash_outflow` | `liquidity_coverage_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_of_bank_cet_specific_buffer_requirements_row_8_row_9_r` | `additional_cet_buffer_requirements_as_a_` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_required_stable_funding` | `net_stable_funding_ratio` | REG_KEY_METRICS | ai_proposed |  |
| DBS | `total_rwa` | `risk_weighted_assets_amounts` | REG_KEY_METRICS | ai_proposed | reg.capital.rwa |
| DBS | `total_rwa_pre_floor` | `risk_weighted_assets_amounts` | REG_KEY_METRICS | ai_proposed | reg.capital.rwa |
| DBS | `total_rwa_pre_floor_3` | `risk_weighted_assets_amounts` | REG_KEY_METRICS | ai_proposed | reg.capital.rwa |
| DBS | `total_sft_exposure_measures` | `sft_exposure_measures` | REG_LEVERAGE | ai_proposed |  |

### 3c. BOTH (same label used in both families) — 0 rows


### 3d. unresolved (no matching live row_dim instance found) — 0 rows


## 4. Next actions

- **Would auto-apply if given permission** (verdict=`keep_existing`, i.e. drop the REG_* duplicate and keep the existing `FS_RATIOS_KEY` row as-is): **40** of 40 collisions, all high-confidence — 9 protected by an existing `human_confirmed` anchor, 31 are byte-identical unmapped duplicates where nothing is lost either way. Recommend a quick skim of the 9 human_confirmed-protected ones specifically (confirm the REG_* side really isn't hiding a better address) before bulk-applying; the 31 identical-duplicate ones are low-risk to apply without individual review.
- **Needs real human review** (`take_new`=0, `merge_metadata`=0, `defer`=0): **0** of 40 collisions.
- **Broader structural issue surfaced**: **every one of the 40 collisions is OCBC** (['OCBC']) — DBS contributes 0 of the 40 (all 105 of its non-colliding rows land on addresses `FS_RATIOS_KEY` doesn't already have) and UOB contributes 0 as well. And within OCBC, **0 collisions have two DIFFERENT non-null `concept_key`s on the existing vs. REG_* side** (a genuine mapping disagreement, not just one side being unmapped) — i.e. essentially all 40 are either protected by an existing `human_confirmed` row, or are byte-identical unmapped duplicates, with zero real concept-identity conflicts found. Read together, this says the 4-way `REG_LCR`/`REG_LEVERAGE`/`REG_NSFR`/`REG_KEY_METRICS` split was likely never a real distinction for OCBC in the first place — all 4 IDs appear to be the SAME physical "Key Financial Ratios" exhibit, captured and classified differently across separate ingest runs, not 4 genuinely different tables. That's a stronger, corpus-level version of the duplicate-registry bug this whole fold is about, worth confirming before assuming the same will hold once DBS/UOB get real `FS_RATIOS_KEY` collisions of their own in a future ingest.
- **`FS_RATIOS_KEY` review-status caveat** (see schema notes above): only 16/143 existing rows are `human_confirmed`. Any `keep_existing` verdict against one of the other 127 `ai_proposed` rows is a coin-flip between two unreviewed anchors, not a protection of reviewed work — flagged per-block above via `existing_reviewer`/status, but calling it out here too since it's easy to miss skimming individual blocks.
- **Scope reminder** (see section 3 above): of the 106 non-colliding rows, only 1 are actually FS and safe to fold; 105 are Pillar 3 and must be excluded from this fold entirely, 0 are unresolved stale anchors needing investigation, and 0 use a label seen in both families. Any follow-up script that folds "the non-colliding rows" must filter to section 3a specifically, not run against all 106 indiscriminately.
