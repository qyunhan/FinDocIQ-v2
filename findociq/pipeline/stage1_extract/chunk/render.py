"""stage1_extract.chunk.render — PDF rendering, image helpers, bank detection, page utilities."""
from __future__ import annotations
import ctypes, io, re
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pdfplumber

from .schema import (
    IMAGE_SCALE, BANKS,
    _pdfium_lock,
)


def parse_pages(pages_field: str) -> list[int]:
    return [int(p) for p in str(pages_field).replace(" ", "").split("+") if p]


# ---------------------------------------------------------------------------
# Invisible text — glyphs the page never paints
# ---------------------------------------------------------------------------
def _obj_fill(obj) -> tuple[int, int, int, int] | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not pdfium_raw.FPDFPageObj_GetFillColor(obj, r, g, b, a):
        return None
    return (r.value, g.value, b.value, a.value)


def _obj_bounds(obj) -> tuple[float, float, float, float]:
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    pdfium_raw.FPDFPageObj_GetBounds(obj, left, bottom, right, top)
    return (left.value, bottom.value, right.value, top.value)


def _covers(outer, inner) -> bool:
    return (outer[0] <= inner[0] + 0.5 and outer[1] <= inner[1] + 0.5
            and outer[2] >= inner[2] - 0.5 and outer[3] >= inner[3] - 0.5)


def invisible_text_objects(page) -> list[tuple[int, str]]:
    """(object index, reason) for every text object on `page` that paints no
    ink a reader could see.

    Three ways a glyph can be there and not be there: text render mode 3, zero
    alpha, or a fill equal to whatever is painted behind it. The background is
    the last FILLED path covering the glyph in paint order, else the page's own
    white — NOT simply "is it white", because white text on a dark banner is
    ordinary visible content (DBS sets its running header in white on a red
    band).

    Colour comes from pdfium, which resolves the colour space for us. That
    matters: the raw component values lie. OCBC sets its body text in a
    Separation space where `1.0` means FULL colorant — solid black — the same
    number that means white in DeviceGray."""
    victims: list[tuple[int, str]] = []
    backdrops: list[tuple[tuple, tuple]] = []
    for i in range(pdfium_raw.FPDFPage_CountObjects(page)):
        obj = pdfium_raw.FPDFPage_GetObject(page, i)
        kind = pdfium_raw.FPDFPageObj_GetType(obj)

        if kind == pdfium_raw.FPDF_PAGEOBJ_PATH:
            fillmode, stroke = ctypes.c_int(), ctypes.c_int()
            if pdfium_raw.FPDFPath_GetDrawMode(obj, fillmode, stroke) and fillmode.value:
                fill = _obj_fill(obj)
                if fill and fill[3] == 255:
                    backdrops.append((_obj_bounds(obj), fill[:3]))
            continue

        if kind != pdfium_raw.FPDF_PAGEOBJ_TEXT:
            continue

        if pdfium_raw.FPDFTextObj_GetTextRenderMode(obj) == \
                pdfium_raw.FPDF_TEXTRENDERMODE_INVISIBLE:
            victims.append((i, "render_mode_invisible"))
            continue

        fill = _obj_fill(obj)
        if fill is None:
            continue
        if fill[3] == 0:
            victims.append((i, "alpha_0"))
            continue

        bounds = _obj_bounds(obj)
        background = (255, 255, 255)
        for shape_bounds, rgb in backdrops:
            if _covers(shape_bounds, bounds):
                background = rgb
        if fill[:3] == background:
            victims.append((i, f"fill{fill[:3]}_on_bg{background}"))
    return victims


def strip_invisible_text(pdf_bytes: bytes) -> tuple[bytes, list[str]]:
    """(pdf without its unpaintable glyphs, one description per removal).

    Why this runs before the extractor sees anything: a glyph that paints no
    ink is still a real character object in the text layer, so EVERY reader
    ingests it — the model reading the PDF and the geometry pass reading
    pdfplumber chars alike. UOB's 2Q26 filing carries a white 'Less:' on the
    'Allowance for credit and other losses' row; the page shows the line bare
    and indented, exactly as the 4Q25 filing prints it, but the extracted label
    came out as 'Less: Allowance for credit and other losses'. That label
    matches no masterlist path, so the row never got an identity — and because
    the invisible glyph sits at the outer margin it also dragged the line's
    ink_x0 left and flattened the row's printed indent.

    The same filing hides runs of numbers ('28%-5%508%...') and a previous one
    ships spreadsheet errors ('#REF!#REF!...') the same way, sitting in data
    regions where they can be read as values.

    Removing a page object forces pdfium to re-serialise that page's content
    stream, which re-computes character advances: vertical positions and every
    line's starting x are preserved exactly, but right-edge x drifts by up to
    ~4pt on a long line and word segmentation can shift by a token. Harmless
    for label matching and indent (both keyed on the line start), and the
    reason a document should be re-extracted rather than half-migrated."""
    doc = pdfium.PdfDocument(pdf_bytes, autoclose=False)
    removed: list[str] = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        victims = invisible_text_objects(page)
        if not victims:
            continue
        # Highest index first: removal renumbers everything after it.
        for obj_index, reason in reversed(victims):
            obj = pdfium_raw.FPDFPage_GetObject(page, obj_index)
            if pdfium_raw.FPDFPage_RemoveObject(page, obj):
                pdfium_raw.FPDFPageObj_Destroy(obj)
                removed.append(f"page {page_index + 1}: {reason}")
        pdfium_raw.FPDFPage_GenerateContent(page)
    if not removed:
        return pdf_bytes, []          # untouched bytes when there is nothing to do
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), removed


