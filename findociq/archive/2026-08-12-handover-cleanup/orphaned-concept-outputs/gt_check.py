"""concept.gt_check — EXTERNAL ground-truth gate for Key Financial Highlights.

`concept/validate.py` checks the corpus against ITSELF: formula identities,
uniqueness, sums_to, nature. Every one of its checks can pass on a corpus that
is internally coherent and still wrong about what the filing actually printed.
This module closes that gap: it compares `v_fact_metric_serving` against
`data/sources/kph_ground_truth_all_periods.csv`, hand-transcribed from the
published filings.

A mismatch here means the PIPELINE'S NUMBER DISAGREES WITH THE PRINTED FILING —
a strictly stronger signal than a validate.py flag. Nothing is auto-corrected;
the run emits a report and exits non-zero so a human adjudicates.

Ground-truth shape: wide, one row per (bank, concept_key), one column per
period label (`1Q23`..`4Q26`, `1H23`..`2H26`, `FY23`..`FY26`). Blank cell = not
transcribed (that bank does not print the figure for that period), which is
SKIPPED, never counted as a miss — the file's coverage is not a claim about the
pipeline's coverage.

Period-label resolution is grounded in what the corpus actually stores, not an
assumption:

  * A quarter/half/FY label maps to its period-END date plus the matching
    `period_span` ('4Q25' -> 2025-12-31 / '4Q').
  * STOCK concepts are stored redundantly: DBS bs.assets.total at 2025-12-31
    exists under '2H', '4Q', 'FY' AND 'as_at', all 897,488. So an exact span
    match works for stocks too, and `as_at` is only a FALLBACK for the rows
    that carry no duration span (observed: some 2025-09-30 rows have
    period_span NULL). Fallbacks are reported in `matched_on`, never silent.

Group-level only: SEG_TOTAL / GLOBAL / IND_TOTAL / CONSOLIDATED. The ground
truth transcribes headline figures, so a segment row must not be allowed to
satisfy a headline check.

Run:  python -m concept.gt_check --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

# Ground-truth columns that are period labels start after these four.
ID_COLUMNS = ("bank", "concept_key", "section", "display")
NOTES_COLUMN = "notes"

# Serving-view axis pins. The ground truth is headline/group-level; without
# these a segment or bank-solo row could satisfy a headline check.
GROUP_AXES = {
    "segment_key": "SEG_TOTAL",
    "geo_key": "GLOBAL",
    "industry_key": "IND_TOTAL",
    "legal_entity": "CONSOLIDATED",
}

# Half-of-last-printed-digit, by the unit the corpus stores. S$m figures are
# printed as whole millions, ratios and per-share figures to 2dp. Slightly
# wider than a strict half-ulp so that two independent roundings of the same
# underlying number (ours and the filing's) do not read as a disagreement.
TOLERANCE_BY_UNIT = {
    "S$m": 0.51,
    "%": 0.011,
    "per_share": 0.011,
}
DEFAULT_TOLERANCE = 0.011

_QUARTER_END = {"1Q": "03-31", "2Q": "06-30", "3Q": "09-30", "4Q": "12-31"}
_HALF_END = {"1H": "06-30", "2H": "12-31"}


def bank_of(name: str) -> str:
    """institution / source-file string -> 'DBS'/'OCBC'/'UOB'/'Other'.

    MIRRORS `app.findociq_app._bank_of` deliberately rather than importing it:
    the pipeline must not depend on the app. `test_gt_check.py` asserts the two
    agree on every institution in the corpus, so the duplication cannot drift
    silently. Matching covers both the ticker and the full legal name (neither
    'Oversea-Chinese Banking Corporation Ltd' nor 'United Overseas Bank Ltd'
    contains its own ticker as a substring)."""
    up = name.upper()
    if "OCBC" in up or "OVERSEA-CHINESE" in up or "OVERSEA CHINESE" in up:
        return "OCBC"
    if "UOB" in up or "UNITED OVERSEAS BANK" in up:
        return "UOB"
    if "DBS" in up:
        return "DBS"
    return "Other"


def parse_period_column(label: str) -> tuple[str, str] | None:
    """'4Q25' -> ('2025-12-31', '4Q'); '1H24' -> ('2024-06-30', '1H');
    'FY23' -> ('2023-12-31', 'FY'). Returns None for a non-period column, which
    is how the caller separates id columns from period columns without having to
    hardcode the column list twice. Pure/testable."""
    label = label.strip()
    if len(label) != 4 or not label[2:].isdigit():
        return None
    tag, yy = label[:2].upper(), int(label[2:])
    year = 2000 + yy
    if tag in _QUARTER_END:
        return f"{year}-{_QUARTER_END[tag]}", tag
    if tag in _HALF_END:
        return f"{year}-{_HALF_END[tag]}", tag
    if tag == "FY":
        return f"{year}-12-31", "FY"
    return None


def parse_gt_value(raw: str) -> float | None:
    """Ground-truth cell -> float, or None for a blank (= not transcribed).
    Tolerates thousands separators and parenthesised negatives, both of which
    appear in transcribed filing figures."""
    s = (raw or "").strip().replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def tolerance_for(unit: str | None) -> float:
    return TOLERANCE_BY_UNIT.get(unit or "", DEFAULT_TOLERANCE)


def compare(expected: float, actual: float, unit: str | None) -> tuple[bool, float]:
    """(is_match, signed difference actual-expected) under the unit's tolerance."""
    diff = actual - expected
    return abs(diff) <= tolerance_for(unit), diff


