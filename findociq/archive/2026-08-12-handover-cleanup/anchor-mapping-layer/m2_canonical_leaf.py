"""m2_canonical_leaf — M2 gate: per-bank identity persistence.

THE MASTERLIST WRITER (level L2 — line item). `docs/specs/2026-08-04-masterlist.md`
is authoritative for what the masterlist is and where it is stored; this is the
one script that writes L2. Do not add a second script that stores L2 masterlist
state -- extend this one.

Architecture (see docs/DECISIONS.md 2026-08-04 "M2 gate" entries for the
three explicit decisions this module encodes):

  M1 (Stage 1, not this module)  -- JSON matches PDF.
  M2 (this module)               -- the masterlist declares, for each
                                     (bank, table_type_id), an ORDERED set of
                                     canonical leaves. Every ingested row must
                                     resolve to exactly one canonical leaf
                                     (direct match or via alias) or the gate
                                     flags it. Same table_type_id across banks
                                     does NOT imply same leaves -- bank-scoped
                                     by design (`bank_line_map`'s own
                                     convention, matched here).
  M3 (concept_key bindings, verified not built here) -- lineage_identity_map/
                                     bank_line_map bind concept_key -> (bank,
                                     canonical_leaf). Bindings inherit
                                     stability from M2; `verify_concept_bindings`
                                     checks this, does not construct it.

Two new tables (additive; nothing here modifies `bank_line_map` addresses,
`fact_metric`, or the extraction pipeline):

  canonical_leaf        -- (bank, table_type_id, canonical_leaf_id) -> the
                            declared identity: label_current, position
                            (ordered), added_quarter, deprecated_quarter,
                            notes. `canonical_leaf_id` is scoped to
                            (bank, table_type_id), NOT globally unique --
                            it's just `row_label_norm` (or
                            `parent_label_norm::row_label_norm` when there's a
                            parent), reusing bank_line_map's own addressing
                            convention rather than inventing a new ID space.
  canonical_leaf_alias   -- (bank, table_type_id, alias_row_label_norm,
                            alias_parent_label_norm) -> canonical_leaf_id.
                            Populated conservatively (see
                            `populate_aliases`'s docstring for exactly what
                            gets auto-aliased vs left for the unresolved
                            report) -- ambiguous cases are reported, never
                            guessed.

    python3 findociq/pipeline/mapping/m2_canonical_leaf.py --db findociq/db/compiled_fs.db --bank OCBC
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
sys.path.insert(0, str(_HERE.parent))
from mapping.normalize import normalize_row_label  # noqa: E402
from mapping.registry import bank_of  # noqa: E402

# Decision 1 (order source): the benchmark document's real printed row order,
# same mechanism app/findociq_app.py's line_item_benchmark_frame already uses
# for the same reason -- see docs/DECISIONS.md.
BENCHMARK_PERIOD = "2025-12-31"   # 4Q25
BENCHMARK_LABEL = "4Q25"

_TRAILING_DIGIT_RE = re.compile(r"_\d+$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quarter_of(doc_period: str | None) -> str | None:
    """'2025-12-31' -> '4Q25'. Best-effort, calendar-quarter based (matches
    how this corpus's doc_ids/labels already talk about periods)."""
    if not doc_period:
        return None
    try:
        y, m, _d = doc_period.split("-")
    except ValueError:
        return None
    q = {("03"): 1, ("06"): 2, ("09"): 3, ("12"): 4}.get(m)
    if q is None:
        return None
    return f"{q}Q{y[2:]}"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS canonical_leaf (
        bank TEXT NOT NULL,
        table_type_id TEXT NOT NULL,
        canonical_leaf_id TEXT NOT NULL,
        row_label_norm TEXT NOT NULL,
        parent_label_norm TEXT NOT NULL DEFAULT '',
        label_current TEXT NOT NULL,
        position INTEGER NOT NULL,
        added_quarter TEXT,
        deprecated_quarter TEXT,
        notes TEXT,
        PRIMARY KEY (bank, table_type_id, canonical_leaf_id)
    )""")
    con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_canonical_leaf_position
        ON canonical_leaf (bank, table_type_id, position)""")
    con.execute("""CREATE TABLE IF NOT EXISTS canonical_leaf_alias (
        bank TEXT NOT NULL,
        table_type_id TEXT NOT NULL,
        alias_row_label_norm TEXT NOT NULL,
        alias_parent_label_norm TEXT NOT NULL DEFAULT '',
        canonical_leaf_id TEXT NOT NULL,
        source TEXT NOT NULL,
        added_at TEXT NOT NULL,
        PRIMARY KEY (bank, table_type_id, alias_row_label_norm, alias_parent_label_norm)
    )""")
    con.commit()


