"""Column-axis identity — the mirror of `resolve_canonical_leaf`, for columns.

Implements §3/§5/§6 of `docs/specs/2026-08-09-column-axis-identity.md`.

The row side answers "which line item is this?"; this answers "which slice of a
hard axis is this column?" — geography, segment, equity component, fair-value
level, measure. Same provenance rule, stated once and enforced in
`resolve_columns`: every id written to `col_dim.canonical_col_id` is copied
VERBATIM from the masterlist's column block. None is computed, inferred from the
printed label, or minted at load.

WHAT THIS DELIBERATELY DOES NOT TOUCH
  * period columns  — `col_period` / `period_span` already carry them, and
    A DATE IS PERIOD DATA, NEVER IDENTITY. A column that resolved a period is
    never offered to this resolver (Gate 1).
  * derived columns — `col_role='derived_skip'`, stamped upstream in
    `_stamp_identity` stage 1, masterlist-independent.
  * the entity axis — `legal_entity` is a TYPED ATTRIBUTE (spec §3.2): a closed
    3-member enumeration that no bank prints on the row axis. Stamping it here
    as well would encode one axis twice.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from . import resolve_canonical_leaf as RCL

MASTERLIST_DIR = Path(__file__).resolve().parents[4] / "data" / "derived" / "masterlist"

# A hierarchy-1 column under a hard-axis banner routinely prints only the UNIT
# ('$m', 'S$m', "$'000", '%'). The unit is not identity — the spec annotates
# UOB's geography table exactly so — and it must not reach the matcher, or the
# printed chain reads 'Singapore > $m' and no masterlist `full_path` matches.
# The banner alone is the column's printed identity in that shape.
_UNIT_ONLY_RX = re.compile(
    r"^[\s(\[]*(?:[a-z]{0,3}\$|\$)?\s*(?:m|bn|k|000|'000|’000|%|bp|bps|x)\s*[)\]]*$",
    re.I)


def is_unit_only(label: str | None) -> bool:
    return bool(label) and bool(_UNIT_ONLY_RX.match(str(label).strip()))


# Fraction of a candidate's VALUE columns that must resolve against a declared
# column block before that block's table type may claim the table. Mirrors
# MIN_MATCH_FRACTION on the row side, and for the same reason: a single hit
# proves nothing, because hard axes SHARE their tail vocabulary. UOB's
# geography and business-segment exhibits both print 'Others' and 'Total', so
# the segment table matched 2 of 5 geography members on a >=1 rule and was
# stamped FS_PERF_BY_GEOGRAPHY anyway. Measured: geography 7/7 = 1.00,
# segment 2/5 = 0.40.
MIN_COL_MATCH_FRACTION = 0.5


def load_col_members(paths=None) -> dict[tuple[str, str], dict]:
    """Read every `*_masterlist_cols.csv` -> {(bank, table_type_id): entry}.

    entry:
      ids      set of canonical_col_id  (the provenance allowlist)
      by_norm  {norm_path(full_path) -> (canonical_col_id, dim, dim_key)}
      ordered  [(col_ordinal, canonical_col_id, label)]

    Absent or empty -> {}. The caller treats that as "no column block authored
    for this corpus yet" and skips the whole stage, exactly as stage 2 does when
    no row masterlist exists.
    """
    if paths is None:
        paths = sorted(MASTERLIST_DIR.glob("*_masterlist_cols.csv"))
    out: dict[tuple[str, str], dict] = {}
    for p in [Path(x) for x in paths]:
        with p.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = (r["bank"].strip(), r["table_type_id"].strip())
                e = out.setdefault(key, dict(ids=set(), by_norm={}, ordered=[],
                                             source=p.name))
                cid = r["canonical_col_id"].strip()
                fp = (r.get("full_path") or r.get("label") or "").strip()
                e["ids"].add(cid)
                e["by_norm"].setdefault(
                    RCL.norm_path(fp),
                    (cid, (r.get("dim") or "").strip() or None,
                     (r.get("dim_key") or "").strip() or None))
                e["ordered"].append((int(r["col_ordinal"]), cid,
                                     (r.get("label") or "").strip()))
    for e in out.values():
        e["ordered"].sort()
    return out


def printed_col_chain(col_id, by_id: dict) -> str:
    """The column's printed ancestor chain, ' > '-joined, unit-only leaves
    dropped — directly comparable to the masterlist's `full_path`.

    Mirrors `resolve_canonical_leaf._printed_chain` on the row side.
    """
    chain, seen, cur = [], set(), col_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        row = by_id.get(cur)
        if row is None:
            break
        label = row["col_leaf_label"]
        if not is_unit_only(label):
            chain.append(str(label or "").strip())
        cur = row["col_parent"]
    return " > ".join(reversed(chain))


def resolve_columns(cur, doc_id, table_id, bank, table_type_id, entry):
    """Match every VALUE-CARRYING column of ONE table to a column member.

    Returns [{col_id, canonical_col_id, dim, dim_key, printed_path, outcome}],
    outcome in {'matched', 'unresolved', 'skipped_period', 'skipped_derived'}.

    A banner (hierarchy 0) is never stamped: `cell_fact` references the LEAF
    column, so an id parked on a banner is unreachable from the fact table and
    the serving layer would have to walk `col_parent` to find it — which is the
    banned "reason about it in the app" shape. The banner contributes its
    segment through `printed_col_chain` instead.
    """
    cols = [dict(col_id=c[0], col_hierarchy=c[1], col_parent=c[2],
                 col_leaf_label=c[3], col_period=c[4], col_role=c[5])
            for c in cur.execute(
                "SELECT col_id, col_hierarchy, col_parent, col_leaf_label, "
                "       col_period, col_role "
                "FROM col_dim WHERE doc_id = ? AND table_id = ?",
                (doc_id, table_id)).fetchall()]
    by_id = {c["col_id"]: c for c in cols}

    results = []
    for c in cols:
        if (c["col_hierarchy"] or 0) == 0:
            continue                                   # banner: never stamped
        if c["col_period"] is not None:
            results.append(dict(col_id=c["col_id"], canonical_col_id=None,
                                dim=None, dim_key=None, printed_path=None,
                                outcome="skipped_period"))
            continue
        if c["col_role"]:
            # ANY role, not `== 'derived_skip'`. A role means "this column is
            # not a measurement", so every present and future member of the
            # vocabulary must be gated here — an exact-match test silently
            # started OFFERING 'reference_skip' columns to the matcher the
            # moment that role was added (2026-08-14). Same allowlist reasoning
            # as the app's anchor query, which admits `col_role IS NULL` only.
            results.append(dict(col_id=c["col_id"], canonical_col_id=None,
                                dim=None, dim_key=None, printed_path=None,
                                outcome=f"skipped_{c['col_role']}"))
            continue
        path = printed_col_chain(c["col_id"], by_id)
        hit = entry["by_norm"].get(RCL.norm_path(path))
        if hit is None:
            results.append(dict(col_id=c["col_id"], canonical_col_id=None,
                                dim=None, dim_key=None, printed_path=path,
                                outcome="unresolved"))
            continue
        cid, dim, dim_key = hit
        results.append(dict(col_id=c["col_id"], canonical_col_id=cid, dim=dim,
                            dim_key=dim_key, printed_path=path,
                            outcome="matched"))
    return results
