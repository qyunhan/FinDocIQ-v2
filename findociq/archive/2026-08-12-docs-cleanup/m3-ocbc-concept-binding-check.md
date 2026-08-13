# M3 concept-binding check — OCBC

For every `concept_key` with a `OCBC` binding in `bank_line_map` (map_status != 'deprecated'), confirms the bound (table_type_id, row_label_norm, parent_label_norm) resolves through the M2 gate.

**99 resolved**, **141 unresolved** out of 240 bindings.

## The 4 concepts shifted by the OCBC dedup fix — summary

| concept_key | bindings | at least 1 resolves? |
|---|---|---|
| `bs.nav_per_share` | 2 | ⚠️ **NO — every binding for this concept is unresolved** |
| `pnl.eps.basic` | 2 | ✅ yes |
| `pnl.eps.diluted` | 2 | ✅ yes |
| `reg.capital.cet1_ratio` | 12 | ✅ yes |

## The 4 concepts shifted by the OCBC dedup fix — detail

### `bs.nav_per_share` — 2 binding(s)

| table_type_id | parent | leaf | map_status | resolution |
|---|---|---|---|---|
| `FS_BALANCE_STATUTORY` | assets | net_asset_value_per_ordinary_share_s | human_confirmed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_RATIOS_KEY` | (none) | net_asset_value_per_share | human_confirmed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |

### `pnl.eps.basic` — 2 binding(s)

| table_type_id | parent | leaf | map_status | resolution |
|---|---|---|---|---|
| `FS_INCOME_STATUTORY` | earnings_per_share | basic | human_confirmed | resolved via direct -> `earnings_per_share::basic` |
| `FS_RATIOS_KEY` | earnings_per_share | basic_earnings | human_confirmed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |

### `pnl.eps.diluted` — 2 binding(s)

| table_type_id | parent | leaf | map_status | resolution |
|---|---|---|---|---|
| `FS_INCOME_STATUTORY` | earnings_per_share | diluted | human_confirmed | resolved via direct -> `earnings_per_share::diluted` |
| `FS_RATIOS_KEY` | earnings_per_share | diluted_earnings | human_confirmed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |

### `reg.capital.cet1_ratio` — 12 binding(s)

| table_type_id | parent | leaf | map_status | resolution |
|---|---|---|---|---|
| `FS_BALANCE_SELECTED` | key_financial_ratios | common_equity_tier_1_capital_adequacy_ratio | ai_proposed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_CAPITAL_ADEQUACY` | capital_adequacy_ratios | common_equity_tier_1 | ai_proposed | resolved via direct -> `capital_adequacy_ratios::common_equity_tier_1` |
| `FS_HIGHLIGHTS_COMBINED` | capital_adequacy_ratios_8 | common_equity_tier_1 | ai_proposed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_HIGHLIGHTS_COMBINED` | key_financial_ratios | common_equity_tier_1_capital_adequacy_ratio | ai_proposed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_HIGHLIGHTS_COMBINED` | key_financial_ratios | common_equity_tier_1_capital_adequacy_ratio_3 | ai_proposed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_INCOME_SELECTED` | key_financial_ratios | common_equity_tier_1_capital_adequacy_ratio | ai_proposed | UNRESOLVED — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED) |
| `FS_PER_SHARE` | key_financial_ratios | common_equity_tier_1_capital_adequacy_ratio | ai_proposed | resolved via direct -> `key_financial_ratios::common_equity_tier_1_capital_adequacy_ratio` |
| `FS_RATIOS_KEY` | (none) | cet_car | ai_proposed | resolved via direct -> `cet_car` |
| `FS_RATIOS_KEY` | capital_adequacy_ratios | common_equity_tier_1 | human_confirmed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_RATIOS_KEY` | key_financial_ratios | common_equity_tier_1_capital_adequacy_ratio | ai_proposed | UNRESOLVED — no direct, alias, or deprecated canonical_leaf match |
| `FS_RATIOS_KEY` | cet_car | fully_phased_in_final_basel_iii_reforms | ai_proposed | resolved via direct -> `cet_car::fully_phased_in_final_basel_iii_reforms` |
| `FS_RATIOS_KEY` | cet_car | transitional_final_basel_iii_reforms | ai_proposed | resolved via direct -> `cet_car::transitional_final_basel_iii_reforms` |

