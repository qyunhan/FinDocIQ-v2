"""stage2_load.load_v7 — map a pass2 GTable `Extraction` (audit `parsed.json`) into
schema_v7 (table_t / col_dim / row_dim / cell_fact + the row_lineage/col_lineage
lineage registries).

Binding design: findociq/docs/specs/2026-07-13-gtable-schema-v7-loader-design.md
Authoritative DDL:  findociq/schema/schema_v7.sql

`document` and `section` rows are OWNED UPSTREAM (TOC stage). This loader asserts
they exist and NEVER authors or deletes them. The lineage registries are GLOBAL,
get-or-create, and are never doc-deleted (orphans are harmless).
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from stage3_stamp.resolve.normalize import normalize_row_label
from stage1_extract.chunk.schema import Extraction, GCell, GColumn, GRow, GTable
from stage1_extract.chunk.transforms import (apply_geometry, drop_echo_groups, header_row_indices,
                         merge_continuation_tables, split_caption_tables)

# ---------------------------------------------------------------------------
# Pure mappers (unit-tested in test_load_v7.py — no DB, no I/O)
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_DASH = {"-", "–", "—"}
_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september "
    "october november december".split(), 1)}
# trailing footnote markers: unicode superscripts (¹²³ … U+2070-2079, U+00B2/B3/B9)
# and parenthesised indices '(1)'. Stripped from lineage identity, not from the
# verbatim label stored on row_dim/col_dim.
_SUP = "¹²³⁰ⁱ⁴⁵⁶⁷⁸⁹"
_FOOTNOTE_TAIL = re.compile(rf"(?:[{_SUP}]+|\s*\((?:\d+|[a-z])\))+\s*$")

# COLUMN-context footnote noise: unlike lineage labels (where _FOOTNOTE_TAIL peels
# only TRAILING markers), a column header carries footnote markers INSIDE the text
# — a superscript glued to the period token ('2H25¹ $m') or a parenthesised index
# before a unit ('2H 2025 (1)'). Strip both forms ANYWHERE so the period grammar
# underneath is recognised. COLUMN context only; title/prose is untouched.
_COL_SUP_RX = re.compile(rf"[{_SUP}]+")
_COL_PAREN_FN_RX = re.compile(r"\s*\((?:\d+|[a-z])\)")


def _strip_col_footnotes(t: str) -> str:
    """Remove footnote markers (superscripts / '(1)' indices) ANYWHERE in a
    COLUMN header, whitespace re-collapsed. General, no per-bank rule."""
    t = _COL_SUP_RX.sub("", t)
    t = _COL_PAREN_FN_RX.sub("", t)
    return _WS.sub(" ", t).strip()


def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "")).strip()


_TITLE_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*[\.\s]\s*")


def slug(title: str) -> str:
    """Fallback table_type from a title: printed number prefix stripped
    (provenance, not identity — same rule as section ids), lowercase,
    non-alphanumeric -> '_', collapsed. Deterministic, no per-bank branch."""
    s = _TITLE_NUM_PREFIX.sub("", _norm(title).strip())
    s = re.sub(r"[^0-9a-z]+", "_", s.lower()).strip("_")
    return s or "table"


def _month_from_token(tok: str) -> int | None:
    """>=3-char token that prefix-matches exactly ONE month name -> its number
    ('dec', 'sept', 'december'). Mirrors html_to_cells._month_from_token."""
    t = tok.lower()
    if len(t) < 3:
        return None
    hits = [i for m, i in _MONTHS.items() if m.startswith(t)]
    return hits[0] if len(hits) == 1 else None


def parse_iso_date(text: str) -> str | None:
    """'30 Jun 2025' / '31 December 2024' -> ISO '2025-06-30'. Shared parser,
    same rule as extract_run/html_to_cells so every stored date is ISO."""
    for m in re.finditer(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text or ""):
        mon = _month_from_token(m.group(2))
        if mon:
            return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"
    return None


_DATE_PREFIXES = {"", "as", "at", "of", "as at", "as of", "for the period ended",
                  "period ended", "as at the"}


def is_date_text(text: str) -> bool:
    """True when a column header IS a date/period (period axis) rather than a
    descriptive header. The date must cover the header once boilerplate leading
    words ('as at', 'as of') are removed — 'Net loans' is never a date."""
    t = _norm(text)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", t)
    if not m or _month_from_token(m.group(2)) is None:
        return False
    residual = (t[:m.start()] + t[m.end():]).strip().lower()
    return residual in _DATE_PREFIXES


# ---------------------------------------------------------------------------
# General PERIOD-EXPRESSION grammar (halves / quarters / full-year / FY),
# a superset of the DD-Month-YYYY date parser above. Deterministic, ORDERED,
# first-match-wins; NO per-bank rules.
#
# CALENDAR-FISCAL ASSUMPTION: every bank in this corpus (DBS / OCBC / UOB — all
# Singapore) closes its fiscal year on 31 December, so fiscal == calendar.
# Under that assumption the period END dates are fixed:
#   H1 -> 30 Jun,  H2 / Full-Year / FY -> 31 Dec,
#   quarter Q -> last day of month 3*Q  (Q1 31 Mar, Q2 30 Jun, Q3 30 Sep, Q4 31 Dec).
# A non-calendar-fiscal issuer would need its fiscal-year-end wired in here; no
# per-bank branch is embedded (this is a single shared calendar rule, not a hack).
# ---------------------------------------------------------------------------
_ISO_DATE_RX = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
_ORD = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4,
        "first": 1, "second": 2, "third": 3, "fourth": 4}
_QEND = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}  # quarter -> (end month, end day)

_HALF_ORD_RX = re.compile(
    r"\b(1st|2nd|first|second)\s*(?:half|h)\b\.?\s*'?(\d{4}|\d{2})\b", re.I)
_HALF_CMP_RX = re.compile(r"\b([12])\s*h\s*'?(\d{4}|\d{2})\b", re.I)      # 1H25 / 2H 2024
_QTR_ORD_RX = re.compile(
    r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\s*(?:qtr|quarter|q)\b\.?\s*'?(\d{4}|\d{2})\b",
    re.I)
_QTR_CMP_RX = re.compile(r"\b([1-4])\s*q\s*'?(\d{4}|\d{2})\b", re.I)      # 2Q25 / 3Q 2024
_FY_RX = re.compile(r"\bfy\s*'?(\d{4}|\d{2})\b", re.I)                     # FY2024 / FY24
_FULLYEAR_RX = re.compile(r"\b(?:full\s+)?year\s+'?(\d{4})\b", re.I)
_NINEM_ORD_RX = re.compile(r"\bnine\s+months?\s+(\d{4})\b", re.I)          # Nine Months 2025
# 9M25 / 9M 2025 / YTD25 / '9 Mths 2025' / '9 Month 2025'. The abbreviated word
# forms matter: DBS prints '9 Mths 2025' in its trading-update ratio table, which
# the bare `9\s*m` form could not consume — it left 'Mths' unmatched, so the
# COLUMN-context bare-year fallback below claimed just the '2025' and returned
# FY/31-Dec for what is a NINE-MONTH cumulative column. GATE A2 caught the
# residual and refused the table (correctly), but the underlying defect was this
# grammar, not the guard. Longest alternative first so 'months' wins over 'm'.
_NINEM_CMP_RX = re.compile(
    r"\b(?:9\s*(?:months|month|mths|mth|mos|mo|m)|ytd)\s*'?(\d{4}|\d{2})\b", re.I)
# COLUMN-context-only forms (a bare year / a month-year IS a period there — see
# _period_match_ctx `column`): 'Dec-25' / 'Dec 2025' -> month-end 'as_at'; '2025' -> FY.
_MONTHYEAR_RX = re.compile(r"\b([A-Za-z]{3,})[ \-/]'?(\d{4}|\d{2})\b")     # Dec-25 / Dec 2025
_BAREYEAR_RX = re.compile(r"\b(\d{4})\b")                                  # 2025
# TITLE-context-only form. A bare year in prose stays guarded (ambiguous start vs
# end -- see _period_match_ctx), but a year that is the ENTIRE trailing caption
# after a title delimiter is the same period slot its siblings print a parsed
# token in. UOB's 4Q25 geography exhibit is split five ways by that caption --
# '-- 1H25', '-- 2H24', '-- 2H25' parsed; '-- 2024' and '-- 2025' did not, so
# both fell through to doc_period and FY2024's 77 cells were stamped 2025-12-31,
# a full year wrong, colliding with the genuine FY2025 table on the same key.
# ANCHORED to the end and requiring a delimiter, so an incidental year inside a
# title ('Basel III 2024 framework', 'Note 3 2025') is untouched.
_TITLE_TRAILING_YEAR_RX = re.compile(r"[—–\-|:]\s*((?:19|20)\d{2})\s*$")

# span vocabulary (printed convention, calendar-fiscal): the human-readable
# duration qualifier of a flow. 'nQ' is THE n-th quarter as banks print it
# ('3Q25'); '9M' is cumulative nine-months ('9M25'); 'as_at' is a point-in-time
# balance (no duration). period_START by span (calendar-fiscal: FY/1H/9M start on
# 1 Jan; 2H on 1 Jul; quarter n on the first day of that quarter). The
# [period_start, period] interval is the MACHINE semantic; the span token is
# provenance. 'as_at' has no interval -> period_start NULL.
_SPAN_START_MMDD: dict[str, tuple[int, int]] = {
    "FY": (1, 1), "1H": (1, 1), "9M": (1, 1), "2H": (7, 1),
    "1Q": (1, 1), "2Q": (4, 1), "3Q": (7, 1), "4Q": (10, 1),
}


def _month_to_quarter(mon: int) -> int:
    return (mon - 1) // 3 + 1


def _span_start(span: str | None, end_iso: str | None) -> str | None:
    """ISO period_start for a (span, end-date) pair via the calendar-fiscal rule;
    None for 'as_at' / no span (a point-in-time balance has no interval)."""
    if not span or not end_iso:
        return None
    mmdd = _SPAN_START_MMDD.get(span)
    if not mmdd:
        return None
    return f"{int(end_iso[:4]):04d}-{mmdd[0]:02d}-{mmdd[1]:02d}"


def _norm_year(y: str) -> int:
    """2-digit -> 20YY (corpus is post-2000); 4-digit as-is."""
    return 2000 + int(y) if len(y) == 2 else int(y)


_BARE_YEAR_ONLY_RX = re.compile(r"^\s*(\d{4})\s*$")
# A period end -> the CUMULATIVE span ending on it, by the same calendar-fiscal
# convention as _SPAN_START_MMDD. Used only by the bare-year clamp below, where
# the document's own reporting date supplies the cycle a bare year omits.
_CUMULATIVE_SPAN_BY_MMDD: dict[tuple[int, int], str] = {
    (3, 31): "1Q", (6, 30): "1H", (9, 30): "9M", (12, 31): "FY",
}


def clamp_bare_year_to_doc_period(label: str, period: str | None, span: str | None,
                                  start: str | None, doc_period: str | None
                                  ) -> tuple[str | None, str | None, str | None, bool]:
    """A BARE-YEAR column period may not end after the document's reporting date.

    A bare year ('2026') is the one period form carrying no month or day, so the
    column grammar resolves it to 31 December of that year, span FY. In a
    YEAR-END filing that is right. In an INTERIM one it invents a future date:
    OCBC 2Q26's Level 3 movements table prints group='2026' meaning the six
    months to the 30 June 2026 reporting date, and the loader stamped
    2026-12-31 — a period that had not happened yet.

    No filing can report a period ending AFTER its own reporting date. That
    holds for every bank and every vintage, so the bare year is re-read against
    the document's cycle: period := doc_period, span := the cumulative span
    ending there. PRIOR-year bare columns are untouched — those are genuine
    comparatives, and which comparative window they cover is a table-level
    question this rule deliberately does not guess at.

    Returns (period, span, start, clamped)."""
    if not (period and doc_period and period > doc_period):
        return period, span, start, False
    if not _BARE_YEAR_ONLY_RX.match(label or ""):
        return period, span, start, False
    mmdd = (int(doc_period[5:7]), int(doc_period[8:10]))
    new_span = _CUMULATIVE_SPAN_BY_MMDD.get(mmdd, span)
    return doc_period, new_span, _span_start(new_span, doc_period), True


def _period_match_ctx(t: str, column: bool) -> tuple[str, str, int, int] | None:
    """First period expression in an ALREADY-NORMALISED `t` ->
    (iso_end, span, start_pos, end_pos), else None. Ordered, first-match-wins:
    explicit DD-Month-YYYY date, half, quarter, nine-months, full-year/FY. `span`
    is the printed-convention duration token derived from the matched branch
    ({as_at,1H,2H,1Q..4Q,9M,FY}); DD-Month dates default 'as_at' unless a duration
    prefix ('year ended'->FY, 'half year ended'->1H/2H by month, 'quarter ended'
    ->nQ, 'nine months ended'/'ytd'->9M) upgrades them.

    CONTEXT: `column=True` additionally treats a bare 4-digit year ('2025'->FY,
    ends 31 Dec) and a month-year ('Dec-25'/'Dec 2025'-> month-end, 'as_at') as a
    period — the COLUMN axis is unambiguously periodic. `column=False` (titles /
    other prose) KEEPS the bare-year guard (a bare year alone is ambiguous start
    vs end -> not a period), with ONE exception, tried last so it can never
    shadow a printed token: a bare year that is the entire TRAILING caption after
    a title delimiter ('... — 2024' -> FY, ends 31 Dec) — the slot its sibling
    exhibits print '1H25'/'2H24' in. A year anywhere else in a title is still not
    a period. COLUMN context ALSO strips footnote markers anywhere
    first, so a footnoted period token ('2H25¹', '2H 2025 (1)') still parses (the
    marker was defeating the grammar); titles are not stripped."""
    if column:
        t = _strip_col_footnotes(t)
    m = _ISO_DATE_RX.search(t)
    if m and _month_from_token(m.group(2)) is not None:
        mon = _month_from_token(m.group(2))
        iso = f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"
        pre = t[:m.start()].lower()
        if "half" in pre:
            span = "1H" if mon <= 6 else "2H"
        elif "nine month" in pre or "9 month" in pre or "ytd" in pre:
            span = "9M"
        elif "quarter" in pre:
            span = f"{_month_to_quarter(mon)}Q"
        elif "year ended" in pre or "full year" in pre or "financial year" in pre:
            span = "FY"
        else:
            span = "as_at"
        return iso, span, m.start(), m.end()
    m = _HALF_ORD_RX.search(t)
    if m:
        h, y = _ORD[m.group(1).lower()], _norm_year(m.group(2))
        return (f"{y:04d}-06-30" if h == 1 else f"{y:04d}-12-31"), \
            ("1H" if h == 1 else "2H"), m.start(), m.end()
    m = _HALF_CMP_RX.search(t)
    if m:
        h, y = int(m.group(1)), _norm_year(m.group(2))
        return (f"{y:04d}-06-30" if h == 1 else f"{y:04d}-12-31"), \
            ("1H" if h == 1 else "2H"), m.start(), m.end()
    m = _QTR_ORD_RX.search(t)
    if m:
        q = _ORD[m.group(1).lower()]
        mm, dd = _QEND[q]
        y = _norm_year(m.group(2))
        return f"{y:04d}-{mm:02d}-{dd:02d}", f"{q}Q", m.start(), m.end()
    m = _QTR_CMP_RX.search(t)
    if m:
        q = int(m.group(1))
        mm, dd = _QEND[q]
        y = _norm_year(m.group(2))
        return f"{y:04d}-{mm:02d}-{dd:02d}", f"{q}Q", m.start(), m.end()
    m = _NINEM_ORD_RX.search(t) or _NINEM_CMP_RX.search(t)
    if m:
        return f"{_norm_year(m.group(1)):04d}-09-30", "9M", m.start(), m.end()
    m = _FY_RX.search(t) or _FULLYEAR_RX.search(t)
    if m:
        return f"{_norm_year(m.group(1)):04d}-12-31", "FY", m.start(), m.end()
    if column:
        m = _MONTHYEAR_RX.search(t)
        if m and _month_from_token(m.group(1)) is not None:
            mon, y = _month_from_token(m.group(1)), _norm_year(m.group(2))
            dd = calendar.monthrange(y, mon)[1]
            return f"{y:04d}-{mon:02d}-{dd:02d}", "as_at", m.start(), m.end()
        m = _BAREYEAR_RX.search(t)
        if m:
            return f"{_norm_year(m.group(1)):04d}-12-31", "FY", m.start(), m.end()
    else:
        # TITLE context, LAST resort — every branch above has already failed, so
        # this can never shadow a printed period token. A bare year that is the
        # whole trailing caption ('... — 2024') occupies the period slot; a year
        # anywhere else in the title does not, and stays guarded.
        m = _TITLE_TRAILING_YEAR_RX.search(t)
        if m:
            return f"{_norm_year(m.group(1)):04d}-12-31", "FY", m.start(1), m.end(1)
    return None


def parse_period_expr(text: str) -> str | None:
    """General period expression -> ISO period END date, else None (LOOSE — finds
    the expression anywhere, e.g. a period trailing a table title). Grammar:
      * DD-Month-YYYY date            -> that date (delegates to _ISO_DATE_RX;
        covers 'Year ended 31 December 2024', 'Half year ended 30 June 2025')
      * '1st Half 2025'/'First Half 2025'/'1H25'/'1H 2025'  -> 2025-06-30
        '2nd Half 2024'/'2H24'                              -> 2024-12-31
      * '2Q25'/'2Q 2025'/'Second Quarter 2025'             -> 2025-06-30
        (quarter-end = last day of month 3*Q)
      * '9M25'/'9M 2025'/'Nine months ended <date>'        -> 2025-09-30
      * 'FY2024'/'FY24'/'Full Year 2024'                   -> 2024-12-31
    GUARD: a bare 4-digit year alone is NOT a period (ambiguous start vs end) -> None,
    EXCEPT as the whole trailing caption of a title ('... — 2024' -> 2024-12-31, FY).
    Calendar-fiscal end dates (see grammar block above). This is the TITLE-context
    (column=False) date-only accessor; parse_period_span returns (end, span,
    start) and takes a `column` flag for the column axis."""
    m = _period_match_ctx(_norm(text), False)
    return m[0] if m else None


def parse_period_span(text: str, *, column: bool = False
                      ) -> tuple[str, str, str | None] | None:
    """General period expression -> (iso_end, span, iso_start), else None. Span is
    the printed-convention duration token; iso_start is the calendar-fiscal period
    start (None for 'as_at'). `column=True` accepts a bare year / month-year as a
    period (the column axis is unambiguously periodic); `column=False` keeps the
    bare-year guard (titles/prose) except for a trailing '— YYYY' caption. This is
    the span/interval-aware sibling of
    parse_period_expr — the loader uses it to populate col_dim/table_t
    period/period_span/period_start."""
    m = _period_match_ctx(_norm(text), column)
    if not m:
        return None
    return m[0], m[1], _span_start(m[1], m[0])


_PERIOD_PREFIXES = _DATE_PREFIXES | {"for", "for the", "the", "in",
                                     "year ended", "half year ended", "half-year ended",
                                     "quarter ended", "full year",
                                     # Fair Value Hierarchy column headers ('Fair value at
                                     # 31 Dec 2025' / '... 2024', OCBC condensed statements
                                     # note 14.3): the date IS the column's whole period axis,
                                     # not an incidental year inside a descriptive label —
                                     # found via Gate A2 (armed 2026-08-03) hard-failing the
                                     # load on this exact column rather than silently
                                     # mis-stamping it to doc_period.
                                     "fair value at"}


def is_period_text(text: str, *, column: bool = False) -> bool:
    """True when a header IS a date OR a period EXPRESSION (period axis) rather
    than a descriptive header — generalises is_date_text to the parse_period_expr
    grammar. The expression must COVER the header once boilerplate leading words
    are removed: '1st Half 2025' -> True, '1H25' -> True, 'As at 30 Jun 2025' ->
    True; a comparison/change banner '1st Half 2025 vs 1st Half 2024' -> False
    (residual 'vs 1st half 2024' is not boilerplate) so it is NOT collapsed onto a
    single period; 'Average balance ($m)' -> False.

    `column=True` (column leaf/group headers) additionally recognises a bare year
    ('2025' -> True) and a month-year ('Dec 2025' -> True) as a period axis; the
    residual guard still rejects a descriptive header carrying an incidental year
    ('Note 3 2025' -> residual 'note 3' -> False). In COLUMN context the residual
    is first cleared of footnote markers (already stripped by _period_match_ctx)
    and UNIT tokens ('$m', 'S$m', '%', \"'000\"), so a combined period+unit header
    ('2025 $m' -> residual '$m' -> '' -> True) is recognised while a descriptive
    header keeps its words ('Net loans $m' -> residual 'net loans' -> False)."""
    t = _norm(text)
    if column:
        t = _strip_col_footnotes(t)
    m = _period_match_ctx(t, column)
    if not m:
        return False
    _, _, s, e = m
    residual = (t[:s] + t[e:]).strip().lower()
    if column:
        residual = _strip_units(residual)
    return residual in _PERIOD_PREFIXES


# ---------------------------------------------------------------------------
# Unit token grammar (schema table_t.unit / col_dim.unit / row_dim.unit).
# Deterministic, ORDERED, first-match-wins; NO per-bank rules. A table-level
# DEFAULT is parsed from label_header then title; explicit row/col markers
# OVERRIDE it (resolved in the v_cell/v_cell_flat unit CASE, not here).
# ---------------------------------------------------------------------------
_UNIT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"%"), "%"),                                    # '%' '(%)' '% change' '(% chg)'
    (re.compile(r"\$\s*m", re.I), "S$m"),                       # ($m) S$m $m 'In $ millions' '$ millions'
    (re.compile(r"'000|\bthousands?\b", re.I), "'000"),         # ('000) thousands
    (re.compile(r"\bper share\b|\bcents?\b", re.I), "per_share"),
    (re.compile(r"\bnumber of\b|\bno\.\s*of\b", re.I), "count"),  # 'number of shares' / 'no. of'
    (re.compile(r"\btimes\b|\(x\)", re.I), "x"),                # times / (x)
]


def _strip_units(t: str) -> str:
    """Remove every unit token (the _UNIT_RULES patterns) from a string,
    whitespace re-collapsed. Used ONLY on the COLUMN-context period residual so a
    combined period+unit header collapses to boilerplate; the unit itself is still
    PARSED separately (parse_unit) for col_dim.unit."""
    for rx, _ in _UNIT_RULES:
        t = rx.sub(" ", t)
    return _WS.sub(" ", t).strip()


def parse_unit(text: str | None) -> str | None:
    """First matching unit token in `text` -> canonical unit, else None.
    General token grammar (no per-bank branch): '%'/(%)/'% change' -> '%';
    ($m)/S$m/'In $ millions' -> 'S$m'; ('000)/thousands -> \"'000\";
    'per share'/cents -> 'per_share'; 'number of'/'no. of' -> 'count';
    'times'/'(x)' -> 'x'. See _UNIT_RULES for exact order (first match wins)."""
    s = _norm(text)
    if not s:
        return None
    for rx, unit in _UNIT_RULES:
        if rx.search(s):
            return unit
    return None


# A '%' printed INSIDE a row NAME as a coupon / interest rate, immediately
# followed by descriptive text ('3.58% non-cumulative non-convertible perpetual
# capital securities', '3.0% perpetual capital securities') — a rate-in-a-name,
# NOT the row's unit. The value cells are currency (S$m capital amounts), so the
# '%' must NOT be inferred as the row unit. Lookahead keeps the trailing letter so
# stripping it out leaves any OTHER (legitimate) unit token intact for re-parse.
_COUPON_IN_NAME_RX = re.compile(r"\d+(?:\.\d+)?\s*%(?=\s*[A-Za-z])")


def parse_row_label_unit(text: str | None) -> str | None:
    """Unit parsed from a ROW label, with the COUPON-IN-NAME guard. Identical to
    parse_unit EXCEPT a '%' that is an embedded numeric coupon/rate followed by
    descriptive text ('3.58% non-cumulative ... perpetual capital securities') is a
    rate PRINTED IN THE ROW NAME, not the row's unit — those cells are S$m capital
    amounts, so the '%' is dropped and the unit resolves via the normal col/table
    chain. A STANDALONE / TERMINAL '%' marker is still a real row unit: a
    parenthesised '(%)' ('Net interest margin (%)', \"Capital Adequacy Ratio ('CAR')
    (%)\") or a ratio name ending in '%'. Non-'%' unit tokens are unaffected — the
    coupon guard fires ONLY when the only '%' present is a coupon-in-name (after
    stripping every coupon occurrence, parse_unit is re-run so a legit unit marker
    elsewhere in the label still wins)."""
    s = _norm(text)
    if not s:
        return None
    u = parse_unit(s)
    if u == "%" and _COUPON_IN_NAME_RX.search(s):
        return parse_unit(_COUPON_IN_NAME_RX.sub(" ", s))
    return u


_VALUE_X = re.compile(r"\d\s*(?:x|times)$", re.I)


def unit_from_value(value_raw: str | None) -> str | None:
    """TOP of the per-cell unit chain — the cell's OWN printed token (schema
    NOTE: 'Total allowances/ NPA' prints '137%', so value_raw keeps the '%').
    Deterministic, no per-bank rule:
      raw ending with '%'                         -> '%'   ('137%', '9.2 %')
      raw matching r'\\d\\s*(x|times)$' (case-ins) -> 'x'   ('1.2x', '3 times')
      else                                        -> None  (defer to row/col/table).
    This BEATS the column/row/table unit: a stray ratio cell inside an S$m block
    is self-labelled and must not inherit the block's currency."""
    s = (value_raw or "").strip()
    if not s:
        return None
    if s.endswith("%"):
        return "%"
    if _VALUE_X.search(s):
        return "x"
    return None


def resolve_cell_unit(value_raw: str | None, row_unit: str | None,
                      col_unit: str | None, table_unit: str | None) -> str | None:
    """Per-cell unit via the precedence chain, steps (1)-(5) — the DOCUMENT DEFAULT
    (step 6) and the unresolvable-NULL warning are applied doc-wide in load_units
    after every table is mapped (the modal table unit is not knowable mid-table):
      (1) cell value token            unit_from_value(value_raw)
      (2) row unit IF it is '%'        (a ratio row beats a currency column)
      (3) col unit                     (derived '% chg' columns cut across rows)
      (4) row unit                     (any other explicit row marker)
      (5) table unit                   (table default)
      -> None here means "unresolved by 1-5" -> doc default / NULL downstream."""
    v = unit_from_value(value_raw)
    if v:
        return v
    if row_unit == "%":
        return "%"
    if col_unit is not None:
        return col_unit
    if row_unit is not None:
        return row_unit
    if table_unit is not None:
        return table_unit
    return None


def parse_value(raw: str) -> tuple[str, float | None]:
    """Value-token-driven (schema NOTE A). Returns (cell_state, value_num) with
    cell_state in schema_v7's vocabulary {empty,null,suppressed,zero,reported}:
      ''            -> ('empty',      None)
      -/–/—         -> ('null',       None)
      '#'           -> ('suppressed', None)
      '0'           -> ('zero',       0.0)
      '(1,234)'     -> ('reported', -1234.0)   (parenthesised negative)
      '1,234'       -> ('reported',  1234.0)
      non-numeric   -> ('reported',  None)     (textual reported, e.g. 'AAA to BBB+')
    """
    s = (raw or "").strip()
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
        return "reported", None


def _clean_label(label: str) -> str:
    """Verbatim label with trailing footnote markers stripped, whitespace
    collapsed, ORIGINAL case preserved (display side of a lineage level)."""
    s = _norm(label)
    prev = None
    while prev != s:                       # peel repeated markers: 'x¹²' or 'x (1)(2)'
        prev = s
        s = _FOOTNOTE_TAIL.sub("", s).strip()
    return s


def lineage_key(display_parts: list[str]) -> str:
    """Registry identity: footnote-stripped levels, lowercased, ' > '-joined
    (schema_v7 §2b comment block). VERBATIM lineage identity — cross-bank
    semantic bridging is concept_map's job, not this key's."""
    return " > ".join(p.lower() for p in display_parts)


def axis_norm(label: str | None) -> str:
    """Normalise a row/col label for an axis-map lookup: footnote markers stripped,
    whitespace collapsed, lowercased — EXACTLY a single-level lineage_key
    (_clean_label then lower). Each map's label_norm is authored in this same
    normalisation, so a lookup is an EXACT full-label equality (never substring:
    'Trading income' -> 'trading income', which is NOT the key 'trading', so it
    does not mis-stamp).

    (Was `geo_norm`: geography stamping is retired, but the rule is
    dimension-agnostic and segment/industry still use it.)"""
    return _clean_label(label or "").lower()


# Business-segment stamping normalises by EXACTLY the same rule.
seg_norm = axis_norm


def seg_lookup(label: str | None, seg_map: dict[str, str]) -> str | None:
    """Exact normalised-label match against segment_map -> segment_key, else None."""
    return seg_map.get(seg_norm(label))


# Industry-of-exposure stamping normalises by EXACTLY the same rule.
ind_norm = axis_norm


def ind_lookup(label: str | None, ind_map: dict[str, str]) -> str | None:
    """Exact normalised-label match against industry_map -> industry_key, else None."""
    return ind_map.get(ind_norm(label))


# legal_entity is a COLUMN axis (spec: migrate_add_legal_entity.py) — the banks put
# 'The Group'/'The Company'/'The Bank' in a span-header banner over period columns,
# not per-row. legal_entity_map.label_norm is authored via normalize_row_label
# (stage3_stamp.resolve.normalize) — a slug ('the_group'), NOT axis_norm's space-lowered form
# ('the group') — so the lookup here MUST use the same normaliser or it never
# matches. Own label wins; else the parent (group banner) label; else None (cell
# falls back to the CONSOLIDATED default at materialisation, same as the migration).
DEFAULT_LEGAL_ENTITY = "CONSOLIDATED"


def le_lookup(own: str | None, parent: str | None, le_map: dict[str, str]) -> str | None:
    """own/parent column labels -> legal_entity_key via legal_entity_map, else None."""
    return (le_map.get(normalize_row_label(own)) if own else None) \
        or (le_map.get(normalize_row_label(parent)) if parent else None)


# ---------------------------------------------------------------------------
# AXIS EXCLUSIVITY (geo / segment / industry). Some verbatim labels collide
# across more than one axis map — the canonical case is 'Others', which is a
# member of segment_map (SEG_OTHER) AND industry_map (IND_OTHERS)
# simultaneously. Stamping every axis a label matches (the old behaviour)
# CONTAMINATES the non-real axis (a segment table's 'Others' COLUMN spuriously
# picking up a geo cut). The fix is table-axis-local and deterministic, never a
# per-bank/per-table special case:
#
#   For one table-axis (rows, or leaf+banner columns, evaluated separately):
#     1. Look up every label against all three maps.
#     2. A label matching 0 or 1 axis, OR matching several axes where AT MOST
#        ONE of those matches is a real MEMBER (not that axis's total/default
#        sentinel), stamps exactly as before on every axis it matched — a
#        'Total' column legitimately IS SEG_TOTAL and IND_TOTAL simultaneously
#        (both are the SAME whole-book default, not a contamination); only a
#        genuine MEMBER-vs-MEMBER collision (>=2 matched axes each a real,
#        non-default member — 'Others' hitting OTH/SEG_OTHER/IND_OTHERS) is
#        ambiguous.
#     3. An ambiguous label resolves to whichever member-matched axis is
#        DOMINANT on this same table-axis: the axis with >=2 OTHER labels (on
#        this axis, this table) that matched IT ALONE and are a real member. If
#        exactly one of the label's member-matched axes qualifies, stamp ONLY
#        that axis with its key (every other axis gets NULL for this label). If
#        zero or more than one axis qualifies, no axis dominates: stamp NOTHING
#        and emit a drift warning so a human can see the collision.
# ---------------------------------------------------------------------------
# Geography was a third axis here until 2026-08-12. It is retired: the loader no
# longer stamps geo_key and geo_map is gone from the schema. The exclusivity rule
# below is unchanged and still generalises to N axes — geo was simply removed
# from the list, not special-cased out of it.
_AXES = ("segment", "industry")
_AXIS_SENTINEL = {"segment": "SEG_TOTAL", "industry": "IND_TOTAL"}


def resolve_axis_labels(labels: list[str], seg_map: dict[str, str],
                        ind_map: dict[str, str],
                        ) -> tuple[list[dict[str, str | None]], list[str]]:
    """Resolve segment/industry stamps for one table-axis's labels (all row labels
    of a table, or all leaf+banner column labels of a table), applying the
    axis-exclusivity rule above. Returns (per-label resolved dict, warnings)
    in the SAME order as `labels`; warnings are UNPREFIXED (caller adds table_id)."""
    lookups = {"segment": seg_map, "industry": ind_map}
    lookup_fn = {"segment": seg_lookup, "industry": ind_lookup}
    raw = [{a: lookup_fn[a](label, lookups[a]) for a in _AXES} for label in labels]
    matched = [[a for a in _AXES if r[a] is not None] for r in raw]
    # "member" match = matched AND not that axis's own total/default sentinel
    # (a real business-line/country/industry, not the whole-book default).
    member_matched = [[a for a in m if r[a] != _AXIS_SENTINEL[a]]
                      for r, m in zip(raw, matched)]

    unambiguous_count = {a: 0 for a in _AXES}
    for r, m in zip(raw, matched):
        if len(m) == 1:
            a = m[0]
            if r[a] != _AXIS_SENTINEL[a]:
                unambiguous_count[a] += 1

    resolved: list[dict[str, str | None]] = []
    warnings: list[str] = []
    for label, r, m, mm in zip(labels, raw, matched, member_matched):
        # <=1 real member match anywhere -> not a contamination (sentinel-vs-
        # sentinel or sentinel-vs-single-member collisions stamp as-is).
        if len(mm) <= 1:
            resolved.append(dict(r))
            continue
        candidates = [a for a in mm if unambiguous_count[a] >= 2]
        if len(candidates) == 1:
            dom = candidates[0]
            resolved.append({a: (r[a] if a == dom else None) for a in _AXES})
        else:
            resolved.append({a: None for a in _AXES})
            warnings.append(f"ambiguous axis label {label!r} — no dominant axis")
    return resolved, warnings


def resolve_printed_parents(rows: list[GRow]) -> dict[int, int]:
    """GRow.parent -> parent INDEX, for the rows where the extractor supplied one.

    The extractor emits `parent='h1'/'h2'/…`, a POSITIONAL header reference, not a
    row id (`GRow.row_id` is None throughout the corpus — which is why the old
    printed-parent cross-check could never resolve it and only ever warned).
    `hN` is the Nth header row, and a header row is one immediately followed by a
    row at a strictly greater level; `section_header` rows are not numbered.

    Verified against DBS 4Q25 'Selected income statement items': headers are
    'Commercial book total income' (h1), 'Markets trading Income' (h2), 'Total
    income' (h3), 'Allowances for credit and other losses' (h4) — and the
    extractor's h3 for 'Of which: Net interest income' is CORRECT where the
    position rule mis-assigned it to the markets book (the defect
    lineage_identity_map.csv records for pnl.nii.net).

    A mapping is used only when the resolved header is strictly shallower than
    the child; otherwise it is dropped and position decides.

    The header rule itself lives in `transforms.header_row_indices` — ONE copy,
    because `merge_continuation_tables` has to rebase these ordinals when it
    appends a continuation and a divergent second copy would rebase them onto
    different rows than this reader counts."""
    headers = header_row_indices(rows)
    out: dict[int, int] = {}
    for i, r in enumerate(rows):
        ref = getattr(r, "parent", None)
        if not isinstance(ref, str):
            continue
        m = re.fullmatch(r"\s*h(\d+)\s*", ref, re.I)
        if m:
            n = int(m.group(1)) - 1
            if 0 <= n < len(headers):
                p = headers[n]
                if p < i and (rows[p].level or 0) < (r.level or 0):
                    out[i] = p
            continue
        # The parent is sometimes a LITERAL LABEL rather than an hN reference —
        # DBS 2Q25 CUSTOMER DEPOSITS ends with four rows carrying parent='Total'.
        # Resolve it against the nearest preceding row with that label.
        want = ref.strip().casefold()
        for j in range(i - 1, -1, -1):
            if (rows[j].label or "").strip().casefold() == want:
                if (rows[j].level or 0) < (r.level or 0):
                    out[i] = j
                break
    return out


def row_parents_by_position(rows: list[GRow], *,
                            skip_terminal: bool = True,
                            sums_to: dict[int, int] | None = None,
                            printed: dict[int, int] | None = None) -> list[int | None]:
    """row_parent = nearest earlier row exactly one level up (html_to_cells
    enricher pattern). Returns a parent INDEX (into rows) per row, None at top.
    GRow.parent (printed) is a display cross-check only; position is authoritative.

    `skip_terminal=False` disables the total/note skip below. It is passed when
    the levels came from the PDF GEOMETRY rather than from the model: the skip
    exists to survive WRONG levels (a mid-table total sitting at the same level
    as the items it aggregates would swallow the next block), but on geometric
    depths an indented row under a printed total genuinely IS its child — DBS's
    'Of which: Net interest income' is printed indented directly beneath 'Total
    income', and skipping the total would mis-parent it to the previous block.

    Terminal rows are SKIPPED as parent candidates: a 'total' aggregates the block
    above it and a 'note' is a footnote — neither is ever the structural parent of a
    DATA row. A candidate that is a total is always skipped; a candidate that is a
    note is skipped UNLESS the child is itself a note (preserves the 'Notes:' note
    block's own display nesting). A row whose only one-level-up candidates are
    totals/notes gets row_parent NULL (top-level) rather than mis-parenting to a
    total — the DEBTS ISSUED defect ('Due within/after 1 year' parenting to the
    preceding Total)."""
    printed = printed or {}
    sums_to = sums_to or {}
    parents: list[int | None] = []
    for i, row in enumerate(rows):
        if i in printed:                       # the extractor said so — trust it
            parents.append(printed[i])
            continue
        p: int | None = None
        for j in range(i - 1, -1, -1):
            cand = rows[j]
            if cand.level != row.level - 1:
                continue
            if skip_terminal and cand.row_type == "total" and not _heads_a_block(
                    rows, j, sums_to):
                continue                       # terminal total — never a parent
            if skip_terminal and cand.row_type == "note" and row.row_type != "note":
                continue                       # notes parent only other notes
            p = j
            break
        parents.append(p)
    return parents


def _heads_a_block(rows: list[GRow], j: int, sums_to: dict[int, int]) -> bool:
    """Is the `total`-typed row at index j actually a SECTION HEADER?

    The blanket total-skip above exists to stop 'Due within/after 1 year'
    parenting to a preceding 'Total' (the DEBTS ISSUED defect). But some rows are
    typed `total` merely because the word appears in their label while they in
    fact HEAD the block beneath them — DBS prints 'Commercial book total income'
    and 'Allowances for credit and other losses' with their components indented
    underneath. Skipping those orphans every child (1,055 rows on the model path,
    19%) or mis-parents them to the previous unrelated line (the ECL rows landing
    under 'Amortisation of intangible assets' in 3Q25).

    DISCRIMINATOR: a genuine terminal total AGGREGATES rows — `verified_sums_to`
    assigns its members to it, so it appears as a VALUE in the sums_to map. A
    header total aggregates nothing and is immediately followed by deeper rows.
    Both conditions are required, so a total that both sums a block above it and
    is followed by deeper rows stays terminal (position then picks it up only if
    nothing shallower intervenes)."""
    aggregates = j + 1 in set(sums_to.values())
    followed_by_deeper = (j + 1 < len(rows)
                          and (rows[j + 1].level or 0) > (rows[j].level or 0))
    return followed_by_deeper and not aggregates


def _row_period_ps(label: str) -> tuple[str | None, str | None, str | None] | None:
    """Parse a ROW label as a period, with the column-context grammar + residual
    guard. THE single row-axis period parse — `row_period_banners` (the pre-pass)
    and the row loop both call this, so the two can never drift apart."""
    return (parse_period_span(label, column=True)
            if is_period_text(label, column=True) else None)


def row_period_banners(rows: list[GRow], doc_period: str | None = None
                       ) -> list[tuple[str, str | None] | None]:
    """Per-row (period, span) inherited from the nearest PRECEDING period BANNER,
    index-aligned to `rows`. None where no banner is in scope.

    WHY THIS EXISTS. The row rung's other inheritance path walks ANCESTORS
    (`row_parent`), which only works when the extractor nests a banner above its
    rows. Banks routinely stack period BLOCKS vertically instead, and the model
    then emits the banner at the SAME level as the rows it heads — or deeper.
    `row_parents_by_position` parents strictly by `level - 1`, so those rows get
    the block caption as their parent, never the banner, and the ancestor walk
    finds nothing:

        DBS_4Q25 'PERFORMANCE BY BUSINESS SEGMENTS' — 51 rows ALL at level 1
        with row_parent=1; banners '2nd Half 2025'/'1st Half 2025'/'2nd Half
        2024'/'Year 2025'/'Year 2024' at rows 2/12/22/32/42 all parsed
        correctly, yet all 225 cells fell through to doc_period (135 wrong).

        UOB_4Q25 'Classification of Financial Assets … - Dec 24' — banner
        'Dec 24' at level 1, data rows at level 0 (banner DEEPER than its own
        rows); all 105 cells stamped 2025-12-31, a whole Dec-2024 balance sheet
        addressable as Dec-2025.

    PREDICATE — valueless-ness, NOT span. An earlier design keyed on a duration
    span (1H/2H/FY) and would have left the UOB table above broken, because its
    banner is `as_at`. Of the 70 valueless period rows in the corpus, 42 carry a
    duration and 28 carry `as_at`, and BOTH kinds scope the rows beneath them.
    What separates a banner from an opening/closing BALANCE row ('At 1 January
    2026', which owns its figures and must never scope anything) is that the
    banner has no values of its own — and `row_type in (section_header,
    sub_header)` is exactly the membership that GUARANTEES no cell_fact is
    emitted (see the skip at the row loop). So "is a banner" and "carries its own
    values" are mutually exclusive by construction, not by measurement. Verified
    over every parsed.json: valueless period rows are section_header 87/87;
    valued ones are data/total 121/121; zero overlap. `note` is excluded — a
    footnote is never a scope.

    DIVERGES from stage3_stamp.masterlist.masterlist_derive.build_ancestry(),
    which solves the same problem downstream for identity, in two ways. Both are
    deliberate; anyone diffing the two implementations will notice:

      1. Only PERIOD banners push and pop. build_ancestry also pushes plain
         (non-period) headers, which here would pop a live banner at a
         sub-header and drop its rows back to the table/doc period. Measured
         cost of copying it: 52 rows / 270 cells, and in all 52 the no-pop
         answer is the correct one.
      2. Popping RESTORES the enclosing banner (read the stack top) rather than
         clearing to None, so a deeper block ending returns its rows to the
         enclosing period instead of leaving a hole. Identical on today's corpus
         — no table has period banners at two levels — but correct if a nested
         block ever appears.

    THE STACK UNWINDS ONLY ON A NEW BANNER. A DATA row at a shallower level does
    NOT end a block, however much it looks like it should. That is forced by the
    UOB table above, whose banner sits at level 1 while its data rows sit at
    level 0: unwinding on a shallower data row would pop that banner before any
    row could read it and re-break the exact case this function exists to fix.
    The cost is that a deeper block stays live until the next banner (or the end
    of the table) — accepted, because a banner is per-table and no table in the
    corpus nests them.

    For the same reason the READ is NOT level-filtered: a de-indented row ('Total
    assets' at level 0 inside a level-0 block) and the UOB table both depend on a
    shallower row still seeing a deeper banner.

    `doc_period` is required for the SAME bare-year clamp the row loop and the
    column axis apply: a banner printed '2026' resolves to 31 December 2026, a
    date that has not happened in a 2Q26 filing. Omitting it here (while the row
    loop clamped) left 7 cells of DBS 2Q26 'Balance at 30 June 2026' stamped
    2026-12-31/FY — measured, not hypothetical.

    Cell-resolution only. `row_dim.row_period` keeps the row's OWN parse, and the
    lineage exclusion is unaffected."""
    out: list[tuple[str, str | None] | None] = [None] * len(rows)
    stack: list[tuple[int, str, str | None]] = []      # (level, period, span)
    for i, r in enumerate(rows):
        lvl = r.level or 0
        ps = _row_period_ps(r.label)
        if ps is not None:
            _p, _s, _st, _ = clamp_bare_year_to_doc_period(
                r.label, ps[0], ps[1], ps[2], doc_period)
            ps = (_p, _s, _st)
        if (ps is not None and ps[0] and not r.values
                and r.row_type in ("section_header", "sub_header")):
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, ps[0], ps[1]))
            continue                       # a banner does not scope itself
        if stack:
            out[i] = (stack[-1][1], stack[-1][2])
    return out


