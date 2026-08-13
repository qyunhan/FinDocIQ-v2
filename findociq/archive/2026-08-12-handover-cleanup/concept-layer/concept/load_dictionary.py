"""concept.load_dictionary — parse concept_dictionary.yaml and expand it into
concept_map rows; plus the additive schema migration the layer needs.

Every concept (line_item AND derived — banks print ratio rows like CIR / NIM as
line items, so a derived concept's aliases are stampable too) contributes, for
each alias AND for its canonical name, one WILDCARD concept_map row:
    (table_type='*', table_type_norm='*', label_norm=norm(alias), concept_key).

table_type_norm mapping (map_table_type_norm) canonicalises a raw table_type
slug; today's dictionary rows are all wildcards, but the column is populated so
future TYPE-SCOPED aliases (e.g. the same word meaning different things in an
income statement vs a balance sheet) are a one-row addition, not a schema change.

The 19 existing NSFR concept_map rows are LEFT UNTOUCHED (table_type='nsfr',
table_type_norm='nsfr'); they are type-scoped and win over wildcards for an NSFR
table.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.normalize import norm  # noqa: E402
from mapping.migrate_serving_views import migrate as rebuild_serving_views  # noqa: E402

_DICT_PATH = Path(__file__).resolve().parent / "concept_dictionary.yaml"

# Raw table_type slug (substring) -> canonical table_type_norm. Ordered; first
# containing match wins (prompt spec). Anything unmatched -> '*' (wildcard scope),
# so its rows only match wildcard aliases.
_TYPE_NORM_RULES: list[tuple[str, str]] = [
    ("income_statement", "income_statement"),
    ("comprehensive_income", "income_statement"),
    ("balance_sheet", "balance_sheet"),
    ("balance_sheets", "balance_sheet"),
    ("customer_loans", "customer_loans"),
    ("customer_deposits", "customer_deposits"),
    ("nsfr", "nsfr"),
    ("lcr", "lcr"),
    # Credit-risk STOCK disclosures (ECL/allowance roll-forwards, NPA/NPL
    # breakdowns) -- an "as at" balance, not a P&L charge, even though the row
    # labels overlap with pnl.provisions.* (e.g. "Total allowances", "ECL Stage
    # 3 (SP)" appears both as the period charge in an income statement AND as
    # the closing balance in one of these tables). Without this bucket they'd
    # fall through to '*' and the flow concept's wildcard alias would silently
    # claim the balance row too -- see concept_dictionary.yaml's `nature` note.
    ("allowance", "credit_quality"),
    ("non_performing_assets", "credit_quality"),
    ("loans_to_customers", "credit_quality"),
]


def map_table_type_norm(table_type: str | None) -> str:
    """Canonical table_type_norm for a raw table_type slug; '*' when nothing
    matches (the row then resolves against wildcard aliases only)."""
    s = (table_type or "").lower()
    for needle, canon in _TYPE_NORM_RULES:
        if needle in s:
            return canon
    return "*"


# ---------------------------------------------------------------------------
# DIMENSIONAL-BREAKDOWN SCOPES (no-wildcard buckets)
#
# A dimensional-breakdown exhibit decomposes the SAME line items the primary
# statements report along a NON-ENTITY axis: geography, business segment,
# industry. Its ROW labels are therefore character-for-character the spine's
# ("Total assets", "Net interest income", "Profit before tax") while its cells
# mean something categorically different -- a slice of the entity, not the
# entity. A WILDCARD alias cannot tell the two apart: by construction it knows
# only the label text, never which exhibit the row landed in, so it claims the
# breakdown's rows exactly as eagerly as the income statement's.
#
# _TYPE_NORM_RULES cannot express this. It is a POSITIVE, substring-of-the-raw-
# title rule: it can say "this bucket has its own meaning for this label", and
# `scoped_aliases` then supplies that meaning -- but a bucket with no scoped
# alias for a label still falls through to the wildcard. What a breakdown
# exhibit needs is the opposite polarity: a scope in which the wildcard is
# NEVER consulted, so a concept lands on a breakdown row only when a human
# DECLARED it for that axis. Dimensional facts become opt-in-by-declaration
# rather than a side effect of a generic alias.
#
# The scope is detected from TWO INDEPENDENT SIGNALS, unioned, neither of them
# per-bank or per-document (see the 2026-08-04 decision-tree-pivot section of
# docs/specs/2026-07-14-concept-resolution.md):
#
#   1. STRUCTURAL (works for an exhibit and a bank the registry has never
#      seen): the table's own COLUMNS resolve to >= 2 distinct non-total keys
#      on one axis -- >=2 geo_key other than GLOBAL, >=2 segment_key other than
#      SEG_TOTAL, >=2 industry_key other than IND_TOTAL. Columns, not rows,
#      deliberately: the label collision this guards against exists precisely
#      when the ROWS are line items and the COLUMNS are the dimension. An
#      exhibit that puts regions in the ROWS has region names for row labels,
#      which match no concept alias and need no suppression.
#      The >=2 threshold excludes a single-geography statement (a subsidiary's
#      own accounts), where the rows DO legitimately mean the spine concept for
#      that one entity, from a breakdown that repeats them per slice.
#
#   2. DECLARED (works when the column headers are region/segment names we do
#      not yet map, so signal 1 is blind): `table_registry.dim_hint` for the
#      table's registry-assigned table_type_id -- the axis the registry already
#      records for "dimensional decompositions" (table_registry.yaml).
#
# Either signal alone is sufficient; both are corroborating, and each covers
# the other's blind spot. Measured on the corpus: signal 1 flags 45 tables and
# signal 2 flags 35, and every table either flags is a genuine geography or
# business-segment breakdown -- including 6 OCBC "Business segments" tables the
# registry leaves UNCLASSIFIED and 4 DBS breakdowns misfiled as
# FS_INCOME_SELECTED, both of which signal 2 alone would have missed.
# ---------------------------------------------------------------------------

# axis -> (col_dim column, the key meaning "not a slice, the whole entity")
_DIM_AXES: list[tuple[str, str, str]] = [
    ("geo", "geo_key", "GLOBAL"),
    ("segment", "segment_key", "SEG_TOTAL"),
    ("industry", "industry_key", "IND_TOTAL"),
]

#: minimum distinct non-total keys on ONE axis for the structural signal to fire
_DIM_MIN_KEYS = 2

#: table_type_norm buckets in which a wildcard alias is NEVER consulted. These
#: are legal `scoped_aliases` bucket names in concept_dictionary.yaml, so
#: deliberately mapping a dimensional line item stays a one-row addition.
NO_WILDCARD_SCOPES = frozenset(f"dim_{axis}" for axis, _, _ in _DIM_AXES)


def _table_columns(con, table: str) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def dimensional_scopes(con) -> dict[tuple[str, str], str]:
    """(doc_id, table_id) -> 'dim_geo' | 'dim_segment' | 'dim_industry' for every
    table that is a dimensional breakdown (see the block comment above).

    Absent tables/columns degrade to "no signal" rather than raising, so this is
    safe on a fresh schema or a DB predating table_registry.
    """
    col_cols = _table_columns(con, "col_dim")
    if not col_cols:
        return {}
    axes = [(a, c, total) for a, c, total in _DIM_AXES if c in col_cols]
    have_registry = bool(_table_columns(con, "table_registry") & {"dim_hint"})

    counts = ", ".join(
        f"COUNT(DISTINCT CASE WHEN c.{col} IS NOT NULL AND c.{col} <> '{total}' "
        f"THEN c.{col} END)" for _, col, total in axes)
    reg_sel = "MAX(g.dim_hint)" if have_registry else "NULL"
    reg_join = ("LEFT JOIN table_registry g ON g.table_type_id = t.table_type_id"
                if have_registry else "")
    try:
        rows = con.execute(
            f"SELECT t.doc_id, t.table_id, {counts}, {reg_sel} "
            f"FROM table_t t "
            f"LEFT JOIN col_dim c ON c.doc_id=t.doc_id AND c.table_id=t.table_id "
            f"{reg_join} "
            f"GROUP BY t.doc_id, t.table_id").fetchall()
    except sqlite3.OperationalError:
        return {}

    out: dict[tuple[str, str], str] = {}
    for row in rows:
        doc_id, table_id = row[0], row[1]
        per_axis = dict(zip((a for a, _, _ in axes), row[2:2 + len(axes)]))
        dim_hint = row[-1]
        # signal 1: widest axis that clears the threshold (a table split on two
        # axes at once is scoped to the one it decomposes most finely; the
        # suppression is identical either way, the bucket name only decides
        # which scoped_aliases could ever opt back in).
        best = max(((n or 0, -i, a) for i, (a, _, _) in enumerate(axes)
                    for n in [per_axis.get(a)]), default=(0, 0, ""))
        if best[0] >= _DIM_MIN_KEYS:
            out[(doc_id, table_id)] = f"dim_{best[2]}"
            continue
        # signal 2: the registry declares this exhibit type as a decomposition
        if dim_hint and f"dim_{dim_hint}" in NO_WILDCARD_SCOPES:
            out[(doc_id, table_id)] = f"dim_{dim_hint}"
    return out


# ---------------------------------------------------------------------------
# UNIT KIND -> canonical serving unit
#
# `concept_dictionary.yaml`'s `unit:` is a unit KIND ('currency', 'percent',
# 'per_share', 'bps'), not a printed unit. Served rows must carry ONE concrete
# unit STRING per concept, and the two vocabularies were leaking into the same
# `fact_metric.unit` column: extracted cells wrote '%' / 'S$m' while the formula
# engine wrote the raw kind ('percent' / 'currency'), so a single concept served
# both spellings (pre-flight D1).
#
# Two categories, and the distinction is what makes this rule general:
#   * CONCEPT-OWNED kinds (percent, bps, per_share) are a property of the
#     CONCEPT. A cost-income ratio is a percentage in every filing of every
#     bank, so the dictionary is authoritative and a table-default unit
#     inherited from a '($m)' caption is noise.
#   * CURRENCY is a property of the DOCUMENT (S$m here, but a filing in another
#     currency is the same concept). The dictionary cannot name it, so the
#     as-loaded unit wins and the canonical string is None.
#
# SCALE is declared here too, not repeated in each formula. A formula states the
# MATHEMATICAL ratio (opex / income); the declared unit states the PRESENTATION
# scale. Keeping the factor in the formula string is what let
# `ratio.credit_cost_bps` carry '* 10000' while every percent formula silently
# emitted a fraction (0.404) against reported percentage points (40.4) — a 100x
# discrepancy hidden behind a different unit spelling. A new ratio added with
# `unit: percent` is now correctly scaled without the author remembering.
# ---------------------------------------------------------------------------

#: unit kind -> (canonical serving unit string or None = "as-loaded wins", scale)
_UNIT_KINDS: dict[str, tuple[str | None, float]] = {
    "percent": ("%", 100.0),
    "bps": ("bps", 10000.0),
    "per_share": ("per_share", 1.0),
    "currency": (None, 1.0),
}


def canonical_unit(kind: str | None) -> str | None:
    """Concrete serving unit string for a dictionary unit KIND, or None when the
    kind is document-dependent (currency) and the as-loaded unit must win."""
    return _UNIT_KINDS.get(kind or "", (None, 1.0))[0]


def unit_scale(kind: str | None) -> float:
    """Multiplier taking a formula's mathematical result into the declared
    unit's presentation scale (fraction -> percentage points / bps)."""
    return _UNIT_KINDS.get(kind or "", (None, 1.0))[1]


