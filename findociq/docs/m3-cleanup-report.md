# M3 cleanup — `bank_line_map.concept_key` wiped to NULL

Executed 2026-08-04 on branch `mapping/close-logged-items`, against
`findociq/db/compiled_fs.db`.

**Status: complete. 593 bindings cleared, 0 remain. Fully reversible — see
Rollback.**

This is step 1 of a three-step sequence. The dashboard's concept-mapped columns
are blank until steps 2 and 3 land:

1. **cleanup (this document)** — `concept_key` wiped.
2. build `canonical_leaf` for DBS and UOB (not started).
3. run `load_anchors.py` to repopulate from `lineage_identity_map.csv` (not run).

---

## Read this before step 3 — the wipe is not fully reversible by `load_anchors`

The task rationale assumed every cleared binding is either (a) KPH-scope, which
`load_anchors` re-establishes from the CSV, or (b) non-KPH corpus noise. There is
a third case, and it is small but real:

**Three DBS KPH bindings will NOT be restored by `load_anchors`.** Their
`lineage_identity_map.csv` rows are `resolution='pending_extraction'`, and
`load_anchors` skips that resolution by design ("`pending_extraction` rows have
no resolved row_lineage yet — not loaded, reported separately",
`load_anchors.py:7`).

| Bank | concept_key | Was | CSV resolution |
|---|---|---|---|
| DBS | `bs.nav_per_share` | `FS_PER_SHARE` / `net_book_value`, human_confirmed | `pending_extraction` |
| DBS | `pnl.eps.basic` | `FS_PER_SHARE` / `basic`, human_confirmed | `pending_extraction` |
| DBS | `pnl.eps.diluted` | `FS_PER_SHARE` / `diluted`, human_confirmed | `pending_extraction` |

All three are in the 26-item KPH set (`app/highlights.yaml`). Before step 3 is
declared done, either those CSV rows get promoted to `resolution='anchor'`, or
these three DBS cells stay blank on the dashboard.

Two related scope facts, not defects:

- `resolution='derived'` rows (`pnl.nii.net` DBS, `pnl.noninterest.other`
  DBS + OCBC) are correctly absent from `bank_line_map` — they route to the
  derivation layer by design.
- `load_anchors` can restore **at most 72** bindings (the CSV's `anchor` rows:
  DBS 21 / OCBC 25 / UOB 26). It replaces 593. The 521-row difference is the
  intended scope reduction, not loss — but it is the whole reduction, so verify
  the dashboard against the 26-item set after step 3 rather than against a row
  count.

---

## Row counts

| Measure | Before | After |
|---|---|---|
| `bank_line_map` rows | 2329 | 2329 |
| rows with non-NULL `concept_key` | **593** | **0** |
| distinct `concept_key` values | 48 | 0 |
| snapshot table rows | — | 2329 (593 with `concept_key`) |

`binding_source` column: **does not exist** on this schema. Acceptance criterion 3
is not applicable — verified via `PRAGMA table_info(bank_line_map)`. The
provenance column on this schema is `mapped_by`, which is **not** cleared: it is
governance metadata for the address, not part of the concept binding.

`concept_key` is declared `TEXT` with no `NOT NULL` constraint, so the wipe needed
no schema change (acceptance criterion 8).

---

## Pre-cleanup distribution by writer (`mapped_by`) and bank

| `mapped_by` | Writer | DBS | OCBC | UOB | Total |
|---|---|---|---|---|---|
| `backfill:corpus` | `backfill_map.py` (`ai_proposed`) | 243 | 199 | 42 | **484** |
| `dashboard_rows.yaml` | `apply_dashboard_rows.py` (`human_confirmed`) | 40 | 26 | 28 | **94** |
| `lineage_identity_map.csv` | `load_anchors.py` (`human_confirmed`) | 0 | 15 | 0 | **15** |
| | **Total** | **283** | **240** | **70** | **593** |

By `map_status`: `ai_proposed` 484, `human_confirmed` 104, `deprecated` 5.

Only 15 of 593 came from the store now designated authoritative, and none of
those were DBS or UOB. Background: `docs/m3-store-relationship.md`.

---

## Pre-cleanup distribution by KPH scope

"KPH-scope" = the `concept_key` appears in `lineage_identity_map.csv` (32
concepts). "corpus-only" = it does not (16 further concepts, machine-stamped).

| Bank | KPH-scope | corpus-only | Total |
|---|---|---|---|
| DBS | 196 | 87 | 283 |
| OCBC | 162 | 78 | 240 |
| UOB | 55 | 15 | 70 |
| **Total** | **413** | **180** | **593** |

413 of 593 cleared bindings were KPH-scope by concept. Note the asymmetry that
motivates the caveat above: KPH-scope *by concept* is a much weaker statement
than *restorable by `load_anchors`*, which requires `resolution='anchor'` and a
PASS through `resolve_anchors.py`.

---

## Exact SQL executed

Run inside a single transaction via the repo venv's `sqlite3` module. No new `.py`
file was created (acceptance criterion 6).

