"""normalize_exhibit_title — a title must not carry its POSITION in the document.

Two classes of drift that fragmented `table_registry_alias` keys (pre-flight A4):

  * hierarchical note numbering — '13.2 Geographical segments' one quarter,
    '14.2 Geographical segments' the next once a note is inserted above it;
    Pillar-3 uses lettered forms ('A.3', 'A.6.1'). Only the flat form
    ('10. Deposits') was stripped, so the numbered exhibits went UNCLASSIFIED.

  * spelled-out period qualifiers — OCBC titles the SAME income summary
    'First Half 2025 Performance', 'Second Quarter 2025 Performance',
    'Nine Months 2025 Performance', 'Full Year 2025 Performance'. Only the YEAR
    was stripped, so one exhibit produced a different alias key every quarter.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
from stage3_stamp.resolve.normalize import normalize_exhibit_title as N  # noqa: E402


def test_hierarchical_and_lettered_numbering_is_stripped():
    assert N("13.2 Geographical segments") == "geographical_segments"
    assert N("14.2 Geographical segments") == "geographical_segments", "renumbering must not fragment"
    assert N("13.1 Business segments") == "business_segments"
    assert N("A.3 Overview of key prudential regulatory metrics") == \
        "overview_of_key_prudential_regulatory_metrics"
    assert N("A.6.1 IRBA RWA flow statement") == "irba_rwa_flow_statement"
    # the flat forms that already worked must keep working
    assert N("10. Deposits and balances") == "deposits_and_balances"
    assert N("5 Classification of Financial Assets") == "classification_of_financial_assets"
    assert N("4. Fees and commissions (net)") == "fees_and_commissions_net"


def test_numbering_strip_does_not_eat_meaningful_leading_tokens():
    # a compact period token opening a title is period noise, handled by the
    # period vocabulary — the numbering rule must not be what consumes it, and
    # must never bite into the identifying words
    assert N("1Q25 key financial indicators") == "key_financial_indicators"
    assert N("4Q25 performance highlights") == "performance_highlights"
    # an abbreviation is not section numbering (no digits follow the letter)
    assert N("e.g. nothing") == "e_g_nothing"
    # digits that are part of the identity survive
    assert N("Tier 1 capital") == "tier_1_capital"


def test_spelled_out_period_qualifiers_collapse_to_one_key():
    same = {N(t) for t in (
        "First Half 2025 Performance", "Second Quarter 2025 Performance",
        "Third Quarter 2025 Performance", "Fourth Quarter 2025 Performance",
        "First Quarter 2025 Performance", "Nine Months 2025 Performance",
        "Full Year 2025 Performance", "9M25 Year-on-Year Performance")}
    assert same == {"performance"}, same


def test_dimensional_qualifiers_are_still_preserved():
    """The period strip must not reach into the words that say WHAT the exhibit
    is — merging these would stamp geography cuts as group totals."""
    assert N("Performance by geography") == "performance_by_geography"
    assert N("Performance by Business Segments") == "performance_by_business_segments"
    assert N("Selected income statement items") == "selected_income_statement_items"
    assert N("Statement of comprehensive income") == "statement_of_comprehensive_income"
    assert N("Financial performance") == "financial_performance"
    # near-misses that must not collapse into each other
    assert N("Statement of changes in equity — The Group") != \
        N("Statement of changes in equity — The Company")


if __name__ == "__main__":
    for t in (test_hierarchical_and_lettered_numbering_is_stripped,
              test_numbering_strip_does_not_eat_meaningful_leading_tokens,
              test_spelled_out_period_qualifiers_collapse_to_one_key,
              test_dimensional_qualifiers_are_still_preserved):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