# --------------------------------------------------------------- addressing
# Deliberately self-contained rather than importing app/findociq_app.py
# (a Streamlit module, not safe to import from pipeline code) or refactoring
# migrate_serving_views.stamp_human_anchors (already-shipped, tested DB-write
# logic -- not worth the regression risk under this task's time budget).
# This is the third near-identical copy of the title-like-parent-collapse
# rule in this codebase (stamp_human_anchors, app's _ordered_row_addresses,
# this one) -- a real DRY debt, flagged in docs/DECISIONS.md, not fixed here.
def ordered_addresses(row_dim_rows: list[dict]) -> list[tuple[tuple[str, str], dict]]:
    """`[(  (row_label_norm, parent_label_norm), row  ), ...]` sorted by
    `(table_id, row_id)` -- table_id first because row_id restarts at 1 per
    table_id, and a caller may legitimately pass rows from more than one
    table_id sharing a table_type_id (real case: OCBC's FS_CAPITAL_ADEQUACY
    has two distinct physical tables in the same 4Q25 document)."""
    rows = list(row_dim_rows)
    parent_at_depth: dict[tuple[str, str, int], set] = {}
    for r in rows:
        depth = r["depth"]
        if depth >= 2:
            lvls = [None, r.get("lvl1"), r.get("lvl2"), r.get("lvl3"), r.get("lvl4"), r.get("lvl5")]
            key = (r["doc_id"], r["table_id"], depth)
            parent_at_depth.setdefault(key, set()).add(lvls[depth - 1])
    title_like = {k: len(v) <= 1 for k, v in parent_at_depth.items()}

    out = []
    for r in sorted(rows, key=lambda r: (r["table_id"], r["row_id"])):
        depth = r["depth"]
        lvls = [None, r.get("lvl1"), r.get("lvl2"), r.get("lvl3"), r.get("lvl4"), r.get("lvl5")]
        leaf = r.get("row_leaf_label") or (lvls[depth] if depth <= 5 else None)
        raw_parent = lvls[depth - 1] if depth >= 2 else None
        key = (r["doc_id"], r["table_id"], depth)
        parent = None if title_like.get(key, depth == 1) else raw_parent
        row_label_norm = normalize_row_label(leaf)
        parent_label_norm = normalize_row_label(parent) if parent else ""
        out.append(((row_label_norm, parent_label_norm), r))
    return out


def canonical_leaf_id_of(row_label_norm: str, parent_label_norm: str) -> str:
    return f"{parent_label_norm}::{row_label_norm}" if parent_label_norm else row_label_norm


