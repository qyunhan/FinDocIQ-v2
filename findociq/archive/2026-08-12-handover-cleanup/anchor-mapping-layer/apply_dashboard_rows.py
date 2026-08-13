"""mapping.apply_dashboard_rows — STEP 5 of docs/specs/MAPPING_LAYER.md §4.

Writes `dashboard_rows.yaml` into `bank_line_map` at
`map_status='human_confirmed'` — the only status that loads a value.

These rows were authored by READING each exhibit's structure. Value-matching may
propose a candidate but never confirms one: DBS's commercial-book NII 14,494 is
within 0.05% of group NII 14,500, so no tolerance can separate them. The parent
can, which is the whole point of the anchor.

Safety properties:
  * Every anchor is verified to EXIST in the corpus before anything is written.
    A missing anchor aborts the run — an authored row that matches nothing is a
    mistake, not a no-op.
  * Every concept_key is verified to exist in the concept dictionary.
  * Writes are UPSERTs keyed on the anchor. Re-running is idempotent.
  * Rows already at human_corrected are NOT overwritten — a human correction
    outranks this seed (precedence: human_corrected > human_confirmed).
  * RETIREMENT: if an anchor was previously authored here (map_status=
    'human_confirmed', mapped_by='dashboard_rows.yaml') and is no longer in
    the current yaml, it is never left stale and never deleted — it is set to
    map_status='deprecated', with superseded_by pointing at the map_id of the
    newly-authored anchor for the same (bank, table_type_id, row_label_norm)
    when exactly one such anchor exists (NULL if ambiguous or absent). This is
    MAPPING_LAYER §2.4.5: "a correction must not destroy the prior mapping."
    Rows at human_corrected, or mapped_by anything other than this script, are
    never touched by retirement.

    python3 findociq/pipeline/mapping/apply_dashboard_rows.py --db findociq/db/compiled_fs.db
    python3 findociq/pipeline/mapping/apply_dashboard_rows.py --check   # verify only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_ROWS = _HERE / "dashboard_rows.yaml"
_DICT = _HERE.parent / "concept" / "concept_dictionary.yaml"

_NATURE_TO_PERIOD_TYPE = {"flow": "duration", "ratio_flow": "duration",
                          "stock": "instant", "ratio_point": "instant"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dictionary() -> dict[str, dict]:
    doc = yaml.safe_load(_DICT.read_text())
    return {c["key"]: c for c in doc.get("concepts", [])}


def verify(con: sqlite3.Connection, rows: list[dict], dictionary: dict) -> list[str]:
    """Return a list of problems. Empty list == safe to write."""
    problems = []
    for r in rows:
        anchor = (r["bank"], r["tt"], r["label"], r.get("parent") or "")
        n = con.execute(
            "SELECT COUNT(*) FROM bank_line_map WHERE bank=? AND table_type_id=? "
            "AND row_label_norm=? AND parent_label_norm=?", anchor).fetchone()[0]
        if not n:
            problems.append(f"ANCHOR NOT IN CORPUS: {anchor}")
        ck = r.get("concept")
        if ck and ck not in dictionary:
            problems.append(f"CONCEPT NOT IN DICTIONARY: {ck}  ({anchor})")
        tt = con.execute("SELECT COUNT(*) FROM table_registry WHERE table_type_id=?",
                         (r["tt"],)).fetchone()[0]
        if not tt:
            problems.append(f"table_type_id NOT IN REGISTRY: {r['tt']}")
    return problems


def apply(con: sqlite3.Connection, rows: list[dict], dictionary: dict) -> dict:
    now = _now()
    s = {"written": 0, "skipped_human_corrected": 0}
    for r in rows:
        anchor = (r["bank"], r["tt"], r["label"], r.get("parent") or "")
        cur = con.execute(
            "SELECT map_status FROM bank_line_map WHERE bank=? AND table_type_id=? "
            "AND row_label_norm=? AND parent_label_norm=?", anchor).fetchone()
        if cur and cur[0] == "human_corrected":
            s["skipped_human_corrected"] += 1
            continue
        ck = r.get("concept")
        concept = dictionary.get(ck or "", {})
        con.execute("""
            UPDATE bank_line_map
               SET concept_key=?, segment_key=?, geo_key=?, industry_key=?,
                   legal_entity=?, period_type=?, basis=?, negated_label=?,
                   is_abstract=0, map_status='human_confirmed',
                   mapped_by='dashboard_rows.yaml', confidence=1.0,
                   mapped_at=?, note=?
             WHERE bank=? AND table_type_id=? AND row_label_norm=? AND parent_label_norm=?
        """, (ck, r.get("segment"), r.get("geo"), r.get("industry"),
              r.get("legal_entity", "CONSOLIDATED"),
              _NATURE_TO_PERIOD_TYPE.get(concept.get("nature")),
              r.get("basis"), 1 if r.get("negated_label") else 0,
              now, r.get("note")) + anchor)
        s["written"] += 1
    con.commit()
    return s


def retire_orphans(con: sqlite3.Connection, rows: list[dict]) -> dict:
    """Deprecate bank_line_map rows this script authored in a PRIOR run whose
    anchor is no longer in the current authored set. Never deletes a row
    (MAPPING_LAYER §2.4.5) — flips map_status to 'deprecated' and, when
    exactly one live replacement anchor shares (bank, table_type_id,
    row_label_norm), records it in superseded_by.
    """
    now = _now()
    current = {(r["bank"], r["tt"], r["label"], r.get("parent") or "") for r in rows}

    authored = con.execute(
        "SELECT map_id, bank, table_type_id, row_label_norm, parent_label_norm "
        "FROM bank_line_map WHERE map_status='human_confirmed' AND mapped_by='dashboard_rows.yaml'"
    ).fetchall()

    s = {"deprecated": 0, "deprecated_with_successor": 0, "deprecated_ambiguous": 0}
    for map_id, bank, ttid, label, parent in authored:
        if (bank, ttid, label, parent) in current:
            continue
        candidates = con.execute(
            "SELECT map_id FROM bank_line_map "
            "WHERE bank=? AND table_type_id=? AND row_label_norm=? "
            "AND map_status='human_confirmed' AND mapped_by='dashboard_rows.yaml' AND map_id != ?",
            (bank, ttid, label, map_id)).fetchall()
        successor = candidates[0][0] if len(candidates) == 1 else None
        con.execute(
            "UPDATE bank_line_map SET map_status='deprecated', superseded_by=?, mapped_at=? "
            "WHERE map_id=?", (successor, now, map_id))
        s["deprecated"] += 1
        if successor is not None:
            s["deprecated_with_successor"] += 1
        else:
            s["deprecated_ambiguous"] += 1
    con.commit()
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    args = ap.parse_args()

    rows = yaml.safe_load(_ROWS.read_text())["rows"]
    dictionary = _dictionary()
    con = sqlite3.connect(args.db)

    problems = verify(con, rows, dictionary)
    print(f"{len(rows)} authored rows; {len(problems)} problems")
    for p in problems:
        print(f"   {p}")
    if problems:
        print("\nABORTED — an authored row that matches nothing is a mistake, not a no-op.")
        sys.exit(1)
    if args.check:
        print("check only — nothing written")
        return

    s = apply(con, rows, dictionary)
    print(f"\nwritten as human_confirmed : {s['written']}")
    print(f"skipped (human_corrected)  : {s['skipped_human_corrected']}")

    r = retire_orphans(con, rows)
    print(f"\nretired (deprecated)               : {r['deprecated']}")
    print(f"  with a single unambiguous successor : {r['deprecated_with_successor']}")
    print(f"  ambiguous/absent (superseded_by NULL): {r['deprecated_ambiguous']}")

    tot = con.execute("SELECT map_status, COUNT(*) FROM bank_line_map GROUP BY 1").fetchall()
    print("\nbank_line_map by status:")
    for st, n in tot:
        print(f"   {st:<18} {n:>5}")
    con.close()


if __name__ == "__main__":
    main()
