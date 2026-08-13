# Plan — FinDocIQ app (friendly website-style Streamlit, 4 views)

Date: 2026-07-30. Runs on the Cloud Workstation. Reuses the existing pipeline +
`app/dashboard.py` query logic; does NOT rebuild extraction.

## Goal

A clean, website-style front end (not a raw Streamlit look) for the FinDocIQ
pipeline, with four functions:
1. **Ingest** a document → live progress → download **Excel + CSV** (+ the original table view).
2. **Database** view — browse all stored data.
3. **Table Registry** view — per bank / per doc / per table, with the identity-tag attributes.
4. **Dashboard** view — compare identity tags across banks/periods.

## Design language (binding)

- **Primary colour: cobalt blue `#0047AB`** (sidebar, buttons, headers, accents). Neutral greys for surfaces; white content area.
- **Large type**: base ≥18px, section headers ~32–40px, big buttons. Generous spacing.
- **NO emojis anywhere.** Use text labels / simple SVG/unicode geometric marks only (·, ▸, ✓, ✕ as status ticks are OK; no 😀/📄/etc.).
- **Layout like the DocuClipper reference**: fixed left **sidebar nav** (brand at top, 4 nav items), main area with big page title + **card** blocks.
- Delivered via `.streamlit/config.toml` `[theme]` + injected custom CSS (`st.markdown(unsafe_allow_html=True)`).

## Architecture

- New entry `app/findociq_app.py`. **Reuse** `dashboard.py` data helpers (import them; refactor shared queries into `app/data.py` if cleaner) so the deployed dashboard keeps working.
- **Source switch** (`FINDOCIQ_DB_SOURCE=sqlite|bq`): live views (Ingest progress) read **SQLite `compiled_fs.db`** (the pipeline writes it live); analytics (Dashboard) can read **BigQuery**. SQLite is the default on the workstation.
- Nav via `st.sidebar` (radio or `streamlit-option-menu` if available; fall back to styled radio — do NOT add a new dependency without noting it).

## The four views

### 1. Ingest (v0 — build first)
- **Pick a doc**: list FS source PDFs (from `source_store.list_sources()` / the sources tree) grouped by bank.
- **Trigger**: a big cobalt "Ingest" button → runs `run_doc.py --pdf <key>` as a subprocess (non-blocking; capture the process).
- **Live progress**: poll `ingest_status` (SQLite) every ~2s for that doc's `source_file`; render the 8 stages `scan → toc → extract → load → concepts → verify → xlsx → sync_bq → done` as a vertical stepper: done ✓ / running (spinner) / failed ✕ (+ error_message). Reuse the dashboard's ingest_status query shape.
- **On done**: show the **original table view** (reuse `dashboard.py`'s table-inspect render), and **download buttons**: **Excel** (via `tag_workbook.py` for the tagging workbook, and/or `db_check_xlsx.py` for the raw per-doc workbook) and **CSV** (flatten the doc's `table_t`/`row_dim`/`cell_fact`).
- Excel purpose (label it): "for finance to view and map concepts".

### 2. Database view (v1)
- Browse stored data: pick a doc → its tables → a table → the `row_dim` + `cell_fact` grid (the original streamlit table view retained). Plus a raw-table browser (documents, table_t counts).

### 3. Table Registry view (v2)
- The concept-formation surface: **per bank / per doc / per table**, one row per line item with the identity attributes: `row_leaf_label`, `concept_key` (identity tag), `agg_role` (total/component/atomic), `group`, `unit`, `segment_key`, `geo_key`, `period`, `period_span`, `sums_to`. Filter by bank/family. This is the read-only DB reflection of the tag workbook; shows coverage (tagged vs blank).

### 4. Dashboard view (v3)
- **Compare identity tags**: pick a concept (or a few) → time series / bar across banks + periods from `fact_metric` (already chart-ready: institution, concept_key, period, value). Reuse the existing dashboard time-series + wide table. **Load `dataviz` skill before building the charts** (cobalt-anchored palette, big labels, no emoji).

## Phases

- **v0**: shell (theme + sidebar nav + cards) + **Ingest view** fully working (trigger, live progress, table view, Excel + CSV download). Other three views are styled stubs.
- **v1**: Database view.
- **v2**: Table Registry view.
- **v3**: Dashboard compare view (with `dataviz`).

## Reuse map (tap existing, don't rebuild)

| Need | Existing |
|---|---|
| live stage/state | `ingest_status` table (SQLite) + dashboard's query |
| trigger ingestion | `run_doc.py --pdf <key>` (subprocess) |
| Excel (tagging) | `tag_workbook.py` |
| Excel (raw per-doc) / table view | `db_check_xlsx.py`, `dashboard.py` table render |
| CSV | flatten `table_t`/`row_dim`/`cell_fact` |
| compare charts | `fact_metric` + dashboard time-series |
| source switch | `FINDOCIQ_DB_SOURCE` (already in dashboard.py) |

## Notes / risks
- Streamlit can be themed to look website-like but isn't pixel-perfect DocuClipper; if a truer web look is required later, revisit (FastAPI + templates). v0 stays Streamlit for speed + logic reuse.
- Live progress needs the app + pipeline in one env sharing SQLite → **workstation** (not Streamlit Cloud) for v0.
- `run_doc` needs the paddle env (STEP 0) — on the workstation this works; trigger must pass the right flags (`--no-ipv4-shim` etc. per the ingest handoff).
