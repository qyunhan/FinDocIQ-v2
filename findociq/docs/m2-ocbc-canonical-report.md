# M2 canonical leaf set — OCBC

Built from each (bank, table_type_id)'s 4Q25 benchmark instance (falls back to the most recent available period if 4Q25 wasn't captured for that table_type_id — see per-table_type note below when that happened). Scope: every table_type_id currently backing a `OCBC` `fact_metric` row, post-dedup.

Total canonical leaves: **364** across **13** table_type_ids.

## `FS_ALLOWANCES` (6 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `allowances_write_back` | Allowances/(write-back): | 0 | active (added 4Q25) |
| 1 | `impaired_loans` | Impaired loans | 0 | active (added 4Q25) |
| 2 | `impaired_other_assets` | Impaired other assets | 0 | active (added 4Q25) |
| 3 | `non_impaired_loans` | Non-impaired loans | 0 | active (added 4Q25) |
| 4 | `non_impaired_other_assets` | Non-impaired other assets | 0 | active (added 4Q25) |
| 5 | `allowances_for_loans_and_other_assets` | Allowances for loans and other assets | 0 | active (added 4Q25) |

## `FS_AVG_BALANCE_SHEET` (25 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `interest_earning_assets` | Interest earning assets | 0 | active (added 4Q25) |
| 1 | `interest_earning_assets::loans_to_customers` | Loans to customers | 0 | active (added 4Q25) |
| 2 | `interest_earning_assets::placements_with_and_loans_to_banks` | Placements with and loans to banks | 0 | active (added 4Q25) |
| 3 | `interest_earning_assets::other_interest_earning_assets` | Other interest earning assets | 0 | active (added 4Q25) |
| 4 | `interest_earning_assets::total_interest_earning_assets` | Total interest earning assets | 0 | active (added 4Q25) |
| 5 | `interest_bearing_liabilities` | Interest bearing liabilities | 0 | active (added 4Q25) |
| 6 | `interest_bearing_liabilities::deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 4Q25) |
| 7 | `interest_bearing_liabilities::deposits_and_balances_of_banks` | Deposits and balances of banks | 0 | active (added 4Q25) |
| 8 | `interest_bearing_liabilities::other_borrowings` | Other borrowings | 0 | active (added 4Q25) |
| 9 | `interest_bearing_liabilities::total_interest_bearing_liabilities` | Total interest bearing liabilities | 0 | active (added 4Q25) |
| 10 | `net_interest_income_margin_1` | Net interest income/margin 1/ | 0 | active (added 4Q25) |
| 11 | `note_1_net_interest_margin_is_net_interest_income_as_a_percentage_of_interest_earning_assets` | Note:
1. Net interest margin is net interest income as a percentage of interest earning assets. | 0 | active (added 4Q25) |
| 12 | `interest_income` | Interest income | 0 | active (added 4Q25) |
| 13 | `interest_income::loans_to_customers` | Loans to customers | 0 | active (added 4Q25) |
| 14 | `interest_income::placements_with_and_loans_to_banks` | Placements with and loans to banks | 0 | active (added 4Q25) |
| 15 | `interest_income::other_interest_earning_assets` | Other interest earning assets | 0 | active (added 4Q25) |
| 16 | `interest_income::total_interest_income` | Total interest income | 0 | active (added 4Q25) |
| 17 | `interest_expense` | Interest expense | 0 | active (added 4Q25) |
| 18 | `interest_expense::deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 4Q25) |
| 19 | `interest_expense::deposits_and_balances_of_banks` | Deposits and balances of banks | 0 | active (added 4Q25) |
| 20 | `interest_expense::other_borrowings` | Other borrowings | 0 | active (added 4Q25) |
| 21 | `interest_expense::total_interest_expense` | Total interest expense | 0 | active (added 4Q25) |
| 22 | `impact_on_net_interest_income` | Impact on net interest income | 0 | active (added 4Q25) |
| 23 | `interest_expense::due_to_change_in_number_of_days` | Due to change in number of days | 0 | active (added 4Q25) |
| 24 | `net_interest_income` | Net interest income | 0 | active (added 4Q25) |

