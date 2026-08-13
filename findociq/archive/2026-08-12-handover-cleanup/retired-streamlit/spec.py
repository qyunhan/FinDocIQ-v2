"""Query-spec contract: registry loader, QuerySpec dataclass, validator.

Deliberately does NOT import slide_kit (and therefore matplotlib) at module
level: slide_kit.py does `import matplotlib` at import time, and matplotlib
is broken/unavailable under the plain system python3 this module (and its
tests) run under. `shorten_institution` and `fetch_series` are imported
lazily, inside the two functions (`load_registry`, `run_query`) that need
them.
"""
import difflib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable

VALID_CHARTS = ["line", "bar", "table"]

MAX_CONCEPTS = 4
SUGGESTION_DISPLAY_CAP = 10


class SpecError(ValueError):
    """Raised for a query spec a user can fix by rereading the message."""


@dataclass
class Registry:
    concepts: dict[str, str]              # concept_key -> representative label
    institutions: list[str]               # full institution names
    institution_aliases: dict[str, str]    # short name -> full name
    periods: list[str]                     # ISO dates, sorted
    col_keys: list[str]
    percent_concepts: set = field(default_factory=set)  # concept_keys whose
    # canonical template_row label carries "(%)" — the general, template-
    # driven signal for percent formatting (never a hardcoded concept_key).


@dataclass
class QuerySpec:
    concepts: list[str]
    institutions: list[str]
    period_start: str
    period_end: str
    column: str = "weighted"
    chart: str = "line"
    title: str | None = None


def load_registry(db_path: str) -> Registry:
    """Build a Registry live from the findb SQLite database."""
    import sys
    from pathlib import Path

    # sys.path bootstrap so `slide_kit` (in findociq/tools/slides/) is importable
    # regardless of caller's cwd.
    tools_dir = str(Path(__file__).resolve().parent.parent / "tools" / "slides")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from slide_kit import shorten_institution  # noqa: E402  (lazy: pulls in matplotlib)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT concept_key, MIN(row_leaf_label) FROM v_cell "
            "WHERE concept_key IS NOT NULL GROUP BY concept_key"
        )
        concepts = {row[0]: row[1] for row in cur.fetchall()}

        cur.execute("SELECT DISTINCT institution FROM document")
        institutions = [row[0] for row in cur.fetchall()]
        institution_aliases = {shorten_institution(name): name for name in institutions}

        cur.execute("SELECT DISTINCT period FROM table_t ORDER BY 1")
        periods = [row[0] for row in cur.fetchall() if row[0] is not None]

        cur.execute(
            "SELECT DISTINCT col_key FROM col_dim WHERE col_key IS NOT NULL AND col_key != ''"
        )
        col_keys = [row[0] for row in cur.fetchall()]

        # Percent-formatted concepts: the general, template-driven signal is
        # the regulatory form's own row label carrying "(%)" — never a
        # hardcoded concept_key (e.g. "nsfr_ratio").
        cur.execute(
            "SELECT DISTINCT concept_key FROM template_row "
            "WHERE canonical_label LIKE '%(!%)%' ESCAPE '!'"
        )
        percent_concepts = {row[0] for row in cur.fetchall() if row[0] is not None}
    finally:
        conn.close()

    return Registry(
        concepts=concepts,
        institutions=institutions,
        institution_aliases=institution_aliases,
        periods=periods,
        col_keys=col_keys,
        percent_concepts=percent_concepts,
    )


def run_query(db_path: str, qs: QuerySpec) -> tuple[dict, int]:
    """Fetch the data slice a QuerySpec describes, via slide_kit.fetch_series.

    slide_kit is imported lazily here (not at module load) so this module
    stays matplotlib-free for callers/tests that never call run_query.
    """
    import sys
    from pathlib import Path

    tools_dir = str(Path(__file__).resolve().parent.parent / "tools" / "slides")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from slide_kit import fetch_series  # noqa: E402  (lazy: pulls in matplotlib)

    data, n = fetch_series(
        db_path,
        qs.concepts,
        col_key=qs.column,
        institutions=qs.institutions,
        period_start=qs.period_start,
        period_end=qs.period_end,
    )
    if n == 0:
        raise SpecError(
            "No data for that slice — concepts="
            f"{', '.join(qs.concepts)}; institutions={', '.join(qs.institutions)}; "
            f"period {qs.period_start} to {qs.period_end}."
        )
    return data, n


def _suggest(name: str, candidates: list[str], n: int = 3) -> list[str]:
    return difflib.get_close_matches(name, candidates, n=n, cutoff=0.5)


