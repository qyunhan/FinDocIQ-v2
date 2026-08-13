"""Chat-with-data: NL question -> validated spec -> chart + UOB slide download.
Run: .venv-reports/bin/streamlit run findociq/app/chat_report.py
"""
import os, sys, tempfile
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))          # findociq/tools/slides
FINDOCIQ = os.path.dirname(os.path.dirname(HERE))          # findociq
sys.path.insert(0, HERE)                                   # slide_kit (same dir)
sys.path.insert(0, os.path.join(FINDOCIQ, "app"))          # spec.py still lives in app/
import streamlit as st
import spec as S
import slide_kit as sk

# NOTE (2026-08-12): final.db is RETIRED — it was never git-tracked and no longer
# exists; the live app reads db/compiled_v2.db (app/findociq_app.py:41). This
# module has no importer and will not run until it is repointed at a real DB.
DB = os.path.join(FINDOCIQ, "db", "final.db")
st.set_page_config(page_title="findociq — chat with the data", layout="wide")

@st.cache_resource
def registry():
    if not os.path.exists(DB):
        st.error(f"DB not found: {DB}"); st.stop()
    return S.load_registry(DB)

def session_tempdir() -> Path:
    """One tempdir per Streamlit session (not per submitted question) — files
    are overwritten with fixed basenames per query, so nothing accumulates
    unbounded within a session."""
    if "chat_report_tmpdir" not in st.session_state:
        st.session_state["chat_report_tmpdir"] = tempfile.mkdtemp(prefix="chatreport_")
    return Path(st.session_state["chat_report_tmpdir"])

@st.cache_resource
def logo() -> str:
    p = Path(tempfile.gettempdir()) / "findociq_logo.png"
    if not p.exists():
        pdf = os.path.join(HERE, "..", "data", "sources", "pillar3", "UOB_4Q25_Pillar 3.pdf")
        if not sk.extract_uob_logo(pdf, p):
            sk.styled_logo_fallback(p)
    return str(p)

reg = registry()
st.title("findociq — chat with the data")
st.caption(f"{len(reg.concepts)} concepts · {len(reg.institutions)} institutions · "
           f"{reg.periods[0]} → {reg.periods[-1]} · every value PDF-verified")

q = st.chat_input("e.g. compare UOB vs DBS required stable funding through 2025")
if q:
    st.chat_message("user").write(q)
    with st.chat_message("assistant"):
        try:
            qs = S.nl_to_spec(q, reg, S.gemini_llm)
        except S.SpecError as e:
            st.warning(f"Couldn't turn that into a query: {e}"); st.stop()
        except Exception as e:
            st.error(f"The language model is temporarily unavailable ({e}). "
                     "Your data is untouched — try again in a minute.")
            st.stop()
        st.markdown("**Interpreted as:** `" + ", ".join(qs.concepts) + "` · "
                    + ", ".join(sk.shorten_institution(i) for i in qs.institutions)
                    + f" · {qs.period_start} → {qs.period_end} · {qs.column} · {qs.chart}")
        try:
            data, nrows = S.run_query(DB, qs)
        except S.SpecError as e:
            st.warning(str(e)); st.stop()

        td = session_tempdir()
        builder = sk.make_bar_chart if qs.chart == "bar" else sk.make_item_chart
        charts = []
        for ck in qs.concepts:
            if not data.get(ck):
                st.warning(f"No data for {ck} in that slice"); continue
            p = td / f"{ck}.png"
            fmt = "percent" if ck in reg.percent_concepts else "thousands"
            title = qs.title or sk.ITEM_TITLES.get(ck) or reg.concepts.get(ck, ck)
            builder(data[ck], ck, p, {}, title=title, value_fmt=fmt)
            charts.append(p)
            st.image(str(p))
        if not charts:
            st.warning("No data to chart for any requested concept in that slice."); st.stop()
        if qs.chart == "table":
            import pandas as pd
            rows = [(ck, sk.shorten_institution(inst), str(d), v)
                    for ck, by in data.items() for inst, pts in by.items() for d, v in pts]
            st.dataframe(pd.DataFrame(rows, columns=["concept", "bank", "period", "value"]))

        footer = f"DB: {DB} · rows: {nrows} · spec: {qs}"
        out = sk.assemble_slide(td, charts[:2], qs.title or "findociq — query report",
                                "Source: Pillar 3 disclosures · generated from chat query",
                                footer, Path(logo()), "chat_report")
        c1, c2 = st.columns(2)
        c1.download_button("Download PPTX", out["pptx"].read_bytes(), "chat_report.pptx")
        c2.download_button("Download PDF", out["pdf"].read_bytes(), "chat_report.pdf")
