# Design — Concept-tagging round-trip (tag workbook ⇄ DB, two Cloud flows)

Status: **design agreed, not built.** Resume in a later session. Captured 2026-07-29.
Concrete reference artifact: `~/Downloads/FinDocIQ_Tag_Highlights_FY25.xlsx` (finance's
hand-built example — treat it as THE spec for the workbook format).
The user will also pass an md + guidelines for concept mapping in a later session —
fold those in before finalising the concept model.

## Goal

Produce, per **table family**, an Excel workbook that finance uses to apply
**concept identities** to every extracted line item, then read the confirmed
workbook back into the DB. Coverage-first (comprehensive per table, never
sampled/thresholded — a skipped line item silently drops out of the time series
forever). The unit of work is the **table template**, not the label
(~20 tables × 3 banks ≈ 60 templates, tagged once each; inheritance identifies
future periods by label match).

## Two Cloud Run flows, with a HUMAN GATE between them

**These are two SEPARATE cloud flows, not one.**

**Flow 1 — Document → tagging Excel** (trigger: a document arrives / user selects)
```
PDF → [EXISTING extraction: run_doc STEP 0–3, load into schema_v7]
    → [NEW: pour this doc's rows into the table-registry TEMPLATE]
    → tagging Excel out  →  STOP (finance tags it)
```

**Flow 2 — Tagged Excel → cloud data** (trigger: finance re-uploads the tagged Excel)
```
tagged Excel → [NEW: read confirmed tags → write row_dim.concept_key (method='confirmed')]
            → [EXISTING downstream, UNCHANGED: concept resolution, period/nature
               resolution, build_fact_metric, compute_ratios, sync_bq → BigQuery]
```

The **"table registry template"** is the per-family structure the Excel is
generated *against*; the populated Excel is one document's rows poured into it.

## Hard principle — reuse the ORIGINAL logic at every step, never reinvent

The two new modules are THIN ADAPTERS that sit *between* extraction and the
existing concept→fact→BQ tail. All period/nature/flow-stock/loading logic stays
in the existing pipeline and is *called*, not re-implemented:

| Decision | Existing logic that owns it (reuse as-is) |
|---|---|
| period as-at date | `run_doc.infer_period` (+ `--doc-period`) |
| flow vs stock (accounting nature) | `nature` in `concept_dictionary.yaml`; "embed accounting nature" (commit `ac04f1b`); `period_span` in `compute_ratios` |
| load rows into schema_v7 | `load_v7` / `load_doc` (STEP 3) |
| concept → fact_metric (period_span, segment/geo keys) | `concept/run.py`, `build_fact_metric.py` |
| into the cloud | `ingest/sync_bq.py` |

**TODO before building:** pin the exact call sites/signatures of `load_v7`,
`build_fact_metric`, the nature-resolution code, and `run_doc` STEP 4b/4c/7 so
the adapters wrap them precisely (a short focused read).

## Resolution rules — AGREED (2026-07-29), the spec for the key logic

**1. Dimension-key resolution precedence** (for `geo_key`, `segment_key`, `period`):
walk this chain, first hit wins —
```
parent  >  row label  >  col label  >  table title  >  section title
```
(i.e. inherit from the row's parent first; else the row's own label; else the
column's label; else the table title; else the section title.) Applies uniformly
to geo, segment, and period. Partly present in `pass2/load_v7.py` /
`concept/discover_cuts.py` — VERIFY it follows this exact order and fix where it
doesn't.

**2. Period granularity + stock/flow.** Period must distinguish, not just carry a date:
- spans: **quarter | 9M | half (1H/2H) | full-year (FY/12M)** — plus as-at point for stocks.
- **stock vs flow** is the concept `nature` (already required in
  `concept_dictionary.yaml`: `flow` / `stock` / `ratio_flow` / `ratio_point`, validated
  by `concept/audit_nature.py`). A **flow** binds to a *span* (Q/9M/H/FY); a **stock**
  binds to an *as-at point*. The two must never be conflated in `period_span`.
- **KNOWN GAP** (found while building the tag workbook): the **column-level
  `period_span` resolver leaves full-year columns NULL** — e.g. DBS
  `selected_income_statement_items_m` col "Year 2025" has `col_period=NULL,
  period_span=NULL`. The full-year span isn't resolved. `tag_workbook.py` currently
  works around this by label-matching the FY column (`select_fy_column`); the PROPER
  fix is in the column period-span resolver (resolve "Year 2025"/"Full year …"/
  "financial year ended … 2025" → period `2025-12-31`, span `FY`). Do this in the
  original resolver, not the tagger.

## The two NEW modules

- **`tag_workbook.py`** (Flow 1 tail) — read what extraction loaded, pour into the
  template, machine-pre-fill everything derivable, emit the workbook.
- **`tag_ingest.py`** (Flow 2 head) — read confirmed tags, `UPDATE row_dim.concept_key`
  + append `concept_resolution_log(method='confirmed')`, then hand off to the
  EXISTING STEP 4b/4c/7. `unique_row_id` (col 1 of every sheet) is the unambiguous
  join key back to `(doc_id, table_id, row_id)`.

## Identity model — a tuple, not a tag

**A cell's identity = row-identity × column-identity. Both axes get tagged;
today only rows carry `concept_key`.**

- **Date-column families** (e.g. Highlights): row = `(concept, agg_role, group)`;
  column = `period`.
- **Dimension families** (e.g. NPA by stage): row = `row_concept`;
  column = `(breakdown_axis, dimension_member)` — the cell inherits BOTH. This is
  the case the current schema can't express (it resolves periods onto cells but
  never tags a column with a semantic axis+member), so NPA tables lose their axis.

