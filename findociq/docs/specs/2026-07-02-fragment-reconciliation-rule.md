# Fragment reconciliation rule — when N ruled fragments are N tables vs decoys over a borderless main table

**Date:** 2026-07-02
**Status:** design (implementation is a separate task; do NOT edit scan.py / merge_map.py from this doc)
**Supersedes:** the Delivery-order §1 acceptance in `2026-07-02-deterministic-upstream-routing.md`
("OCBC_NSFR → 2 units, merge_map shows row-12 colspan=3"). That premise stands; those two
acceptance clauses are corrected here (see §6).

## Problem

`route/scan.py` currently classifies `ruled ≥ 1 → BORDERED_MULTI` and plans one Gemini call per
`find_tables()` fragment. That is correct when fragments ARE the content, but catastrophic when
fragments are small decoys sitting over a **borderless** main table: the main table gets **no
extraction call at all** (silent total loss). BORDERED and BORDERLESS are therefore **not**
mutually exclusive page states; the ruled-count classifier is wrong for mixed pages.

## Decisive evidence (probed on disk 2026-07-02, pdfplumber 0.11.9)

**Numeric-token coverage** = fraction of a page's numeric text tokens whose centre falls inside
any `find_tables()` fragment bbox. This single signal separates the two regimes with a huge margin:

| Page | ruled | bscore | numeric cov | frag_area/content | truth |
|---|---|---|---|---|---|
| uob 12.9 p1 | 10 | 27 | **0.97** | 0.68 | fragments ARE content → 10 siblings |
| uob 12.9 p2 | 4 | 23 | **0.98** | 0.56 | 4 siblings |
| uob 12.9 p3 | 10 | 27 | **0.97** | 0.67 | 10 siblings |
| uob 12.9 p4 | 4 | 23 | **0.97** | 0.55 | 4 siblings |
| OCBC NSFR p2 | 4 | 38 | **0.03** | 0.085 | fragments are DECOYS; 34-row main table borderless |
| OCBC NSFR p3 | 4 | 38 | **0.03** | 0.085 | same |
| DBS 4Q23 NSFR p76 | 1 | — | **1.00** | — | SAME template, fully-ruled 21r×7c grid |
| DBS 4Q25 NSFR p85/87 | 1 | — | **1.00** | — | fully-ruled grid |

**The template-render finding that reframes everything:** the *identical* MAS NSFR regulatory
form renders **borderless with decoy fragments at OCBC** (cov 0.03, main table absent from the
fragment set) and **as a single fully-ruled grid at DBS** (cov 1.00, one 21×7 fragment). So:

1. Render style is a **per-bank/per-render property, not a template property.** Template match
   must NOT decide border-ness; the coverage signal decides it.
2. The row-12 colspan=3 / grey-band merge structure the upstream spec cites was verified on the
   **DBS ruled render** (and encoded in `template_registry.sql` `template_cell`). On the OCBC
   **borderless** render there is no ruled grid for the main table, so `merge_map` geometry cannot
   author those merges there — `merge_map` on the OCBC main region is contaminated (11 boundaries,
   844 stray vertical edges from decoys/furniture; numeric right-edges give 19 noisy clusters, not
   a clean 5). For borderless renders of a **known template**, the merge/shade ground truth handed
   to Stage-3 V2 must come from `template_cell`, not from `merge_map`.

## Signals (all deterministic, zero-token)

Per page, in addition to today's `section_no / title / ruled[] / bscore`, compute:

- `num_cov` — numeric-token coverage in fragments (definition above). **Primary discriminator.**
- `frag_area_frac` — Σ fragment area / content-bbox area. **Corroborating** signal.
- `bscore` — existing aligned-numeric-right-edge count (borderless strength).
- `num_tokens` — count of numeric tokens on the page (guards divide-by-zero / tiny pages).

## §1 Page classification for mixed pages

Replace the `ruled ≥ 1 → BORDERED` rule with a coverage-gated decision. Thresholds are set in the
wide empirical dead-zone (observed values are 0.03 vs ≥0.97 — nothing between 0.10 and 0.97):