def _cell_value_num(row: GRow, pos: int) -> float | None:
    """value_num of a row's cell at leaf position `pos` (0-based); None if the row
    is narrower than pos or the cell parses to no number."""
    if pos >= len(row.values):
        return None
    return parse_value(row.values[pos].value)[1]


_SUM_MAX_ENUM = 14   # cap on non-zero members enumerated in the sign search (2^14)


def verified_sums_to(rows: list[GRow], n_leaf: int, col_units: list[str | None] | None = None
                     ) -> tuple[dict[int, int], dict[int, int], list[str]]:
    """Derive the SIGN-AWARE arithmetic verified-total relation. Returns
    (sums_to, sums_sign, warnings): sums_to maps a member row_id (1-based) -> the
    row_id of the total it verifiably sums to; sums_sign maps that same row_id ->
    +1 (added) / -1 (subtracted).

    For each row_type='total' T: walk UP the contiguous run above it, stopping at a
    section_header / sub_header / table start; notes are skipped (no value). The
    PREVIOUS total (if the run hits one) is captured as a CARRY-IN member (a total
    can be prior-subtotal +/- new lines) and stops the run. Data members are the
    SHALLOWEST hierarchy level in the run (deeper 'of which' rows double-count -> out).

    `col_units` (optional, position-aligned to n_leaf): a column whose parsed unit is
    '%' is EXCLUDED from the arithmetic check — a percent-change column is
    non-additive by nature (the % change of a total is not the sum of the %
    changes of its parts). Guard: at least one non-'%' value column must remain for
    a block to be checkable; an all-'%' block stays NULL with a dedicated warning.

    Resolution:
      1. FAST additive path (preserves prior behaviour EXACTLY): all +1 over the
         DATA members ONLY. If sum == T within tol 1.0*len(data_members) for every
         non-NULL, non-'%' column -> assign sums_to/sums_sign=+1, done (carry-in
         never used).
      2. Else SIGN search over the extended candidate set (data members + carry-in):
         find s_i in {+1,-1} with sum(s_i*v) == T within tol 1.0*len(members) for
         EVERY non-NULL, non-'%' column. Member NULL -> 0. Zero-valued members are
         fixed +1 and EXCLUDED from enumeration (never widen the solution count).
         >14 non-zero members -> NULL + 'block too large'. Exactly ONE solution ->
         assign; none -> 'no solution' + NULL; more than one -> 'ambiguous, k
         solutions' + NULL.
    Never a load failure — only warnings."""
    sums_to: dict[int, int] = {}
    sums_sign: dict[int, int] = {}
    warnings: list[str] = []
    _STOP = {"section_header", "sub_header"}
    pct_cols = {pos for pos in range(n_leaf)
                if col_units and pos < len(col_units) and col_units[pos] == "%"}
    for i, T in enumerate(rows):
        if T.row_type != "total":
            continue
        run: list[tuple[int, GRow]] = []
        carry_in: tuple[int, GRow] | None = None
        for k in range(i - 1, -1, -1):
            rk = rows[k]
            if rk.row_type in _STOP:
                break
            if rk.row_type == "note":
                continue                       # non-value; skip, do not stop the run
            if rk.row_type == "total":
                carry_in = (k, rk)             # previous total = carry-in member; boundary
                break
            run.append((k, rk))
        if not run and carry_in is None:
            continue

        data_members: list[tuple[int, GRow]] = []
        if run:
            block_level = min(r.level for _, r in run)
            data_members = [(k, r) for k, r in run if r.level == block_level]

        t_vec = {pos: _cell_value_num(T, pos) for pos in range(n_leaf)}
        active = [pos for pos in range(n_leaf)
                  if t_vec[pos] is not None and pos not in pct_cols]
        if not active:
            warnings.append(
                f"total row {i + 1} ({T.label!r}): only %-columns, not "
                f"arithmetically verifiable; sums_to left NULL")
            continue

        # --- (1) FAST additive path: all +1 over DATA members only -----------
        if data_members:
            tol = 1.0 * len(data_members)
            if all(abs(sum((_cell_value_num(r, pos) or 0.0) for _, r in data_members)
                       - t_vec[pos]) <= tol for pos in active):
                for k, _ in data_members:
                    sums_to[k + 1] = i + 1
                    sums_sign[k + 1] = 1
                continue

        # --- (2) SIGN search over extended candidate set ---------------------
        members = list(data_members)
        if carry_in is not None:
            members.append(carry_in)
        if not members:
            continue
        tol = 1.0 * len(members)
        vecs = {k: [(_cell_value_num(r, pos) or 0.0) for pos in range(n_leaf)]
                for k, r in members}
        # A member is sign-VARIABLE only if it is non-zero in some column that T
        # actually constrains (active). Zero across all active columns -> its sign
        # is immaterial there: fix +1 and exclude from enumeration so it can't
        # spuriously double the solution count (deep-reasoner trap b).
        nonzero = [k for k, _ in members
                   if any(vecs[k][pos] != 0.0 for pos in active)]
        if len(nonzero) > _SUM_MAX_ENUM:
            warnings.append(
                f"total row {i + 1} ({T.label!r}): block too large "
                f"({len(nonzero)} non-zero members > {_SUM_MAX_ENUM}); sums_to left NULL")
            continue

        def verifies(sign_of: dict[int, int]) -> bool:
            for pos in active:
                if abs(sum(sign_of[k] * vecs[k][pos] for k, _ in members)
                       - t_vec[pos]) > tol:
                    return False
            return True

        solutions: list[dict[int, int]] = []
        for bits in range(1 << len(nonzero)):
            sign_of = {k: 1 for k, _ in members}            # zero members fixed +1
            for b, k in enumerate(nonzero):
                sign_of[k] = 1 if not (bits >> b) & 1 else -1
            if verifies(sign_of):
                solutions.append(sign_of)

        if len(solutions) == 1:
            sol = solutions[0]
            for k, _ in members:
                sums_to[k + 1] = i + 1
                sums_sign[k + 1] = sol[k]
        elif not solutions:
            warnings.append(
                f"total row {i + 1} ({T.label!r}): no sign assignment sums the "
                f"{len(members)} member(s) to the total in all columns; sums_to left NULL")
        else:
            warnings.append(
                f"total row {i + 1} ({T.label!r}): ambiguous, {len(solutions)} sign "
                f"solutions; sums_to left NULL")
    return sums_to, sums_sign, warnings


