"""load_anchors — G13 continuation, Step 2: load resolved anchors into
`bank_line_map` on the stable key `(bank, table_type_id, parent_label_norm,
row_label_norm)`, human_confirmed. See docs/specs/2026-08-03-anchor-scope-resolution.md
and the resolution report for how each row got here.

Only `resolution='anchor'` rows that resolved PASS (via resolve_anchors.py) are
loaded here. `pending_extraction` rows have no resolved row_lineage yet -- not
loaded, reported separately. `derived` rows route to the derivation layer
(Step 5), never here. `not_disclosed` rows go to a separate concept_disclosure
table (no row address exists for them by definition). `pending_anchor` rows are
simply not authored yet -- reported, not loaded.

Reconciliation policy (per the 2026-08-03 resolution): trust the address, not
the label.
  - OVERLAP where concept_key already matches: confirm in place (idempotent).
  - OVERLAP where concept_key differs AND the existing row's concept_key is
    NULL (a placeholder `ai_proposed` row with nothing stamped): superseded,
    deprecated -- never destroyed (MAPPING_LAYER SS2.4.5).
  - OVERLAP where concept_key differs AND the existing row IS a resolved
    concept (historically bs.assets.customer_loans_net vs ..._gross for UOB --
    that pair no longer exists, `_net` was retired 2026-08-04, but the branch
    still guards the general case): the address is correct on both sides, only
    the label disagrees.
    Load the anchor's row as ITS OWN human_confirmed entry, note-flagged, and
    do NOT deprecate the existing entry -- both are correct at the level they
    each claim (existing: right label, same address; new: address is what the
    map anchors, label needs a concept-level decision later). This creates two
    human_confirmed rows at one address by design -- flagged loudly here and
    in DECISIONS.md, not swept under a silent overwrite.

    python3 findociq/pipeline/mapping/load_anchors.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import csv
import datetime
import importlib.util
import sqlite3
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(_HERE.parent))
from mapping.normalize import normalize_row_label  # noqa: E402

MAP_CSV = _REPO / "findociq" / "data" / "derived" / "lineage_identity_map.csv"

spec = importlib.util.spec_from_file_location("resolve_anchors", str(_HERE / "resolve_anchors.py"))
_ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_ra)

# addresses where BOTH the existing human_confirmed row and the new anchor are
# correct -- only the label disagrees. Load both, deprecate neither.
KNOWN_LABEL_ONLY_CONFLICTS = {("UOB", "bs.assets.customer_loans_gross")}


def load(con: sqlite3.Connection) -> dict:
    s = {"loaded": 0, "confirmed_in_place": 0, "superseded_placeholder": 0,
         "label_conflict_loaded": 0, "not_disclosed": 0, "skipped_pending_extraction": 0}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    con.execute("""CREATE TABLE IF NOT EXISTS concept_disclosure (
        bank TEXT NOT NULL,
        concept_key TEXT NOT NULL,
        period TEXT NOT NULL,
        status TEXT NOT NULL,
        note TEXT,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (bank, concept_key, period)
    )""")

    with open(MAP_CSV) as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        if r["resolution"] == "not_disclosed":
            con.execute("INSERT OR REPLACE INTO concept_disclosure VALUES (?,?,?,?,?,?)",
                        (r["bank"], r["concept_key"], r["period"], "not_disclosed",
                         r["review_flag"], now))
            s["not_disclosed"] += 1
            continue
        if r["resolution"] == "pending_extraction":
            s["skipped_pending_extraction"] += 1
            continue
        if r["resolution"] != "anchor":
            continue

        x = _ra.resolve_one(con, r)
        if x["status"] != "PASS":
            raise SystemExit(f"load_anchors: {r['concept_key']} ({r['bank']}) did not resolve PASS "
                              f"-- re-run resolve_anchors.py, do not load an unresolved anchor")

        # The REAL matched address, not the map's raw parent_row/line_item --
        # matters when a row is uniquely identifiable despite a mislabeled or
        # absent parent in the map (e.g. DBS's NII group total: map says no
        # parent, the physical row's own lineage parent is a mis-grouped
        # header text). row_dim gets stamped against this same real address
        # (migrate_serving_views.py's stamp_human_anchors); storing anything
        # else here means the two will never agree for such rows.
        parent_norm = x["matched_parent_norm"]
        line_norm = x["matched_leaf_norm"]
        bank, ttid = x["bank"], x["table_type_id"]

        # UNIQUE(bank, table_type_id, row_label_norm, parent_label_norm) means at
        # most ONE row can ever occupy this address -- "supersede" here has to be
        # an in-place UPDATE (same map_id), not insert-new-and-deprecate-old.
        # superseded_by is for when the ADDRESS itself moves across quarters, not
        # for a same-address precedence upgrade.
        existing = con.execute(
            "SELECT map_id, concept_key, map_status FROM bank_line_map "
            "WHERE bank=? AND table_type_id=? AND row_label_norm=? AND parent_label_norm=?",
            (bank, ttid, line_norm, parent_norm)).fetchone()

        note = r["review_flag"] or None

        if existing is None:
            con.execute("""
                INSERT INTO bank_line_map
                    (bank, table_type_id, row_label_norm, parent_label_norm, concept_key,
                     legal_entity, map_status, mapped_by, confidence, mapped_at, note)
                VALUES (?,?,?,?,?, 'CONSOLIDATED', 'human_confirmed', 'lineage_identity_map.csv', 1.0, ?, ?)
            """, (bank, ttid, line_norm, parent_norm, r["concept_key"], now, note))
            s["loaded"] += 1
            continue

        old_map_id, old_ck, old_status = existing
        if old_status == "human_confirmed" and old_ck == r["concept_key"]:
            s["confirmed_in_place"] += 1
            continue  # idempotent: exact anchor already landed, nothing to write

        # `old_ck is not None` guard: a human_confirmed row with a NULL
        # concept_key is a confirmed ADDRESS with nothing stamped on it, not a
        # rival binding -- it belongs in the supersede-in-place branch below
        # (see this module's docstring, "OVERLAP where ... concept_key is NULL").
        # Without the guard, `None != <any concept>` reads as a conflict and
        # aborts the run. That state is reachable: the 2026-08-04 M3 cleanup
        # wiped concept_key without demoting map_status.
        if old_status == "human_confirmed" and old_ck is not None and old_ck != r["concept_key"]:
            if (bank, r["concept_key"]) not in KNOWN_LABEL_ONLY_CONFLICTS:
                raise SystemExit(f"load_anchors: unexpected unresolved concept_key conflict for "
                                  f"{r['concept_key']} ({bank}) vs existing map_id={old_map_id} "
                                  f"concept_key={old_ck!r} -- not in KNOWN_LABEL_ONLY_CONFLICTS, "
                                  f"stopping rather than overwriting silently")
            # Address is already correctly captured under the existing (different)
            # label -- annotate, don't overwrite the correct existing concept_key.
            flag = (f"LABEL CONFLICT: lineage_identity_map.csv anchors this same address as "
                    f"concept_key={r['concept_key']!r} ({r['review_flag'] or 'no note'}). "
                    f"Address is correct; label disagreement only -- open decision: reconcile "
                    f"net/gross at concept level, see DECISIONS.md.")
            cur_note = con.execute("SELECT note FROM bank_line_map WHERE map_id=?",
                                    (old_map_id,)).fetchone()[0] or ""
            if "LABEL CONFLICT:" not in cur_note:
                con.execute("UPDATE bank_line_map SET note = COALESCE(note || ' | ', '') || ?, "
                            "mapped_at=? WHERE map_id=?", (flag, now, old_map_id))
            s["label_conflict_loaded"] += 1
            continue

        # existing row is ai_proposed / ai_verified / deprecated (any concept_key,
        # including None) -- the human anchor supersedes it in place.
        con.execute("""
            UPDATE bank_line_map
            SET concept_key=?, map_status='human_confirmed', mapped_by='lineage_identity_map.csv',
                confidence=1.0, mapped_at=?, note=?
            WHERE map_id=?
        """, (r["concept_key"], now, note, old_map_id))
        s["superseded_placeholder"] += 1

    con.commit()
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    s = load(con)
    for k, v in s.items():
        print(f"{k:28}: {v}")
    con.close()


if __name__ == "__main__":
    main()