## `FS_BALANCE_SELECTED` (25 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 8 | `selected_income_statement_items::allowances_write_back_charge_for_non_impaired_assets` | Allowances write-back/(charge) for non-impaired assets | 0 | active (added 4Q25) |
| 21 | `note_1_excludes_amortisation_of_intangible_assets` | Note:
1. Excludes amortisation of intangible assets. | 0 | active (added 4Q25) |
| 22 | `selected_income_statement_items` | Selected Income Statement Items | 0 | active (added 4Q25) |
| 23 | `selected_income_statement_items::net_interest_income` | Net interest income | 0 | active (added 4Q25) |
| 24 | `selected_income_statement_items::non_interest_income` | Non-interest income | 0 | active (added 4Q25) |
| 25 | `selected_income_statement_items::total_income` | Total income | 0 | active (added 4Q25) |
| 26 | `selected_income_statement_items::operating_expenses` | Operating expenses | 0 | active (added 4Q25) |
| 27 | `selected_income_statement_items::operating_profit_before_allowances_and_amortisation` | Operating profit before allowances and amortisation | 0 | active (added 4Q25) |
| 28 | `selected_income_statement_items::amortisation_of_intangible_assets` | Amortisation of intangible assets | 0 | active (added 4Q25) |
| 29 | `selected_income_statement_items::allowances_for_impaired_assets` | Allowances for impaired assets | 0 | active (added 4Q25) |
| 30 | `selected_income_statement_items::allowances_charge_write_back_for_non_impaired_assets` | Allowances (charge)/write-back for non-impaired assets | 0 | active (added 4Q25) |
| 31 | `selected_income_statement_items::operating_profit_after_allowances_and_amortisation` | Operating profit after allowances and amortisation | 0 | active (added 4Q25) |
| 32 | `selected_income_statement_items::share_of_results_of_associates_net_of_tax` | Share of results of associates, net of tax | 0 | active (added 4Q25) |
| 33 | `selected_income_statement_items::profit_before_income_tax` | Profit before income tax | 0 | active (added 4Q25) |
| 34 | `selected_income_statement_items::net_profit_attributable_to_equity_holders` | Net profit attributable to equity holders | 0 | active (added 4Q25) |
| 35 | `selected_income_statement_items::cash_basis_net_profit_attributable_to_equity_holders_1` | Cash basis net profit attributable to equity holders 1/ | 0 | active (added 4Q25) |
| 36 | `selected_balance_sheet_items` | Selected Balance Sheet Items | 0 | active (added 4Q25) |
| 37 | `selected_balance_sheet_items::ordinary_equity` | Ordinary equity | 0 | active (added 4Q25) |
| 38 | `selected_balance_sheet_items::equity_attributable_to_equity_holders_of_the_bank` | Equity attributable to equity holders of the Bank | 0 | active (added 4Q25) |
| 39 | `selected_balance_sheet_items::total_assets` | Total assets | 0 | active (added 4Q25) |
| 40 | `selected_balance_sheet_items::assets_excluding_investment_securities_and_other_assets_for_life_insurance_funds` | Assets excluding investment securities and other assets for life insurance funds | 0 | active (added 4Q25) |
| 41 | `selected_balance_sheet_items::net_loans_to_customers` | Net loans to customers | 0 | active (added 4Q25) |
| 42 | `selected_balance_sheet_items::deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 4Q25) |
| 43 | `note` | Note: | 0 | active (added 4Q25) |
| 44 | `1_excludes_amortisation_of_intangible_assets` | 1. Excludes amortisation of intangible assets. | 0 | active (added 4Q25) |

## `FS_BALANCE_STATUTORY` (51 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `equity` | EQUITY | 0 | active (added 4Q25) |
| 1 | `equity::attributable_to_equity_holders_of_the_bank` | Attributable to equity holders of the Bank | 0 | active (added 4Q25) |
| 2 | `share_capital` | Share capital | 0 | active (added 4Q25) |
| 3 | `other_equity_instruments` | Other equity instruments | 0 | active (added 4Q25) |
| 4 | `capital_reserves` | Capital reserves | 0 | active (added 4Q25) |
| 5 | `fair_value_reserves` | Fair value reserves | 0 | active (added 4Q25) |
| 6 | `revenue_reserves` | Revenue reserves | 0 | active (added 4Q25) |
| 7 | `equity::attributable_to_equity_holders_of_the_bank_total` | Attributable to equity holders of the Bank (Total) | 0 | active (added 4Q25) |
| 8 | `equity::other_equity_instruments_issued_by_subsidiary` | Other equity instruments issued by subsidiary | 0 | active (added 4Q25) |
| 9 | `equity::non_controlling_interests` | Non-controlling interests | 0 | active (added 4Q25) |
| 10 | `total_equity` | Total equity | 0 | active (added 4Q25) |
| 11 | `liabilities` | LIABILITIES | 0 | active (added 4Q25) |
| 12 | `liabilities::deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 4Q25) |
| 13 | `liabilities::deposits_and_balances_of_banks` | Deposits and balances of banks | 0 | active (added 4Q25) |
| 14 | `liabilities::due_to_subsidiaries` | Due to subsidiaries | 0 | active (added 4Q25) |
| 15 | `liabilities::due_to_associates` | Due to associates | 0 | active (added 4Q25) |
| 16 | `liabilities::trading_portfolio_liabilities` | Trading portfolio liabilities | 0 | active (added 4Q25) |
| 17 | `liabilities::derivative_payables` | Derivative payables | 0 | active (added 4Q25) |
| 18 | `liabilities::other_liabilities` | Other liabilities | 0 | active (added 4Q25) |
| 19 | `liabilities::current_tax_payables` | Current tax payables | 0 | active (added 4Q25) |
| 20 | `liabilities::deferred_tax_liabilities` | Deferred tax liabilities | 0 | active (added 4Q25) |
| 21 | `liabilities::debt_issued` | Debt issued | 0 | active (added 4Q25) |
| 22 | `liabilities::subtotal_liabilities` | Subtotal Liabilities | 0 | active (added 4Q25) |
| 23 | `liabilities::insurance_contract_liabilities_and_other_liabilities_for_life_insurance_funds` | Insurance contract liabilities and other liabilities for life insurance funds | 0 | active (added 4Q25) |
| 24 | `total_liabilities` | Total liabilities | 0 | active (added 4Q25) |
| 25 | `total_equity_and_liabilities` | Total equity and liabilities | 0 | active (added 4Q25) |
| 26 | `assets` | ASSETS | 0 | active (added 4Q25) |
| 27 | `assets::cash_and_placements_with_central_banks` | Cash and placements with central banks | 0 | active (added 4Q25) |
| 28 | `assets::singapore_government_treasury_bills_and_securities` | Singapore government treasury bills and securities | 0 | active (added 4Q25) |
| 29 | `assets::other_government_treasury_bills_and_securities` | Other government treasury bills and securities | 0 | active (added 4Q25) |
| 30 | `assets::placements_with_and_loans_to_banks` | Placements with and loans to banks | 0 | active (added 4Q25) |
| 31 | `assets::loans_to_customers` | Loans to customers | 0 | active (added 4Q25) |
| 32 | `assets::debt_and_equity_securities` | Debt and equity securities | 0 | active (added 4Q25) |
| 33 | `assets::derivative_receivables` | Derivative receivables | 0 | active (added 4Q25) |
| 34 | `assets::other_assets` | Other assets | 0 | active (added 4Q25) |
| 35 | `assets::deferred_tax_assets` | Deferred tax assets | 0 | active (added 4Q25) |
| 36 | `assets::associates` | Associates | 0 | active (added 4Q25) |
| 37 | `assets::subsidiaries` | Subsidiaries | 0 | active (added 4Q25) |
| 38 | `assets::property_plant_and_equipment` | Property, plant and equipment | 0 | active (added 4Q25) |
| 39 | `assets::investment_property` | Investment property | 0 | active (added 4Q25) |
| 40 | `assets::goodwill_and_other_intangible_assets` | Goodwill and other intangible assets | 0 | active (added 4Q25) |
| 41 | `assets::subtotal_assets` | Subtotal Assets | 0 | active (added 4Q25) |
| 42 | `assets::investment_securities_for_life_insurance_funds` | Investment securities for life insurance funds | 0 | active (added 4Q25) |
| 43 | `assets::other_assets_for_life_insurance_funds` | Other assets for life insurance funds | 0 | active (added 4Q25) |
| 44 | `total_assets` | Total assets | 0 | active (added 4Q25) |
| 45 | `assets::net_asset_value_per_ordinary_share_s_1` | Net asset value per ordinary share – S$ (1) | 0 | active (added 4Q25) |
| 46 | `off_balance_sheet_items` | OFF-BALANCE SHEET ITEMS | 0 | active (added 4Q25) |
| 47 | `off_balance_sheet_items::contingent_liabilities` | Contingent liabilities | 0 | active (added 4Q25) |
| 48 | `off_balance_sheet_items::commitments` | Commitments | 0 | active (added 4Q25) |
| 49 | `off_balance_sheet_items::derivative_financial_instruments` | Derivative financial instruments | 0 | active (added 4Q25) |
| 50 | `1_unaudited_and_unreviewed` | (1) Unaudited and unreviewed. | 0 | active (added 4Q25) |

