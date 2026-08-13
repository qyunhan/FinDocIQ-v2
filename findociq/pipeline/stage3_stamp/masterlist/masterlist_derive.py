"""masterlist_derive — the ONE implementation of masterlist row derivation.

Imported by the seeder (`masterlist/propose_masterlist.py`) and by the resolver
(`resolve/resolve_canonical_leaf.py`).
There is deliberately no second copy: the whole point of the v3 rebuild is that
seeding and resolution cannot drift.

  CAVEAT ON WHAT A ZERO-UNRESOLVED PHASE-2 PROVES. Because seeder and resolver
  share this module, a reverse-tag run reporting 0 unresolved proves registry
  COVERAGE and (bank, table_type_id) scoping — NOT resolver correctness. The
  real correctness test is an unseen document (DBS 2Q26), which is the step
  after v3.

--------------------------------------------------------------------------
THE THREE NORMALISATION RULES — each does one job, none moonlights
--------------------------------------------------------------------------
1. `strip_footnote_markers` — superscripts, *, dagger, trailing glued digits.
2. `strip_trailing_date`    — a trailing printed date on a DATA label
                              ('Balance at 1 January 2024' -> 'Balance at 1
                              January'). The year is period information carried
                              by the enclosing PERIOD_BANNER, never id material.
3. `is_period_label`        — classification only: decides PERIOD_BANNER.

Previously ONE rule (the trailing-digit footnote strip) was doing all three by
accident: it turned 'Balance at 1 January 2024' into 'balance_at_1_january'
(right answer, wrong reason) and fused '31 Dec 2021/2022/2024/2025' into a
single '31_dec' identity segment (wrong answer). They are now separate.

--------------------------------------------------------------------------
ROW CLASSIFICATION (PASS 1)
--------------------------------------------------------------------------
  DATA           has a numeric value in any non-derived column
  SECTION_HEADER no values AND label normalises to the table's seed caption
                 -> dropped, contributes no segment
  EXCLUDED       no values AND label in note/notes/nm...; or no values and no
                 DATA row follows before end of table (trailing prose)
  PERIOD_BANNER  no values AND label is a date/period ('31 Dec 2025',
                 '4th Qtr 2025', '1Q25', 'Year 2025')
  BANNER         no values, none of the above ('By currency and product')

--------------------------------------------------------------------------
ANCESTRY (PASS 2) — banner stack, reset at every table boundary
--------------------------------------------------------------------------
  * an incoming BANNER/PERIOD_BANNER pops open banners at printed level >= its
    own, then pushes. Printed level is used ONLY banner-to-banner: banners are
    stable relative to each other, children's levels drift across vintages.
  * a DATA row NEVER closes a banner whatever its printed level. This is the
    core of Rule 3 — DBS 2Q25 prints currency rows at level 0 under 'By
    currency and product' and 4Q25 prints them at level 1; both must land
    inside the banner. It does, however, only INHERIT the open banners at
    level <= its own: a banner printed strictly deeper than the row cannot be
    its ancestor (OCBC's de-indented 'Total equity' vs the 'Attributable to
    equity holders of the Bank' sub-heading it is printed after).
  * parent of a DATA row, in precedence order:
      (a) printed parent, if it resolves to a strictly shallower row in the
          SAME table and inside the innermost open banner
      (b) else nearest preceding DATA row at shallower printed level within the
          innermost open banner scope
      (c) else the innermost open BANNER
    (b) yields value-carrying parents ('Commercial book total income' -> its
    NII child); (c) yields banner parents ('By business unit' -> 'Consumer
    Banking'). One rule, both hierarchy styles.
  * PERIOD_BANNER scopes exactly like a BANNER but contributes NO id segment
    and stamps row_period on its scope. A date is period data, never identity:
    the '31 Dec 2025' and '30 Jun 2025' panels over the same rows yield ONE
    leaf each, two facts differing by period.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 1. FOOTNOTE MARKERS
# --------------------------------------------------------------------------
SUPERSCRIPT = ("".join(chr(c) for c in range(0x2070, 0x2080)) + "¹²³"
               + "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ" + "*†‡§")
_UNIT_SUFFIX = re.compile(
    r"\(\s*(?:s\$\s*'?m?|us\$\s*'?m?|\$\s*'?m|'m|%|bps?|bp|m|bn)\s*\)\s*$", re.I)
_BARE_UNIT = re.compile(r"\s*(?:s\$m|s\$|us\$m|\$m|%)\s*$", re.I)
# `(?<!\d)` is load-bearing: without it the 1-2 digit cap is no protection at
# all, because the engine happily matches the TAIL of a longer run — '2024'
# loses '24', then '20', and the year is gone before rule 3 can classify it.
# A footnote marker is never a suffix of a longer number.
#
# The optional `/` after EACH marker covers the 'N/' reference convention OCBC
# prints — 'Capital adequacy ratios 8/ 9/', 'Return on assets 3/'. Without it
# the trailing slash defeats the `$` anchor and nothing strips, so the marker
# digits survive into the id: the masterlist authored off one vintage keys
# `capital_adequacy_ratios_8_9` while the next filing, with its footnotes
# RENUMBERED, keys `capital_adequacy_ratios_8`. The addresses then differ for a
# row whose label never changed, the table falls under MIN_MATCH_FRACTION, and
# every leaf in it goes unstamped (OCBC 2Q26 Key Financial Ratios: 8/22).
# Footnote renumbering between filings is normal for any bank, so this is a
# grammar gap, not a per-document quirk.
_TRAILING_FOOTNOTE = re.compile(r"[\s,]*(?<!\d)\d{1,2}/?(?:\s*[,/&]\s*\d{1,2}/?)*\s*$")

# The BRACKETED form of the same convention — '(1)', '[2]', '(1,2)'. OCBC's
# balance sheet prints 'Net asset value per ordinary share – S$ (1)' at
# year-end and the identical line WITHOUT the marker in the interim, so one row
# whose text never changed keyed `net_asset_value_per_ordinary_share_s_1` in
# one vintage and `net_asset_value_per_ordinary_share` in the next.
#
# Its own pattern rather than a tweak to the bare one, for two reasons: the
# brackets defeat the `$` anchor (the digits are not trailing), and they also
# SHIELD whatever precedes them — the '– S$' unit tail was never last, so
# `_BARE_UNIT` could not see it either. Both fall out at once because
# `strip_footnote_markers` iterates to a fixpoint: drop '(1)', the unit becomes
# trailing, drop that on the next turn.
#
# Same 1-2 digit cap as the bare form and for the same reason — a label may
# legitimately end in a parenthesised 4-digit year. ALPHA markers are
# deliberately not matched: '(Total)' is identity and '(S$'m)' is
# `_UNIT_SUFFIX`'s job.
_BRACKET_FOOTNOTE = re.compile(
    r"[\s,]*[(\[](?<!\d)\d{1,2}(?:\s*[,/&]\s*\d{1,2})*[)\]]\s*$")

# --------------------------------------------------------------------------
# 2. DATES / PERIODS
# --------------------------------------------------------------------------
_MON = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?")
# A trailing printed YEAR — and only the year. 'Balance at 1 January 2024' ->
# 'Balance at 1 January': the day and month are part of the line's printed
# identity (opening vs closing balance), the year is the period and drifts
# every vintage. Constrained to 19xx/20xx so a genuine 4-digit quantity at the
# end of a label is not mistaken for a date.
_TRAILING_DATE = re.compile(r"[\s,]*(?<!\d)(?:19|20)\d{2}\s*$")
# a WHOLE label that is a date/period
_PERIOD_WHOLE = [
    re.compile(rf"^\s*(?:as\s+at\s+)?\d{{1,2}}\s+(?:{_MON})\s+\d{{4}}\s*$", re.I),
    re.compile(rf"^\s*(?:as\s+at\s+)?(?:{_MON})\s+\d{{4}}\s*$", re.I),
    re.compile(r"^\s*[1-4](?:st|nd|rd|th)?\s*(?:qtr|quarter|Q)\s*\d{2,4}\s*$", re.I),
    re.compile(r"^\s*[1-4]Q\s*\d{2,4}\s*$", re.I),
    re.compile(r"^\s*[12]H\s*\d{2,4}\s*$", re.I),
    # the spelled forms the reference docs actually print as column headers
    re.compile(r"^\s*[12](?:st|nd)\s+Half\s+\d{2,4}\s*$", re.I),
    re.compile(r"^\s*(?:first|second)\s+half\s+\d{2,4}\s*$", re.I),
    re.compile(r"^\s*(?:full\s+year|year\s+ended)\s*\d{2,4}\s*$", re.I),
    re.compile(r"^\s*(?:FY|Year)\s*(?:ended\s+)?\d{2,4}\s*$", re.I),
    re.compile(r"^\s*9M\s*\d{2,4}\s*$", re.I),
    re.compile(r"^\s*\d{4}\s*$"),
]


def strip_footnote_markers(s: str) -> str:
    """RULE 1 — footnote markers only. Superscripts anywhere (DBS prints
    'ECL¹ Stage 1 and 2' and 'ECL Stage 1 and 2' for one line), then trailing
    glued 1-2 digit markers, bracketed markers and unit suffixes.

    Superscripts must go BEFORE NFKC: NFKC folds '¹' to '1' and the marker
    becomes indistinguishable from a real digit.

    The trailing-digit branch is capped at 2 digits so it can never eat a
    4-digit year — that is rule 2's job, not this one."""
    s = (s or "").replace("\n", " ")
    s = "".join(ch for ch in s if ch not in SUPERSCRIPT)
    s = unicodedata.normalize("NFKC", s).strip()
    prev = None
    while prev != s:
        prev = s
        s = s.strip().rstrip(SUPERSCRIPT).strip()
        s = _UNIT_SUFFIX.sub("", s).strip()
        s = _BARE_UNIT.sub("", s).strip()
        s = _BRACKET_FOOTNOTE.sub("", s).strip()
        s = _TRAILING_FOOTNOTE.sub("", s).strip()
        s = s.rstrip(":;,.").strip()
    return s


