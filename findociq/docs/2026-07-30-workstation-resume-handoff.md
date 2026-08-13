# RESUME HANDOFF — continue FinDocIQ on the Cloud Workstation

You (a fresh Claude Code session running INSIDE the workstation) are picking up
work that was done from a Mac session. Everything is committed + pushed to
branch `v2-concept-toolkit`. **First: `git pull` (HEAD should be ≥ `1d901b6`).**
Then read this, then the linked docs.

## Read these to get full context (all in-repo)
- `findociq/docs/2026-07-30-findociq-app-plan.md` — the 4-view app plan.
- `findociq/docs/2026-07-29-tag-workbook-design.md` — the concept-tagging round-trip design (two Cloud flows, identity tuple, resolution rules).
- `findociq/docs/workstation-setup.md` — env setup (you're doing this now).
- `findociq/PROGRESS.md` (top entry) + `findociq/docs/2026-07-24-ingest-handoff.md`.
- `CLAUDE.md` (orchestration + "humans out of the loop" principles) and the memory dir (esp. `gcp-iam-constraints`).

## What's already built (this branch)
- **FinDocIQ app** `findociq/app/findociq_app.py` — website-style Streamlit, cobalt `#0047AB`, big type, NO emojis. 4 views: **Ingest** (pick doc → trigger `run_doc` → live `ingest_status` stepper → Excel/CSV download), **Database** (drill-down browser), **Table Registry** (per bank/doc/table identity attributes + coverage), **Dashboard** (cross-bank concept compare). 11 tests pass. `.streamlit/config.toml` has the theme.
- **`findociq/pipeline/tag_workbook.py`** — generates the finance tagging workbook (income-statement/FY25 slice; 3 banks stacked; dropdowns; dictionary; coverage). Reference artifact the user hand-built: their `FinDocIQ_Tag_Highlights_FY25.xlsx`.
- **`findociq/pipeline/source_store.py`** — GCS is source of truth for raw PDFs; `run_doc --pdf <gcs-key>` materializes on demand. (Migration Tasks 1–3 done; Task 4 rekey migration written, review-pending — see the SDD plan.)
- **pass2 FS extraction fixes** — family-aware output paths, `--out-root` fix, conditional sheet suffix, column-band validate+repair (already wired in `extract.py`), section-region validators.

> **STALE (2026-08-13).** This document describes the Cloud Workstation + GCS
> setup. The repo is now self-contained and needs none of it — see `README.md`.
> Kept for history. In particular the env setup below is obsolete: `run_doc.py`
> bootstraps `.venv` and (on demand) `.venv-paddle` itself, and there is no
> `$HOME/paddle-fix` any more.

## Immediate next step
1. ~~Finish env setup: `findociq/docs/workstation-setup.md` steps 3–4 (venv + both requirements; paddle mkldnn fix + `.venv-paddle` symlink).~~ **No longer needed** — `run_doc.py` builds both environments itself. ADC works natively here (default compute SA = editor) — no keys; a `GEMINI_API_KEY` also works and needs no GCP.
2. **Run ONE FS doc end-to-end through the app's Ingest view** (step 6b) and watch the live progress. Or CLI: `python3 findociq/pipeline/run_doc.py --pdf findociq/data/sources/financial_statements/DBS_4Q25_performance_summary.pdf` (no `PYTHONPATH=$HOME/paddle-fix` — `paddle_env()` sets that up per-child now).

## Roadmap after that
- **Extraction quality** ("all fields" guarantee): full-year `period_span` (Year 2025 → FY; currently NULL), the `parent→row→col→table→section` key-resolution precedence, header-row noise filter, re-extract the 3 `nt=0` FS docs. Add a "defer whole-DB steps to end" flag to `run_doc` before batching all 18 FS docs.
- **Table → registry mapping** (~20 families × 3 banks), then generalize `tag_workbook.py` beyond the income-statement slice.
- **Two Cloud Run flows**: Flow 1 (doc → tagging Excel), human tags, Flow 2 (tagged Excel → existing concept→fact→BQ tail). Reuse original logic (`infer_period`, nature flow/stock, `load_v7`, `build_fact_metric`, `sync_bq`) — new code is thin adapters.

## Key environment facts
- User = `roles/editor`; ADC works inside the workstation; `git`-tracked DB `findociq/db/compiled_fs.db` (pull brings it). Org policy blocks public `allUsers` hosting; a dedicated minimal SA / Streamlit trigger is deferred pending `projectIamAdmin` (see `docs/2026-07-29-dashboard-trigger-pending-access.md`).
- Delegation model (from memory): Opus to think, Sonnet to execute — orchestrate, don't do everything inline.
