# Retired 2026-08-12 — handover cleanup

Follows the same rule as `archive/2026-08-06-pipeline-cleanup/`: nothing here is
reachable from a live entry point. Established by an AST import graph over
`pipeline/`, `app/` and `tools/`, rooted at every entry point `run_doc.py` reaches —
including the ones it invokes by **subprocess**, not just by import (the 08-06 pass
missed those, which is how the breakage below survived).

## pillar3-branch-b/ — the superseded Pillar-3 orchestrator

`tag_sections.py` · `test_tag_sections.py` · `score_sections.py` · `test_score_sections.py`

**These were already dead — and broken — before this pass.** The 2026-08-06 cleanup
archived `discover/section/section_manifest.py` to `legacy/` on the finding that it was
"only importer of `route/scan.py`". But `tag_sections.py` imports it at module scope
(`import section_manifest`, line 40), so since 2026-08-06:

    $ python3 findociq/pipeline/discover/section/tag_sections.py ...
    ModuleNotFoundError: No module named 'section_manifest'

i.e. PIPELINE.md STEP 1 "Branch B" documented a command that could not start.

Nothing lost, because **`run_doc.py` never called it**. The live Pillar-3 route is the
2026-07-16 pivot, and it is a different code path (`run_doc.py:389`):

    family == 'pillar3'  ->  discover/pass1_toc.py  ->  toc/pass1_to_v7.py  ->  toc/toc_to_db.py

`tag_sections.py` was the *older* Branch B orchestrator (`candidates` -> `toc_match` ->
`assign_tables` -> `section_manifest`). Those first three modules are STILL LIVE —
`candidates.py` is invoked by `run_doc.py` and imports `toc_match`/`typographic_headings`/
`gemini_arrange` — only the orchestrator and the manifest step are retired.

`score_sections.py` goes with it: it scores a `section_manifest.csv` against a GT csv,
and `tag_sections.py` was the only producer of that file. The only ones left on disk are
frozen 2026-07-07 experiment outputs.

PIPELINE.md STEP 1 was rewritten in the same commit to describe the `pass1_toc` route
that actually runs.

## Not archived, though the import graph shows no importer

Standalone operator CLIs, documented in `docs/followthrough.md` and the tech report —
same call as the 08-06 pass made:

`Stamping/{stamp_tables,propose_masterlist}.py` · `mapping/{resolve_anchors,load_anchors,
m2_canonical_leaf,audit_coverage,backfill_map,apply_dashboard_rows,quarantine_*}.py` ·
`concept/{gt_check,audit_nature,discover_cuts,query_db}.py` · `fix_identity_misstamps.py`
· `retry_worker.py` · `tag_workbook.py` · `ingest_manifest.py` · `ingest_quarter.py` ·
`preflight_invariants.py` · `tools/{build_compiled_v2,replay_load,restamp_columns,
timeseries_metrics}.py`

---

# Second pass — full `pipeline/` sweep

The pass above was scoped to Branch B. This one classified EVERY module under
`pipeline/` (plus `app/`, `tools/`) with an AST reverse-import map, then checked each
candidate's target tables against `schema/schema_v7.sql` AND the live
`db/compiled_fs.db`. Grep was not trusted: it matches docstrings, and
`repo_audit.md` (a generated 500KB inventory, deleted in this pass) names every file
in the repo, so every module looked "documented".

Result: **114 -> 50 non-test modules under `pipeline/`**, 56 -> 48 test files.

## branch-b-machinery/ — the rest of the retired Pillar-3 branch

`toc_match.py` · `assign_tables.py` · `typographic_headings.py` · `gemini_arrange.py`
· `sections_from_gemini.py` (+ 3 tests)

The import direction is the opposite of what it looks like: these import
`candidates.py`, **nothing live imports them**. `pass1_toc.py` — the live pillar3
route — imports only stdlib + pypdfium2 + pdfplumber, nothing from
`discover/section/`. `candidates.py` imports none of its siblings either. So the
only live modules left in that package are `candidates.py` (STEP 0) and
`batch_scan.py` (its corpus-wide wrapper).

## anchor-mapping-layer/ — superseded by the masterlist + canonical leaf

