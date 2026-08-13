# Concept-layer outputs — archived 2026-08-12

Four CSVs produced by the **retired concept layer**. All four are write-only
artefacts: verified by grepping `read_csv` / `open()` across the entire live
tree, **nothing reads any of them as input**.

| File | Written by | Read by |
| --- | --- | --- |
| `concept_cuts.csv` | orphaned — its generator `discover_cuts.py` was already archived | nothing |
| `concept_nature_conflicts.csv` | `pipeline/concept/audit_nature.py:24` (write-only `_OUT`) | nothing |
| `fact_metric_conflicts.csv` | the retired `fact_metric` build | nothing |
| `lineage_identity_map.csv` | `m2_canonical_leaf.py`, already archived | nothing |

## Two references that look live and are not

* `pipeline/ingest/sync_bq.py:35` mentions `concept_cuts` **inside a comment** —
  `"concept_resolution_log", # replaces nonexistent concept_dim/concept_cuts`.
  It documents that the table does not exist.
* `pipeline/concept/audit_nature.py` writes `concept_nature_conflicts.csv`, but
  the whole `pipeline/concept/` tree runs only under `--with-concepts`, which is
  **off by default** — a normal ingest prints `STEP 4a — concepts [SKIPPED]`.

None of the four is touched by `app/`, `pipeline/mapping/` or `tools/` — the live
identity path is masterlist → `canonical_leaf_id` / `canonical_col_id` →
`data/derived/dashboards/*.csv`, which is disjoint from all of this.

## Why kept rather than deleted

`DECISIONS.md` and three specs cite them as the evidence behind decisions that
were made and still stand:

    docs/specs/2026-07-27-concept-nature-flow-vs-stock.md
    docs/specs/2026-08-03-anchor-row-resolution.md
    docs/specs/2026-08-05-master-registry-next-steps.md

188 KB total. They remain git-tracked at this path; `git rm` would drop them from
HEAD while git history still retains them, if that is preferred later.

## Still live at time of archiving

`pipeline/concept/` — the code that produces two of these — was NOT moved with
them. A separate cleanup of `pipeline/` was in progress; archiving the outputs
while leaving the generator is deliberate here, not an oversight.
