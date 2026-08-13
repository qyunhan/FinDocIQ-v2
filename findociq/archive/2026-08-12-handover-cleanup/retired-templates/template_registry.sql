-- ============================================================================
-- TEMPLATE REGISTRY — the star-schema home of MAS-regulated table templates.
-- One ordered row-set per table_type. ONLY tables with a fixed regulatory form
-- belong here (shared across all banks, all years) — this is what routes a
-- table into the TEMPLATE pipeline (deterministic time series). Bank-specific
-- tables have no entry and take the generic path.
-- Additive: safe to apply to an existing schema_v5 DB.
-- ============================================================================

CREATE TABLE IF NOT EXISTS template_row (
  table_type      TEXT NOT NULL,     -- 'nsfr' (pilot); 'lcr','km1',... later
  row_ord         INTEGER NOT NULL,  -- reading order in the regulatory form
  line_no         TEXT,              -- printed line number, verbatim
  canonical_label TEXT NOT NULL,     -- the MAS-form label
  parent_line_no  TEXT,              -- printed line of the parent row (hierarchy context)
  concept_key     TEXT NOT NULL,     -- canonical identity (time-series join key)
  PRIMARY KEY (table_type, row_ord)
);

-- The COLUMN axis of the form — a datapoint's identity is (concept_key x col_key x period).
CREATE TABLE IF NOT EXISTS template_col (
  table_type       TEXT NOT NULL,
  col_ord          INTEGER NOT NULL, -- position in the form
  canonical_header TEXT NOT NULL,    -- leaf header text in the MAS form
  group_label      TEXT,             -- span header above (geometry context), if any
  col_key          TEXT NOT NULL,    -- canonical column identity
  PRIMARY KEY (table_type, col_ord)
);

-- The CELL structure of the form — which grid positions are merged/shaded/open.
-- Only exceptions are listed; a (row,col) not covered here is a plain open value cell.
-- This is the form's OWN ground truth for structure: extraction (and the geometric
-- merge map) are validated against it, cell by cell.
CREATE TABLE IF NOT EXISTS template_cell (
  table_type    TEXT NOT NULL,
  row_ord       INTEGER NOT NULL,    -- FK -> template_row.row_ord
  col_start     INTEGER NOT NULL,    -- FK -> template_col.col_ord (anchor)
  col_end       INTEGER NOT NULL,    -- inclusive; > col_start means MERGED
  shaded        INTEGER NOT NULL DEFAULT 0 CHECK (shaded IN (0,1)),
  expects_value INTEGER NOT NULL DEFAULT 1 CHECK (expects_value IN (0,1)),
  PRIMARY KEY (table_type, row_ord, col_start)
);

