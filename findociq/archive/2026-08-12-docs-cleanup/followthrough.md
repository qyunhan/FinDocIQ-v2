# Follow-through — DBS 2Q26 end to end

**Run date:** 2026-08-06 · **Document:** `DBS_2Q26_performance_summary.pdf` (1.03 MB)
**Why this document:** it had never been ingested. Every other DBS filing was in
the DB before the masterlist existed, so this is the first genuine test of
whether an *unseen* document flows
 through and lands correctly tagged.

**Verdict:** the document flows end to end and the Overview IS correctly tagged
— after three fixes this run forced. 21 units, 36 tables (after a caption split),
584 rows, 2,276 cells, **50/50 Overview leaves stamped**, and the dashboard
renders **25 of 26** rows at 1H26. The one blank is `Total equity`, which anchors
to a table (`FS_BALANCE_STATUTORY`) no masterlist covers yet.

The first pass tagged only 5 leaves. What it took to get from there to 50 is the
substance of this document — see §4.

---

## 1. The lifecycle, stage by stage

```
                      data/sources/financial_statements/DBS_2Q26_performance_summary.pdf
                                          │
  ┌───────────────────────────────────────┼─────────────────────────────────────┐
  │ STAGE 1 — EXTRACT            orchestrator: pipeline/run_doc.py (STEP 0-2b)   │
  └───────────────────────────────────────┼─────────────────────────────────────┘
                                          │
   ROUTE  classify/family.py ──────────► institution=DBS, family=fs
          (page-1 FS vocabulary; never a filename hack)
          run_doc: doc_id = pdf stem, doc_period = filename period token
                   2Q26 -> 2026-06-30
                                          │
   STEP 0  discover/section/candidates.py            .venv-paddle (PaddleOCR)
           in  : the PDF
           out : data/derived/paddle_scans/<doc_id>/{regions.csv,candidates.csv}
                                          │
   STEP 1  toc/toc_stage.py                          Gemini · prompts/fs_toc_headings.txt
           in  : the PDF, paddle candidates.csv
           out : data/derived/toc/<doc_id>_toc_raw.json   (Gemini cache)
                 data/derived/toc/<doc_id>_toc.json       (finalised, 43 sections)
           then toc/toc_to_db.py
           writes: document, section                 <- the ONLY writer of these
                                          │
   STEP 2  PASS2_v2.py -> pass2/extract.py           Gemini · prompts/stage2_core.txt
           in  : the PDF + <doc_id>_toc.json
           out : outputs/fs/dbs_2Q26/audit/<doc_id>/<unit>/parsed.json   (21 units)
                 (Extraction -> GTable/GRow/GColumn/GCell)
                                          │
   STEP 2b pass2/geometry.py
           in  : the PDF text layer (pdfplumber chars)
           out : a "geometry" side-car key INSIDE each parsed.json
                 {line_id, indent, label_clean} — never new GRow fields
                                          │
  ┌───────────────────────────────────────┼─────────────────────────────────────┐
  │ STAGE 2 — LOAD               orchestrator: run_doc.py STEP 3                 │
  └───────────────────────────────────────┼─────────────────────────────────────┘
                                          │
   STEP 3  pass2/load_v7.load_units()                doc-scoped, idempotent
           in  : the 21 parsed.json + document/section already in the DB
           out : table_t, row_dim, col_dim, cell_fact           (schema_v7)
           derives, none of it in the JSON:
             row_parent      printed-parent precedence + positional walk
             value_num       parsed from GCell.value
             unit            col -> row -> table -> doc cascade
             period/span/start   parsed from column headers
             sums_to/sign    arithmetic verification
             hierarchy_source 'geometry' | 'model'
                                          │
  ┌───────────────────────────────────────┼─────────────────────────────────────┐
  │ STAGE 3 — STAMP IDENTITY     inside load_units, at the end, before commit    │
  └───────────────────────────────────────┼─────────────────────────────────────┘
                                          │
   load_v7._stamp_identity()
     3a  col_dim.col_role='derived_skip'            masterlist-INDEPENDENT
         every '% chg' / '+/(-)%' column, every table, every document
     3b  mapping/Stamping/resolve_canonical_leaf.py
         in  : data/derived/masterlist/masterlist_DBS_overview.csv   <- THE ONLY
               source of canonical_leaf_id; never derived, never invented
         locate_tables()  scores each table's printed row paths against the
                          masterlist's full_path  (CONTENT, not caption)
         resolve_table()  3-stage match: verbatim path -> normalised path
                          -> caption-stripped; writes the masterlist's id VERBATIM
         out : table_t.table_type_id, row_dim.canonical_leaf_id
               canonical_leaf_path_alias (confirmed raw_path -> id)
         NO-OP when no masterlist covers a table — NULL, never a guess
                                          │
  ┌───────────────────────────────────────┼─────────────────────────────────────┐
  │ SERVING                                                                      │
  └───────────────────────────────────────┼─────────────────────────────────────┘
   tools/build_compiled_v2.py   schema_v7 DB -> db/compiled_v2.db (clean schema,
                                carries the stamps + period_source + title_clean)
   app/findociq_app.py          reads compiled_v2.db
     + data/derived/dashboards/DBS_highlights_dashboard_anchors.csv
       data/derived/dashboards/DBS_highlights_dashboard_formulaanchors.csv
     joins on (bank, table_type_id, canonical_leaf_id), period from
     col_period+period_span, excludes col_role='derived_skip'
```

