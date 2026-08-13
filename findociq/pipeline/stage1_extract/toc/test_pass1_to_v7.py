"""Tests for the Pillar 3 -> schema_v7 TOC adapter (toc/pass1_to_v7.py).
Locks: dotted-number hierarchy derivation + safe parent resolution so the
section self-FK always holds. General — the section_id IS the hierarchy.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.toc import pass1_to_v7 as A  # noqa: E402


def _p1(section_id, number, p0, p1=None):
    return {"section_id": section_id, "part": section_id.split(".")[0],
            "number": number, "title": f"T {section_id}", "page_ref": "",
            "start_page": p0, "end_page": p1 or p0}


def test_level_from_number_depth():
    p1 = {"sections": [_p1("A.2", "2", 5), _p1("A.5.1", "5.1", 7),
                       _p1("A.12.1.1", "12.1.1", 31)]}
    secs = A.adapt(p1, "DOC", "x.pdf")["sections"]
    lv = {s["id"]: s["level"] for s in secs}
    assert lv == {"A.2": 1, "A.5.1": 2, "A.12.1.1": 3}


def test_parent_nearest_existing_ancestor():
    # A.12.1 exists -> parent of A.12.1.1; A.5 absent -> A.5.1 parent None
    p1 = {"sections": [_p1("A.5.1", "5.1", 7), _p1("A.5.2", "5.2", 9),
                       _p1("A.12.1", "12.1", 30), _p1("A.12.1.1", "12.1.1", 31)]}
    secs = {s["id"]: s for s in A.adapt(p1, "DOC", "x.pdf")["sections"]}
    assert secs["A.5.1"]["parent_id"] is None       # A.5 missing -> None
    assert secs["A.5.2"]["parent_id"] is None
    assert secs["A.12.1.1"]["parent_id"] == "A.12.1"  # exists -> attach


def test_every_parent_resolves_or_none():
    """The self-FK invariant: parent_id is always None or an existing id."""
    p1 = {"sections": [_p1("A.2", "2", 5), _p1("A.6.3", "6.3", 17, 18),
                       _p1("A.6.2", "6.2", 17), _p1("B.1.1", "1.1", 40)]}
    secs = A.adapt(p1, "DOC", "x.pdf")["sections"]
    ids = {s["id"] for s in secs}
    for s in secs:
        assert s["parent_id"] is None or s["parent_id"] in ids


def test_doc_family_is_pillar3():
    out = A.adapt({"sections": [_p1("A.1", "1", 1)]}, "DOC", "x.pdf")
    assert out["document"]["doc_family"] == "pillar3"
    assert out["document"]["doc_id"] == "DOC"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
