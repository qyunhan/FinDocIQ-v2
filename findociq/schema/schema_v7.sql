-- ============================================================================
-- Financial-table store — FULL SCHEMA v7  (v5 + lineage registries)
--
-- NEW IN v7 (lineage layer — see NOTE D at bottom):
--   * row_lineage / col_lineage : GLOBAL registries of flattened lineage paths
--       (5-level buffer lvl1..lvl5), keyed on normalised lineage text.
--       DERIVED from row_dim/col_dim parent chains at load — never authored.
--   * cell_fact gains row_lineage_id / col_lineage_id (NOT NULL FKs) so any fact
--       resolves its full lineage in one indexed lookup — no recursive CTE.
--   * row_dim / col_dim gain the same ids as stamped convenience columns.
--   * v_cell exposes the ids; new view v_cell_flat exposes the buffered levels
--       (the melted "generalisable Excel" shape) as a plain join.
--   * Sample DBS seed rows moved OUT of this file into the loader (per the
--       schema's own comment that verbatim data lives in one auditable place).
--
-- Based on: FULL SCHEMA v5
-- Generic across Pillar 3 + financial statements, all banks, all periods.
--
-- Design contract (three levels):
--   document : context  — institution, doc family, source, doc-level period (fallback)
--   section  : document section hierarchy (1 -> 1.2 -> 12.3.1), adjacency list
--   table_t  : structure — one disclosure table, its section, type, and period
--   col_dim  : column axis — header text, order, nesting (col_parent), geography,
--                            and OPTIONAL per-column period (side-by-side Dec/Jun)
--   row_dim  : row axis    — label, hierarchy (row_parent), concept, geography
--   cell_fact: each datapoint — (row, anchor col) + colspan, value, state, shade,
--                               and the RESOLVED period (denormalised, never NULL)
--
-- Reference dimensions (curated once, reused everywhere):
--   geo_dim     : ISO geography codes + region rollup hierarchy
--   concept_map : verbatim label -> canonical concept_key (per table_type)
--
-- Refinements carried from v1:
--   * is_shade is ORTHOGONAL to cell_state    (a cell can be shaded AND hold a dash)
--   * concept_key + geo_key are ASSIGNED (canonical), denormalised onto cell_fact
--   * merge = anchor col_id + colspan; reverse-resolve via half-open range [a, a+span)
--
-- New in v5 (driven by the real UOB 4Q25 Pillar 3 corpus):
--   * cell_state ∈ {empty, reported, zero, null, suppressed}
--       - zero       = a printed 0
--       - null       = a printed dash '-'
--       - suppressed = the '#' sentinel: |amount| < S$0.5m, value WITHHELD.
--                      value_num is ALWAYS NULL; the magnitude is unrecoverable.
--                      A document-wide token rule (not section-scoped), applied in
--                      the same normalisation pass as '-' and '0'. See NOTE A below.
--   * PERIOD PRECEDENCE — period is resolved per cell, most-specific axis wins:
--       col_dim.col_period  >  row_dim.row_period  >  table_t.period  >  document.period
--       (col wins over row when BOTH axes carry a period; a load warning flags the clash)
--       The resolved value is denormalised onto cell_fact.period and is NEVER NULL.
--       Every table MUST resolve to >=1 period (guaranteed by the fallback chain).
--       This REPLACES the old "period lives ONLY on table_t" rule. See NOTE B below.
--   * NO is_total column. A total row is just a leaf row (row_hierarchy >= 1) whose
--       total-ness is carried by its assigned concept_key + unit. Summation is gated
--       on unit/concept_key, never inferred from row_parent. See NOTE C below.
--   * Columns are NEVER additive. col_parent is geometry only (visual grouping under
--       a span header); it carries no arithmetic. See NOTE C below.
-- ============================================================================
PRAGMA foreign_keys = ON;

-- ===========================================================================
-- REFERENCE DIMENSIONS  (geography, concept mapping)  — load these first
-- ===========================================================================

-- Geography reference. geo_key = ISO alpha-2 for countries; named codes for
-- regions/buckets. parent_geo is a self-reference (adjacency list) so leaves
-- roll up into regions. geo_level guards aggregation (sum one level only).
CREATE TABLE geo_dim (
  geo_key    TEXT PRIMARY KEY,         -- 'SG','MY','HK','GREATER_CHINA','ROW','UNALLOC'
  label      TEXT NOT NULL,            -- canonical display name
  geo_level  TEXT NOT NULL CHECK (geo_level IN ('country','region','bucket','global')),
  parent_geo TEXT REFERENCES geo_dim(geo_key),
  iso_alpha2 TEXT                      -- ISO 3166-1 for countries; NULL for regions/buckets
);

-- Verbatim label -> canonical concept_key, scoped by table_type.
-- Authored once per disclosure template (NSFR has ~34 fixed lines).
CREATE TABLE concept_map (
  table_type    TEXT NOT NULL,         -- 'nsfr','lcr',...
  label_norm    TEXT NOT NULL,         -- normalised label (lower, punctuation stripped)
  concept_key   TEXT NOT NULL,
  table_type_norm TEXT,                -- NEW (concept-resolution layer): canonical table-type
                                       -- slug ('income_statement','balance_sheet','customer_loans',
                                       -- 'customer_deposits','nsfr','lcr',...) OR '*' for a
                                       -- TYPE-AGNOSTIC (wildcard) alias. The resolver prefers a
                                       -- row whose table_type_norm matches the row's table type,
                                       -- falling back to '*'. Dictionary aliases seed wildcard
                                       -- ('*') rows; the fixed NSFR template rows are 'nsfr'.
  PRIMARY KEY (table_type, label_norm)
);

-- Concept-resolution audit trail (concept-resolution layer). EVERY stamp of
-- row_dim.concept_key is logged here (deterministic and llm), so any assigned
-- concept is traceable to its label, normalised form, method and confidence.
-- Append-only; portable (no SQLite-only types). ts is an ISO-8601 string.
CREATE TABLE concept_resolution_log (
  doc_id      TEXT,
  table_id    TEXT,
  row_id      INTEGER,
  label       TEXT,                    -- verbatim row_leaf_label
  norm_label  TEXT,                    -- normalize.norm(label) at resolve time
  concept_key TEXT,                    -- the assigned canonical key
  method      TEXT,                    -- 'deterministic' | 'llm'
  confidence  REAL,                    -- 1.0 for deterministic; model score for llm
  ts          TEXT                     -- ISO-8601 UTC timestamp of the stamp
);

-- Consolidation-basis axis (decision: docs/DECISIONS.md legal_entity entry).
-- A COLUMN-level axis -- DBS/UOB/OCBC all print it as a span banner over period
-- columns ('The Group'/'The Company', 'The Group'/'The Bank', 'GROUP'/'BANK'),
-- never per-row. 'total' = the default/whole-entity member (CONSOLIDATED);
-- 'solo' = an unconsolidated single-entity cut.
CREATE TABLE legal_entity_dim (
  legal_entity_key TEXT PRIMARY KEY,   -- 'CONSOLIDATED','PARENT_COMPANY','BANK_SOLO'
  label            TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('total','solo'))
);

-- Verbatim label -> canonical legal_entity_key. label_norm is authored via
-- mapping.normalize.normalize_row_label (an underscore slug, e.g. 'the_group') --
-- NOT segment_map/industry_map's axis_norm (space-lowered) convention;
-- the two axes are looked up with different normalisers by design (see
-- stage2_load/load_v7.py le_lookup).
CREATE TABLE legal_entity_map (
  label_norm       TEXT PRIMARY KEY,
  legal_entity_key TEXT NOT NULL REFERENCES legal_entity_dim(legal_entity_key)
);

-- Business-segment reference (mirrors geo_dim). segment_key = canonical business
-- line; parent_seg is a self-reference (adjacency list) so member lines roll up
-- into SEG_TOTAL, the DEFAULT / whole-bank member. seg_level guards aggregation:
--   'line'   = a real reported business line (retail/wholesale/markets/insurance)
--   'bucket' = a residual/unallocated grouping ('Others')
--   'total'  = the default member (whole-bank); parent_seg NULL.
-- The cross-bank point (dimension_model.md §2): different house names for the same
-- line reconcile to ONE segment_key, so 'UOB GWB' and 'DBS Institutional Banking'
-- both compare as SEG_WHOLESALE.
CREATE TABLE segment_dim (
  segment_key TEXT PRIMARY KEY,        -- 'SEG_RETAIL','SEG_WHOLESALE',... ,'SEG_TOTAL'
  label       TEXT NOT NULL,           -- canonical display name
  seg_level   TEXT NOT NULL CHECK (seg_level IN ('line','bucket','total')),
  parent_seg  TEXT REFERENCES segment_dim(segment_key)
);