def strip_trailing_date(s: str) -> str:
    """RULE 2 — a trailing printed date on a DATA label. 'Balance at 1 January
    2024' -> 'Balance at 1 January'. Only fires when a date TAIL remains after
    other text; a label that is WHOLLY a date is a PERIOD_BANNER (rule 3) and
    must never reach here."""
    s = (s or "").strip()
    out = _TRAILING_DATE.sub("", s).strip()
    return out if out else s          # never annihilate the label


def _period_prep(s: str) -> str:
    """Shared prep for rules 3 and 3b. Deliberately does NOT call rule 1: a
    period label is mostly digits, and running a footnote-marker strip over it is
    how the three rules got tangled in the first place. Only superscripts + NFKC
    are removed, so '4th Qtr 2025¹' still classifies."""
    s = (s or "").replace("\n", " ")
    s = "".join(ch for ch in s if ch not in SUPERSCRIPT)
    return unicodedata.normalize("NFKC", s).strip().rstrip(":;,.").strip()


def is_period_label(s: str) -> bool:
    """RULE 3 — classification only. True when the WHOLE label is a date or
    period token, which makes the row a PERIOD_BANNER (valueless) or a
    PERIOD_ROW (valued)."""
    return any(p.match(_period_prep(s)) for p in _PERIOD_WHOLE)


