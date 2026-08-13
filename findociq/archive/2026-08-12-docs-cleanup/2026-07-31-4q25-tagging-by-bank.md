# 4Q25 concept tagging — DBS / UOB / OCBC

Generated from `db/compiled_fs.db` at HEAD `292732d`.

**Scope.** Period `2025-12-31`, headline axes only (`geo=GLOBAL`, `segment=SEG_TOTAL`,
`industry=IND_TOTAL`) — segment / geography / industry breakdown cells are excluded, so
the candidate counts below are the *headline* ambiguity, not the whole document.
`expected` = the verified FY2025 Group figure from
`docs/specs/2026-07-30-cross-bank-identity-mapping.md`. **Bold** = matches expected.

Verdict key: ✅ clean = exactly one candidate value and it is right ·
⚠️ = right value present but N candidates share the concept at this period ·
❌ = expected value not reachable under this concept.

## DBS

`DBS_4Q25_performance_summary`

744 rows, 269 stamped (36%).

| # | Identity | concept_key | expected | verbatim label(s) tagged → value @ column | verdict |
|---|---|---|---:|---|---|
| 1 | Net interest income | `pnl.nii.net` | 14,500 | `Net interest income` → 15,043 @`Year 2024`; **14,500** @`Year 2025`; **14,500** @`Total`<br>`Net interest income (NII` → **14,500** @`Year 2025`; 14,424 @`Year 2024`; 7,171 @`2nd Half 2025`<br>`Commercial Book NII` → 15,043 @`Year 2024`; **14,494** @`Year 2025`; 7,150 @`2nd Half 2025`<br>_…+3 more labels_ | ⚠️ right value, 24 candidates |
| 2 | Net fee & commission income | `pnl.noninterest.fee_commission` | 4,898 | `Net fee and commission i` → **4,898** @`Year 2025`; **4,898** @`Total`; 4,168 @`Year 2024`<br>`Fee and commission incom` → 5,860 @`Year 2025`; 5,086 @`Year 2024`; 2,962 @`2nd Half 2025`<br>`Total` → **4,898** @`Year 2025`; 4,168 @`Year 2024`; 2,456 @`2nd Half 2025` | ⚠️ right value, 13 candidates |
| 3 | Other non-interest income | `pnl.noninterest.other` | 3,502 | _derived_ `pnl.noninterest.total − pnl.noninterest.fee_commission` → -2,532, -1,884, -946, -869 _…+7 more_ | ❌ derived, expected value absent |
| 4 | Total income | `pnl.income.total` | 22,900 | `Total income` → **22,900** @`Year 2025`; **22,900** @`Total`; 22,297 @`Year 2024`<br>`Commercial book total in` → 21,526 @`Year 2025`; 21,375 @`Year 2024`; 10,670 @`2nd Half 2025` | ⚠️ right value, 14 candidates |
| 5 | Operating expenses | `pnl.opex.total` | 9,249 | `Total expenses` → 9,349 @`Year 2025`; 9,018 @`Year 2024`; 4,865 @`2nd Half 2025¹`<br>`Total` → **9,249** @`Year 2025`; 8,895 @`Year 2024`; 4,765 @`2nd Half 2025`<br>`Expenses` → **9,249** @`Year 2025`; **9,249** @`Total`; 8,895 @`Year 2024` | ⚠️ right value, 12 candidates |
| 6 | Operating profit | `pnl.profit.operating` | 13,651 | `Profit before allowances` → **13,651** @`Year 2025`; 13,551 @`Year 2025`; 13,402 @`Year 2024` | ⚠️ right value, 10 candidates |
| 7 | Amortisation of intangibles | `pnl.opex.amortisation_intangibles` | 23 | `Amortisation of intangib` → **23** @`Year 2025`; **23** @`Year 2024`; **23** @`Total` | ⚠️ right value, 4 candidates |
| 8 | Allowances credit/other | `pnl.provisions.total` | 791 | `Total` → **791** @`Year 2025`; 622 @`Year 2024`; 333 @`2nd Half 2025`<br>`Allowances for credit an` → **791** @`Year 2025`; **791** @`Total`; 622 @`Year 2024`<br>`Total allowances` → 6,281 @`NPA ($m)` | ⚠️ right value, 9 candidates |
| 9 | Share of associates/JVs | `pnl.associates` | 262 | `Share of profits/losses ` → **262** @`Year 2025`; **262** @`Total`; 250 @`Year 2024` | ⚠️ right value, 8 candidates |
| 10 | Profit before tax | `pnl.profit.pretax` | 13,099 | `Profit before tax` → **13,099** @`Year 2025`; **13,099** @`Total`; 13,007 @`Year 2024` | ⚠️ right value, 12 candidates |
| 11 | Net profit | `pnl.profit.net_attributable` | 11,033 | `Net profit` → 11,408 @`Year 2024`; 11,408 @`Total`; 11,290 @`Year 2024`<br>`Reported net profit` → 11,289 @`Year 2024`; 10,933 @`Year 2025`; 5,212 @`2nd Half 2025` | ⚠️ right value, 20 candidates |
| 12 | Net customer loans | `bs.assets.customer_loans_net` | 445,011 | `Loans and advances to cu` → **445,011** @`31 Dec 2025`; -23,317 @`Year 2025`; -13,582 @`Year 2024`<br>`Net total` → **445,011** @`31 Dec 2025`<br>`Customer loans` → **445,011** @`2nd Half 2025`; **445,011** @`Year 2025`; 430,594 @`Year 2024` | ⚠️ right value, 5 candidates |
| 13 | Customer deposits | `bs.liabilities.customer_deposits` | 610,023 | `Deposits and balances fr` → **610,023** @`31 Dec 2025`<br>`Total` → **610,023** @`31 Dec 2025`<br>`Customer deposits` → **610,023** @`2nd Half 2025`; **610,023** @`Year 2025`; 596,127 @`Average balance ($m)` | ⚠️ right value, 22 candidates |
| 14 | Total assets | `bs.assets.total` | 897,488 | `Total assets` → **897,488** @`31 Dec 2025`; **897,488** @`2nd Half 2025`; **897,488** @`Year 2025` | ⚠️ right value, 5 candidates |
| 15 | Total liabilities | `bs.liabilities.total` | 828,572 | `Total liabilities` → **828,572** @`31 Dec 2025`; **828,572** @`2nd Half 2025`; **828,572** @`Year 2025` | ⚠️ right value, 5 candidates |
| 16 | Total equity | `bs.equity.total` | 68,916 | `Total equity` → **68,916** @`31 Dec 2025`; 17,643 @`31 Dec 2025` | ⚠️ right value, 2 candidates |
| 17 | Shareholders' equity | `bs.equity.shareholders` | 68,867 | `Shareholders’ funds` → **68,867** @`31 Dec 2025`; **68,867** @`2nd Half 2025`; **68,867** @`Year 2025`<br>`Shareholders` → 12,860 @`Year 2024`; 11,289 @`Year 2024`; 10,933 @`Year 2025` | ⚠️ right value, 16 candidates |
| 18 | Net interest margin | `ratio.nim` | 2.01 | `Net interest income/marg` → 14,500 @`Interest ($m)`; 14,424 @`Interest ($m)`; 7,171 @`Interest ($m)`<br>`Net interest margin (%)1` → 2.13 @`Year 2024`; **2.01** @`Year 2025`; 1.94 @`2nd Half 2025`<br>`Commercial Book NIM (%)1` → 2.8 @`Year 2024`; 2.48 @`Year 2025`; 2.37 @`2nd Half 2025`<br>_…+2 more labels_ | ⚠️ right value, 9 candidates |
| 19 | Cost/income ratio | `ratio.cir` | 40.4 | `Cost/ income ratio` → 42.3 @`2nd Half 2025`; **40.4** @`Year 2025`; 39.9 @`Year 2024` | ⚠️ right value, 3 candidates |
| 20 | Return on assets | `ratio.roa` | 1.29 | `Return on assets` → 1.45 @`Year 2024`; **1.29** @`Year 2025`; 1.21 @`2nd Half 2025` | ⚠️ right value, 3 candidates |
| 21 | Return on equity | `ratio.roe` | 16.2 | `Return on equity4,5` → 18 @`Year 2024`; **16.2** @`Year 2025`; 15.3 @`2nd Half 2025` | ⚠️ right value, 3 candidates |
| 22 | NPL ratio | `ratio.npl` | 1 | `NPL ratio` → 1.1 @`Year 2024`; **1** @`2nd Half 2025`; **1** @`Year 2025` | ⚠️ right value, 2 candidates |
| 23 | CET1 ratio | `reg.capital.cet1_ratio` | 17 | `Common Equity Tier 1 (CE` → **17** @`31 Dec 2025`<br>`Fully phased-in CET-1¹` → 15 @`31 Dec 2025`<br>`CET-1` → 9.2 @`31 Dec 2025`<br>_…+2 more labels_ | ⚠️ right value, 4 candidates |
| 26 | NAV per share | `bs.nav_per_share` | 24.29 | `Net book value5` → **24.29** @`2nd Half 2025`; **24.29** @`Year 2025`; 23.38 @`Year 2024` | ⚠️ right value, 2 candidates |