```
def classify(ruled, num_cov, frag_area_frac, bscore, num_tokens):
    COV_HI, COV_LO = 0.80, 0.50      # dead-zone 0.50–0.80 (no real page seen inside)
    B_MIN = 3                         # existing borderless floor
    if num_tokens < 5:                # not a data page
        return "NO_TABLE" if ruled == 0 else "BORDERED_MULTI"  # tiny label grid
    if ruled == 0:
        return "BORDERLESS" if bscore >= B_MIN else "NO_TABLE"
    # ruled >= 1:
    if num_cov >= COV_HI:
        return "BORDERED_MULTI" if ruled > 1 else "BORDERED_SINGLE"   # fragments ARE content
    if num_cov < COV_LO and bscore >= B_MIN:
        return "BORDERLESS_MAIN"      # NEW: fragments are decoys over a borderless main table
    return "MIXED_REVIEW"             # NEW: 0.50–0.80, or low cov + weak bscore → MinerU arbitration
```

Rationale for `num_cov` over `frag_area_frac` as primary: the failure mode is **lost numbers**,
so measure numbers directly; area is a proxy that co-moves (0.085 vs ≥0.55) and is kept only as a
tiebreak inside `MIXED_REVIEW`. `bscore` alone can't discriminate — it is *higher* on OCBC NSFR
(38) than on genuinely-bordered uob 12.9 (23–27), because borderless financial tables have more
aligned numeric rows; bscore measures "borderless-ish layout," not "is the ruled set the content."

## §2 Borderless-main-table unit building

For `BORDERLESS_MAIN` (and `BORDERLESS`):

- **Region (bbox / framing):** MinerU detect is **required** to get the main-table row-band bbox.
  Aligned-numeric-edge geometry alone is **insufficient** here — probed on OCBC NSFR it yields 19
  noisy right-edge clusters and a `derive_grid` polluted by decoy rules (11 boundaries, wrong).
  Numeric-edge geometry "suffices" (skip MinerU) **only** when `bscore ≥ 8` AND the clustered
  numeric right-edges collapse to a stable column count (≤ `MIN_DATA_ROWS` singletons) AND no
  decoy fragment overlaps the numeric band — a cheap pre-check; when it fails, fall to MinerU.
- **Column structure:** if a template matches (§3), take the column axis from `template_col`
  (NSFR = 5 leaf cols under the "Unweighted value by residual maturity" span + "Weighted value").
  Otherwise take it from MinerU's detected region.
