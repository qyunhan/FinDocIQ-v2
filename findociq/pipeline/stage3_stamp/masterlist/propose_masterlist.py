"""propose_masterlist — scaffold masterlist entries for table types nobody has
authored yet.

WHAT THIS IS NOT. It is not a masterlist writer. `data/derived/masterlist/` is
hand-curated and is the ONLY authority for `canonical_leaf_id`
(`archive/2026-08-06-masterlist-retirement/README.md`). Three generated
registries were retired on 2026-08-06 precisely because a stamping run matched
against generated ids and overwrote curated ones. So this script:

  * writes ONLY into a proposal directory, never into `data/derived/masterlist/`
  * refuses to run if the output directory is (or is inside) the masterlist dir
  * skips every `(bank, table_type_id)` that already has curated entries

The output is a starting draft for a human to curate and promote.

HOW THE IDS ARE DERIVED. Not reimplemented — the same functions the stamper uses,
so a promoted proposal resolves back to the rows it came from:

  `resolve_canonical_leaf._load_rows`  -> the table's rows
  `masterlist_derive.classify` + `build_ancestry`
                                       -> row class and ancestor chain
  `masterlist_derive.leaf_id`          -> `canonical_leaf_id`
  `resolve_canonical_leaf._printed_chain` -> `full_path` (verbatim, ' > '-joined)
  `resolve_canonical_leaf.doc_source_family` -> `source_family`

ONE DELIBERATE DEVIATION — the of-which memo form. `leaf_id()` emits three
segments, `total_income::of_which::net_interest_income`; every curated file uses
two, `total_income::of_which_net_interest_income`. That exact disagreement is
what triggered the 2026-08-06 retirement, so the CURATED form wins here and
`masterlist_derive` is left untouched. See `_curated_of_which`.

    python3 findociq/pipeline/stage3_stamp/masterlist/propose_masterlist.py
    python3 .../propose_masterlist.py --axis cols
    python3 .../propose_masterlist.py --banks UOB --out /tmp/try
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "findociq/pipeline"))

from stage3_stamp.masterlist import masterlist_derive as D      # noqa: E402
from stage3_stamp.resolve import resolve_canonical_leaf as RCL    # noqa: E402

MASTERLIST = REPO / "findociq/data/derived/masterlist"
COVERAGE = MASTERLIST / "table_type_coverage.csv"
DEFAULT_OUT = REPO / "findociq/data/derived/masterlist_proposed_2026-08-11"
DEFAULT_DB = REPO / "findociq/db/compiled_fs.db"

# Narrative exhibits: classifiable so they can be excluded on purpose rather
# than sitting in the review queue every quarter, but they carry no addressable
# values, so there is nothing for a masterlist to declare.
NARRATIVE = {"FS_KEY_AUDIT_MATTERS", "FS_PERIOD_PERFORMANCE_SUMMARY"}

ROW_COLS = ["bank", "canonical_section", "section_ordinal", "table_type_id",
            "table_ordinal", "line_ordinal", "canonical_leaf_id", "label",
            "full_path", "source_family", "notes"]
COL_COLS = ["bank", "table_type_id", "col_ordinal", "canonical_col_id", "label",
            "full_path", "dim", "dim_key", "source_family", "notes"]

_OF_WHICH_SEG = re.compile(r"::of_which::")


def _curated_of_which(leaf: str) -> str:
    """`a::of_which::b` -> `a::of_which_b`, the form every curated file uses."""
    return _OF_WHICH_SEG.sub("::of_which_", leaf)


def _family_label(fam: str | None) -> str:
    """`performance_summary` -> `Performance_summary`, matching the casing the
    curated files use. `norm_family()` folds case, so this is cosmetic only."""
    return (fam[:1].upper() + fam[1:]) if fam else ""


def load_covered() -> set[tuple[str, str]]:
    """(bank, table_type_id) pairs the curated masterlists already declare."""
    out = set()
    for p in sorted(MASTERLIST.glob("*_masterlist.csv")):
        for r in csv.DictReader(p.open(encoding="utf-8")):
            out.add((r["bank"], r["table_type_id"]))
    return out


def load_wanted(banks: list[str] | None) -> dict[str, set[str]]:
    """bank -> table_type_ids seen in the corpus, from the coverage checklist."""
    want: dict[str, set[str]] = defaultdict(set)
    for r in csv.DictReader(COVERAGE.open(encoding="utf-8")):
        if banks and r["bank"] not in banks:
            continue
        want[r["bank"]].add(r["table_type_id"])
    return want


def tables_by_type(con: sqlite3.Connection) -> dict[tuple[str, str], list[dict]]:
    """(bank, table_type_id) -> its physical tables, richest first.

    `table_t.table_type_id` in the DB is stale (50/342 stamped, some under
    retired names), so the type is resolved live from the registry instead —
    same resolver the ingest uses.
    """
    from stage3_stamp.resolve.normalize import safe_clean
    from stage3_stamp.resolve.registry import bank_of, resolve_table_type

    rows = con.execute("""
        SELECT t.doc_id, t.table_id, t.table_title, t.table_title_clean,
               t.section_id, t.page_range, d.institution, s.section_title
        FROM table_t t
        JOIN document d ON d.doc_id = t.doc_id
        LEFT JOIN section s ON s.doc_id = t.doc_id AND s.section_id = t.section_id
    """).fetchall()
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc_id, tid, title, title_clean, sec_id, pg, inst, sec_title in rows:
        bank = bank_of(inst)
        ttid, _ = resolve_table_type(con, bank, sec_title,
                                     safe_clean(title, title_clean))
        if not ttid:
            continue
        n = con.execute("SELECT count(*) FROM row_dim WHERE doc_id=? AND table_id=?",
                        (doc_id, tid)).fetchone()[0]
        out[(bank, ttid)].append(dict(
            doc_id=doc_id, table_id=tid, title=title, title_clean=title_clean,
            section_id=sec_id, section_title=sec_title, page=pg, n_rows=n))
    for k in out:
        # Prefer the vintage being authored, then the richest table. A masterlist
        # is period-agnostic, but drafting off 4Q25 keeps the labels the curator
        # is reading on screen and avoids picking a 2Q26 half-year variant whose
        # printed banners differ.
        out[k].sort(key=lambda t: (0 if "4Q25" in t["doc_id"] else 1,
                                   -t["n_rows"], t["doc_id"], t["table_id"]))
    return out


def _page(t: dict) -> int:
    head = str(t.get("page") or "").split("-")[0]
    return int(head) if head.isdigit() else 9999


def _date_parents(row) -> list[str]:
    """Ancestors that are a DATE-labelled balance line, e.g. `At 1 January 2024`.

    NOT a period leak — those are gone, fixed by rule 3b in
    `masterlist_derive.classify`. This is a HIERARCHY defect: OCBC's statement of
    changes in equity captures `Profit for the year`, `Other comprehensive
    income` and the rest as CHILDREN of the opening-balance row, so the id comes
    out `at_1_january::profit_for_the_year`. In the printed statement they are
    its SIBLINGS — opening balance, then movements, then closing balance.

    Not repaired here, and not repairable by reclassifying. The row is valued and
    its date IS its identity (opening vs closing balance, the distinction
    masterlist_derive.py:125 exists to protect); DBS prints the same line as
    `Balance at 1 January 2025` and captures it flat. Demoting it to PERIOD_ROW
    would contribute no id segment and lose the balance line entirely. The fix
    belongs in the geometry/loader ancestry, not in leaf-id derivation.
    """
    return [lab for lab in (row.ancestor_labels_raw or row.ancestor_labels)
            if D.is_period_banner_label(lab or "")]


def propose_rows(con, bank, ttid, tabs) -> list[dict]:
    """Draft rows for a table type — the UNION of leaves across every table of
    that type in the same document family.

    Not just the richest table. DBS prints its geography exhibit twice on
    p18-21: 'Performance by geography — Selected income statement items' (11
    leaves, all under a `selected_income_statement_items::` banner) and
    'Performance by geography, Year 2025 / Year 2024' (11 leaves, bare, no
    banner). Both resolve to FS_PERF_BY_GEOGRAPHY, and the two leaf sets are
    DISJOINT — same line items, different ancestry. Drafting from the richest
    alone silently dropped all 11 bare ones.

    Scoped to ONE DOCUMENT, deliberately. `locate_tables` scores a candidate as
    `matched / MIN_MATCH_FRACTION * len(want)`, so every leaf declared inflates
    the denominator for EVERY vintage. Unioning across the family instead cost 3
    entries outright — a 2Q26 half-year variant's extra leaves pushed the 4Q25
    table under the bar — and turned 6 partial matches into 49. Same document,
    every table of this type: that captures the p18-21 case without asking one
    vintage to print another vintage's rows.
    """
    best = tabs[0]
    same = [t for t in tabs if t["doc_id"] == best["doc_id"]]
    fam = _family_label(RCL.doc_source_family(best["doc_id"], bank))
    cur = con.cursor()

    # AND ONLY WHEN THE UNION STILL LOCATES. `locate_tables` requires a candidate
    # to match `MIN_MATCH_FRACTION` (0.5) of everything the entry declares, so
    # every leaf added raises the bar for every table. Where one document prints
    # the type as several DISJOINT slices — DBS's changes-in-equity (audited FY
    # and unaudited half-year, Group 2024 and 2025) and OCBC's Level 3
    # roll-forward (financial assets, liabilities, non-financial) — the union
    # declares 23 and 22 leaves that no single table can half-match, and the
    # type stops resolving at all. Measured: 2 entries lost outright.
    #
    # So union, then check against the resolver's own constants, and fall back
    # to the richest table alone when the union would not survive them.
    sets = {}
    for t in same:
        rows = RCL._load_rows(cur, t["doc_id"], t["table_id"])
        D.classify(rows, D.normalize_caption(t["title"]))
        D.build_ancestry(rows)
        sets[t["table_id"]] = {
            _curated_of_which(D.leaf_id(r.ancestors, r.identity_label or r.label))
            for r in rows if r.cls in (D.DATA, D.PERIOD_ROW)
            and (r.identity_label or r.label)}
    union = set().union(*sets.values()) if sets else set()
    survives = union and max(
        (len(s) for s in sets.values()), default=0
    ) >= max(RCL.MIN_MATCH_ABSOLUTE, RCL.MIN_MATCH_FRACTION * len(union))
    if not survives:
        same = [best]

    out, seen = [], set()
    for t in same:
        rows = RCL._load_rows(cur, t["doc_id"], t["table_id"])
        D.classify(rows, D.normalize_caption(t["title"]))
        D.build_ancestry(rows)
        for r in rows:
            if r.cls not in (D.DATA, D.PERIOD_ROW):
                continue
            own = r.identity_label or r.label
            if not own:
                continue
            leaf = _curated_of_which(D.leaf_id(r.ancestors, own))
            if not leaf or leaf in seen:
                continue                   # ids are unique within a table type
            seen.add(leaf)
            flags = []
            if t is not best:
                flags.append(f"from a second table of this type: {t['table_id']}")
            if parents := _date_parents(r):
                flags.append("DATE PARENT: " + " / ".join(parents)
                             + " is a balance line, not a heading — its movements"
                               " are siblings; drop the segment when curating")
            out.append(dict(
                bank=bank, canonical_section=t["section_title"] or "",
                section_ordinal=0, table_type_id=ttid, table_ordinal=1,
                line_ordinal=len(out) + 1, canonical_leaf_id=leaf, label=own,
                full_path=RCL._printed_chain(r), source_family=fam,
                notes="; ".join(flags)))

    others = sorted({t["doc_id"] for t in tabs if t["doc_id"] != best["doc_id"]})
    if out and others:
        # Printed in other documents too. NOT merged — recorded, so the curator
        # can see which vintages to diff against before adding leaves by hand.
        out[0]["notes"] = "; ".join(filter(None, [
            out[0]["notes"], "also printed in: " + ", ".join(others)]))
    return out


def propose_cols(con, bank, ttid, tabs, reg) -> list[dict]:
    """Draft column entries for exhibits whose COLUMNS carry identity."""
    meta = reg.get(ttid, {})
    dim = meta.get("dim_hint")
    if not dim:
        if meta.get("legal_entity_axis"):
            dim = "legal_entity"
        elif meta.get("statement_class") == "equity":
            # A statement of changes in equity decomposes across the page into
            # equity COMPONENTS — DBS prints Share Capital / Other equity
            # instruments / Other reserves / Revenue reserves / Total
            # Shareholders' funds / Non-controlling interests / Total equity.
            # That is an identity axis with no dim table behind it yet, so the
            # keys come out UNRESOLVED for hand-mapping rather than being lost.
            dim = "equity_component"
        else:
            return []                      # columns are periods, not identities
    best = tabs[0]
    fam = _family_label(RCL.doc_source_family(best["doc_id"], bank))
    lut = _dim_lookup(con, dim)

    # IDENTITY LIVES ON THE SPANNING HEADER, NOT THE LEAF. UOB's geography
    # exhibit prints 'Singapore' / 'Malaysia' / ... at col_hierarchy=0 and a
    # bare '$m' under each; its balance sheet prints 'The Group' / 'The Bank'
    # over 'Dec-25 $m' / 'Dec-24 $m'. The leaves are units and periods — the
    # parents carry the dimension. UOB_masterlist_cols.csv is authored off the
    # parents, so this reads them too. Flat axes (no level-0 header) fall back
    # to the leaves.
    q = ("SELECT col_id, col_hierarchy, col_leaf_label, col_leaf_label_clean, "
         "       col_period, geo_key, segment_key, industry_key, legal_entity "
         "FROM col_dim WHERE doc_id=? AND table_id=? ORDER BY col_hierarchy, col_id")
    rows = con.execute(q, (best["doc_id"], best["table_id"])).fetchall()

    # IDENTITY IS NOT AT A FIXED LEVEL. Three shapes in this corpus:
    #   geography   leaf '$m'          under 'Singapore'      -> parent
    #   balance     leaf 'Dec-25 $m'   under 'The Group'      -> parent
    #   equity      leaf 'Share Capital' under a span header  -> the leaf
    #   segment     leaf 'GR $m', flat, no header             -> the leaf
    # So walk UP from each leaf to the nearest ancestor whose label is neither a
    # period nor a bare unit. A fixed "level 0 headers" rule got equity wrong:
    # DBS spans only columns 1-5 with 'Attributable to shareholders of the
    # Company' and leaves 'Non-controlling interests' / 'Total equity' at the
    # top level, so level 0 is one span, not the seven components.
    by_id = {r[0]: r for r in rows}
    parent = dict(con.execute(
        "SELECT col_id, col_parent FROM col_dim WHERE doc_id=? AND table_id=?",
        (best["doc_id"], best["table_id"])))
    children = {p for p in parent.values() if p is not None}

    src, chosen = [], set()
    for r in rows:
        if r[0] in children:
            continue                       # not a leaf; reached via its children
        node = r
        while node is not None:
            lab = D.strip_footnote_markers((node[3] or node[2] or "")).strip()
            if lab and not D.is_period_label(lab):
                break
            node = by_id.get(parent.get(node[0]))
        if node is not None and node[0] not in chosen:
            chosen.add(node[0])
            src.append(node)

    # `dim_hint` says the exhibit decomposes along an axis; it does NOT say the
    # axis is the columns. UOB's FS_PERF_BY_GEOGRAPHY puts geography in COLUMNS
    # (14 columns carry geo_key), its FS_NPA_BY_GEOGRAPHY puts geography in ROWS
    # (6 rows carry geo_key) and spends the columns on measures — NPL/NPA $m,
    # NPL ratio %. Emitting the latter would mint geography ids out of measure
    # headers.
    #
    # The ingest already answers which axis it is: whichever one it stamped the
    # dimension key on. Ask the data, not the caption.
    keycol = {"geo": "geo_key", "segment": "segment_key",
              "industry": "industry_key"}.get(dim)
    if keycol:
        on_rows = con.execute(
            f"SELECT count(*) FROM row_dim WHERE doc_id=? AND table_id=? "
            f"AND {keycol} IS NOT NULL", (best["doc_id"], best["table_id"])
        ).fetchone()[0]
        on_cols = con.execute(
            f"SELECT count(*) FROM col_dim WHERE doc_id=? AND table_id=? "
            f"AND {keycol} IS NOT NULL", (best["doc_id"], best["table_id"])
        ).fetchone()[0]
        if on_rows and not on_cols:
            return []                      # decomposed down the page, not across

    # `statement_class: equity` is broader than "columns are equity components":
    # FS_DIVIDENDS and FS_SHARE_CAPITAL share the class but column by period
    # ('Half year ended 31 Dec') or legal entity ('The Group'). Require that at
    # least one column actually resolves in equity_component_map — same
    # ask-the-data test as the row/column axis discriminator above.
    if dim == "equity_component" and not any(
            lut.get(D.strip_footnote_markers((r[3] or r[2] or "")).strip().casefold())
            for r in src):
        return []

    out, seen = [], set()
    for _cid, _lvl, lbl, lbl_clean, period, geo, seg, ind, le in src:
        # Units belong to the cell, not the column's identity: UOB prints
        # 'GR $m' / 'Others $m' where the segment is GR / Others.
        label = D.strip_footnote_markers((lbl_clean or lbl or "")).strip()
        if not label or label.casefold() in seen:
            continue
        # A header that is only a period carries no identity — skip rather than
        # mint a column id out of a date.
        if D.is_period_label(label) or (period and not any((geo, seg, ind, le))):
            continue
        seen.add(label.casefold())
        key = ({"geo": geo, "segment": seg, "industry": ind,
                "legal_entity": le}.get(dim)
               or lut.get(label.casefold(), ""))
        out.append(dict(
            bank=bank, table_type_id=ttid, col_ordinal=len(out) + 1,
            canonical_col_id=key or D.normalize_segment(label).upper(),
            label=label, full_path=label, dim=dim, dim_key=key,
            source_family=fam,
            notes="" if key else "dim_key UNRESOLVED — map by hand"))
    return out


def _dim_lookup(con, dim) -> dict[str, str]:
    """printed label (casefolded) -> dim key.

    Prefers the `*_map` alias table where one exists — those carry the verbatim
    house names each bank prints ('gr' -> SEG_RETAIL, 'revenue reserves' ->
    EQ_RETAINED_EARNINGS), which is what a column header actually says. Falls
    back to the `*_dim` canonical label.
    """
    # "geo" removed 2026-08-12 with geography stamping (geo_map dropped from the schema).
    spec = {"segment": ("segment_dim", "segment_key", "label", "segment_map"),
            "industry": ("industry_dim", "industry_key", "industry_name", "industry_map"),
            "equity_component": ("equity_component_dim", "equity_key", "label",
                                 "equity_component_map")}.get(dim)
    if not spec:
        return {}
    table, keycol, labcol, maptable = spec
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    out = {}
    if table in have:
        out.update({str(lab).casefold(): k
                    for k, lab in con.execute(f"SELECT {keycol}, {labcol} FROM {table}")})
    if maptable in have:
        out.update({str(lab).casefold(): k for lab, k in con.execute(
            f"SELECT label_norm, {keycol} FROM {maptable}")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--axis", choices=["rows", "cols", "both"], default="both")
    ap.add_argument("--banks", default=None, help="comma-separated, default all")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).resolve()
    if out_dir == MASTERLIST.resolve() or MASTERLIST.resolve() in out_dir.parents:
        sys.exit(f"refusing to write into the curated masterlist dir: {out_dir}\n"
                 "This script emits PROPOSALS only; curation is manual.")
    out_dir.mkdir(parents=True, exist_ok=True)

    banks = [b.strip() for b in args.banks.split(",")] if args.banks else None
    con = sqlite3.connect(args.db)
    _seed_registry_into(con)

    import yaml
    reg = {t["id"]: t for t in yaml.safe_load(
        (REPO / "findociq/pipeline/stage3_stamp/masterlist/table_registry.yaml").read_text())["types"]}

    covered, wanted, by_type = load_covered(), load_wanted(banks), tables_by_type(con)
    skipped: list[str] = []
    totals: dict[str, tuple[int, int]] = {}

    for bank in sorted(wanted):
        rows_out, cols_out = [], []
        for ttid in sorted(wanted[bank]):
            if (bank, ttid) in covered:
                skipped.append(f"{bank}/{ttid}: already curated")
                continue
            if ttid in NARRATIVE:
                skipped.append(f"{bank}/{ttid}: narrative, no addressable values")
                continue
            tabs = by_type.get((bank, ttid))
            if not tabs:
                skipped.append(f"{bank}/{ttid}: no table resolved in this DB")
                continue
            if args.axis in ("rows", "both"):
                rows_out.extend(propose_rows(con, bank, ttid, tabs))
            if args.axis in ("cols", "both"):
                cols_out.extend(propose_cols(con, bank, ttid, tabs, reg))

        # section/table ordinals: page order within the bank, assigned after all
        # types are collected so they read like the printed document
        _number(rows_out, by_type, bank)
        if args.axis in ("rows", "both") and rows_out:
            _write(out_dir / f"{bank}_masterlist.csv", ROW_COLS, rows_out)
        if args.axis in ("cols", "both") and cols_out:
            _write(out_dir / f"{bank}_masterlist_cols.csv", COL_COLS, cols_out)
        totals[bank] = (len(rows_out), len(cols_out))

    print(f"proposals -> {out_dir}")
    for bank, (nr, nc) in sorted(totals.items()):
        print(f"  {bank:<5} {nr:>5} leaves   {nc:>4} columns")
    print(f"  {'TOTAL':<5} {sum(v[0] for v in totals.values()):>5} leaves "
          f"  {sum(v[1] for v in totals.values()):>4} columns")
    if skipped:
        print(f"\nskipped {len(skipped)}:")
        for s in skipped:
            print(f"   {s}")
    con.close()
    return 0


def _number(rows_out, by_type, bank) -> None:
    """section_ordinal by printed page order; table_ordinal within a section."""
    page = {}
    for (b, ttid), tabs in by_type.items():
        if b == bank:
            page[ttid] = _page(tabs[0])
    sections, per_section = {}, defaultdict(dict)
    for r in sorted(rows_out, key=lambda r: page.get(r["table_type_id"], 9999)):
        sec = r["canonical_section"]
        sections.setdefault(sec, len(sections) + 1)
        per_section[sec].setdefault(r["table_type_id"], len(per_section[sec]) + 1)
    for r in rows_out:
        r["section_ordinal"] = sections[r["canonical_section"]]
        r["table_ordinal"] = per_section[r["canonical_section"]][r["table_type_id"]]


def _write(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _seed_registry_into(con: sqlite3.Connection) -> None:
    """Build `table_registry_alias` in the session only.

    `resolve_table_type` reads SQL, and neither DB has the registry tables, so
    load the YAML into TEMP tables. Nothing on disk is touched — this script is
    read-only against the corpus.
    """
    import yaml
    con.execute("CREATE TEMP TABLE table_registry_alias ("
                "alias_norm TEXT, bank TEXT, table_type_id TEXT, "
                "PRIMARY KEY (alias_norm, bank))")
    doc = yaml.safe_load(
        (REPO / "findociq/pipeline/stage3_stamp/masterlist/table_registry.yaml").read_text())
    for t in doc["types"]:
        for a in t.get("aliases", []):
            alias, bank = ((a["alias"], a.get("bank", "*"))
                           if isinstance(a, dict) else (a, "*"))
            con.execute("INSERT OR REPLACE INTO table_registry_alias VALUES (?,?,?)",
                        (alias, bank, t["id"]))


if __name__ == "__main__":
    raise SystemExit(main())
