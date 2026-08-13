"""Tests for m2_canonical_leaf.py -- the M2 gate (per-bank identity
persistence).

Pure/schema tests build their own tiny sqlite DB (no dependency on the real
corpus). The integration check at the bottom runs the real build+verify
pipeline against a scratch COPY of compiled_fs.db (never the real file --
same pattern as test_migrate_serving_views.py) to confirm it runs clean
end-to-end against real data, without needing to replicate the full corpus
schema for a unit test.

    python3 findociq/pipeline/mapping/test_m2_canonical_leaf.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapping.m2_canonical_leaf import (  # noqa: E402
    canonical_leaf_id_of, ensure_schema, ordered_addresses,
    populate_aliases, populate_canonical_leaves, resolve_address,
    verify_concept_bindings, verify_fact_metric,
)

_REPO = Path(__file__).resolve().parents[3]
_SRC_DB = _REPO / "findociq" / "db" / "compiled_fs.db"

_fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _fail
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        _fail += 1


def _mk_schema_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    ensure_schema(con)
    return con


def _insert_leaf(con, bank, ttid, leaf_id, rln, pln, position, label="x"):
    con.execute("""
        INSERT INTO canonical_leaf
            (bank, table_type_id, canonical_leaf_id, row_label_norm,
             parent_label_norm, label_current, position, added_quarter)
        VALUES (?,?,?,?,?,?,?,'4Q25')
    """, (bank, ttid, leaf_id, rln, pln, label, position))


# --------------------------------------------------------- ordered_addresses
def test_ordered_addresses():
    # NOTE: title-like-parent collapse needs >=2 DISTINCT depth-1 groups to
    # avoid collapsing -- a single depth-2 row under one parent looks
    # trivially "every row at this depth shares one parent", which is
    # exactly the table-title-constant case the rule is designed to strip.
    # Two groups (Earnings / Reported earnings) below avoids that.
    rows = [
        {"doc_id": "d", "table_id": "t", "row_id": 1, "row_leaf_label": "Earnings",
         "lvl1": "Earnings", "lvl2": None, "lvl3": None, "lvl4": None, "lvl5": None, "depth": 1},
        {"doc_id": "d", "table_id": "t", "row_id": 2, "row_leaf_label": "Basic",
         "lvl1": "Earnings", "lvl2": "Basic", "lvl3": None, "lvl4": None, "lvl5": None, "depth": 2},
        {"doc_id": "d", "table_id": "t", "row_id": 3, "row_leaf_label": "Reported earnings",
         "lvl1": "Reported earnings", "lvl2": None, "lvl3": None, "lvl4": None, "lvl5": None, "depth": 1},
        {"doc_id": "d", "table_id": "t", "row_id": 4, "row_leaf_label": "Basic",
         "lvl1": "Reported earnings", "lvl2": "Basic", "lvl3": None, "lvl4": None, "lvl5": None, "depth": 2},
    ]
    out = ordered_addresses(rows)
    check("ordered_addresses: 4 rows in", len(out) == 4)
    check("ordered_addresses: depth-1 has no parent", out[0][0] == ("earnings", ""))
    check("ordered_addresses: depth-2 keeps real parent", out[1][0] == ("basic", "earnings"))
    check("ordered_addresses: same leaf, different parent -> distinct addresses",
          out[3][0] == ("basic", "reported_earnings") and out[1][0] != out[3][0])


def test_ordered_addresses_groups_by_table_not_row_id_alone():
    # row_id restarts per table_id -- must not interleave two tables.
    # NOTE: normalize_row_label strips a trailing digit after a lowercase
    # letter as a footnote marker (confirmed: 'A1'/'A2' both normalize to
    # 'a') -- fixture labels below are chosen to NOT collide under that rule.
    rows = [
        {"doc_id": "d", "table_id": "tb", "row_id": 1, "row_leaf_label": "Beta",
         "lvl1": "Beta", "lvl2": None, "lvl3": None, "lvl4": None, "lvl5": None, "depth": 1},
        {"doc_id": "d", "table_id": "ta", "row_id": 1, "row_leaf_label": "Alpha",
         "lvl1": "Alpha", "lvl2": None, "lvl3": None, "lvl4": None, "lvl5": None, "depth": 1},
        {"doc_id": "d", "table_id": "ta", "row_id": 2, "row_leaf_label": "Gamma",
         "lvl1": "Gamma", "lvl2": None, "lvl3": None, "lvl4": None, "lvl5": None, "depth": 1},
    ]
    out = [addr[0] for addr, _r in ordered_addresses(rows)]
    check("groups by (table_id, row_id): ta's rows stay contiguous",
          out == ["alpha", "gamma", "beta"], detail=str(out))


def test_canonical_leaf_id_of():
    check("no parent -> bare row label", canonical_leaf_id_of("total_income", "") == "total_income")
    check("with parent -> parent::leaf",
          canonical_leaf_id_of("basic", "earnings") == "earnings::basic")


# --------------------------------------------------- schema uniqueness (req'd)
def test_canonical_leaf_position_unique_within_scope():
    con = _mk_schema_db()
    _insert_leaf(con, "OCBC", "FS_X", "a", "a", "", 0)
    threw = False
    try:
        _insert_leaf(con, "OCBC", "FS_X", "b", "b", "", 0)  # same position, different leaf_id
    except sqlite3.IntegrityError:
        threw = True
    check("canonical_leaf: duplicate (bank, table_type, position) rejected", threw)

    # different table_type_id may reuse position 0 -- scope is per table_type
    threw2 = False
    try:
        _insert_leaf(con, "OCBC", "FS_Y", "c", "c", "", 0)
    except sqlite3.IntegrityError:
        threw2 = True
    check("canonical_leaf: same position OK across different table_type_id", not threw2)


def test_canonical_leaf_id_unique_within_scope():
    con = _mk_schema_db()
    _insert_leaf(con, "OCBC", "FS_X", "dup", "dup", "", 0)
    threw = False
    try:
        _insert_leaf(con, "OCBC", "FS_X", "dup", "dup", "", 1)  # same leaf_id, different position
    except sqlite3.IntegrityError:
        threw = True
    check("canonical_leaf: duplicate (bank, table_type, canonical_leaf_id) rejected", threw)

    # same leaf_id string under a DIFFERENT bank is fine -- M2 is bank-scoped by design
    threw2 = False
    try:
        _insert_leaf(con, "UOB", "FS_X", "dup", "dup", "", 0)
    except sqlite3.IntegrityError:
        threw2 = True
    check("canonical_leaf: same canonical_leaf_id OK across different bank (bank-scoped by design)",
          not threw2)


def test_alias_points_at_existing_canonical_leaf():
    con = _mk_schema_db()
    _insert_leaf(con, "OCBC", "FS_X", "real_leaf", "real_leaf", "", 0)
    con.execute("""
        INSERT INTO canonical_leaf_alias
            (bank, table_type_id, alias_row_label_norm, alias_parent_label_norm,
             canonical_leaf_id, source, added_at)
        VALUES ('OCBC','FS_X','real_leaf_2','','real_leaf','test','now')
    """)
    # validate every alias.canonical_leaf_id actually names a row in canonical_leaf
    aliases = con.execute(
        "SELECT bank, table_type_id, canonical_leaf_id FROM canonical_leaf_alias").fetchall()
    all_valid = True
    for bank, ttid, leaf_id in aliases:
        hit = con.execute(
            "SELECT 1 FROM canonical_leaf WHERE bank=? AND table_type_id=? AND canonical_leaf_id=?",
            (bank, ttid, leaf_id)).fetchone()
        all_valid = all_valid and bool(hit)
    check("every canonical_leaf_alias.canonical_leaf_id names an existing canonical_leaf row",
          all_valid)


def test_populate_aliases_never_writes_a_dangling_alias():
    # end-to-end through the real populate_aliases path, not just a hand-built row
    con = _mk_schema_db()
    con.execute("""CREATE TABLE bank_line_map (
        bank TEXT, table_type_id TEXT, row_label_norm TEXT,
        parent_label_norm TEXT, concept_key TEXT, map_status TEXT)""")
    con.execute("""CREATE TABLE fact_metric (
        institution TEXT, source_doc_id TEXT, source_table_id TEXT)""")
    con.execute("""CREATE TABLE table_t (doc_id TEXT, table_id TEXT, table_type_id TEXT)""")
    _insert_leaf(con, "OCBC", "FS_X", "diluted", "diluted", "", 0)
    con.execute("INSERT INTO bank_line_map VALUES ('OCBC','FS_X','diluted_9','','pnl.eps.diluted','ai_proposed')")
    con.execute("INSERT INTO table_t VALUES ('d','t','FS_X')")
    con.execute("INSERT INTO fact_metric VALUES ('Oversea-Chinese Banking Corporation Ltd','d','t')")
    populate_aliases(con, "OCBC")
    aliases = con.execute("SELECT canonical_leaf_id FROM canonical_leaf_alias").fetchall()
    check("footnote-suffix alias created", len(aliases) == 1, detail=str(aliases))
    ok = all(con.execute(
        "SELECT 1 FROM canonical_leaf WHERE bank='OCBC' AND table_type_id='FS_X' AND canonical_leaf_id=?",
        (lid,)).fetchone() for (lid,) in aliases)
    check("populate_aliases never writes a dangling canonical_leaf_id", ok)


# ------------------------------------------------------ resolve_address (req'd)
def test_resolve_address_exactly_one_outcome():
    con = _mk_schema_db()
    _insert_leaf(con, "OCBC", "FS_X", "known", "known", "", 0)

    r1 = resolve_address(con, "OCBC", "FS_X", "known", "")
    check("resolve_address: direct match -> resolved", r1["status"] == "resolved" and r1["via"] == "direct")
    check("resolve_address: resolved has exactly canonical_leaf_id + via + status keys",
          set(r1.keys()) == {"status", "canonical_leaf_id", "via"})

    con.execute("""
        INSERT INTO canonical_leaf_alias VALUES
            ('OCBC','FS_X','alias_label','','known','test','now')
    """)
    r2 = resolve_address(con, "OCBC", "FS_X", "alias_label", "")
    check("resolve_address: alias match -> resolved via alias",
          r2["status"] == "resolved" and r2["via"] == "alias" and r2["canonical_leaf_id"] == "known")

    r3 = resolve_address(con, "OCBC", "FS_X", "nothing_like_this", "")
    check("resolve_address: no match -> unresolved with a reason",
          r3["status"] == "unresolved" and "reason" in r3)
    check("resolve_address: unresolved has exactly status + reason keys",
          set(r3.keys()) == {"status", "reason"})

    r4 = resolve_address(con, "OCBC", "FS_NEVER_BUILT", "anything", "")
    check("resolve_address: no canonical set at all -> unresolved, not an exception",
          r4["status"] == "unresolved")


def test_resolve_address_deprecated_leaf_lower_priority_than_alias():
    con = _mk_schema_db()
    con.execute("""
        INSERT INTO canonical_leaf VALUES
            ('OCBC','FS_X','old','old_label','','Old', 0, '1Q25', '2Q25', NULL)
    """)
    con.execute("""
        INSERT INTO canonical_leaf VALUES
            ('OCBC','FS_X','new','new_label','','New', 1, '2Q25', NULL, NULL)
    """)
    con.execute("""
        INSERT INTO canonical_leaf_alias VALUES
            ('OCBC','FS_X','old_label','','new','migrated','now')
    """)
    # 'old_label' is BOTH a deprecated leaf's own address AND an alias to 'new'
    # -- alias must win per Decision 2's stated priority order
    r = resolve_address(con, "OCBC", "FS_X", "old_label", "")
    check("resolve_address: alias beats deprecated-leaf direct match",
          r["status"] == "resolved" and r["via"] == "alias" and r["canonical_leaf_id"] == "new")


# ------------------------------------------------------------- integration
def test_integration_against_live_db_scratch_copy():
    if not _SRC_DB.exists():
        print("  SKIP  integration: no compiled_fs.db found")
        return
    tmp = Path(tempfile.mkdtemp()) / "scratch.db"
    shutil.copy(_SRC_DB, tmp)
    con = sqlite3.connect(str(tmp))
    try:
        leaf_stats = populate_canonical_leaves(con, "OCBC")
        check("integration: at least one table_type got a canonical set",
              leaf_stats["table_types"] > 0)
        populate_aliases(con, "OCBC")

        fm = verify_fact_metric(con, "OCBC")
        total = len(fm["resolved"]) + len(fm["unresolved"]) + len(fm["ambiguous"])
        check("integration: verify_fact_metric partitions every row into exactly one bucket",
              total > 0, detail=f"resolved={len(fm['resolved'])} unresolved={len(fm['unresolved'])} "
                                 f"ambiguous={len(fm['ambiguous'])}")

        m3 = verify_concept_bindings(con, "OCBC")
        check("integration: verify_concept_bindings covers the 4 shifted concepts",
              set(m3["shifted"].keys()) == {
                  "bs.nav_per_share", "pnl.eps.basic", "pnl.eps.diluted", "reg.capital.cet1_ratio"})

        # idempotency: re-running populate must not change the leaf count
        leaf_stats2 = populate_canonical_leaves(con, "OCBC")
        n1 = con.execute("SELECT COUNT(*) FROM canonical_leaf WHERE bank='OCBC'").fetchone()[0]
        populate_canonical_leaves(con, "OCBC")
        n2 = con.execute("SELECT COUNT(*) FROM canonical_leaf WHERE bank='OCBC'").fetchone()[0]
        check("integration: populate_canonical_leaves is idempotent (row count stable)", n1 == n2,
              detail=f"{n1} vs {n2}")
    finally:
        con.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)


def main() -> None:
    test_ordered_addresses()
    test_ordered_addresses_groups_by_table_not_row_id_alone()
    test_canonical_leaf_id_of()
    test_canonical_leaf_position_unique_within_scope()
    test_canonical_leaf_id_unique_within_scope()
    test_alias_points_at_existing_canonical_leaf()
    test_populate_aliases_never_writes_a_dangling_alias()
    test_resolve_address_exactly_one_outcome()
    test_resolve_address_deprecated_leaf_lower_priority_than_alias()
    test_integration_against_live_db_scratch_copy()
    print(f"\n{'ALL PASS' if _fail == 0 else f'{_fail} FAILED'}")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
