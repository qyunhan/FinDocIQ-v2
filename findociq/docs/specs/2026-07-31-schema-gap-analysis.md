# Step 1 — Current-schema gap analysis (mapping layer / auto-ingestion)

Date: 2026-07-31 · DB: `findociq/db/compiled_fs.db` at HEAD `292732d`
BigQuery: `igc2026-team08-6311.findociq`

Everything below is **measured**, not inferred. Population percentages are live
counts. Read this before the specs — corrections here change the design.

---

## A. Inventory — what exists, at what grain

| table | rows | PK / grain | what it stores |
|---|---:|---|---|
| `document` | 25 | `doc_id` | institution, doc_family, source_file, doc_period |
| `section` | 748 | `doc_id, section_id` | PDF reading order (`seq`) |
| `table_t` | 375 | `doc_id, table_id` | title, **table_type**, period, period_span, period_start, geo_key, page_range, unit, title_clean, **hierarchy_source** |
| `row_dim` | 6,531 | `doc_id, table_id, row_id` | label, hierarchy depth, **row_parent**, concept_key, geo/segment/industry, unit, sums_to/sums_sign, row_lineage_id, label_clean |
| `col_dim` | 2,028 | `doc_id, table_id, col_id` | header, col_period, period_span, period_start, dims, unit, sums_to |
| `cell_fact` | 24,788 | `doc_id, table_id, row_id, col_id` | value_raw, value_num, unit, period, period_span, dims, cell_state, lineage ids |
| `row_lineage` | 2,348 | `row_lineage_id` | **`lineage_key` + `lvl1..lvl5` + `depth` — the full parent chain** |
| `col_lineage` | 320 | `col_lineage_id` | same for columns |
| `concept_map` | 286 | **`table_type, label_norm`** | → concept_key |
| `concept_resolution_log` | 5,571 | — | resolution audit trail |
| `fact_metric` | 2,925 | `institution, concept_key, period, period_span, segment_key, geo_key, industry_key` | the analytic layer |
| `geo_dim` / `segment_dim` / `industry_dim` | 20 / 6 / 10 | `*_key` | axis members (segment_dim has a **`parent`** column) |
| `geo_map` / `segment_map` / `industry_map` | 24 / 16 / 18 | `label_norm` | label → member |
| `ingest_status` | 4 | `source_file` | per-doc/per-stage state machine |

Views: `v_cell`, `v_cell_leaf`, `v_cell_sumsafe`, **`v_cell_flat`** (the BQ fact
table — joins lineage, exposes `row_lvl1..lvl5`, and derives
`period_start = COALESCE(col.period_start, table.period_start)`).

BQ sync = 11 objects: `document, section, table_t, segment_dim, geo_dim,
industry_dim, concept_map, concept_resolution_log, fact_metric, v_cell_flat,
ingest_status`. **`row_dim` / `col_dim` / `cell_fact` are not in BigQuery** —
only the flattened view is.

Not in `compiled_fs.db` at all: `template_row` / `template_col` /
`template_cell` (defined in `pipeline/templates/template_registry.sql`, scoped
to MAS regulatory forms — NSFR/LCR/KM1 — shared across banks). **Notably
`template_row` already carries `parent_line_no` + `concept_key`.** The pattern
you want exists; it was built for regulatory forms and never extended to the
per-bank FS exhibits, which take "the generic path."

---

## B. Population — what is declared vs what is actually filled

The columns exist. They are mostly empty.

| column | populated | note |
|---|---:|---|
| `cell_fact.concept_key` | **0 / 24,788 (0.0%)** | identity lives ONLY on `row_dim`; `v_cell_flat` coalesces row→cell |
| `row_dim.concept_key` | 2,013 / 6,531 (30.8%) | |
| `row_dim.segment_key` | 200 / 6,531 (3.1%) | |
| `row_dim.geo_key` | 230 / 6,531 (3.5%) | |
| `row_dim.unit` | 201 / 6,531 (3.1%) | |
| `row_dim.row_parent` | 3,929 / 6,531 (60.2%) | |
| `row_dim.row_lineage_id` | **6,531 / 6,531 (100%)** | the parent chain is always there |
| `col_dim.col_period` | 1,102 / 2,028 (54.3%) | |
| `col_dim.period_start` | 568 / 2,028 (28.0%) | |
| `cell_fact.period_span` | 19,212 / 24,788 (77.5%) | |
| `table_t.table_type` | 375 / 375 (100%) | but see C-2 |

