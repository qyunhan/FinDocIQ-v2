# Session handoff — 2026-07-24 — 2025/2026-Q1 ingest + dashboards

Read this first, then the memories `paddle-mkldnn-workaround` and `vm-claude-dev-spec`.

## TL;DR
Goal in flight: ingest the 2025 + 2026-Q1 bank filings (DBS/OCBC/UOB) into
`compiled_fs.db` → BigQuery → dashboard. **Everything except the multi-doc ingest
is DONE and merged to `main` (PR #2, merge commit `e1c523b`).** The one thing
blocking the batch ingest is PaddleOCR (STEP 0) on this CPU — see **Blocker**.

## Done & merged to `main` (PR #2)
- **Ingest pipeline**: `pipeline/ingest/scrape_bank_ir.py` (generic `--periods`
  scope filter), `pipeline/classify/family.py` (period parser now handles
  half-year `1H25`, hyphenated `-1q-2025`, date `... 30 september 2025`; new
  `other` family so regulatory notices are dropped; `classify_doc` +
  keep-all `build_manifest`), and `pipeline/ingest_quarter.py` — the
  **per-bank-per-period orchestrator** (`--bank DBS --period 2026-Q2`; scrape →
  run_doc each → sync_bq; `--dry-run`, `--no-scrape`).
- **`toc_stage.py` Vertex fix**: replaced `client.files.upload` (Gemini-Developer
  only) with inline `Part.from_bytes`. This blocked STEP 1 for every doc.
- **`run_doc.py`**: build fact_metric/ratios AFTER verify; add STEP 4c ratios +
  STEP 7 sync_bq + `--no-sync-bq`.
- **`compute_ratios.py`**: `period_span` in the avg() grouping.
- **`sync_bq.py`**: corrected TABLES_TO_SYNC names (segment_dim/geo_dim, added
  concept_resolution_log; removed nonexistent dim_segment/dim_geo/concept_*).
- **`app/dashboard.py`**: dual-source Streamlit (env `FINDOCIQ_DB_SOURCE=sqlite|bq`)
  + `Dockerfile`, `requirements*.txt`, `DEPLOY.md`.

## Data state
- `compiled_fs.db`: **8 docs** = 7 original 2025 + `DBS_1Q25_trading_update`
  (ingested this session end-to-end: ~180 s, ~$0.04, verify PASS). fact_metric 1558.
- BigQuery `igc2026-team08-6311.findociq`: **synced, parity** (8 docs / 1558
  fact_metric), 9 clean tables (orphans dropped).

## Deployments
- Cloud Run `findociq-dashboard` (asia-southeast1): **BQ-backed, PRIVATE**. Reads
  live BQ (no redeploy needed after sync). View it:
  `gcloud run services proxy findociq-dashboard --region asia-southeast1 --project igc2026-team08-6311 --port 8080` → http://localhost:8080.
  Public (`allUsers`) is BLOCKED: user is `roles/editor`; only the provisioner SA
  owner (`provisioner-sa@edmdatadisc…`) / competition organizers can flip it.
- cloudflared tunnel + local streamlit: **stopped**.
- Streamlit Community Cloud: repo prepped (app/requirements.txt + committed DB),
  NOT deployed — needs the user's GitHub click-through at share.streamlit.io.

## Environment (Cloud Shell — EPHEMERAL: VM resets, only 5 GB $HOME persists)
- ADC works for Vertex + BigQuery. Project `igc2026-team08-6311`, region
  `asia-southeast1`.
- Base `python3` has the pipeline requirements installed (this session).
- Paddle venv at `/tmp/paddle-scratch/venv` (paddlepaddle 3.3.1 / paddleocr 3.7.0),
  with `.venv-paddle` symlinked to it. **All under /tmp — dies on session reset.**

## UPDATE (later 2026-07-24) — paddle blocker RESOLVED; new data-quality findings
- **Paddle STEP 0 works in-loop now**: run with `PYTHONPATH=/tmp/paddle-scratch`
  AND `--no-ipv4-shim`. Root cause: run_doc's IPv4-shim writes its own
  `sitecustomize.py` and prepends it to PYTHONPATH; Python loads only the first
  `sitecustomize`, shadowing the mkldnn-patch one. `--no-ipv4-shim` is now a
  passthrough flag on `ingest_quarter.py` (Cloud Shell IPv4 is fine for Gemini).
  Model cache symlinked: `~/.paddlex/official_models/PP-DocLayout-L` ->
  `/tmp/paddle-scratch/paddlehome/.paddlex/...` (no /home space, no re-download).
  Full invocation that works:
    `PYTHONPATH=/tmp/paddle-scratch python3 findociq/pipeline/ingest_quarter.py --bank DBS --period 2026-Q1 --no-scrape --no-ipv4-shim`
- **DB now: 9 docs** (added `DBS_1Q26_trading_update`, verify PASS, ~$0.035). BQ re-synced.
- **Two things to fix before the full sweep (keep-all ingests noise):**
  1. **Transcripts mis-classified as `fs`** — `*_analyst_transcript` / `*_media_transcript`
     have no tables; they fail extraction and pollute the DB (had to delete them).
     The classifier should route transcripts to `other` (or the orchestrator should
     skip 0-table docs).
  2. **Pillar-3 extraction crashes at STEP 2** — `DBS_1Q26_P3...` failed
     `PASS2 rc=1`. The pass1_toc/PASS2 pillar3 path needs debugging before P3 docs
     ingest cleanly.
  Deleted the 3 failed docs across all doc_id tables (document/section; they had no
  table_t/cell_fact rows) and re-synced BQ.

## BLOCKER (RESOLVED — see UPDATE above) — PaddleOCR STEP 0 on this CPU
`paddlepaddle 3.3.1` crashes on PP-DocLayout-L via the oneDNN/PIR path
(`ConvertPirAttribute2RuntimeAttribute`). Fix = force `run_mode="paddle"` by
making `pp_option.is_mkldnn_available()` return False.
- **WORKS**: run paddle with `PYTHONPATH=/tmp/paddle-scratch` where a
  `sitecustomize.py` monkeypatches it. This is how all the 2022 `paddle_scans/`
  were generated (`batch_scan.py`).
- **DOESN'T stick**: baking `sitecustomize.py` into the venv's site-packages —
  the eager patch fails at interpreter startup (swallowed), and a deferred
  import-hook variant also didn't take (`builtins.__import__` unpatched by
  session end). Do not rely on it as-is.