# ---------------------------------------------------- benchmark selection
def select_benchmark_rows(con: sqlite3.Connection, bank: str, table_type_id: str) -> dict:
    """Picks the benchmark document for (bank, table_type_id): the
    BENCHMARK_PERIOD (4Q25) instance if a non-`dedup_status`-tagged one
    exists, else falls back to the most recent available period -- same
    fallback shape as `app/findociq_app.py`'s line_item_benchmark_frame, for
    the same reason (a table type not captured at 4Q25 for this bank
    shouldn't make the whole gate empty for it).

    Returns {'row_dim_rows': [...], 'doc_id':..., 'period_used':...,
    'is_fallback': bool} or {'row_dim_rows': [], ...} if nothing live exists
    at all."""
    candidates = con.execute("""
        SELECT d.institution, d.doc_period, t.doc_id, t.table_id, rd.row_id,
               rd.row_leaf_label, rl.lvl1, rl.lvl2, rl.lvl3, rl.lvl4, rl.lvl5, rl.depth
        FROM table_t t
        JOIN document d ON d.doc_id = t.doc_id
        JOIN row_dim rd ON rd.doc_id = t.doc_id AND rd.table_id = t.table_id
        JOIN row_lineage rl ON rl.row_lineage_id = rd.row_lineage_id
        WHERE t.table_type_id = ?
          AND d.doc_family = 'financial_stmt'
          AND (t.dedup_status IS NULL OR t.dedup_status = '')
    """, (table_type_id,)).fetchall()
    cols = ["institution", "doc_period", "doc_id", "table_id", "row_id",
            "row_leaf_label", "lvl1", "lvl2", "lvl3", "lvl4", "lvl5", "depth"]
    rows = [dict(zip(cols, r)) for r in candidates if bank_of(r[0]) == bank]
    if not rows:
        return {"row_dim_rows": [], "doc_id": None, "period_used": None, "is_fallback": False}

    on_benchmark = [r for r in rows if r["doc_period"] == BENCHMARK_PERIOD]
    if on_benchmark:
        doc_id = on_benchmark[0]["doc_id"]
        is_fallback = False
    else:
        doc_id = max(rows, key=lambda r: r["doc_period"])["doc_id"]
        is_fallback = True
    picked = [r for r in rows if r["doc_id"] == doc_id]
    return {"row_dim_rows": picked, "doc_id": doc_id,
            "period_used": picked[0]["doc_period"], "is_fallback": is_fallback}


def table_types_in_fact_metric(con: sqlite3.Connection, bank: str) -> list[str]:
    """Distinct table_type_id currently backing this bank's fact_metric rows
    (post-dedup, since build_fact_metric.py now excludes dedup_status-tagged
    tables) -- the scope this task asks canonical_leaf to be built for."""
    rows = con.execute("""
        SELECT DISTINCT f.institution, t.table_type_id
        FROM fact_metric f
        LEFT JOIN table_t t ON t.doc_id = f.source_doc_id AND t.table_id = f.source_table_id
    """).fetchall()
    return sorted({tt for inst, tt in rows if tt and bank_of(inst) == bank})


# ------------------------------------------------------- canonical leaf build
def populate_canonical_leaves(con: sqlite3.Connection, bank: str) -> dict:
    """Idempotent: re-running recomputes position/label_current for the same
    addresses (benchmark instance can shift as new periods are ingested) and
    ADDS any newly-appeared leaf; never removes a row here (deprecation is a
    separate, explicit decision -- see `deprecate_missing_leaves`)."""
    ensure_schema(con)
    stats = {"table_types": 0, "leaves_written": 0, "table_types_with_no_benchmark": []}
    now_q = None
    for table_type_id in table_types_in_fact_metric(con, bank):
        stats["table_types"] += 1
        bench = select_benchmark_rows(con, bank, table_type_id)
        if not bench["row_dim_rows"]:
            stats["table_types_with_no_benchmark"].append(table_type_id)
            continue
        now_q = now_q or _quarter_of(bench["period_used"])
        for position, (address, row) in enumerate(ordered_addresses(bench["row_dim_rows"])):
            row_label_norm, parent_label_norm = address
            leaf_id = canonical_leaf_id_of(row_label_norm, parent_label_norm)
            existing = con.execute(
                "SELECT added_quarter FROM canonical_leaf WHERE bank=? AND table_type_id=? AND canonical_leaf_id=?",
                (bank, table_type_id, leaf_id)).fetchone()
            added_quarter = existing[0] if existing else _quarter_of(bench["period_used"])
            con.execute("""
                INSERT INTO canonical_leaf
                    (bank, table_type_id, canonical_leaf_id, row_label_norm,
                     parent_label_norm, label_current, position, added_quarter,
                     deprecated_quarter, notes)
                VALUES (?,?,?,?,?,?,?,?,NULL,?)
                ON CONFLICT(bank, table_type_id, canonical_leaf_id) DO UPDATE SET
                    label_current=excluded.label_current,
                    position=excluded.position
            """, (bank, table_type_id, leaf_id, row_label_norm, parent_label_norm,
                  row.get("row_leaf_label") or row_label_norm, position, added_quarter,
                  f"benchmark={bench['doc_id']}"
                  + (" (fallback, no 4Q25 instance)" if bench["is_fallback"] else "")))
            stats["leaves_written"] += 1
    con.commit()
    return stats


