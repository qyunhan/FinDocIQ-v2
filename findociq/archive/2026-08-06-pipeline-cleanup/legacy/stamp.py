"""stamp — wire template alignment into the DB LOAD path.

Given a (doc_id, table_id, table_type) already loaded into schema_v5 (row_dim/
col_dim/cell_fact populated, concept_key columns NULL), STAMP the row axis
(concept_key) and the column axis (col_key) so cross-bank time series become
queryable via v_cell.

Row stamping: build instance Rows from row_dim (parent via row_parent self-join),
SKIP band rows (zero cells in cell_fact — label-only section bands, e.g. 'RSF
Item'), run align() against template_row + concept_map, UPDATE row_dim and
cell_fact for matched rows, write any drift to the review queue CSV.

Column stamping: match col_dim leaf columns (col_hierarchy=1) to template_col
by normalised header equality first, then by position as a fallback; UPDATE
col_dim.col_key (column added on first use — additive, schema_v5-safe).

Usage:
    python3 stamp.py <db_path> <doc_id>:<table_id> <template_key>
    e.g. python3 stamp.py findociq/db/final.db ocbc_nsfr_2025:nsfr_2025-12-31 nsfr
"""
from __future__ import annotations
import os, sys, argparse, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align import align, Row, _norm, Report
from review import write_queue

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
REVIEW_DIR = os.path.join(ROOT, "findociq", "data", "review")


def load_template_rows(con: sqlite3.Connection, table_type: str) -> list[dict]:
    """template_row self-joined on parent_line_no -> parent_label, in row_ord order."""
    cur = con.cursor()
    cur.execute("""
        SELECT t.line_no, t.canonical_label, t.concept_key, p.canonical_label
        FROM template_row t
        LEFT JOIN template_row p ON p.table_type = t.table_type AND p.line_no = t.parent_line_no
        WHERE t.table_type = ?
        ORDER BY t.row_ord
    """, (table_type,))
    return [dict(line_no=ln, canonical_label=lbl, concept_key=ck, parent_label=pl)
            for ln, lbl, ck, pl in cur.fetchall()]


def load_concept_map(con: sqlite3.Connection, table_type: str) -> dict:
    cur = con.cursor()
    cur.execute("SELECT label_norm, concept_key FROM concept_map WHERE table_type = ?", (table_type,))
    return dict(cur.fetchall())


def load_template_cols(con: sqlite3.Connection, table_type: str) -> list[dict]:
    cur = con.cursor()
    cur.execute("SELECT col_ord, canonical_header, col_key FROM template_col "
                "WHERE table_type = ? ORDER BY col_ord", (table_type,))
    return [dict(col_ord=o, canonical_header=h, col_key=k) for o, h, k in cur.fetchall()]


def _ensure_col_key_column(con: sqlite3.Connection) -> None:
    try:
        con.execute("ALTER TABLE col_dim ADD COLUMN col_key TEXT")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def stamp_columns(con: sqlite3.Connection, doc_id: str, table_id: str, template_cols: list[dict]) -> int:
    """Match col_dim leaf columns (col_hierarchy=1) -> template_col, UPDATE col_key.
       Header equality first; positional fallback (col_ord order) for the rest."""
    _ensure_col_key_column(con)
    cur = con.cursor()
    cur.execute("SELECT col_id, col_leaf_label FROM col_dim "
                "WHERE doc_id = ? AND table_id = ? AND col_hierarchy = 1 ORDER BY col_id",
                (doc_id, table_id))
    leaf_cols = cur.fetchall()

    by_header = {}
    for t in template_cols:
        by_header.setdefault(_norm(t["canonical_header"]), t)

    used_ord: set[int] = set()
    assign: dict[int, str] = {}
    leftover_cols = []
    for col_id, label in leaf_cols:
        t = by_header.get(_norm(label))
        if t is not None and t["col_ord"] not in used_ord:
            assign[col_id] = t["col_key"]
            used_ord.add(t["col_ord"])
        else:
            leftover_cols.append(col_id)

    leftover_templates = [t for t in template_cols if t["col_ord"] not in used_ord]
    for col_id, t in zip(leftover_cols, leftover_templates):
        assign[col_id] = t["col_key"]

    n = 0
    for col_id, col_key in assign.items():
        cur.execute("UPDATE col_dim SET col_key = ? WHERE doc_id = ? AND table_id = ? AND col_id = ?",
                    (col_key, doc_id, table_id, col_id))
        n += cur.rowcount
    return n


