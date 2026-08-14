# The app reads `canonical_leaf_id`, and nothing else

**Status:** in force as of 2026-08-14. Supersedes every app read of the retired
mapping layer.

## 1. The rule

The Streamlit app (`findociq/app/findociq_app.py`) resolves identity by
**`canonical_leaf_id` alone**. The expected set of addresses is DECLARED by the
dashboard anchor CSVs in `findociq/data/derived/dashboards/`; what the corpus
actually holds is `row_dim.canonical_leaf_id`. The two are matched on the leaf
address — no label comparison, no lineage walk, no hand-authored catalog.

These tables are **banned from the app's read path**:

| Banned | Was used by | Now |
|---|---|---|
| `table_catalog` | Table Registry masterlist | anchor CSVs |
| `bank_line_map` | Table Registry line items | `row_dim.canonical_leaf_id` |
| `row_lineage` | Table Registry benchmark rows | `row_dim` directly |
| `v_fact_metric_serving` | Dashboard "Concept compare" | anchor path |
| `v_cell_flat` | Ingest result grid | `cell_fact` + `row_dim` + `col_dim` |

`compiled_v2.db` ships nine tables and zero views, and carries none of the
above. Reading them was not a degraded experience — it was three of four views
dead on the public deploy.

## 2. Why the registry is anchor-keyed

The Dashboard already resolves every figure by
`(bank, table_type_id, canonical_leaf_id)` against the anchor CSVs. Building the
Table Registry on the *same declaration* against the *same stamped column* means:

- the two views can never disagree about what an address means;
- dropping a new `<stem>_anchors.csv` pair into the dashboards directory extends
  **both** with no code change — the same property `available_dashboards()`
  already gives the Dashboard;
- coverage is a measurement, not an assertion. A declared address the corpus
  never stamped is reported with `times_captured = 0` rather than dropped.

The inverse is reported too — captured leaf addresses no anchor declares — because
a registry that only lists declarations can never reveal a leaf worth declaring.

**First run (compiled_v2.db, 2026-08-14):** 159 declared, 156 captured, 3
uncaptured (all OCBC), 1,189 stamped-but-undeclared. It immediately surfaced a
real addressing near-miss: OCBC `FS_CUSTOMER_LOANS / net_loans` is declared and
never captured, while `FS_CUSTOMER_LOANS / allowances::net_loans` is captured 10
times.

## 3. Where the branch is visible

Per the decision-tree-visibility rule, the pivot is observable without reading
code:

- The Table Registry's own caption names the join key and the three banned
  tables explicitly.
- The registry grid prints `table_type_id` + `canonical_leaf_id` per row, so the
  address that resolved (or didn't) is on screen.
- "Show only uncaptured anchors" isolates exactly the addresses that render
  blank on the Dashboard.
- Database → "Per-cell identity" prints `canonical_leaf_id` × `canonical_col_id`
  for every individual cell, with a CSV download.

## 4. Schema drift is handled per COLUMN

The serving schema moves. `run_opt()` degrades a missing **table**; it cannot
help with a missing **column**, because the query raises against a table that
exists. `select_clause(wanted, available, prefix)` probes `PRAGMA table_info`
and emits absent columns as `NULL AS <name>`, which keeps every downstream
frame's shape fixed.

Known absent in `compiled_v2.db`: `row_dim.row_leaf_label_clean`,
`row_dim.concept_key`, `cell_fact.concept_key`, `cell_fact.geo_key`,
`cell_fact.segment_key`.

## 5. Open: the column half of the address

`col_dim.canonical_col_id` is declared (`schema/schema_v7.sql:384`) and
**populated for 0 of 1915 columns** in both `compiled_v2.db` and
`compiled_fs.db`. The column-axis stamp of
`2026-08-09-column-axis-identity.md` has never been run on this corpus.

The app displays the column and states the coverage rather than hiding it. When
the stamp lands, the per-cell address becomes complete with no app change.

## 6. Deployment constraint this rule serves

The public deploy is Streamlit Community Cloud, built from `main`. It has **no
GCS, no BigQuery, no PaddleOCR**, and every push redeploys with a fresh process
and the newly committed `compiled_v2.db`. Anything the app reaches for must
therefore be either committed to the repo or optional. In particular, any import
from `findociq/pipeline/` must be `try`-wrapped — an unguarded one on the
landing view took the whole page down until 2026-08-14.
