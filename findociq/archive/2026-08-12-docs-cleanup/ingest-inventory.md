# FinDocIQ ingest pipeline — inventory

Read-only survey, 2026-08-04, branch `mapping/close-logged-items`. No script or
data modified. Every claim below is backed by a file:line or a query result run
against `findociq/db/compiled_fs.db` at the time of writing.

**Headline:** stages 1a → 2a are orchestrated and automatic. Stages 2b and 3 are
**not wired into any driver** — they are hand-run scripts. Full answer in §5.

---

## Section 1 — Stage-by-stage script inventory

### Stage 1a — PDF → parsed JSON (extraction)

| | |
|---|---|
| **Canonical script** | `pipeline/PASS2_v2.py` |
| **Entry point** | CLI: `python3 PASS2_v2.py <pdf> --toc <toc.json> --no-pause --workers 5 [--family F] [--section S] [--batch] [--force]`, run with `cwd=pipeline/` |
| **Invoked by** | `run_doc.py:435` (`step2_extract`) |
| **Input** | PDF path + the TOC JSON from STEP 1 |
| **Output** | Filesystem only — no DB writes. Per-unit audit dirs under `outputs/<family>/<bank_period>/audit/<pdf_stem>/<unit>/`, each holding `meta.json` + `parsed.json`. |

Two prerequisite steps produce 1a's inputs, both inside `run_doc.py`:

- **STEP 0** `pipeline/discover/section/candidates.py` (PaddleOCR, needs
  `.venv-paddle`) → `data/derived/paddle_scans/<doc_id>/regions.csv`. Skipped if
  `regions.csv` exists.
- **STEP 1** routes by document family (`run_doc.py:388`): `pillar3` →
  `discover/pass1_toc.py` + `toc/pass1_to_v7.py` (deterministic, zero-API);
  everything else → `toc/toc_stage.py` (Gemini). Both converge on
  `data/derived/toc/<doc_id>_toc.json`, then `toc/toc_to_db.py` writes the
  `document` and `section` rows.

