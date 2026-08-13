"""Tests for running-header detection (toc_stage.detect_running_headers /
apply_running_header_strip). Locks the 2026-07-16 pivot: a Gemini heading that
is really a repeated page HEADER (not a section) is detected + dropped, its
children reparented to the real base heading. General — keyed on geometry +
the parent graph, never a title/bank literal. See
docs/specs/2026-07-16-running-header-detection.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.toc import toc_stage as T  # noqa: E402


def _sec(sid, title, parent, p0, p1, has_tables):
    return {"id": sid, "title": title, "parent_id": parent,
            "page_start": p0, "page_end": p1, "has_tables": has_tables}


def test_phantom_detected_and_children_reparented():
    """OCBC shape: 'FINANCIAL HIGHLIGHTS (continued)' spans p10-22, recurs at
    page-top on every page, has table-bearing children -> flagged; children
    re-homed to the real 'financial_highlights' base heading."""
    secs = [
        _sec("financial_highlights", "FINANCIAL HIGHLIGHTS", None, 10, 10, False),
        _sec("financial_highlights_continued", "FINANCIAL HIGHLIGHTS (continued)",
             None, 10, 22, True),
        _sec("selected_income_statement_items_2", "Selected Income Statement Items",
             "financial_highlights_continued", 11, 11, False),
        _sec("net_interest_income", "NET INTEREST INCOME",
             "financial_highlights_continued", 13, 13, False),
        _sec("non_interest_income", "NON-INTEREST INCOME",
             "financial_highlights_continued", 14, 14, True),
        _sec("about_ocbc", "ABOUT OCBC", None, 23, 23, False),
    ]
    top = {10: T.squash("FINANCIAL HIGHLIGHTS")}
    for p in range(11, 23):
        top[p] = T.squash("FINANCIAL HIGHLIGHTS (continued)")
    top[23] = T.squash("ABOUT OCBC")

    flagged = T.detect_running_headers(secs, top)
    assert len(flagged) == 1
    f = flagged[0]
    assert f["id"] == "financial_highlights_continued"
    assert f["reparent_to"] == "financial_highlights"
    assert f["recurrence"] == 1.0

    cleaned = T.apply_running_header_strip(secs, flagged)
    ids = {s["id"] for s in cleaned}
    assert "financial_highlights_continued" not in ids
    for cid in ("selected_income_statement_items_2", "net_interest_income",
                "non_interest_income"):
        s = next(x for x in cleaned if x["id"] == cid)
        assert s["parent_id"] == "financial_highlights"
        assert s["path"].startswith("financial_highlights.")


def test_auditor_report_not_flagged_has_tables_gate():
    """A prose section whose running header recurs (0.88) but bears NO
    parent-level table must NOT be stripped — the has_tables gate saves it."""
    secs = [
        _sec("aud", "INDEPENDENT AUDITOR'S REPORT", None, 39, 46, False),
        _sec("opinion", "Our opinion", "aud", 39, 39, False),
        _sec("basis", "Basis for opinion", "aud", 40, 40, False),
    ]
    top = {p: T.squash("INDEPENDENT AUDITOR'S REPORT") for p in range(39, 47)}
    assert T.detect_running_headers(secs, top) == []


def test_table_bearing_parent_no_recurrence_not_flagged():
    """DBS 'overview' shape: table-bearing parent with label-only children, title
    does NOT recur at page-top -> not a running header -> kept."""
    secs = [
        _sec("overview", "OVERVIEW", None, 4, 9, True),
        _sec("second_half", "Second Half", "overview", 9, 9, False),
        _sec("full_year", "Full Year", "overview", 9, 9, False),
    ]
    top = {p: T.squash(f"some other line {p}") for p in range(4, 10)}
    assert T.detect_running_headers(secs, top) == []


def test_short_continued_below_min_span_not_flagged():
    """A genuine 2-page '(continued)' section is below RUNHDR_MIN_SPAN -> kept."""
    secs = [
        _sec("q4", "Q4 PERFORMANCE", None, 4, 6, True),
        _sec("q4c", "Q4 PERFORMANCE (continued)", None, 5, 6, True),
        _sec("a", "A", "q4c", 5, 5, False),
        _sec("b", "B", "q4c", 6, 6, False),
    ]
    top = {4: T.squash("Q4 PERFORMANCE"),
           5: T.squash("Q4 PERFORMANCE (continued)"),
           6: T.squash("Q4 PERFORMANCE (continued)")}
    assert T.detect_running_headers(secs, top) == []


def test_no_children_not_flagged():
    """Recurs + has_tables but has no children -> nothing to re-home -> kept
    (a real long single-section table, not a wrapper)."""
    secs = [
        _sec("big_table", "BIG TABLE", None, 5, 9, True),
    ]
    top = {p: T.squash("BIG TABLE") for p in range(5, 10)}
    assert T.detect_running_headers(secs, top) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