-- Verbatim label -> canonical segment_key. Authored aliases for every house name
-- the corpus prints; loader normalises a row/col label with the SAME rule as
-- segment_map (_clean_label then lower) and looks up by EXACT full-label equality.
CREATE TABLE segment_map (
  label_norm  TEXT PRIMARY KEY,        -- normalised segment label
  segment_key TEXT NOT NULL REFERENCES segment_dim(segment_key)
);

-- Industry-of-exposure reference (mirrors segment_dim EXACTLY). industry_key =
-- canonical MAS-standard industry classification used across the 3 banks' 'NPL/
-- NPA by industry' disclosure tables; parent is a self-reference (adjacency
-- list) so members roll up into IND_TOTAL, the DEFAULT / whole-book member.
-- level guards aggregation, same semantics as seg_level:
--   'sector' = a real reported MAS industry classification
--   'total'  = the default member (whole book); parent NULL.
CREATE TABLE industry_dim (
  industry_key   TEXT PRIMARY KEY,     -- 'IND_MFG','IND_CONSTRUCTION',...,'IND_TOTAL'
  industry_name  TEXT NOT NULL,        -- canonical display name
  level          TEXT NOT NULL CHECK (level IN ('sector','total')),
  parent         TEXT REFERENCES industry_dim(industry_key)
);

-- Verbatim label -> canonical industry_key. Authored aliases for every printed
-- variant the corpus carries; loader normalises a row/col label with the SAME
-- rule as segment_map (_clean_label then lower) and looks up by EXACT
-- full-label equality (never substring).
CREATE TABLE industry_map (
  label_norm    TEXT PRIMARY KEY,      -- normalised industry label
  industry_key  TEXT NOT NULL REFERENCES industry_dim(industry_key)
);

-- Equity-component reference (mirrors segment_dim EXACTLY). A statement of
-- changes in equity decomposes ACROSS THE PAGE — its columns are equity
-- components, not periods — and until now that axis had no dimension behind it,
-- so all 33 such columns in the corpus resolved to no key.
--
-- The axis is HIERARCHICAL and the columns are not peers:
--     EQ_TOTAL        = EQ_ATTRIBUTABLE + EQ_NCI (+ EQ_OEI_SUB where printed)
--     EQ_ATTRIBUTABLE = share capital + reserves + retained earnings
-- Anything summing the printed columns as siblings double-counts, which is what
-- eq_level and parent_eq exist to prevent (same guard as seg_level/geo_level).
CREATE TABLE equity_component_dim (
  equity_key TEXT PRIMARY KEY,         -- 'EQ_SHARE_CAPITAL','EQ_NCI','EQ_TOTAL',...
  label      TEXT NOT NULL,            -- canonical display name
  eq_level   TEXT NOT NULL CHECK (eq_level IN ('component','subtotal','total')),
  parent_eq  TEXT REFERENCES equity_component_dim(equity_key)
);

CREATE TABLE equity_component_map (
  label_norm TEXT PRIMARY KEY,         -- normalised equity-component label
  equity_key TEXT NOT NULL REFERENCES equity_component_dim(equity_key)
);

-- Document section hierarchy. section_no is the printed path (e.g. '12.3.1');
-- parent_section points one level up ('12.3.1' -> '12.3'). Adjacency list, same
-- pattern as row_parent / parent_geo. A section may hold many tables.
CREATE TABLE section (
  doc_id         TEXT NOT NULL REFERENCES document(doc_id),
  section_id     TEXT NOT NULL,          -- stable id (can equal section_no)
  section_no     TEXT,                   -- printed number, verbatim: '1.2', '12.3.1'
  section_title  TEXT NOT NULL,          -- heading text
  section_level  INTEGER NOT NULL,       -- depth: 1 = '12', 2 = '12.3', 3 = '12.3.1'
  parent_section TEXT,                   -- section_id one level up; NULL at top
  section_path   TEXT,                   -- full ancestor chain, dot-joined ids
                                         -- ('other_financial_information.fair_value_
                                         -- of_financial_instruments.fair_value_
                                         -- hierarchy'); derived at TOC insert,
                                         -- display/query convenience — hierarchy
                                         -- TRUTH stays relational (parent_section)
  seq            INTEGER,                -- order within the document
  PRIMARY KEY (doc_id, section_id),
  FOREIGN KEY (doc_id, parent_section) REFERENCES section(doc_id, section_id)
);

-- ===========================================================================
-- 1) DOCUMENT
-- ===========================================================================
CREATE TABLE document (
  doc_id      TEXT PRIMARY KEY,
  institution TEXT NOT NULL,
  doc_family  TEXT NOT NULL CHECK (doc_family IN ('pillar3','financial_stmt')),
  source_file TEXT,
  doc_period  DATE              -- document-level "as at" date; LAST-RESORT period
                                -- fallback when neither col nor table carries one.
);

-- ===========================================================================
-- 1b) INGEST STATUS  (pipeline stage tracking, NOT part of the v5/v7 star
-- schema lineage above — this is process metadata, not extracted data)
-- ===========================================================================
-- Keyed by source_file, NOT doc_id: STEP 0 (paddle scan) and a STEP 1 failure
-- both happen BEFORE the document row exists, so doc_id may still be NULL.
-- One row per source file; each run_doc.py step UPDATEs it in place (via
-- pipeline/ingest_status.py, its own autocommit connection) so a status write
-- survives even when the step's own load transaction rolls back.
CREATE TABLE IF NOT EXISTS ingest_status (
  source_file     TEXT PRIMARY KEY,
  doc_id          TEXT,               -- filled in once STEP 1 creates the document row
  bank            TEXT,
  period          TEXT,
  family          TEXT,
  stage           TEXT NOT NULL,      -- scan|toc|extract|load|concepts|verify|xlsx|sync_bq|done
  state           TEXT NOT NULL CHECK (state IN ('pending','running','ok','failed')),
  error_class     TEXT CHECK (error_class IN ('transient','structural') OR error_class IS NULL),
  error_message   TEXT,
  attempt_count   INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT,
  updated_at      TEXT NOT NULL
);

-- ===========================================================================
-- 2) TABLE
-- ===========================================================================
CREATE TABLE table_t (
  doc_id        TEXT NOT NULL REFERENCES document(doc_id),
  table_id      TEXT NOT NULL,
  table_title   TEXT NOT NULL,                        -- the table's own caption
  table_title_clean TEXT,                             -- table_title with footnote superscripts
                                                       -- stripped via TYPOGRAPHY detection (the
                                                       -- PDF-geometry stage's own superscript-run
                                                       -- test), never regex. NULL when the geometry
                                                       -- stage did not run/match this table.
  hierarchy_source TEXT,                               -- VISIBLE record of which routing branch
                                                       -- produced this table's row hierarchy:
                                                       -- 'geometry' = PDF-layer geometry side-car
                                                       -- (pass2 ground truth), 'model' = fell back
                                                       -- to the extraction model's own `level`
                                                       -- field. Project rule: decision-tree pivots
                                                       -- must be observable without reading code.
  table_type    TEXT NOT NULL,                        -- nsfr | lcr | balance_sheet ...
  table_type_id TEXT,                                  -- STAMPED AT LOAD. The masterlist's
                                                       -- canonical table identity (FS_INCOME_SELECTED,
                                                       -- FS_RATIOS_KEY, ...), matched by CONTENT:
                                                       -- this table's printed row paths against the
                                                       -- masterlist's full_path values. NULL where no
                                                       -- masterlist covers the table yet — partial
                                                       -- coverage is the expected state, not a defect.
                                                       -- Pairs with row_dim.canonical_leaf_id: a fact's
                                                       -- document-independent address is
                                                       -- (bank, table_type_id, canonical_leaf_id).
  section_id    TEXT,                                 -- FK into section (the leaf section it sits under)
  section_no    TEXT,                                 -- denormalised printed path ('12.3.1') for fast display
  period        DATE,                                 -- table-level period (mid-precedence).
                                                       -- May be NULL ONLY if every column
                                                       -- carries its own period; otherwise
                                                       -- required. See PERIOD PRECEDENCE.
  period_span   TEXT,                                 -- duration qualifier of the table-level period
                                                       -- (as_at,1Q..4Q,1H,2H,9M,FY). period stays the
                                                       -- END date; joins/sorting on period unchanged.
  period_start  DATE,                                 -- calendar-fiscal START of [period_start, period]
                                                       -- (FY/1H/9M->Jan-01, 2H->Jul-01, nQ->qtr-first-day);
                                                       -- NULL for as_at. The MACHINE flow interval.
  geo_key       TEXT REFERENCES geo_dim(geo_key),     -- if the WHOLE table is one geography
  page_range    TEXT,                                 -- e.g. '1-2' (continuation stitched)
  unit          TEXT,                                 -- table-DEFAULT unit (parsed from label_header
                                                       -- then title: '($m)'->'S$m', '(%)'->'%', ...).
                                                       -- Overridden per cell by col_dim.unit/row_dim.unit
                                                       -- (see the v_cell/v_cell_flat unit CASE).
  PRIMARY KEY (doc_id, table_id),
  FOREIGN KEY (doc_id, section_id) REFERENCES section(doc_id, section_id)
);

