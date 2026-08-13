"""Unit tests for extract_run helpers (no API, no DB).

Run: python3 findociq/pipeline/test_extract_run.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_run


def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return cond


ok = True

# _resolve_period: table's own parsed period wins; otherwise the manifest
# period_hint is normalized to ISO through the same deterministic parser; an
# unparseable hint is kept raw rather than dropped.
got = extract_run._resolve_period("2023-12-31", "31 Dec 2023")
ok &= check("table period wins over hint", got == "2023-12-31", got)

got = extract_run._resolve_period(None, "31 Dec 2023")
ok &= check("abbrev-month hint -> ISO", got == "2023-12-31", got)

got = extract_run._resolve_period(None, "As at 31 December 2024")
ok &= check("full-month hint -> ISO", got == "2024-12-31", got)

got = extract_run._resolve_period(None, "FY2024 interim")
ok &= check("unparseable hint kept raw", got == "FY2024 interim", got)

got = extract_run._resolve_period(None, None)
ok &= check("no period, no hint -> None", got is None, got)

# framing_for_route: framing follows the CLASS FAMILY (BORDERLESS*), not one
# exact class name; per-page route lists (spanning units) use the first page.
got = extract_run.framing_for_route("BORDERLESS_MAIN")
ok &= check("BORDERLESS_MAIN -> borderless", got == "borderless", got)

got = extract_run.framing_for_route("BORDERLESS")
ok &= check("BORDERLESS (family) -> borderless", got == "borderless", got)

got = extract_run.framing_for_route("BORDERED_SINGLE")
ok &= check("BORDERED_SINGLE -> ruled", got == "ruled", got)

got = extract_run.framing_for_route(["BORDERED_SINGLE", "BORDERED_SINGLE"])
ok &= check("per-page route list -> ruled", got == "ruled", got)

# --from-html mode: extract_unit/extract_doc read existing <page>.html
# artifacts — zero API calls (client=None, pdf never opened) — and a missing
# artifact FAILS LOUDLY (FileNotFoundError), never FLAGGED-and-skipped.
import tempfile

_HTML = """<table>
<thead>
<tr><th>In S$ million, as at 31 Dec 2025</th></tr>
<tr><th></th><th>Unweighted</th><th>Weighted</th></tr>
</thead>
<tbody>
<tr data-level="0"><td>1</td><td>Capital</td><td>100</td><td>90</td></tr>
<tr data-level="0"><td>2</td><td>Deposits</td><td>200</td><td>180</td></tr>
</tbody>
</table>"""

SEC = {"sec_no": "12.9", "title": "Net Stable Funding Ratio"}
UNIT = {"pages": [7], "route": "BORDERED_SINGLE", "template": "nsfr"}
MISSING = {"pages": [8], "route": "BORDERED_SINGLE", "template": "nsfr"}

tmpdir = tempfile.mkdtemp()
with open(os.path.join(tmpdir, "7.html"), "w") as f:
    f.write(_HTML)

try:
    tables, usages = extract_run.extract_unit(
        None, "docX", "/nonexistent.pdf", SEC, UNIT, tmpdir, from_html=True)
    got_tables, got_usages = tables, usages
except Exception as e:
    got_tables = got_usages = e
ok &= check("from-html: parses table from artifact",
            not isinstance(got_tables, Exception) and len(got_tables) == 1
            and len(got_tables[0].rows) == 2 and got_tables[0].period == "2025-12-31",
            got_tables)
ok &= check("from-html: usage marks zero-cost source",
            not isinstance(got_usages, Exception) and got_usages
            and got_usages[0].get("from_html") is True, got_usages)

try:
    extract_run.extract_unit(
        None, "docX", "/nonexistent.pdf", SEC, MISSING, tmpdir, from_html=True)
    got = "no exception"
except FileNotFoundError:
    got = "FileNotFoundError"
except Exception as e:
    got = e
ok &= check("from-html: missing artifact -> FileNotFoundError",
            got == "FileNotFoundError", got)

# silent-empty regression (ocbc_4q24 p96 was a 0-byte artifact silently loaded
# as a 1-table doc): an EMPTY artifact = missing content -> FileNotFoundError
# (loud, same as a missing file); non-empty HTML that parses to ZERO tables
# raises RuntimeError (live path: caller FLAGs the unit and skips its DB load
# -- a router-selected unit is table-bearing by construction, so 0 tables is
# always an extraction failure, never a legitimate result).
EMPTY_UNIT = {"pages": [9], "route": "BORDERED_SINGLE", "template": "nsfr"}
with open(os.path.join(tmpdir, "9.html"), "w") as f:
    f.write("")
try:
    extract_run.extract_unit(
        None, "docX", "/nonexistent.pdf", SEC, EMPTY_UNIT, tmpdir, from_html=True)
    got = "no exception"
except FileNotFoundError:
    got = "FileNotFoundError"
except Exception as e:
    got = e
ok &= check("from-html: EMPTY artifact -> FileNotFoundError", got == "FileNotFoundError", got)

NO_TABLE_UNIT = {"pages": [10], "route": "BORDERED_SINGLE", "template": "nsfr"}
with open(os.path.join(tmpdir, "10.html"), "w") as f:
    f.write("<p>This page contains no quantitative disclosure table.</p>")
try:
    extract_run.extract_unit(
        None, "docX", "/nonexistent.pdf", SEC, NO_TABLE_UNIT, tmpdir, from_html=True)
    got = "no exception"
except RuntimeError:
    got = "RuntimeError"
except Exception as e:
    got = e
ok &= check("zero tables parsed -> RuntimeError (unit FLAGGED, never silent)",
            got == "RuntimeError", got)

out_root = tempfile.mkdtemp()
os.makedirs(os.path.join(out_root, "docY"))
with open(os.path.join(out_root, "docY", "7.html"), "w") as f:
    f.write(_HTML)
doc_ok = {"doc_id": "docY", "pdf": "/nonexistent.pdf", "section": SEC, "units": [UNIT]}
try:
    results = extract_run.extract_doc(None, doc_ok, "nsfr", out_root, from_html=True)
    got = results
except Exception as e:
    got = e
ok &= check("extract_doc from-html: 1 unit result",
            not isinstance(got, Exception) and len(got) == 1, got)

doc_missing = {"doc_id": "docZ", "pdf": "/nonexistent.pdf", "section": SEC, "units": [MISSING]}
try:
    extract_run.extract_doc(None, doc_missing, "nsfr", out_root, from_html=True)
    got = "no exception"
except FileNotFoundError:
    got = "FileNotFoundError"
except Exception as e:
    got = e
ok &= check("extract_doc from-html: missing artifact propagates (not FLAGGED)",
            got == "FileNotFoundError", got)

# --- post-load verification gate (spec: findociq/docs/specs/
# 2026-07-06-post-load-verification-gate.md) --------------------------------
# verify_and_report delegates the pdfplumber-facing check to
# verify_cells.verify_doc; stub that boundary (same style as the from-html
# tests above stub the Gemini boundary) rather than fabricating real PDFs.
import io
import contextlib
import json as _json

_verify_out_dir = tempfile.mkdtemp()


def _fake_verify_doc_missing(manifest, con, doc_id):
    return {
        "doc_id": doc_id,
        "pdf": "/nonexistent.pdf",
        "tables": [
            {"table_id": "nsfr_2025-12-31", "period": "2025-12-31", "pages": [7],
             "rows_total": 2, "rows_line_tier": 1, "rows_page_tier": 0,
             "rows_failed": 1, "values_checked": 4,
             "values_missing": [
                 {"row_id": 2, "row_label": "Deposits", "line_no": "2",
                  "missing_value": 180.0, "value_raw": "180"},
             ]},
        ],
    }


def _fake_verify_doc_clean(manifest, con, doc_id):
    return {
        "doc_id": doc_id,
        "pdf": "/nonexistent.pdf",
        "tables": [
            {"table_id": "nsfr_2025-12-31", "period": "2025-12-31", "pages": [7],
             "rows_total": 2, "rows_line_tier": 2, "rows_page_tier": 0,
             "rows_failed": 0, "values_checked": 4, "values_missing": []},
        ],
    }


_orig_verify_doc = extract_run.verify_cells.verify_doc

extract_run.verify_cells.verify_doc = _fake_verify_doc_missing
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        report_a, flagged_a = extract_run.verify_and_report(
            {}, None, "docFlag", out_dir=_verify_out_dir)
finally:
    extract_run.verify_cells.verify_doc = _orig_verify_doc
stdout_a = buf.getvalue()

ok &= check("verify gate: table with missing values -> flagged True", flagged_a is True, flagged_a)
ok &= check("verify gate: stdout shows FLAGGED-verify with missing count",
            "FLAGGED-verify" in stdout_a and "docFlag::nsfr_2025-12-31" in stdout_a
            and "1 value(s) missing" in stdout_a, stdout_a)

_report_path_a = os.path.join(_verify_out_dir, "docFlag_verify.json")
ok &= check("verify gate: report JSON persisted under out_dir", os.path.exists(_report_path_a))
with open(_report_path_a) as f:
    _written_a = _json.load(f)
ok &= check("verify gate: persisted report matches verify_cells output",
            _written_a["doc_id"] == "docFlag"
            and len(_written_a["tables"][0]["values_missing"]) == 1, _written_a)

extract_run.verify_cells.verify_doc = _fake_verify_doc_clean
buf2 = io.StringIO()
try:
    with contextlib.redirect_stdout(buf2):
        report_b, flagged_b = extract_run.verify_and_report(
            {}, None, "docClean", out_dir=_verify_out_dir)
finally:
    extract_run.verify_cells.verify_doc = _orig_verify_doc
stdout_b = buf2.getvalue()

ok &= check("verify gate: clean load -> flagged False", flagged_b is False, flagged_b)
ok &= check("verify gate: stdout shows verified-clean summary line",
            "verified-clean" in stdout_b and "docClean::nsfr_2025-12-31" in stdout_b
            and "FLAGGED-verify" not in stdout_b, stdout_b)
ok &= check("verify gate: clean report also persisted",
            os.path.exists(os.path.join(_verify_out_dir, "docClean_verify.json")))

print("\nRESULT:", "ALL PASS ✓" if ok else "FAILURES ✗")
sys.exit(0 if ok else 1)
