# FinDocIQ Technical Report

**Handover Edition · Version 0.5 · 2026-08-12**

> **Version 0.5** adds §2.0 (how to run the pipeline: every flag, what it costs,
> and whether a re-run clears the database or replaces one document) and
> Chapter 6 (the design decisions stage by stage, and what is still open). It
> also rewrites Chapter 1, whose figures were badly out of date — the hand-written
> lists were shown as 47 / 114 / 43 lines when they are 448 / 525 / 460 — and
> refreshes Appendix A from 9 documents and 616 matched lines to 10 and 4,064.
> Shorthand has been written out in full throughout.
>
> Numbers are measured on the day of writing, not remembered. Version 0.5 figures
> were checked against `db/compiled_fs.db`, `db/compiled_v2.db` and the working
> tree on 2026-08-12; earlier chapters were checked on 2026-08-07 and are marked
> where they have since moved.

For the person taking over from Yunhan. Assumes Python, SQL, and PDF familiarity;
assumes no prior context on this project.

Read top-to-bottom. Pipeline overview → design → stages with I/O → data model →
masterlist and config → runbook → open questions. Everything else is reference.

Where a source file's own comments contradict its code, that is called out
inline — trust the code.

---

## Pipeline in one page

Bank quarterly PDF → cross-bank dashboard cell. 

**1. Extract.** Gemini reads the PDF section by section and produces structured
JSON — every printed row, faithfully, no interpretation. A section spanning more
than 2 pages is split into contiguous ≤2-page chunks. PaddleOCR does layout
first. `pdfplumber` provides a second check on the indentation

**2. Load.** JSON goes into a relational DB (`schema_v7`). `row_dim` per printed
row, `col_dim` per column, `cell_fact` per value, `table_t` per table.

**3. Map.** For each printed row, match its full path against that bank's
masterlist. Four attempts: persisted alias → verbatim → normalised (casefold,
strip footnote markers, strip unit suffixes) → caption-stripped. Hit → stamp
`canonical_leaf_id`. Miss → NULL. **Never invent.**

**4. Serve.** `build_compiled_v2` denormalises write-side → read-side DB.
Streamlit reads `compiled_v2.db` through two config CSVs — `anchors` maps
concepts to `(bank, table_type_id, canonical_leaf_id)`; `formulaanchors`
handles rollups.

**The seam.** Extraction never guesses meaning. Mapping never invents ids.
Two separate concerns, one deliberate boundary.

```
     Raw PDF          Faithful transcription       Canonical mapping
                      (extract + load)             (map)

  ┌──────────┐      ┌─────────────────────┐      ┌──────────────────┐
  │          │      │                     │      │                  │
  │   PDF    │ ───► │  What the document  │ ───► │  What it means   │
  │          │      │  says, structured   │      │  in a shared     │
  │          │      │  as JSON + rows     │      │  vocabulary      │
  └──────────┘      └─────────────────────┘      └──────────────────┘

                      preserves footnotes,          matches to
                      unit suffixes, casing,        canonical_leaf_id
                      row hierarchy                 via per-bank
                                                    masterlist
```

**The rule that makes it safe.** NULL beats a guess. A blank on the dashboard
is a coverage gap, not a bug. Fix in the CSV, not the code.

**New quarter.** `run_doc.py --pdf <new.pdf>` runs end to end into
`compiled_fs.db`; `stage3_stamp/serve/build_compiled_v2.py` then produces the DB the dashboard
actually reads (§2.0). The masterlist you authored once applies automatically —
stamping is re-derived at every load. Anything genuinely new stays NULL until you
author a row and re-load (seconds). No language model in the matching step.

---

## Chapter 1 — What we are working with

Every quarter DBS, UOB and OCBC publish their results as PDFs written for people
to read, not for software. We want to line up the same twenty-odd figures across
all three banks and across the last few years.

Three things make that harder than it sounds, and the whole design is a response
to them:

- **The same bank changes its own layout.** DBS printed its Overview as one
  table in one quarter and three tables in another. Anything that remembers
  "the figure is in the second table" quietly reads the wrong number next time.
- **A language model on its own is confidently wrong.** It will average two
  columns, skip a row that has a footnote mark, or attach a number to the wrong
  period — and the result still looks tidy. Nothing raises an error.
- **"The same figure" is not the same wording.** DBS writes *Commercial book
  total income*; OCBC writes *Fees and commissions (net)*. There is no shared
  dictionary to look them up in. Anyone who invents one before writing down what
  each bank actually prints ends up treating one bank's habits as the standard.

So the work is split in two. First we copy down what the document says, exactly,
with no interpretation. Separately, we attach meaning by matching each printed
line against a list we wrote by hand for that bank. If a line is not on the list,
it stays empty — we never guess. That is what makes a gap visible instead of
silent.

**The hand-written lists, as they stand today:**

| Bank | Lines on the list | Table types | Documents covered |
|---|---|---|---|
| DBS | 448 | 35 | Performance summary |
| OCBC | 525 | 36 | Condensed financial statements, media release |
| UOB | 460 | 31 | Condensed financial statements, financial statements |

Alongside them sit the column lists (50 / 63 / 36 rows) that name each column of
a table, and `masterlist_leaf_aliases.yaml`, which records the handful of cases
where a bank printed the same line under a changed name — see §4.1.

## Chapter 2 — Pipeline stages

The full flow, with the code behind each step and what it writes. Everything up
to and including STEP 7 is `run_doc.py`; the last two are separate commands.

```
   PDF
    │
    ▼  route      stage1_extract/route/family.py
  decide the family: fs | pillar3 | slides | other      (in memory only)
    │             a deck classifies as "slides" and is skipped on purpose
    ▼
  STEP 0  layout          stage1_extract/toc/candidates.py   (PaddleOCR)
    │     ────► data/derived/paddle_scans/<doc_id>/{regions,candidates}.csv
    ▼
  STEP 1  contents page   pillar3 -> toc/pass1_toc.py + pass1_to_v7.py  (rule-based)
    │                     fs      -> toc/toc_stage.py                   (Gemini)
    │                     then    -> toc/toc_to_db.py
    │     ────► data/derived/toc/<doc_id>_toc{,_raw}.json
    │           + writes the document and section rows
    ▼
  STEP 2  extraction      stage1_extract/chunk/PASS2_v2.py
    │                       -> chunk/extract.py, or chunk/batch.py
    │     ────► outputs/<family>/<run_dir>/audit/<doc_id>/<unit>/parsed.json
    │           + the spreadsheet, via excel/workbook.py
    ▼
  STEP 2b geometry        stage1_extract/chunk/geometry.py  (imported, not a subprocess)
    │     ────► indentation written back into parsed.json
    ▼
  STEP 3  load            stage2_load/load_v7.py            (imported)
    │     ────► table_t, row_dim, col_dim, cell_fact
    │           + the period cascade, and the identity stamping below
    │
    ├── column roles  ───► col_dim.col_role
    ├── canonical leaf ──► row_dim.canonical_leaf_id, table_t.table_type_id
    └── canonical column ► col_dim.canonical_col_id
    ▼
  STEP 3b registry        stage3_stamp/resolve/seed_registry.py
  STEP 5  verify          common/verify_cells.py
  STEP 6  check workbook  common/db_check_xlsx.py  ────► outputs/checks/compiled_fs.xlsx
  STEP 7  sync            common/sync_bq.py
    ▼
  db/compiled_fs.db        <- run_doc.py STOPS HERE
    │
    ▼  separate command    stage3_stamp/serve/build_compiled_v2.py
  db/compiled_v2.db        the only database the dashboard opens
    │
    ▼  separate command    app/findociq_app.py
  Streamlit                reads compiled_v2.db + data/derived/dashboards/*.csv
```

STEP 4a/4b/4c (the concept layer) are absent from that list on purpose: they are
off unless `--with-concepts` is passed, and `compiled_v2.db` drops what they
produce. A normal run prints `STEP 4a — concepts [SKIPPED]`.

Each stage below: reads → writes → what it does → what to check → common failures.

### 2.0 Running it — `run_doc.py`, its flags, and what each one costs

`run_doc.py` is the orchestrator. One document, end to end:

```bash
python3 findociq/pipeline/run_doc.py \
    --pdf findociq/data/sources/financial_statements/DBS_2Q26_performance_summary.pdf
```

It runs STEP 0 → 7 and stops at `compiled_fs.db`. **It does not build
`compiled_v2.db`** — that is a separate command (§2.9), and the Streamlit app
reads only `compiled_v2.db`. A run that "succeeded" therefore changes nothing on
the dashboard until you rebuild v2.

#### Does a re-run CLEAR the database or just re-parse?

**Neither wholesale — it REPLACES that one document.** `load_v7._delete_doc`
(`stage2_load/load_v7.py:1212`, called at `2037`) runs before inserting:

```sql
DELETE FROM cell_fact WHERE doc_id = ?
DELETE FROM row_dim   WHERE doc_id = ?
DELETE FROM col_dim   WHERE doc_id = ?
DELETE FROM table_t   WHERE doc_id = ?
```

Scoped to `doc_id`. Every other document is untouched, and there is no append
path — re-loading the same document twice cannot double it. `document` and
`section` are NOT deleted; they are load INPUTS, written by STEP 1 and upserted.

The one command that does clear everything is `--rebuild-db`, which rebuilds the
whole DB from `schema_v7.sql` plus every cached contents page with a matching audit dir,
ignoring `--pdf`. Narrow it with `--only`:

```bash
run_doc.py --rebuild-db --only 4Q25,1Q26,2Q26     # fresh DB, maintained corpus only
```