# The bare 'At <date>' form — 'At 31 December 2025', 'At 30 June 2026'. Note
# _PERIOD_WHOLE already covers 'as at <date>'; this is the same construct minus
# the 'as', and it is SPLIT OUT rather than folded in because the two callers
# need different answers. See is_period_banner_label.
_PERIOD_WHOLE_VALUELESS = [
    re.compile(rf"^\s*at\s+\d{{1,2}}\s+(?:{_MON})\s+\d{{4}}\s*$", re.I),
    re.compile(rf"^\s*at\s+(?:{_MON})\s+\d{{4}}\s*$", re.I),
    # '<Half|Full|First|Second> year ended <full date>' — OCBC's segment banner.
    # _PERIOD_WHOLE already has 'year ended YYYY'; this is the same phrase with a
    # day and month spelled out, which that pattern's bare \d{2,4} cannot reach.
    re.compile(rf"^\s*(?:half|full|first|second|1st|2nd)?\s*"
               rf"(?:year|quarter|period|half)\s+ended\s+"
               rf"\d{{1,2}}\s+(?:{_MON})\s+\d{{4}}\s*$", re.I),
]


def is_period_banner_label(s: str) -> bool:
    """RULE 3b — classification of a VALUELESS row only. Rule 3, plus the bare
    'At <date>' form.

    WHY THIS CANNOT JUST WIDEN RULE 3. `classify` asks the same question of
    valued and valueless rows, and 'At 1 January 2025' means opposite things in
    the two cases:

      VALUELESS  UOB and OCBC head a segment BALANCE block with 'At 31 December
                 2025'. It scopes the rows under it — period, no identity. Left
                 as a BANNER it became an id segment:
                 `at_31_december::segment_assets`, 62 leaves across the corpus.

      VALUED     'At 1 January 2025' is the OPENING BALANCE line of a statement
                 of changes in equity or a Level 3 roll-forward, and 'At 31
                 December 2025' is the closing one. Both carry 4-8 figures. The
                 day and month ARE the line's printed identity — opening vs
                 closing — which masterlist_derive.py:125 already records for
                 'Balance at 1 January'. Calling them PERIOD_ROW would collapse
                 opening and closing into one leaf and lose the movement.

    Measured on the 4Q25 corpus: of the 7 distinct 'At <date>' labels, every
    valued occurrence is an opening/closing balance and every valueless one is a
    segment banner. The split is on `has_values`, not on the wording.

    Unlike rule 3 this DOES strip footnote markers first: OCBC prints the same
    banner as both 'Half year ended 31 December 2025' and '… (1)', and a
    valueless banner's footnote is never identity."""
    if is_period_label(s):
        return True
    prepped = _period_prep(strip_footnote_markers(s or ""))
    return any(p.match(prepped) for p in _PERIOD_WHOLE_VALUELESS)


