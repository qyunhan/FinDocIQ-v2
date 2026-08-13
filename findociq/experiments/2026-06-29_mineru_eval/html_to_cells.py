"""html_to_cells — parse a Plan-9 Stage-2 HTML table into schema_v5 cells.

Deterministic loader shared by every Stage-2 backend (Gemini or MinerU): given the
pinned HTML contract (Appendix A), produce table_t / col_dim / row_dim / cell_fact
rows. Re-seeds the lost HTML->DB parser.

Defensive rules proven necessary by a real Gemini sample (see the spec, §3):
  1. tolerate `rowspan` in the header grid (the contract says none, but models emit it).
  2. a leading all-numeric column with no header is the printed line number -> row_dim.line_no,
     NOT a data column; strip it before validating column counts.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
import lxml.html


# ----------------------------------------------------------------------------- models
@dataclass
class Col:
    col_id: int            # grid column index (value columns only; label col excluded)
    leaf_label: str
    group: str | None      # span-header parent, or None


@dataclass
class Cell:
    col_id: int
    colspan: int
    value_raw: str
    value_num: float | None
    cell_state: str        # reported | zero | null | empty | suppressed
    is_shade: int


@dataclass
class Row:
    row_idx: int
    level: int
    kind: str | None       # 'total' etc.
    line_no: str | None
    label: str
    parent_idx: int | None
    cells: list[Cell]


@dataclass
class Table:
    period: str | None     # ISO date
    context_rows: list[str]
    cols: list[Col]
    rows: list[Row]
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- helpers
_WS = re.compile(r"\s+")
def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "")).strip()

_DASH = {"-", "–", "—"}
_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}

def _month_from_token(tok: str) -> int | None:
    """A token of >=3 chars that prefix-matches exactly ONE month name resolves
    to that month ('dec', 'sept', 'december'). No enumerated abbreviation list —
    the prefix rule covers any bank's shortening deterministically."""
    t = tok.lower()
    if len(t) < 3:
        return None
    hits = [i for m, i in _MONTHS.items() if m.startswith(t)]
    return hits[0] if len(hits) == 1 else None