```sql
BEGIN;

-- 1. Snapshot (verified identical: same row count, same SHA-256 over all
--    columns ordered by map_id, before any mutation)
CREATE TABLE bank_line_map_pre_cleanup_2026_08_04 AS
SELECT * FROM bank_line_map;

-- 2. Wipe. 593 rows affected.
UPDATE bank_line_map
   SET concept_key = NULL
 WHERE concept_key IS NOT NULL;

COMMIT;
```

Preceded by a file-level snapshot, matching the repo's existing
`db/snapshots/pre_<change>_<date>.db` convention:

```bash
cp findociq/db/compiled_fs.db \
   findociq/db/snapshots/pre_m3_concept_key_cleanup_2026-08-04.db
```

### Verification queries

```sql
-- AC2: zero remaining bindings                        -> 0
SELECT COUNT(*) FROM bank_line_map WHERE concept_key IS NOT NULL;

-- AC1: snapshot row count matches pre-cleanup          -> 2329 = 2329
SELECT COUNT(*) FROM bank_line_map_pre_cleanup_2026_08_04;

-- AC3: non-concept columns unchanged. SHA-256 over every column EXCEPT
--      concept_key, ordered by map_id, computed pre and post.
SELECT map_id, bank, table_type_id, row_label_norm, parent_label_norm,
       legal_entity, segment_key, geo_key, industry_key, period_type, balance,
       is_abstract, negated_label, map_status, mapped_by, confidence,
       mapped_at, superseded_by, note, basis
  FROM bank_line_map ORDER BY map_id;
```

| Check | Result |
|---|---|
| non-NULL `concept_key` after wipe | **0** |
| row count pre / post | 2329 / 2329 — match |
| non-concept checksum pre | `1994466c0197cdca2aeaec5926757cd5d8f69fe4f748520664b99377bd0704f5` |
| non-concept checksum post | `1994466c0197cdca2aeaec5926757cd5d8f69fe4f748520664b99377bd0704f5` |
| verdict | **UNCHANGED** |

Also cleared: all `segment_key` / `geo_key` / `industry_key` / `period_type`
values were left **untouched**. Only `concept_key` was modified. The checksum
above includes those columns and is unchanged, which proves it.

---

## Snapshot

| | |
|---|---|
| In-DB table | `bank_line_map_pre_cleanup_2026_08_04` |
| Rows | 2329 (593 with non-NULL `concept_key`) |
| Schema | identical to `bank_line_map` (`CREATE TABLE ... AS SELECT *`) |
| File snapshot | `findociq/db/snapshots/pre_m3_concept_key_cleanup_2026-08-04.db` |

The in-DB snapshot is a plain table with no `UNIQUE` constraint, no indexes, and
no `map_id` primary key — `CREATE TABLE AS SELECT` does not carry those over.
That does not affect rollback, which joins on `map_id` as a plain column.

Both snapshots may be dropped once the full sequence (cleanup → M2 build for DBS
and UOB → `load_anchors` → verification) has succeeded. Not before.

---

## Rollback SQL

SQLite does not support `UPDATE ... FROM` before 3.33. Both forms are given; the
correlated-subquery form works on every version.

**Portable form (recommended):**

```sql
BEGIN;

UPDATE bank_line_map
   SET concept_key = (
       SELECT s.concept_key
         FROM bank_line_map_pre_cleanup_2026_08_04 s
        WHERE s.map_id = bank_line_map.map_id
   )
 WHERE EXISTS (
       SELECT 1
         FROM bank_line_map_pre_cleanup_2026_08_04 s
        WHERE s.map_id = bank_line_map.map_id
   );

COMMIT;
```

**`UPDATE ... FROM` form (SQLite ≥ 3.33):**

```sql
UPDATE bank_line_map
   SET concept_key = s.concept_key
  FROM bank_line_map_pre_cleanup_2026_08_04 s
 WHERE bank_line_map.map_id = s.map_id;
```

There is no `binding_source` column to restore; drop that clause from the
rollback plan as written in the task.

**Verify rollback succeeded:**

```sql
SELECT COUNT(*) FROM bank_line_map WHERE concept_key IS NOT NULL;   -- expect 593

SELECT COUNT(*) FROM bank_line_map b
  JOIN bank_line_map_pre_cleanup_2026_08_04 s USING (map_id)
 WHERE b.concept_key IS NOT s.concept_key;                          -- expect 0
```

**Full-file rollback** (if the DB is damaged rather than just wrong):

```bash
cp findociq/db/snapshots/pre_m3_concept_key_cleanup_2026-08-04.db \
   findociq/db/compiled_fs.db
```

Note this also reverts the snapshot table's own creation, and any other change
made to the DB after 14:21 on 2026-08-04.

---

## Out of scope, confirmed untouched

`lineage_identity_map.csv` · `canonical_leaf` · `canonical_leaf_alias` ·
`backfill_map.py` · `apply_dashboard_rows.py` · `load_anchors.py` · `app/` ·
no new `.py` file anywhere in the repo. `load_anchors` was not run.