## UOB

`UOB_4Q25_condensed-financial-statements`

755 rows, 145 stamped (19%).

| # | Identity | concept_key | expected | verbatim label(s) tagged → value @ column | verdict |
|---|---|---|---:|---|---|
| 1 | Net interest income | `pnl.nii.net` | 9,355 | `Net interest income` → 9,674 @`Total`; **9,355** @`2025`; **9,355** @`2025 $m` | ⚠️ right value, 14 candidates |
| 2 | Net fee & commission income | `pnl.noninterest.fee_commission` | 2,569 | `Net fee and commission i` → **2,569** @`2025`; **2,569** @`2025 $m`; 1,239 @`2H25` | ⚠️ right value, 5 candidates |
| 3 | Other non-interest income | `pnl.noninterest.other` | 1,884 | _derived_ `pnl.noninterest.total − pnl.noninterest.fee_commission` → **1,884**, 837 | ⚠️ derived, right value among 2 |
| 4 | Total income | `pnl.income.total` | 13,808 | `Total operating income` → **13,808** @`2025 $m`; 6,687 @`2H25¹ $m` | ⚠️ right value, 2 candidates |
| 5 | Operating expenses | `pnl.opex.total` | 6,157 | `Less: Operating expenses` → **6,157** @`2025`; 3,062 @`2H25`; -4 @`+/(-) %`<br>`Total operating expenses` → **6,157** @`2025 $m`; 3,062 @`2H25¹ $m`<br>`Operating expenses` → -6,310 @`Total`; -6,157 @`Total`; -3,062 @`Total` | ⚠️ right value, 8 candidates |
| 6 | Operating profit | `pnl.profit.operating` | 7,651 | `Operating profit` → **7,651** @`2025`; 3,625 @`2H25`; -11 @`+/(-) %` | ⚠️ right value, 5 candidates |
| 7 | Amortisation of intangibles | `pnl.opex.amortisation_intangibles` | 31 | `Amortisation of intangib` → **31** @`2025 $m`; -31 @`Total`; -28 @`Total`<br>`Less: Amortisation of in` → **31** @`2025`; **31** @`2025 $m`; 14 @`2H25` | ⚠️ right value, 8 candidates |
| 8 | Allowances credit/other | `pnl.provisions.total` | 2,042 | `Allowance for credit and` → **2,042** @`2025 $m`; **2,042** @`2025`; 1,474 @`2H25` | ⚠️ right value, 2 candidates |
| 9 | Share of associates/JVs | `pnl.associates` | 79 | `Share of profit of assoc` → -79 @`2025 $m`; **79** @`2025 $m`; 60 @`2H25¹ $m`<br>`Add: Share of profit of ` → **79** @`2025`; 60 @`2H25`; -35 @`+/(-) %` | ⚠️ right value, 5 candidates |
| 10 | Profit before tax | `pnl.profit.pretax` | 5,657 | `Net profit before tax` → **5,657** @`2025`; 2,196 @`2H25`; -39 @`+/(-) %`<br>`Profit before tax` → 7,151 @`Total`; **5,657** @`2025 $m`; **5,657** @`Total` | ⚠️ right value, 6 candidates |
| 11 | Net profit | `pnl.profit.net_attributable` | 4,682 | `Net profit ¹` → **4,682** @`2025`; 1,853 @`2H25`; -41 @`+/(-) %` | ⚠️ right value, 5 candidates |
| 12 | Net customer loans | `bs.assets.customer_loans_net` | 347,877 | `Loans to customers` → **347,877** @`Dec-25 $m`; 271,118 @`Dec-25 $m`<br>`Net customer loans` → **347,877** @`$m` | ⚠️ right value, 2 candidates |
| 13 | Customer deposits | `bs.liabilities.customer_deposits` | 425,938 | `Customer deposits` → 412,813 @`Average balance $m`; 407,057 @`Average balance $m`; 9,093 @`Interest $m` | ❌ expected value absent |
| 14 | Total assets | `bs.assets.total` | 572,061 | `Total assets` → **572,061** @`Dec-25 $m`; **572,061** @`Total`; 537,664 @`Total` | ⚠️ right value, 3 candidates |
| 15 | Total liabilities | `bs.liabilities.total` | 520,568 | `Total liabilities` → **520,568** @`Dec-25 $m`; **520,568** @`Total`; 487,707 @`Total` | ⚠️ right value, 3 candidates |
| 16 | Total equity | `bs.equity.total` | 51,493 | `Total equity` → **51,493** @`Dec-25 $m`; 43,852 @`Dec-25 $m` | ⚠️ right value, 2 candidates |
| 17 | Shareholders' equity | `bs.equity.shareholders` | 51,248 | `Equity attributable to e` → **51,248** @`Dec-25 $m`; 43,852 @`Dec-25 $m` | ⚠️ right value, 2 candidates |
| 18 | Net interest margin | `ratio.nim` | 1.89 | `Net interest margin ²` → **1.89** @`2025`; 1.83 @`2H25`<br>`Net interest margin ¹` → **1.89** @`Average rate %`; 1.83 @`Average rate %` | ⚠️ right value, 2 candidates |
| 19 | Cost/income ratio | `ratio.cir` | 44.6 | `Cost/Income ratio` → 45.8 @`2H25`; **44.6** @`2025` | ⚠️ right value, 2 candidates |
| 20 | Return on assets | `ratio.roa` | 0.86 | `Return on average total ` → **0.86** @`2025`; 0.68 @`2H25` | ⚠️ right value, 2 candidates |
| 21 | Return on equity | `ratio.roe` | 9.6 | `Return on average ordina` → **9.6** @`2025`; 7.6 @`2H25` | ⚠️ right value, 2 candidates |
| 22 | NPL ratio | `ratio.npl` | 1.5 | `NPL ratio ³` → **1.5** @`2025`; **1.5** @`2H25` | ✅ clean |
| 23 | CET1 ratio | `reg.capital.cet1_ratio` | 15.1 | `CET1` → **15.1** @`$m` | ✅ clean |
| 26 | NAV per share | `bs.nav_per_share` | 29.36 | `Net asset value per ordi` → **29.36** @`Dec-25 $m`; 24.88 @`Dec-25 $m` | ⚠️ right value, 2 candidates |

