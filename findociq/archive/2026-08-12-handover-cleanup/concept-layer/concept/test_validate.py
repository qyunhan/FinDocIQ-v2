"""Plain check() runner for concept.validate's standing assertion.

    python findociq/pipeline/concept/test_validate.py

Covers assert_single_legal_entity_per_group: passes on good data (each
group has exactly one legal_entity, as fact_metric's PK guarantees in
production) and raises AssertionError on planted bad data (two CONSOLIDATED
rows landing in the same analytic group -- the exact failure mode the
serving view exists to prevent). The live-DB "passes on good data" check
lives in test_fact_metric.py (test_serving_view_and_assertion_live_db);
this file exercises the assertion's SQL/logic directly against small,
hand-built fixtures so it doesn't depend on compiled_fs.db's current state.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.validate import assert_single_legal_entity_per_group  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, got, want) -> None:
    global _PASS, _FAIL
    ok = got == want
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + ("" if ok else f"  got={got!r} want={want!r}"))


# Minimal fixture: a bare fact_metric-shaped table plus v_fact_metric_serving.
# `filtered` controls whether the view carries its production
# `WHERE legal_entity = 'CONSOLIDATED'` clause: the assertion's exact SQL
# (COUNT(DISTINCT legal_entity) > 1 over the SERVING VIEW) can only ever
# find a violation if that filter is missing/broken -- with the filter in
# place, every row the view exposes already shares one legal_entity value by
# construction, so this is a regression trap, not a today-can-happen bug.
# filtered=False reproduces exactly that regression to prove the trap fires.
_COLS = ("institution", "concept_key", "period", "period_span", "segment_key",
         "geo_key", "industry_key", "legal_entity", "value_num")


def _fixture_con(rows: list[tuple], *, filtered: bool = True) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(f"CREATE TABLE fact_metric ({', '.join(c + ' TEXT' for c in _COLS[:-1])}, "
                f"value_num REAL)")
    where = "WHERE legal_entity = 'CONSOLIDATED'" if filtered else ""
    con.execute(f"CREATE VIEW v_fact_metric_serving AS SELECT * FROM fact_metric {where}")
    con.executemany(f"INSERT INTO fact_metric VALUES ({','.join('?' * len(_COLS))})", rows)
    con.commit()
    return con


def _row(inst="UOB", ck="bs.assets.total", period="2025-12-31", span="FY",
         seg="SEG_TOTAL", geo="GLOBAL", ind="IND_TOTAL", entity="CONSOLIDATED",
         val=1.0) -> tuple:
    return (inst, ck, period, span, seg, geo, ind, entity, val)


def test_passes_on_good_data():
    print("assertion passes: one legal_entity per group (CONSOLIDATED + BANK_SOLO coexist)")
    con = _fixture_con([
        _row(entity="CONSOLIDATED", val=572061.0),
        _row(entity="BANK_SOLO", val=485263.0),          # different entity: fine, filtered out
        _row(ck="pnl.nii.net", entity="CONSOLIDATED", val=9355.0),
    ])
    try:
        n = assert_single_legal_entity_per_group(con)
        check("no exception raised", True, True)
        check("checked 2 CONSOLIDATED groups", n, 2)
    except AssertionError as e:
        check(f"unexpected AssertionError: {e}", False, True)
    finally:
        con.close()


def test_fails_on_planted_bad_data():
    print("assertion fails loudly: serving view regressed (no CONSOLIDATED filter), "
          "Group + Bank rows leak into the same group -- exactly the UOB bug this axis fixes")
    con = _fixture_con([
        _row(entity="CONSOLIDATED", val=572061.0),
        _row(entity="BANK_SOLO", val=485263.0),   # same group key, different entity, both leak
    ], filtered=False)
    try:
        assert_single_legal_entity_per_group(con)
        check("AssertionError raised on bad data", False, True)
    except AssertionError as e:
        check("AssertionError raised on bad data", True, True)
        check("message names the offending group", "bs.assets.total" in str(e), True)
    finally:
        con.close()


def main() -> int:
    for t in (test_passes_on_good_data, test_fails_on_planted_bad_data):
        t()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
