# Running-header detection + unit containment guard — design (2026-07-16)

**Pipeline pivot.** The TOC branch now detects Gemini headings that are actually
*running page headers* and drops them; and `build_units` drops any extraction
unit that strictly contains another. Both are general, deterministic, no per-bank
/ per-document literal.

## Problem

`OCBC_4Q25_Media_Release_and_Financial_Highlights.pdf` prints
`FINANCIAL HIGHLIGHTS (continued)` as the **top line of every page 11–22** (a
running header). Gemini's TOC call transcribed that repeated header as a single
L1 section spanning p10–22, parent of ~30 real subsections. Downstream:

- `pass2/extract.py` `load_sections()` selects extraction units by `has_tables`
  (not strict childless-leaf), and `build_units()` makes one unit per selected
  section over `start_page..end_page`. So the phantom became ONE 13-page unit.
- `extract_unit_chunked` split it into 7 dense chunks (16 tables / 278 rows). The
  density induced a single-row extraction slip (an extra 6th value on a 5-column
  row) that fails the DB loader (`load_v7.py` fail-loud on row width > columns).

The real per-page sections (NET INTEREST INCOME p13, NON-PERFORMING ASSETS p17…)
are the *second* top line of each page and were correctly detected as children,
just swallowed under the phantom.

## Invariant

For every doc: no section that is really a *repeated page header* survives as a
section, and **no extraction unit's page range strictly contains another unit's**.
`seq(parent) < seq(child)` (the 2026-07-16 topo pivot) still holds.

## Layer 1 — running-header detection (`toc_stage.py`)

`detect_running_headers(sections, top_line_sq)` flags a heading `S` iff ALL:

1. **Renders like a header** — `squash(strip_cont_marker(S.title))` matches the
   page TOP-LINE (`get_page_lines(pdf,p)[0]`) on `>= RUNHDR_RECUR_FRAC` (0.6) of
   the pages in `[page_start, page_end]`, span `>= RUNHDR_MIN_SPAN` (3).
2. **Would become a unit** — `has_tables` (the harm gate: a prose parent that
   merely prints a running header, e.g. an auditor's report, has
   `has_tables=False` and is left alone).
3. **Groups real sections** — `>= RUNHDR_MIN_CHILDREN` (2) direct children on
   distinct pages (a wrapper, not a leaf).

Action (`apply_running_header_strip`): reparent each child to the base heading (a
sibling with the marker-stripped title near `page_start`; else the phantom's
parent), drop the phantom, recompute `path`; then **re-run `build_windows`**.
`parent_id` is otherwise preserved — the fault is a mis-transcribed header, not a
mis-nesting; genuine hierarchy reconciliation is the separate parent-child 2-pass.

Wired in `main()` between the first `build_windows` and validation; the emitted
`_toc.json` `document` block carries `running_headers_dropped: [{id, title, span,
recurrence, n_children, reparent_to}]` and the STEP-1 report prints
`RUNNING-HEADER DROPPED …` so a human SEES the classification.

### Validated against the whole corpus (recurrence of signal 1)

| section | span | recur | has_tables | verdict |
|---|---|---|---|---|
| OCBC `financial_highlights_continued` | 10–22 | 1.00 | True | **STRIP** |
| DBS `overview`, `performance_by_*` | 3–6p | 0.00 | True | keep |
| OCBC `fourth_quarter_2025_performance` | 4–6 | 0.33 | True | keep (<0.6) |
| DBS `independent_auditor_s_report` | 39–46 | 0.88 | False | keep (gate 2) |
| UOB `third_quarter…highlights` | 1–4 | 1.00 | False | keep (gate 2) |

Fires on exactly one section across all 7 corpus TOCs — the phantom. The two legit
sections that also recur at page-top are saved by the `has_tables` gate.

## Layer 2 — unit containment guard (`extract.py` `_drop_containing_wrappers`)

After Layer 1, the phantom's base heading (`financial_highlights`, L1 p10) still
*re-extends* its hierarchical span to p10–22 (next L1 is `about_ocbc` p23), so it
would again become a 13-page unit. The guard, in `build_units`, drops any leaf
that has another leaf in its **strict interior** (`a0 < b0 && b1 < a1`, both
strict) — the wrapper; the inner units carry the data. Empirically on OCBC the
p10–22 wrapper is dropped (interior unit e.g. `non_interest_income` p14).

This is NOT "extract strict childless leaves only" — that would regress DBS
`overview` (a legit table-bearing parent whose children are label-only,
`has_tables=False`, so it contains no unit and is correctly kept).

### STRICT interior — required to not regress Pillar 3 (verified)

An earlier `a0<=b0 && b1<=a1` (boundary-inclusive) version **wrongly dropped 4
legit DBS 4Q25 Pillar 3 tables** (A.6.3 p17-18, A.12.2.3 p37-39, A.12.2.7
p45-49, A.13.2.4 p59-63): each merely SHARES a boundary page with a sibling
(A.6.2 p17, A.12.2.8 p49, …) — a TOC span-estimate touch, not containment.
Requiring strict interior (`<`, not `<=`) makes the guard **inert on Pillar 3**
(DBS 4Q25: 64 leaves → 64 kept, 0 dropped) while still catching the FS phantom.
Pillar 3 uses the deterministic `pass1_toc` framework, not `toc_stage`, so Layer
1 never touches it; only this shared guard could — and strict interior keeps it
safe. `pass2/test_build_units.py` locks both P3 boundary cases.

Residual (accepted, not the target defect): a 3-page wrapper whose child ends on
its own last page (OCBC `fourth_quarter_2025_performance` p4-6, child p5-6) is
NOT dropped (child touches the p6 boundary). This is normal FS overlap, far from
the 13-page mega-unit. The fully clean rule is hierarchy-based ("drop a unit with
a descendant unit"), deferred — it needs parent/path threaded into `build_units`.

## Blast radius

- `toc_to_db.py` stores only `level/parent_id/path/seq` (spans/has_tables are
  route-manifest state) → cleaner lineage, no schema change.
- `run_doc.py` STEP 1 just checks `_toc.json` exists → unchanged.
- Detection + guard fire on ZERO sections in DBS 2Q/4Q, both OCBC docs' legit
  sections, all three UOB docs → no regression to already-loaded docs.

## Known caveats (deferred, not hidden)

- If a future running header is not the topmost line (a logo/date prints above
  it), gate 1 needs top-2 lines — widen `get_page_lines(...)[:2]`.
- `financial_highlights` is recorded as a p10–22 parent envelope (its own summary
  tables are p10–12; p13–22 belong to its child subsections). Re-scoping the
  parent to its own content vs. keeping the document's running-header grouping is
  a modeling choice for the parent-child 2-pass; it does not affect extraction.