# --------------------------------------------------------------- aliasing
def populate_aliases(con: sqlite3.Connection, bank: str) -> dict:
    """Conservative, auto-applied alias detection -- ONLY the case that's
    deterministic and explainable without human judgment:

      a `bank_line_map` address for this (bank, table_type_id) whose
      row_label_norm, with a trailing `_<digits>` footnote-marker residue
      stripped, EXACTLY equals an EXISTING canonical_leaf's row_label_norm
      (also footnote-stripped for symmetry), under the SAME parent_label_norm
      (footnote-stripped the same way).

    This exists because `normalize_row_label`'s footnote-stripping regexes
    don't catch every raw-text footnote format a given period's extraction
    produced (confirmed: DBS's bank_line_map carries `diluted` (current) AND
    `diluted_9` (an older period's leftover) as two distinct addresses for
    what is the same real line) -- this is the SAME failure mode at the
    row_label_norm layer instead of the raw-text layer, so the same kind of
    regex strip resolves it.

    Anything else -- a different parent, a genuinely different label, a
    concept_key match with no label similarity -- is NOT auto-aliased. It's
    surfaced in the unresolved report with whatever context is available
    (e.g. "shares concept_key X with canonical leaf Y") so a human can
    decide, per this task's "do NOT auto-decide" requirement."""
    ensure_schema(con)
    stats = {"aliases_written": 0}
    for table_type_id in table_types_in_fact_metric(con, bank):
        canon_rows = con.execute(
            "SELECT canonical_leaf_id, row_label_norm, parent_label_norm FROM canonical_leaf "
            "WHERE bank=? AND table_type_id=?", (bank, table_type_id)).fetchall()
        if not canon_rows:
            continue
        by_stripped: dict[tuple[str, str], str] = {}
        for leaf_id, rln, pln in canon_rows:
            key = (_TRAILING_DIGIT_RE.sub("", rln), _TRAILING_DIGIT_RE.sub("", pln))
            by_stripped.setdefault(key, leaf_id)

        blm_rows = con.execute(
            "SELECT row_label_norm, parent_label_norm FROM bank_line_map "
            "WHERE bank=? AND table_type_id=? AND map_status != 'deprecated'",
            (bank, table_type_id)).fetchall()
        canon_addrs = {(rln, pln) for _lid, rln, pln in canon_rows}
        for rln, pln in blm_rows:
            pln = pln or ""
            if (rln, pln) in canon_addrs:
                continue  # direct match, not an alias
            stripped_key = (_TRAILING_DIGIT_RE.sub("", rln), _TRAILING_DIGIT_RE.sub("", pln))
            target = by_stripped.get(stripped_key)
            if target is None or stripped_key == (rln, pln):
                continue  # no footnote-stripped match, or nothing to strip -> not this rule
            con.execute("""
                INSERT INTO canonical_leaf_alias
                    (bank, table_type_id, alias_row_label_norm, alias_parent_label_norm,
                     canonical_leaf_id, source, added_at)
                VALUES (?,?,?,?,?,'footnote_variant_heuristic',?)
                ON CONFLICT(bank, table_type_id, alias_row_label_norm, alias_parent_label_norm)
                DO UPDATE SET canonical_leaf_id=excluded.canonical_leaf_id
            """, (bank, table_type_id, rln, pln, target, _now()))
            stats["aliases_written"] += 1
    con.commit()
    return stats


