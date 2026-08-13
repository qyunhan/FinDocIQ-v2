# Spec: Pillar-3 full-PDF extraction (v1 — assemble the pipeline)

**Date:** 2026-06-29
**Status:** Design — pending user review
**Owner:** yunhan
**Related:** `FinDocIQ_Plan_9.docx` (§2 pipeline, §4 templates, §5 orchestration, App. A prompts);
`docs/specs/2026-06-29-universal-stage1-discovery-design.md` (the broader universal-discovery spec
this narrows); `schema/schema_v5.sql`.

## 1. Goal & milestone

**v1 milestone (one sentence):** Point the pipeline at a **full Pillar-3 PDF** and land
**every table** into `schema_v5`, with **per-table prompt routing** driven by a seeded
Basel/MAS reference-template library, and a **batched new-table review** at the end of the run.

Why Pillar 3 first: it has a parseable TOC (discovery is zero-AI), it is the only doc type
where named regulatory table types (KM1, OV1, LIQ2/NSFR…) and known templates are meaningful
and testable, and it has **no MinerU dependency** — sidestepping the blocker owned by a
separate spike. Financial statements (no TOC → offline detector) are an explicit later milestone.

## 2. Current state (what we are assembling, not inventing)

The hard parts already work; they are trapped in a dated experiment folder:

| Plan 9 stage | Status today | Location |
|---|---|---|
| 2. Extract (PDF→HTML, Gemini) | ✅ works, real samples captured | `experiments/2026-06-29_mineru_eval/gemini_extract.py` |
| 3. Parse (HTML→cells) | ✅ 18/18 tests on two real models | `experiments/.../html_to_cells.py` |
| 3. Load (cells→DB) | ⚠️ proven end-to-end but demo-grade (bugs §6) | `experiments/.../load_to_db.py` |
| `schema_v5` star schema | ✅ designed, loads clean | `schema/schema_v5.sql` |
| Stage-2 prompts (core + framings + known modifier) | ✅ surfaced verbatim from Plan 9 | `pipeline/prompts/` |
| 1. Discover (full-PDF → manifest) | ❌ spec only | — |
| Routing (classify + known-table modifier) | ❌ manual `--framing` flag only | — |
| 4/5. Orchestration (multi-table, resumable, gate) | ❌ not built | — |

**Diagnosis:** this is a validated spike, not a broken extractor. v1 = promote the proven code
into a real `pipeline/` package, build the discovery front-end and the orchestration spine
around it, fix the load-path bugs, and add the routing + review loop.

## 3. Architecture

### 3.1 Package layout

```
pipeline/
  prompts/              # EXISTS — stage2_core.txt, stage2_framings.txt
  extract.py            # PROMOTE from experiments/gemini_extract.py (Stage 2)
  html_to_cells.py      # PROMOTE from experiments/ (Stage 3 parse) — tests come too
  enrich.py             # NEW — Stage-3 enrich: unit assignment, parent/level finalisation
  load.py               # REWRITE of load_to_db.py (Stage 3 load) — additive, multi-table
  templates/
    library/            # NEW — curated reference templates: NSFR.yaml, KM1.yaml, OV1.yaml…
    candidates/         # NEW — auto-drafted templates for NEW tables, awaiting review
    seed.py             # NEW — library/*.yaml → DB row_template/col_template (runtime cache)
    schema.py           # NEW — load/validate a template YAML; render the EXPECTED-ROWS block
  discover/
    toc.py              # NEW — TOC tree → sections[]/units[] (engine-pluggable). zero-AI
    classify.py         # NEW — title/code → table_type, matched against the library
    reconcile.py        # NEW — map tables to sections/pages; framing per unit; period(s)
  orchestrate.py        # NEW — spine: discover → write manifest → per-table route+extract+load
  review.py             # NEW — `findociq review`: approve/edit/reject candidates → library
  cli.py                # NEW — `findociq run <pdf> [--dry-run] [--fresh]`, `findociq review`
```

Boundaries (each independently testable): **discovery** is zero-AI and emits a manifest;
**classification** is pure string/structure matching against the library; **extraction** is
the only AI call; **parse→enrich→load** is deterministic.

### 3.2 Data flow (one full PDF)

```
PDF ─▶ discover/toc.py ─▶ units[] (title, code, pages, section)
         ├▶ classify.py:   code "LIQ2"/"NSFR" → table_type='nsfr' (known) | 'new'
         ├▶ reconcile.py:  framing per unit (SINGLE/SPANNING/MULTIPLE) + period(s)
         ▼
   write document + table_t rows  ── the MANIFEST = the DB (Plan 9 §5.2); resumable checkpoint
         ▼  for each table_t row:
   extract.py:  core + framing + (if known) EXPECTED-ROWS modifier  ─▶ HTML
         ▼
   html_to_cells.py ─▶ enrich.py (units, parents) ─▶ load.py ─▶ schema_v5
         ▼
   completeness gate: block-count + period coverage; shortfall → halt+flag (never load short)
         ▼  (end of run)
   review report: NEW tables → candidate YAMLs queued for `findociq review`
```

