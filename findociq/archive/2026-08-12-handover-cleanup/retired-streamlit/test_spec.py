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

try:
    S.validate_spec({**good, "concepts": ["asf_total", "rsf_total", "nsfr_ratio",
                                           "asf_total", "rsf_total"]}, REG)
    ok &= check(">4 concepts rejected", False)
except S.SpecError as e:
    ok &= check(">4 concepts rejected mentions narrowing", "narrow" in str(e).lower(), str(e))

qs = S.validate_spec({**good, "column": "unw_ge_1y"}, REG)
ok &= check("column from reg.col_keys accepted", qs.column == "unw_ge_1y", qs.column)

try:
    S.validate_spec({**good, "column": "unw_6m_1y"}, REG); ok &= check("bad column rejected", False)
except S.SpecError as e:
    ok &= check("bad column rejected lists reg.col_keys",
                "weighted" in str(e) and "unw_ge_1y" in str(e), str(e))

qs = S.validate_spec({**good, "chart": "table"}, REG)
ok &= check("chart 'table' accepted", qs.chart == "table", qs.chart)

# --- run_query, against a fixture sqlite DB ---
import sqlite3
import tempfile

INST_A = "Fixture Bank A"
INST_B = "Fixture Bank B"

_tmpdir = tempfile.TemporaryDirectory()
fixture_db_path = os.path.join(_tmpdir.name, "fixture.db")
_con = sqlite3.connect(fixture_db_path)
_con.executescript("""
CREATE TABLE document(doc_id TEXT PRIMARY KEY, institution TEXT);
CREATE TABLE table_t(doc_id TEXT, table_id TEXT, table_type TEXT, period TEXT, page_range TEXT);
CREATE TABLE row_dim(doc_id TEXT, table_id TEXT, row_id TEXT, row_leaf_label TEXT,
                     row_hierarchy TEXT, line_no INTEGER, unit TEXT);
CREATE TABLE col_dim(doc_id TEXT, table_id TEXT, col_id TEXT, col_hierarchy TEXT,
                     col_parent TEXT, col_leaf_label TEXT, col_period TEXT, geo_key TEXT,
                     unit TEXT, col_key TEXT);
CREATE TABLE cell_fact(doc_id TEXT, table_id TEXT, row_id TEXT, col_id TEXT, colspan INTEGER,
                       concept_key TEXT, geo_key TEXT, value_raw TEXT, value_num REAL,
                       cell_state TEXT, is_shade INTEGER, period TEXT);
CREATE TABLE template_row(template_id TEXT, row_id TEXT, concept_key TEXT, canonical_label TEXT);

CREATE VIEW v_cell AS
SELECT f.doc_id, f.table_id, t.table_type, f.period, d.institution,
       f.row_id, r.row_leaf_label, r.row_hierarchy, r.line_no, r.unit AS row_unit,
       f.col_id, f.colspan, c.col_leaf_label, c.col_period, c.unit AS col_unit,
       f.concept_key, f.geo_key,
       f.value_raw, f.value_num, f.cell_state, f.is_shade
FROM cell_fact f
JOIN row_dim   r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id
JOIN col_dim   c ON c.doc_id=f.doc_id AND c.table_id=f.table_id AND c.col_id=f.col_id
JOIN table_t   t ON t.doc_id=f.doc_id AND t.table_id=f.table_id
JOIN document  d ON d.doc_id=f.doc_id
/* v_cell(doc_id,table_id,table_type,period,institution,row_id,row_leaf_label,row_hierarchy,line_no,row_unit,col_id,colspan,col_leaf_label,col_period,col_unit,concept_key,geo_key,value_raw,value_num,cell_state,is_shade) */;

INSERT INTO document VALUES ('doc_a', 'Fixture Bank A');
INSERT INTO document VALUES ('doc_b', 'Fixture Bank B');

INSERT INTO table_t VALUES ('doc_a', 't1', 'nsfr', '2024-06-30', '1-1');
INSERT INTO table_t VALUES ('doc_b', 't1', 'nsfr', '2024-06-30', '1-1');

INSERT INTO row_dim VALUES ('doc_a', 't1', 'r1', 'Total ASF', 'Total ASF', 1, 'SGD');
INSERT INTO row_dim VALUES ('doc_b', 't1', 'r1', 'Total ASF', 'Total ASF', 1, 'SGD');

INSERT INTO col_dim VALUES ('doc_a', 't1', 'c1', 'Weighted', NULL, 'Weighted', '2024-06-30', NULL, 'SGD', 'weighted');
INSERT INTO col_dim VALUES ('doc_b', 't1', 'c1', 'Weighted', NULL, 'Weighted', '2024-06-30', NULL, 'SGD', 'weighted');

INSERT INTO cell_fact VALUES ('doc_a', 't1', 'r1', 'c1', 1, 'asf_total', NULL, '100', 100.0, 'ok', 0, '2024-06-30');
INSERT INTO cell_fact VALUES ('doc_b', 't1', 'r1', 'c1', 1, 'asf_total', NULL, '200', 200.0, 'ok', 0, '2024-06-30');

INSERT INTO template_row VALUES ('tmpl1', 'r1', 'asf_total', 'Total ASF');
INSERT INTO template_row VALUES ('tmpl1', 'r2', 'nsfr_ratio', 'Net Stable Funding Ratio (%)');
""")
_con.commit()
_con.close()