## `FS_CAPITAL_ADEQUACY` (36 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `ordinary_shares` | Ordinary shares | 0 | active (added 4Q25) |
| 1 | `disclosed_reserves_others` | Disclosed reserves/others | 0 | active (added 4Q25) |
| 3 | `common_equity_tier_1_capital` | Common Equity Tier 1 Capital | 0 | active (added 4Q25) |
| 4 | `additional_tier_1_capital` | Additional Tier 1 capital | 0 | active (added 4Q25) |
| 6 | `tier_1_capital` | Tier 1 Capital | 0 | active (added 4Q25) |
| 7 | `tier_2_capital` | Tier 2 capital | 0 | active (added 4Q25) |
| 8 | `regulatory_adjustments` | Regulatory adjustments | 0 | active (added 4Q25) |
| 9 | `total_eligible_capital` | Total Eligible Capital | 0 | active (added 4Q25) |
| 10 | `risk_weighted_assets` | Risk Weighted Assets | 0 | active (added 4Q25) |
| 11 | `capital_adequacy_ratios` | Capital Adequacy Ratios | 0 | active (added 4Q25) |
| 12 | `common_equity_tier_1` | Common Equity Tier 1 | 0 | active (added 4Q25) |
| 13 | `tier_1` | Tier 1 | 0 | active (added 4Q25) |
| 14 | `total` | Total | 0 | active (added 4Q25) |
| 15 | `key_financial_ratios` | Key Financial Ratios (%) | 0 | active (added 4Q25) |
| 16 | `performance_ratios` | Performance ratios | 0 | active (added 4Q25) |
| 17 | `performance_ratios::return_on_equity_1_2` | Return on equity 1/ 2/ | 0 | active (added 4Q25) |
| 18 | `performance_ratios::return_on_assets_3` | Return on assets 3/ | 0 | active (added 4Q25) |
| 19 | `revenue_mix_efficiency_ratios` | Revenue mix/efficiency ratios | 0 | active (added 4Q25) |
| 20 | `revenue_mix_efficiency_ratios::net_interest_margin` | Net interest margin | 0 | active (added 4Q25) |
| 21 | `revenue_mix_efficiency_ratios::non_interest_income_to_total_income` | Non-interest income to total income | 0 | active (added 4Q25) |
| 22 | `revenue_mix_efficiency_ratios::cost_to_income` | Cost-to-income | 0 | active (added 4Q25) |
| 23 | `revenue_mix_efficiency_ratios::loans_to_deposits` | Loans-to-deposits | 0 | active (added 4Q25) |
| 24 | `revenue_mix_efficiency_ratios::npl_ratio` | NPL ratio | 0 | active (added 4Q25) |
| 25 | `capital_adequacy_ratios_8_9` | Capital adequacy ratios 8/ 9/ | 0 | active (added 4Q25) |
| 26 | `capital_adequacy_ratios::common_equity_tier_1` | Common Equity Tier 1 | 0 | active (added 4Q25) |
| 27 | `capital_adequacy_ratios::tier_1` | Tier 1 | 0 | active (added 4Q25) |
| 28 | `capital_adequacy_ratios::total` | Total | 0 | active (added 4Q25) |
| 29 | `leverage_ratio_5_8_9` | Leverage ratio 5/ 8/ 9/ | 0 | active (added 4Q25) |
| 30 | `liquidity_coverage_ratios_6_8` | Liquidity coverage ratios 6/ 8/ | 0 | active (added 4Q25) |
| 31 | `liquidity_coverage_ratios::singapore_dollar` | Singapore dollar | 0 | active (added 4Q25) |
| 32 | `liquidity_coverage_ratios::all_currency` | All-currency | 0 | active (added 4Q25) |
| 33 | `net_stable_funding_ratio_7_8` | Net stable funding ratio 7/ 8/ | 0 | active (added 4Q25) |
| 34 | `earnings_per_share_2` | Earnings per share (S$) 2/ | 0 | active (added 4Q25) |
| 35 | `earnings_per_share::basic_earnings` | Basic earnings | 0 | active (added 4Q25) |
| 36 | `earnings_per_share::diluted_earnings` | Diluted earnings | 0 | active (added 4Q25) |
| 37 | `net_asset_value_per_share` | Net asset value per share (S$) | 0 | active (added 4Q25) |

