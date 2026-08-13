# FinDocIQ — bank financial-statement extraction pipeline

> **Continuing this project from a clone alone?** Everything you need is here —
> no Cloud Workstation, no GCS bucket, no GCP project required. Start below.

This repo is **self-contained**. No Cloud Workstation, no GCS bucket, no access
to GCP project `igc2026-team08-6311` is required to rebuild the database, verify
every number against the source filings, or run the dashboard.

That was not true of the previous repo, where the source PDFs and the database
lived only in GCS. If you are reading this after losing GCP access, everything
below still works.

## 60-second start

```bash
git clone https://github.com/qyunhan/FinDocIQ-v2.git
cd FinDocIQ-v2

# Rebuild the database from the committed artifacts. No API calls, $0.
# run_doc.py builds its own .venv on first use — no setup step.
python3 findociq/pipeline/run_doc.py --rebuild-db --only 4Q25,1Q26,2Q26
```

Expected: **10 docs · 488 sections · 342 tables · 5,771 rows · 21,581 cells,
verify PASS (10 verified)**, ~60s plus a one-off venv build.

**Use `--only 4Q25,1Q26,2Q26`.** That is the *maintained corpus* — the documents
the masterlist covers and the dashboard serves, and it reproduces the shipped
`compiled_v2.db` exactly. A bare `--rebuild-db` instead loads **every** cached
document (25 docs / 33,671 cells), pulling in 1Q22–3Q25 filings that have no
masterlist coverage; useful for archaeology, wrong as a serving DB.

That single command reconstructs `findociq/db/compiled_fs.db` from
`findociq/outputs/**/parsed.json` (507 extraction artifacts) and
`findociq/data/derived/toc/*.json` (52 cached tables-of-contents). Both are
committed. Then:

```bash
# Serving DB the Streamlit app reads
python3 findociq/pipeline/run_doc.py --stage3

streamlit run findociq/app/findociq_app.py
```

## What works with no GCP at all

| capability | needs | works from a clone |
|---|---|---|
| rebuild `compiled_fs.db` from artifacts | nothing | **yes** |
| verify every cell against the PDF | the committed PDFs | **yes** |
| build `compiled_v2.db` (`--stage3`) | nothing | **yes** |
| Streamlit dashboard | `compiled_v2.db` | **yes** |
| the 33 pipeline tests | nothing | **yes** |
| **extract a NEW document** | a Gemini key (below) | **yes, with a key** |
| BigQuery sync (`STEP 7`) | GCP | no — pass `--no-sync-bq` |

`STEP 7` is the only step that needs GCP, it is not on the critical path, and a
failure there prints a warning and continues.

## Extracting a NEW document without GCP

Gemini auth defaults to **Vertex AI**, which binds every call to one GCP
project. Set an AI Studio key instead and no GCP is involved:

```bash
export GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
python3 findociq/pipeline/run_doc.py --pdf <file.pdf> --no-sync-bq
```

`gemini_client.build_client()` prefers the key when present and falls back to
Vertex otherwise, so existing GCP setups are unaffected. `FINDOCIQ_GCP_PROJECT`
and `FINDOCIQ_GCP_LOCATION` override the Vertex target if you keep that path.

## The three stages

`run_doc.py` runs stages 1+2 by default; each can run alone.

```
--stage1  EXTRACT  PDF        -> outputs/fs/<bank>_<period>/   (Gemini)
--stage2  LOAD     artifacts  -> compiled_fs.db                ($0, no API)
--stage3  SERVE    compiled_fs.db -> compiled_v2.db            ($0, no API)
```

Reloading after a loader change costs ~12s and no API spend — extraction
artifacts are cached, so `--stage2` alone replays them.

## What is deliberately NOT in this repo

- **`.venv/`** — rebuilt automatically on first run.
- **`findociq/db/compiled_fs.db`** — regenerate with `--rebuild-db` in ~20s.
  Keeping it out is what stopped the old repo growing to 234 MB (72% of that
  history was this one file committed once per ingest).
- **`findociq/db/compiled_v2.db`** is committed (10 MB) because the app reads it.

## Read next

1. `findociq/PROGRESS.md` — newest-first running log; start at the top.
2. `findociq/docs/DECISIONS.md` — why things are the way they are, and what was
   tried and rejected **with the evidence**.
3. `findociq/docs/Techreport/FinDocIQ_technical_report_v0.1.md` — the full
   architecture writeup.
4. `CLAUDE.md` — working agreements if you use Claude Code on this repo.

`findociq/docs/workstation-persistence.md` and the `*-handoff.md` files describe
the OLD Cloud Workstation + GCS setup. They are kept for history and no longer
describe how to work on this project.