## 4. Stage 1 — Discovery (zero-AI, engine-pluggable)

`discover/toc.py` adopts the legacy `PASS1_TOC.py` engine and exposes:
`discover_toc(pdf) -> {sections: [{section_no, title, start_page, level, parent_section}],
units: [{title, code?, section_no, page_range}], has_usable_toc: bool}`.

The **TOC engine is pluggable behind this contract.** v1 uses the pdfplumber/bookmark-based
PASS1_TOC port. A separate session is evaluating MinerU layout as an alternative TOC-tree
builder; if it wins, it swaps *inside* `toc.py` only — nothing downstream changes.

`discover/reconcile.py`:
- Build table **units** from sections (one unit per disclosure table; stitch continuations by
  title / climbing line numbers / "columns resume, no new heading" — Plan 9 SPANNING rule).
- Assign each unit its section context, page-range, and **period(s)** (from the section
  sub-header date, e.g. "As at 31 Dec 2025"; a unit may advertise several periods).
- Choose **framing** per unit from geometry: one table on one page → `SINGLE`; spans a page
  range → `SPANNING`; one page carrying tables from multiple sections → `MULTIPLE`.

**Period & date-blocks (user decision):** different reporting dates are **different tables,
never merged** — two NSFR date-blocks in a 4Q doc become **two units → two `table_t` rows**.

## 5. Stage 2 — Routing + extraction

### 5.1 `classify.py` — title/code → table_type
Match each unit's code/title against every library entry's `codes` + `title_patterns`
(case-insensitive, normalised). Hit → `(table_type=<entry>, known=True)`; miss →
`(table_type='new:'+slug(title), known=False)`. Pure, zero-AI, fully unit-testable.

### 5.2 Prompt routing in `orchestrate.py` (per `table_t` row)
1. **Framing** from the manifest geometry (SINGLE/SPANNING/MULTIPLE).
2. **Known-table modifier:** if `known`, `templates/schema.py` renders the `EXPECTED ROWS`
   block from the matched template's `rows` (the modifier shape already exists in
   `stage2_framings.txt`) and appends it. If `new`, blind extract (framing only).
3. Compose `core + framing + [expected-rows]` and call `extract.py`.

### 5.3 Multi-table chunking (Plan 9 §5.4a)
For a section with many blocks whose combined HTML risks exceeding `max_output_tokens`
(e.g. an asset-class matrix), the orchestrator chunks the page range so each call's output
fits, then stitches — carrying prior-chunk tables forward in code, not in the prompt.
v1 ships a simple size heuristic; the completeness gate (§8) is the safety net.

### 5.4 `extract.py`
Promoted `gemini_extract.py`: `gemini-3.5-flash`, `temperature=0`, PDF as a native
`application/pdf` Part, `response_mime_type="text/plain"`, 503-backoff. The `--framing`
flag becomes an orchestrator-supplied argument, not a hand-set CLI option.

## 6. Stage 3 — Enrich + Load

### 6.1 `enrich.py` (deterministic clean-up, Plan 9 §5.5)
- **Unit assignment** (fixes the hardcoded-`S$m` bug): template `unit` override → section-band
  label override (e.g. "Key Financial Ratios (%)" → `%`) → non-monetary regex
  (`%|ratio|number|employees`) → table default. So headcount/%/ratio rows never sum into S$m.
- **Parents/levels:** `row_parent` = nearest earlier row at level−1 (already in the parser);
  line-number suffix (`4a`→`4`) as a hierarchy cue; unlabelled-total synthesised label.

