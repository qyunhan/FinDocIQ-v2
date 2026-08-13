# Retired 2026-08-06 — generated masterlists and the table registry seed

Nothing in this directory is read by the pipeline. Kept for provenance only;
git history has the full record.

The one-time generator scripts (`build_masterlist_proposed.py`,
`build_masterlist_v3.py`) were DELETED rather than archived — single-purpose,
and recoverable from git if ever needed.

## Why

`canonical_leaf_id` now has exactly one source: **`data/derived/masterlist/`**,
authored and curated by hand. Everything here was either a *generated proposal*
for those ids, or infrastructure that only existed to produce them.

The immediate trigger: a stamping run matched against `masterlist_proposed_v3/`
and wrote ids that disagreed with the authored masterlist — the of-which memo
form (`of_which::net_interest_income` vs `of_which_net_interest_income`) and all
of `FS_PER_SHARE`. Generated ids are a proposal; only the curated file is
authority. The stamping path now has no code path that can read anything else.

## Contents

| item | was | replaced by |
|---|---|---|
| `masterlist_proposed/` | v1 generated registry (1,613 leaves) | `data/derived/masterlist/` |
| `masterlist_proposed_v2/` | v2 generated registry | ″ |
| `masterlist_proposed_v3/` | v3 generated registry (reference-set + banner rule) | ″ |
| `table_registry_seed.csv` | caption → `table_type_id`, `doc_kind` per table | **content matching** |

## Why the seed is gone

Tables are now located by CONTENT, not by caption: a table *is* the one whose
printed row paths match the masterlist's `full_path` values
(`resolve_canonical_leaf.locate_tables`). Measured, that is strictly better:

* **Immune to caption collisions.** DBS prints `Selected balance sheet items ($m)`
  in Overview and `Selected balance sheet items` under PERFORMANCE BY GEOGRAPHY.
  Caption matching fused them — 12 unresolved rows and 3 rows wearing the wrong
  table's ids. Content matching scores the geography table 0.
* **Finds every vintage for free.** One masterlist locates the DBS Overview
  tables in 1Q23, 1Q25, 2Q25, 3Q25, 1Q26 and 4Q25 with no per-period config.
* **Needs only the masterlist and the DB.**

`table_catalog` (in the DB) was populated from this CSV by
`pipeline/mapping/migrate_add_table_catalog.py` and still serves the dashboard;
that migration has already run. Re-running it is the only reason to restore this
file.

## NOT retired

* `data/derived/masterlist/masterlist_leaf_aliases.yaml` — moved INTO the
  masterlist folder, not archived. Its 5 entries are the DBS markets-book and
  `profit_before_allowances` renames, and they are exactly the rows that fail to
  resolve on 1Q23 (`Treasury Markets total income²`, `Profit before allowances`,
  `Other non-interest income`, and the two Treasury-Markets children). Nothing
  reads it yet — folding it into the masterlist CSVs as extra `full_path` rows
  is the outstanding task.
* `pipeline/mapping/Stamping/masterlist_derive.py` — the shared normalisation and
  ancestry rules. Despite the name it holds no leaf ids.

## Also retired: `app/highlights.yaml` (2026-08-06)

The Key Financial Highlights view's item list. It named 26 `concept_key` values
and their `unit_hint` / `section`, which the app resolved through `fact_metric`.

Replaced by **`data/derived/dashboards/<BANK>_highlights_dashboard_{anchors,formulaanchors}.csv`**,
which address the DB directly on `(bank, table_type_id, canonical_leaf_id)`.
Nothing about the row list is declared twice any more:

* `unit_hint` — now the unit the filing reported on the row's non-derived cells
  (`S$m` / `%` / `per_share`), unambiguous because the query drops
  `col_role='derived_skip'`.
* `section` — now the printed caption of the table the leaf came from.
* rollups — now DECLARED in the formula file (`Net interest income = commercial
  book NII + markets NII`) instead of inferred by a resolver tie-break.

`load_highlights_config` and `highlights_frame` were deleted from
`findociq_app.py` along with their 7 tests.

The one judgment call this file recorded — moving customer loans to the GROSS
basis (`docs/six-bug-diagnosis.md` bug 3, on the grounds that a row labelled
"Net" serving a gross figure is worse than a blank) — is carried forward by the
anchors, verified rather than assumed:

    bank_line_map   bs.assets.customer_loans_gross <- DBS FS_BALANCE_SELECTED
                                                      customer_loans (human_confirmed)
    fact_metric     bs.assets.customer_loans_gross FY25 = 445,011
    anchor path     "Gross Customer Loans"         FY25 = 445,011

DBS's printed `Customer loans` line in the Overview table IS the gross figure.
Nothing was lost in the swap.
