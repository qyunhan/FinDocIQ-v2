import sqlite3
import migrate_ingest_status_keys as mig


def _db(tmp_path, rows):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ingest_status (source_file TEXT PRIMARY KEY, doc_id TEXT)")
    con.executemany("INSERT INTO ingest_status(source_file, doc_id) VALUES (?, ?)", rows)
    con.commit(); con.close()
    return str(db)


def test_rekey_strips_findociq_data_sources_prefix():
    assert mig.rekey("findociq/data/sources/financial_statements/DBS_1Q25_trading_update.pdf") \
        == "financial_statements/DBS_1Q25_trading_update.pdf"


def test_rekey_collapses_nested_bank_year_quarter():
    assert mig.rekey("findociq/data/sources/financial_statements/DBS/2025/1/x.pdf") \
        == "financial_statements/x.pdf"


def test_rekey_is_idempotent_on_bare_key():
    assert mig.rekey("pillar3/OCBC_1Q26_pillar3.pdf") == "pillar3/OCBC_1Q26_pillar3.pdf"


def test_migrate_updates_rows(tmp_path):
    db = _db(tmp_path, [
        ("findociq/data/sources/financial_statements/DBS_1Q25_trading_update.pdf", "DBS_1Q25_trading_update"),
        ("pillar3/OCBC_1Q26_pillar3.pdf", "OCBC_1Q26_pillar3"),  # already bare
    ])
    changed = mig.migrate(db)
    assert changed == 1
    con = sqlite3.connect(db)
    keys = {r[0] for r in con.execute("SELECT source_file FROM ingest_status")}
    con.close()
    assert keys == {
        "financial_statements/DBS_1Q25_trading_update.pdf",
        "pillar3/OCBC_1Q26_pillar3.pdf",
    }


def test_migrate_is_idempotent_second_run(tmp_path):
    db = _db(tmp_path, [
        ("findociq/data/sources/pillar3/x.pdf", "x"),
    ])
    assert mig.migrate(db) == 1
    assert mig.migrate(db) == 0   # nothing left to change
