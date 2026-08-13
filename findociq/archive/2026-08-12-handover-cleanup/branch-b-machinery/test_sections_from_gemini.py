"""Plain check()-style test for sections_from_gemini.py (no pytest). Injects a
MOCK llm (prompt -> canned JSON string) over fixture candidates.csv/regions.csv
written to a temp out_root, so no real Gemini API call is made.

NOTE: assign_tables.py (the shared deterministic table assigner, pinned
interface `assign(boundaries, regions) -> [{page,table_idx,section_id,
section_title}]`, built in parallel per findociq/docs/specs/2026-07-09-
section-table-tagging-design.md AMENDMENT 2026-07-09 PM) did not exist yet at
the time this test was written. Per instructions, this test ships a LOCAL stub
matching that interface (reading-order sweep: cursor = section of the last
boundary crossed, `continued` non-downgrade rule for ancestor boundaries,
PREAMBLE before any boundary) so this test can run standalone. The stub lives
ONLY in this test file, injected via sys.modules before importing
sections_from_gemini — the pipeline module itself imports the real
`assign_tables` at module level (`from assign_tables import assign`) and must
not ship a stub. Once assign_tables.py lands, this stub should be deleted and
the real module used instead (the test only needs sys.modules injection
removed).

Validates:
  (a) happy path: mock returns sections with candidate_idxs -> boundaries carry
      correct page/y0 from fixture candidates; section_tags.csv has one row per
      region; a region under a level-2 heading gets the level-2 (leaf) id.
  (b) idx out of range -> SectionArrangeError.
  (c) idx claimed twice (by two sections) -> SectionArrangeError.
  (d) a "(continued)" second instance of a section yields continued=True.

Usage:
  python3 findociq/pipeline/discover/section/test_sections_from_gemini.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# --- inject a local stub for assign_tables (see module docstring) BEFORE
# importing sections_from_gemini, which does `from assign_tables import assign`
# at module level. ---


def _stub_assign(boundaries: list[dict], regions: list[dict]) -> list[dict]:
    """Minimal stand-in for the pinned assign_tables.assign interface: reading-
    order sweep (page asc, y asc; boundary before region at equal y). Cursor =
    section of the last boundary crossed -> each region gets the cursor's
    section. A `continued` boundary whose section is an ANCESTOR (id is a
    dotted-prefix of) the cursor's current section does not downgrade the
    cursor. Regions before any boundary -> PREAMBLE."""

    def is_ancestor(anc_id: str, other_id: str) -> bool:
        if anc_id == other_id:
            return False
        return other_id.startswith(anc_id + ".")

    events = []
    for b in boundaries:
        events.append((b["page"], b["y0"], 0, ("boundary", b)))
    for i, r in enumerate(regions):
        events.append((int(r["page"]), float(r["y0"]), 1, ("region", i)))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    cur_id, cur_title = None, None
    out = [None] * len(regions)
    for _page, _y0, _rank, payload in events:
        if payload[0] == "boundary":
            b = payload[1]
            if b["continued"] and cur_id is not None and is_ancestor(b["section_id"], cur_id):
                continue
            cur_id, cur_title = b["section_id"], b["section_title"]
        else:
            idx = payload[1]
            r = regions[idx]
            if cur_id is None:
                sid, title = "PREAMBLE", "PREAMBLE"
            else:
                sid, title = cur_id, cur_title
            out[idx] = dict(page=int(r["page"]), table_idx=int(r["table_idx"]),
                             section_id=sid, section_title=title)
    return out


_stub_module = types.ModuleType("assign_tables")
_stub_module.assign = _stub_assign
sys.modules["assign_tables"] = _stub_module

import sections_from_gemini  # noqa: E402  (module reference, for monkeypatching _retry_wait)
from sections_from_gemini import (  # noqa: E402
    GeminiTransportError,
    SectionArrangeError,
    attribute_from_gemini,
    boundaries_from_response,
)

_PASS = _FAIL = 0


def check(label, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {label}")
    else:
        _FAIL += 1
        print(f"  FAIL {label}")


# idx: 0=Capital Adequacy(sec 6), 1=date line(ignored), 2=6.1 subheading,
# 3=6.1 (continued) repeat, 4=Liquidity Coverage Ratio(sec 9, level1)
CAND_ROWS = [
    dict(page=1, y0=100, x0=50, text="6 Capital Adequacy", font_size=14, bold=True,
         alignment="left", is_dateish=False),
    dict(page=1, y0=120, x0=50, text="31 Dec 2024", font_size=12, bold=True,
         alignment="left", is_dateish=True),
    dict(page=1, y0=150, x0=50, text="6.1 Risk-Weighted Assets", font_size=12, bold=True,
         alignment="left", is_dateish=False),
    dict(page=2, y0=50, x0=50, text="6.1 Risk-Weighted Assets (continued)", font_size=12,
         bold=True, alignment="left", is_dateish=False),
    dict(page=3, y0=90, x0=50, text="9 Liquidity Coverage Ratio", font_size=14, bold=True,
         alignment="left", is_dateish=False),
]

REGION_ROWS = [
    dict(page=1, table_idx=0, x0=10, y0=200, x1=500, y1=400),   # under 6.1 (leaf, not 6)
    dict(page=2, table_idx=0, x0=10, y0=150, x1=500, y1=350),   # under 6.1 (continued)
    dict(page=3, table_idx=0, x0=10, y0=150, x1=500, y1=350),   # under 9
]

GOOD_RESPONSE = json.dumps({
    "sections": [
        {"id": "6", "title": "Capital Adequacy", "level": 1, "parent_id": None,
         "candidate_idxs": [0]},
        {"id": "6.1", "title": "Risk-Weighted Assets", "level": 2, "parent_id": "6",
         "candidate_idxs": [2, 3]},
        {"id": "9", "title": "Liquidity Coverage Ratio", "level": 1, "parent_id": None,
         "candidate_idxs": [4]},
    ],
})


def _write_fixture(out_root: str, tag: str) -> None:
    out_dir = os.path.join(out_root, tag)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "candidates.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "page", "y0", "x0", "text", "font_size", "bold", "alignment", "is_dateish"])
        w.writeheader()
        w.writerows(CAND_ROWS)
    with open(os.path.join(out_dir, "regions.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["page", "table_idx", "x0", "y0", "x1", "y1"])
        w.writeheader()
        w.writerows(REGION_ROWS)


def test_happy_path():
    print("-- attribute_from_gemini: happy path, leaf-level assignment + continued flag")
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp, "doc_a")
        captured_prompt = {}

        def mock_llm(prompt: str) -> str:
            captured_prompt["prompt"] = prompt
            return GOOD_RESPONSE

        path = attribute_from_gemini("doc_a", out_root=tmp, llm=mock_llm)
        check("returns section_tags.csv path", path.endswith("section_tags.csv"))
        check("prompt has no TABLES: block (headings only)",
              "TABLES:" not in captured_prompt["prompt"])
        check("prompt includes CANDIDATES: block", "CANDIDATES:" in captured_prompt["prompt"])
        check("prompt indexes candidates with '0 |' prefix",
              "0 | 1 |" in captured_prompt["prompt"])

        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        check("section_tags.csv has exactly one row per region", len(rows) == len(REGION_ROWS))
        check("every row has source=='gemini'", all(r["source"] == "gemini" for r in rows))

        by_key = {(r["page"], r["table_idx"]): r for r in rows}
        r1 = by_key[("1", "0")]
        check("p1/t0 -> leaf section 6.1 (not parent 6)", r1["section_id"] == "6.1")
        r2 = by_key[("2", "0")]
        check("p2/t0 (after the continued repeat) -> still 6.1",
              r2["section_id"] == "6.1")
        r3 = by_key[("3", "0")]
        check("p3/t0 -> section 9", r3["section_id"] == "9")

        raw_path = os.path.join(tmp, "doc_a", "gemini_sections_raw.txt")
        json_path = os.path.join(tmp, "doc_a", "gemini_sections.json")
        check("raw response persisted", os.path.exists(raw_path))
        check("parsed json persisted", os.path.exists(json_path))


def test_boundaries_from_response_positions():
    print("-- boundaries_from_response: page/y0 carried from fixture candidates")
    parsed = json.loads(GOOD_RESPONSE)
    boundaries = boundaries_from_response(parsed, CAND_ROWS)
    check("4 boundaries (one per referenced candidate_idx)", len(boundaries) == 4)
    b0 = next(b for b in boundaries if b["section_id"] == "6")
    check("section 6 boundary carries idx-0 page/y0",
          b0["page"] == 1 and b0["y0"] == 100)
    cont_boundaries = [b for b in boundaries if b["section_id"] == "6.1"]
    check("two 6.1 instances", len(cont_boundaries) == 2)
    first, second = sorted(cont_boundaries, key=lambda b: (b["page"], b["y0"]))
    check("first 6.1 instance is not continued", first["continued"] is False)
    check("second 6.1 instance IS continued (repeat instance)", second["continued"] is True)
    check("boundaries sorted in reading order",
          [(b["page"], b["y0"]) for b in boundaries] ==
          sorted((b["page"], b["y0"]) for b in boundaries))


def test_out_of_range_idx_raises():
    print("-- boundaries_from_response: candidate_idx out of range -> raises")
    parsed = {"sections": [
        {"id": "6", "title": "Capital Adequacy", "level": 1, "parent_id": None,
         "candidate_idxs": [99]},
    ]}
    try:
        boundaries_from_response(parsed, CAND_ROWS)
        check("raises SectionArrangeError on out-of-range idx", False)
    except SectionArrangeError:
        check("raises SectionArrangeError on out-of-range idx", True)


def test_idx_claimed_twice_raises():
    print("-- boundaries_from_response: candidate_idx claimed by two sections -> raises")
    parsed = {"sections": [
        {"id": "6", "title": "Capital Adequacy", "level": 1, "parent_id": None,
         "candidate_idxs": [0, 2]},
        {"id": "6.1", "title": "Risk-Weighted Assets", "level": 2, "parent_id": "6",
         "candidate_idxs": [2, 3]},
    ]}
    try:
        boundaries_from_response(parsed, CAND_ROWS)
        check("raises SectionArrangeError on idx claimed twice", False)
    except SectionArrangeError:
        check("raises SectionArrangeError on idx claimed twice", True)


def test_via_llm_out_of_range_raises():
    print("-- attribute_from_gemini: mock response with out-of-range idx -> raises end-to-end")
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp, "doc_d")
        bad = json.dumps({"sections": [
            {"id": "6", "title": "Capital Adequacy", "level": 1, "parent_id": None,
             "candidate_idxs": [999]},
        ]})

        def mock_llm(prompt: str) -> str:
            return bad

        try:
            attribute_from_gemini("doc_d", out_root=tmp, llm=mock_llm)
            check("raises SectionArrangeError end-to-end", False)
        except SectionArrangeError:
            check("raises SectionArrangeError end-to-end", True)


# --- adaptive/chunked transport fixtures -----------------------------------
#
# CAND_ROWS (5 candidates) chunked at chunk_size=2 splits into 3 chunks whose
# local (chunk-relative) indices are:
#   chunk0 = idx 0,1  -> "6 Capital Adequacy", "31 Dec 2024"
#   chunk1 = idx 2,3  -> "6.1 Risk-Weighted Assets", "... (continued)"
#   chunk2 = idx 4    -> "9 Liquidity Coverage Ratio"
# None of the real sections straddle a chunk boundary in this fixture, so each
# chunk's mock response uses LOCAL idxs that, once shifted by the chunk offset
# in _attribute_chunked, reproduce exactly GOOD_RESPONSE's global idxs.

CHUNK0_RESPONSE = json.dumps({"sections": [
    {"id": "6", "title": "Capital Adequacy", "level": 1, "parent_id": None,
     "candidate_idxs": [0]},
]})
CHUNK1_RESPONSE = json.dumps({"sections": [
    {"id": "6.1", "title": "Risk-Weighted Assets", "level": 2, "parent_id": "6",
     "candidate_idxs": [0, 1]},
]})
CHUNK2_RESPONSE = json.dumps({"sections": [
    {"id": "9", "title": "Liquidity Coverage Ratio", "level": 1, "parent_id": None,
     "candidate_idxs": [0]},
]})


def _chunk_response_for_prompt(prompt: str) -> str:
    if "Risk-Weighted" in prompt:
        return CHUNK1_RESPONSE
    if "Liquidity" in prompt:
        return CHUNK2_RESPONSE
    if "Capital Adequacy" in prompt:
        return CHUNK0_RESPONSE
    raise AssertionError(f"unrecognized chunk prompt: {prompt[:200]!r}")


def _read_tags(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_chunked_matches_oneshot():
    print("-- attribute_from_gemini: forced chunked mode matches one-shot output")
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp, "doc_oneshot")
        _write_fixture(tmp, "doc_chunked")

        def mock_oneshot(prompt: str) -> str:
            return GOOD_RESPONSE

        def mock_chunked(prompt: str) -> str:
            return _chunk_response_for_prompt(prompt)

        path_oneshot = attribute_from_gemini(
            "doc_oneshot", out_root=tmp, llm=mock_oneshot, chunk_size=0)
        path_chunked = attribute_from_gemini(
            "doc_chunked", out_root=tmp, llm=mock_chunked, chunk_size=2)

        rows_oneshot = _read_tags(path_oneshot)
        rows_chunked = _read_tags(path_chunked)
        check("chunked section_tags.csv matches one-shot section_tags.csv",
              rows_oneshot == rows_chunked)
        check("chunk cache dir was populated",
              len(os.listdir(os.path.join(tmp, "doc_chunked", "gemini_chunks"))) == 3)
        check("merged gemini_sections.json written for chunked path",
              os.path.exists(os.path.join(tmp, "doc_chunked", "gemini_sections.json")))


def test_chunk_cache_resume():
    print("-- attribute_from_gemini: pre-cached chunk 0 is never re-fetched")
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp, "doc_resume")
        out_dir = os.path.join(tmp, "doc_resume")
        cache_dir = os.path.join(out_dir, "gemini_chunks")
        os.makedirs(cache_dir, exist_ok=True)
        cached = json.loads(CHUNK0_RESPONSE)
        cached["_model"] = "pre-cached"
        with open(os.path.join(cache_dir, "00.json"), "w") as fh:
            json.dump(cached, fh)

        calls = []

        def mock_llm(prompt: str) -> str:
            calls.append(prompt)
            if "Capital Adequacy" in prompt:
                raise AssertionError("chunk 0 must be served from cache, not called")
            return _chunk_response_for_prompt(prompt)

        path = attribute_from_gemini("doc_resume", out_root=tmp, llm=mock_llm, chunk_size=2)
        check("chunk 0 never invoked the mock (2 calls, not 3)", len(calls) == 2)
        rows = _read_tags(path)
        check("resume run still tags 3 regions", len(rows) == 3)


def test_chunk_failure_raises_and_keeps_completed_caches():
    print("-- attribute_from_gemini: one chunk fails -> raises, others cached")
    orig_wait = sections_from_gemini._retry_wait
    sections_from_gemini._retry_wait = lambda attempt: 0  # speed up retries in test
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _write_fixture(tmp, "doc_fail")

            def mock_llm(prompt: str) -> str:
                if "Risk-Weighted" in prompt:
                    raise RuntimeError("simulated transport failure")
                return _chunk_response_for_prompt(prompt)

            try:
                attribute_from_gemini("doc_fail", out_root=tmp, llm=mock_llm, chunk_size=2)
                check("raises GeminiTransportError when a chunk exhausts retries", False)
            except GeminiTransportError:
                check("raises GeminiTransportError when a chunk exhausts retries", True)

            cache_dir = os.path.join(tmp, "doc_fail", "gemini_chunks")
            check("chunk 0 cache persisted", os.path.exists(os.path.join(cache_dir, "00.json")))
            check("chunk 2 cache persisted", os.path.exists(os.path.join(cache_dir, "02.json")))
            check("chunk 1 (failed) cache does NOT exist",
                  not os.path.exists(os.path.join(cache_dir, "01.json")))
    finally:
        sections_from_gemini._retry_wait = orig_wait


def test_adaptive_fallback_to_chunked():
    print("-- attribute_from_gemini: adaptive mode falls back to chunked on one-shot stall")
    with tempfile.TemporaryDirectory() as tmp:
        _write_fixture(tmp, "doc_adaptive")
        calls = {"n": 0}

        def mock_llm(prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                # simulate the stalled one-shot call: sleeps well past the tiny
                # oneshot_timeout below, so the daemon-thread join times out.
                time.sleep(0.5)
                return GOOD_RESPONSE
            # fallback chunk size (25) > 5 candidates -> a single chunk covering
            # everything, offset 0, so GOOD_RESPONSE's global idxs apply as-is.
            return GOOD_RESPONSE

        path = attribute_from_gemini(
            "doc_adaptive", out_root=tmp, llm=mock_llm, oneshot_timeout=0.2)
        check("adaptive fallback made exactly 2 calls (stalled one-shot + 1 chunk)",
              calls["n"] == 2)
        rows = _read_tags(path)
        check("adaptive fallback still tags 3 regions", len(rows) == 3)
        by_key = {(r["page"], r["table_idx"]): r for r in rows}
        check("adaptive fallback result matches happy path (p1/t0 -> 6.1)",
              by_key[("1", "0")]["section_id"] == "6.1")
        check("no gemini_sections_raw.txt (fallback took the chunked path)",
              not os.path.exists(os.path.join(tmp, "doc_adaptive", "gemini_sections_raw.txt")))
        check("merged gemini_sections.json written by the chunked fallback",
              os.path.exists(os.path.join(tmp, "doc_adaptive", "gemini_sections.json")))


def main():
    test_happy_path()
    test_boundaries_from_response_positions()
    test_out_of_range_idx_raises()
    test_idx_claimed_twice_raises()
    test_via_llm_out_of_range_raises()
    test_chunked_matches_oneshot()
    test_chunk_cache_resume()
    test_chunk_failure_raises_and_keeps_completed_caches()
    test_adaptive_fallback_to_chunked()
    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