def load_concepts(path: Path | None = None) -> list[dict]:
    """Parse the YAML into a flat list of concept dicts:
    {key, name, kind, nature, unit, aliases:[...], scoped_aliases:{bucket:[...]},
    formula:str|None}.
    `nature` is required on every concept (flow/stock/ratio_flow/ratio_point) --
    a KeyError here means a new concept was added without classifying it.
    `scoped_aliases` is optional: {table_type_norm: [alias,...]} for a label that
    means something DIFFERENT depending on which table it's in (e.g. "ECL Stage
    3 (SP)" is the P&L charge in an income_statement table but a balance in a
    credit_quality/customer_loans one) -- these seed a TYPE-SCOPED concept_map
    row instead of a wildcard, so the two meanings don't collide (see
    resolve_deterministic.build_lookup: a scoped row always beats a wildcard)."""
    doc = yaml.safe_load((path or _DICT_PATH).read_text())
    out: list[dict] = []
    for c in doc.get("concepts", []):
        out.append(dict(
            key=c["key"], name=c.get("name", ""), kind=c.get("kind", "line_item"),
            nature=c["nature"], unit=c.get("unit"),
            aliases=list(c.get("aliases", []) or []),
            scoped_aliases={b: list(a or []) for b, a in
                           (c.get("scoped_aliases") or {}).items()},
            formula=c.get("formula")))
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Additive schema migration (idempotent, portable). For an already-built DB the
# schema_v7.sql edits are not retro-applied, so apply them here:
#   * concept_map.table_type_norm column (+ backfill NSFR rows to 'nsfr')
#   * concept_resolution_log table
#   * cell_fact.segment_key / cell_fact.industry_key materialisation columns
#   * v_cell / v_cell_leaf / v_cell_sumsafe / v_cell_flat
# The four views are NOT defined here. There is exactly one definition of
# them, in mapping.migrate_serving_views (row_dim columns + human-anchor
# projection + concept_period_kind + the views themselves, in that order) --
# this used to be a second, independent copy of the view DDL (pre-human-
# anchor, pre-period-label), and because this function runs unconditionally
# as the first thing every concept/run.py pass does (STEP 4a of run_doc.py,
# the STANDARD PRODUCTION INGEST PATH), it silently clobbered the merged
# views on every real document ingest -- confirmed live during the pre-
# flight pass, 2026-08-03 (see docs/DECISIONS.md). Calling the shared
# migration here instead means STEP 4a REBUILDS the full merged views
# (including a fresh human-anchor stamp over any newly-loaded row_dim rows)
# rather than reverting them -- nothing downstream has to "restore" what
# this step tore down.
# A freshly-built DB already has all of this; every step is guarded/idempotent.
# ---------------------------------------------------------------------------