**Stamping is re-derived on every load.** `canonical_leaf_id`,
`table_type_id` and `canonical_col_id` are written at STEP 3 from the CURRENT
masterlists, so a masterlist edit reaches the DB by re-loading the affected
document — no separate stamping pass required.

#### `--force`: what it actually forces

`--force` acts on EXTRACTION, not on the database. STEP 2 caches per unit: if
`audit/<unit>/parsed.json` exists, `load_cached_unit()` replays it and makes **no
model call** (`stage1_extract/chunk/PASS2_v2.py:293-295`). There is a second, coarser skip: if the
workbook already has every tab for a group, the whole group is skipped
(`stage1_extract/chunk/PASS2_v2.py:388`, prints `⏭️ group N already present`). `--force` disables both
and clears the existing section tabs before rebuilding (`stage1_extract/chunk/PASS2_v2.py:395`), so it
is a replacement rather than a merge.

| | plain re-run | `--force` |
| --- | --- | --- |
| STEP 1 contents page | **re-runs — no skip-if-exists.** `fs` → a Gemini call; `pillar3` → deterministic, no model calls | same |
| STEP 2 units with cached `parsed.json` | replayed, **no model call** | re-extracted, **paid** |
| `<doc>_fs.xlsx`, `.index.json`, `logs/` | written | written |
| `prompt.txt`, `response.txt`, `pages.pdf` | **not** written — a cache hit makes no call, so there is no prompt or response to record | written |
| dropped-page rescue (§2.4) | cannot fire — nothing is re-asked | fires |
| `compiled_fs.db` | that doc replaced, re-stamped | same |

So a plain re-run is the cheap way to regenerate a missing workbook from an audit
trail that already exists — it costs one contents-page call, not a full extraction. Use
`--force` when you need the prompt/response evidence or want the rescue to run.

#### Every flag

| Flag | Effect |
| --- | --- |
| `--pdf <key>` | the document to run (GCS key or repo-relative path) |
| `--all` | every source in the bucket |
| `--force` | ignore the extraction cache; clear and rebuild section tabs |
| `--batch` | Gemini Batch API — async, 50% cost, identical prompts |
| `--rebuild-db` | rebuild the WHOLE DB from schema + cached contents pages; ignores `--pdf` |
| `--only <subs>` | with `--rebuild-db`, keep only matching doc_ids |
| `--db <path>` | target DB (default `db/compiled_fs.db`) |
| `--with-concepts` | run STEP 4a/4b/4c. **Off by default** — the concept layer is retired and `compiled_v2.db` drops it |
| `--defer-db-steps` | skip whole-DB steps during a sweep; run `--db-steps-only` after |
| `--db-steps-only` | run only the whole-DB steps |
| `--verify-only` | STEP 5 only |
| `--dry-run` | plan without executing |
| `--doc-period <ISO>` | override the detected period |
| `--bank <KEY>` | override bank detection |
| `--no-llm` | suppress language-model calls in the concept step |
| `--no-sync-bq` | skip STEP 7 |
| `--ipv4-shim` / `--no-ipv4-shim` | force/disable the IPv4 shim for Gemini auth |

#### Where the human-auditable output lands

Everything below is meant to be opened and read. `<run_dir>` is
`outputs/<family>/<bank>_<period>/`, for example `outputs/fs/dbs_2Q26/`.

| Artifact | Path | What it is for |
| --- | --- | --- |
| Extraction workbook | `<run_dir>/<doc>_fs.xlsx` | **the main human view** — one sheet per table, as extracted |
| Section index | `<run_dir>/<doc>_fs.index.json` | which sections produced which sheets |
| Model prompt | `<run_dir>/audit/<doc>/<unit>/prompt.txt` | exactly what was asked |
| Model response | `<run_dir>/audit/<doc>/<unit>/response.txt` | exactly what came back, before parsing |
| Pages sent | `<run_dir>/audit/<doc>/<unit>/pages.pdf` | the page crop the model saw |
| Parsed result | `<run_dir>/audit/<doc>/<unit>/parsed.json` | the structured tables — **git-tracked** |
| Unit metadata | `<run_dir>/audit/<doc>/<unit>/meta.json` | pages, usage — **git-tracked** |
| Chunk sub-calls | `<run_dir>/audit/<doc>/<unit>/chunks/c<N>/` | same five files, per chunk, for spanning units |
| Cost + API log | `<run_dir>/logs/cost_summary.json`, `logs/api_log.xlsx` | tokens and spend for the run |
| Cross-run ledger | `outputs/<family>/_ledgers/<bank>_api_usage.jsonl` | append-only usage across all runs |
| Whole-DB check | `outputs/checks/compiled_fs.xlsx` | STEP 6 — every table in the DB, one workbook |
| Contents page | `data/derived/toc/<doc_id>_toc.json` | sections found, before extraction |
| OCR regions | `data/derived/paddle_scans/<doc_id>/{regions,candidates}.csv` | STEP 0 layout evidence |

**Only `parsed.json` and `meta.json` are git-tracked** (the `.gitignore`
whitelist). A document cloned from git therefore arrives with its audit trail and
nothing else — no workbook, no prompt, no response, no `logs/`. That is not a
failed run; it is the whitelist working. Confirm which case you have by comparing
tracked against on-disk counts:

```bash
git ls-files findociq/outputs/fs/<run_dir> | wc -l    # for example uob_2Q26 → 74
find findociq/outputs/fs/<run_dir> -type f | wc -l    #      uob_2Q26 → 74  (git-only)
                                                      #      dbs_2Q26 → 142 (ran here)
```

Equal counts mean the document was ingested on another machine. Re-run
`run_doc.py` on it to materialise the workbook locally — free of extraction cost,
because the cached `parsed.json` is exactly what the replay needs.

#### Getting a change onto the dashboard

`run_doc.py` alone is not enough. The full path from a source PDF to a rendered
dashboard is:

```bash
# 1. ingest / re-ingest one document        → db/compiled_fs.db
run_doc.py --pdf <key>

# 2. reduce to the serving DB               → db/compiled_v2.db  (9 tables, ~10 MB)
python3 findociq/pipeline/stage3_stamp/serve/build_compiled_v2.py \
    --src findociq/db/compiled_fs.db --dst findociq/db/compiled_v2.db
# 3. (only if the COLUMN masterlist changed without a re-load)
python3 findociq/pipeline/stage3_stamp/apply/restamp_columns.py --db findociq/db/compiled_v2.db --write

# 4. publish                                 → the Streamlit mirror
cd /home/user/findociq-dashboard && ./sync.sh /home/user/FinDocIQ
git add -A && git commit -m "sync: ..." && git push
```

Step 3 exists because `col_dim.canonical_col_id` is written at LOAD time, so a
column-masterlist edit is invisible to a database already on disk. `restamp_columns.py`
applies that one stage to a built DB, driving the same `resolve_canonical_col`.

### 2.1 Route (`stage1_extract/route/family.py`)

- **Reads:** PDF (page 1).
- **Writes:** nothing to disk; sets `institution` + `family` in memory.

Classifies bank + doc family from page 1 vocabulary, never from filename.

**Check.** Console prints classification at start of run. Wrong → everything
downstream is wrong. Diagnose by checking page 1 vocabulary against
`stage1_extract/route/family.py`.

### 2.2 STEP 0 — Layout detection (`stage1_extract/toc/candidates.py`)

- **Reads:** PDF.
- **Writes:**
  - `data/derived/paddle_scans/<doc_id>/regions.csv`
  - `data/derived/paddle_scans/<doc_id>/candidates.csv`

PaddleOCR's `PP-DocLayout-L` detects text and table regions.

**The environment builds itself — there is no setup step.** STEP 0 is the only
stage that needs the ~1 GB PaddleOCR stack, and most runs never touch it: a
document whose `regions.csv` already exists is skipped outright, and 226 scan
artifacts are committed, so the entire current corpus reloads without paddle
ever being installed. `ensure_paddle_venv()` (`run_doc.py:450`) therefore builds
`.venv-paddle` **on demand** — never at import, never for a cached document — by
shelling out to `tools/setup_paddle_venv.sh`.

Two environment details are load-bearing, and both are handled by that script
plus `paddle_env()` (`run_doc.py:466`) rather than by the operator:

- **mkldnn must be disabled.** paddlepaddle 3.3.1 segfaults on CPU through the
  oneDNN/PIR path. The fix is `is_mkldnn_available = lambda: False`, and it only
  sticks when injected through a `sitecustomize.py` on `PYTHONPATH` — patching
  site-packages does *not* survive. The setup script writes that file into
  `/tmp/paddle-scratch`, and `paddle_env()` points `PYTHONPATH` at it.
- **The IPv4 shim is deliberately NOT applied to this child.** Python loads only
  the FIRST `sitecustomize.py` on `PYTHONPATH` and paddle's must win, so STEP 0
  is the one subprocess that runs without the shim.

`HOME` is redirected to `/tmp/paddle-scratch/paddlehome` as well, so the
downloaded `PP-DocLayout-L` weights land off the ~5 GB `/home` quota. Nothing
lives in the operator's home directory: an earlier setup told you to create
`$HOME/paddle-fix` by hand, which meant the fix died with the machine.

**Check.** Region count roughly "one per body paragraph + one per table."

**Failure.** `ModuleNotFoundError: paddleocr` → the on-demand build did not run,
or failed. Run it directly: `bash tools/setup_paddle_venv.sh`. Set
`FINDOCIQ_NO_PADDLE_BOOTSTRAP=1` to forbid the build entirely, so STEP 0 fails
loudly instead of installing 1 GB unasked. The first real scan needs internet
for the model download — but no GCP.

### 2.3 STEP 1 — Reading the contents page (`stage1_extract/toc/toc_stage.py` → `stage1_extract/toc/toc_to_db.py`)

