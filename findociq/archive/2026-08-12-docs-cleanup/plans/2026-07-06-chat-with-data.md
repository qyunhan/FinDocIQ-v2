# Chat-with-data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NL question → validated JSON query spec (Gemini flash) → deterministic SQL on final.db → chart preview + downloadable UOB-branded PPTX/PDF slide, in a local Streamlit app.

**Architecture:** Extract reusable rendering from `findociq/tools/nsfr_slide.py` into `findociq/tools/slide_kit.py` (pure move, then minimal generalization). New `findociq/app/spec.py` holds the query-spec contract: registry loader, validator, SQL builder, Gemini call with one-retry loop — no Streamlit imports, fully testable with canned responses. `findociq/app/chat_report.py` is a thin Streamlit UI over spec.py + slide_kit.

**Tech Stack:** Python 3, sqlite3, matplotlib (Agg), python-pptx, pdfplumber-free (no PDF reads here), Streamlit, google-genai (Gemini flash), all inside `.venv-reports`.

**Spec:** `findociq/docs/specs/2026-07-06-chat-with-data-design.md` (binding).

## Global Constraints

- **NO git commits.** The repo owner batches commits manually; the working tree deliberately carries uncommitted session work. A task is done when its tests are green and files saved — never run `git add`/`git commit`.
- **LLM boundary:** the LLM produces ONLY the JSON spec. No LLM-written SQL, no LLM-touched numbers, anywhere.
- **No per-bank/per-doc conditionals** (CLAUDE.md). Registry is read live from the DB — new stamped concepts must become queryable with zero interface changes.
- Tests are plain scripts with the repo's `check(name, cond, got)` pattern (see `findociq/pipeline/test_verify_cells.py`) — no pytest. Run with `python3 <file>`; exit 0 on pass.
- Rendering code runs with `.venv-reports/bin/python3` (matplotlib/pptx live there). Pure-logic tests (spec.py validator) run with system `python3`.
- DB fixture tests build their own tiny sqlite in a temp dir; only read-only integration checks may touch `findociq/db/final.db`.
- Paths are repo-root-relative; run everything from `/Users/Qianyunhan/Desktop/FinancialParser`.

---

### Task 1: Extract `slide_kit.py` (pure move, CLI behavior unchanged)

**Files:**
- Create: `findociq/tools/slide_kit.py`
- Modify: `findociq/tools/nsfr_slide.py` (becomes a thin CLI importing slide_kit)
- Test: `findociq/tools/test_slide_kit.py`

**Interfaces:**
- Produces (moved verbatim from nsfr_slide.py, same signatures): `PALETTE`, `FALLBACK_HUES`, `INK_PRIMARY`, `INK_SECONDARY`, `INK_MUTED`, `GRID_COLOR`, `SURFACE`, `UOB_NAVY`, `UOB_RED`, `MARKER_SIZE`, `LINE_WIDTH`, `ITEM_TITLES`, `shorten_institution(name) -> str`, `color_for(short_name, seen) -> str`, `fmt_period(d) -> str`, `fetch_series(db_path, concept_keys) -> (dict, int)`, `make_nsfr_chart(nsfr_by_inst, out_path, color_seen)`, `make_item_chart(item_by_inst, item_key, out_path, color_seen)`, `extract_uob_logo(pdf_path, out_path) -> bool`, `styled_logo_fallback(out_path)`, `fit_chart_layout(aspect1, aspect2, avail_w_in, gap_in, max_h_in) -> float` (copy the exact current signature from nsfr_slide.py — verify before moving), `render_preview(...)`, `build_pptx(...)` (exact current signatures).

- [ ] **Step 1: Write the failing test** — `findociq/tools/test_slide_kit.py`:

```python
"""Smoke tests for slide_kit (no API, reads final.db read-only, renders to tmp)."""
import os, sys, tempfile
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return cond

ok = True
import slide_kit as sk

data, n = sk.fetch_series("findociq/db/final.db", ["nsfr_ratio", "asf_total", "rsf_total"])
ok &= check("fetch_series returns 54 rows", n == 54, n)
ok &= check("3 institutions in asf_total", len(data["asf_total"]) == 3, len(data.get("asf_total", {})))

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "c.png"
    seen = {}
    sk.make_item_chart(data["asf_total"], "asf_total", p, seen)
    ok &= check("item chart file non-empty", p.exists() and p.stat().st_size > 10_000, p.stat().st_size if p.exists() else 0)

print(); print("ALL PASS" if ok else "FAILURES ABOVE"); sys.exit(0 if ok else 1)
```

