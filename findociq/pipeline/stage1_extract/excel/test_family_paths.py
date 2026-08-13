"""Plain check() script for family-aware output paths/labelling (NO pytest).
Exit 0 all-pass / 1 any-fail.

Binding acceptance criteria from
docs/specs/2026-07-29-family-aware-output-paths.md:
  1. family="pillar3" REGRESSION — RunPaths output identical to pre-change.
  2. family="fs" produces outputs/fs/... and DOC_TITLE "Financial Statements".
  3. Unknown/None family falls back to pillar3 paths AND prints a visible note.
  4. Sheet-name truncation: no trailing space, <=31 chars, "Table N" suffix intact.

Run:  python findociq/pipeline/stage2_load/test_family_paths.py
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # pipeline/ on path
import stage1_extract.chunk.schema as schema  # noqa: E402
from stage1_extract.excel.workbook import sheet_name, table_sheet_name  # noqa: E402

_REPO = Path(__file__).resolve().parents[4]

_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


# ===========================================================================
# 1) REGRESSION — family="pillar3" byte-identical to pre-change.
#
# Baseline captured from the working tree BEFORE this change (clean checkout,
# `python3 -c "... schema.RunPaths('dbs','2Q25','DBS_2Q25_report') ..."`):
#   run_dir   findociq/outputs/pillar3/dbs_2Q25
#   xlsx      findociq/outputs/pillar3/dbs_2Q25/DBS_2Q25_report_pillar3.xlsx
#   index     findociq/outputs/pillar3/dbs_2Q25/DBS_2Q25_report_pillar3.index.json
#   audit_dir findociq/outputs/pillar3/dbs_2Q25/audit/DBS_2Q25_report
#   logs_dir  findociq/outputs/pillar3/dbs_2Q25/logs
#   cost      findociq/outputs/pillar3/dbs_2Q25/logs/cost_summary.json
#   api_log   findociq/outputs/pillar3/dbs_2Q25/logs/api_log.xlsx
#   ledger    findociq/outputs/pillar3/_ledgers/dbs_api_usage.jsonl
#   DOC_TITLE Pillar 3 Disclosures
# ===========================================================================
def test_pillar3_regression() -> None:
    print("1) REGRESSION — family='pillar3' identical to pre-change")
    rp = schema.RunPaths("dbs", "2Q25", "DBS_2Q25_report", family="pillar3")

    def rel(p: Path) -> str:
        return str(p.relative_to(_REPO))

    check("run_dir", rel(rp.run_dir) == "findociq/outputs/pillar3/dbs_2Q25",
          rel(rp.run_dir))
    check("xlsx", rel(rp.xlsx) ==
          "findociq/outputs/pillar3/dbs_2Q25/DBS_2Q25_report_pillar3.xlsx",
          rel(rp.xlsx))
    check("index", rel(rp.index) ==
          "findociq/outputs/pillar3/dbs_2Q25/DBS_2Q25_report_pillar3.index.json",
          rel(rp.index))
    check("audit_dir", rel(rp.audit_dir) ==
          "findociq/outputs/pillar3/dbs_2Q25/audit/DBS_2Q25_report",
          rel(rp.audit_dir))
    check("logs_dir", rel(rp.logs_dir) == "findociq/outputs/pillar3/dbs_2Q25/logs",
          rel(rp.logs_dir))
    check("cost", rel(rp.cost) ==
          "findociq/outputs/pillar3/dbs_2Q25/logs/cost_summary.json", rel(rp.cost))
    check("api_log", rel(rp.api_log) ==
          "findociq/outputs/pillar3/dbs_2Q25/logs/api_log.xlsx", rel(rp.api_log))
    check("ledger", rel(rp.ledger) ==
          "findociq/outputs/pillar3/_ledgers/dbs_api_usage.jsonl", rel(rp.ledger))

    _, title, known = schema.resolve_family("pillar3")
    check("DOC_TITLE", title == "Pillar 3 Disclosures", title)
    check("pillar3 is a known family", known is True)

    # Also confirm RunPaths()'s DEFAULT (no family kwarg) is still pillar3 —
    # standalone callers that never pass family must see no behaviour change.
    rp_default = schema.RunPaths("dbs", "2Q25", "DBS_2Q25_report")
    check("default (no family kwarg) == explicit pillar3",
          str(rp_default.xlsx) == str(rp.xlsx))


# ===========================================================================
# 2) family="fs" -> outputs/fs/... + "Financial Statements"
# ===========================================================================
def test_fs_family() -> None:
    print("2) family='fs' -> outputs/fs/... + 'Financial Statements'")
    rp = schema.RunPaths("dbs", "1Q26", "DBS_1Q26_trading_update", family="fs")

    def rel(p: Path) -> str:
        return str(p.relative_to(_REPO))

    check("run_dir", rel(rp.run_dir) == "findociq/outputs/fs/dbs_1Q26", rel(rp.run_dir))
    check("xlsx", rel(rp.xlsx) ==
          "findociq/outputs/fs/dbs_1Q26/DBS_1Q26_trading_update_fs.xlsx", rel(rp.xlsx))
    check("index", rel(rp.index) ==
          "findociq/outputs/fs/dbs_1Q26/DBS_1Q26_trading_update_fs.index.json",
          rel(rp.index))
    check("audit_dir", rel(rp.audit_dir) ==
          "findociq/outputs/fs/dbs_1Q26/audit/DBS_1Q26_trading_update", rel(rp.audit_dir))
    check("ledger", rel(rp.ledger) == "findociq/outputs/fs/_ledgers/dbs_api_usage.jsonl",
          rel(rp.ledger))
    check("does NOT collide with pillar3 root",
          "pillar3" not in rel(rp.run_dir))

    _, title, known = schema.resolve_family("fs")
    check("DOC_TITLE", title == "Financial Statements", title)
    check("fs is a known family", known is True)


# ===========================================================================
# 3) Unknown / None family -> pillar3 fallback + visible note
# ===========================================================================
def test_unknown_family_fallback() -> None:
    print("3) unknown/None family -> pillar3 fallback + visible note")

    buf = io.StringIO()
    with redirect_stdout(buf):
        family, title, known = schema.resolve_family("slides")
    out = buf.getvalue()
    check("unknown family resolves to pillar3", family == "pillar3", family)
    check("unknown family title falls back", title == "Pillar 3 Disclosures", title)
    check("unknown family known=False", known is False)
    check("unknown family prints a visible note", "slides" in out and "pillar3" in out, out)

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        family2, title2, known2 = schema.resolve_family(None)
    out2 = buf2.getvalue()
    check("None family resolves to pillar3", family2 == "pillar3", family2)
    check("None family known=False", known2 is False)
    check("None family prints a visible note", "pillar3" in out2, out2)

    # RunPaths itself must surface the same fallback+note when constructed
    # directly with a bad family (not pre-resolved by a caller).
    buf3 = io.StringIO()
    with redirect_stdout(buf3):
        rp = schema.RunPaths("dbs", "2Q25", "DBS_2Q25_report", family="bogus")
    out3 = buf3.getvalue()
    check("RunPaths falls back to pillar3 paths",
          str(rp.run_dir) == str(schema._P3_ROOT / "dbs_2Q25"))
    check("RunPaths prints a visible note for unknown family",
          "bogus" in out3, out3)


# ===========================================================================
# 4) Sheet-name truncation: no trailing space, <=31 chars, 'Table N' intact
# ===========================================================================
def test_sheet_name_truncation() -> None:
    print("4) sheet-name truncation + conditional ' Table N' suffix")

    # MULTI-table section: the ' Table N' suffix is present and never amputated,
    # even when the section_id is long. (The reported bug: 'key_financial_ratios_2_3'
    # + ' Table 1' amputated the trailing '1' of 'Table 1' under a bare [:31].)
    used = set()
    name = table_sheet_name(used, "key_financial_ratios_2_3", 1, total_tables=2)
    check("multi: no trailing space", name == name.rstrip(), repr(name))
    check("multi: <=31 chars", len(name) <= 31, f"len={len(name)}")
    check("multi: 'Table 1' suffix intact (not amputated to 'Table ')",
          name.endswith("Table 1"), repr(name))

    # SINGLE-table section: NO ' Table N' suffix — the tab keeps the section id
    # (truncated to 31), so we don't lose part of the visible name for nothing.
    used_s = set()
    single = table_sheet_name(used_s, "selected_income_statement_items", 1, total_tables=1)
    check("single: no ' Table' suffix", "Table" not in single, repr(single))
    check("single: keeps section id (truncated to 31)",
          single == "selected_income_statement_items"[:31].rstrip(), repr(single))
    check("single: <=31 chars", len(single) <= 31, f"len={len(single)}")
    check("single: no trailing space", single == single.rstrip(), repr(single))

    # A short multi-table section_id + suffix stays intact untouched.
    used2 = set()
    name2 = table_sheet_name(used2, "per_share_data_3", 1, total_tables=2)
    check("short multi: exact expected value", name2 == "per_share_data_3 Table 1", name2)
    check("short multi: no trailing space", name2 == name2.rstrip(), repr(name2))
    check("short multi: <=31 chars", len(name2) <= 31)

    # sheet_name (no Table-N suffix) must also never trail a space and must
    # respect the 31-char Excel limit.
    used3 = set()
    long_title = "selected_balance_sheet_items_much_longer_title_here"
    name3 = sheet_name(used3, "x", long_title)
    check("sheet_name: no trailing space", name3 == name3.rstrip(), repr(name3))
    check("sheet_name: <=31 chars", len(name3) <= 31, f"len={len(name3)}")

    # Collision handling (name already used) must also stay clean.
    used4 = {"per_share_data_3 Table 1"}
    name4 = table_sheet_name(used4, "per_share_data_3", 1, total_tables=2)
    check("collision: no trailing space", name4 == name4.rstrip(), repr(name4))
    check("collision: <=31 chars", len(name4) <= 31, f"len={len(name4)}")
    check("collision: distinct from original", name4 != "per_share_data_3 Table 1", name4)

    # Sweep many synthetic long ids to make sure the invariant holds broadly:
    # total_tables>1 -> ' Table N' present + intact; total_tables==1 -> NO suffix.
    # All names <=31 chars and never end in a space.
    bad = []
    for n in range(1, 40):
        sid = "x" * n
        for table_n, total in ((1, 1), (1, 3), (2, 3), (10, 20), (99, 99)):
            used_sw = set()
            nm = table_sheet_name(used_sw, sid, table_n, total_tables=total)
            ok = nm == nm.rstrip() and len(nm) <= 31
            ok = ok and (nm.endswith(f"Table {table_n}") if total > 1 else "Table" not in nm)
            if not ok:
                bad.append((n, table_n, total, nm))
    check("sweep: suffix rule holds + no overflow/trailing-space",
          not bad, str(bad[:5]))


def test_out_root_redirects_all_families() -> None:
    print("5) --out-root redirects EVERY family (regression: fs was ignored,"
          " writing into the repo working tree)")
    import shutil
    import tempfile
    orig_out, orig_p3 = schema._OUTPUTS_ROOT, schema._P3_ROOT
    tmp = Path(tempfile.mkdtemp(prefix="findociq_outroot_test_"))
    try:
        # mimic PASS2_v2.py's --out-root handling (both roots repointed)
        schema._P3_ROOT = tmp
        schema._OUTPUTS_ROOT = tmp
        fs_root = schema._family_root("fs")
        p3_root = schema._family_root("pillar3")
        check("fs root is under the override", tmp in fs_root.parents, str(fs_root))
        check("fs root does NOT fall under the repo", _REPO not in fs_root.parents, str(fs_root))
        check("pillar3 root is the override (legacy byte-identical target)",
              p3_root == tmp, str(p3_root))
        rp = schema.RunPaths("DBS", "2026-03-31", "DBS_1Q26_trading_update", family="fs")
        check("RunPaths(fs).xlsx lands under the override, not the repo",
              tmp in rp.xlsx.parents and _REPO not in rp.xlsx.parents, str(rp.xlsx))
    finally:
        schema._OUTPUTS_ROOT, schema._P3_ROOT = orig_out, orig_p3
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    test_pillar3_regression()
    test_fs_family()
    test_unknown_family_fallback()
    test_sheet_name_truncation()
    test_out_root_redirects_all_families()
    print(f"\n{'ALL PASS' if _FAILS == 0 else f'{_FAILS} FAILURE(S)'}")
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