`resolve_anchors.py` · `load_anchors.py` · `backfill_map.py` · `audit_coverage.py`
· `apply_dashboard_rows.py` · `m2_canonical_leaf.py` · `quarantine_duplicate_page_tables.py`
· `quarantine_f2_geo_wildcard.py` (+ 2 tests, + `test_mapping_anchor_half.py`)

Every one addresses `bank_line_map` / `table_catalog` / `v_fact_metric_serving` /
`anchor_*`. None of those is declared in `schema_v7.sql`, and none exists in the
built DB:

    sqlite> select name from sqlite_master where name like '%anchor%' or name like '%line_map%';
    (0 rows — only col_lineage / row_lineage, which schema_v7 DOES declare)

`mapping/Stamping/` (masterlist -> `canonical_leaf_id` / `canonical_col_id`) replaced
this layer. Archiving it also removed 2 of the 5 then-failing tests, which were
failing precisely because they queried these tables.

`pipeline/mapping/test_mapping.py` was SPLIT, not moved: lines 1-99 test
`mapping/normalize.py` + `mapping/registry.py` (both live) and stay; the anchor half
is here as `test_mapping_anchor_half.py`.

**KEPT:** `mapping/migrate_serving_views.py` — live, imported by
`preflight_invariants.py:413`.

## applied-migrations/ — schema_v7 now declares it outright

`migrate_add_equity_component_dim.py` — `schema_v7.sql` declares `equity_component_dim`
(7 refs). Same call as the 2026-08-06 `migrations/` archive.

## orphaned-concept-outputs/

`discover_cuts.py` — writes `data/derived/concept_cuts.csv`. The 2026-08-06 README kept
it because "`ingest/sync_bq.py` reads it"; that is no longer true —
`ingest/sync_bq.py:35` now reads `concept_resolution_log`, commented
"replaces nonexistent concept_dim/concept_cuts". Nothing reads the CSV.

`gt_check.py` — the GT gate, reads the retired `v_fact_metric_serving`.

## retired-streamlit/

`chat_report.py` — the OLD chat-with-data Streamlit app. Exactly three files in the
repo import streamlit: this one, the already-archived `2026-08-06-dashboard-retirement/
dashboard.py`, and `app/findociq_app.py` — the live one, deployed per `app/DEPLOY.md`
and consumed by the Findociq-Dashboard repo. `chat_report.py` was the only caller of
`app/spec.py`'s `load_registry` / `fetch_data`.

## Relocated, NOT archived

`tools/slides/` — `slide_kit.py`, `nsfr_slide.py`, `timeseries_metrics.py`,
`test_slide_kit.py`. Off the pipeline path but kept as reference; see that folder's
README for why they cannot run (v5 `col_key` queries vs schema_v7).

## Deleted outright

`repo_audit.json` (503KB) + `repo_audit.md` (41KB) — a stale 2026-08-06 generated
inventory, superseded by this pass. `audit_pipeline.py` kept so it can be re-run.

---

# Third pass — the concept layer, and the last of the anchor era

## concept-layer/ — `pipeline/concept/` in full (10 modules + 9 tests)

Retired on measurement, not on the docs (which still described it as a stage):

    fact_metric              ABSENT from compiled_fs.db
    concept_dim              ABSENT
    metric_definition        ABSENT
    concept_resolution_log   present, 0 rows
    concept_map              present, 19 rows
    concept tables in compiled_v2.db (what the app reads):  NONE

`run_doc.py` said it itself — "concept layer OFF by default — identity comes from
the masterlist at load (STEP 3)" — and `findociq_app.py:2172` says "compiled_v2.db
drops the concept layer **by design**". So the layer ran only behind
`--with-concepts`, built a table nothing reads, and logged nothing. Row identity is
`mapping/`'s `canonical_leaf_id` (4,064 rows).

`run_doc.py` lost STEP 4a/4b/4c, `--with-concepts` and `--no-llm` (-51 lines).
STEP 3b (registry seed + classify) SURVIVES the retirement but moved to its own
`--seed-registry` flag: it was only ever bundled with the concept gate, and the
masterlist AUTHORING flow needs it (`propose_masterlist.py` resolves table types
through `table_registry_alias`).

`ingest/sync_bq.py` dropped `concept_map` / `concept_resolution_log` / `fact_metric`
/ `v_fact_metric_serving` from `TABLES_TO_SYNC` — a listed name that no longer
exists makes the sync fail on `SELECT * FROM <missing>`. Every remaining entry was
AST-checked against the built DB.

