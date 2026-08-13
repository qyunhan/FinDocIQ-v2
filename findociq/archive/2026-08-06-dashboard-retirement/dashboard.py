"""dashboard.py — browse the extracted data: documents, tables, canonical
metrics, and ingest logs.

Data source is chosen by env var FINDOCIQ_DB_SOURCE:
  * "sqlite" (default) — reads the local compiled_fs.db (self-contained; used by
    Streamlit Community Cloud, which can't reach BigQuery on the locked project).
  * "bq" — reads BigQuery dataset `<project>.<dataset>` via the ambient service
    account (used on Cloud Run, which needs no key). Set FINDOCIQ_BQ_PROJECT /
    FINDOCIQ_BQ_DATASET to override the defaults.

Run locally:
    streamlit run findociq/app/dashboard.py --server.port 8501 \
        --server.address 0.0.0.0 --server.headless true
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Fixed categorical color per institution (never re-cycled by a filter) --
# first 3 slots of the validated default palette (blue/orange/aqua), the
# only ones that clear the CVD floor pairwise for all 3-of-3 combinations.
_BANK_COLOR = {"DBS": "#2a78d6", "OCBC": "#eb6834", "UOB": "#1baf7a"}

REPO = Path(__file__).resolve().parents[2]
OUTPUTS = REPO / "findociq" / "outputs"
DB = REPO / "findociq" / "db" / "compiled_fs.db"
SOURCE = os.environ.get("FINDOCIQ_DB_SOURCE", "sqlite").lower()
PROJECT = os.environ.get("FINDOCIQ_BQ_PROJECT", "igc2026-team08-6311")
DATASET = os.environ.get("FINDOCIQ_BQ_DATASET", "findociq")

st.set_page_config(page_title="FinDocIQ — extracted data", layout="wide")

# --- backend: sqlite or bigquery, behind a uniform run(sql) / TBL(name) --------
if SOURCE == "bq":
    from google.cloud import bigquery

    @st.cache_resource
    def _backend():
        return bigquery.Client(project=PROJECT)

    def TBL(name: str) -> str:
        return f"`{PROJECT}.{DATASET}.{name}`"

    def _exec(sql: str) -> pd.DataFrame:
        return _backend().query(sql).to_dataframe()

    SRC_LABEL = f"BigQuery · {PROJECT}.{DATASET}"
else:
    import sqlite3

    @st.cache_resource
    def _backend():
        if not DB.exists():
            st.error(f"DB not found: {DB}")
            st.stop()
        return sqlite3.connect(str(DB), check_same_thread=False)

    def TBL(name: str) -> str:
        return name

    def _exec(sql: str) -> pd.DataFrame:
        return pd.read_sql_query(sql, _backend())

    SRC_LABEL = f"SQLite · {DB.name}"


@st.cache_data(ttl=600, show_spinner=False)
def run(sql: str) -> pd.DataFrame:
    return _exec(sql)


def run_opt(sql: str) -> pd.DataFrame:
    """run() for a table/view that may not exist — empty df instead of raising."""
    try:
        return run(sql)
    except Exception:
        return pd.DataFrame()


def _esc(v: str) -> str:
    return str(v).replace("'", "''")


# --- pretty Bank_Period_DocType labels (display-only; doc_id / source_file --
# stay the real keys everywhere else, so this never touches routing/loading) -
_BANK_OF = {"DBS Group Holdings Ltd": "DBS",
           "Oversea-Chinese Banking Corporation Ltd": "OCBC",
           "United Overseas Bank Ltd": "UOB"}

# Ordered (most-specific-first) substring -> DocType tag, matched against the
# doc_id case-insensitively. Covers every doc_type naming convention seen
# across DBS/OCBC/UOB's own filenames (DBS: trading_update / performance_
# summary; OCBC: results__press_release / condensed_financial_statements /
# media_release...highlights; UOB: performance-highlights / condensed-
# financial-statements), plus both pillar3 spellings ('pillar3',
# 'P3_other_regulatory_disclosures').
_DOC_TYPE_PATTERNS = [
    ("pillar3", "Pillar3"), ("p3_other_regulatory", "Pillar3"),
    ("trading_update", "TradingUpdate"),
    ("performance_summary", "PerformanceSummary"),
    ("performance-highlights", "PerformanceHighlights"),
    ("performance_highlights", "PerformanceHighlights"),
    ("results", "ResultsPressRelease"),
    ("media_release", "MediaHighlights"),
    ("condensed", "CondensedFS"),
]


def _doc_type_tag(doc_id: str, doc_family: str) -> str:
    low = doc_id.lower()
    for needle, tag in _DOC_TYPE_PATTERNS:
        if needle in low:
            return tag
    return "Pillar3" if doc_family == "pillar3" else "Other"


def _period_label(doc_period: str) -> str:
    """'2026-03-31' -> '1Q26'. Falls back to the raw value for the one known
    non-ISO period in the corpus ('2023-Q1') rather than crashing the page."""
    try:
        year, month, _ = str(doc_period).split("-")
        quarter = (int(month) - 1) // 3 + 1
        return f"{quarter}Q{year[2:]}"
    except (ValueError, AttributeError):
        return str(doc_period)


def pretty_label(doc_id: str, institution: str, doc_family: str, doc_period: str) -> str:
    bank = _BANK_OF.get(institution, institution)
    return f"{bank}_{_period_label(doc_period)}_{_doc_type_tag(doc_id, doc_family)}"


st.title("FinDocIQ — extracted data")
st.caption(f"Source: **{SRC_LABEL}**")

tab_docs, tab_tables, tab_metric, tab_logs, tab_status = st.tabs(
    ["📄 Documents", "📊 Tables", "🧮 fact_metric", "🧾 Logs", "🚦 Ingest Status"])

# ---------------------------------------------------------------- Documents
with tab_docs:
    docs = run(f"""SELECT doc_id, institution, doc_family AS family,
                          doc_period AS period, source_file
                   FROM {TBL('document')}""")
    for name, col in [("section", "sections"), ("table_t", "tables"),
                      ("v_cell_flat", "cells")]:
        cnt = run(f"SELECT doc_id, COUNT(*) AS n FROM {TBL(name)} GROUP BY doc_id")
        m = dict(zip(cnt["doc_id"], cnt["n"])) if not cnt.empty else {}
        docs[col] = docs["doc_id"].map(m).fillna(0).astype(int)
    if not docs.empty:
        docs.insert(0, "label", docs.apply(
            lambda r: pretty_label(r["doc_id"], r["institution"], r["family"], r["period"]),
            axis=1))
    docs = docs.sort_values(["institution", "period", "doc_id"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documents", len(docs))
    c2.metric("Tables", int(docs["tables"].sum()) if not docs.empty else 0)
    c3.metric("Cells", int(docs["cells"].sum()) if not docs.empty else 0)
    c4.metric("Institutions", docs["institution"].nunique() if not docs.empty else 0)
    st.caption("`label` is a display-only Bank_Period_DocType tag; `doc_id` / "
              "`source_file` stay the real keys (and the link to the original PDF).")
    st.dataframe(docs, use_container_width=True, hide_index=True)

# ------------------------------------------------------------------- Tables
with tab_tables:
    dd = run(f"""SELECT DISTINCT t.doc_id, d.institution, d.doc_family AS family,
                        d.doc_period AS period
                 FROM {TBL('table_t')} t JOIN {TBL('document')} d ON d.doc_id = t.doc_id
                 ORDER BY d.institution, t.doc_id""")
    if dd.empty:
        st.info("No tables yet.")
    else:
        labels = {r.doc_id: f"{pretty_label(r.doc_id, r.institution, r.family, r.period)}  ({r.doc_id})"
                 for r in dd.itertuples()}
        doc = st.selectbox("Document", dd["doc_id"], format_func=lambda d: labels[d],
                            key="tbl_doc")
        tbls = run(f"""SELECT table_id, table_title, table_type, page_range, unit,
                              period, period_span
                       FROM {TBL('table_t')} WHERE doc_id = '{_esc(doc)}'
                       ORDER BY table_id""")
        st.caption(f"{len(tbls)} table(s) in {doc}")
        st.dataframe(tbls, use_container_width=True, hide_index=True)
        if not tbls.empty:
            tid = st.selectbox("Inspect table", tbls["table_id"], key="tbl_tid")
            cells = run(f"""SELECT row_lvl1, row_lvl2, col_lvl1, col_lvl2,
                                   value_raw, value_num, unit, concept_key
                            FROM {TBL('v_cell_flat')}
                            WHERE doc_id = '{_esc(doc)}' AND table_id = '{_esc(tid)}'
                            ORDER BY line_no""")
            st.caption(f"{len(cells)} cell(s) in {tid}")
            st.dataframe(cells, use_container_width=True, hide_index=True)

def _fm_period_label(period, span: str) -> str:
    """('2025-03-31','1Q') -> '1Q25'; ('2025-06-30','FY') -> 'FY25';
    ('2025-03-31','as_at') / ('2025-03-31','') -> '1Q25' (derived from the
    calendar quarter of the period-end date, same convention analysts use for
    a balance-sheet as-at date) -- one consistent categorical x-axis label
    for the time-series chart below, regardless of the underlying duration
    qualifier."""
    ts = pd.Timestamp(period)
    yy = f"{ts.year % 100:02d}"
    span = "" if pd.isna(span) else str(span).strip()
    if span in ("1Q", "2Q", "3Q", "4Q", "1H", "2H"):
        return f"{span}{yy}"
    if span == "FY":
        return f"FY{yy}"
    if span == "9M":
        return f"9M{yy}"
    return f"{ts.quarter}Q{yy}"          # as_at / blank -> derived quarter


# --------------------------------------------------------------- fact_metric
with tab_metric:
    # v_fact_metric_serving = fact_metric filtered to legal_entity='CONSOLIDATED'
    # (the Group/canonical figure) -- see build_fact_metric.py. Reading the base
    # fact_metric table here would resurface the Bank/Company duplicate rows
    # (e.g. UOB total assets 485,263 Bank vs 572,061 Group) the serving view
    # exists to hide from the dashboard.
    fm = run_opt(f"SELECT * FROM {TBL('v_fact_metric_serving')}")
    if fm.empty:
        st.info("fact_metric is empty or not present in this source.")
    else:
        fm["period_label"] = fm.apply(
            lambda r: _fm_period_label(r["period"], r["period_span"]), axis=1)

        c1, c2, c3 = st.columns(3)
        insts = c1.multiselect("Institution", sorted(fm["institution"].dropna().unique()))
        concepts = c2.multiselect("Concept", sorted(fm["concept_key"].dropna().unique()))
        periods = c3.multiselect("Period", sorted(fm["period_label"].dropna().unique()))
        view = fm
        if insts:
            view = view[view["institution"].isin(insts)]
        if concepts:
            view = view[view["concept_key"].isin(concepts)]
        if periods:
            view = view[view["period_label"].isin(periods)]
        st.caption(f"{len(view)} / {len(fm)} rows")
        # resolved_by is QA provenance (how a value was picked among candidate
        # rows -- 'single' / 'twin_collapse' / 'prefer_table' / 'conflict' /
        # 'sign_normalized'), not something an analyst filters a metric by;
        # hidden from this table, still in the DB for anyone auditing conflicts.
        keep = ["institution", "concept_key", "concept_name", "period_label",
                "segment_key", "geo_key", "value_num", "unit", "source_doc_id"]
        st.dataframe(view[[c for c in keep if c in view.columns]],
                     use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📈 Time series")
        st.caption("Pick one concept to chart its value across every period "
                  "currently loaded, split by institution.")
        tcol1, tcol2, tcol3 = st.columns([2, 1, 1])
        concept_choice = tcol1.selectbox(
            "Concept", sorted(fm["concept_key"].dropna().unique()),
            format_func=lambda k: f"{k} — {fm.loc[fm['concept_key'] == k, 'concept_name'].iloc[0]}"
                if (fm["concept_key"] == k).any() else k,
            key="ts_concept")
        seg_choice = tcol2.selectbox(
            "Segment", sorted(fm.loc[fm["concept_key"] == concept_choice,
                                     "segment_key"].dropna().unique()) or ["SEG_TOTAL"],
            key="ts_segment")
        # Several concepts carry both a consolidated GLOBAL row and per-region
        # rows (SG/HK/...) under the same concept+segment -- without this filter
        # the chart/table below would silently mix totals with regional splits.
        geo_opts = sorted(fm.loc[(fm["concept_key"] == concept_choice)
                                 & (fm["segment_key"] == seg_choice), "geo_key"].dropna().unique())
        geo_default = geo_opts.index("GLOBAL") if "GLOBAL" in geo_opts else 0
        geo_choice = tcol3.selectbox("Geography", geo_opts or ["GLOBAL"],
                                     index=geo_default if geo_opts else 0, key="ts_geo")
        if st.button("Generate chart", type="primary"):
            series = fm[(fm["concept_key"] == concept_choice)
                       & (fm["segment_key"] == seg_choice)
                       & (fm["geo_key"] == geo_choice)].copy()
            if series.empty:
                st.warning("No rows for that concept/segment combination.")
            else:
                series = series.sort_values("period")
                series["bank"] = series["institution"].map(_BANK_OF).fillna(series["institution"])
                period_order = series.drop_duplicates("period_label")["period_label"].tolist()
                unit = series["unit"].dropna().iloc[0] if series["unit"].notna().any() else ""
                concept_name = fm.loc[fm["concept_key"] == concept_choice, "concept_name"].iloc[0]
                st.caption(f"{concept_choice} ({concept_name})  ·  segment={seg_choice}  "
                          f"·  geo={geo_choice}  ·  unit={unit or 'n/a'}")

                banks_present = [b for b in _BANK_COLOR if b in series["bank"].unique()]
                chart = (
                    alt.Chart(series)
                    .mark_line(point=True, strokeWidth=2)
                    .encode(
                        x=alt.X("period_label:N", sort=period_order, title=None),
                        y=alt.Y("value_num:Q", title=unit or "value"),
                        color=alt.Color("bank:N", sort=banks_present,
                                       scale=alt.Scale(domain=banks_present,
                                                       range=[_BANK_COLOR[b] for b in banks_present]),
                                       legend=alt.Legend(title=None) if len(banks_present) > 1 else None),
                        tooltip=["bank", "period_label", "value_num", "unit", "source_doc_id"],
                    )
                    .properties(height=380)
                    .interactive()
                )
                st.altair_chart(chart, use_container_width=True)

                st.caption("Same series, wide format — one row per bank, one column per period.")
                wide = series.pivot_table(index="bank", columns="period_label",
                                          values="value_num", aggfunc="first")
                wide = wide.reindex(columns=[p for p in period_order if p in wide.columns])
                wide = wide.reindex([b for b in _BANK_COLOR if b in wide.index] +
                                    [b for b in wide.index if b not in _BANK_COLOR])
                st.dataframe(wide, use_container_width=True)

# --------------------------------------------------------------------- Logs
with tab_logs:
    st.subheader("Ingest cost (per doc)")
    # cost_summary.json is written per BANK+PERIOD run directory, and two
    # doc_ids routinely share one (e.g. a bank's pillar3 + trading_update for
    # the same quarter) -- see docs/specs/2026-07-27-table-region-overlap-
    # attribution.md. The writer now stamps + merges a "by_document" per-call
    # breakdown so each doc's cost survives a sibling doc's later run in the
    # same directory. Older files predating that fix have no "by_document"
    # key; fall back to one row keyed by output_file (the old behaviour).
    rows = []
    for cs in sorted(OUTPUTS.rglob("logs/cost_summary.json")):
        try:
            d = json.loads(cs.read_text())
            by_doc = d.get("by_document")
            if by_doc:
                for doc, t in by_doc.items():
                    rows.append({"document": doc, "run_dir": cs.parent.parent.name,
                                 "model": d.get("model", ""), "calls": t.get("calls"),
                                 "input_tok": t.get("input_tokens"),
                                 "output_tok": t.get("output_tokens"),
                                 "est_cost_usd": t.get("est_cost_usd")})
            else:
                t = d.get("totals", {})
                rows.append({"document": d.get("output_file", cs.parent.parent.name),
                             "run_dir": cs.parent.parent.name,
                             "model": d.get("model", ""), "calls": t.get("calls"),
                             "input_tok": t.get("input_tokens"),
                             "output_tok": t.get("output_tokens"),
                             "est_cost_usd": t.get("est_cost_usd")})
        except Exception as e:  # noqa: BLE001
            rows.append({"document": str(cs), "model": f"ERR {e}"})
    if rows:
        cost = pd.DataFrame(rows)
        st.metric("Total logged Gemini cost", f"${cost['est_cost_usd'].dropna().sum():,.4f}")
        st.dataframe(cost.sort_values("document"), use_container_width=True, hide_index=True)
    else:
        st.info("No cost_summary.json logs bundled with this deploy.")

    log = run_opt(f"SELECT * FROM {TBL('concept_resolution_log')} LIMIT 2000")
    if not log.empty:
        st.subheader("concept_resolution_log")
        st.caption(f"{len(log)} row(s) (capped 2000)")
        st.dataframe(log, use_container_width=True, hide_index=True)

# ------------------------------------------------------------- Ingest Status
with tab_status:
    MANIFEST = REPO / "findociq" / "data" / "sources" / "manifest.csv"
    if not MANIFEST.exists():
        st.info(f"manifest.csv not bundled with this deploy: {MANIFEST}")
    elif SOURCE == "bq":
        st.info("Ingest Status compares against the local SQLite corpus; "
                 "not available against the BigQuery source.")
    else:
        import sys as _sys
        _sys.path.insert(0, str(REPO / "findociq" / "pipeline"))
        from ingest_manifest import _db_coverage  # noqa: E402

        # Live (bank, period, family) -> [doc_id, ...], recomputed from the DB
        # on every load — never trusts manifest.csv's own have(y/n)/file_notes
        # columns, which are only as fresh as the last CLI reconcile run.
        coverage = _db_coverage(DB)
        n_tables_by_doc = run(f"""SELECT doc_id, COUNT(*) AS n
                                   FROM {TBL('table_t')} GROUP BY doc_id""")
        docs_with_tables = set(n_tables_by_doc[n_tables_by_doc["n"] > 0]["doc_id"])

        mf = pd.read_csv(MANIFEST, dtype=str).fillna("")

        def _status(row) -> str:
            ids = coverage.get((row["bank"], row["period"], row["family"]))
            if not ids:
                return "not yet ingested"
            hits = [d in docs_with_tables for d in ids]
            if all(hits):
                return "ingested"
            if not any(hits):
                return "loaded, 0 tables (flagged)"
            return "partial — one doc has 0 tables (flagged)"

        mf["status"] = mf.apply(_status, axis=1)
        mf["doc_ids"] = mf.apply(
            lambda r: ",".join(coverage.get((r["bank"], r["period"], r["family"]), [])),
            axis=1)

        # --- stage/state per doc, from ingest_status (run_doc.py instruments
        # every STEP 0-7 boundary there) -- rolls up to "worst wins" when a
        # manifest row's (bank,period,family) maps to >1 doc_id: a failed
        # sibling should never be hidden behind an ok one.
        ist = run_opt(f"""SELECT doc_id, stage, state, error_class, error_message,
                                 attempt_count, last_attempt_at
                          FROM {TBL('ingest_status')} WHERE doc_id IS NOT NULL""")
        ist_by_doc = {r.doc_id: r for r in ist.itertuples()} if not ist.empty else {}

        def _stage_info(row) -> tuple[str, str, int]:
            ids = coverage.get((row["bank"], row["period"], row["family"]), [])
            statuses = [ist_by_doc[d] for d in ids if d in ist_by_doc]
            if not statuses:
                return ("", "", 0)
            failed = [s for s in statuses if s.state == "failed"]
            if failed:
                s = failed[0]
                return (f"{s.stage} — {s.error_class}", s.error_message or "", s.attempt_count)
            running = [s for s in statuses if s.state == "running"]
            if running:
                s = running[0]
                return (f"{s.stage} — running", "", s.attempt_count)
            s = statuses[0]
            return (f"{s.stage} — ok", "", s.attempt_count)

        mf[["stage", "last_error", "attempts"]] = mf.apply(
            lambda r: pd.Series(_stage_info(r)), axis=1)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Planned (manifest rows)", len(mf))
        c2.metric("Ingested", int((mf["status"] == "ingested").sum()))
        c3.metric("Loaded, 0 tables", int((mf["status"] == "loaded, 0 tables (flagged)").sum()))
        c4.metric("Not yet ingested", int((mf["status"] == "not yet ingested").sum()))
        c5.metric("Stuck/failed", int(mf["stage"].str.contains(
            "transient|structural", na=False, regex=True).sum()))

        f1, f2 = st.columns(2)
        bank_f = f1.multiselect("Bank", sorted(mf["bank"].unique()))
        status_f = f2.multiselect("Status", sorted(mf["status"].unique()))
        view = mf
        if bank_f:
            view = view[view["bank"].isin(bank_f)]
        if status_f:
            view = view[view["status"].isin(status_f)]
        keep = ["bank", "year", "quarter", "family", "doc_type", "status",
                "stage", "last_error", "attempts",
                "doc_ids", "availability", "source_url"]
        st.dataframe(
            view[[c for c in keep if c in view.columns]]
                .sort_values(["bank", "year", "quarter"]),
            use_container_width=True, hide_index=True)
