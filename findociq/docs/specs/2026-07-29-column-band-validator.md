# Spec — geometric column-band validator (`validate_column_bands`)

Status: **phase 2 — detect AND repair** (amended 2026-07-29).
Date: 2026-07-29

> **Phase 2 amendment.** Auto-repair, listed as a non-goal below, is now
> implemented as `repair_column_bands` — a separate pass run AFTER validation at
> both `extract.py` call sites, never inside `_apply_transforms` (which runs
> after `meta.json` is written, so a repair there could not be recorded — the
> silent correction this design forbids). Detection runs first on the raw model
> output so `meta.json` carries BOTH the `col-shift:` finding and the
> `col-repair:` record for the same row; the firing-rate evidence survives the
> fix. Repair is a pure permutation of the row's own GCell objects, guarded by a
> multiset equality between extracted values and printed tokens, so it can only
> re-slot known values — never invent, drop, merge or reformat one. Rows that
> detection does not flag are never entered.

## Problem

`pass2` can emit a table whose values are structurally valid but sit in the
**wrong columns**. No existing validator can see this.

Observed on `DBS_1Q26_trading_update` p6, `selected_balance_sheet_items_m`:

| row | 1Q26 | 1Q25 | % chg | 4Q25 | % chg |
|---|---|---|---|---|---|
| printed x-band | 312.6 | 357.6 | **430.0** | 447.7 | **520.2** |
| Customer loans (printed) | 453,180 | 435,295 | **4** | 445,011 | **2** |
| Constant-currency (printed) | — | — | **6** | — | **2** |
| Constant-currency (**extracted**) | — | **6** | — | **2** | — |

The two percentage values landed one value-column to the left.

### Why nothing caught it

- `validate_spans` — checks cell **count** only. Count was correct (5).
- `validate_numbers` — counts numeric tokens **page-wide, without column
  regard**. Both values are present on the page, so recall is satisfied.
- `_reasonable()` (`extract.py:397`) — gates on extraction **thinness**. The
  extraction looked healthy, so the rasterized-image fallback
  (`extract.py:656`) never fired.

### Why this is not a prompt problem

The COLUMN ALIGNMENT rule (`extract.py:179-189`) mandates *structural*
integrity — one GCell per column, emit empties, never truncate. The model
**complied**: 5 cells, empties in place. What the rule cannot specify is *which*
slot a value belongs to; that is perception, not instruction-following. The unit
ran `image_used: False`, so the model saw a flattened text stream in which a
label at x=87 and two lone numbers at x=430/x=520 carry no column association.

Rewording the prompt would be per-document hand-tuning with no deterministic
guarantee. Rejected per CLAUDE.md.

## Signal

The information is already available deterministically and for free:
`pdfplumber.extract_words()` gives exact x-positions. A value's x-centre
unambiguously identifies its column.

## Algorithm

Per table, per page. No bank-, document- or section-specific logic.

1. **Group printed words into lines** by `top` (tolerance 3pt).
2. **Calibrate column bands.** A *dense line* is one whose numeric-token count
   equals the table's value-column count `N`. Collect numeric-token x-centres
   across all dense lines and cluster into `N` bands; each band is
   `[min(x0), max(x1)]` over its cluster.
   - **Guard:** fewer than 2 dense lines → emit a single
     `col-bands: uncalibrated (N dense lines)` note and validate nothing.
     Silence here would read as "checked and clean"; it must be visible.
3. **Assign** each numeric token on every printed line to the band containing
   its x-centre.
4. **Match** each extracted row to a printed line by normalized label
   (casefold, collapse whitespace, strip footnote digits/markers). No match →
   skip that row (unverifiable, not a failure).
5. **Compare** the set of occupied value-slots in the extracted row against the
   set of occupied bands on the matched printed line. Difference → issue.

## Output

Same shape as the other validators — `list[str]`, appended to the unit's
`meta.json` and printed in the run log:

```
  col-shift: 'Constant-currency change' printed bands [3,5] -> extracted slots [2,4]
```

## Placement

`pass2/transforms.py`, beside the existing validators. Wired at both existing
call sites — `extract.py:441-444` and `extract.py:782-785` — and surfaced
alongside `number_issues` in the run output.

## Companion fix (same geometry machinery)

`validate_numbers` currently scans the **whole page** while extraction units are
**section-scoped**. On a multi-section page every unit is charged with the other
sections' numbers: `DBS_1Q26` p6 carries 3 sections and produced **51 spurious
deficits** on one unit — symmetric across units. Scope `_page_numbers` to the
unit's section y-region using the existing `page_section_regions`
(`transforms.py:287`).

This matters more than it looks: a validator that fires ~51 false warnings on
every multi-section page guarantees real failures are ignored.

## Non-goals (phase 1)

- No auto-repair. Detect and report only; measure firing rate across the 39
  committed docs before deciding whether repair is warranted.
- No change to prompts.
- No change to the image-attachment policy. If measurement shows shifts
  concentrate in `image_used: False` units, revisit `_reasonable()`'s gate as a
  separate spec.