# ------------------------------------------------------------- resolution
def resolve_address(con: sqlite3.Connection, bank: str, table_type_id: str,
                     row_label_norm: str, parent_label_norm: str) -> dict:
    """THE GATE FUNCTION (Decision 2's priority order):
    exact current label > alias table > deprecated leaf label > unresolved.

    Returns exactly one of:
      {'status': 'resolved', 'canonical_leaf_id':..., 'via': 'direct'|'alias'|'deprecated'}
      {'status': 'unresolved', 'reason': ...}
    Never both, never neither -- callers can rely on 'status' alone."""
    parent_label_norm = parent_label_norm or ""
    row = con.execute(
        "SELECT canonical_leaf_id, deprecated_quarter FROM canonical_leaf "
        "WHERE bank=? AND table_type_id=? AND row_label_norm=? AND parent_label_norm=?",
        (bank, table_type_id, row_label_norm, parent_label_norm)).fetchone()
    if row and row[1] is None:
        return {"status": "resolved", "canonical_leaf_id": row[0], "via": "direct"}

    alias_row = con.execute(
        "SELECT canonical_leaf_id FROM canonical_leaf_alias "
        "WHERE bank=? AND table_type_id=? AND alias_row_label_norm=? AND alias_parent_label_norm=?",
        (bank, table_type_id, row_label_norm, parent_label_norm)).fetchone()
    if alias_row:
        return {"status": "resolved", "canonical_leaf_id": alias_row[0], "via": "alias"}

    if row and row[1] is not None:
        return {"status": "resolved", "canonical_leaf_id": row[0], "via": "deprecated"}

    if not con.execute(
            "SELECT 1 FROM canonical_leaf WHERE bank=? AND table_type_id=? LIMIT 1",
            (bank, table_type_id)).fetchone():
        return {"status": "unresolved",
                "reason": f"no canonical leaf set exists yet for ({bank}, {table_type_id})"}
    return {"status": "unresolved",
            "reason": "no direct, alias, or deprecated canonical_leaf match"}


def _row_dim_rows_for_table(con: sqlite3.Connection, doc_id: str, table_id: str) -> list[dict]:
    rows = con.execute("""
        SELECT rd.doc_id, rd.table_id, rd.row_id, rd.row_leaf_label,
               rl.lvl1, rl.lvl2, rl.lvl3, rl.lvl4, rl.lvl5, rl.depth
        FROM row_dim rd
        JOIN row_lineage rl ON rl.row_lineage_id = rd.row_lineage_id
        WHERE rd.doc_id = ? AND rd.table_id = ?
    """, (doc_id, table_id)).fetchall()
    cols = ["doc_id", "table_id", "row_id", "row_leaf_label",
            "lvl1", "lvl2", "lvl3", "lvl4", "lvl5", "depth"]
    return [dict(zip(cols, r)) for r in rows]


