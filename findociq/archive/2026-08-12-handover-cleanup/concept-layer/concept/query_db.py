"""concept.query_db — the parameterised pull TOOL for the FS query layer.

A clean function + CLI the router / analyst / demo calls to extract values from a
compiled FS DB (schema_v7). Deterministic, zero API. Reads v_cell_flat and joins
the concept dictionary for human names.

    pull(db, concept_keys=None, institutions=None, spans=None, periods=None,
         segment=None, geo=None, dimension=None, tables=None, prefer_table=None)
      -> list[dict]

Each returned row:
    {institution, concept_key, concept_name, period, period_span,
     segment_key, geo_key, value_num, unit, doc_id, table_id, row_label}

DIMENSION
  dimension='geo'     -> the geography breakdown: only non-default geo members
                         (geo_key != 'GLOBAL').
  dimension='segment' -> the segment breakdown: only non-default segment members
                         (segment_key != 'SEG_TOTAL').
  otherwise           -> the whole-bank slice (segment SEG_TOTAL, geo GLOBAL),
                         unless `segment` / `geo` are given explicitly.

DEDUP
  The same concept-value often appears in several tables (income statement +
  highlights + segment). By default duplicates collapse by
  (institution, concept, period, span, segment, geo, unit) to one row. When the
  collapsed rows DISAGREE on value_num the kept row is FLAGGED (row['conflict']
  = True, row['conflict_values'] = [...]) — surfaced, never hidden. `prefer_table`
  is a substring hint: among duplicates a table_type/table_id containing it wins.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.normalize import norm  # noqa: E402
from concept.resolve_deterministic import skip_reason  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DB = _ROOT / "findociq" / "db" / "compiled_fs.db"
_DICT = Path(__file__).resolve().parent / "concept_dictionary.yaml"

# short institution aliases -> a LIKE fragment matching the DB's full legal name
_INST_ALIAS = {
    "UOB": "United Overseas",
    "DBS": "DBS",
    "OCBC": "Oversea-Chinese",
}

_FIELDS = ["institution", "concept_key", "concept_name", "period", "period_span",
           "segment_key", "geo_key", "value_num", "unit", "doc_id", "table_id",
           "row_label"]


def _name_map(dict_path: Path = _DICT) -> dict[str, str]:
    import yaml
    doc = yaml.safe_load(dict_path.read_text())
    return {c["key"]: c.get("name", "") for c in doc.get("concepts", [])}


def _concept_needles(dict_path: Path = _DICT) -> dict[str, set[str]]:
    """concept_key -> set of normalised needles (name + aliases) for the label
    fallback's fuzzy match."""
    import yaml
    doc = yaml.safe_load(dict_path.read_text())
    out: dict[str, set[str]] = {}
    for c in doc.get("concepts", []):
        needles = set()
        for txt in [c.get("name", "")] + list(c.get("aliases", []) or []):
            n = norm(txt)
            if n:
                needles.add(n)
        out[c["key"]] = needles
    return out


def _fuzzy(row_norm: str, needles: set[str]) -> bool:
    """Order-independent token match of a normalised row label against a concept's
    needles. True when the row is one of the needles, one is a token-subset of the
    other (smaller side >=2 tokens, so bare 'income'/'total' can't over-match), or
    token-Jaccard >= 0.6."""
    rt = set(row_norm.split())
    if not rt:
        return False
    for nd in needles:
        if not nd:
            continue
        if row_norm == nd:
            return True
        ndt = set(nd.split())
        if ndt <= rt and len(ndt) >= 2:
            return True
        if rt <= ndt and len(rt) >= 2:
            return True
        inter = rt & ndt
        if inter and len(inter) / len(rt | ndt) >= 0.6:
            return True
    return False


def _inst_like(arg: str) -> str:
    """A LIKE pattern for an institution filter token (alias-aware)."""
    return f"%{_INST_ALIAS.get(arg.upper(), arg)}%"


def _in_clause(col: str, values, params: dict, prefix: str) -> str:
    """Build a portable 'col IN (:p0,:p1,...)' with named params."""
    keys = []
    for i, v in enumerate(values):
        k = f"{prefix}{i}"
        params[k] = v
        keys.append(f":{k}")
    return f"{col} IN ({','.join(keys)})"


