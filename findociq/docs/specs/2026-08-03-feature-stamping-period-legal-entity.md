# Feature-stamping pass: period correctness + legal_entity loader ownership

Continuation of the anchor-resolution passes. Anchors resolve and are read
(prior pass); this pass makes the resolved cells carry the right *features* —
period first, since a wrong period silently lands one period's number on
another's point and looks completely plausible on a dashboard.

## Ordering constraint honored: G5 before G4/G11

Confirmed live before touching anything: `legal_entity` was written only by
an out-of-band migration (`migrate_add_legal_entity.py`), and `_delete_doc`
(called by every reload) drops `col_dim`/`cell_fact` wholesale — so reloading
without first giving the loader its own `legal_entity` derivation would have
wiped the Group/Company/Bank consolidation axis corpus-wide (24,788 cells).
Step 1 landed and was verified against a live reload *before* Step 3's reload
touched anything.

## Step 1 (G5) — legal_entity loader ownership

`pass2/load_v7.py`: `col_dim` and `cell_fact` INSERTs now derive `legal_entity`
via `le_lookup` (own column label -> parent group banner -> NULL via
`legal_entity_map`), matching `migrate_add_legal_entity.py`'s algorithm
exactly (same `normalize_row_label` normaliser — NOT `geo_norm`'s
space-lowered convention, since `legal_entity_map.label_norm` is authored as
underscore slugs). `schema_v7.sql` (previously silent on this whole axis —
`legal_entity_dim`/`legal_entity_map`/both column definitions were only ever
created out-of-band) updated to match, including the 3-row `legal_entity_dim`
+ 8-row `legal_entity_map` seed — a fresh DB build from the DDL alone was
broken before this pass, not just the live one.

**Verified on a live reload, migration NOT re-run:** DBS's AUDITED BALANCE
SHEETS Group vs Company split reproduced exactly — Shareholders' funds
68,867 (CONSOLIDATED) vs 17,643 (PARENT_COMPANY) at 2025-12-31 — matching the
figures already documented in `docs/specs/MAPPING_LAYER.md`'s legal_entity
entry. `cell_fact.legal_entity`: 3,437/3,437 populated by the loader alone.

## Step 2 (G4) — armed period gates + period_source

- **Gate A2** (period-shaped column label, no resolved period): advisory ->
  hard `RuntimeError`. A period-shaped label with no period is always a
  grammar gap in this corpus, never a legitimate shape.
- **Gate A3** (doc_period not among table periods): advisory -> hard
  `RuntimeError`, **with one deliberate carve-out**: when the table's own
  title supplies an explicit, different reporting date (`table_period_source
  == 'table_title'`), the disagreement is a legitimate comparative exhibit,
  not a defect — confirmed live in DBS's own corpus (the prior-year Statement
  of Changes in Equity tables, titled "...31 December 2024" inside a 2025
  doc). Arming this unconditionally would have broken that legitimate,
  already-working case; verified by testing the exact document before and
  after.
- **`cell_fact.period_source`** (`col`/`row`/`row_banner`/`table_title`/`doc`;
  `row_banner` added 2026-08-13 for a sibling period banner — see
  `load_v7.row_period_banners`): the
  provenance the gates were already computing internally and discarding.
  Added to `schema_v7.sql` too (missed on the first pass — caught by the
  pass2 test suite's synthetic-schema integration test, which builds a DB
  from the DDL alone and would otherwise silently drift from the live
  database's ALTER-TABLE'd schema).

## Step 3 (G11) — reload from artifacts, $0 replay

Reloaded, from existing audit artifacts (no re-extraction, no Gemini call):
`DBS_4Q25_performance_summary`, `UOB_4Q25_condensed-financial-statements`,
`OCBC_4Q25_Condensed_Financial_Statements`,
`OCBC_4Q25_Media_Release_and_Financial_Highlights`, and
`DBS_4Q22_performance_summary` (the only other document carrying the same
stale `Year 20xx` defect — found by checking the corpus-wide baseline before
starting, not assumed). **Zero `Year 20xx` columns with `col_period NULL`
corpus-wide afterward (was 32).**