## OCBC

`OCBC_4Q25_Condensed_Financial_Statements`, `OCBC_4Q25_Media_Release_and_Financial_Highlights`

1494 rows, 336 stamped (22%).

| # | Identity | concept_key | expected | verbatim label(s) tagged → value @ column | verdict |
|---|---|---|---:|---|---|
| 1 | Net interest income | `pnl.nii.net` | 9,150 | `Net interest income` → **9,150** @`Group`; **9,150** @`2025`; **9,150** @`FY25`<br>`Net Interest Income` → -6 @`YoY Change` | ⚠️ right value, 8 candidates |
| 2 | Net fee & commission income | `pnl.noninterest.fee_commission` | 2,411 | `Fees and commissions (ne` → **2,411** @`2025`; 1,285 @`2H 2025 (1)`; 22 @`+/(-) %`<br>`of which: Fees and commi` → **2,411** @`FY25`; 602 @`4Q25`; 22 @`YoY (%)` | ⚠️ right value, 7 candidates |
| 3 | Other non-interest income | `pnl.noninterest.other` | 3,053 | _derived_ `pnl.noninterest.total − pnl.noninterest.fee_commission` → **3,053**, 1,605, 718 | ⚠️ derived, right value among 3 |
| 4 | Total income | `pnl.income.total` | 14,614 | `Total income` → **14,614** @`Group`; **14,614** @`2025`; 7,412 @`Group`<br>`Total income total` → **14,614** @`2025`; 7,412 @`2H 2025 (1)`<br>`Total Income` → 1 @`YoY Change` | ⚠️ right value, 7 candidates |
| 5 | Operating expenses | `pnl.opex.total` | 5,882 | `Total operating expenses` → -5,882 @`2025`; **5,882** @`2025`; -3,078 @`2H 2025 (1)`<br>`Operating expenses` → -5,882 @`2025`; -3,078 @`2H25`; -1,559 @`4Q25`<br>`Operating Expenses` → 2 @`YoY Change` | ⚠️ right value, 7 candidates |
| 6 | Operating profit | `pnl.profit.operating` | 8,732 | `Operating profit before ` → **8,732** @`Group`; **8,732** @`2025`; 4,334 @`Group` | ⚠️ right value, 7 candidates |
| 7 | Amortisation of intangibles | `pnl.opex.amortisation_intangibles` | 21 | `Amortisation of intangib` → -64 @`YoY (%)`; -64 @`+/(-) %`; -64 @`+/(-) %` | ⚠️ right value, 8 candidates |
| 8 | Allowances credit/other | `pnl.provisions.total` | 665 | `Allowances for loans and` → -665 @`Group`; **665** @`2025`; -665 @`2025`<br>`Allowances` → -665 @`FY25`; -200 @`4Q25`; 44 @`QoQ (%)` | ⚠️ right value, 7 candidates |
| 9 | Share of associates/JVs | `pnl.associates` | 1,077 | `Share of results of asso` → **1,077** @`Group`; -1,077 @`2025`; **1,077** @`2025` | ⚠️ right value, 9 candidates |
| 10 | Profit before tax | `pnl.profit.pretax` | 9,123 | `Profit before income tax` → **9,123** @`Group`; **9,123** @`2025`; **9,123** @`FY25`<br>`Profit before income tax` → **9,123** @`2025`; 4,525 @`2H 2025 (1)`<br>`Profit before tax` → 2,113 @`4Q25`; 12 @`YoY (%)`; -12 @`QoQ (%)` | ⚠️ right value, 8 candidates |
| 11 | Net profit | `pnl.profit.net_attributable` | 7,422 | `Group net profit` → **7,422** @`FY25`; 1,745 @`4Q25`; -12 @`QoQ (%)`<br>`Net profit attributable ` → **7,422** @`2025`; 3,723 @`2H25`; 1,745 @`4Q25` | ⚠️ right value, 8 candidates |
| 12 | Net customer loans | `bs.assets.customer_loans_net` | 336,692 | `Loans to customers` → **336,692** @`31 Dec 2025`; 245,802 @`31 Dec 2025`<br>`Net loans` → **336,692** @`31 Dec 2025`<br>`Net loans to customers` → **336,692** @`2H25`; **336,692** @`2025`; **336,692** @`4Q25` | ⚠️ right value, 5 candidates |
| 13 | Customer deposits | `bs.liabilities.customer_deposits` | 428,286 | `Deposits of non-bank cus` → **428,286** @`31 Dec 2025`; **428,286** @`2H25`; **428,286** @`2025`<br>`Deposits of non-bank cus` → **428,286** @`31 Dec 2025`<br>`Customer Deposits` → 10 @`YoY Change`<br>_…+1 more labels_ | ⚠️ right value, 15 candidates |
| 14 | Total assets | `bs.assets.total` | 675,688 | `Subtotal Assets` → 566,079 @`31 Dec 2025`; 434,204 @`31 Dec 2025`<br>`Total assets` → **675,688** @`31 Dec 2025`; **675,688** @`Group`; **675,688** @`2H25`<br>`Total assets total` → **675,688** @`31 Dec 2025` | ⚠️ right value, 6 candidates |
| 15 | Total liabilities | `bs.liabilities.total` | 612,118 | `Subtotal Liabilities` → 502,719 @`31 Dec 2025`; 391,664 @`31 Dec 2025`<br>`Total liabilities` → **612,118** @`31 Dec 2025`; **612,118** @`Group`; 391,664 @`31 Dec 2025` | ⚠️ right value, 3 candidates |
| 16 | Total equity | `bs.equity.total` | 63,570 | `Total equity` → **63,570** @`31 Dec 2025`; 42,540 @`31 Dec 2025` | ⚠️ right value, 2 candidates |
| 17 | Shareholders' equity | `bs.equity.shareholders` | 61,768 | `Attributable to equity h` → **61,768** @`31 Dec 2025`; 42,540 @`31 Dec 2025`<br>`Ordinary equity` → 60,070 @`2H25`; 60,070 @`2025`; 60,070 @`4Q25`<br>`Equity attributable to e` → **61,768** @`2H25`; **61,768** @`2025`; **61,768** @`4Q25` | ⚠️ right value, 5 candidates |
| 18 | Net interest margin | `ratio.nim` | 1.91 | `Net interest income/marg` → 9,150 @`Average Interest`; **1.91** @`Average Rate %`<br>`Net interest margin` → **1.91** @`2025`; 1.86 @`4Q25`; 1.85 @`2H25`<br>`Net Interest Margin` → **1.91** @`Value` | ⚠️ right value, 4 candidates |
| 19 | Cost/income ratio | `ratio.cir` | 40.2 | `Cost-to-income` → 43.1 @`4Q25`; 41.5 @`2H25`; **40.2** @`2025` | ⚠️ right value, 3 candidates |
| 20 | Return on assets | `ratio.roa` | 1.37 | `Return on assets 3/` → **1.37** @`2025`; 1.35 @`2H25`; 1.25 @`4Q25` | ⚠️ right value, 3 candidates |
| 21 | Return on equity | `ratio.roe` | 12.6 | `Return on equity 1/ 2/` → **12.6** @`2025`; 12.5 @`2H25`; 11.6 @`4Q25`<br>`Group ROE – annualised` → 11.6 @`4Q25` | ⚠️ right value, 3 candidates |
| 22 | NPL ratio | `ratio.npl` | 0.9 | `NPL ratio` → **0.9** @`2025`; **0.9** @`4Q25`; **0.9** @`2H25`<br>`NPL Ratio` → **0.9** @`Value` | ✅ clean |
| 23 | CET1 ratio | `reg.capital.cet1_ratio` | 16.9 | `Transitional final Basel` → **16.9** @`Dec 2025`<br>`Fully phased-in final Ba` → 15.1 @`Dec 2025` | ⚠️ right value, 2 candidates |
| 26 | NAV per share | `bs.nav_per_share` | 13.38 | `Net asset value per ordi` → **13.38** @`31 Dec 2025`; 9.1 @`31 Dec 2025` | ⚠️ right value, 2 candidates |
