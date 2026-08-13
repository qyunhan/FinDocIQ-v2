# Cloud Workstation setup — run FinDocIQ (extraction + the app)

> **STALE (2026-08-13) — read `README.md` instead.** This describes the old
> Cloud Workstation + GCS setup and is kept only for history. Specifically:
> §1 clones the **wrong (retired) repo**; §3's manual venv build is unnecessary
> (`run_doc.py` bootstraps it); §4 is obsolete (paddle builds itself); §5 pulls
> the DB from GCS, which is no longer needed — both databases are committed, and
> `--rebuild-db --only 4Q25,1Q26,2Q26` regenerates them. The repo now needs no
> GCP at all.

Run these in the **workstation's own terminal** (`w-yunhan-ms4efqkw`). ADC works
natively inside the workstation (default compute SA = editor), so Gemini/Vertex,
BigQuery, and GCS need **no key**. Recipe mirrors
`archive/2026-08-12-handover-cleanup/retired-gcp-retry/Dockerfile` (retired with
the Cloud Run retry worker, but still the authoritative dependency list).

## 1. Clone the repo (branch has all of today's work)
```bash
# If the repo is private, authenticate first:  gh auth login   (or use a PAT)
git clone https://github.com/qyunhan/FinDocIQ.git
cd FinDocIQ
git checkout v2-concept-toolkit
```

## 2. System libraries (PaddleOCR / opencv need these)
```bash
# Ubuntu 24.04 (the workstation base): libglib2.0-0 was renamed libglib2.0-0t64
# (t64 transition). On older Debian/Ubuntu use libglib2.0-0 instead.
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    libgomp1 libgl1 libglib2.0-0t64
```
Note: runtime apt installs on a Cloud Workstation usually do NOT persist across
a workstation restart (only /home persists) — re-run this after a restart, or
bake the libs into the workstation config image for a durable fix.

## 3. Python env — ONE venv, ALL THREE requirement files
(A workstation has no $HOME quota problem, so unlike Cloud Shell there is no
split paddle venv.)
```bash
# venv creation needs the venv module (not preinstalled on the workstation base):
sudo apt-get install -y --no-install-recommends python3.12-venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r findociq/requirements.txt -r findociq/requirements-paddle.txt \
            -r findociq/app/requirements.txt
```
(`findociq/app/requirements.txt` is needed too — the app + its tests import
streamlit/altair/matplotlib, which the pipeline requirements don't cover.)

## 4. PaddleOCR (STEP 0) — NOTHING TO DO, it builds itself

> **This step is obsolete. Do not follow the old instructions below.**
> `run_doc.py` builds the PaddleOCR environment on demand and applies the
> mkldnn fix itself. Skip to step 5.

`ensure_paddle_venv()` (`pipeline/run_doc.py:450`) builds `.venv-paddle` the
first time a document actually needs a scan — never at import, never for a
document whose `regions.csv` is already cached (226 such artifacts are
committed, so the whole current corpus reloads with paddle never installed). It
shells out to `tools/setup_paddle_venv.sh`, which also writes the
mkldnn-disabling `sitecustomize.py` into `/tmp/paddle-scratch`; `paddle_env()`
points `PYTHONPATH` there and `HOME` at `/tmp/paddle-scratch/paddlehome` so the
model weights stay off the ~5 GB `/home` quota.

To build it eagerly: `bash tools/setup_paddle_venv.sh`.
To forbid the build: `FINDOCIQ_NO_PADDLE_BOOTSTRAP=1` (STEP 0 then fails loudly
instead of installing ~1 GB unasked).

First real scan downloads the `PP-DocLayout-L` model — needs internet, no GCP.

<details>
<summary>Superseded manual workaround (kept for history — do not run)</summary>

paddlepaddle crashes on CPU via oneDNN/PIR unless `is_mkldnn_available()` is
forced False, picked up via PYTHONPATH (patching site-packages does NOT stick).
```bash
mkdir -p "$HOME/paddle-fix"
printf '%s\n' \
  "from paddlex.inference.models.runners.paddle_static.config import pp_option as _pp" \
  "_pp.is_mkldnn_available = lambda: False" \
  > "$HOME/paddle-fix/sitecustomize.py"
# run_doc STEP 0 shells out to REPO/.venv-paddle/bin/python3 — point it at this venv:
mkdir -p .venv-paddle/bin && ln -sf "$(command -v python3)" .venv-paddle/bin/python3
```
Why it was replaced: it put the fix in `$HOME`, outside the repo, so it died
with the machine and no clone could reproduce it.
</details>

## 5. Get the DB (if not already in the clone) + verify ADC
```bash
# pull the compiled DB checkpoint from GCS (source PDFs are pulled on demand by
# run_doc via source_store.materialize, so you don't need to sync all PDFs)
mkdir -p findociq/db
gsutil cp gs://findociq-sources-igc2026-team08-6311/db/compiled_fs.db findociq/db/ || \
  echo "no DB in GCS yet — a fresh run will create one"
# ADC sanity (should print a token, no key needed):
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
```

## 6a. Run ONE ingest end-to-end (CLI)
`--pdf` accepts a GCS source key; `source_store.materialize` pulls the PDF from
the bucket automatically. Use `--no-ipv4-shim` (workstation IPv4 is fine, and
the shim's sitecustomize would otherwise shadow the paddle fix).
```bash
python3 findociq/pipeline/run_doc.py \
    --pdf financial_statements/DBS_1Q26_trading_update.pdf --no-ipv4-shim
# list available source keys first if unsure:
python3 -c "import sys; sys.path.insert(0,'findociq/pipeline'); import source_store as s; print('\n'.join(s.list_sources()))"
```
Notes:
- `run_doc`/`ingest_quarter`/`ingest_manifest` launch their subprocess steps with
  `sys.executable`, so running `.venv/bin/python3 run_doc.py …` without activating
  the venv also works. (Before 2026-07-30 they hardcoded `python3`, which broke
  unless the venv was on PATH.)
- Not every doc in `compiled_fs.db` has its PDF in GCS (some 2025 docs were
  ingested pre-migration from local disk) — always pick from `list_sources()`.

## 6b. Or launch the app and watch it live
```bash
FINDOCIQ_DB_SOURCE=sqlite \
  python3 -m streamlit run findociq/app/findociq_app.py \
  --server.port 8080 --server.address 0.0.0.0 --server.headless true \
  --server.enableCORS false --server.enableXsrfProtection false
```
Then open it via the workstation's **web preview** on port 8080
(`https://8080-<WEB_HOST>` — see `printenv WEB_HOST`). The two `enable*=false`
flags are REQUIRED behind the cloudworkstations.dev proxy: without them
Streamlit's WebSocket origin check rejects the proxy domain and the page
never finishes loading ("Rejecting WebSocket connection from disallowed
origin"). Safe here — the preview URL is already auth-gated by Google login. The Ingest view
lists source docs (from GCS), triggers `run_doc`, and shows live per-stage
progress from `ingest_status`. Excel/CSV download and the Database / Table
Registry / Dashboard views all work against the DB.

## Notes
- Everything runs under the workstation's ambient service account (editor) — no
  key files, no IAM wall for Gemini/BQ/GCS.
- `run_doc` redoes whole-DB steps (concepts/fact_metric/ratios/xlsx) per doc; for
  a full 18-doc FS sweep that's slow — worth adding a "defer whole-DB steps to
  end" flag before batching (tracked in the extraction-quality roadmap).
- Smoothest for iterative work: run **Claude Code inside the workstation** so it
  has direct terminal + ADC (this Mac session can only hand you commands).
