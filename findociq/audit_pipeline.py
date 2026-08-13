
#!/usr/bin/env python3
"""
audit_pipeline.py — static audit of the FinDocIQ pipeline.

Produces repo_audit.json + repo_audit.md: a machine-readable map of what each
script does, who imports it, who writes which DB table, and where the same
function is implemented in more than one place. Read-only. No execution of
pipeline code — pure AST + text analysis, so it can't spend Gemini money or
touch the DB.

Run from repo root:  python audit_pipeline.py --root findociq --out repo_audit
Then hand repo_audit.md + repo_audit.json to Claude Code.
"""

import argparse, ast, json, os, re, sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# what we're looking for
# ---------------------------------------------------------------------------

# DB tables whose writers we want to trace (single-writer is the invariant)
DB_TABLES = [
    "document", "section", "table_t", "row_dim", "col_dim", "cell_fact",
    "canonical_leaf", "canonical_leaf_alias", "canonical_leaf_path_alias",
    "line_anchor", "concept_rollup", "ingest_status", "fact_metric",
    "concept_map", "table_registry",
]
WRITE_SQL = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"REPLACE\s+INTO)\s+[`\"']?(\w+)", re.I)

# Column-level writes. File-count-per-table is too blunt: two passes that write
# DISJOINT columns of one table are the design working (load_v7 lays down the row,
# a stamping pass adds canonical_leaf_id) -- what's a real bug is two passes that
# both write the SAME column, where the later one silently wins. These pull the
# actual column set out of each statement so the report can say WHICH columns
# collide instead of just how many files touched the table.
UPDATE_SET = re.compile(
    r"\bUPDATE\s+[`\"']?(\w+)[`\"']?\s+SET\s+(.*?)(?:\s+WHERE\b|$)", re.I | re.S)
INSERT_COLS = re.compile(
    r"\b(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+[`\"']?(\w+)[`\"']?\s*\(([^)]*)\)",
    re.I | re.S)