- **Reads:** PDF, `candidates.csv`.
- **Writes:**
  - `data/derived/toc/<doc_id>_toc_raw.json` (cached — delete to force regen)
  - `data/derived/toc/<doc_id>_toc.json` (finalised)
  - `document` + `section` DB rows (only writer)

Uses Gemini to extract table of contents with page ranges.
Prompt: `prompts/fs_toc_headings.txt`.

**Check.** Section count is in the low 40s for DBS quarterlies (2Q26: 43).
>20% off from the previous vintage = red flag.

**Failure.** Gemini merging sections with shared prefix. Diagnose against
printed contents page.

### 2.4 STEP 2 — Per-section extraction (`stage1_extract/chunk/PASS2_v2.py` → `stage1_extract/chunk/extract.py`)

- **Reads:** PDF, `toc.json`.
- **Writes:** `outputs/fs/<run_dir>/audit/<doc_id>/<unit>/parsed.json` — one per audit unit.

For each section listed in the contents page, sends the section's pages to Gemini for JSON with `GTable`,
`GRow`, `GColumn`, `GCell`, `GTitle` fields. Prompt: `prompts/stage2_core.txt`.

**Chunking.** This is *not* a fixed window sliding over the PDF. A **non-spanning**
unit goes to Gemini in one call regardless of page count (`stage1_extract/chunk/extract.py:682-685`).
A **spanning** unit longer than `--chunk-pages` (default **2**; `0` disables) is
split into contiguous, non-overlapping ≤2-page chunks, each its own independent
call against unit_id `{uid}/chunks/c{n}`. On a `MAX_TOKENS` response a multi-page
chunk halves at the midpoint and retries, down to single pages — below which it
errors rather than splitting further (`stage1_extract/chunk/extract.py:704-717`; mirrored for batch
mode in `batch.py:211-224`). `run_doc.py` never overrides the default, so 2 is
what every ingest actually runs.

**Dropped-page rescue (2026-08-12).** A chunk can return having answered for only
SOME of its pages. The table count cannot detect that — one table legitimately
spans a chunk. So after every multi-page chunk, `pages_with_no_output()` judges
each page: a page carrying ≥12 distinct numeric tokens whose tokens appear in NO
returned cell produced nothing, and is re-asked for ALONE
(`chunks/c{n}p{page}`) before merging. Wired into both the sync path
(`extract.py`, `extract_unit_chunked`) and the batch assembly (`stage1_extract/chunk/batch.py`), which
has its own merge loop and would otherwise keep the defect. Spec:
`docs/specs/2026-08-12-dropped-page-rescue.md`.

This is not hypothetical: DBS 2Q26 prints its overview twice — half-year basis on
pages 4-6, quarter basis on 7-8. The `[6,7]` chunk returned one table (page 6's
per-share block) and dropped page 7 entirely, so the whole quarter basis was
missing from the DB (632 cells span `1H` against 10 spanning `2Q`) with no error
anywhere. The guard fires at extraction time only — a cached re-run cannot catch
it; use `--force`.

**Caching.** A unit whose `parsed.json` already exists is replayed by
`load_cached_unit()` with NO model call, and a group whose workbook tabs all exist
is skipped entirely. `--force` disables both and clears the tabs first. See §2.0
for the cost table.

**Check.** Unit count against section count. Open the Excel companion
(`outputs/fs/<run_dir>/<doc_id>_fs.xlsx`) and eyeball 2–3 tables for column alignment.

**Failure.** Column shift, duplicate row labels, missing numbers. DBS 2Q26 had
17 col-shift, 9 dup labels, 18 missing numbers *outside* Overview — known
blocker for dashboards beyond Highlights (see §5.3 D8).

### 2.5 STEP 2b — Geometry sidecar (`stage1_extract/chunk/geometry.py`)

- **Reads:** PDF text layer (via pdfplumber), `parsed.json`.
- **Writes:** sidecar keys inside `parsed.json` — `line_id`, `indent`, `label_clean`, `hierarchy_source`.

Deterministic geometry, added to complement Gemini's spatial guesses. When they
disagree, geometry wins for hierarchy.

**Check.** Every table's `hierarchy_source` should be `'geometry'` (trustworthy)
or `'model'` (fallback, lower confidence).

### 2.6 STEP 3 — Load into schema_v7 (`stage2_load/load_v7.py`)

- **Reads:** all `parsed.json` files, `document` + `section` rows from DB.
- **Writes:**
  - `table_t` (one row per extracted table)
  - `row_dim` (one row per printed row)
  - `col_dim` (one row per column)
  - `cell_fact` (one row per value cell)

Doc-scoped and idempotent — re-loading REPLACES that document; other documents
untouched. `_delete_doc()` (`stage2_load/load_v7.py:1212`, called at `2037`) issues
`DELETE FROM {cell_fact,row_dim,col_dim,table_t} WHERE doc_id = ?` before
inserting, so there is no append path and a document cannot be doubled by running
it twice. `document` and `section` are NOT deleted — they are load INPUTS written
by STEP 1. The only command that clears the whole DB is `run_doc.py --rebuild-db`
(§2.0).

Stamping is re-derived here on every load, from the CURRENT masterlists — which
is why a masterlist edit reaches the DB by re-loading, with no separate pass.

The loader derives five things not in the JSON:

| Field | Derived from |
|---|---|
| `row_parent` | Printed-parent precedence + positional walk over geometry sidecar |
| `value_num` | Numeric parsing of `GCell.value` (strip commas, parens, unit suffixes) |
| `unit` | Cascade: col header → row prefix → table title → doc default |
| `col_period`, `period_span`, `period_start` | Column header parsing (`1H26`, `2Q25`, `FY24`) |
| `sums_to`, `sums_sign` | Arithmetic against printed subtotals |

**Check.** Row/cell counts against the Excel companion. 100 load warnings
normal; 500 not.

**Failure.** Unit inheritance from wrong level (fallback to `S$m` when title
states no unit; fixed for 2Q26 — see Appendix B lesson 2). `row_parent`
dangling: 0/4,881 rows across all 9 loaded documents currently — watch it.

### 2.7 STEP 3a — Column role stamping

- **Reads:** `col_dim`.
- **Writes:** `col_dim.col_role = 'derived_skip'` on `% chg` / delta columns.

Marks derived columns to keep them out of dashboard `cell_fact` scans. Runs on
every column of every table; no masterlist needed.

**Check.** `'derived_skip'` count ≈ number of `% chg` columns in the PDF. DBS
2Q26: 20 of 184 columns. 118 across all 9 loaded documents.

### 2.8 STEP 3b — Canonical leaf resolution (`mapping/resolve_canonical_leaf.py`)

- **Reads:** `data/derived/masterlist/*.csv`, `table_t` + `row_dim` from DB.
- **Writes:**
  - `row_dim.canonical_leaf_id` (NULL if no match)
  - `row_dim.table_type_id` — **this is the one the dashboard addresses by**
  - `table_t.table_type_id` (table-grain convenience copy; see warning below)
  - `canonical_leaf_path_alias` (records confirmed matches)

> ⚠️ **Address the leaf via `row_dim.table_type_id`, never `table_t`'s.** One
> printed exhibit can carry rows from several masterlist types, and `table_t`
> keeps only the *last* type to match — which previously stranded correctly
> stamped leaves under the losing type. The row-grain column is stamped by the
> same masterlist entry that resolved the leaf, so the two halves of the address
> can never disagree. See the comment on `_ANCHOR_SQL` in `findociq_app.py:901`.

Matching stages, tried in the order the code actually runs them
(`resolve_canonical_leaf.py:226-235`):

1. **Persisted alias** — the row's raw printed chain found in
   `canonical_leaf_path_alias`, confirmed by an earlier run
2. **Verbatim path** — exact match of `full_path`
3. **Normalised path** — casefold + strip footnote markers + strip unit suffixes
4. **Caption-stripped** — remove leading caption segments

Stages 2–4 *write* a new alias row on a hit, which is what stage 1 reads back on
every subsequent run. That feedback loop is why re-stamping is fast and stable.

No match → NULL. Never invents.

> ⚠️ **The module docstring is stale.** `resolve_canonical_leaf.py:37-46` lists
> only three stages with alias *last*, and names the table `canonical_leaf_alias`.
> The code checks alias *first*, and reads `canonical_leaf_path_alias` —
> `canonical_leaf_alias` is a different legacy table in `compiled_fs.db` keyed on
> label pairs (see the comment at `:275`). Trust the code, not the docstring.

```
  Each row in row_dim
        │
        ▼
  ┌───────────────────────────┐
  │ 1. Persisted alias        │─── HIT ──► stamp canonical_leaf_id
  │    (canonical_leaf_path_  │            (no new alias written)
  │     alias, prior runs)    │
  └───────────────────────────┘
        │ MISS
        ▼
  ┌───────────────────────────┐
  │ 2. Verbatim path match    │─── HIT ──► stamp + record alias
  └───────────────────────────┘
        │ MISS
        ▼
  ┌───────────────────────────┐
  │ 3. Normalised match       │─── HIT ──► stamp + record alias
  │    (casefold, strip       │            in canonical_leaf_path_alias
  │     footnotes and units)  │
  └───────────────────────────┘
        │ MISS
        ▼
  ┌───────────────────────────┐
  │ 4. Caption-stripped       │─── HIT ──► stamp + record alias
  └───────────────────────────┘
        │ MISS
        ▼
      NULL  (surfaces in unresolved queue on next dashboard render)
```

**Check.** Console prints "X leaves stamped / Y candidates." DBS 2Q26: 50/50 Overview.