- **Decoy fragments:** **dropped** — absorbed into the main-table unit region, never emitted as
  their own extraction units. (They are shaded total-bands / banner strips that `find_tables()`
  latched partial ruling onto; on OCBC they carry 6/177 numeric tokens, all also inside the main
  table's span.) Record them under `dropped_fragments` in the JSON for audit, not for extraction.

## §3 Template-match decision — where and what it changes

Match happens in Stage-1 **after** classification and unit building, **per candidate unit** (not
per raw page), so it works identically for a ruled DBS unit and a borderless OCBC unit.

- **Signals (deterministic):** (a) title keyword scanned over the **full page text**, not just the
  top-14% running header — on OCBC the running header is the period date ("31 December 2025"), the
  form title lives in the body; (b) **column-header signature**: normalized leaf headers matched
  against `template_col.canonical_header` (+ the group_label span). The column-header signature is
  the robust discriminator — present on both renders (OCBC: "Unweighted…maturity" span at y≈119,
  "Weighted" at y≈112). Require title-keyword hit AND ≥ ⌈0.6·ncols⌉ header matches.
- **What a match changes (and only this):**
  1. **Unit consolidation across pages:** same `table_type` + continuation → a spanning unit
     (NOT applicable to OCBC p2/p3, which are different *periods*, so they stay 2 separate units).
  2. **Framing choice:** hand the template's known ncols + column axis to Stage-2 framing.
  3. **Merge/shade expectations to Stage-3 V2:** from `template_cell` when the render is borderless
     (merge_map can't see them); from `merge_map` geometry when the render is ruled (DBS), with
     `template_cell` as the cross-check. V2 compares HTML colspans/shading to whichever authority
     applies.
- **Non-goal preserved:** the template is used for alignment / framing / verification only. It is
  **never** prompt-stuffed as a reference answer table into Stage-2.

## §4 Reconciliation rule proper (fragments → logical tables)

```
if page.class in (BORDERED_MULTI, BORDERED_SINGLE):     # num_cov >= 0.80
    # fragments ARE the content. Each fragment = one sibling extraction unit,
    # UNLESS two fragments are continuation strips of ONE table split by page
    # furniture: consolidate only when SAME section_no + cont'd + identical column
    # signature (grid boundary count & positions match within TOL) across the gap.
    #   uob 12.9: 10/4/10/4 fragments have DIFFERENT column counts (5/9/8/10/13/…),
    #   no cross-fragment edge sharing, MinerU parity 28==28 -> NO consolidation ->
    #   28 sibling extraction units. (correct today)
elif page.class == BORDERLESS_MAIN:                      # num_cov < 0.50, bscore strong
    emit exactly 1 main-table unit (MinerU/template region); drop decoy fragments.
elif page.class == BORDERLESS:                           # ruled == 0
    MinerU detect -> per-region units.
elif page.class == MIXED_REVIEW:
    MinerU arbitrates: fragments MinerU also confirms as tables -> siblings;
    remainder -> borderless-main unit. (belt-and-suspenders; no test page lands here)
```

Continuation consolidation is **column-signature gated**, so sibling tables that merely stack
vertically (12.9) never collapse, while a true one-table split across a page break does.

## §5 Data structures added to the route JSON

Per page object, add:
```json
"num_cov": 0.03, "frag_area_frac": 0.085, "num_tokens": 177,
"template": {"table_type": "nsfr", "matched_by": ["title_kw","col_signature"], "ncols": 5},
"dropped_fragments": [ {"bbox": [283,331,557,346], "reason": "decoy_low_coverage"} ]
```
Per emitted unit, add `unit_kind ∈ {bordered_sibling, borderless_main, borderless_region}`,
`region_source ∈ {pdfplumber_fragment, mineru_detect, numeric_edge}`, `template_type|null`,
and `structure_authority ∈ {merge_map, template_cell}`. Keep the existing `units[]` (section
continuation grouping) but rename its count to `n_extraction_units` to end the
"28 units vs 1 unit" ambiguity: 12.9 → 1 section-group containing **28 extraction units**;
OCBC NSFR → 2 section-groups (2 periods) each containing **1 extraction unit**.

## §6 Updated acceptance criteria (replace upstream-spec Delivery §1)

1. **uob 12.9** → 28 extraction units (10/4/10/4), every page `num_cov ≥ 0.95` →
   `BORDERED_MULTI`, all `section_no 12.9`, pages 2–4 `continuation:true`, **no** cross-page
   consolidation (distinct column signatures). *(unchanged in count; now coverage-justified)*
2. **OCBC NSFR** → each data page classified `BORDERLESS_MAIN` (`num_cov ≈ 0.03`, `bscore 38`),
   the 4 fragments per page recorded as `dropped_fragments`, **exactly 1 borderless-main
   extraction unit per page** (p2 and p3 = 2 units total, one per reporting period — NOT one
   spanning unit, NOT 4 per-fragment calls). Template `nsfr` matched; `structure_authority =
   template_cell`. *(replaces "2 units, merge_map row-12 colspan=3" — merge_map does not apply to
   the borderless render.)*
3. **DBS NSFR** (4Q23 p76/p78, 4Q25 p85/p87) → `num_cov ≈ 1.00`, 1 fragment → `BORDERED_SINGLE`,
   template `nsfr` matched, `structure_authority = merge_map` cross-checked against `template_cell`
   (this is where row-12 colspan=3 is geometrically visible). Same regulatory table, different
   render, same final unit shape (1 NSFR unit) — the acceptance test that proves render-independence.

## §7 Validation plan (threshold calibration on the other quarterlies)

Run the coverage probe (no LLM) over every `*_Pillar3.pdf` in `data/sources/pillar3/` and confirm:

1. **Bimodality:** `num_cov` histogram is bimodal with an empty 0.10–0.95 band; no data page lands
   in the 0.50–0.80 dead-zone. If any does, inspect and adjust `COV_HI/COV_LO`.
2. **NSFR render census:** locate the NSFR page in OCBC/UOB/DBS × 4Q23/4Q24/4Q25/1Q26 (title
   keyword + "Total ASF"/"Weighted value"); record ruled-count and `num_cov`. Expect OCBC ≈ 0.03
   (borderless) and DBS ≈ 1.00 (ruled); classify UOB. Every NSFR page must yield exactly 1 unit.
3. **Bordered multi census:** the SA(CR) 12.x pages across banks must stay `num_cov ≥ 0.95` and
   keep per-fragment sibling counts (spot-check vs MinerU on one bank).
4. **Decoy audit:** for every `BORDERLESS_MAIN` page, assert every dropped fragment's numeric
   tokens are a subset of the main-unit region (no numbers lost by dropping).
5. **Template-match precision:** confirm the title-kw + col-signature matcher fires on all NSFR
   pages (both renders) and does NOT fire on 12.x SA(CR) pages (no false template match).