DELETE_TBL = re.compile(r"\bDELETE\s+FROM\s+[`\"']?(\w+)", re.I)
CREATE_TBL = re.compile(
    r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+[`\"']?(\w+)", re.I)
# A SET clause splits on commas, but not commas inside a call: COALESCE(a, b).
SET_ASSIGN = re.compile(r"(\w+)\s*=", re.I)


def _sql_literals(tree):
    """Every string constant in the module, with f-string placeholders blanked.

    Adjacent string literals are concatenated by the parser, so a multi-line
    "UPDATE row_dim SET ..." "WHERE ..." arrives here as ONE constant -- which is
    why this reads the AST rather than scanning raw source line by line.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                else:
                    parts.append(" ? ")  # interpolated table/column name
            out.append("".join(parts))
    return out


def _split_set_cols(clause):
    """Column names assigned in a SET clause, ignoring '=' inside parentheses."""
    cols, depth, buf = [], 0, []
    for ch in clause:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            cols.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    cols.append("".join(buf))
    out = []
    for c in cols:
        m = SET_ASSIGN.match(c.strip())
        if m:
            out.append(m.group(1).lower())
    return out


def column_writes(tree):
    """[{table, op, cols}] for every write statement in the module.

    cols is [] for DELETE (whole row) and for CREATE TABLE (schema definition,
    not a data write -- kept separate so a schema builder isn't miscounted as a
    competing writer of every column it declares).
    """
    writes = []
    for sql in _sql_literals(tree):
        for m in UPDATE_SET.finditer(sql):
            writes.append({"table": m.group(1).lower(), "op": "UPDATE",
                           "cols": _split_set_cols(m.group(2))})
        for m in INSERT_COLS.finditer(sql):
            cols = [c.strip().strip('`"\'').lower()
                    for c in m.group(2).split(",") if c.strip()]
            writes.append({"table": m.group(1).lower(), "op": "INSERT",
                           "cols": [c for c in cols if re.fullmatch(r"\w+", c)]})
        for m in DELETE_TBL.finditer(sql):
            writes.append({"table": m.group(1).lower(), "op": "DELETE", "cols": []})
        for m in CREATE_TBL.finditer(sql):
            writes.append({"table": m.group(1).lower(), "op": "CREATE", "cols": []})
    return [w for w in writes if w["table"] in DB_TABLES]

# functions whose duplication matters most (the drift-prone ones)
CRITICAL_FUNCS = [
    "normalize_segment", "normalize_path", "build_canonical_leaf_id",
    "resolve_table", "resolve", "stamp", "_stamp_identity",
    "split_caption_tables", "row_parents_by_position", "resolve_printed_parents",
    "has_values", "is_period_label", "locate_tables",
]

# ---------------------------------------------------------------------------

def py_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in {".venv", ".venv-paddle", "__pycache__",
                                ".git", "node_modules", "data", "outputs", "db"}]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def analyze_file(path, root):
    rel = os.path.relpath(path, root)
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return {"path": rel, "error": str(e)}
    info = {
        "path": rel, "loc": src.count("\n") + 1,
        "defs": [], "classes": [], "imports": [], "imports_from": [],
        "db_writes": [], "column_writes": [], "sql_normalize_hits": [],
        "calls_subprocess": False, "reads_files": [], "writes_files": [],
        # A `__main__` guard makes a file an entrypoint, so "nothing imports it"
        # is expected, not dead code. Detected from source: the guard is an
        # `if` statement, so it never appears among the function defs.
        "is_entrypoint": bool(re.search(
            r"^\s*if\s+__name__\s*==\s*[\"']__main__[\"']", src, re.M)),
    }
    # SQL writes (text scan — catches f-strings and concatenations AST misses)
    for m in WRITE_SQL.finditer(src):
        tbl = m.group(2)
        if tbl in DB_TABLES:
            info["db_writes"].append({"op": m.group(1).split()[0].upper(), "table": tbl})
    # normalization-looking regexes (footnote/unit strip) — to spot copies
    for pat in ["casefold", "strip_footnote", "FOOT", "UNIT_SUFFIX",
                "_SUP", "normalize", "'::'", '"::"']:
        if pat in src:
            info["sql_normalize_hits"].append(pat)
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        info["error"] = f"syntax: {e}"
        return info
    info["column_writes"] = column_writes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            info["defs"].append({"name": node.name, "line": node.lineno,
                                 "args": [a.arg for a in node.args.args]})
        elif isinstance(node, ast.ClassDef):
            info["classes"].append(node.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                info["imports"].append(n.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for n in node.names:
                info["imports_from"].append(f"{mod}.{n.name}")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "subprocess":
                info["calls_subprocess"] = True
    return info


def build_report(root):
    files = [analyze_file(p, root) for p in py_files(root)]
    files = [f for f in files if "error" not in f or f.get("defs")]

    # ---- 1. function -> files that DEFINE it (duplication detector) ----
    func_defs = defaultdict(list)
    for f in files:
        for d in f.get("defs", []):
            func_defs[d["name"]].append({"file": f["path"], "line": d["line"],
                                         "args": d["args"]})
    duplicated = {name: locs for name, locs in func_defs.items()
                  if len(locs) > 1}
    critical_dupes = {n: l for n, l in duplicated.items()
                      if n in CRITICAL_FUNCS}

    # ---- 2. module -> who imports it (dead-code / dependency map) ----
    module_stem = {}
    for f in files:
        stem = os.path.splitext(os.path.basename(f["path"]))[0]
        module_stem.setdefault(stem, []).append(f["path"])
    imported_by = defaultdict(set)
    for f in files:
        for imp in f.get("imports", []) + f.get("imports_from", []):
            for part in imp.split("."):
                if part in module_stem:
                    imported_by[part].add(f["path"])
    never_imported = sorted(
        f["path"] for f in files
        if os.path.splitext(os.path.basename(f["path"]))[0] not in imported_by
        and "test" not in f["path"].lower()
        and not f.get("is_entrypoint"))

    # ---- 3. DB table -> writers (single-writer invariant check) ----
    writers = defaultdict(list)
    for f in files:
        for w in f.get("db_writes", []):
            writers[w["table"]].append({"file": f["path"], "op": w["op"]})
    multi_writer = {t: ws for t, ws in writers.items()
                    if len({w["file"] for w in ws}) > 1}

    # ---- 3b. table.column -> writers (the check that has teeth) ----
    # Disjoint column sets across passes are the design; a SHARED column is where
    # one pass can silently clobber another's decision. Archive/experiments and
    # tests are excluded -- fixtures legitimately write every column, and counting
    # them buries the handful of real collisions in live code.
    def _live(p):
        return not (p.startswith("archive/") or p.startswith("experiments/")
                    or os.path.basename(p).startswith("test_")
                    or p.endswith("_test.py"))

    col_writers = defaultdict(lambda: defaultdict(set))   # table -> col -> files
    table_ops = defaultdict(lambda: defaultdict(set))     # table -> file -> ops
    for f in files:
        if not _live(f["path"]):
            continue
        for w in f.get("column_writes", []):
            table_ops[w["table"]][f["path"]].add(w["op"])
            for c in w["cols"]:
                col_writers[w["table"]][c].add(f["path"])

    column_collisions = {}
    for tbl, cols in col_writers.items():
        shared = {c: sorted(fs) for c, fs in cols.items() if len(fs) > 1}
        if shared:
            column_collisions[tbl] = shared

    # Per-table breakdown: who writes which columns, so a disjoint split is
    # visible as disjoint rather than reported as a violation.
    column_map = {}
    for tbl, cols in col_writers.items():
        by_file = defaultdict(set)
        for c, fs in cols.items():
            for fp in fs:
                by_file[fp].add(c)
        column_map[tbl] = {
            fp: {"cols": sorted(cs),
                 "ops": sorted(table_ops[tbl].get(fp, set()))}
            for fp, cs in sorted(by_file.items())}
        # DELETE/CREATE-only writers have no columns but still touch the table.
        for fp, ops in table_ops[tbl].items():
            if fp not in column_map[tbl]:
                column_map[tbl][fp] = {"cols": [], "ops": sorted(ops)}

    # ---- 4. files that touch normalization (shared-normalizer check) ----
    normalize_touchers = sorted(
        f["path"] for f in files
        if any(h in ("casefold", "normalize", "FOOT", "_SUP", "'::'", '"::"')
               for h in f.get("sql_normalize_hits", [])))

    return {
        "n_files": len(files),
        "files": sorted(files, key=lambda x: x["path"]),
        "duplicated_functions": duplicated,
        "critical_duplicated_functions": critical_dupes,
        "never_imported_non_entrypoint": never_imported,
        "db_table_writers": {t: ws for t, ws in writers.items()},
        "multi_writer_tables": multi_writer,
        "column_collisions": column_collisions,
        "column_map": column_map,
        "normalize_touchers": normalize_touchers,
    }


def write_md(report, out_md):
    L = []
    L.append("# Pipeline audit\n")
    L.append(f"{report['n_files']} Python files analyzed.\n")

    L.append("## Critical duplicated functions (drift risk)\n")
    if report["critical_duplicated_functions"]:
        L.append("These functions are defined in MORE THAN ONE file. For "
                 "normalization/resolution/stamping, duplication means the copies "
                 "can drift and break identity matching. Consolidate to one owner.\n")
        for name, locs in report["critical_duplicated_functions"].items():
            L.append(f"- **`{name}`** defined in {len(locs)} files:")
            for loc in locs:
                L.append(f"    - `{loc['file']}:{loc['line']}` ({', '.join(loc['args'])})")
    else:
        L.append("None — no critical function is defined twice.\n")

    L.append("\n## All duplicated functions\n")
    for name, locs in sorted(report["duplicated_functions"].items()):
        if name in report["critical_duplicated_functions"]:
            continue
        files = ", ".join(f"`{l['file']}:{l['line']}`" for l in locs)
        L.append(f"- `{name}` — {files}")

    L.append("\n## Shared-column writes (live code — the real clobber risk)\n")
    L.append("Two passes writing DISJOINT columns of one table is the design "
             "(load_v7 lays the row down, a stamping pass adds its own columns). "
             "A SHARED column is where one pass can silently overwrite another's "
             "decision. Archive, experiments and tests excluded.\n")
    if report["column_collisions"]:
        for tbl, cols in sorted(report["column_collisions"].items()):
            L.append(f"\n### `{tbl}`\n")
            for c, fs in sorted(cols.items()):
                L.append(f"- **`{c}`** ← {', '.join('`'+f+'`' for f in fs)}")
    else:
        L.append("None — every column in a traced table has one live writer.\n")

    L.append("\n### Column ownership map (live code)\n")
    for tbl, by_file in sorted(report["column_map"].items()):
        L.append(f"\n**`{tbl}`**\n")
        for fp, d in by_file.items():
            ops = "/".join(d["ops"])
            cols = ", ".join(f"`{c}`" for c in d["cols"]) if d["cols"] else "_(whole row)_"
            L.append(f"- `{fp}` [{ops}] → {cols}")

    L.append("\n## Multi-writer DB tables (file-level — coarse, see above)\n")
    if report["multi_writer_tables"]:
        L.append("Each table should have ONE writer. These have several — a "
                 "consistency risk (the D4 stamp_tables/load_v7 split was this class).\n")
        for t, ws in report["multi_writer_tables"].items():
            files = sorted({w["file"] for w in ws})
            L.append(f"- **`{t}`** written by: {', '.join('`'+f+'`' for f in files)}")
    else:
        L.append("None — every traced table has a single writer.\n")

    L.append("\n## Files touching normalization\n")
    L.append("The masterlist seeder and the resolver MUST share ONE normalize "
             "implementation. If more than one file here defines its own "
             "normalize/footnote-strip, that's the highest-stakes duplication.\n")
    for f in report["normalize_touchers"]:
        L.append(f"- `{f}`")

    L.append("\n## Never-imported non-entrypoint files (possible dead code)\n")
    L.append("Not imported by anything and no `__main__` — candidates for "
             "deletion or archival. Verify each before removing.\n")
    for f in report["never_imported_non_entrypoint"]:
        L.append(f"- `{f}`")

    L.append("\n## Full DB writer map\n")
    for t, ws in sorted(report["db_table_writers"].items()):
        files = sorted({w['file'] for w in ws})
        L.append(f"- `{t}`: {', '.join('`'+f+'`' for f in files)}")

    open(out_md, "w").write("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="findociq")
    ap.add_argument("--out", default="repo_audit")
    args = ap.parse_args()
    if not os.path.isdir(args.root):
        print(f"root not found: {args.root}", file=sys.stderr); sys.exit(1)
    rep = build_report(args.root)
    json.dump(rep, open(args.out + ".json", "w"), indent=2)
    write_md(rep, args.out + ".md")
    print(f"wrote {args.out}.json and {args.out}.md")
    print(f"  {rep['n_files']} files")
    print(f"  {len(rep['critical_duplicated_functions'])} critical duplicated functions")
    ncol = sum(len(c) for c in rep["column_collisions"].values())
    print(f"  {ncol} shared columns across {len(rep['column_collisions'])} tables "
          f"(live code)")
    print(f"  {len(rep['multi_writer_tables'])} multi-writer tables (file-level)")
    print(f"  {len(rep['never_imported_non_entrypoint'])} possibly-dead files")
    print(f"  {len(rep['normalize_touchers'])} files touch normalization")
