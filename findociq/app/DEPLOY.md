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

## Cloud Run — the older, private deployment

    https://findociq-dashboard-663571970232.asia-southeast1.run.app

**GIT PUSHES DO NOT UPDATE IT.** The image is built from a staged bundle and the
DB is baked INTO it. This is the trap that left revision 00002 serving a months-old
`dashboard.py` + `compiled_fs.db` snapshot while `main` moved on. Any new document,
re-stamp or masterlist edit needs a fresh deploy:

    STAGE=$(mktemp -d)
    mkdir -p "$STAGE/findociq/app" "$STAGE/findociq/db" \
             "$STAGE/findociq/data/derived/dashboards"
    cp findociq/app/findociq_app.py  "$STAGE/findociq/app/"
    cp findociq/app/Dockerfile       "$STAGE/Dockerfile"
    cp findociq/app/requirements.txt "$STAGE/requirements.txt"
    cp findociq/db/compiled_v2.db    "$STAGE/findociq/db/"
    cp findociq/data/derived/dashboards/*.csv \
       "$STAGE/findociq/data/derived/dashboards/"
    ( cd "$STAGE" && gcloud run deploy findociq-dashboard --source . \
        --project igc2026-team08-6311 --region asia-southeast1 \
        --memory 1Gi --min-instances 0 )

The staged tree must keep the `findociq/app/` depth: `findociq_app.py` computes
`REPO = Path(__file__).resolve().parents[2]`, and the Dockerfile's `WORKDIR /app`
+ `COPY findociq/ findociq/` puts it at `/app/findociq/app/`, so `REPO` resolves
to `/app`.

### Access
The service is **not** public — no IAM bindings, so every unauthenticated request
returns 403. To open it:

    gcloud run services add-iam-policy-binding findociq-dashboard \
      --region asia-southeast1 --project igc2026-team08-6311 \
      --member=allUsers --role=roles/run.invoker

This needs `run.services.setIamPolicy`, and on an org with
`constraints/iam.allowedPolicyMemberDomains` a binding to `allUsers` may be
refused outright. Not yet attempted.

## What compiled_v2.db must carry

Nine tables, and nothing else is required: `document`, `table_t`, `section`,
`row_dim`, `col_dim`, `cell_fact`, `geo_dim`, `segment_dim`, `ingest_status`.

**The app no longer reads `table_catalog`, `bank_line_map`, `row_lineage`,
`v_fact_metric_serving` or `v_cell_flat`** (2026-08-14). The retired mapping
layer is gone from the read path entirely; the only identity join anywhere in
the app is `canonical_leaf_id`, declared by the dashboard anchor CSVs. There is
nothing left for `build_compiled_v2.py --carry-from` to carry for the app's sake.

`col_dim.canonical_col_id` is declared but **0 of 1915 populated** in both
`compiled_v2.db` and `compiled_fs.db` — the column-axis stamp
(`docs/specs/2026-08-09-column-axis-identity.md`) has never been run. The app
displays the column and says so rather than hiding it; when the stamp lands it
populates with no code change.

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
