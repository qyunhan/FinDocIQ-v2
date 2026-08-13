"""concept.run — orchestrate the concept-resolution layer: migrate -> load
dictionary (2) -> deterministic (3) -> llm residue (4) -> validate (5), then a
coverage summary (concept × institution × period).

Idempotent and safe to re-run: deterministic re-stamps identically; only
genuinely-new labels reach the LLM (accepted answers become concept_map aliases,
so they match deterministically next time).

One-command entry:
  python3 findociq/pipeline/concept/run.py --db findociq/db/compiled_fs.db \
      [--dry-run] [--no-llm]

--dry-run runs on a THROW-AWAY COPY of the DB (zero mutation) and implies --no-llm:
it reports the deterministic coverage that WOULD result.

Portable SQL (named params, COALESCE, no SQLite-only funcs beyond the reporting
GROUP_CONCAT, which is isolated to the summary).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import ensure_schema, load_into_concept_map  # noqa: E402
from concept.resolve_deterministic import resolve_deterministic  # noqa: E402
from concept.validate import validate  # noqa: E402


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def print_coverage(con) -> None:
    """concept × institution × period matrix from stamped, numeric cells."""
    rows = con.execute(
        "SELECT concept_key, institution, period, COUNT(*) "
        "FROM v_cell WHERE concept_key IS NOT NULL AND row_hierarchy >= 1 "
        "GROUP BY concept_key, institution, period "
        "ORDER BY concept_key, institution, period").fetchall()
    by_concept: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    institutions: set[str] = set()
    for key, inst, period, _ in rows:
        short = inst.split()[0]
        institutions.add(short)
        by_concept[key][short].add(period)
    insts = sorted(institutions)
    print("\n=== COVERAGE MATRIX (concept × bank; cell = #distinct periods, "
          "'.' = none) ===")
    print(f"{'concept_key':40} " + " ".join(f"{i:>6}" for i in insts) + "   periods")
    for key in sorted(by_concept):
        cells = []
        allp: set = set()
        for i in insts:
            ps = by_concept[key].get(i, set())
            allp |= ps
            cells.append(f"{len(ps):>6}" if ps else f"{'.':>6}")
        pr = ",".join(sorted(p[:7] for p in allp))
        print(f"{key:40} " + " ".join(cells) + f"   {pr}")
    print(f"\nconcepts resolved: {len(by_concept)}  banks: {insts}")


def run(db_path: str, *, dry_run: bool = False, no_llm: bool = False) -> int:
    work_path = db_path
    tmp = None
    if dry_run:
        no_llm = True
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy(db_path, tmp.name)
        work_path = tmp.name
        print(f"[dry-run] operating on throw-away copy {work_path}")

    con = _connect(work_path)
    try:
        # --- migration + (2) dictionary expansion ----------------------------
        actions = ensure_schema(con)
        for a in actions:
            print(f"[schema] {a}")
        dsum = load_into_concept_map(con)
        print(f"\n[dictionary] concepts={dsum['concepts']} aliases_seen={dsum['aliases_seen']} "
              f"wildcard_rows_total={dsum['wildcard_rows_total']} "
              f"inserted_now={dsum['wildcard_rows_inserted']}")
        if dsum["collisions"]:
            print(f"[dictionary] ALIAS COLLISIONS ({len(dsum['collisions'])}): "
                  f"{dsum['collisions']}")

        # --- (3) deterministic ----------------------------------------------
        det = resolve_deterministic(con)
        print(f"\n=== DETERMINISTIC (pre-LLM) ===")
        print(f"  rows total            {det['total']}")
        print(f"  stamped (new)         {det['stamped']}")
        print(f"  re-stamped (changed)  {det['restamped']}")
        print(f"  already-correct       {det['already_correct']}")
        print(f"  skipped-structural    {det['skipped_structural']}")
        print(f"  dim-scope suppressed  {det['suppressed_dimensional']} "
              f"(of which un-stamped {det['unstamped_dimensional']})")
        print(f"  unmatched (residue)   {det['unmatched']}")

        # --- (4) LLM residue -------------------------------------------------
        if not no_llm and det["residue"]:
            from concept.resolve_llm import resolve_llm  # deferred: only import when used
            llm = resolve_llm(con, det["residue"])
            print(f"\n=== LLM RESIDUE ===")
            print(f"  residue rows          {llm['residue_rows']}")
            print(f"  distinct labels       {llm['distinct_labels']}")
            print(f"  distinct contexts     {llm['distinct_contexts']} (label × table_type)")
            print(f"  calls                 {llm['calls']}")
            print(f"  tokens in/out/think   {llm['prompt_tokens']}/{llm['output_tokens']}/{llm['thinking_tokens']}")
            print(f"  cost                  ${llm['cost_usd']:.4f}")
            print(f"  accepted              {llm['accepted']} (rows stamped {llm['rows_stamped']})")
            print(f"  rejected(low-conf)    {llm['rejected_low_conf']}")
            print(f"  none                  {llm['none']}")
            if llm["aliases_appended"]:
                print(f"  aliases appended ({len(llm['aliases_appended'])}):")
                for a in llm["aliases_appended"]:
                    print(f"     + {a['label_norm']!r} -> {a['concept_key']}")
            if llm.get("aliases_skipped_ambiguous"):
                print(f"  aliases NOT promoted — ambiguous across table types "
                      f"({len(llm['aliases_skipped_ambiguous'])}):")
                for a in llm["aliases_skipped_ambiguous"]:
                    print(f"     ~ {a['label_norm']!r} -> {a['concept_key']} "
                          f"(types {a['table_types']})")
            if llm["review"]:
                print(f"  REVIEW (left NULL, {len(llm['review'])}):")
                for r in llm["review"]:
                    print(f"     ? {r['label']!r} ({r.get('table_type','')}) — {r['reason']}"
                          + (f" [{r['concept_key']}]" if r.get("concept_key") else ""))
        elif no_llm:
            print("\n[llm] skipped (--no-llm / dry-run)")

        # --- (5) validate ----------------------------------------------------
        val = validate(con)
        print(f"\n=== VALIDATION ===")
        for c in val["checks"]:
            mark = "PASS" if c["failed"] == 0 else "FAIL"
            print(f"  [{mark}] {c['name']}: checked {c['checked']}, "
                  f"passed {c['passed']}, failed {c['failed']}")
        if val["flags"]:
            print(f"  FLAGS ({val['total_failed']}) — mapping suspect, NOT auto-unstamped:")
            for f in val["flags"]:
                print(f"     {f}")

        # --- coverage --------------------------------------------------------
        print_coverage(con)
        return 0
    finally:
        con.close()
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Concept-resolution layer orchestrator.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="run on a throw-away copy, no LLM, report only")
    ap.add_argument("--no-llm", action="store_true", help="deterministic + validate only")
    a = ap.parse_args(argv)
    return run(a.db, dry_run=a.dry_run, no_llm=a.no_llm)


if __name__ == "__main__":
    raise SystemExit(main())