def normalize_segment(s: str | None, *, is_data: bool = True) -> str:
    """A label -> one id segment. Rules 1 then 2 (2 only for DATA labels),
    then casefold and non-word -> underscore."""
    s = strip_footnote_markers(s or "")
    s = re.sub(r"\s*&\s*", " and ", s)
    if is_data:
        s = strip_trailing_date(s)
    s = re.sub(r"[^\w]+", "_", s.casefold(), flags=re.UNICODE)
    return s.strip("_")


# --------------------------------------------------------------------------
# CAPTION RESOLUTION — section(doc-kind)-scoped, never caption alone
# --------------------------------------------------------------------------
_NOTE_NUM = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")
_CONTD = re.compile(r"\s*[\(\[]?\s*cont(?:inued|'?d|’d)?\s*[\)\]]?\s*", re.I)
_PERIOD_TAIL = re.compile(
    rf"\s*[—–-]\s*(?:(?:as\s+)?at\s+)?(?:\d{{1,2}}\s+)?(?:{_MON})?\s*"
    rf"(?:\d{{4}}|[1-4]Q\s*\d{{2,4}}|[12]H\s*\d{{2,4}}|FY\s*\d{{2,4}}|"
    rf"half\s+year\s+ended.*|year\s+ended.*)\s*$", re.I)
# a trailing BREAKDOWN discriminator: '— NPLs by Industry', '— by Geography'
_DISCRIMINATOR = re.compile(r"\s*[—–]\s*(?P<disc>(?:[A-Za-z][\w\s/']*)?\bby\b[\w\s/']*)$", re.I)


def normalize_caption(cap: str | None) -> str:
    """Caption -> match key. Strips leading note numbering ('3. Net interest
    income'), '(cont'd)' and trailing period labels ('— 1H25')."""
    s = (cap or "").replace("\n", " ").strip()
    s = _NOTE_NUM.sub("", s)
    s = _CONTD.sub(" ", s)
    s = _PERIOD_TAIL.sub("", s)
    return normalize_segment(s, is_data=False)


