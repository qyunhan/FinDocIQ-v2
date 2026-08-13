# DBS 4Q25 — Overview exhibit (pages 4–8), with identity tags

Original table shape as rendered in the Streamlit **Raw table** view
(`raw_table_frame`, row/column order and cell text byte-faithful to the PDF),
with one column added: the `concept_key` each row carries in `row_dim`.
Columns are period separators only, as you said — the identity is the row.

`—` in the identity column = row carries **no** concept key.

## Selected income statement items ($m)

_table_id_ `overview_selected_income_statement_items_m_2025-12-31` · p.4-8 · row hierarchy: model levels

| &nbsp; | identity (`concept_key`) | 2nd Half 2025 | 2nd Half 2024 | % chg | 1st Half 2025 | % chg​ | Year 2025 | Year 2024 | % chg​​ |
|---|---|---|---|---|---|---|---|---|---|
| Selected income statement items ($m) | — |  |  |  |  |  |  |  |  |
|     Commercial book total income | `pnl.income.total` | 10,670 | 10,769 | (1) | 10,856 | (2) | 21,526 | 21,375 | 1 |
|         Net interest income | `pnl.nii.net` | 7,150 | 7,627 | (6) | 7,344 | (3) | 14,494 | 15,043 | (4) |
|         Net fee and commission income | `pnl.noninterest.fee_commission` | 2,456 | 2,077 | 18 | 2,442 | 1 | 4,898 | 4,168 | 18 |
|         Treasury customer sales and other income | `pnl.noninterest.trading` | 1,064 | 1,065 | (0) | 1,070 | (1) | 2,134 | 2,164 | (1) |
|     Markets trading Income | `pnl.noninterest.trading` | 593 | 489 | 21 | 781 | (24) | 1,374 | 922 | 49 |
|         Net interest income | `pnl.nii.net` | 21 | (302) | NM | (15) | NM | 6 | (619) | NM |
|         Non-interest income | `pnl.noninterest.total` | 572 | 791 | (28) | 796 | (28) | 1,368 | 1,541 | (11) |
|     Total income | `pnl.income.total` | 11,263 | 11,258 | 0 | 11,637 | (3) | 22,900 | 22,297 | 3 |
|         Of which: Net interest income | `pnl.nii.net` | 7,171 | 7,325 | (2) | 7,329 | (2) | 14,500 | 14,424 | 1 |
|     Expenses | `pnl.opex.total` | 4,765 | 4,644 | 3 | 4,484 | 6 | 9,249 | 8,895 | 4 |
|     Profit before allowances and amortisation | `pnl.profit.operating` | 6,498 | 6,614 | (2) | 7,153 | (9) | 13,651 | 13,402 | 2 |
|     Amortisation of intangible assets | `pnl.opex.amortisation_intangibles` | 11 | 11 | - | 12 | (8) | 23 | 23 | - |
|     Allowances for credit and other losses | `pnl.provisions.total` | 333 | 339 | (2) | 458 | (27) | 791 | 622 | 27 |
|         ECL Stage 3 (SP) | `pnl.provisions.stage3_sp` | 584 | 349 | 67 | 270 | >100 | 854 | 559 | 53 |
|         ECL Stage 1 and 2 (GP) | `pnl.provisions.stage12_gp` | (251) | (10) | (>100) | 188 | NM | (63) | 63 | NM |
|     Share of profits/losses of associates and JVs | `pnl.associates` | 120 | 136 | (12) | 142 | (15) | 262 | 250 | 5 |
|     Profit before tax | `pnl.profit.pretax` | 6,274 | 6,400 | (2) | 6,825 | (8) | 13,099 | 13,007 | 1 |
|     Net profit | `pnl.profit.net_attributable` | 5,312 | 5,649 | (6) | 5,721 | (7) | 11,033 | 11,408 | (3) |
|     Citi Integration | — |  |  |  |  |  |  | (19) | NM |
|     Provision for CSR¹ | — | (100) | (100) |  |  | NM | (100) | (100) |  |
|     Reported net profit | `pnl.profit.net_attributable` | 5,212 | 5,549 | (6) | 5,721 | (9) | 10,933 | 11,289 | (3) |

## Selected balance sheet items ($m)

_table_id_ `overview_selected_balance_sheet_items_m_2025-12-31` · p.4-8 · row hierarchy: model levels