# ------------------------------------------------------- fact_metric gate
def verify_fact_metric(con: sqlite3.Connection, bank: str) -> dict:
    """M2 acceptance criterion 2: every fact_metric row for `bank` resolves
    to exactly one canonical leaf. Traces each row back to its source
    (doc_id, table_id) address by matching `normalize_row_label(source_row_label)`
    against that table's own computed addresses (row_id alone isn't stored on
    fact_metric, only the raw label text, so this is a label-based rematch --
    ambiguous when >1 row in the table shares the same normalized label AND
    those rows resolve to different canonical leaves; reported, not guessed).

    Returns {'resolved': [...], 'unresolved': [...], 'ambiguous': [...]}."""
    rows = con.execute("""
        SELECT rowid, concept_key, institution, period, source_doc_id,
               source_table_id, source_row_label, resolved_by
        FROM fact_metric
    """).fetchall()

    resolved, unresolved, ambiguous = [], [], []
    table_type_cache: dict[tuple[str, str], str | None] = {}
    row_dim_cache: dict[tuple[str, str], list[dict]] = {}

    for rowid, concept_key, institution, period, doc_id, table_id, source_row_label, resolved_by in rows:
        if bank_of(institution) != bank:
            continue
        key = (doc_id, table_id)
        if key not in table_type_cache:
            r = con.execute(
                "SELECT table_type_id FROM table_t WHERE doc_id=? AND table_id=?",
                (doc_id, table_id)).fetchone()
            table_type_cache[key] = r[0] if r else None
        table_type_id = table_type_cache[key]

        base = {"concept_key": concept_key, "period": period, "source_doc_id": doc_id,
                "source_table_id": table_id, "source_row_label": source_row_label,
                "resolved_by": resolved_by, "table_type_id": table_type_id}

        if not table_type_id:
            unresolved.append({**base, "reason": "source table_t row not found or has no table_type_id"})
            continue

        if key not in row_dim_cache:
            row_dim_cache[key] = _row_dim_rows_for_table(con, doc_id, table_id)
        target_norm = normalize_row_label(source_row_label)
        candidate_addrs = {addr for addr, _r in ordered_addresses(row_dim_cache[key])
                            if addr[0] == target_norm}

        if not candidate_addrs:
            unresolved.append({**base, "reason": "source_row_label matches no row_dim address in its own table"})
            continue

        resolutions = {addr: resolve_address(con, bank, table_type_id, *addr) for addr in candidate_addrs}
        resolved_ids = {r["canonical_leaf_id"] for r in resolutions.values() if r["status"] == "resolved"}

        if len(candidate_addrs) == 1:
            addr, res = next(iter(resolutions.items()))
            if res["status"] == "resolved":
                resolved.append({**base, "row_label_norm": addr[0], "parent_label_norm": addr[1], **res})
            else:
                unresolved.append({**base, "row_label_norm": addr[0], "parent_label_norm": addr[1], **res})
        elif len(resolved_ids) == 1 and all(r["status"] == "resolved" for r in resolutions.values()):
            # >1 candidate address, but all agree on the same canonical leaf -- not really ambiguous
            addr, res = next(iter(resolutions.items()))
            resolved.append({**base, "row_label_norm": addr[0], "parent_label_norm": addr[1], **res})
        else:
            ambiguous.append({**base, "candidates": [
                {"row_label_norm": a[0], "parent_label_norm": a[1], **r}
                for a, r in resolutions.items()]})

    return {"resolved": resolved, "unresolved": unresolved, "ambiguous": ambiguous}


# ---------------------------------------------------- M3 concept-binding check
SHIFTED_CONCEPTS = ["bs.nav_per_share", "pnl.eps.basic", "pnl.eps.diluted", "reg.capital.cet1_ratio"]


def verify_concept_bindings(con: sqlite3.Connection, bank: str) -> dict:
    """M2 acceptance criterion 3: for every concept_key with a `bank` binding
    in `bank_line_map`, the bound (table_type_id, row_label_norm,
    parent_label_norm) resolves via the gate. `SHIFTED_CONCEPTS` (the 4
    concepts the OCBC dedup fix touched) get flagged explicitly regardless of
    outcome, per the task's explicit ask -- not just folded into the general
    pass/fail list."""
    rows = con.execute("""
        SELECT concept_key, table_type_id, row_label_norm, parent_label_norm, map_status
        FROM bank_line_map
        WHERE bank = ? AND concept_key IS NOT NULL AND map_status != 'deprecated'
    """, (bank,)).fetchall()

    results = []
    for concept_key, table_type_id, rln, pln, map_status in rows:
        res = resolve_address(con, bank, table_type_id, rln, pln or "")
        results.append({
            "concept_key": concept_key, "table_type_id": table_type_id,
            "row_label_norm": rln, "parent_label_norm": pln or "",
            "map_status": map_status, **res,
        })

    by_concept: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_concept[r["concept_key"]].append(r)

    shifted = {ck: by_concept.get(ck, []) for ck in SHIFTED_CONCEPTS}
    n_ok = sum(1 for r in results if r["status"] == "resolved")
    n_bad = sum(1 for r in results if r["status"] == "unresolved")
    return {"all": results, "by_concept": dict(by_concept), "shifted": shifted,
            "n_resolved": n_ok, "n_unresolved": n_bad}