**Failure.** Missing printed path → add alias row to masterlist. Wrong
`table_type_id` on a table → usually upstream (extractor merged/split incorrectly).

**Re-stamping is cheap.** Since 2Q26, `stamp_tables.py` writes `table_type_id` +
`canonical_leaf_id` together. Masterlist edit = seconds-long re-stamp, not full
re-load. Iterate freely.

### 2.9 Build compiled_v2 (`stage3_stamp/serve/build_compiled_v2.py`)

- **Reads:** `schema_v7` DB.
- **Writes:** `db/compiled_v2.db` (read-side).

Carries forward identity stamps + `period_source` + `table_title_clean`.

`compiled_v2.db` is **not** a single flat table. It is the same star schema with
the concept layer dropped by design — 9 tables: `document`, `section`, `table_t`,
`row_dim`, `col_dim`, `cell_fact`, `geo_dim`, `segment_dim`, `ingest_status`. It
carries **no views** (`compiled_fs.db` has `v_cell`, `v_cell_leaf`,
`v_cell_sumsafe`, `v_cell_flat`; the app uses none of them).

### 2.10 Streamlit (`app/findociq_app.py`)

- **Reads:** `db/compiled_v2.db`, `data/derived/dashboards/*.csv`.
- **Writes:** browser (no disk output).

**One query for the whole board, then filtered in Python** — not one query per
cell. `_ANCHOR_SQL` (`findociq_app.py:901`) pulls every addressable fact once:

```sql
SELECT r.table_type_id, r.canonical_leaf_id,
       COALESCE(t.table_title_clean, t.table_title) AS table_title,
       d.institution,
       c.col_period AS period, c.period_span, f.value_num, f.unit, d.doc_period
FROM cell_fact f
JOIN row_dim  r ON r.doc_id = f.doc_id AND r.table_id = f.table_id AND r.row_id = f.row_id
JOIN col_dim  c ON c.doc_id = f.doc_id AND c.table_id = f.table_id AND c.col_id = f.col_id
JOIN table_t  t ON t.doc_id = f.doc_id AND t.table_id = f.table_id
JOIN document d ON d.doc_id = f.doc_id
WHERE r.canonical_leaf_id IS NOT NULL
  AND r.table_type_id    IS NOT NULL
  AND (c.col_role IS NULL OR c.col_role <> 'derived_skip')
  AND c.col_period IS NOT NULL
  AND f.value_num  IS NOT NULL
```

Three things to internalise before writing any query of your own:

- **There are no surrogate keys.** No `row_key` / `col_key` / `table_key`. Every
  join is composite on `doc_id` + `table_id` (+ `row_id` / `col_id`). `USING`
  will not work.
- **There is no `bank` column.** Bank is `document.institution`, and it holds the
  full legal name (`DBS Group Holdings Ltd`), not `DBS`. The masterlist and
  dashboard CSVs use the short form — the app maps between them.
- **`table_type_id` comes from `row_dim`**, per the warning in §2.8.

**Vintage dedupe.** Overlapping filings both print the same period — DBS 4Q25 and
2Q25 each print 1H25. `dedupe_by_latest_document` (`findociq_app.py:923`) keeps
one value per `(institution, table_type_id, leaf, period, span)`, and **the most
recent document wins** on the grounds that it carries the filing's latest
restatement of that figure. Without it the winner is whichever row the query
happened to yield last.

---

## Chapter 3 — Data model

Six core tables carry the pipeline (`schema_v7` defines ~19 in total — the rest
are the concept/geo/segment/industry mapping layer, unused by the highlights
dashboard). `compiled_v2` mirrors the same shape minus that concept layer.

**Keys are composite, not surrogate.** `table_t` is keyed `(doc_id, table_id)`;
`row_dim` `(doc_id, table_id, row_id)`; `col_dim` `(doc_id, table_id, col_id)`;
`cell_fact` `(doc_id, table_id, row_id, col_id)`. Column names below are exact.

```
   document  ────►  one row per ingested PDF
      │             columns: doc_id, institution, doc_family,
      │                      source_file, doc_period
      │             (written by STEP 1)
      │ has-many
      ▼
   section   ────►  one row per section from the contents page
      │             (written by STEP 1)
      │ has-many
      ▼
   table_t   ────►  one row per extracted table
      │             columns: table_title, table_title_clean, unit,
      │                      hierarchy_source, page_range, section_id,
      │             table_type_id ◄── stamped by STEP 3b (convenience copy)
      │
      │ has-many
      ├──────► row_dim  ────►  one row per printed row
      │           │            columns: row_leaf_label, row_leaf_label_clean,
      │           │                     row_hierarchy, row_parent, line_no,
      │           │                     unit, sums_to, sums_sign,
      │           │            canonical_leaf_id ◄── stamped by STEP 3b
      │           │            table_type_id     ◄── stamped by STEP 3b
      │           │                                  (THE addressing column)
      │           │ intersects with col_dim
      │           ▼
      │        cell_fact ────►  one row per value cell
      │           ▲             columns: value_num, value_raw, unit,
      │           │                      cell_state, period, period_span,
      │           │                      period_source, colspan
      │           │ intersects with row_dim
      │           │
      └──────► col_dim  ────►  one row per column
                               columns: col_leaf_label, col_leaf_label_clean,
                                        col_period, period_span, period_start,
                                        canonical_col_id (NULL today),
                               col_role ◄── stamped by STEP 3a
```

There is **no `period_type` column** anywhere, and **no `indent` column** on
`row_dim` — period granularity is `period_span` + `period_start`; indentation
lives in `row_hierarchy` / `row_parent`.

| Table | One row per | Written by |
|---|---|---|
| `document` | ingested PDF | STEP 1 only |
| `section` | a section from the contents page | STEP 1 only |
| `table_t` | extracted table | STEP 3 (structure), 3b (`table_type_id`) |
| `row_dim` | printed row | STEP 3 (structure), 3b (`canonical_leaf_id`, `table_type_id`) |
| `col_dim` | column | STEP 3 (structure), 3a (`col_role`) |
| `cell_fact` | value cell | STEP 3 only |

Identity is per-row, not per-cell. Two cells sharing the same `row_dim` share
the same identity. Columns are periods today; segment/geo/entity/measure/level
come later.

**The terminal contract** — the fields the dashboard reads:

```
(document.institution, row_dim.table_type_id, row_dim.canonical_leaf_id,
 col_dim.col_period, col_dim.period_span, cell_fact.value_num)
```

The address is a **pair** — `table_type_id` *and* `canonical_leaf_id`. A leaf id
alone is not unique across table types.

`period_source` is *not* in the terminal contract. It is a `cell_fact` column
carried forward by `build_compiled_v2.py:227` and currently read by nothing —
see §5.3.

### 3.1 Where everything lives on disk

The pipeline folder was reorganised on 2026-08-12 and now mirrors the four
stages this report walks through: extract, load, stamp, then serve. If you are
looking for the code behind a stage, the folder is named after it.

```
findociq/
├── pipeline/
│   ├── run_doc.py                      the orchestrator — this is what you call
│   │
│   ├── stage1_extract/                 PDF -> parsed.json
│   │   ├── route/family.py             decides fs | pillar3 | slides | other
│   │   ├── toc/                        the contents page: pass1_toc.py (rule-based,
│   │   │                               no model calls) and toc_stage.py (Gemini),
│   │   │                               then toc_to_db.py writes document + section
│   │   ├── chunk/                      the extraction itself
│   │   │   ├── PASS2_v2.py             the driver (--chunk-pages lives here)
│   │   │   ├── extract.py              one call per unit; chunking; dropped-page rescue
│   │   │   ├── batch.py                the same work through the batch interface
│   │   │   └── geometry.py             row-indentation sidecar from the PDF layer
│   │   ├── excel/workbook.py           writes the per-document spreadsheet
│   │   └── gemini/                     the model client, cost accounting, and
│   │                                   prompts/ — these are CODE, not scratch
│   │
│   ├── stage2_load/load_v7.py          parsed.json -> compiled_fs.db, and the
│   │                                   period cascade (§6.2)
│   │
│   ├── stage3_stamp/                   meaning, from the hand-written lists
│   │   ├── masterlist/                 table_registry.yaml, proposal tooling
│   │   ├── resolve/                    the matchers: resolve_canonical_leaf.py,
│   │   │                               resolve_canonical_col.py, normalize.py
│   │   ├── apply/                      stamp_tables.py, restamp_columns.py
│   │   └── serve/build_compiled_v2.py  compiled_fs.db -> compiled_v2.db
│   │
│   └── common/                         used by more than one stage: source_store.py,
│                                       ingest_status.py, verify_cells.py,
│                                       db_check_xlsx.py, sync_bq.py, tag_workbook.py
│
├── tools/
│   ├── replay_load.py                  re-run the loader over the tracked audit files
│   └── slide_ingest/run_slides.py      decks: PDF -> spreadsheet + audit + its own db
│
├── app/findociq_app.py                 the Streamlit dashboard
├── schema/schema_v7.sql                the write-side schema
│
├── data/
│   ├── sources/                        input PDFs (drop new quarters here)
│   └── derived/
│       ├── paddle_scans/<doc_id>/      STEP 0 layout output
│       ├── toc/                        contents-page output
│       ├── masterlist/                 HAND-WRITTEN — the source of truth for meaning
│       └── dashboards/                 one CSV pair per dashboard
│
├── outputs/<family>/<run_dir>/
│   ├── audit/<doc_id>/<unit>/          parsed.json, meta.json, prompt.txt,
│   │                                   response.txt, pages.pdf — per unit
│   └── <doc_id>_<family>.xlsx          the spreadsheet you open to read a run
│
└── db/
    ├── compiled_fs.db                  the write side — every ingested document
    └── compiled_v2.db                  the read side — the ONLY database the app opens
```