def _resolve_concept(raw: str, reg: Registry) -> str:
    if raw in reg.concepts:
        return raw
    pool = list(reg.concepts.keys()) + list(reg.concepts.values())
    suggestions = _suggest(raw, pool)
    msg = f"Unknown concept {raw!r}."
    if suggestions:
        msg += f" Did you mean one of: {', '.join(suggestions[:3])}?"
    else:
        all_keys = sorted(reg.concepts.keys())
        shown = all_keys[:SUGGESTION_DISPLAY_CAP]
        suffix = "..." if len(all_keys) > SUGGESTION_DISPLAY_CAP else ""
        msg += f" Known concepts: {', '.join(shown)}{suffix}"
    raise SpecError(msg)


def _resolve_institution(raw: str, reg: Registry) -> str:
    if raw in reg.institutions:
        return raw
    # case-insensitive alias lookup first
    for short, full in reg.institution_aliases.items():
        if short.lower() == raw.lower():
            return full
    # case-insensitive full-name lookup
    for full in reg.institutions:
        if full.lower() == raw.lower():
            return full
    pool = reg.institutions + list(reg.institution_aliases.keys())
    suggestions = _suggest(raw, pool)
    msg = f"Unknown institution {raw!r}."
    if suggestions:
        msg += f" Did you mean one of: {', '.join(suggestions[:3])}?"
    else:
        msg += f" Known institutions: {', '.join(reg.institutions)}."
    raise SpecError(msg)


def validate_spec(raw: dict, reg: Registry) -> QuerySpec:
    # --- concepts ---
    concepts_in = raw.get("concepts") or []
    if not concepts_in:
        raise SpecError(
            "No concepts given — tell me at least one thing to chart, e.g. "
            f"{', '.join(sorted(reg.concepts.keys())[:3])}."
        )
    if len(concepts_in) > MAX_CONCEPTS:
        raise SpecError(
            f"Too many concepts ({len(concepts_in)}) — please narrow to at most "
            f"{MAX_CONCEPTS} so the chart stays readable."
        )
    concepts = [_resolve_concept(c, reg) for c in concepts_in]

    # --- institutions ---
    institutions_in = raw.get("institutions") or []
    if not institutions_in:
        institutions = list(reg.institutions)
    else:
        institutions = [_resolve_institution(i, reg) for i in institutions_in]

    # --- column ---
    column = raw.get("column", "weighted")
    if column not in reg.col_keys:
        raise SpecError(
            f"Unknown column {column!r}. Valid columns are: {', '.join(reg.col_keys)}."
        )

    # --- chart ---
    chart = raw.get("chart", "line")
    if chart not in VALID_CHARTS:
        raise SpecError(
            f"Unknown chart type {chart!r}. Valid chart types are: {', '.join(VALID_CHARTS)}."
        )

    # --- periods: clamp, don't reject ---
    period_start = raw.get("period_start")
    period_end = raw.get("period_end")
    if not reg.periods:
        raise SpecError("No periods are available in the registry to chart against.")
    lo, hi = min(reg.periods), max(reg.periods)
    if period_start is None:
        period_start = lo
    if period_end is None:
        period_end = hi
    if period_start > period_end:
        period_start, period_end = period_end, period_start
    period_start = min(max(period_start, lo), hi)
    period_end = min(max(period_end, lo), hi)

    title = raw.get("title")

    return QuerySpec(
        concepts=concepts,
        institutions=institutions,
        period_start=period_start,
        period_end=period_end,
        column=column,
        chart=chart,
        title=title,
    )


# ===========================================================================
# NL layer — build_system_prompt / parse_llm_json / nl_to_spec / gemini_llm
# ===========================================================================

def build_system_prompt(reg: Registry) -> str:
    """Fixed instruction text + the registry serialized for the LLM.

    No bank/concept names are hardcoded here — everything after the fixed
    instruction block is generated from `reg`.
    """
    concept_lines = "\n".join(f"  {key}: {label}" for key, label in sorted(reg.concepts.items()))
    institution_lines = "\n".join(
        f"  {name}" + (
            f" (aliases: {', '.join(sorted(a for a, full in reg.institution_aliases.items() if full == name))})"
            if any(full == name for full in reg.institution_aliases.values())
            else ""
        )
        for name in reg.institutions
    )
    period_lines = ", ".join(reg.periods)
    col_key_lines = ", ".join(reg.col_keys)
    chart_lines = ", ".join(VALID_CHARTS)
    percent_lines = ", ".join(sorted(reg.percent_concepts)) or "(none)"

    return f"""You translate a natural-language question about bank regulatory
filings into a single JSON query spec.

Return ONLY a JSON object with keys concepts, institutions, period_start,
period_end, column, chart, title. Use concept KEYS, not labels.

Pick at most {MAX_CONCEPTS} concepts — a request for more will be rejected.

concepts (key: label):
{concept_lines}

These concepts are percentages (formatted with a "%" suffix, not thousands):
{percent_lines}

institutions:
{institution_lines}

periods (ISO dates, choose period_start/period_end from this range):
{period_lines}

column (col_key enum):
{col_key_lines}

chart (enum):
{chart_lines}

title: null or a short human-readable chart title.
"""


