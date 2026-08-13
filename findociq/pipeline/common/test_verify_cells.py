"""Unit tests for verify_cells helpers (no DB, no PDFs, no API).

Run: python3 findociq/pipeline/test_verify_cells.py
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # pipeline/ on path
from common import verify_cells as vc


def check(name, cond, got=None):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   GOT: {got!r}"))
    return cond


ok = True

# --- norm_token ---------------------------------------------------------

got = vc.norm_token("334,152")
ok &= check("plain thousands-comma int", got == 334152.0, got)

got = vc.norm_token("(1,234)")
ok &= check("parens -> negative", got == -1234.0, got)

got = vc.norm_token("116.4%")
ok &= check("trailing percent stripped, value unscaled", got == 116.4, got)

got = vc.norm_token("1,234#")
ok &= check("footnote glyph stripped", got == 1234.0, got)

got = vc.norm_token("abc")
ok &= check("non-numeric -> None", got is None, got)

got = vc.norm_token("-")
ok &= check("dash -> None", got is None, got)

got = vc.norm_token("")
ok &= check("empty -> None", got is None, got)

got = vc.norm_token(None)
ok &= check("None -> None", got is None, got)

got = vc.norm_token("1,234.5*")
ok &= check("decimal + star glyph", got == 1234.5, got)

got = vc.norm_token("(45.6%)")
ok &= check("parens + percent combined -> negative unscaled", got == -45.6, got)

# --- norm_token: currency prefixes (D27 fix) ------------------------------

got = vc.norm_token("S$1.63")
ok &= check("S$ prefix stripped", got == 1.63, got)

got = vc.norm_token("US$450")
ok &= check("US$ prefix stripped (checked before bare $)", got == 450.0, got)

got = vc.norm_token("$1,234.56")
ok &= check("bare $ prefix stripped, thousands-comma still works", got == 1234.56, got)

got = vc.norm_token("SGD 500")
ok &= check("SGD prefix + space stripped", got == 500.0, got)

got = vc.norm_token("USD100")
ok &= check("USD prefix (no space) stripped", got == 100.0, got)

got = vc.norm_token("RM45")
ok &= check("RM prefix stripped", got == 45.0, got)

got = vc.norm_token("HK$3.2")
ok &= check("HK$ prefix stripped", got == 3.2, got)

got = vc.norm_token("s$1.63")
ok &= check("prefix match is case-insensitive", got == 1.63, got)

got = vc.norm_token("S$  1.63")
ok &= check("prefix strip tolerates extra whitespace", got == 1.63, got)

got = vc.norm_token("(S$45.6)")
ok &= check("negative-in-parens combined with a currency prefix", got == -45.6, got)

got = vc.norm_token("(US$1,234)")
ok &= check("negative-in-parens + prefix + thousands-comma", got == -1234.0, got)

# Magnitude suffixes (b/bn/m/k) are NOT handled anywhere in norm_token today
# (only the currency prefix is new territory here) -- stripping the prefix
# alone still leaves a non-numeric remainder, so this correctly still
# resolves to None. Documented so a future magnitude-suffix change doesn't
# silently regress this boundary.
got = vc.norm_token("S$2.1b")
ok &= check("prefix + magnitude suffix: suffix still unhandled -> None", got is None, got)

got = vc.norm_token("S$ 2.1bn")
ok &= check("prefix + space + magnitude suffix -> None", got is None, got)

got = vc.norm_token("US$450m")
ok &= check("US$ prefix + magnitude suffix -> None", got is None, got)

# Non-prefixed existing behaviour must be unchanged by the prefix-stripping code.
got = vc.norm_token("334,152")
ok &= check("non-prefixed thousands-comma int unchanged", got == 334152.0, got)

got = vc.norm_token("(1,234)")
ok &= check("non-prefixed parens negative unchanged", got == -1234.0, got)

got = vc.norm_token("116.4%")
ok &= check("non-prefixed percent unchanged", got == 116.4, got)

got = vc.norm_token("-")
ok &= check("bare dash still -> None (not mistaken for a prefix remainder)", got is None, got)

# --- parse_page_range ----------------------------------------------------

got = vc.parse_page_range("95")
ok &= check("single page", got == [95], got)

got = vc.parse_page_range("75-76")
ok &= check("page range", got == [75, 76], got)

# --- build_lines: word -> physical line clustering ------------------------

def w(text, top, x0):
    return {"text": text, "top": top, "x0": x0}

words = [
    w("14", 100.0, 10),
    w("Total", 100.3, 20),
    w("ASF", 100.1, 60),
    w("44,212", 100.2, 300),
    w("Next", 112.0, 10),
    w("line", 112.1, 40),
]
lines = vc.build_lines(words, tol=3.0)
ok &= check("clusters into 2 physical lines", len(lines) == 2, len(lines))
ok &= check("line0 x-ordered tokens", lines[0]["tokens"] == ["14", "Total", "ASF", "44,212"], lines[0]["tokens"])
ok &= check("line1 tokens", lines[1]["tokens"] == ["Next", "line"], lines[1]["tokens"])

# --- values_on -------------------------------------------------------------

got = vc.values_on(lines)
ok &= check("values_on collects numeric tokens", sorted(got) == sorted([14.0, 44212.0]), got)

# --- normalize_label / anchor_lines_for_row --------------------------------

got = vc.normalize_label("Capital:")
ok &= check("normalize_label strips punctuation", got == "capital", got)

# Simple unambiguous single-line case.
simple_lines = vc.build_lines([
    w("14", 100.0, 10), w("Total", 100.0, 20), w("ASF", 100.0, 60), w("44,212", 100.0, 300),
    w("15", 112.0, 10), w("Total", 112.0, 20), w("RSF", 112.0, 60), w("39,000", 112.0, 300),
], tol=3.0)
anchors = vc.anchor_lines_for_row("Total ASF", "14", simple_lines)
ok &= check("unambiguous label anchors exactly one line", anchors == [0], anchors)

# Wrapped-label case: the row label is split across two physical lines in the
# PDF (long label wraps), value sits on the second physical line only.
wrapped_lines = vc.build_lines([
    w("20", 100.0, 10), w("Performing", 100.0, 20), w("loans", 100.0, 60),
    w("to", 108.0, 20), w("non-financial", 108.0, 40), w("corporates", 108.0, 90),
    w("12,345", 108.0, 300),
], tol=3.0)
anchors = vc.anchor_lines_for_row(
    "Performing loans to non-financial corporates, loans to retail", "20", wrapped_lines)
ok &= check("wrapped label still anchors to its first physical line", anchors == [0], anchors)
# tier-line check must look at the anchor line PLUS next line to find the value
window_values = vc.values_on([wrapped_lines[i] for i in [0, 1] if i < len(wrapped_lines)])
ok &= check("value found in anchor+next-line window", 12345.0 in window_values, window_values)

# Ambiguous case: two lines both plausibly match the row label/line_no -- must
# NOT guess; anchor_lines_for_row should report both (caller falls back to
# tier-page rather than picking one).
ambiguous_lines = vc.build_lines([
    w("9", 100.0, 10), w("Other", 100.0, 20), w("assets", 100.0, 60), w("1,000", 100.0, 300),
    w("9", 112.0, 10), w("Other", 112.0, 20), w("assets", 112.0, 60), w("2,000", 112.0, 300),
], tol=3.0)
anchors = vc.anchor_lines_for_row("Other assets", "9", ambiguous_lines)
ok &= check("ambiguous duplicate label yields 2 anchors (not exactly one)", len(anchors) == 2, anchors)

# --- multiset containment helper -------------------------------------------

got_missing = vc.missing_values([44212.0, 1531.0, 6905.0], [44212.0, 6905.0])
ok &= check("all wanted values found -> no missing", got_missing == [], got_missing)

got_missing = vc.missing_values([44212.0, 1531.0], [44212.0, 9999.0])
ok &= check("missing value reported", got_missing == [9999.0], got_missing)

got_missing = vc.missing_values([5.0], [5.0, 5.0])
ok &= check("multiset containment: duplicate wanted value needs 2 occurrences", got_missing == [5.0], got_missing)

# --- words_from_chars: letter-spacing-safe char-level word extraction ------

class FakePage:
    def __init__(self, chars):
        self.chars = chars


def cw(text, x0, top, width=6.0):
    """One synthetic char dict."""
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top}


def make_word_chars(text, start_x, top, char_width=6.0, gap=0.1):
    """Build a run of char dicts for `text`, each char_width wide, separated
    by a sub-point `gap` (mimics normal same-word letter spacing)."""
    chars = []
    x = start_x
    for ch in text:
        chars.append(cw(ch, x, top, width=char_width))
        x += char_width + gap
    return chars


# Test A: token building, with a filtered-out letter-spacing blank glyph
# sitting between the two numbers.
num1 = "48,590"
num2 = "1,200"
top_a = 100.0
chars_a = make_word_chars(num1, start_x=10.0, top=top_a, char_width=6.0, gap=0.1)
last_x1_num1 = chars_a[-1]["x1"]
blank_char = cw(" ", last_x1_num1 + 2.0, top_a, width=6.0)
chars_a.append(blank_char)
num2_start = last_x1_num1 + 20.0
chars_a.extend(make_word_chars(num2, start_x=num2_start, top=top_a, char_width=6.0, gap=0.1))

words_a = vc.words_from_chars(FakePage(chars_a))
tokens_a = [wd["text"] for wd in words_a]
ok &= check("token building: exactly 2 tokens", len(tokens_a) == 2, tokens_a)
ok &= check("token building: tokens are the two numbers in x0 order", tokens_a == [num1, num2], tokens_a)
ok &= check("token building: blank glyph text never appears standalone", all(t.strip() for t in tokens_a), tokens_a)

# Test B: adaptive per-page threshold.
# Scenario 1: wide glyphs (~10pt wide) -> thr ~5, a 4pt gap must NOT split.
wide_chars = []
x = 0.0
for ch in "AB":
    wide_chars.append(cw(ch, x, 200.0, width=10.0))
    x += 10.0
x += 4.0  # 4pt gap
for ch in "CD":
    wide_chars.append(cw(ch, x, 200.0, width=10.0))
    x += 10.0

words_wide = vc.words_from_chars(FakePage(wide_chars))
tokens_wide = [wd["text"] for wd in words_wide]
ok &= check("adaptive threshold: wide glyphs, 4pt gap joins into one token", tokens_wide == ["ABCD"], tokens_wide)

# Scenario 2: narrow glyphs (~3pt wide) -> thr ~1.5, the same 4pt gap SHOULD split.
narrow_chars = []
x = 0.0
for ch in "AB":
    narrow_chars.append(cw(ch, x, 200.0, width=3.0))
    x += 3.0
x += 4.0  # same 4pt gap
for ch in "CD":
    narrow_chars.append(cw(ch, x, 200.0, width=3.0))
    x += 3.0

words_narrow = vc.words_from_chars(FakePage(narrow_chars))
tokens_narrow = [wd["text"] for wd in words_narrow]
ok &= check("adaptive threshold: narrow glyphs, 4pt gap splits into two tokens", tokens_narrow == ["AB", "CD"], tokens_narrow)

# Test C: empty page -> [].
got_empty = vc.words_from_chars(FakePage([]))
ok &= check("empty page chars -> []", got_empty == [], got_empty)

got_blank_only = vc.words_from_chars(FakePage([cw(" ", 0.0, 100.0), cw("  ", 6.0, 100.0)]))
ok &= check("all-blank chars -> []", got_blank_only == [], got_blank_only)

print()
print("ALL PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
