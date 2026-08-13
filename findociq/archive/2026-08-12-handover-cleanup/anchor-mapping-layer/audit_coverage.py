"""mapping.audit_coverage — key-field extractability audit.

Replays the 89 human_confirmed dashboard anchors against every (bank, quarter)
in the corpus and classifies each cell:

  HIT               anchor resolves structurally AND the value cell is clean
  MISS-STRUCTURE    a row with that label exists in the exhibit, but its parent
                    chain does not resolve to the anchor
  MISS-ABSENT       no row with that label in the exhibit at all
  CONTAMINATED      anchor resolves, but the value cell is unusable
  N/A-BY-DISCLOSURE the exhibit itself is not in this quarter's filing — a
                    disclosure-cadence fact, NOT an extraction failure

A HIT REQUIRES THE STRUCTURAL PATH TO RESOLVE. Value-matching is never used to
confirm one; it appears only in the defect log, to propose where a miss might
live. (DBS commercial-book NII 14,494 is within 0.05% of group NII 14,500 — no
tolerance can separate them, only the parent can.)

Read-only. Writes nothing to the DB.

    python3 findociq/pipeline/mapping/audit_coverage.py --db findociq/db/compiled_fs.db
    python3 findociq/pipeline/mapping/audit_coverage.py --md out.md
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mapping.normalize import normalize_row_label, safe_clean  # noqa: E402
from mapping.registry import bank_of                           # noqa: E402

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_DICT = _HERE.parent / "concept" / "concept_dictionary.yaml"

HIT, MISS_STRUCT, MISS_ABSENT, CONTAM, NA = (
    "HIT", "MISS-STRUCTURE", "MISS-ABSENT", "CONTAMINATED", "N/A-BY-DISCLOSURE")

# unit_class -> the cell units that are legitimate for it
_UNIT_OK = {
    "currency":  lambda u: u is None or "$" in u or u.lower() in ("m", "sgd"),
    "percent":   lambda u: u is None or "%" in u,
    "per_share": lambda u: True,     # banks are inconsistent here; not a defect signal
    "bps":       lambda u: True,
}


def _dictionary() -> dict[str, dict]:
    return {c["key"]: c for c in yaml.safe_load(_DICT.read_text()).get("concepts", [])}


def _parent_of(lvls, depth) -> str:
    return "" if (not depth or depth < 2) else (lvls[depth - 2] or "")


def load_doc_rows(con) -> dict:
    """(doc_id) -> list of {ttid, label_norm, parent_norm, contaminated_label, row ids}."""
    out = defaultdict(list)
    for (doc_id, table_id, row_id, ttid, label, clean,
         l1, l2, l3, l4, l5, depth) in con.execute("""
            SELECT rd.doc_id, rd.table_id, rd.row_id, t.table_type_id,
                   rd.row_leaf_label, rd.row_leaf_label_clean,
                   rl.lvl1, rl.lvl2, rl.lvl3, rl.lvl4, rl.lvl5, rl.depth
            FROM row_dim rd
            JOIN table_t t ON t.doc_id=rd.doc_id AND t.table_id=rd.table_id
            JOIN row_lineage rl ON rl.row_lineage_id = rd.row_lineage_id
            WHERE t.table_type_id IS NOT NULL"""):
        leaf = safe_clean(label, clean)
        out[doc_id].append(dict(
            ttid=ttid, table_id=table_id, row_id=row_id,
            label_norm=normalize_row_label(leaf),
            parent_norm=normalize_row_label(_parent_of((l1, l2, l3, l4, l5), depth)),
            # the geometry stage emitted a clean label that ADDS tokens -> the
            # printed label itself carries value fragments (UOB 'Gross customer
            # loans 3 52'). Flagged, not silently repaired.
            label_contaminated=bool(clean) and safe_clean(label, clean) != clean))
    return out


def value_state(con, doc_id, table_id, row_id, unit_class, period) -> tuple[bool, str]:
    """-> (clean, reason). Consolidated cells only; a non-consolidated-only row
    is NOT a clean group value."""
    rows = con.execute("""
        SELECT value_num, unit, legal_entity, period FROM cell_fact
         WHERE doc_id=? AND table_id=? AND row_id=?""", (doc_id, table_id, row_id)).fetchall()
    if not rows:
        return False, "no cells"
    usable = [r for r in rows if r[0] is not None
              and (r[2] is None or r[2] == "CONSOLIDATED")]
    if not usable:
        if any(r[0] is not None for r in rows):
            return False, "only non-consolidated cells"
        return False, "no numeric value"
    at_period = [r for r in usable if r[3] == period]
    if not at_period:
        return False, "no cell at the document period"
    ok = _UNIT_OK.get(unit_class, lambda u: True)
    bad = [r for r in at_period if not ok(r[1])]
    if bad and len(bad) == len(at_period):
        return False, f"unit mismatch: {unit_class} concept carrying {bad[0][1]!r}"
    return True, ""


def audit(con) -> tuple[dict, list, list]:
    dictionary = _dictionary()
    anchors = con.execute("""
        SELECT map_id, bank, table_type_id, row_label_norm, parent_label_norm,
               concept_key, basis, segment_key, note
          FROM bank_line_map WHERE map_status='human_confirmed'
         ORDER BY bank, table_type_id, row_label_norm, parent_label_norm""").fetchall()

    docs = con.execute("""
        SELECT d.doc_id, d.institution, d.doc_period FROM document d
         WHERE d.doc_family='financial_stmt' ORDER BY d.institution, d.doc_period""").fetchall()
    by_quarter = defaultdict(list)          # (bank, period) -> [doc_id]
    for doc_id, inst, period in docs:
        by_quarter[(bank_of(inst), period)].append(doc_id)

    doc_rows = load_doc_rows(con)
    matrix, defects = {}, []

    for (map_id, bank, ttid, label, parent, ck, basis, seg, note) in anchors:
        unit_class = dictionary.get(ck or "", {}).get("unit")
        for (qbank, period), doc_ids in sorted(by_quarter.items()):
            if qbank != bank:
                continue
            rows = [r for d in doc_ids for r in doc_rows.get(d, [])]
            in_exhibit = [r for r in rows if r["ttid"] == ttid]
            key = (map_id, period)
            if not in_exhibit:
                matrix[key] = NA
                continue
            exact = [r for r in in_exhibit
                     if r["label_norm"] == label and r["parent_norm"] == parent]
            if not exact:
                same_label = [r for r in in_exhibit if r["label_norm"] == label]
                if same_label:
                    matrix[key] = MISS_STRUCT
                    defects.append(dict(map_id=map_id, bank=bank, period=period, ttid=ttid,
                                        label=label, want_parent=parent, concept=ck,
                                        status=MISS_STRUCT,
                                        detail="parent chain is " +
                                        ", ".join(sorted({repr(r['parent_norm']) for r in same_label}))))
                else:
                    matrix[key] = MISS_ABSENT
                    defects.append(dict(map_id=map_id, bank=bank, period=period, ttid=ttid,
                                        label=label, want_parent=parent, concept=ck,
                                        status=MISS_ABSENT, detail="no row with this label in the exhibit"))
                continue
            # structural path resolves -> is the value usable?
            best, reason = False, ""
            for r in exact:
                if r["label_contaminated"]:
                    reason = reason or "label carries value fragments (geometry clean-label defect)"
                    continue
                doc_id = next(d for d in doc_ids if any(
                    x is r for x in doc_rows.get(d, [])))
                ok, why = value_state(con, doc_id, r["table_id"], r["row_id"], unit_class, period)
                if ok:
                    best = True
                    break
                reason = reason or why
            if best:
                matrix[key] = HIT
            else:
                matrix[key] = CONTAM
                defects.append(dict(map_id=map_id, bank=bank, period=period, ttid=ttid,
                                    label=label, want_parent=parent, concept=ck,
                                    status=CONTAM, detail=reason or "value cell unusable"))
    return matrix, defects, anchors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--md", help="write the full matrix as markdown to this path")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    matrix, defects, anchors = audit(con)

    periods = sorted({p for (_, p) in matrix})
    bank_of_map = {a[0]: a[1] for a in anchors}
    # per bank x quarter summary
    agg = defaultdict(lambda: defaultdict(int))
    for (map_id, period), st in matrix.items():
        agg[(bank_of_map[map_id], period)][st] += 1

    print("=== coverage summary (bank x quarter) ===")
    print(f"{'bank':<5} {'quarter':<12} {'HIT':>4} {'STRUCT':>7} {'ABSENT':>7} {'CONTAM':>7} {'N/A':>5} {'%hit*':>7}")
    for (bank, period) in sorted(agg):
        a = agg[(bank, period)]
        applicable = sum(a[s] for s in (HIT, MISS_STRUCT, MISS_ABSENT, CONTAM))
        pct = (100 * a[HIT] / applicable) if applicable else float("nan")
        print(f"{bank:<5} {period:<12} {a[HIT]:>4} {a[MISS_STRUCT]:>7} {a[MISS_ABSENT]:>7} "
              f"{a[CONTAM]:>7} {a[NA]:>5} {pct:>6.0f}%")
    print("  *%hit excludes N/A-BY-DISCLOSURE from the denominator")

    print(f"\n=== defect log: {len(defects)} non-hits ===")
    groups = defaultdict(list)
    for d in defects:
        groups[(d["status"], d["detail"][:70])].append(d)
    for (status, detail), ds in sorted(groups.items(), key=lambda x: -len(x[1])):
        banks = sorted({d["bank"] for d in ds})
        print(f"\n  [{status}] x{len(ds)}  banks={','.join(banks)}")
        print(f"     {detail}")
        for d in ds[:4]:
            print(f"       {d['bank']} {d['period']} {d['ttid']}/{d['label'][:34]} -> {d['concept']}")
        if len(ds) > 4:
            print(f"       ... +{len(ds)-4} more")

    if args.md:
        write_md(Path(args.md), matrix, defects, anchors, periods, bank_of_map)
        print(f"\nmatrix written to {args.md}")
    con.close()


def write_md(path, matrix, defects, anchors, periods, bank_of_map):
    ICON = {HIT: "✅", MISS_STRUCT: "🟠", MISS_ABSENT: "❌", CONTAM: "⚠️", NA: "·"}
    L = ["# Key-field extractability matrix", "",
         "89 human_confirmed anchors x bank-quarter. A HIT requires the structural",
         "path to resolve AND a clean consolidated value at the document period.", "",
         "✅ HIT · 🟠 MISS-STRUCTURE · ❌ MISS-ABSENT · ⚠️ CONTAMINATED · · N/A-BY-DISCLOSURE", ""]
    for bank in ("DBS", "UOB", "OCBC"):
        bp = [p for p in periods if any(bank_of_map[m] == bank and (m, p) in matrix
                                        for m, _ in [(a[0], 0) for a in anchors])]
        rows = [a for a in anchors if a[1] == bank]
        if not rows:
            continue
        L += [f"## {bank}", "", "| concept | exhibit | anchor | " + " | ".join(bp) + " |",
              "|---|---|---|" + "---|" * len(bp)]
        for a in rows:
            mid, _, ttid, label, parent, ck, basis, seg, note = a
            anchor = f"`{label}`" + (f" ‹{parent}›" if parent else "")
            cells = [ICON.get(matrix.get((mid, p), NA), "?") for p in bp]
            L.append(f"| `{ck}`{' ['+basis+']' if basis else ''} | {ttid} | {anchor} | "
                     + " | ".join(cells) + " |")
        L.append("")
    path.write_text("\n".join(L))


if __name__ == "__main__":
    main()