def load_ground_truth(path: str | Path) -> list[dict]:
    """Melt the wide ground truth into one record per non-blank cell:
    {bank, concept_key, section, display, period, period_span, column, expected}.
    Blank cells are dropped here — see the module docstring on why a blank is
    not a miss. Pure: a file read plus reshaping, no DB."""
    out: list[dict] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            for col, raw in row.items():
                if col is None or col in ID_COLUMNS or col == NOTES_COLUMN:
                    continue
                parsed = parse_period_column(col)
                if parsed is None:
                    continue
                value = parse_gt_value(raw)
                if value is None:
                    continue
                period, span = parsed
                out.append({
                    "bank": row["bank"].strip(),
                    "concept_key": row["concept_key"].strip(),
                    "section": (row.get("section") or "").strip(),
                    "display": (row.get("display") or "").strip(),
                    "column": col,
                    "period": period,
                    "period_span": span,
                    "expected": value,
                })
    return out


def _serving_index(con: sqlite3.Connection) -> dict:
    """(bank, concept_key, period, period_span) -> list of serving rows, plus a
    span-agnostic (bank, concept_key, period) index for the fallback. One pass
    over the view — the checker is O(cells), not O(cells x queries)."""
    where = " AND ".join(f"{k} = ?" for k in GROUP_AXES)
    sql = (
        "SELECT institution, concept_key, period, period_span, value_num, unit, "
        "       source_doc_id, source_row_label, resolved_by "
        "FROM v_fact_metric_serving "
        f"WHERE {where} AND value_num IS NOT NULL"
    )
    exact: dict = {}
    by_period: dict = {}
    for r in con.execute(sql, tuple(GROUP_AXES.values())):
        inst, ck, period, span, value, unit, doc, label, resolved = r
        rec = {
            "value": value, "unit": unit, "period_span": span,
            "source_doc_id": doc, "source_row_label": label,
            "resolved_by": resolved,
        }
        bank = bank_of(inst or "")
        exact.setdefault((bank, ck, period, span), []).append(rec)
        by_period.setdefault((bank, ck, period), []).append(rec)
    return {"exact": exact, "by_period": by_period}