-- ===========================================================================
-- 2b) LINEAGE REGISTRIES  (NEW IN v7 — global, derived, never authored)
-- ===========================================================================
-- lineage_key normalisation (loader-applied, same spirit as concept_map):
--   strip trailing footnote markers '(1)', lowercase, collapse whitespace,
--   join levels with ' > '. Identity here is VERBATIM lineage — cross-bank
--   semantic identity stays in concept_map (do not conflate the two layers).
-- Column lineage EXCLUDES the period axis by design (period resolves via the
--   col_period > table_t.period > doc_period chain onto cell_fact.period);
--   otherwise every new quarter would mint fresh col_lineage ids and the
--   registry would never converge.
-- Depth overflow is a HARD LOAD FAILURE, never silent truncation.
CREATE TABLE row_lineage (
  row_lineage_id INTEGER PRIMARY KEY,
  lineage_key   TEXT NOT NULL UNIQUE,
  lvl1 TEXT, lvl2 TEXT, lvl3 TEXT, lvl4 TEXT, lvl5 TEXT,  -- display-cased, right-padded NULL
  depth         INTEGER NOT NULL CHECK (depth BETWEEN 1 AND 5)
);

CREATE TABLE col_lineage (
  col_lineage_id INTEGER PRIMARY KEY,
  lineage_key   TEXT NOT NULL UNIQUE,
  lvl1 TEXT, lvl2 TEXT, lvl3 TEXT, lvl4 TEXT, lvl5 TEXT,
  depth         INTEGER NOT NULL CHECK (depth BETWEEN 1 AND 5)
);

