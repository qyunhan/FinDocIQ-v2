"""Two structural rules for picking a concept's LEVEL (pre-flight E2 follow-up).

1. row_depth tie-break — a statement prints its GRAND TOTAL above its
   SUBTOTALS, so when one concept legitimately matches both, the shallower row
   is the concept's own figure. OCBC's balance sheet has 'Total liabilities'
   (depth 1, 612,118) AND 'Subtotal Liabilities' (depth 2, 502,719); the old
   smallest-magnitude tie-break preferred the SUBTOTAL by construction.

2. a nature='stock' concept never takes its level from a CASH FLOW STATEMENT —
   IAS-7 presents the MOVEMENT in a balance, not the balance ('Loans and
   advances to customers' in DBS's cash flow statement is -23,317 for FY25; the
   closing loan book is 445,011).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.build_fact_metric import (_is_cash_flow_table,  # noqa: E402
                                       _is_stock_from_cash_flow, _resolve_group)


def _m(value, *, table_type="balance_sheet", depth=1, label="row"):
    return {"value_num": value, "table_type": table_type, "row_depth": depth,
            "row_label": label, "period_start": None, "unit": "S$m",
            "unit_promoted": False, "doc_id": "D", "table_id": "T",
            "cell_state": "reported"}


def test_grand_total_beats_subtotal_in_the_same_statement():
    """The OCBC shape: both rows are the same table and the same tier, so the
    old (tier, -support, magnitude) key picked the SMALLER subtotal."""
    members = [_m(612118.0, depth=1, label="Total liabilities"),
               _m(502719.0, depth=2, label="Subtotal Liabilities")]
    res = _resolve_group(members, "bs.liabilities.total")
    assert res["value_num"] == 612118.0, res["value_num"]
    assert res["source_row_label"] == "Total liabilities"


def test_depth_outranks_support():
    """A grand total printed ONCE still beats a subtotal printed twice --
    otherwise a breakdown block out-votes the total it sums to."""
    members = [_m(612118.0, depth=1, label="Total liabilities"),
               _m(502719.0, depth=2), _m(502719.0, depth=2)]
    assert _resolve_group(members, "bs.liabilities.total")["value_num"] == 612118.0


def test_equal_depth_keeps_the_previous_behaviour():
    """When depth cannot discriminate, resolution is unchanged: most-supported
    value wins. (DBS's CASA rows are all depth 2 -- the rule must not fire.)"""
    members = [_m(225514.0, depth=2), _m(225514.0, depth=2), _m(1773.0, depth=2)]
    assert _resolve_group(members, "bs.liabilities.deposits_casa")["value_num"] == 225514.0


def test_cash_flow_table_detected_from_either_signal():
    # declared (registry statement_class)
    assert _is_cash_flow_table("some_unclassified_slug", "cash_flow")
    # raw slug, for an exhibit the registry has not classified
    assert _is_cash_flow_table("audited_consolidated_cash_flow_statement", None)
    assert _is_cash_flow_table("consolidated_cashflow", None)
    assert not _is_cash_flow_table("balance_sheet", "balance_sheet")
    assert not _is_cash_flow_table("income_statement", "income_statement")


def test_stock_concept_is_excluded_from_a_cash_flow_statement_only():
    stock, flow = {"nature": "stock"}, {"nature": "flow"}
    cf = "audited_consolidated_cash_flow_statement"
    assert _is_stock_from_cash_flow(stock, cf, "cash_flow")
    # a FLOW concept is legitimately reported there -- must NOT be excluded,
    # otherwise this becomes a blanket exclusion of the whole exhibit
    assert not _is_stock_from_cash_flow(flow, cf, "cash_flow")
    # and a stock concept elsewhere is untouched
    assert not _is_stock_from_cash_flow(stock, "balance_sheet", "balance_sheet")
    # a ratio concept is not a stock -- unaffected
    assert not _is_stock_from_cash_flow({"nature": "ratio_point"}, cf, "cash_flow")


def test_cash_flow_movement_cannot_win_a_stock_concept_via_depth():
    """Belt-and-braces: even if the movement row were SHALLOWER than the
    balance row, the nature rule removes it before resolution sees it, so the
    fix does not depend on the cash flow line happening to sit deeper."""
    assert _is_stock_from_cash_flow(
        {"nature": "stock"}, "audited_consolidated_cash_flow_statement", None)


if __name__ == "__main__":
    for t in (test_grand_total_beats_subtotal_in_the_same_statement,
              test_depth_outranks_support,
              test_equal_depth_keeps_the_previous_behaviour,
              test_cash_flow_table_detected_from_either_signal,
              test_stock_concept_is_excluded_from_a_cash_flow_statement_only,
              test_cash_flow_movement_cannot_win_a_stock_concept_via_depth):
        t()
        print(f"  [PASS] {t.__name__}")
    print("ALL PASS")
