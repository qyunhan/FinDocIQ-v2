# findociq_app.py — deploy notes

The ONE Streamlit app. `dashboard.py` was retired 2026-08-06 (see
`archive/2026-08-06-dashboard-retirement/`); everything renders here now.

Reads **`findociq/db/compiled_v2.db`** plus
`findociq/data/derived/dashboards/<BANK>_highlights_dashboard_{anchors,formulaanchors}.csv`.

## Local
    PYTHONPATH="$HOME/paddle-fix" .venv/bin/python -m streamlit run \
      findociq/app/findociq_app.py --server.port 8501 --server.address 0.0.0.0 \
      --server.headless true

**A DB rebuild needs a process RESTART.** `_backend()` is `@st.cache_resource`, so
it holds the sqlite connection — and `build_compiled_v2.py` deletes and recreates
the file, leaving the cached connection pointed at the old, unlinked inode.
Editing the source reruns the script but keeps that connection. "Clear cache"
from the app menu also works.

## Cloud Run — the live deployment

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
`build_compiled_v2.py --carry-from` copies reference tables the loader does not
produce. Without `table_catalog` the **Table Registry view renders empty with no
error**; `bank_line_map` / `row_lineage` back its drill-down.
