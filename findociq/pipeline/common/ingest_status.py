"""ingest_status.py — durable per-document pipeline stage/state tracking.

Answers "what stage is this document in, and why did it stop" for the
dashboard's Ingest Status tab, and "is this worth retrying" for an automated
retry sweep. Backed by the `ingest_status` table (schema_v7.sql), keyed by
`source_file` (not doc_id — a STEP 0/1 failure happens before the `document`
row, and therefore doc_id, exists).

Every write here uses its OWN short-lived autocommit connection, deliberately
separate from whatever connection run_doc.py/load_v7.py is using for the real
extracted data. This matters: load_v7.load_units() rolls back its whole
transaction on a load failure (the exact bug class hit on
OCBC_4Q25_Media_Release_and_Financial_Highlights, "row 4: cell 6 has no leaf
column") — a status write inside that same transaction would vanish with it.

STAGES, in pipeline order (mirrors run_doc.py's STEP 0-7):
    scan | toc | extract | geometry | load | concepts | verify | xlsx | sync_bq | done
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

STAGES = ["scan", "toc", "extract", "geometry", "load", "concepts", "verify", "xlsx",
          "sync_bq", "done"]

# Known transient signatures: worth retrying as-is. Anything else defaults to
# structural (a real bug/data-shape issue — retrying won't help, needs a fix).
_TRANSIENT_PATTERNS = [
    r"\b503\b", r"\btimeout\b", r"timed out", r"deadline exceeded",
    r"connection reset", r"connection refused", r"connection aborted",
    r"metadata server", r"service account info is missing",
    r"temporarily unavailable", r"rate limit", r"\b429\b",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_error(error) -> str:
    return str(error).strip()


def classify_error(error) -> str:
    """Pattern-match a raw error/exception into 'transient' or 'structural'.
    Callers needing the escalation rule (same error recurring -> structural
    regardless) should use mark(), which applies it automatically."""
    msg = _norm_error(error).lower()
    for pat in _TRANSIENT_PATTERNS:
        if re.search(pat, msg):
            return "transient"
    return "structural"


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    return con


def mark(db_path: str, source_file: str, stage: str, state: str, *,
         doc_id: str | None = None, bank: str | None = None,
         period: str | None = None, family: str | None = None,
         error=None) -> None:
    """Record this document's status. Opens+closes its own connection so the
    write commits independently of any caller-side transaction.

    stage/state: see module docstring / schema CHECK constraints.
    error: the exception (or message) on a 'failed' state. Classified via
    classify_error(), with an escalation rule: if the SAME (stage,
    error_message) was already the last recorded failure for this
    source_file, force error_class='structural' regardless of the raw
    classification — a 503-shaped error that recurs identically is not
    actually transient.
    """
    assert stage in STAGES, f"unknown stage {stage!r}"
    assert state in ("pending", "running", "ok", "failed"), f"unknown state {state!r}"

    now = _now()

    con = _connect(db_path)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT stage, error_class, error_message, attempt_count "
            "FROM ingest_status WHERE source_file = ?",
            (source_file,),
        )
        prev = cur.fetchone()
        prev_stage, prev_error_class, prev_error_message, prev_attempt_count = (
            prev if prev is not None else (None, None, None, 0))

        # error_class/error_message are STICKY across the non-failed marks of
        # the same attempt (scan-ok, toc-ok, ...) so the escalation check below
        # can compare this failure against the PRIOR ATTEMPT's failure, not
        # against a same-attempt success that would otherwise have clobbered
        # it. Only three things touch them: a new failure (recompute), a full
        # done+ok (clear), or nothing (carry forward as-is).
        if state == "failed":
            error_message = _norm_error(error)
            error_class = classify_error(error)
            if (error_class == "transient" and prev_stage == stage
                    and prev_error_message == error_message):
                error_class = "structural"
        elif stage == "done" and state == "ok":
            error_class, error_message = None, None
        else:
            error_class, error_message = prev_error_class, prev_error_message

        is_new_attempt = stage == "scan" and state == "running"
        attempt_count = prev_attempt_count + 1 if is_new_attempt else prev_attempt_count
        last_attempt_at = now if is_new_attempt else None  # None -> keep existing, below

        cur.execute(
            """
            INSERT INTO ingest_status
                (source_file, doc_id, bank, period, family, stage, state,
                 error_class, error_message, attempt_count, last_attempt_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                doc_id          = COALESCE(excluded.doc_id, ingest_status.doc_id),
                bank            = COALESCE(excluded.bank, ingest_status.bank),
                period          = COALESCE(excluded.period, ingest_status.period),
                family          = COALESCE(excluded.family, ingest_status.family),
                stage           = excluded.stage,
                state           = excluded.state,
                error_class     = excluded.error_class,
                error_message   = excluded.error_message,
                attempt_count   = excluded.attempt_count,
                last_attempt_at = COALESCE(excluded.last_attempt_at, ingest_status.last_attempt_at),
                updated_at      = excluded.updated_at
            """,
            (source_file, doc_id, bank, period, family, stage, state,
             error_class, error_message, attempt_count, last_attempt_at, now),
        )
    finally:
        con.close()
