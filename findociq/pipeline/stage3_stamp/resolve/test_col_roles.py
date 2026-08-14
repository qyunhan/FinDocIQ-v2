"""Tests for col_roles.role_for — the col_role vocabulary.

The reference_skip rule exists because OCBC's income statement prints a `Note`
column whose cells inherited the table's period and became indistinguishable
from the real figure at the (leaf, period, span) grain. Net interest income
rendered as 3 instead of 4,486. These pin both halves: it must claim the
reference column, and it must NOT claim a line item that merely starts with
the same letters.
"""
from __future__ import annotations

import pytest

from col_roles import DERIVED_COL_RX, REFERENCE_COL_RX, role_for  # noqa: F401


@pytest.mark.parametrize("label", [
    "Note", "Notes", "NOTE", "note",
    "Note 1", "Note (a)", "Note no.", "Notes*", " Note ", "Ref.", "Reference",
])
def test_reference_columns_are_skipped(label):
    assert role_for(label) == "reference_skip"


@pytest.mark.parametrize("label", [
    # Real line items / value columns that a loose \bnote\b would have claimed.
    "Notes receivable", "Notes and coins", "Note to the financial statements",
    "Reference rate", "Denoted",
    # Period and hard-axis value columns.
    "1H 2026", "1H26", "2025", "Singapore", "Global Markets", "Group", "Bank",
    "Insurance", "Others", "Total",
    # Unit-only headers.
    "$m", "S$m", "%",
])
def test_value_columns_keep_no_role(label):
    assert role_for(label) is None


@pytest.mark.parametrize("label", ["% chg", "+/(-)%", "Change", "variance", "vs"])
def test_derived_columns_still_win(label):
    assert role_for(label) == "derived_skip"


def test_derived_is_tested_before_reference():
    # A header that reads as both is a restatement first — order is fixed so
    # the loader and restamp_columns can never disagree.
    assert role_for("Note % chg") == "derived_skip"


@pytest.mark.parametrize("label", [None, "", "   "])
def test_blank_labels_earn_nothing(label):
    assert role_for(label) is None


def test_role_for_accepts_non_string_labels():
    # col_leaf_label arrives from sqlite; a numeric header is possible.
    assert role_for(2026) is None
