# GCP_TASKS.md — immediate cloud tasks + where to record lineage

**For the agent running on GCP (Cloud Shell / dev VM / Cloud Run).** Read
`HANDOFF.md` first for the full architecture; this file is the near-term task list
and the GCP-native tooling to use. Pair it with the adoption plan we mapped
(pipelines · governance · administration · migration).

## Context you're starting from

- **Ingestion is configured.** The source bank sites are wired up, so documents
  **auto-download** and land for parsing — you do not fetch them by hand. Treat the
  landing bucket (Cloud Storage) as the entry point; a new file there is the trigger.
- **Only two families are in scope: `pillar3` and financial statements (`fs`).**
  The driver auto-detects family (`findociq/pipeline/classify/family.py`, content
  fingerprint) and routes the TOC step:
  - `pillar3` → the deterministic **`pass1_toc`** framework (proven, zero-API),
  - `fs` → the Gemini **`toc_stage`** framework,
  - both load into **one** `schema_v7` database (`compiled_fs.db` → BigQuery).
  Do not add a third family or a per-bank branch — routing is content-decided.

---

## Task 1 — Extend coverage: run the financial-results docs we have NOT tested

Only 7 FS documents are proven so far (`verify_cells` 0-fail). Several remain
**unrun** — the shorter formats (trading updates, results/press releases,
media-release highlights) and the condensed statements not yet loaded, plus the
Pillar 3 corpus (3 years × 3 banks).

**Do:** run each remaining `pillar3` + `fs` document end-to-end
(`run_doc.py --pdf <file>`; `--all` sweeps the FS corpus). The family router picks
the right TOC framework automatically.

**How to treat failures — this is the important rule:** the pipeline is
**fail-loud by design**. A document with a layout it hasn't seen will **halt at a
gate** (e.g. a row wider than its columns, a section-hierarchy FK) rather than load
wrong numbers. When that happens:
- Fix it with a **general, deterministic rule** that would also work for a bank we
  have never seen — **never** a per-document or per-bank hack (the project's #1 rule).
- Record the drift (which doc, which gate, the general fix) so coverage is auditable.

**Goal:** every `pillar3` + `fs` document either pulls cleanly, or is queued with the
general fix it needs. "Pull any document" is reached by closing these gaps one
general rule at a time, not by loosening the gates.

## Task 2 — `fact_metric`: the canonical fact table (BigQuery)

Raw `v_cell_flat` returns **multiple values per concept** (sign variants, rounding
twins, %-cells, cross-table duplicates, a few mis-stamps) — charts, graph retrieval,
and the chat analyst cannot read it directly.

**Do:** build **`fact_metric`** in BigQuery — one canonical row per
`(institution, concept, period, span, segment, geo)` — applying the
dedup/sign/conflict logic already in `concept/query_db.py`, and emit the
**data-quality punch-list** (the conflicts to fix, never silently dropped).
This is the gate before any dashboard, graph layer, or ratio (ROE/NIM/CIR).

---

## Task 3 — Record the ENTIRE lineage (workflow process): use GCP-native tools

You want one place to see the whole story of a number: **which source page it came
from → how it was extracted → every transform → the table serving it**, plus **what
ran when**. GCP gives this in three layers. Use the native tool for each; do not
hand-roll lineage tracking.

| Lineage layer | What it captures | GCP-native tool | Notes |
|---|---|---|---|
| **Cell-level provenance** | value → source PDF page; row/col lineage; verified | **already in `schema_v7`** (`row_lineage`, `col_lineage`, `verify_cells`, `page_range`) | Surface these columns INTO BigQuery so they show up in the graph below. This is finer than any GCP tool provides — keep it. |
| **Table-level lineage** | table → table (e.g. `cell_fact` → `v_cell_flat` → `fact_metric`) | **BigQuery data lineage** (automatic, powered by **Dataplex**) | Turn it on; every BQ job is tracked and rendered as a graph in the console for free. |
| **Estate lineage + catalog + DQ** | GCS PDF → extracted → loaded → BQ, across the whole project | **Dataplex Universal Catalog** | The single catalog + cross-service lineage view. Register your data-quality checks here (formalize `verify_cells` / the `fact_metric` punch-list as Dataplex **Auto DQ** rules). |
| **Transformation workflow (SQL/ELT)** | the dependency graph that BUILDS `fact_metric` + ratios, versioned + tested | **Dataform** (BigQuery-native) | **Recommended home for the SQL workflow.** Define transforms as SQLX with `dependencies` + `assertions`; Dataform gives you the DAG, lineage, tests, and scheduling — all inside BigQuery. This is where you "record the workflow process" for the warehouse layer. |
| **Orchestration workflow (the pipeline steps)** | ingest → extract → load → BQ → `fact_metric`, and every run's history | **Cloud Workflows** (+ **Cloud Scheduler** trigger); **Vertex AI Pipelines** if you want ML-Metadata-style run lineage for extraction | Execution history is the process record. Composer (Airflow) only if the DAG gets complex. |
| **Audit / "what ran"** | every job, who ran it, cost | **Cloud Logging** + **Cloud Audit Logs** + **`INFORMATION_SCHEMA.JOBS`** | The immutable record for handover. |

**Put simply — where to record what:**
- **The number's origin** → keep your in-schema lineage (`row_lineage`/`verify_cells`), mirror it into BigQuery.
- **How tables derive from each other** → **BigQuery data lineage** (automatic) + **Dataplex** (the catalog).
- **The SQL that builds `fact_metric`/ratios** → **Dataform** (version-controlled, tested, lineage-tracked in BQ).
- **The end-to-end pipeline process + run history** → **Cloud Workflows** (orchestration) + **Cloud Logging** (record).

Together those give you the *entire* lineage — cell → table → transform → process —
each in the GCP tool built for it, all viewable from Dataplex + BigQuery.

---

## Guardrails (apply to every task)

- **No overfitting / humans out of the loop.** Every fix is a general rule, visible
  in the route manifest, recorded in `findociq/docs/specs/` — never a per-doc branch.
- **Fail loud, never silently wrong.** A gate that halts is doing its job; queue the
  drift and add the general fix.
- **One schema, one DB.** Everything → `schema_v7` → BigQuery. The legacy
  `schema_v5` / `discovery.db` Pillar 3 path is dead.