## 2. What each stage actually produced

| stage | measure | value |
|---|---|---|
| ROUTE | institution / family | `DBS` / `fs` -> Gemini toc_stage branch |
| ROUTE | doc_id / doc_period | `DBS_2Q26_performance_summary` / `2026-06-30` |
| STEP 0 | paddle scan | `regions.csv`, `candidates.csv` written |
| STEP 1 | sections | **43** |
| STEP 2 | audit units | **21** |
| STEP 2b | geometry | side-car written; OVERVIEW table still `hierarchy_source='model'` |
| STEP 3 | tables / rows / cells | **36 / 584 / 2,276** (32 before the caption split) |
| STEP 3 | load warnings | 100 |
| STEP 3a | `col_role='derived_skip'` | **16** columns |
| STEP 3b | `canonical_leaf_id` | **50** leaves — 5 on the first pass |
| STEP 3b | `table_type_id` | **5** tables — 1 on the first pass |

## 3. Tables the masterlist reached

| table_type_id | matched | printed table |
|---|---|---|
| FS_INCOME_SELECTED | 20/21 | `Selected income statement items ($m)` — split out |
| FS_BALANCE_SELECTED | 8/8 | `Selected balance sheet items ($m)` — split out |
| FS_RATIOS_KEY | 12/12 | `Key financial ratios (%)²,³` — split out |
| FS_PER_SHARE | 5/5 | `Per share data ($)³` — quarterly (2Q26/2Q25/1Q26) |
| FS_PER_SHARE | 5/5 | `Per share data ($)3` — half-year (1H26/1H25/2H25), title repaired |

All 40 identity-bearing rows of the merged table resolved to a masterlist id.
The one FS_INCOME_SELECTED miss is `citi_integration`, which 2Q26 does not print
— the same absence as 1Q26, and not a defect.

---

## 4. Discrepancies found (the point of the exercise)

### D1 — FIXED · the extractor merged three tables into one

4Q25 prints the Overview section as **three** tables. 2Q26 produced **one**
captioned `OVERVIEW`, the three captions demoted to rows inside it:

```
OVERVIEW (45 rows)
  r1  Selected income statement items ($m)   <- was a table caption
  r2    Commercial book total income
  r24 Selected balance sheet items ($m)      <- was a table caption
  r33 Key financial ratios (%)2,3            <- was a table caption
```