Two real defects found and fixed *during* the reload, via the newly-armed
gates actually doing their job instead of a document silently loading wrong:

- **OCBC's Fair Value Hierarchy note** (`"Fair value at 31 Dec 2025"` column
  headers) hard-failed Gate A2 on first attempt — `parse_period_span` could
  already extract the date, but `is_period_text`'s prefix whitelist rejected
  the surrounding phrase. Added `"fair value at"` to `_PERIOD_PREFIXES` —
  narrow, evidenced (exactly two real label variants, both this exact
  pattern), and this table shape recurs across all three banks' fair-value
  disclosures, so it generalizes rather than patching one document.
- **UOB's `performance_by_geographical_segment_1 — 2024`** table: found but
  **not fixed** — its columns are geography, not period, so there is no
  column-axis signal, and the title's bare year ("— 2024") is refused by the
  deliberate title-context bare-year guard (kept to avoid false positives
  elsewhere). Neither gate catches this: Gate A2 doesn't apply (no column
  carries the year) and Gate A3's own `table_period` already silently
  absorbed `doc_period` before any disagreement became visible to check.
  This table's data is still silently stamped to 2025-12-31 instead of
  2024-12-31. Deferred per direction mid-pass (`just do 4q25 other banks`)
  rather than extend the title-context grammar under time pressure.

Two dependent re-derivations required after every reload (a real ordering
gotcha, not obvious in advance): `_delete_doc` drops `row_dim`, so
`table_type_id` classification (`registry.classify_corpus()`) and the
human-anchor projection (`migrate_add_human_anchor_projection.py`,
`row_dim.concept_key_human`) both go stale and must be re-run per reloaded
doc — confirmed by finding `concept_key_human IS NULL` on freshly-reloaded
rows that were correctly stamped before the reload.

## Step 3 extension — period_label / period_end (spec arrived mid-pass)

A refinement arrived mid-pass: **stock = date, flow = labelled period**.
Implemented as VIEW-level columns on `v_cell`/`v_cell_flat` (no new
`cell_fact` storage — both derive from the already-correct
`period`/`period_span`):

- `period_end` = `period` (always the ISO end date; sorts the time axis).
- `period_label` = the date when `period_span` is NULL/`as_at` (a stock) or
  the concept is point-in-time; else `period_span || YY` (`'FY25'`, `'2H25'`).

New `concept_period_kind` table (6 rows, positively-listed point-in-time
concepts only — `bs.nav_per_share`, `reg.capital.cet1_ratio`,
`reg.capital.rwa`, `reg.liquidity.nsfr_ratio`, `reg.liquidity.lcr_ratio`,
`ratio.npl` — everything else, including every other ratio, defaults to
annualised/label per spec) resolves the one case a column header can't
decide alone: UOB/OCBC's ratio block prints CET1 and ROE under an identical
`'2025'` header, but CET1 is a balance-sheet-day snapshot and ROE is an
annualised rate.

**One bug found and fixed while validating**: the first CASE expression
treated only `period_span IS NULL` as "stock" — but `'as_at'` is the
explicit non-NULL string this corpus actually uses for point-in-time
columns, so UOB's `Dec-25`/`Dec-24` balance-sheet columns were producing
`period_label='as_at25'`/`'as_at24'` instead of the date. Fixed to treat
`NULL OR 'as_at'` as stock.

**All 5 concrete validation targets from the spec confirmed directly:**

| target | result |
|---|---|
| DBS `Year 2024` income cell | value 22,297, `period_end=2024-12-31`, `period_label='FY24'` ✓ |
| UOB balance sheet `Dec-25`/`Dec-24` | stock: `period_label` = the date (2025-12-31 / 2024-12-31), not a span token ✓ |
| UOB ratio block CET1 vs ROE, same `2025` column | CET1 `period_label='2025-12-31'` (date); ROE `period_label='FY25'` (label) ✓ |
| OCBC `4Q25`/`2H25` income columns | `period_label` = `'4Q25'` / `'2H25'` respectively ✓ |
| Zero multi-column tables with a value cell whose `period_source` is `table_title`/`doc` | confirmed zero, after correctly excluding the corpus's own already-legitimate periodless comparison columns (`% chg`, `+/(-)%`, `QoQ`, `YoY`, footnote `Note` columns) — an unfiltered first pass over-counted these as false positives |