`cell_fact` dims are 100% non-NULL but that is **sentinel fill, not
information**: `geo_key` 89.2% `GLOBAL`, `segment_key` 93.1% `SEG_TOTAL`,
`industry_key` 98.8% `IND_TOTAL`. Real dimensional members are on ~1–7% of cells.

---

## C. Five findings that change the proposed design

### C-1. The parent anchor already exists as data — reuse it, don't rebuild it
`row_lineage` is 100% populated and stores the normalized chain
(`lineage_key` = `"ecl stage 3 (sp) for loans1 > singapore"`, plus `lvl1..lvl5`
and `depth`). `v_cell_flat` already exposes `row_lvl1..row_lvl5` to BigQuery.

So `bank_line_map.parent_label_norm` should be **derived from `row_lineage`**,
not a new extraction. Your anchor is available today at zero cost.

### C-2. `table_type` is NOT a stable anchor — this breaks the proposed key
`table_type` is 100% populated but **217 distinct values across 375 tables, and
158 of 217 (73%) appear in exactly one document.** It is a slug of the model's
table title, so it drifts with the title — and sometimes bakes the period in:

```
DBS_1Q26_trading_update       → selected_income_statement_items_m
DBS_4Q25_performance_summary  → selected_income_statement_items_m
DBS_2Q25_performance_summary  → selected_income_statement_items_1st_half_2025   ← period in the key
DBS_2Q22_performance_summary  → performance_by_business_segments1_selected_income_statement_items2  ← footnote digit in the key
OCBC_1Q25                     → financial_highlights_unaudited
OCBC_4Q25                     → financial_highlights_continued
```

The same logical exhibit lands on four different `table_type` values. Keying
`bank_line_map` on `(bank, table_type, row_label_norm, parent_label_norm)` would
therefore **miss on most new quarters** and dump everything into the review
queue — the map would never converge.

**This needs a decision before the specs.** A `table_type` must become a
registry-assigned stable ID (a small controlled vocabulary, matched by
normalized title with footnote digits and period tokens stripped), not a
title slug. This is the single highest-risk item in the proposal.

### C-3. Derived metrics ARE currently stored as facts — violates your layering
`fact_metric` holds 117 rows of `pnl.noninterest.other` with
`source_doc_id='derived'`, `source_table_id='formula'`, `resolved_by='formula'`.
Your principle says derived metrics live in a formula table and are computed at
read time. Today they are materialized into the fact table alongside atomic
values. `metric_definition` is therefore not purely additive — it requires
**removing** the derived rows from `fact_metric`, or the same metric will
resolve twice with different values.

There is no `metric_definition` table. Formulas live in two places:
`concept_dictionary.yaml` (`formula` / `metric_kind` fields, 10 of 48 concepts
are `kind: derived`) and `compute_ratios.py`.

### C-4. The concept dictionary has half of the XBRL element attributes
48 concepts. Fields present: `key, name, definition, unit, kind, nature,
thesis, aliases, scoped_aliases, formula, metric_kind`.

- `nature` ∈ {flow(22), stock(14), ratio_point(6), ratio_flow(6)} — **this is
  already periodType** (duration/instant). Present.
- `unit` ∈ {currency(35), percent(11), per_share(1), bps(1)} — **unit_class**. Present.
- **`balance` (debit/credit): ABSENT.**
- **`is_abstract`: ABSENT** (`kind` is line_item(38)/derived(10)).
- `meta.basis: group_consolidated` is declared as a **global assumption** with
  no mechanism to represent or enforce it — this is the Group/Company merge.

