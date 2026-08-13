# KPH ground truth — archived 2026-08-12

Two files from the Key Performance Highlights verification loop, both keyed on
the **retired `concept_key` vocabulary** (`pnl.nii.net`, …). `compiled_v2.db` has
no `concept_key`; the live address is `(bank, table_type_id, canonical_leaf_id)`
resolved through the masterlist and `data/derived/dashboards/*.csv`.

## `kph_ground_truth_all_periods.csv` — 78 rows, HAND-AUTHORED

Was `data/sources/`. One row per (bank, concept), one column per period
1Q23→FY26, holding the figure as filed:

    bank,concept_key,section,display,1Q23,2Q23,...,FY26,notes
    DBS,pnl.nii.net,Selected income statement items ($m),Net interest income,3271,3433,...

Nothing wrote it — it was typed by hand. Nothing read it at time of archiving.

**What it was for, and what is lost.** This was the only check that could catch a
WRONG number rather than a missing one. Every other verification in the pipeline
tests internal consistency: that rows loaded, that leaves stamped, that a cell is
present. This tested the value against the filing. Retiring it makes verification
"open `<doc>_fs.xlsx` and eyeball it against the PDF" (technical report §5.1).

Archived at Yunhan's direction: the masterlist and dashboard anchors now address
figures directly by canonical leaf id, and this file could not verify the live DB
without re-keying all 78 rows off `concept_key`.

**To revive:** map each `concept_key` to its `(bank, table_type_id,
canonical_leaf_id)` — the dashboards CSVs already carry that mapping for the
concepts they cover — then compare against `cell_fact` joined on the stamped
identity, matching period on `f.period` + `f.period_span`.

## `kph_ground_truth_report.csv` — 1,294 rows, GENERATED

Was `data/derived/`. The expected-vs-actual comparison the file above fed:

    bank,concept_key,display,section,column,period,period_span,status,expected,actual,diff,...
    DBS,pnl.nii.net,Net interest income,1Q23,2023-03-31,1Q,mismatch,3271.0,-113.0,-3384.0

Stale, and misleading if opened cold: `actual -113.0` for net interest income is
the retired concept resolver selecting the wrong cell entirely, not a real
discrepancy in the data. Produced by a mechanism that no longer exists.
