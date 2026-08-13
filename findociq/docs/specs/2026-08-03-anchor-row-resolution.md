# Anchor row-level resolution: registry → bank_line_map → human-anchor projection

Continuation of `2026-08-03-anchor-scope-resolution.md` (document → section → table).
This pass closes row-level resolution: every anchor resolves through the registry to
a stable `(doc_id, table_type_id, row_lineage)`, loads into `bank_line_map` on that
stable key, and is projected onto `row_dim`/`v_cell` so the pipeline actually reads it.

## Prerequisite check — FAILED, then built

`section_registry`, `doc_cadence`, `normalise_caption` did not exist anywhere in the
repo (not on `v2-concept-toolkit`, not in the DB). Only `table_registry_seed.csv`
(102 rows, untracked) and the alias-based `table_registry`/`table_registry_alias`
mechanism from the prior pass existed. Built as Step 0:
`pipeline/mapping/migrate_add_table_catalog.py` — `table_catalog` (the seed, loaded
verbatim + normalized caption), `section_registry` (9 rows, scoped to what the
anchors + cadence validation need), `doc_cadence` (10 docs), `normalise_caption`
(aliased to the existing, tested `normalize_exhibit_title` rather than reimplemented).

**Scoping decision, made explicit and confirmed correct by later checks:** the seed's
vocabulary renames/folds 11 of the 26 live `table_registry` ids and was authored only
against 2025 documents. Rather than force a corpus-wide re-stamp of `table_t`/
`bank_line_map` for periods and doc families (pillar3, 2022 filings) the seed was
never verified against, the old ids were kept alive with a `SUPERSEDED` note and
`table_registry` extended additively. None of the 72 anchors touch a renamed id —
all use the 7 "core" ids, byte-identical in both vocabularies.

## Step 1 — 75 rows resolved, all four levels

72 `anchor` + 3 `pending_extraction` rows, all PASS. Table-level resolution reused
the proven `resolve_table_type` alias mechanism from the prior pass (not a fresh
lookup against `table_catalog`'s exact captions, which don't literally string-match
every raw `table_name` the map records — e.g. UOB's "Selected income statement" vs
the seed's canonical "Selected income statement items").

Row-level matching (new this pass) went through three real bugs, found and fixed by
comparing resolver output against actual `row_dim`/`row_lineage` content rather than
trusting a clean PASS count:

1. **Leaf position assumed constant at `lvl2`.** Wrong for UOB's combined-highlights
   table (no shared title row — depth=1 rows ARE the leaf). Fixed to depth-relative
   (`lvl{depth}` / `lvl{depth-1}`).
2. **Empty `parent_row` treated as "skip the check" instead of "assert uniqueness."**
   Silently matched DBS's ambiguous "Net interest income" (present under both
   Commercial-book and Markets-book groups) to whichever row the query returned
   first. A structural "is this a real grouping" heuristic was tried first and
   discarded — it broke OCBC's ASSETS/LIABILITIES-sectioned balance sheet one way and
   UOB's genuinely-unique "Common Equity Tier 1" the other way. Fixed to: an empty
   parent_row requires the leaf to be unique across the whole search scope, or the
   resolver raises `Ambiguous` — a hard stop, never a guess.
3. **Stored address used the footnote-suffixed display label, not the footnote-clean
   raw one**, minting spurious duplicate addresses for 5 UOB rows the corpus already
   had clean entries for. Fixed to prefer `row_dim.row_leaf_label` (raw) whenever it's
   the form that actually matched.

## Step 2 — loaded on the stable key, reconciled

`(bank, table_type_id, parent_label_norm, row_label_norm)`, `UNIQUE`-constrained —
learned mid-build that "supersede" therefore has to be an in-place `UPDATE`, not
insert-new-and-deprecate-old (that pattern hits the constraint immediately).

Reconciliation policy, per instruction: **trust the address, flag the label, never
block on a naming mismatch — only a failed address resolution is a hard stop.**
- **4 table/row-level resolution failures** — all address errors, all corrected in
  the map with evidence, not guessed: OCBC `ratio.roa`/`ratio.roe` parent →
  "Performance ratios"; `reg.capital.cet1_ratio` parent → "Capital Adequacy Ratios";
  UOB `bs.nav_per_share` → top-level, no parent.