def cut_pdf(pdf_path: str, pages_1based: list[int]) -> bytes:
    """Return a new PDF containing only the given (1-based) pages, with any
    text the page never paints removed.

    Sanitising HERE is deliberate: these bytes are both what the extractor is
    handed and what is written to the unit's `pages.pdf`, which the geometry
    stage prefers as its source. One rule, and the two readers cannot disagree
    about what the page says."""
    with _pdfium_lock:
        src = pdfium.PdfDocument(pdf_path)
        dest = pdfium.PdfDocument.new()
        dest.import_pages(src, [p - 1 for p in pages_1based])
        buf = io.BytesIO()
        dest.save(buf)
        cleaned, removed = strip_invisible_text(buf.getvalue())
    for note in removed:
        print(f"   • invisible text removed — {note}")
    return cleaned


_MONTH_TO_QUARTER = {
    "march": "1Q", "jun": "2Q", "september": "3Q", "december": "4Q",
    "mar":   "1Q", "june": "2Q", "sep": "3Q",      "dec":      "4Q",
}
_MONTH_ABBREV = {
    "january": "Jan", "february": "Feb", "april": "Apr", "may": "May",
    "july": "Jul",    "august": "Aug",   "october": "Oct", "november": "Nov",
    "jan": "Jan",     "feb": "Feb",      "apr": "Apr",
    "jul": "Jul",     "aug": "Aug",      "oct": "Oct",     "nov": "Nov",
}


def derive_period(doc_date: str, doc_stem: str = "") -> str:
    """Map a detected doc_date string to a period slug.
    Quarter-end months → "{Q}{YY}" (e.g. "4Q25").
    Other months       → "{Mon}{YY}" (e.g. "Feb26").
    Empty/unparseable  → slug from doc_stem with a loud warning."""
    if not doc_date or not doc_date.strip():
        slug = re.sub(r"[^a-zA-Z0-9]", "", doc_stem)[:12] or "unknown"
        print(f"   ⚠ WARNING: DOC_DATE not detected — using doc_stem slug '{slug}' as period. "
              f"Output filename will not be quarter-keyed.")
        return slug
    m = re.search(
        r'\b(\d{1,2})?\s*([A-Za-z]+)\s+(\d{4})\b', doc_date.strip()
    )
    if not m:
        slug = re.sub(r"[^a-zA-Z0-9]", "", doc_stem)[:12] or "unknown"
        print(f"   ⚠ WARNING: could not parse DOC_DATE '{doc_date}' — using '{slug}' as period.")
        return slug
    month_raw = m.group(2).lower()
    year      = m.group(3)
    yy        = year[-2:]
    if month_raw in _MONTH_TO_QUARTER:
        return f"{_MONTH_TO_QUARTER[month_raw]}{yy}"
    abbrev = _MONTH_ABBREV.get(month_raw, month_raw.capitalize()[:3])
    return f"{abbrev}{yy}"


