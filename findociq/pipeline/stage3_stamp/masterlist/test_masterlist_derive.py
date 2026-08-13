"""Tests for masterlist_derive — the shared row-derivation module.

Pins the contract v3 depends on. The three normalisation rules are tested for
INDEPENDENCE, not just for their outputs: the v2 defect was one rule silently
doing all three jobs (a trailing-digit footnote strip that also ate years and
therefore destroyed dates before they could be classified), which produced the
right answer for 'Balance at 1 January 2024' and the wrong one for '31 Dec 2024'.

Run:  python -m pytest findociq/pipeline/mapping/test_masterlist_derive.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path

from stage3_stamp.masterlist import masterlist_derive as D  # noqa: E402


def _row(rid, label, level, parent=None, values=True):
    return D.Row(row_id=rid, label=label, level=level, parent=parent,
                 has_values=values)


# ===========================================================================
# THE THREE RULES, AND THAT THEY DO NOT MOONLIGHT
# ===========================================================================
def test_rule1_strips_footnote_markers_only():
    assert D.normalize_segment("ECL¹ Stage 1 and 2 (GP)") == "ecl_stage_1_and_2_gp"
    assert D.normalize_segment("Provision for CSR¹") == "provision_for_csr"
    assert D.normalize_segment("Others2") == "others"
    assert D.normalize_segment("Net book value5") == "net_book_value"


def test_rule1_never_touches_a_four_digit_year():
    """The guard that keeps rule 1 out of rule 2 and 3's territory. Without the
    `(?<!\\d)` lookbehind the engine eats '2024' two digits at a time."""
    for s in ("31 Dec 2024", "31 Dec 2025", "Year 2025", "2025"):
        assert D.strip_footnote_markers(s) == s, s


def test_rule2_strips_the_year_and_keeps_the_printed_line_identity():
    # day and month are identity (opening vs closing balance); the year is period
    assert D.normalize_segment("Balance at 1 January 2024") == "balance_at_1_january"
    assert D.normalize_segment("Balance at 31 December 2024") == "balance_at_31_december"
    assert D.normalize_segment("Net profit") == "net_profit"


def test_rule2_gives_vintage_stability():
    assert (D.leaf_id([], "Balance at 1 January 2024")
            == D.leaf_id([], "Balance at 1 January 2025"))


def test_rule3_classifies_whole_period_labels_only():
    for s in ("31 Dec 2024", "30 Jun 2025", "4th Qtr 2025", "1Q25", "2H25",
              "Year 2025", "2025", "As at 31 Dec 2025", "December 2025",
              "4th Qtr 2025¹", "2nd Half 2025"):
        assert D.is_period_label(s), s
    for s in ("By currency and product", "Total assets", "Net profit",
              "Others2", "Balance at 1 January 2024"):
        assert not D.is_period_label(s), s


# ===========================================================================
# CLASSIFICATION
# ===========================================================================
def test_valueless_date_row_is_a_period_banner():
    rows = [_row(1, "31 Dec 2025", 0, values=False), _row(2, "Total assets", 1, 1)]
    D.classify(rows, None)
    assert rows[0].cls == D.PERIOD_BANNER
    assert rows[1].cls == D.DATA


def test_valued_date_row_is_a_period_row_not_data():
    """UOB/OCBC print 'Dec-25' as a VALUED leaf row under a geography. It is
    still period data — it must not become 'singapore::31_dec'."""
    rows = [_row(1, "Singapore", 0), _row(2, "31 Dec 2025", 1, 1)]
    D.classify(rows, None)
    assert rows[1].cls == D.PERIOD_ROW


def test_caption_echo_row_is_dropped():
    rows = [_row(1, "Selected income statement items ($m)", 0, values=False),
            _row(2, "Total income", 1, 1)]
    D.classify(rows, D.normalize_caption("Selected income statement items"))
    assert rows[0].cls == D.SECTION_HEADER


# ===========================================================================
# ANCESTRY — captured chain is the base, banner repairs orphans
# ===========================================================================
def test_captured_chain_is_the_base_hierarchy():
    rows = [_row(1, "Total income", 0), _row(2, "Of which: Net interest income", 1, 1)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    assert D.leaf_id(rows[1].ancestors, rows[1].identity_label) == \
        "total_income::of_which::net_interest_income"


def test_period_row_borrows_its_parents_identity():
    """The G2 fix: 'Singapore' at two dates is ONE leaf with two periods."""
    rows = [_row(1, "Singapore", 0), _row(2, "31 Dec 2025", 1, 1),
            _row(3, "31 Dec 2024", 1, 1)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    ids = {D.leaf_id(r.ancestors, r.identity_label) for r in rows if r.identity_label}
    assert ids == {"singapore"}, ids
    assert rows[1].period_banner and rows[2].period_banner


def test_banner_absorbs_level_drift_across_vintages():
    """Rule 3's reason for existing. 2Q25 prints currency rows at level 0 under
    the banner, 4Q25 at level 1; both must yield the same id, no alias."""
    def build(child_level):
        rows = [_row(1, "By currency and product", 0, values=False),
                _row(2, "Singapore dollar", child_level, None)]
        D.classify(rows, None)
        D.build_ancestry(rows)
        return D.leaf_id(rows[1].ancestors, rows[1].identity_label)
    assert build(0) == build(1) == "by_currency_and_product::singapore_dollar"


def test_data_row_never_closes_a_banner():
    rows = [_row(1, "By geography", 0, values=False),
            _row(2, "Singapore", 0, None),
            _row(3, "Malaysia", 0, None)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    for r in rows[1:]:
        assert r.ancestors == ["by_geography"], (r.label, r.ancestors)


def test_ancestor_labels_are_display_text_not_id_segments():
    """full_hierarchy must show what the page printed, so the ghost-ancestor
    gate compares two independently derived things rather than itself."""
    rows = [_row(1, "Commercial book total income", 0),
            _row(2, "Net interest income", 1, 1)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    assert rows[1].ancestor_labels == ["Commercial book total income"]
    assert rows[1].ancestors == ["commercial_book_total_income"]


# ===========================================================================
# CAPTION RESOLUTION
# ===========================================================================
def test_leading_note_number_is_stripped():
    assert D.normalize_caption("3. Net interest income") == "net_interest_income"
    assert D.normalize_caption("9. Share capital") == "share_capital"


def test_breakdown_discriminator_splits_and_becomes_a_banner_segment():
    base, disc = D.split_discriminator(
        "NON-PERFORMING ASSETS (continued) — NPLs by Industry")
    assert D.normalize_caption(base) == "non_performing_assets"
    assert D.discriminator_segment(disc) == "by_industry"


def test_period_tail_is_not_mistaken_for_a_discriminator():
    base, disc = D.split_discriminator(
        "Performance by Business Segment 1 (cont’d) — 1H25")
    assert disc is None
    assert D.normalize_caption(base) == "performance_by_business_segment"


def test_caption_variants_reach_a_multiline_title():
    title = ("AUDITED CONSOLIDATED STATEMENT OF CHANGES IN EQUITY\n"
             "FOR THE YEAR ENDED 31 DECEMBER 2025 — The Group (2024)")
    got = {D.normalize_caption(v) for v in D.caption_variants(title)}
    assert "consolidated_statement_of_changes_in_equity" in got or \
           "audited_consolidated_statement_of_changes_in_equity" in got, got


# ===========================================================================
# ID CONSTRUCTION
# ===========================================================================
def test_subtotal_collapse_folds_consecutive_identical_segments():
    assert D.leaf_id(["total", "total"], "Due within 1 year") == \
        "total::due_within_1_year"


def test_of_which_memo_form():
    assert D.leaf_id(["total_income"], "of which: Non-performing assets") == \
        "total_income::of_which::non_performing_assets"


# ===========================================================================
# RULE 3b — 'At <date>' is a banner only when the row carries NO values
# ===========================================================================
def test_rule3b_valueless_at_date_is_a_period_banner_not_an_id_segment():
    """UOB/OCBC head a segment BALANCE block with a valueless 'At 31 December
    2025'. Rule 3 does not match the bare 'At <date>' form, so it used to be a
    BANNER and leaked into the id as `at_31_december::segment_assets`."""
    rows = [_row(1, "At 31 December 2025", 0, values=False),
            _row(2, "Segment assets", 1, 1)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    assert rows[0].cls == D.PERIOD_BANNER
    assert D.leaf_id(rows[1].ancestors, rows[1].identity_label) == "segment_assets"


def test_rule3b_valued_at_date_keeps_its_identity():
    """The other half, and why rule 3 could not simply be widened: a VALUED
    'At 1 January 2025' is the opening balance of a changes-in-equity or Level 3
    roll-forward. Opening and closing must stay two leaves, not collapse."""
    rows = [_row(1, "At 1 January 2025", 0),
            _row(2, "At 31 December 2025", 0)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    assert [r.cls for r in rows] == [D.DATA, D.DATA]
    ids = {D.leaf_id(r.ancestors, r.identity_label or r.label) for r in rows}
    assert ids == {"at_1_january", "at_31_december"}, ids


def test_rule3b_half_year_ended_banner_with_a_footnote_marker():
    """OCBC prints the same segment banner as 'Half year ended 31 December 2025'
    and '... (1)'. Rule 3 deliberately skips the footnote strip; rule 3b applies
    it, because a valueless banner's footnote is never identity."""
    for label in ("Half year ended 31 December 2025",
                  "Half year ended 31 December 2025 (1)"):
        assert D.is_period_banner_label(label), label
        assert not D.is_period_label(label), label