- **21 concept_key renames** (crosswalk, logged in DECISIONS.md, reversible) — same
  identity under a name that doesn't match the already-`human_confirmed` dictionary
  vocabulary elsewhere in the corpus (e.g. `pnl.fee.net` → `pnl.noninterest.fee_commission`).
- **1 flagged, not blocked**: UOB `bs.assets.customer_loans_net` — the canonical
  label says "net," the anchored row is gross. User-confirmed policy: use gross.
  UOB's and OCBC's rows renamed to `bs.assets.customer_loans_gross` to match; DBS's
  was **not** touched — it already has its own separately-verified `human_confirmed`
  entry asserting NET is correct for DBS specifically (dashboard_rows.yaml note:
  "D3-consistent: NET. Fixes the gross/net mis-stamp"), a different, already-settled
  decision the gross policy doesn't override.

**MERGE invariant verified by hash**, not just count, after every load: the
`human_confirmed` row set is byte-identical before and after re-running
`backfill_map.py`.

## Step 3 — human-anchor projection (the un-inerting)

`row_dim.concept_key_human` / `segment_key_human` / `identity_source` — new columns,
never touched by `resolve_deterministic` (unlike `row_dim.concept_key`, which is
re-derived and overwritten on every `concept/run.py` pass — the exact defect recorded
in DECISIONS.md 2026-07-31). `v_cell`/`v_cell_flat`'s `COALESCE` inverted to prefer
the human column, falling back to the existing derived one.

**Verified: DBS's Net Interest Income separates into 3 distinct rows** (was 1
collapsed `pnl.nii.net`/NULL row) — 14,494 @ SEG_COMMERCIAL, 6 @ SEG_MARKETS.

**Real upstream bug found and logged, not fixed here:** `row_dim.row_parent` (not
just the `row_lineage` reconstruction — checked the base column directly) genuinely
parents the group-total "Of which: Net interest income" row under "Markets trading
Income," a mis-grouped header — an extraction/geometry-stage hierarchy bug for this
table shape. **User directive received mid-pass**: don't anchor the group total to
that row at all — derive it instead as Commercial-book NII + Markets-book NII
(14,494 + 6 = 14,500, arithmetic already verified in `PROGRESS.md` 2026-07-31). The
map's `pnl.nii.net` DBS row was converted from `anchor` to `derived` accordingly; the
group-total slot now falls back to the pre-existing `resolve_deterministic` value
(still correct) rather than resting on the buggy row.

## Step 4 — cross-table EPS/NAV concept_home

New `concept_home` table, `(concept_key, bank, table_type_id)`. All 3 banks' EPS/NAV
addresses confirmed distinct and each correct for that bank — UOB's EPS lives inside
the combined highlights ratios table (not a dedicated per-share exhibit), OCBC prints
EPS in **two** legitimate places (statutory income-statement foot AND the
media-release ratios table — both recorded), OCBC's NAV is at the foot of the
statutory Balance Sheet. No anchor was forced to share a table id across banks.

## Step 5 — derivation layer

Of the 3 concepts named in scope, 2 were **already implemented** in
`concept_dictionary.yaml` (`pnl.noninterest.other`, `kind: derived`,
`metric_kind: metric`, fill-only) — the formula `pnl.noninterest.total -
pnl.noninterest.fee_commission` already serves DBS, OCBC, and UOB uniformly, with
arithmetic verified in the dictionary's own comment (DBS 8400-4898=3502, OCBC
5464-2411=3053). The 3rd (OCBC `bs.equity.shareholders`) needed no formula at all:
OCBC's statutory Balance Sheet prints "Attributable to equity holders of the Bank
(Total)" directly — converted from `derived` to a plain `anchor` in the map instead
of building a subtraction formula from concepts (non-controlling interests, other
equity instruments) that aren't tracked in the dictionary yet.