def row_lineage(rows: list[GRow], parents: list[int | None], i: int,
                labels_clean: list[str | None] | None = None) -> list[str]:
    """Root->row display chain (footnote-stripped labels) for row i, with the
    PERIOD AXIS EXCLUDED exactly like col_lineage (schema_v7 §2b): a row whose
    label IS a period ('Dec-25'/'Jun-25'/'Dec-24' in a UOB NPL table) drops out of
    the lineage — otherwise every reporting date would mint a fresh row_lineage id
    and the registry would never converge (period resolves via row_period onto
    cell_fact.period instead). Any ancestor that is a period is dropped too; an
    empty chain falls back to the canonical token 'value', mirroring columns.

    `labels_clean` (when the geometry stage matched this table) supplies the
    TYPOGRAPHICALLY superscript-stripped label per row. It is preferred over the
    verbatim label because _clean_label's regex only peels parenthesised and
    unicode-superscript tails — it cannot see that the '5' in 'Return on
    equity4, 5' is printed as a footnote marker, so the registry identity would
    otherwise carry the footnote numbering (and change when the footnotes are
    renumbered next quarter). Period exclusion still tests the VERBATIM label:
    whether a row IS a period is a property of what is printed, not of the
    cleaning."""
    chain: list[str] = []
    j: int | None = i
    while j is not None:
        lbl = rows[j].label
        if not is_period_text(lbl, column=True):
            clean = labels_clean[j] if labels_clean else None
            chain.append(_clean_label(clean or lbl))
        j = parents[j]
    chain.reverse()
    if not chain:
        chain = ["value"]
    return chain


