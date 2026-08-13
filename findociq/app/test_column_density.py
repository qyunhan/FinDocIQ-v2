"""Minimum column density — a period column has to carry a real share of the
grid to earn its header.

A vintage contributing two of twenty-six lines reads as "we have this period"
when what we have is a fragment of it. DBS's 4Q24 and 3Q25 columns were three
per-share rows and whitespace.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from findociq_app import MIN_COLUMN_DENSITY, period_axis_order   # noqa: E402


def _rows(period, span, labels):
    return [{"period": period, "period_span": span, "label": lb}
            for lb in labels]


# 10 items in the config; 1H26 carries 5 of them, 4Q24 carries 1.
DENSE = _rows("2026-06-30", "1H", [f"item{i}" for i in range(5)])
SPARSE = _rows("2024-12-31", "4Q", ["item0"])
TOTAL_ITEMS = 10


def test_column_above_threshold_is_shown():
    """5/10 = 0.50, comfortably over the 0.20 bar."""
    assert period_axis_order(DENSE + SPARSE, total_items=TOTAL_ITEMS) == ["1H26"]
    assert "1H26" in period_axis_order(DENSE, total_items=TOTAL_ITEMS)


def test_column_below_threshold_is_hidden():
    """1/10 = 0.10, under the bar -- and it is the COLUMN that goes, not the
    anchor rows: the same call with the rule off still returns it."""
    assert period_axis_order(SPARSE, total_items=TOTAL_ITEMS) == []
    assert period_axis_order(SPARSE) == ["4Q24"]
    assert period_axis_order(DENSE + SPARSE) == ["4Q24", "1H26"]


def test_threshold_is_configurable():
    """Raising the bar drops a column that cleared the default; lowering it
    admits one that did not."""
    assert period_axis_order(DENSE + SPARSE, total_items=TOTAL_ITEMS,
                             min_density=0.05) == ["4Q24", "1H26"]
    assert period_axis_order(DENSE + SPARSE, total_items=TOTAL_ITEMS,
                             min_density=0.75) == []
    # Exactly at the bar counts as meeting it.
    assert period_axis_order(_rows("2026-06-30", "1H", ["a", "b"]),
                             total_items=TOTAL_ITEMS,
                             min_density=0.20) == ["1H26"]
    assert MIN_COLUMN_DENSITY == 0.20


def test_rule_is_off_without_total_items():
    """Every caller that just wants chronological order passes nothing and
    gets every period, unchanged."""
    assert period_axis_order(DENSE + SPARSE) == ["4Q24", "1H26"]
    assert period_axis_order(DENSE + SPARSE, total_items=None) == ["4Q24", "1H26"]
    assert period_axis_order(DENSE + SPARSE, total_items=0) == ["4Q24", "1H26"]


def test_density_counts_distinct_items_not_rows():
    """A multi-member formula line emits one row per member. Counting rows
    would let a single line vote several times and push its own period past
    the bar."""
    repeated = _rows("2024-12-31", "4Q", ["item0"] * 8)
    assert period_axis_order(repeated, total_items=TOTAL_ITEMS) == []


def test_chronological_order_survives_the_filter():
    """Pruning must not reorder what is left."""
    rows = (_rows("2025-06-30", "1H", [f"i{n}" for n in range(4)])
            + _rows("2025-12-31", "FY", [f"i{n}" for n in range(4)])
            + _rows("2024-12-31", "FY", ["i0"]))
    assert period_axis_order(rows, total_items=TOTAL_ITEMS) == ["1H25", "FY25"]
