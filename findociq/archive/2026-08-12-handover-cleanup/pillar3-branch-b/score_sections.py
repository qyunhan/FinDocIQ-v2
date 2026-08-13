"""score_sections.py — Stage 5 of section->table tagging (see
findociq/docs/specs/2026-07-09-section-table-tagging-design.md).

Scores a section_manifest.csv (see section_manifest.py) against a
hand-labeled ground-truth CSV. GT is SECTION-ROLLUP (one row per section
with a table COUNT), not per-table:

  section_id,section_title,first_page,last_page,n_pages,n_tables,confidence,note

GT is hierarchical: a parent row (e.g. "5") and its children ("5.1","5.2")
where the parent's n_tables = sum of its descendants' n_tables. We score
primarily at LEAF sections — a section_id that is not a strict dotted-prefix
of any other section_id in the SAME GT file — and verify parent rollups only
as a secondary consistency check (parents are not double-scored into the
headline number).

Rows with GT confidence == "review" (uncertain hand-labels) are scored but
kept OUT of the headline numbers, in their own bucket, so a handful of
"review" ambiguity doesn't drown the signal on "high"-confidence rows.

Usage:
  python3 score_sections.py <section_manifest.csv> <gt.csv>
Writes <manifest_dir>/section_score.json and prints a scorecard.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _our_rollup(manifest_rows: list[dict]) -> dict[str, dict]:
    """GROUP BY section_id -> (min page, max page, n_tables = row count, title).
    Rows with a blank section_id (unattributed regions) are excluded — they
    cannot be credited to any GT section."""
    our: dict[str, dict] = {}
    for r in manifest_rows:
        sid = (r.get("section_id") or "").strip()
        if not sid:
            continue
        pg = int(r["page"])
        d = our.setdefault(sid, {"min_page": pg, "max_page": pg, "n_tables": 0,
                                 "title": (r.get("section_title") or "").strip(),
                                 "section_id": sid})
        d["min_page"] = min(d["min_page"], pg)
        d["max_page"] = max(d["max_page"], pg)
        d["n_tables"] += 1
    return our


def _norm_title(s: str) -> str:
    s = re.sub(r"\(continued\)", "", (s or "").casefold())
    s = re.sub(r"^\s*(fs)?\d+(\.\d+)*\.?\s*", "", s.strip())   # strip leading numbering
    return re.sub(r"[\s\W]+", "", s)


_NUMBERED_GT_ID = re.compile(r"^[A-Za-z]?\.?\d")


def _id_prefix_aggregate(gt_id: str, our: dict) -> dict | None:
    """Aggregate ALL our sections whose section_id == gt_id or starts with
    gt_id + '.' into one virtual rollup (n_tables summed, page span =
    min/max). Only meaningful for numbered GT ids (see _NUMBERED_GT_ID); the
    prefix match is dot-bounded so GT '5.1' never absorbs our '5.2' (only
    '5.1' itself or '5.1.x...'). Returns None if nothing matches."""
    prefix = gt_id + "."
    members = [o for sid, o in our.items() if sid == gt_id or sid.startswith(prefix)]
    if not members:
        return None
    n_tables = sum(m["n_tables"] for m in members)
    min_page = min(m["min_page"] for m in members)
    max_page = max(m["max_page"] for m in members)
    title = next((m["title"] for m in members if m["section_id"] == gt_id), members[0]["title"])
    return dict(section_id=gt_id, title=title, min_page=min_page, max_page=max_page,
                n_tables=n_tables, n_our_sections_aggregated=len(members))


def _resolve(gt_title: str, gt_first: int, gt_last: int, our: dict) -> dict | None:
    """Match a GT section to OUR section by MEANING, not by the arbitrary
    section_id string: the no-TOC (Gemini) branch derives its own ids
    (income_statements) that can never equal a hand id (FS1), yet the titles
    AGREE (near-identical text). Match on TITLE similarity only; page overlap is
    only a tiebreak among title-candidates, NEVER an accept shortcut — letting a
    weak title pass on page overlap falsely credits a neighbouring section's
    tables (e.g. GT 7.2 'Main Sources of Differences...' vs our 7.1 'Differences
    between...' share words and a page). Accept iff title sim >= 0.85."""
    gtn = _norm_title(gt_title)
    best, best_sc, best_sim = None, 0.0, 0.0
    for o in our.values():
        sim = difflib.SequenceMatcher(None, gtn, _norm_title(o["title"])).ratio()
        sc = sim + (0.05 if _page_overlap(gt_first, gt_last,
                                          o["min_page"], o["max_page"]) else 0.0)
        if sc > best_sc:
            best, best_sc, best_sim = o, sc, sim
    return best if best and best_sim >= 0.85 else None


def _is_leaf(sid: str, all_ids: list[str]) -> bool:
    """sid is a leaf iff no OTHER gt section_id is sid + '.' + <anything>
    (a strict dotted-prefix child)."""
    prefix = sid + "."
    return not any(other != sid and other.startswith(prefix) for other in all_ids)


def _page_overlap(gt_first: int, gt_last: int, our_min: int, our_max: int) -> bool:
    return not (our_max < gt_first or our_min > gt_last)


def _score_group(ids, gt_by_id, our):
    """Score one bucket of GT section_ids (headline or review) against our
    rollup. Sections with GT n_tables == 0 (prose-only) are excluded — there
    is nothing to detect."""
    found, missing, matches, mismatches = [], [], [], []
    total_gt_tables = total_our_tables = 0
    for sid in ids:
        g = gt_by_id[sid]
        gt_n = int(g["n_tables"])
        if gt_n == 0:
            continue
        total_gt_tables += gt_n
        gt_pages = [int(g["first_page"]), int(g["last_page"])]

        matched_by = None
        n_aggregated = 0
        o = None
        if _NUMBERED_GT_ID.match(sid):
            # numbered ids are DECISIVE (user rule): id/prefix match or nothing.
            # A title fallback here silently credits a near-identical SIBLING
            # (GT 19.4 resolving onto our 19.3 at 0.92 similarity) and hides a
            # genuine miss — numbered ids never fall back to title matching.
            agg = _id_prefix_aggregate(sid, our)
            if agg is not None and agg["n_tables"] >= 1:
                o = agg
                matched_by = "id_prefix"
                n_aggregated = agg["n_our_sections_aggregated"]
        else:
            o = _resolve(g["section_title"], gt_pages[0], gt_pages[1], our)
            if o is not None:
                matched_by = "title"
                n_aggregated = 1

        if o is None:
            missing.append(sid)
            mismatches.append(dict(section_id=sid, gt_n_tables=gt_n, our_n_tables=0,
                                    gt_pages=gt_pages, our_pages=None, reason="missing"))
            continue
        found.append(sid)
        total_our_tables += o["n_tables"]
        our_pages = [o["min_page"], o["max_page"]]
        rec = dict(section_id=sid, gt_n_tables=gt_n, our_n_tables=o["n_tables"],
                    gt_pages=gt_pages, our_pages=our_pages,
                    page_overlap=_page_overlap(gt_pages[0], gt_pages[1], our_pages[0], our_pages[1]),
                    matched_by=matched_by, n_our_sections_aggregated=n_aggregated)
        if o["n_tables"] == gt_n:
            matches.append(rec)
        else:
            rec["reason"] = "n_tables_mismatch"
            mismatches.append(rec)
    return dict(found=found, missing=missing, matches=matches, mismatches=mismatches,
                total_gt_tables=total_gt_tables, total_our_tables=total_our_tables)


def score(manifest_csv: str, gt_csv: str) -> dict:
    manifest_rows = _read_csv(manifest_csv)
    gt_rows = _read_csv(gt_csv)
    gt_by_id = {g["section_id"].strip(): g for g in gt_rows}
    all_gt_ids = list(gt_by_id.keys())

    our = _our_rollup(manifest_rows)

    leaf_ids = [sid for sid in all_gt_ids if _is_leaf(sid, all_gt_ids)]
    parent_ids = [sid for sid in all_gt_ids if sid not in leaf_ids]

    review_leaf = [sid for sid in leaf_ids
                    if gt_by_id[sid].get("confidence", "").strip().lower() == "review"]
    headline_leaf = [sid for sid in leaf_ids if sid not in review_leaf]

    headline = _score_group(headline_leaf, gt_by_id, our)
    review = _score_group(review_leaf, gt_by_id, our)

    sections_extra = sorted(set(our.keys()) - set(all_gt_ids))

    # Secondary consistency check: parent rollup = sum of its leaf descendants
    # (our-side), compared to the parent's own GT n_tables.
    parent_checks = []
    for pid in parent_ids:
        prefix = pid + "."
        desc = [sid for sid in leaf_ids if sid == pid or sid.startswith(prefix)]
        our_sum = sum(our.get(sid, {}).get("n_tables", 0) for sid in desc)
        gt_n = int(gt_by_id[pid]["n_tables"])
        parent_checks.append(dict(section_id=pid, gt_n_tables=gt_n,
                                   our_children_sum=our_sum, match=our_sum == gt_n))

    n_found, n_missing = len(headline["found"]), len(headline["missing"])
    n_checked = n_found + n_missing
    section_match_rate = round(n_found / n_checked, 4) if n_checked else None
    n_table_match = len(headline["matches"])
    table_match_rate = round(n_table_match / n_found, 4) if n_found else None

    summary = dict(
        manifest_csv=manifest_csv, gt_csv=gt_csv,
        total_gt_leaf_tables=headline["total_gt_tables"],
        total_detected_tables=headline["total_our_tables"],
        sections_checked=n_checked,
        sections_found=n_found,
        sections_missing=headline["missing"],
        sections_extra=sections_extra,
        section_match_rate=section_match_rate,
        n_tables_match_count=n_table_match,
        n_tables_checked=n_found,
        n_tables_match_rate=table_match_rate,
        matches=headline["matches"],
        mismatches=headline["mismatches"],
        review=dict(
            n_leaf_sections=len(review_leaf),
            total_gt_tables=review["total_gt_tables"],
            total_detected_tables=review["total_our_tables"],
            found=review["found"], missing=review["missing"],
            mismatches=review["mismatches"],
        ),
        parent_rollup_checks=parent_checks,
    )

    _print_scorecard(summary)

    out_path = os.path.join(os.path.dirname(os.path.abspath(manifest_csv)), "section_score.json")
    with open(out_path, "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nwrote {out_path}")
    return summary


def _print_scorecard(s: dict) -> None:
    print("=== section_score ===")
    print(f"GT leaf tables (high-confidence): {s['total_gt_leaf_tables']}  "
          f"detected: {s['total_detected_tables']}")
    print(f"sections checked: {s['sections_checked']}  found: {s['sections_found']}  "
          f"missing: {len(s['sections_missing'])}  "
          f"section_match_rate: {s['section_match_rate']}")
    print(f"n_tables match: {s['n_tables_match_count']}/{s['n_tables_checked']}  "
          f"n_tables_match_rate: {s['n_tables_match_rate']}")
    print(f"sections_extra (ours, not in GT): {s['sections_extra'] or '(none)'}")
    if s["mismatches"]:
        print("\n-- mismatches (section_id, gt_n_tables, our_n_tables, gt_pages, our_pages) --")
        for m in s["mismatches"]:
            print(f"  {m['section_id']:>8}  gt={m['gt_n_tables']}  our={m['our_n_tables']}  "
                  f"gt_pages={m['gt_pages']}  our_pages={m['our_pages']}  ({m['reason']})")
    else:
        print("\n-- no mismatches --")
    r = s["review"]
    print(f"\n-- review-flagged leaf sections (excluded from headline): {r['n_leaf_sections']} --")
    print(f"   gt_tables={r['total_gt_tables']}  detected={r['total_detected_tables']}  "
          f"missing={r['missing'] or '(none)'}")
    if r["mismatches"]:
        for m in r["mismatches"]:
            print(f"   {m['section_id']:>8}  gt={m['gt_n_tables']}  our={m['our_n_tables']}  "
                  f"({m['reason']})")
    bad_parents = [p for p in s["parent_rollup_checks"] if not p["match"]]
    print(f"\n-- parent rollup consistency: {len(s['parent_rollup_checks']) - len(bad_parents)}"
          f"/{len(s['parent_rollup_checks'])} match --")
    for p in bad_parents:
        print(f"   {p['section_id']:>8}  gt={p['gt_n_tables']}  children_sum={p['our_children_sum']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest_csv")
    ap.add_argument("gt_csv")
    args = ap.parse_args()
    score(args.manifest_csv, args.gt_csv)


if __name__ == "__main__":
    main()