-- ===========================================================================
-- 3) COL DIM
-- ===========================================================================
CREATE TABLE col_dim (
  doc_id         TEXT NOT NULL,
  table_id       TEXT NOT NULL,
  col_id         INTEGER NOT NULL,    -- position / col_order within the table.
                                      -- LEAVES use 1..N = physical page position (what
                                      -- cell_fact anchors to); GROUP HEADERS (hierarchy 0)
                                      -- use an out-of-band namespace 100, 101, ... so span
                                      -- ids can never collide with real positions. No
                                      -- cell_fact row may ever reference col_id >= 100.
  col_hierarchy  INTEGER NOT NULL,    -- 0 = group header, 1 = leaf
  col_parent     INTEGER,             -- col_id of the spanning group; NULL if top.
                                      -- GEOMETRY ONLY — never implies parent = sum(children).
  col_leaf_label TEXT NOT NULL,
  col_leaf_label_clean TEXT,          -- col_leaf_label with footnote superscripts stripped via
                                      -- TYPOGRAPHY detection (PDF-geometry stage), never regex.
                                      -- NULL when the geometry stage did not match this column.
  col_period     DATE,                -- OPTIONAL per-column period. Set when periods sit
                                      -- in the column axis (Dec25 | Dec24 side-by-side,
                                      -- 5-period Key Metrics strip). Highest precedence;
                                      -- overrides table_t.period for cells in this column.
  period_span    TEXT,               -- duration qualifier of col_period (as_at,1Q..4Q,1H,2H,
                                      -- 9M,FY). DISTINGUISHES a 2H flow from an FY flow that
                                      -- share the same 31-Dec END date. period stays the END.
  period_start   DATE,               -- calendar-fiscal START of the flow interval [period_start,
                                      -- col_period]; NULL for as_at. Lets Q3=9M-1H etc. be date math.
  geo_key        TEXT REFERENCES geo_dim(geo_key),  -- RETIRED 2026-08-12: the loader no
                                      -- longer stamps geography and geo_map is gone. Column
                                      -- kept (nullable, FK intact) so the v_cell views and
                                      -- the app keep their shape; NULL on anything loaded
                                      -- after that date.
  segment_key    TEXT REFERENCES segment_dim(segment_key),  -- business-line axis when
                                      -- segments sit in COLUMNS (all 3 banks print
                                      -- Consumer/Institutional/Markets/... as leaf COLUMNS,
                                      -- and DBS prints 'Markets' as a span banner too).
                                      -- Stamped on whichever axis it varies along, by exact
                                      -- normalised segment_map match; effective per-cell
                                      -- segment = COALESCE(row,col,'SEG_TOTAL').
  industry_key   TEXT REFERENCES industry_dim(industry_key),  -- industry-of-exposure
                                      -- axis when industries sit in COLUMNS (mirrors
                                      -- segment_key EXACTLY: same axis-exclusivity
                                      -- stamping, same default-member trick). Effective
                                      -- per-cell industry = COALESCE(row,col,'IND_TOTAL').
  unit           TEXT,
  sums_to        INTEGER,             -- col_id of the TOTAL column this column is a verified
                                      -- component of; derived at load, ARITHMETIC-VERIFIED
                                      -- (the member columns sum, within tolerance, to the
                                      -- default-member/total column per value-bearing row —
                                      -- the COLUMN mirror of row_dim.sums_to). NULL when the
                                      -- columns do not partition, do not reconcile, or on the
                                      -- total column itself (like a row total).
  sums_sign      INTEGER,             -- +1 (added) / -1 (subtracted, e.g. a 'less: eliminations'
                                      -- member column) of this column in its verified total;
                                      -- NULL unless sums_to is set. Mirrors row_dim.sums_sign.
  col_lineage_id  INTEGER REFERENCES col_lineage(col_lineage_id),
                                      -- NEW v7: stamped flattened lineage (leaves only;
                                      -- NULL on hierarchy-0 span headers). Derived from
                                      -- the col_parent chain at load.
  col_role       TEXT,                -- STAMPED AT LOAD, masterlist-independent.
                                      -- 'derived_skip' marks a column that restates other
                                      -- columns rather than reporting a fact ('% chg',
                                      -- '+/(-)%', volume/rate deltas): never ingested as a
                                      -- period fact. NOT a row rule — a line whose only
                                      -- values sit in derived columns still exists and still
                                      -- gets an id (DBS 'Constant-currency change').
  canonical_col_id TEXT,              -- STAMPED AT LOAD. Hard-axis column identity
                                      -- (equity component, segment, geography, measure) for
                                      -- tables whose columns are not a period axis. NULL for
                                      -- period columns, which are addressed by
                                      -- col_period + period_span instead.
  legal_entity   TEXT REFERENCES legal_entity_dim(legal_entity_key),  -- consolidation
                                      -- axis (Group / Company / Bank), a COLUMN-only axis
                                      -- -- own leaf label wins, else the parent span
                                      -- banner ('The Group'/'The Company'/'The Bank'),
                                      -- else NULL. Stamped by exact normalised match on
                                      -- legal_entity_map (mapping.normalize.normalize_row_label
                                      -- slug form, NOT geo_norm's). Effective per-cell
                                      -- legal_entity = COALESCE(col, 'CONSOLIDATED') -- no
                                      -- row-level concept exists for this axis.
  PRIMARY KEY (doc_id, table_id, col_id),
  FOREIGN KEY (doc_id, table_id) REFERENCES table_t(doc_id, table_id)
);

-- ===========================================================================
-- 4) ROW DIM
-- ===========================================================================
CREATE TABLE row_dim (
  doc_id         TEXT NOT NULL,
  table_id       TEXT NOT NULL,
  row_id         INTEGER NOT NULL,
  row_hierarchy  INTEGER NOT NULL,    -- 0 section/total, 1 item, 2 sub-item, 3 rare
  row_parent     INTEGER,             -- row_id of nearest ancestor one level up
  row_leaf_label TEXT NOT NULL,       -- verbatim row label (kept VERBATIM even when the row
                                      -- IS a period, e.g. a 'Dec-25' NPL row)
  row_leaf_label_clean TEXT,          -- row_leaf_label with footnote superscripts stripped via
                                      -- TYPOGRAPHY detection (PDF-geometry stage), never regex.
                                      -- NULL when the geometry stage did not match this row.
  row_period     DATE,                -- OPTIONAL per-row period. Symmetric with col_dim.col_period:
                                      -- set when periods sit in the ROW axis (UOB NPL tables print
                                      -- 'Dec-25'/'Jun-25'/'Dec-24' as leaf ROWS, geographies in
                                      -- COLUMNS). Overrides table_t.period for cells in this row,
                                      -- but col_dim.col_period still wins (col > row > table > doc).
  period_span    TEXT,               -- duration qualifier of row_period (as_at,1Q..4Q,1H,2H,9M,FY);
                                      -- period stays the END date. Mirrors col_dim.period_span.
  period_start   DATE,               -- calendar-fiscal START of [period_start, row_period]; NULL for
                                      -- as_at. Mirrors col_dim.period_start.
  line_no        TEXT,                -- printed line number, verbatim (display only)
  concept_key    TEXT,               -- the row's canonical identity (assigned)
  geo_key        TEXT REFERENCES geo_dim(geo_key),  -- if geography sits in ROWS
  segment_key    TEXT REFERENCES segment_dim(segment_key),  -- business-line axis when
                                      -- segments sit in ROWS (stamped on whichever axis it
                                      -- varies along, same mechanics as geo_key).
  industry_key   TEXT REFERENCES industry_dim(industry_key),  -- industry-of-exposure
                                      -- axis when industries sit in ROWS ('NPL/NPA by
                                      -- industry' tables); mirrors segment_key EXACTLY.
  unit           TEXT,
  sums_to        INTEGER,             -- row_id of the total row this row is a verified
                                      -- component of; derived at load, ARITHMETIC-VERIFIED
                                      -- (SIGN-AWARE: every value column sums within tolerance
                                      -- for a UNIQUE sign assignment — see sums_sign); NULL
                                      -- when not a component, ambiguous, or no solution.
  sums_sign      INTEGER,             -- +1 (added) / -1 (subtracted) of this member in its
                                      -- verified total; NULL unless sums_to is set. Lets a
                                      -- total be a subtraction chain (Net profit = PBT - tax).
  row_lineage_id  INTEGER REFERENCES row_lineage(row_lineage_id),
                                      -- NEW v7: stamped flattened lineage, derived from
                                      -- the row_parent chain at load (every row, incl.
                                      -- hierarchy-0 section rows: their lineage is just
                                      -- themselves, depth 1).
  canonical_leaf_id TEXT,             -- STAMPED AT LOAD. The row's identity WITHIN its table:
                                      -- the '::'-joined normalised ancestor path, with the
                                      -- caption echo dropped and anything period-shaped
                                      -- excluded (a date is period data, never identity).
                                      -- The value is ALWAYS copied verbatim from
                                      -- data/derived/masterlist/ — never derived, never
                                      -- invented. NULL when the row matches no masterlist
                                      -- entry. Unlike (table_id, row_id) this address means
                                      -- the same thing across every vintage.
  table_type_id TEXT,                 -- STAMPED AT LOAD, alongside canonical_leaf_id and by the
                                      -- SAME masterlist entry that resolved this row. The other
                                      -- half of the document-independent address
                                      -- (bank, table_type_id, canonical_leaf_id).
                                      -- WHY IT LIVES HERE AND NOT ONLY ON table_t: one printed
                                      -- exhibit can hold rows from SEVERAL masterlist types
                                      -- (a 'Financial Highlights' page prints income, balance
                                      -- and ratio lines), and a small masterlist entry can also
                                      -- claim a neighbouring table — MIN_MATCH_FRACTION of a
                                      -- 6-leaf entry is only 3 rows. table_t holds ONE type, so
                                      -- the last entry to match used to overwrite the earlier
                                      -- one and every leaf under the losing type became
                                      -- unreachable to a join on (table_type_id, leaf) even
                                      -- though the leaf itself was stamped correctly.
                                      -- Row grain is the true grain: it makes that collision
                                      -- structurally impossible rather than arbitrated by a
                                      -- tie-break. table_t.table_type_id is KEPT as the
                                      -- exhibit's dominant type for coverage reporting.
  PRIMARY KEY (doc_id, table_id, row_id),
  FOREIGN KEY (doc_id, table_id) REFERENCES table_t(doc_id, table_id)
);

-- ===========================================================================
-- 5) CELL FACT
-- ===========================================================================
CREATE TABLE cell_fact (
  doc_id      TEXT NOT NULL,
  table_id    TEXT NOT NULL,
  row_id      INTEGER NOT NULL,
  col_id      INTEGER NOT NULL,        -- ANCHOR (leftmost) column of the cell
  colspan     INTEGER NOT NULL DEFAULT 1 CHECK (colspan >= 1),  -- covers [col_id, col_id+colspan)
  value_raw   TEXT,                    -- verbatim ('65,326','(1,505)','-','#','118','AAA to BBB+')
  value_num   REAL,                    -- parsed number; NULL for null/empty/suppressed/text
  unit        TEXT,                    -- RESOLVED per-cell unit, materialised at load via the
                                       -- FULL precedence chain: (1) cell value token ('137%'->'%',
                                       -- '1.2x'->'x'); (2) row unit if '%'; (3) col_dim.unit;
                                       -- (4) row_dim.unit; (5) table_t.unit; (6) document default
                                       -- (modal table_t.unit across the doc); else NULL (genuinely
                                       -- unknowable, one load warning per table). The source cols
                                       -- row_dim.unit/col_dim.unit/table_t.unit stay populated for
                                       -- inspection; the v_cell/v_cell_flat views READ THIS COLUMN
                                       -- directly (no CASE) so every consumer sees the same unit.
  cell_state  TEXT NOT NULL DEFAULT 'reported'
              CHECK (cell_state IN ('empty','reported','zero','null','suppressed')),
              -- suppressed = '#': nonzero but |x| < S$0.5m, magnitude WITHHELD (value_num NULL).
  is_shade    INTEGER NOT NULL DEFAULT 0 CHECK (is_shade IN (0,1)),  -- orthogonal to state
  period      DATE NOT NULL,           -- RESOLVED period (col_period > row_period > table.period
                                       -- > doc_period). Denormalised at load; NEVER NULL. See
                                       -- PERIOD PRECEDENCE.
  period_span TEXT,                    -- duration class of the cell's period
                                       -- ({1Q..4Q,1H,2H,9M,FY,as_at} or NULL when no printed
                                       -- duration); materialised at load from the EFFECTIVE
                                       -- col/row/table span, PAIRED with `period` (col span if the
                                       -- period came from the column, row span if from the row,
                                       -- else table span; never a period from one axis with a span
                                       -- from another). Same self-describing
                                       -- rationale as cell_fact.unit: a bare period=2025-12-31 is
                                       -- FY-vs-2H ambiguous without the span on the SAME row. The
                                       -- v_cell/v_cell_flat views READ THIS COLUMN directly (no
                                       -- COALESCE). period_start stays on the dims only (optional).
  period_source TEXT,                  -- WHICH link of the chain supplied this cell's period:
                                       -- 'col' / 'row' / 'row_banner' / 'table_title' / 'doc'.
                                       -- 'row_banner' = inherited from a valueless period BANNER
                                       -- row that SCOPES a block of siblings ('2nd Half 2025',
                                       -- 'Dec 24'), as opposed to 'row' where the cell's own row
                                       -- prints the date. Kept distinct on purpose: folding the
                                       -- two would make an inherited period indistinguishable
                                       -- from a printed one, which is exactly what this column
                                       -- exists to prevent. (The last two both
                                       -- come from table_t.period, distinguished by whether that
                                       -- itself came from an explicit table-title date or fell
                                       -- back to doc_period — see table_period_source in
                                       -- stage2_load/load_v7.py). Provenance for period, not a value on
                                       -- its own; lets a cell that inherited a coarser period than
                                       -- its table actually prints be found and audited instead of
                                       -- looking identical to one that resolved correctly.
  concept_key TEXT,                    -- denormalised from row_dim (fast filtering)
  geo_key     TEXT,                    -- EFFECTIVE geography of the cell, MATERIALISED at load
                                       -- via COALESCE(row_dim.geo, col_dim.geo, table_t.geo,
                                       -- 'GLOBAL') — ROW axis first, MATCHING the old
                                       -- v_cell/v_cell_flat COALESCE order so cell_fact == view
                                       -- exactly. Self-describing, same rationale as
                                       -- cell_fact.unit / period_span: the v_cell/v_cell_flat
                                       -- views READ THIS COLUMN directly (no COALESCE). Default
                                       -- member 'GLOBAL' (whole-bank).
  segment_key TEXT REFERENCES segment_dim(segment_key),
                                       -- EFFECTIVE business segment of the cell, MATERIALISED at
                                       -- load via COALESCE(row_dim.segment, col_dim.segment,
                                       -- 'SEG_TOTAL') — row axis first, matching the view order.
                                       -- Self-describing mirror of geo_key; the views READ THIS
                                       -- COLUMN directly (no COALESCE). Default member 'SEG_TOTAL'.
  industry_key TEXT REFERENCES industry_dim(industry_key),
                                       -- EFFECTIVE industry-of-exposure of the cell, MATERIALISED
                                       -- at load via COALESCE(row_dim.industry, col_dim.industry,
                                       -- 'IND_TOTAL') — row axis first, matching geo/segment.
                                       -- Self-describing mirror of segment_key. Default member
                                       -- 'IND_TOTAL' (whole book).
  legal_entity TEXT REFERENCES legal_entity_dim(legal_entity_key),
                                       -- EFFECTIVE consolidation basis of the cell,
                                       -- MATERIALISED at load via COALESCE(col_dim.legal_entity,
                                       -- 'CONSOLIDATED') -- COLUMN axis only, no row-level
                                       -- concept exists for it (unlike geo/segment/industry).
                                       -- Default member 'CONSOLIDATED' (Group). Without this
                                       -- axis in the grain, Group and Company/Bank cuts collide
                                       -- as duplicates at the SAME (concept,period,segment,geo,
                                       -- industry) key and a downstream resolver silently picks
                                       -- one (see docs/specs/MAPPING_LAYER.md legal_entity entry).
  row_lineage_id INTEGER NOT NULL REFERENCES row_lineage(row_lineage_id),
  col_lineage_id INTEGER NOT NULL REFERENCES col_lineage(col_lineage_id),
                                       -- NEW v7: boss-mandated direct lineage FKs.
                                       -- Copied from row_dim/col_dim stamps at load;
                                       -- a rebuild from the parent chains must be a no-op.
  PRIMARY KEY (doc_id, table_id, row_id, col_id),
  FOREIGN KEY (doc_id, table_id, row_id) REFERENCES row_dim(doc_id, table_id, row_id),
  FOREIGN KEY (doc_id, table_id, col_id) REFERENCES col_dim(doc_id, table_id, col_id)
);

