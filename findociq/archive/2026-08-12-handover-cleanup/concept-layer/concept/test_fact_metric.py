"""Plain check() runner for build_fact_metric.

    python findociq/pipeline/concept/test_fact_metric.py

Unit checks on the canonicalization rules (sign normalization, rounding-twin /
sign-variant collapse, conflict -> punch-list, %-change exclusion vs ratio-level
inclusion, note-column exclusion) plus an integration assertion on the known-good
UOB/OCBC FY2025 values against the live compiled_fs.db.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept import build_fact_metric as B  # noqa: E402

_DB = Path(__file__).resolve().parents[3] / "findociq" / "db" / "compiled_fs.db"

_PASS = 0
_FAIL = 0


def check(name: str, got, want) -> None:
    global _PASS, _FAIL
    ok = got == want
    _PASS += ok
    _FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + ("" if ok else f"  got={got!r} want={want!r}"))


def _row(value_num, table_type, *, unit="S$m", col1=None, col2=None,
         doc="D", tid="T", label="row", ps=None, industry_key="IND_TOTAL",
         legal_entity="CONSOLIDATED"):
    return {"institution": "X", "concept_key": "c", "period": "2025-12-31",
            "period_span": "FY", "period_start": ps, "segment_key": "SEG_TOTAL",
            "geo_key": "GLOBAL", "industry_key": industry_key,
            "legal_entity": legal_entity, "value_num": float(value_num), "unit": unit,
            "cell_state": "reported", "table_type": table_type,
            "col_lvl1": col1, "col_lvl2": col2, "doc_id": doc, "table_id": tid,
            "row_label": label}


def test_column_and_level_filters():
    print("column / level filters")
    check("change col +/(-)", B._is_change_col("+/(-) %", None, "financial_highlights"), True)
    check("change col YoY", B._is_change_col("YoY", "+/(-)%", "financial_performance"), True)
    check("change table vol/rate", B._is_change_col("2025 vs 2024", "Net change $m",
          "volume_and_rate_analysis_2025_vs_2024"), True)
    check("real ratio col not change", B._is_change_col("NPL ratio %", None,
          "financial_highlights"), False)
    check("normal value col not change", B._is_change_col("GROUP", None,
          "consolidated_income_statement"), False)
    check("note col lvl2", B._is_note_col("GROUP", "Note"), True)
    check("note col none", B._is_note_col("GROUP", None), False)
    check("company-only excluded", B._is_company_only(
          "audited_statement_of_changes_in_equity_for_the_year_ended_the_company"), True)
    check("group not company", B._is_company_only(
          "audited_consolidated_statement_of_changes_in_equity_the_group_2025"), False)


def test_family_and_magnitude():
    print("family / magnitude classification")
    check("opex is magnitude", B._is_magnitude("pnl.opex.total"), True)
    check("provisions is magnitude", B._is_magnitude("pnl.provisions.total"), True)
    check("tax is magnitude", B._is_magnitude("pnl.tax"), True)
    check("nii not magnitude", B._is_magnitude("pnl.nii.net"), False)
    check("ratio.cir percent family", B._is_percent_family("ratio.cir", {"unit": "percent"}), True)
    check("credit_cost bps family", B._is_percent_family("ratio.credit_cost_bps", {"unit": "bps"}), True)
    check("nii currency not percent", B._is_percent_family("pnl.nii.net", {"unit": "currency"}), False)


def test_clustering():
    print("magnitude clustering (rounding twins + sign variants)")
    check("rounding twin merges", B._cluster([10933, 10934, 10933]), [[10933.0, 10933.0, 10934.0]])
    check("sign variant merges", len(B._cluster([2042, -2042])), 1)
    check("different values split", len(B._cluster([9150, 3])), 2)
    check("far values split", len(B._cluster([100, 200, 201])), 2)


def test_sign_normalization():
    print("sign normalization")
    # magnitude concept with +/- variants -> positive magnitude
    r = B._resolve_group([_row(2042, "income_statement_audited"),
                          _row(-2042, "performance_by_business_segment_1_2025")],
                         "pnl.provisions.total")
    check("provisions sign -> +", r["value_num"], 2042.0)
    check("provisions single cluster clean", r["conflict"], False)
    # non-magnitude concept: sign from preferred (primary-statement) tier
    r2 = B._resolve_group([_row(6157, "income_statement_audited"),
                           _row(-6157, "performance_by_business_segment_1_2025")],
                          "pnl.opex.total")
    check("opex sign -> +", r2["value_num"], 6157.0)


def test_rounding_twin_collapse():
    print("rounding-twin collapse")
    r = B._resolve_group([_row(9150, "consolidated_income_statement"),
                          _row(9150, "net_interest_income"),
                          _row(9151, "financial_highlights")], "pnl.nii.net")
    check("twin collapse no conflict", r["conflict"], False)
    check("twin collapse n_candidates", r["n_candidates"], 3)
    check("twin resolved_by", r["resolved_by"], "twin_collapse")


def test_note_col_beats_stray():
    print("note-column stray excluded upstream of resolution")
    # simulate the OCBC 'Note'=3 stray being dropped BEFORE grouping: a level
    # fetch would exclude it; here confirm a real conflict without it collapses.
    r = B._resolve_group([_row(9150, "consolidated_income_statement"),
                          _row(9150, "business_segments_year_ended")], "pnl.nii.net")
    check("clean 9150", r["value_num"], 9150.0)
    check("no conflict", r["conflict"], False)


def test_unit_promotion():
    print("percent-family unit promotion (structural: column-unit-declaring)")
    meta_pct = {"unit": "percent"}
    meta_bps = {"unit": "bps"}
    meta_cur = {"unit": "currency"}

    # (a) percent concept, non-column-unit-declaring table, cell stamped with
    # the table's currency default ('S$m') -> PROMOTED to '%'.
    d = B._resolve_cell_unit(meta_pct, "S$m", 0)
    check("promote: kept", d is not None, True)
    check("promote: unit -> %", d[0], "%")
    check("promote: flagged promoted", d[1], True)

    # (b) same concept, column-unit-declaring table (e.g. average_balance_sheet
    # has 'Average rate (%)' / 'Interest ($m)' column units), cell under the
    # currency column -> NOT promoted, stays excluded (column is authoritative).
    d2 = B._resolve_cell_unit(meta_pct, "S$m", 1)
    check("no promote in column-unit-declaring table: dropped", d2, None)

    # (c) bps concept, non-column-unit-declaring table -> promotes to 'bps',
    # not '%'.
    d3 = B._resolve_cell_unit(meta_bps, "S$m", 0)
    check("bps promotes to 'bps'", d3, ("bps", True))

    # (d) a '%' cell is untouched regardless of col_unit_declaring, and is
    # NOT flagged as promoted (unit_source stays 'as_loaded').
    d4 = B._resolve_cell_unit(meta_pct, "%", 0)
    check("as-loaded '%' untouched", d4, ("%", False))
    d5 = B._resolve_cell_unit(meta_pct, "%", 1)
    check("as-loaded '%' untouched (declaring table)", d5, ("%", False))

    # currency concepts are unaffected by promotion (a '%' cell is a change,
    # still dropped; a currency cell is kept as-is regardless of the flag).
    check("currency concept '%' cell still dropped",
          B._resolve_cell_unit(meta_cur, "%", 0), None)
    check("currency concept normal cell kept, not promoted",
          B._resolve_cell_unit(meta_cur, "S$m", 0), ("S$m", False))

    # end-to-end through _resolve_group: unit_source surfaces on the fact row.
    promoted_row = _row(12.3, "uob_performance_highlights", unit="%")
    promoted_row["unit_promoted"] = True
    r = B._resolve_group([promoted_row], "ratio.roe")
    check("resolve_group carries unit_source=dictionary_promoted",
          r["unit_source"], "dictionary_promoted")

    plain_row = _row(1.9, "financial_highlights", unit="%")
    r2 = B._resolve_group([plain_row], "ratio.nim")
    check("resolve_group defaults unit_source=as_loaded",
          r2["unit_source"], "as_loaded")


def test_conflict_to_punchlist():
    print("genuinely-different values -> conflict")
    # two different magnitudes, same authority tier (both SOCE) -> punch-list
    r = B._resolve_group([_row(10933, "consolidated_statement_of_changes_in_equity"),
                          _row(8601, "consolidated_statement_of_changes_in_equity")],
                         "pnl.profit.net_attributable")
    check("conflict flagged", r["conflict"], True)
    check("conflict resolved_by", r["resolved_by"], "conflict")
    # different magnitudes, one strictly-better tier -> resolved by prefer_table
    r2 = B._resolve_group([_row(9150, "consolidated_income_statement"),
                           _row(8000, "performance_by_business_segment_1_2025")],
                          "pnl.nii.net")
    check("prefer_table resolves", r2["conflict"], False)
    check("prefer_table picks tier0", r2["value_num"], 9150.0)
    check("prefer_table resolved_by", r2["resolved_by"], "prefer_table")


def test_legal_entity_grain():
    print("legal_entity is part of the grain (Group vs Bank/Company)")
    # Same table, same everything else, only legal_entity differs -- e.g. the
    # UOB total-assets bug: 572,061 (Group/CONSOLIDATED) and 485,263
    # (Bank/BANK_SOLO) live in the SAME balance_sheet table for the SAME
    # concept/period. They must NOT collapse into one group / one conflict.
    group_row = _row(572061, "statement_of_financial_position",
                     legal_entity="CONSOLIDATED", label="Total assets")
    solo_row = _row(485263, "statement_of_financial_position",
                    legal_entity="BANK_SOLO", label="Total assets")
    check("different group keys", B._group_key(group_row) == B._group_key(solo_row), False)

    fact_rows, conflicts = B._canonicalize([group_row, solo_row], {"c": {"name": "", "thesis": ""}})
    check("two fact rows, not one", len(fact_rows), 2)
    check("no conflicts emitted", len(conflicts), 0)
    by_entity = {r["legal_entity"]: r["value_num"] for r in fact_rows}
    check("CONSOLIDATED keeps 572061", by_entity.get("CONSOLIDATED"), 572061.0)
    check("BANK_SOLO keeps 485263", by_entity.get("BANK_SOLO"), 485263.0)

    # sanity: identical legal_entity still collapses/conflicts as before.
    fact_rows2, conflicts2 = B._canonicalize(
        [_row(572061, "statement_of_financial_position", legal_entity="CONSOLIDATED"),
         _row(485263, "statement_of_financial_position", legal_entity="CONSOLIDATED")],
        {"c": {"name": "", "thesis": ""}})
    check("same-entity divergent values still ONE group", len(fact_rows2), 1)
    check("same-entity divergent values -> conflict", len(conflicts2), 1)


def test_serving_view_and_assertion_live_db():
    print("v_fact_metric_serving + assertion (live compiled_fs.db)")
    if not _DB.exists():
        check("db present", False, True)
        return
    import sqlite3
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(fact_metric)")}
        check("fact_metric has legal_entity column", "legal_entity" in cols, True)
        has_view = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view' "
            "AND name='v_fact_metric_serving'").fetchone()[0]
        check("v_fact_metric_serving exists", has_view, 1)
        if has_view:
            entities = {r[0] for r in con.execute(
                "SELECT DISTINCT legal_entity FROM v_fact_metric_serving")}
            check("serving view is CONSOLIDATED-only", entities <= {"CONSOLIDATED"}, True)

            # period_span='as_at' pins this to the audited balance sheet row
            # specifically (the diagnosed UOB bug); other period_spans for
            # this concept_key legitimately hold different subtotal-table
            # values (e.g. a geo-segment breakdown's None-span row) that are
            # a separate grain group, not a legal_entity duplicate.
            row = con.execute(
                "SELECT value_num FROM v_fact_metric_serving WHERE "
                "institution='United Overseas Bank Ltd' AND concept_key='bs.assets.total' "
                "AND period='2025-12-31' AND period_span='as_at'").fetchone()
            if row is not None:
                check("UOB total assets serves the Group figure", row[0], 572061.0)

            from concept.validate import assert_single_legal_entity_per_group  # noqa: E402
            try:
                assert_single_legal_entity_per_group(con)
                check("assertion passes on live (good) data", True, True)
            except AssertionError as e:
                check(f"assertion passes on live (good) data ({e})", False, True)
    finally:
        con.close()


def test_integration_known_values():
    print("integration: UOB/OCBC FY2025 known-good (live DB)")
    if not _DB.exists():
        check("db present", False, True)
        return
    res = B.build(_DB, dry_run=True)
    # legal_entity is now part of the grain (see test_legal_entity_grain), so
    # a concept can legitimately have both a CONSOLIDATED and a BANK_SOLO/
    # PARENT_COMPANY row here -- the known-good comparisons below are the
    # canonical (dashboard-serving) Group figures, so filter to CONSOLIDATED.
    fact = {(r["institution"][:4], r["concept_key"], r["period_span"]): r
            for r in res["fact_rows"]
            if r["segment_key"] == "SEG_TOTAL" and r["geo_key"] == "GLOBAL"
            and r["period"] == "2025-12-31" and r["legal_entity"] == "CONSOLIDATED"}

    def val(inst4, ck):
        r = fact.get((inst4, ck, "FY"))
        return None if r is None else r["value_num"]

    check("UOB nii.net", val("Unit", "pnl.nii.net"), 9355.0)
    check("UOB provisions.total", val("Unit", "pnl.provisions.total"), 2042.0)
    check("UOB net_attributable", val("Unit", "pnl.profit.net_attributable"), 4682.0)
    check("UOB income.total", val("Unit", "pnl.income.total"), 13808.0)
    check("UOB opex.total", val("Unit", "pnl.opex.total"), 6157.0)
    check("OCBC nii.net (not stray 3)", val("Over", "pnl.nii.net"), 9150.0)


def main() -> int:
    for t in (test_column_and_level_filters, test_family_and_magnitude,
              test_clustering, test_sign_normalization, test_rounding_twin_collapse,
              test_note_col_beats_stray, test_unit_promotion, test_conflict_to_punchlist,
              test_legal_entity_grain, test_serving_view_and_assertion_live_db,
              test_integration_known_values):
        t()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