- [ ] **Step 2: Run it to verify it fails** — `.venv-reports/bin/python3 findociq/tools/test_slide_kit.py` → expect `ModuleNotFoundError: No module named 'slide_kit'`.

- [ ] **Step 3: Create `slide_kit.py`** by MOVING (cut, not copy) everything from `nsfr_slide.py` EXCEPT `main()` and the `if __name__` block: the module docstring gets a new one-liner ("Reusable NSFR slide rendering kit — palette, data access, charts, slide assembly."), keep all imports it needs (`matplotlib` with `Agg`, `sqlite3`, `re`, `sys`, `date`, `Path`, pptx imports stay inside `build_pptx` as today). Do not edit any function body.

- [ ] **Step 4: Rewrite `nsfr_slide.py`** as the thin CLI: keep its shebang/docstring/argparse `main()` exactly as-is, replace all moved definitions with `from slide_kit import *` is NOT acceptable — use an explicit import list of exactly the names `main()` uses (`fetch_series, make_nsfr_chart, make_item_chart, ITEM_TITLES, extract_uob_logo, styled_logo_fallback, render_preview, build_pptx, fit_chart_layout` — check `main()` for the actual list) via `from slide_kit import ...` after an `sys.path.insert(0, os.path.dirname(...))` guard matching the repo's test style.

- [ ] **Step 5: Run the test to verify it passes** — `.venv-reports/bin/python3 findociq/tools/test_slide_kit.py` → `ALL PASS`.

- [ ] **Step 6: Regression-run the CLI** — `.venv-reports/bin/python3 findociq/tools/nsfr_slide.py` → exit 0, stdout still reports `Rows queried: 54; institutions: 3`, and the three outputs in `findociq/reports/` regenerate with fresh mtimes.

### Task 2: Generalize slide_kit for app use

**Files:**
- Modify: `findociq/tools/slide_kit.py`
- Test: `findociq/tools/test_slide_kit.py` (extend)

**Interfaces:**
- Produces:
  - `fetch_series(db_path, concept_keys, col_key="weighted", institutions=None, period_start=None, period_end=None) -> (dict, int)` — same return shape (`concept -> institution -> [(date, value)]`); new filters are optional and additive; existing callers unchanged.
  - `make_item_chart(item_by_inst, item_key, out_path, color_seen, title=None, value_fmt="thousands")` — `title` overrides `ITEM_TITLES` lookup (fallback when key absent: the title arg is REQUIRED if `item_key not in ITEM_TITLES`, else `ValueError`); `value_fmt="percent"` switches y/end-label formatting to `f"{v:.0f}%"`.
  - `assemble_slide(out_dir: Path, charts: list[Path], title: str, subtitle: str, footer: str, logo_path: Path, basename: str) -> dict` — returns `{"pptx": Path, "png": Path, "pdf": Path}`; accepts 1 or 2 chart paths (2-chart path delegates to existing `render_preview`/`build_pptx` layout math; 1-chart path centers the single chart using the same `fit_chart_layout` with the second aspect = 0 — implement by treating a single chart as `chart_paths=[c]` and centering `min(width, avail)`).

- [ ] **Step 1: Write failing tests** (append to test_slide_kit.py):

```python
# --- Task 2: generalized fetch filters -------------------------------------
data, n = sk.fetch_series("findociq/db/final.db", ["asf_total"], col_key="weighted",
                          institutions=["DBS Group Holdings Ltd"],
                          period_start="2024-01-01", period_end="2025-12-31")
insts = list(data.get("asf_total", {}).keys())
ok &= check("institution filter -> 1 inst", len(insts) == 1, insts)
pts = data["asf_total"][insts[0]]
ok &= check("period filter -> 4 points (2024-09..2025-12)", len(pts) == 4, [str(p[0]) for p in pts])

# --- title override + percent fmt ------------------------------------------
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "r.png"
    sk.make_item_chart(data["asf_total"], "some_unknown_key", p, {}, title="Custom", value_fmt="percent")
    ok &= check("custom title renders unknown key", p.exists() and p.stat().st_size > 10_000)
    try:
        sk.make_item_chart(data["asf_total"], "some_unknown_key", Path(td) / "x.png", {})
        ok &= check("unknown key w/o title raises", False)
    except ValueError:
        ok &= check("unknown key w/o title raises", True)

# --- single-chart slide assembly --------------------------------------------
with tempfile.TemporaryDirectory() as td:
    logo = Path(td) / "logo.png"; sk.styled_logo_fallback(logo)
    c = Path(td) / "c.png"; sk.make_item_chart(data["asf_total"], "asf_total", c, {})
    out = sk.assemble_slide(Path(td), [c], "T", "sub", "foot", logo, "one")
    ok &= check("assemble_slide 1-chart emits pptx+png+pdf",
                all(out[k].exists() and out[k].stat().st_size > 5_000 for k in ("pptx", "png", "pdf")), out)
```

