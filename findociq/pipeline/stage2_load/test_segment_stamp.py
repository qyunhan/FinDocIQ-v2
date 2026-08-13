"""Plain check() script for BUSINESS-SEGMENT STAMPING in stage2_load.load_v7 (NO pytest).
Exit 0 all-pass / 1 any-fail.

Run:  python findociq/pipeline/stage2_load/test_segment_stamp.py

Covers the deterministic, zero-API segment dimension wired 2026-07-14 (mirror of
the geo stamp):
  * normalisation (seg_norm) = _clean_label then lower — the SAME rule segment_map
    label_norm is authored in; footnote markers stripped ('Others¹' -> others).
  * EXACT full-label match only — 'Trading income' must NOT stamp (never substring),
    incl. the user-approved plain aliases 'group'/'total' -> SEG_TOTAL and the UOB
    abbreviations 'GR'/'GWB'/'GM'.
  * segment axis in COLUMNS (all 3 banks) stamped on col leaves AND span-banner
    groups ('Markets' banner -> SEG_MARKETS); axis in ROWS stamped on row_dim.
  * DEFAULT-MEMBER trick: effective per-cell segment = COALESCE(row,col,'SEG_TOTAL')
    and geo = COALESCE(row,col,table) in v_cell / v_cell_flat (always NULL since
    geography stamping was retired 2026-08-12) — a whole-bank
    cell (no explicit slice) reports SEG_TOTAL / GLOBAL, so it is a filter not a case.
  * column-sum reconciliation (warning gate): FIRES on a segment table where member
    columns must sum to the total column (one bad member row -> exactly one warning);
    does NOT fire on a period-column table (no dimension partitions the columns).
  * dim integrity: every segment_map target + every parent_seg resolves in
    segment_dim; SEG_TOTAL is the level-'total', parent-NULL default member.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # pipeline/ on path
from stage2_load.load_v7 import load_units, seg_lookup, seg_norm  # noqa: E402
from stage1_extract.chunk.schema import Extraction, GCell, GColumn, GRow, GTable  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_SCHEMA = _REPO / "findociq/schema/schema_v7.sql"

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


def _fresh_db(td: str) -> Path:
    db = Path(td) / "seg_v7.db"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA.read_text())
    con.commit()
    con.close()
    return db


# ===========================================================================
# 1) NORMALISATION + EXACT MATCH (pure, against the real seeded segment_map)
# ===========================================================================
def normalisation_tests(seg_map: dict[str, str]) -> None:
    print("Normalisation + exact match (seg_norm / seg_lookup)")

    check("seg_norm('Institutional Banking') lowercases",
          seg_norm("Institutional Banking") == "institutional banking")
    check("seg_norm('Others¹') strips footnote -> 'others'",
          seg_norm("Others¹") == "others", seg_norm("Others¹"))
    check("seg_norm('Global  Markets') collapses ws",
          seg_norm("Global  Markets") == "global markets", seg_norm("Global  Markets"))
    check("seg_norm(None) -> ''", seg_norm(None) == "")

    # canonical house names across the three banks
    check("DBS 'Consumer Banking/ Wealth Management' -> SEG_RETAIL",
          seg_lookup("Consumer Banking/ Wealth Management", seg_map) == "SEG_RETAIL")
    check("OCBC 'Global Consumer/ Private Banking' -> SEG_RETAIL",
          seg_lookup("Global Consumer/ Private Banking", seg_map) == "SEG_RETAIL")
    check("DBS 'Institutional Banking' -> SEG_WHOLESALE",
          seg_lookup("Institutional Banking", seg_map) == "SEG_WHOLESALE")
    check("OCBC 'Global Wholesale Banking' -> SEG_WHOLESALE",
          seg_lookup("Global Wholesale Banking", seg_map) == "SEG_WHOLESALE")
    check("OCBC 'Global Markets' -> SEG_MARKETS",
          seg_lookup("Global Markets", seg_map) == "SEG_MARKETS")
    check("DBS 'Trading' -> SEG_MARKETS", seg_lookup("Trading", seg_map) == "SEG_MARKETS")
    check("DBS 'Markets' (span banner) -> SEG_MARKETS",
          seg_lookup("Markets", seg_map) == "SEG_MARKETS")
    check("OCBC 'Insurance' -> SEG_INSURANCE",
          seg_lookup("Insurance", seg_map) == "SEG_INSURANCE")
    check("'Others' -> SEG_OTHER", seg_lookup("Others", seg_map) == "SEG_OTHER")

    # UOB abbreviations (harvested as leaves) + full names
    check("UOB 'GR' -> SEG_RETAIL", seg_lookup("GR", seg_map) == "SEG_RETAIL")
    check("UOB 'GWB' -> SEG_WHOLESALE", seg_lookup("GWB", seg_map) == "SEG_WHOLESALE")
    check("UOB 'GM' -> SEG_MARKETS", seg_lookup("GM", seg_map) == "SEG_MARKETS")
    check("UOB 'Group Retail' -> SEG_RETAIL",
          seg_lookup("Group Retail", seg_map) == "SEG_RETAIL")
    check("UOB 'Group Wholesale Banking' -> SEG_WHOLESALE",
          seg_lookup("Group Wholesale Banking", seg_map) == "SEG_WHOLESALE")

    # USER-APPROVED default-member plain aliases
    check("'Group' -> SEG_TOTAL (whole-bank column IS the total slice)",
          seg_lookup("Group", seg_map) == "SEG_TOTAL")
    check("'Total' -> SEG_TOTAL", seg_lookup("Total", seg_map) == "SEG_TOTAL")

    # SUBSTRING GUARD — a label CONTAINING a segment name but not equal to it must
    # NOT stamp (exact full-label match only).
    check("SUBSTRING GUARD 'Trading income' -> None",
          seg_lookup("Trading income", seg_map) is None,
          str(seg_lookup("Trading income", seg_map)))
    check("SUBSTRING GUARD 'Total income' -> None (not the total column)",
          seg_lookup("Total income", seg_map) is None,
          str(seg_lookup("Total income", seg_map)))
    check("'Net interest income' -> None",
          seg_lookup("Net interest income", seg_map) is None)
    check("DBS 'Commercial Book' -> None (supra-segment, deliberately unmapped)",
          seg_lookup("Commercial Book", seg_map) is None)


# ===========================================================================
# 2) DIM INTEGRITY (every segment_map target + parent resolves in segment_dim)
# ===========================================================================
def dim_tests(db: Path) -> None:
    print("\nDim integrity (segment_dim / segment_map)")
    con = sqlite3.connect(db)
    cur = con.cursor()

    orphan_map = cur.execute(
        "SELECT label_norm, segment_key FROM segment_map WHERE segment_key NOT IN "
        "(SELECT segment_key FROM segment_dim)").fetchall()
    check("every segment_map target exists in segment_dim", orphan_map == [], str(orphan_map))

    orphan_parent = cur.execute(
        "SELECT segment_key, parent_seg FROM segment_dim WHERE parent_seg IS NOT NULL AND "
        "parent_seg NOT IN (SELECT segment_key FROM segment_dim)").fetchall()
    check("every segment_dim parent_seg resolves", orphan_parent == [], str(orphan_parent))

    total = cur.execute("SELECT seg_level, parent_seg FROM segment_dim WHERE "
                        "segment_key='SEG_TOTAL'").fetchone()
    check("SEG_TOTAL is level 'total', parent NULL (the default member)",
          total == ("total", None), str(total))
    members = dict(cur.execute("SELECT segment_key, parent_seg FROM segment_dim WHERE "
                               "segment_key != 'SEG_TOTAL'").fetchall())
    check("all 5 members parent to SEG_TOTAL",
          set(members) == {"SEG_RETAIL", "SEG_WHOLESALE", "SEG_MARKETS",
                           "SEG_INSURANCE", "SEG_OTHER"}
          and all(p == "SEG_TOTAL" for p in members.values()), str(members))
    con.close()


# ===========================================================================
# 3) SYNTHETIC LOAD — col leaves, span banner, rows, default member, reconcile
# ===========================================================================
def _cells(*vals: str) -> list[GCell]:
    return [GCell(value=v) for v in vals]


def load_tests(db: Path) -> None:
    print("\nSynthetic load — col/row/banner stamping + default member + reconcile")

    # --- Table SEG_COLS: DBS shape. 'Commercial Book' + 'Markets' span banners over
    # segment leaves + a 'Total' column. Row 'Profit before tax' is a BAD member row
    # (1+2+3+4=10 != 99) -> exactly ONE reconciliation warning.
    seg_cols = GTable(
        title="Segment Cols Table",
        label_header="$m",
        columns=[
            GColumn(group="Commercial Book", leaf="Consumer Banking/ Wealth Management"),
            GColumn(group="Commercial Book", leaf="Institutional Banking"),
            GColumn(group="Commercial Book", leaf="Others"),
            GColumn(group="Markets", leaf="Trading"),
            GColumn(group=None, leaf="Total"),
        ],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                 values=_cells("10", "20", "5", "15", "50")),   # 10+20+5+15 = 50 OK
            GRow(row_id="2", row_type="total", level=1, label="Profit before tax",
                 values=_cells("1", "2", "3", "4", "99")),       # 1+2+3+4 = 10 != 99 BAD
        ],
    )
    # --- Table SEG_RECON: DBS shape where EVERY member row reconciles to the Total
    # column -> the verified column-sum relation is RECORDED: each member col gets
    # col_dim.sums_to = the Total col_id (+ sums_sign +1); the Total col stays NULL.
    seg_recon = GTable(
        title="Segment Recon Table",
        label_header="$m",
        columns=[
            GColumn(group=None, leaf="Consumer Banking/ Wealth Management"),
            GColumn(group=None, leaf="Institutional Banking"),
            GColumn(group=None, leaf="Others"),
            GColumn(group=None, leaf="Trading"),
            GColumn(group=None, leaf="Total"),
        ],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                 values=_cells("10", "20", "5", "15", "50")),   # 10+20+5+15 = 50 OK
            GRow(row_id="2", row_type="data", level=1, label="Fee income",
                 values=_cells("4", "3", "2", "1", "10")),       # 4+3+2+1 = 10 OK
        ],
    )
    # --- Table SEG_ROWS: segments in ROWS (stamp row_dim.segment_key). Single value col.
    seg_rows = GTable(
        title="Segment Rows Table",
        label_header="$m",
        columns=[GColumn(group="2025", leaf="$m")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Group Retail", values=_cells("100")),
            GRow(row_id="2", row_type="data", level=1, label="Group Wholesale Banking",
                 values=_cells("80")),
            GRow(row_id="3", row_type="data", level=1, label="Global Markets", values=_cells("30")),
            GRow(row_id="4", row_type="total", level=0, label="Total", values=_cells("210")),
        ],
    )
    # --- Table PERIOD: period columns, no segment axis. Reconcile must NOT fire.
    period_tbl = GTable(
        title="Income Statement",
        label_header="$m",
        columns=[GColumn(group=None, leaf="2025"), GColumn(group=None, leaf="2024")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Net interest income",
                 values=_cells("100", "90")),
            GRow(row_id="2", row_type="data", level=1, label="Fee income", values=_cells("40", "35")),
        ],
    )

    with tempfile.TemporaryDirectory() as td:
        con = sqlite3.connect(db)
        con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                    "VALUES ('SYN','Synthetic Bank','financial_stmt','2025-12-31')")
        con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                    "section_level,parent_section,seq) VALUES ('SYN','s1','1','S',1,NULL,1)")
        con.commit()
        con.close()

        parsed = Path(td) / "parsed.json"
        parsed.write_text(json.dumps(
            Extraction(tables=[seg_cols, seg_recon, seg_rows, period_tbl]).model_dump()))
        summary = load_units(str(db), "SYN",
                             [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])

        con = sqlite3.connect(db)
        con.execute("PRAGMA foreign_keys = ON")
        cur = con.cursor()

        def tid(prefix: str) -> str:
            return cur.execute("SELECT table_id FROM table_t WHERE doc_id='SYN' AND "
                               "table_id LIKE ?", (f"%{prefix}%",)).fetchone()[0]

        c_id, r_id, p_id = tid("segment_cols"), tid("segment_rows"), tid("income_statement")
        rec_id = tid("segment_recon")

        # --- COLUMN leaf stamping (Table SEG_COLS) ---
        cseg = dict(cur.execute("SELECT col_leaf_label, segment_key FROM col_dim WHERE "
                                "doc_id='SYN' AND table_id=? AND col_hierarchy=1", (c_id,)).fetchall())
        check("col 'Consumer Banking/ Wealth Management' -> SEG_RETAIL",
              cseg.get("Consumer Banking/ Wealth Management") == "SEG_RETAIL", str(cseg))
        check("col 'Institutional Banking' -> SEG_WHOLESALE",
              cseg.get("Institutional Banking") == "SEG_WHOLESALE")
        check("col 'Trading' -> SEG_MARKETS", cseg.get("Trading") == "SEG_MARKETS")
        check("col 'Others' -> SEG_OTHER", cseg.get("Others") == "SEG_OTHER")
        check("col 'Total' -> SEG_TOTAL", cseg.get("Total") == "SEG_TOTAL")

        # --- SPAN-BANNER (hierarchy 0) stamping ---
        gseg = dict(cur.execute("SELECT col_leaf_label, segment_key FROM col_dim WHERE "
                                "doc_id='SYN' AND table_id=? AND col_hierarchy=0", (c_id,)).fetchall())
        check("banner 'Markets' -> SEG_MARKETS", gseg.get("Markets") == "SEG_MARKETS", str(gseg))
        check("banner 'Commercial Book' -> NULL (supra-segment, unmapped)",
              gseg.get("Commercial Book") is None)

        # --- ROW-axis stamping (Table SEG_ROWS) ---
        rseg = dict(cur.execute("SELECT row_leaf_label, segment_key FROM row_dim WHERE "
                                "doc_id='SYN' AND table_id=?", (r_id,)).fetchall())
        check("row 'Group Retail' -> SEG_RETAIL", rseg.get("Group Retail") == "SEG_RETAIL", str(rseg))
        check("row 'Group Wholesale Banking' -> SEG_WHOLESALE",
              rseg.get("Group Wholesale Banking") == "SEG_WHOLESALE")
        check("row 'Global Markets' -> SEG_MARKETS", rseg.get("Global Markets") == "SEG_MARKETS")
        check("row 'Total' -> SEG_TOTAL", rseg.get("Total") == "SEG_TOTAL")

        # --- DEFAULT-MEMBER COALESCE in v_cell / v_cell_flat ---
        # SEG_COLS: a cell under the 'Consumer …' column reports SEG_RETAIL (col stamp).
        retail_cell = cur.execute(
            "SELECT segment_key FROM v_cell WHERE doc_id='SYN' AND table_id=? AND "
            "col_id=(SELECT col_id FROM col_dim WHERE doc_id='SYN' AND table_id=? AND "
            "col_leaf_label='Consumer Banking/ Wealth Management') LIMIT 1", (c_id, c_id)).fetchone()
        check("v_cell effective segment (col axis) = SEG_RETAIL", retail_cell[0] == "SEG_RETAIL",
              str(retail_cell))

        # --- cell_fact SELF-DESCRIBING: segment_key/geo_key MATERIALISED at load ---
        # segment-in-cols cell (Consumer column) -> segment SEG_RETAIL, geo NULL (retired).
        cf_retail = cur.execute(
            "SELECT segment_key, geo_key FROM cell_fact WHERE doc_id='SYN' AND table_id=? AND "
            "col_id=(SELECT col_id FROM col_dim WHERE doc_id='SYN' AND table_id=? AND "
            "col_leaf_label='Consumer Banking/ Wealth Management') LIMIT 1", (c_id, c_id)).fetchone()
        # geo_key is NULL everywhere since geography stamping was retired 2026-08-12;
        # the column and the v_cell pass-through remain, the loader just never writes it.
        check("cell_fact segment-in-cols: Consumer cell segment_key=SEG_RETAIL, geo_key=NULL",
              cf_retail == ("SEG_RETAIL", None), str(cf_retail))
        # plain period-table cell (no slice) -> SEG_TOTAL default member, geo NULL.
        cf_plain = cur.execute("SELECT segment_key, geo_key FROM cell_fact WHERE doc_id='SYN' "
                               "AND table_id=? LIMIT 1", (p_id,)).fetchone()
        check("cell_fact plain cell segment_key=SEG_TOTAL, geo_key=NULL",
              cf_plain == ("SEG_TOTAL", None), str(cf_plain))
        # PARITY: materialised value == the pre-change view's COALESCE(row,col,default)
        # for EVERY cell (0 mismatch) — the invariant the view change relies on.
        mism = cur.execute(
            "SELECT COUNT(*) FROM cell_fact f "
            "JOIN row_dim r ON r.doc_id=f.doc_id AND r.table_id=f.table_id AND r.row_id=f.row_id "
            "JOIN col_dim c ON c.doc_id=f.doc_id AND c.table_id=f.table_id AND c.col_id=f.col_id "
            "JOIN table_t t ON t.doc_id=f.doc_id AND t.table_id=f.table_id "
            "WHERE f.doc_id='SYN' AND ("
            "  f.segment_key IS NOT COALESCE(r.segment_key, c.segment_key, 'SEG_TOTAL') OR "
            "  f.geo_key IS NOT COALESCE(r.geo_key, c.geo_key, t.geo_key))").fetchone()[0]
        check("PARITY: cell_fact segment/geo == COALESCE(row,col,default), 0 mismatch",
              mism == 0, str(mism))
        # PERIOD table: no explicit slice -> default member SEG_TOTAL and geo GLOBAL.
        pdef = cur.execute("SELECT segment_key, geo_key FROM v_cell WHERE doc_id='SYN' AND "
                           "table_id=? LIMIT 1", (p_id,)).fetchone()
        check("v_cell default member: unstamped cell -> segment SEG_TOTAL", pdef[0] == "SEG_TOTAL",
              str(pdef))
        check("v_cell default member: unstamped cell -> geo NULL", pdef[1] is None, str(pdef))
        pflat = cur.execute("SELECT segment_key FROM v_cell_flat WHERE doc_id='SYN' AND "
                            "table_id=? LIMIT 1", (p_id,)).fetchone()
        check("v_cell_flat exposes default member SEG_TOTAL", pflat[0] == "SEG_TOTAL", str(pflat))
        # SEG_ROWS: a 'Global Markets' row cell reports SEG_MARKETS (row stamp).
        rmk = cur.execute(
            "SELECT segment_key FROM v_cell_flat WHERE doc_id='SYN' AND table_id=? AND "
            "row_id=(SELECT row_id FROM row_dim WHERE doc_id='SYN' AND table_id=? AND "
            "row_leaf_label='Global Markets')", (r_id, r_id)).fetchone()
        check("v_cell_flat effective segment (row axis) = SEG_MARKETS", rmk[0] == "SEG_MARKETS",
              str(rmk))

        # --- COLUMN-SUM RECONCILIATION ---
        recon = [w for w in summary["warnings"] if "members sum" in w]
        c_recon = [w for w in recon if w.startswith(c_id) and "segment" in w]
        p_recon = [w for w in recon if w.startswith(p_id)]
        check("SEG_COLS: exactly ONE segment reconciliation warning (bad member row)",
              len(c_recon) == 1 and "Profit before tax" in c_recon[0], str(c_recon))
        check("SEG_COLS: good row 'Net interest income' does NOT warn",
              not any("Net interest income" in w for w in c_recon), str(c_recon))
        check("PERIOD table: ZERO reconciliation warnings (no dimension partitions cols)",
              len(p_recon) == 0, str(p_recon))
        check("SEG_ROWS: ZERO column reconciliation warnings (segments in rows, not cols)",
              not any(w.startswith(r_id) for w in recon), str([w for w in recon if w.startswith(r_id)]))

        # --- COL_DIM.SUMS_TO (verified column-sum relation, mirror of row_dim) ---
        # SEG_RECON: every member row reconciles -> members link to the Total col.
        rec_cols = dict(cur.execute(
            "SELECT col_leaf_label, sums_to FROM col_dim WHERE doc_id='SYN' AND "
            "table_id=? AND col_hierarchy=1", (rec_id,)).fetchall())
        total_col_id = cur.execute(
            "SELECT col_id FROM col_dim WHERE doc_id='SYN' AND table_id=? AND "
            "col_leaf_label='Total'", (rec_id,)).fetchone()[0]
        check("SEG_RECON: member 'Consumer …' col sums_to = Total col_id",
              rec_cols.get("Consumer Banking/ Wealth Management") == total_col_id, str(rec_cols))
        check("SEG_RECON: member 'Institutional Banking' col sums_to = Total col_id",
              rec_cols.get("Institutional Banking") == total_col_id)
        check("SEG_RECON: member 'Others' col sums_to = Total col_id",
              rec_cols.get("Others") == total_col_id)
        check("SEG_RECON: member 'Trading' col sums_to = Total col_id",
              rec_cols.get("Trading") == total_col_id)
        check("SEG_RECON: Total col sums_to NULL (nothing stored on the total)",
              rec_cols.get("Total") is None, str(rec_cols.get("Total")))
        rec_signs = dict(cur.execute(
            "SELECT col_leaf_label, sums_sign FROM col_dim WHERE doc_id='SYN' AND "
            "table_id=? AND col_hierarchy=1 AND sums_to IS NOT NULL", (rec_id,)).fetchall())
        check("SEG_RECON: all recorded members sums_sign = +1",
              all(s == 1 for s in rec_signs.values()) and len(rec_signs) == 4, str(rec_signs))
        # SEG_COLS: a BAD member row means the block does NOT reconcile -> NULL.
        c_sums = [s for (s,) in cur.execute(
            "SELECT sums_to FROM col_dim WHERE doc_id='SYN' AND table_id=? AND "
            "col_hierarchy=1", (c_id,)).fetchall()]
        check("SEG_COLS: non-reconciling block leaves every col sums_to NULL",
              all(s is None for s in c_sums), str(c_sums))
        # PERIOD table: no dimension partitions the columns -> no col sums_to.
        p_sums = [s for (s,) in cur.execute(
            "SELECT sums_to FROM col_dim WHERE doc_id='SYN' AND table_id=?", (p_id,)).fetchall()]
        check("PERIOD table: no col sums_to (columns are periods, not a partition)",
              all(s is None for s in p_sums), str(p_sums))

        fk = cur.execute("PRAGMA foreign_key_check").fetchall()
        check("PRAGMA foreign_key_check clean", fk == [], str(fk))
        con.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        db = _fresh_db(td)
        sm = dict(sqlite3.connect(db).execute(
            "SELECT label_norm, segment_key FROM segment_map").fetchall())
        normalisation_tests(sm)
        dim_tests(db)
        load_tests(db)
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
