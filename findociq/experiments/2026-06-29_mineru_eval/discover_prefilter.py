"""discover_prefilter — Stage-1 deterministic table pre-filter (zero-API).

For each page: does it contain a table? Two offline signals:
  1. RULED  — pdfplumber.find_tables() (ruled / lined tables).
  2. BORDERLESS — rows of numeric tokens sharing aligned right-edges (financial
     statements are mostly borderless). >= MIN_DATA_ROWS aligned numeric rows = a table.
Also guesses a title (largest-font line in the page's top third).

This is the always-available fallback + the cheap gate that decides which pages need
a heavier detector (MinerU layout / vision). Run:
    python3 discover_prefilter.py <pdf>
"""
from __future__ import annotations
import sys, re, statistics
import pdfplumber

NUM = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?%?$|^-$")
MIN_DATA_ROWS = 3          # aligned numeric rows to call a page tabular
ALIGN_TOL = 3.0            # px tolerance for shared column right-edges


def _rows(words, tol=2.5):
    """Cluster words into visual rows by their 'top'."""
    rows, cur, last = [], [], None
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if last is None or abs(w["top"] - last) <= tol:
            cur.append(w)
        else:
            rows.append(cur); cur = [w]
        last = w["top"]
    if cur:
        rows.append(cur)
    return rows


def borderless_score(words) -> int:
    """Count aligned numeric data-rows: rows with >=2 numeric tokens whose right-edges
    line up into shared columns across the page."""
    rows = _rows(words)
    numeric_rows = []
    for r in rows:
        nums = [w for w in r if NUM.match(w["text"])]
        if len(nums) >= 2:
            numeric_rows.append([round(w["x1"], 1) for w in nums])
    if len(numeric_rows) < MIN_DATA_ROWS:
        return 0
    # column alignment: how many right-edges recur across rows
    edges = [x for row in numeric_rows for x in row]
    cols, used = [], [False] * len(edges)
    for i, e in enumerate(edges):
        if used[i]:
            continue
        group = [j for j, f in enumerate(edges) if not used[j] and abs(f - e) <= ALIGN_TOL]
        for j in group:
            used[j] = True
        if len(group) >= MIN_DATA_ROWS:        # a column present in >= N rows
            cols.append(e)
    return len(numeric_rows) if len(cols) >= 2 else 0


def title_guess(page) -> str:
    """Largest-font text line in the top third of the page."""
    top_cut = page.bbox[1] + (page.height) * 0.33
    chars = [c for c in page.chars if c["top"] < top_cut and c["text"].strip()]
    if not chars:
        return ""
    big = max(chars, key=lambda c: c.get("size", 0))["size"]
    line = [c for c in chars if abs(c.get("size", 0) - big) < 0.4]
    line.sort(key=lambda c: (round(c["top"]), c["x0"]))
    return re.sub(r"\s+", " ", "".join(c["text"] for c in line)).strip()[:80]


def scan(pdf_path: str):
    rows_out = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            ruled = len(page.find_tables())
            words = page.extract_words(use_text_flow=False)
            bscore = borderless_score(words)
            has = ruled > 0 or bscore >= MIN_DATA_ROWS
            rows_out.append((i, ruled, bscore, has, title_guess(page)))
    return rows_out


if __name__ == "__main__":
    pdf = sys.argv[1]
    rows = scan(pdf)
    flagged = sum(1 for r in rows if r[3])
    print(f"{pdf.split('/')[-1]}  —  {len(rows)} pages, {flagged} flagged as tabular\n")
    print(f"{'pg':>3} {'ruled':>5} {'bscore':>6} {'table?':>7}  title-guess")
    for i, ruled, bscore, has, title in rows:
        print(f"{i:>3} {ruled:>5} {bscore:>6} {'YES' if has else '·':>7}  {title}")
