"""format_highlight_value — ONE precision rule for every unit.

Split out of test_findociq_app.py because the rule changed shape: precision
used to be selected by `unit_hint`, and is now a property of the VALUE. The
tests that pinned the per-unit branches live on in test_findociq_app.py where
they still hold (thousands separator, derived marker, None/NaN); what is
pinned here is the part that is new — that the unit can no longer change the
answer.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from findociq_app import format_highlight_value      # noqa: E402


# ------------------------------------------------- S$m no longer rounds to int
def test_s_dollar_m_does_not_round_to_integer():
    """The old `S$m` branch was `:,.0f`. OCBC files its per-share lines inside
    a table whose modal unit is S$m, so EPS 0.81 rendered `1` and NAV 13.73
    rendered `14` -- a wrong number, not a rounded one."""
    assert format_highlight_value(0.81, "S$m") == "0.81"
    assert format_highlight_value(13.73, "S$m") == "13.73"
    assert format_highlight_value(1.67, "S$m") == "1.67"
    # Whole S$m amounts still read as whole amounts -- no invented '.00' tail.
    assert format_highlight_value(5000.0, "S$m") == "5,000"
    assert format_highlight_value(897488.0, "S$m") == "897,488"


def test_unit_hint_never_changes_the_result():
    """The defect was a formatter whose correctness depended on a DIFFERENT
    column being right. The same value formats identically whatever unit it
    arrives with -- including None and an unknown unit."""
    for unit in ("S$m", "%", "per_share", "", None, "unknown-unit"):
        assert format_highlight_value(13.73, unit) == "13.73", unit
        assert format_highlight_value(29.99, unit) == "29.99", unit
        assert format_highlight_value(0.81, unit) == "0.81", unit


# ---------------------------------------------------- source precision, capped
def test_renders_at_source_precision():
    """The three figures named in the acceptance check, plus the DBS/UOB
    per-share values that were already correct and must stay so."""
    assert format_highlight_value(0.81, "S$m") == "0.81"        # OCBC EPS basic
    assert format_highlight_value(13.73, "S$m") == "13.73"      # OCBC NAV
    assert format_highlight_value(29.99, "%") == "29.99"        # UOB NAV
    assert format_highlight_value(24.29, "per_share") == "24.29"  # DBS NAV
    assert format_highlight_value(3.46, "%") == "3.46"          # UOB EPS basic
    # Fewer decimals than the cap are not padded up to it.
    assert format_highlight_value(3.7, "S$m") == "3.7"
    assert format_highlight_value(4.0, "S$m") == "4"


def test_caps_at_two_decimals():
    assert format_highlight_value(1.234, "S$m") == "1.23"
    assert format_highlight_value(12.345, "%") == "12.35"
    assert format_highlight_value(0.987654, "per_share") == "0.99"
    # Float representation error is absorbed by rounding, not printed.
    assert format_highlight_value(0.1 + 0.2, "S$m") == "0.3"


def test_negative_and_derived_still_render():
    """OCBC prints its expense lines negative; the sign survives the cap, and
    the derived marker is still appended after it."""
    assert format_highlight_value(-3023.0, "S$m") == "-3,023"
    assert format_highlight_value(-0.5, "S$m") == "-0.5"
    assert format_highlight_value(1.234, "S$m", is_derived=True) == "1.23 ᵈ"


def test_none_and_nan_still_blank():
    assert format_highlight_value(None, "S$m") == ""
    assert format_highlight_value(float("nan"), "S$m") == ""