def split_discriminator(cap: str | None) -> tuple[str, str | None]:
    """Split a caption into (base, breakdown-discriminator).

    OCBC prints as separately-captioned sub-tables what DBS prints as banner
    rows inside one table: 'NON-PERFORMING ASSETS (continued) — NPLs by
    Industry'. The suffix is not a caption needing its own seed row — the
    seed already declares this table's row axis as 'breakdown axis'. It is a
    breakdown discriminator, and it is injected as a BANNER ancestor so the
    ids come out structurally identical to DBS's banner-row form
    (`by_industry::manufacturing` means the same thing in both banks)."""
    s = (cap or "").replace("\n", " ").strip()
    s = _NOTE_NUM.sub("", s)
    s = _CONTD.sub(" ", s)
    s = _PERIOD_TAIL.sub("", s).strip()
    m = _DISCRIMINATOR.search(s)
    if not m:
        return s, None
    disc = m.group("disc").strip()
    base = s[:m.start()].strip()
    if not base:
        return s, None
    return base, disc


_DATE_TAIL = re.compile(
    rf"\s*(?:[-—–]\s*)?(?:for\s+the\s+.*|as\s+at\s+.*|as\s+of\s+.*|"
    rf"\d{{1,2}}\s+\w+\s+\d{{4}}.*|(?:{_MON})\s+\d{{4}}\s*$)", re.I)


def caption_variants(caption: str | None):
    """Yield a printed caption progressively stripped, so an exact registry
    match can still land. Purely syntactic — no per-bank special-casing.

    A printed caption routinely carries what the registry never names: a
    newline + date subtitle ('AUDITED ... STATEMENT OF CHANGES IN EQUITY\\nFOR
    THE YEAR ENDED 31 DECEMBER 2025'), a panel suffix ('— The Group (2024)',
    '— Year 2025, Year 2024'), a '(continued)' marker, a leading note number,
    or an audited/unaudited qualifier (the same exhibit is unaudited at Q1-Q3
    and audited at Q4 — a vintage qualifier, not identity)."""
    c = (caption or "").strip()
    if not c:
        return
    seen = set()
    heads = [c, c.split("\n")[0].strip()]
    for h in list(heads):
        heads.append(_CONTD.sub(" ", h).strip())
        heads.append(_DATE_TAIL.sub("", h).strip())
        heads.append(_DATE_TAIL.sub("", _CONTD.sub(" ", h)).strip())
    for h in list(heads):
        for sep in (" — ", " – ", " - "):
            if sep in h:
                heads.append(h.split(sep)[0].strip())
        heads.append(_NOTE_NUM.sub("", h).strip())
    for h in heads:
        for v in (h, re.sub(r"\s*\(?\s*(?:un)?audited\s*\)?\s*", " ", h,
                            flags=re.I).strip()):
            v = re.sub(r"\s{2,}", " ", v).strip(" -—–:,")
            if v and v not in seen:
                seen.add(v)
                yield v


def discriminator_segment(disc: str | None) -> str | None:
    """'NPLs by Industry' -> 'by_industry'; 'by Geography' -> 'by_geography'.
    Keeps only the 'by X' part so OCBC's sub-table form and DBS's banner-row
    form ('By industry') collapse to the same segment."""
    if not disc:
        return None
    m = re.search(r"\bby\b\s*(?P<what>[\w\s/']+)$", disc, re.I)
    what = m.group("what") if m else disc
    return normalize_segment(f"by {what}", is_data=False) or None


# --------------------------------------------------------------------------
# PASS 1 — classification
# --------------------------------------------------------------------------
DATA = "DATA"
BANNER = "BANNER"
PERIOD_BANNER = "PERIOD_BANNER"
PERIOD_ROW = "PERIOD_ROW"
SECTION_HEADER = "SECTION_HEADER"
EXCLUDED = "EXCLUDED"

EXCLUDE_LABELS = {"note", "notes", "nm", "nm_not_meaningful", "not_meaningful",
                  "n_m", "na", "n_a"}