## `FS_CUSTOMER_DEPOSITS` (8 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 4Q25) |
| 1 | `fixed_deposits` | Fixed deposits | 0 | active (added 4Q25) |
| 2 | `savings_deposits` | Savings deposits | 0 | active (added 4Q25) |
| 3 | `current_accounts` | Current accounts | 0 | active (added 4Q25) |
| 4 | `others` | Others | 0 | active (added 4Q25) |
| 5 | `deposits_of_non_bank_customers_total` | Deposits of non-bank customers total | 0 | active (added 4Q25) |
| 6 | `deposits_and_balances_of_banks` | Deposits and balances of banks | 0 | active (added 4Q25) |
| 7 | `total_deposits` | Total deposits | 0 | active (added 4Q25) |

## `FS_CUSTOMER_LOANS` (44 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `gross_loans` | Gross loans | 0 | active (added 4Q25) |
| 1 | `allowances` | Allowances | 0 | active (added 4Q25) |
| 2 | `impaired_loans` | Impaired loans | 0 | active (added 4Q25) |
| 3 | `non_impaired_loans` | Non-impaired loans | 0 | active (added 4Q25) |
| 4 | `net_loans` | Net loans | 0 | active (added 4Q25) |
| 37 | `` |  | 0 | active (added 4Q25) |
| 38 | `note` | Note: | 0 | active (added 4Q25) |
| 39 | `1_loans_by_geography_are_determined_based_on_where_the_credit_risk_resides_which_may_be_different_from_the_borrower_s_country_of_residence_or_the_booking_location_of_the_loans` | 1. Loans by geography are determined based on where the credit risk resides, which may be different from the borrower’s country of residence or the booking location of the loans. | 0 | active (added 4Q25) |
| 42 | `allowances::impaired_loans` | Impaired loans | 0 | active (added 4Q25) |
| 43 | `allowances::non_impaired_loans` | Non-impaired loans | 0 | active (added 4Q25) |
| 45 | `by_maturity` | By Maturity | 0 | active (added 4Q25) |
| 46 | `by_maturity::within_1_year` | Within 1 year | 0 | active (added 4Q25) |
| 47 | `by_maturity::1_to_3_years` | 1 to 3 years | 0 | active (added 4Q25) |
| 48 | `by_maturity::over_3_years` | Over 3 years | 0 | active (added 4Q25) |
| 49 | `by_maturity::total_by_maturity` | Total By Maturity | 0 | active (added 4Q25) |
| 50 | `by_industry` | By Industry | 0 | active (added 4Q25) |
| 51 | `by_industry::agriculture_mining_and_quarrying` | Agriculture, mining and quarrying | 0 | active (added 4Q25) |
| 52 | `by_industry::manufacturing` | Manufacturing | 0 | active (added 4Q25) |
| 53 | `by_industry::building_and_construction` | Building and construction | 0 | active (added 4Q25) |
| 54 | `by_industry::housing_loans` | Housing loans | 0 | active (added 4Q25) |
| 55 | `by_industry::general_commerce` | General commerce | 0 | active (added 4Q25) |
| 56 | `by_industry::transport_storage_and_communication` | Transport, storage and communication | 0 | active (added 4Q25) |
| 57 | `by_industry::financial_institutions_investment_and_holding_companies` | Financial institutions, investment and holding companies | 0 | active (added 4Q25) |
| 58 | `by_industry::professionals_and_individuals` | Professionals and individuals | 0 | active (added 4Q25) |
| 59 | `by_industry::others` | Others | 0 | active (added 4Q25) |
| 60 | `by_industry::total_by_industry` | Total By Industry | 0 | active (added 4Q25) |
| 61 | `by_currency` | By Currency | 0 | active (added 4Q25) |
| 62 | `by_currency::singapore_dollar` | Singapore Dollar | 0 | active (added 4Q25) |
| 63 | `by_currency::united_states_dollar` | United States Dollar | 0 | active (added 4Q25) |
| 64 | `by_currency::malaysian_ringgit` | Malaysian Ringgit | 0 | active (added 4Q25) |
| 65 | `by_currency::indonesian_rupiah` | Indonesian Rupiah | 0 | active (added 4Q25) |
| 66 | `by_currency::hong_kong_dollar` | Hong Kong Dollar | 0 | active (added 4Q25) |
| 67 | `by_currency::chinese_renminbi` | Chinese Renminbi | 0 | active (added 4Q25) |
| 68 | `by_currency::others` | Others | 0 | active (added 4Q25) |
| 69 | `by_currency::total_by_currency` | Total By Currency | 0 | active (added 4Q25) |
| 70 | `by_geography_1` | By Geography 1/ | 0 | active (added 4Q25) |
| 71 | `by_geography_1::singapore` | Singapore | 0 | active (added 4Q25) |
| 72 | `by_geography_1::malaysia` | Malaysia | 0 | active (added 4Q25) |
| 73 | `by_geography_1::indonesia` | Indonesia | 0 | active (added 4Q25) |
| 74 | `by_geography_1::greater_china` | Greater China | 0 | active (added 4Q25) |
| 75 | `by_geography_1::other_asia_pacific` | Other Asia Pacific | 0 | active (added 4Q25) |
| 76 | `by_geography_1::rest_of_the_world` | Rest of the World | 0 | active (added 4Q25) |
| 77 | `by_geography_1::total_by_geography` | Total By Geography | 0 | active (added 4Q25) |
| 78 | `note_1_loans_by_geography_are_determined_based_on_where_the_credit_risk_resides_which_may_be_different_from_the_borrower_s_country_of_residence_or_the_booking_location_of_the_loans` | Note:
1. Loans by geography are determined based on where the credit risk resides, which may be different from the borrower’s country of residence or the booking location of the loans. | 0 | active (added 4Q25) |