- `run_doc` STEP 0 invokes `candidates.py` via `subprocess_env(...)` — unverified
  whether that env propagates PYTHONPATH.

## Recommended next steps (pick one for the paddle blocker)
1. **Pre-generate scans, then let STEP 0 skip paddle** (cleanest — STEP 0 skips
   when `data/derived/paddle_scans/<doc_id>/regions.csv` exists). For each target
   doc: `PYTHONPATH=/tmp/paddle-scratch HOME=/tmp/paddle-scratch/paddlehome
   .venv-paddle/bin/python3 pipeline/discover/section/candidates.py <pdf> <doc_id>
   --out findociq/data/derived/paddle_scans`. Then `ingest_quarter.py --no-scrape`.
   NB: doc_id = pdf stem with spaces→underscores; make the pdf filename canonical
   so it matches (and, for 2025, may match an already-committed scan tag).
2. OR make `run_doc.subprocess_env` pass `PYTHONPATH=/tmp/paddle-scratch` to the
   STEP 0 subprocess so the patch applies in-loop.
3. OR (last resort, tracked code) set `run_mode="paddle"` in `candidates.py`'s
   `create_model` — treat as a general CPU-compat option and record the pivot.

Then: run `ingest_quarter.py` per (bank, period) for 2025 Q1–Q4 + 2026-Q1,
`sync_bq.py` after, and the Cloud Run dashboard reflects it live.

## Gotchas
- Scraped filenames lose the bank prefix / have double spaces → doc_ids don't
  match committed 2025 scan tags. Normalize before ingest. `ingest_quarter.py`
  already passes `--doc-period` (quarter-end date) so period is correct regardless.
- `run_doc` redoes whole-DB steps (concepts/fact_metric/ratios/xlsx) PER doc →
  slow for batches. A "defer whole-DB steps to end" flag would speed the sweep.
- OCBC Pillar 3 is not crawlable (JS-rendered; static HTML tops out ≤2019). The
  classifier handles its date-naming; fetch modern P3 by direct URL if needed.
- Uncommitted on disk (not in repo): the 2022 `paddle_scans/`, `data/sources/`
  (downloaded PDFs are gitignored), and unrelated `ARCHITECTURE.md`/`GEMINI.md`.
