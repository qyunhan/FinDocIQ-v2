"""concept.validate — reconciliation gate. A failing check means a MAPPING is
suspect (a wrong stamp), NOT that the data is wrong. Nothing is auto-unstamped;
failures are reported loudly for a human/次-cycle to adjudicate.

Checks:
  (a) FORMULA CONSISTENCY.
      - Additive subtotal identities among line_items (the prompt's example
        pnl.nii.net = interest_income - interest_expense; also total income and
        net profit). These are standard IAS-1 subtotals not expressible as the
        dictionary's ratio formulas, so they are named here explicitly.
      - Ratio formulas taken straight from the dictionary (kind='derived',
        formula 'num / den [* factor]'), checked where the derived concept AND
        both line_item inputs are stamped in the SAME table/period. avg()-based
        formulas (ROE, credit-cost-bps) need two periods and are skipped.
  (b) UNIQUENESS. A concept_key stamped more than once per (doc_id, table_id) is
      an ambiguous match -> flag. GRANULARITY NOTE: the prompt says per
      doc/table/period/geo, but concept lives on row_dim which is PERIOD- and
      GEO-AGNOSTIC (cells carry period; geo is a separate axis). Two cells of one
      stamped row differ only by period/geo — legitimately. A true ambiguity is
      TWO ROWS in one table stamped the same concept, i.e. per (doc_id, table_id).
  (c) SUMS_TO CROSS-CHECK. row_dim.sums_to links a verified component to its
      total. If a component row and its total row carry the SAME concept_key, the
      stamp is self-contradictory (a part cannot equal its whole) -> flag.
  (d) NATURE (flow vs stock, IAS-1). Each concept declares `nature` in the
      dictionary (flow/stock/ratio_flow/ratio_point). Two checks, both scoped to
      avoid the pipeline's own benign period_span noise (many stock concepts are
      legitimately re-stamped with a duration span like '1Q' by table-level
      convention, not just 'as_at' -- so this does NOT assert "stock never gets a
      duration span"; that would flood on real, harmless data):
        (d1) FLOW_AS_AT: a nature='flow' concept stamped with period_span='as_at'
             is unambiguous evidence of the wrong concept_key (an 'as_at' tag is a
             deliberate balance-sheet marker; a flow concept should never carry
             one). This is the check that would have caught pnl.provisions.total
             also matching a Pillar3 "allowances for loans" BALANCE row.
        (d2) AS_AT_MAGNITUDE: within one (institution, concept_key, period,
             segment_key, geo_key) group, if there's exactly one 'as_at' value and
             it disagrees with a same-group duration-span value by >2x, they are
             not the same underlying number and the mapping is suspect -- flag for
             review (mirrors the additive/ratio tolerance-check style: reconcile,
             don't silently pick one).

Tolerances absorb printed rounding (values are S$m / %). Portable SQL.

STANDING ASSERTION (different in kind from (a)-(d) above): everything above is
a SUSPICION for a human to adjudicate -- nothing here is. `assert_single_legal_entity_per_group`
is a hard invariant the dashboard's serving view depends on: no
(institution, concept_key, period, period_span, segment_key, geo_key,
industry_key) group in v_fact_metric_serving may resolve to more than one
legal_entity. It is structurally guaranteed today (the view's own WHERE
legal_entity='CONSOLIDATED' filter plus fact_metric's PK, which now includes
legal_entity, make a violation impossible by construction) -- this assertion
exists as a regression trap for if either of those ever changes. Unlike (a)-(d)
it does not return a flag to be reviewed: it raises AssertionError and is
meant to be run as a gate (`python -m concept.validate --db PATH`), FAILING
LOUDLY (non-zero exit), not printing a warning. This is the exact class of bug
the axis fixes: UOB bs.assets.total 572,061 (Group/CONSOLIDATED) vs 485,263
(Bank/BANK_SOLO) silently collapsing into one row and losing the Group figure.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import load_concepts, unit_scale  # noqa: E402

_DURATION_SPANS = {"1Q", "2Q", "3Q", "4Q", "1H", "2H", "FY", "9M"}

# Standard additive subtotal identities: total_concept = Σ sign * component.
_ADDITIVE_IDENTITIES: list[tuple[str, list[tuple[str, int]]]] = [
    ("pnl.nii.net",
     [("pnl.nii.interest_income", 1), ("pnl.nii.interest_expense", -1)]),
    ("pnl.income.total",
     [("pnl.nii.net", 1), ("pnl.noninterest.total", 1)]),
    ("pnl.profit.net_attributable",
     [("pnl.profit.pretax", 1), ("pnl.tax", -1)]),
]

_KEY_RX = re.compile(r"[a-z]+(?:\.[a-z0-9_]+)+")


def _concept_values(con) -> dict:
    """(doc_id, table_id, period) -> {concept_key: [value_num, ...]}. Only real
    numeric cells (v_cell_sumsafe). A concept with >1 value in a slice is
    ambiguous THERE and is skipped by the arithmetic checks."""
    out: dict = defaultdict(lambda: defaultdict(list))
    for doc_id, table_id, period, key, val in con.execute(
            "SELECT doc_id, table_id, period, concept_key, value_num "
            "FROM v_cell_sumsafe WHERE concept_key IS NOT NULL"):
        out[(doc_id, table_id, period)][key].append(val)
    return out


def _unique(vals: dict, key: str):
    v = vals.get(key)
    return v[0] if v and len(v) == 1 else None


def _ratio_checks_from_dict() -> list[tuple[str, str, str, float]]:
    """(derived_key, num_key, den_key, factor) for simple 'num / den [* F]'
    dictionary formulas whose inputs are concept_keys. avg()/multi-term skipped."""
    out = []
    for c in load_concepts():
        f = c.get("formula")
        if c["kind"] != "derived" or not f or "avg(" in f:
            continue
        f = f.split("#", 1)[0].strip()
        # ONE source of truth for presentation scale: the declared unit kind
        # (load_dictionary.unit_scale), not a literal repeated per call site.
        # A formula may still carry an explicit factor, which wins below.
        factor = unit_scale(c.get("unit"))
        m = re.match(r"^\s*([a-z0-9_.]+)\s*/\s*([a-z0-9_.]+)\s*(?:\*\s*([0-9.]+))?\s*$", f)
        if not m:
            continue
        num, den = m.group(1), m.group(2)
        if m.group(3):
            factor = float(m.group(3))
        if _KEY_RX.fullmatch(num) and _KEY_RX.fullmatch(den):
            out.append((c["key"], num, den, factor))
    return out


# cell_rows tuple order shared by both nature checks below (matches validate()'s
# v_cell_sumsafe SELECT):
_DOC_ID, _TABLE_ID, _ROW_ID, _CONCEPT_KEY, _ROW_LABEL, _PERIOD_SPAN = range(6)
_INSTITUTION, _PERIOD, _SEGMENT_KEY, _GEO_KEY, _VALUE_NUM = range(6, 11)


def _flow_as_at_flags(cell_rows, nature_by_key: dict[str, str]) -> tuple[int, list[str]]:
    """(checked, failed) -- a nature='flow' concept should never carry
    period_span='as_at' (that's a deliberate balance-sheet marker); this is the
    check that would have caught pnl.provisions.total also matching a Pillar3
    "allowances for loans" BALANCE row before the dictionary split."""
    checked = 0
    failed = []
    for r in cell_rows:
        key = r[_CONCEPT_KEY]
        if nature_by_key.get(key) != "flow":
            continue
        checked += 1
        if r[_PERIOD_SPAN] == "as_at":
            failed.append(
                f"[flow_as_at] {r[_DOC_ID]}/{r[_TABLE_ID]} row{r[_ROW_ID]}: "
                f"{r[_ROW_LABEL]!r} stamped {key} (nature=flow) but carries "
                f"period_span='as_at' -- likely also matches a balance-sheet/"
                f"disclosure row with a similar label")
    return checked, failed


def _as_at_magnitude_flags(cell_rows) -> tuple[int, list[str]]:
    """(checked, failed) -- within one (institution, concept_key, period,
    segment_key, geo_key) group, an 'as_at' value that disagrees >2x with a
    same-group duration-span value is not the same underlying number; mirrors
    the additive/ratio tolerance-check style (reconcile, don't silently pick)."""
    groups: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in cell_rows:
        key = (r[_INSTITUTION], r[_CONCEPT_KEY], r[_PERIOD], r[_SEGMENT_KEY], r[_GEO_KEY])
        groups[key][r[_PERIOD_SPAN] or "NULL"].append(r[_VALUE_NUM])

    checked = 0
    failed = []
    for (inst, ck, period, seg, geo), by_span in groups.items():
        as_at_vals = by_span.get("as_at")
        if not as_at_vals or len(as_at_vals) != 1:
            continue
        av = as_at_vals[0]
        if av == 0:
            continue
        for span, vals in by_span.items():
            if span == "as_at":
                continue
            for ov in vals:
                checked += 1
                ratio = abs(ov) / abs(av)
                if ratio < 0.5 or ratio > 2.0:
                    failed.append(
                        f"[as_at_magnitude] {inst[:30]} {ck} {period}: as_at={av:g} "
                        f"vs {span}={ov:g} (ratio={ratio:.2g}, seg={seg}, geo={geo})")
    return checked, failed


def validate(con) -> dict:
    values = _concept_values(con)
    report = dict(checks=[], flags=[])

    def record(name: str, checked: int, failed: list[str]) -> None:
        report["checks"].append(dict(name=name, checked=checked,
                                     failed=len(failed), passed=checked - len(failed)))
        report["flags"].extend(failed)

    # (a1) additive identities -------------------------------------------------
    add_failed, add_checked = [], 0
    for total_key, comps in _ADDITIVE_IDENTITIES:
        for (doc_id, table_id, period), vals in values.items():
            tv = _unique(vals, total_key)
            cvs = [(k, s, _unique(vals, k)) for k, s in comps]
            if tv is None or any(v is None for _, _, v in cvs):
                continue
            add_checked += 1
            # SIGN ROBUSTNESS: a bank may store a subtractive component (interest
            # expense, tax) as a printed NEGATIVE, in which case the identity is a
            # RAW SUM of stored values (net = ii + ie_negative); another bank stores
            # it positive, so the identity applies the declared sign (net = ii -
            # ie_positive). Accept EITHER — a genuinely wrong mapping fails BOTH.
            raw = sum(v for _, _, v in cvs)                 # signs already in the data
            signed = sum(s * v for _, s, v in cvs)          # declared signs applied
            tol = max(1.0, 0.01 * abs(tv))
            if min(abs(raw - tv), abs(signed - tv)) > tol:
                add_failed.append(
                    f"[additive] {doc_id}/{table_id} {period}: {total_key}={tv} != "
                    f"{' , '.join(f'{k}({v})' for k, _, v in cvs)} "
                    f"(raw_sum={raw}, signed_sum={signed})")
    record("additive_identity", add_checked, add_failed)

    # (a2) ratio formulas from the dictionary ---------------------------------
    ratio_failed, ratio_checked = [], 0
    for derived_key, num_key, den_key, factor in _ratio_checks_from_dict():
        for (doc_id, table_id, period), vals in values.items():
            dv = _unique(vals, derived_key)
            nv = _unique(vals, num_key)
            de = _unique(vals, den_key)
            if dv is None or nv is None or de is None or de == 0:
                continue
            ratio_checked += 1
            expected = nv / de * factor
            tol = max(0.5, 0.02 * abs(dv))    # 2% or 0.5pp, printed-rounding slack
            if abs(expected - dv) > tol:
                ratio_failed.append(
                    f"[ratio] {doc_id}/{table_id} {period}: {derived_key}={dv} != "
                    f"{num_key}/{den_key}*{factor} = {expected:.3f}")
    record("ratio_formula", ratio_checked, ratio_failed)

    # (b) uniqueness per (doc, table) -----------------------------------------
    dup_failed = []
    dups = con.execute(
        "SELECT doc_id, table_id, concept_key, COUNT(*) c, "
        "GROUP_CONCAT(row_leaf_label, ' | ') labels "
        "FROM row_dim WHERE concept_key IS NOT NULL "
        "GROUP BY doc_id, table_id, concept_key HAVING c > 1 "
        "ORDER BY doc_id, table_id, concept_key").fetchall()
    for doc_id, table_id, key, c, labels in dups:
        dup_failed.append(f"[dup] {doc_id}/{table_id}: {key} stamped {c}x -> {labels}")
    record("uniqueness_per_table", len(dups) + 0, dup_failed)  # checked = #groups flagged

    # (c) sums_to cross-check: a row and its total carry the same concept -----
    sums_failed = []
    same = con.execute(
        "SELECT r.doc_id, r.table_id, r.row_id, r.concept_key, r.row_leaf_label, "
        "       tot.row_leaf_label "
        "FROM row_dim r JOIN row_dim tot ON tot.doc_id=r.doc_id AND "
        "     tot.table_id=r.table_id AND tot.row_id=r.sums_to "
        "WHERE r.sums_to IS NOT NULL AND r.concept_key IS NOT NULL "
        "AND r.concept_key = tot.concept_key").fetchall()
    for doc_id, table_id, row_id, key, lbl, tlbl in same:
        sums_failed.append(
            f"[sums_to] {doc_id}/{table_id}: component {lbl!r} and its total {tlbl!r} "
            f"both stamped {key} (a part cannot equal its whole)")
    record("sums_to_component_vs_total", len(same), sums_failed)

    # (d1)+(d2) nature checks -- pure logic in _flow_as_at_flags/
    # _as_at_magnitude_flags below (unit-tested directly, no DB fixture needed)
    nature_by_key = {c["key"]: c["nature"] for c in load_concepts()}
    cell_rows = con.execute(
        "SELECT doc_id, table_id, row_id, concept_key, row_leaf_label, period_span, "
        "       institution, period, segment_key, geo_key, value_num "
        "FROM v_cell_sumsafe WHERE concept_key IS NOT NULL").fetchall()
    flow_as_at_checked, flow_as_at_failed = _flow_as_at_flags(cell_rows, nature_by_key)
    record("nature_flow_as_at", flow_as_at_checked, flow_as_at_failed)
    magnitude_checked, magnitude_failed = _as_at_magnitude_flags(cell_rows)
    record("nature_as_at_magnitude", magnitude_checked, magnitude_failed)

    report["total_failed"] = sum(c["failed"] for c in report["checks"])
    return report


# --------------------------------------------------------------------------
# STANDING ASSERTION -- see module docstring. Deliberately NOT folded into
# validate()/record() above: those checks return flags for a human to
# adjudicate and must never raise (audit_nature.py and others call validate()
# expecting a report, not an exception). This one is a hard invariant and
# raises on violation.
# --------------------------------------------------------------------------
def assert_single_legal_entity_per_group(con) -> int:
    """Raise AssertionError if any (institution, concept_key, period,
    period_span, segment_key, geo_key, industry_key) group in
    v_fact_metric_serving carries more than one legal_entity. Returns the
    number of groups checked (informational) on success."""
    rows = con.execute("""
        SELECT institution, concept_key, period, period_span, segment_key,
               geo_key, industry_key, COUNT(DISTINCT legal_entity) AS n
        FROM v_fact_metric_serving
        GROUP BY 1,2,3,4,5,6,7
    """).fetchall()
    violations = [r for r in rows if r[7] > 1]
    if violations:
        detail = "; ".join(
            f"{r[0][:24]}/{r[1]}/{r[2]}/{r[3]} seg={r[4]} geo={r[5]} ind={r[6]} "
            f"(n_legal_entity={r[7]})" for r in violations[:10])
        more = f" ... and {len(violations) - 10} more" if len(violations) > 10 else ""
        raise AssertionError(
            f"{len(violations)} group(s) in v_fact_metric_serving resolve to "
            f">1 legal_entity -- the serving-view invariant (one CONSOLIDATED "
            f"row per concept/period/bank) is violated: {detail}{more}")
    return len(rows)


def assert_single_unit_per_concept(con) -> int:
    """Raise AssertionError if any concept_key in v_fact_metric_serving carries
    more than one unit. Returns the number of concepts checked on success.

    A HARD GATE, not a suspicion check, for the same reason as
    assert_single_legal_entity_per_group: a concept serving two units is not a
    cosmetic inconsistency, it is a silent scale error. The failure this guards
    (pre-flight D1) had `ratio.cir` serving 40.4 as '%' and 0.404 as 'percent' —
    the same quantity 100x apart under two spellings, so any consumer averaging
    or charting across periods silently mixed scales.

    NULL counts as a distinct unit: an unstamped unit is exactly as unusable to
    a consumer as a wrong one, and COUNT(DISTINCT unit) would skip it.
    """
    rows = con.execute("""
        SELECT concept_key,
               COUNT(DISTINCT COALESCE(unit, '<null>')) AS n,
               GROUP_CONCAT(DISTINCT COALESCE(unit, '<null>')) AS units
        FROM v_fact_metric_serving
        GROUP BY concept_key
    """).fetchall()
    violations = [r for r in rows if r[1] > 1]
    if violations:
        detail = "; ".join(f"{r[0]} -> {r[2]}" for r in violations[:10])
        more = f" ... and {len(violations) - 10} more" if len(violations) > 10 else ""
        raise AssertionError(
            f"{len(violations)} concept(s) in v_fact_metric_serving carry >1 unit "
            f"-- one canonical unit per concept is a serving invariant "
            f"(concept_dictionary.yaml declares the unit KIND; "
            f"load_dictionary.canonical_unit maps it to the served string): "
            f"{detail}{more}")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    _default_db = Path(__file__).resolve().parents[3] / "findociq" / "db" / "compiled_fs.db"
    ap.add_argument("--db", default=str(_default_db))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        report = validate(con)
        print("=== VALIDATION (suspicion checks -- reviewed, not gating) ===")
        for c in report["checks"]:
            mark = "PASS" if c["failed"] == 0 else "FAIL"
            print(f"  [{mark}] {c['name']}: checked {c['checked']}, "
                  f"passed {c['passed']}, failed {c['failed']}")

        n = assert_single_legal_entity_per_group(con)
        print(f"\n[PASS] legal_entity_uniqueness_serving: {n} group(s) checked, 0 violations")
        n = assert_single_unit_per_concept(con)
        print(f"[PASS] unit_uniqueness_serving: {n} concept(s) checked, 0 violations")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