A 4th derivation entered scope mid-pass (DBS NII group total, Step 3 above) but was
**not** wired into `compute_ratios.py` — that engine's data model pivots
`segment_key` into the row index, so same-segment cross-concept formulas (its
existing use) can't express a cross-segment same-concept sum. Left as a manual
reconciliation (verified equal, not automated) rather than force-fitting the engine
or building a second one under time pressure; flagged as a real follow-up.

**Note on scope discipline:** `build_fact_metric.py` was run once to sanity-check
this step and reverted immediately — it rebuilds `fact_metric` wholesale from
`row_dim.concept_key` (not the new `_human` columns), touches files with unrelated
pre-existing WIP (`fact_metric_conflicts.csv`), and is explicitly out of scope: this
pass makes anchors resolve and be read; the serving/dashboard rebuild is next.
`fact_metric` was restored to its pre-run state (3,130 rows, verified via table-level
row-count diff against the git-committed baseline) before continuing.

## Step 6 — cadence-aware coverage

New `v_anchor_coverage` view, joining `doc_cadence` + `table_catalog` + `table_t`.
**Both explicit acceptance checks verified directly**, not inferred from aggregate
counts: zero interim (`quarter_only`) docs have a `half_year`-cadence table type
incorrectly flagged as anything but `NOT_AT_THIS_CADENCE`; all three real statutory
docs (DBS/UOB/OCBC condensed statements) show `PRESENT` for
`FS_INCOME_STATUTORY`/`FS_BALANCE_STATUTORY`/`FS_CASHFLOW`.

Broader catalog coverage (all 32 seed types, not just the 12 anchor targets) surfaced
one real, evidence-backed catalog defect, fixed: the seed listed UOB's income/
balance/ratios exhibits as 3 separate expected table types, but UOB's page is
physically ONE combined table (confirmed repeatedly this session) — corrected those
3 `table_catalog` rows to `FS_HIGHLIGHTS_COMBINED`, matching what every UOB anchor
already resolves to. Two more nuances were found and **reported, not fixed** (out of
time budget, genuinely uncertain without more evidence): OCBC's Q1 vs Q3 press
releases differ in whether the highlights page splits into sub-tables at all (a real
cross-quarter structural difference, not a bug); the media release's narrative
"Full Year 2025 Performance"/"Fourth Quarter 2025 Performance" summary blocks are
seeded as `table_type_id=FS_INCOME_STATUTORY, is_narrative=False`, but this
session's own earlier survey found them unclassified/narrative-shaped in the actual
corpus — a seed-authoring question for whoever owns `table_registry_seed.csv`, not
resolved here since none of the 72 anchors depend on it.

## Report

| Metric | Value |
|---|---|
| Anchors resolving through registry (of 75) | 72 anchor + 3 pending_extraction = 75 / 75 |
| Anchors keyed on stable table_type_id (not raw title) | 75 / 75 |
| DBS NII rows at FY25 | 3 distinct (was 1 collapsed) |
| `eps.basic` banks served | 3 / 3 |
| Derived concepts reconciling | 3 / 3 (2 pre-existing + verified, 1 became a direct anchor, DBS NII group-total reconciled manually — 4th, unplanned) |
| human_confirmed rows preserved | 89 → 104, MERGE invariant hash-verified, zero pre-existing rows altered |
| Interim anchors falsely flagged MISSING | 0 |

## Out of scope, confirmed still out of scope

Feature-stamping fixes (period leakage, legal_entity owner, unit promotion), the
18-doc re-extraction sweep, the extraction-schema pivot, DBS's `3Q25_trading_update`
doc-id prefix, and the OCBC naming slug — logged, not touched.

**New follow-ups surfaced this pass** (not attempted, logged for later): the
`row_dim.row_parent` mis-grouping bug in `pass2/geometry`'s hierarchy assignment for
DBS's income-statement table shape; a proper cross-segment aggregation mechanism in
the derivation layer (today's `compute_ratios.py` can't express "sum this concept
across two different segment values into the unsegmented total"); the
`table_registry_seed.csv` narrative-vs-typed inconsistency for OCBC's media-release
"Performance" summary blocks.
