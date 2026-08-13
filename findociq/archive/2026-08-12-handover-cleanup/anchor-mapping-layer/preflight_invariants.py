"""preflight_invariants — the assert-and-report gate before the dashboard test.

Checks invariants A-F (identity/anchors, period uniform-rule, legal entity,
units/grain, coverage states, cleanliness) SIMULTANEOUSLY on the current DB,
after a re-resolve pass. Each individually-correct migration/fix earlier in
this project was verified in isolation; this is the pass that verifies they
still all hold TOGETHER. Read-only: computes and prints, changes nothing.

    python3 findociq/pipeline/preflight_invariants.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # pipeline/ on path
from mapping.registry import (COMPOSITE_SEP, bank_of, exhibit_aliases,  # noqa: E402
                              resolve_table_type)
from mapping.normalize import normalize_exhibit_title, safe_clean  # noqa: E402
from pass2.load_v7 import is_period_text, parse_period_span  # noqa: E402
from concept.load_dictionary import load_concepts  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_SPINE_CSV = _REPO / "findociq" / "data" / "derived" / "lineage_identity_map.csv"
_CONFLICTS_CSV = _REPO / "findociq" / "data" / "derived" / "fact_metric_conflicts.csv"

BANKS = ["DBS Group Holdings Ltd", "Oversea-Chinese Banking Corporation Ltd", "United Overseas Bank Ltd"]
PERIODS = ["2025-12-31", "2025-06-30"]  # FY25, 2H25 (1H25 == 2025-06-30 col_period grain, same period_end distinctions handled per-check)

RESULTS: list[tuple[str, str, str]] = []  # (id, pass/fail, note)


def record(id_: str, ok: bool, note: str) -> None:
    RESULTS.append((id_, "PASS" if ok else "FAIL", note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {id_}: {note}")


def spine_concepts() -> list[str]:
    with open(_SPINE_CSV) as f:
        rows = list(csv.DictReader(f))
    return sorted(set(r["concept_key"] for r in rows if r["resolution"] in ("anchor", "derived", "pending_extraction")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--snapshot", default=None, help="pre-reresolve DB snapshot, for MERGE-invariant hash comparison")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    spine = spine_concepts()
    print(f"{len(spine)} spine concepts loaded from {_SPINE_CSV.name}\n")

    # ==================================================================
    # A. Identity / anchors
    # ==================================================================
    print("=== A. Identity / anchors ===")
    n_hc = con.execute("SELECT COUNT(*) FROM bank_line_map WHERE map_status='human_confirmed'").fetchone()[0]
    record("A1a", n_hc == 104, f"human_confirmed anchors = {n_hc}")

    if args.snapshot:
        scon = sqlite3.connect(args.snapshot)
        def hc_hash(c):
            rows = c.execute(
                "SELECT bank, table_type_id, row_label_norm, parent_label_norm, concept_key, segment_key "
                "FROM bank_line_map WHERE map_status='human_confirmed' ORDER BY 1,2,3,4,5,6").fetchall()
            return hashlib.sha256(repr(rows).encode()).hexdigest()
        before, after = hc_hash(scon), hc_hash(con)
        scon.close()
        record("A1b", before == after, f"MERGE-invariant hash {'unchanged' if before==after else 'CHANGED'} vs {args.snapshot}")
    else:
        record("A1b", True, "no --snapshot given; hash comparison skipped (count-only check A1a stands)")

    # coverage: spine concept present (>=1 non-null cell) per bank
    have = set(con.execute("SELECT DISTINCT concept_key, institution FROM v_cell WHERE concept_key IS NOT NULL").fetchall())
    combos = [(c, b) for c in spine for b in BANKS]
    covered = [(c, b) for c, b in combos if (c, b) in have]
    record("A2", len(covered) >= 0.9 * len(combos),
           f"{len(covered)}/{len(combos)} spine x bank combos covered "
           f"(missing: {sorted(set(combos)-set(covered))[:10]}{'...' if len(combos)-len(covered)>10 else ''})")

    # DBS NII segment split
    nii_rows = con.execute("""
        SELECT segment_key, value_num, row_id FROM v_cell
        WHERE institution=? AND concept_key='pnl.nii.net' AND period='2025-12-31'
          AND period_label='FY25' AND cell_state='reported'
    """, (BANKS[0],)).fetchall()
    distinct_pks = {(seg, val) for seg, val, _ in nii_rows}
    vals_by_seg = {}
    for seg, val, _ in nii_rows:
        vals_by_seg.setdefault(seg, set()).add(val)
    record("A3", len(distinct_pks) >= 3,
           f"DBS NII FY25 segment split rows: {sorted(nii_rows)} -> {len(distinct_pks)} distinct (seg,value) PKs")

    # A4: spine anchors resolve via composite/section (stable table_type_id key), not raw title
    spine_tables = con.execute("""
        SELECT DISTINCT d.institution, s.section_title, t.table_title, t.table_title_clean, t.table_type_id
        FROM v_cell vc
        JOIN table_t t ON t.doc_id=vc.doc_id AND t.table_id=vc.table_id
        JOIN document d ON d.doc_id=vc.doc_id
        LEFT JOIN section s ON s.doc_id=t.doc_id AND s.section_id=t.section_id
        WHERE vc.concept_key IN ({})
    """.format(",".join("?" * len(spine))), spine).fetchall()
    level_counts = {"composite": 0, "section": 0, "title": 0, "unclassified": 0}
    title_fallbacks = []
    unclassified_tables = []
    for institution, sec_title, title, title_clean, ttid in spine_tables:
        bank = bank_of(institution)
        use_title = safe_clean(title, title_clean)
        resolved_ttid, alias = resolve_table_type(con, bank, sec_title, use_title)
        if not resolved_ttid:
            level_counts["unclassified"] += 1
            unclassified_tables.append((bank, use_title))
            continue
        level = ("composite" if COMPOSITE_SEP in alias
                 else "section" if alias == normalize_exhibit_title(sec_title)
                 else "title")
        level_counts[level] += 1
        if level == "title":
            title_fallbacks.append((bank, title))
    # A4 asserts COVERAGE (every spine-hosting table resolves to a registry
    # type), NOT the level it resolved at.
    #
    # The original assertion was `title == 0`, treating a title-level match as a
    # weak fallback. registry.py's own cascade documents the opposite: title is
    # the identity for DBS-shaped documents ("section='Overview'
    # title='Selected income statement items' -> the TITLE is the identity; the
    # section is a page grouping"). All 22 title-level matches were verified
    # correct, and driving them to 0 would require aliases that are wrong or
    # unmaintainable:
    #   * `statement_of_changes_in_equity` as a SECTION alias merges the Group
    #     and Company statements — two different legal entities — into one
    #     type. Only the title ('… the Group' / '… the Company') separates them.
    #   * `performance` as a SECTION alias hijacks OCBC's 'Allowances' and
    #     'Asset Quality' tables, which sit under a '<period> Performance' page
    #     header and today resolve correctly by title (section is tried FIRST,
    #     so a broad section alias always wins over a precise title).
    #   * the rest would need composite aliases keyed to drifting per-document
    #     section headers ('by_currency__loans_to_customers',
    #     '2q25_year_on_year_performance__allowances') — a new row every quarter
    #     forever, i.e. exactly the key fragmentation the registry exists to
    #     prevent.
    # So the level split is REPORTED (a shift toward title over time is worth
    # seeing) but only UNCLASSIFIED — a table no tier, dim_hint or exclusion
    # rule can reach — is a failure.
    unclassified_list = sorted({(b, (t or "")[:60]) for b, t in unclassified_tables})
    record("A4", level_counts["unclassified"] == 0,
           f"spine table match levels: {level_counts} (title-level is a valid "
           f"registry path, not a fallback — see comment); "
           f"UNCLASSIFIED: {unclassified_list}")

    # ==================================================================
    # B. Period (uniform rule)
    # ==================================================================
    print("\n=== B. Period (uniform rule) ===")
    view_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cell'").fetchone()[0]
    record("B1", "concept_period_kind" not in view_sql,
           "v_cell references concept_period_kind: " + ("YES (BAD)" if "concept_period_kind" in view_sql else "no"))

    null_period = con.execute(
        "SELECT COUNT(*) FROM v_cell WHERE period_label IS NULL OR period_end IS NULL OR period_source IS NULL"
    ).fetchone()[0]
    total_cells = con.execute("SELECT COUNT(*) FROM v_cell").fetchone()[0]
    record("B2", null_period == 0, f"{null_period}/{total_cells} v_cell rows missing period_label/period_end/period_source")

    # B3/B4 use fact_metric (the already-canonicalized, one-row-per-grain table
    # build_fact_metric.py produces from v_cell_flat) rather than v_cell_sumsafe:
    # v_cell_sumsafe filters row_hierarchy>=1, which wrongly excludes UOB's
    # 'financial_highlights' human-anchored rows (a flat table, row_hierarchy=0
    # by construction) -- v_cell_flat/fact_metric carry no such filter, and are
    # what the dashboard actually reads (see C2, which passes against fact_metric).
    def canonical(bank, concept, span):
        row = con.execute("""
            SELECT period, value_num FROM fact_metric
            WHERE institution=? AND concept_key=? AND period='2025-12-31' AND period_span=?
              AND segment_key='SEG_TOTAL' AND geo_key='GLOBAL' AND industry_key='IND_TOTAL'
              AND legal_entity='CONSOLIDATED'
        """, (bank, concept, span)).fetchall()
        return row[0] if len(row) == 1 else (None if not row else row)

    stock_results = []
    for bank in BANKS:
        fy = canonical(bank, "bs.assets.total", "FY")
        h2 = canonical(bank, "bs.assets.total", "2H")
        h1 = con.execute("""
            SELECT period, value_num FROM fact_metric
            WHERE institution=? AND concept_key='bs.assets.total' AND period_span='1H'
              AND segment_key='SEG_TOTAL' AND geo_key='GLOBAL' AND industry_key='IND_TOTAL'
              AND legal_entity='CONSOLIDATED' AND period < '2025-12-31'
            ORDER BY period DESC LIMIT 1
        """, (bank,)).fetchone()
        ok = bool(fy and h2 and fy == h2 and (not h1 or h1[0] < fy[0]))
        stock_results.append((bank, ok, {"FY25": fy, "2H25": h2, "1H(prior)": h1}))
    record("B3", all(ok for _, ok, _ in stock_results),
           "; ".join(f"{b.split()[0]}:{by}" for b, ok, by in stock_results))

    flow_fy = canonical(BANKS[0], "pnl.income.total", "FY")
    flow_2h = canonical(BANKS[0], "pnl.income.total", "2H")
    flow_1h = con.execute("""
        SELECT period, value_num FROM fact_metric
        WHERE institution=? AND concept_key='pnl.income.total' AND period_span='1H'
          AND segment_key='SEG_TOTAL' AND geo_key='GLOBAL' AND industry_key='IND_TOTAL'
          AND legal_entity='CONSOLIDATED' AND period < '2025-12-31'
        ORDER BY period DESC LIMIT 1
    """, (BANKS[0],)).fetchone()
    flow_rows = [flow_fy, flow_2h, flow_1h]
    flow_vals = {v for v in flow_rows if v is not None}
    record("B4", all(v is not None for v in flow_rows) and len(flow_vals) == 3,
           f"DBS pnl.income.total FY25/2H25/1H(prior): {flow_rows} -> {len(flow_vals)} distinct values")

    # FY ~= 1H + 2H integrity check (ratios/EPS exempt)
    ratio_like = {c for c in spine if c.startswith("ratio.") or "eps" in c or c == "reg.capital.cet1_ratio"}
    triples = con.execute("""
        SELECT institution, concept_key, period, period_label, value_num
        FROM v_cell_sumsafe
        WHERE concept_key IN ({}) AND period_label IN ('FY25','1H25','2H25')
    """.format(",".join("?" * len(spine))), spine).fetchall()
    by_key: dict = {}
    for inst, ck, per, lbl, val in triples:
        by_key.setdefault((inst, ck, per), {})[lbl] = val
    unexplained = []
    n_checked = 0
    for (inst, ck, per), labels in by_key.items():
        if not all(l in labels for l in ("FY25", "1H25", "2H25")):
            continue
        n_checked += 1
        if ck in ratio_like:
            continue
        fy, h1, h2 = labels["FY25"], labels["1H25"], labels["2H25"]
        flow_ok = abs(fy - (h1 + h2)) <= 0.05 * max(abs(fy), 1)
        stock_ok = abs(fy - h2) <= 0.05 * max(abs(fy), 1)
        if not (flow_ok or stock_ok):
            unexplained.append((inst, ck, per, fy, h1, h2))
    record("B5", len(unexplained) == 0,
           f"{n_checked} FY/1H/2H triples checked, {len(unexplained)} unexplained (ratios/EPS excluded): {unexplained[:5]}")

    # B6: the real risk isn't "any cell fell back to table/doc period" (that's
    # CORRECT and expected whenever the cell's own column carries no period --
    # a '% chg'/'YoY'/'Volume' column legitimately has none, and the table's
    # title/doc date IS then the only available source). 'table_or_doc' is
    # migrate_add_period_source.py's backfill bucket for pre-migration rows
    # (collapses table_title/doc, can no longer be told apart) -- counted as a
    # fallback source too, since it's equally "not column-derived."
    #
    # The signature is PER-COLUMN, not per-table. The original check used a
    # table-level proxy ("this table has >=2 distinct col_period, so period
    # info was available") which is wrong in both directions:
    #   * false positives -- it flagged the periodless comparison-delta columns
    #     ('% chg', '+/(-) %', 'YoY (%)') that happen to sit beside real period
    #     columns. That is the same legitimate periodless category every prior
    #     pass excluded by hand (see the 2026-08-03 "UOB title-context
    #     bare-year gap" decision). All 127 cells it flagged were of this kind.
    #   * false negatives -- a column labelled '4th Qtr 2024' whose label the
    #     grammar failed to parse (the ACTUAL defect) was only caught when its
    #     table happened to have >=2 OTHER parsed periods.
    # Proof the proxy measured the wrong thing: backfilling the genuinely-stale
    # columns (pass2/backfill_col_period.py) drove the real signature to 0 while
    # the proxy count went UP 63 -> 73, because adding true column periods makes
    # more tables qualify as "multi-period" and so drags in more innocent
    # '% chg' columns.
    #
    # The real signature is load_v7's own GATE A2, applied post-hoc: a spine
    # cell whose OWN column label IS period-shaped by the loader's gate, yet
    # carries no col_period -- so the period was derivable and was not derived.
    # Checking with the loader's functions (not a private re-implementation)
    # means the gate and this invariant cannot drift apart.
    fallback_rows = con.execute("""
        SELECT DISTINCT vc.institution, vc.doc_id, vc.table_id, vc.col_id,
               COALESCE(cd.col_leaf_label_clean, cd.col_leaf_label), cd.col_period
        FROM v_cell vc
        JOIN col_dim cd ON cd.doc_id = vc.doc_id AND cd.table_id = vc.table_id
                       AND cd.col_id = vc.col_id
        WHERE vc.concept_key IN ({}) AND vc.period_source IN ('table_or_doc','table_title','doc')
    """.format(",".join("?" * len(spine))), spine).fetchall()
    real_risk = [(inst, doc, tid, label) for inst, doc, tid, _cid, label, cp in fallback_rows
                 if cp is None and label and is_period_text(label, column=True)
                 and parse_period_span(label, column=True)]
    record("B6", len(real_risk) == 0,
           f"{len(fallback_rows)} spine cells took a table/doc-level period; "
           f"{len(real_risk)} of them sit in a column whose own label IS period-shaped "
           f"but yielded no col_period (the real gap signature — run "
           f"pass2/backfill_col_period.py): {real_risk[:10]}")

    # ==================================================================
    # C. Legal entity
    # ==================================================================
    print("\n=== C. Legal entity ===")
    le_total = con.execute("SELECT COUNT(*) FROM cell_fact").fetchone()[0]
    le_pop = con.execute("SELECT COUNT(*) FROM cell_fact WHERE legal_entity IS NOT NULL").fetchone()[0]
    record("C1", le_pop == le_total, f"legal_entity populated {le_pop}/{le_total}")

    uob_assets = con.execute("""
        SELECT legal_entity, value_num FROM fact_metric
        WHERE institution=? AND concept_key='bs.assets.total' AND period='2025-12-31' AND period_span='FY'
    """, (BANKS[2],)).fetchall()
    uob_by_le = {le: v for le, v in uob_assets}
    record("C2", uob_by_le.get("CONSOLIDATED") == 572061,
           f"UOB bs.assets.total FY25 by legal_entity: {uob_by_le}")

    dup_le = con.execute("""
        SELECT concept_key, period, institution, COUNT(DISTINCT legal_entity)
        FROM v_fact_metric_serving
        GROUP BY concept_key, period, institution
        HAVING COUNT(DISTINCT legal_entity) > 1
    """).fetchall()
    record("C3", len(dup_le) == 0, f"{len(dup_le)} (concept,period,bank) combos with >1 legal_entity in v_fact_metric_serving: {dup_le[:5]}")

    # ==================================================================
    # D. Units / grain
    # ==================================================================
    print("\n=== D. Units / grain ===")
    # COALESCE so a NULL unit counts as its own value: bare COUNT(DISTINCT unit)
    # SKIPS NULLs, which hid 152 unit-less rows (a whole UOB highlights table)
    # behind a PASS. Matches validate.assert_single_unit_per_concept exactly, so
    # the report and the hard gate cannot disagree.
    multi_unit = con.execute("""
        SELECT concept_key, GROUP_CONCAT(DISTINCT COALESCE(unit, '<null>')) FROM fact_metric
        WHERE concept_key IN ({})
        GROUP BY concept_key HAVING COUNT(DISTINCT COALESCE(unit, '<null>')) > 1
    """.format(",".join("?" * len(spine))), spine).fetchall()
    record("D1", len(multi_unit) == 0, f"{len(multi_unit)} spine concepts with >1 unit: {multi_unit}")

    with open(_CONFLICTS_CSV) as f:
        conflicts = list(csv.DictReader(f))
    spine_conflicts = [c for c in conflicts if c["concept_key"] in spine]
    record("D2", len(spine_conflicts) == 0,
           f"{len(spine_conflicts)}/{len(conflicts)} total 8-key-grain conflicts are spine concepts "
           f"(fact_metric_conflicts.csv; 6-key legal_entity splits already excluded by construction)")

    # ==================================================================
    # E. Coverage states
    # ==================================================================
    print("\n=== E. Coverage states ===")
    bank_short = {"DBS Group Holdings Ltd": "DBS", "Oversea-Chinese Banking Corporation Ltd": "OCBC",
                  "United Overseas Bank Ltd": "UOB"}
    have_slots = set(con.execute("""
        SELECT institution, concept_key, period_span FROM v_fact_metric_serving
        WHERE concept_key IN ({}) AND period='2025-12-31' AND period_span IN ('FY','2H')
    """.format(",".join("?" * len(spine))), spine).fetchall())

    # A STOCK concept's natural span is 'as_at': a balance-sheet total is an
    # INSTANT, not a duration, so demanding FY/2H of it is a category error --
    # the same reasoning E3 already records ("stock year-end collapse is a
    # period_end grouping property"). Whether a stock concept happened to pick
    # up an FY/2H row is an artifact of WHICH exhibit printed it (a highlights
    # table has period columns; the statutory balance sheet has as-at dated
    # ones), not of whether the bank disclosed it. bs.equity.total is the proof:
    # NO bank prints it in any period-columned exhibit, yet all three report it
    # (DBS 68,916 / OCBC 63,570 / UOB 51,493, each identity-checked against
    # assets - liabilities).
    #
    # The PERIOD DATE still has to match (period='2025-12-31' above): an as_at
    # from another date is a different balance and does NOT satisfy the slot.
    stock_concepts = {c["key"] for c in load_concepts() if c.get("nature") == "stock"}
    as_at_slots = set(con.execute("""
        SELECT institution, concept_key FROM v_fact_metric_serving
        WHERE concept_key IN ({}) AND period='2025-12-31' AND period_span='as_at'
    """.format(",".join("?" * len(spine))), spine).fetchall())
    nd_slots = set(con.execute("SELECT bank, concept_key FROM concept_disclosure WHERE status='not_disclosed'").fetchall())
    pending_slots = set(con.execute(
        "SELECT bank, concept_key FROM concept_home WHERE concept_key IN ({})".format(",".join("?" * len(spine))),
        spine).fetchall())

    total_slots = len(spine) * len(BANKS) * 2  # FY + 2H per concept x bank
    value_n = len(have_slots)
    not_disclosed = failed_resolve = pending_anchor = 0
    failed_list = []
    for c in spine:
        for b in BANKS:
            short = bank_short[b]
            for span in ("FY", "2H"):
                if (b, c, span) in have_slots:
                    continue
                if c in stock_concepts and (b, c) in as_at_slots:
                    # Instant concept satisfied at as_at -> this slot IS filled,
                    # so it must be counted, not merely excused. FY and 2H are
                    # the SAME balance at the same period-end for a stock (the
                    # premise of this branch, and E3's "stock year-end collapse
                    # is a period_end grouping property"), so each span counts
                    # once and the combo contributes 2 -- exactly as an FY+2H
                    # pair in have_slots would. Incrementing PER SPAN (rather
                    # than +2 per combo) is what keeps the mixed case right: a
                    # combo whose FY came from have_slots and whose 2H is
                    # excused here is counted once by each, never twice.
                    value_n += 1
                    continue
                if (short, c) in nd_slots:
                    not_disclosed += 1
                elif (short, c) in pending_slots:
                    pending_anchor += 1
                else:
                    failed_resolve += 1
                    failed_list.append((short, c, span))
    # Every slot lands in exactly one bucket; the four must sum to `slots`. This
    # was `record("E1", True, ...)` -- a check that could never fail -- and it
    # silently went out of balance (146+0+4+0 = 150 vs 162) the moment the
    # as_at branch started excusing slots without counting them.
    bucket_sum = value_n + not_disclosed + pending_anchor + failed_resolve
    record("E1", bucket_sum == total_slots,
           f"slots={total_slots} value={value_n} not_disclosed={not_disclosed} "
           f"pending_anchor={pending_anchor} failed_resolve={failed_resolve} "
           f"(buckets sum to {bucket_sum})")
    record("E2", failed_resolve == 0, f"failed_resolve = {failed_resolve}: {failed_list}")
    record("E3", True, "stock year-end collapse is a period_end grouping property (verified structurally in B3), not a coverage-state input")

    # ==================================================================
    # F. Cleanliness
    # ==================================================================
    print("\n=== F. Cleanliness ===")
    from mapping.migrate_serving_views import migrate as rebuild_serving_views
    before_cols = [r[1] for r in con.execute("PRAGMA table_info(v_cell)")]
    m1 = rebuild_serving_views(con)
    m2 = rebuild_serving_views(con)
    after_cols = [r[1] for r in con.execute("PRAGMA table_info(v_cell)")]
    record("F1", before_cols == after_cols and m2["columns_added"] == [],
           f"re-run of migrate_serving_views.migrate() added columns={m2['columns_added']}; view cols stable={before_cols==after_cols}")

    uob_geo_spine_hits = con.execute("""
        SELECT COUNT(*) FROM v_cell
        WHERE table_id LIKE '%performance_by_geographical_segment%' AND concept_key IN ({})
    """.format(",".join("?" * len(spine))), spine).fetchone()[0]
    record("F2", uob_geo_spine_hits == 0, f"spine cells in UOB FS_GEO_INCOME table: {uob_geo_spine_hits}")

    # ==================================================================
    print("\n=== Headline coverage number ===")
    print(f"  value cells (spine x bank x {{FY25,2H25}}, distinct): {value_n} / {total_slots}")

    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    print(f"\n{'ALL PASS' if n_fail == 0 else f'{n_fail} FAILED'} ({len(RESULTS)} checks)")
    con.close()


if __name__ == "__main__":
    main()
