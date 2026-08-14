# findociq_app.py — deploy notes

The ONE Streamlit app. `dashboard.py` was retired 2026-08-06 (see
`archive/2026-08-06-dashboard-retirement/`); everything renders here now.

Reads **`findociq/db/compiled_v2.db`** plus every anchor pair in
`findociq/data/derived/dashboards/<stem>_{anchors,formulaanchors}.csv`, and the
source PDFs under `findociq/data/sources/`. All three are committed to git, so
the app needs **no cloud credentials to render** — that is what makes the public
deploy work.

## Streamlit Community Cloud — the live public deployment

Built straight from `main` on GitHub. It clones the repo, so `compiled_v2.db`
(a 10 MB blob committed at `findociq/db/`) ships with it and **every push
redeploys with the new DB** — including a fresh process, so the
`@st.cache_resource` connection trap below cannot bite on a push.

Root `requirements.txt` is a one-line `-r findociq/app/requirements.txt`
pointer, because Community Cloud only reads the repo root.

**No GCS, no BigQuery, no PaddleOCR there.** Anything that reaches for them must
degrade, not raise — see "The four ways this app has died" below.

## Local
    .venv/bin/python -m streamlit run \
      findociq/app/findociq_app.py --server.port 8501 --server.address 0.0.0.0 \
      --server.headless true

**A DB rebuild needs a process RESTART.** `_backend()` is `@st.cache_resource`, so
it holds the sqlite connection — and `build_compiled_v2.py` deletes and recreates
the file, leaving the cached connection pointed at the old, unlinked inode.
Editing the source reruns the script but keeps that connection. "Clear cache"
from the app menu also works.

## Cloud Run — RETIRED

The `findociq-dashboard` Cloud Run service and the GCP project behind it were
retired in August 2026. The deployment steps that used to be here (staged
bundle, `gcloud run deploy`, the `allUsers` IAM binding that was never granted)
are gone with them — see git history if you need them.

Streamlit Community Cloud is the only deployment. It needs no cloud account:
the database, the anchor CSVs and the source PDFs are all committed.

## What compiled_v2.db must carry

Nine tables, and nothing else is required: `document`, `table_t`, `section`,
`row_dim`, `col_dim`, `cell_fact`, `geo_dim`, `segment_dim`, `ingest_status`.

**The app no longer reads `table_catalog`, `bank_line_map`, `row_lineage`,
`v_fact_metric_serving` or `v_cell_flat`** (2026-08-14). The retired mapping
layer is gone from the read path entirely; the only identity join anywhere in
the app is `canonical_leaf_id`, declared by the dashboard anchor CSVs. There is
nothing left for `build_compiled_v2.py --carry-from` to carry for the app's sake.

`col_dim.canonical_col_id` is **210 of 1915 populated** (2026-08-14). It had
regressed to 0 — the stamping was once patched into the built artifact and a
rebuild dropped it — and is now applied to `compiled_fs.db` and carried through
by `build_compiled_v2`, so a rebuild keeps it. The remaining columns are period
columns (never stamped, by design) and table types with no column block
authored yet; the app shows the coverage rather than hiding the column.

## The four ways this app has died

All four were live on the public deploy and all four are fixed; keep them in
mind before adding a read.

1. **A missing COLUMN, not a missing table.** `row_dim` in `compiled_v2.db` has
   no `row_leaf_label_clean` / `concept_key`, and `cell_fact` no
   `concept_key`/`geo_key`/`segment_key`. `_raw_frame` selected them through
   `run()`, so picking ANY table in Database raised `no such column` — `run_opt`
   is table-level and could not help. Use `_sel(table, [cols])`
   (`select_clause`) for anything that might drift; absent columns are served as
   `NULL AS <name>` so frame shapes stay fixed.
2. **An unguarded pipeline import on the LANDING view.** Ingest is the first
   radio option, and it did a bare `import source_store` — wrong path (the
   module is `pipeline/common/source_store.py`) and unguarded, so every fresh
   session rendered the sidebar followed by a traceback. Reaching into
   `pipeline/` must always be `try`-wrapped: that tree is not guaranteed to
   import on a deploy with no credentials.
3. **A path convention the resolver did not know.** `document.source_file`
   records both a flat and a foldered key; 3 of 10 documents use the foldered
   form the repo does not store. `resolve_source_pdf` now falls back to the
   basename anywhere under `data/sources/`, which resolves all 10.
4. **A DB rebuild without a process restart** — see below.