def check(con: sqlite3.Connection, gt_rows: list[dict]) -> list[dict]:
    """One result record per ground-truth cell. `status` is one of:

      match          — corpus agrees with the filing within tolerance
      mismatch       — corpus has the figure and DISAGREES  (the real finding)
      missing        — corpus has no group-level row for that concept/period
      ambiguous      — >1 distinct group-level value; cannot adjudicate here

    `matched_on` records how the row was found ('span' or 'as_at_fallback'), so
    a fallback never hides inside a pass."""
    idx = _serving_index(con)
    results: list[dict] = []
    for cell in gt_rows:
        key = (cell["bank"], cell["concept_key"], cell["period"])
        recs = idx["exact"].get(key + (cell["period_span"],))
        matched_on = "span"
        if not recs:
            # Rows carrying no duration span (observed at 2025-09-30) or stored
            # only as a balance-sheet marker still legitimately answer the
            # question; take them, but say so.
            recs = [r for r in idx["by_period"].get(key, [])
                    if r["period_span"] in (None, "as_at")]
            matched_on = "as_at_fallback"

        base = dict(cell)
        if not recs:
            results.append({**base, "status": "missing", "actual": None,
                            "diff": None, "unit": None, "matched_on": None,
                            "source_doc_id": None, "source_row_label": None})
            continue

        distinct = {round(r["value"], 6) for r in recs}
        if len(distinct) > 1:
            results.append({**base, "status": "ambiguous",
                            "actual": sorted(distinct), "diff": None,
                            "unit": recs[0]["unit"], "matched_on": matched_on,
                            "source_doc_id": recs[0]["source_doc_id"],
                            "source_row_label": recs[0]["source_row_label"]})
            continue

        rec = recs[0]
        ok, diff = compare(cell["expected"], rec["value"], rec["unit"])
        results.append({**base,
                        "status": "match" if ok else "mismatch",
                        "actual": rec["value"], "diff": round(diff, 6),
                        "unit": rec["unit"], "matched_on": matched_on,
                        "source_doc_id": rec["source_doc_id"],
                        "source_row_label": rec["source_row_label"]})
    return results


def summarise(results: list[dict]) -> dict:
    counts: dict = {"match": 0, "mismatch": 0, "missing": 0, "ambiguous": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    counts["total"] = len(results)
    checked = counts["match"] + counts["mismatch"]
    counts["agreement_pct"] = round(100.0 * counts["match"] / checked, 2) if checked else 0.0
    return counts


REPORT_COLUMNS = ["bank", "concept_key", "display", "section", "column", "period",
                  "period_span", "status", "expected", "actual", "diff", "unit",
                  "matched_on", "source_doc_id", "source_row_label"]


def write_report(results: list[dict], path: str | Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(results, key=lambda r: (r["status"] != "mismatch",
                                                r["bank"], r["concept_key"],
                                                r["period"])):
            w.writerow(r)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]          # findociq/
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(root / "db" / "compiled_fs.db"))
    ap.add_argument("--gt", default=str(root / "data" / "sources"
                                        / "kph_ground_truth_all_periods.csv"))
    ap.add_argument("--out", default=str(root / "data" / "derived"
                                        / "kph_ground_truth_report.csv"))
    ap.add_argument("--fail-on-missing", action="store_true",
                    help="also exit non-zero when the corpus lacks a figure "
                         "the filing prints (default: only mismatches fail)")
    args = ap.parse_args(argv)

    gt_rows = load_ground_truth(args.gt)
    con = sqlite3.connect(args.db)
    try:
        results = check(con, gt_rows)
    finally:
        con.close()
    write_report(results, args.out)
    s = summarise(results)

    print(f"ground-truth cells: {s['total']}   "
          f"match {s['match']}   mismatch {s['mismatch']}   "
          f"missing {s['missing']}   ambiguous {s['ambiguous']}")
    print(f"agreement on checkable cells: {s['agreement_pct']}%")
    print(f"report: {args.out}")

    for r in results:
        if r["status"] == "mismatch":
            print(f"  MISMATCH {r['bank']:5s} {r['concept_key']:38s} {r['column']:5s} "
                  f"filing={r['expected']}  corpus={r['actual']}  diff={r['diff']}")

    failed = s["mismatch"] > 0 or (args.fail_on_missing and s["missing"] > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
