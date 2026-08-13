"""Tests for the mapping layer (steps 1-4 of docs/specs/MAPPING_LAYER.md §4).

Pure/offline: builds a tiny in-memory DB, no Gemini, no network.

    python3 findociq/pipeline/mapping/test_mapping.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stage3_stamp.resolve.normalize import normalize_exhibit_title as N, normalize_row_label as R  # noqa: E402
from stage3_stamp.resolve.registry import exhibit_aliases, resolve_table_type  # noqa: E402

_fail = 0


def check(name, cond, detail=""):
    global _fail
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        _fail += 1


print("normalize_exhibit_title — period / footnote / unit noise is stripped")
check("period clause", N("BALANCE SHEETS\nAs at 31 December 2025") == "balance_sheets", N("BALANCE SHEETS\nAs at 31 December 2025"))
check("year-ended clause", N("Income Statement (Audited) for the financial year ended 31 December 2025") == "income_statement")
check("half-year token", N("Selected income statement items 1st Half 2025") == "selected_income_statement_items")
check("quarter token", N("Financial Highlights 1Q25") == "financial_highlights")
check("continued", N("FINANCIAL HIGHLIGHTS (continued)") == "financial_highlights")
check("cont'd", N("Financial Highlights (cont'd)") == "financial_highlights")
check("unit parenthetical", N("Selected income statement items ($m)") == "selected_income_statement_items")
check("OCBC n/ footnote", N("Credit costs (bps) 1/") == "credit_costs_bps", N("Credit costs (bps) 1/"))
check("trailing bare footnote", N("Performance by Business Segment 1 - 2025") == "performance_by_business_segment")
check("consolidated stripped", N("AUDITED CONSOLIDATED INCOME STATEMENT") == "income_statement")

print("\nnormalize_exhibit_title — identity-bearing content SURVIVES")
check("income != comprehensive income",
      N("AUDITED CONSOLIDATED INCOME STATEMENT") != N("AUDITED CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME"))
check("by_geography preserved",
      N("PERFORMANCE BY GEOGRAPHY\nSelected income statement items") ==
      "performance_by_geography_selected_income_statement_items")
check("geography variant != plain variant",
      N("PERFORMANCE BY GEOGRAPHY\nSelected income statement items") != N("Selected income statement items ($m)"))
check("group != company",
      N("STATEMENT OF CHANGES IN EQUITY\nThe Group") != N("STATEMENT OF CHANGES IN EQUITY\nThe Company"))

print("\nnormalize_row_label — footnotes go, MEANINGFUL digits stay")
check("footnote glued", R("Return on equity4,5") == "return_on_equity")
check("footnote glued 2", R("Net book value5") == "net_book_value")
check("ECL Stage 3 keeps its 3", R("ECL Stage 3 (SP)") == "ecl_stage_3_sp", R("ECL Stage 3 (SP)"))
check("ECL Stage 1 and 2 keeps digits", R("ECL Stage 1 and 2 (GP)") == "ecl_stage_1_and_2_gp", R("ECL Stage 1 and 2 (GP)"))
check("CET-1 keeps its 1", "1" in R("Common Equity Tier 1 (CET-1) ratio"))
check("'Of which' survives — it is the group-NII distinguisher",
      R("Of which: Net interest income") == "of_which_net_interest_income")
check("of-which != bare label", R("Of which: Net interest income") != R("Net interest income"))

# REGRESSION: NFKC maps '¹' -> '1'. Folding before stripping turned a footnote
# marker into a digit, so the SAME UOB line drifted between quarters:
#   "Shareholders' equity ¹" -> shareholders_equity_1   (4Q25)
#   "Shareholders' equity"   -> shareholders_equity     (another quarter)
# Superscripts must be removed BEFORE unicode folding.
check("superscript footnote stripped, not digitised", R("Shareholders' equity ¹") == "shareholders_equity", R("Shareholders' equity ¹"))
check("marked and unmarked forms agree", R("Total income ¹") == R("Total income"))
check("superscript in ratio label", R("NPL ratio ³") == "npl_ratio", R("NPL ratio ³"))
check("...but Tier 1 keeps its ordinary digit", R("Common Equity Tier 1 (CET-1) ratio") == "common_equity_tier_1_cet_1_ratio")

print("\nexhibit_aliases — section is tried BEFORE title")
al = exhibit_aliases("Performance Ratios", "FINANCIAL HIGHLIGHTS (continued)")
check("composite first", al[0] == "performance_ratios__financial_highlights", str(al))
check("section before title", al.index("performance_ratios") < al.index("financial_highlights"), str(al))
check("identical section+title collapses to one", exhibit_aliases("Customer Loans", "Customer Loans") == ["customer_loans"])

print("\nresolve_table_type — bank-specific beats '*', miss returns None (never a guess)")
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE table_registry_alias (alias_norm TEXT, bank TEXT, table_type_id TEXT, source TEXT, added_at TEXT, PRIMARY KEY(alias_norm,bank))")
con.executemany("INSERT INTO table_registry_alias VALUES (?,?,?,'seed','now')", [
    ("performance_ratios", "*", "FS_RATIOS_KEY"),
    ("selected_income_statement_items", "*", "FS_INCOME_SELECTED"),
    ("financial_highlights", "*", "FS_HIGHLIGHTS_COMBINED"),
    ("customer_loans", "UOB", "FS_CUSTOMER_LOANS_UOB"),
    ("customer_loans", "*", "FS_CUSTOMER_LOANS"),
])
tt, _ = resolve_table_type(con, "OCBC", "Performance Ratios", "FINANCIAL HIGHLIGHTS (continued)")
check("OCBC page-header table resolves via SECTION, not the header", tt == "FS_RATIOS_KEY", str(tt))
tt, _ = resolve_table_type(con, "DBS", "Overview", "Selected income statement items ($m)")
check("DBS resolves via TITLE when section is unseeded", tt == "FS_INCOME_SELECTED", str(tt))
tt, _ = resolve_table_type(con, "UOB", "Financial Highlights", "Financial Highlights")
check("UOB combined highlights resolves", tt == "FS_HIGHLIGHTS_COMBINED", str(tt))
tt, _ = resolve_table_type(con, "UOB", "Customer Loans", "Customer Loans")
check("bank-specific alias beats '*'", tt == "FS_CUSTOMER_LOANS_UOB", str(tt))
tt, _ = resolve_table_type(con, "DBS", "Customer Loans", "Customer Loans")
check("other banks fall back to '*'", tt == "FS_CUSTOMER_LOANS", str(tt))
tt, alias = resolve_table_type(con, "DBS", "Overview", "Some Exhibit Nobody Has Seen")
check("total miss -> UNCLASSIFIED, no fuzzy fallback", tt is None and alias is None, str(tt))


print()
if _fail:
    print(f"{_fail} FAILURES")
    sys.exit(1)
print("all mapping tests pass")