def pull(db: str | Path = _DB, *, concept_keys=None, institutions=None,
         spans=None, periods=None, segment=None, geo=None, dimension=None,
         tables=None, prefer_table=None, fallback_label=False) -> list[dict]:
    names = _name_map()
    where = ["v.value_num IS NOT NULL", "v.cell_state = 'reported'"]
    params: dict = {}

    if concept_keys:
        where.append(_in_clause("v.concept_key", list(concept_keys), params, "ck"))
    if spans:
        where.append(_in_clause("v.period_span", list(spans), params, "sp"))
    if periods:
        where.append(_in_clause("v.period", [str(p) for p in periods], params, "pd"))
    if institutions:
        ors = []
        for i, inst in enumerate(institutions):
            k = f"inst{i}"
            params[k] = _inst_like(inst)
            ors.append(f"v.institution LIKE :{k}")
        where.append("(" + " OR ".join(ors) + ")")
    if tables:
        ors = []
        for i, tbl in enumerate(tables):
            k = f"tbl{i}"
            params[k] = f"%{tbl}%"
            ors.append(f"(v.table_type LIKE :{k} OR v.table_id LIKE :{k})")
        where.append("(" + " OR ".join(ors) + ")")

    # dimension / default-slice handling
    if dimension == "geo":
        where.append("v.geo_key <> 'GLOBAL'")
    elif segment is None and geo is None and dimension != "segment":
        # whole-bank default on the geo axis unless a breakdown was requested
        where.append("v.geo_key = COALESCE(:geo_default, v.geo_key)")
        params["geo_default"] = "GLOBAL"
    if geo is not None:
        params["geo"] = geo
        where.append("v.geo_key = :geo")

    if dimension == "segment":
        where.append("v.segment_key <> 'SEG_TOTAL'")
    elif segment is None and geo is None and dimension != "geo":
        where.append("v.segment_key = COALESCE(:seg_default, v.segment_key)")
        params["seg_default"] = "SEG_TOTAL"
    if segment is not None:
        params["segment"] = segment
        where.append("v.segment_key = :segment")

    sql = f"""
        SELECT v.institution, v.concept_key, v.period, v.period_span,
               v.segment_key, v.geo_key, v.value_num, v.unit,
               v.doc_id, v.table_id, v.table_type, r.row_leaf_label AS row_label
        FROM v_cell_flat v
        JOIN row_dim r
          ON r.doc_id = v.doc_id AND r.table_id = v.table_id AND r.row_id = v.row_id
        WHERE {' AND '.join(where)}
        ORDER BY v.institution, v.concept_key, v.period, v.period_span,
                 v.segment_key, v.geo_key, v.table_id
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        raw = [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

    for r in raw:
        r["concept_name"] = names.get(r["concept_key"], "") if r["concept_key"] else ""

    rows = _dedup(raw, prefer_table)
    if not fallback_label:
        return rows

    for r in rows:
        r["via"] = "concept"
    # Safety net: only when the concept pull came back empty for the requested
    # (institution, span/period) do we scan row labels for a fuzzy concept match.
    # Explicit + opt-in so default pulls stay concept-clean.
    if concept_keys and not rows:
        rows = _label_fallback(db, concept_keys=concept_keys,
                               institutions=institutions, spans=spans,
                               periods=periods, segment=segment, geo=geo,
                               dimension=dimension, tables=tables,
                               prefer_table=prefer_table, names=names)
    return rows


def _label_fallback(db, *, concept_keys, institutions, spans, periods, segment,
                    geo, dimension, tables, prefer_table, names) -> list[dict]:
    """Fuzzy-match row_leaf_label against each requested concept's dictionary
    name/aliases and return the value-bearing rows tagged via='label_fallback'.
    Reuses pull()'s SAME slice filters but WITHOUT the concept filter."""
    needles = _concept_needles()
    where = ["v.value_num IS NOT NULL", "v.cell_state = 'reported'"]
    params: dict = {}
    if spans:
        where.append(_in_clause("v.period_span", list(spans), params, "sp"))
    if periods:
        where.append(_in_clause("v.period", [str(p) for p in periods], params, "pd"))
    if institutions:
        ors = []
        for i, inst in enumerate(institutions):
            k = f"inst{i}"
            params[k] = _inst_like(inst)
            ors.append(f"v.institution LIKE :{k}")
        where.append("(" + " OR ".join(ors) + ")")
    if tables:
        ors = []
        for i, tbl in enumerate(tables):
            k = f"tbl{i}"
            params[k] = f"%{tbl}%"
            ors.append(f"(v.table_type LIKE :{k} OR v.table_id LIKE :{k})")
        where.append("(" + " OR ".join(ors) + ")")
    # mirror pull()'s default whole-bank slice (or the requested breakdown/member)
    if dimension == "geo":
        where.append("v.geo_key <> 'GLOBAL'")
    elif segment is None and geo is None and dimension != "segment":
        where.append("v.geo_key = 'GLOBAL'")
    if geo is not None:
        params["geo"] = geo
        where.append("v.geo_key = :geo")
    if dimension == "segment":
        where.append("v.segment_key <> 'SEG_TOTAL'")
    elif segment is None and geo is None and dimension != "geo":
        where.append("v.segment_key = 'SEG_TOTAL'")
    if segment is not None:
        params["segment"] = segment
        where.append("v.segment_key = :segment")

    sql = f"""
        SELECT v.institution, v.period, v.period_span,
               v.segment_key, v.geo_key, v.value_num, v.unit,
               v.doc_id, v.table_id, v.table_type, r.row_leaf_label AS row_label
        FROM v_cell_flat v
        JOIN row_dim r
          ON r.doc_id = v.doc_id AND r.table_id = v.table_id AND r.row_id = v.row_id
        WHERE {' AND '.join(where)}
        ORDER BY v.institution, v.period, v.period_span, v.table_id
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cand = [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()

    out: list[dict] = []
    for ck in concept_keys:
        nd = needles.get(ck, set())
        if not nd:
            continue
        matched = []
        for r in cand:
            label = r["row_label"]
            nl = norm(label)
            if skip_reason(label, nl):          # skip dates/notes/no-alpha rows
                continue
            if _fuzzy(nl, nd):
                m = dict(r)
                m["concept_key"] = ck            # the concept we were searching for
                matched.append(m)
        for m in matched:
            m["concept_name"] = names.get(ck, "")
        for m in _dedup(matched, prefer_table):
            m["via"] = "label_fallback"
            out.append(m)
    return out


def _dedup(rows: list[dict], prefer_table: str | None) -> list[dict]:
    """Collapse duplicate (institution, concept, period, span, segment, geo, unit)
    rows to one; flag value disagreement instead of hiding it."""
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        k = (r["institution"], r["concept_key"], r["period"], r["period_span"],
             r["segment_key"], r["geo_key"], r["unit"])
        groups[k].append(r)

    out: list[dict] = []
    for k, members in groups.items():
        if prefer_table:
            pref = [m for m in members
                    if prefer_table in (m["table_type"] or "")
                    or prefer_table in (m["table_id"] or "")]
            members = pref or members
        chosen = members[0]
        vals = sorted({round(m["value_num"], 6) for m in members})
        conflict = len(vals) > 1
        row = {f: chosen.get(f) for f in _FIELDS}
        row["conflict"] = conflict
        if conflict:
            row["conflict_values"] = vals
        out.append(row)
    out.sort(key=lambda r: (r["institution"], r["concept_key"] or "", str(r["period"]),
                            r["period_span"] or "", r["segment_key"], r["geo_key"]))
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_table(rows: list[dict]) -> None:
    cols = ["institution", "concept_key", "period", "period_span", "segment_key",
            "geo_key", "value_num", "unit", "table_type", "row_label"]
    if any("via" in r for r in rows):
        cols.insert(2, "via")
    _rename = {"institution": "inst", "concept_key": "concept", "period_span": "span",
               "segment_key": "segment", "geo_key": "geo", "value_num": "value",
               "table_type": "table", "row_label": "row"}
    hdr = {c: _rename.get(c, c) for c in cols}
    short = {"United Overseas Bank Ltd": "UOB", "DBS Group Holdings Ltd": "DBS",
             "Oversea-Chinese Banking Corporation Ltd": "OCBC"}
    disp = []
    for r in rows:
        d = {c: r.get(c) for c in cols}
        d["institution"] = short.get(d["institution"], d["institution"])
        d["value_num"] = "" if d["value_num"] is None else f"{d['value_num']:g}"
        for c in cols:
            s = "" if d[c] is None else str(d[c])
            d[c] = (s[:34] + "…") if len(s) > 35 else s
        if r.get("conflict"):
            d["value_num"] += f"  !CONFLICT{r.get('conflict_values')}"
        disp.append(d)
    widths = {c: max(len(hdr[c]), *(len(row[c]) for row in disp)) if disp else len(hdr[c])
              for c in cols}
    line = "  ".join(hdr[c].ljust(widths[c]) for c in cols)
    print(line)
    print("-" * len(line))
    for row in disp:
        print("  ".join(row[c].ljust(widths[c]) for c in cols))
    print(f"\n[{len(rows)} rows]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Parameterised pull from a compiled FS DB.")
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--concept", action="append", dest="concepts",
                    help="concept_key (repeatable)")
    ap.add_argument("--institution", action="append", dest="institutions",
                    help="institution / alias (UOB, DBS, OCBC); repeatable")
    ap.add_argument("--span", action="append", dest="spans",
                    help="period_span (FY,1Q..4Q,1H,2H,9M,as_at); repeatable")
    ap.add_argument("--period", action="append", dest="periods",
                    help="period end date YYYY-MM-DD; repeatable")
    ap.add_argument("--segment", help="explicit segment_key filter")
    ap.add_argument("--geo", help="explicit geo_key filter")
    ap.add_argument("--dimension", choices=["geo", "segment"],
                    help="return the breakdown (non-default members) on this axis")
    ap.add_argument("--table", action="append", dest="tables",
                    help="restrict to table_type/table_id substring; repeatable")
    ap.add_argument("--prefer-table", help="dedup tie-break: prefer this table substring")
    ap.add_argument("--fallback", action="store_true",
                    help="if a concept pull is empty, fuzzy-match row labels to the "
                         "concept's dictionary name/aliases (rows tagged label_fallback)")
    args = ap.parse_args()

    rows = pull(args.db, concept_keys=args.concepts, institutions=args.institutions,
                spans=args.spans, periods=args.periods, segment=args.segment,
                geo=args.geo, dimension=args.dimension, tables=args.tables,
                prefer_table=args.prefer_table, fallback_label=args.fallback)
    _print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
