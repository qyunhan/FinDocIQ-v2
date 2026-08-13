# Spec: Universal Stage-1 Discovery + full-PDF orchestration

**Date:** 2026-06-29
**Status:** Design — pending user review
**Related:** `FinDocIQ_Plan_9.docx` §5 (orchestration); `_legacy/.../pillar3/PASS1_TOC.py` (TOC engine to adopt)

## 1. Goal

Point the pipeline at a **complete PDF — Pillar 3 *or* financial statement** — and get a
**complete, correct table manifest**, then extract→load every table into `schema_v5`.
Must work whether or not the document has a usable TOC.

Established facts (this repo): Pillar-3 PDFs have a parseable TOC; the financial statements
(`OCBC/DBS_financialstatement_2025.pdf`) and press releases have **0 bookmarks, no contents
page** — tables start on page 1. So a TOC-only discovery is insufficient and silently empty
for FS. The prior `PASS1_TOC.py` works for Pillar 3 but its prompt-coupled Pass 2 was
JSON-schema-fixed; we keep the TOC engine, drop the old Pass 2 in favour of the HTML path.

## 2. Architecture

```
Stage 1 — DISCOVER (universal)        → writes document + table_t rows to the DB (the manifest)
  ├─ TOC engine     (adopt PASS1_TOC.py, ZERO-API)     when a TOC enumerates tables
  ├─ DETECT engine  (pdfplumber + MinerU layout, OFFLINE/zero-token)  always (FS) / verifier (Pillar 3)
  └─ RECONCILE      classify every page; stitch continuations; flag gaps; classify table_type vs DB
Stage 2 — EXTRACT   per table_t row → HTML (gemini_extract.py; known type → customised prompt)
Stage 3 — ENRICH+LOAD  html_to_cells.py → schema_v5 (derive parents/concept_key); write DB
```

**The manifest is the DB, not a file.** Per Plan 9 §5.2, discovery writes the `document` row +
one `table_t` row per discovered table **before** any extraction → resumable (fail on table 17 ⇒
1–16 already loaded), whole-doc visible upfront. No `manifest.json` artifact. A `--dry-run` mode
previews the would-be `table_t` rows + coverage report without committing (cheap eyeball).

**Detection is offline (zero Gemini tokens).** pdfplumber + MinerU layout detect table regions;
Gemini is spent only on Stage-2 extraction. Cheap-vision Gemini detect is a last-resort fallback
for pages both miss. (MinerU is evaluated for detection here AND table→HTML in the MinerU spike.)

**Doc-type-agnostic:** the manifest is the contract. Pillar 3 vs FS differ only in which
discovery engines fire; Stages 2–3 are identical.

## 3. Decisions (user, 2026-06-29)
- **MinerU is the single Stage-1 engine** (validated — see
  [findings/2026-06-29-mineru-replaces-pdfplumber-toc]). It gives BOTH the section tree
  (matched ~64/64 on DBS 4Q25 Pillar 3, parts + dotted numbering + page) AND per-page table
  detection (29/29 on the no-TOC DBS FS) — fully offline, zero LLM tokens. It therefore
  **replaces pdfplumber's TOC pass AND the old Gemini per-page table-count.**
- **pdfplumber** is demoted to a fast fallback / sanity cross-check (and the deterministic
  pre-filter remains as a cheap signal). **Gemini is removed from Stage 1.**
- **Gemini stays for Stage-2 extraction only** — MinerU flattens cell hierarchy (no
  `data-level`, no shading), which `schema_v5` needs.
- **The manifest is the DB**, not a `manifest.json`: discovery writes `document` + `table_t`
  rows before extraction. `--dry-run` previews them + coverage without committing.
- **table_type is a DB reference:** discovery matches title/structure against the DB's
  `table_type` table; **known** → its stored `row_template`/`col_template` build a customised
  Stage-2 prompt (clean, fast HTML); **new** → blind extract, then its dimensions populate the
  reference and it becomes known. No separate catalog file.

## 4. Components

