# Spec — TOC emits section windows; validators consume them

Status: proposed
Date: 2026-07-29
Supersedes the region-finding approach taken in
`2026-07-29-column-band-validator.md` (companion fix), which is demoted to a
fallback rather than removed.

## Problem

`toc_stage.py` computes authoritative section geometry and then throws it away.

Computed:
- `s["anchor_page"]`, `s["anchor_y"]` — `toc_stage.py:348,353,357`, with
  provenance in `s["anchor_source"]` (`candidates` | `text_search` |
  `page_fallback`)
- `s["_win_lo"]`, `s["_win_hi"]` — `build_windows` (`toc_stage.py:419-455`);
  half-open `(page, y)` lexicographic windows, `TOP_OF_PAGE_Y`-snapped so a
  window's boundary agrees with the previous window's end

Emitted (`toc_stage.py:679`):
```python
fields = ["id", "title", "page_start", "page_end", "level", "parent_id",
          "path", "seq", "section_no", "has_tables", "n_regions", "anchor_source"]
```

`anchor_page`, `anchor_y`, `_win_lo`, `_win_hi` are absent. Only the provenance
*label* survives — hence TOC entries reading `"anchor_source": "candidates"`
with no coordinate anywhere.

**Consequence:** `validate_numbers` needed a section region and could not get
one, so `section_region_for_unit` (`transforms.py`) re-derives it by title-text
search. Two implementations of one concept, free to diverge — the weaker one
being the one actually used on the FS path.

`page_section_regions` (`transforms.py:287`) is likewise a **Pillar-3-era**
helper: it qualifies headings via a numbered-heading regex (`16.2.1`, `A.12`).
FS documents do not number their headings — that is the entire reason the FS
branch uses Gemini to read them. Applying it to FS was a category error.

## Design

### 1. Emit the geometry
Add to the `fields` list at `toc_stage.py:679`: `anchor_page`, `anchor_y`,
`win_lo`, `win_hi`. Serialize `_win_lo`/`_win_hi` as two-element arrays
`[page, y]`. Preserve half-open semantics; document them at the emit site.

### 2. Propagate it
`load_sections` (`extract.py:930-958`) builds an **explicit field projection**
for the FS format — new keys are silently dropped there. Carry
`anchor_page`, `anchor_y`, `win_lo`, `win_hi` through it, each via `.get()` so
the 44 already-committed TOCs (which lack them) still load.

`build_units` already places the whole section dict into `unit["leaves"][0]`,
so no change is needed beyond the projection.

### 3. Consume it, with explicit precedence
`section_region_for_unit` resolves a page's `(y_start, y_end)` by tiers:

| tier | source | applies to |
|---|---|---|
| 1 | TOC `win_lo`/`win_hi` | any TOC regenerated after this change |
| 2 | `page_section_regions` | numbered Pillar 3 sections |
| 3 | title-text search | legacy TOCs, hand-built TOCs, `page_fallback` anchors |
| 4 | page-wide + visible note | nothing resolved |

Cross-page window → per-page range, mirroring `_window_overlaps_region`
(`toc_stage.py:406-417`): for page `P` in `[p_lo, p_hi]`,
`y_start = y_lo if P == p_lo else 0.0`;
`y_end = y_hi if P == p_hi else page_height`.

**The tier that fired must be visible** in the unit's `meta.json` — a routing
decision a human can see without reading code, per CLAUDE.md.

## Tests

`$0`, no API, no paddle install. `DBS_1Q26_trading_update` has BOTH a committed
paddle scan (`data/derived/paddle_scans/DBS_1Q26_trading_update/{candidates,regions}.csv`)
and a cached `_toc_raw.json`, so `toc_stage` regenerates its TOC with no Gemini
call — it only *reads* those CSVs.

1. Regenerate the DBS TOC; assert the four new fields are present and
   well-formed.
2. Tier-1 regions for the three page-6 sections agree with the tier-3
   title-search regions already asserted in `test_number_scoping.py`
   (`y_start ~= 126.1 / 378.7 / 496.4`). Divergence means one of the two is
   wrong — that is the point of the test.
3. A TOC without the fields still resolves via tier 3; `test_number_scoping.py`
   and `test_column_bands.py` continue to pass unchanged.
4. The fired tier is recorded and visible.

## Non-goals

- Do not delete `section_region_for_unit` or `page_section_regions` — both
  remain as lower tiers.
- No re-extraction, no prompt change, no auto-repair of column shifts.
- Regenerating the other 43 committed TOCs is out of scope; they stay on
  tier 3 until independently regenerated.