Decoded from the example artifact:
- **`Agg role`** dropdown = `total | component | atomic` (sum-tree role; the
  arithmetic the machine verifies via `sums_to`).
- **`Group #`** = `G1, G2, …` (which components roll up into which total).

## Machine / human split (the sharp line)

| Machine (derivable — pre-filled) | Human (manual, in Excel) |
|---|---|
| label, value, unit, **segment, geography, period**, agg_role, group, arithmetic checks (children→parent), AI-*proposed* Concept ID | **Concept identity** — cross-bank equivalence, naming new/bank-unique concepts, resolving flagged questions |

- **Concept-identity tagging is a POST-PROCESSING step** — comprehensive, manual,
  three banks side-by-side (structural: OCBC "non-impaired" vs DBS "Stage 1 and 2"
  can only be judged with all three in view; sequential → mutually-incompatible
  vocabularies).
- **Bank-unique items are first-class, named by WHAT not WHO**:
  `bs.assets.excl_life_insurance_funds`, never `ocbc.insurance_assets`. Dictionary
  will grow to several hundred entries — fine if the namespace stays disciplined.

## Workbook format (from `FinDocIQ_Tag_Highlights_FY25.xlsx` — the spec)

Four sheets per table-family workbook:
1. **`START HERE`** — instructions.
2. **`TAG — <family>`** — 3 banks stacked, bank-section header rows. Columns:
   `Bank | # | Blk | Line label as printed | <period value> | Unit | Concept ID | Agg role | Group # | Note/question`.
   Two dropdowns: **Concept ID** ← `'Concept Dictionary'!$A$5:$A$n`; **Agg role** ← `"total,component,atomic"`. Applied to all data rows.
   *(Dimension families additionally need axis/member + segment/geo columns.)*
3. **`Concept Dictionary`** — `Concept ID | Meaning | New?` (NEW = newly-introduced,
   highlighted). Drives the Concept ID dropdown.
4. **`Coverage check`** — per-bank COUNTIF: extracted / tagged / still blank /
   flagged-with-a-question. Coverage, not throughput.

## Three kinds of empty (must be distinguishable in the schema)

1. **Not disclosed by this bank** (structural — OCBC has it, UOB never will) —
   knowable ONLY because tagging is comprehensive: concept ∈ family universe AND
   (bank, concept) ∉ tagged rows.
2. **Disclosed but not yet extracted** (a real gap to chase).
3. **Disclosed as zero.**
Today all three are NULL and indistinguishable. The dashboard must be able to say
"not disclosed" instead of showing a hole — so the schema has to record it.

## First slice (when we build)

Reproduce the example: **Highlights / Selected income statement**
(`selected_income_statement_items_m` and per-bank equivalents) across DBS/OCBC/UOB
for **FY25**, generated from the DB and machine-pre-filled. Match the hand-built
sheet, then generalise the registry to the ~20 families.

Table families already exist in the DB as `table_t.table_type` values
(`selected_income_statement_items_m`, `selected_balance_sheet_items_m`,
`key_financial_ratios_2_3`, `non_performing_assets_and_loss_allowance_coverage`,
`by_industry`, `by_loan_grading`, …). The registry maps a family →
per-bank `table_type` (banks name tables differently, e.g. OCBC
`selected_income_statement_items_1st_half_2025`).

## Where it runs — Cloud Workstation

`w-yunhan-ms4efqkw` (cluster `cluster-ms4dwnq3`, config `config-ms4dwgkw`) is
RUNNING. Plan: clone the repo there (branch `v2-concept-toolkit` — **pushed to
origin**, so the clone has today's work), install requirements (+paddle), then:
1. extract the FY25 FS docs for all 3 banks (family-aware paths + column repair
   already committed),
2. run `tag_workbook.py` for the Highlights family,
3. finance tags, re-upload,
4. `tag_ingest.py` → existing tail → BQ.
ADC works inside the workstation via the default compute SA (editor) — no keys.

## Current repo state (2026-07-29)

- Branch `v2-concept-toolkit` pushed to `origin` (github.com/qyunhan/FinDocIQ).
- Committed this session: family-aware output paths + `--out-root` fix + conditional
  sheet suffix (`79d8849`); section-region validators / column-band repair
  (`95c42cb`); repair test; source→GCS migration Tasks 1–3 + rekey migration
  (Task 4 review-pending) — see `docs/superpowers/plans/2026-07-29-gcs-source-migration.md`
  (that migration is PAUSED at Task 4).
- IAM: user is `roles/editor`; concept-tagging Cloud flows run under the default
  compute SA (editor) — no new IAM needed for the flows themselves. The dedicated
  minimal SA / Streamlit trigger is deferred pending `projectIamAdmin`
  (see `docs/2026-07-29-dashboard-trigger-pending-access.md`).

## Open items / next-session checklist

- [ ] Ingest the user's incoming concept-mapping md + guidelines; reconcile with this design.
- [ ] Pin exact existing call sites (`load_v7`, `build_fact_metric`, nature resolution, STEP 4b/4c/7).
- [ ] Decide the schema change for the identity tuple (column-side axis/member tag; the three-kinds-of-empty representation).
- [ ] Build `tag_workbook.py` (first slice: Highlights/FY25, 3 banks) → match the example.
- [ ] Build `tag_ingest.py` → route through the existing tail.
- [ ] Wrap Flow 1 and Flow 2 as two Cloud Run flows.
- [ ] Extraction coverage: confirm FY25 FS extracted for all 3 banks (may need a run first).