fixture_reg = S.load_registry(fixture_db_path)
ok &= check("percent_concepts populated from fixture template_row",
            fixture_reg.percent_concepts == {"nsfr_ratio"}, fixture_reg.percent_concepts)

live_db_path = os.path.join(HERE, "..", "db", "final.db")
if os.path.exists(live_db_path):
    live_reg = S.load_registry(live_db_path)
    ok &= check("live percent_concepts includes nsfr_ratio (at minimum)",
                "nsfr_ratio" in live_reg.percent_concepts, live_reg.percent_concepts)
else:
    check("live percent_concepts check skipped (final.db not found)", True)

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

# --- NL layer: build_system_prompt, parse_llm_json, nl_to_spec ---

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

# --- parse_llm_json: robust extraction against realistic LLM chatter ---

leading_prose = 'Sure thing (see {note above}) here you go: {"concepts": ["asf_total"]}'
d = S.parse_llm_json(leading_prose)
ok &= check("leading prose w/ stray brace parses", d == {"concepts": ["asf_total"]}, d)

trailing_prose = '{"concepts": ["asf_total"]} — hope that helps {not json}'
d = S.parse_llm_json(trailing_prose)
ok &= check("trailing prose w/ stray brace parses", d == {"concepts": ["asf_total"]}, d)

double_object = '{"concepts": ["asf_total"]} also consider {"concepts": ["rsf_total"]}'
d = S.parse_llm_json(double_object)
ok &= check("second JSON-ish aside -> first object wins", d == {"concepts": ["asf_total"]}, d)

upper_fence = '```JSON\n{"concepts": ["asf_total"]}\n```'
d = S.parse_llm_json(upper_fence)
ok &= check("uppercase JSON fence parses", d == {"concepts": ["asf_total"]}, d)

try:
    S.parse_llm_json("I cannot answer that. {not json at all"); ok &= check("pure prose no valid object raises", False)
except S.SpecError:
    ok &= check("pure prose no valid object raises", True)

# --- _with_backoff: pure retry/backoff helper, no real sleeping ------------

def make_recording_sleeper():
    delays = []
    def sleeper(d):
        delays.append(d)
    return sleeper, delays

# succeeds on the 3rd attempt -> 2 sleeps, delays [2, 4]
calls = {"n": 0}
def flaky_then_ok():
    calls["n"] += 1
    if calls["n"] < 3:
        raise RuntimeError("transient")
    return "ok"

sleeper, delays = make_recording_sleeper()
result = S._with_backoff(flaky_then_ok, attempts=5, base_delay=2, sleeper=sleeper)
ok &= check("_with_backoff succeeds after N failures", result == "ok", result)
ok &= check("_with_backoff call count == 3", calls["n"] == 3, calls["n"])
ok &= check("_with_backoff delays are 2,4 (exponential)", delays == [2, 4], delays)

# never succeeds -> exhausts all attempts, re-raises last exception, no extra sleep
calls2 = {"n": 0}
def always_fails():
    calls2["n"] += 1
    raise RuntimeError(f"fail {calls2['n']}")

sleeper2, delays2 = make_recording_sleeper()
try:
    S._with_backoff(always_fails, attempts=5, base_delay=2, sleeper=sleeper2)
    ok &= check("_with_backoff re-raises after exhausting attempts", False)
except RuntimeError as e:
    ok &= check("_with_backoff re-raises after exhausting attempts", str(e) == "fail 5", str(e))
ok &= check("_with_backoff call count == attempts", calls2["n"] == 5, calls2["n"])
ok &= check("_with_backoff delays capped at 4 sleeps for 5 attempts", delays2 == [2, 4, 8, 16], delays2)

# non-retryable exception -> no retry, no sleep, raised on first attempt
calls3 = {"n": 0}
def fails_once():
    calls3["n"] += 1
    raise ValueError("client error, not retryable")

sleeper3, delays3 = make_recording_sleeper()
try:
    S._with_backoff(fails_once, attempts=5, base_delay=2, sleeper=sleeper3,
                     is_retryable=lambda e: False)
    ok &= check("_with_backoff does not retry non-retryable exceptions", False)
except ValueError:
    ok &= check("_with_backoff does not retry non-retryable exceptions",
                calls3["n"] == 1 and delays3 == [], (calls3["n"], delays3))

print(); print("ALL PASS" if ok else "FAILURES ABOVE"); sys.exit(0 if ok else 1)
