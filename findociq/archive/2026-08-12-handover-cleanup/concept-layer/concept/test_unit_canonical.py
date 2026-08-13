"""One canonical unit per concept (pre-flight D1).

`concept_dictionary.yaml`'s `unit:` is a unit KIND ('currency', 'percent',
'per_share', 'bps'); served rows must carry a concrete unit STRING. The two
vocabularies were leaking into one `fact_metric.unit` column, so `ratio.cir`
served 40.4 as '%' AND 0.404 as 'percent' — the same quantity 100x apart behind
two spellings.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.build_fact_metric import (_infer_missing_currency_units,  # noqa: E402
                                       _resolve_cell_unit)
from concept.load_dictionary import canonical_unit, unit_scale  # noqa: E402
from concept.validate import assert_single_unit_per_concept  # noqa: E402


def test_kind_to_string_and_scale():
    assert canonical_unit("percent") == "%"
    assert canonical_unit("bps") == "bps"
    assert canonical_unit("per_share") == "per_share"
    # currency is a property of the DOCUMENT, not the concept — as-loaded wins
    assert canonical_unit("currency") is None
    assert unit_scale("percent") == 100.0
    assert unit_scale("bps") == 10000.0
    assert unit_scale("per_share") == 1.0 and unit_scale("currency") == 1.0
    assert canonical_unit(None) is None and unit_scale(None) == 1.0


def test_percent_family_serves_the_declared_string_not_the_printed_one():
    pct, bps = {"unit": "percent"}, {"unit": "bps"}
    # a '%' cell on a percent concept: already canonical, not "promoted"
    assert _resolve_cell_unit(pct, "%", True) == ("%", False)
    # a '%' cell on a BPS concept is a level, but the served unit is bps:
    # credit costs print in the '%' column of a ratios block (values 6-36)
    assert _resolve_cell_unit(bps, "%", True) == ("bps", True)
    # column authoritative and not '%' -> genuinely not a ratio level, DROP
    # (average_balance_sheet's "Interest ($m)" column on a nim row)
    assert _resolve_cell_unit(pct, "S$m", True) is None
    # table declares no column units -> table default is noise, trust the dict
    assert _resolve_cell_unit(pct, "S$m", False) == ("%", True)


def test_per_share_overrides_a_table_default_but_currency_does_not():
    ps, cur = {"unit": "per_share"}, {"unit": "currency"}
    # 'S$m' here is the table's '($m)' caption bleeding onto a per-share row
    # ('Net asset value per ordinary share ($)', value 29.36) — concept wins
    assert _resolve_cell_unit(ps, "S$m", True) == ("per_share", True)
    assert _resolve_cell_unit(ps, None, True) == ("per_share", True)
    assert _resolve_cell_unit(ps, "per_share", True) == ("per_share", False)
    # currency concepts keep what the document printed
    assert _resolve_cell_unit(cur, "S$m", True) == ("S$m", False)
    assert _resolve_cell_unit(cur, "US$m", True) == ("US$m", False)
    # a '%' cell on a currency concept is a period-over-period change -> DROP
    assert _resolve_cell_unit(cur, "%", True) is None


def test_missing_currency_unit_is_inferred_only_when_unambiguous():
    rows = [
        {"institution": "UOB", "concept_key": "bs.assets.total", "unit": "S$m"},
        {"institution": "UOB", "concept_key": "bs.assets.total", "unit": None},
        # no same-bank evidence, but the concept is unanimous corpus-wide
        {"institution": "DBS", "concept_key": "reg.capital.rwa", "unit": "S$m"},
        {"institution": "UOB", "concept_key": "reg.capital.rwa", "unit": None},
        # genuinely ambiguous -> must REFUSE to guess, leave NULL for the gate
        {"institution": "XYZ", "concept_key": "pnl.income.total", "unit": "S$m"},
        {"institution": "XYZ", "concept_key": "pnl.income.total", "unit": "US$m"},
        {"institution": "XYZ", "concept_key": "pnl.income.total", "unit": None},
    ]
    assert _infer_missing_currency_units(rows) == 2
    assert rows[1]["unit"] == "S$m" and rows[1]["unit_source"] == "inferred_institution"
    assert rows[3]["unit"] == "S$m" and rows[3]["unit_source"] == "inferred_concept"
    assert rows[6]["unit"] is None, "ambiguous currency must not be guessed"


def _serving_db(units: list[tuple[str, str | None]]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE v_fact_metric_serving (concept_key TEXT, unit TEXT)")
    con.executemany("INSERT INTO v_fact_metric_serving VALUES (?,?)", units)
    con.commit()
    return con


def test_gate_passes_on_one_unit_per_concept():
    con = _serving_db([("ratio.cir", "%"), ("ratio.cir", "%"), ("bs.assets.total", "S$m")])
    assert assert_single_unit_per_concept(con) == 2
    con.close()


def test_gate_raises_on_the_D1_shape_and_on_a_null():
    con = _serving_db([("ratio.cir", "%"), ("ratio.cir", "percent")])
    try:
        assert_single_unit_per_concept(con)
        raise SystemExit("gate did not raise on two unit spellings")
    except AssertionError as e:
        assert "ratio.cir" in str(e)
    con.close()
    # NULL counts as a distinct unit: unusable to a consumer, and a bare
    # COUNT(DISTINCT unit) would skip it (this is what hid 152 rows)
    con = _serving_db([("bs.assets.total", "S$m"), ("bs.assets.total", None)])
    try:
        assert_single_unit_per_concept(con)
        raise SystemExit("gate did not raise on a NULL unit")
    except AssertionError as e:
        assert "<null>" in str(e)
    con.close()


if __name__ == "__main__":
    for t in (test_kind_to_string_and_scale,
              test_percent_family_serves_the_declared_string_not_the_printed_one,
              test_per_share_overrides_a_table_default_but_currency_does_not,
              test_missing_currency_unit_is_inferred_only_when_unambiguous,
              test_gate_passes_on_one_unit_per_concept,
              test_gate_raises_on_the_D1_shape_and_on_a_null):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
