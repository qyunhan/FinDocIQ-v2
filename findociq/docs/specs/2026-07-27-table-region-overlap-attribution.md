# Table-region overlap attribution — design (2026-07-27)

**Pipeline pivot.** `toc_stage.build_windows()` now attributes a PaddleOCR table
region to *every* section whose window vertically overlaps the region, instead
of a single "first window that contains its top-left point" match. Window
boundaries are also computed once and shared between `page_end` and region
attribution, instead of being derived twice with different rounding.

## Problem

PaddleOCR (STEP 0) was already returning correct table region boxes — the
"true tables" per page. The bug was entirely downstream, in how `toc_stage.py`
(STEP 1) decided which section owns each region:

- **Bug 1 — window boundary disagreement.** `page_end` already snaps to a
  page's top when the *next* section's anchor sits in that page's top strip
  (`TOP_OF_PAGE_Y`) — the intent being "the whole page belongs to the next
  section, not a sliver of the previous one." The attribution window
  (`_win_lo`/`_win_hi`) used the next anchor's raw `(page, y)` instead of that
  same snapped value, so a region sitting in the next page's top strip could
  be wrongly swallowed by the *previous* section's window.
- **Bug 2 — exclusive point attribution.** A region was attributed to exactly
  one section: the first window whose point-test matched. But PaddleOCR
  sometimes emits ONE region box that visually spans several distinct,
  vertically-stacked tables belonging to *different* sections on the same
  page (a summary page with 4 short tables back-to-back). Every section after
  the first was silently left with `has_tables=False` and never routed to
  Gemini extraction.

### How it was found

`1Q23_trading_update.pdf` page 5 has one PaddleOCR region (`y0=89.74,
y1=728.14`) covering 4 stacked sections (`selected_income_statement_items`,
`selected_balance_sheet_items`, `key_financial_ratios`, `per_share_data`).
Under the old code only the first section got the region; the other three —
including the income statement and balance sheet — never got extracted.
Checking `compiled_fs.db` showed the failure mode is not always a loud
"0 tables" doc-level failure (which is how `DBS_1Q22_trading_update` /
`DBS_3Q22_trading_update` surfaced during the 2026-07-26 sweep, logged in
PROGRESS.md as an *unconfirmed, possibly-legit* "headline-only 2022 doc"
finding — it wasn't; it was this bug): a doc can partially succeed, loading
some real tables while silently missing others on the same page.

## Fix

- `_breakpoint(page, y)` — the single top-of-page snap rule, now used for
  BOTH `page_end` and the window `_win_lo`/`_win_hi` boundaries, so they can
  no longer disagree.
- `_window_overlaps_region(win_lo, win_hi, page, y0, y1)` — half-open
  lexicographic-window / page-span overlap test.
- `build_windows()` attributes each region to the list of ALL overlapping
  sections (`attributed[(page, table_idx)] = [section, ...]`), incrementing
  `n_regions` on every owner. `sum(n_regions)` can now legitimately exceed
  `len(regions)`.
- Validation invariant changed from an exact partition (`sum(n_regions) +
  preamble == len(regions)`) to coverage (every region attributed to >=1
  section or recorded as preamble; `len(attributed) + len(preamble) ==
  len(regions)`).

Tests: `pipeline/toc/test_toc_stage.py` (both boundary-agreement cases, the
one-region-four-sections case, and the two must-not-regress cases: a region
cleanly inside one window, and a preamble region before the first section).

## Blast radius

- Only `toc_stage.py`'s FS branch is affected. Pillar 3 uses the deterministic
  `pass1_toc` framework (see the 2026-07-16 running-header spec) and never
  calls `build_windows()` — unaffected by construction.
- `toc_to_db.py` stores `has_tables`/`n_regions` as route-manifest state only
  (not schema columns), so no DB migration; the fix changes what STEP 2 routes
  for extraction, not the schema.
- **Every already-loaded FS doc needs re-checking.** STEP 1 is
  resume-friendly (the Gemini heading call is cached in `<doc_id>_toc_raw.json`
  and skipped if present; PaddleOCR regions are cached in
  `paddle_scans/<doc_id>/regions.csv`), so re-running `toc_stage.py` on an
  already-ingested doc costs $0 — it only recomputes the deterministic window/
  attribution step. A full sweep across the 18 loaded FS-family docs (Pillar 3
  excluded, per above) found **9 of 18 with at least one section flipping
  table-bearing status** under the fix (4 to 31 sections each, mostly
  previously-invisible small ratio/per-share/capital tables sharing a page
  with an already-detected table): `1Q23_trading_update`,
  `3Q25_trading_update`, `DBS_1Q22_trading_update`, `DBS_1Q25_trading_update`,
  `DBS_1Q26_trading_update`, `DBS_2Q22_performance_summary`,
  `DBS_3Q22_trading_update`, `DBS_4Q22_performance_summary`,
  `OCBC_1Q25_Results__Press_Release`. 7 more FS docs could not be checked in
  this pass — their source PDFs are gitignored and no longer present on disk
  under the path recorded in `document.source_file` (`DBS_2Q25/4Q25_
  performance_summary`, `OCBC_4Q25_Condensed_Financial_Statements`,
  `OCBC_4Q25_Media_Release_and_Financial_Highlights`, all 3 UOB docs — the
  UOB PDFs exist on disk but under a renamed path from a later re-scrape,
  orphaned from the doc_id, the same `source_file`-drift gotcha flagged in
  the 2026-07-24 handoff). Re-checking those needs the PDF re-fetched/
  relinked first, tracked separately — not a reason to delay fixing the
  20-doc majority.

## Known caveats (deferred, not hidden)

- One-to-many attribution means the SAME PaddleOCR region can now drive
  extraction of several sections that share it. Gemini extraction is still
  scoped per-section page range, so it independently re-reads the shared
  page and picks out only its own section's table — this has not produced
  duplicate cell data in the cases checked (the 4-way DBS trading-update
  split), but it is a new sharing pattern worth watching in verify_cells
  output on the next fleet re-verify.
- The true fix for a Paddle region that visually merges what a human would
  call 2+ tables (rather than 2+ *sections* sharing a page) is still the
  parked "de-merge" item from 2026-07-13 (NII p10). This pivot does not
  attempt that — it only ensures every legitimately-overlapping SECTION sees
  the region, not that the region's own internal table count is corrected.
