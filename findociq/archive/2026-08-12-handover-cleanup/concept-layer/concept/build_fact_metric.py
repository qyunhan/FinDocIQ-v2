"""concept.build_fact_metric — build the canonical, chart-ready analytics table.

Collapses the many raw v_cell_flat rows that carry the SAME economic concept
(income statement + highlights + segment/geo tables + sign variants + rounding
twins + %-change cells + note-column mis-stamps) into ONE canonical value per

    (institution, concept_key, period, period_span, segment_key, geo_key,
     industry_key, legal_entity)

and materialises it as table `fact_metric` in the compiled FS DB. `legal_entity`
(CONSOLIDATED / PARENT_COMPANY / BANK_SOLO, from cell_fact.legal_entity — see
migrate_add_legal_entity.py) is now part of the grain: the SAME table's Group
and Bank/Company columns (e.g. UOB total assets 572,061 Group vs 485,263 Bank)
are legitimately different economic entities, not duplicate reports of the same
number, so they must NOT collapse into one row / conflict. Callers that want the
one canonical dashboard number should read the `v_fact_metric_serving` view
(CONSOLIDATED only), not this base table directly.

Everything it could NOT cleanly resolve (>1 genuinely-different value
surviving inside one group) is written to a data-quality punch-list CSV so a
human can fix the stamp.

Deterministic, zero API. Reuses the resolution philosophy of concept.query_db
(dedup by the analytic key, prefer-table tie-break, surface — never hide —
disagreement) and lifts it into a persisted, sign/twin-normalised fact table.

  python -m concept.build_fact_metric [--db PATH] [--dry-run]

=================================  RULES  =====================================
LEVEL-ROW FILTER (what feeds canonicalization). A raw v_cell_flat row is a
"level" (a reported magnitude, not a growth/derived cell) iff ALL hold:
  * value_num IS NOT NULL and cell_state='reported';
  * concept_key is known in the dictionary;
  * its column is NOT a change/variance banner and NOT a note column
    (see _is_change_col / _is_note_col) and its table is not a change table
    (volume_and_rate_analysis);
  * its table is GROUP-consolidated basis — bank/company-only statements
    (table_type contains the token 'company') are dropped (dictionary basis is
    ALWAYS group_consolidated);
  * unit is consistent with the concept's dictionary unit family:
      - currency concept  -> cell unit is NOT '%'  (a '%' cell on a currency
        concept is a period-over-period change, e.g. NII +/(-)%, EXCLUDED);
      - percent/bps concept (ratio.* / reg.*_ratio / credit_cost_bps) -> cell
        unit IS '%', UNLESS the cell's TABLE does not declare column units
        (see UNIT PROMOTION below), in which case the dictionary unit is
        trusted instead.

  UNIT PROMOTION (percent-family cells whose stamped unit isn't '%'). Some
  tables (UOB performance-highlights, capital_adequacy, financial_highlights*)
  print ratio rows with NO column unit of their own, so the loader falls back
  to the table/document default currency unit ('S$m' or NULL) for every cell
  in the table, ratio rows included — even though the printed value is a
  percent. Naively trusting the concept dictionary for ANY mismatched unit is
  unsafe, though: in `average_balance_sheet` exhibits the row "Net interest
  income/margin" is stamped `ratio.nim`, but that table DOES declare column
  units ("Average rate (%)" vs "Interest ($m)"), and the SAME row has real
  CURRENCY cells (14,500 / 7,099) under the "Interest ($m)" column — those
  must stay excluded, not get promoted to a NIM of 14,500%. So the tie-break
  is structural, not per-bank/per-document: a table is "column-unit-declaring"
  iff at least one of its col_dim rows has a non-null unit ON A NON-CHANGE
  COLUMN (see _is_change_label). The non-change carve-out matters: UOB's
  `financial_highlights` stamps unit='%' on its own '+/(-) %' change columns
  (a real percent-change, correctly '%'), while its VALUE columns ('2025',
  '2024', ...) all have unit=NULL — that '%' on the change column says
  nothing about the value columns' unit and must not itself make the table
  look column-unit-declaring. When the table IS column-unit-declaring, the
  column is authoritative and the existing unit != '%' -> excluded rule
  stands. When the table is NOT column-unit-declaring, the table-default
  unit carries no row-level information, so a percent-family cell is
  PROMOTED: kept, with its unit set to the concept's dictionary unit ('%'
  for percent concepts, 'bps' for bps concepts). Promoted rows are marked
  unit_promoted=True upstream and surface in `fact_metric.unit_source` as
  'dictionary_promoted' (vs 'as_loaded').

CANONICALIZATION per group:
  1. Cluster the candidate values by ABSOLUTE magnitude with a 1.0 tolerance.
     This merges (a) rounding twins (10,933 vs 10,934) and (b) sign variants
     (+2,042 in the P&L vs -2,042 as a deduction in a segment waterfall) into
     ONE magnitude cluster. Distinct clusters = genuinely different numbers.
  2. SIGN normalization (store the ECONOMIC value):
       - magnitude concepts (pnl.opex.*, pnl.provisions.*, pnl.tax) are stored
         as the POSITIVE magnitude (+|v|) — these are one-signed costs; their
         negative appearance is only their position in a parent waterfall.
       - every other concept is stored with the sign of the value taken from
         the highest-priority (preferred-tier) table in the cluster, i.e. the
         primary financial statement's printed sign.
  3. RESOLUTION when >1 magnitude cluster survives (a real conflict):
       - give each cluster its best (lowest) table tier; tiers are
         primary-statement(0) > highlights(1) > supplementary(2) >
         segment/geo(3) > other(4);
       - if exactly one cluster owns the strictly-best tier, take it
         (resolved_by='prefer_table');
       - otherwise the disagreement is inside one authority tier: emit the
         group to the punch-list, and still stamp a deterministic representative
         (best tier, then most-supported value, then smallest) so the table has
         a row (resolved_by='conflict').
  4. Provenance: the chosen representative row's doc_id/table_id/row_label,
     n_candidates (raw level rows collapsed), and resolved_by (the rule).

Tiers use TABLE_TIERS substring patterns on table_type — a general signal, not a
per-bank/per-document rule.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import canonical_unit  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DB = _ROOT / "findociq" / "db" / "compiled_fs.db"
_DICT = Path(__file__).resolve().parent / "concept_dictionary.yaml"
_CONFLICTS = _ROOT / "findociq" / "data" / "derived" / "fact_metric_conflicts.csv"

# concepts stored as a positive magnitude (their negative appearance is only a
# waterfall position, not the concept's own economic sign)
_MAGNITUDE_PREFIXES = ("pnl.opex", "pnl.provisions", "pnl.tax")

# table_type substring -> tier (lower = more authoritative). First match wins.
TABLE_TIERS: list[tuple[int, tuple[str, ...]]] = [
    (0, ("income_statement", "consolidated_income", "comprehensive_income",
         "balance_sheet", "statement_of_financial_position", "cash_flow",
         "statement_of_changes_in_equity")),
    (1, ("financial_highlights", "financial_performance", "performance_summary",
         "performance_highlights", "highlights", "selected_")),
    (2, ("net_interest_income", "operating_expenses", "allowance_for_credit",
         "allowance_for_")),
    (3, ("business_segment", "performance_by_business", "geographical_segment",
         "performance_by_geograph", "_segment", "segment")),
]
_WORST_TIER = 9
_ROUND_TOL = 1.0

# normalised column banners that mark a change/variance cell (never a level)
_CHANGE_NEEDLES = ("+/(-)", "yoy", "qoq", "chg", "change", "variance", "growth",
                   " vs ", "vs ")
_CHANGE_TABLE_NEEDLES = ("volume_and_rate",)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _is_change_label(label: str | None) -> bool:
    n = _norm(label)
    return bool(n) and any(k in n for k in _CHANGE_NEEDLES)


def _is_change_col(col_lvl1: str | None, col_lvl2: str | None,
                   table_type: str | None) -> bool:
    tt = _norm(table_type)
    if any(n in tt for n in _CHANGE_TABLE_NEEDLES):
        return True
    return _is_change_label(col_lvl1) or _is_change_label(col_lvl2)


def _is_cash_flow_table(table_type: str | None, statement_class: str | None) -> bool:
    """Is this exhibit a CASH FLOW STATEMENT?

    Two independent signals, unioned, neither per-bank (same pattern as the
    dimensional-breakdown scope): the registry's DECLARED `statement_class`, and
    the raw table_type slug for exhibits the registry has not classified.
    """
    return (_norm(statement_class) == "cash_flow"
            or "cash_flow" in _norm(table_type)
            or "cashflow" in _norm(table_type))


def _is_stock_from_cash_flow(meta: dict, table_type: str | None,
                             statement_class: str | None) -> bool:
    """A `nature='stock'` concept must never take its level from a cash flow
    statement.

    The cash flow statement reports the MOVEMENT in a balance over the period,
    never the balance itself -- 'Loans and advances to customers' there is the
    net lending during the year (DBS FY25: -23,317), not the closing loan book
    (445,011). It is the row/table analogue of `_is_change_col`, which already
    refuses a '+/(-) %' COLUMN as a level, and it is an accounting invariant
    (IAS-7 presents movements), so it needs no per-bank label list and holds for
    a statement layout we have never seen.

    Scoped to stock concepts on purpose: a `flow` concept legitimately IS
    reported in the cash flow statement, so this must not become a blanket
    exclusion of the exhibit.
    """
    return meta.get("nature") == "stock" and _is_cash_flow_table(table_type, statement_class)


def _is_note_col(col_lvl1: str | None, col_lvl2: str | None) -> bool:
    return _norm(col_lvl1) == "note" or _norm(col_lvl2) == "note"


def _is_company_only(table_type: str | None) -> bool:
    return "company" in _norm(table_type).split("_") or _norm(table_type).endswith("company")


def _tier(table_type: str | None) -> int:
    tt = _norm(table_type)
    for tier, needles in TABLE_TIERS:
        if any(n in tt for n in needles):
            return tier
    return _WORST_TIER


def _load_dict(path: Path = _DICT) -> dict[str, dict]:
    import yaml
    doc = yaml.safe_load(path.read_text())
    out: dict[str, dict] = {}
    for c in doc.get("concepts", []):
        out[c["key"]] = {
            "name": c.get("name", ""),
            "unit": c.get("unit", ""),          # currency | percent | bps
            "nature": c.get("nature", ""),      # flow | stock | ratio_flow | ratio_point
            "thesis": ",".join(c.get("thesis", []) or []),
        }
    return out


def _is_percent_family(concept_key: str, meta: dict) -> bool:
    return meta.get("unit") in ("percent", "bps")


def _is_magnitude(concept_key: str) -> bool:
    return any(concept_key == p or concept_key.startswith(p + ".")
              for p in _MAGNITUDE_PREFIXES)


def _resolve_cell_unit(meta: dict, unit: str | None, col_unit_declaring,
                       ) -> tuple[str | None, bool] | None:
    """Decide the level-filter outcome for one cell given its concept's
    dictionary meta, its own stamped unit, and whether its TABLE has at least
    one column with a non-null col_dim.unit (col_unit_declaring, truthy).

    Returns None to DROP the cell, or (final_unit, promoted) to KEEP it —
    promoted=True iff the unit was overridden from the dictionary rather than
    taken as-loaded. See the module docstring's UNIT PROMOTION section.
    """
    if _is_percent_family("", meta):
        if unit == "%":
            # '%' identifies this as a genuine ratio LEVEL, but it is not
            # necessarily the concept's served unit: credit costs print in the
            # '%' column of a ratios block while being basis points
            # ('Credit costs (bps)', values 6-36). The declared kind decides
            # the string; the '%' only decided that the cell is a level.
            canon = canonical_unit(meta.get("unit"))
            return canon, canon != unit
        if col_unit_declaring:
            # column is authoritative in this table and did not print '%'
            # for this cell -> genuinely not a ratio level (e.g.
            # average_balance_sheet's "Interest ($m)" column).
            return None
        # table declares no column units at all -> the table/document
        # default unit carries no row-level evidence; trust the dictionary
        # ('percent' -> '%' to match as-loaded '%' cells; 'bps' -> 'bps').
        return canonical_unit(meta.get("unit")), True
    if unit == "%":                  # a % cell on a non-ratio concept = change
        return None
    canon = canonical_unit(meta.get("unit"))
    if canon is not None:
        # A CONCEPT-OWNED unit that is not the percent family: per_share. The
        # concept is a per-share amount in every filing, so a unit inherited
        # from the table's '($m)' caption ('S$m') — or no unit at all — is
        # table-default noise, not row-level evidence. Values confirm it:
        # every such cell is 0.8-29.4, per-share scale, never millions
        # ('Net asset value per ordinary share ($)', 'Basic', 'Diluted').
        # Currency concepts return None here and keep their as-loaded unit,
        # because the concrete currency belongs to the document, not the
        # dictionary.
        return canon, canon != unit
    return unit, False


def _column_unit_declaring_tables(con: sqlite3.Connection) -> set[tuple[str, str]]:
    """(doc_id, table_id) set for tables with >=1 column whose col_dim.unit is
    non-null on a NON-change column. A '+/(-) %' change/variance column is
    legitimately stamped unit='%' for ITS OWN cells (a real percent-change),
    but that says nothing about the unit of the table's VALUE columns — so it
    must not, by itself, make the table look column-unit-declaring (this is
    exactly the UOB `financial_highlights` case: value columns '2025'/'2024'
    have unit=NULL, only the '+/(-) %' columns carry unit='%')."""
    declaring: set[tuple[str, str]] = set()
    for doc_id, table_id, label, unit in con.execute(
            "SELECT doc_id, table_id, col_leaf_label, unit FROM col_dim"):
        if unit is not None and not _is_change_label(label):
            declaring.add((doc_id, table_id))
    return declaring


def _fetch_levels(db: str | Path, cdict: dict[str, dict]) -> list[dict]:
    # v_cell_flat does not expose cell_fact.legal_entity, so join cell_fact
    # directly on its PK (doc_id, table_id, row_id, col_id) to pull it in.
    sql = """
        SELECT v.institution, v.concept_key, v.period, v.period_span,
               v.period_start, v.segment_key, v.geo_key, v.industry_key, v.value_num,
               v.unit, v.cell_state, v.table_type, v.col_lvl1, v.col_lvl2,
               v.doc_id, v.table_id, r.row_leaf_label AS row_label,
               v.row_depth AS row_depth, reg.statement_class AS statement_class,
               cf.legal_entity AS legal_entity,
               -- v_cell_flat sets identity_source='human_anchor' when the row
               -- carries a concept_key_human, i.e. an address a human authored
               -- in lineage_identity_map.csv. _resolve_group treats that as
               -- authoritative -- see its docstring.
               v.identity_source AS identity_source
        FROM v_cell_flat v
        JOIN row_dim r
          ON r.doc_id = v.doc_id AND r.table_id = v.table_id AND r.row_id = v.row_id
        JOIN cell_fact cf
          ON cf.doc_id = v.doc_id AND cf.table_id = v.table_id
         AND cf.row_id = v.row_id AND cf.col_id = v.col_id
        LEFT JOIN table_t t ON t.doc_id = v.doc_id AND t.table_id = v.table_id
        LEFT JOIN table_registry reg ON reg.table_type_id = t.table_type_id
        WHERE v.value_num IS NOT NULL
          AND v.cell_state = 'reported'
          AND v.concept_key IS NOT NULL
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        # table_t.dedup_status (quarantine_duplicate_page_tables.py) tags
        # table_t rows that are a duplicate EXTRACTION of the same physical
        # page (confirmed: OCBC media-release docs split one page into up
        # to 8 table_t rows with byte-identical cell values) -- without this
        # exclusion every duplicate contributes its own extra candidate to
        # the conflict-resolution clustering below, inflating apparent
        # conflicts for no real reason. Column-existence check keeps this
        # additive: a DB that predates the dedup migration (or a synthetic
        # test DB with no table_t at all) still works, just without the
        # exclusion.
        has_dedup_col = any(
            r[1] == "dedup_status" for r in con.execute("PRAGMA table_info(table_t)"))
        if has_dedup_col:
            sql += " AND (t.dedup_status IS NULL OR t.dedup_status = '')"
        raw = [dict(r) for r in con.execute(sql).fetchall()]
        declaring = _column_unit_declaring_tables(con)
    finally:
        con.close()

    out: list[dict] = []
    for r in raw:
        ck = r["concept_key"]
        meta = cdict.get(ck)
        if meta is None:
            continue
        if _is_change_col(r["col_lvl1"], r["col_lvl2"], r["table_type"]):
            continue
        if _is_note_col(r["col_lvl1"], r["col_lvl2"]):
            continue
        if _is_company_only(r["table_type"]):
            continue
        if _is_stock_from_cash_flow(meta, r["table_type"], r["statement_class"]):
            continue
        col_unit_declaring = (r["doc_id"], r["table_id"]) in declaring
        decision = _resolve_cell_unit(meta, r["unit"], col_unit_declaring)
        if decision is None:
            continue
        r["unit"], r["unit_promoted"] = decision
        out.append(r)
    return out


