# Runbook execution — steps 3–7, re-hydration after the M3 cleanup

Executed 2026-08-04, branch `mapping/close-logged-items`, against
`findociq/db/compiled_fs.db`. Runbook source: `docs/ingest-inventory.md` §6.
Steps 0–2 (extraction) and 8 (GCS sync) skipped as instructed.

**Outcome: 4 of 5 steps completed. Step 5 (`load_anchors.py`) aborted on a guard
trip caused by yesterday's cleanup. Diagnosed below; not fixed.**

**Total wall time: 4.2 s of script execution.** Runbook estimate was 5–8 min;
actual is two orders of magnitude faster because these are DB-only steps.

Headline: despite step 5 failing, **all 26 DBS KPH concepts have a value-loading
binding, and all three "expected blank" per-share cells produce real 4Q25
values.** Two of the task's stated expectations did not hold — see
"Expectations that did not hold".

---

## Rollback state captured before step 3

| Artifact | Rows |
|---|---|
| `bank_line_map_pre_runbook_test` (in-DB, per task) | 2329 |
| `findociq/db/snapshots/pre_runbook_rehydrate_2026-08-04.db` (file) | full DB |
| `bank_line_map_pre_cleanup_2026_08_04` (yesterday's backstop) | 2329 |

> The task's rollback plan (`DROP TABLE bank_line_map; ALTER TABLE
> bank_line_map_pre_runbook_test RENAME TO bank_line_map`) **would lose the
> schema** — `CREATE TABLE AS SELECT` does not carry the `UNIQUE(bank,
> table_type_id, row_label_norm, parent_label_norm)` constraint, the
> `ix_blm_anchor` / `ix_blm_status` indexes, or the `superseded_by` foreign key.
> Use the file snapshot for a catastrophic rollback; use the in-DB table only for
> a column-wise `UPDATE ... FROM` restore.

Pre-state: `bank_line_map` = 2329 rows, `concept_key` non-NULL = **0** (all
banks), as left by yesterday's cleanup.

---

## Step 3 — `backfill_map.py` ✅

```
python3 findociq/pipeline/mapping/backfill_map.py --db findociq/db/compiled_fs.db
```

```
bank_line_map backfill (all rows at map_status='ai_proposed')

  row_dim rows in classified tables : 5,426
  distinct anchors                  : 2,555
    inserted / updated              : 336 / 2,116
    protected (human_*) skipped     : 103

  proposed WITH a concept           : 579
  CONFLICT (rival concepts)         : 1
  structural headers (is_abstract)  : 516
  no concept in corpus              : 1,356
```

**0.52 s.**

| After step 3 | |
|---|---|
| `bank_line_map` total | 2329 → **2665** (+336 inserted) |
| `concept_key` non-NULL | DBS 177, OCBC 298, UOB 104 — **579** |
| by `mapped_by` | `backfill:corpus` 574, `dashboard_rows.yaml` 5 |
| by `map_status` | `ai_proposed` 574, `deprecated` 5 |

Note the 336 inserts: these are addresses that did not exist in `bank_line_map`
before. The DB has gained documents since the last backfill run.

---

## Step 4a — `apply_dashboard_rows.py --check` ✅

```
89 authored rows; 0 problems
check only — nothing written
```

**0.27 s.** Clean — every authored anchor exists in the corpus and every
`concept_key` exists in the dictionary.

---

## Step 4b — `apply_dashboard_rows.py` (write) ✅

```
89 authored rows; 0 problems

written as human_confirmed : 89
skipped (human_corrected)  : 0

retired (deprecated)               : 0
  with a single unambiguous successor : 0
  ambiguous/absent (superseded_by NULL): 0

bank_line_map by status:
   ai_proposed         2556
   deprecated             5
   human_confirmed      104
```

**0.27 s.**

| After step 4b | |
|---|---|
| `bank_line_map` total | 2665 (unchanged — all UPDATEs) |
| `concept_key` non-NULL | DBS 212, OCBC 324, UOB 132 — **668** |
| by `mapped_by` | `backfill:corpus` 574, `dashboard_rows.yaml` 94 |

---

## Step 5 — `load_anchors.py` ❌ ABORTED

```
python3 findociq/pipeline/mapping/load_anchors.py --db findociq/db/compiled_fs.db
```

```
load_anchors: unexpected unresolved concept_key conflict for pnl.nii.net (OCBC)
vs existing map_id=1489 concept_key=None -- not in KNOWN_LABEL_ONLY_CONFLICTS,
stopping rather than overwriting silently
```

**0.18 s. Exit non-zero, nothing written.**

### Root cause — yesterday's cleanup broke a schema invariant

`load_anchors.py:130`:

```python
if old_status == "human_confirmed" and old_ck != r["concept_key"]:
    if (bank, r["concept_key"]) not in KNOWN_LABEL_ONLY_CONFLICTS:
        raise SystemExit(...)
```

The guard assumes `map_status='human_confirmed'` ⟹ `concept_key IS NOT NULL`.
Yesterday's cleanup wiped `concept_key` but left `map_status` untouched, so that
invariant no longer holds. `None != 'pnl.nii.net'` evaluates True, and the guard
fires on what is actually an empty row.

Measured:

```
human_confirmed rows with concept_key IS NULL : 15 of 104
  all 15 have mapped_by = 'lineage_identity_map.csv'
```

Those 15 are precisely the rows `load_anchors` itself wrote on 2026-08-03. The
script is refusing to overwrite its own wiped output.

`map_id=1489` — OCBC / `FS_INCOME_STATUTORY` / `net_interest_income` /
`parent=''`, `map_status='human_confirmed'`, `mapped_by='lineage_identity_map.csv'`,
`concept_key=None`.

The intended branch for "nothing stamped" is the third one (supersede in place),
per the docstring at `load_anchors.py:15-18` — but that branch is only reachable
when `old_status != 'human_confirmed'`.

### Proposed one-line fixes — NOT applied

**Option A — script (`load_anchors.py:130`), one line:**

```python
if old_status == "human_confirmed" and old_ck is not None and old_ck != r["concept_key"]:
```

A `human_confirmed` row with a NULL `concept_key` then falls through to the
supersede-in-place branch, which is the documented intent for a placeholder with
nothing stamped. Makes `load_anchors` robust to this state permanently.

**Option B — data, one statement, no script change:**

```sql
UPDATE bank_line_map SET map_status = 'ai_proposed'
 WHERE map_status = 'human_confirmed' AND concept_key IS NULL;   -- 15 rows
```

Restores the invariant the cleanup broke, then `load_anchors` runs unmodified.
Arguably the more correct framing: the cleanup should have demoted `map_status`
alongside the wipe, since `human_confirmed` with no concept asserts a human
confirmed nothing.

**Recommendation: A.** B fixes today's DB; A prevents the class. Both are outside
this task's scope (`Do not modify any .py file`; no hand-edits beyond what the
scripts do), so neither was applied.

---

## Step 6 — `build_fact_metric.py` ✅ (run against a PARTIAL binding state)

Run despite step 5 aborting, to complete the picture. **`fact_metric` is
therefore built without `load_anchors`' 15 bindings.** Re-run after step 5 is
fixed.

```
fact_metric rows:        2068
  clean (single/twin):   1730
  resolved (prefer_tbl): 49
  conflict (punch-list): 289
  clean-resolution rate: 86.0%
conflicts CSV:           findociq/data/derived/fact_metric_conflicts.csv
```

**1.27 s.** Top conflicts are the known `bs.assets.npa` and
`bs.liabilities.deposits_casa` DBS multi-candidate cases, unchanged in character
from the pre-cleanup punch-list.

---

## Step 7 — `preflight_invariants.py` ✅ (3 checks FAIL)

**1.65 s. 21 checks, 3 FAILED: A4, D2, E2.**

### A1a did NOT fail — the task's expectation was wrong

```
[PASS] A1a: human_confirmed anchors = 104
```

**Actual count: 104. The hard-coded expectation is 104. The check passes.**

The reasoning behind the predicted failure was that the cleanup would leave a
stale count — but the cleanup only cleared `concept_key`, never `map_status`, so
the `human_confirmed` population never changed. Step 4b then re-wrote 89 of those
same rows at the same status. The count was 104 before the cleanup, during it,
and now.

This does mean A1a is weaker than it looks: it counted 104 yesterday when every
one of those rows had a NULL `concept_key` and the dashboard was fully dark. **It
is a row-status counter, not a binding-health check.** Not fixed (out of scope).

### The three genuine failures

| Check | Result |
|---|---|
| **A4** | spine table match levels `{composite: 2, section: 118, title: 23, unclassified: 12}`. Unclassified: 2 DBS `UNAUDITED CONSOLIDATED STATEMENT OF CHANGES IN EQUITY` variants + 7 OCBC `… Performance` section titles. |
| **D2** | 175/289 8-key-grain conflicts are spine concepts (`fact_metric_conflicts.csv`). |
| **E2** | `failed_resolve = 4`: OCBC `pnl.noninterest.other` (FY, 2H), OCBC `reg.capital.cet1_ratio` (FY, 2H). |

All three are pre-existing and OCBC/DBS-structural, not caused by this run.

### Passing checks worth recording

```
[PASS] A2: 80/81 spine x bank combos covered
           (missing: pnl.noninterest.other / OCBC)
[PASS] B2: 0/24788 v_cell rows missing period_label/period_end/period_source
[PASS] C1: legal_entity populated 24788/24788
[PASS] E1: slots=162 value=158 not_disclosed=0 pending_anchor=0 failed_resolve=4

Headline coverage: 158 / 162 value cells (spine x bank x {FY25,2H25})
```

---

## Post-execution verification

### Final counts

```sql
SELECT bank, COUNT(*) FROM bank_line_map WHERE concept_key IS NOT NULL GROUP BY bank;
```

| Bank | Expected (task) | **Actual** |
|---|---|---|
| DBS | ~18 | **212** |
| OCBC | ~25 | **324** |
| UOB | ~26 | **132** |
| **Total** | ~69 | **668** |

`bank_line_map` total: **2665**. By `mapped_by`: `backfill:corpus` 574,
`dashboard_rows.yaml` 94. By `map_status`: `ai_proposed` 574, `deprecated` 5,
`human_confirmed` 89.

**Acceptance criteria 2–4 are not met, and could not have been met by this
runbook.** See "Expectations that did not hold" below.

### DBS KPH coverage — 26 of 26 bound

Every concept in `app/highlights.yaml` checked for a DBS binding at a
value-loading status (`human_confirmed` / `human_corrected`):

```
DBS KPH with NO value-loading binding : []
DBS KPH with NO binding at all        : []
```

All 26 have at least one `human_confirmed` binding. Nothing to flag under
criterion 6.

### The three "expected blank" concepts are NOT blank

Criterion 5 predicted `bs.nav_per_share`, `pnl.eps.basic`, `pnl.eps.diluted`
would remain NULL for DBS. They are bound, and they produce real 4Q25 values:

| concept | span | value | source |
|---|---|---|---|
| `bs.nav_per_share` | FY | 24.29 | `DBS_4Q25_performance_summary` / `Net book value5` |
| `bs.nav_per_share` | 2H | 24.29 | `DBS_4Q25_performance_summary` / `Net book value5` |
| `pnl.eps.basic` | FY | 3.88 | `DBS_4Q25_performance_summary` / `Basic` |
| `pnl.eps.basic` | 2H | 3.71 | `DBS_4Q25_performance_summary` / `Basic` |
| `pnl.eps.diluted` | FY | 3.86 | `DBS_4Q25_performance_summary` / `Diluted9` |
| `pnl.eps.diluted` | 2H | 3.69 | `DBS_4Q25_performance_summary` / `Diluted9` |

Backing addresses:

```
bs.nav_per_share  FS_PER_SHARE ('net_book_value', '')                          human_confirmed
bs.nav_per_share  FS_PER_SHARE ('net_book_value', 'per_basic_and_diluted_share') ai_proposed
bs.nav_per_share  FS_PER_SHARE ('net_book_value', 'reported_earnings')          ai_proposed
pnl.eps.basic     FS_PER_SHARE ('basic', 'earnings')                            human_confirmed
pnl.eps.basic     FS_PER_SHARE ('basic', 'reported_earnings')                   human_confirmed
pnl.eps.diluted   FS_PER_SHARE ('diluted', 'earnings')                          human_confirmed
pnl.eps.diluted   FS_PER_SHARE ('diluted', 'reported_earnings')                 human_confirmed
```

**Step 3 created `('net_book_value', 'reported_earnings')` — the mis-parented
4Q25 address — as an `ai_proposed` row and stamped `bs.nav_per_share` on it from
the corpus** (`row_dim` row 7 carries that concept). So the geometry defect is
absorbed by `backfill_map`, not fatal. EPS was never affected: both `basic` and
`diluted` are authored under *both* parent variants in `dashboard_rows.yaml`, so
the 3Q25→4Q25 restructure is already bridged.

---

## Expectations that did not hold

**1. "DBS ~18, OCBC ~25, UOB ~26 (~69 total)."** This assumed `load_anchors` is
the only writer of `concept_key`. It is not — `backfill_map.py` (step 3) stamps
574 on its own, and `apply_dashboard_rows.py` (step 4b) another 94. The ~69
figure is the size of `load_anchors`' contribution alone, which is at most 72
corpus-wide. Even with step 5 succeeding, the total would be ~683, not ~69.
See `docs/m3-store-relationship.md` §3 for the three-writer breakdown.

**2. "3 DBS KPH cells will not be restored."** True for `load_anchors`, false for
the runbook as a whole: `dashboard_rows.yaml` anchors all three DBS per-share
concepts independently of `lineage_identity_map.csv`, and step 4b restored them.

**3. "Preflight A1a will fail with a stale count."** It passes at exactly 104.

None of these are defects in the scripts. They follow from `concept_key` having
three independent writers.

### Consequence for the DECISIONS.md entry logged earlier today

That entry states the mis-parent contributes to "the blank DBS NAV cell". **The
DBS NAV cell is not blank** — it reads 24.29 for FY25 and 2H25, sourced from
`DBS_4Q25_performance_summary`. The geometry defect is real and the address
mismatch against the `human_confirmed` anchor is real, but `backfill_map`'s
corpus-stamped `ai_proposed` row at the mis-parented address covers it. The
blast-radius claim in that entry should be narrowed. Not edited here — flagged
for your decision.

---

## Tests

Run from `findociq/` (relative imports require it):

| Suite | Result |
|---|---|
| `app/test_findociq_app.py` (pytest, from `app/`) | **85 passed** |
| `pipeline/concept/test_fact_metric.py` | PASS |
| `pipeline/mapping/test_mapping.py` | PASS |
| `pipeline/mapping/test_m2_canonical_leaf.py` | PASS |
| `pipeline/mapping/test_quarantine_duplicate_page_tables.py` | PASS |
| `pipeline/mapping/test_migrate_serving_views.py` | PASS |
| `pipeline/concept/test_concept.py` | **FAIL — pre-existing** |

`test_concept.py` fails with a `RuntimeError` on a synthetic period mis-stamp
fixture. Pre-existing and unrelated to this run: it builds its own temp DBs from
`schema_v7.sql` (zero references to `compiled_fs.db`) and imports none of the
scripts executed here. Documented, not fixed.

---

## Current DB state

Partially re-hydrated. `bank_line_map` holds 668 `concept_key` values from two of
the three writers; `load_anchors`' 15 are missing. `fact_metric` was rebuilt
against that partial state.

**To finish:** apply fix A or B above, re-run step 5, then re-run step 6. Neither
was done here — the fix is out of this task's scope.

Nothing modified outside `findociq/db/compiled_fs.db` (by the scripts),
`findociq/data/derived/fact_metric_conflicts.csv` (rewritten by step 6), the two
snapshots, and this document. No `.py`, `.yaml`, or `.csv` input file was edited.

---

# Fix applied and re-run

Same session, immediately following. **Fix A adopted; steps 5 and 6 re-run to
completion. Re-hydration is now complete.**

## Change — `load_anchors.py:131`

```diff
-        if old_status == "human_confirmed" and old_ck != r["concept_key"]:
+        # `old_ck is not None` guard: a human_confirmed row with a NULL
+        # concept_key is a confirmed ADDRESS with nothing stamped on it, not a
+        # rival binding -- it belongs in the supersede-in-place branch below
+        # (see this module's docstring, "OVERLAP where ... concept_key is NULL").
+        # Without the guard, `None != <any concept>` reads as a conflict and
+        # aborts the run. That state is reachable: the 2026-08-04 M3 cleanup
+        # wiped concept_key without demoting map_status.
+        if old_status == "human_confirmed" and old_ck is not None and old_ck != r["concept_key"]:
```

One condition added; the comment records why, so the next reader doesn't
"simplify" it back. `git diff --stat`: 1 file, 8 insertions, 1 deletion.
Revert with `git checkout findociq/pipeline/mapping/load_anchors.py`.

## Step 5 re-run — `load_anchors.py` ✅

```
loaded                      : 0
confirmed_in_place          : 57
superseded_placeholder      : 15
label_conflict_loaded       : 0
not_disclosed               : 5
skipped_pending_extraction  : 3
```

**0.33 s. Exit 0.**

The numbers reconcile exactly against the CSV's 72 `anchor` rows:

- **57 `confirmed_in_place`** — addresses where `apply_dashboard_rows` (step 4b)
  had already written the same `concept_key` at `human_confirmed`. Idempotent
  no-ops, as designed.
- **15 `superseded_placeholder`** — precisely the 15 rows the cleanup had left at
  `human_confirmed` with a NULL `concept_key`. These are the rows the old guard
  aborted on; they now take the supersede-in-place branch and are re-stamped
  `mapped_by='lineage_identity_map.csv'`.
- 57 + 15 = **72** = the CSV's full `anchor` set. No anchor was skipped.
- **3 `skipped_pending_extraction`** — the DBS `bs.nav_per_share` /
  `pnl.eps.basic` / `pnl.eps.diluted` rows, skipped as expected. CSV not touched.
- **5 `not_disclosed`** → `concept_disclosure`, not `bank_line_map`.
- **0 `loaded`** — no new addresses inserted, because steps 3 and 4b had already
  created every one of them. Correct for a re-hydration.

### State after step 5

```sql
SELECT bank, COUNT(*) FROM bank_line_map WHERE concept_key IS NOT NULL GROUP BY bank;
```

| Bank | After step 4b | **After step 5** |
|---|---|---|
| DBS | 212 | **212** |
| OCBC | 324 | **339** (+15) |
| UOB | 132 | **132** |
| **Total** | 668 | **683** |

By `mapped_by`: `backfill:corpus` 574, `dashboard_rows.yaml` 94,
**`lineage_identity_map.csv` 15**.

All 15 restored rows are OCBC — matching the pre-cleanup distribution recorded in
`docs/m3-cleanup-report.md` (`lineage_identity_map.csv`: DBS 0, OCBC 15, UOB 0).

**The invariant is repaired:**

```
human_confirmed rows with concept_key IS NULL:  15  ->  0
```

## Step 6 re-run — `build_fact_metric.py` ✅

```
fact_metric rows:        2068
  clean (single/twin):   1730
  resolved (prefer_tbl): 49
  conflict (punch-list): 289
  clean-resolution rate: 86.0%
```

**1.19 s.** Byte-identical to the partial-state build earlier today. That is the
expected result, not a sign the rebuild did nothing: the 15 restored bindings are
all OCBC addresses that `backfill:corpus` had already stamped with the same
`concept_key`, so the resolved value set does not move. What changed is
`map_status` — those 15 now load at `human_confirmed` rather than depending on an
`ai_proposed` row, which is the point of the M3 layer.

## Verification

| Criterion | Result |
|---|---|
| 1. Guard clause present at `load_anchors.py:131` | ✅ |
| 2. `load_anchors` completes, no `SystemExit` | ✅ exit 0 |
| 3. `build_fact_metric` completes | ✅ 2068 rows, 86.0% clean |
| 4. DBS KPH coverage 26/26 with real values | ✅ no regression |
| 5. This document updated | ✅ |
| 6. Previously-passing tests still pass | ✅ |

### DBS KPH coverage — unchanged at 26/26

```
DBS KPH concepts: 26   with value-loading binding: 26/26
blank: none
```

The three per-share concepts still carry real 4Q25 values:

| concept | 2H | 4Q | FY | source (2H/FY) |
|---|---|---|---|---|
| `bs.nav_per_share` | 24.29 | 24.29 | 24.29 | `DBS_4Q25_performance_summary` |
| `pnl.eps.basic` | 3.71 | 3.30 | 3.88 | `DBS_4Q25_performance_summary` |
| `pnl.eps.diluted` | 3.69 | 3.28 | 3.86 | `DBS_4Q25_performance_summary` |

(4Q values source from `DBS_1Q26_trading_update`, which carries the 4Q25
comparative column.)

### Tests

Run from `findociq/`:

| Suite | Result |
|---|---|
| `app/test_findociq_app.py` (pytest, from `app/`) | **85 passed** |
| `pipeline/mapping/test_mapping.py` | PASS |
| `pipeline/concept/test_fact_metric.py` | PASS |
| `pipeline/mapping/test_m2_canonical_leaf.py` | PASS |
| `pipeline/mapping/test_quarantine_duplicate_page_tables.py` | PASS |
| `pipeline/mapping/test_migrate_serving_views.py` | PASS |
| `pipeline/concept/test_concept.py` | FAIL — pre-existing, unchanged |

No test regressed. `test_concept.py` fails identically before and after the fix;
it builds its own temp DBs and imports neither `load_anchors` nor
`build_fact_metric`.

> **Test-coverage gap, not fixed:** no test exercises `load_anchors`' branch
> selection, so nothing would have caught this guard bug and nothing now protects
> the fix. `test_mapping.py` covers `apply_dashboard_rows` retirement logic but
> not `load_anchors`. A regression test — human_confirmed row with NULL
> concept_key must supersede rather than raise — is worth adding. Out of scope
> here (`Do not modify any other script`).

## Final state

**Re-hydration complete.** `bank_line_map`: 2665 rows, 683 with a `concept_key`,
from all three writers. `fact_metric`: 2068 rows at 86.0% clean resolution.
`human_confirmed` rows with a NULL `concept_key`: 0.

Still outstanding, all deliberately untouched: preflight A1a's hard-coded 104
(passes, but is a row-status counter rather than a binding-health check); the
4Q25 geometry mis-parent; DBS and UOB `canonical_leaf`; the 3 DBS
`pending_extraction` CSV rows. GCS sync not run.