def parse_llm_json(text: str) -> dict:
    """Strip ``` fences / leading-trailing prose, parse the first {...} block.

    Robust to realistic LLM chatter: leading prose containing a stray brace,
    trailing prose containing a brace, or a second JSON-ish aside after the
    real object. Tries a balanced decode (json.JSONDecoder.raw_decode) at
    each "{" position in the text and returns the first one that succeeds
    and yields a dict — this is what "first {...} block" means in practice,
    as opposed to a naive first-"{"-to-last-"}" span.
    """
    stripped = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fence_match:
        stripped = fence_match.group(1).strip()

    decoder = json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stripped, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    raise SpecError(f"No JSON object found in LLM response: {text!r}")


def nl_to_spec(question: str, reg: Registry, llm: Callable[[str, str], str]) -> QuerySpec:
    """question -> QuerySpec via an injected LLM transport, with one retry.

    llm(system_prompt, user_text) is called, its response parsed and
    validated; if either step raises SpecError, the error message is
    appended to the user text and the call is retried exactly once. A
    second SpecError propagates to the caller.
    """
    system_prompt = build_system_prompt(reg)
    user_text = question
    try:
        response = llm(system_prompt, user_text)
        raw = parse_llm_json(response)
        return validate_spec(raw, reg)
    except SpecError as e:
        user_text = f"{question}\n\nYour previous answer was rejected: {e}. Return corrected JSON only."
        response = llm(system_prompt, user_text)
        raw = parse_llm_json(response)
        return validate_spec(raw, reg)


def _with_backoff(fn: Callable[[], object], attempts: int = 5, base_delay: float = 2,
                   sleeper: Callable[[float], None] = time.sleep,
                   is_retryable: Callable[[Exception], bool] = lambda e: True):
    """Call fn() with bounded retry + exponential backoff for transient errors.

    Retries up to `attempts` times total. Between attempts, sleeps
    base_delay, base_delay*2, base_delay*4, ... (2, 4, 8, 16 for the
    defaults) via the injectable `sleeper` (real code: time.sleep; tests:
    a recording stub — no real sleeping in unit tests). `is_retryable(e)`
    decides whether a caught exception is worth retrying; a non-retryable
    exception (or the final attempt's exception) is re-raised immediately.
    Pure/transport-agnostic so it can be unit-tested without google-genai.
    """
    delay = base_delay
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            if not is_retryable(e) or attempt == attempts - 1:
                raise
            sleeper(delay)
            delay *= 2


def gemini_llm(system_prompt: str, user_text: str) -> str:
    """Real transport: google-genai client (Vertex AI/ADC — gemini_client.py),
    gemini-3.5-flash, temperature 0. google-genai is imported lazily here so
    the rest of this module works without it installed.

    Transient server errors (ServerError, or APIError with a 5xx code —
    e.g. the documented gemini-3.5-flash 503 "high demand" UNAVAILABLE
    condition) are retried up to 5 attempts with exponential backoff
    (2s/4s/8s/16s) via `_with_backoff`. Client errors (4xx) are never
    retried and propagate on the first attempt.
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    pipeline_dir = str(root / "findociq" / "pipeline")
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from gemini_client import build_client

    from google.genai import errors as genai_errors
    from google.genai import types

    client = build_client()

    def _call():
        return client.models.generate_content(
            model="gemini-3.5-flash",
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0,
                response_mime_type="application/json",
            ),
        )

    def _is_retryable(e: Exception) -> bool:
        if isinstance(e, genai_errors.ServerError):
            return True
        if isinstance(e, genai_errors.APIError):
            return (getattr(e, "code", None) or 0) >= 500
        return False

    response = _with_backoff(_call, attempts=5, base_delay=2, is_retryable=_is_retryable)
    return response.text