def col_lineage(col: GColumn) -> list[str]:
    """Column lineage with the period axis EXCLUDED (schema_v7 §2b): a date OR
    period-expression leaf/group drops out (is_period_text); empty lineage falls
    back to the group banner, else the canonical token 'value'. So side-by-side
    period columns converge to ONE col_lineage_id — including LEAVES under a
    period-expression GROUP banner ('1st Half 2025' / '1st Half 2024' / '2nd Half
    2024'): the banner is excluded, so 'Average balance ($m)' converges across the
    three period groups, distinguished only by col_period.

    COLUMN context (bare-year fix 2026-07-14): a bare-year group banner ('2025' /
    '2024') and month-year banners are ALSO period-axis, so their leaves converge
    to the same '$m'/'%' lineage as the half-year groups (was '2025 > $m')."""
    parts: list[str] = []
    if col.group and not is_period_text(col.group, column=True):
        parts.append(_clean_label(col.group))
    if col.leaf and not is_period_text(col.leaf, column=True):
        parts.append(_clean_label(col.leaf))
    if not parts:
        parts = ["value"]
    return parts


def _pad5(levels: list[str]) -> list[str | None]:
    return (levels + [None] * 5)[:5]


# ---------------------------------------------------------------------------
# Column-sum reconciliation (user-scoped A5) — a WARNING gate AND a verified
# RECORD, never a failure. A dimension genuinely partitions across columns only
# when >=2 leaf columns are stamped with MEMBER keys of that dimension AND >=1 leaf
# column carries its DEFAULT member (SEG_TOTAL / GLOBAL — the 'Total'/'Group'
# whole-bank column, i.e. THE total column). %-unit columns are excluded from both
# sets (a percent column is non-additive, like verified_sums_to's %-gating). Then
# per value-bearing row the member cells must sum (sign-aware: a 'less:
# eliminations' member subtracts) to the total cell within tolerance 1.0*n_members.
#   * reconciles across EVERY checkable row  -> RECORD col_dim.sums_to = total
#     col_id + col_dim.sums_sign on each MEMBER column (mirror of row_dim.sums_to);
#     the total column keeps sums_to NULL (like a row total).
#   * does NOT reconcile                     -> leave sums_to NULL + one warning
#     per mismatching row (unchanged behaviour).
# All other tables (period columns, single-axis tables, no total column) are
# SILENTLY skipped — the gate never fires there, nothing is recorded.
# ---------------------------------------------------------------------------
_ELIMINATION_RX = re.compile(r"\b(?:less|eliminat)", re.I)


def _column_sum_reconcile(cur: sqlite3.Cursor, doc_id: str, table_id: str,
                          rows: list[GRow], seg_by_pos: list[str | None],
                          ind_by_pos: list[str | None],
                          col_labels: list[str], col_units: list[str | None],
                          warnings: list[str]) -> None:
    for dim, key_by_pos, default_key in (
            ("segment", seg_by_pos, "SEG_TOTAL"),
            ("industry", ind_by_pos, "IND_TOTAL")):
        # %-unit columns are non-additive -> never a member or the total column.
        member_pos = [p for p, k in enumerate(key_by_pos)
                      if k is not None and k != default_key and col_units[p] != "%"]
        total_pos = [p for p, k in enumerate(key_by_pos)
                     if k == default_key and col_units[p] != "%"]
        if len(member_pos) < 2 or not total_pos:
            continue                       # dimension does not partition across columns here
        n_members = len(member_pos)
        tol = 1.0 * n_members
        # sign per member column: -1 for a detectable 'less: eliminations' member,
        # else +1. The reconciliation check is sign-aware so an elimination column
        # is subtracted, matching how it is recorded.
        sign_by_pos = {p: (-1 if _ELIMINATION_RX.search(col_labels[p] or "") else 1)
                       for p in member_pos}
        total_col = total_pos[0]           # THE total column (first default-member col)
        checked = False                    # at least one checkable (all-member-present) row
        reconciles = True
        for r in rows:
            if r.row_type in ("section_header", "sub_header", "note"):
                continue                   # header/note rows carry no cells
            mvals = {p: _cell_value_num(r, p) for p in member_pos}
            if any(v is None for v in mvals.values()):
                continue                   # skip rows with any NULL/text member cell
            s = sum(sign_by_pos[p] * mvals[p] for p in member_pos)
            row_checked = False
            for tp in total_pos:
                t = _cell_value_num(r, tp)
                if t is None:
                    continue
                row_checked = True
                if abs(s - t) > tol:
                    reconciles = False
                    warnings.append(
                        f"{table_id} row {r.label!r}: {dim} members sum {s} "
                        f"!= total {t}")
            checked = checked or row_checked
        # RECORD the verified relation only when it reconciled across >=1 checkable
        # row; nothing is stored on the total column (sums_to NULL there).
        if checked and reconciles:
            for p in member_pos:
                cur.execute(
                    "UPDATE col_dim SET sums_to = ?, sums_sign = ? WHERE "
                    "doc_id = ? AND table_id = ? AND col_id = ?",
                    (total_col + 1, sign_by_pos[p], doc_id, table_id, p + 1))


# ---------------------------------------------------------------------------
# Registry get-or-create (global, never doc-deleted)
# ---------------------------------------------------------------------------
def _get_or_create_header(cur: sqlite3.Cursor, table: str, id_col: str,
                          display_parts: list[str]) -> int:
    depth = len(display_parts)
    if not (1 <= depth <= 5):
        raise RuntimeError(
            f"{table} lineage depth {depth} out of range 1..5 (overflow is a "
            f"hard load failure): {display_parts!r}")
    key = lineage_key(display_parts)
    l1, l2, l3, l4, l5 = _pad5(display_parts)
    cur.execute(
        f"INSERT OR IGNORE INTO {table}(lineage_key,lvl1,lvl2,lvl3,lvl4,lvl5,depth) "
        f"VALUES (?,?,?,?,?,?,?)", (key, l1, l2, l3, l4, l5, depth))
    row = cur.execute(
        f"SELECT {id_col} FROM {table} WHERE lineage_key = ?", (key,)).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Idempotency — doc-scoped reload in FK order. NEVER touches section/document
# (upstream-owned) or the global lineage registries.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# SWAPPED CAPTION REPAIR
#
# The extractor occasionally fills `title` with the PAGE MASTHEAD (the running
# header naming the filer) and pushes the table's real caption down into
# `label_header`. Observed in DBS_4Q25_performance_summary's per-share exhibit:
#     title='DBS GROUP HOLDINGS LTD AND ITS SUBSIDIARIES'
#     label_header='Per share data ($)3,8'
# Both values are already in the cached parsed.json -- this is a field-assignment
# error, not missing data, so it is repairable at LOAD time for $0 (no
# re-extraction). Left unrepaired it costs the table its identity: `table_type`
# and `table_id` are both slugged from the title, so the registry cannot
# classify it (`table_type_id IS NULL`) and `stamp_human_anchors` -- which
# requires a table_type_id -- silently refuses to project the exhibit's
# human_confirmed anchors.
#
# The repair SWAPS the two fields rather than overwriting either, so no verbatim
# text is invented or lost: both strings survive, each in the field it belongs
# to. It is also unit-neutral by construction, because the table-default unit is
# `parse_unit(label_header) or parse_unit(title)` -- reading the same two strings
# in the other order.
#
# TWO SIGNALS, BOTH REQUIRED. Each one alone corrupts real data -- verified
# against the two other corpus tables that share the masthead shape:
#
#   (a) the title, once its OWN institution's name and corporate boilerplate are
#       removed, has NOTHING left. A caption that merely MENTIONS the filer
#       ("INDEPENDENT AUDITOR'S REPORT TO THE MEMBERS OF DBS GROUP HOLDINGS LTD
#       (continued) - Key audit matter") keeps a large residue and is NOT a
#       masthead -- signal (a) rejects it, protecting DBS_4Q25's
#       key_audit_matters table whose label_header is a legitimate short column
#       header.
#
#   (b) the label_header carries a REAL caption: alphabetic content survives
#       stripping unit/currency parentheticals and trailing footnote digits.
#       DBS_2Q25's NPA table has the same masthead title but label_header='($m)'
#       -- a bare unit banner, no caption to recover -- so signal (b) rejects it
#       and the table keeps resolving through its section slug as before.
#
# Anything that fails either signal is left EXACTLY as extracted.
# ---------------------------------------------------------------------------