def _cluster(vals: list[float]) -> list[list[float]]:
    """Cluster magnitudes so rounding twins / sign variants merge. Sort by |v|,
    start a new cluster when the gap to the previous |v| exceeds the tolerance."""
    if not vals:
        return []
    mags = sorted(abs(v) for v in vals)
    clusters: list[list[float]] = [[mags[0]]]
    for m in mags[1:]:
        if m - clusters[-1][-1] <= _ROUND_TOL:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    return clusters


def _mode(seq: list[float]) -> float:
    counts = defaultdict(int)
    for x in seq:
        counts[x] += 1
    # most frequent, tie -> smallest magnitude (stable, deterministic)
    return sorted(counts.items(), key=lambda kv: (-kv[1], abs(kv[0])))[0][0]


def _resolve_group(members: list[dict], concept_key: str) -> dict:
    """Return the canonical stamp for one analytic group.

    ANCHOR PRECEDENCE (2026-08-04). If ANY member is a human anchor
    (`identity_source='human_anchor'`, i.e. its address was authored in
    `lineage_identity_map.csv` and projected by `stamp_human_anchors`), the
    non-anchored members stop being candidates entirely. Everything below --
    magnitude clustering, table tier, row depth -- then arbitrates only WITHIN
    the anchored set.

    Why this outranks value clustering: clustering asks "which number do most
    rows agree on", which is a fair question only when nothing better is known.
    An anchor IS something better -- a human read the filing and said *this
    row, in this table, is the concept*. Without this, the anchor was one
    entrant in a popularity contest it routinely lost: DBS
    `bs.assets.customer_loans_gross` had 28 candidates of which 6 were
    anchored, and the winner came from a net-interest-income AVERAGE BALANCE
    SHEET table (16,174) instead of the anchored balance-sheet row (445,011).
    Measured across the KPH ground truth, the anchored candidate carried the
    correct value in every case checked; the resolver was discarding it.

    Narrowing only ever REMOVES candidates, so a group with no anchor behaves
    exactly as before -- this is additive, not a rewrite of the tie-breaks."""
    anchored = [m for m in members if m.get("identity_source") == "human_anchor"]
    anchor_narrowed = bool(anchored) and len(anchored) < len(members)
    if anchored:
        members = anchored

    magnitude_pos = _is_magnitude(concept_key)
    clusters = _cluster([m["value_num"] for m in members])
    n_clusters = len(clusters)

    # map each member to its cluster index (by |value|)
    def cluster_of(v: float) -> int:
        av = abs(v)
        for i, cl in enumerate(clusters):
            if cl[0] - _ROUND_TOL <= av <= cl[-1] + _ROUND_TOL:
                return i
        return 0

    by_cluster: dict[int, list[dict]] = defaultdict(list)
    for m in members:
        by_cluster[cluster_of(m["value_num"])].append(m)

    # best (lowest) tier owned by each cluster
    cluster_best_tier = {i: min(_tier(m["table_type"]) for m in ms)
                         for i, ms in by_cluster.items()}
    # shallowest row-hierarchy depth owned by each cluster. A statement prints
    # its GRAND TOTAL at the top of the hierarchy and its SUBTOTALS underneath,
    # so when one concept legitimately matches both (OCBC's balance sheet has
    # 'Total liabilities' at depth 1 AND 'Subtotal Liabilities' at depth 2;
    # DBS's NPA table has 'Total non-performing assets (NPA)' at depth 1 above
    # 'Total non-performing loans (NPL)' at depth 2), depth is the structural
    # signal for which one is the concept's own figure. Reading the printed
    # hierarchy is general: it needs no per-bank label list and works for a
    # statement layout we have never seen.
    cluster_min_depth = {i: min((m.get("row_depth") or 0) for m in ms)
                         for i, ms in by_cluster.items()}

    conflict = False
    if n_clusters == 1:
        chosen_cluster = 0
        resolved_by = ("single" if len({round(abs(m["value_num"]), 6) for m in members}) == 1
                       and len(members) == 1 else "twin_collapse")
    else:
        best_tier = min(cluster_best_tier.values())
        owners = [i for i, t in cluster_best_tier.items() if t == best_tier]
        if len(owners) == 1:
            chosen_cluster = owners[0]
            resolved_by = "prefer_table"
        else:
            # disagreement inside one authority tier -> punch-list
            conflict = True
            # deterministic representative: best tier, then most-supported value,
            # then smallest magnitude
            def cl_key(i: int) -> tuple:
                ms = by_cluster[i]
                support = len(ms)
                rep = _mode([m["value_num"] for m in ms])
                # depth outranks support and magnitude: a grand total printed
                # once still beats a subtotal printed twice, and the old
                # smallest-magnitude tie-break actively preferred the SUBTOTAL
                # (it is smaller by construction) -- which is how OCBC served
                # 'Subtotal Liabilities' 502,719 instead of 'Total liabilities'
                # 612,118 (identity: assets 675,688 - equity 63,570 = 612,118).
                return (cluster_best_tier[i], cluster_min_depth[i], -support, abs(rep))
            chosen_cluster = sorted(owners + [i for i in by_cluster if i not in owners],
                                    key=cl_key)[0]
            resolved_by = "conflict"

    picked = by_cluster[chosen_cluster]
    # representative row inside the chosen cluster: best tier, then modal value
    rep_val = _mode([m["value_num"] for m in picked])
    best_t = min(_tier(m["table_type"]) for m in picked)
    tier_rows = [m for m in picked if _tier(m["table_type"]) == best_t]
    # within the chosen cluster, attribute to the shallowest row for the same
    # reason (source_row_label should name the grand total, not a subtotal)
    _min_d = min((m.get("row_depth") or 0) for m in tier_rows)
    tier_rows = [m for m in tier_rows if (m.get("row_depth") or 0) == _min_d] or tier_rows
    # prefer a row whose printed value == modal magnitude
    rep_rows = [m for m in tier_rows if abs(abs(m["value_num"]) - abs(rep_val)) <= _ROUND_TOL]
    rep = (rep_rows or tier_rows)[0]

    magnitude = abs(rep["value_num"])
    if magnitude_pos:
        value_num = magnitude
        if any(m["value_num"] < 0 for m in picked):
            if resolved_by in ("single", "twin_collapse"):
                resolved_by = "sign_normalized" if resolved_by == "single" else resolved_by
    else:
        # sign from the preferred-tier representative (the primary statement's
        # printed sign); rep is already the best-tier row in the chosen cluster
        value_num = rep["value_num"]

    # Surface anchor precedence in the audit trail: a cell decided by narrowing
    # to the human anchor reads `anchor:<how it was then settled>`, so the
    # punch-list and m2_canonical_leaf can tell "the human said so" apart from
    # "the numbers happened to agree". Prefix, not replacement -- the existing
    # single/twin_collapse/prefer_table/conflict vocabulary stays readable.
    if anchor_narrowed:
        resolved_by = f"anchor:{resolved_by}"

    return {
        "value_num": value_num,
        "unit": rep["unit"],
        "unit_source": "dictionary_promoted" if rep.get("unit_promoted") else "as_loaded",
        "source_doc_id": rep["doc_id"],
        "source_table_id": rep["table_id"],
        "source_row_label": rep["row_label"],
        "period_start": rep["period_start"],
        "n_candidates": len(members),
        "resolved_by": resolved_by,
        "conflict": conflict,
        "clusters": clusters,
        "by_cluster": by_cluster,
        "cluster_best_tier": cluster_best_tier,
    }


