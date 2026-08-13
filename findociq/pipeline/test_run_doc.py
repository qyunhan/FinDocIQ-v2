"""test_run_doc.py — pure-helper regression for the one-command driver.

No subprocess, no DB, no network. Exercises the three unit-testable helpers:
  1. infer_period      — period-token inference incl. the fail-loud case
  2. unit_from_meta    — building a load_units unit from an audit meta.json
  3. failing_table_ids — verify report -> the tables that must be re-extracted

check() runner convention (mirrors pass2/test_load_v7.py).
Run: python3 findociq/pipeline/test_run_doc.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # pipeline/ on path
from common import source_store as ss  # noqa: E402
import run_doc  # noqa: E402
from run_doc import (infer_period, unit_from_meta, failing_table_ids, doc_id_for,  # noqa: E402
                      aggregate_geometry_stats)

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


def test_infer_period() -> None:
    print("infer_period — period-token inference")
    check("1Q25 -> 2025-03-31",
          infer_period("DBS_1Q25_trading_update") == "2025-03-31")
    check("2Q25 -> 2025-06-30",
          infer_period("DBS_2Q25_performance_summary") == "2025-06-30")
    check("3Q25 -> 2025-09-30",
          infer_period("DBS_3Q25_trading_update") == "2025-09-30")
    check("4Q25 -> 2025-12-31",
          infer_period("OCBC_4Q25_Condensed_Financial_Statements") == "2025-12-31")
    check("FY2025 -> 2025-12-31", infer_period("BANK_FY2025_annual") == "2025-12-31")
    check("FY25 -> 2025-12-31", infer_period("BANK_FY25_annual") == "2025-12-31")
    check("case-insensitive 2q25", infer_period("dbs_2q25_summary") == "2025-06-30",
          infer_period("dbs_2q25_summary"))
    check("--doc-period override wins over token",
          infer_period("DBS_2Q25_x", "2099-01-01") == "2099-01-01")
    check("override wins even with no token",
          infer_period("no_token_here", "2030-05-05") == "2030-05-05")
    # fail-loud: no token, no override
    raised = False
    try:
        infer_period("DBS_condensed_financials")
    except ValueError:
        raised = True
    check("no token + no override -> ValueError", raised)
    raised2 = False
    try:
        # '5Q25' is not a valid quarter; no FY either -> fail loud
        infer_period("BANK_5Q25_x")
    except ValueError:
        raised2 = True
    check("5Q25 is not a valid quarter -> ValueError", raised2)


def test_unit_from_meta() -> None:
    print("unit_from_meta — audit meta.json -> load_units unit")
    meta = {
        "unit_id": "debts_issued_p25",
        "section_ids": ["debts_issued"],
        "pages": [25],
    }
    u = unit_from_meta(meta, "/audit/debts_issued_p25")
    check("section_id = section_ids[0]", u["section_id"] == "debts_issued")
    check("pages coerced to int list", u["pages"] == [25])
    check("parsed_path joins the unit dir",
          u["parsed_path"] == "/audit/debts_issued_p25/parsed.json", u["parsed_path"])
    # multi-page + string page tokens
    meta2 = {"section_ids": ["net_interest_income", "x"], "pages": ["10", "11"]}
    u2 = unit_from_meta(meta2, Path("/a/net_interest_income_p10-11"))
    check("first of several section_ids used", u2["section_id"] == "net_interest_income")
    check("string pages -> ints", u2["pages"] == [10, 11], str(u2["pages"]))


def test_failing_table_ids() -> None:
    print("failing_table_ids — verify report -> re-extract set")
    report = {
        "doc_id": "D", "tables": [
            {"table_id": "t_ok", "rows_failed": 0, "values_missing": []},
            {"table_id": "t_rowfail", "rows_failed": 2, "values_missing": []},
            {"table_id": "t_missing", "rows_failed": 0,
             "values_missing": [{"missing_value": 1.0}]},
            {"table_id": "t_both", "rows_failed": 1,
             "values_missing": [{"missing_value": 2.0}]},
        ]}
    got = failing_table_ids(report)
    check("clean table excluded", "t_ok" not in got)
    check("rows_failed>0 included", "t_rowfail" in got)
    check("values_missing included", "t_missing" in got)
    check("both-signals included once", got.count("t_both") == 1)
    check("exactly the 3 failing tables", set(got) == {"t_rowfail", "t_missing", "t_both"},
          str(got))
    check("all-clean report -> empty",
          failing_table_ids({"tables": [
              {"table_id": "a", "rows_failed": 0, "values_missing": []}]}) == [])
    check("no tables key -> empty", failing_table_ids({}) == [])


def test_aggregate_geometry_stats() -> None:
    print("aggregate_geometry_stats — geometry stage stats fold")
    empty = aggregate_geometry_stats([])
    check("no units -> all-zero stats",
          empty == {"units": 0, "unit_errors": 0, "tables_matched": 0,
                    "tables_total": 0, "rows_matched": 0, "rows_total": 0}, str(empty))

    unit_stats = [
        {"unit": "u1", "source": "pages.pdf", "tables_matched": 2, "tables_total": 2,
         "rows_matched": 10, "rows_total": 10},
        {"unit": "u2", "source": "source_pdf", "tables_matched": 1, "tables_total": 2,
         "rows_matched": 5, "rows_total": 8},
    ]
    got = aggregate_geometry_stats(unit_stats)
    check("units counted", got["units"] == 2, str(got))
    check("tables_matched summed", got["tables_matched"] == 3, str(got))
    check("tables_total summed", got["tables_total"] == 4, str(got))
    check("rows_matched summed", got["rows_matched"] == 15, str(got))
    check("rows_total summed", got["rows_total"] == 18, str(got))
    check("unit_errors defaults to 0", got["unit_errors"] == 0, str(got))

    got_err = aggregate_geometry_stats(unit_stats, unit_errors=1)
    check("unit_errors passed through", got_err["unit_errors"] == 1, str(got_err))
    check("units includes errored units", got_err["units"] == 3, str(got_err))
    # totals unaffected by an errored unit not present in unit_stats
    check("tables_matched unaffected by unit_errors",
          got_err["tables_matched"] == 3, str(got_err))


def test_source_file_is_bare_canonical_key():
    # a materialized PDF's ingest_status key must be the bare canonical key,
    # NOT a findociq/data/sources/... relpath.
    p = ss.SOURCES_ROOT / "financial_statements" / "DBS_1Q25_trading_update.pdf"
    assert ss.key_for(p) == "financial_statements/DBS_1Q25_trading_update.pdf"
    assert doc_id_for(p) == "DBS_1Q25_trading_update"


def test_defer_db_steps_default_and_dispatch(monkeypatch) -> None:
    print("--defer-db-steps / --db-steps-only — CLI defaults + dispatch precedence")

    # default False = today's behaviour exactly unchanged; --pdf still routes to
    # run_one, and it sees defer_db_steps=False.
    seen = {}
    monkeypatch.setattr(run_doc, "run_one", lambda args: seen.setdefault("run_one", args))
    run_doc.main(["--pdf", "some.pdf"])
    check("--defer-db-steps defaults to False",
          seen["run_one"].defer_db_steps is False)

    # flag flows through to run_one's Namespace unchanged.
    seen.clear()
    run_doc.main(["--pdf", "some.pdf", "--defer-db-steps"])
    check("--defer-db-steps=True reaches run_one",
          seen["run_one"].defer_db_steps is True)

    # --db-steps-only ignores --pdf entirely and dispatches to run_db_steps_only,
    # never run_one — same "alternative entrypoint" pattern as --rebuild-db/--all.
    monkeypatch.setattr(run_doc, "run_db_steps_only",
                        lambda args: seen.setdefault("db_steps_only", args))
    seen.clear()
    run_doc.main(["--db-steps-only"])
    check("--db-steps-only needs no --pdf", "db_steps_only" in seen)
    check("--db-steps-only does not call run_one", "run_one" not in seen)

    # --db-steps-only wins even if --pdf is also given (mutually exclusive
    # entrypoint precedence, checked first in main()).
    seen.clear()
    run_doc.main(["--pdf", "some.pdf", "--db-steps-only"])
    check("--db-steps-only takes precedence over --pdf",
          "db_steps_only" in seen and "run_one" not in seen)


def test_run_db_steps_only_order(monkeypatch) -> None:
    print("run_db_steps_only — whole-DB step order (registry -> sync_bq), "
          "never reordered")
    calls = []
    # step3b_registry MUST be mocked too, same as every other whole-DB step --
    # left real, this test called the actual seed_registry.py subprocess
    # against run_doc.DEFAULT_DB (the LIVE db), a real mutation caught only by
    # an independent DB-hash check after a routine test run, not by this test
    # itself. Never leave a new whole-DB step unmocked here.
    monkeypatch.setattr(run_doc, "step3b_registry",
                        lambda db: calls.append("registry"))
    monkeypatch.setattr(run_doc, "step7_sync_bq",
                        lambda db: calls.append("sync_bq") or 0)
    monkeypatch.setattr(run_doc, "db_counts",
                        lambda db, doc_id=None: {"sections": 0, "tables": 0,
                                                  "rows": 0, "cells": 0})

    import argparse
    args = argparse.Namespace(db=str(run_doc.DEFAULT_DB), ipv4_shim=True,
                              no_sync_bq=False)
    run_doc.run_db_steps_only(args)
    # The concept stages (4a/4b/4c) that used to sit between registry and
    # sync_bq were retired 2026-08-12 with pipeline/concept/.
    check("order is registry, sync_bq",
          calls == ["registry", "sync_bq"], str(calls))

    calls.clear()
    args.no_sync_bq = True
    run_doc.run_db_steps_only(args)
    check("--no-sync-bq skips sync_bq", calls == ["registry"], str(calls))


def test_verify_failure_records_status_and_exits_loud(monkeypatch) -> None:
    print("verify_with_reextract — a deliberately-failing verify (D29): non-zero "
          "exit, a persisted ingest_status record naming the skipped stages, and "
          "a loud stderr message. No PDF/DB/network -- an in-memory schema_v7 db "
          "and monkeypatched re-extract loop only.")
    import io
    import sqlite3
    import tempfile
    from pathlib import Path as _Path

    tmp_dir = tempfile.mkdtemp(prefix="test_run_doc_verify_fail_")
    db_path = _Path(tmp_dir) / "test.db"
    con = sqlite3.connect(db_path)
    con.executescript(run_doc.SCHEMA_V7.read_text())
    con.commit()
    con.close()

    doc_id = "BANK_9Q99_deliberately_failing"
    source_file = "financial_statements/BANK_9Q99_deliberately_failing.pdf"

    # A report that never clears, however many re-extract rounds run: 2 tables,
    # 2 total missing cell values between them (one carrying a currency-prefixed
    # raw value, unrelated to this test but realistic).
    failing_report = {
        "doc_id": doc_id,
        "tables": [
            {"table_id": "t1", "rows_failed": 1,
             "values_missing": [{"row_id": 1, "missing_value": 2.1, "value_raw": "S$2.1b"}]},
            {"table_id": "t2", "rows_failed": 0,
             "values_missing": [{"row_id": 2, "missing_value": 450.0, "value_raw": "US$450m"}]},
        ],
    }

    monkeypatch.setattr(run_doc, "find_audit_root", lambda doc_id: None)
    monkeypatch.setattr(run_doc, "verify_doc_report", lambda db, doc_id, pdf: failing_report)
    monkeypatch.setattr(run_doc, "sections_for_tables", lambda db, doc_id, table_ids: [])
    monkeypatch.setattr(run_doc, "step2_extract", lambda *a, **k: None)
    monkeypatch.setattr(run_doc, "step2b_geometry", lambda doc_id: None)
    monkeypatch.setattr(run_doc, "load_doc", lambda db, doc_id, audit_root: None)

    captured_stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_stderr)

    raised = None
    try:
        run_doc.verify_with_reextract(
            db_path, doc_id, _Path("/nonexistent.pdf"), _Path("/nonexistent_toc.json"),
            batch=False, shim=False, max_rounds=2, family="fs", source_file=source_file)
    except SystemExit as e:
        raised = e

    check("(a) non-zero exit", raised is not None and raised.code == 1,
          str(raised.code) if raised else "no SystemExit raised")
    check("exit is flagged pre-recorded (so run_one's generic handler won't "
          "clobber the detailed record with a bare error string)",
          raised is not None and getattr(raised, "ingest_status_recorded", False) is True)

    stderr_text = captured_stderr.getvalue()
    check("(c) stderr message printed at all", "VERIFY FAILED" in stderr_text, stderr_text)
    check("stderr names the doc_id", doc_id in stderr_text, stderr_text)
    check("stderr names the skipped downstream stages",
          "xlsx" in stderr_text and "sync_bq" in stderr_text, stderr_text)

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT doc_id, stage, state, error_message FROM ingest_status WHERE source_file=?",
        (source_file,)).fetchone()
    con.close()

    check("(b) ingest_status row persisted", row is not None)
    if row:
        got_doc_id, stage, state, error_message = row
        check("persisted doc_id matches", got_doc_id == doc_id, got_doc_id)
        check("persisted stage is 'verify' (the stage that failed)", stage == "verify", stage)
        check("persisted state is 'failed'", state == "failed", state)
        check("persisted error names the cell-failure count (2 across the two tables)",
              "2 cell" in error_message, error_message)
        check("persisted error names the skipped downstream stages (xlsx, sync_bq)",
              "xlsx" in error_message and "sync_bq" in error_message, error_message)


def main() -> int:
    test_infer_period()
    test_unit_from_meta()
    test_failing_table_ids()
    test_aggregate_geometry_stats()
    test_source_file_is_bare_canonical_key()

    class _MP:
        """Minimal monkeypatch-alike for the plain __main__ runner (pytest
        supplies the real fixture when run under pytest)."""
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    mp = _MP()
    try:
        test_defer_db_steps_default_and_dispatch(mp)
    finally:
        mp.undo()

    mp2 = _MP()
    try:
        test_run_db_steps_only_order(mp2)
    finally:
        mp2.undo()

    mp3 = _MP()
    try:
        test_verify_failure_records_status_and_exits_loud(mp3)
    finally:
        mp3.undo()

    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