### 6.2 `load.py` (rewrite of `load_to_db.py`) — bug fixes
| Bug today | Fix |
|---|---|
| `fresh_db()` wipes the DB every run | Open existing DB; additive `INSERT`s; `--fresh` opt-in only; idempotent per `doc_id` (re-run replaces that doc's rows). |
| `unit="S$m"` hardcoded on every row/col | Take unit from `enrich.py` (§6.1). |
| `concept_key`/`geo_key` never set | v1: store verbatim label + `line_no`, leave `concept_key` NULL (deferred — §10). Loading does not need it. |
| one hardcoded title/type for all tables | Per-`table_t` title/type/period from the manifest. |

**Storage = per-instance (user decision, overrides Plan 9 §4.1):** every table instance writes
**fresh** `row_dim`/`cell_fact`. The template shapes the *prompt*, never the *stored rows*; row
drift across periods/banks is preserved by storing both faithfully, not reconciled to a shared
skeleton. Cross-period/cross-bank joins are a later analytics concern (by label/`concept_key`).
FK-safe order: `document → table_t → col_dim → row_dim → cell_fact`.

## 7. Reference-template library

One YAML per Basel/MAS table type, curated **once** from the regulator's published format
(authoritative structure, not guessed). The YAML is the **source of truth**; `seed.py` loads it
into the DB `row_template`/`col_template` tables as the **runtime cache** the prompt-builder reads.

```yaml
# library/NSFR.yaml   (Basel LIQ2 / MAS Notice 637)
table_type: nsfr
codes: ["LIQ2", "NSFR"]            # classifier match keys (as labelled in docs)
title_patterns: ["net stable funding ratio"]
unit_default: "S$m"
rows:                              # the authoritative EXPECTED-ROWS list (PROMPT-ONLY)
  - {line_no: "1",  label: "Capital", level: 1}
  - {line_no: "2",  label: "Regulatory capital", level: 2}
  # … full standard row list …
  - {line_no: "34", label: "Net Stable Funding Ratio (%)", level: 1, unit: "%"}
```

**Starter set for v1: NSFR + KM1 + OV1** — enough to prove routing across multiple known types.
Everything else falls through to blind extract and still loads.

## 8. New-table review loop (minimal, file-based)

The full FastAPI/web review system stays out. v1 ships a lightweight, batched version:

- During a run, a `new` table is **extracted blind, loaded normally** (`table_type='new:<slug>'`),
  and a **candidate template** is drafted from its extracted col/row structure into
  `templates/candidates/<slug>.yaml` (pre-filled, ready to edit). The candidate is recorded in
  the run's review report.
- **The run never pauses** (user decision: "at the end of the pdf run, don't break it halfway").
  Resumable and unattended.
- `findociq review` is a **separate, deliberate step**: for each candidate, show extracted
  structure → `[a]pprove / [e]dit / [r]eject`. Approve moves it to `library/` and re-seeds →
  the type is `known` on the next run.

## 9. Completeness gate (anti-silent-omission, Plan 9 §5.6)

After extracting a section: assert `extracted_block_count == expected` (from TOC/known
template/size-probe) and that **every advertised period is represented**. Shortfall →
**halt + flag** into the run's `flagged[]` report; never load a short section silently. This is
the only check that catches "returned 1 of 14 blocks, wrong period, no error."

## 10. CLI

- `findociq run <pdf> [--dry-run] [--fresh] [--db PATH]`
  - `--dry-run`: discovery only — print the manifest (`table_t` rows) + coverage report,
    **spend zero tokens**. The fast eyeball loop.
  - default: full run discover→extract→load + completeness gate; emits a run report
    (`{pages_total, with_table, continuation, empty, flagged[], new_candidates[]}`).
- `findociq review`: process queued candidate templates.
- `findociq seed`: (re)load `templates/library/*.yaml` into the DB.

## 11. Testing

- **Unit (pure, no AI), TDD:** `classify.py` (code/title→type), `enrich.py` (unit assignment
  precedence), `toc.py` (TOC parse on a real Pillar-3 doc), `templates/schema.py` (render
  EXPECTED-ROWS), `seed.py`.
- **Carried intact:** the `html_to_cells` 18/18 tests move with the file.
- **Integration (zero-token):** `findociq run <pillar3.pdf> --dry-run` asserts the manifest +
  coverage on one real doc.
- **End-to-end (real Gemini, one doc):** full run landing cells in `schema_v5`, asserted by
  query-back (e.g. NSFR% retrievable as a cross-period series within the doc).

## 12. Out of scope (YAGNI for v1)

- FS / no-TOC detection, MinerU integration, vision-detect fallback (separate session).
- `concept_key`/`geo_key` assignment + geo dimension + rollups.
- The FastAPI/web review UI and template **auto-creation**/drift reconciliation beyond the
  file-based candidate flow (§8).
- Multi-doc batch; Postgres/BigQuery (SQLite only).
- Cross-period/cross-bank analytics queries (the DB will support them later via `concept_key`).

## 13. Build order

1. **Promote** `extract.py` + `html_to_cells.py` (with tests) into `pipeline/`; green tests.
2. **`load.py` rewrite** + `enrich.py`: additive, multi-table, unit assignment. End-to-end on
   the existing NSFR HTML sample → DB (re-prove the path through the new modules).
3. **Template library:** YAML schema + `seed.py` + `templates/schema.py` (render modifier);
   author NSFR/KM1/OV1. Unit tests.
4. **`classify.py`** against the library. Unit tests.
5. **`discover/toc.py`** (port PASS1_TOC) + **`reconcile.py`** (units, framing, periods).
   `--dry-run` manifest on one real Pillar-3 doc.
6. **`orchestrate.py`** spine: manifest → route → extract → enrich → load + completeness gate.
   One full end-to-end run.
7. **`review.py`** + `findociq review`: candidate → library promotion.

## 14. Open items to confirm during build

- Exact authoritative row lists for KM1/OV1 (NSFR row list already proven from the sample).
- Continuation-stitch reliability on real multi-page Pillar-3 sections (reuse Plan 9 rules).
- Size-probe threshold for §5.3 chunking (start conservative; gate catches misses).
- `--fresh` vs idempotent-replace default semantics for re-running the same `doc_id`.