def stamp_table(con: sqlite3.Connection, doc_id: str, table_id: str, table_type: str,
                 drift_accum: list | None = None) -> dict:
    cur = con.cursor()

    # rows with at least one cell in cell_fact -- band rows (zero cells) are skipped
    cur.execute("SELECT DISTINCT row_id FROM cell_fact WHERE doc_id = ? AND table_id = ?",
                (doc_id, table_id))
    rows_with_cells = {r[0] for r in cur.fetchall()}

    cur.execute("""
        SELECT r.row_id, r.row_leaf_label, r.line_no, p.row_leaf_label
        FROM row_dim r
        LEFT JOIN row_dim p ON p.doc_id = r.doc_id AND p.table_id = r.table_id AND p.row_id = r.row_parent
        WHERE r.doc_id = ? AND r.table_id = ?
        ORDER BY r.row_id
    """, (doc_id, table_id))

    instance_rows: list[Row] = []
    row_id_by_line: dict[str, int] = {}
    for row_id, label, line_no, parent_label in cur.fetchall():
        if row_id not in rows_with_cells:
            continue                                    # skip label-only band row
        instance_rows.append(Row(label=label, line_no=line_no, parent_label=parent_label))
        row_id_by_line[line_no] = row_id

    template = load_template_rows(con, table_type)
    cmap = load_concept_map(con, table_type)
    report = align(instance_rows, template, cmap)

    stamped_cells = 0
    for m in report.matched:
        row_id = row_id_by_line.get(m.line_no)
        if row_id is None:
            continue
        cur.execute("UPDATE row_dim SET concept_key = ? WHERE doc_id = ? AND table_id = ? AND row_id = ?",
                    (m.concept_key, doc_id, table_id, row_id))
        cur.execute("UPDATE cell_fact SET concept_key = ? WHERE doc_id = ? AND table_id = ? AND row_id = ?",
                    (m.concept_key, doc_id, table_id, row_id))
        stamped_cells += cur.rowcount

    # When a caller processes multiple tables of the same doc in one invocation
    # (bare '<doc_id>' on the CLI), drift rows all land under the SAME queue
    # file (keyed by doc_id + table_type only, not table_id). Writing per-table
    # would let each table's write clobber the previous table's rows. So when
    # drift_accum is supplied, just collect rows here -- the caller writes the
    # file ONCE after all tables are processed. Direct callers that don't pass
    # drift_accum keep the old immediate-write, single-table behavior.
    if drift_accum is not None:
        drift_accum.extend(report.drift)
    else:
        # overwrite, never append, so re-running never duplicates rows and a
        # since-fixed drift set clears the file (idempotent).
        path = os.path.join(REVIEW_DIR, f"{doc_id}_{table_type}_drift.csv")
        write_queue(report, doc_id, table_type, path)

    template_cols = load_template_cols(con, table_type)
    stamp_columns(con, doc_id, table_id, template_cols)

    con.commit()
    return dict(matched=len(report.matched), drift=len(report.drift),
                absent=len(report.absent), stamped_cells=stamped_cells)


def _parse_table_ident(ident: str, con: sqlite3.Connection) -> list[str]:
    """'<doc_id>:<table_id>' -> that single table_id.
       '<doc_id>' alone -> every table_id under that doc (all periods)."""
    cur = con.cursor()
    if ":" in ident:
        doc_id, table_id = ident.split(":", 1)
        cur.execute("SELECT 1 FROM table_t WHERE doc_id = ? AND table_id = ?", (doc_id, table_id))
        if cur.fetchone() is None:
            raise SystemExit(f"no such table: doc_id={doc_id!r} table_id={table_id!r}")
        return doc_id, [table_id]
    doc_id = ident
    cur.execute("SELECT table_id FROM table_t WHERE doc_id = ? ORDER BY table_id", (doc_id,))
    table_ids = [r[0] for r in cur.fetchall()]
    if not table_ids:
        raise SystemExit(f"no tables found for doc_id={doc_id!r}")
    return doc_id, table_ids


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Stamp template concept_key/col_key onto already-loaded row_dim/col_dim rows.")
    ap.add_argument("db_path")
    ap.add_argument("table_ident", help="'<doc_id>:<table_id>' or bare '<doc_id>' for all its tables")
    ap.add_argument("template_key", help="template_row.table_type, e.g. nsfr")
    a = ap.parse_args()

    con = sqlite3.connect(a.db_path)
    con.execute("PRAGMA foreign_keys = ON;")
    doc_id, table_ids = _parse_table_ident(a.table_ident, con)

    totals = dict(matched=0, drift=0, absent=0, stamped_cells=0)
    drift_accum: list = []
    for table_id in table_ids:
        stats = stamp_table(con, doc_id, table_id, a.template_key, drift_accum=drift_accum)
        print(f"{table_id}: {stats}")
        for k in totals:
            totals[k] += stats[k]

    # write the queue file ONCE, after all tables in this invocation are
    # processed, so drift from every table is preserved in the union (see
    # stamp_table's drift_accum handling above).
    path = os.path.join(REVIEW_DIR, f"{doc_id}_{a.template_key}_drift.csv")
    write_queue(Report(drift=drift_accum), doc_id, a.template_key, path)

    print(f"TOTAL ({len(table_ids)} tables): {totals}")