# --------------------------------------------------------------- reports
def write_canonical_report(con: sqlite3.Connection, bank: str, path: Path) -> None:
    rows = con.execute("""
        SELECT table_type_id, canonical_leaf_id, position, label_current,
               parent_label_norm, added_quarter, deprecated_quarter, notes
        FROM canonical_leaf WHERE bank=? ORDER BY table_type_id, position
    """, (bank,)).fetchall()
    alias_counts = dict(con.execute(
        "SELECT canonical_leaf_id, COUNT(*) FROM canonical_leaf_alias WHERE bank=? GROUP BY canonical_leaf_id",
        (bank,)).fetchall())

    lines = [f"# M2 canonical leaf set — {bank}\n",
             f"Built from each (bank, table_type_id)'s {BENCHMARK_LABEL} benchmark instance "
             f"(falls back to the most recent available period if {BENCHMARK_LABEL} wasn't "
             f"captured for that table_type_id — see per-table_type note below when that "
             f"happened). Scope: every table_type_id currently backing a `{bank}` "
             f"`fact_metric` row, post-dedup.\n",
             f"Total canonical leaves: **{len(rows)}** across "
             f"**{len(set(r[0] for r in rows))}** table_type_ids.\n"]

    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r[0]].append(r)

    for table_type_id, leaves in sorted(by_type.items()):
        lines.append(f"## `{table_type_id}` ({len(leaves)} leaves)\n")
        lines.append("| position | canonical_leaf_id | label_current | aliases | status |")
        lines.append("|---|---|---|---|---|")
        for _tt, leaf_id, position, label, parent, added_q, dep_q, notes in leaves:
            n_alias = alias_counts.get(leaf_id, 0)
            status = f"deprecated {dep_q}" if dep_q else f"active (added {added_q})"
            lines.append(f"| {position} | `{leaf_id}` | {label} | {n_alias} | {status} |")
        lines.append("")

    path.write_text("\n".join(lines) + "\n")


def write_unresolved_report(con: sqlite3.Connection, bank: str, verify: dict, path: Path) -> None:
    unresolved, ambiguous = verify["unresolved"], verify["ambiguous"]
    lines = [f"# M2 unresolved rows — {bank}\n"]
    if not unresolved and not ambiguous:
        lines.append(f"None. Every `{bank}` `fact_metric` row resolves to exactly one "
                      f"canonical leaf ({len(verify['resolved'])} rows checked).\n")
        path.write_text("\n".join(lines) + "\n")
        return

    lines.append(f"**{len(unresolved)} unresolved**, **{len(ambiguous)} ambiguous** "
                  f"(out of {len(verify['resolved']) + len(unresolved) + len(ambiguous)} "
                  f"`{bank}` `fact_metric` rows checked). Each needs a human decision -- "
                  f"none of these were auto-resolved.\n")

    if unresolved:
        lines.append("## Unresolved (no candidate canonical leaf at all)\n")
        for r in unresolved:
            lines.append(f"- **{r['concept_key']}** @ {r['period']} — "
                         f"`{r['table_type_id']}` / \"{r['source_row_label']}\" "
                         f"(doc={r['source_doc_id']}, table={r['source_table_id']}) — {r['reason']}")
        lines.append("")

    if ambiguous:
        lines.append("## Ambiguous (source_row_label matches >1 address, not all agreeing)\n")
        for r in ambiguous:
            lines.append(f"- **{r['concept_key']}** @ {r['period']} — "
                         f"`{r['table_type_id']}` / \"{r['source_row_label']}\" "
                         f"(doc={r['source_doc_id']}, table={r['source_table_id']})")
            for c in r["candidates"]:
                lines.append(f"    - parent=`{c['parent_label_norm']}` leaf=`{c['row_label_norm']}` "
                             f"-> {c['status']}" + (f" ({c.get('canonical_leaf_id')})" if c["status"] == "resolved" else f" -- {c.get('reason','')}"))
        lines.append("")

    path.write_text("\n".join(lines) + "\n")


