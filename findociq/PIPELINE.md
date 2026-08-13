# FinancialParser — FS pipeline

End-to-end: PDF → PaddleOCR scan → section map (TOC) → extraction → schema_v7 DB → verify.
Two Python environments, **both built automatically by `run_doc.py`** — there is no
setup step: `.venv` for everything, and `.venv-paddle` for the PaddleOCR steps, built
ON DEMAND the first time a document actually needs a scan (see STEP 0). The FS
database is `findociq/db/compiled_fs.db`.

Inputs live FLAT under `findociq/data/sources/{financial_statements,pillar3}/<name>.pdf`.
(They were once nested as `<BANK>/<year>/<qtr>/`; that layout is gone. Cached TOCs still
record the old path in `document.source_pdf`, which is why `run_doc._find_source_pdf`
resolves a document's PDF by NAME rather than by the stored path.)
Derived artifacts under `findociq/data/derived/` (`paddle_scans/`, `toc/`). Checks under
`findociq/outputs/checks/`.

## ONE COMMAND (run this)

`run_doc.py` drives the whole pipeline. Every step is idempotent and resumable, so a
re-run costs nothing beyond what actually changed (cached TOC + existing audit units
resume for $0).

```
# whole document, end-to-end (scan → TOC → extract → load → verify → xlsx)
python3 findociq/pipeline/run_doc.py --pdf <path/to/FS.pdf>

# rebuild the ENTIRE db from schema_v7 + every cached TOC that has an audit dir
python3 findociq/pipeline/run_doc.py --rebuild-db

# load-from-artifacts + verify only, no extraction ($0)
python3 findociq/pipeline/run_doc.py --verify-only --pdf <path/to/FS.pdf>
```

Deterministic metadata (no per-bank rule): `doc_id` = pdf stem (spaces→underscores);
`doc_period` = the filename period token (`1Q25`→`2025-03-31`, `2Q25`→`2025-06-30`,
`3Q25`→`2025-09-30`, `4Q25`/`FY2025`→`2025-12-31`, case-insensitive). `--doc-period`
overrides; no token and no flag → fail loud.

Flags: `--db` (default `findociq/db/compiled_fs.db`), `--batch` (Gemini Batch API, 50%
cost), `--force` (re-extract even when an audit unit exists), `--seed-registry` (run
STEP 3b registry seed/classify — needed only by the masterlist authoring flow),
`--no-ipv4-shim` (disable the AF_INET getaddrinfo shim, on by
default for this host's IPv6 blackhole). On any verify failure the driver auto
re-extracts JUST the failing sections (`--section --force`), doc-scoped reloads, and
re-verifies — up to 2 rounds — then exits non-zero listing anything still failing.

Pure-helper tests: `python3 findociq/pipeline/test_run_doc.py`.

---

The rest of this file is the **manual / debug path** — the individual stages
`run_doc.py` orchestrates, for when you need to run or inspect one step in isolation.

## STEP 0 — PaddleOCR scan (`.venv-paddle`, built on demand)
Emits `candidates.csv` / `regions.csv` / `stitch_verdicts.csv` per doc. Once per corpus
(skips docs whose `regions.csv` exists — 226 such artifacts are committed, so the whole
current corpus reloads with paddle never installed).

`run_doc.py` calls `stage1_extract/toc/candidates.py` under `.venv-paddle`, building
that environment first if absent (`ensure_paddle_venv` → `tools/setup_paddle_venv.sh`,
~1 GB, a few minutes, once). The script also writes the mkldnn-disabling
`sitecustomize.py` into `/tmp/paddle-scratch`; `paddle_env()` points `PYTHONPATH` there
and `HOME` at `/tmp/paddle-scratch/paddlehome`. Nothing goes in the operator's `$HOME`.
```
# normally just: python3 findociq/pipeline/run_doc.py --pdf <file.pdf>
bash tools/setup_paddle_venv.sh          # build the env eagerly
FINDOCIQ_NO_PADDLE_BOOTSTRAP=1 …         # forbid the build; STEP 0 then fails loudly
.venv-paddle/bin/python3 findociq/pipeline/stage1_extract/toc/batch_scan.py   # whole corpus
# → findociq/data/derived/paddle_scans/<tag>/
```

## STEP 1 — Section map (TOC). Two branches, same downstream contract.
**Branch A — FS docs (Gemini headings).** The promoted spike: Gemini returns bare
headings, deterministic coordinate windows anchor them to PaddleOCR regions. ~1 API
call/doc (raw response cached; re-runs free). Contents page auto-detected.
```
python3 findociq/pipeline/stage1_extract/toc/toc_stage.py \
  --pdf findociq/data/sources/financial_statements/DBS/2025/2Q25/DBS_2Q25_performance_summary.pdf
# → findociq/data/derived/toc/<doc_id>_toc.json
python3 findociq/pipeline/stage1_extract/toc/toc_to_db.py \
  --toc findociq/data/derived/toc/DBS_2Q25_performance_summary_toc.json \
  --db findociq/db/compiled_fs.db --doc-period 2025-06-30
# → document + section rows in compiled_fs.db
```
**Branch B — Pillar 3 docs (deterministic printed-TOC).** Zero API cost. Since the
2026-07-16 pivot this is the `pass1_toc` framework plus a schema_v7 adapter — the
route `run_doc.py:389` actually takes when `classify/family.py` returns `pillar3`:
```
python3 findociq/pipeline/stage1_extract/toc/pass1_toc.py <pdf> --out <doc_id>_toc.json
python3 findociq/pipeline/toc/pass1_to_v7.py --pass1 <doc_id>_toc.json \
  --doc-id <doc_id> --source-rel <path/to/FS.pdf> --out findociq/data/derived/toc/<doc_id>_toc.json
```
`pass1_toc.py` is self-contained — stdlib + pypdfium2 + pdfplumber, and imports NOTHING
from `discover/section/`. The only live modules left in that package are `candidates.py`
(STEP 0's PaddleOCR region scan, invoked by `run_doc.py`) and `batch_scan.py` (the
corpus-wide wrapper around it); `candidates.py` imports none of its siblings either.
The OLDER orchestrator (`tag_sections.py` + `section_manifest.py`, `pick_branch`) is
RETIRED — see `archive/2026-08-12-handover-cleanup/`; it had been import-broken since
2026-08-06 and `run_doc.py` never called it.

Both branches emit a section map consumed identically by STEP 2+, and both rejoin at
`toc/toc_to_db.py`.

**Which branch fires is not a manual choice.** `classify/family.py` reads page 1 and
returns `pillar3` | `fs` | `slides` | `other` from general content/geometry signals (no
per-bank branch); `run_doc.py` routes on that, and prints the decision as
`[route] family=… -> …`.

## STEP 2/3 — Extraction + load (base `python3`, Gemini)
Extract one tab per section, then load parsed cells into schema_v7.
```
python3 findociq/pipeline/stage1_extract/chunk/PASS2_v2.py <pdf> --toc findociq/data/derived/toc/<doc_id>_toc.json
python3 findociq/pipeline/stage2_load/load_v7.py --db findociq/db/compiled_fs.db \
  --doc-id <doc_id> --section-id <sid> --pages 25 --parsed <parsed.json>
```
`--batch` runs extraction via the Gemini Batch API (async, bills at 50%; prompts/config/contract byte-identical to the sync path) — use it for cost-sensitive bulk/backfill runs where a few minutes of latency is acceptable, not interactive iteration.

## STEP 4 — Verify (base `python3`)
```
python3 findociq/pipeline/common/verify_cells.py --manifest <manifest> --db findociq/db/compiled_fs.db
python3 findociq/pipeline/common/db_check_xlsx.py --db findociq/db/compiled_fs.db
# → findociq/outputs/checks/compiled_fs.xlsx (one sheet per table, DB truth vs printed page)
```

**Loader regression:** `python3 findociq/pipeline/stage2_load/test_load_v7.py` (178 checks).
**Whole suite:** every `test_*.py` under `pipeline/`, `app/`, `tools/` is a plain
script — `python3 <file>`, exit 0 = pass. All 38 pass as of 2026-08-12.

**Retired 2026-08-12:** the concept layer (`pipeline/concept/`, old STEP 4a/4b/4c)
and the anchor/`bank_line_map` mapping layer. Row identity comes from the masterlist
at load (STEP 3) via `mapping/` → `canonical_leaf_id`. See
`archive/2026-08-12-handover-cleanup/README.md`.
**DB:** `findociq/db/compiled_fs.db` (tracked). Audit replay evidence (`parsed.json`/`meta.json`
under `findociq/outputs/pillar3/**/audit/`) is tracked; other `findociq/outputs/` is not.

## pipeline/ — three stages, named for what they do

```
pipeline/
  run_doc.py            THE one command; drives all three stages
  stage1_extract/       PDF -> parsed.json + per-doc .xlsx   (nothing DB-shaped)
    route/              family.py — pillar3 | fs | slides | other
    toc/                toc_stage (Gemini) · pass1_toc (deterministic)
                        pass1_to_v7 · toc_to_db · candidates (PaddleOCR) · batch_scan
    gemini/             gemini_client · cost · prompts/*.txt
    chunk/              PASS2_v2 · extract · batch · schema · transforms
                        geometry · render     (the Gemini call + its contract)
    excel/              workbook.py -> outputs/fs/<tag>/<DOC>_fs.xlsx
  stage2_load/          load_v7.py — the ONLY writer of schema_v7 tables:
                        table_t · row_dim · col_dim · cell_fact · *_lineage
  stage3_stamp/         canonical identity, per bank / section / line
    masterlist/         masterlist_derive · propose_masterlist · table_registry.yaml
    resolve/            resolve_canonical_leaf · resolve_canonical_col
                        normalize · registry · seed_registry
    apply/              stamp_tables · restamp_columns  (write the ids into a built DB)
    serve/              build_compiled_v2.py -> db/compiled_v2.db (what the app reads)
  common/               verify_cells · db_check_xlsx · source_store · ingest_status
                        ingest_quarter · ingest_manifest · tag_workbook
                        scrape_bank_ir · sync_bq · fix_identity_misstamps
```

Stage boundary in one line each:
1. **stage1_extract** ends when a page's tables exist as `parsed.json` (+ the audit
   trail) and the per-doc workbook is written. Nothing here knows the schema.
2. **stage2_load** turns that into rows in `schema_v7.sql`'s tables — and it is the
   only place that does. `docs/specs/2026-07-13-gtable-schema-v7-loader-design.md`
   is its contract.
3. **stage3_stamp** assigns identity: `canonical_leaf_id` / `canonical_col_id` read
   off the CURATED masterlist in `data/derived/masterlist/` (never generated — see
   `archive/2026-08-06-masterlist-retirement/`), keyed `(bank, table_type_id)`, so
   the same printed line in the same section resolves to the same id across banks
   and quarters. `serve/build_compiled_v2.py` then emits `compiled_v2.db`.

Imports use the full path from `pipeline/` (`from stage2_load.load_v7 import ...`),
which is on `sys.path`. Directory names avoid a leading digit deliberately — a
Python package cannot start with one.

## Folder map (what every top-level directory is for)

| dir | role | status |
|---|---|---|
| `pipeline/` | THE pipeline — `stage1_extract` / `stage2_load` / `stage3_stamp` / `common`, driven by `run_doc.py` (see the stage map above) | active |
| `db/` | THE database: `compiled_fs.db` (schema_v7, multi-document, tracked) | active |
| `schema/` | SQL DDL — `schema_v7.sql` is authoritative and the only one (v5/`final.db` retired) | active |
| `data/sources/` | input PDFs (pillar3 / financial_statements / presentations / regulatory) | active |
| `data/derived/` | deterministic per-doc artifacts: `paddle_scans/`, `toc/` | active |
| `outputs/` | per-run artifacts (pass2 workbooks, audit dirs, `checks/` xlsx). Gitignored EXCEPT `audit/**/parsed.json|meta.json` — the $0 DB-replay source | generated |
| `docs/` | `specs/` (binding design records), `plans/`, `diagrams/` | active |
| `app/` | the LIVE Streamlit dashboard (`findociq_app.py`, reads `db/compiled_v2.db` + `data/derived/dashboards/*.csv`). Upstream source for the Findociq-Dashboard deploy repo — see `app/DEPLOY.md` | consumer |
| `tools/` | manual CLIs, NOT called by `run_doc.py` or any stage: `replay_load.py` (tracked audit artifacts -> `compiled_reload.db`) and `slide_ingest/run_slides.py` (decks: PDF -> workbook + audit + its own `compiled_slides.db`). `build_compiled_v2.py` / `restamp_columns.py` moved to `pipeline/stage3_stamp/`; the v5-era `tools/slides/` deck kit and `reports/` are gone | consumer |
| `experiments/` | frozen research records (dated spikes; code that graduated was MOVED to `pipeline/` — see each dir's POINTER.md) | frozen |
| `_legacy/` | pre-findociq DELIVERABLE era, frozen reference (pass2 was ported FROM here) | frozen |