# A whole footnote BLOCK arrives as one valueless row whose text is the marker
# word plus the notes themselves ('Notes: 1 Relates to ... 3 Refers to ...').
# `EXCLUDE_LABELS` catches only the bare word, so the block was matched by a
# `note_` prefix — which missed the PLURAL, the form every filing in the corpus
# actually prints. It cost nothing while the block was the last row of its table
# (the trailing-prose rule excluded it anyway); once `merge_continuation_tables`
# appends a carry-over page BENEATH it, it becomes a mid-table BANNER and
# prefixes the whole continuation: UOB 2Q26's NSFR / Leverage ratio / NAV rows
# resolved as 'Notes: 1 Relates to ... > Liquidity coverage ratios > NSFR',
# two ancestors deep where `match_variants` can strip only one.
# Anchored and underscore-terminated, so a valued line item is untouched — and
# this branch is only reached by rows with NO values at all.
_NOTE_BLOCK_RX = re.compile(r"notes?_")


@dataclass
class Row:
    row_id: int
    label: str
    level: int
    parent: int | None          # printed/positional parent row_id from the loader
    has_values: bool
    cls: str = ""
    ancestors: list[str] = field(default_factory=list)   # id segments, outer->in
    ancestor_labels_raw: list[str] = field(default_factory=list)  # VERBATIM
                                  # labels, footnotes intact — what the page
                                  # printed. The masterlist's `full_path` is
                                  # written this way, so an exact path match
                                  # needs them unmodified.
    ancestor_labels: list[str] = field(default_factory=list)  # DISPLAY labels,
                                  # index-aligned to `ancestors`. Kept separate so
                                  # full_hierarchy shows what the page printed
                                  # while the id stays normalised — and so the
                                  # ghost-ancestor gate compares two independently
                                  # derived things instead of itself.
    period_banner: str | None = None
    parent_row: int | None = None
    identity_label: str = ""      # label the id is built from; differs from
                                  # `label` only for PERIOD_ROW, which borrows
                                  # its parent's identity and adds only period


def classify(rows: list[Row], seed_caption_norm: str | None) -> None:
    """Assign `cls` to every row in place. Table-local; no cross-table state."""
    n = len(rows)
    for i, r in enumerate(rows):
        if r.has_values:
            # A VALUED row whose whole label is a date is period data too — UOB
            # and OCBC print 'Dec-25' / '31 Dec 2025' as valued leaf ROWS under a
            # geography. Same principle as PERIOD_BANNER: it carries row_period
            # and contributes NO id segment, so 'Singapore' at two dates is ONE
            # leaf with two facts, not 'singapore::31_dec'.
            r.cls = PERIOD_ROW if is_period_label(r.label) else DATA
            continue
        norm = normalize_segment(r.label, is_data=False)
        if seed_caption_norm and norm == seed_caption_norm:
            r.cls = SECTION_HEADER
            continue
        if norm in EXCLUDE_LABELS or _NOTE_BLOCK_RX.match(norm):
            r.cls = EXCLUDED
            continue
        if is_period_banner_label(r.label):   # rule 3b — valueless rows only
            r.cls = PERIOD_BANNER
            continue
        if not any(rows[j].has_values for j in range(i + 1, n)):
            r.cls = EXCLUDED          # trailing prose: no DATA row follows
            continue
        r.cls = BANNER