Two notes on that tree. **`prompts/` is code**: a prompt change is a pipeline
change, and it belongs in a commit like any other (see `CLAUDE.md`). And **`db/`
holds exactly two files**: everything else that used to sit there — one-off
period databases, backups, replay intermediates, migration snapshots — was
deleted on 2026-08-12 after checking that each was reproducible, taking the
folder from 464 MB to 41 MB.

**`compiled_fs.db` is the one that matters.** It is the accumulated write-side
across every ingested document, and it is what `CLAUDE.md` tells you to push to
GCS after a run. The per-period names (`compiled_2q26.db`) are one-off targets
from `--db` on a single reproduction run — Appendix A uses one deliberately so it
cannot clobber the real DB. Don't mistake a per-period file for the canonical one.

Two directories matter most in day-to-day use. `data/derived/masterlist/` is
authored by humans — never write to it from a script without a review step.
`outputs/fs/` is disposable per-run scratch — delete a run directory to redo it
cleanly. Everything else regenerates.

---

## Chapter 4 — Masterlist and dashboard config

### 4.1 Masterlist

One CSV per bank at `data/derived/masterlist/`, uniformly named
`<BANK>_masterlist.csv`:

| Bank | File | Rows | Columns |
|---|---|---|---|
| DBS | `DBS_masterlist.csv` | 47 | 9 |
| OCBC | `OCBC_masterlist.csv` | 114 | 10 |
| UOB | `UOB_masterlist.csv` | 43 | 10 |

**Columns:** `bank`, `canonical_section`, `section_ordinal`, `table_type_id`,
`table_ordinal`, `line_ordinal`, `canonical_leaf_id`, `label`, `full_path`, `source_family`

`canonical_leaf_id` is a `::`-joined normalised path — the durable id for the
concept. Multiple `full_path` rows may share one — that's aliasing (Appendix B
lesson 3), the mechanism for the same concept printed under different parents.

`source_family` uses period-agnostic slugs:
- `overview` — DBS
- `Condensed_financial_statements` — OCBC + UOB
- `Media_release_and_financial_highlights` — OCBC ratios

**Key structural rule** 
`coining_canonical_id.md` gives a rough overview of how canonical ids are coined

### 4.1b Leaf aliases (`masterlist_leaf_aliases.yaml`)

A fourth, hand-authored file in the same directory. It maps a **historical leaf
id to its current one** when a bank *renames* a line across vintages — for example DBS
renamed the markets book between 1Q23 and 1Q25:

```yaml
DBS:
  FS_INCOME_SELECTED:
    treasury_markets_total_income: markets_trading_income
    treasury_markets_total_income::net_interest_income: markets_trading_income::net_interest_income
```

**The rule for what belongs here** (from the file's own header, confirmed
2026-08-05): if it is the *exact same printed line* under a changed name, record
an alias. If it is not the same line, do **not** alias it — leave it flagged so
it queues for review.

**Only renames belong here.** A label differing by a footnote marker, `&` vs
`and`, a unit suffix, or a section prefix is a **normaliser** concern, handled in
`build_masterlist_proposed.py` — never hand-aliased. This is the most likely
place for a new maintainer to make a mess: hand-aliasing something the
normaliser already collapses hides drift instead of resolving it.

Note this is a *different mechanism* from the two aliasing concepts above — keep
the three straight:

| Mechanism | Grain | Authored by |
|---|---|---|
| Multiple `full_path` rows sharing one `canonical_leaf_id` | printed path → leaf | human, in the masterlist CSV |
| `masterlist_leaf_aliases.yaml` | old leaf id → new leaf id | human, on rename |
| `canonical_leaf_path_alias` (DB table) | raw printed chain → leaf | **machine**, written on each confirmed match |

### 4.2 Dashboard config

Two files per dashboard at `data/derived/dashboards/`. The app globs on the
suffixes `_anchors.csv` and `_formulaanchors.csv` (`findociq_app.py:774`), so the
prefixes need not match each other — and today they don't:

**Anchors** — `highlights_dashboard_anchors.csv`. Pure match, one row per
(concept, bank). Columns: `concept`, `row_order`, `bank`, `table_type_id`,
`canonical_leaf_id`, `sign`, `source_family`.

**Formulaanchors** — `highlights_formulaanchors.csv` (note: **no** `dashboard_`
segment). Computed rollups, multiple rows per (concept, bank). Columns: same as
anchors + `member_ordinal` (position within formula). `sign = -1` marks
subtraction.

`bank` here is the short form (`DBS`), not `document.institution`'s legal name.

