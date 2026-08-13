"""load_to_db — Stage-3 load: html_to_cells output -> schema_v5 SQLite (the DB).

Demonstrates the full HTML -> schema_v5 path actually landing rows in a database
(document/table_t/col_dim/row_dim/cell_fact), with FK enforcement on, then a query-back.

Usage:
    python3 load_to_db.py <stage2.html> <out.db> --doc-id ocbc_nsfr --inst OCBC \
        --family pillar3 --table-type nsfr --title "Net Stable Funding Ratio (NSFR)"
"""
from __future__ import annotations
import os, sys, argparse, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html_to_cells import parse_html

SCHEMA = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "schema", "schema_v5.sql"))


def fresh_db(path: str) -> sqlite3.Connection:
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.executescript(open(SCHEMA).read())   # full schema_v5 (tables + views)
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def load(html: str, con: sqlite3.Connection, doc_id: str, inst: str, family: str,
         table_type: str, title: str, source_file: str) -> dict:
    tables = parse_html(html)
    cur = con.cursor()
    # document
    doc_period = max((t.period for t in tables if t.period), default=None)
    cur.execute("INSERT INTO document(doc_id,institution,doc_family,source_file,doc_period) "
                "VALUES (?,?,?,?,?)", (doc_id, inst, family, source_file, doc_period))
    n_cells = 0
    for ti, t in enumerate(tables):
        table_id = f"{table_type}_{t.period or ti}"
        cur.execute("INSERT INTO table_t(doc_id,table_id,table_title,table_type,period,page_range) "
                    "VALUES (?,?,?,?,?,?)", (doc_id, table_id, title, table_type, t.period, None))
        # group columns (hierarchy 0) get synthetic ids 100+; leaf value cols use their grid id
        groups, gid = {}, 100
        for c in t.cols:
            if c.group and c.group not in groups:
                groups[c.group] = gid
                cur.execute("INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
                            "col_leaf_label,unit) VALUES (?,?,?,?,?,?,?)",
                            (doc_id, table_id, gid, 0, None, c.group, "S$m"))
                gid += 1
        for c in t.cols:
            cur.execute("INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
                        "col_leaf_label,unit) VALUES (?,?,?,?,?,?,?)",
                        (doc_id, table_id, c.col_id, 1, groups.get(c.group), c.leaf_label, "S$m"))
        # rows + cells
        for r in t.rows:
            parent_rowid = t.rows[r.parent_idx].row_idx if r.parent_idx is not None else None
            cur.execute("INSERT INTO row_dim(doc_id,table_id,row_id,row_hierarchy,row_parent,"
                        "row_leaf_label,line_no,unit) VALUES (?,?,?,?,?,?,?,?)",
                        (doc_id, table_id, r.row_idx, r.level, parent_rowid, r.label, r.line_no, "S$m"))
        for r in t.rows:
            for c in r.cells:
                cur.execute("INSERT INTO cell_fact(doc_id,table_id,row_id,col_id,colspan,value_raw,"
                            "value_num,cell_state,is_shade,period) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (doc_id, table_id, r.row_idx, c.col_id, c.colspan, c.value_raw,
                             c.value_num, c.cell_state, c.is_shade, t.period))
                n_cells += 1
    con.commit()
    return dict(tables=len(tables), cells=n_cells)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("html"); ap.add_argument("db")
    ap.add_argument("--doc-id", required=True); ap.add_argument("--inst", required=True)
    ap.add_argument("--family", default="pillar3"); ap.add_argument("--table-type", required=True)
    ap.add_argument("--title", required=True); ap.add_argument("--source-file", default="")
    a = ap.parse_args()
    con = fresh_db(a.db)
    stats = load(open(a.html).read(), con, a.doc_id, a.inst, a.family, a.table_type, a.title, a.source_file)
    print(f"loaded: {stats['tables']} tables, {stats['cells']} cells -> {a.db}")