#: unit / currency parentheticals: ($m), ($'000), (%), (S$), ($)
#: mirrors stage3_stamp.resolve.normalize._UNIT_RE; kept local so pass2 (upstream) does not
#: take a dependency on the mapping layer (downstream).
_CAPTION_UNIT_RE = re.compile(r"\(\s*(?:s?\$[^)]*|%|[^)]*\bmillion\b[^)]*)\s*\)", re.I)
_CAPTION_FOOTNOTE_RE = re.compile(r"[\d\s,]+$")
_CAPTION_WORD_RE = re.compile(r"[a-z0-9]+")

#: corporate boilerplate that carries no exhibit identity on its own
_MASTHEAD_BOILERPLATE = frozenset({
    "and", "its", "it", "the", "of", "subsidiary", "subsidiaries",
    "group", "holdings", "holding", "limited", "ltd", "plc", "inc",
    "incorporated", "corporation", "corp", "company", "co", "berhad", "bhd",
    "consolidated",
})


def _caption_words(text: str | None) -> list[str]:
    return _CAPTION_WORD_RE.findall((text or "").lower())


def _title_is_bare_masthead(title: str | None, institution: str | None) -> bool:
    """Signal (a): the title is FULLY explained by the filer's own name plus
    corporate boilerplate -- i.e. it identifies the company, not the exhibit."""
    words = _caption_words(title)
    if not words:
        return False
    own = set(_caption_words(institution))
    return not [w for w in words if w not in own and w not in _MASTHEAD_BOILERPLATE]


def _label_header_has_caption(label_header: str | None) -> bool:
    """Signal (b): real alphabetic content survives once unit parentheticals and
    trailing footnote markers are stripped -- so it is a caption, not a banner."""
    s = _CAPTION_UNIT_RE.sub(" ", label_header or "")
    s = _CAPTION_FOOTNOTE_RE.sub("", s)
    return bool(re.search(r"[a-z]{3,}", s, re.I))


def repair_swapped_captions(tables, institution: str | None,
                            warnings: list[str] | None = None) -> int:
    """Swap title <-> label_header on tables where the extractor put the page
    masthead in `title` and the real caption in `label_header`. Returns the
    number repaired. Mutates in place; a table failing either signal is
    untouched."""
    n = 0
    for gt in tables:
        if (_title_is_bare_masthead(gt.title, institution)
                and _label_header_has_caption(gt.label_header)):
            gt.title, gt.label_header = gt.label_header, gt.title
            n += 1
            if warnings is not None:
                warnings.append(
                    f"swapped-caption repair: title was the page masthead "
                    f"({gt.label_header!r}); real caption recovered from "
                    f"label_header -> {gt.title!r}")
    return n


def _delete_doc(cur: sqlite3.Cursor, doc_id: str) -> None:
    cur.execute("DELETE FROM cell_fact WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM row_dim   WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM col_dim   WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM table_t   WHERE doc_id = ?", (doc_id,))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _read_extraction(parsed_path: Path) -> Extraction:
    if not parsed_path.exists():
        raise FileNotFoundError(f"parsed.json not found: {parsed_path}")
    text = parsed_path.read_text()
    if not text.strip():
        raise RuntimeError(f"empty extraction artifact (0-byte): {parsed_path}")
    try:
        ext = Extraction.model_validate_json(text)
    except ValidationError as e:
        raise RuntimeError(f"parsed.json does not match Extraction: {parsed_path}\n{e}") from e
    if not ext.tables:
        raise RuntimeError(f"extraction has 0 tables: {parsed_path}")
    return ext


def _read_geometry(parsed_path: Path) -> list[dict | None]:
    """The stage1_extract.chunk.geometry side-car for one unit, as a per-table list aligned to
    `Extraction.tables`. Empty list when the unit has no side-car (a document
    extracted before the geometry stage existed, or a unit whose source PDF was
    unavailable) — the loader then falls back to model levels for every table.

    Read separately from `_read_extraction` on purpose: "geometry" is an EXTRA
    top-level key in parsed.json, deliberately outside the `Extraction` pydantic
    model, because adding fields to GRow would change the Gemini response schema
    and invalidate the whole extraction cache."""
    try:
        raw = json.loads(parsed_path.read_text())
    except Exception:
        return []
    geom = raw.get("geometry")
    if not isinstance(geom, dict):
        return []
    tables = geom.get("tables")
    return list(tables) if isinstance(tables, list) else []


def _page_range(pages: list[int]) -> str:
    ps = [int(p) for p in pages]
    return str(ps[0]) + (f"-{ps[-1]}" if len(ps) > 1 else "")