## preflight_invariants.py -> anchor-mapping-layer/

Kept by BOTH previous passes as a "standalone operator CLI". It is not — it is
broken, and nothing invokes it (`run_doc.py` has zero references):

    $ python3 findociq/pipeline/preflight_invariants.py --db db/compiled_fs.db
    sqlite3.OperationalError: no such table: bank_line_map        # line 61

It also queried `v_fact_metric_serving` (line 358) and was the ONLY live importer
of `concept.load_dictionary` — the single thread that made `concept/` look reachable.

## migrate_serving_views.py -> applied-migrations/

Its last importer was `preflight_invariants.py`. It retrofits `v_cell` /
`v_cell_leaf` / `v_cell_sumsafe` into an older DB, and `schema_v7.sql:621` now
declares all of them outright — so it is an applied migration, same as the others.
**`v_cell` itself is LIVE and stays**; only the migration that used to create it is
retired.

## Flattened, not archived

`mapping/Stamping/*` -> `mapping/`. With the anchor layer gone, `mapping/` held 4
modules and `Stamping/` 5; a package inside a package for that is noise. All
`mapping.Stamping` imports rewritten across code and docs, and the two moved tests
had their `sys.path` depth corrected (`parents[2]` -> `parents[1]`).

## Result

`pipeline/`: **114 -> 38 non-test modules**. Tests: **56 -> 38 files, and for the
first time ALL of them pass.**

## Also moved out of pipeline/ (not archived)

`pipeline/workflows/retry_worker_workflow.yaml` -> `tools/retry_worker_workflow.yaml`.
It is a GCP Cloud Workflows DEPLOY MANIFEST, not pipeline code: nothing in the repo
reads it, `gcloud workflows deploy --source=...` does. It now sits with the other
infra scripts (`vm_up.sh` / `vm_down.sh` / `setup_paddle_venv.sh`). GCP is used
mainly to hold the `compiled_fs.db` checkpoint, so this is optional infra rather
than part of `run_doc.py`. `pipeline/` now contains only code the pipeline runs.

`prompts/concept_classify.txt` joined `dead-prompts/` — its only reader was
`concept/resolve_llm.py`, archived in the same pass. `pipeline/stage1_extract/gemini/
prompts/` is down to the one prompt that is actually selected and sent:
`fs_toc_headings.txt` (read by `toc/toc_stage.py`). The pass2 extraction prompt is
INLINE at `stage1_extract/chunk/extract.py` (`_PROMPT`, hashed for the audit trail).

## retired-gcp-retry/ — the Cloud Run retry harness that never ran

`retry_worker.py` · `test_retry_worker.py` · `Dockerfile` · `retry_worker_workflow.yaml`

Built, blocked on access it never received, never used. Evidence, not opinion:

* **3 commits ever touched `retry_worker.py`** — the original build, one refactor,
  and the 2026-08-12 stage split. Nothing else.
* **`ingest_status` holds exactly ONE row**, from an ordinary local run
  (`UOB_2Q26 … stage=done, state=ok`, 2026-08-07). No retry-worker-driven rows.
* **Its orchestration layer was never unblocked.** The Phase C plan
  (`archive/2026-08-12-docs-cleanup/2026-07-29-dashboard-trigger-pending-access.md`)
  reads *"BLOCKED on IAM grant. Do NOT start until the user says access has landed"* —
  granting roles to a service account needs `setIamPolicy`, which `roles/editor`
  lacks. The dashboard was moved to Streamlit Community Cloud instead, and GCP kept
  only the `compiled_fs.db` checkpoint.
* **`run_doc.py` never referenced any of it.**

The `Dockerfile` goes with them: its `ENTRYPOINT` was `retry_worker.py`. It is still
the authoritative dependency recipe (`docs/workstation-setup.md` mirrors it), which
is why it is archived rather than deleted.

`ingest_status.should_retry()` was removed in the same change — `retry_worker` was
its only caller. `ingest_status.mark()` stays: `run_doc.py` uses it.

How unused this path was is measurable: the stage split left the Dockerfile's
ENTRYPOINT pointing at a moved file, and nothing noticed until this retirement.