`scoped_aliases` exists and scopes by **`table_type` only**, so it disambiguates
*across* tables, never *within* one. It cannot resolve the three-NII case.

### C-5. No review queue exists at row grain
`ingest_status` is 4 rows, keyed `source_file`, tracking per-document/per-stage
state (`stage, state, error_class, attempt_count`). There is nothing at
`(run, row)` grain, and no `resolved_by` / `resolution` writeback path.
`ingest_review_queue` is genuinely new, not an extension.

---

## D. Capability → current state → gap

| # | capability | current state | gap |
|---|---|---|---|
| 1 | Per-bank line map | `concept_map` PK `(table_type, label_norm)`; **no bank, no parent** | NEW table. Anchor needs C-1 (parent from `row_lineage`) + C-2 (stable `table_type`) |
| 2 | Parent-qualified identity | `row_parent` 60%, `row_lineage` 100% | Data exists; **resolver ignores it**. `concept/run.py` matches leaf label + table_type only |
| 3 | Dims as fact identity | `fact_metric` PK **already includes** segment/geo/industry ✅ | Correct at the analytic layer. But dims are 89–99% sentinel, so the axis is unused in practice |
| 4 | Legal entity / consolidation basis | **absent everywhere** | NEW axis. Live bug: DBS total equity 68,916 (Group) vs 17,643 (Company) collide; UOB assets 485,263 (Bank) vs 572,061 (Group) |
| 5 | instant vs duration | `nature` on concept ✅; `period_span` on cell 77.5% | `cell_fact` **has no `period_start` column** — it is derived in `v_cell_flat` via COALESCE(col, table). Flows cannot assert `period_start` at fact grain |
| 6 | Variance columns excluded | **not excluded** | 5,576 cells (22.5%) have NULL span, dominated by `% chg` (703), `+/(-) %` (696), `+/(-)\n%` (532). **456 of 2,925 `fact_metric` rows carry NULL span** — the QoQ-delta pollution |
| 7 | `balance` / sign | `sums_sign` on row (40.4%), no element-level balance | NEW field. OCBC opex stored as both +5,882 and −5,882; UOB `Loans to customers` −16,970 |
| 8 | Calc linkbase | `sums_to`/`sums_sign` on `row_dim` (40.4%), instance-level only | NEW table. No element-level tree, no weights, **no assertion run at ingest** |
| 9 | `is_abstract` | headers get `concept_key = NULL` | Indistinguishable from "unmapped" — the ambiguity visible as `—` in the tagged-table report |
| 10 | unit_class enforcement | `unit` on concept ✅, on cell 98.6% | **Not enforced**: `ratio.nim` carries `S$m` on 142 cells, `ratio.npl` 106, `ratio.cir` 94, `ratio.roe` 94 |
| 11 | Metric definitions | `concept_dictionary.yaml` `formula` + `compute_ratios.py` | NEW table + **must un-materialize** the 117 derived rows now in `fact_metric` (C-3) |
| 12 | Generic dim axis registry | 3 hardcoded pairs (`geo_dim`/`geo_map`, etc.), each with its own column on 4 tables | Adding an axis today = schema migration on `row_dim`, `col_dim`, `cell_fact`, `fact_metric` + `v_cell_flat` + `sync_bq`. Needs `dim_axis`/`dim_member` + a fact→member bridge so new axes are DATA |
| 13 | Review queue | `ingest_status` per doc/stage, 4 rows | NEW table at `(run, row)` grain |
| 14 | Provenance | `fact_metric` has `source_doc_id/table_id/row_label` ✅; `cell_fact` PK is the pointer ✅ | Adequate. `source_col_id` is not kept on `fact_metric` — needed to prove which column a value came from |
| 15 | Idempotency | template registry uses MERGE; `run_doc` reload is doc-scoped delete+insert | Doc-scoped reload **wipes `row_dim.concept_key`** (recorded gotcha) — would clobber `human_confirmed` mappings. MERGE semantics required |
| 16 | as-reported vs normalized | `cell_fact` never overwritten ✅ | Holds today. But mis-stamp fixes write directly to `row_dim.concept_key`, and `concept/run.py` re-derives it unconditionally — so the *mapping* layer has no stable home |

