# Key-field extractability matrix

89 human_confirmed anchors x bank-quarter. A HIT requires the structural
path to resolve AND a clean consolidated value at the document period.

✅ HIT · 🟠 MISS-STRUCTURE · ❌ MISS-ABSENT · ⚠️ CONTAMINATED · · N/A-BY-DISCLOSURE

## DBS

| concept | exhibit | anchor | 2022-03-31 | 2022-06-30 | 2022-09-30 | 2022-12-31 | 2023-03-31 | 2025-03-31 | 2025-06-30 | 2025-09-30 | 2025-12-31 | 2026-03-31 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bs.liabilities.customer_deposits` | FS_BALANCE_SELECTED | `customer_deposits` ‹selected_balance_sheet_items› | · | · | · | · | 🟠 | 🟠 | ✅ | 🟠 | ✅ | 🟠 |
| `bs.assets.customer_loans_net` | FS_BALANCE_SELECTED | `customer_loans` ‹selected_balance_sheet_items› | · | · | · | · | 🟠 | 🟠 | ✅ | 🟠 | ✅ | 🟠 |
| `bs.equity.shareholders` | FS_BALANCE_SELECTED | `shareholders_funds` ‹selected_balance_sheet_items› | · | · | · | · | 🟠 | 🟠 | ✅ | 🟠 | ✅ | 🟠 |
| `bs.assets.total` | FS_BALANCE_SELECTED | `total_assets` ‹selected_balance_sheet_items› | · | · | · | · | 🟠 | 🟠 | ✅ | 🟠 | ✅ | 🟠 |
| `bs.liabilities.total` | FS_BALANCE_SELECTED | `total_liabilities` ‹selected_balance_sheet_items› | · | · | · | · | 🟠 | 🟠 | ✅ | 🟠 | ✅ | 🟠 |
| `bs.equity.total` | FS_BALANCE_STATUTORY | `total_equity` | · | ✅ | · | · | · | · | ✅ | · | ✅ | · |
| `pnl.provisions.total` [underlying] | FS_INCOME_SELECTED | `allowances_for_credit_and_other_losses` | · | ✅ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.opex.amortisation_intangibles` [underlying] | FS_INCOME_SELECTED | `amortisation_of_intangible_assets` | · | ❌ | · | · | ❌ | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.income.total` [underlying] | FS_INCOME_SELECTED | `commercial_book_total_income` | · | ❌ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.opex.total` [underlying] | FS_INCOME_SELECTED | `expenses` | · | ✅ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.income.total` [underlying] | FS_INCOME_SELECTED | `markets_trading_income` | · | ❌ | · | · | ❌ | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.noninterest.fee_commission` [underlying] | FS_INCOME_SELECTED | `net_fee_and_commission_income` ‹commercial_book_total_income› | · | 🟠 | · | · | ✅ | ✅ | ✅ | 🟠 | ✅ | ✅ |
| `pnl.nii.net` [underlying] | FS_INCOME_SELECTED | `net_interest_income` ‹commercial_book_total_income› | · | 🟠 | · | · | ✅ | ✅ | ✅ | 🟠 | ✅ | ✅ |
| `pnl.nii.net` [underlying] | FS_INCOME_SELECTED | `net_interest_income` ‹markets_trading_income› | · | 🟠 | · | · | 🟠 | ✅ | ✅ | 🟠 | ✅ | ✅ |
| `pnl.profit.net_attributable` [underlying] | FS_INCOME_SELECTED | `net_profit` | · | ✅ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.noninterest.total` [underlying] | FS_INCOME_SELECTED | `non_interest_income` ‹markets_trading_income› | · | ❌ | · | · | 🟠 | ✅ | ✅ | 🟠 | ✅ | ✅ |
| `pnl.nii.net` [underlying] | FS_INCOME_SELECTED | `of_which_net_interest_income` ‹total_income› | · | ❌ | · | · | ❌ | ❌ | ❌ | 🟠 | 🟠 | ✅ |
| `pnl.profit.operating` [underlying] | FS_INCOME_SELECTED | `profit_before_allowances_and_amortisation` | · | ❌ | · | · | ❌ | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.profit.pretax` [underlying] | FS_INCOME_SELECTED | `profit_before_tax` | · | ✅ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.profit.net_attributable` [reported] | FS_INCOME_SELECTED | `reported_net_profit` | · | ❌ | · | · | ❌ | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.associates` [underlying] | FS_INCOME_SELECTED | `share_of_profits_losses_of_associates_and_jvs` | · | ❌ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.income.total` [underlying] | FS_INCOME_SELECTED | `total_income` | · | ✅ | · | · | 🟠 | 🟠 | 🟠 | ✅ | 🟠 | ✅ |
| `pnl.noninterest.other` [underlying] | FS_INCOME_SELECTED | `treasury_customer_sales_and_other_income` ‹commercial_book_total_income› | · | ❌ | · | · | ❌ | ✅ | ✅ | 🟠 | ✅ | ✅ |
| `pnl.eps.basic` [underlying] | FS_PER_SHARE | `basic` ‹earnings› | · | ❌ | · | · | ❌ | ❌ | · | ❌ | · | ✅ |
| `pnl.eps.basic` [reported] | FS_PER_SHARE | `basic` ‹reported_earnings› | · | ❌ | · | · | ❌ | ❌ | · | ❌ | · | ✅ |
| `pnl.eps.diluted` [underlying] | FS_PER_SHARE | `diluted` ‹earnings› | · | ❌ | · | · | ❌ | ❌ | · | ❌ | · | ✅ |
| `pnl.eps.diluted` [reported] | FS_PER_SHARE | `diluted` ‹reported_earnings› | · | ❌ | · | · | ❌ | ❌ | · | ❌ | · | ✅ |
| `bs.nav_per_share` | FS_PER_SHARE | `net_book_value` | · | 🟠 | · | · | 🟠 | 🟠 | · | 🟠 | · | ✅ |
| `reg.capital.cet1_ratio` | FS_RATIOS_KEY | `common_equity_tier_1_cet_1_ratio` | · | · | · | · | ❌ | ✅ | 🟠 | ✅ | 🟠 | ✅ |
| `ratio.cir` | FS_RATIOS_KEY | `cost_income_ratio` | · | · | · | · | ✅ | ✅ | 🟠 | ✅ | 🟠 | ✅ |
| `ratio.nim` | FS_RATIOS_KEY | `net_interest_margin_commercial_book` | · | · | · | · | ✅ | ✅ | 🟠 | ✅ | 🟠 | ✅ |
| `ratio.nim` | FS_RATIOS_KEY | `net_interest_margin_group` | · | · | · | · | ✅ | ✅ | 🟠 | ✅ | 🟠 | ✅ |
| `ratio.npl` | FS_RATIOS_KEY | `npl_ratio` | · | · | · | · | ✅ | ✅ | 🟠 | ✅ | 🟠 | ✅ |
| `ratio.roa` | FS_RATIOS_KEY | `return_on_assets` | · | · | · | · | ✅ | ✅ | 🟠 | ✅ | 🟠 | ✅ |
| `ratio.roe` | FS_RATIOS_KEY | `return_on_equity` | · | · | · | · | ✅ | ✅ | 🟠 | ✅ | 🟠 | ✅ |

## UOB

| concept | exhibit | anchor | 2025-03-31 | 2025-09-30 | 2025-12-31 |
|---|---|---|---|---|---|
| `bs.liabilities.customer_deposits` | FS_BALANCE_STATUTORY | `deposits_and_balances_of_customers` | · | · | ✅ |
| `bs.assets.customer_loans_net` | FS_BALANCE_STATUTORY | `loans_to_customers` | · | · | ✅ |
| `bs.equity.total` | FS_BALANCE_STATUTORY | `total_equity` | · | · | ✅ |
| `bs.liabilities.total` | FS_BALANCE_STATUTORY | `total_liabilities` | · | · | ✅ |
| `pnl.associates` | FS_HIGHLIGHTS_COMBINED | `add_share_of_profit_of_associates_and_joint_ventures` | 🟠 | 🟠 | ✅ |
| `pnl.provisions.total` | FS_HIGHLIGHTS_COMBINED | `allowance_for_credit_and_other_losses` | 🟠 | 🟠 | ✅ |
| `pnl.eps.basic` | FS_HIGHLIGHTS_COMBINED | `basic` ‹earnings_per_ordinary_share› | ✅ | ✅ | ✅ |
| `reg.capital.cet1_ratio` | FS_HIGHLIGHTS_COMBINED | `common_equity_tier_1` ‹capital_adequacy_ratios› | ✅ | ✅ | ⚠️ |
| `ratio.cir` | FS_HIGHLIGHTS_COMBINED | `cost_income_ratio` | 🟠 | 🟠 | ⚠️ |
| `bs.liabilities.customer_deposits` | FS_HIGHLIGHTS_COMBINED | `customer_deposits` | 🟠 | 🟠 | ⚠️ |
| `pnl.eps.diluted` | FS_HIGHLIGHTS_COMBINED | `diluted` ‹earnings_per_ordinary_share› | ✅ | ✅ | ✅ |
| `bs.assets.customer_loans_gross` | FS_HIGHLIGHTS_COMBINED | `gross_customer_loans` | 🟠 | 🟠 | ⚠️ |
| `pnl.opex.amortisation_intangibles` | FS_HIGHLIGHTS_COMBINED | `less_amortisation_of_intangible_assets` | 🟠 | 🟠 | ✅ |
| `pnl.opex.total` | FS_HIGHLIGHTS_COMBINED | `less_operating_expenses` | ❌ | ❌ | ✅ |
| `bs.nav_per_share` | FS_HIGHLIGHTS_COMBINED | `net_asset_value_nav_per_ordinary_share` | 🟠 | 🟠 | ⚠️ |
| `pnl.noninterest.fee_commission` | FS_HIGHLIGHTS_COMBINED | `net_fee_and_commission_income` | ❌ | ❌ | ✅ |
| `pnl.nii.net` | FS_HIGHLIGHTS_COMBINED | `net_interest_income` | 🟠 | 🟠 | ✅ |
| `ratio.nim` | FS_HIGHLIGHTS_COMBINED | `net_interest_margin` | 🟠 | 🟠 | ⚠️ |
| `pnl.profit.net_attributable` | FS_HIGHLIGHTS_COMBINED | `net_profit` | 🟠 | 🟠 | ✅ |
| `pnl.profit.pretax` | FS_HIGHLIGHTS_COMBINED | `net_profit_before_tax` | 🟠 | 🟠 | ✅ |
| `ratio.npl` | FS_HIGHLIGHTS_COMBINED | `npl_ratio` | 🟠 | 🟠 | ⚠️ |
| `pnl.profit.operating` | FS_HIGHLIGHTS_COMBINED | `operating_profit` | 🟠 | 🟠 | ✅ |
| `pnl.noninterest.other` | FS_HIGHLIGHTS_COMBINED | `other_non_interest_income` | 🟠 | 🟠 | ✅ |
| `ratio.roe` | FS_HIGHLIGHTS_COMBINED | `return_on_average_ordinary_shareholders_equity` | 🟠 | ❌ | ⚠️ |
| `ratio.roa` | FS_HIGHLIGHTS_COMBINED | `return_on_average_total_assets` | 🟠 | 🟠 | ⚠️ |
| `bs.equity.shareholders` | FS_HIGHLIGHTS_COMBINED | `shareholders_equity` | 🟠 | 🟠 | ⚠️ |
| `bs.assets.total` | FS_HIGHLIGHTS_COMBINED | `total_assets` | 🟠 | 🟠 | ⚠️ |
| `pnl.income.total` | FS_HIGHLIGHTS_COMBINED | `total_income` | 🟠 | 🟠 | ⚠️ |

## OCBC

| concept | exhibit | anchor | 2025-03-31 | 2025-06-30 | 2025-09-30 | 2025-12-31 |
|---|---|---|---|---|---|---|
| `bs.equity.total` | FS_BALANCE_STATUTORY | `total_equity` | · | · | · | ✅ |
| `bs.liabilities.total` | FS_BALANCE_STATUTORY | `total_liabilities` | · | · | · | ✅ |
| `pnl.opex.amortisation_intangibles` | FS_INCOME_SELECTED | `amortisation_of_intangible_assets` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `bs.liabilities.customer_deposits` | FS_INCOME_SELECTED | `deposits_of_non_bank_customers` ‹selected_balance_sheet_items› | ✅ | · | · | ✅ |
| `bs.equity.shareholders` | FS_INCOME_SELECTED | `equity_attributable_to_equity_holders_of_the_bank` ‹selected_balance_sheet_items› | ✅ | · | · | ✅ |
| `pnl.nii.net` | FS_INCOME_SELECTED | `net_interest_income` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `bs.assets.customer_loans_net` | FS_INCOME_SELECTED | `net_loans_to_customers` ‹selected_balance_sheet_items› | ✅ | · | · | ✅ |
| `pnl.profit.net_attributable` | FS_INCOME_SELECTED | `net_profit_attributable_to_equity_holders` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `pnl.noninterest.total` | FS_INCOME_SELECTED | `non_interest_income` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `pnl.opex.total` | FS_INCOME_SELECTED | `operating_expenses` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `pnl.profit.operating` | FS_INCOME_SELECTED | `operating_profit_before_allowances_and_amortisation` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `pnl.profit.pretax` | FS_INCOME_SELECTED | `profit_before_income_tax` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `pnl.associates` | FS_INCOME_SELECTED | `share_of_results_of_associates_net_of_tax` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `bs.assets.total` | FS_INCOME_SELECTED | `total_assets` ‹selected_balance_sheet_items› | ✅ | · | · | ✅ |
| `pnl.income.total` | FS_INCOME_SELECTED | `total_income` ‹selected_income_statement_items› | ✅ | · | · | ✅ |
| `pnl.provisions.total` | FS_INCOME_STATUTORY | `allowances_for_loans_and_other_assets` | · | · | · | ✅ |
| `pnl.noninterest.fee_commission` | FS_INCOME_STATUTORY | `fees_and_commissions_net` | · | · | · | ✅ |
| `pnl.eps.basic` | FS_RATIOS_KEY | `basic_earnings` ‹earnings_per_share› | 🟠 | ❌ | ❌ | ✅ |
| `reg.capital.cet1_ratio` | FS_RATIOS_KEY | `common_equity_tier_1` ‹capital_adequacy_ratios› | ❌ | ❌ | ❌ | ⚠️ |
| `ratio.cir` | FS_RATIOS_KEY | `cost_to_income` ‹revenue_mix_efficiency_ratios› | 🟠 | ❌ | ❌ | ⚠️ |
| `pnl.eps.diluted` | FS_RATIOS_KEY | `diluted_earnings` ‹earnings_per_share› | 🟠 | ❌ | ❌ | ✅ |
| `bs.nav_per_share` | FS_RATIOS_KEY | `net_asset_value_per_share` | 🟠 | ❌ | ❌ | ✅ |
| `ratio.nim` | FS_RATIOS_KEY | `net_interest_margin` ‹revenue_mix_efficiency_ratios› | 🟠 | 🟠 | 🟠 | ⚠️ |
| `ratio.npl` | FS_RATIOS_KEY | `npl_ratio` ‹revenue_mix_efficiency_ratios› | 🟠 | 🟠 | 🟠 | ⚠️ |
| `ratio.roa` | FS_RATIOS_KEY | `return_on_assets` ‹performance_ratios› | ❌ | ❌ | ❌ | ⚠️ |
| `ratio.roe` | FS_RATIOS_KEY | `return_on_equity` ‹performance_ratios› | ❌ | ❌ | ❌ | ⚠️ |
