# Period logic: uniform column-label rule (supersedes stock=date/flow=label)

Follows the same-day read-only audit that found the `concept_period_kind`
value_kind branch was a hybrid — new columns present, correct for 6 ratio
concepts, silently wrong for every other stock concept. This pass replaces
the branch with the simpler rule directed: **every cell carries its column
label; two cells share a period iff they share `period_end`. No stock/flow
decision at stamp time.**

## Step 1 — the branch removed

`v_cell`/`v_cell_flat`'s `period_label` CASE, before:
```sql
CASE
    WHEN f.period_span IS NULL OR f.period_span = 'as_at' THEN f.period
    WHEN COALESCE(cpk.kind, 'annualised') = 'point_in_time' THEN f.period  -- REMOVED
    ELSE f.period_span || substr(f.period, 3, 2)
END
```
After: the `point_in_time` branch is gone. `period_label` is always the
column's own label (`'FY25'`, `'2H25'`, `'4Q25'`) unless the column is
`as_at`/unspecified, in which case it's the date itself (a literal-date
column has no other label to show). `period_end` (`= cell_fact.period`,
already correct, unchanged) is the only thing that decides whether two cells
are "the same point."

`concept_period_kind` (6 rows) is kept as a table — unused by stamping now,
available if a serving/display layer later wants a ratio's own
point-in-time-vs-annualised flag for *rendering*, which is a different
question from what gets stored.

## Step 2 — the collapse verified via period_end

DBS `bs.assets.total`, three columns of the same table:

| col_leaf_label | period_label | period_end | value |
|---|---|---|---|
| Year 2025 | FY25 | 2025-12-31 | 897,488 |
| 2nd Half 2025 | 2H25 | 2025-12-31 | 897,488 |
| 1st Half 2025 | 1H25 | 2025-06-30 | 841,896 |

Three labeled cells, **two distinct `period_end`s** — FY25 and 2H25 share
both the date and the value (the same balance, printed under two panel
headers), 1H25 is its own point. `pnl.income.total` over the identical three
columns: 22,900 / 11,263 / 11,637 — three labels, three distinct values, no
collapse (a flow genuinely differs by duration). Confirmed for DBS, UOB
(572,061 / 572,061 / 537,838 for `bs.assets.total`), OCBC (analogous).

## Step 3 — year-agnostic integrity assertion

Per (bank, concept, doc row, year), pulled matching FY/1H/2H triples and
classified: `FY ≈ 1H+2H` -> flow; `FY = 2H ≠ 1H` -> stock; neither -> bug.

First pass (all concepts, 2% tolerance): 26 flow, 14 stock, **24 flagged**.
Every flagged case was a ratio (ROE, ROA, NIM, CIR, NPL, CET1) or EPS — a
ratio's FY figure is its own period-specific computed rate, neither a sum
nor a static repeat of its halves; not a stamping defect, a real third
category the binary rule can't and shouldn't try to classify. Excluding
ratios/EPS and widening tolerance to 5% (ordinary $m-rounding — e.g. UOB
amortisation 31 vs 16+14=30) leaves **zero unexplained failures** across 38
flow/stock triples (27 flow, 11 stock).

## Step 4 — anchor projection re-applied, two real bugs found doing it

Re-running `migrate_add_human_anchor_projection.py` (required regardless —
the last reload wiped `row_dim.concept_key_human`, coverage 6/23/19/38
across the 4 reloaded docs) surfaced that a straight re-run did **not**
restore coverage to what it should be. Root cause: the script's address
computation had never received two fixes made to `resolve_anchors.py` during
the row-level anchor pass, so the two scripts compute different addresses
for the same row and `bank_line_map` lookups silently miss:

1. **No title-like-parent collapse.** DBS's row_lineage carries the table's
   own title at `lvl1` on every row (not a real grouping); `resolve_anchors.py`
   already collapses this to an empty parent to match the corpus's stored
   convention. The projection script didn't, so it computed
   `parent='Selected income statement items ($m)'` instead of `''` for DBS's
   `Total income` row — never matching `bank_line_map`'s `''`-keyed entry.
   Ported the same per-`(doc_id, table_id, depth)` collapse.
2. **No raw-label preference.** UOB's row_lineage carries the
   footnote-RESOLVED display form (`'Total assets 5 72'`); `bank_line_map`'s
   addresses were written from the footnote-clean raw form
   (`row_dim.row_leaf_label`, `'Total assets'`). Ported the same preference.

Both fixes verified on the exact rows that exposed them (DBS `Total income`,
UOB `Total assets`), then confirmed at scale:

