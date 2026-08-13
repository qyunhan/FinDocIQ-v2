# THE MASTERLIST — what it is, where it is stored, and the one-place rule

**Status:** authoritative. Supersedes every scattered description of "the
masterlist" living in individual script docstrings. If a script needs to explain
the masterlist, it links here instead of re-describing it.

The masterlist is a **pipeline component**, not a dashboard feature. The
Table Registry tab *renders* it; it does not own it.

---

## 0. Definition

The masterlist answers two questions, at two levels, independent of which
document or period any given fact came from:

| Level | Question | Grain |
|---|---|---|
| **L1 — table** | Which tables does this bank report, in which document kind, in which section? | `(bank, doc_kind, section_canonical, table_type_id)` |
| **L2 — line item** | Which rows exist inside one of those tables, in what printed order? | `(bank, table_type_id, canonical_leaf_id)` |

L1 is **declared** (hand-authored from official filings — the one manual step
CLAUDE.md tolerates). L2 is **derived** (built from a benchmark document
instance by code, never hand-curated).

---

## 1. Where it is stored — the canonical locations

### L1 — table level

| Artifact | Kind | Contents |
|---|---|---|
| `findociq/data/derived/table_registry_seed.csv` | hand-authored seed, git-tracked | **The L1 source of truth.** 102 rows: DBS 31 (`performance_summary`), UOB 28 (`condensed_financial_statements`), OCBC 43 (22 `media_release_financial_highlights` + 21 `condensed_financial_statements`). Authored from the real 4Q25 documents. |
| `findociq/pipeline/mapping/table_registry.yaml` | hand-authored seed, git-tracked | The bank-agnostic `table_type_id` **vocabulary** + caption aliases. Not the masterlist itself — the type dictionary the masterlist's `table_type_id` column points into. |
| `table_catalog` (SQLite) | materialized | The seed loaded verbatim + a normalized caption column. 102 rows. Carries `cadence` / `expected` / `is_narrative` for coverage checks. |
| `table_registry`, `table_registry_alias` (SQLite) | materialized | The YAML vocabulary. 43 types / 81 aliases. |
| `section_registry`, `doc_cadence` (SQLite) | hand-authored inside the loader | `(bank, doc_kind, section_raw_norm) -> section_canonical`, and `doc_id -> cadence`. The seed has no columns to derive these from. |

### L2 — line-item level

| Artifact | Kind | Contents |
|---|---|---|
| `canonical_leaf`, `canonical_leaf_alias` (SQLite) | **derived** | The declared, ORDERED leaf set per `(bank, table_type_id)`. **Currently OCBC only** — 364 leaves across 13 table types. DBS and UOB are NOT built. |
| `bank_line_map` (SQLite) | accumulated | **NOT the masterlist.** A period-agnostic, additive UNION of every address ever seen since 2022 — footnote variants, mis-parented defects, addresses from document forms that no longer exist. It is the *identity/concept binding* store. It enriches the masterlist; it can never define it. (Confirmed live: DBS `FS_PER_SHARE` has 12 accumulated addresses for a table that only ever prints 7.) |

**Nothing else stores the masterlist.** In particular, no CSV/XLSX export, no
`outputs/` artifact, and no in-app cache is a source of truth — all of those are
regenerable renderings.

---

## 2. Who writes it — and the one-place rule

> **RULE: one writer per masterlist level. Do not add a second script that
> stores masterlist state.** A new consumer reads the tables above. A new
> *source* of masterlist state is a change to the existing writer, not a new
> script beside it.

Current writers:

| Level | Writer | Reads | Writes |
|---|---|---|---|
| L1 | `pipeline/mapping/migrate_add_table_catalog.py` | `table_registry_seed.csv` | `table_catalog`, `section_registry`, `doc_cadence` |
| L1 (vocabulary) | `pipeline/mapping/seed_registry.py` | `table_registry.yaml` | `table_registry`, `table_registry_alias` |
| L2 | `pipeline/mapping/m2_canonical_leaf.py` | `row_dim` @ benchmark period | `canonical_leaf`, `canonical_leaf_alias` |

Both L1 writers are additive + idempotent; seed rows are UPSERTed and
`source='human_confirmed'` aliases are never overwritten by a re-seed.

`pipeline/mapping/migrate_consolidate_table_type_ids.py` is a **one-shot
rename migration**, not a writer — it re-points existing rows when the
vocabulary changes. It does not author masterlist state.

### Known debt — L1 is split across two writers

L1 is authored in **two** files (`table_registry_seed.csv` + `table_registry.yaml`)
and loaded by **two** scripts. That split is historical, not designed: the YAML
vocabulary predates the seed CSV, and the seed's `table_type_id` column renames
or folds 11 of the YAML's original 26 ids. The target state is a single L1
writer over a single seed. Until that is done, `table_registry_seed.csv` wins on
any disagreement — it is the one authored against the real 4Q25 documents.

---

## 3. Who reads it

- `app/findociq_app.py` — `table_masterlist_frame()` (`:213`) joins `table_catalog`
  (expected) against live `table_t` occurrences (captured). `line_item_benchmark_frame()`
  (`:370`) builds the L2 display from `row_dim` at `BENCHMARK_PERIOD = "2025-12-31"`,
  enriched — not defined — by `bank_line_map`.
- `pipeline/mapping/resolve_anchors.py:215` — consults `table_catalog` as a
  *confirmation* of a resolved `table_type_id`, deliberately not as a gate.
- `pipeline/mapping/registry.py` `classify_corpus()` — classifies live tables
  into the `table_registry` vocabulary.
- `pipeline/concept/build_fact_metric.py` — `table_registry` for statement class /
  period nature.

**Renderings are never stored.** `table_masterlist_frame()` and
`line_item_benchmark_frame()` compute on every render, by design — a persisted
copy would become a third place the masterlist lives.

---

## 4. Live coverage (as of 2026-08-04)

| Store | Rows | DBS | OCBC | UOB |
|---|---|---|---|---|
| `table_catalog` (L1) | 102 | 31 | 43 | 28 |
| `table_registry` | 43 | bank-agnostic | | |
| `table_registry_alias` | 81 | 79 `*` | 1 | 1 |
| `canonical_leaf` (L2) | 364 | **0 — not built** | 364 | **0 — not built** |
| `bank_line_map` (enrichment only) | 2329 | 984 | 890 | 455 |

**The open gap is L2 for DBS and UOB.** L1 is complete for all three banks.

---

## 5. Excluded from the masterlist

- **Pillar 3.** L1 is scoped to `doc_family='financial_stmt'` only.
- **Narrative tables.** `is_narrative=1` rows exist in `table_catalog` but are
  filtered out of every masterlist rendering.
- **Quarantined duplicates.** `table_t.dedup_status` non-empty rows are excluded
  from live-occurrence counts (see `docs/DECISIONS.md` 2026-08-04, the OCBC
  `media_release_financial_highlights` 17-cluster split).