def ensure_schema(con) -> list[str]:
    """Apply the additive migration on `con` (a sqlite3 connection). Idempotent.
    Returns a list of human-readable actions taken."""
    actions: list[str] = []
    cur = con.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(concept_map)").fetchall()}
    if "table_type_norm" not in cols:
        cur.execute("ALTER TABLE concept_map ADD COLUMN table_type_norm TEXT")
        cur.execute("UPDATE concept_map SET table_type_norm = 'nsfr' "
                    "WHERE table_type = 'nsfr' AND table_type_norm IS NULL")
        actions.append("added concept_map.table_type_norm (+ backfilled NSFR rows)")
    have = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "concept_resolution_log" not in have:
        cur.execute(
            "CREATE TABLE concept_resolution_log ("
            "doc_id TEXT, table_id TEXT, row_id INTEGER, label TEXT, "
            "norm_label TEXT, concept_key TEXT, method TEXT, confidence REAL, ts TEXT)")
        actions.append("created concept_resolution_log")
    # cell_fact.segment_key materialisation column (geo_key predates this). A
    # freshly-built DB from schema_v7.sql already has it; an older DB gets it here
    # so the recreated views below can read f.segment_key. Idempotent.
    cf_cols = {r[1] for r in cur.execute("PRAGMA table_info(cell_fact)").fetchall()}
    if "segment_key" not in cf_cols:
        cur.execute("ALTER TABLE cell_fact ADD COLUMN segment_key TEXT "
                    "REFERENCES segment_dim(segment_key)")
        actions.append("added cell_fact.segment_key")
    # cell_fact.industry_key materialisation column (mirror of segment_key above).
    # A freshly-migrated DB (migrate_add_industry_dim.py) already has it; guarded
    # here too so ensure_schema() alone is sufficient on an unmigrated DB.
    if "industry_key" not in cf_cols:
        have_dim = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "industry_dim" not in have_dim:
            cur.execute(
                "CREATE TABLE industry_dim (industry_key TEXT PRIMARY KEY, "
                "industry_name TEXT NOT NULL, level TEXT NOT NULL "
                "CHECK (level IN ('sector','total')), "
                "parent TEXT REFERENCES industry_dim(industry_key))")
            actions.append("created industry_dim (empty — run "
                           "migrate_add_industry_dim.py to seed it)")
        cur.execute("ALTER TABLE cell_fact ADD COLUMN industry_key TEXT "
                    "REFERENCES industry_dim(industry_key)")
        cur.execute("UPDATE cell_fact SET industry_key = 'IND_TOTAL' "
                    "WHERE industry_key IS NULL")
        actions.append("added cell_fact.industry_key (backfilled IND_TOTAL)")
    con.commit()
    # Single source of truth for v_cell/v_cell_leaf/v_cell_sumsafe/v_cell_flat --
    # see the module-docstring note above. Rebuilds row_dim's human-anchor
    # columns (idempotent, additive), re-stamps against bank_line_map (picks
    # up any row_dim rows loaded since the last stamp), and (re)builds the
    # full merged views -- so this step never has to be followed by a manual
    # migration re-run to restore what it tore down.
    rsv = rebuild_serving_views(con)
    actions.append(
        f"rebuilt v_cell/v_cell_leaf/v_cell_sumsafe/v_cell_flat via "
        f"mapping.migrate_serving_views (row_dim columns added={rsv['columns_added'] or 'none'}, "
        f"row_dim rows stamped={rsv['row_dim_stamped']}, "
        f"concept_period_kind rows={rsv['concept_period_kind_rows']})")
    return actions