| Metric | Before this pass | After |
|---|---|---|
| Period resolver branches on value_kind at stamp | yes | **no (removed)** |
| Stock FY-col vs 2H-col → same period_end + value | no (untested/broken) | **yes**, confirmed all 3 banks |
| bs.assets.total points across half-year grain | 3 (no collapse) | **2** (Jun30, Dec31) |
| pnl.income.total points | 3 | **3** (unchanged, correct) |
| FY ≈ 1H+2H integrity check | not run | **pass — 0 unexplained** (27 flow, 11 stock; 24 ratio/EPS correctly excluded as a 3rd category) |
| concept_key coverage, 4 reloaded docs (rows stamped) | 6/23/19/38 | **29/23/19/38**, all 14 sampled spine concepts now resolve for all 3 banks (except DBS EPS/NAV — the known, already-logged extraction gap) |
| DBS NII segment split | 3 distinct (already restored last run) | 3 distinct, unchanged |
| human_confirmed rows (MERGE invariant) | 104 | 104, hash-stable through this pass too |
| legal_entity populated, 4 reloaded docs | 100% | 100%, unchanged (verified after re-running the view-owning scripts) |

## Constraints honored

No re-extraction, no reload — the whole change is view-level (`period`/
`period_span` were already correct; only their presentation changed) plus
one address-computation fix to an existing migration script. `legal_entity`
confirmed still loader-owned and 100% populated after all script re-runs.
Logged, not fixed: the UOB title-context bare-year gap (unchanged); the two
view-owning migration scripts clobbering each other if run out of order
(hit again this pass — ran anchor-projection before period-label, in the
order the clobbering requires, exactly as logged last time) — still not
merged into one script, still a trap for whoever runs these next.

## Stale `col_period` is derived data, not extracted data (2026-08-04)

Fixes pre-flight **B6**, and the largest single slice of **D2**.

`load_v7`'s period grammar is improved over time (this file's own 2026-08-03
entry taught it digit ordinals + `Qtr`/`Q` and `Year YYYY`). A grammar fix only
reaches rows that are RE-LOADED, and re-loading means re-extraction — so that
fix shipped with "STEP-3 reload of all docs" as its delivery mechanism, and the
reload never happened. 39 columns across the three `*_trading_update` documents
kept `col_period IS NULL` on labels the grammar now parses perfectly
(`4th Qtr 2024`, `1st Qtr 2022`, `9 Mths 2025`). Every cell in them fell through
the precedence chain (col > row > table > doc) to the DOC period, so six
quarters of columns collapsed onto one date and competed for one `fact_metric`
grain slot — exactly the cross-period contamination predicted when the grammar
fix was written.

**`col_period` is a pure function of the column's stored label**, so it can be
re-derived in place — no re-extraction, no API cost. `pass2/backfill_col_period.py`
replays the loader's OWN decision (it calls `is_period_text` / `parse_period_span`
with the same `column=True` context and the same leaf-beats-group-banner
precedence) rather than carrying a second grammar that could drift. It re-stamps
only cells whose `period_source` is a fallback bucket, so a `row`- or
`col`-sourced period still outranks it. Idempotent; re-run it after any future
grammar change. Verified: 0 disagreements between stored `col_period` values and
the current grammar corpus-wide, so the backfill is purely additive.

### B6's signature was a table-level proxy, and it was wrong in both directions

B6 asked "does this table have >= 2 distinct `col_period`?" as a stand-in for
"was a period available for this cell?". It is not one:

- **False positives.** It flagged the periodless comparison-delta columns
  (`% chg`, `+/(-) %`, `YoY (%)`, `Volume`, `Rate`) that merely sit *beside*
  real period columns. All 127 cells it flagged were of this kind — the same
  legitimate periodless category every prior pass had excluded by hand.
- **False negatives.** A column labelled `4th Qtr 2024` whose label failed to
  parse — the ACTUAL defect — was caught only if its table happened to have
  >= 2 OTHER parsed periods.

Proof it measured the wrong thing: fixing the real defect made the proxy
**worse** — backfilling drove the true signature 36 -> 0 while the proxy count
rose 63 -> 73, because adding real column periods makes more tables qualify as
"multi-period" and drags in more innocent `% chg` columns.

B6 now asserts `load_v7`'s own **GATE A2** condition post-hoc: a spine cell
whose OWN column label is period-shaped by the loader's gate yet carries no
`col_period` — the period was derivable and was not derived. The check calls the
loader's functions, so gate and invariant cannot drift apart.
