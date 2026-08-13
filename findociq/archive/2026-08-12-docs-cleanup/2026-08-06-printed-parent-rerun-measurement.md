# Printed-parent tests + masterlist re-run — measurement report

**Date:** 2026-08-06 · **Branch:** `mapping/masterlist-registry`

Two-part task: (A) pin the printed-parent resolution path with tests, (B) re-run
the load + masterlist build and measure whether the loader fixes deleted the
phantom leaves.

**Headline: two of three success criteria met. The third (historical count down)
did NOT hold, and the cause is a pre-existing registry bug newly exposed — not a
regression from the loader fixes. No curation was applied. Stopped for review.**

---

## PART A — tests

New file `pipeline/pass2/test_printed_parents.py` (15 cases). Placed with the
other pass2 tests rather than a top-level `tests/`, per repo convention.

| # | Contract pinned | Cases |
|---|---|---|
| 1 | `hN` decoding — headers are POSITIONAL; `section_header` rows do not consume a slot | `a1_hn_decoding_h1_h2_h3`, `a1_section_header_does_not_consume_an_hn_slot`, `a1_hn_reference_pointing_forward_is_dropped` |
| 2 | Literal-label fallback binds to the NEAREST preceding match | `a2_literal_label_binds_to_nearest_preceding_match` (+ case/whitespace, no-match) |
| 3 | Shallower-than-child guard DROPS the mapping (not clamp, not re-point) | `a3_hn_resolving_to_same_level_is_dropped_not_clamped`, `a3_literal_label_at_same_or_deeper_level_is_dropped` |
| 4 | Disagreement → printed wins, and a valid printed parent is NOT warnable | `b1_printed_wins_where_position_disagrees`, `b1_valid_printed_parent_is_not_a_warnable_event` |
| 5 | Unresolvable reference → residual warning once per row; suppressed under geometry | `c1`, `c2_residual_warning_fires_once_per_unresolvable_row`, `c3_residual_warning_suppressed_under_geometry` |
| — | Regression guard on the positional path | `b2_positional_path_unchanged_when_no_printed_parent` |
| — | End-to-end: printed parent reaches `row_dim.row_parent` | `c4_printed_parent_reaches_row_dim` |

Part A fixtures are synthetic `GRow` lists; Part C loads a synthetic
`parsed.json` through `load_units` into a temp schema_v7 DB, so the residual
warning is observed where it is actually emitted rather than re-derived.

### Mutation-verified

Each rule was confirmed to bite by reverting it in `load_v7.py` and re-running:

| Mutation | Tests that failed |
|---|---|
| `if i in printed:` short-circuit removed | `b1_printed_wins`, `b2_positional_path`, `c4_reaches_row_dim` |
| `section_header` no longer excluded from `hN` numbering | `a1_hn_decoding`, `a1_section_header_does_not_consume` |
| shallower-than-child guard removed | `a3_hn_resolving_to_same_level` |
| geometry suppression (`if not geom.applied`) removed | `c3_suppressed_under_geometry` |
| literal-label nearest→first match | `a2_literal_label_binds_to_nearest`, `a3_literal_label_at_same_or_deeper` |

`c4` fails with `Markets trading income` where `Total income` is expected — i.e.
it reproduces the exact `pnl.nii.net` defect the fix was written for.

### Two stale assertions repaired in `test_load_v7.py`

Already red at HEAD, unrelated to this file's additions. Commit 01151d1 replaced
the blanket total-skip with `_heads_a_block`, which needs `sums_to` to tell a
terminal total from a total-shaped section header. The two fixtures called
`row_parents_by_position` without it, so the discriminator had no aggregation
evidence and treated the total as a header. Fixed by supplying the `sums_to` the
production caller always supplies (`load_v7.py:1563-1566`) — same fixtures, same
expectations. `test_geometry_load.py` Part B had already been updated for this
contract change; `test_load_v7.py` was missed.

**Only one production caller** of `row_parents_by_position` exists
(`load_v7.py:1566`) and it always passes `sums_to`, so the permissive
no-evidence default is unreachable in production.

Suite: **`pytest pass2` 76 passed** (was 61). Two `check()`-script failures
remain in `test_load_v7.py` — both `original spike DB exists`, missing fixtures
on this workstation, pre-existing and environmental.

---

## PART B — reload + re-measure

### What was written where

| Artifact | Path | Note |
|---|---|---|
| Reload (pass2 replay) | `db/compiled_reload_rerun.db` | new file; existing `compiled_reload.db` untouched |
| Clean target schema | `db/compiled_v2_rerun.db` | new file; **git-tracked `compiled_v2.db` untouched** |
| Registries | `data/derived/masterlist_proposed_v2/` | **`masterlist_proposed/` untouched** |
| Pre-fix baseline registry | scratchpad `masterlist_before/` | built from `compiled_fs.db` |

`compiled_fs.db` (deployed, git-tracked) was **read only** — it is the pre-fix
baseline. Nothing in `db/snapshots/` was touched.

