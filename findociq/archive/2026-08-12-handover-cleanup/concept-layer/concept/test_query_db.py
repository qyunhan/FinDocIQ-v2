"""Plain check()-style tests for concept.query_db.pull — no pytest required.

Run: python3 findociq/pipeline/concept/test_query_db.py
Asserts a couple of pulls return the expected shape and that the dimension
breakdown path returns MEMBERS, not the default whole-bank total.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.query_db import pull, _DB, _FIELDS  # noqa: E402

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {msg}")
    else:
        _FAIL += 1
        print(f"  FAIL {msg}")


def test_shape():
    rows = pull(_DB, concept_keys=["pnl.nii.net"], institutions=["UOB"], spans=["FY"])
    check(len(rows) > 0, "UOB pnl.nii.net FY returns rows")
    r = rows[0]
    check(all(f in r for f in _FIELDS), "row has all declared fields")
    check(r["concept_key"] == "pnl.nii.net", "concept_key filtered correctly")
    check(r["concept_name"] == "Net interest income", "concept_name joined from dict")
    check("United Overseas" in r["institution"], "institution alias UOB matched")
    check(r["period_span"] == "FY", "span filter applied")


def test_default_is_whole_bank():
    rows = pull(_DB, concept_keys=["pnl.income.total"], institutions=["OCBC"])
    check(len(rows) > 0, "OCBC pnl.income.total default returns rows")
    check(all(r["segment_key"] == "SEG_TOTAL" for r in rows),
          "default slice is whole-bank segment (SEG_TOTAL)")
    check(all(r["geo_key"] == "GLOBAL" for r in rows),
          "default slice is whole-bank geo (GLOBAL)")


def test_segment_breakdown_returns_members():
    rows = pull(_DB, concept_keys=["pnl.income.total"], institutions=["OCBC"],
                dimension="segment")
    check(len(rows) > 0, "segment breakdown returns rows")
    check(all(r["segment_key"] != "SEG_TOTAL" for r in rows),
          "dimension=segment excludes the SEG_TOTAL default (members only)")
    segs = {r["segment_key"] for r in rows}
    check(len(segs) >= 3, f"multiple segment members present ({len(segs)})")


def test_geo_breakdown_returns_members():
    rows = pull(_DB, concept_keys=["bs.assets.total"], institutions=["DBS"],
                dimension="geo")
    check(len(rows) > 0, "geo breakdown returns rows")
    check(all(r["geo_key"] != "GLOBAL" for r in rows),
          "dimension=geo excludes the GLOBAL default (members only)")
    geos = {r["geo_key"] for r in rows}
    check(len(geos) >= 3, f"multiple geo members present ({len(geos)})")


def test_dedup_collapses():
    # pnl.nii.net for UOB appears in highlights + performance tables; the default
    # dedup must collapse (institution, concept, period, span, seg, geo, unit).
    rows = pull(_DB, concept_keys=["pnl.nii.net"], institutions=["UOB"], spans=["FY"])
    keys = [(r["institution"], r["concept_key"], r["period"], r["period_span"],
             r["segment_key"], r["geo_key"], r["unit"]) for r in rows]
    check(len(keys) == len(set(keys)), "dedup leaves one row per identity key")


def test_label_fallback_off_by_default():
    # bs.assets.customer_loans_net is NOT stamped for OCBC (printed 'Net loans').
    # Default (no fallback) stays concept-clean: 0 rows, no 'via' tag.
    rows = pull(_DB, concept_keys=["bs.assets.customer_loans_net"], institutions=["OCBC"])
    check(len(rows) == 0, "OCBC customer_loans_net has no stamped rows (default)")
    check(all("via" not in r for r in rows), "no 'via' tag on default pulls")


def test_label_fallback_finds_rows():
    # With fallback on, the empty concept pull triggers a fuzzy row-label search
    # that surfaces the 'Net loans' rows, tagged via='label_fallback'.
    rows = pull(_DB, concept_keys=["bs.assets.customer_loans_net"], institutions=["OCBC"],
                fallback_label=True)
    check(len(rows) > 0, "fallback surfaces OCBC net-loans rows")
    check(all(r["via"] == "label_fallback" for r in rows),
          "fallback rows tagged via='label_fallback'")
    check(all(r["concept_key"] == "bs.assets.customer_loans_net" for r in rows),
          "fallback rows carry the requested concept_key")
    check(any("loan" in (r["row_label"] or "").lower() for r in rows),
          "fallback matched a loan-labelled row")


def test_label_fallback_prefers_concept_when_stamped():
    # When the concept IS stamped, fallback=True returns the concept rows tagged
    # via='concept' and does NOT fall through to label matching.
    rows = pull(_DB, concept_keys=["pnl.nii.net"], institutions=["UOB"], spans=["FY"],
                fallback_label=True)
    check(len(rows) > 0, "stamped concept returns rows with fallback on")
    check(all(r["via"] == "concept" for r in rows),
          "stamped rows tagged via='concept', not label_fallback")


def main() -> int:
    for t in (test_shape, test_default_is_whole_bank,
              test_segment_breakdown_returns_members,
              test_geo_breakdown_returns_members, test_dedup_collapses,
              test_label_fallback_off_by_default, test_label_fallback_finds_rows,
              test_label_fallback_prefers_concept_when_stamped):
        print(f"\n{t.__name__}")
        t()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