---

## E. Worked example — the three-NII case, as the data stands

`DBS_1Q26_trading_update`, table `selected_income_statement_items_m`
(this one is on the **geometry** branch, so the hierarchy is trustworthy):

| row | depth | label | current `concept_key` | 1Q26 | 4Q25 | 1Q25 |
|---|---|---|---|---:|---:|---:|
| 1 | 0 | Commercial book total income | `pnl.income.total` | 5,559 | 5,177 | 5,542 |
| 2 | 1 | Net interest income | `pnl.nii.net` | 3,475 | **3,592** | 3,719 |
| 5 | 0 | Markets trading income | `pnl.noninterest.trading` | 389 | 154 | 363 |
| 6 | 1 | Net interest income | `pnl.nii.net` | 19 | **1** | (38) |
| 8 | 0 | Total income | `pnl.income.total` | 5,948 | 5,331 | 5,905 |
| 9 | 1 | Of which: Net interest income | `pnl.nii.net` | 3,494 | **3,593** | 3,681 |

Three rows share `pnl.nii.net`; two share `pnl.income.total`. They are
distinguished **only** by parent — which `row_lineage` already records and the
resolver already ignores.

The calc holds on every period column, which is what makes it structural
rather than coincidental:

```
1Q26:  3,475 + 19   = 3,494  ✅
4Q25:  3,592 +  1   = 3,593  ✅   ← your figure
1Q25:  3,719 + (38) = 3,681  ✅
```

Note row 6 at 1Q25 is **−38** — a real negative NII on the markets book, not a
variance artifact. The `% chg` columns beside it (`col_period` NULL,
`period_span` NULL, values `(7)`, `NM`, `>100`) are the ones that must be
dropped; they are currently loaded.

Target identity under the proposed model:

| row | element | `BusinessSegmentAxis` | legal_entity |
|---|---|---|---|
| 2 | `pnl.nii.net` | `SEG_COMMERCIAL` (new) | Consolidated |
| 6 | `pnl.nii.net` | `SEG_MARKETS` | Consolidated |
| 9 | `pnl.nii.net` | *default (total)* | Consolidated |
| 5 | `pnl.income.total` ← **element change** | `SEG_MARKETS` | Consolidated |
| 1 | `pnl.income.total` | `SEG_COMMERCIAL` | Consolidated |

Row 9 is the one that maps to UOB's and OCBC's single NII line.

`segment_dim` already has a `parent` column, and the business-segments table
proves `SEG_COMMERCIAL` is an intermediate node, not a rival partition:
CBG 5,257 + IBG 4,400 + Others 1,013 = **10,670** = `Commercial book total
income` (2H25), and the `Trading` column equals the Markets block exactly
(NII 21, total income 593). So this is one hierarchy, not two.

---

## F. Decisions needed before I write the specs

1. **`table_type` stability (C-2)** — blocking. Registry-assigned stable IDs, or
   the map never converges. Do you want a controlled vocabulary seeded from the
   exhibits we have, with normalized-title matching?
2. **Derived metrics in `fact_metric` (C-3)** — confirm the 117 derived rows get
   removed when `metric_definition` lands.
3. **Dimension storage shape (D-12)** — keep the column-per-axis pattern
   (cheap, but every new axis is a 4-table migration) or move to a
   `fact_dim_member` bridge (axes become data, but rewrites `v_cell_flat`,
   `sync_bq`, and every fact query)?
4. **Where the mapping layer's output lands (D-16)** — `row_dim.concept_key` is
   re-derived unconditionally by `concept/run.py` and wiped by doc-scoped
   reload. Confirm `bank_line_map` becomes the source of truth and `row_dim`
   becomes a materialized projection of it.