- [ ] **Step 2: Run to verify failure** — `.venv-reports/bin/python3 findociq/tools/test_slide_kit.py` → TypeError on `col_key=` kwarg.

- [ ] **Step 3: Implement.** `fetch_series`: add the three optional filter kwargs into the existing SQL (`AND v.institution IN (...)` when given, `AND v.period >= ? / <= ?`; keep `cd.col_key = ?` parameterized instead of the literal `'weighted'`). `make_item_chart`: `title = title or ITEM_TITLES.get(item_key)`; `if title is None: raise ValueError(...)`; thread `value_fmt` into the y-formatter and end-label f-strings (two branches: `f"{v:,.0f}"` vs `f"{v:.0f}%"`). `assemble_slide`: for `len(charts)==2` call the existing `render_preview`+`build_pptx` with the given title/subtitle/footer (parameterize the currently-hardcoded title/subtitle strings in both — add `title=`/`subtitle=` kwargs with the current strings as defaults so `nsfr_slide.py` needs no change); for `len(charts)==1` pass the same chart twice is NOT acceptable — add a single-chart branch in both functions that centers the one image (same `fit_chart_layout` call with `aspect2=0.0, gap_in=0.0`; guard `fit_chart_layout` for aspect2==0).

- [ ] **Step 4: Run tests** — all Task 1 + Task 2 checks `ALL PASS`; rerun the `nsfr_slide.py` CLI once more (regression: exit 0, same stdout counts).

### Task 3: `app/spec.py` — registry, QuerySpec, validator

**Files:**
- Create: `findociq/app/__init__.py` (empty), `findociq/app/spec.py`
- Test: `findociq/app/test_spec.py`

**Interfaces:**
- Produces:
  - `@dataclass Registry: concepts: dict[str, str]` (key → representative label), `institutions: list[str]` (full names), `institution_aliases: dict[str, str]` (short → full, built with `slide_kit.shorten_institution`), `periods: list[str]` (ISO, sorted), `col_keys: list[str]`.
  - `load_registry(db_path: str) -> Registry` — live SQL: concepts from `SELECT concept_key, MIN(row_leaf_label) FROM v_cell WHERE concept_key IS NOT NULL GROUP BY concept_key`; institutions from `document`; periods from `SELECT DISTINCT period FROM table_t ORDER BY 1`; col_keys from `SELECT DISTINCT col_key FROM col_dim WHERE col_key IS NOT NULL AND col_key != ''`.
  - `@dataclass QuerySpec: concepts: list[str]; institutions: list[str]; period_start: str; period_end: str; column: str = "weighted"; chart: str = "line"; title: str | None = None`
  - `validate_spec(raw: dict, reg: Registry) -> QuerySpec` — raises `SpecError(msg)` (a ValueError subclass) with a **conversational** message: unknown concept → includes up to 3 `difflib.get_close_matches` from `reg.concepts` (against both keys and labels); unknown institution → resolves via `institution_aliases` case-insensitively first, then close-matches; >4 concepts → "narrow to at most 4"; empty concepts/institutions → defaults to ALL institutions but NEVER defaults concepts (reject); bad chart/column → list valid values; period clamping (not rejection): clamp start/end to `[min(periods), max(periods)]`, swap if reversed.

- [ ] **Step 1: Write failing tests** — `findociq/app/test_spec.py`, repo check-style; build a `Registry` literal in-test (no DB):