Reload reproduced exactly: 368 tables / 6,331 rows / 23,834 cells. The 2 failed
documents are the known missing-artifact pair (`DBS_1Q22_trading_update`,
`DBS_3Q22_trading_update`, worklist §3.3), both contributing 0 tables.

**Tool change (one):** added `--out` to `build_masterlist_proposed.py` so a
candidate registry can be built without overwriting the curated one. No
normalizer function was touched.

### Correction to a claim made mid-session

`build_compiled_v2.py --parents` defaults to **`trust`**, so `compiled_v2.db`
carries the loader's `row_parent` directly. The 0-row difference I measured
between `compiled_reload` and `compiled_v2` is therefore true by construction,
not an independent confirmation of the recompute rule described in that file's
docstring.

### The re-run reproduces last session's build

`masterlist_proposed_v2/` vs `masterlist_proposed/`: OCBC and UOB byte-identical;
DBS differs by **one alias line reordering** (`Others` moved position) — set
iteration nondeterminism, not semantic. **Minor defect worth noting: alias list
ordering is not deterministic across builds.**

So the loader fixes were already reflected in the committed registry. 3Q25 was
already included (`periods_seen` carries `3Q25` throughout).

### 1. Orphans — DOWN ✅

The 1,055 baseline is the model-path count under the *absolute-level* definition
(`row_hierarchy > 0 AND row_parent IS NULL`). Reproduced exactly.

| Definition | BEFORE (`compiled_fs`) | AFTER (`compiled_v2_rerun`) |
|---|---|---|
| model path, absolute level | **1,055** (baseline ✓) | **946** |
| all paths, absolute level | 1,062 | 949 |
| all paths, table-relative (deeper than table min, no parent) | 811 (12.4%) | **666 (10.5%)** |
| model path, table-relative | 804 (14.1%) | 663 (11.3%) |

The 19% in the brief does not correspond to any of these denominators; 1,055/5,695
model-path rows is 18.5%, which is the closest reading.

### 2. Historical leaves — UP by 3 ❌

| | BEFORE | AFTER | Δ |
|---|---|---|---|
| Total historical | 336 | 339 | **+3** |
| DBS | 182 | **169** | −13 ✅ |
| OCBC | 153 | **169** | **+16** ❌ |
| UOB | 1 | 1 | 0 |

The 388 baseline in the brief was not reproducible; 336 is what the pre-fix DB
yields under the current id rules.

**Decomposed, the rise is not a loader regression:**

| | BEFORE | AFTER | Δ |
|---|---|---|---|
| Historical whose ONLY period is `4Q25` (self-contradictory) | 19 (all OCBC) | 29 (all OCBC) | +10 |
| Genuine historical (no `4Q25` in `periods_seen`) | 224 | 222 | −2 |

A leaf marked `historical` whose only observed period is `4Q25` is the **known
`Q4_DOCS` two-document bug** (worklist §2): OCBC's 4Q25 picture spans the Media
Release *and* the Condensed FS, but `Q4_DOCS["OCBC"]` names only the Condensed
FS, so Media-Release-only leaves are mislabelled historical with a fabricated
ordinal.

It got worse **because the loader fixes recovered tables that were previously
destroyed**. The OCBC 4Q25 media-release width-overflow abort (`'Total income'`
emitted 6 cells against 5 declared columns) had wiped whole tables; the
row-scoped fix brings them back:

- `OCBC FS_RATIOS_KEY`: **0 → 17 leaves, all active** — table recovered outright.
- `OCBC FS_VOLUME_RATE`: 11 → 12.
- `OCBC FS_DIVIDENDS`: 9 → 16, of which 7 new leaves are `periods_seen=['4Q25']`
  yet marked historical — pure `Q4_DOCS` mislabelling.
- `OCBC FS_EXPENSES_DETAIL`: 16 → 19, same pattern.

The residual OCBC genuine-historical rise (41 → 52) is concentrated in
`FS_HIGHLIGHTS_COMBINED` (75 → 64 leaves, 23 gone / 12 new, historical +13, all
`periods_seen=['2Q25']`) and is attributable to the **missing page-split dedup
step** — `dedup_status` is NULL in the replayed DB (worklist §3.3), so id churn
there is expected and is not a hierarchy effect.

### 3. DBS 3Q25 phantom leaves — GONE ✅ (but 2, not 6)

`DBS FS_INCOME_SELECTED`, BEFORE → AFTER: **23 leaves / 2 historical → 21 leaves
/ 0 historical.**

The two, both named and both `periods_seen=['3Q25']`:

1. `amortisation_of_intangible_assets::ecl_stage_1_and_2_gp`
2. `amortisation_of_intangible_assets::ecl_stage_3_sp`

These are exactly the documented defect — 3Q25's ECL rows parenting to
*Amortisation of intangible assets* instead of *Allowances for credit and other
losses*. Both are gone after the fix.

