"""mapping.backfill_map — STEP 4 of docs/specs/MAPPING_LAYER.md §4.

Seeds `bank_line_map` from what the corpus ALREADY knows, at
`map_status='ai_proposed'`. Nothing is promoted to `human_confirmed` here —
an `ai_proposed` row never loads a value (MAPPING_LAYER §2.4).

Source of each field:
  anchor        (bank, table_type_id, row_label_norm, parent_label_norm)
                bank        <- document.institution
                table_type_id <- table_t.table_type_id (registry, step 3)
                row_label_norm   <- normalize_row_label(row_leaf_label)
                parent_label_norm<- row_lineage.lvl{depth-1}, '' at depth 1
  concept_key   <- the concept the corpus stamps on that anchor, but ONLY when
                   every stamped occurrence agrees. Anchors whose occurrences
                   disagree are inserted with concept_key NULL and the rival
                   keys recorded in `note` — they are review items, not guesses.
  period_type   <- concept dictionary `nature` (flow/ratio_flow -> duration,
                   stock/ratio_point -> instant)
  is_abstract   <- 1 when EVERY occurrence of the anchor has zero cell_fact rows
                   (a structural header). This is what makes "header" separable
                   from "unmapped", which today are both just a NULL concept.
  balance       <- NULL. `balance` is a spec'd addition to the concept
                   dictionary that has not been authored yet; leaving it NULL is
                   honest, and the load gate treats NULL as "unknown", not
                   "credit".

Idempotent + MERGE-safe: an anchor already present as human_confirmed /
human_corrected is left untouched and counted separately.

    python3 findociq/pipeline/mapping/backfill_map.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mapping.normalize import normalize_row_label, safe_clean  # noqa: E402
from mapping.registry import bank_of              # noqa: E402

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_DICT = _HERE.parent / "concept" / "concept_dictionary.yaml"

_NATURE_TO_PERIOD_TYPE = {
    "flow": "duration", "ratio_flow": "duration",
    "stock": "instant", "ratio_point": "instant",
}

PROTECTED = ("human_confirmed", "human_corrected")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _period_type_by_concept() -> dict[str, str]:
    doc = yaml.safe_load(_DICT.read_text())
    return {c["key"]: _NATURE_TO_PERIOD_TYPE.get(c.get("nature"), None)
            for c in doc.get("concepts", [])}


def _parent_of(lvls: tuple, depth: int) -> str:
    """row_lineage stores lvl1..lvl5 with the LEAF at lvl{depth}; the parent is
    lvl{depth-1}. Depth 1 is top-level and has no parent."""
    if not depth or depth < 2:
        return ""
    return lvls[depth - 2] or ""


def collect(con: sqlite3.Connection) -> tuple[dict, dict]:
    """-> (anchors, stats). anchors[(bank, ttid, label, parent)] = accumulator."""
    rows = con.execute("""
        SELECT d.institution, t.table_type_id,
               rd.row_leaf_label, rd.row_leaf_label_clean, rd.concept_key,
               rd.segment_key, rd.geo_key, rd.industry_key,
               rl.lvl1, rl.lvl2, rl.lvl3, rl.lvl4, rl.lvl5, rl.depth,
               (SELECT COUNT(*) FROM cell_fact cf
                 WHERE cf.doc_id=rd.doc_id AND cf.table_id=rd.table_id
                   AND cf.row_id=rd.row_id) AS n_cells
        FROM row_dim rd
        JOIN table_t  t ON t.doc_id=rd.doc_id AND t.table_id=rd.table_id
        JOIN document d ON d.doc_id=rd.doc_id
        JOIN row_lineage rl ON rl.row_lineage_id = rd.row_lineage_id
        WHERE t.table_type_id IS NOT NULL
    """).fetchall()

    anchors: dict = defaultdict(lambda: {
        "concepts": defaultdict(int), "n_rows": 0, "n_stamped": 0,
        "all_zero_cells": True, "segment": set(), "geo": set(), "industry": set()})
    stats = {"rows_in_classified_tables": len(rows)}

    for (inst, ttid, label, label_clean, ck, seg, geo, ind,
         l1, l2, l3, l4, l5, depth, n_cells) in rows:
        leaf = safe_clean(label, label_clean)
        key = (bank_of(inst), ttid, normalize_row_label(leaf),
               normalize_row_label(_parent_of((l1, l2, l3, l4, l5), depth)))
        a = anchors[key]
        a["n_rows"] += 1
        if n_cells:
            a["all_zero_cells"] = False
        if ck:
            a["concepts"][ck] += 1
            a["n_stamped"] += 1
        if seg:
            a["segment"].add(seg)
        if geo:
            a["geo"].add(geo)
        if ind:
            a["industry"].add(ind)
    return anchors, stats


def backfill(con: sqlite3.Connection) -> dict:
    anchors, stats = collect(con)
    ptype = _period_type_by_concept()
    now = _now()

    s = dict(stats, anchors=len(anchors), proposed_with_concept=0,
             proposed_conflict=0, proposed_abstract=0, proposed_no_concept=0,
             protected_skipped=0, inserted=0, updated=0)

    for (bank, ttid, label, parent), a in sorted(anchors.items()):
        existing = con.execute(
            "SELECT map_id, map_status FROM bank_line_map "
            "WHERE bank=? AND table_type_id=? AND row_label_norm=? AND parent_label_norm=?",
            (bank, ttid, label, parent)).fetchone()
        if existing and existing[1] in PROTECTED:
            s["protected_skipped"] += 1
            continue

        concepts = a["concepts"]
        note = None
        concept = None
        if len(concepts) == 1:
            concept = next(iter(concepts))
            s["proposed_with_concept"] += 1
        elif len(concepts) > 1:
            rivals = ", ".join(f"{k}({v})" for k, v in
                               sorted(concepts.items(), key=lambda x: -x[1]))
            note = f"CONFLICT: corpus stamps {len(concepts)} concepts on this anchor: {rivals}"
            s["proposed_conflict"] += 1
        elif a["all_zero_cells"]:
            note = "structural header: every occurrence has zero cells"
            s["proposed_abstract"] += 1
        else:
            note = "no concept stamped in corpus"
            s["proposed_no_concept"] += 1

        is_abstract = 1 if (concept is None and a["all_zero_cells"]) else 0
        # a single-member axis observed on EVERY stamped occurrence is a safe
        # proposal; a mixed set is not, and is left NULL for review.
        one = lambda st: (next(iter(st)) if len(st) == 1 else None)  # noqa: E731
        confidence = (a["n_stamped"] / a["n_rows"]) if a["n_rows"] and concept else None

        con.execute("""
            INSERT INTO bank_line_map (bank, table_type_id, row_label_norm, parent_label_norm,
                concept_key, legal_entity, segment_key, geo_key, industry_key,
                period_type, balance, is_abstract, negated_label,
                map_status, mapped_by, confidence, mapped_at, note)
            VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,0, 'ai_proposed','backfill:corpus',?,?,?)
            ON CONFLICT(bank, table_type_id, row_label_norm, parent_label_norm)
            DO UPDATE SET concept_key=excluded.concept_key,
                          segment_key=excluded.segment_key, geo_key=excluded.geo_key,
                          industry_key=excluded.industry_key,
                          period_type=excluded.period_type, is_abstract=excluded.is_abstract,
                          confidence=excluded.confidence, mapped_at=excluded.mapped_at,
                          note=excluded.note
        """, (bank, ttid, label, parent, concept, "CONSOLIDATED",
              one(a["segment"]), one(a["geo"]), one(a["industry"]),
              ptype.get(concept) if concept else None, None, is_abstract,
              confidence, now, note))
        if existing:
            s["updated"] += 1
        else:
            s["inserted"] += 1

    con.commit()
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    s = backfill(con)
    print("bank_line_map backfill (all rows at map_status='ai_proposed')\n")
    print(f"  row_dim rows in classified tables : {s['rows_in_classified_tables']:,}")
    print(f"  distinct anchors                  : {s['anchors']:,}")
    print(f"    inserted / updated              : {s['inserted']:,} / {s['updated']:,}")
    print(f"    protected (human_*) skipped     : {s['protected_skipped']:,}")
    print()
    print(f"  proposed WITH a concept           : {s['proposed_with_concept']:,}")
    print(f"  CONFLICT (rival concepts)         : {s['proposed_conflict']:,}")
    print(f"  structural headers (is_abstract)  : {s['proposed_abstract']:,}")
    print(f"  no concept in corpus              : {s['proposed_no_concept']:,}")
    con.close()


if __name__ == "__main__":
    main()