```python
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return cond

ok = True
import spec as S

REG = S.Registry(
    concepts={"asf_total": "Total ASF", "rsf_total": "Total RSF", "nsfr_ratio": "Net Stable Funding Ratio"},
    institutions=["DBS Group Holdings Ltd", "United Overseas Bank Limited"],
    institution_aliases={"DBS": "DBS Group Holdings Ltd", "UOB": "United Overseas Bank Limited"},
    periods=["2023-09-30", "2023-12-31", "2024-09-30"],
    col_keys=["weighted", "unw_ge_1y"],
)

good = {"concepts": ["asf_total"], "institutions": ["DBS"], "period_start": "2023-09-30",
        "period_end": "2024-09-30", "column": "weighted", "chart": "line", "title": None}
qs = S.validate_spec(good, REG)
ok &= check("alias resolves to full name", qs.institutions == ["DBS Group Holdings Ltd"], qs.institutions)

try:
    S.validate_spec({**good, "concepts": ["asf_totall"]}, REG); ok &= check("typo concept rejected", False)
except S.SpecError as e:
    ok &= check("typo concept rejected w/ suggestion", "asf_total" in str(e), str(e))

qs = S.validate_spec({**good, "institutions": []}, REG)
ok &= check("empty institutions -> all", len(qs.institutions) == 2, qs.institutions)

try:
    S.validate_spec({**good, "concepts": []}, REG); ok &= check("empty concepts rejected", False)
except S.SpecError:
    ok &= check("empty concepts rejected", True)

qs = S.validate_spec({**good, "period_start": "1990-01-01", "period_end": "2099-01-01"}, REG)
ok &= check("periods clamped", (qs.period_start, qs.period_end) == ("2023-09-30", "2024-09-30"),
            (qs.period_start, qs.period_end))

try:
    S.validate_spec({**good, "chart": "pie"}, REG); ok &= check("bad chart rejected", False)
except S.SpecError as e:
    ok &= check("bad chart rejected lists valid", "line" in str(e), str(e))

print(); print("ALL PASS" if ok else "FAILURES ABOVE"); sys.exit(0 if ok else 1)
```

- [ ] **Step 2: Run to verify failure** — `python3 findociq/app/test_spec.py` → import error.
- [ ] **Step 3: Implement `spec.py`** (dataclasses + validator per the Produces block; `sys.path` bootstrap to import `slide_kit` from `../tools` for `shorten_institution`).
- [ ] **Step 4: Run tests** → `ALL PASS`.
- [ ] **Step 5: Integration check of `load_registry`** — one-liner: `python3 -c "import sys; sys.path.insert(0,'findociq/app'); import spec; r = spec.load_registry('findociq/db/final.db'); print(len(r.concepts), len(r.institutions), len(r.periods), r.col_keys)"` → expect ~30+ concepts, 3 institutions, 6 periods, 5 col_keys.

### Task 4: `spec.py` — SQL builder + data fetch

**Files:**
- Modify: `findociq/app/spec.py`
- Test: `findociq/app/test_spec.py` (extend)

**Interfaces:**
- Produces: `run_query(db_path: str, qs: QuerySpec) -> tuple[dict, int]` — delegates to `slide_kit.fetch_series(db_path, qs.concepts, col_key=qs.column, institutions=qs.institutions, period_start=qs.period_start, period_end=qs.period_end)`; raises `SpecError("no data for that slice — …")` when row count is 0 (message names the concepts/institutions/periods asked for).

- [ ] **Step 1: Write failing tests** — extend test_spec.py with a FIXTURE sqlite built in a tempdir: create tables `document(doc_id, institution)`, `table_t(doc_id, table_id, table_type, period, page_range)`, `row_dim(doc_id, table_id, row_id, row_leaf_label, row_hierarchy, line_no, unit)`, `col_dim(doc_id, table_id, col_id, col_hierarchy, col_parent, col_leaf_label, col_period, geo_key, unit, col_key)`, `cell_fact(doc_id, table_id, row_id, col_id, colspan, concept_key, geo_key, value_raw, value_num, cell_state, is_shade, period)` and the `v_cell` view (copy the CREATE VIEW from `sqlite3 findociq/db/final.db ".schema v_cell"` verbatim). Insert 2 docs (2 institutions), 1 table each, one row with `concept_key='asf_total'`, one weighted col, values 100 and 200. Then:

```python
qs = S.QuerySpec(concepts=["asf_total"], institutions=[INST_A], period_start="2023-01-01",
                 period_end="2026-01-01", column="weighted", chart="line", title=None)
data, n = S.run_query(fixture_db_path, qs)
ok &= check("run_query returns only filtered inst", list(data["asf_total"].keys()) == [INST_A], data)
try:
    S.run_query(fixture_db_path, S.QuerySpec(concepts=["asf_total"], institutions=[INST_A],
                period_start="1980-01-01", period_end="1981-01-01", column="weighted", chart="line", title=None))
    ok &= check("empty slice raises SpecError", False)
except S.SpecError as e:
    ok &= check("empty slice raises SpecError", "no data" in str(e).lower(), str(e))
```

- [ ] **Step 2: Run to verify failure** → AttributeError (`run_query` missing).
- [ ] **Step 3: Implement `run_query`** per Produces.
- [ ] **Step 4: Run** — `python3 findociq/app/test_spec.py` → `ALL PASS`. (This test file must not import matplotlib at module load — import `slide_kit` lazily inside `run_query`, or the fixture test runs under `.venv-reports/bin/python3` instead; pick lazy import so system python3 keeps working for the validator tests.)

### Task 5: `spec.py` — Gemini NL layer (canned-response tested)

**Files:**
- Modify: `findociq/app/spec.py`
- Test: `findociq/app/test_spec.py` (extend)

**Interfaces:**
- Produces:
  - `build_system_prompt(reg: Registry) -> str` — fixed instruction text + the registry serialized (concept key → label list; institutions with aliases; periods; col_keys; chart enum). MUST state: "Return ONLY a JSON object with keys concepts, institutions, period_start, period_end, column, chart, title. Use concept KEYS, not labels."
  - `parse_llm_json(text: str) -> dict` — strips ``` fences and leading/trailing prose, `json.loads` the first `{...}` block; raises `SpecError` on no-JSON.
  - `nl_to_spec(question: str, reg: Registry, llm: Callable[[str, str], str]) -> QuerySpec` — `llm(system_prompt, user_text)` is an injected transport; flow: call → parse → validate; on ANY SpecError, ONE retry with the error message appended to the user text ("Your previous answer was rejected: <err>. Return corrected JSON only."); second failure re-raises.
  - `gemini_llm(system_prompt: str, user_text: str) -> str` — the real transport: `google-genai` client, model `gemini-3.5-flash`, temperature 0, key from `findociq/.env` (load with the same dotenv pattern used elsewhere in the repo — check `findociq/pipeline/extract_run.py` for the existing env-loading helper and reuse it). NOT exercised by tests.

- [ ] **Step 1: Write failing tests** — canned transports:

```python
def llm_good(sys_p, user_p):
    return '```json\n{"concepts": ["asf_total"], "institutions": ["DBS"], "period_start": "2023-09-30", "period_end": "2024-09-30", "column": "weighted", "chart": "line", "title": null}\n```'

def make_flaky():
    calls = {"n": 0}
    def llm(sys_p, user_p):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"concepts": ["asf_totall"], "institutions": ["DBS"], "period_start": "2023-09-30", "period_end": "2024-09-30", "column": "weighted", "chart": "line", "title": null}'
        assert "rejected" in user_p.lower() or "asf_total" in user_p
        return llm_good(sys_p, user_p)
    return llm, calls

qs = S.nl_to_spec("total ASF for DBS", REG, llm_good)
ok &= check("good response -> spec", qs.concepts == ["asf_total"], qs)

llm, calls = make_flaky()
qs = S.nl_to_spec("total ASF for DBS", REG, llm)
ok &= check("retry loop recovers on 2nd try", qs.concepts == ["asf_total"] and calls["n"] == 2, calls)

def llm_bad(sys_p, user_p):
    return "I cannot answer that."
try:
    S.nl_to_spec("weather?", REG, llm_bad); ok &= check("no-JSON twice raises", False)
except S.SpecError:
    ok &= check("no-JSON twice raises", True)

