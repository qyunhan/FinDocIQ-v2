"""Unit tests for the batch round/chunk-splitting planner — NO API calls.

Dry-verifies that stage1_extract.chunk.batch.plan_unit_requests splits units into batch
requests identically to how extract_unit_chunked splits spanning units into
chunks, so the dependency-round submission carries the right pages/unit_ids.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import stage1_extract.chunk.batch as batch


def _unit(uid, pages, typ):
    return {"unit_id": uid, "pages": pages, "type": typ,
            "leaves": [{"section_id": "x", "number": "1", "title": "T"}]}


def test_single_unit_one_request():
    u = _unit("customer_deposits_p24", [24], "single")
    reqs = batch.plan_unit_requests(u, chunk_size=2, force_image=False)
    assert len(reqs) == 1
    assert reqs[0]["chunk_unit"] is u
    assert reqs[0]["chunk_unit"]["pages"] == [24]
    assert reqs[0]["kind"] == "first"


def test_spanning_splits_into_chunks():
    u = _unit("foo_p10-12", [10, 11, 12], "spanning")
    reqs = batch.plan_unit_requests(u, chunk_size=2, force_image=False)
    assert len(reqs) == 2
    assert [r["chunk_unit"]["pages"] for r in reqs] == [[10, 11], [12]]
    assert [r["chunk_unit"]["unit_id"] for r in reqs] == [
        "foo_p10-12/chunks/c1", "foo_p10-12/chunks/c2"]
    # ci ordering is monotonic so merge order is deterministic
    assert [r["ci"] for r in reqs] == sorted(r["ci"] for r in reqs)


def test_spanning_within_chunk_size_not_split():
    u = _unit("foo_p10-11", [10, 11], "spanning")
    reqs = batch.plan_unit_requests(u, chunk_size=2, force_image=False)
    assert len(reqs) == 1
    assert reqs[0]["chunk_unit"]["pages"] == [10, 11]


def test_chunking_disabled_single_request():
    u = _unit("foo_p10-15", [10, 11, 12, 13, 14, 15], "spanning")
    reqs = batch.plan_unit_requests(u, chunk_size=0, force_image=False)
    assert len(reqs) == 1
    assert reqs[0]["chunk_unit"]["pages"] == [10, 11, 12, 13, 14, 15]


def test_force_image_propagates():
    u = _unit("foo_p10-12", [10, 11, 12], "spanning")
    reqs = batch.plan_unit_requests(u, chunk_size=2, force_image=True)
    assert all(r["attach_image"] is True for r in reqs)


def test_chunk_boundaries_match_sync_splitting():
    # extract_unit_chunked uses: [pages[i:i+cs] for i in range(0,len,cs)]
    pages = list(range(20, 27))  # 20..26, 7 pages
    for cs in (1, 2, 3, 4):
        u = _unit("s_p20-26", pages, "spanning")
        reqs = batch.plan_unit_requests(u, chunk_size=cs, force_image=False)
        expected = [pages[i:i + cs] for i in range(0, len(pages), cs)]
        assert [r["chunk_unit"]["pages"] for r in reqs] == expected


# ---------------------------------------------------------------------------
# Plain-script runner (repo convention: no pytest; exit 0 all-pass / 1 fail)
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import importlib
    globals()["batch"] = importlib.import_module("stage1_extract.chunk.batch")
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as exc:
                failed += 1
                print(f"[FAIL] {name}: {exc}")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