def _corpus_label_buckets(con) -> dict[str, set[str]]:
    """norm(row_leaf_label) -> {table_type_norm,...} actually observed in this
    DB's ingested documents. Powers the ambiguity gate below -- the same
    "a label seen under >1 table type is context-dependent" instinct
    resolve_llm._dedupe_residue already applies before promoting an LLM answer
    to a wildcard alias, just computed from the corpus instead of one run's
    residue, and applied to the dictionary-seeding path (which had no
    equivalent guard: every curated alias became a wildcard unconditionally)."""
    out: dict[str, set[str]] = defaultdict(set)
    try:
        rows = con.execute(
            "SELECT r.doc_id, r.table_id, r.row_leaf_label, t.table_type FROM row_dim r "
            "JOIN table_t t ON t.doc_id=r.doc_id AND t.table_id=r.table_id "
            "WHERE r.row_leaf_label IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return out          # row_dim/table_t not present yet (fresh schema) -> no signal
    # Dimensional-breakdown tables are EXCLUDED from the ambiguity signal. Their
    # rows re-print spine labels by definition, so counting them as a second
    # bucket would disqualify the wildcard alias of every concept a breakdown
    # happens to repeat ("Total assets", "Net interest income") -- unstamping
    # the genuine income-statement/balance-sheet rows to guard against a
    # collision that no longer exists, since a wildcard can no longer reach a
    # breakdown row at all.
    dim = dimensional_scopes(con)
    for doc_id, table_id, label, table_type in rows:
        if (doc_id, table_id) in dim:
            continue
        out[norm(label)].add(map_table_type_norm(table_type))
    return out


def load_into_concept_map(con, concepts: list[dict] | None = None,
                          *, dry_run: bool = False) -> dict:
    """Expand the dictionary into concept_map rows on `con`. Idempotent (INSERT
    OR IGNORE on PK (table_type,label_norm)). Two families of alias:
      * `aliases` (+ `name`) -> a WILDCARD row (table_type='*'), UNLESS the
        label is AMBIGUOUS in the observed corpus (seen under >=2 distinct real
        table_type_norm buckets) -- an ambiguous wildcard would silently let
        this concept claim a row that means something else in another table
        (see concept_dictionary.yaml's `nature` note); it's skipped and
        reported in `ambiguous_skipped` instead of inserted, same as
        resolve_llm does for a same-shaped ambiguity in the LLM residue path.
      * `scoped_aliases` -> a TYPE-SCOPED row per declared bucket; a scoped row
        always wins over a wildcard for a row of that table type
        (resolve_deterministic.build_lookup), so this is how two concepts
        legitimately share one label text (e.g. "ECL Stage 3 (SP)" is the P&L
        charge in an income_statement table, a balance in a credit_quality one).
    Returns a summary dict with counts, alias collisions (two concepts writing
    the SAME (bucket,label) slot), and skipped-ambiguous aliases."""
    concepts = concepts if concepts is not None else load_concepts()
    cur = con.cursor()
    existing = {(r[0], r[1]): r[2] for r in cur.execute(
        "SELECT table_type, label_norm, concept_key FROM concept_map").fetchall()}
    corpus_buckets = _corpus_label_buckets(con)

    seen: dict[tuple[str, str], str] = {}   # (bucket,label_norm) -> concept_key
    collisions: list[tuple[str, str, str, str]] = []
    ambiguous_skipped: list[dict] = []
    to_insert: list[tuple[str, str, str, str]] = []
    n_alias = 0

    def _add(bucket: str, ln: str, key: str) -> None:
        slot = (bucket, ln)
        if slot in seen and seen[slot] != key:
            collisions.append((bucket, ln, seen[slot], key))
            return
        seen.setdefault(slot, key)
        pk = (bucket, ln)
        if pk in existing:
            return
        to_insert.append((bucket, ln, key, bucket))

    for c in concepts:
        for alias in [c["name"], *c["aliases"]]:
            ln = norm(alias)
            if not ln:
                continue
            n_alias += 1
            real_buckets = corpus_buckets.get(ln, set()) - {"*"}
            if len(real_buckets) > 1:
                ambiguous_skipped.append(dict(label_norm=ln, concept_key=c["key"],
                                              buckets=sorted(real_buckets)))
                continue
            _add("*", ln, c["key"])
        for bucket, aliases in c["scoped_aliases"].items():
            for alias in aliases:
                ln = norm(alias)
                if not ln:
                    continue
                n_alias += 1
                _add(bucket, ln, c["key"])

    if not dry_run and to_insert:
        cur.executemany(
            "INSERT OR IGNORE INTO concept_map"
            "(table_type,label_norm,concept_key,table_type_norm) VALUES (?,?,?,?)",
            to_insert)
        con.commit()
    return dict(concepts=len(concepts), aliases_seen=n_alias,
                wildcard_rows_inserted=len(to_insert),
                wildcard_rows_total=len(seen), collisions=collisions,
                ambiguous_skipped=ambiguous_skipped)