| &nbsp; | identity (`concept_key`) | 2nd Half 2025 | 2nd Half 2024 | % chg | 1st Half 2025 | % chg​ | Year 2025 | Year 2024 | % chg​​ |
|---|---|---|---|---|---|---|---|---|---|
| Selected balance sheet items ($m) | — |  |  |  |  |  |  |  |  |
|     Customer loans | `bs.assets.customer_loans_net` | 445,011 | 430,594 | 3 | 433,046 | 3 | 445,011 | 430,594 | 3 |
|         Constant-currency change | — |  |  | 6 |  | 3 |  |  | 6 |
|     Total assets | `bs.assets.total` | 897,488 | 827,219 | 8 | 841,896 | 7 | 897,488 | 827,219 | 8 |
|         of which: Non-performing assets | `bs.assets.npa` | 4,843 | 5,036 | (4) | 4,686 | 3 | 4,843 | 5,036 | (4) |
|     Customer deposits | `bs.liabilities.customer_deposits` | 610,023 | 561,730 | 9 | 573,965 | 6 | 610,023 | 561,730 | 9 |
|         Constant-currency change | — |  |  | 12 |  | 6 |  |  | 12 |
|     Total liabilities | `bs.liabilities.total` | 828,572 | 758,386 | 9 | 773,286 | 7 | 828,572 | 758,386 | 9 |
|     Shareholders’ funds | `bs.equity.shareholders` | 68,867 | 68,786 | 0 | 68,564 | 0 | 68,867 | 68,786 | 0 |

## Key financial ratios (%)2,3

_table_id_ `overview_key_financial_ratios_2_3_2025-12-31` · p.4-8 · row hierarchy: model levels

| &nbsp; | identity (`concept_key`) | 2nd Half 2025 | 2nd Half 2024 | 1st Half 2025 | Year 2025 | Year 2024 |
|---|---|---|---|---|---|---|
| Key financial ratios (%)2,3 | — |  |  |  |  |  |
|     Net interest margin – Group | `ratio.nim` | 1.94 | 2.13 | 2.08 | 2.01 | 2.13 |
|     Net interest margin – Commercial Book | `ratio.nim` | 2.37 | 2.80 | 2.61 | 2.48 | 2.80 |
|     Cost/ income ratio | `ratio.cir` | 42.3 | 41.3 | 38.5 | 40.4 | 39.9 |
|     Return on assets | `ratio.roa` | 1.21 | 1.41 | 1.38 | 1.29 | 1.45 |
|     Return on equity4,5 | `ratio.roe` | 15.3 | 17.2 | 17.0 | 16.2 | 18.0 |
|     Return on tangible equity4,5,6 | — | 16.9 | 19.1 | 18.8 | 17.8 | 20.0 |
|     NPL ratio | `ratio.npl` | 1.0 | 1.1 | 1.0 | 1.0 | 1.1 |
|     Total allowances/ NPA | — | 130 | 129 | 137 | 130 | 129 |
|     Total allowances/ unsecured NPA | — | 197 | 226 | 236 | 197 | 226 |
|     SP for loans/ average loans (bp) | `ratio.credit_cost_bps` | 26 | 17 | 12 | 19 | 13 |
|     Common Equity Tier 1 (CET-1) ratio | `reg.capital.cet1_ratio` | 17.0 | 17.0 | 17.0 | 17.0 | 17.0 |
|     Fully phased-in CET-1 ratio7 | `reg.capital.cet1_ratio` | 15.0 | 15.1 | 15.1 | 15.0 | 15.1 |

## DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES

_table_id_ `overview_dbs_group_holdings_ltd_and_its_subsidiaries_2025-12-31` · p.4-8 · row hierarchy: model levels

| &nbsp; | identity (`concept_key`) | 2nd Half 2025 | 2nd Half 2024 | % chg | 1st Half 2025 | % chg​ | Year 2025 | Year 2024 | % chg​​ |
|---|---|---|---|---|---|---|---|---|---|
| Earnings2 | — |  |  |  |  |  |  |  |  |
|     Basic | — | 3.71 | 3.92 |  | 4.04 |  | 3.88 | 3.98 |  |
|     Diluted9 | — | 3.69 | 3.92 |  | 4.04 |  | 3.86 | 3.98 |  |
| Reported earnings | — |  |  |  |  |  |  |  |  |
|     Basic | — | 3.67 | 3.89 |  | 4.04 |  | 3.84 | 3.94 |  |
|     Diluted9 | — | 3.66 | 3.89 |  | 4.04 |  | 3.82 | 3.94 |  |
|     Net book value5 | `bs.nav_per_share` | 24.29 | 23.38 |  | 23.82 |  | 24.29 | 23.38 |  |