# --------------------------------------------------------------------------
# PASS 2 — ancestry via the banner stack
# --------------------------------------------------------------------------
def build_ancestry(rows: list[Row], extra_banner: str | None = None) -> None:
    """Fill `ancestors` (id segments outer->in), `period_banner` and
    `parent_row` for every DATA row. All state is table-local and resets here.

    THE CAPTURED CHAIN IS THE BASE HIERARCHY. `Row.parent` is the loader's
    `row_dim.row_parent` — already carrying the printed-parent precedence the
    extractor stated (`GRow.parent`, consumed since 01151d1). Walking it is the
    structure the pipeline actually captured, and v3 does not re-litigate it.

    The banner rule is a REPAIR, not a replacement. It supplies a parent only
    where the chain leaves a row orphaned (`row_parent IS NULL` on an indented
    row) — the level-drift case Rule 3 exists for: DBS 2Q25 prints currency rows
    at level 0 under 'By currency and product' and 4Q25 at level 1, and the
    banner absorbs the drift so both vintages yield one id.

    Two ancestor kinds are dropped from the id but kept in the walk:
      * SECTION_HEADER — the table's own caption, redundant with table_type_id
      * PERIOD_BANNER / PERIOD_ROW — a date is period data, never identity

    `extra_banner` is the caption breakdown discriminator ('by_industry') when
    this table is an OCBC-style sub-table; it becomes the outermost banner."""
    stack: list[tuple[int, str, str, str]] = []   # (level, cls, segment, label)
    period: str | None = None
    by_id = {r.row_id: r for r in rows}

    for r in rows:
        if r.cls in (SECTION_HEADER, EXCLUDED):
            continue

        if r.cls in (BANNER, PERIOD_BANNER):
            # an incoming banner pops open banners at level >= its own
            while stack and stack[-1][0] >= r.level:
                popped = stack.pop()
                if popped[1] == PERIOD_BANNER:
                    period = None
            if r.cls == PERIOD_BANNER:
                period = strip_footnote_markers(r.label)
                stack.append((r.level, PERIOD_BANNER, "", ""))   # NO id segment
            else:
                stack.append((r.level, BANNER,
                              normalize_segment(r.label, is_data=False),
                              strip_footnote_markers(r.label)))
            continue

        # --- DATA / PERIOD_ROW. Never CLOSES a banner, whatever its level: the
        # stack is left untouched here, because a later, deeper row may still
        # belong inside a banner this row sits outside of.
        #
        # But a banner printed STRICTLY DEEPER than the row cannot be that row's
        # ancestor — nothing is a child of something more indented than itself.
        # OCBC's balance sheet is the case: 'EQUITY' (level 0) opens, the
        # sub-heading 'Attributable to equity holders of the Bank' (level 1)
        # opens inside it, and the section total 'Total equity' is printed
        # DE-INDENTED back to level 0. Taking the whole open stack gave it
        # 'EQUITY > Attributable to equity holders of the Bank > Total equity'
        # against a masterlist 'EQUITY > Total equity', and the leaf went
        # unstamped in every vintage. 'Total liabilities' and 'Total assets'
        # only escaped because their sections have no sub-heading.
        #
        # The filter is `<=`, not `<`: a banner at the row's OWN level is kept.
        # That is Rule 3, the level-drift repair — DBS prints currency rows at
        # level 0 under a level-0 'By currency and product' in 2Q25 and at
        # level 1 in 4Q25, and both must land inside the banner. Drift makes a
        # child look SHALLOWER than expected, so only a strictly-deeper banner
        # is provably not an ancestor.
        open_banners = [seg for lvl, cls, seg, _lab in stack
                        if cls == BANNER and seg and lvl <= r.level]
        open_banner_labels = [lab for lvl, cls, seg, lab in stack
                              if cls == BANNER and seg and lvl <= r.level]
        r.period_banner = period

        chain = _captured_chain(r, by_id)      # nearest ancestor LAST
        if chain:
            # THE CHAIN WINS. Identity segments are its ancestors minus the
            # caption echo and minus anything period-shaped.
            base, base_labels, base_raw = [], [], []
            for anc in chain:
                if anc.cls in (SECTION_HEADER, PERIOD_BANNER, PERIOD_ROW):
                    continue
                # A DATE-LABELLED BALANCE LINE SCOPES NOTHING. The `cls` test
                # above catches period ancestors that carry no values; this
                # catches the VALUED ones. OCBC indents its changes-in-equity
                # movements under the opening balance, so the loader captured
                # 'At 1 January 2024' as their parent and every movement came
                # out `at_1_january::profit_for_the_year`. In the printed
                # statement they are its SIBLINGS — opening balance, movements,
                # closing balance — which is how DBS already captures the same
                # exhibit.
                #
                # The row keeps its OWN leaf: it is still DATA, still derives
                # `at_1_january` from its own label, still carries its figures.
                # Only its role as a heading over the rows below it is dropped.
                # Measured: 24 rows across 4 OCBC tables, nothing elsewhere.
                if is_period_banner_label(anc.identity_label or anc.label):
                    continue
                lab = anc.identity_label or anc.label
                seg = normalize_segment(lab)
                if seg:
                    base.append(seg)
                    base_labels.append(strip_footnote_markers(lab))
                    base_raw.append(lab)
            r.parent_row = chain[-1].row_id
        else:
            # ORPHAN — the chain gave nothing. This is the level-drift case the
            # banner rule exists to repair.
            base = list(open_banners)
            base_labels = list(open_banner_labels)
            base_raw = list(open_banner_labels)
            r.parent_row = None

        if r.cls == PERIOD_ROW:
            # Borrows its parent's identity outright and contributes only
            # period. A top-level period row has no identity to borrow and is
            # dropped rather than becoming a date-named leaf.
            r.period_banner = strip_footnote_markers(r.label)
            if base:
                r.ancestors = base[:-1]
                r.ancestor_labels = base_labels[:-1]
                r.ancestor_labels_raw = base_raw[:-1]
                r.identity_label = base_labels[-1] if base_labels else base[-1]
            else:
                r.ancestors = []
                r.ancestor_labels = []
                r.identity_label = ""
            continue

        if extra_banner:
            keep = [i for i, b in enumerate(base) if b != extra_banner]
            base_labels = [extra_banner.replace("_", " ")] + [base_labels[i] for i in keep]
            base_raw = [extra_banner.replace("_", " ")] + [base_raw[i] for i in keep]
            base = [extra_banner] + [base[i] for i in keep]
        r.ancestors = base
        r.ancestor_labels = base_labels
        r.ancestor_labels_raw = base_raw
        r.identity_label = r.label


