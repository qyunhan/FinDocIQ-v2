# Concept nature: flow vs stock (IAS-1) — pivot (2026-07-27)

Amends `2026-07-14-concept-resolution.md`. Package: `findociq/pipeline/concept/`.
Target DB: `findociq/db/compiled_fs.db`.

## Why

The Streamlit chart/table over `fact_metric` surfaced a real mapping bug:
`pnl.provisions.total` (defined as "Expected credit loss charge... the cyclical,
volatile line" — a P&L flow) was also being stamped onto `as_at` balance rows
worth 15-20x more than the real quarterly charge, every period, for DBS. Root
cause: the dictionary alias `"allowances for loans and other assets"` matched
both the income-statement line AND the title of a Pillar3 credit-quality
disclosure table (a roll-forward ending in a **closing balance**, not a charge).
`load_into_concept_map()` seeded every dictionary alias as a wildcard
(`table_type='*'`), so it fired on both — a flow number and a stock number
silently shared one `concept_key`. A full corpus scan found the same pattern in
3 concepts (`pnl.provisions.total`, `.stage12_gp`, `.stage3_sp` — all three
ECL/allowance lines) plus one benign false-positive-shaped case
(`bs.assets.customer_loans_gross`'s alias legitimately appearing in two
compatible table types, not two different meanings).

## Pivot: `nature` is now a required field on every concept

`concept_dictionary.yaml` concepts declare one of:
- `flow` — income-statement/cash-flow amount for a DURATION (never legitimately
  `period_span='as_at'`).
- `stock` — balance-sheet amount AT A POINT IN TIME.
- `ratio_flow` — a ratio computed OVER a period (CIR, NIM, ROE, credit-cost-bps).
- `ratio_point` — a ratio computed AS OF a date (CET1, LCR, NSFR, CASA, LDR, NPL).

`load_dictionary.load_concepts()` requires it (`c["nature"]` — a `KeyError` on a
new concept that forgot to classify itself is the dictionary-lint).

## New mechanism 1: scoped_aliases (uses EXISTING infra, previously unused by the dictionary path)

`concept_map.table_type_norm` already let a type-scoped row beat a wildcard
(proved by the 19 NSFR rows) — the dictionary-seeding path just never used it;
every curated alias became `table_type='*'` unconditionally. A concept can now
declare:

```yaml
scoped_aliases:
  income_statement: ["ecl stage 3 (sp)"]
```

`load_into_concept_map()` inserts these as real `(table_type_norm, label_norm)`
rows instead of wildcards. Two concepts can now legitimately share one label
text as long as they're scoped to different buckets (e.g. `pnl.provisions.
stage3_sp` scoped to `income_statement`, `bs.credit.allowances_stage3_sp`
scoped to `credit_quality`+`customer_loans`).

## New mechanism 2: the alias-ambiguity gate

`resolve_llm._dedupe_residue()` already had the right instinct for the LLM
path: a label seen under >1 `table_type` in the corpus is "ambiguous" and isn't
promoted to a wildcard alias. `load_into_concept_map()` now applies the same
test to the dictionary path: before seeding a **wildcard** alias, it checks
`row_dim`/`table_t` for this DB — if the label is seen under >=2 distinct real
`table_type_norm` buckets, the wildcard is skipped and reported in
`ambiguous_skipped` instead of silently inserted. A dictionary maintainer must
then supply an explicit `scoped_aliases` entry (or decide it's a benign
same-meaning multi-bucket case, like `customer_loans_gross`, and scope it
anyway to make both contexts explicit).

## New mechanism 3: `_TYPE_NORM_RULES` — credit_quality bucket

Pillar3 ECL/allowance/NPA disclosure tables (`allowances_for_...`,
`non_performing_assets*`, `loss_allowance_coverage`, `loans_to_customers`)
previously fell through to `'*'` — meaning even a scoped guard couldn't have
distinguished them from "unclassified." Added a `credit_quality` norm bucket
covering these.

## New mechanism 4: `concept.validate()` nature checks

Two new checks, same `record()` pattern as the existing four (additive
identity / ratio formula / uniqueness / sums_to):
- `nature_flow_as_at` — a `nature='flow'` concept stamped `period_span='as_at'`.
  Empirically precise (in the pre-fix corpus: 2.3% of `pnl.*` rows, concentrated
  exactly in the 3 known-bad concepts).
- `nature_as_at_magnitude` — within one `(institution, concept_key, period,
  segment_key, geo_key)` group, an `as_at` value disagreeing >2x with a
  same-group duration-span value. General-purpose (doesn't require getting a
  concept's `nature` right to catch a magnitude-implausible pairing) —
  deliberately does NOT assert "a stock concept never gets a duration span":
  the corpus shows that's a common, benign stamping convention (bs.* concepts
  legitimately carry both `as_at` and quarter-labeled rows for the same date),
  so a strict span whitelist per nature would flood on real data (verified
  against `reg.liquidity.nsfr_ratio`, which is always stamped `1Q`/`NULL`,
  never `as_at`, despite being a point-in-time ratio by definition).

Both are pure functions (`_flow_as_at_flags` / `_as_at_magnitude_flags` in
`validate.py`) unit-tested directly in `test_concept.py` without a DB fixture.

Punch-list driver: `concept.audit_nature` (`python -m concept.audit_nature
[--db PATH]`) runs `validate()` and writes the nature-related flags to
`findociq/data/derived/concept_nature_conflicts.csv`, mirroring
`build_fact_metric._write_conflicts`'s CSV pattern. Re-run before/after a
dictionary edit to confirm the mismatch count actually dropped
(98 → 32 failed on `nature_flow_as_at` for this pivot).

## Concrete split applied this pivot

- `pnl.provisions.total` (flow) keeps `["allowances for credit and other
  losses", "allowance for credit and other losses", "impairment losses"]` as
  wildcards; `"allowances for loans and other assets"` is now scoped to
  `income_statement` only. `"total allowances"`/`"total allowance"` were
  REMOVED (corpus evidence: these phrases never appear in an income_statement
  table, only `credit_quality` ones — they were simply wrong for this concept,
  not ambiguous).
- New **`bs.credit.allowances_total`** (stock) owns `"total allowances"` /
  `"total allowance"` as wildcards, plus `"allowances for loans and other
  assets"` scoped to `credit_quality`.
- Same split pattern for `pnl.provisions.stage12_gp` /
  **`bs.credit.allowances_stage12_gp`** and `pnl.provisions.stage3_sp` /
  **`bs.credit.allowances_stage3_sp`** (`"ecl stage 1 and 2 (gp)"` /
  `"ecl stage 3 (sp)"` scoped to `income_statement` on the flow side,
  `credit_quality`+`customer_loans` on the stock side).
- `bs.assets.customer_loans_gross`'s `"gross customer loans"` alias moved from
  wildcard to scoped (`balance_sheet` + `customer_loans`) — a false-positive
  catch by the new ambiguity gate (same figure, same concept, legitimately
  disclosed in two table types — not a meaning conflict, just made explicit).

**One-time migration note:** `concept_map` is additive (`INSERT OR IGNORE`) by
design — reassigning an alias's ownership in the dictionary does NOT overwrite
an already-inserted wildcard row from a prior run; the old row silently keeps
winning until manually cleared. This pivot required a one-off `DELETE FROM
concept_map WHERE ...` for the 5 reassigned wildcard slots before re-running
`concept/run.py`. Not automated — this class of change (an alias changing
which concept owns it) should be rare now that the ambiguity gate catches new
instances before they're ever wildcarded.

## Known gaps, NOT fixed by this pivot (tracked, not silently papered over)

- **`pnl.profit.net_attributable`** still flags `nature_flow_as_at` (12 rows).
  Root cause is different: "Net profit" inside a Statement-of-Changes-in-Equity
  table gets `period_span='as_at'` because SOCE tables mix balance rows
  (Opening/Closing balance, genuinely as_at) with movement rows (Net profit, a
  flow) in one table, and period-span stamping isn't row-aware yet. The VALUE
  is correct; only the span tag is wrong. This is a `pass2`/stamp-layer fix,
  not a concept-dictionary one — left flagged rather than force-split into a
  concept that wouldn't be semantically right.
- **LLM-promoted residue aliases** from earlier runs (`"General"`, `"Specific"`,
  `"Non-impaired loans"`, `"Impaired loans"`, `"Less: General allowance"`,
  `"Specific allowance"` — 6 labels, 32 remaining `nature_flow_as_at` flags)
  predate this pivot and aren't covered by the new dictionary-path ambiguity
  gate (a separate insertion path in `resolve_llm.py`, which already has its
  own — different-shaped — ambiguity guard). Needs either a nature-aware
  reclassification pass or manual review via `concept_nature_conflicts.csv`.
- **`fact_metric_conflicts.csv`** (`resolved_by='conflict'` rows, e.g.
  `bs.assets.npa` colliding across `by_industry`/`by_loan_grading`/
  `by_collateral_type` breakdown tables) is a pre-existing, unrelated issue —
  those tables all stamp `geo_key='GLOBAL'` instead of a real breakdown key,
  so `build_fact_metric`'s own grouping can't tell them apart. Not touched here.