**Highlights dashboard state:** 74 anchor rows across 3 banks, 9 formulaanchor
rows for 4 computed concepts (DBS Net interest income + Other non-interest
income; OCBC Other non-interest income + Shareholders' equity).

How one dashboard concept fans out to bank-specific triples:

```
   Dashboard concept:  "Net interest income"
         │
         ├── For DBS  (formulaanchors, 2 members — computed rollup)
         │      └── FS_INCOME_SELECTED / commercial_book_total_income::net_interest_income (sign +1)
         │      └── FS_INCOME_SELECTED / markets_trading_income::net_interest_income     (sign +1)
         │
         ├── For UOB  (anchors, direct row)
         │      └── FS_INCOME_SELECTED / net_interest_income                              (sign +1)
         │
         └── For OCBC (anchors, direct row)
                └── FS_INCOME_CONSOLIDATED / net_interest_income                          (sign +1)


   Dashboard concept:  "Other non-interest income" — same shape,
                                                      subtraction for OCBC
         │
         └── For OCBC (formulaanchors, 2 members)
                └── FS_INCOME_CONSOLIDATED / non_interest_income          (sign +1)
                └── FS_INCOME_CONSOLIDATED / fees_and_commissions_net     (sign -1)  ◄── subtracted
```

Same concept, different `(table_type_id, canonical_leaf_id)` triple per bank.
The masterlist decides what each printed row means canonically; the anchors
CSV decides which of those canonical ids the dashboard cell should pull.

---

## Chapter 5 — Verifying, extending, open questions

### 5.1 Verifying a run

**Acceptance = dashboard-visible.** Not load counts. Not stamping totals. Clean
row counts + broken stamping happened on 2Q26 (5/50 leaves stamped; load
looked fine).

Three checks in order:
1. **Row counts** (console log): section count, unit count, table/row/cell counts.
2. **Stamping coverage:** tables typed, leaves stamped, against masterlist coverage.
3. **Dashboard render:** open Streamlit, select period, eyeball against PDF.
   Remember the dashboard reads `compiled_v2.db` — if you have not run
   `build_compiled_v2.py` since the ingest, you are eyeballing the OLD data and
   check 3 is meaningless (§2.0).

**Read the run by hand.** Open `<run_dir>/<doc>_fs.xlsx` — one sheet per table,
as extracted — and compare against the PDF. When a number looks wrong, the chain
back to its origin is `audit/<doc>/<unit>/`: `pages.pdf` is what the model saw,
`prompt.txt` what it was asked, `response.txt` what it said, `parsed.json` what
was kept. Spanning units keep the same five files per chunk under `chunks/c<N>/`.
See §2.0 for the full path table.

**A page can go missing without any error.** A multi-page chunk may answer for
only some of its pages, and the table COUNT will not show it — one table
legitimately spans a chunk. This cost DBS 2Q26 its entire quarter basis (page 7,
`Selected income statement items ($m)`) with a clean run log. The extractor now
detects it per page and re-asks (§2.4, `docs/specs/2026-08-12-dropped-page-rescue.md`),
but the guard fires at EXTRACTION time — a cached re-run cannot catch it, so a
document extracted before 2026-08-12 needs `--force` to be re-checked.

Diagnosing a blank cell — walk the tree:

```
   Dashboard cell is blank
         │
         ▼
   Is the masterlist authored for this concept in this bank?
         │
         ├── NO ──► Coverage gap. Author a masterlist row.
         │           Re-stamp. Done.
         │
         ▼ YES
   Query row_dim for the printed row → canonical_leaf_id = NULL?
         │
         ├── YES ──► Extraction succeeded but mapping missed.
         │           The printed label likely varies from what's in the
         │           masterlist. Add an alias row (new label + full_path,
         │           same canonical_leaf_id). Re-stamp.
         │
         ▼ NO
   Is the row missing from row_dim entirely?
         │
         ├── YES ──► Extractor dropped it.
         │           Open the Excel companion to confirm. If the row is
         │           also missing from parsed.json, it's a STEP 2 defect
         │           — likely a footnote-heavy header row or a page
         │           break. Diagnose upstream.
         │
         ▼ NO
   Is the col_role = 'derived_skip' on that column?
         │
         └── YES ──► Working as designed. The dashboard excludes derived
                     (% chg, delta) columns from cell_fact scans.
```

### 5.2 Extending

**Add masterlist for a new table:**
1. Open Excel companion for a document containing the table.
2. Decide `canonical_leaf_id` per row.
3. Write rows into `<BANK>_masterlist.csv`.
4. Re-stamp: `stamp_tables.py --db <db> --bank <BANK> --docs <doc_id> --out out.db --write`.
5. Confirm leaves resolve. Add concept to dashboard config.

**Onboard a new bank:**
1. Ingest a full year without any masterlist — verify extraction.
2. Author masterlist against the richest reference doc (annual report). Other periods contribute aliases only.
3. **Never build masterlist as union across periods.**
4. Re-stamp, verify dashboard renders per period, add to dashboard config.

**Ingest a new quarter:**
1. Drop PDF into `data/sources/`.
2. `run_doc.py --pdf <path>` — replaces that one document in `compiled_fs.db`
   (doc-scoped DELETE + re-insert, never an append; §2.0), re-stamped from the
   current masterlists.
3. `stage3_stamp/serve/build_compiled_v2.py` — otherwise the dashboard still shows the old DB.
4. Three checks per §5.1.

### 5.3 Open questions, ranked by how much they bite

> Six further open problems — measured on 2026-08-12, including the one blocking
> a clean rebuild — are in **§6.3**, with the difficulty classes and design
> decisions behind them in §6.1–6.2. Full evidence in `docs/TO_FIX.md`.

1. **D8 — extraction quality outside Overview.** 17 col-shift + 9 dup labels +
   18 missing numbers in DBS 2Q26 non-Overview. Harmless until a masterlist
   covers those tables; blocking every dashboard expansion beyond Highlights.
2. **`col_dim` canonicalisation — the stamper is built, the column masterlists
   are not.** `load_v7.py:2233` writes `col_dim.canonical_col_id` from
   `<BANK>_masterlist_cols.csv`, keyed `(bank, table_type_id)` exactly like the
   leaf side. What is missing is the authored data. Measured 2026-08-13: **UOB
   35/317 (11%) and 21/280 (7%); every OCBC and DBS document 0%** — OCBC 2Q26
   media release is 0/179 stamped columns against 406/572 stamped leaves. The
   column half of the canonical address is therefore empty for two of three
   banks, still blocking Changes in Equity, Loans by Industry and NPA by
   Geography. Second-order cost: the column **veto** (`load_v7.py:2160`) only
   fires for a type that *declares* a column block, so the check that caught
   UOB's geography-vs-segment misidentification cannot protect OCBC or DBS
   tables at all. `col_role = 'derived_skip'` is unaffected — it is
   masterlist-independent (`load_v7.py:2116`) and runs for every document.
3. **Masterlist column asymmetry.** *(File naming is resolved — all three are
   `<BANK>_masterlist.csv`.)* What remains: `DBS_masterlist.csv` has 9 columns
   and no `source_family`; OCBC and UOB have 10. Any code reading masterlists
   generically must tolerate the missing column, and adding a second DBS source
   family requires adding it.
4. **`FS_BALANCE_STATUTORY` gap — 3 blank cells across 2 banks.** Anchors point
   at `FS_BALANCE_STATUTORY` for DBS `Total equity` (row 15 of the anchors CSV),
   UOB `Total liabilities` (row 40) and UOB `Total equity` (row 41). **Zero
   masterlist rows carry that table type** for any bank — the concepts live in
   the Audited BS section, not authored. Three blank Highlights cells until
   fixed, not two, and it is not a UOB-only problem.
5. **`period_source` is carried but read by nothing.** *(Earlier drafts said it
   was "exposed in four serving views" — it is not.* `compiled_v2.db` has **zero**
   views; the four views `v_cell` / `v_cell_leaf` / `v_cell_sumsafe` /
   `v_cell_flat` live only in `compiled_fs.db`, none selects `period_source`, and
   the app queries none of them.) It exists as a `cell_fact` column carried by
   `build_compiled_v2.py:227`. Decide: keep carrying it as provenance, or drop it.
6. **Masterlist automation.** Currently manual. Deterministic parts (label
   normalisation, path walking) are a small Python module; `table_type_id`
   classification needs review. Not blocking a dashboard, blocking time.
7. **D6, D7 — DB targeting.** STEP 3b's `registry classify` crashes on
   schema_v7 DB; `compiled_v2` can't be a load target. Cosmetic but confusing.
8. **D5 — `--dry-run` ignored on single `--pdf`.** Spends Gemini money.
9. **D10 — DBS half-year EPS anomaly.** 1H26 prints as average not sum (4.27
   against quarterly 4.19/4.35). Verify against filing.
10. **Pillar 3 is loaded but unmapped.**
    `DBS_1Q26_P3_other_regulatory_disclosures` (family `pillar3`) sits in the DB
    with 8 tables and 197 rows and **0 stamped leaves** — no Pillar 3 masterlist
    exists and no dashboard addresses it. Harmless, but it makes the DB-wide
    stamped-vs-rows ratio look worse than it is. Decide whether Pillar 3 is in
    scope or should stop being ingested.

---

## Chapter 6 — Design decisions, stage by stage

§5.3 is a ranked backlog. This chapter walks the same four stages as Chapter 2 —
**Extract → Load → Stamp → Serve** — and for each states the decisions taken, the
defect that forced each one, and what it costs. A successor who reads this will
predict the next failure rather than rediscover it.

Two properties recur at every stage and are worth naming once:

- **Silent partial success is the dominant failure mode.** Not crashes — stages
  that return cleanly having done part of the job. Every guard below exists
  because something looked fine and was not.
- **NULL beats a guess.** A blank is a coverage gap to be authored, never a
  number to be inferred. Each stage has its own version of this rule.

---

### 6.1 EXTRACT — PDF → `parsed.json`

**The job.** Faithful transcription. Every printed row, verbatim, no
interpretation. Meaning is attached two stages later.

| Decision | Instead of | Cost |
| --- | --- | --- |
| Sections are units; a spanning unit splits into ≤2-page chunks | a fixed window over the PDF | a table crossing a chunk boundary needs the continuation merge |
| Detect a dropped page **per page**, not per chunk | "fewer tables than pages" | one extra render + text scan per multi-page chunk |
| The audit trail is the resume point — cached `parsed.json` replays with no model call | re-extracting every run | a cached re-run cannot produce prompt/response, nor fire extraction-time guards |
| Decks get their own tool and DB (`tools/slide_ingest`) | one extractor for everything | two entry points; but a deck has no contents page and a slide IS the unit |

**The defect that shaped this stage.** DBS 2Q26 prints its overview twice —
half-year basis on pages 4-6, quarter basis on 7-8. `overview_p4-8` chunked to
`[4,5] [6,7] [8]`. The `[6,7]` call returned ONE table — page 6's per-share block
— and dropped page 7 entirely. Run log clean. The whole quarter basis was missing
from the database: **632 cells span `1H` against 10 spanning `2Q`**, and every
page-7 figure returned zero rows from `cell_fact`.

The obvious check does not work: fewer tables than pages is **normal**, because
one table legitimately spans a chunk — that is why spanning units exist. So the
rule is per page: a page carrying ≥12 distinct numeric tokens whose tokens appear
in **no** returned cell produced nothing, and is re-asked for alone. The 12-token
floor is measured, not chosen — it sits between the thinnest real exhibit (DBS
`Per share data`, 21 tokens) and the fattest prose page (the notes block, 6).

**Watch for:** the same capability living in two code paths. This guard was
written into `extract_unit_chunked`, which `stage1_extract/chunk/batch.py` never calls — so it first
covered the path *less* likely to need it.

---

### 6.2 LOAD — `parsed.json` → `compiled_fs.db`, and the date problem

**The job.** Turn transcription into rows, and resolve **when** every number
refers to. Dates are the hardest part of this stage by a wide margin.

#### Why dates are hard

A printed figure does not carry its own period. The period may be on the column
(`30 Jun 2026`), on a row banner (`31 Dec 2025` with line items beneath), in the
table title (`— 2H25`), or nowhere at all — in which case it belongs to the
document's reporting date. Banks mix all four **within one document**.

**The cascade.** `load_v7` resolves per cell: `col > row > table_title > doc`,
and records which rung answered in `period_source`. Measured across the corpus:

| `period_source` | cells |
| --- | --- |
| `col` | 12,531 |
| `table_title` | 2,662 |
| `row` | 2,437 |
| `doc` | 2,041 |
| `row_banner` | 1,910 |
| **no period at all** | **0** — the loader raises rather than leave one unresolved |

`row_banner` (added 2026-08-13) is the rung for a **period BANNER that is a
SIBLING of the rows it heads**, not their ancestor. Banks stack period blocks
vertically — DBS `PERFORMANCE BY BUSINESS SEGMENTS` prints five blocks
(`2nd Half 2025`, `1st Half 2025`, `2nd Half 2024`, `Year 2025`, `Year 2024`)
whose banners the model emits at the *same* level as their data rows. The
ancestor-only walk could never see them, so all 225 cells fell through to
`doc_period` and **135 were wrong**. Worse, UOB's
`Classification of Financial Assets … Dec 24` prints its banner *deeper* than
its own rows, and all 105 cells — an entire Dec-2024 balance sheet — were
stamped Dec-2025. Every *value* matched the PDF, so `verify_cells` passed
throughout: this class of defect is invisible to cell verification.

The banner is told apart from an opening/closing **balance** row (`At 1 January
2026`, which owns its figures and must never scope anything) by
**valueless-ness** — `row_type in (section_header, sub_header)`, the same
membership that guarantees the row emits no `cell_fact`. Span is *not* the
discriminator: 28 of the 70 banners carry `as_at`, including the UOB one above.

**Nothing is invented.** `load_v7` stamps only what the document prints. A column
that carries no printed date gets **no `col_dim.col_period`** — the loader will
not guess one — and its cells fall to the next rung down. Where that is `doc`,
the cell gets the document's reporting date but **`period_span` stays NULL**,
because a reporting date carries no printed duration. So a NULL span is not a
failure; it is the honest record that nothing on the page stated a period for
that cell. 9% of cells sit there today, and ~70% of those are `+/(-) %` change
columns that are not period facts at all. Every cell always has a *period* —
the loader raises rather than leave one unresolved — but only a cell whose
period came from a printed date carries a *span*.

| Decision | Instead of | Cost |
| --- | --- | --- |
| Period is a **cell stamp**, resolved once at load | re-deriving it per query | a period fix needs a re-load, not a re-stamp |
| The SPAN travels with whichever axis won the period | taking span from the table | never mix a period from one axis with a span from another |
| `col` wins over `row` when both carry a period, and warns | silent precedence | a genuine both-axes clash is visible in the warnings |
| A bare year in a TITLE is not a period — **except** as the whole trailing caption | accepting any bare year | `— 2024` resolves; `Basel III 2024 framework` does not |
| No filing reports a period ending after its own reporting date | trusting the parse | a bare `2026` on an interim filing clamps to the doc's cycle |
| A stock and a flow closing on the same day are **different columns** | one column per date | `31-Dec-25` (as-at) and `FY25` must render as distinct tokens |

**The defect that shaped this stage.** UOB's 4Q25 geography exhibit is split five
ways by a printed caption: `— 1H25`, `— 2H24`, `— 2H25`, `— 2024`, `— 2025`. The
first three parsed. The two bare years did not, because a bare year in a title is
deliberately ambiguous — so both fell through to `doc_period`. For `— 2025` that
was coincidentally right. For `— 2024` it was **wrong by a full year**: 77 cells
of FY2024 stamped `2025-12-31`, colliding with the genuine FY2025 table on the
same period key.

The fix is not "accept bare years". That would make `Note 3 2025` a period. It is
positional: a bare year that occupies the title's **trailing caption slot** —
where its siblings print `1H25` — is the period; a year anywhere else is not.

**Also decided here:** a re-load **replaces** one document
(`DELETE … WHERE doc_id`), never appends. A document cannot be doubled by running
it twice, and other documents are untouched.

---

### 6.3 STAMP — masterlist → `canonical_leaf_id`, `canonical_col_id`

**The job.** Attach shared meaning to bank-specific printed labels. This is where
"the same concept" becomes addressable across three banks.

| Decision | Instead of | Cost |
| --- | --- | --- |
| **Every id copied VERBATIM from the masterlist** | deriving ids from labels | ids must be authored; but drift is *detectable* — see below |
| NULL on no match | fuzzy / nearest-label matching | blank cells to author, never a wrong number |
| Stamped at LOAD, from the masterlist as it is *then* | resolved at query time | a masterlist edit reaches the DB only by re-loading |
| One table, one `table_type_id` | letting a table wear several | a mixed exhibit forces a contest, not a merge |
| A printing slip is ALIASED, not normalised | a general singular/plural rule | one line per slip; a general rule would collapse distinct leaves |
| Bank comes from the masterlist row | a CLI `--bank` loop | none — `locate_tables` already matched every bank; the loop was waste |
| Column identity is a **separate axis** from the leaf | one id per cell | two stamps to author; but a segment column and an income row are different questions |

**Why "verbatim" is the load-bearing rule.** Because ids can only come from the
masterlist, an id in the database that is *not* in the masterlist is provably
residue. That is how 234 orphaned stamps were found: of 312 stamps present in
`compiled_fs.db` but absent from a clean re-stamp, 234 carry ids no masterlist
declares. Had ids been derived from labels, they would have looked legitimate.

**The difficulty this stage owns: accumulation.** `stamp_tables` adds and
overwrites but never clears, so the shipped 4,064 stamped leaves accumulated
across runs against successive masterlist vintages. A clean re-stamp with today's
masterlists yields **3,753**. The gap is 234 orphans (should go) plus 78 valid
leaves the matcher no longer finds (§6.5 #1). **A number being in the database is
not evidence that today's rules produce it.**

**And a seam worth knowing.** `col_dim.canonical_col_id` is written at load, so a
column-masterlist edit is invisible to a database already on disk.
`stage3_stamp/apply/restamp_columns.py` applies that one stage to a built DB — it drives the
same resolver, so the artifact and a rebuild agree today, and will diverge the
moment either side changes alone. `compiled_v2.db` currently carries **197
stamped columns and 4,064 stamped leaves**.

---

### 6.4 SERVE — `dashboards/*.csv` + `compiled_v2.db` → Streamlit

**The job.** Turn stamped identity into a rendered cell. The app reads **only**
`compiled_v2.db`, never `compiled_fs.db`.

**How a cell is resolved**, in order:

```
data/derived/dashboards/<set>_anchors.csv          ← the row list IS data
   concept, row_order, section, bank,
   table_type_id, canonical_leaf_id, canonical_col_id, sign, filter_by
        │
        ▼  address = (bank, table_type_id, canonical_leaf_id [, canonical_col_id])
compiled_v2.db   row_dim ⋈ col_dim ⋈ cell_fact
        │
        ▼  placement: filter_by decides WHICH period column the fact lands in
fiscal axis  →  one grid cell
```

| Decision | Instead of | Cost |
| --- | --- | --- |
| The row list is **data**, not code — a CSV pair per dashboard | a concept dictionary in the app | authoring is a CSV edit; adding a dashboard is dropping a pair in |
| An anchor set is **one dashboard**; sets are never merged | globbing everything | `row_order` is per file, so merging interleaves them |
| A multi-leaf line is **DECLARED** in the formula file | resolved by tie-break | rollups must be authored, never inferred |
| One address **per bank** | one shared address | OCBC and DBS declaring the same key double-counted when merged |
| The fiscal axis is built from **FLOWS only** | letting stocks mint columns | a stock cannot raise `30-Jun-26` beside the `1H26` it closes |
| …unless the set has no flows at all | applying that rule blindly | a balance-only dashboard uses its own closes as the axis |
| `filter_by=period_end_date` **fans a stock across every column closing on its date** | one fact, one column | a closing balance is correctly the closing figure for both 1H26 and 2Q26 |
| `legal_entity = CONSOLIDATED` and `col_role IS NULL` are **allowlists** | denylists | a new role can never silently start serving |

**The defects that shaped this stage.** Three, each a different way of being
quietly wrong:

1. **Merging anchor sets interleaved them.** `row_order` is per-file and every
   set starts at 1, so a second dashboard's rows scattered through the first's
   sections — unlabelled, because section headers emit only once.
2. **A stock-only dashboard rendered nothing.** All 304 facts resolved, but the
   axis is built from flows and every member was `as_at`, so the axis came back
   empty and every fact placed onto nothing. With no flows there is no column to
   duplicate, so the stocks' own closes ARE the axis.
3. **Consolidation basis was decided by column order.** OCBC prints `GROUP` and
   `BANK` for the same line; `Total assets` arrived as both 729,887 and 477,550,
   and the group figure survived only because `col_id 1` sorts before `col_id 3`.
   Now stated as a filter, not left to row order.

---

### 6.5 Open problems this chapter adds to §5.3

Measured 2026-08-12. Full evidence in `docs/TO_FIX.md`.

1. **A clean re-stamp loses 78 valid leaves** (STAMP), 61 in one exhibit — OCBC
   2Q26 `FINANCIAL HIGHLIGHTS`, one physical table carrying both income and
   balance-sheet rows. **This is what blocks a clean rebuild**: fix it and a
   rebuild drops 234 orphans while keeping the good stamps.
2. **OCBC has no segment columns in any lineage** (STAMP/SERVE).
   `FS_BALANCE_BY_SEGMENT` has zero columns for OCBC, so its five
   By-Business-Unit anchors cannot resolve under any vocabulary. UOB's equivalents
   reconcile exactly — gross loans 361,411 = sum of business units.
3. **Verification lost its only correctness check.** The archived KPH ground
   truth was the sole test of whether a number was RIGHT; everything remaining
   tests whether one is PRESENT. Revive path in the archive README.
4. **Batch-extracted documents cannot be re-checked offline** (EXTRACT). The
   batch audit trail omits `pages.pdf`, so the dropped-page sweep could only
   examine sync-path chunks — 6 of them, 1 defect found. Settling the rest needs
   a `--force` re-extract.
5. **Output directories are keyed on the scraped cover date**, not `doc_period`,
   so one period lands in two folders — `ocbc_Feb26` (media release, published
   Feb) and `ocbc_4Q25` (statements, period end Dec).
6. **Deck ingestion is unproven** — `tools/slide_ingest/` has tested parsing, an
   audit trail and its own DB, but has never run against a live deck.


---

## Appendix A — Reproducing DBS 2Q26

Fresh run:
```bash
python3 findociq/pipeline/run_doc.py \
    --pdf findociq/data/sources/financial_statements/DBS_2Q26_performance_summary.pdf \
    --db findociq/db/compiled_2q26.db --no-sync-bq
```

Re-stamp only (no re-load, no API):
```bash
.venv/bin/python findociq/pipeline/stage3_stamp/apply/stamp_tables.py \
    --db findociq/db/compiled_fs.db --out /tmp/stamped.db --write
```
`--bank` is an optional NARROWING filter; omitted, every bank the masterlist
declares is stamped in ONE pass. `locate_tables` already matches all
`(bank, table_type_id)` pairs by content, so looping per bank re-ran the whole
location pass for the same result. The source DB is never modified — it is
copied to `--out` first.

Re-stamp COLUMN identity on a built DB (when only the column masterlist changed):
```bash
.venv/bin/python findociq/pipeline/stage3_stamp/apply/restamp_columns.py \
    --db findociq/db/compiled_v2.db --write
```

Build serving DB:
```bash
.venv/bin/python findociq/pipeline/stage3_stamp/serve/build_compiled_v2.py \
    --src findociq/db/compiled_fs.db --dst findociq/db/compiled_v2.db
```

**Expected.** 21 audit units. 35 tables, 584 rows, 2,276 cells, 184 columns of
which 20 `derived_skip`. 5 tables typed (`FS_INCOME_SELECTED`,
`FS_BALANCE_SELECTED`, `FS_RATIOS_KEY`, `FS_PER_SHARE` ×2), 50/50 Overview leaves
stamped. Dashboard renders 25/26 rows at 1H26. Blank on Total equity
(`FS_BALANCE_STATUTORY` masterlist not authored — see §5.3 #4).

**Whole-DB state for reference** (`db/compiled_fs.db`, measured 2026-08-12): 10
documents, 342 tables, 5,772 rows, 21,581 cells, **4,064 stamped leaves**, 144
`derived_skip` columns.

| doc_id | tables | rows | cells | stamped |
|---|---|---|---|---|
| `DBS_1Q26_P3_other_regulatory_disclosures` | 8 | 197 | 425 | 0 |
| `DBS_1Q26_trading_update` | 4 | 47 | 201 | 45 |
| `DBS_2Q26_performance_summary` | 35 | 584 | 2,276 | 383 |
| `DBS_4Q25_performance_summary` | 46 | 734 | 3,409 | 613 |
| `OCBC_2Q26_Media_Release_and_Financial_Highlights` | 34 | 572 | 1,978 | 417 |
| `OCBC_2Q26_Unaudited_Interim_Financial_Statements` | 32 | 509 | 1,613 | 282 |
| `OCBC_4Q25_Condensed_Financial_Statements` | 41 | 628 | 2,377 | 462 |
| `OCBC_4Q25_Media_Release_and_Financial_Highlights` | 53 | 858 | 3,118 | 613 |
| `UOB_2Q26_Condensed_Interim_Financial_Statements` | 45 | 891 | 3,066 | 626 |
| `UOB_4Q25_condensed-financial-statements` | 44 | 752 | 3,118 | 623 |

The stamped counts are far above the 2026-08-07 figures this table previously
carried (616 corpus-wide) because all three banks' masterlists were curated on
2026-08-11. **Caveat:** 4,064 is NOT reproducible from a clean re-stamp — it
accumulated across runs against successive masterlist vintages, and 234 of those
stamps carry ids no longer in any masterlist. See `docs/TO_FIX.md` §2-3.

## Appendix B — Three lessons from DBS 2Q26

First unseen-doc test. Three defects surfaced, each fixed with a general rule.

**Lesson 1 — The extractor sometimes merges tables that should be separate.**
4Q25 printed Overview as three tables; 2Q26 as one with captions demoted to
rows. Every leaf gained an extra ancestor; verbatim match resolved 0/41
candidates. Fix: `transforms.split_caption_tables()` — when a table has ≥2
valueless rows at min printed level stating a unit (`($m)`, `(%)`, `($)`),
split into that many tables. Unit test load-bearing — without it, ordinary
banners like `Earnings2` get shattered.

**Lesson 2 — A demoted caption silently changes a table's unit.**
2Q26's half-year per-share table had page header taken as title and real
caption `Per share data ($)3` demoted to row 1. Title stated no unit →
fallback to doc default `S$m`. NAV rendered 25 instead of 24.69. Fix: title
repair — when exactly one unit-stating caption sits at row 0 and title states
no unit, promote caption to title, drop row.

**Lesson 3 — Same leaf, different printed parent.**
`Net book value` sits under `Reported earnings` in quarterly per-share,
directly under `Per share data ($)` caption in half-year. Fix: alias row in
masterlist. One `canonical_leaf_id` reachable by multiple printed paths.
Don't "normalise away" the parent — that violates the never-invent rule.

## Appendix C — Coverage tracker

### At a glance

```
                          │  DBS  │  UOB  │       OCBC        │
                          │       │       │ consolfs │ fshigh │
──────────────────────────┼───────┼───────┼──────────┼────────┤
 Income statement         │   ●   │   ●   │    ●     │   ●    │
 Balance sheet            │  ▲●   │  ▲●   │    ●     │   ●    │
 Comprehensive income     │       │       │    ○     │        │
 Changes in equity        │       │       │   ○ ✱    │        │
 Cash flow                │       │       │    ○     │        │
 Key financial ratios     │   ●   │   ●   │          │   ●    │
 Per-share (Basic/Dil)    │   ●   │   ●   │    ●     │   ●    │
 Loans / NPA / Deposits   │       │       │    ○     │   ○    │
 Segment breakdowns       │       │       │   ○ ✱    │  ○ ✱   │
──────────────────────────┴───────┴───────┴──────────┴────────┘

  ● populated   ○ deferred

  ▲ blocking gap — FS_BALANCE_STATUTORY is authored for NO bank, yet the anchors
    CSV references it 3×: DBS Total equity, UOB Total liabilities, UOB Total
    equity. 3 blank Highlights cells across 2 banks. See §5.3 #4.

  ✱ col_dim canonicalisation blocks authoring — these tables carry segment/geo
    columns, not period columns. See §5.3 #2.

  consolfs = Condensed_financial_statements   fshigh = Media_release_and_financial_highlights
  DBS has no source_family column, so it gets one column.
```

Read the ● row-by-row, not bank-by-bank: a ● means *some* masterlist rows exist
for that concept in that source family, not that the section is exhaustively
authored. The per-bank tables below are the real coverage.

**Per-share is ● four times for four different reasons** — worth knowing before
you assume a shared structure. DBS prints a discrete `FS_PER_SHARE` table. UOB
carries EPS/Basic/Diluted/NAV inline in `FS_RATIOS_KEY`. OCBC consolfs splits
them: EPS in `FS_INCOME_CONSOLIDATED`, NAV in `FS_BALANCE_CONSOLIDATED`. OCBC
fshigh carries all four in `FS_RATIOS_KEY`. Same concept, four shapes.

Row counts: DBS 47 · OCBC 114 · UOB 43. Highlights dashboard: 74 anchors +
9 formulaanchors across 3 banks. Not shown: DBS Pillar 3, loaded but with no
masterlist and no dashboard — see §5.3 #10.

### DBS
File: `DBS_masterlist.csv` — 47 rows. No `source_family` column.

| Section | Table types | Rows | Status |
|---|---|---|---|
| Overview | FS_INCOME_SELECTED, FS_BALANCE_SELECTED, FS_RATIOS_KEY, FS_PER_SHARE | 47 | Populated |

DBS is the only bank with `FS_PER_SHARE` — Per share data is a discrete
caption-split table. UOB and OCBC don't publish that structure.

### OCBC
File: `OCBC_masterlist.csv` — 114 rows. `source_family` disambiguates.

| Section (page) | source_family | Table types | Rows | Status |
|---|---|---|---|---|
| Consolidated Income Statement (p.2) | Condensed_financial_statements | FS_INCOME_CONSOLIDATED (Basic/Diluted EPS included as tail rows) | 29 | Populated |
| Statement of Comprehensive Income (p.3) | Condensed_financial_statements | — | — | Deferred |
| Balance Sheets (p.4) | Condensed_financial_statements | FS_BALANCE_CONSOLIDATED | 43 | Populated |
| Changes in Equity Group/Bank (p.5, p.7) | Condensed_financial_statements | — | — | Deferred — col_dim blocked |
| Cash Flow (p.8), Notes (p.9+) | Condensed_financial_statements | — | — | Deferred |
| FH → Selected IS Items (MR p.10–11) | Media_release_and_financial_highlights | FS_INCOME_SELECTED | 14 (incl. 1 alias) | Populated |
| FH → Selected BS Items (MR p.10–11) | Media_release_and_financial_highlights | FS_BALANCE_SELECTED | 6 | Populated |
| FH → Key Financial Ratios (MR p.12) | Media_release_and_financial_highlights | FS_RATIOS_KEY | 22 | Populated |
| Other MR sections (p.2–4, 6–8, 13–21) | Media_release_and_financial_highlights | — | — | Deferred |

### UOB
File: `UOB_masterlist.csv` — 43 rows.

| Section | source_family | Table types | Rows | Status |
|---|---|---|---|---|
| Financial Highlights → Selected IS items | Condensed_financial_statements | FS_INCOME_SELECTED | 12 | Populated |
| Financial Highlights → Selected BS items | Condensed_financial_statements | FS_BALANCE_SELECTED | 4 | Populated |
| Financial Highlights → Key financial ratios (p.2–3) | Condensed_financial_statements | FS_RATIOS_KEY (EPS + NAV inline; no FS_PER_SHARE) | 27 | Populated |
| Audited BS — Total liabilities, Total equity | Condensed_financial_statements | FS_BALANCE_STATUTORY | 0 | **Blocking gap** — 2 anchor rows |
| Other sections | Condensed_financial_statements | — | — | Deferred |

`FS_BALANCE_STATUTORY` is unauthored for **DBS too** (`Total equity`, 1 anchor
row) — 3 blank cells in total. See §5.3 #4.

### Pillar 3
No masterlist. `DBS_1Q26_P3_other_regulatory_disclosures` is loaded (8 tables,
197 rows) with 0 stamped leaves and no dashboard addressing it. See §5.3 #10.

### Highlights dashboard
`data/derived/dashboards/`:

| File | Rows | Coverage |
|---|---|---|
| `highlights_dashboard_anchors.csv` | 74 | 24 DBS + 26 UOB + 24 OCBC |
| `highlights_formulaanchors.csv` | 9 | 4 DBS + 0 UOB + 5 OCBC rollup members |

The two prefixes differ (`highlights_dashboard_` against `highlights_`). The app globs
on suffix only, so both load — but don't "fix" one to match the other without
checking `load_dashboard_anchors` (`findociq_app.py:747`).

### Update workflow
- Populate new section → update this appendix in the same commit.
- Row count = all masterlist rows including aliases.
- Retired sections: keep the row, flip to `Retired: <reason>`. History matters.
- Naming: `<BANK>_masterlist.csv` for all three banks. Per-doc split files may
  exist as review artefacts — they are not what the resolver reads.

---

*End of v0.4 draft.*