def _group_key(r: dict) -> tuple:
    """The analytic identity a raw level row collapses into. legal_entity is
    part of the grain (see module docstring): the SAME table's Group and
    Bank/Company columns are different economic entities, not duplicate
    reports of one number, so they must land in DIFFERENT groups rather than
    colliding into a single (spurious) conflict."""
    return (r["institution"], r["concept_key"], r["period"], r["period_span"],
            r["segment_key"], r["geo_key"], r["industry_key"], r["legal_entity"])


def _canonicalize(levels: list[dict], cdict: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Pure grouping + resolution: raw level rows -> (fact_rows, conflicts).
    No DB I/O -- split out from build() so the grain/grouping behaviour is
    unit-testable without a live sqlite fixture."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in levels:
        groups[_group_key(r)].append(r)

    fact_rows: list[dict] = []
    conflicts: list[dict] = []
    for k, members in groups.items():
        inst, ck, period, span, seg, geo, ind, entity = k
        res = _resolve_group(members, ck)
        meta = cdict.get(ck, {})
        fact_rows.append({
            "institution": inst,
            "concept_key": ck,
            "concept_name": meta.get("name", ""),
            "thesis": meta.get("thesis", ""),
            "period": period,
            "period_span": span,
            "period_start": res["period_start"],
            "segment_key": seg,
            "geo_key": geo,
            "industry_key": ind,
            "legal_entity": entity,
            "value_num": res["value_num"],
            "unit": res["unit"],
            "unit_source": res["unit_source"],
            "source_doc_id": res["source_doc_id"],
            "source_table_id": res["source_table_id"],
            "source_row_label": res["source_row_label"],
            "n_candidates": res["n_candidates"],
            "resolved_by": res["resolved_by"],
        })
        if res["conflict"]:
            comp = []
            for i, ms in res["by_cluster"].items():
                rep = _mode([m["value_num"] for m in ms])
                tabs = sorted({m["table_type"] for m in ms})
                comp.append((rep, res["cluster_best_tier"][i], tabs))
            comp.sort(key=lambda c: (c[1], abs(c[0])))
            conflicts.append({
                "institution": inst, "concept_key": ck, "concept_name": meta.get("name", ""),
                "period": period, "period_span": span,
                "segment_key": seg, "geo_key": geo, "industry_key": ind,
                "legal_entity": entity,
                "n_candidates": res["n_candidates"],
                "competing_values": " | ".join(f"{v:g}" for v, _, _ in comp),
                "competing_sources": " || ".join(
                    f"{v:g}@tier{t}:{','.join(tabs)}" for v, t, tabs in comp),
                "chosen_value": res["value_num"],
            })

    _infer_missing_currency_units(fact_rows)
    return fact_rows, conflicts


def _infer_missing_currency_units(fact_rows: list[dict]) -> int:
    """Fill a NULL unit from the SAME (institution, concept)'s other rows, but
    only when those rows agree on exactly one unit. Returns rows filled.

    A currency concept's unit is a property of the institution's reporting, not
    of the individual row: UOB reports total assets in S$m in every exhibit. A
    table that declares no unit anywhere (no column unit, no '($m)' caption)
    therefore leaves the cell NULL even though the answer is unambiguous from
    the rest of the corpus -- all 152 such rows came from a single UOB
    highlights table. Serving NULL is as unusable to a consumer as serving the
    wrong unit, which is why the D1 gate counts NULL as a distinct unit.

    Deliberately refuses to guess: if the institution+concept carries MORE than
    one distinct unit, that is a real disagreement (a genuinely different
    currency, or a mis-stamp) and inference would paper over it, so the NULL is
    left for the gate to report. Recorded as unit_source='inferred_institution'
    so an inferred unit is never mistaken for a printed one.
    """
    by_inst: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_concept: dict[str, set[str]] = defaultdict(set)
    for r in fact_rows:
        if r.get("unit"):
            by_inst[(r["institution"], r["concept_key"])].add(r["unit"])
            by_concept[r["concept_key"]].add(r["unit"])
    filled = 0
    for r in fact_rows:
        if r.get("unit"):
            continue
        # narrowest evidence first: the same bank's own other rows for this
        # concept, then the concept across the corpus. The second tier is NOT
        # "all banks report in one currency" -- it fires only when the concept
        # has exactly ONE observed unit anywhere, so the arrival of a filer
        # reporting in another currency makes the set ambiguous and inference
        # switches itself off. (Needed for reg.capital.rwa, where UOB's ONLY
        # rows come from the unit-less table and there is no same-bank
        # evidence, while DBS and OCBC agree on S$m across 21 rows.)
        for scope, candidates in (("inferred_institution",
                                   by_inst.get((r["institution"], r["concept_key"]), set())),
                                  ("inferred_concept", by_concept.get(r["concept_key"], set()))):
            if len(candidates) == 1:
                r["unit"] = next(iter(candidates))
                r["unit_source"] = scope
                filled += 1
                break
    return filled


def build(db: str | Path = _DB, *, dry_run: bool = False) -> dict:
    cdict = _load_dict()
    levels = _fetch_levels(db, cdict)
    fact_rows, conflicts = _canonicalize(levels, cdict)

    if not dry_run:
        _write_fact(db, fact_rows)
    _write_conflicts(conflicts)

    n_groups = len({_group_key(r) for r in levels})
    return {"fact_rows": fact_rows, "conflicts": conflicts, "n_groups": n_groups}


_FACT_DDL = """
DROP TABLE IF EXISTS fact_metric;
CREATE TABLE fact_metric (
  institution      TEXT NOT NULL,
  concept_key      TEXT NOT NULL,
  concept_name     TEXT,
  thesis           TEXT,
  period           DATE NOT NULL,
  period_span      TEXT,
  period_start     DATE,
  segment_key      TEXT NOT NULL,
  geo_key          TEXT NOT NULL,
  industry_key     TEXT NOT NULL DEFAULT 'IND_TOTAL',
                       -- DEFAULT (not just NOT NULL) so a writer that predates
                       -- this axis (e.g. compute_ratios.py's derived-row INSERT,
                       -- which does not name this column) still gets the
                       -- correct base-slice default instead of a constraint
                       -- violation -- mirrors how segment_key/geo_key's
                       -- default member is always the whole-book/GLOBAL slice.
  legal_entity     TEXT NOT NULL DEFAULT 'CONSOLIDATED',
                       -- DEFAULT (not just NOT NULL) for the same documented
                       -- reason as industry_key above: a writer that predates
                       -- this axis (compute_ratios.py's INSERT, which does not
                       -- name this column) still gets the correct base-slice
                       -- default -- the Group/consolidated figure -- instead of
                       -- a constraint violation. Part of the grain because the
                       -- SAME table can carry both a Group (CONSOLIDATED) and a
                       -- Bank/Company (BANK_SOLO/PARENT_COMPANY) column for one
                       -- concept/period; those are different economic entities,
                       -- not duplicate reports, and must not collapse into one
                       -- row (see module docstring). See v_fact_metric_serving
                       -- below for the CONSOLIDATED-only dashboard slice.
  value_num        REAL,
  unit             TEXT,
  unit_source      TEXT,               -- 'as_loaded' | 'dictionary_promoted' —
                                        -- see build_fact_metric.py UNIT PROMOTION
  source_doc_id    TEXT,
  source_table_id  TEXT,
  source_row_label TEXT,
  n_candidates     INTEGER,
  resolved_by      TEXT,
  PRIMARY KEY (institution, concept_key, period, period_span, segment_key, geo_key,
               industry_key, legal_entity)
);

-- CONSOLIDATED is the canonical dashboard slice: one number per
-- (institution, concept, period, segment, geo, industry), matching what a
-- bank's own Group-level statements report. The solo entities (BANK_SOLO /
-- PARENT_COMPANY) stay in the base fact_metric table for drill-down and
-- audit (e.g. reconciling a Bank-only figure against the Group figure) but
-- are deliberately excluded from serving so no consumer can silently pick up
-- a Bank/Company number instead of the Group one (the bug this axis fixes).
DROP VIEW IF EXISTS v_fact_metric_serving;
CREATE VIEW v_fact_metric_serving AS
SELECT * FROM fact_metric WHERE legal_entity = 'CONSOLIDATED';
"""


def _write_fact(db: str | Path, rows: list[dict]) -> None:
    con = sqlite3.connect(str(db))
    try:
        con.executescript(_FACT_DDL)
        con.executemany(
            """INSERT INTO fact_metric
               (institution, concept_key, concept_name, thesis, period,
                period_span, period_start, segment_key, geo_key, industry_key,
                legal_entity, value_num, unit, unit_source, source_doc_id,
                source_table_id, source_row_label, n_candidates, resolved_by)
               VALUES
               (:institution,:concept_key,:concept_name,:thesis,:period,
                :period_span,:period_start,:segment_key,:geo_key,:industry_key,
                :legal_entity,:value_num,:unit,:unit_source,:source_doc_id,
                :source_table_id,:source_row_label,:n_candidates,:resolved_by)""", rows)
        con.commit()
    finally:
        con.close()


def _write_conflicts(conflicts: list[dict]) -> None:
    _CONFLICTS.parent.mkdir(parents=True, exist_ok=True)
    cols = ["institution", "concept_key", "concept_name", "period", "period_span",
            "segment_key", "geo_key", "industry_key", "legal_entity", "n_candidates",
            "competing_values", "competing_sources", "chosen_value"]
    with _CONFLICTS.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for c in sorted(conflicts, key=lambda c: (-c["n_candidates"],
                        c["institution"], c["concept_key"])):
            w.writerow(c)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_DB))
    ap.add_argument("--dry-run", action="store_true",
                    help="report + write conflicts CSV only; do not write fact_metric")
    args = ap.parse_args()

    res = build(args.db, dry_run=args.dry_run)
    fact, conflicts = res["fact_rows"], res["conflicts"]
    clean = sum(1 for r in fact if r["resolved_by"] in ("single", "twin_collapse",
                                                        "sign_normalized"))
    resolved = sum(1 for r in fact if r["resolved_by"] == "prefer_table")
    print(f"fact_metric rows:        {len(fact)}")
    print(f"  clean (single/twin):   {clean}")
    print(f"  resolved (prefer_tbl): {resolved}")
    print(f"  conflict (punch-list): {len(conflicts)}")
    print(f"  clean-resolution rate: {(clean + resolved) / len(fact):.1%}" if fact else "n/a")
    print(f"conflicts CSV:           {_CONFLICTS}")
    if args.dry_run:
        print("[dry-run] fact_metric table NOT written")
    print("\nTop 10 conflicts (by n_candidates):")
    for c in sorted(conflicts, key=lambda c: -c["n_candidates"])[:10]:
        print(f"  {c['concept_key']:32s} {c['institution'][:22]:22s} "
              f"{str(c['period_span']):>4s} {str(c['period'])} seg={c['segment_key']:9s} "
              f"vals=[{c['competing_values']}] -> {c['chosen_value']:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
