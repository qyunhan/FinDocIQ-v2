# PaddleOCR Stage-2 spike — design (2026-07-07)

**Status:** approved design, pre-implementation. **Binding for the implementation plan.**
**Amended 2026-07-08 (user request, execution session):** T4 region-detection test +
Gate 4 added — PaddleOCR as `region_source` candidate replacing the router's dead MinerU
branch. Corpus gains UOB 4Q25 §12.9 as a registry-only third document.

## Question

Can PaddleOCR PP-StructureV3 (geometry-based table structure recognition) replace or
reduce Gemini in Stage 2 extraction — and can it capture table titles well enough to
build a document TOC (the job that killed MinerU)?

## Motivation

- **Merge Perception Floor** (finding, 7+ controlled arms): the Gemini API cannot see
  merge/shade structure regardless of prompt/workflow/doc. Geometry authors structure;
  an LLM reads text. PP-Structure reads geometry.
- Gemini 3.5-flash is a capacity/latency liability (intermittent 503s) and its NSFR HTML
  is **known incorrect** (user verdict 2026-07-07) — it cannot even serve as ground truth.
- MinerU was dropped entirely (2026-07-02): unreliable at counting/titling tables
  (caption bleed). The TOC test asks whether PaddleOCR fails the same way.

## Decision this spike produces

Per table class (ruled / borderless): **drop / reduce / keep Gemini** in Stage 2.
Separately: a TOC-capability verdict for discovery. Separately (added 2026-07-08): a
**region-detection verdict** — does Paddle qualify as the router's `region_source`
candidate for the borderless branch (replacing the dead MinerU stub,
`pipeline/route/scan.py::_mineru_detect` → always `(None, "pending")`)?
This is an EXPERIMENT — no routing
change ships with it. If the verdict is "adopt", that is a subsequent routing branch +
spec + route_map visibility, announced as a pipeline pivot per CLAUDE.md.

## Test matrix

| # | Capability | Corpus | Ground truth |
|---|-----------|--------|--------------|
| T1 | Stage-2 cells, **ruled** render | DBS 4Q23 Pillar 3 (`data/sources/pillar3/DBS_4Q23_Pillar3.pdf`), NSFR pages | `GT_dbs_4q23_p3.csv` (repo root, 288 cells, period 2023-12-31) |
| T2 | Stage-2 cells, **borderless** render | OCBC 4Q24 Pillar 3 (`OCBC_4Q24_Pillar3.pdf`), NSFR pages | `GT_ocbc_4q24_p3.csv` (repo root, 284 cells, period 2024-12-31) |

NSFR page ranges are located deterministically from the printed TOC (PASS1_TOC output),
not hardcoded — the same mechanism any future doc would use.
| T3 | **TOC building**: capture every table title + page across the full document | both PDFs, full doc | printed TOC via PASS1_TOC (legacy `toc.json` outputs believed to exist for all 12 quarterlies — VERIFY at implementation start; if absent, regenerate with the PASS1_TOC port) |

T1 vs T2 covers both render styles of the same MAS 653 template (DBS prints it fully
ruled; OCBC prints it borderless — per-bank render-style finding, 2026-07-02). T2 is the
differentiator: no drawn rects, so structure must come from Paddle's grid inference.

| T4 | **Region detection** (Stage-1 candidate, added 2026-07-08): find WHERE tables are, not what's in them | T4a: UOB 4Q25 `UOB_4Q25_Pillar 3.pdf` §12.9 pp38–41 (ruled; located from printed TOC by section_id, same mechanism as NSFR). T4b: OCBC 4Q24 NSFR table pages (borderless main + 4 decoy strips). T4c: every page labeled NO_TABLE in the existing route manifests (`pipeline/route/out/*_route*.json`) for the two full-capture docs | T4a: pdfplumber `find_tables()` bboxes — known-true 10/4/10/4 = 28 (cross-confirmed in `route/out/UOB_4Q25_Pillar 3_route.json`, num_cov ≈ 0.97/page). T4b: the router's own coverage machinery as referee (`scan.py` `NUM` regex + `_in_bbox` center rule — IMPORTED, never reimplemented). T4c: the route manifests' NO_TABLE labels |