**Not canonical, same stage:** `pipeline/extract_run.py` ("reconciled extraction
driver"), `pipeline/universal/auto_extract.py` (HTML-out OBSERVE→EXTRACT),
`pipeline/pass2/extract.py` (the Gemini-call library PASS2_v2 imports — a module,
not a driver). See §3.

### Stage 1b — parsed JSON → `compiled_fs.db` (loading)

| | |
|---|---|
| **Canonical script** | `pipeline/pass2/load_v7.py` |
| **Entry point** | `load_units(db_path, doc_id, units)` — `load_v7.py:1732`. Called **in-process**, not by subprocess, from `run_doc.py:294` (`load_doc`). |
| **Input** | One doc-scoped list of units built by `run_doc.build_units_from_audit()` (`run_doc.py:149`) from the audit dirs — each `{section_id, pages, parsed_path, table_type?}`. Units with no `section_ids`, unparseable JSON, or zero tables are skipped. |
| **Output tables** | Writes `table_t`, `col_dim`, `row_dim`, `cell_fact`. Get-or-creates the global `row_lineage` / `col_lineage` registries. |
| **Does NOT write** | `document`, `section` — "OWNED UPSTREAM (TOC stage). This loader asserts they exist and NEVER authors or deletes them" (`load_v7.py:7`). |

Doc-scoped and idempotent by delete-then-reload: `load_v7.py:1061-1064` deletes
`cell_fact` / `row_dim` / `col_dim` / `table_t` for that `doc_id` before loading.

Also run here: **STEP 2b geometry** (`run_doc.py:450`, `pass2.geometry` side-car,
in-process) supplies PDF-layer row ground truth. It never fails the run — when
absent, `load_v7` falls back to the model's per-table levels.

### Stage 2a — `table_type_id` tagging (level-1 M2)

| | |
|---|---|
| **Canonical script** | `pipeline/mapping/seed_registry.py` → `pipeline/mapping/registry.py::classify_corpus()` |
| **Entry point** | CLI: `python3 findociq/pipeline/mapping/seed_registry.py --db <db>`. Invoked by `run_doc.py:518` (`step3b_registry`). |
| **Input — files** | **`table_registry.yaml` only.** |
| **Input — DB tables** | `table_t` ⨝ `document` ⨝ `section` (`registry.py:101-107`) — reads `institution`, `section_title`, `table_title`, `table_title_clean`. |
| **Output** | `UPDATE table_t SET table_type_id = ?` (`registry.py:118`). Also UPSERTs `table_registry` + `table_registry_alias` from the YAML. Never overwrites `table_t.table_type` (as-reported preserved), never overwrites a `source='human_confirmed'` alias. |

> **Does it read `table_registry_seed.csv`? No.** Verified:
> `grep -c table_registry_seed pipeline/mapping/registry.py
> pipeline/mapping/seed_registry.py` → **0 and 0**.
>
> The seed CSV feeds `migrate_add_table_catalog.py` → `table_catalog`, which is
> the **masterlist** (what tables *should* exist) and is read by the app and by
> `resolve_anchors.py` as a confirmation. It plays no part in tagging. Tagging is
> driven entirely by the YAML's caption/section aliases. This matters for §5.

### Stage 2b — `bank_line_map` population

| | |
|---|---|
| **Canonical script** | `pipeline/mapping/backfill_map.py` |
| **Entry point** | CLI: `python3 findociq/pipeline/mapping/backfill_map.py --db <db>` (`backfill_map.py:188`); library: `backfill(con)` at `:120`, `collect(con)` at `:77` |
| **Input — DB tables** | The corpus itself: `table_t` (for `table_type_id`), `row_dim` (`row_leaf_label` → `normalize_row_label`), `row_lineage` (for `parent_label_norm`), `cell_fact` (to decide `is_abstract`), `document` (bank), plus the concept dictionary for `period_type`. |
| **Output** | `INSERT INTO bank_line_map` at `map_status='ai_proposed'`, `mapped_by='backfill:corpus'`. Columns written: the anchor 4 (`bank`, `table_type_id`, `row_label_norm`, `parent_label_norm`), plus `concept_key` (only when every stamped occurrence agrees — disagreements are inserted NULL with rivals recorded in `note`), `period_type`, `is_abstract`, `legal_entity`, `industry_key`, `map_status`, `mapped_by`, `mapped_at`, `note`. `balance` is deliberately left NULL. |

**Two other scripts write `bank_line_map`** and are part of this stage in
practice:

- `pipeline/mapping/apply_dashboard_rows.py` — `dashboard_rows.yaml` →
  `human_confirmed` rows. 94 bindings before yesterday's cleanup.
- `pipeline/mapping/load_anchors.py` — stage 3, below.

**`backfill_map.py` is canonical for *creating* the address rows.** The other two
*update* rows at existing addresses.

> **Not in any driver.** `grep -n "backfill_map\|apply_dashboard_rows\|load_anchors" pipeline/run_doc.py` → no
> matches. All three are hand-run.

### Stage 3 — `concept_key` tagging (M3)

| | |
|---|---|
| **Canonical script** | `pipeline/mapping/load_anchors.py` (confirmed) |
| **Entry point** | CLI: `python3 findociq/pipeline/mapping/load_anchors.py --db <db>` (`:165`); library: `load(con)` at `:57` |
| **Input** | `data/derived/lineage_identity_map.csv` (`MAP_CSV`, `:46`) + `bank_line_map`. Imports `resolve_anchors.py` at runtime to resolve each authored anchor to a physical address. |
| **Output** | `bank_line_map.concept_key`, at `map_status='human_confirmed'`, `mapped_by='lineage_identity_map.csv'`, `confidence=1.0`. Also writes `concept_disclosure` for `resolution='not_disclosed'` rows. |

Only `resolution='anchor'` rows that resolve PASS are loaded. `derived` routes to
the derivation layer, `not_disclosed` to `concept_disclosure`, `pending_anchor` /
`pending_extraction` are reported and skipped (`load_anchors.py:6-11`).

### Summary — is there a canonical script for every stage?

| Stage | Canonical script | In a driver? |
|---|---|---|
| 1a extraction | `PASS2_v2.py` | ✅ `run_doc.py` STEP 2 |
| 1b loading | `pass2/load_v7.py::load_units` | ✅ `run_doc.py` STEP 3 |
| 2a `table_type_id` | `mapping/seed_registry.py` | ✅ `run_doc.py` STEP 3b |
| 2b `bank_line_map` | `mapping/backfill_map.py` | ❌ **manual** |
| 3 `concept_key` | `mapping/load_anchors.py` | ❌ **manual** |

No stage is a "manual step" in the sense of having no script — all five have one.
Two have no orchestration.

---

## Section 2 — Existing orchestration

**Yes — `run_doc.py` chains stages 1a, 1b and 2a. It does not touch 2b or 3.**

`pipeline/run_doc.py` (1129 lines) is "the ONE-COMMAND driver for the FS pipeline",
every step idempotent and resumable.

| run_doc step | Stage | Script |
|---|---|---|
| STEP 0 scan | pre-1a | `discover/section/candidates.py` (PaddleOCR, `.venv-paddle`) |
| STEP 1 TOC | pre-1a | `toc/toc_stage.py` (or `discover/pass1_toc.py` + `toc/pass1_to_v7.py` for pillar3) → `toc/toc_to_db.py` |
| STEP 2 extraction | **1a** | `PASS2_v2.py` |
| STEP 2b geometry | 1b input | `pass2.geometry` (in-process) |
| STEP 3 load | **1b** | `pass2.load_v7.load_units` (in-process) |
| STEP 3b registry | **2a** | `mapping/seed_registry.py` |
| STEP 4a concepts | post-3 | `concept/run.py` |
| STEP 4b fact_metric | post-3 | `concept/build_fact_metric.py` |
| STEP 4c ratios | post-3 | `concept/compute_ratios.py` |
| STEP 5 verify | — | pdfplumber check + auto re-extract, ≤2 rounds |
| STEP 6 xlsx | — | `db_check_xlsx.py` |
| STEP 7 sync_bq | — | `ingest/sync_bq.py` (skip with `--no-sync-bq`) |

**Skipped / assumed already done:** stage 2b (`backfill_map.py`) and stage 3
(`load_anchors.py`). `run_doc.py` assumes `bank_line_map` is already populated —
STEP 4a's `stamp_human_anchors()` *reads* `bank_line_map` and projects existing
`human_confirmed` anchors onto the new document. It never creates them.

Ordering note worth keeping: STEP 3b runs **before** STEP 4a deliberately, because
`stamp_human_anchors()` returns early without a `table_type_id`. The docstring at
`run_doc.py:493` records the measured consequence of getting this wrong — on a
DBS_4Q25 dry-run re-ingest, 0/45 tables classified and 0 anchors projected.

**CLI signature:**

```
python3 findociq/pipeline/run_doc.py --pdf <path>
  [--db PATH] [--doc-period YYYY-MM-DD] [--bank BANK] [--all] [--dry-run]
  [--batch] [--force] [--no-llm] [--no-sync-bq]
  [--ipv4-shim | --no-ipv4-shim]        # shim ON by default (IPv6 blackhole host)
  [--rebuild-db] [--verify-only]
  [--defer-db-steps] [--db-steps-only]  # batch sweeps: run O(corpus) steps once
```

Typical: `python3 findociq/pipeline/run_doc.py --pdf findociq/data/sources/financial_statements/DBS/2026/1Q26/DBS_1Q26_performance_summary.pdf`

Two further entry points in the same file: `--rebuild-db` (whole DB from
`schema_v7` + every cached TOC with a matching audit dir) and `--verify-only`
(load-from-artifacts + verify, no extraction, $0).

Higher-level orchestrators exist above `run_doc.py`: `pipeline/ingest_quarter.py`
(one bank, one period) and `pipeline/ingest_manifest.py` (every doc on disk,
reconciled against `data/sources/manifest.csv`). Neither adds stage 2b or 3.

---

## Section 3 — Dead / redundant candidates

Name matches `extract|ingest|load|map|tag|classif|populate|backfill`, and not
canonical for stages 1a–3. **Nothing deleted.**

| Path | Purpose (docstring) | Verdict |
|---|---|---|
| `pipeline/extract_run.py` | "reconciled extraction driver" — routing signals from `route/scan.py` | **suspected dead** — superseded by `PASS2_v2.py`; not referenced by `run_doc.py` |
| `pipeline/universal/auto_extract.py` | "automated OBSERVE→EXTRACT pipeline (HTML out)" | **suspected dead** — HTML output, not the schema_v7 path |
| `pipeline/pass2/extract.py` | "Gemini API calls, prompt building, unit grouping, caching" | **live, not a driver** — the library `PASS2_v2` imports |
| `pipeline/route/merge_map.py` | "per-table geometric structure map, all derived from the table's own ink" | **unclear** — geometry helper; name matches `map` but unrelated to `bank_line_map` |
| `pipeline/classify/family.py` | "document-family classifier (the doc-family ROUTER's decision)" | **live** — routing input to STEP 1 |
| `pipeline/ingest/scrape_bank_ir.py` | "crawl each bank's IR page for quarterly PDFs" | **live, upstream** — acquisition, before stage 1a |
| `pipeline/ingest/sync_bq.py` | "sync the local SQLite ground truth to BigQuery" | **live, downstream** — STEP 7 |
| `pipeline/ingest_quarter.py` | "one bank, one period, end to end" | **live** — orchestrator above `run_doc.py` |
| `pipeline/ingest_manifest.py` | "manifest-driven orchestrator: full ingestion of every doc already on disk" | **live** — batch orchestrator |
| `pipeline/ingest_status.py` | ingest state tracking | **live** — imported by `run_doc.py` |
| `pipeline/mapping/audit_coverage.py` | "key-field extractability audit — replays the 89 human_confirmed dashboard anchors" | **live, diagnostic** — the "89" is stale vs today's 104 |
| `pipeline/pass2/backfill_col_period.py` | "re-derive `col_dim.col_period` for columns loaded BEFORE a period-grammar improvement" | **one-shot migration**, spent |
| `pipeline/tag_workbook.py` | "DB rows → per-table-family concept-tagging Excel" | **live, side path** — human tagging workbook, not ingest |
| `pipeline/concept/load_dictionary.py` | concept dictionary loader + `ensure_schema` | **live** — used by STEP 4a |
| `pipeline/mapping/apply_dashboard_rows.py` | `dashboard_rows.yaml` → `bank_line_map` human_confirmed | **live, stage 2b**, hand-run |
| `pipeline/mapping/quarantine_f2_geo_wildcard.py` | tag-don't-delete quarantine for F2 geo wildcards | **live, one-shot** |
| `pipeline/mapping/quarantine_duplicate_page_tables.py` | OCBC duplicate-page-split quarantine | **live, one-shot stopgap** |
| `pipeline/migrate_add_mapping_layer.py`, `mapping/migrate_add_*.py`, `mapping/migrate_consolidate_table_type_ids.py`, `migrate_ingest_status_keys.py` | schema migrations | **spent one-shots**, idempotent, keep for rebuild |
| `pipeline/fix_identity_misstamps.py` | one-shot identity repair | **suspected spent** |

Highest-confidence dead: `extract_run.py` and `universal/auto_extract.py`. Both
predate the PASS2_v2 + schema_v7 path and are unreachable from any driver.

---

## Section 4 — Data-shape sanity check (DBS 4Q25)

`doc_id = DBS_4Q25_performance_summary`, `doc_period = 2025-12-31`,
`doc_family = financial_stmt`.

Audit root: `outputs/pillar3/dbs_4Q25/audit/DBS_4Q25_performance_summary/`.
**Note the path** — an FS document filed under `outputs/pillar3/`.
`find_audit_root()` globs every family root (`run_doc.py:143`) so it still
resolves, but the directory name is misleading.

### Stage 1a output — parsed JSON, first 15 lines

`.../overview_p4-8/parsed.json`, table 0 of 4:

```json
{
  "title": "Selected income statement items ($m)",
  "label_header": "",
  "continued_from_previous": false,
  "section_id": "",
  "columns": [
    {
      "group": null,
      "leaf": "2nd Half 2025"
    },
    {
      "group": null,
      "leaf": "2nd Half 2024"
    },
    {
```

Top-level shape: `{"tables": [...]}`; each table has
`title`, `label_header`, `continued_from_previous`, `section_id`, `columns`, `rows`.

### Stage 1b output — `row_dim`

22 columns: `doc_id, table_id, row_id, row_hierarchy, row_parent, row_leaf_label,
row_period, period_span, period_start, line_no, concept_key, geo_key, segment_key,
unit, sums_to, sums_sign, row_lineage_id, industry_key, row_leaf_label_clean,
concept_key_human, segment_key_human, identity_source`.

`SELECT * FROM row_dim WHERE doc_id='DBS_4Q25_performance_summary' LIMIT 5`
(abridged to the populated columns):

| row_id | table_id | row_hier | row_parent | row_leaf_label | concept_key | geo_key | row_lineage_id |
|---|---|---|---|---|---|---|---|
| 1 | allowances_for_credit_and_other_losses | 1 | — | ECL Stage 1 and 2 (GP) | `bs.credit.allowances_stage12_gp` | — | 1 |
| 2 | allowances_for_credit_and_other_losses | 1 | — | ECL Stage 3 (SP) for loans¹ | `pnl.provisions.stage3_sp` | — | 1273 |
| 3 | allowances_for_credit_and_other_losses | 2 | 2 | Singapore | — | SG | 1274 |
| 4 | allowances_for_credit_and_other_losses | 2 | 2 | Hong Kong | — | HK | 1275 |
| 5 | allowances_for_credit_and_other_losses | 2 | 2 | Rest of Greater China | — | GC_EX_HK | 1276 |

> **`row_dim.concept_key` is a different column from `bank_line_map.concept_key`**
> and is still populated. Yesterday's cleanup
> (`docs/m3-cleanup-report.md`) touched only `bank_line_map`.

### Stage 2a output — `table_type_id` on DBS 4Q25

| | count |
|---|---|
| populated | **38** |
| NULL | **7** |
| total `table_t` rows | 45 |

Breakdown (22 distinct types): `FS_NII_DETAIL` 7, `FS_NPA_COVERAGE` 5,
`FS_EQUITY_CHANGES_COMPANY` 4, `FS_PERF_BY_GEOGRAPHY` 3,
`FS_EQUITY_CHANGES_GROUP` 2, `FS_PERF_BY_SEGMENT` 2, then 1 each for
`FS_ALLOWANCES`, `FS_BALANCE_SELECTED`, `FS_BALANCE_STATUTORY`,
`FS_CAPITAL_ADEQUACY`, `FS_CASHFLOW`, `FS_COMPREHENSIVE_INCOME`,
`FS_CUSTOMER_DEPOSITS`, `FS_CUSTOMER_LOANS`, `FS_DEBTS_ISSUED`,
`FS_EXPENSES_DETAIL`, `FS_FAIR_VALUE_HIERARCHY`, `FS_INCOME_SELECTED`,
`FS_INCOME_STATUTORY`, `FS_PER_SHARE`, `FS_RATIOS_KEY`. Plus 7 `<NULL>`.

`FS_INCOME_SELECTED` resolves to `table_id =
overview_selected_income_statement_items_m_2025-12-31`, title "Selected income
statement items ($m)" — the same table as the Stage 1a sample above. **1a → 1b →
2a is traceable end to end for this table.**

**84% classified. The 7 NULLs are the level-1 M2 gap for this document** — tables
whose title/section produced no YAML alias match.

### Stage 2b output — `bank_line_map` (DBS)

21 columns (schema in `docs/m3-store-relationship.md` §2).
`SELECT * FROM bank_line_map WHERE bank='DBS' LIMIT 5`:

| map_id | table_type_id | row_label_norm | parent | concept_key | is_abstract | map_status | mapped_by |
|---|---|---|---|---|---|---|---|
| 1 | FS_ALLOWANCES | `1_refers_to_expected_credit_loss` | '' | NULL | 1 | ai_proposed | backfill:corpus |
| 2 | FS_ALLOWANCES | `1_sp_for_loans_by_geography_are_` | '' | NULL | 1 | ai_proposed | backfill:corpus |
| 3 | FS_ALLOWANCES | `1_sp_for_loans_by_geography_are_` | notes | NULL | 1 | ai_proposed | backfill:corpus |
| 4 | FS_ALLOWANCES | `2_sp_for_loans_by_geography_are_` | '' | NULL | 1 | ai_proposed | backfill:corpus |
| 5 | FS_ALLOWANCES | `allowances_for_other_assets` | '' | NULL | 0 | ai_proposed | backfill:corpus |

Rows 1–4 are footnote text captured as structural headers (`is_abstract=1`,
note "structural header: every occurrence has zero cells"). Row 5 is a real line
item with no concept ("no concept stamped in corpus"). This is
`backfill_map.py`'s honest-NULL behaviour, working as documented.

### Stage 3 output — DBS `concept_key` bindings

```sql
SELECT bank, concept_key, table_type_id, row_label_norm
  FROM bank_line_map WHERE bank='DBS' AND concept_key IS NOT NULL LIMIT 10;
```

**0 rows.**

Expected — not a defect. Yesterday's M3 cleanup wiped all 593 `concept_key`
values corpus-wide, DBS's 283 among them. Snapshot
`bank_line_map_pre_cleanup_2026_08_04` holds them; rollback SQL and the full
pre-cleanup distribution are in `docs/m3-cleanup-report.md`. Stage 3 has not been
re-run since.

---

## Section 5 — Can a fresh DBS PDF flow 1b → 2a → 2b → 3 and end up correctly tagged?

**Partially.** Stages 1b and 2a run automatically and correctly. Stages 2b and 3
do not run at all without a human, and stage 3 has a known scope gap for DBS.

### Works automatically for a new doc

- **Stage 1b.** `run_doc.py` STEP 3 calls `load_units` doc-scoped, delete-then-reload,
  fully idempotent. Nothing per-bank.
- **Stage 2a.** `run_doc.py` STEP 3b calls `classify_corpus()`. Deterministic
  alias resolution over the whole corpus, no per-document config.
- **STEP 4a's anchor projection** — `stamp_human_anchors()` projects existing
  `human_confirmed` `bank_line_map` anchors onto the new document's rows, keyed on
  `(bank, table_type_id, row_label_norm, parent_label_norm)`. **For an unchanged
  table this is what makes a new quarter work with zero human input** — but only
  if stage 3 has been run at some point, which right now it has not.

### Requires human intervention

- **New or relabelled rows.** A row whose normalized label isn't already an
  address gets a fresh `ai_proposed` `bank_line_map` row from `backfill_map.py`
  with `concept_key` NULL. `ai_proposed` never loads a value. A human must author
  the binding — in `lineage_identity_map.csv` (then `load_anchors.py`) or in
  `dashboard_rows.yaml` (then `apply_dashboard_rows.py`).
- **New table captions.** 2a matched 38/45 on DBS 4Q25; a caption with no YAML
  alias yields `table_type_id IS NULL`, and `stamp_human_anchors()` then returns
  early for that table — no anchors project, so every row in it is dark. The fix
  is a new alias in `table_registry.yaml`, authored by hand.
- **Running stages 2b and 3 at all.** Neither is in any driver. Confirmed:
  `grep -n "backfill_map\|apply_dashboard_rows\|load_anchors" pipeline/run_doc.py`
  → no matches.

### Known-broken / gaps for new docs

1. **`table_registry_seed.csv` does not participate in tagging.** The premise in
   the question — "table_type_id (from `table_registry_seed.csv`)" — does not hold.
   Tagging reads `table_registry.yaml`; the seed CSV populates `table_catalog`,
   the masterlist. Verified: 0 references to the seed CSV in `registry.py` and
   `seed_registry.py`. A caption added to the seed CSV and **not** to the YAML will
   not tag anything. The two L1 sources are also mutually inconsistent — the seed
   renames or folds 11 of the YAML's original 26 ids
   (`migrate_add_table_catalog.py:58-62`); see
   `docs/specs/2026-08-04-masterlist.md` §2.
2. **Three DBS KPH concepts cannot be restored by stage 3.**
   `bs.nav_per_share`, `pnl.eps.basic`, `pnl.eps.diluted` are
   `resolution='pending_extraction'` in `lineage_identity_map.csv`, which
   `load_anchors.py` skips by design. Running stage 3 today leaves these three DBS
   dashboard cells blank. Detail: `docs/m3-cleanup-report.md`.
3. **Stage 3 restores at most 72 bindings** corpus-wide (the CSV's `anchor` rows:
   DBS 21 / OCBC 25 / UOB 26) against the 593 that existed. Whether that is
   sufficient is a KPH-coverage question, not a row-count one.
4. **7/45 DBS 4Q25 tables are unclassified** — the level-1 M2 gap above.
5. **DBS has no `canonical_leaf` set** (OCBC only, 364 leaves), so there is no
   level-2 M2 gate for a new DBS document. Out of scope here; noted because it is
   what makes "correctly tagged" unverifiable for DBS today.

**Bottom line:** a fresh DBS PDF reaches correct `table_type_id` automatically for
~84% of its tables. It reaches `concept_key` only if a human runs two more
scripts, and even then three KPH concepts stay unbound.

---

## Section 6 — Minimum viable orchestration (runbook)

Ingest one new DBS PDF end to end, today. Runtimes are order-of-magnitude from
prior sessions, not measured for this document — treat as estimates.

```bash
cd ~/FinDocIQ

# 0. Prereqs. PaddleOCR needs .venv-paddle; everything else uses .venv.
#    STEP 0 must run UNSANDBOXED (libomp).           [~2-5 min for a new PDF]

# 1. Stages 1a + 1b + 2a + concepts + verify + xlsx, one command.
#    Covers run_doc STEPs 0,1,2,2b,3,3b,4a,4b,4c,5,6.   [~10-25 min, Gemini-bound]
python3 findociq/pipeline/run_doc.py \
  --pdf findociq/data/sources/financial_statements/DBS/2026/1Q26/DBS_1Q26_performance_summary.pdf \
  --no-sync-bq

# 2. MANUAL GATE — inspect what 2a failed to classify.        [~1 min]
sqlite3 findociq/db/compiled_fs.db \
  "SELECT table_id, table_title FROM table_t
    WHERE doc_id='DBS_1Q26_performance_summary' AND table_type_id IS NULL;"
#    Any row here -> author an alias in pipeline/mapping/table_registry.yaml,
#    then re-run:  python3 findociq/pipeline/mapping/seed_registry.py --db findociq/db/compiled_fs.db

# 3. Stage 2b — create address rows for anything new.          [~1-2 min]
python3 findociq/pipeline/mapping/backfill_map.py --db findociq/db/compiled_fs.db

# 4. Stage 2b (authored) — dashboard anchors.                  [<1 min]
python3 findociq/pipeline/mapping/apply_dashboard_rows.py --check   # inspect first
python3 findociq/pipeline/mapping/apply_dashboard_rows.py --db findociq/db/compiled_fs.db

# 5. Stage 3 — concept bindings from the authoritative CSV.    [<1 min]
python3 findociq/pipeline/mapping/load_anchors.py --db findociq/db/compiled_fs.db

# 6. Rebuild the serving layer after 2b/3 changed bindings.    [~1-2 min]
python3 findociq/pipeline/concept/build_fact_metric.py --db findociq/db/compiled_fs.db

# 7. Verify.                                                    [~1 min]
python3 findociq/pipeline/preflight_invariants.py --db findociq/db/compiled_fs.db

# 8. Persist — the workstation loses everything outside /home.  [~1 min]
gsutil cp findociq/db/compiled_fs.db \
  gs://findociq-sources-igc2026-team08-6311/db/compiled_fs.db
```

Eight commands plus one manual gate. It fits the ~10-command budget.

### Ordering constraints that are not optional

- **2a before 4a.** `stamp_human_anchors()` returns early without a
  `table_type_id`. `run_doc.py` already enforces this internally (STEP 3b before
  STEP 4a).
- **2b before 3.** `load_anchors` updates rows at existing addresses; if
  `backfill_map` hasn't created the address, the anchor inserts fresh rather than
  confirming — a different and less-reviewed path.
- **Step 4 before step 5, or accept last-writer-wins.**
  `apply_dashboard_rows` overwrites any row not at `human_corrected`, including
  rows `load_anchors` wrote. Running it *after* step 5 silently replaces stage-3
  bindings. See `docs/m3-store-relationship.md` §5.

### Known failure modes

| Step | Failure | Response |
|---|---|---|
| 0 | PaddleOCR / libomp under sandbox | Run STEP 0 manually, unsandboxed, then re-run `run_doc.py`. The error message prints the exact command. |
| 1 | `toc_to_db` "table_t rows reference…" | Expected on a re-run. `run_doc.py:420` catches it, skips the section rewrite, continues. |
| 2 | Gemini IPv6 blackhole | `--ipv4-shim` is ON by default; use `--no-ipv4-shim` only when a shim conflict is diagnosed. |
| 2 | Extraction disagrees with PDF | STEP 5 auto re-extracts, ≤2 rounds, then gives up and reports. |
| 5 | `load_anchors` exits with "unexpected unresolved concept_key conflict" | By design (`load_anchors.py:130`) — it refuses to overwrite a conflicting `human_confirmed` binding. Resolve at concept level; don't force. |
| 5 | 3 DBS KPH concepts stay unbound | Known, §5 item 2. Requires promoting those CSV rows from `pending_extraction` to `anchor`. |
| 7 | `preflight_invariants` A1a fails | It hard-codes `human_confirmed == 104`; that count is stale after any 2b/3 change. Read it as a change detector, not a correctness gate. |

---

## Scope note

Read-only as specified. No script, data file, or DB row was modified in producing
this document. The only new file is this one.

Related: `docs/architecture/00-overview.md` (M1/M2/M3 model),
`docs/specs/2026-08-04-masterlist.md` (masterlist storage + one-writer rule),
`docs/m3-store-relationship.md` (the three `concept_key` writers),
`docs/m3-cleanup-report.md` (yesterday's wipe), `PIPELINE.md` (run instructions).