def _load_table(cur: sqlite3.Cursor, doc_id: str, doc_period: str | None,
                unit_section_id: str, pages: list[int],
                table_type_hint: str | None, gt: GTable,
                warnings: list[str],
                seg_map: dict[str, str], ind_map: dict[str, str],
                le_map: dict[str, str],
                table_geom: dict | None = None) -> tuple[int, int]:
    """Load one GTable. Returns (n_rows, n_cells)."""
    if gt.continued_from_previous:
        raise RuntimeError(
            "continued_from_previous=True reached the loader — upstream must "
            "merge continuation fragments before loading (contract violation).")

    # GEOMETRY FIRST — everything downstream (parent walk, lineage, sums, cell
    # positions) reads gt.rows, so the printed-line twin merge and the depth
    # override must happen before any of it. All-or-nothing per table; when it
    # does not apply this is a no-op and `hierarchy_source` records 'model'.
    geom = apply_geometry(gt, table_geom)
    gt = geom.table
    geom_warnings = list(geom.warnings)   # emitted once table_id is known

    # Section attribution is ROUTER knowledge: the unit's section is
    # authoritative. GTable.section_id is the model's echo (often the printed
    # note number, e.g. '1'/'2' on shared note pages) — advisory only: warn
    # when it matches neither the unit's id nor its printed section_no.
    section_id = unit_section_id
    sec = cur.execute(
        "SELECT section_no FROM section WHERE doc_id = ? AND section_id = ?",
        (doc_id, section_id)).fetchone()
    if sec is None:
        raise RuntimeError(
            f"unknown section_id {section_id!r} for doc {doc_id!r} — section is "
            f"upstream-owned and must exist before load.")
    section_no = sec[0]
    echo = gt.section_id.strip()
    if echo and echo not in (section_id, section_no or ""):
        # If the echo RESOLVES to a different existing section of this doc,
        # the model asserts the table belongs elsewhere — that section's own
        # unit is responsible for it; loading it here would double-load it
        # (boundary leak on a shared page). Skip with a warning; the
        # verify/coverage gates catch the case where the owner missed it too.
        other = cur.execute(
            "SELECT section_id FROM section WHERE doc_id = ? AND "
            "(section_id = ? OR section_no = ?)", (doc_id, echo, echo)).fetchone()
        ancestors = set()
        walk = section_id
        while walk is not None:
            ancestors.add(walk)
            row = cur.execute(
                "SELECT parent_section FROM section WHERE doc_id = ? AND section_id = ?",
                (doc_id, walk)).fetchone()
            walk = row[0] if row else None
        if other and other[0] not in ancestors:
            warnings.append(
                f"{section_id}: LEAKED table {gt.title!r} skipped — echo {echo!r} "
                f"resolves to section {other[0]!r}, which owns it")
            return 0, 0
        warnings.append(
            f"{section_id}: GTable.section_id echo {echo!r} != unit section "
            f"(id {section_id!r}, no {section_no!r}) — unit wins")

    table_type = table_type_hint or slug(gt.title)

    # table-DEFAULT unit: label_header first (the row-label column's '($m)' /
    # 'In $ millions' banner), then the title ('Key financial ratios (%)').
    # Explicit row/col markers override it in the v_cell/v_cell_flat unit CASE.
    table_unit = parse_unit(gt.label_header) or parse_unit(gt.title)

    # --- columns: leaves 1..N; period-axis expressions -> col_period/span/start,
    # lineage='value'. Group banners (span headers) take out-of-band col_id 100+
    # (no cell_fact ref). Each leaf carries a (period, span, start) triple:
    # period = ISO END date; span = printed duration token (2H / FY / 1H / nQ /
    # 9M / as_at); start = calendar-fiscal period START (NULL for as_at). This
    # DISTINGUISHES 2H25 (2025-12-31, span 2H) from FY2025 (2025-12-31, span FY),
    # which collide on the end date alone (defect b).
    leaf_cols: list[tuple[int, GColumn, str | None, str | None, str | None]] = []
    group_ids: dict[str, int] = {}
    next_group_id = 100
    unstamped_period_candidate_labels: list[str] = []  # GATE — see below, emitted once table_id is known
    bare_year_clamped: list[str] = []                  # bare-year columns re-read against doc_period
    for idx, col in enumerate(gt.columns, start=1):
        if col.group and col.group not in group_ids:
            group_ids[col.group] = next_group_id
            next_group_id += 1
        # Period axis (site a + b): a period-expression GROUP banner stamps every
        # leaf under it (period-axis, excluded from lineage); a leaf's OWN explicit
        # date/period takes precedence over its group banner. COLUMN context: a
        # bare-year banner ('2025' / '2024') and month-year forms ARE periods here
        # (defect a — was refused, so leaves kept '2025 > $m' and cells fell to the
        # doc default, mis-stamping the FY2024 column).
        group_ps = (parse_period_span(col.group, column=True)
                    if col.group and is_period_text(col.group, column=True) else None)
        leaf_ps = (parse_period_span(col.leaf, column=True)
                   if is_period_text(col.leaf, column=True) else None)
        ps = leaf_ps or group_ps
        cp, csp, cst = (ps[0], ps[1], ps[2]) if ps else (None, None, None)
        # A bare-year column resolves to 31 December, which is a FUTURE date in
        # an interim filing — see clamp_bare_year_to_doc_period.
        cp, csp, cst, _clamped = clamp_bare_year_to_doc_period(
            col.leaf if leaf_ps else (col.group or ""), cp, csp, cst, doc_period)
        if _clamped:
            bare_year_clamped.append(col.leaf if leaf_ps else (col.group or ""))
        leaf_cols.append((idx, col, cp, csp, cst))
        # GATE A2 — a leaf column that LOOKS period-shaped (its label carries a
        # 4-digit year) but got NO col_period is a grammar miss (the class of
        # bug this file's period grammar exists to catch): every such column
        # falls through to the table/doc default period, silently mis-stamping
        # it. Excludes 'chg'/'change' columns (a comparison banner like '% chg
        # vs 1Q 2025' legitimately carries no period of its own — see
        # is_period_text's residual guard). ARMED (was advisory-only): a
        # period-shaped column with no period is not a legitimate document
        # shape in this corpus (unlike a table whose OWN title deliberately
        # names a comparative year, which the table-level gate below excludes
        # on purpose) — it is always evidence of a grammar gap, so it fails
        # the load rather than silently mis-stamping the column to the
        # table/doc default.
        if (cp is None and re.search(r"\d{4}", col.leaf)
                and not re.search(r"chg|change", col.leaf, re.I)):
            unstamped_period_candidate_labels.append(col.leaf)

    # SHARED-RESIDUAL RESCUE (before the gate fires). is_period_text refuses a
    # leaf whose period match leaves non-boilerplate text behind, so a
    # DESCRIPTIVE header carrying an incidental year ('Note 3 2025') is not
    # mistaken for a period axis. But an extractor sometimes FLATTENS a two-level
    # header into the leaf: DBS 2Q25 'By collateral type' emits
    # leaf='30 Jun 2025 NPA' with group=None, while its sibling tables in the
    # same audit unit keep the same header two-level (group='30 Jun 2025',
    # leaf='NPA ($m)'). There the residual 'NPA' is a MEASURE token that belongs
    # on the leaf axis, not evidence that the column is non-periodic.
    #
    # DISCRIMINATOR: every period-candidate leaf in the table leaves the SAME
    # residual. A real descriptive header does not repeat itself across the
    # period axis ('Note 3 2025' / 'Note 4 2024' differ); a flattened measure
    # token does, because it labels the axis. Generalises the existing
    # _strip_units rescue ('2025 $m' -> residual '$m' -> stripped -> period) to
    # any repeated token, with no term list and no per-bank branch.
    if unstamped_period_candidate_labels:
        residuals, rescued = set(), {}
        for idx, col, cp, csp, cst in leaf_cols:
            if cp is not None or col.leaf not in unstamped_period_candidate_labels:
                continue
            t = _strip_col_footnotes(_norm(col.leaf))
            m = _period_match_ctx(t, True)
            if not m:
                residuals.add(None)
                continue
            residuals.add(_strip_units((t[:m[2]] + t[m[3]:]).strip().lower()))
            rescued[idx] = (m[0], m[1], _span_start(m[1], m[0]))
        if (len(residuals) == 1 and None not in residuals
                and len(rescued) == len(unstamped_period_candidate_labels)
                and next(iter(residuals))):
            warnings.append(
                f"{section_id}/{gt.title!r}: period-candidate leaves share residual "
                f"{next(iter(residuals))!r} — treated as a flattened measure "
                f"token on the period axis, columns stamped")
            leaf_cols = [(idx, col, *rescued[idx]) if idx in rescued
                         else (idx, col, cp, csp, cst)
                         for idx, col, cp, csp, cst in leaf_cols]
            unstamped_period_candidate_labels = []

    # table_t.period: NULL iff every leaf carries col_period (schema NOTE B).
    all_cols_have_period = bool(leaf_cols) and all(cp for _, _, cp, _, _ in leaf_cols)
    # site c: table-title period — general period expression, not just a bare date
    # (fixes 'Selected income statement items 1st Half 2025' geography instances).
    # Title context KEEPS the bare-year guard (column=False) except for a trailing
    # '— YYYY' caption.
    title_ps = parse_period_span(gt.title, column=False)
    # SAME INVARIANT AS THE COLUMN AXIS: no filing reports a period ending after
    # its own reporting date. A trailing '— 2026' caption on an INTERIM filing
    # resolves to 31 Dec 2026, a date that has not happened yet, so it is re-read
    # against the document's cycle exactly as a bare-year column is. Only the
    # bare-year branch can do this — an explicitly printed title date is left
    # alone, since it says what it means.
    if title_ps:
        _ty = _TITLE_TRAILING_YEAR_RX.search(_norm(gt.title))
        if _ty:
            _p, _s, _st, _clamped = clamp_bare_year_to_doc_period(
                _ty.group(1), title_ps[0], title_ps[1], title_ps[2], doc_period)
            if _clamped:
                warnings.append(
                    f"{table_id}: title-trailing bare year {_ty.group(1)} ends after "
                    f"doc_period {doc_period} — clamped to {_p}/{_s}")
                title_ps = (_p, _s, _st)
    title_period = title_ps[0] if title_ps else None
    max_col_period = max((cp for _, _, cp, _, _ in leaf_cols if cp), default=None)
    # table_period_source feeds cell_fact.period_source (site 'table_title') when
    # a cell falls back to the table-level period — distinguished from 'doc' so a
    # cell whose date came from an explicit table title (a deliberate comparative
    # exhibit) is never confused with one that just inherited the document
    # default because nothing more specific was available.
    if all_cols_have_period:
        table_period = table_span = table_start = None
        table_period_source = None
    elif title_ps:
        table_period, table_span, table_start = title_ps
        table_period_source = "table_title"
    else:
        table_period, table_span, table_start = doc_period, None, None
        table_period_source = "doc"

    # table_id carries a period token even when table_t.period is NULL (column-
    # period table): title date > latest col_period > doc_period. Same template +
    # different reporting date => a distinct table_t row.
    id_token = title_period or max_col_period or doc_period or "na"
    table_id = f"{section_id}_{table_type}_{id_token}"

    # GATE A2 — ARMED: a period-shaped column label with no resolved period is a
    # grammar miss, not a legitimate shape (see the in-loop comment above). Fails
    # the whole table load rather than letting every such column silently fall
    # through to the table/doc default period.
    if unstamped_period_candidate_labels:
        # TABLE-SCOPED, not document-scoped. The gate's judgement is unchanged —
        # a period-shaped column that yields no period is a grammar miss and the
        # table must NOT load with a silently defaulted period. What changes is
        # the blast radius: this used to `raise`, and load_units wraps the whole
        # document in `except: con.rollback(); raise`, so one unparseable column
        # label discarded every table in the document. DBS 2Q25 (37 tables) and
        # 3Q25 (4 tables) were both lost this way to a single '9 Mths 2025'
        # column. Returning None skips THIS table before any table_t/row_dim/
        # cell_fact write, and the rest of the document loads.
        warnings.append(
            f"table {table_id}: period-looking column(s) yielded no period: "
            f"{unstamped_period_candidate_labels!r} — period grammar gap, not a "
            f"silent default; TABLE SKIPPED (document continues)")
        return None
    for w in geom_warnings:
        warnings.append(f"{table_id}: geometry — {w}")

    cur.execute(
        "INSERT INTO table_t(doc_id,table_id,table_title,table_title_clean,"
        "table_type,section_id,section_no,period,period_span,period_start,"
        "page_range,unit,hierarchy_source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, table_id, gt.title, geom.title_clean, table_type, section_id,
         section_no, table_period, table_span, table_start,
         _page_range(pages), table_unit,
         "geometry" if geom.applied else "model"))

    # GATE A3 — period consistency. ARMED, with one deliberate carve-out: if
    # doc_period is neither among the table's column periods nor equal to the
    # table period, the doc-level "as at" date disagrees with what this table
    # actually reports. That is a real defect UNLESS the table's OWN title
    # names an explicit, different reporting date on purpose — a comparative
    # exhibit (e.g. "Statement of Changes in Equity ... 31 December 2024" inside
    # a 2025 doc) is a legitimate, observed shape in this corpus (DBS 4Q25's own
    # prior-year equity-changes tables), not a mis-stamp; table_period_source
    # distinguishes an explicit title date from a bare doc_period fallback. Only
    # the fallback case — columns disagree with doc_period AND the table has no
    # independent title-based dating of its own — fails the load; that
    # combination has no legitimate explanation and is exactly the silent
    # mis-stamp this gate exists to catch (e.g. the UOB duplicate-period-stamp
    # case this pass was written to find).
    if bare_year_clamped:
        warnings.append(
            f"{table_id}: bare-year label(s) {sorted(set(bare_year_clamped))} "
            f"resolved past the reporting date and were clamped to doc_period "
            f"{doc_period} — interim filing, a bare year is not 31 December here")

    if doc_period is not None:
        col_periods = sorted({cp for _, _, cp, _, _ in leaf_cols if cp})
        table_periods = col_periods or ([table_period] if table_period else [])
        if doc_period not in col_periods and doc_period != table_period:
            if table_period_source == "table_title":
                warnings.append(
                    f"{table_id}: doc_period {doc_period} not among table periods "
                    f"{table_periods} — advisory, table has its own explicit title date")
            else:
                raise RuntimeError(
                    f"table {table_id}: doc_period {doc_period} not among table periods "
                    f"{table_periods}, and the table has no independent title date of its "
                    f"own to explain the disagreement — period mis-stamp, not a comparative "
                    f"exhibit")

    # AXIS EXCLUSIVITY (spec §5): resolve geo/segment/industry for the WHOLE
    # column axis of this table (hierarchy-0 banners + hierarchy-1 leaves) in one
    # pool, BEFORE any col_dim row is inserted — an ambiguous label's dominant
    # axis depends on how many OTHER columns on this axis unambiguously matched
    # each candidate axis, so every column must be looked up first.
    group_labels = list(group_ids.keys())
    leaf_labels = [col.leaf for _, col, *_ in leaf_cols]
    col_resolved, col_axis_warnings = resolve_axis_labels(
        group_labels + leaf_labels, seg_map, ind_map)
    banner_resolved = dict(zip(group_labels, col_resolved[:len(group_labels)]))
    leaf_resolved = col_resolved[len(group_labels):]
    warnings.extend(f"{table_id}: {w}" for w in col_axis_warnings)

    # group-header (hierarchy-0 span) col_dim rows — now that table_id is known. A
    # period group banner carries its own period/span/start (col groups, spec §2).
    for group_label, gid in group_ids.items():
        g_hdr = _get_or_create_header(cur, "col_lineage", "col_lineage_id",
                                      [_clean_label(group_label)])
        g_ps = (parse_period_span(group_label, column=True)
                if is_period_text(group_label, column=True) else None)
        g_period, g_span, g_start = g_ps if g_ps else (None, None, None)
        g_stamp = banner_resolved[group_label]
        g_le = le_lookup(group_label, None, le_map)
        cur.execute(
            "INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
            "col_leaf_label,col_leaf_label_clean,col_period,period_span,period_start,"
            "segment_key,industry_key,legal_entity,col_lineage_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, table_id, gid, 0, None, group_label, None, g_period, g_span,
             g_start, g_stamp["segment"], g_stamp["industry"], g_le, g_hdr))

    # invariant assertion (schema NOTE B / spec §2)
    if table_period is None and not all_cols_have_period:
        raise RuntimeError(
            f"table {table_id}: period is NULL but not every leaf column carries "
            f"col_period (period invariant violated).")

    col_hdr_by_id: dict[int, int] = {}
    col_period_by_id: dict[int, str | None] = {}
    col_span_by_id: dict[int, str | None] = {}   # leaf col_id -> printed span, PAIRED with col_period
    col_unit_by_id: dict[int, str | None] = {}
    col_seg_by_id: dict[int, str | None] = {}     # leaf col_id -> segment_key (reconcile)
    n_seg_member_cols = 0          # leaf cols stamped a segment MEMBER (!= SEG_TOTAL)
    unstamped_seg_candidate_cols: list[str] = []  # non-period, unit-less, unstamped
    col_ind_by_id: dict[int, str | None] = {}     # leaf col_id -> industry_key (reconcile)
    n_ind_member_cols = 0          # leaf cols stamped an industry MEMBER (!= IND_TOTAL)
    unstamped_ind_candidate_cols: list[str] = []  # non-period, unit-less, non-seg, unstamped
    col_le_by_id: dict[int, str | None] = {}      # leaf col_id -> legal_entity (cell materialisation)
    for pos, (col_id, col, col_period, col_span, col_start) in enumerate(leaf_cols):
        parent = group_ids.get(col.group) if col.group else None
        hdr = _get_or_create_header(cur, "col_lineage", "col_lineage_id", col_lineage(col))
        col_hdr_by_id[col_id] = hdr
        col_period_by_id[col_id] = col_period
        col_span_by_id[col_id] = col_span
        # col_dim.unit: EXPLICIT marker (group+leaf), else NULL. A pure-date or
        # pure-period leaf carries no unit token, so parse_unit returns None for it
        # naturally — but a COMBINED period+unit leaf ('2025 $m') is BOTH a period
        # axis (col_period, above) AND unit-bearing (S$m); parse_unit keeps the
        # unit while col_lineage still drops the period text. (No is_period_text
        # gate here: it would strip the unit off a footnoted/bare-year period col.)
        col_unit = parse_unit(f"{col.group or ''} {col.leaf}")
        col_unit_by_id[col_id] = col_unit
        stamp = leaf_resolved[pos]     # AXIS-EXCLUSIVITY-RESOLVED segment/industry
        col_seg = stamp["segment"]     # segment axis in COLUMNS (all 3 banks)
        col_seg_by_id[col_id] = col_seg
        if col_seg and col_seg != "SEG_TOTAL":
            n_seg_member_cols += 1
        elif (col_seg is None and col_period is None and col_unit is None
              and seg_norm(col.leaf) not in ("total", "group", "bank", "")):
            unstamped_seg_candidate_cols.append(col.leaf)
        col_ind = stamp["industry"]    # industry axis in COLUMNS (mirror of segment)
        col_ind_by_id[col_id] = col_ind
        if col_ind and col_ind != "IND_TOTAL":
            n_ind_member_cols += 1
        elif (col_ind is None and col_period is None and col_unit is None
              and not col_seg
              and ind_norm(col.leaf) not in ("total", "group", "bank", "")):
            unstamped_ind_candidate_cols.append(col.leaf)
        # legal_entity: own leaf label wins (a leaf can say 'The Group' directly,
        # no banner), else the parent group banner ('The Group' / 'The Company' /
        # 'The Bank' spanning several period columns — the shape all three banks
        # actually use). None if neither matches; materialises to the
        # CONSOLIDATED default on cell_fact, same precedence as the migration.
        col_le = le_lookup(col.leaf, col.group, le_map)
        col_le_by_id[col_id] = col_le
        cur.execute(
            "INSERT INTO col_dim(doc_id,table_id,col_id,col_hierarchy,col_parent,"
            "col_leaf_label,col_leaf_label_clean,col_period,period_span,period_start,"
            "unit,segment_key,industry_key,legal_entity,col_lineage_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            # col_leaf_label_clean is STORED but deliberately NOT fed into
            # col_lineage / the column axis lookups: column identity already has
            # its own footnote handling (_COL_FOOTNOTE) tuned around combined
            # period+unit headers ('2H25¹ $m'), and the mis-nesting defect this
            # stage exists to fix lives entirely on the ROW axis. Revisit only
            # with column-side evidence.
            (doc_id, table_id, col_id, 1, parent, col.leaf,
             geom.col_labels_clean[col_id - 1], col_period, col_span,
             col_start, col_unit, col_seg, col_ind, col_le, hdr))

    # --- rows: row_id = 1-based enumeration; printed GRow.row_id -> line_no.
    rows = gt.rows
    # Geometric depths make the total/note skip harmful rather than protective —
    # see row_parents_by_position. `labels_clean` is per-row and index-aligned to
    # `rows` (apply_geometry returns them together); all-None when it fell back.
    labels_clean = geom.row_labels_clean
    col_units_by_pos = [col_unit_by_id.get(pos + 1) for pos in range(len(leaf_cols))]
    # sums BEFORE parents: the header-vs-terminal discriminator in
    # row_parents_by_position needs to know which totals actually aggregate.
    sums_map, sign_map, sum_warnings = verified_sums_to(rows, len(leaf_cols), col_units_by_pos)
    warnings.extend(f"{table_id} {w}" for w in sum_warnings)
    printed_parents = resolve_printed_parents(rows)
    parents = row_parents_by_position(rows, skip_terminal=not geom.applied,
                                      sums_to=sums_map, printed=printed_parents)
    # Sibling period banners — the rung the ancestor walk structurally cannot
    # reach. Index-aligned to `rows`; consumed in the cell loop below.
    banner_by_pos = row_period_banners(rows, doc_period)
    # A banner outranks the table title, so where the two DISAGREE the title's
    # answer is being overridden. That is intended (the banner is printed closer
    # to the rows it scopes), but it is the one place this rung changes an
    # already-resolved period rather than filling a gap — so say so out loud
    # instead of letting it be discovered later in a dashboard.
    if table_period is not None and table_period_source == "table_title":
        _clash = sorted({b[0] for b in banner_by_pos if b and b[0] != table_period})
        if _clash:
            warnings.append(
                f"{table_id}: row banner(s) {_clash} disagree with the title "
                f"period {table_period} — banner wins for the rows they scope")

    # Unit-conflict advisory: a row marker and a column marker that both fire,
    # differ, and neither is '%' (a '%' row deliberately wins). Warn ONCE per
    # distinct (row_unit, col_unit) pair in this table; the CASE resolves to col.
    distinct_col_units = {u for u in col_unit_by_id.values() if u}
    warned_unit_pairs: set[tuple[str, str]] = set()
    # Printed-parent cross-check — suppressed under geometry: GRow.parent is the
    # model's echo of its OWN level scheme, so on a geometry-corrected table it
    # disagrees by construction and would emit a warning per reparented row.
    # The printed parent is now CONSUMED (resolve_printed_parents), not merely
    # cross-checked — it is the extractor's own statement of the hierarchy and is
    # right where the position rule is wrong. Warn only when the extractor gave a
    # reference we could NOT resolve, so position silently decided instead.
    if not geom.applied:
        for i, r in enumerate(rows):
            if isinstance(r.parent, str) and i not in printed_parents:
                warnings.append(
                    f"{table_id} row {i + 1} ({r.label!r}): printed parent "
                    f"{r.parent!r} did not resolve to a shallower header "
                    f"(position used instead)")

    # AXIS EXCLUSIVITY (spec §5): resolve geo/segment/industry for the WHOLE row
    # axis of this table in one pool, BEFORE any row_dim row is inserted — mirror
    # of the column-axis resolution above. Fed the geometry-cleaned label where
    # there is one: the maps are keyed on exact normalised full-label equality,
    # so a footnote marker printed inside the label ('Others2' in UOB's NPL-by-
    # industry table) silently loses the stamp entirely.
    row_resolved, row_axis_warnings = resolve_axis_labels(
        [(labels_clean[i] or r.label) for i, r in enumerate(rows)],
        seg_map, ind_map)
    warnings.extend(f"{table_id}: {w}" for w in row_axis_warnings)

    row_hdr_by_rid: dict[int, int] = {}
    row_seg_by_rid: dict[int, str | None] = {}   # for the segment drift check below
    row_ind_by_rid: dict[int, str | None] = {}   # for the industry drift check below
    row_period_by_rid: dict[int, str | None] = {}   # ROW-axis period, PAIRED with span
    row_span_by_rid: dict[int, str | None] = {}
    n_cells = 0
    for i, r in enumerate(rows):
        row_id = i + 1
        row_parent = (parents[i] + 1) if parents[i] is not None else None
        hdr = _get_or_create_header(cur, "row_lineage", "row_lineage_id",
                                    row_lineage(rows, parents, i, labels_clean))
        row_hdr_by_rid[row_id] = hdr
        # ROW-axis period (mirror of the leaf-column period): parse the row label
        # with the SAME column-context grammar + residual guard so a period ROW
        # ('Dec-25'/'Jun-25'/'Dec-24', UOB NPL tables) yields (row_period, span,
        # start) while a descriptive row carrying an incidental date is refused
        # ('Balance at 1 January 2025' -> residual 'balance at' fails the whitelist).
        row_ps = _row_period_ps(r.label)
        row_period, row_span, row_start = row_ps if row_ps else (None, None, None)
        # A bare-year ROW banner ('2026') resolves to 31 December, a FUTURE date
        # in an interim filing — the same defect the column axis clamps at the
        # leaf/group parse and the title parse. Left unclamped here, two '2026'
        # changes-in-equity banners put 141 cells at 2026-12-31 in 2Q26 filings,
        # i.e. later than their own doc_period.
        row_period, row_span, row_start, _row_clamped = clamp_bare_year_to_doc_period(
            r.label, row_period, row_span, row_start, doc_period)
        if _row_clamped:
            bare_year_clamped.append(r.label)
        row_period_by_rid[row_id] = row_period
        row_span_by_rid[row_id] = row_span
        # INHERITANCE (year section headers '2025'/'2024' with line items nested
        # under them): a row with NO own period inherits the row_period of its
        # NEAREST ANCESTOR that IS a period row. Own parse wins over the ancestor's.
        # Ancestors have a LOWER index (already processed) so their own parse is
        # available. This is a CELL-RESOLUTION concept ONLY — row_dim.row_period and
        # the lineage exclusion store/act on the OWN parse alone; inheriting children
        # are real line items (row_period stays NULL, real lineage kept).
        #
        # This walk handles the NESTED topology ONLY. It used to claim that
        # "sibling year blocks are naturally isolated by the parent chain" — true,
        # but it silently assumed the banner is always an ANCESTOR. When a table
        # stacks period blocks vertically the model emits the banner at the SAME
        # level as its rows, they share one parent, and this walk finds nothing.
        # `banner_by_pos` (row_period_banners) is the rung that covers that shape;
        # it is consulted BELOW this one, so an ancestor still wins when both fire.
        eff_row_period, eff_row_span = row_period, row_span
        if eff_row_period is None:
            a = parents[i]
            while a is not None:
                if row_period_by_rid.get(a + 1) is not None:
                    eff_row_period = row_period_by_rid[a + 1]
                    eff_row_span = row_span_by_rid[a + 1]
                    break
                a = parents[a]
        row_stamp = row_resolved[i]   # AXIS-EXCLUSIVITY-RESOLVED segment/industry
        row_seg = row_stamp["segment"]  # segment axis in ROWS (if it varies there)
        row_seg_by_rid[row_id] = row_seg
        row_ind = row_stamp["industry"]  # industry axis in ROWS ('NPL/NPA by industry')
        row_ind_by_rid[row_id] = row_ind
        row_unit = parse_row_label_unit(r.label)   # EXPLICIT marker only (e.g. a
        # '% change' row); the coupon-in-name guard drops a rate printed in the row
        # name ('3.58% ... perpetual capital securities') so it does not mis-stamp '%'.
        if row_unit:
            for cu in distinct_col_units:
                if cu != row_unit and row_unit != "%" and cu != "%" \
                        and (row_unit, cu) not in warned_unit_pairs:
                    warned_unit_pairs.add((row_unit, cu))
                    warnings.append(
                        f"{table_id}: unit conflict — row unit {row_unit!r} vs col "
                        f"unit {cu!r} (neither '%'); per-cell CASE resolves to the column")
        cur.execute(
            "INSERT INTO row_dim(doc_id,table_id,row_id,row_hierarchy,row_parent,"
            "row_leaf_label,row_leaf_label_clean,row_period,period_span,period_start,"
            "segment_key,industry_key,line_no,unit,row_lineage_id,sums_to,"
            "sums_sign) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, table_id, row_id, r.level, row_parent, r.label, labels_clean[i],
             row_period, row_span, row_start, row_seg, row_ind, r.row_id,
             row_unit, hdr, sums_map.get(row_id), sign_map.get(row_id)))

        # section_header / sub_header / note rows carry NO cells (spec §1).
        if r.row_type in ("section_header", "sub_header", "note"):
            continue
        # WIDTH OVERFLOW IS ROW-SCOPED, NOT UNIT-SCOPED. A row with more cells
        # than the table declares columns cannot be placed on the column axis, so
        # none of its cells are loaded — but the rest of the table, and the rest
        # of the audit unit, load normally.
        #
        # This used to `raise`, which propagated out of the per-table loop and
        # killed the WHOLE audit unit. Observed cost: OCBC 4Q25 media release,
        # 'FINANCIAL HIGHLIGHTS (continued)' row 4 ('Total income') emitted 6
        # cells against 5 declared columns — one duplicated trailing '%' — and
        # took `Key Financial Ratios`, `NET INTEREST INCOME — Average Balance
        # Sheet` and `Volume and Rate Analysis` down with it (9 of 12 tables
        # reached table_t; the load was left PARTIAL, not rolled back).
        #
        # The row is skipped whole rather than truncated to the declared width:
        # truncating assumes the surplus cell is at the END. A spurious LEADING
        # cell would shift every value one column left and load wrong numbers
        # under right-looking headers — silent corruption, which is worse than a
        # missing row. The missing figure stays recoverable downstream (this
        # 'Total income' is NII + non-interest income).
        if len(r.values) > len(leaf_cols):
            warnings.append(
                f"{table_id} row {row_id} ({r.label!r}): row width "
                f"{len(r.values)} > {len(leaf_cols)} declared columns — row's "
                f"cells SKIPPED (table and unit continue)")
            continue
        for pos, cell in enumerate(r.values):
            col_id = pos + 1
            if col_id not in col_hdr_by_id:
                warnings.append(
                    f"{table_id} row {row_id}: cell {col_id} has no leaf column "
                    f"— cell skipped")
                continue
            is_shade = 1 if cell.cell_state == "grey" else 0
            state, num = parse_value(cell.value)
            expected = {"nil": "null", "zero": "zero", "empty": "empty",
                        "reported": "reported"}.get(cell.cell_state)
            if expected and expected != state:
                warnings.append(
                    f"{table_id} r{row_id}c{col_id}: GCell.cell_state "
                    f"{cell.cell_state!r}->{expected} disagrees with value token "
                    f"{state!r} for {cell.value!r} (value token wins)")
            # Period resolves per cell (col > ROW > table > doc); the SPAN is
            # resolved the SAME way and kept PAIRED — the span of whichever axis
            # won its period (col span for a col period, row span for a row period,
            # table span for the table period; doc_period fallback carries no
            # printed duration -> NULL). Never mix a period from one axis with a
            # span from another. Self-describing, same as unit. If BOTH axes carry a
            # period and they DIFFER, col wins and a warning flags the clash.
            # `bp` (sibling banner) is kept in its OWN variable, never folded into
            # `rp`: the both-axes warning above compares a col period against the
            # row's own/ancestor period, and folding an inherited banner into it
            # would change what that warning means and start firing on every
            # banner table. It ranks BELOW the row axis and ABOVE table_title.
            cp, rp = col_period_by_id[col_id], eff_row_period
            bp = banner_by_pos[i]
            if cp is not None:
                period, period_span, period_source = cp, col_span_by_id[col_id], "col"
                if rp is not None and rp != cp:
                    warnings.append(
                        f"{table_id} r{row_id}c{col_id}: period on both axes "
                        f"(col {cp} vs row {rp}) — col wins")
            elif rp is not None:
                period, period_span, period_source = rp, eff_row_span, "row"
            elif bp is not None:
                period, period_span, period_source = bp[0], bp[1], "row_banner"
            elif table_period is not None:
                period, period_span, period_source = table_period, table_span, table_period_source
            else:
                period, period_span, period_source = doc_period, None, "doc"
            if period is None:
                raise RuntimeError(
                    f"{table_id} r{row_id}c{col_id}: could not resolve a period "
                    f"(no col_period, no table period, no doc_period).")
            # Per-cell unit, chain steps (1)-(5); step (6) doc-default + the
            # NULL warning are applied doc-wide in load_units after all tables.
            cell_unit = resolve_cell_unit(
                cell.value, row_unit, col_unit_by_id.get(col_id), table_unit)
            # EFFECTIVE segment/industry MATERIALISED onto the fact (self-describing,
            # same as unit/period_span). Precedence MATCHES the v_cell/v_cell_flat
            # COALESCE EXACTLY: segment = row > col > 'SEG_TOTAL'; industry =
            # row > col > 'IND_TOTAL' (member from row XOR col). Reuses the already-
            # computed per-row/per-col stamps — no re-query, so cell_fact equals the
            # view value cell-for-cell. (geo_key is no longer written: geography
            # stamping retired 2026-08-12, the column stays and is simply NULL.)
            cell_seg = (row_seg_by_rid[row_id] or col_seg_by_id.get(col_id)
                        or "SEG_TOTAL")
            cell_ind = (row_ind_by_rid[row_id] or col_ind_by_id.get(col_id)
                        or "IND_TOTAL")
            # legal_entity is a COLUMN-only axis (no row-level concept exists for
            # it, unlike segment/industry) — col wins, else CONSOLIDATED.
            # Matches migrate_add_legal_entity.py's cascade exactly so a reload
            # reproduces what the migration used to backfill out-of-band.
            cell_le = col_le_by_id.get(col_id) or DEFAULT_LEGAL_ENTITY
            cur.execute(
                "INSERT INTO cell_fact(doc_id,table_id,row_id,col_id,colspan,value_raw,"
                "value_num,unit,cell_state,is_shade,period,period_span,period_source,"
                "segment_key,industry_key,legal_entity,row_lineage_id,col_lineage_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, table_id, row_id, col_id, 1, cell.value, num, cell_unit, state,
                 is_shade, period, period_span, period_source, cell_seg, cell_ind,
                 cell_le, row_hdr_by_rid[row_id], col_hdr_by_id[col_id]))
            n_cells += 1

    # (GEO DRIFT removed 2026-08-12 with geography stamping — it warned about
    # labels that geo_map did not cover, and there is no geo_map any more.)
    _SKIP = {"total", "note", "section_header", "sub_header"}
    data_rows = [i for i, r in enumerate(rows) if r.row_type not in _SKIP]

    # SEGMENT DRIFT — majority-gate rule. Segments
    # are overwhelmingly a COLUMN axis in this corpus, but both axes are covered
    # for generality. MEMBER stamps only (SEG_TOTAL excluded) drive the gate, so a
    # geo table whose 'Others'/'Total' incidentally carry SEG_OTHER/SEG_TOTAL never
    # trips it (needs >=2 member columns / a majority-segment row axis).
    n_seg_rows = sum(1 for s in row_seg_by_rid.values() if s and s != "SEG_TOTAL")
    if data_rows and n_seg_rows >= 2 and n_seg_rows / len(data_rows) >= 0.5:
        for i in data_rows:
            s = row_seg_by_rid.get(i + 1)
            if not s or s == "SEG_TOTAL":
                warnings.append(
                    f"{table_id}: possible unmapped segment label "
                    f"{rows[i].label!r} — extend segment_map if it is one")
    if n_seg_member_cols >= 2:
        # column-segment table (all 3 banks): a novel business line would be an
        # unstamped NON-PERIOD, unit-less, non-geo column, not a row.
        for c in unstamped_seg_candidate_cols:
            warnings.append(
                f"{table_id}: possible unmapped segment column {c!r} "
                f"— extend segment_map if it is one")

    # INDUSTRY DRIFT — mirror of the segment drift, SAME majority-gate rule. NPL/NPA
    # by-industry tables are overwhelmingly a ROW axis in this corpus, but both axes
    # are covered for generality. MEMBER stamps only (IND_TOTAL excluded) drive the
    # gate, so a geo/segment table whose 'Others'/'Total' incidentally carries
    # IND_OTHERS/IND_TOTAL never trips it (needs >=2 member rows / columns).
    n_ind_rows = sum(1 for v in row_ind_by_rid.values() if v and v != "IND_TOTAL")
    if data_rows and n_ind_rows >= 2 and n_ind_rows / len(data_rows) >= 0.5:
        for i in data_rows:
            v = row_ind_by_rid.get(i + 1)
            if not v or v == "IND_TOTAL":
                warnings.append(
                    f"{table_id}: possible unmapped industry label "
                    f"{rows[i].label!r} — extend industry_map if it is one")
    if n_ind_member_cols >= 2:
        # column-industry table: a novel industry would be an unstamped
        # NON-PERIOD, unit-less, non-geo, non-segment column, not a row.
        for c in unstamped_ind_candidate_cols:
            warnings.append(
                f"{table_id}: possible unmapped industry column {c!r} "
                f"— extend industry_map if it is one")

    # COLUMN-SUM RECONCILIATION (warning gate; segment + geo + industry). Only
    # fires where a dimension genuinely partitions across columns (>=2 member
    # cols + a default-member total col); every other table is silently skipped.
    # Uses the AXIS-EXCLUSIVITY-RESOLVED per-column stamps (col_*_by_id), not a
    # raw re-lookup, so reconciliation never contradicts what was materialised.
    seg_by_pos = [col_seg_by_id.get(pos + 1) for pos in range(len(leaf_cols))]
    ind_by_pos = [col_ind_by_id.get(pos + 1) for pos in range(len(leaf_cols))]
    col_labels_by_pos = [col.leaf for _, col, *_ in leaf_cols]
    _column_sum_reconcile(cur, doc_id, table_id, rows, seg_by_pos,
                          ind_by_pos, col_labels_by_pos, col_units_by_pos, warnings)
    return len(rows), n_cells