sp = S.build_system_prompt(REG)
ok &= check("system prompt carries registry", "asf_total" in sp and "DBS" in sp and "2023-09-30" in sp)
```

- [ ] **Step 2: Run to verify failure** → AttributeError.
- [ ] **Step 3: Implement** per Produces (`gemini_llm` imports google-genai lazily; everything else pure).
- [ ] **Step 4: Run** → `ALL PASS` (still zero live API calls).

### Task 6: Streamlit UI + deps + eval list

**Files:**
- Create: `findociq/app/chat_report.py`, `findociq/app/eval_questions.md`
- Modify: none

**Interfaces:**
- Consumes: `spec.load_registry / nl_to_spec / gemini_llm / run_query / SpecError`; `slide_kit.make_item_chart / assemble_slide / extract_uob_logo / styled_logo_fallback / shorten_institution / ITEM_TITLES`.

- [ ] **Step 1: Install deps** — `.venv-reports/bin/pip install streamlit google-genai` (report versions).
- [ ] **Step 2: Write `chat_report.py`**:

```python
"""Chat-with-data: NL question -> validated spec -> chart + UOB slide download.
Run: .venv-reports/bin/streamlit run findociq/app/chat_report.py
"""
import os, sys, tempfile
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import streamlit as st
import spec as S
import slide_kit as sk

DB = os.path.join(HERE, "..", "db", "final.db")
st.set_page_config(page_title="findociq — chat with the data", layout="wide")

@st.cache_resource
def registry():
    if not os.path.exists(DB):
        st.error(f"DB not found: {DB}"); st.stop()
    return S.load_registry(DB)

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
        st.markdown("**Interpreted as:** `" + ", ".join(qs.concepts) + "` · "
                    + ", ".join(sk.shorten_institution(i) for i in qs.institutions)
                    + f" · {qs.period_start} → {qs.period_end} · {qs.column} · {qs.chart}")
        try:
            data, nrows = S.run_query(DB, qs)
        except S.SpecError as e:
            st.warning(str(e)); st.stop()

        td = Path(tempfile.mkdtemp(prefix="chatreport_"))
        charts = []
        for ck in qs.concepts:
            p = td / f"{ck}.png"
            fmt = "percent" if ck == "nsfr_ratio" else "thousands"
            title = qs.title or sk.ITEM_TITLES.get(ck) or reg.concepts.get(ck, ck)
            sk.make_item_chart(data.get(ck, {}), ck, p, {}, title=title, value_fmt=fmt)
            charts.append(p)
            st.image(str(p))
        if qs.chart == "table":
            import pandas as pd
            rows = [(ck, sk.shorten_institution(inst), str(d), v)
                    for ck, by in data.items() for inst, pts in by.items() for d, v in pts]
            st.dataframe(pd.DataFrame(rows, columns=["concept", "bank", "period", "value"]))

        footer = f"DB: {DB} · rows: {nrows} · spec: {qs}"
        out = sk.assemble_slide(td, charts[:2], qs.title or "findociq — query report",
                                f"Source: Pillar 3 disclosures · generated from chat query",
                                footer, Path(logo()), "chat_report")
        c1, c2 = st.columns(2)
        c1.download_button("Download PPTX", out["pptx"].read_bytes(), "chat_report.pptx")
        c2.download_button("Download PDF", out["pdf"].read_bytes(), "chat_report.pdf")
```

(Adjust to the exact `assemble_slide`/`make_item_chart` signatures from Task 2 if they differ — Task 2's Produces block is authoritative.)

- [ ] **Step 3: Import smoke test** — `.venv-reports/bin/python3 -c "import ast; ast.parse(open('findociq/app/chat_report.py').read())"` then `timeout 20 .venv-reports/bin/streamlit run findociq/app/chat_report.py --server.headless true & sleep 8; curl -s localhost:8501 | head -c 200; kill %1` → HTML served.
- [ ] **Step 4: Write `eval_questions.md`** — 10 phrasings with expected spec sketches (e.g. "compare UOB vs DBS required stable funding through 2025" → rsf_total, [UOB, DBS], …→2025-12-31, weighted, line; include one percent case, one unweighted case, one typo case, one out-of-scope case that must be rejected).
- [ ] **Step 5: DO NOT run the live eval** — that is a user-facing acceptance step (costs API calls); report the app is up and hand the eval list to the orchestrator.

---

## Self-review notes (done at authoring)

- Spec coverage: contract ✔ (T3/T5), components ✔ (T1-T6), error handling ✔ (T3 conversational SpecError, T4 empty-slice, T6 banners/fail-loud DB), testing ✔ (canned transports, fixture DB, no live API), run/deps ✔ (T6).
- Signatures cross-checked T2 ↔ T4 ↔ T6 (`fetch_series` kwargs, `assemble_slide` dict return, `make_item_chart(title=, value_fmt=)`).
- Deliberate deviations from skill defaults: no git-commit steps (Global Constraints, harness rule + observed repo convention); plan lives in `findociq/docs/plans/` (project layout).
