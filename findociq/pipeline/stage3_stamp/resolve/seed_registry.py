"""stage3_stamp.resolve.seed_registry — STEP 3 of docs/specs/MAPPING_LAYER.md §4.

MASTERLIST WRITER (L1 vocabulary only — the `table_type_id` dictionary the
masterlist points into, NOT the masterlist itself). See
`docs/specs/2026-08-04-masterlist.md` §2 "Known debt": L1 being split across
this YAML writer and `migrate_add_table_catalog.py`'s seed-CSV writer is
historical, not designed; the seed CSV wins on any disagreement.

Loads `table_registry.yaml` into `table_registry` + `table_registry_alias`, then
classifies every table in the DB and reports match statistics.

Idempotent. Seed rows are UPSERTed; alias rows whose `source='human_confirmed'`
are NEVER overwritten by the seed (same MERGE-protects-manual-fixes semantics as
the template registry).

    python3 findociq/pipeline/stage3_stamp/resolve/seed_registry.py --db findociq/db/compiled_fs.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage3_stamp.resolve.registry import classify_corpus  # noqa: E402

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[4]
_YAML = _HERE / "table_registry.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def seed(con: sqlite3.Connection, yaml_path: Path = _YAML) -> dict:
    doc = yaml.safe_load(yaml_path.read_text())
    now = _now()
    n_types = n_alias = n_kept = 0

    for t in doc["types"]:
        con.execute("""
            INSERT INTO table_registry (table_type_id, display_name, statement_class,
                period_nature, dim_hint, legal_entity_default, legal_entity_axis,
                is_regulatory, notes)
            VALUES (:id,:dn,:sc,:pn,:dh,:led,:lea,:reg,:notes)
            ON CONFLICT(table_type_id) DO UPDATE SET
                display_name=excluded.display_name,
                statement_class=excluded.statement_class,
                period_nature=excluded.period_nature,
                dim_hint=excluded.dim_hint,
                legal_entity_default=excluded.legal_entity_default,
                legal_entity_axis=excluded.legal_entity_axis,
                is_regulatory=excluded.is_regulatory
        """, dict(id=t["id"], dn=t["display_name"], sc=t["statement_class"],
                  pn=t["period_nature"], dh=t.get("dim_hint"),
                  led=t.get("legal_entity_default", "CONSOLIDATED"),
                  lea=t.get("legal_entity_axis"),
                  reg=1 if t.get("is_regulatory") else 0, notes=t.get("notes")))
        n_types += 1

        for a in t.get("aliases", []):
            if isinstance(a, dict):
                alias_norm, bank = a["alias"], a.get("bank", "*")
            else:
                alias_norm, bank = a, "*"
            existing = con.execute(
                "SELECT source FROM table_registry_alias WHERE alias_norm=? AND bank=?",
                (alias_norm, bank)).fetchone()
            if existing and existing[0] == "human_confirmed":
                n_kept += 1          # never clobber a human decision
                continue
            con.execute("""
                INSERT INTO table_registry_alias (alias_norm, bank, table_type_id, source, added_at)
                VALUES (?,?,?,'seed',?)
                ON CONFLICT(alias_norm, bank) DO UPDATE SET
                    table_type_id=excluded.table_type_id, added_at=excluded.added_at
            """, (alias_norm, bank, t["id"], now))
            n_alias += 1
    con.commit()
    return {"types": n_types, "aliases": n_alias, "human_aliases_kept": n_kept}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_REPO / "findociq" / "db" / "compiled_fs.db"))
    ap.add_argument("--show-unclassified", type=int, default=25)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    s = seed(con)
    print(f"seeded: {s['types']} types, {s['aliases']} aliases "
          f"({s['human_aliases_kept']} human aliases preserved)")

    st = classify_corpus(con)
    pct = 100 * st["matched"] / st["total"] if st["total"] else 0
    print(f"\nclassified {st['matched']}/{st['total']} tables ({pct:.0f}%) — "
          f"{st['unclassified']} UNCLASSIFIED")
    print(f"  by match level: {st['by_level']}")
    print("\n  by table_type_id:")
    for k, v in sorted(st["by_type"].items(), key=lambda x: -x[1]):
        print(f"     {k:<28} {v:>4}")
    if st["unclassified_aliases"]:
        print(f"\n  top UNCLASSIFIED (would go to review queue):")
        for k, v in sorted(st["unclassified_aliases"].items(),
                           key=lambda x: -x[1])[:args.show_unclassified]:
            print(f"     {v:>3}  {k[:96]}")
    con.close()


if __name__ == "__main__":
    main()