def _apply_document_default_unit(cur: sqlite3.Cursor, doc_id: str,
                                 warnings: list[str]) -> None:
    """BOTTOM of the per-cell unit chain (step 6). After every table of the doc
    is mapped, cells still NULL (unresolved by chain steps 1-5) fall back to the
    DOCUMENT DEFAULT: the MODAL non-NULL table_t.unit across the doc's tables.
    A tie (no strict single mode) -> NO default (those cells stay NULL). The doc
    default is stored nowhere (it is derivable; the per-table warning is its only
    provenance):
      * every table that used the doc default for >=1 cell gets ONE warning
        f\"{table_id}: unit from document default {unit!r} (modal across N/M tables)\".
      * every table with cells STILL NULL afterwards gets ONE warning
        f\"{table_id}: N cells with unresolvable unit\"."""
    units = [u for (u,) in cur.execute(
        "SELECT unit FROM table_t WHERE doc_id = ?", (doc_id,)).fetchall()
        if u is not None]
    m_total = len(units)

    doc_default: str | None = None
    if units:
        counts: dict[str, int] = {}
        for u in units:
            counts[u] = counts.get(u, 0) + 1
        top = max(counts.values())
        modal = [u for u, c in counts.items() if c == top]
        if len(modal) == 1:            # strict single mode; a tie -> no default
            doc_default = modal[0]
            n_modal = top

    if doc_default is not None:
        # tables with >=1 currently-NULL cell will consume the doc default
        to_fill = cur.execute(
            "SELECT table_id, COUNT(*) FROM cell_fact WHERE doc_id = ? AND unit IS NULL "
            "GROUP BY table_id ORDER BY table_id", (doc_id,)).fetchall()
        cur.execute(
            "UPDATE cell_fact SET unit = ? WHERE doc_id = ? AND unit IS NULL",
            (doc_default, doc_id))
        for table_id, _n in to_fill:
            warnings.append(
                f"{table_id}: unit from document default {doc_default!r} "
                f"(modal across {n_modal}/{m_total} tables)")

    # anything still NULL is genuinely unknowable -> one warning per table
    for table_id, n in cur.execute(
            "SELECT table_id, COUNT(*) FROM cell_fact WHERE doc_id = ? AND unit IS NULL "
            "GROUP BY table_id ORDER BY table_id", (doc_id,)).fetchall():
        warnings.append(f"{table_id}: {n} cells with unresolvable unit")


