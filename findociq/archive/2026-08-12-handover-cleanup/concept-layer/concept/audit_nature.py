"""concept.audit_nature — punch-list for concept.validate()'s nature checks
(nature_flow_as_at, nature_as_at_magnitude). Read-only: runs validate() against
an existing DB and writes the flagged rows to a CSV, same pattern as
build_fact_metric._write_conflicts. Meant to be re-run before/after a
concept_dictionary.yaml nature/alias edit to confirm the mismatch count
actually dropped.

  python -m concept.audit_nature [--db PATH]
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.validate import validate  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _ROOT / "findociq" / "db" / "compiled_fs.db"
_OUT = _ROOT / "findociq" / "data" / "derived" / "concept_nature_conflicts.csv"

_FLAG_RX = re.compile(r"^\[(\w+)\]\s*(.*)$")


def audit(db: str | Path = _DEFAULT_DB) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        report = validate(con)
    finally:
        con.close()

    checks = {c["name"]: c for c in report["checks"]}
    nature_checks = {k: v for k, v in checks.items() if k.startswith("nature_")}
    rows = [_FLAG_RX.match(f).groups() for f in report["flags"]
            if f.startswith("[flow_as_at]") or f.startswith("[as_at_magnitude]")]

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["check", "detail"])
        w.writerows(rows)

    return dict(checks=nature_checks, n_flags=len(rows), csv=str(_OUT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DEFAULT_DB))
    args = ap.parse_args()

    res = audit(args.db)
    for name, c in res["checks"].items():
        print(f"{name:24s} checked={c['checked']:5d}  failed={c['failed']:5d}")
    print(f"\n{res['n_flags']} flag lines -> {res['csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