**On "six":** not reproducible. Only one version of the registry was ever
committed (`bef3bb5`) and it already shows 21 leaves / 0 historical. The
worklist's "was 55 before the loader fixes **and the id rules**" indicates the
six-leaf count predates the current id rules; the BEFORE built here isolates the
loader fix alone (old DB + current id rules) and yields 2.

### 4. Current leaves vs printed rows

`DBS FS_INCOME_SELECTED`: **21 leaves carrying `4Q25`** against **22 printed rows**
in `DBS_4Q25_performance_summary` "Selected income statement items ($m)", 0
orphans in that table. Within one row of the printed table; slightly above the
17–20 expected in the brief.

Corpus-wide, leaves with `periods_seen ∋ 4Q25`: 1,350 → **1,381**.

### 5. Registry diff — 142 leaves gone, 171 new, across 26 tables

Full per-bank per-table table is in the session output; the movers are:

| bank | table | before | after | gone | new | hist Δ |
|---|---|---|---|---|---|---|
| OCBC | FS_HIGHLIGHTS_COMBINED | 75 | 64 | 23 | 12 | +13 |
| OCBC | FS_RATIOS_KEY | 0 | 17 | 0 | 17 | 0 |
| OCBC | FS_INCOME_STATUTORY | 44 | 42 | 17 | 15 | −2 |
| OCBC | FS_NPA_COVERAGE | 35 | 38 | 12 | 15 | +1 |
| DBS | FS_INCOME_SELECTED | 23 | 21 | 15 | 13 | −2 |
| DBS | FS_CUSTOMER_DEPOSITS | 33 | 38 | 6 | 11 | −15 |
| DBS | FS_EQUITY_CHANGES_GROUP | 19 | 29 | 1 | 11 | 0 |
| UOB | FS_INCOME_STATUTORY | 26 | 25 | 13 | 12 | 0 |

---

## PART B item 9 — GROUND-TRUTH REGRESSION: **BLOCKED**

The AFTER number could not be produced.

`concept/gt_check.py` reads `v_fact_metric_serving`, which sits on top of
`fact_metric`. The reloaded DB has no such layer:

| DB | `table_registry` | `table_catalog` | `fact_metric` |
|---|---|---|---|
| `compiled_fs` | ✅ | ✅ | ✅ 2,240 rows |
| `compiled_reload_rerun` | ❌ | ❌ | ❌ |

Rebuilding it got two stages in before hitting a wall:

- `concept.run --no-llm` — **succeeded** (40 concepts resolved).
- `concept.build_fact_metric` — **failed**: `no such table: table_registry`.
- `concept.compute_ratios` — failed downstream (`no such table: fact_metric`).

`table_registry` is a **mapping-layer** artifact (`migrate_add_mapping_layer.py`
plus the resolution stages), so the AFTER GT number requires rebuilding the whole
mapping layer against the reloaded corpus. Worklist §3.4 sequences that work
*after* leaf ids are stable, so it is out of scope here and was not attempted.

**BEFORE baseline, reproduced exactly** (fresh run against `compiled_fs.db` is
byte-identical to the committed `data/derived/kph_ground_truth_report.csv`):

| status | n |
|---|---|
| match | 609 |
| mismatch | 186 |
| missing | 499 |
| **total** | **1,294** |

match / (match + mismatch) = **76.6%**. All rows = 47.1%.

**The 92.2% baseline is not reproducible from the tree.** Per bank: DBS 94.5%,
UOB 79.6%, OCBC 49.2%. No family exclusion tried (`ratio`, `reg`, `pnl`) lands on
92.2%. Since the committed report reproduces byte-identically, the discrepancy is
in the *definition* of the 92.2% figure, not in drift of the underlying data —
worth pinning down before it is used as a gate.

---

## Success criteria

| Criterion | Result |
|---|---|
| Phantom leaves gone | ✅ 2 named leaves, both gone; table 23→21, 0 historical |
| Orphan count down | ✅ 1,055 → 946 (model path); 811 → 666 (table-relative) |
| Historical count down | ❌ 336 → 339. DBS −13 ✅; OCBC +16, of which +10 is the `Q4_DOCS` bug exposed by recovered tables and the rest is missing dedup |
| GT match rate ≥ baseline | ⚠️ not computable — mapping layer absent from the reloaded DB |

**No curation applied. No normalizer changed. `masterlist_proposed/` untouched.**

## Recommended next steps (not taken)

1. Fix `Q4_DOCS` → reference **set** per bank from the seed's `doc_kind`
   (worklist §3.1). This alone should clear the +10 self-contradictory OCBC
   historicals and is a precondition for the historical metric meaning anything.
2. Run page-split dedup against the replay so `FS_HIGHLIGHTS_COMBINED` id churn
   stops masking real movement.
3. Pin down the 92.2% GT definition, then decide whether rebuilding the mapping
   layer on the reloaded corpus is worth doing before registry curation.
4. Make registry alias-list ordering deterministic.