def test_rule3_is_unchanged_by_rule3b():
    """Rule 3 still answers the VALUED-row question, and still says no to the
    bare 'At <date>' form — that asymmetry is the whole point."""
    assert not D.is_period_label("At 30 June 2025")
    assert D.is_period_label("As at 31 December 2025")
    assert not D.is_period_banner_label("Balance at 1 January 2025")


def test_a_dated_balance_line_does_not_parent_its_movements():
    """OCBC indents its changes-in-equity movements under the opening balance,
    so the loader captures 'At 1 January 2024' as their parent. In print they
    are siblings — opening balance, movements, closing balance."""
    rows = [_row(1, "At 1 January 2024", 0),
            _row(2, "Profit for the year", 1, 1),
            _row(3, "Dividends and distributions", 1, 1),
            _row(4, "At 31 December 2024", 0)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    ids = [D.leaf_id(r.ancestors, r.identity_label or r.label) for r in rows]
    assert ids == ["at_1_january", "profit_for_the_year",
                   "dividends_and_distributions", "at_31_december"], ids


def test_the_dated_balance_line_keeps_its_own_leaf_and_its_path():
    """It is dropped as an ANCESTOR, not as a row: both balances stay
    addressable, and full_path loses the prefix in step with the id so the
    masterlist and table_paths() still agree."""
    rows = [_row(1, "At 1 January 2024", 0),
            _row(2, "Profit for the year", 1, 1)]
    D.classify(rows, None)
    D.build_ancestry(rows)
    assert rows[0].cls == D.DATA and rows[0].has_values
    assert rows[1].ancestors == [] and rows[1].ancestor_labels_raw == []
