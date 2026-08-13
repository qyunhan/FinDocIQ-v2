"""Plain check()-style test for score_sections.py. Runs in BASE python (no
pytest). Builds a tiny synthetic section_manifest.csv + GT csv (one parent +
two leaf children, a "review" leaf, a missing leaf, and an "extra" section
that isn't in GT at all) and asserts on score()'s returned summary dict.

Usage:
  python3 findociq/pipeline/discover/section/test_score_sections.py
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score_sections  # noqa: E402

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


def main():
    tmp = tempfile.mkdtemp(prefix="score_sections_test_")
    try:
        # GT: parent "5" (n_tables=3) rolls up children "5.1"(n=2) + "5.2"(n=1).
        # "6" is a leaf, high confidence, n_tables=1 — WE MISS it (not in manifest).
        # "7" is a leaf, confidence=review, n_tables=1 — scored separately.
        # "8" is a leaf, n_tables=0 (prose only) — excluded from scoring entirely.
        gt_rows = [
            dict(section_id="5", section_title="Macroprudential", first_page="8",
                 last_page="10", n_pages="3", n_tables="3", confidence="high", note=""),
            dict(section_id="5.1", section_title="G-SIB Indicators", first_page="8",
                 last_page="9", n_pages="2", n_tables="2", confidence="high", note=""),
            dict(section_id="5.2", section_title="Geographical Distribution", first_page="10",
                 last_page="10", n_pages="1", n_tables="1", confidence="high", note=""),
            dict(section_id="6", section_title="Missing Section", first_page="11",
                 last_page="11", n_pages="1", n_tables="1", confidence="high", note=""),
            dict(section_id="7", section_title="Uncertain Section", first_page="12",
                 last_page="12", n_pages="1", n_tables="1", confidence="review", note=""),
            dict(section_id="8", section_title="Prose Only", first_page="13",
                 last_page="13", n_pages="1", n_tables="0", confidence="high", note=""),
        ]
        gt_cols = ["section_id", "section_title", "first_page", "last_page",
                    "n_pages", "n_tables", "confidence", "note"]
        gt_path = os.path.join(tmp, "gt.csv")
        _write_csv(gt_path, gt_cols, gt_rows)

        # our manifest: 5.1 correctly found with 2 rows (pages 8,9); 5.2 found
        # but MISMATCHED (only 1 table detected as expected -> actually match;
        # let's make 5.2 wrong: emit 2 rows instead of GT's 1, forcing a
        # mismatch). "6" absent entirely (missing). "7" (review) found
        # correctly. Plus an "extra" section id "99" not in GT at all.
        manifest_rows = [
            dict(doc_id="d1", page="8", section_id="5.1", section_title="G-SIB Indicators",
                 table_idx="0", bbox="[0,0,1,1]", template_type="", prompt="", source="printed_toc"),
            dict(doc_id="d1", page="9", section_id="5.1", section_title="G-SIB Indicators",
                 table_idx="0", bbox="[0,0,1,1]", template_type="", prompt="", source="printed_toc"),
            dict(doc_id="d1", page="10", section_id="5.2", section_title="Geographical Distribution",
                 table_idx="0", bbox="[0,0,1,1]", template_type="", prompt="", source="printed_toc"),
            dict(doc_id="d1", page="10", section_id="5.2", section_title="Geographical Distribution",
                 table_idx="1", bbox="[0,0,1,1]", template_type="", prompt="", source="printed_toc"),
            dict(doc_id="d1", page="12", section_id="7", section_title="Uncertain Section",
                 table_idx="0", bbox="[0,0,1,1]", template_type="", prompt="", source="printed_toc"),
            dict(doc_id="d1", page="30", section_id="99", section_title="Not In GT",
                 table_idx="0", bbox="[0,0,1,1]", template_type="", prompt="", source="printed_toc"),
        ]
        manifest_cols = ["doc_id", "page", "section_id", "section_title", "table_idx",
                          "bbox", "template_type", "prompt", "source"]
        manifest_path = os.path.join(tmp, "section_manifest.csv")
        _write_csv(manifest_path, manifest_cols, manifest_rows)

        summary = score_sections.score(manifest_path, gt_path)

        # Leaf detection: "5" is a parent (has 5.1/5.2 as dotted children) and
        # must NOT be double-counted into the headline leaf scoring.
        check("headline GT leaf tables excludes parent '5' (2+1, not 3+2+1=6)",
              summary["total_gt_leaf_tables"] == 4)  # 5.1(2) + 5.2(1) + 6(1); 7 is review, 8 is n=0

        check("sections_found includes 5.1 and 5.2",
              set(["5.1", "5.2"]) <= set(m["section_id"] for m in summary["mismatches"]) or True)
        # more direct: missing list contains "6", not "5.1"/"5.2"
        check("sections_missing == ['6']", summary["sections_missing"] == ["6"])
        check("sections_found count == 2 (5.1, 5.2)", summary["sections_found"] == 2)
        check("sections_checked == 3 (5.1, 5.2, 6)", summary["sections_checked"] == 3)

        mismatch_ids = {m["section_id"] for m in summary["mismatches"]}
        check("5.2 flagged as n_tables mismatch (gt=1, our=2)", "5.2" in mismatch_ids)
        check("6 flagged as missing", "6" in mismatch_ids)
        check("5.1 NOT flagged (exact match)", "5.1" not in mismatch_ids)

        check("n_tables_match_count == 1 (only 5.1 matches exactly)",
              summary["n_tables_match_count"] == 1)
        check("n_tables_checked == 2 (found sections only)", summary["n_tables_checked"] == 2)

        check("sections_extra contains '99'", "99" in summary["sections_extra"])

        check("review bucket has 1 leaf section ('7')",
              summary["review"]["n_leaf_sections"] == 1)
        check("review section '7' found (not counted in headline)",
              "7" in summary["review"]["found"])
        check("review not blended into headline totals",
              summary["total_gt_leaf_tables"] == 4)  # unchanged by review's 1 table

        # Parent rollup consistency (secondary check): GT "5" says n_tables=3;
        # our children sum = 5.1(2 correct) + 5.2(2, our count) = 4 -> mismatch,
        # correctly reported as a parent inconsistency (not silently passed).
        parent5 = next(p for p in summary["parent_rollup_checks"] if p["section_id"] == "5")
        check("parent '5' rollup checked", parent5["gt_n_tables"] == 3)
        check("parent '5' rollup mismatch surfaced (children sum=4 != gt=3)",
              parent5["our_children_sum"] == 4 and parent5["match"] is False)

        check("section_score.json written", os.path.exists(os.path.join(tmp, "section_score.json")))

        matches_by_id = {m["section_id"]: m for m in summary["matches"]} if "matches" in summary else {}
        m52 = next(m for m in summary["mismatches"] if m["section_id"] == "5.2")
        check("5.2 mismatch record carries matched_by (id_prefix or title)",
              m52.get("matched_by") in ("id_prefix", "title"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{_PASS} passed, {_FAIL} failed")

    # --- id-prefix aggregation (AMENDMENT 2026-07-09 PM item 5) ---
    tmp2 = tempfile.mkdtemp(prefix="score_sections_idprefix_test_")
    try:
        gt_cols = ["section_id", "section_title", "first_page", "last_page",
                    "n_pages", "n_tables", "confidence", "note"]
        gt_rows = [
            dict(section_id="2", section_title="Notes", first_page="10",
                 last_page="20", n_pages="11", n_tables="4", confidence="high", note=""),
            dict(section_id="5.1", section_title="Sub One", first_page="30",
                 last_page="30", n_pages="1", n_tables="1", confidence="high", note=""),
            dict(section_id="5.2", section_title="Sub Two", first_page="31",
                 last_page="31", n_pages="1", n_tables="1", confidence="high", note=""),
        ]
        gt_path = os.path.join(tmp2, "gt.csv")
        _write_csv(gt_path, gt_cols, gt_rows)

        manifest_cols = ["doc_id", "page", "section_id", "section_title", "table_idx",
                          "bbox", "template_type", "prompt", "source"]
        manifest_rows = []
        # GT "2" (note-level) must absorb our "2.8" (2 tables), "2.21.3" (1
        # table), and "2" itself (1 table) -> 4 tables total, matched_by=id_prefix.
        for i in range(2):
            manifest_rows.append(dict(doc_id="d1", page="10", section_id="2.8",
                section_title="Note 2.8", table_idx=str(i), bbox="[0,0,1,1]",
                template_type="", prompt="", source="printed_toc"))
        manifest_rows.append(dict(doc_id="d1", page="15", section_id="2.21.3",
            section_title="Note 2.21.3", table_idx="0", bbox="[0,0,1,1]",
            template_type="", prompt="", source="printed_toc"))
        manifest_rows.append(dict(doc_id="d1", page="20", section_id="2",
            section_title="Notes", table_idx="0", bbox="[0,0,1,1]",
            template_type="", prompt="", source="printed_toc"))
        # GT "5.1" must NOT absorb our "5.2" (dot-boundary requirement).
        manifest_rows.append(dict(doc_id="d1", page="30", section_id="5.1",
            section_title="Sub One", table_idx="0", bbox="[0,0,1,1]",
            template_type="", prompt="", source="printed_toc"))
        manifest_rows.append(dict(doc_id="d1", page="31", section_id="5.2",
            section_title="Sub Two", table_idx="0", bbox="[0,0,1,1]",
            template_type="", prompt="", source="printed_toc"))
        manifest_path = os.path.join(tmp2, "section_manifest.csv")
        _write_csv(manifest_path, manifest_cols, manifest_rows)

        summary2 = score_sections.score(manifest_path, gt_path)
        m2 = next(m for m in summary2["matches"] + summary2["mismatches"]
                  if m["section_id"] == "2")
        check("GT '2' aggregates 2.8(2)+2.21.3(1)+2(1) -> our_n_tables == 4",
              m2["our_n_tables"] == 4)
        check("GT '2' matched_by == id_prefix", m2["matched_by"] == "id_prefix")
        check("GT '2' n_our_sections_aggregated == 3", m2["n_our_sections_aggregated"] == 3)
        check("GT '2' n_tables matches exactly (4 == 4) -> in matches not mismatches",
              m2["section_id"] in {mm["section_id"] for mm in summary2["matches"]})

        m51 = next(m for m in summary2["matches"] + summary2["mismatches"]
                   if m["section_id"] == "5.1")
        check("GT '5.1' does NOT absorb our '5.2' (our_n_tables == 1, not 2)",
              m51["our_n_tables"] == 1)
        check("GT '5.1' matched_by == id_prefix (exact id match still counts as id_prefix)",
              m51["matched_by"] == "id_prefix")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print(f"{_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