Every leaf gained an extra ancestor (`direct match 0/41`), and worse, three
logical tables competed for ONE `table_t.table_type_id` — `FS_RATIOS_KEY` won by
write order, leaving 28 correctly-stamped leaves unreachable by the dashboard's
`(table_type_id, canonical_leaf_id)` key.

**FIX — `transforms.split_caption_tables()`, called from `load_units` before
`_load_table`.** A table with TWO OR MORE valueless rows at its minimum printed
level that STATE A UNIT is several tables; each part takes its caption as title.

> **The unit test is load-bearing.** Without it the rule fired on `Earnings2` and
> `Reported earnings` — ordinary row banners — shattering the per-share table and
> breaking five geometry tests. A printed table caption declares the unit its
> columns are in (`($m)`, `(%)`, `($)`); a banner scoping a group inside a table
> does not. Typographic convention of the filings, not a per-bank rule.

It also split `CAPITAL ADEQUACY` into 3 unprompted — the rule generalising.

### D2 — FIXED · a demoted caption left the table mis-titled and mis-united

`DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES` was not a false positive, as first
diagnosed — it is DBS's **half-year** per-share table (1H26/1H25/2H25, distinct
from the quarterly one at 2Q26/2Q25/1Q26). The extractor took the page header as
its title and demoted the real caption `Per share data ($)3` to row 1.

Two failures followed: the loader derives a table's unit from its title, so every
per-share figure inherited the document default `S$m` and NAV rendered **25**
instead of **24.69**; and every row carried the caption as an extra ancestor.

**FIX — title repair in the same transform.** Exactly one unit-stating caption,
at row 0, and a title that states no unit -> promote the caption to the title and
drop the row.

### D3 — FIXED · one printed path was missing from the masterlist

`Net book value` sits under `Reported earnings` in the quarterly table but
directly under the caption in the half-year one, so it resolved in one and not
the other. Not a normalisation gap — normalisation is label-only by design;
supplying a missing ancestor would be inventing identity.

**FIX — an alias row in the masterlist**, which is exactly what the `full_path`
column is for: several printed paths, one canonical id.

```
...,reported_earnings::net_book_value,Net book value5,Reported earnings > Net book value5
...,reported_earnings::net_book_value,Net book value5,Per share data ($) > Net book value5
```

### D4 — FIXED · re-stamping required a full re-load

`stamp_tables.py` wrote `canonical_leaf_id` but not `table_type_id`, so the
one-row masterlist edit in D3 forced a complete re-load — re-reading 21
`parsed.json` and re-deriving parents, periods, sums and units that had not
changed. The masterlist changes far more often than the extraction does.
**FIX:** `stamp_tables.py` now writes `table_type_id` too, so a masterlist edit
is a seconds-long standalone re-stamp. Load-time stamping stays, so a fresh
ingest is still correct by default.

### D5 — OPEN · `--dry-run` is ignored for a single `--pdf`

`run_doc.py:1024` handles it only inside the `--all` branch. `--pdf … --dry-run`
starts the real pipeline, PaddleOCR and Gemini included. Cost-relevant.

### D6 — OPEN · STEP 3b `registry classify` crashes on a schema_v7 DB

`sqlite3.OperationalError: no such table: table_registry`. That table exists only
in `compiled_fs.db`; a DB built from `schema_v7.sql` — the loader's own target —
does not have it, so run_doc exits 1 *after* a successful load and stamp.

### D7 — OPEN · `compiled_v2.db` cannot be a load target

`ingest_status.mark()` uses `ON CONFLICT`, but `build_compiled_v2`'s
`ingest_status` DDL drops the PK. Correct by design — v2 is derived — but the
error names none of that.

### D8 — OPEN · extraction quality outside the Overview

17 col-shift, 9 duplicate row labels, 18 numbers present in the PDF and missing
from the extraction. Concentrated in
`non_performing_assets_and_loss_allowance_coverage_p21-23` (71 discrepancies in
chunk c1, 8 in c2) and `capital_adequacy_p26` (9). Harmless today because no
masterlist covers those tables; blocking the moment one does.