def _captured_chain(r: Row, by_id: dict[int, Row]) -> list[Row]:
    """The loader's row_parent chain for `r`, root-first, nearest ancestor LAST.

    Cycle-guarded and depth-capped. Returns [] when the row has no parent —
    which is the only case the banner rule is allowed to repair."""
    out: list[Row] = []
    seen: set[int] = {r.row_id}
    cur = by_id.get(r.parent) if r.parent is not None else None
    while cur is not None and len(out) < 12:
        if cur.row_id in seen:
            break                      # defensive: a cycle would hang the walk
        seen.add(cur.row_id)
        out.append(cur)
        cur = by_id.get(cur.parent) if cur.parent is not None else None
    out.reverse()
    return out


def _index_of(rows: list[Row], r: Row) -> int:
    for i, x in enumerate(rows):
        if x.row_id == r.row_id:
            return i
    return -1


def _innermost_banner_index(rows: list[Row], stack, r: Row) -> int:
    """Index just after the innermost open banner row — a DATA parent from
    before the banner opened is not eligible."""
    if not stack:
        return 0
    lvl = stack[-1][0]
    ri = _index_of(rows, r)
    for j in range(ri - 1, -1, -1):
        if rows[j].cls in (BANNER, PERIOD_BANNER) and rows[j].level == lvl:
            return j + 1
    return 0


# --------------------------------------------------------------------------
# PASS 3 — the id
# --------------------------------------------------------------------------
_OF_WHICH = re.compile(r"^\s*(?:of\s+which|o/w)\b\s*:?\s*", re.I)


def leaf_id(ancestors: list[str], own_label: str) -> str:
    """'::'-join ancestors + self, then subtotal collapse and the of-which memo.

    Subtotal collapse: consecutive identical segments fold to one.
    Of-which memo: a row whose label starts 'of which' attaches under its
    nearest non-memo ancestor as `<parent>::of_which::<rest>`."""
    own = own_label or ""
    memo = bool(_OF_WHICH.match(own))
    if memo:
        rest = normalize_segment(_OF_WHICH.sub("", own))
        segs = list(ancestors) + ["of_which", rest]
    else:
        segs = list(ancestors) + [normalize_segment(own)]
    out: list[str] = []
    for s in segs:
        if not s:
            continue
        if out and out[-1] == s:
            continue                       # subtotal collapse
        out.append(s)
    return "::".join(out)