## All unresolved bindings (not just the 4 shifted concepts)

- **pnl.provisions.total** — `FS_ALLOWANCES` / parent=(none) leaf=allowances_charge_write_back_for_loans_and_other_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage3_sp** — `FS_ALLOWANCES` / parent=allowances_write_back leaf=impaired_loans (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage3_sp** — `FS_ALLOWANCES` / parent=allowances_write_back leaf=impaired_loans_total (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage12_gp** — `FS_ALLOWANCES` / parent=allowances_charge_write_back_for_loans_and_other_assets leaf=non_impaired (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage12_gp** — `FS_ALLOWANCES` / parent=allowances_for_loans_and_other_assets leaf=non_impaired (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.npa** — `FS_ALLOWANCES` / parent=(none) leaf=non_performing_assets_npas (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.npa** — `FS_ALLOWANCES` / parent=non_performing_assets_npas leaf=non_performing_assets_npas (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_ALLOWANCES` / parent=(none) leaf=non_performing_loan_npl_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_ALLOWANCES` / parent=non_performing_assets_npas leaf=non_performing_loan_npl_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage3_sp** — `FS_ALLOWANCES` / parent=allowances_charge_write_back_for_loans_and_other_assets leaf=of_which_impaired (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage3_sp** — `FS_ALLOWANCES` / parent=allowances_for_loans_and_other_assets leaf=of_which_impaired (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.npa** — `FS_ALLOWANCES` / parent=total_loans leaf=of_which_impaired_loans (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.opex.staff** — `FS_ALLOWANCES` / parent=(none) leaf=staff_costs (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_gross** — `FS_ALLOWANCES` / parent=(none) leaf=total_loans (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.opex.total** — `FS_ALLOWANCES` / parent=(none) leaf=total_operating_expenses (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=all_currency_liquidity_coverage_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.capital.cet1_ratio** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=common_equity_tier_1_capital_adequacy_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.cir** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=cost_to_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.ldr** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=loans_to_deposits (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.nim** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=net_interest_margin_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.nsfr_ratio** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=net_stable_funding_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.noninterest_share** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=non_interest_income_to_total_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=npl_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.roe** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=return_on_equity_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_BALANCE_SELECTED` / parent=key_financial_ratios leaf=singapore_dollar_liquidity_coverage_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.nav_per_share** — `FS_BALANCE_STATUTORY` / parent=assets leaf=net_asset_value_per_ordinary_share_s (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_gross** — `FS_CUSTOMER_LOANS` / parent=(none) leaf=total_by_currency (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_gross** — `FS_CUSTOMER_LOANS` / parent=(none) leaf=total_by_geography (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_gross** — `FS_CUSTOMER_LOANS` / parent=(none) leaf=total_by_industry (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_gross** — `FS_CUSTOMER_LOANS` / parent=(none) leaf=total_by_maturity (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.credit.allowances_total** — `FS_EXPENSES_DETAIL` / parent=(none) leaf=allowances_for_loans_and_other_assets (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **pnl.provisions.total** — `FS_EXPENSES_DETAIL` / parent=(none) leaf=allowances_write_back (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **pnl.provisions.stage3_sp** — `FS_EXPENSES_DETAIL` / parent=allowances_write_back leaf=impaired_loans (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **pnl.provisions.stage3_sp** — `FS_EXPENSES_DETAIL` / parent=allowances_write_back leaf=impaired_loans_total (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **pnl.provisions.stage12_gp** — `FS_EXPENSES_DETAIL` / parent=allowances_write_back leaf=non_impaired_loans (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **pnl.opex.staff** — `FS_EXPENSES_DETAIL` / parent=(none) leaf=staff_costs (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **pnl.opex.total** — `FS_EXPENSES_DETAIL` / parent=(none) leaf=total_operating_expenses (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_EXPENSES_DETAIL)
- **reg.liquidity.lcr_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=liquidity_coverage_ratios_6_8 leaf=all_currency (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=all_currency_liquidity_coverage_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage12_gp** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=allowances_charge_write_back_for_non_impaired_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage3_sp** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=allowances_for_impaired_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.opex.amortisation_intangibles** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=amortisation_of_intangible_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.net_attributable** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=cash_basis_net_profit_attributable_to_equity_holders_1 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.capital.cet1_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=capital_adequacy_ratios_8 leaf=common_equity_tier_1 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.capital.cet1_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=common_equity_tier_1_capital_adequacy_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.capital.cet1_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=common_equity_tier_1_capital_adequacy_ratio_3 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.cir** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=cost_to_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.cir** — `FS_HIGHLIGHTS_COMBINED` / parent=revenue_mix_efficiency_ratios leaf=cost_to_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.liabilities.customer_deposits** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_balance_sheet_items leaf=deposits_of_non_bank_customers (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.equity.shareholders** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_balance_sheet_items leaf=equity_attributable_to_equity_holders_of_the_bank (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=liquidity_coverage_ratios_6_8 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.ldr** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=loans_to_deposits (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.ldr** — `FS_HIGHLIGHTS_COMBINED` / parent=revenue_mix_efficiency_ratios leaf=loans_to_deposits (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.nii.net** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=net_interest_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.nim** — `FS_HIGHLIGHTS_COMBINED` / parent=revenue_mix_efficiency_ratios leaf=net_interest_margin (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.nim** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=net_interest_margin_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_net** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_balance_sheet_items leaf=net_loans_to_customers (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.net_attributable** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=net_profit_attributable_to_equity_holders (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.nsfr_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=net_stable_funding_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.nsfr_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=net_stable_funding_ratio_7_8 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.noninterest.total** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=non_interest_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.noninterest_share** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=non_interest_income_to_total_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.noninterest_share** — `FS_HIGHLIGHTS_COMBINED` / parent=revenue_mix_efficiency_ratios leaf=non_interest_income_to_total_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=npl_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_HIGHLIGHTS_COMBINED` / parent=revenue_mix_efficiency_ratios leaf=npl_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.opex.total** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=operating_expenses (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.operating** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=operating_profit_before_allowances_and_amortisation (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.equity.shareholders** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_balance_sheet_items leaf=ordinary_equity (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.pretax** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=profit_before_income_tax (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.roe** — `FS_HIGHLIGHTS_COMBINED` / parent=performance_ratios leaf=return_on_equity_1_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.roe** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=return_on_equity_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.associates** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=share_of_results_of_associates_net_of_tax (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_HIGHLIGHTS_COMBINED` / parent=key_financial_ratios leaf=singapore_dollar_liquidity_coverage_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.total** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_balance_sheet_items leaf=total_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.income.total** — `FS_HIGHLIGHTS_COMBINED` / parent=selected_income_statement_items leaf=total_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=all_currency_liquidity_coverage_ratio (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.provisions.stage12_gp** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=allowances_charge_write_back_for_non_impaired_assets (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.provisions.stage3_sp** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=allowances_for_impaired_assets (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.opex.amortisation_intangibles** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=amortisation_of_intangible_assets (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.profit.net_attributable** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=cash_basis_net_profit_attributable_to_equity_holders_1 (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **reg.capital.cet1_ratio** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=common_equity_tier_1_capital_adequacy_ratio (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **ratio.cir** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=cost_to_income (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **bs.liabilities.customer_deposits** — `FS_INCOME_SELECTED` / parent=selected_balance_sheet_items leaf=deposits_of_non_bank_customers (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **bs.equity.shareholders** — `FS_INCOME_SELECTED` / parent=selected_balance_sheet_items leaf=equity_attributable_to_equity_holders_of_the_bank (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **ratio.ldr** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=loans_to_deposits (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.nii.net** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=net_interest_income (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **ratio.nim** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=net_interest_margin_2 (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **bs.assets.customer_loans_net** — `FS_INCOME_SELECTED` / parent=selected_balance_sheet_items leaf=net_loans_to_customers (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.profit.net_attributable** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=net_profit_attributable_to_equity_holders (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **reg.liquidity.nsfr_ratio** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=net_stable_funding_ratio (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.noninterest.total** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=non_interest_income (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **ratio.noninterest_share** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=non_interest_income_to_total_income (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **ratio.npl** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=npl_ratio (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.opex.total** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=operating_expenses (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.profit.operating** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=operating_profit_before_allowances_and_amortisation (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **bs.equity.shareholders** — `FS_INCOME_SELECTED` / parent=selected_balance_sheet_items leaf=ordinary_equity (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.profit.pretax** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=profit_before_income_tax (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **ratio.roe** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=return_on_equity_2 (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.associates** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=share_of_results_of_associates_net_of_tax (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **reg.liquidity.lcr_ratio** — `FS_INCOME_SELECTED` / parent=key_financial_ratios leaf=singapore_dollar_liquidity_coverage_ratio (ai_proposed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **bs.assets.total** — `FS_INCOME_SELECTED` / parent=selected_balance_sheet_items leaf=total_assets (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **pnl.income.total** — `FS_INCOME_SELECTED` / parent=selected_income_statement_items leaf=total_income (human_confirmed) — no canonical leaf set exists yet for (OCBC, FS_INCOME_SELECTED)
- **reg.liquidity.lcr_ratio** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=all_currency_liquidity_coverage_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage12_gp** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=allowances_charge_write_back_for_non_impaired_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.provisions.stage3_sp** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=allowances_for_impaired_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.opex.amortisation_intangibles** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=amortisation_of_intangible_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.eps.basic** — `FS_RATIOS_KEY` / parent=earnings_per_share leaf=basic_earnings (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.net_attributable** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=cash_basis_net_profit_attributable_to_equity_holders_1 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.capital.cet1_ratio** — `FS_RATIOS_KEY` / parent=capital_adequacy_ratios leaf=common_equity_tier_1 (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **reg.capital.cet1_ratio** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=common_equity_tier_1_capital_adequacy_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.cir** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=cost_to_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.cir** — `FS_RATIOS_KEY` / parent=revenue_mix_efficiency_ratios leaf=cost_to_income (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_gross** — `FS_RATIOS_KEY` / parent=(none) leaf=customer_loans (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.liabilities.customer_deposits** — `FS_RATIOS_KEY` / parent=selected_balance_sheet_items leaf=deposits_of_non_bank_customers (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.eps.diluted** — `FS_RATIOS_KEY` / parent=earnings_per_share leaf=diluted_earnings (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **bs.equity.shareholders** — `FS_RATIOS_KEY` / parent=selected_balance_sheet_items leaf=equity_attributable_to_equity_holders_of_the_bank (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.ldr** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=loans_to_deposits (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.nav_per_share** — `FS_RATIOS_KEY` / parent=(none) leaf=net_asset_value_per_share (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.nii.net** — `FS_RATIOS_KEY` / parent=(none) leaf=net_interest_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.nii.net** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=net_interest_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.nim** — `FS_RATIOS_KEY` / parent=revenue_mix_efficiency_ratios leaf=net_interest_margin (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.nim** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=net_interest_margin_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.customer_loans_net** — `FS_RATIOS_KEY` / parent=selected_balance_sheet_items leaf=net_loans_to_customers (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.net_attributable** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=net_profit_attributable_to_equity_holders (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.nsfr_ratio** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=net_stable_funding_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.noninterest.total** — `FS_RATIOS_KEY` / parent=(none) leaf=non_interest_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.noninterest.total** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=non_interest_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.noninterest_share** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=non_interest_income_to_total_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=npl_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.npl** — `FS_RATIOS_KEY` / parent=revenue_mix_efficiency_ratios leaf=npl_ratio (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.opex.total** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=operating_expenses (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.operating** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=operating_profit_before_allowances_and_amortisation (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.equity.shareholders** — `FS_RATIOS_KEY` / parent=selected_balance_sheet_items leaf=ordinary_equity (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.profit.pretax** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=profit_before_income_tax (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.roa** — `FS_RATIOS_KEY` / parent=performance_ratios leaf=return_on_assets (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.roe** — `FS_RATIOS_KEY` / parent=performance_ratios leaf=return_on_equity (human_confirmed) — no direct, alias, or deprecated canonical_leaf match
- **ratio.roe** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=return_on_equity_2 (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.associates** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=share_of_results_of_associates_net_of_tax (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **reg.liquidity.lcr_ratio** — `FS_RATIOS_KEY` / parent=key_financial_ratios leaf=singapore_dollar_liquidity_coverage_ratio (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **bs.assets.total** — `FS_RATIOS_KEY` / parent=selected_balance_sheet_items leaf=total_assets (ai_proposed) — no direct, alias, or deprecated canonical_leaf match
- **pnl.income.total** — `FS_RATIOS_KEY` / parent=selected_income_statement_items leaf=total_income (ai_proposed) — no direct, alias, or deprecated canonical_leaf match