def detect_bank(pdf_path: str) -> tuple[str | None, str | None]:
    """Scan the first two pages for a bank fingerprint. Returns (key, detected_date)."""
    txt = ""
    try:
        with _pdfium_lock:
            pdf = pdfium.PdfDocument(pdf_path)
            txt = pdf[0].get_textpage().get_text_range()
            if len(pdf) > 1:
                txt += " " + pdf[1].get_textpage().get_text_range()
    except Exception:
        pass
    key = None
    for k, info in BANKS.items():
        if re.search(info["match"], txt, re.I):
            key = k
            break
    m = re.search(r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b", txt)
    return key, (m.group(1) if m else None)


def page_is_narrative(pdf_path: str, page_1based: int, min_numbers: int = 10) -> bool:
    """Cheap deterministic pre-filter: a page with very few numbers is almost
    certainly narrative text (intro / scope / policy) — skip it to avoid an
    empty, billed extraction call. Conservative: only skips clearly text pages."""
    try:
        with _pdfium_lock:
            pdf = pdfium.PdfDocument(pdf_path)
            txt = pdf[page_1based - 1].get_textpage().get_text_range()
    except Exception:
        return False
    return len(re.findall(r"\d[\d,\.]*", txt)) < min_numbers


def page_has_table_structure(pdf_path: str, page_1based: int,
                              min_h_edges: int = 5) -> bool:
    """Return True if pdfplumber detects meaningful horizontal ruling lines on
    the page — the structural signature of a real table. A page with only 2
    h-edges (top/bottom page border) is narrative; a data table has many row
    separators. min_h_edges=5 is conservative: even a 3-row table has 4 lines."""
    if pdfplumber is None:
        return True   # can't tell — don't suppress the retry
    try:
        with _pdfium_lock:
            with pdfplumber.open(pdf_path) as pdf:
                page = pdf.pages[page_1based - 1]
                page_w = page.width
                real_h = [e for e in page.edges
                          if e.get("orientation") == "h"
                          and (e.get("x1", 0) - e.get("x0", 0)) > page_w * 0.10]
                return len(real_h) >= min_h_edges
    except Exception:
        return True   # can't tell — don't suppress the retry


def render_images(pdf_path: str, pages_1based: list[int], scale: float = IMAGE_SCALE) -> list[bytes]:
    """Render the given pages to PNG bytes (used only as a fallback)."""
    with _pdfium_lock:
        src = pdfium.PdfDocument(pdf_path)
        out = []
        for p in pages_1based:
            pil = src[p - 1].render(scale=scale).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            out.append(buf.getvalue())
    return out


def render_images_with_page_numbers(pdf_path: str, pages_1based: list[int],
                                     scale: float = IMAGE_SCALE) -> list[tuple[bytes, int]]:
    """Like render_images but returns (img_bytes, page_number) pairs."""
    with _pdfium_lock:
        src = pdfium.PdfDocument(pdf_path)
        out = []
        for p in pages_1based:
            pil = src[p - 1].render(scale=scale).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            out.append((buf.getvalue(), p))
    return out


def _pil_image_from_bytes(img_bytes: bytes):
    from PIL import Image as _PIL_Image
    return _PIL_Image.open(io.BytesIO(img_bytes))


def _pil_image_to_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def compute_boundary_crop(pdf_path: str, page_num: int,
                           next_anchor_text: str, dpi: int = 150) -> int | None:
    """Return crop y-px just above next_anchor_text on page_num, or None if unsafe.
    Unsafe: anchor not found; landscape page; anchor x0 > 15% width; word to LEFT
    of anchor at same y-band (±3pt) — multi-column signature."""
    if not next_anchor_text:
        return None
    first_token = next_anchor_text.strip().split()[0] if next_anchor_text.strip() else ""
    if not first_token:
        return None
    def _clean_exact(s): return re.sub(r"[^a-z0-9.]", "", s.lower())
    def _clean_alpha(s): return re.sub(r"[^a-z0-9]", "", s.lower())
    first_exact = _clean_exact(first_token)
    first_alpha = _clean_alpha(first_token)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num - 1]
            pw, ph = page.width, page.height
            if pw > ph:
                return None
            words = page.extract_words()
            if not words:
                return None
            anchor_word_obj = None
            for w in words:
                if _clean_exact(w["text"]) == first_exact or _clean_alpha(w["text"]) == first_alpha:
                    anchor_word_obj = w
                    break
            if anchor_word_obj is None:
                return None
            anchor_x0  = float(anchor_word_obj["x0"])
            anchor_top = float(anchor_word_obj["top"])
            if anchor_x0 > pw * 0.15:
                return None
            y_lo, y_hi = anchor_top - 3, anchor_top + 3
            for w in words:
                if w is anchor_word_obj:
                    continue
                if y_lo <= float(w["top"]) <= y_hi and float(w["x0"]) < anchor_x0 - 2:
                    return None
            return max(10, int(anchor_top * dpi / 72) - 25)
    except Exception:
        return None


def get_column_boundaries(pdf_path: str, pages: list[int],
                           min_count: int = 10, tolerance: float = 3.0) -> list[float]:
    """Extract dominant vertical column boundaries from the PDF data region.
    Returns sorted x-coordinates of column dividers (including left and right edges),
    so len(result)-1 == number of data columns. Returns [] on failure."""
    try:
        all_x: dict[float, int] = {}
        with pdfplumber.open(pdf_path) as pdf:
            for pg in pages:
                page = pdf.pages[pg - 1]
                page_w = page.width
                for e in page.edges:
                    if e.get("orientation") != "v":
                        continue
                    x = e["x0"]
                    if x < page_w * 0.20 or x > page_w * 0.98:
                        continue
                    bucket = round(x / tolerance) * tolerance
                    all_x[bucket] = all_x.get(bucket, 0) + 1
        candidates = sorted((cnt, x) for x, cnt in all_x.items() if cnt >= min_count)
        candidates = sorted(x for _, x in candidates)
        merged = []
        for x in candidates:
            if not merged or x - merged[-1] > tolerance * 2:
                merged.append(x)
        return merged
    except Exception:
        return []


def col_boundaries_hint(pdf_path: str, pages: list[int]) -> str:
    """Reserved for future deterministic span pre-computation (see DEVLOG E-07). Not called."""
    return ""
