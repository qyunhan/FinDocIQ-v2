"""concept.compute_ratios — compute derived ratios (ROE, NIM, CIR, etc.) AND
derived metrics (e.g. a fill-only non-interest-income estimate) from fact_metric.

Phase C of the FinDocIQ pipeline. Reads formulas from concept_dictionary.yaml,
fetches line items from fact_metric, computes the ratios/metrics, and writes
them back to fact_metric as canonical rows.

Two kinds of entry share the same 'kind: derived' registry, distinguished by
`metric_kind` (defaults to 'ratio' when absent, i.e. every pre-existing entry
keeps today's behaviour of a full recompute every run):
  - metric_kind: ratio (default) - unit is a percent/bps/ratio.
  - metric_kind: metric - unit is a source unit (e.g. 'S$m').

Both kinds are written the same NULL-safe, fill-only-missing way (see
_write_rows): on every run, a key's OWN prior resolved_by='formula' rows are
dropped and freshly recomputed ones inserted -- but ONLY for an identity
(institution, period, period_span, segment_key, geo_key) that has no existing
non-formula (reported/human) row for that concept_key. A reported value always
wins and is NEVER overwritten by a derived one, ratio or metric alike. This
is a deliberate extension beyond the letter of "ratios keep today's
overwrite behaviour": several ratio concept_keys (e.g. ratio.cir, ratio.roe)
turn out to ALSO be reported directly by banks in the corpus (a bank prints
its own CIR/ROE/NIM), so an unconditional INSERT OR REPLACE for ratios was
silently clobbering reported disclosures whenever the engine re-ran -- a
violation of the pipeline's non-negotiable invariant that re-running this
engine only ever touches resolved_by='formula' rows. Recompute-every-run is
preserved for ratios (nothing here makes ratios "sticky" once written); what's
added is only the guard against writing over a reported value.

Before evaluating ANY formula (ratio or metric), the set of concept-key tokens
it references that are not present as columns in the pivot is computed; if
non-empty, the formula is skipped (printed, not raised) instead of hitting a
pandas eval NameError.

Usage:
    python3 findociq/pipeline/concept/compute_ratios.py [--db PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
import yaml
import pandas as pd
import numpy as np
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import canonical_unit, unit_scale  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "findociq" / "db" / "compiled_fs.db"
DICT_PATH = REPO / "findociq" / "pipeline" / "concept" / "concept_dictionary.yaml"

# A dotted concept-key token, e.g. "pnl.noninterest.trading" (mirrors
# validate.py's _KEY_RX). Requires >=1 dot so bare words like the avg()
# function name itself never get mistaken for a concept reference.
_CONCEPT_RX = re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\b")

def _load_dict() -> list[dict]:
    with open(DICT_PATH) as f:
        doc = yaml.safe_load(f)
    return [c for c in doc.get("concepts", []) if c.get("kind") == "derived"]

_INSERT_SQL = """INSERT INTO fact_metric
    (institution, concept_key, concept_name, thesis, period,
     period_span, period_start, segment_key, geo_key, value_num, unit,
     source_doc_id, source_table_id, source_row_label, n_candidates,
     resolved_by)
    VALUES
    (:institution,:concept_key,:concept_name,:thesis,:period,
     :period_span,:period_start,:segment_key,:geo_key,:value_num,:unit,
     :source_doc_id,:source_table_id,:source_row_label,:n_candidates,
     :resolved_by)"""

def _write_rows(conn, rows: list[dict], label: str) -> None:
    """Write freshly-computed derived rows (ratio OR metric) for a re-run:
    every key's OWN prior resolved_by='formula' rows are dropped first (full
    recompute, every run -- 'today's' ratio behaviour, extended uniformly to
    metrics), then rows are (re)inserted -- but ONLY where fact_metric has no
    existing non-formula (reported/human) row at that exact identity.
    A reported value always wins and a derived one -- ratio or metric -- must
    NEVER clobber it; this is the global invariant the pipeline promises
    (re-running the engine touches resolved_by='formula' rows only).
    NULL-safe on period_span ("IS", not "=") since fact_metric's PK includes
    that nullable column and plain INSERT OR REPLACE can't be trusted there
    (two NULLs are never equal for uniqueness purposes -- it would silently
    accumulate duplicate rows instead of replacing)."""
    if not rows:
        return
    cur = conn.cursor()
    keys = sorted({row["concept_key"] for row in rows})

    cur.executemany(
        "DELETE FROM fact_metric WHERE concept_key = ? AND resolved_by = 'formula'",
        [(k,) for k in keys])
    conn.commit()

    existing_reported = set(cur.execute(
        "SELECT institution, concept_key, period, period_span, segment_key, geo_key "
        "FROM fact_metric WHERE concept_key IN ({}) AND resolved_by != 'formula'"
        .format(",".join("?" * len(keys))), keys).fetchall())

    fill_rows = [
        row for row in rows
        if (row["institution"], row["concept_key"], row["period"],
            row["period_span"], row["segment_key"], row["geo_key"])
        not in existing_reported
    ]
    skipped = len(rows) - len(fill_rows)
    if skipped:
        print(f"  fill-only: skipped {skipped} {label} row(s) that already "
              f"have a reported value")

    if fill_rows:
        print(f"Writing {len(fill_rows)} {label} rows to fact_metric...")
        cur.executemany(_INSERT_SQL, fill_rows)
        conn.commit()

def compute(db_path: str | Path):
    ratios = _load_dict()
    if not ratios:
        print("No derived ratios found in dictionary.")
        return

    conn = sqlite3.connect(str(db_path))
    # Load fact_metric into a DataFrame
    # Filter: segment_key='SEG_TOTAL' and geo_key='GLOBAL' for now? 
    # Actually, ratios can apply to segments too (e.g. Segment NIM).
    # We'll compute for all groups present in fact_metric.
    df = pd.read_sql_query("SELECT * FROM fact_metric", conn)
    # concept_key -> the concrete unit its REPORTED rows carry. Used only for
    # CURRENCY metrics, where the dictionary declares the kind but not the
    # printed unit (S$m here; a filing in another currency is the same
    # concept), so the unit has to be inherited from the inputs.
    reported_units: dict[str, str] = {}
    if not df.empty and "resolved_by" in df.columns:
        rep = df[(df["resolved_by"] != "formula") & df["unit"].notna()]
        if not rep.empty:
            reported_units = (rep.groupby("concept_key")["unit"]
                                 .agg(lambda s: s.value_counts().idxmax()).to_dict())
    input_units: dict[str, str] = {}
    for _r in ratios:
        _f = _r.get("formula") or ""
        _inputs = [t for t in _CONCEPT_RX.findall(_f) if t in reported_units]
        if _inputs:
            input_units[_r["key"]] = reported_units[_inputs[0]]
    if df.empty:
        print("fact_metric is empty. Run build_fact_metric first.")
        conn.close()
        return

    # legal_entity is now part of fact_metric's grain (build_fact_metric.py):
    # the SAME concept/period/segment/geo can legitimately carry a
    # CONSOLIDATED row AND a BANK_SOLO/PARENT_COMPANY row (e.g. UOB total
    # assets Group vs Bank). Ratios are computed on the base/CONSOLIDATED
    # slice only -- same treatment as industry_key (also not part of the
    # pivot index: derived rows are inherently the whole-book slice) -- both
    # to avoid a duplicate-index pivot crash and, more importantly, so a
    # BANK_SOLO input never gets silently written out under the
    # legal_entity DEFAULT ('CONSOLIDATED') as if it were the Group ratio.
    n_before = len(df)
    df = df[df["legal_entity"] == "CONSOLIDATED"]
    if len(df) != n_before:
        print(f"  legal_entity filter: {n_before - len(df)} non-CONSOLIDATED "
              f"row(s) excluded from ratio inputs ({len(df)} remain)")
    if df.empty:
        print("fact_metric has no CONSOLIDATED rows. Nothing to compute.")
        conn.close()
        return

    # Pivot so concepts are columns
    # Index: institution, period, period_span, segment_key, geo_key
    idx_cols = ["institution", "period", "period_span", "segment_key", "geo_key"]
    pivoted = df.pivot(index=idx_cols, columns="concept_key", values="value_num")
    
    # Sort by period within each group to support avg() lookup.
    # period_span is part of the grouping key: quarterly and YTD rows form
    # separate series, so a Q avg() must not pull a YTD spot as its "prev".
    pivoted = pivoted.sort_index(
        level=["institution", "segment_key", "geo_key", "period_span", "period"])

    # Results to append back: ratios keep today's behaviour (INSERT OR REPLACE,
    # every run); metrics are fill-only and written separately below.
    new_rows = []
    metric_new_rows = []

    for r in ratios:
        key = r["key"]
        formula = r.get("formula")
        metric_kind = r.get("metric_kind", "ratio")
        if not formula:
            continue

        print(f"Computing {key}: {formula}...")

        # 0. Missing-input guard. Any dotted concept-key token the formula
        # references (bare or inside avg(...)) that isn't a pivot column means
        # this corpus has no data for that concept yet -- skip the eval
        # entirely (would otherwise be a pandas eval NameError) rather than
        # crash or silently fabricate a result from a fabricated NaN column.
        stripped = re.sub(r"avg\(([^)]+)\)", r"\1", formula)
        referenced = set(_CONCEPT_RX.findall(stripped))
        missing = sorted(c for c in referenced if c not in pivoted.columns)
        if missing:
            print(f"  skip {key}: missing concepts {missing}")
            continue

        # Simple formula evaluator
        # Supports: +, -, *, /, (), avg(concept)

        # 1. Handle avg(concept) -> (concept + prev_concept) / 2
        # We find all avg(...) patterns
        avgs = re.findall(r"avg\(([^)]+)\)", formula)
        eval_formula = formula
        for a_concept in avgs:
            # Create a shifted column for this concept
            prev_col = f"{a_concept}_prev"
            if a_concept not in pivoted.columns:
                pivoted[a_concept] = np.nan
            
            # Group by institution/segment/geo and shift period
            pivoted[prev_col] = pivoted.groupby(
                level=["institution", "segment_key", "geo_key", "period_span"])[a_concept].shift(1)
            
            # Replace avg(concept) with ((concept + concept_prev)/2)
            # Note: if prev is NaN, we might want to just use current?
            # Industry standard for ROE is average equity. If only one period, 
            # some use spot. We'll use (current + prev)/2, and if prev is NaN, 
            # we'll use current (spot).
            pivoted[f"{a_concept}_avg"] = pivoted[[a_concept, prev_col]].mean(axis=1)
            eval_formula = eval_formula.replace(f"avg({a_concept})", f"`{a_concept}_avg`")

        # 2. Prepare concepts for eval (wrap in backticks to handle dots)
        # Find all concept keys (word.word...). Must allow underscores (e.g.
        # bs.liabilities.deposits_casa) -- the same _CONCEPT_RX as the
        # missing-input guard above, else an underscored concept silently
        # fails to get backtick-wrapped and pandas.eval throws a NameError on
        # its first dotted segment (e.g. "name 'bs' is not defined").
        concepts_in_formula = _CONCEPT_RX.findall(eval_formula)
        for c in concepts_in_formula:
            if c in pivoted.columns and not c.endswith("_avg"):
                eval_formula = re.sub(rf"\b{re.escape(c)}\b", f"`{c}`", eval_formula)
        
        try:
            # Use pandas eval
            result_series = pivoted.eval(eval_formula)

            # The dictionary declares a unit KIND; served rows must carry a
            # concrete unit STRING, and the formula's mathematical result must
            # be lifted into that unit's scale (fraction -> percentage points).
            # Writing the raw kind here is what made one concept serve both
            # 'percent' (0.404) and '%' (40.4) — pre-flight D1. For a CURRENCY
            # metric the dictionary cannot name the unit, so inherit it from
            # the inputs this formula was actually computed from.
            kind = r.get("unit")
            scale = unit_scale(kind)
            out_unit = canonical_unit(kind) or input_units.get(key)

            # Map back to fact_metric rows
            for idx, val in result_series.items():
                if pd.isna(val) or np.isinf(val):
                    continue

                # Reconstruct keys from index
                inst, period, span, seg, geo = idx

                row = {
                    "institution": inst,
                    "concept_key": key,
                    "concept_name": r.get("name"),
                    "thesis": ",".join(r.get("thesis", [])),
                    "period": period,
                    "period_span": span,
                    "period_start": None, # Could be derived but optional
                    "segment_key": seg,
                    "geo_key": geo,
                    "value_num": float(val) * scale,
                    "unit": out_unit,
                    "source_doc_id": "derived",
                    "source_table_id": "formula",
                    "source_row_label": formula,
                    "n_candidates": 1,
                    "resolved_by": "formula"
                }
                if metric_kind == "metric":
                    metric_new_rows.append(row)
                else:
                    new_rows.append(row)
        except Exception as e:
            print(f"  Error evaluating {key}: {e}")

    _write_rows(conn, new_rows, "derived-ratio")
    _write_rows(conn, metric_new_rows, "derived-metric")

    conn.close()

# ---------------------------------------------------------------------------
# SEGMENT ROLL-UP  (concept_dictionary.yaml `segment_partitions:`)
# ---------------------------------------------------------------------------
#: the full analytic grain, minus segment_key. Every member of a partition must
#: agree on ALL of these before their values may be added together.
_ROLLUP_GRAIN = ("institution", "concept_key", "period", "period_span",
                 "geo_key", "industry_key", "legal_entity", "unit")


def _load_partitions(path: Path = DICT_PATH) -> list[dict]:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    return list(doc.get("segment_partitions") or [])


def segment_rollup(conn, partitions=None, concept_units=None) -> dict:
    """Recover a parent segment slice by summing a declared, exhaustive
    partition of member segments -- FALLBACK ONLY.

    Generality: the partition is declared once per BANK, and applies to EVERY
    additive concept measured at those segments. Nothing here names a concept.

    Three guards, each of which can silently corrupt data if skipped:

      * ADDITIVE ONLY. Summing across segments is meaningful for an amount
        (currency), never for an intensive quantity -- a cost-income ratio or an
        EPS does not add across business units. Keyed off the concept's declared
        unit KIND, so percent/bps/per_share are excluded by construction.

      * GRAIN MATCH. Members must agree on institution, period, period_span,
        geo_key, industry_key, legal_entity AND unit; only segment_key may
        differ. Enforced by construction (the grain tuple is the dict key), and
        a partition whose members disagree simply never forms a complete group,
        so mismatched data is never summed.

      * FILL ONLY. A parent slot that already carries a value after
        build_fact_metric's tier resolution is left alone -- a direct anchor or
        a statutory-statement row always outranks a computed sum. This is what
        keeps DBS's pnl.nii.net FY/2H (14,500 / 7,171, resolved_by
        'prefer_table') untouched while still supplying the quarters where no
        group row was printed.

    Returns a report dict; writes rows with resolved_by='segment_rollup' so a
    summed parent is never mistaken for a reported one.
    """
    partitions = _load_partitions() if partitions is None else partitions
    if not partitions:
        return {"written": 0, "skipped_existing": 0, "skipped_non_additive": 0,
                "incomplete": 0, "unknown_members": []}
    if concept_units is None:
        doc = yaml.safe_load(Path(DICT_PATH).read_text()) or {}
        concept_units = {c["key"]: c.get("unit") for c in doc.get("concepts", [])}

    cur = conn.cursor()
    cols = ", ".join(_ROLLUP_GRAIN)
    rows = cur.execute(
        f"SELECT {cols}, segment_key, value_num, concept_name, thesis, period_start "
        f"FROM fact_metric WHERE value_num IS NOT NULL").fetchall()
    n = len(_ROLLUP_GRAIN)
    by_grain: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        by_grain[tuple(r[:n])][r[n]] = r[n + 1:]

    written = skipped_existing = skipped_non_additive = incomplete = 0
    unknown: list[str] = []
    new_rows: list[dict] = []
    for part in partitions:
        bank, parent = part["bank"], part["parent"]
        members = list(part["members"])
        seen_member = False
        for grain, segs in by_grain.items():
            inst, ck = grain[0], grain[1]
            if bank.lower() not in (inst or "").lower():
                continue
            if not all(m in segs for m in members):
                if any(m in segs for m in members):
                    incomplete += 1
                    seen_member = True
                continue
            seen_member = True
            if concept_units.get(ck) != "currency":
                skipped_non_additive += 1
                continue
            if parent in segs:
                skipped_existing += 1        # a real value outranks the sum
                continue
            g = dict(zip(_ROLLUP_GRAIN, grain))
            rep = segs[members[0]]
            new_rows.append({
                "institution": g["institution"], "concept_key": ck,
                "concept_name": rep[1], "thesis": rep[2],
                "period": g["period"], "period_span": g["period_span"],
                "period_start": rep[3], "segment_key": parent,
                "geo_key": g["geo_key"], "industry_key": g["industry_key"],
                "legal_entity": g["legal_entity"], "unit": g["unit"],
                "value_num": sum(segs[m][0] for m in members),
                "unit_source": "segment_rollup", "source_doc_id": "derived",
                "source_table_id": "segment_rollup",
                "source_row_label": f"{parent} = " + " + ".join(members),
                "n_candidates": len(members), "resolved_by": "segment_rollup",
            })
            written += 1
        if not seen_member:
            unknown.append(f"{bank}/{parent}: no fact_metric row for any of {members}")

    if new_rows:
        cur.executemany(_ROLLUP_INSERT_SQL, new_rows)
        conn.commit()
    return {"written": written, "skipped_existing": skipped_existing,
            "skipped_non_additive": skipped_non_additive,
            "incomplete": incomplete, "unknown_members": unknown}


_ROLLUP_INSERT_SQL = """
    INSERT INTO fact_metric
    (institution, concept_key, concept_name, thesis, period, period_span,
     period_start, segment_key, geo_key, industry_key, legal_entity, value_num,
     unit, unit_source, source_doc_id, source_table_id, source_row_label,
     n_candidates, resolved_by)
    VALUES
    (:institution,:concept_key,:concept_name,:thesis,:period,:period_span,
     :period_start,:segment_key,:geo_key,:industry_key,:legal_entity,:value_num,
     :unit,:unit_source,:source_doc_id,:source_table_id,:source_row_label,
     :n_candidates,:resolved_by)
"""


def main():
    ap = argparse.ArgumentParser(description="Compute derived ratios.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    
    compute(args.db)

    # Segment roll-up runs LAST: it may only fill parent slices still empty
    # after tier resolution AND after the formula passes.
    conn = sqlite3.connect(str(args.db))
    try:
        r = segment_rollup(conn)
    finally:
        conn.close()
    print(f"\nsegment roll-up: wrote {r['written']} parent slice(s) "
          f"(skipped {r['skipped_existing']} with a direct value, "
          f"{r['skipped_non_additive']} non-additive, {r['incomplete']} incomplete)")
    for u in r["unknown_members"]:
        print(f"  [segment-rollup WARN] {u}")

if __name__ == "__main__":
    main()
