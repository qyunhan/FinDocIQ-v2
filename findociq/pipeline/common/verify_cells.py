"""Zero-token verification: does every extracted cell_fact value_num appear,
verbatim, in the source PDF's printed text?

No LLM calls. Pure pdfplumber text extraction + deterministic string/number
matching against findb (final.db).

Usage:
    python3 verify_cells.py --manifest findociq/pipeline/route/out/nsfr_manifest.json \
        --db findociq/db/final.db [--doc dbs_4q23_p3] [--out <json path>]

Page-index convention (mirrors findociq/_legacy/DELIVERABLE/pillar3/pass2/render.py
cut_pdf): page_range values in table_t are 1-based printed/PDF page numbers;
pdfplumber's .pages[] is 0-based, so page N -> pdf.pages[N - 1].

Tiering (per row, cheapest/most-precise first):
  line  - the row's label anchors to exactly one physical PDF text line, and
          that line (plus the next line, to tolerate value-wrap) contains
          every one of the row's reported values.
  page  - anchoring failed (zero or >1 candidate lines) or the anchored
          line didn't carry all values; fall back to: are all the row's
          values present anywhere on the table's page(s)?
  fail  - neither tier accounts for all values -> mismatch, reported.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

_FOOTNOTE_GLYPHS = ("#", "*", "†", "‡")  # #, *, dagger, double-dagger

# Currency prefixes stripped before numeric parsing (D27 fix). Longest-first
# within a shared symbol so "US$" is tried before "$" ever gets a chance to
# leave a stray "US" behind. Case-insensitive; optional whitespace after the
# prefix ("S$ 2.1bn").
_CURRENCY_PREFIXES = ("US$", "S$", "HK$", "SGD", "USD", "RM", "$")


def norm_token(tok):
    """Normalize a single printed-page token to a float, or None if it isn't
    a reportable number. Handles thousands commas, footnote glyphs, trailing
    percent signs, parenthesized negatives (accounting convention), and a
    leading currency prefix (S$, US$, $, SGD, USD, RM, HK$ -- case-insensitive,
    optional whitespace after)."""
    if tok is None:
        return None
    s = tok.strip()
    if not s:
        return None
    if s in ("-", "–", "—"):  # bare dash / en-dash / em-dash = null cell
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.strip()
    upper = s.upper()
    for p in _CURRENCY_PREFIXES:
        if upper.startswith(p):
            s = s[len(p):].lstrip()
            break
    for g in _FOOTNOTE_GLYPHS:
        s = s.replace(g, "")
    s = s.replace(",", "")
    if s.endswith("%"):
        s = s[:-1]
    s = s.strip()
    if not s or s in ("-", "–", "—"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_page_range(page_range: str) -> list[int]:
    """"75-76" -> [75, 76]; "95" -> [95]. 1-based, inclusive."""
    s = page_range.strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(s)]


def _cluster_by_top(ordered, tol: float):
    """Shared running-mean clustering: given items pre-sorted so that items
    destined for the same physical line arrive in a stable relative order,
    group them by `top` within `tol`, keeping each cluster's anchor as the
    running mean of its members' `top` (so drift doesn't accumulate across a
    long line). Returns clusters as {"top": float, "n": int, "items": [...]},
    sorted by final top. Each item must be a dict with a "top" key."""
    clusters = []
    for it in ordered:
        placed = False
        for c in clusters:
            if abs(it["top"] - c["top"]) <= tol:
                c["items"].append(it)
                c["top"] = (c["top"] * c["n"] + it["top"]) / (c["n"] + 1)
                c["n"] += 1
                placed = True
                break
        if not placed:
            clusters.append({"top": it["top"], "n": 1, "items": [it]})
    clusters.sort(key=lambda c: c["top"])
    return clusters


def build_lines(words, tol: float = 3.0):
    """Group pdfplumber words into physical lines by y-position (`top`),
    clustering within `tol` points, then order each line's tokens by x0.
    `words` is any iterable of dicts with at least text/top/x0 keys (matches
    pdfplumber's extract_words() output, and plain dicts in tests)."""
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    clusters = _cluster_by_top(ordered, tol)
    lines = []
    for c in clusters:
        ws = sorted(c["items"], key=lambda w: w["x0"])
        tokens = [w["text"] for w in ws]
        lines.append({"top": c["top"], "tokens": tokens, "text": " ".join(tokens)})
    return lines


def words_from_chars(page, line_tol: float = 3.0):
    """Reconstruct word-dicts {"text","x0","x1","top"} directly from
    pdfplumber's page.chars, instead of page.extract_words(). This avoids two
    failure modes of extract_words() seen in the wild: letter-spaced text
    layers get shredded into one "word" per glyph, and tight kerning between
    a leading digit and the rest of a number ("182,768") can get split into
    separate words. We rebuild tokens ourselves using a per-page adaptive
    x-gap threshold derived from the median character width."""
    chars = [c for c in page.chars if c["text"].strip()]
    if not chars:
        return []

    thr = 0.5 * statistics.median(c["x1"] - c["x0"] for c in chars)

    ordered = sorted(chars, key=lambda c: (c["top"], c["x0"]))
    clusters = _cluster_by_top(ordered, line_tol)

    words = []
    for c in clusters:
        line_chars = sorted(c["items"], key=lambda ch: ch["x0"])
        line_top = c["top"]
        token_text = ""
        token_x0 = None
        token_x1 = None
        prev_x1 = None
        for ch in line_chars:
            if prev_x1 is not None and (ch["x0"] - prev_x1) > thr:
                words.append({"text": token_text, "x0": token_x0, "x1": token_x1, "top": line_top})
                token_text = ""
                token_x0 = None
            if token_x0 is None:
                token_x0 = ch["x0"]
            token_text += ch["text"]
            token_x1 = ch["x1"]
            prev_x1 = ch["x1"]
        if token_text:
            words.append({"text": token_text, "x0": token_x0, "x1": token_x1, "top": line_top})
    return words


def values_on(lines) -> list[float]:
    """Multiset (list, duplicates preserved) of every numeric token across
    the given lines."""
    out = []
    for ln in lines:
        for tok in ln["tokens"]:
            v = norm_token(tok)
            if v is not None:
                out.append(v)
    return out


def normalize_label(s: str) -> str:
    """Lowercase, alnum + single spaces only -- used for row-label /
    page-line fuzzy anchoring."""
    if not s:
        return ""
    out = []
    prev_space = False
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        elif not prev_space:
            out.append(" ")
            prev_space = True
    return "".join(out).strip()


def _norm_line_token(tok: str) -> str:
    """Strip trailing punctuation from a page token for line_no comparison,
    e.g. "14." -> "14"."""
    return "".join(ch for ch in tok if ch.isalnum()).lower()


def anchor_lines_for_row(row_label: str, line_no, lines) -> list[int]:
    """Return the indices of every line in `lines` that plausibly anchors
    this row: either (a) the normalized label shares >=60% of its first 6
    words with the line's normalized text, or (b) the line's first token
    equals the row's line_no and the line's second token matches the
    label's first word. Callers must treat len() != 1 as "don't guess"."""
    label_norm = normalize_label(row_label)
    label_words = label_norm.split(" ") if label_norm else []
    label_words = [w for w in label_words if w]
    first6 = label_words[:6]
    first_word = label_words[0] if label_words else None
    line_no_norm = _norm_line_token(str(line_no)) if line_no is not None else None

    hits = []
    for i, ln in enumerate(lines):
        line_words_norm = [normalize_label(t) for t in ln["tokens"]]
        line_word_set = set(w for w in line_words_norm if w)

        matched = sum(1 for w in first6 if w in line_word_set)
        overlap_ok = bool(first6) and (matched / len(first6)) >= 0.6

        line_no_ok = False
        if line_no_norm and first_word and ln["tokens"]:
            first_tok_norm = _norm_line_token(ln["tokens"][0])
            if first_tok_norm == line_no_norm:
                rest_words = [normalize_label(t) for t in ln["tokens"][1:]]
                if first_word in rest_words:
                    line_no_ok = True

        if overlap_ok or line_no_ok:
            hits.append(i)
    return hits


def missing_values(available: list[float], wanted: list[float], eps: float = 1e-6) -> list[float]:
    """Multiset containment: which of `wanted` have no matching (within eps)
    unused entry in `available`? Order of `wanted` preserved in output."""
    pool = list(available)
    missing = []
    for v in wanted:
        idx = None
        for i, a in enumerate(pool):
            if abs(a - v) < eps:
                idx = i
                break
        if idx is None:
            missing.append(v)
        else:
            pool.pop(idx)
    return missing


def multiset_contains_all(available: list[float], wanted: list[float]) -> bool:
    return len(missing_values(available, wanted)) == 0


# --------------------------------------------------------------------------
# DB + PDF driven verification
# --------------------------------------------------------------------------


def load_manifest(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _doc_pdf_path(manifest: dict, doc_id: str) -> str | None:
    for d in manifest.get("docs", []):
        if d["doc_id"] == doc_id:
            return d["pdf"]
    return None


def _fetch_tables(con: sqlite3.Connection, doc_id: str):
    cur = con.cursor()
    cur.execute(
        "SELECT table_id, period, page_range FROM table_t WHERE doc_id = ?",
        (doc_id,),
    )
    return cur.fetchall()


def _fetch_rows_with_values(con: sqlite3.Connection, doc_id: str, table_id: str):
    """Return list of (row_id, row_leaf_label, line_no, [value_num, ...],
    [value_raw, ...]) for rows that have at least one non-null value_num."""
    cur = con.cursor()
    cur.execute(
        "SELECT row_id, row_leaf_label, line_no FROM row_dim "
        "WHERE doc_id = ? AND table_id = ? ORDER BY row_id",
        (doc_id, table_id),
    )
    rows = cur.fetchall()
    cur.execute(
        "SELECT row_id, value_num, value_raw FROM cell_fact "
        "WHERE doc_id = ? AND table_id = ? AND value_num IS NOT NULL "
        "ORDER BY row_id, col_id",
        (doc_id, table_id),
    )
    by_row = defaultdict(list)
    for row_id, value_num, value_raw in cur.fetchall():
        by_row[row_id].append((value_num, value_raw))

    out = []
    for row_id, label, line_no in rows:
        vals = by_row.get(row_id, [])
        if not vals:
            continue
        out.append((row_id, label, line_no, [v[0] for v in vals], [v[1] for v in vals]))
    return out


def _extract_lines_for_pages(pdf, page_numbers_1based: list[int], tol: float = 3.0):
    lines = []
    for p in page_numbers_1based:
        page = pdf.pages[p - 1]
        words = words_from_chars(page)
        lines.extend(build_lines(words, tol=tol))
    return lines


def verify_table(pdf, doc_id: str, table_id: str, period: str, page_range: str, rows) -> dict:
    pages = parse_page_range(page_range)
    lines = _extract_lines_for_pages(pdf, pages)
    page_values = values_on(lines)

    result = {
        "table_id": table_id,
        "period": period,
        "pages": pages,
        "rows_total": len(rows),
        "rows_line_tier": 0,
        "rows_page_tier": 0,
        "rows_failed": 0,
        "values_checked": 0,
        "values_missing": [],
    }

    for row_id, label, line_no, value_nums, value_raws in rows:
        result["values_checked"] += len(value_nums)

        anchors = anchor_lines_for_row(label, line_no, lines)
        tier = None
        if len(anchors) == 1:
            idx = anchors[0]
            window_idx = [idx] + ([idx + 1] if idx + 1 < len(lines) else [])
            window_values = values_on([lines[i] for i in window_idx])
            if multiset_contains_all(window_values, value_nums):
                tier = "line"

        if tier is None:
            if multiset_contains_all(page_values, value_nums):
                tier = "page"

        if tier == "line":
            result["rows_line_tier"] += 1
        elif tier == "page":
            result["rows_page_tier"] += 1
        else:
            result["rows_failed"] += 1
            missing = missing_values(page_values, value_nums)
            for mv in missing:
                # find the value_raw that produced this missing number
                raw = None
                for vn, vr in zip(value_nums, value_raws):
                    if vn == mv:
                        raw = vr
                        break
                result["values_missing"].append(
                    {
                        "row_id": row_id,
                        "row_label": label,
                        "line_no": line_no,
                        "missing_value": mv,
                        "value_raw": raw,
                    }
                )

    return result


def verify_doc(manifest: dict, con: sqlite3.Connection, doc_id: str):
    import pdfplumber

    pdf_path = _doc_pdf_path(manifest, doc_id)
    if pdf_path is None:
        raise SystemExit(f"doc_id {doc_id!r} not found in manifest")
    if not os.path.exists(pdf_path):
        raise SystemExit(f"pdf not found: {pdf_path}")

    tables = _fetch_tables(con, doc_id)
    report = {"doc_id": doc_id, "pdf": pdf_path, "tables": []}

    with pdfplumber.open(pdf_path) as pdf:
        for table_id, period, page_range in tables:
            rows = _fetch_rows_with_values(con, doc_id, table_id)
            t_report = verify_table(pdf, doc_id, table_id, period, page_range, rows)
            report["tables"].append(t_report)

    return report


def print_summary(reports: list[dict]):
    header = f"{'doc_id':<16} {'table_id':<20} {'rows':>5} {'line':>5} {'page':>5} {'fail':>5} {'vals':>6} {'missing':>8}"
    print(header)
    print("-" * len(header))
    for r in reports:
        for t in r["tables"]:
            print(
                f"{r['doc_id']:<16} {t['table_id']:<20} "
                f"{t['rows_total']:>5} {t['rows_line_tier']:>5} {t['rows_page_tier']:>5} "
                f"{t['rows_failed']:>5} {t['values_checked']:>6} {len(t['values_missing']):>8}"
            )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--doc", default=None, help="restrict to one doc_id")
    ap.add_argument("--out", default=None, help="output JSON path (default: one file per doc under route/out/verify/)")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    con = sqlite3.connect(args.db)

    if args.doc:
        doc_ids = [args.doc]
    else:
        doc_ids = [d["doc_id"] for d in manifest.get("docs", [])]

    out_dir = os.path.join(HERE, "route", "out", "verify")
    reports = []
    any_missing = False

    for doc_id in doc_ids:
        report = verify_doc(manifest, con, doc_id)
        reports.append(report)
        for t in report["tables"]:
            if t["values_missing"]:
                any_missing = True

        if args.out and len(doc_ids) == 1:
            out_path = args.out
        else:
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{doc_id}_verify.json")
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)

    print_summary(reports)
    con.close()
    sys.exit(1 if any_missing else 0)


if __name__ == "__main__":
    main()
