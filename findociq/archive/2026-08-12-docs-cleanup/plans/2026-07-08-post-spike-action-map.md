# Post-spike action map — what each PaddleOCR gate outcome triggers (2026-07-08)

**Status:** decision map, written while the spike executes. Consumed AFTER `scorecard.md`
exists. Each branch that fires is a **pipeline pivot** per CLAUDE.md: spec + route_map
visibility + explicit callout. Nothing here ships during the spike.

## How we use PaddleOCR accurately (the division of labor the spike tests)

PaddleOCR is used ONLY for what geometry can author and Gemini provably cannot see:

| Job | Authority | Why |
|---|---|---|
| Where cells are, how they span (grid, colspan) | **PP-StructureV3** | geometry-based TSR; attacks the merge-perception floor |
| What's in the cells (values, labels) | **pdfplumber** (text PDFs are exact) | Paddle re-OCRs text it doesn't need to; fusion by bbox containment kills OCR noise |
| Row hierarchy (indent levels) | **pdfplumber** x0 clusters | Paddle HTML has no data-level |
| Shading | **pdfplumber** fill rects (luminance-relative) | Paddle doesn't read fills |
| Coordinates | we render at 200 DPI ourselves → px·72/200 = pt exactly | deterministic fusion, no rasterizer guessing |
| Stitching fragments → period tables | deterministic rule (period change OR line_no restart) | never captions, never a model |

Gemini's remaining role is decided per table class by the gates below.

## Gate → action

**Gate 1+2 PASS on ruled (T1, DBS):**
→ Routing branch: `BORDERED_*` units extract via Paddle path (zero tokens); Gemini
dropped for ruled tables. Port spike components into `pipeline/`:
run_paddle → md_tables adapter → overlay → SAME html_to_cells → SAME loader → SAME
post-load gate (extractor-agnostic; also adjudicates Paddle TEXT errors).
New spec + route_map shows `extractor: paddle|gemini` per unit.

**Gate 1+2 PASS on borderless (T2, OCBC):**
→ Same branch for `BORDERLESS_MAIN`/`BORDERLESS`; template_cell demoted from structure
*authority* to structure *cross-check* on borderless renders.

**STRUCTURE passes, TEXT fails (pre-identified likely outcome):**
→ Not a failure. Trigger the fusion follow-up: Paddle grid + pdfplumber tokens
assigned by bbox containment; value text never comes from OCR.

**Gate 4 PASS (T4 regions — scored per class):**
→ Paddle becomes `region_source` for `BORDERLESS_MAIN` (replaces the dead MinerU
branch; kills `region_source: "pending"`), and region bboxes enable the never-built
per-fragment CROP for Stage-2 input (today: whole page + prompt anchor).
Gate 4 can pass even if Gate 1 fails → Paddle as detector-only is a valid reduced verdict.

**Gate 3 PASS (TOC titles):**
→ Discovery branch for docs with NO printed TOC (financial statements):
PASS1_TOC stays primary where a printed TOC exists; Paddle replaces the Gemini
whole-doc TOC fallback. Caption-bleed counter must beat MinerU's failure mode.

**Any gate FAIL:**
→ Gemini stays for that class. Priority flips to defending Gemini's structure:
wire V2 (HTML colspans/shading vs merge_map / template_cell — the structure_authority
consumer that nothing reads today), and keep the KNOWN-TABLE modifier on the shelf.

## Sequenced plan after scorecard.md (integrates the legacy-port findings, 07-08)

1. **Verdict + spec.** Read scorecard → drop/reduce/keep per class → write the routing
   spec, announce the pivot, update route_map + management diagrams.
2. **extract_run safety port-backs** (needed wherever Gemini remains, tiny):
   MAX_TOKENS finish-reason guard (legacy extract.py:538-543 — truncated response
   currently loads as complete) + `--from-html` prompt-hash sidecar validation
   (legacy extract.py:427-462).
3. **Manifest builder** — needed regardless of spike outcome (the missing stage):
   port legacy `build_units`/`group_key`/`next_leaves` (extract.py:799-828) +
   `detect_bank`/`derive_period` (render.py:28-83, fingerprints → registry).
   Consumes scan.py route JSONs; kills the hand-built manifest and `--only nsfr`.
4. **Wire the winning Paddle paths** from step 1 (extractor branch, region_source,
   crops, fusion), each as its own routing branch + route_map field.
5. **Fleet-level QA ports** (cheap, zero tokens): duplicate-table detection
   (transforms.py:180-203, 479-526) + duplicate-row-label check → verify stage.
6. **Re-run the NSFR fleet** through whatever changed; post-load gate must stay 18/18.
   Then first KM1/LCR instances (templates already seeded) — the first non-NSFR
   passengers through the full pipe.
7. **Shelf (when their routing case first fires):** route_tables (multi-section pages),
   split_date_blocks (merged two-period tables), MULTIPLE framing.

## Immediate blocker (Task 1)

`outputs/smoke/` has the PNG only — no persisted result JSON/markdown. Task 3's adapter
is written against the smoke dialect report (real keys for `pred_html`/`cell_box_list`,
save_to_json dir-vs-file convention, thead/colspan presence). Until the smoke completes
and reports those five facts, everything downstream is speculative.
Known machine facts so far: chart-recognition VLM segfaults, formula recognizer
SIGBUSes (both disabled — scoped, documented, not needed for financial tables).
