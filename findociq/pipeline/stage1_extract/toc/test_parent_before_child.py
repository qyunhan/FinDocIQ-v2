"""Generality tests for toc_stage._parent_before_child().

Locks the 2026-07-16 pivot: `seq` is parent-before-child topological order.
Anchor (page, y) order can place a section before its mis-anchored parent;
this reorder restores parent-before-child WITHOUT reparenting, preserving
reading order otherwise. See docs/specs/2026-07-13-fs-branch-pipeline.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.toc import toc_stage as T  # noqa: E402


def _ids(secs):
    return [s["id"] for s in secs]


def _S(i, p=None):
    return {"id": i, "parent_id": p}


def _invariant_holds(out):
    pos = {s["id"]: i for i, s in enumerate(out)}
    return all(s["parent_id"] is None
               or s["parent_id"] not in pos          # dangling => root-like
               or pos[s["parent_id"]] < pos[s["id"]]
               for s in out)


def test_normal_order_is_identity():
    """Inversion-free doc: reading order already parent-before-child. Must be
    returned unchanged — no regrouping of levels."""
    secs = [_S("L1a"), _S("L2a1", "L1a"), _S("L2a2", "L1a"),
            _S("L1b"), _S("L2b1", "L1b")]
    out = T._parent_before_child(secs)
    assert _ids(out) == ["L1a", "L2a1", "L2a2", "L1b", "L2b1"]
    assert _invariant_holds(out)


def test_dbs_shape_child_before_parent():
    """DBS 4Q25: `dividends` anchored before its parent `financial_results`."""
    secs = [_S("dividends", "fin"), _S("fin"), _S("overview")]
    out = T._parent_before_child(secs)
    assert _ids(out) == ["fin", "dividends", "overview"]
    assert _invariant_holds(out)


def test_ocbc_shape_sibling_children_around_parent():
    """OCBC 4Q25: one child precedes the parent, another follows it."""
    secs = [_S("qoq", "q4"), _S("q4"), _S("npas", "aqa"),
            _S("aqa"), _S("allow", "aqa")]
    out = T._parent_before_child(secs)
    assert _ids(out) == ["q4", "qoq", "aqa", "npas", "allow"]
    assert _invariant_holds(out)


def test_multiple_children_precede_parent():
    """Parent lifted before the EARLIEST child; children keep their order."""
    secs = [_S("c1", "p"), _S("c2", "p"), _S("p"), _S("z")]
    out = T._parent_before_child(secs)
    assert _ids(out) == ["p", "c1", "c2", "z"]
    assert _invariant_holds(out)


def test_deep_chain_fully_reversed():
    """grandchild <- child <- root, all inverted."""
    secs = [_S("gc", "c"), _S("c", "r"), _S("r")]
    out = T._parent_before_child(secs)
    assert _ids(out) == ["r", "c", "gc"]
    assert _invariant_holds(out)


def test_dangling_parent_treated_as_root():
    """A parent_id with no matching section (validated away elsewhere) must not
    crash the reorder."""
    secs = [_S("x", "ghost"), _S("y")]
    out = T._parent_before_child(secs)
    assert _ids(out) == ["x", "y"]


def test_cycle_fails_loud():
    """A parent cycle is unrepresentable data — fail loud, never loop."""
    secs = [_S("a", "b"), _S("b", "a")]
    try:
        T._parent_before_child(secs)
    except SystemExit:
        return
    raise AssertionError("cycle did not fail loud")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
