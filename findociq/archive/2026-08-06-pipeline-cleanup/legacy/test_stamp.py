"""Tests for stamp.py — the drift/review-queue CSV must accumulate drift rows
from ALL tables processed within one invocation, not be clobbered per-table."""
from __future__ import annotations
import csv
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stamp


def _build_db(con: sqlite3.Connection) -> None:
    con.executescript("""
        CREATE TABLE document (doc_id TEXT PRIMARY KEY);
        CREATE TABLE table_t (
            doc_id TEXT NOT NULL, table_id TEXT NOT NULL, table_title TEXT,
            table_type TEXT NOT NULL, period DATE,
            PRIMARY KEY (doc_id, table_id)
        );
        CREATE TABLE col_dim (
            doc_id TEXT NOT NULL, table_id TEXT NOT NULL, col_id INTEGER NOT NULL,
            col_hierarchy INTEGER NOT NULL, col_leaf_label TEXT NOT NULL,
            PRIMARY KEY (doc_id, table_id, col_id)
        );
        CREATE TABLE row_dim (
            doc_id TEXT NOT NULL, table_id TEXT NOT NULL, row_id INTEGER NOT NULL,
            row_hierarchy INTEGER NOT NULL, row_parent INTEGER,
            row_leaf_label TEXT NOT NULL, line_no TEXT, concept_key TEXT,
            PRIMARY KEY (doc_id, table_id, row_id)
        );
        CREATE TABLE cell_fact (
            doc_id TEXT NOT NULL, table_id TEXT NOT NULL, row_id INTEGER NOT NULL,
            col_id INTEGER NOT NULL, value_raw TEXT, value_num REAL, concept_key TEXT
        );
        CREATE TABLE template_row (
            table_type TEXT NOT NULL, row_ord INTEGER NOT NULL, line_no TEXT,
            canonical_label TEXT NOT NULL, parent_line_no TEXT, concept_key TEXT NOT NULL,
            PRIMARY KEY (table_type, row_ord)
        );
        CREATE TABLE template_col (
            table_type TEXT NOT NULL, col_ord INTEGER NOT NULL,
            canonical_header TEXT NOT NULL, col_key TEXT NOT NULL,
            PRIMARY KEY (table_type, col_ord)
        );
        CREATE TABLE concept_map (
            table_type TEXT NOT NULL, label_norm TEXT NOT NULL, concept_key TEXT NOT NULL,
            PRIMARY KEY (table_type, label_norm)
        );
    """)

    # one column of template + one column of data, enough to stamp columns
    con.execute("INSERT INTO template_col VALUES ('nsfr', 1, 'No maturity', 'unw_no_maturity')")

    # a two-line template so one instance row matches ('Regulatory capital')
    # and 'Total ASF' is a distinct concept -- unmatched instance rows drift.
    con.executemany(
        "INSERT INTO template_row (table_type,row_ord,line_no,canonical_label,parent_line_no,concept_key) VALUES (?,?,?,?,?,?)",
        [
            ("nsfr", 1, "2", "Regulatory capital", None, "asf_capital_reg"),
            ("nsfr", 2, "14", "Total ASF", None, "asf_total"),
        ],
    )

    con.execute("INSERT INTO document VALUES ('doc1')")

    # table A ("p2025_09_30" / Sep) -- has one row that matches template AND one
    # row that has no good match at all -> produces >= 1 drift row. table_id
    # sorts BEFORE the Dec table below, so it is processed FIRST (matching the
    # observed real-world ordering: Sep before Dec).
    con.execute("INSERT INTO table_t VALUES ('doc1', 'p2025_09_30', 'NSFR Sep', 'nsfr', '2025-09-30')")
    con.execute("INSERT INTO row_dim VALUES ('doc1', 'p2025_09_30', 1, 1, NULL, 'Regulatory capital', '2', NULL)")
    con.execute("INSERT INTO row_dim VALUES ('doc1', 'p2025_09_30', 2, 1, NULL, 'Some completely unrelated widget row', '99', NULL)")
    con.execute("INSERT INTO col_dim VALUES ('doc1', 'p2025_09_30', 1, 1, 'No maturity')")
    con.execute("INSERT INTO cell_fact VALUES ('doc1', 'p2025_09_30', 1, 1, '100', 100.0, NULL)")
    con.execute("INSERT INTO cell_fact VALUES ('doc1', 'p2025_09_30', 2, 1, '5', 5.0, NULL)")

    # table B ("p2025_12_31" / Dec) -- rows that match the template cleanly ->
    # zero drift. Processed SECOND (last) -- this is the write that clobbered
    # the Sep drift row before the fix.
    con.execute("INSERT INTO table_t VALUES ('doc1', 'p2025_12_31', 'NSFR Dec', 'nsfr', '2025-12-31')")
    con.execute("INSERT INTO row_dim VALUES ('doc1', 'p2025_12_31', 1, 1, NULL, 'Regulatory capital', '2', NULL)")
    con.execute("INSERT INTO row_dim VALUES ('doc1', 'p2025_12_31', 2, 1, NULL, 'Total ASF', '14', NULL)")
    con.execute("INSERT INTO col_dim VALUES ('doc1', 'p2025_12_31', 1, 1, 'No maturity')")
    con.execute("INSERT INTO cell_fact VALUES ('doc1', 'p2025_12_31', 1, 1, '100', 100.0, NULL)")
    con.execute("INSERT INTO cell_fact VALUES ('doc1', 'p2025_12_31', 2, 1, '200', 200.0, NULL)")

    con.commit()


