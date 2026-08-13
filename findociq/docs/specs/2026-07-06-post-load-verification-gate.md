# Post-load verification gate — no extracted cell persists unless its value re-appears verbatim in the source PDF text layer

**Date:** 2026-07-06
**Status:** decided. `pipeline/verify_cells.py` implemented + fleet-validated 2026-07-06;
wiring the gate into `extract_run.py` is the implementation task tracked from this spec.
**Pipeline pivot:** yes — adds a mandatory post-load stage to every extraction run
(live and `--from-html` replay). Called out to user 2026-07-06.

## Problem

LLM extraction has an irreducible hallucination floor. The merge-perception finding
(2026-06) established that Gemini reads text, not drawn table geometry; blank cells inside
merged/shaded regions are ambiguous to it. Concrete failure caught 2026-07-06: on DBS RSF
parent rows (Other assets, initial margin, NSFR derivative assets/liabilities, off-balance
sheet) the printed table leaves the ≥1yr **unweighted** cell blank; Gemini back-filled it
with a copy of the row's **weighted** value. 11 phantom cells across dbs_4q23 (6) and
dbs_4q25 (5). Quarter-inconsistent — dbs_4q24, same bank, same layout, is clean — so this
is sampling nondeterminism, **not fixable by prompting**. The defect exists in Gemini's raw
HTML artifact (verified: `route/out/extract/dbs_4q25_p3/85.html` prints `8,022` twice), so
every deterministic downstream step reproduces it faithfully. Until now nothing compared
loaded cells against the source document; defects persisted silently.

## Mechanism (already implemented: `pipeline/verify_cells.py`)

Zero-LLM, fully deterministic:

1. **Token reconstruction from raw glyphs** — `words_from_chars(page)`: drop blank glyphs,
   cluster `page.chars` into physical lines by `top`, split tokens where the gap
   `next.x0 − prev.x1` exceeds **0.5 × median glyph advance width of that page**. The
   threshold is derived from the page's own metrics (no magic constants, no per-bank
   branches); measured separation band on the fleet: intra-number gaps ≤ 0.62 pt vs column
   gutters ≥ 20.48 pt (~30× margin). This generally absorbs both observed text-layer
   pathologies (OCBC 4q23 letter-spaced layer; UOB/DBS first-digit splits).
2. **Tiered containment per row** — `line` tier (label anchors exactly one physical line;
   values found in anchor+next-line window) → `page` tier (multiset containment over the
   table's pages) → `fail` (value not on page ⇒ reported with row/label/raw).

## Evidence

Fleet run 2026-07-06 (9 docs, 18 tables, ~1,900 values): 16/18 tables verify with zero
missing; the only flags are the 11 phantom duplicates above — independently confirmed by
(a) cell-level classification of every residue, (b) the residue prediction of the
tokenization-fix validation, (c) raw-HTML inspection. False-positive rate after the
tokenizer fix: zero observed.

## Rule (the gate)

- An extraction run (live or `--from-html` replay) is **not COMPLETE for a doc until
  post-load verification passes** for every loaded table.
- Any table with `values_missing > 0` ⇒ the unit is **FLAGGED** (existing `extract_run`
  mechanism), the verify report JSON is persisted under `route/out/verify/`, and a row is
  queued for review — never silent persistence.
- The gate never auto-deletes and never auto-corrects; remediation is re-extraction or the
  review queue. No per-doc thresholds, no bank conditionals.

## One-time cleanup (2026-07-06)

The 11 pre-gate phantom cells were deleted verifier-guided (non-weighted copy removed,
weighted copy kept; pre-delete backup `final_pre_phantom_cleanup.db` in session
scratchpad); fleet re-verified to 18/18 clean afterwards.

## Non-goals

- Not a substitute for the routing-time coverage classifier (upstream spec 2026-07-02) —
  this gate catches value-level hallucination after extraction, not table detection errors.
- Shaded/merged-structure fidelity (cell_fact flags beyond value_num) is out of scope;
  values only.
