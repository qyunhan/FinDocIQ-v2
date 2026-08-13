# Retired 2026-08-06 — pipeline dead code

Nothing here is reachable from any live entry point. Established by an AST import
graph over `pipeline/`, rooted at everything the DBS 2Q26 end-to-end run actually
touches (`docs/followthrough.md` §1 is that lifecycle):

    run_doc.py · ingest_quarter.py · PASS2_v2.py · db_check_xlsx.py
    toc/toc_stage.py · toc/toc_to_db.py · toc/pass1_to_v7.py
    discover/pass1_toc.py · discover/section/candidates.py
    concept/run.py · concept/build_fact_metric.py · concept/compute_ratios.py
    mapping/seed_registry.py · mapping/Stamping/*
    ingest/scrape_bank_ir.py · ingest/sync_bq.py · preflight_invariants.py

141 -> 115 files. Every module below has **zero non-test importers** outside its
own cluster; each cluster is closed.

## legacy/ — superseded by the current pipeline

| module | last touched | superseded by |
|---|---|---|
| `discover/discover.py`, `discover_mineru.py` | 2026-06-29 | `discover/pass1_toc.py` + `toc/toc_stage.py` |
| `templates/{stamp,align,review}.py` (+ test) | 2026-07-01/13 | the masterlist + `mapping/Stamping/` |
| `route/{scan,merge_map}.py` | 2026-07-13 | `classify/family.py` routing in `run_doc` |
| `discover/section/section_manifest.py` (+ test) | — | only importer of `route/scan.py` |
| `universal/auto_extract.py` | 2026-07-16 | `PASS2_v2.py` -> `pass2/` package |
| `extract_run.py` (+ test) | 2026-07-16 | `pass2/load_v7.py` |

`route/scan.py` looked live on a grep — `run_doc.py` contains the word "scan"
(STEP 0, PaddleOCR). An AST read of run_doc's imports shows
`ingest_status, source_store, pass2.load_v7, verify_cells, pass2.geometry,
classify.family` and no `route`.

## migrations/ — one-off, already applied

`migrate_add_clean_labels` · `migrate_add_industry_dim` · `migrate_add_mapping_layer`
· `migrate_ingest_status_keys` (+ test) · `mapping/migrate_add_document_alias`
· `mapping/migrate_add_legal_entity` · `mapping/migrate_add_table_catalog`
· `mapping/migrate_consolidate_table_type_ids` · `mapping/test_migrate_serving_views`
· `pass2/backfill_col_period` (+ test) · `pass2/migrate_add_period_source`

Each ALTERs or backfills a schema that `schema_v7.sql` now declares outright. They
are recoverable from git if a historical DB ever needs re-migrating.

`migrate_add_table_catalog.py` is the one worth knowing about: it built
`table_catalog` from `table_registry_seed.csv` (itself retired on 2026-08-06). That
table is still LIVE — the dashboard's Table Registry view reads it, and
`build_compiled_v2 --carry-from` copies it into `compiled_v2.db`. Restore both this
script and the seed CSV if `table_catalog` ever needs rebuilding.

## KEPT despite having no importers — standalone CLI tools

`concept/gt_check.py` (the external ground-truth gate) · `concept/audit_nature.py`
· `concept/discover_cuts.py` (writes `concept_cuts.csv`, which `ingest/sync_bq.py`
reads) · `concept/query_db.py` · `mapping/{resolve_anchors,load_anchors,m2_canonical_leaf}.py`
· `fix_identity_misstamps.py` · `retry_worker.py` · `tag_workbook.py`
· `ingest_manifest.py` · the `__init__.py` package markers.

## Verified after the move
`pass2` + `Stamping` 90 · `mapping` 35 · `app` 89, and `import run_doc` succeeds.

`mapping/test_mapping.py` fails to collect from the repo root
(`ModuleNotFoundError: apply_dashboard_rows`) — it needs `mapping/` on `sys.path`.
PRE-EXISTING, confirmed by `git stash`; unrelated to this cleanup.