## `FS_HIGHLIGHTS_COMBINED` (12 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `loans` | Loans | 0 | active (added 4Q25) |
| 1 | `loans::in_constant_currency_terms` | % ∆ in constant currency terms | 0 | active (added 4Q25) |
| 2 | `deposits` | Deposits | 0 | active (added 4Q25) |
| 3 | `deposits::of_which_casa_deposits` | of which: CASA deposits | 0 | active (added 4Q25) |
| 4 | `casa_ratio` | CASA ratio | 0 | active (added 4Q25) |
| 5 | `leverage_ratio_1` | Leverage ratio 1/ | 0 | active (added 4Q25) |
| 6 | `all_ccy_lcr_for_quarter_ended` | All-ccy LCR (for quarter ended) | 0 | active (added 4Q25) |
| 7 | `cet_car` | CET1 CAR | 0 | active (added 4Q25) |
| 8 | `cet_car::transitional_final_basel_iii_reforms_1` | Transitional final Basel III reforms 1/ | 0 | active (added 4Q25) |
| 9 | `cet_car::fully_phased_in_final_basel_iii_reforms_2` | Fully phased-in final Basel III reforms 2/ | 0 | active (added 4Q25) |
| 10 | `1_computed_based_on_mas_final_basel_iii_reform_rules_with_effect_from_1_july_2024` | 1/ Computed based on MAS’ final Basel III reform rules with effect from 1 July 2024. | 0 | active (added 4Q25) |
| 11 | `2_assumed_the_position_at_period_end_was_subject_to_the_full_application_of_final_basel_iii_reforms_which_will_take_effect_on_1_january_2029` | 2/ Assumed the position at period end was subject to the full application of final Basel III reforms, which will take effect on 1 January 2029. | 0 | active (added 4Q25) |

## `FS_INCOME_STATUTORY` (33 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `interest_income` | Interest income | 0 | active (added 4Q25) |
| 1 | `interest_expense` | Interest expense | 0 | active (added 4Q25) |
| 2 | `net_interest_income` | Net interest income | 0 | active (added 4Q25) |
| 3 | `insurance_service_results_from_life_insurance_2` | Insurance service results from life insurance (2) | 0 | active (added 4Q25) |
| 4 | `net_investment_income_from_life_insurance` | Net investment income from life insurance | 0 | active (added 4Q25) |
| 5 | `net_insurance_financial_result_from_life_insurance` | Net insurance financial result from life insurance | 0 | active (added 4Q25) |
| 6 | `insurance_service_results_from_general_insurance` | Insurance service results from general insurance | 0 | active (added 4Q25) |
| 7 | `fees_and_commissions_net` | Fees and commissions (net) | 0 | active (added 4Q25) |
| 8 | `net_trading_income` | Net trading income | 0 | active (added 4Q25) |
| 9 | `other_income` | Other income | 0 | active (added 4Q25) |
| 10 | `non_interest_income` | Non-interest income | 0 | active (added 4Q25) |
| 11 | `total_income` | Total income | 0 | active (added 4Q25) |
| 12 | `staff_costs` | Staff costs | 0 | active (added 4Q25) |
| 13 | `other_operating_expenses` | Other operating expenses | 0 | active (added 4Q25) |
| 14 | `total_operating_expenses` | Total operating expenses | 0 | active (added 4Q25) |
| 15 | `operating_profit_before_allowances_and_amortisation` | Operating profit before allowances and amortisation | 0 | active (added 4Q25) |
| 16 | `amortisation_of_intangible_assets` | Amortisation of intangible assets | 0 | active (added 4Q25) |
| 17 | `allowances_for_loans_and_other_assets` | Allowances for loans and other assets | 0 | active (added 4Q25) |
| 18 | `operating_profit_after_allowances_and_amortisation` | Operating profit after allowances and amortisation | 0 | active (added 4Q25) |
| 19 | `share_of_results_of_associates_net_of_tax` | Share of results of associates, net of tax | 0 | active (added 4Q25) |
| 20 | `profit_before_income_tax` | Profit before income tax | 0 | active (added 4Q25) |
| 21 | `income_tax_expense` | Income tax expense | 0 | active (added 4Q25) |
| 22 | `profit_for_the_period_year` | Profit for the period/year | 0 | active (added 4Q25) |
| 23 | `attributable_to` | Attributable to: | 0 | active (added 4Q25) |
| 24 | `attributable_to::equity_holders_of_the_bank` | Equity holders of the Bank | 0 | active (added 4Q25) |
| 25 | `attributable_to::non_controlling_interests` | Non-controlling interests | 0 | active (added 4Q25) |
| 26 | `attributable_to_total` | Attributable to: Total | 0 | active (added 4Q25) |
| 27 | `earnings_per_share` | Earnings per share (S$) | 0 | active (added 4Q25) |
| 28 | `earnings_per_share::basic` | Basic | 0 | active (added 4Q25) |
| 29 | `earnings_per_share::diluted` | Diluted | 0 | active (added 4Q25) |
| 30 | `1_unaudited_and_unreviewed` | (1) Unaudited and unreviewed. | 0 | active (added 4Q25) |
| 31 | `2_includes_insurance_revenue_of_s_6_466_million_and_s_3_264_million_for_2025_and_2h_respectively_and_insurance_service_expense_of_s_5_634_million_and_s_2_943_million_for_2025_and_2h_respectively` | (2) Includes insurance revenue of S$6,466 million and S$3,264 million for 2025 and 2H2025 respectively (2024: S$6,180 million and 2H2024: S$3,252 million) and insurance service expense of S$5,634 million and S$2,943 million for 2025 and 2H2025 respectively (2024: S$5,701 million and 2H2024: S$3,153 million). | 0 | active (added 4Q25) |
| 32 | `3_represents_amounts_less_than_s_0_5_million` | (3) # represents amounts less than S$0.5 million. | 0 | active (added 4Q25) |

