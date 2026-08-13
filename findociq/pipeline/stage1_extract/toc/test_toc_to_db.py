"""Regression tests for toc_to_db.load().

Root cause covered: `seq` is PHYSICAL reading order (toc_stage renumbers by
anchor position), NOT a topological order over `parent_section`. A subsection
can print BEFORE its parent heading, so a child may carry a smaller seq than
its parent. The loader must still insert parents before children for the
self-FK (doc_id, parent_section) -> section(doc_id, section_id); it therefore
cannot rely on seq order.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.toc import toc_to_db  # noqa: E402


def _fresh_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    toc_to_db.ensure_db(conn, db)
    return conn


def _doc(doc_id="DBS_x"):
    return {"doc_id": doc_id, "doc_family": "financial_stmt",
            "source_pdf": "x.pdf"}


def test_child_before_parent_in_seq_loads(tmp_path):
    """The DBS/OCBC 4Q25 shape: a level-2 child anchored on an earlier page
    gets seq=1 while its level-1 parent gets seq=2. Must load, not raise."""
    conn = _fresh_db(tmp_path)
    doc = _doc()
    sections = [
        # child first in reading order (seq 1) ...
        {"id": "dividends", "section_no": None, "title": "Dividends",
         "level": 2, "parent_id": "financial_results", "path": None, "seq": 1},
        # ... parent prints later (seq 2)
        {"id": "financial_results", "section_no": None,
         "title": "Financial results", "level": 1, "parent_id": None,
         "path": None, "seq": 2},
    ]
    toc_to_db.load(conn, doc, sections, "DBS Group Holdings Ltd", "2025-12-31")

    rows = dict(conn.execute(
        "SELECT section_id, parent_section FROM section WHERE doc_id=?",
        (doc["doc_id"],)).fetchall())
    assert rows == {"dividends": "financial_results",
                    "financial_results": None}
    # every FK resolves
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_topo_chain_reversed_in_seq_loads(tmp_path):
    """3-level chain whose seq order is fully reversed (grandchild, child,
    root). Topological insert must still satisfy the self-FK."""
    conn = _fresh_db(tmp_path)
    doc = _doc("DBS_y")
    sections = [
        {"id": "gc", "section_no": None, "title": "GC", "level": 3,
         "parent_id": "c", "path": None, "seq": 1},
        {"id": "c", "section_no": None, "title": "C", "level": 2,
         "parent_id": "r", "path": None, "seq": 2},
        {"id": "r", "section_no": None, "title": "R", "level": 1,
         "parent_id": None, "path": None, "seq": 3},
    ]
    toc_to_db.load(conn, doc, sections, "DBS Group Holdings Ltd", "2025-12-31")
    n = conn.execute("SELECT COUNT(*) FROM section WHERE doc_id=?",
                     (doc["doc_id"],)).fetchone()[0]
    assert n == 3
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_institution_for_falls_back_to_source_pdf_path():
    # doc_id prefix wins when present
    assert toc_to_db.institution_for("DBS_1Q25_trading_update", "x.pdf") \
        == "DBS Group Holdings Ltd"
    # UOB's own IR filenames never carry a bank code — the scraper's
    # <BANK>/<year>/<quarter>/ placement directory is the only signal left
    assert toc_to_db.institution_for(
        "performance-highlights-1q-2025",
        "findociq/data/sources/financial_statements/UOB/2025/Q1/"
        "performance-highlights-1q-2025.pdf") == "United Overseas Bank Ltd"
    # neither prefix nor path resolves -> None (fail loud upstream)
    assert toc_to_db.institution_for("mystery_doc", "some/random/dir/x.pdf") is None
    assert toc_to_db.institution_for("mystery_doc", None) is None


if __name__ == "__main__":
    import tempfile
    for fn in (test_child_before_parent_in_seq_loads,
               test_topo_chain_reversed_in_seq_loads):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print(f"PASS  {fn.__name__}")
    test_institution_for_falls_back_to_source_pdf_path()
    print(f"PASS  {test_institution_for_falls_back_to_source_pdf_path.__name__}")
    print("ALL PASS")
