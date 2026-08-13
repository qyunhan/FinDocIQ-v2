"""Plain check()-style test for section_manifest.py. Runs in BASE python (no
pytest). Builds a small synthetic <tag>/{regions,section_tags}.csv fixture
(the pinned Stage-1/Stage-2/3 shapes), runs build_manifest(), and asserts on
the emitted section_manifest.csv + section_map.html.

Usage:
  python3 findociq/pipeline/discover/section/test_section_manifest.py
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import section_manifest  # noqa: E402

MANIFEST_COLS = ["doc_id", "page", "section_id", "section_title", "table_idx",
                  "bbox", "unit_id", "unit_n_tables", "page_shared", "framing",
                  "template_type", "prompt", "source"]

_PASS = _FAIL = 0


def check(label, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


def _write_csv(path, cols, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    out_root = tempfile.mkdtemp(prefix="section_manifest_test_")
    tag = "unit_test_doc"
    tag_dir = os.path.join(out_root, tag)
    os.makedirs(tag_dir, exist_ok=True)
    try:
        # regions.csv: 4 table regions across 3 pages
        regions_rows = [
            dict(page="7", table_idx="0", x0="10", y0="20", x1="500", y1="200"),
            dict(page="8", table_idx="0", x0="10", y0="20", x1="500", y1="200"),
            dict(page="8", table_idx="1", x0="10", y0="220", x1="500", y1="400"),
            dict(page="9", table_idx="0", x0="10", y0="20", x1="500", y1="200"),
        ]
        _write_csv(os.path.join(tag_dir, "regions.csv"),
                    ["page", "table_idx", "x0", "y0", "x1", "y1"], regions_rows)

        # section_tags.csv: page7 tbl0 -> section 4 "Key Metrics"; page8 both
        # tables -> section 5.1; page9 tbl0 has NO matching tag (simulate an
        # unattributed region — must not crash the join).
        tags_rows = [
            dict(page="7", table_idx="0", section_id="4", section_title="Key Metrics",
                 source="printed_toc"),
            dict(page="8", table_idx="0", section_id="5.1",
                 section_title="Disclosure of G-SIB Indicators", source="printed_toc"),
            dict(page="8", table_idx="1", section_id="5.1",
                 section_title="Disclosure of G-SIB Indicators", source="printed_toc"),
        ]
        _write_csv(os.path.join(tag_dir, "section_tags.csv"),
                    ["page", "table_idx", "section_id", "section_title", "source"], tags_rows)

        manifest_path = section_manifest.build_manifest(tag, "doc123", out_root=out_root)

        check("manifest file written", os.path.exists(manifest_path))
        rows = _read_csv(manifest_path)
        check("manifest has exact columns", list(rows[0].keys()) == MANIFEST_COLS if rows else False)
        check("manifest row count == regions row count", len(rows) == len(regions_rows))

        by_key = {(r["page"], r["table_idx"]): r for r in rows}

        r1 = by_key[("7", "0")]
        check("doc_id propagated", r1["doc_id"] == "doc123")
        check("section_id joined correctly (page7)", r1["section_id"] == "4")
        check("section_title joined correctly (page7)", r1["section_title"] == "Key Metrics")
        check("source joined correctly (page7)", r1["source"] == "printed_toc")
        check("bbox formatted as [x0,y0,x1,y1]", r1["bbox"] == "[10,20,500,200]")

        r2 = by_key[("8", "1")]
        check("second table on page8 also tagged 5.1", r2["section_id"] == "5.1")

        r3 = by_key[("9", "0")]
        check("unattributed region gets blank section_id (no crash)", r3["section_id"] == "")
        check("unattributed region gets blank source", r3["source"] == "")
        check("unattributed region gets blank template_type", r3["template_type"] == "")

        # every region now gets a routing prompt (cardinality-based), even
        # unattributed ones — prompt is no longer gated by template_type.
        check("template_type is either blank or a known registry type",
              all(r["template_type"] in ("", "nsfr", "km1", "lcr") for r in rows))
        check("every row has a core+framing prompt",
              all(r["prompt"].startswith("stage2_core.txt + ")
                  and r["framing"] in ("SINGLE", "MULTIPLE") for r in rows))
        check("KNOWN[...] modifier present iff a template matched",
              all(("KNOWN[" in r["prompt"]) == (r["template_type"] != "") for r in rows))
        check("unit_n_tables >= 1 for every tagged region",
              all(int(r["unit_n_tables"]) >= 1 for r in rows))
        check("multi-table framing tags carry the (xN tables ...) note",
              all(("tables, this section only)" in r["prompt"]) == (int(r["unit_n_tables"]) > 1)
                  for r in rows))

        map_path = os.path.join(tag_dir, "section_map.html")
        check("section_map.html written", os.path.exists(map_path))
        html_text = open(map_path).read()
        check("section_map.html mentions doc_id", "doc123" in html_text)
        check("section_map.html lists section 4", ">4<" in html_text)
        check("section_map.html lists section 5.1", ">5.1<" in html_text)
        check("section_map.html shows table_count=2 for 5.1 (two rows)",
              "<td>2</td>" in html_text)
    finally:
        shutil.rmtree(out_root, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
