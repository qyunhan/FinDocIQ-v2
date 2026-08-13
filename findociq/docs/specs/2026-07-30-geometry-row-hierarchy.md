# Spec — PASS2 geometry stage: PDF typography is authoritative for row hierarchy

Status: **shipped** — stage wired into `run_doc` as STEP 2b; loader consumes the
side-car; `table_t.hierarchy_source` records which branch fired.
Date: 2026-07-30
Code: `pipeline/pass2/geometry.py`, `pipeline/pass2/transforms.py::apply_geometry`,
`pipeline/pass2/load_v7.py`, `pipeline/run_doc.py::step2b_geometry`
Tests: `pipeline/pass2/test_geometry.py` (13), `pipeline/pass2/test_geometry_load.py` (18)
Decision record: `docs/DECISIONS.md`, entry *2026-07-30 — Row hierarchy: PDF
geometry becomes authoritative over model-emitted levels*

## Problem

`GRow.level` — the model's own notion of nesting depth — is the ONLY input the
loader has for `row_dim.row_parent` (`row_parents_by_position`). So every level
error becomes a parentage error, and parentage is what `row_lineage` (the
cross-document registry identity) is built from.

The field conflates "this is a data row" with "this is visually indented", and
it wobbles between tables of the **same** document. Confirmed on
`DBS_1Q26_trading_update`:

| defect | printed page | model output |
|---|---|---|
| mis-nesting | `Expenses` … `Reported net profit` are flush-left siblings | `Expenses` level 1, `Reported net profit` level 0 → rows 10–21 parented under `Markets trading income` |
| mis-nesting | per-share `Net book value` is flush-left | leveled like the indented `Basic`/`Diluted` → parented under `Reported earnings` |
| phantom twin | one printed line `Commercial book total income` | TWO rows: a valueless `section_header` + an identical-label `data` twin |
| footnote pollution | `Return on equity` with superscript `4, 5` | label `'Return on equity4, 5'` → lineage + concept alias carry the footnote numbering |

### Why this is not a prompt problem

The extraction prompt is inline at `extract.py:65` and is shared by BOTH the `fs`
and `pillar3` branches with no router split. **Pillar 3 uses the same prompt and
the same loader and its hierarchy is correct** — P3 is a fixed regulatory
template whose valueless section headers exactly match the model's level scheme
(per-doc phantom-dup counts: DBS 1Q26 FS 2-in-4-tables vs P3 1-in-8-tables). It
is freeform FS typography — bold rows that CARRY values, space-rendered indents,
mid-table totals — that breaks it. Editing the shared prompt would flip
`_PROMPT_HASH` and invalidate ~324 cached units (≈$5.50 of re-extraction plus a
model-nondeterminism re-roll) to fix a defect that is deterministically
recoverable from the PDF text layer.

## The stage (STEP 2b)

Runs after extraction, before every load, over every audit unit. In-process,
$0, no model call. Reads the unit's `pages.pdf` (or materializes the source PDF
from GCS) with pdfplumber and writes a **side-car** into `parsed.json`:

```json
{"geometry": {"source": "pages.pdf" | "source_pdf" | "unavailable",
              "tables": [{"rows": [{"line_id", "indent", "label_clean"}, ...],
                          "title_clean", "col_labels_clean",
                          "all_rows_matched", "band_calibrated"}]}}
```

It is a side-car and NOT new `GRow` fields precisely because adding fields to the
pydantic model would change the Gemini response schema — the same cache
invalidation the prompt edit would have caused.

Every threshold is expressed **relative to font size**, never in absolute points,
so it generalises across banks rather than being tuned to one PDF:

| step | rule |
|---|---|
| line clustering | chars grouped by `top`, tolerance `0.55 × modal char size` (keeps detached superscript runs attached to their row, stays under row pitch) |
| superscript test | `size < line_median_size − 0.5` **AND** `top < line_median_top − 0.15` — both required. **No digit regex**: a footnote marker is identified by how it is PRINTED, not by looking like a number |
| label band | chars left of the value-column band start, calibrated by reusing `transforms._calibrate_bands` |
| `ink_x0` | x0 of the first non-space, non-superscript char — banks indent with leading SPACES, so the raw first-char x0 is not indent-safe |
| indent depth | single-linkage cluster of `ink_x0` over the table's matched rows, threshold `0.5 × modal char size`; depth = cluster rank, leftmost = 0 |
| row↔line alignment | monotone forward scan; a row may re-match the PREVIOUS row's line (the twin case) but never an earlier one; 2–3 line wrap-merge for word-wrapped labels |

## The routing branch (visible, per table)

`transforms.apply_geometry` is **all-or-nothing per table**. The geometry branch
fires only when the side-car matched EVERY row of that table
(`all_rows_matched`), the row counts agree, and every matched row carries an
indent. Any shortfall and that table falls back WHOLLY to today's model-level
behaviour — a partial override would mix two incompatible depth scales inside
one parent walk.

**Where a human SEES which branch fired**, without reading code:

- `table_t.hierarchy_source` — `'geometry'` or `'model'`, one row per table,
  queryable and surfaced in the app's Table Registry / Database views.
- STEP 2b prints per-unit `tables_matched a/b  rows_matched c/d` and a TOTAL.
- Load warnings name the table and the geometry action taken
  (`… : geometry — geometry merged N phantom printed-line twin row(s) …`).