def _parse_period(context_rows: list[str]) -> str | None:
    """'31 December 2025' / '31 Dec 2025' -> '2025-12-31'."""
    for t in context_rows:
        for m in re.finditer(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", t):
            mon = _month_from_token(m.group(2))
            if mon:
                return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"
    return None

def _state_num(raw: str) -> tuple[str, float | None]:
    s = raw.strip()
    if s == "":
        return "empty", None
    if s in _DASH:
        return "null", None
    if s == "#":
        return "suppressed", None
    if s == "0":
        return "zero", 0.0
    neg = s.startswith("(") and s.endswith(")")
    t = s.strip("()").replace(",", "").replace("%", "").replace("S$", "").strip()
    try:
        v = float(t)
        return "reported", (-v if neg else v)
    except ValueError:
        return "reported", None          # textual reported (e.g. a label-as-value)

def _is_grey(style: str | None) -> int:
    if not style:
        return 0
    m = re.search(r"background-color:\s*#?([0-9a-fA-F]{6})", style)
    if not m:
        return 0
    r, g, b = (int(m.group(1)[i:i+2], 16) for i in (0, 2, 4))
    # grey = channels close together AND mid/light (excludes coloured title bars)
    return int(max(r, g, b) - min(r, g, b) <= 24 and 150 <= (r + g + b) / 3 <= 240)


# ----------------------------------------------------------------------------- header grid
def _build_grid(header_trs) -> tuple[int, list[tuple[str, str | None]]]:
    """Return (ncols, [(leaf_label, group_label) per grid col]) honouring colspan+rowspan."""
    matrix: dict[tuple[int, int], str] = {}
    occ: set[tuple[int, int]] = set()
    nrows, maxc = len(header_trs), 0
    for r, tr in enumerate(header_trs):
        c = 0
        for cell in tr.xpath("./th|./td"):
            while (r, c) in occ:
                c += 1
            cs = int(cell.get("colspan", 1) or 1)
            rs = int(cell.get("rowspan", 1) or 1)
            txt = _norm(cell.text_content())
            for dr in range(rs):
                for dc in range(cs):
                    matrix[(r + dr, c + dc)] = txt
                    occ.add((r + dr, c + dc))
            c += cs
            maxc = max(maxc, c)
    cols = []
    for c in range(maxc):
        leaf = ""
        for r in range(nrows - 1, -1, -1):       # bottom-most non-empty = leaf
            if matrix.get((r, c)):
                leaf = matrix[(r, c)]; break
        group = None
        for r in range(nrows):                   # top-most that differs = group
            t = matrix.get((r, c))
            if t and t != leaf:
                group = t; break
        cols.append((leaf, group))
    return maxc, cols


# ----------------------------------------------------------------------------- main parse
def parse_table(tbl) -> Table:
    warnings: list[str] = []
    thead = tbl.find("thead")
    head_trs = thead.findall("tr") if thead is not None else []
    # context rows = leading single-cell rows; the rest are the column headers
    context, header_trs = [], []
    for tr in head_trs:
        cells = tr.xpath("./th|./td")
        if len(cells) == 1 and not header_trs:
            context.append(_norm(cells[0].text_content()))
        else:
            header_trs.append(tr)
    ncols, gridcols = _build_grid(header_trs) if header_trs else (0, [])

    body = tbl.find("tbody")
    body_trs = body.findall("tr") if body is not None else []

    # --- defensive rule 2: detect a leading line-number column ---
    def row_width(tr):
        return sum(int(td.get("colspan", 1) or 1) for td in tr.xpath("./td"))
    widths = [row_width(tr) for tr in body_trs]
    plus1 = sum(1 for w in widths if w == ncols + 1)
    exact = sum(1 for w in widths if w == ncols)
    has_lineno = ncols > 0 and plus1 > exact

    # value columns = grid cols 1..ncols-1 (grid col 0 is the row-label column)
    cols = [Col(col_id=c, leaf_label=gridcols[c][0], group=gridcols[c][1])
            for c in range(1, ncols)]

    rows: list[Row] = []
    for ri, tr in enumerate(body_trs):
        tds = tr.xpath("./td")
        # section band emitted as a full-width <th> row (3.5 style) — label only, no values
        if not tds:
            ths = tr.xpath("./th")
            if ths:
                blvl = tr.get("data-level") or ths[0].get("data-level") or "1"
                rows.append(Row(row_idx=ri, level=int(blvl or 1), kind=tr.get("data-kind"),
                                line_no=None, label=_norm(ths[0].text_content()),
                                parent_idx=None, cells=[]))
            continue
        # level/kind: from <tr> (2.5 style) or fall back to a <td> carrying it (3.5 style)
        lvl, kind = tr.get("data-level"), tr.get("data-kind")
        if lvl is None:
            for td in tds:
                if td.get("data-level") is not None:
                    lvl = td.get("data-level"); kind = kind or td.get("data-kind"); break
        level = int(lvl or 0)

        line_no = None
        idx = 0
        if has_lineno:                                  # rule 2a: separate line-no column
            line_no = _norm(tds[0].text_content()) or None
            idx = 1
        label = _norm(tds[idx].text_content()) if idx < len(tds) else ""
        idx += 1
        if line_no is None:                             # rule 2b: line-no prefixed in label
            m = re.match(r"(\d+[a-z]?)\s+(.+)", label)
            if m:
                line_no, label = m.group(1), m.group(2).strip()
        # remaining tds map to value grid cols 1..ncols-1
        cells: list[Cell] = []
        gcol = 1
        for td in tds[idx:]:
            cs = int(td.get("colspan", 1) or 1)
            raw = _norm(td.text_content())
            state, num = _state_num(raw)
            cells.append(Cell(col_id=gcol, colspan=cs, value_raw=raw,
                              value_num=num, cell_state=state,
                              is_shade=_is_grey(td.get("style"))))
            gcol += cs
        # validate width — but a label-only row is a section band, not a violation
        if cells:
            data_w = sum(c.colspan for c in cells) + 1  # +1 for label col
            if ncols and data_w != ncols:
                warnings.append(f"row {ri} (line {line_no}): width {data_w} != ncols {ncols}")
        rows.append(Row(row_idx=ri, level=level, kind=kind, line_no=line_no,
                        label=label, parent_idx=None, cells=cells))

    # --- row_parent = nearest earlier row at level-1 (Plan 9 enricher) ---
    for i, row in enumerate(rows):
        for j in range(i - 1, -1, -1):
            if rows[j].level == row.level - 1:
                row.parent_idx = j; break

    return Table(period=_parse_period(context), context_rows=context,
                 cols=cols, rows=rows, warnings=warnings)


def parse_html(html: str) -> list[Table]:
    """Parse a Stage-2 HTML string (one or more <table>) into Tables."""
    root = lxml.html.fromstring(f"<root>{html}</root>")
    return [parse_table(t) for t in root.xpath(".//table")]