-- ---------------------------------------------------------------------------
-- NSFR Disclosure Template (MAS 649) — all 34 lines
-- ---------------------------------------------------------------------------
DELETE FROM template_row WHERE table_type='nsfr';
INSERT INTO template_row (table_type,row_ord,line_no,canonical_label,parent_line_no,concept_key) VALUES
 ('nsfr', 1,'1','Capital:',NULL,'asf_capital'),
 ('nsfr', 2,'2','Regulatory capital','1','asf_capital_reg'),
 ('nsfr', 3,'3','Other capital instruments','1','asf_capital_other'),
 ('nsfr', 4,'4','Retail deposits and deposits from small business customers:',NULL,'asf_retail'),
 ('nsfr', 5,'5','Stable deposits','4','asf_retail_stable'),
 ('nsfr', 6,'6','Less stable deposits','4','asf_retail_less'),
 ('nsfr', 7,'7','Wholesale funding:',NULL,'asf_wholesale'),
 ('nsfr', 8,'8','Operational deposits','7','asf_ws_op'),
 ('nsfr', 9,'9','Other wholesale funding','7','asf_ws_other'),
 ('nsfr',10,'10','Liabilities with matching interdependent assets',NULL,'asf_interdep'),
 ('nsfr',11,'11','Other liabilities:',NULL,'asf_other'),
 ('nsfr',12,'12','NSFR derivative liabilities','11','asf_other_deriv'),
 ('nsfr',13,'13','All other liabilities and equity not included in the above categories','11','asf_other_rest'),
 ('nsfr',14,'14','Total ASF',NULL,'asf_total'),
 ('nsfr',15,'15','Total NSFR high-quality liquid assets (HQLA)',NULL,'rsf_hqla'),
 ('nsfr',16,'16','Deposits held at other financial institutions for operational purposes',NULL,'rsf_fi_op'),
 ('nsfr',17,'17','Performing loans and securities:',NULL,'rsf_perf'),
 ('nsfr',18,'18','Performing loans to financial institutions secured by Level 1 HQLA','17','rsf_perf_fi_l1'),
 ('nsfr',19,'19','Performing loans to financial institutions secured by non-Level 1 HQLA and unsecured performing loans to financial institutions','17','rsf_perf_fi_other'),
 ('nsfr',20,'20','Performing loans to non-financial corporate clients, loans to retail and small business customers, and loans to sovereigns, central banks and PSEs, of which:','17','rsf_perf_corp'),
 ('nsfr',21,'21','With a risk weight of less than or equal to 35% under MAS Notice 637''s standardised approach to credit risk','20','rsf_perf_corp_le35rw'),
 ('nsfr',22,'22','Performing residential mortgages, of which:','17','rsf_resi_mort'),
 ('nsfr',23,'23','With a risk weight of less than or equal to 35% under MAS Notice 637''s standardised approach to credit risk','22','rsf_resi_mort_le35rw'),
 ('nsfr',24,'24','Securities that are not in default and do not qualify as HQLA, including exchange-traded equities','17','rsf_sec_nonhqla'),
 ('nsfr',25,'25','Assets with matching interdependent liabilities',NULL,'rsf_interdep'),
 ('nsfr',26,'26','Other assets:',NULL,'rsf_other'),
 ('nsfr',27,'27','Physical trade commodities, including gold','26','rsf_commodities'),
 ('nsfr',28,'28','Assets posted as initial margin for derivative contracts and contributions to default funds of CCPs','26','rsf_initial_margin'),
 ('nsfr',29,'29','NSFR derivative assets','26','rsf_deriv_assets'),
 ('nsfr',30,'30','NSFR derivative liabilities before deduction of variation margin posted','26','rsf_deriv_liab_gross'),
 ('nsfr',31,'31','All other assets not included in the above categories','26','rsf_other_rest'),
 ('nsfr',32,'32','Off-balance sheet items',NULL,'rsf_offbal'),
 ('nsfr',33,'33','Total RSF',NULL,'rsf_total'),
 ('nsfr',34,'34','Net Stable Funding Ratio (%)',NULL,'nsfr_ratio');

-- ---------------------------------------------------------------------------
-- NSFR column axis (5 leaf columns; cols 1-4 grouped under the unweighted span)
-- ---------------------------------------------------------------------------
DELETE FROM template_col WHERE table_type='nsfr';
INSERT INTO template_col (table_type,col_ord,canonical_header,group_label,col_key) VALUES
 ('nsfr',1,'No maturity','Unweighted value by residual maturity','unw_no_maturity'),
 ('nsfr',2,'< 6 months','Unweighted value by residual maturity','unw_lt_6m'),
 ('nsfr',3,'6 months to < 1 yr','Unweighted value by residual maturity','unw_6m_to_1y'),
 ('nsfr',4,'≥ 1yr','Unweighted value by residual maturity','unw_ge_1y'),
 ('nsfr',5,'Weighted value',NULL,'weighted');

-- ---------------------------------------------------------------------------
-- NSFR cell structure — the MAS form's fixed merges/shading (geometric ground
-- truth verified on DBS 4Q23 and OCBC 2025, 2026-07-02). Rows not listed = five
-- plain open cells. col_end > col_start = merged; shaded=1 = grey; the grey
-- blanks carry no value by construction (expects_value=0).
-- ---------------------------------------------------------------------------
DELETE FROM template_cell WHERE table_type='nsfr';
INSERT INTO template_cell (table_type,row_ord,col_start,col_end,shaded,expects_value) VALUES
 -- line 11 'Other liabilities:' — unweighted maturities merged into one value
 ('nsfr',11,1,1,0,1), ('nsfr',11,2,4,0,1), ('nsfr',11,5,5,0,1),
 -- line 12 'NSFR derivative liabilities' — grey | merged value | grey  (the row 9 model arms failed)
 ('nsfr',12,1,1,1,0), ('nsfr',12,2,4,0,1), ('nsfr',12,5,5,1,0),
 -- line 14 'Total ASF' / 15 'Total NSFR HQLA' — grey band, weighted only
 ('nsfr',14,1,4,1,0), ('nsfr',14,5,5,0,1),
 ('nsfr',15,1,4,1,0), ('nsfr',15,5,5,0,1),
 -- line 26 'Other assets:' — like line 11
 ('nsfr',26,1,1,0,1), ('nsfr',26,2,4,0,1), ('nsfr',26,5,5,0,1),
 -- line 27 'Physical trade commodities' — value | grey merged | value
 ('nsfr',27,1,1,0,1), ('nsfr',27,2,4,1,0), ('nsfr',27,5,5,0,1),
 -- lines 28-30 derivative block — grey col 1 (printed as one vertical band), merged value, weighted
 ('nsfr',28,1,1,1,0), ('nsfr',28,2,4,0,1), ('nsfr',28,5,5,0,1),
 ('nsfr',29,1,1,1,0), ('nsfr',29,2,4,0,1), ('nsfr',29,5,5,0,1),
 ('nsfr',30,1,1,1,0), ('nsfr',30,2,4,0,1), ('nsfr',30,5,5,0,1),
 -- line 32 'Off-balance sheet items' — grey | merged value | weighted
 ('nsfr',32,1,1,1,0), ('nsfr',32,2,4,0,1), ('nsfr',32,5,5,0,1),
 -- lines 33 'Total RSF' / 34 'NSFR (%)' — grey band, weighted only
 ('nsfr',33,1,4,1,0), ('nsfr',33,5,5,0,1),
 ('nsfr',34,1,4,1,0), ('nsfr',34,5,5,0,1);

