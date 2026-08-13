"""resolve_anchors — G13 continuation, Step 1: resolve every `anchor` /
`pending_extraction` row in `lineage_identity_map.csv` through the registry to
a stable `(doc_id, table_type_id, row_lineage_id)`, failing loud at the level
that misses. See docs/specs/2026-08-XX-anchor-row-resolution.md.

Four levels, each scoped by the previous, exactly the chain built across the
two prior passes:

    source_doc  -> document_alias          -> doc_id
    doc_section -> section_registry        -> section_canonical  (+ doc_kind, from doc_cadence)
    table_name  -> normalise_caption + table_registry_alias (proven, see notes)
                                            -> table_type_id, cross-checked live in table_catalog
    (parent_row, line_item) -> row_lineage (lvl2/lvl3, normalized) -> row_lineage_id

Table-level mechanism note: this reuses `mapping.registry.resolve_table_type`
(the alias-based mechanism from the prior pass) rather than a fresh lookup
directly against `table_catalog`, because `table_catalog.caption_canonical`
is the SEED's canonical spelling and does not literally string-match every raw
`table_name` the map records (e.g. UOB's map says "Selected income statement",
the seed's canonical caption is "Selected income statement items" -- these
normalize to DIFFERENT strings, not the same one, so an exact-normalized-match
against table_catalog would wrongly FAIL a target that is not actually
ambiguous). `resolve_table_type` already correctly resolves all 11 (bank,
section, table) combinations the 72 anchor rows use -- proven in the prior
pass by cross-checking against real `table_t` rows, not just alias-lookup
success. `table_catalog` is still consulted here, but as a *confirmation* (the
resolved table_type_id must be a live catalog entry for this bank/doc_kind) and
as the source of `expected`/`is_narrative`/cadence metadata for Step 6 -- not
as the primary lookup.

    python3 findociq/pipeline/mapping/resolve_anchors.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(_HERE.parent))
from mapping.normalize import normalize_exhibit_title, normalize_row_label  # noqa: E402
from mapping.registry import resolve_table_type  # noqa: E402

MAP_CSV = _REPO / "findociq" / "data" / "derived" / "lineage_identity_map.csv"


class Ambiguous(Exception):
    """Raised when parent_row is empty in the map but the leaf label matches
    more than one physical row -- the map didn't disambiguate and guessing
    which one is wrong is exactly what this layer refuses to do."""
    def __init__(self, candidates):
        self.candidates = candidates


def resolve_row(con, doc_id, table_type_id, parent_row, line_item):
    """Level 4: find the row_lineage row for (parent_row, line_item) across
    every physical table_id in this doc carrying table_type_id.

    The leaf label lives at `lvl{depth}` and its immediate parent at
    `lvl{depth-1}` -- NOT at a fixed lvl2/lvl3 position. Confirmed this varies
    by table: DBS's row_lineage carries a constant lvl1 = the table's own
    title (so its real rows sit at lvl2/lvl3), but UOB's combined-highlights
    table has NO shared title row -- each top-level line item's own label is
    lvl1 itself (depth=1), and only genuinely nested groups (e.g. "Capital
    Adequacy Ratios" -> "Common Equity Tier 1") go to depth=2. A fixed
    lvl2-is-always-the-leaf assumption silently misses every depth=1 row.

    When the map gives no parent_row, "top-level" is decided by UNIQUENESS,
    not by table shape: if the leaf label matches exactly one row anywhere in
    scope (at any depth), that's the match, parent or no parent -- OCBC's
    "Loans to customers" sits under an "ASSETS" section header but is the only
    row with that label, so it resolves fine unparented. If the label matches
    more than one row (DBS's "Net interest income" appears once under
    "Commercial book total income" and once under "Markets trading income",
    with no third, unambiguous top-level occurrence), an empty parent_row
    cannot disambiguate and this raises Ambiguous rather than guessing.
    Inferring "is this a real grouping" from table structure (distinct-parent
    counts, title-vs-group heuristics) was tried and discarded -- it silently
    passed OCBC's ASSETS/LIABILITIES split as "not a real parent" while also
    silently failing UOB's genuinely-unique "Common Equity Tier 1" for the
    wrong reason. Uniqueness is the actual invariant the map's empty
    parent_row is asserting; check that directly.

    Returns (row_lineage_id, table_id, matched_parent, matched_leaf) or None.
    Raises Ambiguous if parent_row is empty and >1 row matches the leaf alone.
    """
    table_ids = [r[0] for r in con.execute(
        "SELECT table_id FROM table_t WHERE doc_id=? AND table_type_id=?", (doc_id, table_type_id))]
    if not table_ids:
        return None
    line_norm = normalize_row_label(line_item)
    parent_norm = normalize_row_label(parent_row) if parent_row else ""
    qmarks = ",".join("?" * len(table_ids))
    # row_lineage.lvl{depth} carries the FOOTNOTE-RESOLVED display label (e.g.
    # "Total income 1", the marker turned into its footnote number) -- fine for
    # display, but the trailing " <digit>" only strips in normalize_row_label
    # when preceded by punctuation (so "Tier 1" / "Stage 3" survive). Letter-
    # preceded footnote digits ("Total income 1", "Total assets 5 72") slip
    # through and fragment the key. row_dim.row_leaf_label is the pre-footnote
    # raw label for the SAME row and is footnote-clean already (verified: raw
    # "Total income" vs clean "Total income 1"). Try both; raw first.
    rows = con.execute(f"""
        SELECT DISTINCT rl.row_lineage_id, cf.table_id, rl.lvl1, rl.lvl2, rl.lvl3, rl.lvl4, rl.lvl5, rl.depth,
               (SELECT rd.row_leaf_label FROM row_dim rd
                 WHERE rd.doc_id=cf.doc_id AND rd.table_id=cf.table_id AND rd.row_lineage_id=rl.row_lineage_id
                 LIMIT 1) AS leaf_raw
        FROM cell_fact cf JOIN row_lineage rl ON rl.row_lineage_id = cf.row_lineage_id
        WHERE cf.doc_id=? AND cf.table_id IN ({qmarks})
    """, (doc_id, *table_ids)).fetchall()

    # Matching (above) is correctly parent-agnostic when parent_norm is empty --
    # uniqueness alone decides it. But the parent value we RETURN is what gets
    # PERSISTED as bank_line_map.parent_label_norm, and some tables (DBS-shaped)
    # carry a CONSTANT lvl1 = the table's own title on every single row -- that
    # is not a real parent, and the corpus's own existing convention
    # (backfill_map.py / the pre-existing human_confirmed rows) already treats
    # it as "" (confirmed: DBS "Total income" is human_confirmed at parent="",
    # never at parent="selected income statement items"). Collapse a
    # table-title-constant parent back to None so a fresh match lands on the
    # SAME address the rest of the corpus already uses, instead of minting a
    # second, table-title-qualified address for the same row.
    parent_at_depth: dict[tuple[str, int], set] = {}
    for row_lineage_id, table_id, lvl1, lvl2, lvl3, lvl4, lvl5, depth, leaf_raw in rows:
        if depth >= 2:
            lvls = [None, lvl1, lvl2, lvl3, lvl4, lvl5]
            parent_at_depth.setdefault((table_id, depth), set()).add(lvls[depth - 1])
    title_like = {k: len(v) <= 1 for k, v in parent_at_depth.items()}

    matches = []
    for row_lineage_id, table_id, lvl1, lvl2, lvl3, lvl4, lvl5, depth, leaf_raw in rows:
        lvls = [None, lvl1, lvl2, lvl3, lvl4, lvl5]  # 1-indexed
        leaf = lvls[depth] if depth <= 5 else None
        parent = lvls[depth - 1] if depth >= 2 else None
        if leaf is None:
            continue
        leaf_matches = (normalize_row_label(leaf) == line_norm
                         or (leaf_raw and normalize_row_label(leaf_raw) == line_norm))
        if not leaf_matches:
            continue
        # Prefer the footnote-clean raw label for what gets RETURNED (and thus
        # stored as bank_line_map.row_label_norm) whenever it's the one that
        # actually matched -- else letter-preceded footnote digits ("Total
        # income 1", "Total assets 5 72") that row_lineage's clean-display
        # form still carries would mint a second, footnote-suffixed address
        # for a row the corpus already has a clean entry for.
        if leaf_raw and normalize_row_label(leaf_raw) == line_norm:
            leaf = leaf_raw
        if parent_norm:
            if parent is None or normalize_row_label(parent) != parent_norm:
                continue
        stored_parent = None if title_like.get((table_id, depth), depth == 1) else parent
        matches.append((row_lineage_id, table_id, stored_parent, leaf))

    if not matches:
        return None
    if len(matches) > 1 and not parent_norm:
        raise Ambiguous(matches)
    return matches[0]


def resolve_one(con, r):
    bank, src, sec, tbl = r["bank"], r["source_doc"], r["doc_section"], r["table_name"]
    concept_key = r["concept_key"]

    doc_row = con.execute("SELECT doc_id FROM document_alias WHERE alias_filename=?", (src,)).fetchone()
    if not doc_row:
        return dict(concept_key=concept_key, bank=bank, doc_id=None, table_type_id=None,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="FAIL", level="document", detail=f"source_doc {src!r} has no document_alias")
    doc_id = doc_row[0]

    cad = con.execute("SELECT doc_kind FROM doc_cadence WHERE doc_id=?", (doc_id,)).fetchone()
    doc_kind = cad[0] if cad else None

    sec_norm = normalize_exhibit_title(sec)
    sec_row = None
    if doc_kind:
        sec_row = con.execute(
            "SELECT section_canonical FROM section_registry WHERE bank=? AND doc_kind=? AND section_raw_norm=?",
            (bank, doc_kind, sec_norm)).fetchone()
    db_sec = con.execute(
        "SELECT section_id FROM section WHERE doc_id=? AND LOWER(section_title)=LOWER(?) "
        "ORDER BY section_level ASC", (doc_id, sec)).fetchall()
    if not db_sec:
        return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=None,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="FAIL", level="section", detail=f"section {sec!r} not found in doc_id {doc_id}")
    section_canonical = sec_row[0] if sec_row else None

    ttid, alias = resolve_table_type(con, bank, sec, tbl)
    if not ttid:
        return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=None,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="FAIL", level="table", detail=f"table {tbl!r} not found in {doc_id} / {sec}")

    if r["resolution"] == "pending_extraction":
        return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=ttid,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="PENDING_EXTRACTION", level="row",
                    detail="table alias resolves; no table_t row of that type exists in this doc yet "
                           "(mistitled at extraction, not missing) "
                           "-- see docs/specs/2026-08-03-anchor-scope-resolution.md")

    real = con.execute("SELECT COUNT(*) FROM table_t WHERE doc_id=? AND table_type_id=?",
                        (doc_id, ttid)).fetchone()[0]
    if real == 0:
        return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=ttid,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="FAIL", level="table",
                    detail=f"alias {alias!r} resolved to {ttid} but no table_t row of that type exists in {doc_id}")
    catalog = con.execute(
        "SELECT 1 FROM table_catalog WHERE bank=? AND section_canonical=? AND table_type_id=?",
        (bank, section_canonical, ttid)).fetchone() if section_canonical else None

    try:
        row_match = resolve_row(con, doc_id, ttid, r["parent_row"], line_item=r["line_item"])
    except Ambiguous as amb:
        locations = "; ".join(f"parent={p!r}" for _, _, p, _ in amb.candidates)
        return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=ttid,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="FAIL", level="row",
                    detail=f"AMBIGUOUS: {r['line_item']!r} matches {len(amb.candidates)} rows in "
                           f"{doc_id} / table_type {ttid} with no parent_row given to disambiguate "
                           f"({locations}) -- map needs a parent_row")
    if not row_match:
        pr = f"{r['parent_row']!r} / " if r["parent_row"] else ""
        return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=ttid,
                    parent_row=r["parent_row"], line_item=r["line_item"],
                    status="FAIL", level="row",
                    detail=f"row {pr}{r['line_item']!r} not found in {doc_id} / table_type {ttid}")
    row_lineage_id, table_id, matched_parent, matched_leaf = row_match
    catalog_note = "" if catalog else " [WARN: table_type_id not in table_catalog for this bank/section]"
    return dict(concept_key=concept_key, bank=bank, doc_id=doc_id, table_type_id=ttid,
                parent_row=r["parent_row"], line_item=r["line_item"],
                # the REAL physical address, not the map's possibly-empty parent_row --
                # row_dim gets stamped against this same real address (see
                # migrate_serving_views.py's stamp_human_anchors), so bank_line_map
                # must store the same thing or the two will never agree on a
                # mislabeled-parent row.
                matched_parent_norm=normalize_row_label(matched_parent) if matched_parent else "",
                matched_leaf_norm=normalize_row_label(matched_leaf),
                status="PASS", level="-",
                detail=f"table_id={table_id} row_lineage_id={row_lineage_id} "
                       f"matched=({matched_parent!r},{matched_leaf!r})" + catalog_note)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    with open(MAP_CSV) as f:
        rows = list(csv.DictReader(f))
    targets = [r for r in rows if r["resolution"] in ("anchor", "pending_extraction")]

    results = [resolve_one(con, r) for r in targets]
    n_pass = sum(1 for x in results if x["status"] == "PASS")
    n_pending = sum(1 for x in results if x["status"] == "PENDING_EXTRACTION")
    n_fail = sum(1 for x in results if x["status"] == "FAIL")

    print(f"{'concept_key':28} | {'bank':5} | {'doc_id':45} | {'table_type_id':20} | "
          f"{'parent_row':20} | {'line_item':30} | status | detail")
    for x in results:
        print(f"{x['concept_key']:28} | {x['bank']:5} | {(x['doc_id'] or ''):45} | "
              f"{(x['table_type_id'] or ''):20} | {(x['parent_row'] or ''):20} | "
              f"{x['line_item']:30} | {x['status']:18} | {x['detail']}")

    print(f"\nPASS={n_pass}  PENDING_EXTRACTION={n_pending}  FAIL={n_fail}  total={len(results)}")
    if n_fail:
        print("\nFAILs by level:")
        from collections import Counter
        for lvl, cnt in Counter(x["level"] for x in results if x["status"] == "FAIL").items():
            print(f"  {lvl}: {cnt}")
    con.close()


if __name__ == "__main__":
    main()
