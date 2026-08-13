"""concept.discover_cuts — dimension-cut discovery (the analyst "menu").

For every line item (concept) in a compiled FS DB, enumerate the dimensional
breakdowns it is cut by, in each document, using the repeated-total + sums_to
signal that the loader already arithmetic-verified.

A CUT is a sums_to GROUP (a total + its verified member rows/cols):
  * ROW axis  — row_dim members whose sums_to points at a total row_id.
  * COL axis  — col_dim members whose sums_to points at a total col_id.
Both already reconcile arithmetically at load, so every cut is verified=True.

DIMENSION LABEL
  ROW: the nearest section-header row (row_hierarchy=0 with NO value cells, e.g.
       'By business unit' / 'By geography' / 'By industry' / 'By currency') at or
       above the members. If none, fall back to the members' shared stamped
       dimension (all geo_key -> 'geography', all segment_key -> 'segment') else
       'unlabeled'.
  COL: the members' shared stamped dimension (segment_key -> 'segment',
       geo_key -> 'geography') else the group banner (col_parent label) else
       'unlabeled'.

CONCEPT IDENTITY
  ROW: the total row's concept_key if stamped, else 'label:' + norm(total label).
       REPEATED-VALUE MERGE — totals in the SAME table with the SAME value
       signature AND a compatible concept/label are ONE line-item instance
       (customer_loans 'Total (Gross)'=439100 appears 4x, one per cut); they are
       merged so their 4 cuts group under a single concept.
  COL: the ROW concept (the columns cut a row's value across the dimension); one
       cut is emitted per distinct value-bearing row concept in the table.

Catalog granularity: one row per (doc_id, concept, dimension, axis) — the same
cut appearing in several sibling tables (e.g. OCBC segment tables per period)
collapses, taking the max n_members.

Deterministic, zero API. Reads findociq/db/compiled_fs.db; writes the machine
catalog to findociq/data/derived/concept_cuts.csv and prints the full report.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.normalize import norm  # noqa: E402
from pass2.load_v7 import _clean_label  # noqa: E402  (footnote-tail + ws strip, keep case)

_ROOT = Path(__file__).resolve().parents[3]  # repo root
_DB = _ROOT / "findociq" / "db" / "compiled_fs.db"
_CSV = _ROOT / "findociq" / "data" / "derived" / "concept_cuts.csv"


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
class Cut:
    __slots__ = ("doc_id", "institution", "concept", "concept_name", "dimension",
                 "axis", "n_members", "verified", "members", "table_id")

    def __init__(self, doc_id, institution, concept, concept_name, dimension,
                 axis, n_members, members, table_id):
        self.doc_id = doc_id
        self.institution = institution
        self.concept = concept
        self.concept_name = concept_name
        self.dimension = dimension
        self.axis = axis
        self.n_members = n_members
        self.verified = True          # every cut is a verified sums_to group
        self.members = members        # list[str] member labels
        self.table_id = table_id


def _name_map(dict_path: Path | None = None) -> dict[str, str]:
    """concept_key -> human name, parsed straight from the dictionary YAML."""
    import yaml
    p = dict_path or (Path(__file__).resolve().parent / "concept_dictionary.yaml")
    doc = yaml.safe_load(p.read_text())
    return {c["key"]: c.get("name", "") for c in doc.get("concepts", [])}


def _fetch_tables(con: sqlite3.Connection):
    """{(doc_id, table_id): {'institution':.., 'rows':[...], 'cols':[...],
    'rowvals':{row_id:[(col_id,value_num)...]}, 'colvals':{col_id:[(row_id,val)]}}}"""
    rows = con.execute("""
        SELECT r.doc_id, r.table_id, d.institution,
               r.row_id, r.row_hierarchy, r.row_parent, r.row_leaf_label,
               r.concept_key, r.geo_key, r.segment_key, r.sums_to
        FROM row_dim r JOIN document d ON d.doc_id = r.doc_id
        ORDER BY r.doc_id, r.table_id, r.row_id
    """).fetchall()
    cols = con.execute("""
        SELECT doc_id, table_id, col_id, col_hierarchy, col_parent,
               col_leaf_label, geo_key, segment_key, sums_to
        FROM col_dim ORDER BY doc_id, table_id, col_id
    """).fetchall()
    vals = con.execute("""
        SELECT doc_id, table_id, row_id, col_id, value_num
        FROM cell_fact WHERE value_num IS NOT NULL
    """).fetchall()

    T: dict = defaultdict(lambda: {"institution": None, "rows": [], "cols": [],
                                   "rowvals": defaultdict(list),
                                   "colvals": defaultdict(list)})
    for r in rows:
        t = T[(r[0], r[1])]
        t["institution"] = r[2]
        t["rows"].append(dict(row_id=r[3], hierarchy=r[4], parent=r[5],
                              label=r[6], concept=r[7], geo=r[8], seg=r[9],
                              sums_to=r[10]))
    for c in cols:
        t = T[(c[0], c[1])]
        t["cols"].append(dict(col_id=c[2], hierarchy=c[3], parent=c[4],
                              label=c[5], geo=c[6], seg=c[7], sums_to=c[8]))
    for v in vals:
        t = T[(v[0], v[1])]
        t["rowvals"][v[2]].append((v[3], v[4]))
        t["colvals"][v[3]].append((v[2], v[4]))
    return T


def _value_sig(rowvals: dict, row_id: int) -> tuple:
    """Deterministic value signature of a row: its (col_id, value_num) pairs
    sorted by col_id. Used to detect repeated totals (same line item, many cuts)."""
    return tuple(sorted(rowvals.get(row_id, [])))


def _members_dim(members: list[dict], key: str) -> bool:
    """True iff EVERY member row/col carries a non-null stamp on `key`."""
    return bool(members) and all(m.get(key) for m in members)


# --------------------------------------------------------------------------- #
# row cuts
# --------------------------------------------------------------------------- #
def _row_cuts(doc_id, table_id, t, names) -> list[Cut]:
    rows = t["rows"]
    by_id = {r["row_id"]: r for r in rows}
    rowvals = t["rowvals"]
    # section headers: hierarchy-0 rows with NO value cells anywhere in the row.
    headers = [r for r in rows
               if r["hierarchy"] == 0 and not rowvals.get(r["row_id"])]

    # group members by their total row_id
    groups: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r["sums_to"] is not None:
            groups[r["sums_to"]].append(r)

    raw: list[dict] = []
    for total_id, members in groups.items():
        total = by_id.get(total_id)
        if total is None or not members:
            continue
        min_pos = min(m["row_id"] for m in members)
        # nearest section header at or above the members
        above = [h for h in headers if h["row_id"] <= min_pos]
        if above:
            dim = _clean_label(max(above, key=lambda h: h["row_id"])["label"])
        elif _members_dim(members, "geo"):
            dim = "geography"
        elif _members_dim(members, "seg"):
            dim = "segment"
        else:
            dim = "unlabeled"
        concept = total["concept"] or ("label:" + norm(total["label"]))
        raw.append(dict(total=total, members=members, dim=dim, concept=concept,
                        sig=_value_sig(rowvals, total_id)))

    # REPEATED-VALUE MERGE: within this table, totals with the same non-empty
    # value signature AND a compatible concept/label are one line item.
    merged: dict[int, dict] = {}      # cluster_id -> {concept, cuts:[raw...]}
    sig_index: dict[tuple, list[int]] = defaultdict(list)
    next_id = 0
    for rc in raw:
        placed = None
        if rc["sig"]:
            for cid in sig_index[rc["sig"]]:
                head = merged[cid]["cuts"][0]
                if _compatible(head, rc):
                    placed = cid
                    break
        if placed is None:
            placed = next_id
            next_id += 1
            merged[placed] = {"cuts": []}
            if rc["sig"]:
                sig_index[rc["sig"]].append(placed)
        merged[placed]["cuts"].append(rc)

    out: list[Cut] = []
    for cl in merged.values():
        cuts = cl["cuts"]
        # merged identity: prefer a stamped concept_key, else the shared label form
        concept = next((c["concept"] for c in cuts
                        if not c["concept"].startswith("label:")), cuts[0]["concept"])
        for rc in cuts:
            members = sorted(rc["members"], key=lambda m: m["row_id"])
            out.append(Cut(doc_id, t["institution"], concept,
                           names.get(concept, ""), rc["dim"], "row",
                           len(members), [m["label"] for m in members], table_id))
    return out


def _compatible(a: dict, b: dict) -> bool:
    """Two same-value totals are the same line item iff concept keys match, or
    labels normalise equal, or at least one concept is unstamped (a bare total)."""
    ca, cb = a["concept"], b["concept"]
    if ca == cb:
        return True
    la = ca.startswith("label:")
    lb = cb.startswith("label:")
    if la and lb:
        return ca == cb                       # same normalised label
    if la or lb:                              # one stamped, one bare total -> merge
        return True
    return False                              # two DIFFERENT stamped concepts: keep apart


# --------------------------------------------------------------------------- #
# col cuts
# --------------------------------------------------------------------------- #
def _col_cuts(doc_id, table_id, t, names) -> list[Cut]:
    cols = t["cols"]
    by_id = {c["col_id"]: c for c in cols}
    rows = t["rows"]
    row_by_id = {r["row_id"]: r for r in rows}

    groups: dict[int, list[dict]] = defaultdict(list)
    for c in cols:
        if c["sums_to"] is not None:
            groups[c["sums_to"]].append(c)

    out: list[Cut] = []
    for total_id, members in groups.items():
        if not members:
            continue
        if _members_dim(members, "seg"):
            dim = "segment"
        elif _members_dim(members, "geo"):
            dim = "geography"
        else:
            parent = members[0].get("parent")
            banner = by_id.get(parent, {}).get("label") if parent else None
            dim = _clean_label(banner) if banner else "unlabeled"
        member_labels = [m["label"] for m in sorted(members, key=lambda m: m["col_id"])]
        # the columns cut EACH value-bearing row concept in the table
        seen: set[str] = set()
        for c in members:
            for (row_id, _v) in t["colvals"].get(c["col_id"], []):
                r = row_by_id.get(row_id)
                if r is None or r["hierarchy"] < 1:
                    continue
                concept = r["concept"] or ("label:" + norm(r["label"]))
                if concept in seen:
                    continue
                seen.add(concept)
                out.append(Cut(doc_id, t["institution"], concept,
                               names.get(concept, ""), dim, "col",
                               len(members), member_labels, table_id))
    return out


# --------------------------------------------------------------------------- #
# dedup + report
# --------------------------------------------------------------------------- #
def discover(db: Path = _DB) -> list[Cut]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = _name_map()
        T = _fetch_tables(con)
        cuts: list[Cut] = []
        for (doc_id, table_id), t in T.items():
            cuts += _row_cuts(doc_id, table_id, t, names)
            cuts += _col_cuts(doc_id, table_id, t, names)
    finally:
        con.close()

    # collapse the same cut across sibling tables: one row per
    # (doc_id, concept, dimension, axis), taking the widest breakdown.
    best: dict[tuple, Cut] = {}
    for c in cuts:
        k = (c.doc_id, c.concept, c.dimension, c.axis)
        cur = best.get(k)
        if cur is None or c.n_members > cur.n_members:
            best[k] = c
    return sorted(best.values(),
                  key=lambda c: (c.institution, c.doc_id, c.concept, c.axis, c.dimension))


def _label(c: Cut) -> str:
    if c.concept.startswith("label:"):
        return c.concept[len("label:"):] + "  (unstamped)"
    nm = f"  — {c.concept_name}" if c.concept_name else ""
    return c.concept + nm


def write_csv(cuts: list[Cut], path: Path = _CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doc_id", "institution", "concept_key_or_label", "dimension",
                    "axis", "n_members", "verified", "member_sample"])
        for c in cuts:
            sample = "; ".join(_clean_label(m) for m in c.members[:5])
            w.writerow([c.doc_id, c.institution, c.concept, c.dimension, c.axis,
                        c.n_members, c.verified, sample])


def report(cuts: list[Cut]) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("DIMENSION-CUT DISCOVERY REPORT  (repeated-total + sums_to signal)")
    lines.append("=" * 78)

    # group by (institution, doc) then concept
    by_doc: dict[tuple, list[Cut]] = defaultdict(list)
    for c in cuts:
        by_doc[(c.institution, c.doc_id)].append(c)

    for (inst, doc_id) in sorted(by_doc):
        doc_cuts = by_doc[(inst, doc_id)]
        lines.append("")
        lines.append("#" * 78)
        lines.append(f"# {inst}")
        lines.append(f"#   {doc_id}")
        lines.append("#" * 78)
        by_concept: dict[str, list[Cut]] = defaultdict(list)
        for c in doc_cuts:
            by_concept[c.concept].append(c)
        for concept in sorted(by_concept):
            cs = by_concept[concept]
            multi = " *** MULTI-DIMENSIONAL ***" if len(cs) > 1 else ""
            lines.append("")
            lines.append(f"  {_label(cs[0])}{multi}")
            for c in sorted(cs, key=lambda x: (x.axis, x.dimension)):
                sample = "; ".join(_clean_label(m) for m in c.members[:4])
                if len(c.members) > 4:
                    sample += "; ..."
                vflag = "verified" if c.verified else "UNVERIFIED"
                lines.append(f"      - [{c.axis}] {c.dimension:<22} "
                             f"n={c.n_members:<2} ({vflag})  {sample}")

    # summary
    concepts = {(c.doc_id, c.concept) for c in cuts}
    per_concept: dict[tuple, set] = defaultdict(set)
    for c in cuts:
        per_concept[(c.doc_id, c.concept)].add((c.dimension, c.axis))
    multi = [k for k, v in per_concept.items() if len(v) > 1]
    lines.append("")
    lines.append("=" * 78)
    lines.append("SUMMARY")
    lines.append("=" * 78)
    lines.append(f"  concepts with >=1 cut          : {len(concepts)}")
    lines.append(f"  total cuts (dedup per doc)     : {len(cuts)}")
    lines.append(f"  multi-dimensional concepts     : {len(multi)}")
    lines.append(f"  row-axis cuts                  : {sum(1 for c in cuts if c.axis=='row')}")
    lines.append(f"  col-axis cuts                  : {sum(1 for c in cuts if c.axis=='col')}")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover dimensional cuts per concept.")
    ap.add_argument("--db", type=Path, default=_DB)
    ap.add_argument("--csv", type=Path, default=_CSV)
    args = ap.parse_args()
    cuts = discover(args.db)
    write_csv(cuts, args.csv)
    print(report(cuts))
    print(f"\n[catalog written] {args.csv}  ({len(cuts)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