def write_m3_report(con: sqlite3.Connection, bank: str, check: dict, path: Path) -> None:
    lines = [f"# M3 concept-binding check — {bank}\n",
             f"For every `concept_key` with a `{bank}` binding in `bank_line_map` "
             f"(map_status != 'deprecated'), confirms the bound (table_type_id, "
             f"row_label_norm, parent_label_norm) resolves through the M2 gate.\n",
             f"**{check['n_resolved']} resolved**, **{check['n_unresolved']} unresolved** "
             f"out of {check['n_resolved'] + check['n_unresolved']} bindings.\n"]

    lines.append("## The 4 concepts shifted by the OCBC dedup fix — summary\n")
    lines.append("| concept_key | bindings | at least 1 resolves? |")
    lines.append("|---|---|---|")
    for ck in SHIFTED_CONCEPTS:
        bindings = check["shifted"].get(ck, [])
        any_ok = any(b["status"] == "resolved" for b in bindings)
        verdict = ("✅ yes" if any_ok else "⚠️ **NO — every binding for this concept is unresolved**")
        lines.append(f"| `{ck}` | {len(bindings)} | {verdict} |")
    lines.append("")

    lines.append("## The 4 concepts shifted by the OCBC dedup fix — detail\n")
    for ck in SHIFTED_CONCEPTS:
        bindings = check["shifted"].get(ck, [])
        if not bindings:
            lines.append(f"### `{ck}` — NO bank_line_map binding found for {bank}\n")
            continue
        lines.append(f"### `{ck}` — {len(bindings)} binding(s)\n")
        lines.append("| table_type_id | parent | leaf | map_status | resolution |")
        lines.append("|---|---|---|---|---|")
        for b in bindings:
            res = (f"resolved via {b['via']} -> `{b['canonical_leaf_id']}`" if b["status"] == "resolved"
                   else f"UNRESOLVED — {b['reason']}")
            lines.append(f"| `{b['table_type_id']}` | {b['parent_label_norm'] or '(none)'} "
                         f"| {b['row_label_norm']} | {b['map_status']} | {res} |")
        lines.append("")

    unresolved_bindings = [r for r in check["all"] if r["status"] == "unresolved"]
    if unresolved_bindings:
        lines.append("## All unresolved bindings (not just the 4 shifted concepts)\n")
        for r in unresolved_bindings:
            lines.append(f"- **{r['concept_key']}** — `{r['table_type_id']}` / "
                         f"parent={r['parent_label_norm'] or '(none)'} leaf={r['row_label_norm']} "
                         f"({r['map_status']}) — {r['reason']}")
        lines.append("")
    else:
        lines.append("## All non-shifted bindings\n")
        lines.append("All resolved. No further action needed.\n")

    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--bank", default="OCBC")
    ap.add_argument("--out-dir", default=str(_REPO / "findociq" / "docs"))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    ensure_schema(con)
    leaf_stats = populate_canonical_leaves(con, args.bank)
    alias_stats = populate_aliases(con, args.bank)
    fm_verify = verify_fact_metric(con, args.bank)
    m3_check = verify_concept_bindings(con, args.bank)

    out_dir = Path(args.out_dir)
    write_canonical_report(con, args.bank, out_dir / f"m2-{args.bank.lower()}-canonical-report.md")
    write_unresolved_report(con, args.bank, fm_verify, out_dir / f"m2-{args.bank.lower()}-unresolved-rows.md")
    write_m3_report(con, args.bank, m3_check, out_dir / f"m3-{args.bank.lower()}-concept-binding-check.md")

    print(f"canonical leaves: {leaf_stats}")
    print(f"aliases: {alias_stats}")
    print(f"fact_metric verify: resolved={len(fm_verify['resolved'])} "
          f"unresolved={len(fm_verify['unresolved'])} ambiguous={len(fm_verify['ambiguous'])}")
    print(f"M3 bindings: resolved={m3_check['n_resolved']} unresolved={m3_check['n_unresolved']}")
    con.close()


if __name__ == "__main__":
    main()
