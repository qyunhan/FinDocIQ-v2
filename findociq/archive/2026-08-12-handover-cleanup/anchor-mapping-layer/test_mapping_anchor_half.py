"""The anchor / bank_line_map half of the old pipeline/mapping/test_mapping.py.
Split out 2026-08-12 with the rest of that layer; the surviving half (normalize +
registry, both LIVE) stays at pipeline/mapping/test_mapping.py. Kept verbatim for
the record — it imports apply_dashboard_rows, archived alongside it.
"""
print("\nthe three-NII anchors are DISTINCT (the case this layer exists for)")
anchors = {
    (R("Net interest income"), R("Commercial book total income")),
    (R("Net interest income"), R("Markets trading income")),
    (R("Of which: Net interest income"), R("Total income")),
}
check("3 rows sharing a concept produce 3 distinct anchors", len(anchors) == 3, str(anchors))

print("\napply_dashboard_rows.retire_orphans — a dropped anchor is DEPRECATED, never deleted")
from apply_dashboard_rows import apply, retire_orphans  # noqa: E402

def _blm_db():
    c = sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE bank_line_map (
        map_id INTEGER PRIMARY KEY, bank TEXT, table_type_id TEXT,
        row_label_norm TEXT, parent_label_norm TEXT NOT NULL DEFAULT '',
        concept_key TEXT, legal_entity TEXT, segment_key TEXT, geo_key TEXT,
        industry_key TEXT, period_type TEXT, balance TEXT,
        is_abstract INTEGER DEFAULT 0, negated_label INTEGER DEFAULT 0,
        map_status TEXT, mapped_by TEXT, confidence REAL, mapped_at TEXT,
        superseded_by INTEGER, note TEXT, basis TEXT,
        UNIQUE(bank, table_type_id, row_label_norm, parent_label_norm))""")
    c.execute("CREATE TABLE table_registry (table_type_id TEXT PRIMARY KEY)")
    c.execute("INSERT INTO table_registry VALUES ('FS_BALANCE_SELECTED')")
    return c

_dict = {"bs.assets.total": {"key": "bs.assets.total", "nature": "stock"}}

# Simulate: a PRIOR run authored total_assets under the old (model-branch) parent.
con = _blm_db()
con.execute("""INSERT INTO bank_line_map
    (bank, table_type_id, row_label_norm, parent_label_norm, concept_key,
     map_status, mapped_by, mapped_at)
    VALUES ('DBS','FS_BALANCE_SELECTED','total_assets','selected_balance_sheet_items',
            'bs.assets.total','human_confirmed','dashboard_rows.yaml','t0')""")
# The corpus (geometry branch) also has the row at top-level, still ai_proposed.
con.execute("""INSERT INTO bank_line_map
    (bank, table_type_id, row_label_norm, parent_label_norm, concept_key,
     map_status, mapped_by, mapped_at)
    VALUES ('DBS','FS_BALANCE_SELECTED','total_assets','','bs.assets.total',
            'ai_proposed','backfill:corpus','t0')""")
con.commit()

# Current yaml now authors ONLY the top-level (parent: "") anchor.
new_rows = [{"bank": "DBS", "tt": "FS_BALANCE_SELECTED", "label": "total_assets",
             "parent": "", "concept": "bs.assets.total"}]
apply(con, new_rows, _dict)
r = retire_orphans(con, new_rows)
check("exactly one anchor retired", r["deprecated"] == 1, str(r))
check("retirement resolved an unambiguous successor", r["deprecated_with_successor"] == 1, str(r))

old = con.execute("SELECT map_status, superseded_by FROM bank_line_map "
                   "WHERE parent_label_norm='selected_balance_sheet_items'").fetchone()
new = con.execute("SELECT map_id, map_status FROM bank_line_map "
                   "WHERE parent_label_norm=''").fetchone()
check("old anchor is DEPRECATED, not deleted", old is not None and old[0] == "deprecated", str(old))
check("old anchor's superseded_by points at the new anchor", old[1] == new[0], f"{old} vs {new}")
check("new anchor is human_confirmed", new[1] == "human_confirmed", str(new))
n_rows = con.execute("SELECT COUNT(*) FROM bank_line_map").fetchone()[0]
check("row count unchanged — nothing was deleted", n_rows == 2, str(n_rows))

# Ambiguous case: TWO live successor anchors share (bank, ttid, label) -> superseded_by must be NULL.
con2 = _blm_db()
con2.execute("""INSERT INTO bank_line_map
    (bank, table_type_id, row_label_norm, parent_label_norm, concept_key,
     map_status, mapped_by, mapped_at)
    VALUES ('DBS','FS_BALANCE_SELECTED','total_assets','old_parent',
            'bs.assets.total','human_confirmed','dashboard_rows.yaml','t0')""")
con2.execute("""INSERT INTO bank_line_map
    (bank, table_type_id, row_label_norm, parent_label_norm, concept_key,
     map_status, mapped_by, mapped_at)
    VALUES ('DBS','FS_BALANCE_SELECTED','total_assets','parent_a',
            'bs.assets.total','human_confirmed','dashboard_rows.yaml','t0')""")
con2.execute("""INSERT INTO bank_line_map
    (bank, table_type_id, row_label_norm, parent_label_norm, concept_key,
     map_status, mapped_by, mapped_at)
    VALUES ('DBS','FS_BALANCE_SELECTED','total_assets','parent_b',
            'bs.assets.total','human_confirmed','dashboard_rows.yaml','t0')""")
con2.commit()
ambiguous_rows = [
    {"bank": "DBS", "tt": "FS_BALANCE_SELECTED", "label": "total_assets", "parent": "parent_a", "concept": "bs.assets.total"},
    {"bank": "DBS", "tt": "FS_BALANCE_SELECTED", "label": "total_assets", "parent": "parent_b", "concept": "bs.assets.total"},
]
r2 = retire_orphans(con2, ambiguous_rows)
check("ambiguous retirement still deprecates", r2["deprecated"] == 1, str(r2))
check("ambiguous retirement leaves superseded_by NULL", r2["deprecated_ambiguous"] == 1, str(r2))
old2 = con2.execute("SELECT map_status, superseded_by FROM bank_line_map WHERE parent_label_norm='old_parent'").fetchone()
check("ambiguous old anchor deprecated with NULL superseded_by", old2 == ("deprecated", None), str(old2))

print()
if _fail:
    print(f"{_fail} FAILURES")
    sys.exit(1)
print("all mapping tests pass")