### D9 — FIXED · `.venv-paddle` was broken

`.venv-paddle/bin/python3` symlinked into `.venv/bin/` with no `pyvenv.cfg`
beside it, so Python resolved to the system interpreter and lost the venv —
`ModuleNotFoundError: paddleocr`. Made `.venv-paddle` a symlink to `.venv`, which
is what `docs/workstation-setup.md` step 4 specifies.

### D10 — TO VERIFY AGAINST THE FILING · half-year EPS below both quarters

1Q26 basic EPS 4.19, 2Q26 4.35, but 1H26 prints **4.27** — an average, not a sum.
That is what the PDF shows, so it may be DBS's basis, but it is worth one human
glance before anyone relies on the half-year per-share figures.

---

## 5. Answering the expectation

> *"I should expect just the OVERVIEW table to be all correctly tagged."*

**Met.** All four Overview tables carry their own `table_type_id` and every
identity-bearing row its own `canonical_leaf_id` — 50/50, verified against the
masterlist with zero ids written that the masterlist does not contain.

The dashboard renders **25 of 26** rows at 1H26. The one blank, `Total equity`,
anchors to `FS_BALANCE_STATUTORY`, which no masterlist covers yet — a coverage
gap, not a failure. It is blank on every other period too.

Everything outside the Overview is deliberately untagged: 5 of 36 tables typed,
because the masterlist covers four table types. Nothing wrong is stamped.

## 6. What this run proved, and what it cost

The value of running an unseen document was that **three real defects only appear
when the print shape changes between vintages**, and none of them were visible on
the six DBS documents already in the DB:

* a section printed as one table in one quarter and three in another (D1)
* a caption demoted to a body row, which silently changes a table's UNIT (D2)
* a line whose parent differs between two tables in the SAME document (D3)

All three were fixed with general rules — a unit-stating caption test, a title
repair, and an alias row — not per-document conditionals. The stamping guard
("never write an id the masterlist does not contain") did its job throughout: it
surfaced each of these as a visible blank rather than a silently wrong binding.

The cost was one Gemini extraction of 21 units; STEP 1 reused its cached TOC.

## 7. What to do next, in order

1. **Author `FS_BALANCE_STATUTORY`** — the only thing standing between 25/26 and
   26/26 on the DBS dashboard.
2. **Make STEP 3b tolerate a missing `table_registry`** (D6) so `run_doc` exits 0
   on a schema_v7 target instead of reporting failure after a successful load.
3. **Honour `--dry-run` for a single `--pdf`** (D5) — it currently spends money.
4. **Verify the half-year EPS basis** against the filing (D10).
5. Author the remaining DBS masterlists (~30 tables). Each is now a seconds-long
   `stamp_tables.py` re-stamp rather than a re-load (D4).

## 8. Reproducing this run

```bash
# Stage 1 + 2 + 3 (one orchestrator; stamping happens inside the load)
PYTHONPATH="$HOME/paddle-fix" .venv/bin/python findociq/pipeline/run_doc.py \
    --pdf findociq/data/sources/financial_statements/DBS_2Q26_performance_summary.pdf \
    --db findociq/db/compiled_2q26.db --no-ipv4-shim --no-sync-bq

# re-stamp only (masterlist changed, no re-extraction)
.venv/bin/python findociq/pipeline/mapping/Stamping/stamp_tables.py \
    --db findociq/db/compiled_2q26.db --bank DBS \
    --docs DBS_2Q26_performance_summary --out <out.db> --write

# serving
.venv/bin/python findociq/tools/build_compiled_v2.py \
    --src findociq/db/compiled_2q26.db --dst findociq/db/compiled_v2.db
```

Gemini spend: STEP 1 reused its cached TOC; STEP 2 was a fresh extraction of 21
units.