## `FS_NII_DETAIL` (27 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `gross_fee_and_commission_income` | Gross fee and commission income | 0 | active (added 4Q25) |
| 1 | `brokerage` | Brokerage | 0 | active (added 4Q25) |
| 2 | `credit_card` | Credit card | 0 | active (added 4Q25) |
| 3 | `fund_management` | Fund management | 0 | active (added 4Q25) |
| 4 | `guarantees` | Guarantees | 0 | active (added 4Q25) |
| 5 | `investment_banking` | Investment banking | 0 | active (added 4Q25) |
| 6 | `loan_related` | Loan-related | 0 | active (added 4Q25) |
| 7 | `service_charges` | Service charges | 0 | active (added 4Q25) |
| 8 | `trade_related_and_remittances` | Trade-related and remittances | 0 | active (added 4Q25) |
| 9 | `wealth_management_2` | Wealth management (2) | 0 | active (added 4Q25) |
| 10 | `others` | Others | 0 | active (added 4Q25) |
| 11 | `gross_fee_and_commission_income_total` | Gross fee and commission income total | 0 | active (added 4Q25) |
| 12 | `fee_and_commission_expense` | Fee and commission expense | 0 | active (added 4Q25) |
| 13 | `fees_and_commissions_net` | Fees and commissions (net) | 0 | active (added 4Q25) |
| 15 | `2_includes_trust_and_custodian_fees` | (2) Includes trust and custodian fees. | 0 | active (added 4Q25) |
| 16 | `interest_income` | Interest income | 0 | active (added 4Q25) |
| 17 | `interest_income::loans_to_customers` | Loans to customers | 0 | active (added 4Q25) |
| 18 | `interest_income::placements_with_and_loans_to_banks` | Placements with and loans to banks | 0 | active (added 4Q25) |
| 19 | `interest_income::other_interest_earning_assets` | Other interest-earning assets | 0 | active (added 4Q25) |
| 20 | `interest_income::total_interest_income` | Total interest income | 0 | active (added 4Q25) |
| 21 | `interest_expense` | Interest expense | 0 | active (added 4Q25) |
| 22 | `interest_expense::deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 4Q25) |
| 23 | `interest_expense::deposits_and_balances_of_banks` | Deposits and balances of banks | 0 | active (added 4Q25) |
| 24 | `interest_expense::other_borrowings` | Other borrowings | 0 | active (added 4Q25) |
| 25 | `interest_expense::total_interest_expense` | Total interest expense | 0 | active (added 4Q25) |
| 26 | `net_interest_income` | Net interest income | 0 | active (added 4Q25) |
| 27 | `1_unaudited_and_unreviewed` | (1) Unaudited and unreviewed. | 0 | active (added 4Q25) |

## `FS_NPA_COVERAGE` (36 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `singapore` | Singapore | 0 | active (added 4Q25) |
| 3 | `malaysia` | Malaysia | 0 | active (added 4Q25) |
| 6 | `indonesia` | Indonesia | 0 | active (added 4Q25) |
| 9 | `greater_china` | Greater China | 0 | active (added 4Q25) |
| 12 | `other_asia_pacific` | Other Asia Pacific | 0 | active (added 4Q25) |
| 15 | `rest_of_the_world` | Rest of the World | 0 | active (added 4Q25) |
| 18 | `group` | Group | 0 | active (added 4Q25) |
| 19 | `31_dec_2025` | 31 Dec 2025 | 0 | active (added 4Q25) |
| 20 | `31_dec_2024` | 31 Dec 2024 | 0 | active (added 4Q25) |
| 21 | `notes` | Notes: | 0 | active (added 4Q25) |
| 22 | `1_refer_to_non_performing_assets_comprise_loans_to_customers_debt_securities_and_contingent_liabilities` | 1. Refer to Non-performing assets. Comprise loans to customers, debt securities and contingent liabilities. | 0 | active (added 4Q25) |
| 23 | `2_refer_to_non_performing_loans_exclude_debt_securities_and_contingent_liabilities` | 2. Refer to Non-performing loans. Exclude debt securities and contingent liabilities. | 0 | active (added 4Q25) |
| 48 | `npls_by_industry` | NPLs by Industry | 0 | active (added 4Q25) |
| 49 | `loans_and_advances` | Loans and advances | 0 | active (added 4Q25) |
| 50 | `agriculture_mining_and_quarrying` | Agriculture, mining and quarrying | 0 | active (added 4Q25) |
| 51 | `manufacturing` | Manufacturing | 0 | active (added 4Q25) |
| 52 | `building_and_construction` | Building and construction | 0 | active (added 4Q25) |
| 53 | `housing_loans` | Housing loans | 0 | active (added 4Q25) |
| 54 | `general_commerce` | General commerce | 0 | active (added 4Q25) |
| 55 | `transport_storage_and_communication` | Transport, storage and communication | 0 | active (added 4Q25) |
| 56 | `financial_institutions_investment_and_holding_companies` | Financial institutions, investment and holding companies | 0 | active (added 4Q25) |
| 57 | `professionals_and_individuals` | Professionals and individuals | 0 | active (added 4Q25) |
| 58 | `others` | Others | 0 | active (added 4Q25) |
| 59 | `total_npls` | Total NPLs | 0 | active (added 4Q25) |
| 60 | `classified_debt_securities` | Classified debt securities | 0 | active (added 4Q25) |
| 61 | `classified_contingent_liabilities` | Classified contingent liabilities | 0 | active (added 4Q25) |
| 62 | `total_npas` | Total NPAs | 0 | active (added 4Q25) |
| 63 | `over_180_days` | Over 180 days | 0 | active (added 4Q25) |
| 64 | `over_90_to_180_days` | Over 90 to 180 days | 0 | active (added 4Q25) |
| 65 | `30_to_90_days` | 30 to 90 days | 0 | active (added 4Q25) |
| 66 | `less_than_30_days` | Less than 30 days | 0 | active (added 4Q25) |
| 67 | `not_overdue` | Not overdue | 0 | active (added 4Q25) |
| 69 | `substandard` | Substandard | 0 | active (added 4Q25) |
| 70 | `doubtful` | Doubtful | 0 | active (added 4Q25) |
| 71 | `loss` | Loss | 0 | active (added 4Q25) |
| 72 | `total` | Total | 0 | active (added 4Q25) |

## `FS_PER_SHARE` (44 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `selected_income_statement_items` | Selected Income Statement Items | 0 | active (added 1Q25) |
| 1 | `selected_income_statement_items::net_interest_income` | Net interest income | 0 | active (added 1Q25) |
| 2 | `selected_income_statement_items::non_interest_income` | Non-interest income | 0 | active (added 1Q25) |
| 3 | `selected_income_statement_items::total_income` | Total income | 0 | active (added 1Q25) |
| 4 | `selected_income_statement_items::operating_expenses` | Operating expenses | 0 | active (added 1Q25) |
| 5 | `selected_income_statement_items::operating_profit_before_allowances_and_amortisation` | Operating profit before allowances and amortisation | 0 | active (added 1Q25) |
| 6 | `selected_income_statement_items::amortisation_of_intangible_assets` | Amortisation of intangible assets | 0 | active (added 1Q25) |
| 7 | `selected_income_statement_items::allowances_for_impaired_assets` | Allowances for impaired assets | 0 | active (added 1Q25) |
| 8 | `selected_income_statement_items::allowances_charge_write_back_for_non_impaired_assets` | Allowances (charge)/write-back for non-impaired assets | 0 | active (added 1Q25) |
| 9 | `selected_income_statement_items::operating_profit_after_allowances_and_amortisation` | Operating profit after allowances and amortisation | 0 | active (added 1Q25) |
| 10 | `selected_income_statement_items::share_of_results_of_associates_net_of_tax` | Share of results of associates, net of tax | 0 | active (added 1Q25) |
| 11 | `selected_income_statement_items::profit_before_income_tax` | Profit before income tax | 0 | active (added 1Q25) |
| 12 | `selected_income_statement_items::net_profit_attributable_to_equity_holders` | Net profit attributable to equity holders | 0 | active (added 1Q25) |
| 13 | `selected_income_statement_items::cash_basis_net_profit_attributable_to_equity_holders_1` | Cash basis net profit attributable to equity holders 1/ | 0 | active (added 1Q25) |
| 14 | `selected_balance_sheet_items` | Selected Balance Sheet Items | 0 | active (added 1Q25) |
| 15 | `selected_balance_sheet_items::ordinary_equity` | Ordinary equity | 0 | active (added 1Q25) |
| 16 | `selected_balance_sheet_items::equity_attributable_to_equity_holders_of_the_bank` | Equity attributable to equity holders of the Bank | 0 | active (added 1Q25) |
| 17 | `selected_balance_sheet_items::total_assets` | Total assets | 0 | active (added 1Q25) |
| 18 | `selected_balance_sheet_items::assets_excluding_investment_securities_and_other_assets_for_life_insurance_funds` | Assets excluding investment securities and other assets for life insurance funds | 0 | active (added 1Q25) |
| 19 | `selected_balance_sheet_items::net_loans_to_customers` | Net loans to customers | 0 | active (added 1Q25) |
| 20 | `selected_balance_sheet_items::deposits_of_non_bank_customers` | Deposits of non-bank customers | 0 | active (added 1Q25) |
| 21 | `selected_balance_sheet_items::goodwill_and_other_intangible_assets` | Goodwill and other intangible assets | 0 | active (added 1Q25) |
| 22 | `selected_changes_in_equity_items` | Selected Changes in Equity Items | 0 | active (added 1Q25) |
| 23 | `selected_changes_in_equity_items::total_comprehensive_income_net_of_tax` | Total comprehensive income, net of tax | 0 | active (added 1Q25) |
| 24 | `selected_changes_in_equity_items::dividends_and_distributions` | Dividends and distributions | 0 | active (added 1Q25) |
| 25 | `key_financial_ratios` | Key Financial Ratios (%) | 0 | active (added 1Q25) |
| 26 | `key_financial_ratios::return_on_equity_2` | Return on equity 2/ | 0 | active (added 1Q25) |
| 27 | `key_financial_ratios::return_on_assets_2` | Return on assets 2/ | 0 | active (added 1Q25) |
| 28 | `key_financial_ratios::net_interest_margin_2` | Net interest margin 2/ | 0 | active (added 1Q25) |
| 29 | `key_financial_ratios::non_interest_income_to_total_income` | Non-interest income to total income | 0 | active (added 1Q25) |
| 30 | `key_financial_ratios::cost_to_income` | Cost-to-income | 0 | active (added 1Q25) |
| 31 | `key_financial_ratios::loans_to_deposits` | Loans-to-deposits | 0 | active (added 1Q25) |
| 32 | `key_financial_ratios::npl_ratio` | NPL ratio | 0 | active (added 1Q25) |
| 33 | `key_financial_ratios::common_equity_tier_1_capital_adequacy_ratio` | Common Equity Tier 1 capital adequacy ratio | 0 | active (added 1Q25) |
| 34 | `key_financial_ratios::tier_1_capital_adequacy_ratio` | Tier 1 capital adequacy ratio | 0 | active (added 1Q25) |
| 35 | `key_financial_ratios::total_capital_adequacy_ratio` | Total capital adequacy ratio | 0 | active (added 1Q25) |
| 36 | `key_financial_ratios::leverage_ratio` | Leverage ratio | 0 | active (added 1Q25) |
| 37 | `key_financial_ratios::singapore_dollar_liquidity_coverage_ratio` | Singapore dollar liquidity coverage ratio | 0 | active (added 1Q25) |
| 38 | `key_financial_ratios::all_currency_liquidity_coverage_ratio` | All-currency liquidity coverage ratio | 0 | active (added 1Q25) |
| 39 | `key_financial_ratios::net_stable_funding_ratio` | Net stable funding ratio | 0 | active (added 1Q25) |
| 40 | `earnings_per_share_2` | Earnings per share (S$) 2/ | 0 | active (added 1Q25) |
| 41 | `earnings_per_share_2::basic_earnings` | Basic earnings | 0 | active (added 1Q25) |
| 42 | `earnings_per_share_2::diluted_earnings` | Diluted earnings | 0 | active (added 1Q25) |
| 43 | `earnings_per_share_2::net_asset_value_per_share` | Net asset value per share (S$) | 0 | active (added 1Q25) |

## `FS_RATIOS_KEY` (17 leaves)

| position | canonical_leaf_id | label_current | aliases | status |
|---|---|---|---|---|
| 0 | `profit_before_tax` | Profit before Tax | 0 | active (added 4Q25) |
| 1 | `group_net_profit` | Group Net Profit | 0 | active (added 4Q25) |
| 2 | `total_dividend` | Total Dividend | 0 | active (added 4Q25) |
| 3 | `roe` | ROE | 0 | active (added 4Q25) |
| 4 | `eps` | EPS | 0 | active (added 4Q25) |
| 5 | `total_income` | Total Income | 0 | active (added 4Q25) |
| 6 | `total_income::net_interest_income` | Net Interest Income | 0 | active (added 4Q25) |
| 7 | `total_income::non_interest_income` | Non-Interest Income | 0 | active (added 4Q25) |
| 8 | `operating_expenses` | Operating Expenses | 0 | active (added 4Q25) |
| 9 | `net_interest_margin` | Net Interest Margin | 0 | active (added 4Q25) |
| 10 | `credit_costs` | Credit Costs | 0 | active (added 4Q25) |
| 11 | `customer_loans_change_based_on_constant_currency_terms` | Customer Loans (% change based on constant currency terms) | 0 | active (added 4Q25) |
| 12 | `customer_deposits` | Customer Deposits | 0 | active (added 4Q25) |
| 13 | `npl_ratio` | NPL Ratio | 0 | active (added 4Q25) |
| 14 | `cet_car` | CET1 CAR | 0 | active (added 4Q25) |
| 15 | `cet_car::transitional_final_basel_iii_reforms` | Transitional final Basel III reforms | 0 | active (added 4Q25) |
| 16 | `cet_car::fully_phased_in_final_basel_iii_reforms` | Fully phased-in final Basel III reforms | 0 | active (added 4Q25) |