-- ===========================================================================
-- INDEXES — scaled for full Pillar 3 + financial statements, all years
-- ===========================================================================
-- primary hot path: pick a concept (+ column) and sweep periods across the corpus
CREATE INDEX ix_concept ON cell_fact (concept_key, col_id, doc_id, table_id);
-- geography slice: "everything Singapore" and composed concept+geo
CREATE INDEX ix_geo     ON cell_fact (geo_key, concept_key, doc_id, table_id);
-- segment slice: "everything Wholesale" and composed concept+segment (mirror of ix_geo)
CREATE INDEX ix_segment ON cell_fact (segment_key, concept_key, doc_id, table_id);
-- industry slice: "everything Manufacturing" and composed concept+industry (mirror of ix_geo/ix_segment)
CREATE INDEX ix_industry ON cell_fact (industry_key, concept_key, doc_id, table_id);
-- scope a whole table fast (reconstruction, audit)
CREATE INDEX ix_table   ON cell_fact (doc_id, table_id, row_id, col_id);
-- NEW v7: flat lineage search — the whole point of the lineage layer
CREATE INDEX ix_rowhdr  ON cell_fact (row_lineage_id, col_lineage_id, period);
CREATE INDEX ix_colhdr  ON cell_fact (col_lineage_id, period);
CREATE INDEX ix_section  ON table_t (doc_id, section_id);
CREATE INDEX ix_sec_tree ON section (doc_id, parent_section);

-- ===========================================================================
-- VIEWS — make correct aggregation the default (avoid parent+child double-count,
--          and never let a non-numeric state silently vanish from a SUM)
-- ===========================================================================
-- Cells joined to their row/table context. NOTE: period comes from cell_fact
-- (the RESOLVED period), NOT table_t — so column-period tables report correctly.
-- row_hierarchy is exposed so callers can sum one level only.
-- UNIT: read straight from cell_fact.unit, MATERIALISED at load via the full
-- precedence chain (cell value token > row '%' > col > row > table > document
-- default > NULL). row_dim.unit / col_dim.unit / table_t.unit remain exposed as
-- row_unit/col_unit/table_unit for inspection (they are the chain's sources), but
-- the effective per-cell unit is no longer a view-time CASE — it is the stored
-- column, so every consumer (view, xlsx check, NL layer) sees one identical value.
CREATE VIEW v_cell AS
SELECT f.doc_id, f.table_id, t.table_type, f.period, d.institution,
       f.row_id, r.row_leaf_label, r.row_hierarchy, r.line_no, r.unit AS row_unit,
       f.col_id, f.colspan, c.col_leaf_label, c.col_period, c.unit AS col_unit,
       f.period_span                            AS period_span,   -- MATERIALISED at load (paired with f.period)
       COALESCE(c.period_start, t.period_start) AS period_start,
       t.unit AS table_unit,
       f.unit AS unit,
       -- CONCEPT INHERITANCE (concept-resolution layer): the concept is a property
       -- of the LINE ITEM (row_dim.concept_key, stamped post-load by the resolver),
       -- and cells inherit it here. cell_fact.concept_key is a load-time denormal
       -- (currently always NULL — the loader never sets it), kept as a fallback so a
       -- future denormalising loader is compatible. row wins.
       COALESCE(r.concept_key, f.concept_key) AS concept_key,
       -- DEFAULT-MEMBER trick (dimension_model.md): a base-table NULL means 'no
       -- explicit slice', which IS the default member — so the whole-bank number is
       -- a plain filter (geo_key='GLOBAL' / segment_key='SEG_TOTAL'), never a special
       -- case. The effective value (row > col > table > default) is now MATERIALISED
       -- onto cell_fact at load (same as unit / period_span), so read it DIRECTLY.
       f.geo_key     AS geo_key,
       f.segment_key AS segment_key,
       f.industry_key AS industry_key,
       f.value_raw, f.value_num, f.cell_state, f.is_shade,
       f.row_lineage_id, f.col_lineage_id
FROM cell_fact f
JOIN row_dim   r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id
JOIN col_dim   c ON c.doc_id=f.doc_id AND c.table_id=f.table_id AND c.col_id=f.col_id
JOIN table_t   t ON t.doc_id=f.doc_id AND t.table_id=f.table_id
JOIN document  d ON d.doc_id=f.doc_id;

-- Leaf-only view: excludes section-header rows so casual SUM()s don't double-count
-- a parent against its children. NOTE: a TOTAL row is itself a leaf (row_hierarchy>=1)
-- and IS included here — total-ness is decided by concept_key/unit at query time, not
-- by excluding it structurally. Sum over THIS, not raw cell_fact.
CREATE VIEW v_cell_leaf AS
SELECT * FROM v_cell WHERE row_hierarchy >= 1;

-- Sum-safe view: only cells that carry a real, parseable number. Excludes
-- empty / null('-') / suppressed('#') so a column full of '#' does not sum as if
-- complete. If COUNT(*) here < COUNT(*) in v_cell_leaf for the same slice, the
-- total is a LOWER BOUND (suppressed cells exist). Analytics should surface that.
CREATE VIEW v_cell_sumsafe AS
SELECT * FROM v_cell_leaf
WHERE cell_state = 'reported' AND value_num IS NOT NULL;

-- NEW v7: the melted flat shape (one generalisable layout for every table).
-- This is the Excel export and the NL layer's search surface: filter on the
-- buffered levels directly, no recursive CTE. period comes from cell_fact
-- (resolved), lineage from the registries.
CREATE VIEW v_cell_flat AS
SELECT d.institution, f.period,
       f.period_span                            AS period_span,   -- MATERIALISED at load (paired with f.period)
       COALESCE(c.period_start, t.period_start) AS period_start,
       t.table_type, t.table_title,
       t.section_no,
       r.line_no,
       rh.lvl1 AS row_lvl1, rh.lvl2 AS row_lvl2, rh.lvl3 AS row_lvl3,
       rh.lvl4 AS row_lvl4, rh.lvl5 AS row_lvl5, rh.depth AS row_depth,
       ch.lvl1 AS col_lvl1, ch.lvl2 AS col_lvl2, ch.depth AS col_depth,
       f.unit AS unit,
       f.value_num, f.value_raw, f.cell_state, f.is_shade, f.colspan,
       -- CONCEPT INHERITANCE: cells inherit the row's stamped concept (see v_cell).
       COALESCE(r.concept_key, f.concept_key) AS concept_key,
       -- DEFAULT-MEMBER trick (dimension_model.md): NULL on the base row/col = the
       -- default member, so whole-bank facts are a filter not a special case. The
       -- effective value (row > col > table > default) is MATERIALISED onto cell_fact
       -- at load (same as unit / period_span) — read it DIRECTLY, no COALESCE.
       f.geo_key     AS geo_key,
       f.segment_key AS segment_key,
       f.industry_key AS industry_key,
       f.row_lineage_id, f.col_lineage_id,
       f.doc_id, f.table_id, f.row_id, f.col_id, r.row_hierarchy
FROM cell_fact f
JOIN row_lineage rh ON rh.row_lineage_id = f.row_lineage_id
JOIN col_lineage ch ON ch.col_lineage_id = f.col_lineage_id
JOIN row_dim  r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id
JOIN col_dim  c ON c.doc_id=f.doc_id AND c.table_id=f.table_id AND c.col_id=f.col_id
JOIN table_t  t ON t.doc_id=f.doc_id AND t.table_id=f.table_id
JOIN document d ON d.doc_id=f.doc_id;

-- ============================================================================
-- REFERENCE DATA — geography for the markets these banks report
-- ============================================================================
INSERT INTO geo_dim (geo_key,label,geo_level,parent_geo,iso_alpha2) VALUES
 ('GLOBAL','Group / Global','global',NULL,NULL),
 ('ASIA','Asia','region','GLOBAL',NULL),
 ('ASEAN','Southeast Asia','region','ASIA',NULL),
 ('GREATER_CHINA','Greater China','region','ASIA',NULL),
 ('SOUTH_ASIA','South Asia','region','ASIA',NULL),
 ('SG','Singapore','country','ASEAN','SG'),
 ('MY','Malaysia','country','ASEAN','MY'),
 ('ID','Indonesia','country','ASEAN','ID'),
 ('TH','Thailand','country','ASEAN','TH'),
 ('VN','Vietnam','country','ASEAN','VN'),
 ('HK','Hong Kong','country','GREATER_CHINA','HK'),
 ('CN','China (Mainland)','country','GREATER_CHINA','CN'),
 ('TW','Taiwan','country','GREATER_CHINA','TW'),
 ('IN','India','country','SOUTH_ASIA','IN'),
 ('ROW','Rest of the World','bucket','GLOBAL',NULL),
 ('OTH','Others','bucket','GLOBAL',NULL),
 ('UNALLOC','Unallocated','bucket',NULL,NULL);

-- Printed COMBINED buckets harvested from the FS corpus. Each is a residual/combined
-- region the banks print as a single line; modelled as its own bucket with an HONEST
-- parent so rollups never double-count a sibling that is printed separately.
--   GC_EX_HK — DBS prints 'Hong Kong' AND 'Rest of Greater China' as separate lines.
--     'Rest of Greater China' therefore EXCLUDES Hong Kong; mapping it to GREATER_CHINA
--     would double-count HK when both roll up. Parent GREATER_CHINA, and HK + GC_EX_HK
--     = GREATER_CHINA with no overlap.
--   SSEA — DBS's combined 'South and Southeast Asia' line (its own spelling variants).
--     Not a country; a multi-country residual within Asia. Parent ASIA (judgment call:
--     it spans ASEAN + South Asia, so it is attached at the ASIA region, not ASEAN).
--   OTH_APAC — OCBC's 'Other Asia Pacific' residual (Asia-Pacific beyond its named
--     markets). Parent ASIA (judgment call: an Asia-region residual bucket; 'Pacific'
--     is approximated to ASIA rather than minting a separate Oceania region the corpus
--     never itemises).
INSERT INTO geo_dim (geo_key,label,geo_level,parent_geo,iso_alpha2) VALUES
 ('GC_EX_HK','Greater China ex-HK','bucket','GREATER_CHINA',NULL),
 ('SSEA','South and Southeast Asia','bucket','ASIA',NULL),
 ('OTH_APAC','Other Asia Pacific','bucket','ASIA',NULL);

