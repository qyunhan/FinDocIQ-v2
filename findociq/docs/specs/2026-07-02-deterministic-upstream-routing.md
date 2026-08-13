# Deterministic upstream: route → extract → verify → load (no human, no reference tables)

**Date:** 2026-07-02
**Premise (boss's, proven correct):** if numbers + structure reach the DB correctly, everything
downstream is deterministic. `html_to_cells → schema_v5` verified lossless on OCBC NSFR
(merges, shading, header groups all landed).
**Gap (proven this week):** upstream. One-by-one worked because a human silently did two jobs —
picked the framing per table, and eyeballed/retried bad output. At scale those roles were
unfilled. Both are automatable deterministically, zero tokens.

## Evidence base (all verified 2026-06-30 → 07-02)

| Finding | Evidence |
|---|---|
| pdfplumber table count is the TRUE count on bordered pages | uob 12.9: 10/4/10/4 — confirmed exactly by MinerU (28 = 28) |
| Each counted region is a distinct table with its OWN columns | region dumps: Cash items (3 cols) vs Corporate (11 cols) |
| Section-per-page readable from running header, no TOC | "12.9 SA(CR) … (cont'd)" on all 4 pages, ~300ms/page |
| Wrong framing → silent number damage | framed 12.9 HTML: 97 PDF numbers missing, 68 phantom |
| Ruled grid encodes merges geometrically | NSFR row 12: dividers at x385,435 absent → cols 2-4 = one cell (colspan 3) |
| Gemini API got that merge wrong; DB loaded it faithfully | HTML: grey span2 + value span1; DB matches HTML, not PDF |
| Width validation cannot catch mis-split merges | wrong row 12 sums to the same width as the right one |
| Model families differ structurally | 2.5: 0 tbody colspans; 3.5: 20 (same page, same prompt) |
| MinerU: detection parity but minutes vs pdfplumber's 1.4s | 12.9 run; MinerU flattens cells so it cannot verify structure |

## Root problems in current code

1. No router — framing is a human CLI flag (`gemini_extract.py --framing`).
2. Discovery manifest (`table_t`) written but never consumed by any extractor.
3. Two disjoint Stage-2 input forms (whole native PDF vs per-page PNG), neither manifest-fed.
4. No verification: `html_to_cells` warnings unread; width check blind to same-width merge errors.
5. `auto_extract` silently falls back across model families with divergent structural dialects.
6. `thinking_budget=0` hardcoded (A/B pending on the NSFR merge).
7. Seed fixtures in `schema_v5.sql` (≈L285-299) pollute every fresh DB with empty demo tables.

## Architecture

```
STAGE 1 — ROUTE (deterministic, 0 tokens, ~350ms/page)
  pdfplumber per page:
    section_id   ← running header regex (dotted number + title + (cont'd))
    ruled[]      ← find_tables() bboxes         (bordered signal)
    bscore       ← aligned numeric right-edges  (borderless signal)
    merge_map    ← per ruled bbox: divider grid + missing internal edges
                   + grey fills (the geometric truth for colspans/shading)
  page class:
    BORDERED  (ruled ≥ 1)          → route per-table
    BORDERLESS(ruled=0, bscore ≥ 3)→ MinerU detect → then per-region
    NO-TABLE                       → skip
  unit build:
    same section + (cont'd) + same bbox column-signature across pages
      → continuation unit (spanning)
    N distinct bboxes on a page → N sibling units (never one call for all)

STAGE 2 — EXTRACT (Gemini, one SMALL call per unit)
  input: native-PDF page(s) cropped to the unit bbox (+small margin)
  prompt: stage2_core + framing chosen BY THE ROUTER
        + merge hints derived from merge_map (a few tokens, not reference tables)
  model: pinned single model (no cross-family fallback); thinking per A/B result

STAGE 3 — VERIFY (deterministic, 0 tokens)
  V1 numbers : every HTML number ∈ PDF token set within unit bbox, and coverage
               of PDF numeric tokens in bbox ≥ threshold (catches drop+invent)
  V2 merges  : every colspan/shade in HTML == geometric merge_map (catches row-12)
  V3 grid    : per-row cumulative colspan == ncols; leaf column count consistent
  fail → retry that unit only (same model, then thinking↑, then flag)
  pass → html_to_cells → schema_v5 (unchanged — already lossless)
```

## Delivery order

1. `pipeline/route/scan.py` — the Stage-1 scanner (signals above, JSON out) + routing
   decision; `route_map.html` visual (regenerated per run → "the mindmap").
   Acceptance: 12.9 → 28 units (10/4/10/4), all section 12.9, pages 2-4 continuations
   flagged; OCBC_NSFR → 2 units, merge_map shows row-12 colspan=3.
2. `pipeline/route/verify.py` — V1/V2/V3 against existing sample HTMLs.
   Acceptance: rejects framed 12.9 (V1) and gemini35 NSFR row 12 (V2);
   accepts the correct NSFR rows.
3. Wire Stage-2: manifest-fed per-unit extraction (crop + framing + hints), retry loop.
   Acceptance: 12.9 end-to-end → 28 tables in DB, V1 coverage 100%, zero human touches.
4. Remove seed fixtures from schema_v5.sql; pin model; set thinking per A/B.

## Non-goals

- Reference tables / known-table templates as accuracy crutch (verification replaces them;
  the KNOWN-TABLE modifier stays only as optional label guidance).
- MinerU as primary on bordered docs (fallback for borderless / no-header only).