T4 tests detection, not extraction: the router's borderless branch has no working region
source (`_mineru_detect` is a stub; `_numeric_edge_precheck` is defeated by OCBC's decoy
strips — 844 stray edges, 19 noisy clusters per the fragment-reconciliation spec). T4b is
therefore the real prize; T4a proves parity where pdfplumber is already truth; T4c proves
the detector doesn't hallucinate regions on prose pages (which would poison routing).

## Ground-truth format

The GT CSVs are the schema-v7 `v_cell_flat` melted shape (user-provided "flat view to
make testing easier"): `institution, period, table_type, table_title, section_no,
line_no, row_lvl1..row_lvl5, row_depth, col_lvl1, col_lvl2, col_depth, value_num,
value_raw, cell_state, is_shade, colspan, concept_key, geo_key, row_header_id,
col_header_id, doc_id, table_id, row_id, col_id, row_hierarchy`.
Schema v7 = v5 + header-lineage registries (`findociq/schema/schema_v7.sql`, renamed
from schema_v6.sql 2026-07-07; global row_header/col_header lvl1..5 lineage, FK ids on
cell_fact, `v_cell_flat` view).

## Architecture (approach A′ — markdown container)

```
PDF page-range
  → run_paddle.py        PP-StructureV3 (.venv-paddle) → outputs/<doc>/:
                         markdown (human-readable container) + raw JSON (cell bbox, spans)
  → md_tables.py         extract the <table> HTML blocks embedded in the markdown
                         (pure markdown cannot express rowspan/colspan — PP-Structure
                         embeds HTML for tables, same as MinerU did) + dialect adapter
                         to the HTML html_to_cells tolerates
  → html_to_cells.py     REUSED from experiments/2026-06-29_mineru_eval (hardened,
                         18/18 tests) → cells (schema shape)
  → flatten.py           cells → v_cell_flat-shaped rows (row_lvl1..5 / col_lvl1..2
                         computed from parent chains; no DB round-trip needed)
  ├→ cells_to_xlsx.py    Excel verification view: one .xlsx per table with REAL merged
  │                      cells + shading, generated FROM the parsed cells (what you see
  │                      in Excel is byte-for-byte what scoring/loading sees)
  ├→ load into paddle_eval.db  built from schema_v7.sql — proves DB loadability and
  │                      exercises v7's header-lineage derivation. NEVER touches final.db.
  → score_cells.py       Gate 1 + Gate 2 (below)
  → score_toc.py         Gate 3
  → scorecard.md         verdicts
```

Key comparison property: the diff is flat-to-flat on identical column sets, joined on
`(line_no, col_lvl1, col_lvl2)` — deterministic, no hierarchy walking.

## Gates

**Gate 1 — cell parity (T1, T2).** Every GT row must be matched. Mismatches are
classified:
- `STRUCTURE` — wrong lineage levels, missing/extra rows or cols, wrong line_no order,
  colspan, cell_state, or is_shade. **Pass bar: zero structural mismatches** (the DB is
  structure; a structural error poisons everything downstream).
- `TEXT` — right cell, wrong characters (OCR noise in value_raw or labels).
  Auto-adjudicated against pdfplumber's words for the same page region (text PDFs ⇒
  pdfplumber text is exact); tallied with a note on geometry+pdfplumber-fusion
  fixability. TEXT errors do NOT fail Gate 1 but are reported.

**Gate 2 — geometry (T1, T2).** Paddle raw cell bboxes/spans vs the GT merge set
(colspan anchors). **Pass bar: the Paddle-derived merge set equals the GT merge set
exactly** (same anchors, same spans). Shading: `is_shade` truth comes from GT;
pdfplumber drawn-rect overlay remains the shade authority for ruled pages (Paddle does
not read fill; for borderless pages there are no rects, so shading is out of Paddle's
scope by design and excluded from Gate 2).

**Gate 4 — regions (T4, added 2026-07-08).**
- **T4a (ruled parity):** greedy 1:1 IoU matching of Paddle table regions vs pdfplumber
  `find_tables()` bboxes on the 4 §12.9 pages. **Pass bar: 28/28 matched at IoU ≥ 0.5,
  zero unmatched regions on either side**; mean IoU reported for quality.
- **T4b (borderless — the real prize):** per OCBC NSFR table page, Paddle must emit
  **exactly one** table region, and that region must contain **≥ 95% of the page's
  numeric tokens** (numeric = `scan.py` `NUM` regex; containment = `_in_bbox` word-center
  rule — the router's own coverage machinery imported as referee). The 4 decoy strips
  must not surface as regions.
- **T4c (false positives):** every route-manifest NO_TABLE page of the two full-capture
  docs yields **zero** Paddle table regions.
- Verdict: all three pass → Paddle is the `region_source` candidate for the router's
  borderless branch. Wiring it is a subsequent routing branch + spec + route_map
  visibility (announced as a pipeline pivot) — NOT part of this spike.

**Gate 3 — TOC (T3).** Precision/recall of captured table titles vs printed-TOC
entries + page attribution. Title matching is by deterministic normalization first
(casefold, whitespace collapse, strip leading section numbers), then
`difflib` ratio ≥ 0.9 for near-misses — near-misses are reported, not silently
accepted. Explicit caption-bleed counter: titles polluted by adjacent prose or glued
captions (MinerU's failure mode). Verdict: can PaddleOCR-sourced titles build a TOC for
documents with no printed TOC?

## Environment

- Isolated `.venv-paddle`: paddlepaddle (CPU, arm64) + paddleocr 3.x. Versions PINNED
  and recorded in the finding doc (MinerU numpy/TF ABI lesson).
- Zero Gemini tokens anywhere in the spike.
- Runs from repo root; artifacts under `findociq/experiments/2026-07-07_paddleocr_eval/`.

## Error handling

Fail-loudly (repo convention): empty PP-Structure output → exception, not skip; a page
with no detected table where GT expects one → scored as STRUCTURE failure, not silently
absent; every stage persists artifacts under `outputs/<doc>/` so runs are resumable and
each intermediate is inspectable.

## Risks

1. **arm64 wheel availability / install pain.** Mitigation: pin known-good versions;
   isolated venv; worst case x86 wheels under Rosetta. Install is the first plan task —
   fail fast before any code.
2. **PP-Structure HTML dialect distance** from what html_to_cells tolerates.
   Mitigation: md_tables adapter with its own check-style tests (mirror
   test_html_to_cells.py pattern).
3. **OCR text quality** (Paddle re-reads text pdfplumber already has exactly).
   Contained by design: TEXT mismatches are classified separately and pre-identified as
   fixable by geometry+pdfplumber fusion (the follow-up architecture if structure passes
   but text doesn't).
4. **toc.json availability** for T3 ground truth — verify first; regenerate via the
   PASS1_TOC port if missing.

## Deliverables

`findociq/experiments/2026-07-07_paddleocr_eval/`: `run_paddle.py`, `md_tables.py`,
`flatten.py`, `score_cells.py`, `score_toc.py`, `score_regions.py` (Gate 4, added
2026-07-08), `cells_to_xlsx.py`, `outputs/`,
`paddle_eval.db`, `scorecard.md`; finding doc under `docs/findings/`. Workflow diagram
already in `docs/diagrams/2026-07-07-pipeline-workflows.md` (§2) — update it if the
design shifts during implementation.

## Constraints (inherited, non-negotiable)

- NO git commits (owner batches manually).
- Never touch `final.db`.
- No per-bank/per-doc conditionals in any code that could graduate to the pipeline —
  the adapter must be dialect-general, not "if OCBC".
- Tests = plain `check(name, cond, got)` scripts, no pytest.