-- ---------------------------------------------------------------------------
-- concept_map alias additions — known label VARIANTS across banks (learned or
-- seeded). Keyed (table_type,label_norm) -> concept_key; grows via review.py.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO concept_map (table_type,label_norm,concept_key) VALUES
 ('nsfr','total nsfr hqla','rsf_hqla'),                                     -- OCBC short form
 ('nsfr','net stable funding ratio','nsfr_ratio'),
 ('nsfr','nsfr','nsfr_ratio'),
 ('nsfr','deposits held at other financial institutions for operational purposes','rsf_fi_op'),
 ('nsfr','performing loans and securities','rsf_perf'),
 ('nsfr','performing loans to financial institutions secured by level 1 hqla','rsf_perf_fi_l1'),
 ('nsfr','performing loans to financial institutions secured by non level 1 hqla and unsecured performing loans to financial institutions','rsf_perf_fi_other'),
 ('nsfr','performing loans to non financial corporate clients loans to retail and small business customers and loans to sovereigns central banks and pses of which','rsf_perf_corp'),
 ('nsfr','performing residential mortgages of which','rsf_resi_mort'),
 ('nsfr','securities that are not in default and do not qualify as hqla including exchange traded equities','rsf_sec_nonhqla'),
 ('nsfr','assets with matching interdependent liabilities','rsf_interdep'),
 ('nsfr','other assets','rsf_other'),
 ('nsfr','physical trade commodities including gold','rsf_commodities'),
 ('nsfr','assets posted as initial margin for derivative contracts and contributions to default funds of ccps','rsf_initial_margin'),
 ('nsfr','nsfr derivative assets','rsf_deriv_assets'),
 ('nsfr','nsfr derivative liabilities before deduction of variation margin posted','rsf_deriv_liab_gross'),
 ('nsfr','all other assets not included in the above categories','rsf_other_rest'),
 ('nsfr','off balance sheet items','rsf_offbal'),
 -- OFFICIAL MAS Notice 653 Annex 1 wordings (differ slightly from what banks print;
 -- canonical_label = bank-print form, these aliases cover the notice's own text).
 -- Source: findociq/data/sources/regulatory/MAS_Notice_653.pdf, validated 2026-07-02.
 ('nsfr','performing loans to non financial corporates loans to retail and small business customers and loans to sovereigns central banks and public sector entities pses of which','rsf_perf_corp'),
 ('nsfr','with a risk weight of less than or equal to 35 under paragraphs 7 3 42 to 7 3 51 7 3 67 to 7 3 79 7 3 93 7 3 94 and 7 3 98 of mas notice 637','rsf_perf_corp_le35rw'),
 ('nsfr','with a risk weight of less than or equal to 35 under paragraphs 7 3 91 and 7 3 92 of mas notice 637','rsf_resi_mort_le35rw'),
 ('nsfr','physical traded commodities including gold','rsf_commodities');

-- ---------------------------------------------------------------------------
-- STAMPING (findociq/pipeline/templates/stamp.py) — additive columns on the
-- INSTANCE dims so a loaded table can carry its resolved template identity.
-- row_dim.concept_key already exists in schema_v5 (see schema_v5.sql); only
-- col_dim.col_key needed adding. Safe to re-run (duplicate-column guarded).
-- ---------------------------------------------------------------------------
ALTER TABLE col_dim ADD COLUMN col_key TEXT;   -- canonical column identity (join key), post-load stamp