-- label normalisation convention (applied by the loader before lookup):
--   lowercase, strip punctuation, collapse whitespace, expand known abbreviations

-- ============================================================================
-- REFERENCE DATA — business segments (mirrors the geo seeds above)
-- ============================================================================
-- SEG_TOTAL is inserted FIRST: it is the parent of every member (self-FK with
-- foreign_keys=ON is checked immediately), and it is the DEFAULT member.
INSERT INTO segment_dim (segment_key,label,seg_level,parent_seg) VALUES
 ('SEG_TOTAL','Total / Whole-bank','total',NULL),                            -- DEFAULT member
 ('SEG_RETAIL','Consumer / Retail / Wealth','line','SEG_TOTAL'),
 ('SEG_WHOLESALE','Institutional / Wholesale / Corporate','line','SEG_TOTAL'),
 ('SEG_MARKETS','Global Markets / Treasury / Trading','line','SEG_TOTAL'),
 ('SEG_INSURANCE','Insurance','line','SEG_TOTAL'),
 ('SEG_OTHER','Others / Unallocated','bucket','SEG_TOTAL');

-- label normalisation convention: EXACTLY the segment_map rule (loader axis_norm /
-- seg_norm = _clean_label then lower — footnote markers stripped, whitespace
-- collapsed, lowercased). Lookup is EXACT full-label equality, never substring,
-- so 'Trading income' -> 'trading income' != key 'trading' and does not mis-stamp.
-- Every alias below is a verbatim house name harvested from the DBS/OCBC/UOB
-- 4Q25/2Q25 FS corpus (see the loader-design spec §3c for the harvest table).
INSERT INTO segment_map (label_norm,segment_key) VALUES
 -- SEG_RETAIL — consumer + wealth/private banking
 ('consumer banking/ wealth management','SEG_RETAIL'),  -- DBS (verbatim '/ ' kept; only ws/footnote normalised)
 ('global consumer/ private banking','SEG_RETAIL'),     -- OCBC
 ('gr','SEG_RETAIL'),                                   -- UOB abbreviation (Group Retail)
 ('group retail','SEG_RETAIL'),                         -- UOB full name (cont'd panels / other banks)
 -- SEG_WHOLESALE — institutional / corporate / wholesale
 ('institutional banking','SEG_WHOLESALE'),             -- DBS
 ('global wholesale banking','SEG_WHOLESALE'),          -- OCBC
 ('gwb','SEG_WHOLESALE'),                               -- UOB abbreviation
 ('group wholesale banking','SEG_WHOLESALE'),           -- UOB full name
 -- SEG_MARKETS — treasury / trading / global markets
 ('global markets','SEG_MARKETS'),                      -- OCBC
 ('markets','SEG_MARKETS'),                             -- DBS span banner (over the 'Trading' leaf)
 ('trading','SEG_MARKETS'),                             -- DBS leaf under the 'Markets' banner
 ('gm','SEG_MARKETS'),                                  -- UOB abbreviation
 -- SEG_INSURANCE
 ('insurance','SEG_INSURANCE'),                         -- OCBC
 -- SEG_OTHER — residual bucket. JUDGMENT CALL: the printed label 'Others' is shared
 --   with geo OTH; exact-match stamps BOTH keys on the same column (symmetric with
 --   how segment_map stamps a segment table's 'Others' as SEG_OTHER). Which axis is
 --   'real' is context; the per-axis COALESCE default and the reconciliation gate
 --   keep each dimension internally consistent. No per-table special-case (would be
 --   overfitting) — the ambiguity is accepted, same as geo.
 ('others','SEG_OTHER'),
 -- SEG_TOTAL default member (USER-APPROVED plain aliases): a 'Total' or a 'Group'
 --   column IS the whole-bank slice, so SEG_TOTAL on it is CONSISTENT, not
 --   contradictory. It also lets the column-sum reconciliation gate locate the
 --   total column deterministically.
 ('total','SEG_TOTAL'),
 ('group','SEG_TOTAL');
-- NOT mapped, deliberately: DBS 'Commercial Book' — a supra-segment span banner
--   (= Consumer + Institutional + Others, i.e. everything except Markets). It is not
--   one of the canonical members; leaving it NULL is correct (its leaves carry the
--   real member keys and sum, with Markets/Trading, to Total).

-- ============================================================================
-- REFERENCE DATA — industry-of-exposure (mirrors segment_dim/segment_map above).
-- All 3 banks (DBS/OCBC/UOB) publish 'NPL/NPA by industry' tables against these
-- MAS-standard categories. IND_TOTAL is inserted FIRST (self-FK parent, DEFAULT
-- member), same rationale as SEG_TOTAL.
-- ============================================================================
INSERT INTO industry_dim (industry_key,industry_name,level,parent) VALUES
 ('IND_TOTAL','Total / whole book','total',NULL),                                       -- DEFAULT member
 ('IND_MFG','Manufacturing','sector','IND_TOTAL'),
 ('IND_CONSTRUCTION','Building and construction','sector','IND_TOTAL'),
 ('IND_HOUSING','Housing loans','sector','IND_TOTAL'),
 ('IND_COMMERCE','General commerce','sector','IND_TOTAL'),
 ('IND_TRANSPORT_COMMS','Transport, storage and communication','sector','IND_TOTAL'),
 ('IND_FI_INVEST','Financial institutions, investment and holding companies','sector','IND_TOTAL'),
 ('IND_PROF_INDIV','Professionals and private individuals','sector','IND_TOTAL'),
 ('IND_AGRI_MINING','Agriculture, mining and quarrying','sector','IND_TOTAL'),
 ('IND_OTHERS','Others','sector','IND_TOTAL');

-- label normalisation convention: EXACTLY the segment_map rule (loader
-- ind_norm = geo_norm — _clean_label then lower). Lookup is EXACT full-label
-- equality, never substring. Every alias below is a verbatim house name/spelling
-- variant harvested from the DBS/OCBC/UOB 'NPL/NPA by industry' FS corpus.
INSERT INTO industry_map (label_norm,industry_key) VALUES
 ('manufacturing','IND_MFG'),
 ('building and construction','IND_CONSTRUCTION'),
 ('housing loans','IND_HOUSING'),
 ('general commerce','IND_COMMERCE'),
 ('transport, storage and communication','IND_TRANSPORT_COMMS'),
 ('transportation, storage & communications','IND_TRANSPORT_COMMS'),  -- UOB spelling variant
 ('financial institutions, investment and holding companies','IND_FI_INVEST'),
 ('financial institutions, investment & holding companies','IND_FI_INVEST'),  -- '&' variant
 ('professionals and private individuals','IND_PROF_INDIV'),
 ('professionals and individuals','IND_PROF_INDIV'),                          -- shortened variant
 ('professionals & private individuals (excluding housing loans)','IND_PROF_INDIV'),
 ('agriculture, mining and quarrying','IND_AGRI_MINING'),
 ('others','IND_OTHERS'),
 -- SEE the segment_map 'others' comment above: this label is now a THREE-way
 -- collision (geo OTH / segment SEG_OTHER / industry IND_OTHERS). The loader's
 -- axis-exclusivity rule (spec §5) resolves it per table-axis at load time —
 -- NOT here. No per-table special-case; the ambiguity is accepted at map level,
 -- same as geo/segment.
 -- IND_TOTAL default member: every verbatim 'Total' spelling the corpus prints
 -- on an industry table.
 ('total','IND_TOTAL'),
 ('total npls','IND_TOTAL'),
 ('total npas','IND_TOTAL'),
 ('total non-performing loans','IND_TOTAL'),
 ('total non-performing assets (npa)','IND_TOTAL');

-- ---------------------------------------------------------------------------
-- equity_component seed. Members are the union of what the three banks column
-- their statements of changes in equity with; the hierarchy is what their own
-- printed subtotals assert.
INSERT INTO equity_component_dim (equity_key,label,eq_level,parent_eq) VALUES
 ('EQ_TOTAL','Total equity','total',NULL),                                  -- DEFAULT member
 ('EQ_ATTRIBUTABLE','Attributable to equity holders of the parent','subtotal','EQ_TOTAL'),
 ('EQ_NCI','Non-controlling interests','component','EQ_TOTAL'),
 -- OUTSIDE attributable on purpose: OCBC prints 'Other equity instruments
 -- issued by subsidiary' AFTER its 'Total' column, i.e. it is added to the
 -- attributable subtotal to reach total equity, not included in it. DBS's plain
 -- 'Other equity instruments' sits BEFORE its subtotal and is a different
 -- member (EQ_OTHER_EQUITY_INSTRUMENTS below).
 ('EQ_OEI_SUB','Other equity instruments issued by subsidiary','component','EQ_TOTAL'),
 ('EQ_SHARE_CAPITAL','Share capital','component','EQ_ATTRIBUTABLE'),
 ('EQ_OTHER_EQUITY_INSTRUMENTS','Other equity instruments','component','EQ_ATTRIBUTABLE'),
 ('EQ_RETAINED_EARNINGS','Retained earnings / revenue reserves','component','EQ_ATTRIBUTABLE'),
 ('EQ_OTHER_RESERVES','Other reserves','component','EQ_ATTRIBUTABLE'),
 -- OCBC splits what DBS and UOB print as one 'Other reserves' column, so these
 -- are CHILDREN of it rather than siblings: summing one level stays correct
 -- whichever bank is in front of you.
 ('EQ_CAPITAL_RESERVES','Capital reserves','component','EQ_OTHER_RESERVES'),
 ('EQ_FAIR_VALUE_RESERVES','Fair value reserves','component','EQ_OTHER_RESERVES');

-- label normalisation convention: EXACTLY the segment_map rule
-- (_clean_label then lower). Lookup is EXACT full-label equality.
-- Every alias is a verbatim column header harvested from the DBS/OCBC/UOB
-- 4Q25 statements of changes in equity.
INSERT INTO equity_component_map (label_norm,equity_key) VALUES
 -- EQ_SHARE_CAPITAL. OCBC and UOB FOLD other equity instruments into the share
 -- capital column; DBS prints them separately. Mapping the folded columns here
 -- keeps the member the banks actually report — splitting them would invent a
 -- figure neither bank prints.
 ('share capital','EQ_SHARE_CAPITAL'),                        -- DBS
 ('share capital and other equity','EQ_SHARE_CAPITAL'),       -- OCBC
 ('share capital and other capital','EQ_SHARE_CAPITAL'),      -- UOB
 ('other equity instruments','EQ_OTHER_EQUITY_INSTRUMENTS'),  -- DBS
 ('other equity instruments issued by subsidiary','EQ_OEI_SUB'),  -- OCBC
 -- EQ_RETAINED_EARNINGS — 'revenue reserves' IS retained earnings under a
 -- different house name (DBS, OCBC); UOB prints the plain term.
 ('revenue reserves','EQ_RETAINED_EARNINGS'),
 ('retained earnings','EQ_RETAINED_EARNINGS'),
 ('other reserves','EQ_OTHER_RESERVES'),                      -- DBS, UOB
 ('capital reserves','EQ_CAPITAL_RESERVES'),                  -- OCBC
 ('fair value reserves','EQ_FAIR_VALUE_RESERVES'),            -- OCBC
 ('non-controlling interests','EQ_NCI'),
 -- THE COUNTERINTUITIVE ONE, and the reason this map has to exist. On an equity
 -- statement a bare 'Total' column is NOT total equity — it is the subtotal
 -- attributable to equity holders of the parent, printed BEFORE the NCI column.
 -- OCBC and UOB print 'Total'; DBS spells it out. Mapping 'total' to EQ_TOTAL
 -- here would silently equate the attributable subtotal with total equity.
 ('total','EQ_ATTRIBUTABLE'),                                 -- OCBC, UOB
 -- both apostrophes: DBS prints the curly U+2019, and the normalisation rule
 -- shared with segment_map does not fold quote characters
 ('total shareholders'' funds','EQ_ATTRIBUTABLE'),            -- DBS, straight
 ('total shareholders’ funds','EQ_ATTRIBUTABLE'),             -- DBS, as printed
 ('attributable to shareholders of the company','EQ_ATTRIBUTABLE'),  -- DBS span header
 ('attributable to equity holders of the bank','EQ_ATTRIBUTABLE'),   -- UOB span header
 ('total equity','EQ_TOTAL');
-- ('Total Non-performing assets (NPA)' normalises to the SAME key as
--  'Total non-performing assets (NPA)' above — case-fold is part of ind_norm.)
-- NOT mapped, deliberately (stay NULL/default IND_TOTAL at load, never a member):
--   'Classified debt securities', 'Classified contingent liabilities',
--   'Debt securities, contingent liabilities & others', 'Loans and advances',
--   and footnote/note rows — these are NOT MAS industry categories; they are
--   residual/composite line items on the same disclosure tables.

-- concept map for the NSFR template (fixed 34-line regulatory layout)
INSERT INTO concept_map (table_type,label_norm,concept_key) VALUES
 ('nsfr','capital','asf_capital'),
 ('nsfr','regulatory capital','asf_capital_reg'),
 ('nsfr','other capital instruments','asf_capital_other'),
 ('nsfr','retail deposits and deposits from small business customers','asf_retail'),
 ('nsfr','stable deposits','asf_retail_stable'),
 ('nsfr','less stable deposits','asf_retail_less'),
 ('nsfr','wholesale funding','asf_wholesale'),
 ('nsfr','operational deposits','asf_ws_op'),
 ('nsfr','other wholesale funding','asf_ws_other'),
 ('nsfr','liabilities with matching interdependent assets','asf_interdep'),
 ('nsfr','other liabilities','asf_other'),
 ('nsfr','nsfr derivative liabilities','asf_other_deriv'),
 ('nsfr','all other liabilities and equity not included in the above categories','asf_other_rest'),
 ('nsfr','total asf','asf_total'),
 ('nsfr','total nsfr high quality liquid assets hqla','rsf_hqla'),
 ('nsfr','deposits held at other financial institutions for operational purposes','rsf_fi_op'),
 ('nsfr','performing loans and securities','rsf_perf'),
 ('nsfr','total rsf','rsf_total'),
 ('nsfr','net stable funding ratio','nsfr_ratio');
-- The fixed NSFR template rows are type-scoped: table_type_norm='nsfr' so the
-- concept resolver prefers them over any wildcard dictionary alias for an NSFR table.
UPDATE concept_map SET table_type_norm = 'nsfr' WHERE table_type = 'nsfr';

-- (v7: sample DBS document/section/table seed rows moved to the loader,
--  per the rule that verbatim data lives in one auditable place.)

-- ============================================================================
-- REFERENCE DATA — legal_entity (consolidation basis; mirrors geo/segment/
-- industry seeding above). CONSOLIDATED inserted FIRST: it is the DEFAULT
-- member (col_dim/cell_fact.legal_entity falls back to it when no column
-- carries an explicit Group/Company/Bank banner).
-- ============================================================================
INSERT INTO legal_entity_dim (legal_entity_key,label,kind) VALUES
 ('CONSOLIDATED','Group / consolidated','total'),                             -- DEFAULT member
 ('PARENT_COMPANY','The Company (holding company, unconsolidated)','solo'),
 ('BANK_SOLO','The Bank (banking entity, unconsolidated)','solo');

-- label_norm authored via mapping.normalize.normalize_row_label (underscore
-- slug) — NOT geo_norm's space-lowered convention (see stage2_load/load_v7.py
-- le_lookup / DECISIONS.md legal_entity entry for why the two axes use
-- different normalisers).
INSERT INTO legal_entity_map (label_norm,legal_entity_key) VALUES
 ('the_group','CONSOLIDATED'),
 ('group','CONSOLIDATED'),
 ('consolidated','CONSOLIDATED'),
 ('the_group_consolidated','CONSOLIDATED'),
 ('the_company','PARENT_COMPANY'),
 ('company','PARENT_COMPANY'),
 ('the_bank','BANK_SOLO'),
 ('bank','BANK_SOLO');

-- ============================================================================
-- NOTES (referenced inline above; required reading before downstream analytics)
-- ============================================================================
--
-- NOTE A — The '#' suppressed sentinel
--   '#' means |amount| < S$0.5m and the magnitude is WITHHELD (doc note 3, p.4).
--   It is NOT a dash, NOT a zero, NOT a number. cell_state='suppressed', value_num NULL.
--   It is a DOCUMENT-WIDE lexical rule, not section-scoped: apply it in the same
--   normalisation pass that maps '-'->null and '0'->zero. Do NOT route it through the
--   per-section UNIT override (that pass is for $m vs % vs headcount, a different axis).
--   Analytics consequence: any SUM over a slice containing 'suppressed' cells is a
--   LOWER BOUND. Use v_cell_sumsafe and compare its row count to v_cell_leaf to detect.
--
-- NOTE B — Period precedence (most-specific axis wins)
--   Resolved per cell at load time, denormalised onto cell_fact.period (NEVER NULL):
--       1. col_dim.col_period   (period sits in the column axis: Dec25|Dec24, Key Metrics)
--       2. row_dim.row_period   (period sits in the ROW axis: UOB NPL 'Dec-25'/'Jun-25' rows,
--                                geographies in columns) — the row-axis mirror of col_period;
--                                col wins if BOTH axes carry one (warning emitted on a clash)
--       3. table_t.period       (single-period table; period from the table title/caption)
--       4. document.doc_period  (last-resort fallback from the report's "as at" date)
--   Guarantee: every table resolves to >=1 period. A table may have NULL table_t.period
--   ONLY if every leaf column carries col_period. Loader must assert this invariant.
--   This MIRRORS the unit-override rule (§5.5): the most specific axis overrides the
--   table default, on both the period axis and the unit axis.
--
-- NOTE C — Hierarchy is structural; summation is opt-in; columns are never additive
--   * row_parent (from indentation) gives STRUCTURE only. A section header and its
--     items are parent/child WITHOUT the parent being the sum of children.
--   * A TOTAL row is just a leaf row whose concept_key/unit marks it as a total. There
--     is no is_total column — total-ness lives in the assigned concept, not in structure.
--   * Therefore NEVER enforce "parent = sum(children)" as a constraint. The v_cell_leaf
--     guard only prevents the COMMON double-count (summing a section header with its
--     items); it is a convenience, not a claim that every parent equals its children.
--   * col_parent is GEOMETRY ONLY (visual grouping under a span header). Columns carry
--     no arithmetic relationship in either direction.
--
-- ----------------------------------------------------------------------------
-- DOWNSTREAM ANALYTICS — rules to honour when building the query/analytics layer
-- ----------------------------------------------------------------------------
--   1. PERIOD: always resolve via cell_fact.period (already denormalised). Never read
--      table_t.period directly for a time series — column-period tables would be wrong.
--      When charting "Dec24 vs Dec25" from a side-by-side table, the two points come
--      from two COLUMNS of one table_t row, distinguished by col_period.
--   2. SUMMING: sum over v_cell_sumsafe, not raw cell_fact and not v_cell. If the same
--      slice has more rows in v_cell_leaf than in v_cell_sumsafe, emit a "lower bound,
--      N suppressed cells" caveat rather than a bare number.
--   3. ONE LEVEL ONLY: when aggregating, pick a single hierarchy level (geo_level, or a
--      fixed row level / concept set). Never mix a parent concept with its children in
--      the same SUM. Prefer summing explicit leaf concepts and reconciling against the
--      reported total row (which is itself a leaf) as a CHECK, not as a summand.
--   4. NON-ADDITIVE COLUMNS: some tables (e.g. UOB 11.1 accounting/regulatory linkage)
--      state that column (b) != sum of (c)-(g) because items fall in several risk
--      categories. The schema does NOT encode additivity. Do not cross-sum category
--      columns without checking the table's own footnotes. Treat category columns as
--      independent measures, not a partition.
--   5. NON-NUMERIC COLUMNS: rating-agency columns (S&P/Fitch/Moody's in 12.14 backtests)
--      and headcount/ratio/% rows carry a NON-MONETARY unit. Filter them out of any
--      S$m aggregation by unit; they are row-identifying attributes, not facts.
--   6. COMPOSITE ROW IDENTITY: in IRBA PD-range tables, a PD band ("0.00 to <0.15") is
--      NOT a concept on its own — the same band repeats under every portfolio. Identity
--      is (portfolio, PD-range). concept_key must encode the path, never the bare band.
--      FIRB and AIRB are DIFFERENT table_types (different parameter columns / portfolios).
--   7. RISK-WEIGHT MATRICES (12.9): each asset-class block has its own column set; "150%"
--      is a col_leaf_label string, not a typed dimension. Cross-asset "everything at 150%"
--      queries must match on the label across blocks — there is no risk_weight dimension.
--   8a. LINEAGE REGISTRIES (v7): row_lineage/col_lineage are VERBATIM-lineage identity.
--      Same printed lineage anywhere in the corpus = same id (time series become
--      GROUP BY row_lineage_id). Different wording = different id, even when the
--      meaning is identical (DBS 'Total NSFR high-quality liquid assets (HQLA)'
--      vs OCBC 'Total NSFR HQLA') — bridging those is concept_map's job, and
--      ONLY concept_map's job. Registries are rebuilt-from-dims idempotently;
--      if a rebuild changes anything, the load was corrupt.
--   8. NON-NUMERIC / TEXT TABLES (5.2 instrument features): marked with a non-numeric
--      table_type. EXCLUDE by default from concept reconciliation and from concept
--      time-series sweeps; they store as text and bypass the numeric machinery by design.
-- ============================================================================
