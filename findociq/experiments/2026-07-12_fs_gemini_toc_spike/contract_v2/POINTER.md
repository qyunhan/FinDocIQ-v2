# contract_v2 — MOVED to the pipeline (2026-07-14)

This directory is the **frozen research record** for the FS Gemini-TOC spike.
The working pipeline code that grew out of it now lives under `findociq/pipeline/`
and `findociq/data/`. Do not edit the copies here — edit the promoted homes.

| what was here (contract_v2/…) | now lives at |
|---|---|
| `toc_stage.py` | `findociq/pipeline/toc/toc_stage.py` (generalized: `--pdf`) |
| `toc_to_db.py` | `findociq/pipeline/toc/toc_to_db.py` (generalized: `--toc --db --doc-period`) |
| `prompt_v3.txt` | `findociq/pipeline/prompts/fs_toc_headings.txt` |
| `db_to_xlsx_check.py` | `findociq/pipeline/db_check_xlsx.py` |
| `batch_scan.py` | `findociq/pipeline/discover/section/batch_scan.py` |
| `paddle_out/<tag>/{candidates,regions,stitch_verdicts}.csv` | `findociq/data/derived/paddle_scans/<tag>/` |
| `outputs/DBS_2Q25_toc_v3.json` | `findociq/data/derived/toc/DBS_2Q25_performance_summary_toc.json` |
| `outputs/DBS_2Q25_toc_v3_raw*.json` | `findociq/data/derived/toc/DBS_2Q25_performance_summary_toc_raw*.json` |
| `outputs/fs_eval_v7_loaded.db` (copied) | `findociq/db/fs_v7.db` — THE FS database |
| `outputs/fs_check.xlsx` (copied) | `findociq/outputs/checks/fs_v7.xlsx` |

The `paddle_out/<tag>/pages/` render dirs were dropped (regenerable, gitignored).
`fs_eval_v7.db` and `fs_eval_v7_loaded.db` remain here as the loader-test fixtures
(`findociq/pipeline/pass2/test_load_v7.py` copies from them in place).

See `findociq/PIPELINE.md` for the end-to-end step map.