# A column that RESTATES other columns rather than reporting a fact. Marked
# 'derived_skip' so it is never ingested as a period fact. Kept beside the
# loader (not in the masterlist package) because it is masterlist-independent.
_DERIVED_COL_RX = re.compile(
    r"(\+\s*/\s*\(?-\)?|%\s*chg|\bchg\b|\bchange\b|\bvariance\b|\bvs\b)", re.I)


def load_units(db_path: str, doc_id: str, units: list[dict]) -> dict:
    """Load every unit of one document into schema_v7 (idempotent, doc-scoped).

    units: list of {section_id, pages, parsed_path, table_type?}.
    Returns a summary dict. Fails loud per spec §3.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    drow = cur.execute("SELECT doc_period, institution FROM document WHERE doc_id = ?",
                       (doc_id,)).fetchone()
    if drow is None:
        raise RuntimeError(f"document {doc_id!r} does not exist (upstream-owned).")
    doc_period, institution = drow[0], drow[1]

    # segment_map / industry_map (verbatim label_norm -> key) loaded ONCE from the
    # target DB; the loader stamps row_dim/col_dim segment_key + industry_key by
    # exact normalised match on each axis (same mechanics, one map per dimension),
    # then applies axis exclusivity across both. (geo_map was a third map here
    # until 2026-08-12; geography stamping is retired and the table is gone.)
    seg_map = dict(cur.execute("SELECT label_norm, segment_key FROM segment_map").fetchall())
    ind_map = dict(cur.execute("SELECT label_norm, industry_key FROM industry_map").fetchall())
    # legal_entity_map (verbatim label_norm -> legal_entity_key), same mechanics —
    # the loader now OWNS legal_entity (was previously written only by the
    # out-of-band migrate_add_legal_entity.py, which _delete_doc silently erases
    # on every reload since it drops col_dim/cell_fact wholesale).
    le_map = dict(cur.execute("SELECT label_norm, legal_entity_key FROM legal_entity_map").fetchall())

    warnings: list[str] = []
    try:
        _delete_doc(cur, doc_id)   # doc-scoped reload; load_units owns the full unit set
        n_tables = n_rows = n_cells = n_skipped = 0
        # ---- PASS A: read every unit, then reconcile PRINTED shape to LOGICAL
        # shape across the whole document before loading anything.
        #
        # The continuation merge has to see ACROSS units. A filing that prints
        # one exhibit over two pages gets two TOC sections — UOB's 'Key
        # financial ratios (%)' (p5) and 'Financial Highlights (cont'd)' (p6) —
        # so the halves arrive as separate units and nothing unit-local can
        # rejoin them. Left apart, one 27-leaf masterlist entry is split 8/15
        # and only the 15-half clears MIN_MATCH_FRACTION.
        #
        # This also subsumes the old per-unit `resolve_continuations`: the merge
        # clears `continued_from_previous` on every table that survives as its
        # own, which is what `_load_table`'s contract requires.
        # SPLIT BEFORE MERGE — the two are inverse and they COMPOSE in this
        # order. UOB p5 emits ONE table titled 'Financial Highlights' carrying
        # three sub-captions as rows (the same shape as DBS's 'OVERVIEW'), and
        # p6 continues only the LAST of them. Merging first compares p6's
        # "Key financial ratios (%) (cont'd)" against a predecessor titled
        # 'Financial Highlights' and correctly finds no match; splitting first
        # exposes a 'Key financial ratios (%)' table for it to rejoin.
        prepared = []
        for u in units:
            parsed_path = Path(u["parsed_path"])
            ext = drop_echo_groups(_read_extraction(parsed_path))
            repair_swapped_captions(ext.tables, institution, warnings)
            # Side-car entries are positionally aligned to ext.tables (both come
            # from the same parsed.json and drop_echo_groups only rewrites
            # columns). A short/absent list yields None -> model-level fallback.
            g = _read_geometry(parsed_path)
            parts_all: list[tuple] = []
            for ti, gt0 in enumerate(ext.tables):
                g0 = g[ti] if ti < len(g) else None
                parts = split_caption_tables(gt0, g0)
                if len(parts) > 1:
                    warnings.append(
                        f"{gt0.title!r}: split into {len(parts)} tables at its "
                        f"caption rows ({', '.join(repr(p[0].title) for p in parts)})")
                parts_all.extend(parts)
            prepared.append([u, parts_all])

        # Flatten in PAGE ORDER, merge, regroup. Page order is essential and is
        # NOT the order `units` arrives in: `run_doc.build_units_from_audit`
        # sorts audit directories ALPHABETICALLY, which puts
        # 'financial_highlights_cont_d_p6' BEFORE 'financial_highlights_p5' and
        # would offer the continuation before the table it continues. Sorting is
        # confined to this pass so the load order itself is unchanged.
        _order = sorted(range(len(prepared)),
                        key=lambda i: (min((int(p) for p in prepared[i][0]["pages"]),
                                           default=0), i))
        _flat = [(ui, gt, gsub) for ui in _order for gt, gsub in prepared[ui][1]]
        _merged, _groups = merge_continuation_tables([t for _ui, t, _g in _flat],
                                                     warnings)
        for _p in prepared:
            _p[1] = []
        for _out_i, _grp in enumerate(_groups):
            _ui, _t, _gsub = _flat[_grp[0]]
            # PAGES ARE THE UNION OF EVERY SOURCE. The merged table lives in the
            # unit where the exhibit STARTED, but now carries rows printed on the
            # continuation page. STEP 5 verifies a table's numbers against its
            # page range, so keeping only the owning unit's pages makes a
            # correctly-merged table fail verification — and run_doc's
            # auto-re-extract then rewrites the section, destroying it. Observed
            # on UOB 2Q26: FS_RATIOS_KEY went from 15 stamped to none at all.
            _pages = sorted({int(p) for _i in _grp
                             for p in prepared[_flat[_i][0]][0]["pages"]})
            prepared[_ui][1].append((_merged[_out_i], _gsub, _pages))

        # ---- PASS B: load.
        for u, parts_all in prepared:
              for gt, gsub, pages in parts_all:
                res = _load_table(cur, doc_id, doc_period, u["section_id"],
                                  pages,
                                  u.get("table_type"), gt, warnings, seg_map,
                                  ind_map, le_map, gsub)
                if res is None:      # table refused by a gate — see warnings
                    n_skipped += 1
                    continue
                nr, nc = res
                n_tables += 1
                n_rows += nr
                n_cells += nc

        _apply_document_default_unit(cur, doc_id, warnings)
        stamps = _stamp_identity(con, cur, doc_id, warnings)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    for w in warnings:
        print(f"[load_v7 WARN] {w}")
    return dict(doc_id=doc_id, tables=n_tables, rows=n_rows, cells=n_cells,
                skipped_tables=n_skipped,
                warnings=warnings, **stamps)


def _stamp_identity(con, cur, doc_id: str, warnings: list[str]) -> dict:
    """Stamp the document-INDEPENDENT identity columns, at load.

    Two stages, deliberately separated by what they depend on:

      1. `col_dim.col_role` — masterlist-INDEPENDENT, so it runs for every table
         of every document. A column that restates other columns ('% chg',
         '+/(-)%') is marked 'derived_skip' and must never be ingested as a
         period fact. This is a COLUMN rule only: a row whose values sit solely
         in derived columns still exists and still earns an id.

      2. `table_t.table_type_id` + `row_dim.canonical_leaf_id` — from
         `data/derived/masterlist/`, the ONLY source of canonical ids. Tables are
         identified by CONTENT (printed row paths vs the masterlist's
         `full_path`), not by caption, so a caption collision cannot borrow
         another table's leaves and every vintage resolves off one masterlist.

    NO-OP WHEN NO MASTERLIST IS PRESENT. Stage 2 is skipped silently if the
    directory is absent or empty, so a bare-schema load and the pass2 test suite
    run unchanged. Partial coverage is the expected state — only DBS Overview is
    authored today; everything else stays NULL until its masterlist exists."""
    out = dict(cols_derived=0, tables_typed=0, leaves_stamped=0,
               cols_stamped=0, cols_unresolved=0)

    # --- 1. derived columns ------------------------------------------------
    for cid_doc, tid, col_id, lab in cur.execute(
            "SELECT doc_id, table_id, col_id, col_leaf_label FROM col_dim "
            "WHERE doc_id = ?", (doc_id,)).fetchall():
        if lab and _DERIVED_COL_RX.search(str(lab)):
            cur.execute("UPDATE col_dim SET col_role = 'derived_skip' WHERE "
                        "doc_id = ? AND table_id = ? AND col_id = ?",
                        (cid_doc, tid, col_id))
            out["cols_derived"] += 1

    # --- 2. masterlist identity -------------------------------------------
    try:
        from stage3_stamp.resolve import resolve_canonical_leaf as RCL
        master = RCL.load_masterlist()
    except (ImportError, FileNotFoundError):
        return out                      # no masterlist -> stage 2 is a no-op
    if not master:
        return out

    # --- 3. column-axis identity (spec 2026-08-09-column-axis-identity) ----
    # Loaded here, applied per identified table below: a column block is keyed
    # (bank, table_type_id) exactly like the row block, so it can only be
    # applied once `locate_tables` has decided WHICH table this is. Absent ->
    # {}, and every table falls through unstamped, same no-op contract as
    # stage 2.
    try:
        from stage3_stamp.resolve import resolve_canonical_col as RCC
        col_master = RCC.load_col_members()
    except (ImportError, FileNotFoundError):
        RCC, col_master = None, {}

    hits = RCL.locate_tables(con, master, doc_ids={doc_id})
    for (bank, tt), tabs in hits.items():
        entry = master[(bank, tt)]
        col_entry = col_master.get((bank, tt)) if RCC else None
        for t in tabs:
            # COLUMN VETO. `locate_tables` scores ROW content only, and a
            # geography exhibit and a segment exhibit print the SAME P&L line
            # items — so UOB's 11-leaf FS_PERF_BY_GEOGRAPHY cleared
            # MIN_MATCH_FRACTION against `Performance by Business Segment`
            # (7 of 11 matched) and stamped 21 rows with geography ids under a
            # segment table. Measured on the 2026-08-09 pilot.
            #
            # The column block already knows better: that table's columns are
            # 'GR' / 'GWB' / 'GM', which match no geography member. So when a
            # type DECLARES a column block, resolving none of the candidate's
            # value-columns is disqualifying. This is a VETO, not a score —
            # column members deliberately stay out of the coverage denominator
            # (spec §6), because a vintage that reprints fewer columns must
            # still match its rows.
            col_results = (RCC.resolve_columns(cur, t["doc_id"], t["table_id"],
                                               bank, tt, col_entry)
                           if col_entry else [])
            if col_entry:
                offered = [c for c in col_results
                           if c["outcome"] in ("matched", "unresolved")]
                n_col_hit = sum(1 for c in offered if c["outcome"] == "matched")
                if (not offered
                        or n_col_hit < RCC.MIN_COL_MATCH_FRACTION * len(offered)):
                    warnings.append(
                        f"{t['table_id']}: vetoed as {tt} — {n_col_hit}/"
                        f"{len(offered)} columns resolved against its column "
                        f"block, below {RCC.MIN_COL_MATCH_FRACTION:.0%} "
                        f"({[c['printed_path'] for c in offered][:5]})")
                    continue
            cur.execute("UPDATE table_t SET table_type_id = ? WHERE doc_id = ? "
                        "AND table_id = ?", (tt, t["doc_id"], t["table_id"]))
            out["tables_typed"] += 1
            results, _aliases = RCL.resolve_table(
                cur, t["doc_id"], t["table_id"], bank, tt, entry,
                seed_caption=t["title"])
            for r in results:
                cid = r["canonical_leaf_id"]
                if not cid:
                    continue
                if cid not in entry["ids"]:
                    raise RuntimeError(
                        f"refusing to stamp {cid!r} — not in the masterlist for "
                        f"({bank}, {tt})")
                # table_type_id is written HERE, on the row, by the same
                # masterlist entry that resolved the leaf — the two halves of
                # the address (bank, table_type_id, canonical_leaf_id) are
                # stamped together and can never disagree. The table-level
                # UPDATE above is last-writer-wins by construction (several
                # entries legitimately match one exhibit), which used to leave
                # correctly-stamped leaves unreachable to a join on the losing
                # type. See row_dim.table_type_id in schema_v7.sql.
                cur.execute(
                    "UPDATE row_dim SET canonical_leaf_id = ?, table_type_id = ? "
                    "WHERE doc_id = ? AND table_id = ? AND row_id = ?",
                    (cid, tt, t["doc_id"], t["table_id"], r["row_id"]))
                out["leaves_stamped"] += 1
            n_un = sum(1 for r in results if r["outcome"] == "unresolved")
            if n_un:
                warnings.append(
                    f"{t['table_id']}: {n_un} row(s) matched no masterlist entry "
                    f"for {tt} — left unstamped")

            # --- 3. hard-axis columns, for THIS identified table ------------
            # Already resolved above for the veto; stamp what it found.
            for c in col_results:
                if c["outcome"] != "matched":
                    if c["outcome"] == "unresolved":
                        out["cols_unresolved"] += 1
                        warnings.append(
                            f"{t['table_id']}: column {c['col_id']} "
                            f"({c['printed_path']!r}) matched no column member "
                            f"for {tt} — left unstamped")
                    continue
                ccid = c["canonical_col_id"]
                # Same provenance invariant as the row side: an id that is not
                # in the masterlist's own set is never written, so "every
                # stamped id is copied verbatim" holds on both axes.
                if ccid not in col_entry["ids"]:
                    raise RuntimeError(
                        f"refusing to stamp column id {ccid!r} — not in the "
                        f"column block for ({bank}, {tt})")
                sets, vals = ["canonical_col_id = ?"], [ccid]
                if c["dim"] in ("geo", "segment", "industry") and c["dim_key"]:
                    sets.append(f"{c['dim']}_key = ?")
                    vals.append(c["dim_key"])
                cur.execute(
                    f"UPDATE col_dim SET {', '.join(sets)} WHERE doc_id = ? "
                    f"AND table_id = ? AND col_id = ?",
                    (*vals, t["doc_id"], t["table_id"], c["col_id"]))
                out["cols_stamped"] += 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load a pass2 GTable unit into schema_v7.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--section-id", required=True)
    ap.add_argument("--pages", required=True, help="comma-separated page numbers, e.g. 25 or 25,26")
    ap.add_argument("--parsed", required=True, help="path to parsed.json")
    ap.add_argument("--table-type", default=None)
    a = ap.parse_args(argv)
    unit = dict(section_id=a.section_id,
                pages=[int(p) for p in a.pages.split(",")],
                parsed_path=a.parsed, table_type=a.table_type)
    summary = load_units(a.db, a.doc_id, [unit])
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
