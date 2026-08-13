-- ============================================================================
-- TEMPLATE REGISTRY — KM1 (Key Metrics) + LCR (Liquidity Coverage Ratio
-- Disclosure) seeds. Companion to template_registry.sql (which owns the
-- CREATE TABLE DDL and the NSFR pilot). ADDITIVE: this file only
-- DELETE+INSERTs the 'km1' and 'lcr' rows into the already-existing
-- template_row/template_col/template_cell/concept_map tables; it does NOT
-- (re)create any table. Reviewed and applied to findociq/db/final.db
-- 2026-07-02 (km1 25r/5c/12 aliases, lcr 27r/2c/16 cells/3 aliases).
-- Idempotent — safe to re-apply.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- KM1 rows — the 25 numbered line items of Table 11-2, in reading order.
-- The 7 grey band captions ("Available capital (amounts)", "RWA (amounts)",
-- "Risk-based capital ratios ...", "Additional CET1 buffer requirements ...",
-- "Leverage Ratio", "Liquidity Coverage Ratio", "Net Stable Funding Ratio")
-- are UNNUMBERED section headers, not form line items — they carry no datapoint
-- and are NOT modelled as template rows (cf. NSFR's excluded 'RSF Item' band).
-- Rows 4a/5a/6a/7a/14a are the final-Basel-III sub-metrics (parent_line_no set
-- to the base numbered line). canonical_label = MAS's exact printed text.
-- ---------------------------------------------------------------------------
DELETE FROM template_row WHERE table_type='km1';
INSERT INTO template_row (table_type,row_ord,line_no,canonical_label,parent_line_no,concept_key) VALUES
 ('km1', 1,'1','CET1 Capital',NULL,'km1_cet1_capital'),
 ('km1', 2,'2','Tier 1 Capital',NULL,'km1_tier1_capital'),
 ('km1', 3,'3','Total capital',NULL,'km1_total_capital'),
 ('km1', 4,'4','Total RWA',NULL,'km1_total_rwa'),
 ('km1', 5,'4a','Total RWA (pre-floor)','4','km1_total_rwa_prefloor'),
 ('km1', 6,'5','CET1 ratio (%)',NULL,'km1_cet1_ratio'),
 ('km1', 7,'5a','CET1 ratio (%) (pre-floor ratio)','5','km1_cet1_ratio_prefloor'),
 ('km1', 8,'6','Tier 1 ratio (%)',NULL,'km1_tier1_ratio'),
 ('km1', 9,'6a','Tier 1 ratio (%) (pre-floor ratio)','6','km1_tier1_ratio_prefloor'),
 ('km1',10,'7','Total capital ratio (%)',NULL,'km1_total_capital_ratio'),
 ('km1',11,'7a','Total capital ratio (%) (pre-floor ratio)','7','km1_total_capital_ratio_prefloor'),
 ('km1',12,'8','Capital conservation buffer requirement (%)',NULL,'km1_ccb'),
 ('km1',13,'9','Countercyclical buffer requirement (%)',NULL,'km1_ccyb'),
 ('km1',14,'10','G-SIB and/or D-SIB additional requirements (%)',NULL,'km1_gsib_dsib_buffer'),
 ('km1',15,'11','Total of CET1 specific buffer requirements (%) (row 8 + row 9 + row 10)',NULL,'km1_total_cet1_buffer'),
 ('km1',16,'12','CET1 available after meeting the Reporting Bank''s minimum capital requirements (%)',NULL,'km1_cet1_available'),
 ('km1',17,'13','Total Leverage Ratio exposure measure',NULL,'km1_lr_exposure'),
 ('km1',18,'14','Leverage Ratio (%) (row 2 / row 13)',NULL,'km1_leverage_ratio'),
 ('km1',19,'14a','Leverage Ratio (%) incorporating mean values for SFT assets','14','km1_leverage_ratio_sft_mean'),
 ('km1',20,'15','Total High Quality Liquid Assets',NULL,'km1_lcr_hqla'),
 ('km1',21,'16','Total net cash outflow',NULL,'km1_lcr_net_outflow'),
 ('km1',22,'17','Liquidity Coverage Ratio (%)',NULL,'km1_lcr_ratio'),
 ('km1',23,'18','Total available stable funding',NULL,'km1_nsfr_asf'),
 ('km1',24,'19','Total required stable funding',NULL,'km1_nsfr_rsf'),
 ('km1',25,'20','Net Stable Funding Ratio (%)',NULL,'km1_nsfr_ratio');

-- ---------------------------------------------------------------------------
-- KM1 column axis — 5 leaf columns, one per reporting period. Table 11-2 heads
-- them (a)..(e) with the period tokens T, T-1, T-2, T-3, T-4 (T = reporting
-- period-end; T-1..T-4 = the 4 previous quarter-ends). No span/group header.
-- (Banks print concrete quarter-end dates, e.g. "31 Dec 2023"; the canonical
--  axis is the relative period, matched by col_ord/position.)
-- ---------------------------------------------------------------------------
DELETE FROM template_col WHERE table_type='km1';
INSERT INTO template_col (table_type,col_ord,canonical_header,group_label,col_key) VALUES
 ('km1',1,'T',NULL,'period_t'),
 ('km1',2,'T-1',NULL,'period_t1'),
 ('km1',3,'T-2',NULL,'period_t2'),
 ('km1',4,'T-3',NULL,'period_t3'),
 ('km1',5,'T-4',NULL,'period_t4');

-- ---------------------------------------------------------------------------
-- KM1 cell structure — NONE. Table 11-2 is a flat grid: every numbered row has
-- 5 plain open value cells (one per period), with no prescribed merges and no
-- shading in the MAS form. There is therefore no template_cell block for 'km1'
-- (contrast NSFR, whose form carries merged/grey exception cells). Any (row,col)
-- absent from template_cell is a plain open value cell by construction.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- concept_map alias additions — genuine label VARIANTS observed in real bank
-- KM1 tables that would NOT auto-resolve to the canonical MAS label (exact-norm
-- or line-number anchor). Keyed (table_type,label_norm) -> concept_key.
-- Extracted via pdfplumber from findociq/data/sources/pillar3/*, 2026-07-02.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO concept_map (table_type,label_norm,concept_key) VALUES
 -- OCBC abbreviates the liquidity/funding rows heavily (no line-anchor rescue,
 -- fuzzy < 0.5) — these are the load-bearing aliases.
 ('km1','total hqla','km1_lcr_hqla'),                                   -- OCBC 4Q23 / 1Q26
 ('km1','total nco','km1_lcr_net_outflow'),                             -- OCBC 4Q23 / 1Q26
 ('km1','lcr','km1_lcr_ratio'),                                         -- OCBC 1Q26 ("LCR (%)")
 ('km1','total asf','km1_nsfr_asf'),                                    -- OCBC 1Q26
 ('km1','total rsf','km1_nsfr_rsf'),                                    -- OCBC 1Q26
 ('km1','nsfr','km1_nsfr_ratio'),                                       -- OCBC 1Q26 ("NSFR (%)")
 -- OCBC row 11 drops "buffer" and adds "Bank"/footnote, no row-sum text.
 ('km1','total of bank cet1 specific requirements','km1_total_cet1_buffer'),  -- OCBC 4Q23 / 1Q26
 -- OCBC leverage ratio row prints just "Leverage Ratio (%)" (+footnote marker).
 ('km1','leverage ratio','km1_leverage_ratio'),                         -- OCBC 4Q23 / 1Q26
 -- DBS/UOB append "(2.5% from 2019)" to the conservation-buffer label.
 ('km1','capital conservation buffer requirement 2 5 from 2019','km1_ccb'),   -- DBS 1Q26 / UOB 1Q26
 -- DBS prefixes "Bank" to the G-SIB/D-SIB and CET1-specific-buffer rows.
 ('km1','bank g sib and or d sib additional requirements','km1_gsib_dsib_buffer'),  -- DBS 1Q26 / OCBC 4Q23
 ('km1','total of bank cet1 specific buffer requirements row 8 row 9 row 10','km1_total_cet1_buffer'),  -- DBS 1Q26
 -- UOB uses singular "requirement" for the G-SIB/D-SIB row.
 ('km1','g sib and or d sib additional requirement','km1_gsib_dsib_buffer');  -- UOB 4Q23 / 1Q26


-- ---------------------------------------------------------------------------
-- LCR row axis — 23 numbered lines + 4 section-header band rows, in reading
-- order. Section headers (HIGH-QUALITY LIQUID ASSETS / CASH OUTFLOWS / CASH
-- INFLOWS / TOTAL ADJUSTED VALUE) carry no line_no and no value (see
-- template_cell). canonical_label = the notice's exact Appendix 1 text.
-- ---------------------------------------------------------------------------
DELETE FROM template_row WHERE table_type='lcr';
INSERT INTO template_row (table_type,row_ord,line_no,canonical_label,parent_line_no,concept_key) VALUES
 ('lcr', 1,NULL,'HIGH-QUALITY LIQUID ASSETS',NULL,'hqla_section'),
 ('lcr', 2,'1','Total high-quality liquid assets (HQLA)',NULL,'hqla_total'),
 ('lcr', 3,NULL,'CASH OUTFLOWS',NULL,'outflows_section'),
 ('lcr', 4,'2','Retail deposits and deposits from small business customers, of which:',NULL,'of_retail'),
 ('lcr', 5,'3','Stable deposits','2','of_retail_stable'),
 ('lcr', 6,'4','Less stable deposits','2','of_retail_less'),
 ('lcr', 7,'5','Unsecured wholesale funding, of which:',NULL,'of_unsec_ws'),
 ('lcr', 8,'6','Operational deposits (all counterparties) and deposits in networks of cooperative banks','5','of_ws_op'),
 ('lcr', 9,'7','Non-operational deposits (all counterparties)','5','of_ws_nonop'),
 ('lcr',10,'8','Unsecured debt','5','of_ws_debt'),
 ('lcr',11,'9','Secured wholesale funding',NULL,'of_sec_ws'),
 ('lcr',12,'10','Additional requirements, of which:',NULL,'of_addl'),
 ('lcr',13,'11','Outflows related to derivative exposures and other collateral requirements','10','of_addl_deriv'),
 ('lcr',14,'12','Outflows related to loss of funding on debt products','10','of_addl_debtfund'),
 ('lcr',15,'13','Credit and liquidity facilities','10','of_addl_facilities'),
 ('lcr',16,'14','Other contractual funding obligations',NULL,'of_other_contractual'),
 ('lcr',17,'15','Other contingent funding obligations',NULL,'of_other_contingent'),
 ('lcr',18,'16','TOTAL CASH OUTFLOWS',NULL,'of_total'),
 ('lcr',19,NULL,'CASH INFLOWS',NULL,'inflows_section'),
 ('lcr',20,'17','Secured lending (eg reverse repos)',NULL,'if_secured'),
 ('lcr',21,'18','Inflows from fully performing exposures',NULL,'if_performing'),
 ('lcr',22,'19','Other cash inflows',NULL,'if_other'),
 ('lcr',23,'20','TOTAL CASH INFLOWS',NULL,'if_total'),
 ('lcr',24,NULL,'TOTAL ADJUSTED VALUE',NULL,'adjusted_section'),
 ('lcr',25,'21','TOTAL HQLA',NULL,'adj_hqla_total'),
 ('lcr',26,'22','TOTAL NET CASH OUTFLOWS',NULL,'adj_net_outflows'),
 ('lcr',27,'23','LIQUIDITY COVERAGE RATIO (%)',NULL,'lcr_ratio');

-- ---------------------------------------------------------------------------
-- LCR column axis — 2 leaf value columns, no span header above them.
-- ---------------------------------------------------------------------------
DELETE FROM template_col WHERE table_type='lcr';
INSERT INTO template_col (table_type,col_ord,canonical_header,group_label,col_key) VALUES
 ('lcr',1,'Total Unweighted Value (average)',NULL,'unweighted'),
 ('lcr',2,'Total Weighted Value (average)',NULL,'weighted');

-- ---------------------------------------------------------------------------
-- LCR cell structure — the MAS form's fixed merges/shading (geometric ground
-- truth read from MAS_Notice_651.pdf p.3 rects, 2026-07-02: dark pattern fill
-- = shaded/no-value, 0.698 = section band, 0.851 = open value cell). Rows not
-- listed = two plain open value cells. col_end > col_start = merged;
-- shaded=1 grey band; shaded cells carry no value (expects_value=0).
-- ---------------------------------------------------------------------------
DELETE FROM template_cell WHERE table_type='lcr';
INSERT INTO template_cell (table_type,row_ord,col_start,col_end,shaded,expects_value) VALUES
 -- section-header bands span both value cols, shaded, no value
 ('lcr', 1,1,2,1,0),   -- HIGH-QUALITY LIQUID ASSETS
 ('lcr', 3,1,2,1,0),   -- CASH OUTFLOWS
 ('lcr',19,1,2,1,0),   -- CASH INFLOWS
 ('lcr',24,1,2,0,0),   -- TOTAL ADJUSTED VALUE — label spanning value cols, white/no value
 -- weighted-only rows: unweighted cell shaded (dark), weighted cell open
 ('lcr', 2,1,1,1,0), ('lcr', 2,2,2,0,1),   -- line 1  Total HQLA
 ('lcr',11,1,1,1,0), ('lcr',11,2,2,0,1),   -- line 9  Secured wholesale funding
 ('lcr',18,1,1,1,0), ('lcr',18,2,2,0,1),   -- line 16 TOTAL CASH OUTFLOWS
 ('lcr',25,1,1,1,0), ('lcr',25,2,2,0,1),   -- line 21 TOTAL HQLA (adjusted)
 ('lcr',26,1,1,1,0), ('lcr',26,2,2,0,1),   -- line 22 TOTAL NET CASH OUTFLOWS
 ('lcr',27,1,1,1,0), ('lcr',27,2,2,0,1);   -- line 23 LIQUIDITY COVERAGE RATIO (%)

-- ---------------------------------------------------------------------------
-- concept_map alias additions — genuine label VARIANTS seen in real bank
-- Pillar 3 LCR tables vs the notice's Appendix 1 wording (validated against
-- align() 2026-07-02: DBS 4Q23 & UOB 4Q23 both 23/23 matched). Keyed
-- (table_type,label_norm) -> concept_key.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO concept_map (table_type,label_norm,concept_key) VALUES
 ('lcr','operational deposits all counterparties and deposits in institutional networks of cooperative banks','of_ws_op'),  -- DBS 4Q23 ("institutional networks")
 ('lcr','outflows related to derivatives exposures and other collateral requirements','of_addl_deriv'),                     -- DBS 4Q23 ("derivatives" plural)
 ('lcr','total high quality liquid assests hqla','hqla_total');                                                             -- UOB 4Q23 (misspelling "assests")