def _run_full_doc(tmp_path, monkeypatch) -> str:
    """Mirror stamp.py's __main__ loop for a bare '<doc_id>' invocation: run
    stamp_table for every table_id under doc1 (table_t ORDER BY table_id ->
    Sep then Dec, matching _parse_table_ident), accumulating drift, then write
    the queue CSV once. Returns the path to the drift CSV."""
    db_path = os.path.join(tmp_path, "test.db")
    con = sqlite3.connect(db_path)
    _build_db(con)

    review_dir = os.path.join(tmp_path, "review")
    monkeypatch.setattr(stamp, "REVIEW_DIR", review_dir)

    doc_id, table_ids = stamp._parse_table_ident("doc1", con)
    assert table_ids == ["p2025_09_30", "p2025_12_31"]  # Sep (drift) first, Dec (clean) last

    drift_accum: list = []
    for table_id in table_ids:
        stamp.stamp_table(con, doc_id, table_id, "nsfr", drift_accum=drift_accum)
    path = os.path.join(review_dir, "doc1_nsfr_drift.csv")
    stamp.write_queue(stamp.Report(drift=drift_accum), doc_id, "nsfr", path)
    con.close()
    return path


def test_baseline_full_doc_run_produces_drift_csv(tmp_path, monkeypatch):
    path = _run_full_doc(str(tmp_path), monkeypatch)
    assert os.path.exists(path)


def test_drift_from_earlier_table_is_not_clobbered_by_later_table(tmp_path, monkeypatch):
    """Sep processes first with >=1 drift row, Dec processes second with 0
    drift rows. The final CSV must still contain Sep's drift row -- it must
    not be overwritten/cleared by Dec's (later, empty) write."""
    path = _run_full_doc(str(tmp_path), monkeypatch)
    with open(path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 1, "expected sep's drift row to survive in the final CSV, found none"
    assert any(r["instance_label"] == "Some completely unrelated widget row" for r in rows)


def test_rerun_does_not_accumulate_stale_rows_across_invocations(tmp_path, monkeypatch):
    """Re-running the whole thing a second time (fresh call) must not double
    the rows -- each invocation still starts from a clean slate."""
    path = _run_full_doc(str(tmp_path), monkeypatch)
    with open(path) as f:
        first_rows = list(csv.DictReader(f))

    # second, independent invocation reusing the same DB fixture builder
    db_path = os.path.join(str(tmp_path), "test2.db")
    con = sqlite3.connect(db_path)
    _build_db(con)
    review_dir = os.path.join(str(tmp_path), "review")
    monkeypatch.setattr(stamp, "REVIEW_DIR", review_dir)
    doc_id, table_ids = stamp._parse_table_ident("doc1", con)
    drift_accum: list = []
    for table_id in table_ids:
        stamp.stamp_table(con, doc_id, table_id, "nsfr", drift_accum=drift_accum)
    stamp.write_queue(stamp.Report(drift=drift_accum), doc_id, "nsfr", path)
    con.close()

    with open(path) as f:
        second_rows = list(csv.DictReader(f))
    assert len(second_rows) == len(first_rows)