- The app's Ingest stepper gained a `geometry` stage between `extract` and `load`.

When the branch fires, two rewrites happen in order:

1. **Printed-line twin merge.** A run of consecutive rows sharing one `line_id`
   is ONE printed line the model emitted twice. The valued row survives, the
   valueless phantom is dropped. A run with NO valued row is a real section
   header printed once — keep the first. A run with SEVERAL valued rows is not a
   twin pattern geometry can adjudicate — keep them all and warn.
2. **Depth override.** `level := indent`. The parent walk then runs on corrected
   depths with `skip_terminal=False`.

### Why the total/note skip is disabled under geometry

`row_parents_by_position` normally refuses a `total` row as a parent candidate.
That rule exists to survive WRONG levels (a mis-leveled mid-table total would
otherwise swallow the following block — the DEBTS ISSUED defect). On *geometric*
depths it becomes harmful: DBS prints `Of which: Net interest income` indented
directly beneath `Total income`, and it genuinely IS its child. The skip is
therefore disabled exactly on the tables where the depths are geometric, and
kept everywhere else.

## Clean labels

Labels are **never rewritten**. `GRow.label` / `col_leaf_label` / `table_title`
stay byte-identical to the source document (they are the evidence the verifier
eyeballs against the PDF). The typographically-stripped forms land in new
nullable columns — `row_dim.row_leaf_label_clean`, `col_dim.col_leaf_label_clean`,
`table_t.table_title_clean` — NULL wherever geometry did not match.

Identity paths that now PREFER the clean label (falling back to verbatim, so
documents not yet re-loaded behave exactly as before):

- `row_lineage` — the cross-document registry key. `'Return on equity4, 5'` and
  `'Return on equity3, 4'` are the same line item in two quarters; the footnote
  numbering must not fork the registry.
- geo / segment / **industry** row-axis lookups — these are EXACT normalised
  full-label equality, so a footnote marker silently loses the stamp entirely
  (`'Others2'` in UOB's NPL-by-industry table).
- `concept.resolve_deterministic._fetch_rows` — which also feeds the LLM
  residue, so accepted answers stop minting footnote-polluted `concept_map`
  aliases. Three such aliases already exist (`'return on equity4 5'`,
  `'return on equity3 4'`, `'net interest margin commercial book1'`), each a
  duplicate of a clean alias that is already present.

`col_leaf_label_clean` is STORED but deliberately NOT fed into `col_lineage` or
the column-axis lookups: column identity has its own footnote handling
(`_COL_FOOTNOTE`) tuned around combined period+unit headers (`'2H25¹ $m'`), and
the mis-nesting defect this stage exists to fix lives entirely on the row axis.
Revisit only with column-side evidence. (Observed: on DBS_1Q26 the column
`_find_clean_match` returns NULL for every header — best-effort by design.)

## Verified result (DBS_1Q26_trading_update, reloaded 2026-07-30)

4/4 tables matched, 49/49 rows. `hierarchy_source='geometry'` on all four.
49 → 47 rows (2 phantom twins merged), cells unchanged at 201 — nothing lost.

- income statement: `Of which: Net interest income` → parent `Total income`;
  the three commercial-book items → parent `Commercial book total income`;
  `Expenses` / `Profit before tax` / `Net profit` / `Reported net profit` all
  top-level. (Arithmetic cross-check of the recovered structure: 5,559 + 389 =
  5,948 — Commercial book + Markets trading = Total income, siblings.)
- per-share: `Net book value5` top-level (was under `Reported earnings`).
- lineage: ratios row 5 registry key is `Return on equity`, not
  `Return on equity4, 5`.
- title: `Key financial ratios (%)2, 3` → `table_title_clean` `Key financial ratios (%)`.

**Fallback verified as a genuine no-op.** Three real documents with NO side-car
(`UOB_4Q25_condensed-financial-statements`, `DBS_1Q26_P3_other_regulatory_disclosures`,
`OCBC_1Q25_Results__Press_Release`) reloaded against a copy of the pre-change DB:
row and cell counts identical in every case (755/3084, 197/425, 307/1258),
`hierarchy_source` `'model'` on all 322 tables, zero non-NULL `*_clean` values.
(`load_units`' returned `tables` count reads 46 vs 44 for the UOB doc — a
pre-existing quirk: the summary increments per table processed, including LEAKED
tables that `_load_table` skips. `table_t` itself holds 44 both before and after.)

## Non-goals / open items

- **Column-axis geometry** — see above; stored, not consumed.
- **`concept_map` alias purge.** The three polluted aliases must NOT be deleted
  until the batch re-extraction sweep has re-loaded every FS doc: until then,
  rows in un-reloaded documents still carry the polluted verbatim label with a
  NULL clean label, and deleting the alias would drop their concept stamp.
  Sequenced AFTER the sweep.
- **DB-wide concept checks** (`uniqueness_per_table`, `sums_to_component_vs_total`,
  `additive_identity`) are dominated by the 318 tables still on the `model`
  branch and will not move until the sweep lands. Measured before/after this
  change: essentially unchanged, as expected.
- **Indent-cluster threshold validated on DBS only** (`0.5 × body size`). Check
  UOB/OCBC pages as their docs come through the sweep — the all-or-nothing gate
  means a bad threshold degrades to today's behaviour rather than corrupting.