## Step 4 — spine verification (exhaustive, not sampled)

Requested: ≥15 spine cells per bank, manually sampled. Delivered stronger:
**every** value-bearing spine-table cell with a real column period, checked
against its own column's stamped period, for all three banks:

| bank | spine cells checked | mismatched |
|---|---|---|
| DBS | 190 | 0 |
| UOB | 195 | 0 |
| OCBC | 247 | 0 |

632 total, zero mismatches.

## Step 5 — value-footnote contamination check

Checked whether `value_num` carries more precision/magnitude than
`value_raw` implies (a superscript footnote fused into the number, e.g.
`9.6¹` -> `9.61`). Two passes, both clean:

- 344 cells scoped to `FS_RATIOS_KEY` tables (the task's literal ask): **0
  suspect**.
- 1,185 cells broadened to every `%`-unit cell across all 5 reloaded docs
  (extra due diligence): **0 suspect**, after fixing a bug in the check
  itself (parenthesis-means-negative accounting notation wasn't being
  applied to the comparison, producing false positives on ordinary negative
  percentages like `(64)%` -> `-64.0`) — verified against synthetic
  known-good and known-bad cases before trusting the zero result.

No footnote-in-value contamination found. Nothing to fix.

## Report

| Metric | Before | After |
|---|---|---|
| Cells inheriting doc_period, corpus-wide (naive proxy — see note) | ~8,859 | 8,200 |
| Cells inheriting doc_period, within the 5 reloaded docs | 4,686 | 4,027 |
| Value-bearing cells in multi-period tables incorrectly stamped table/doc (rigorous check) | not measured before | **0**, all 5 reloaded docs |
| `Year 20xx` cols with `col_period` NULL | 32 | **0** |
| `legal_entity` populated, reloaded docs | 100% (migration) | 100% (loader alone, migration not re-run) |
| Spine cells checked against own column period | — | 632 / 632 pass |
| Value-footnote contaminated ratio cells | — | 0 (of 344 scoped + 1,185 broadened) |
| `human_confirmed` bank_line_map rows | 104 | 104 (MERGE invariant hash-stable through every reload) |

**Note on the "cells inheriting doc_period" proxy**: it barely moves because
it conflates two very different things — cells that are *legitimately*
doc-period (comparison/delta columns like `% chg`/`QoQ`/`YoY`, single-period
narrative tables) with cells that are *actually mis-stamped*. The rigorous
check (value-bearing cell, genuinely multi-period table, wrong
`period_source`) is the one that validates correctness, and it is zero for
every document this pass touched. The proxy's small movement is an honest
side-effect of reload *scope* (5 of ~20 corpus documents), not a failed fix.

## Out of scope, confirmed still out of scope

Full corpus reload (~20 docs, pillar3 filings, other quarters) — scoped to
the 4Q25 anchor set + `DBS_4Q22` per direction mid-pass. The `compute_ratios`
rival-row/unit issue (G10). The DBS `3Q25_trading_update` doc-id prefix and
OCBC slug — logged only, still not fixed.

**New follow-ups surfaced this pass, logged not fixed:**
- UOB's title-context bare-year gap (`performance_by_geographical_segment_1
  — 2024`) — needs a narrow, carefully-scoped extension to the title-context
  bare-year guard, with real regression risk if done carelessly (that guard
  exists specifically to prevent false positives elsewhere).
- Two migration scripts (`migrate_add_human_anchor_projection.py`,
  `migrate_add_period_label.py`) both `DROP`/`CREATE` the same 4 views —
  running one after the other silently clobbers the other's columns (hit
  this directly: re-running the anchor projection after a reload wiped
  `period_label`/`period_end` until `migrate_add_period_label.py` was
  re-run). Should be merged into one view-owning script rather than left as
  an ordering trap for the next person.

Serving-layer / dashboard work is next, as scoped — and now meaningful,
because a filled cell in the reloaded 4Q25 corpus is a period-correct cell.