### 4.1 `discover/toc.py` (adapt PASS1_TOC.py)
Deterministic, zero-API. Output: `sections[] = {section_no, title, start_page, part?}` +
`has_usable_toc: bool` (false for FS → page-scan carries discovery).

### 4.2 `discover/detect.py` (offline detector — the completeness engine)
- `prefilter(page)` — pdfplumber signal per page: ruled-line count, `find_tables()`, and a
  borderless heuristic (≥N rows of right-aligned numeric tokens in aligned x-columns).
  Returns `{has_tabular: bool, score, regions[]}`. Cheap, offline; skips empty/prose pages.
- `mineru_layout(pdf)` — MinerU layout pass → per page, region list incl. `table` bboxes +
  nearest title/heading region. Offline, zero-token. (Pluggable: validated by the MinerU spike;
  pdfplumber-only works as a fallback today.)
- Output per page: `tables[] = {title, bbox, continues_prev?}`. NO Gemini here.

### 4.3 `discover/reconcile.py`
- Merge TOC sections + detected tables into table **units** (stitch continuations by
  title / continues_prev / climbing row numbers).
- Assign section context (TOC if present, else detected heading).
- Pillar-3 cross-check: TOC sections ⟷ detected tables; emit mismatches to `review_queue`.
- Coverage: classify every page ∈ {has_table, continuation, empty}; any unclassified ⇒ flag.
- `table_type`: match title/structure vs the DB `table_type` reference → known type id or `new`.

### 4.4 `discover.py` (entrypoint) — writes the manifest INTO the DB
`discover(pdf, db, dry_run=False)`: builds the table units, then writes one `document` row +
one `table_t` row per unit (`title, pages, section?, table_type, source, continued, flags`).
With `dry_run=True` it prints those rows + a coverage report and commits nothing. The DB rows
ARE the manifest (Plan 9 §5.2); there is no JSON artifact.
Coverage report: `{pages_total, with_table, continuation, empty, flagged}` + `review_queue[]`.

### 4.5 `orchestrate.py`
`run(pdf)`: discover → write `document` + `table_t` rows → for each table: pick framing
(SINGLE/SPANNING/MULTIPLE) + known-template modifier → `gemini_extract` (HTML) →
`html_to_cells` → load `col_dim/row_dim/cell_fact` (parents before children). Resumable;
honours Plan 9 block-count + period-coverage halts.

## 5. Scope
**In:** universal discovery + manifest + orchestration skeleton; run end-to-end on ONE Pillar-3
doc and ONE financial-statement doc; coverage report.
**Out (YAGNI for now):** concept-review UI loop; template auto-creation; geo dimension; the
MinerU backend (separate spike); multi-doc batch. Stage-4 template reconciliation is stubbed
(provisional_table_type only).

## 6. Success criteria
- Pillar-3 doc: manifest matches the TOC, page-scan cross-check adds/finds nothing missed
  (or flags it); every page classified.
- FS doc (no TOC): page-scan alone yields a non-empty manifest covering the statement tables;
  coverage report shows every page classified; continuations stitched.
- Both feed Stage 2→3 and land cells in `schema_v5` for at least a sample of tables.

## 7. Risks
- Borderless-table recall (FS) — the hybrid's vision step is the safety net; tune the
  pdfplumber prefilter to over-include (false positives are cheap; misses are not).
- Vision detect cost on long FS (131 pp) — prefilter must cut prose pages; detect-only keeps
  output tiny. Measure cost on the OCBC 131-page FS.
- Continuation stitching across page breaks — reuse Plan 9 rules (columns resume, no new title).
- 3.5-flash latency/503 — batch detect calls, backoff.

## 8. Build order
1. `discover/toc.py` — port PASS1_TOC, expose `has_usable_toc`. Test on a Pillar-3 doc (zero-API).
2. `discover/pagescan.py` prefilter (pdfplumber, offline). Test recall on the FS first pages.
3. `prompts/stage1_detect.txt` + `vision_detect`; wire hybrid. Measure cost on FS.
4. `reconcile.py` + `discover.py` → manifest + coverage on one Pillar-3 + one FS doc.
5. `orchestrate.py` end-to-end into `schema_v5` for a sample.
