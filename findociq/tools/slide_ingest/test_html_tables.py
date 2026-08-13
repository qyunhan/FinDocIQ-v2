"""Tests for the slide HTML parser. Pure — no API, no PDF, no DB.

Run: python3 findociq/tools/slide_ingest/test_html_tables.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from html_tables import parse_elements  # noqa: E402

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    if cond:
        print(f"  [PASS] {name}")
    else:
        _FAILS += 1
        print(f"  [FAIL] {name}  -- {detail}")


TEXT_TABLE = """
<table border="1" data-element="text_table" data-title="Key figures (S$m)">
<thead><tr><th></th><th>2Q26</th><th>2Q25</th></tr></thead>
<tbody>
  <tr><td>Net interest income</td><td>3,483</td><td>3,625</td></tr>
  <tr><td>Total income</td><td>6,093</td><td>5,732</td></tr>
</tbody></table>
"""

WATERFALL = """
<table border="1" data-element="waterfall" data-title="PBT bridge">
<thead><tr><th>Bar</th><th>Value</th></tr></thead>
<tbody>
  <tr data-kind="total"><td>1H25 PBT</td><td>5,200</td></tr>
  <tr data-kind="bridge" data-sign="+"><td>Net interest income</td><td>320</td></tr>
  <tr data-kind="bridge" data-sign="-"><td>Expenses</td><td>145</td></tr>
  <tr data-kind="total"><td>1H26 PBT</td><td>5,375</td></tr>
</tbody></table>
"""


def main() -> int:
    print("slide HTML parser")

    els = parse_elements(TEXT_TABLE)
    check("one element parsed", len(els) == 1, str(len(els)))
    check("element_type from data-element",
          els[0]["element_type"] == "text_table", els[0]["element_type"])
    check("element_title from data-title",
          els[0]["element_title"] == "Key figures (S$m)", els[0]["element_title"])
    check("leading header cell dropped so columns align with cells",
          els[0]["columns"] == ["2Q26", "2Q25"], str(els[0]["columns"]))
    check("row label separated from its cells",
          els[0]["rows"][0]["label"] == "Net interest income"
          and els[0]["rows"][0]["cells"] == ["3,483", "3,625"],
          str(els[0]["rows"][0]))
    check("values kept VERBATIM — commas not stripped",
          els[0]["rows"][1]["cells"] == ["6,093", "5,732"],
          str(els[0]["rows"][1]["cells"]))

    w = parse_elements(WATERFALL)[0]
    check("waterfall data-kind captured on the row",
          [r["kind"] for r in w["rows"]] == ["total", "bridge", "bridge", "total"],
          str([r["kind"] for r in w["rows"]]))
    check("waterfall data-sign captured — the colour-derived sign",
          [r["sign"] for r in w["rows"]] == [None, "+", "-", None],
          str([r["sign"] for r in w["rows"]]))

    both = parse_elements(TEXT_TABLE + WATERFALL)
    check("multiple elements on one slide",
          [e["element_type"] for e in both] == ["text_table", "waterfall"],
          str([e["element_type"] for e in both]))

    check("no header row -> columns []",
          parse_elements(
              '<table data-element="kpi_grid"><tbody><tr><td>CET1</td>'
              '<td>15.1%</td></tr></tbody></table>')[0]["columns"] == [],
          "")
    check("empty / junk input is not a crash",
          parse_elements("") == [] and parse_elements("no tables here") == [], "")
    check("a printed dash survives as data, not as an empty cell",
          parse_elements(
              '<table data-element="text_table"><tbody><tr><td>x</td>'
              '<td>-</td></tr></tbody></table>')[0]["rows"][0]["cells"] == ["-"],
          "")

    print("\nALL PASS" if not _FAILS else f"\n{_FAILS} FAILED")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
