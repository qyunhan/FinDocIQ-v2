# FinDocIQ — bank financial-statement extraction pipeline

Turns DBS / OCBC / UOB disclosure PDFs into a verified, queryable database, and
serves it as a dashboard where **every figure traces back to the page it came
from**.

**Live dashboard:** https://findociq-dashboard-bmffbmpoyhah9a3uhmyepk.streamlit.app/

Covers 3 banks · 10 filings (FY2025 → 1H2026) · 342 tables · 21,581 figures.

## No cloud account required

This repo is self-contained. **GCP was retired in August 2026** — there is no
Cloud Workstation, no GCS bucket, no BigQuery dataset and no Cloud Run service in
the working path. Everything below runs from a clone, offline, at no cost.

The one exception is extracting a **brand-new** PDF, which calls Gemini and needs
an API key (see below). Rebuilding, verifying and serving the existing corpus
need nothing.

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
the masterlist covers and the dashboard serves. A bare `--rebuild-db` loads
**every** cached document (25 docs / 33,671 cells), pulling in 1Q22–3Q25 filings
that have no masterlist coverage: useful for archaeology, wrong as a serving DB.

> ⚠️ **A rebuild OVERWRITES `findociq/db/compiled_fs.db`.** Use
> `--db <tmp-path>` to send it somewhere else while you are experimenting.
>
> The rebuild is deterministic and, as of 2026-08-14, produces the *correct*
> database — it is what exposed and fixed a double count that had six cells of
> the live dashboard serving exactly twice the filed figure (Appendix E of the
> technical report). It does **not** reproduce the historical artifact
> byte-for-byte, and that is the point: compare on IDENTITY
> (`(doc, table, row) -> canonical_leaf_id`), never on row counts, which match
> even when the numbers are wrong.
>
> `canonical_col_id` is NOT written at load time. After any rebuild, run
> `python3 findociq/pipeline/stage3_stamp/apply/restamp_columns.py --db <db>
> --write` before `--stage3`, or the serving DB ships with 0 of 210 column
> stamps.

It reconstructs `compiled_fs.db` from `findociq/outputs/**/parsed.json` (507
extraction artifacts) and `findociq/data/derived/toc/*.json` (52 cached
tables-of-contents), all committed. Then:

```bash
# Serving DB the Streamlit app reads
python3 findociq/pipeline/run_doc.py --stage3

streamlit run findociq/app/findociq_app.py
```

## The three stages

`run_doc.py` runs stages 1+2 by default; each can run alone.

```
--stage1  EXTRACT  PDF            -> outputs/fs/<bank>_<period>/   (Gemini)
--stage2  LOAD     artifacts      -> compiled_fs.db                ($0, no API)
--stage3  SERVE    compiled_fs.db -> compiled_v2.db                ($0, no API)
```

Reloading after a loader change costs ~12s and no API spend — extraction
artifacts are cached, so `--stage2` alone replays them.

## Extracting a NEW document

Only stage 1 needs credentials. Use an AI Studio key:

```bash
export GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
python3 findociq/pipeline/run_doc.py --pdf <file.pdf>
```

The key path involves no GCP project. `gemini_client.build_client()` still has a
Vertex AI fallback for anyone who wants it, but it is not used or maintained.

## How the dashboard is published

**Two repositories, and only one of them publishes.**

| repo | role |
|---|---|
| `qyunhan/FinDocIQ-v2` (this one) | source of truth |
| `qyunhan/Findociq-Dashboard` | what Streamlit Community Cloud actually builds |

Pushing here does **not** update the live site. The deploy repo is *generated* —
its `sync.sh` flattens the layout, trims the app to the two views that work
without credentials (Dashboard, Database), and copies in the database, the
dashboard anchor CSVs and the source PDFs:

```bash
git push origin main                                  # source of truth
cd <findociq-dashboard clone> && ./sync.sh && git push # publishes
```

`sync.sh` prints the source branch and revision before copying, and refuses to
run against a dirty tree or a branch that is not on `origin/main` — it publishes,
so it will not guess.

## What is and is not committed

- **`findociq/db/compiled_v2.db`** (10 MB) — committed; the app reads it.
- **`findociq/db/compiled_fs.db`** (31 MB) — committed, so the serving DB can be
  rebuilt without re-running extraction.
- **Source PDFs** under `findociq/data/sources/` — committed, which is what lets
  the Database view show the original page beside each table with no cloud
  storage behind it.
- **`.venv/`** — not committed; rebuilt automatically on first run.

Dependencies are **pinned exactly** for the render path
(`streamlit==1.60.0`, `pandas==3.0.5`, `altair==6.2.2`, `pypdfium2==5.13.0`).
Floating versions have twice silently changed what the deployed page renders.

## Read next

1. `Techreport/FinDocIQ_technical_report_v0.1.md` — the full architecture
   writeup. **Appendix C** is what is covered today; **Appendix D** is the
   prioritised worklist of what to build next.
2. `findociq/PROGRESS.md` — newest-first running log; start at the top.
3. `findociq/docs/DECISIONS.md` — why things are the way they are, and what was
   tried and rejected **with the evidence**.
4. `findociq/docs/README.md` — index of the remaining docs, plus the redirect for
   historical reports now under `findociq/archive/`.
5. `CLAUDE.md` — working agreements if you use Claude Code on this repo.

## Historical documents

`HANDOFF.md`, `findociq/docs/workstation-persistence.md`,
`findociq/docs/workstation-setup.md` and the `*-handoff.md` files describe the
retired Cloud Workstation + GCS + BigQuery setup. They are kept as a record and
**do not describe how to work on this project today**.
