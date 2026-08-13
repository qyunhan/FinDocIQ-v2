"""Plain check() script for the CONCEPT RESOLUTION LAYER (NO pytest).
Exit 0 all-pass / 1 any-fail.  No network — the LLM step is not exercised here.

Run:  python3 findociq/pipeline/concept/test_concept.py

Covers:
  * normalize.norm — footnote stripping (unicode ¹ + glued 'EXPENSES1'), '&'->'and'
    fold, punctuation/whitespace, consistency with the loader's base rule.
  * load_dictionary — YAML expansion into wildcard concept_map rows,
    map_table_type_norm, alias-collision detection, ensure_schema idempotency.
  * resolve_deterministic — exact-match stamping on a real synthetic load,
    structural skips (date/note/no-alpha), scoped-vs-wildcard preference,
    idempotency (re-run re-stamps identically, does not re-log).
  * validate — additive-identity PASS and FAIL, uniqueness dup flag, nature
    checks (flow_as_at, as_at_magnitude) via pure-function unit tests.
  * load_dictionary — scoped_aliases resolution + the corpus ambiguity gate
    (a label seen under >=2 real table_type_norm buckets is not wildcarded).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on path
from concept.load_dictionary import (ensure_schema, load_concepts,  # noqa: E402
                                     load_into_concept_map, map_table_type_norm)
from concept.normalize import norm  # noqa: E402
from concept.resolve_deterministic import (build_lookup, resolve_deterministic,  # noqa: E402
                                           skip_reason)
from concept.validate import (_as_at_magnitude_flags, _flow_as_at_flags,  # noqa: E402
                              validate)
from pass2.load_v7 import load_units  # noqa: E402
from pass2.schema import Extraction, GCell, GColumn, GRow, GTable  # noqa: E402

_REPO = Path(__file__).resolve().parents[3]
_SCHEMA = _REPO / "findociq/schema/schema_v7.sql"
_FAILS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _FAILS
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILS += 1
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not cond else ""))


def _cells(*vals: str) -> list[GCell]:
    return [GCell(value=v) for v in vals]


# ===========================================================================
# 1) NORMALISE
# ===========================================================================
def normalize_tests() -> None:
    print("normalize.norm")
    check("'Subordinated term debts¹' strips superscript footnote",
          norm("Subordinated term debts¹") == "subordinated term debts",
          norm("Subordinated term debts¹"))
    check("'Net fee & commission income' folds '&'->'and'",
          norm("Net fee & commission income") == "net fee and commission income",
          norm("Net fee & commission income"))
    check("'&' fold == the spelled-out alias",
          norm("Net fee & commission income") == norm("net fee and commission income"))
    check("'EXPENSES1' glued footnote -> 'expenses'",
          norm("EXPENSES1") == "expenses", norm("EXPENSES1"))
    check("'CET1 ratio' keeps the real digit (guard <5 letters)",
          norm("CET1 ratio") == "cet1 ratio", norm("CET1 ratio"))
    check("'Stage 3' keeps un-glued digit", norm("Stage 3") == "stage 3", norm("Stage 3"))
    check("'Less: Operating expenses' strips leading sign marker",
          norm("Less: Operating expenses") == "operating expenses",
          norm("Less: Operating expenses"))
    check("'Less: Total expenses' -> 'total expenses' (matches opex alias)",
          norm("Less: Total expenses") == "total expenses", norm("Less: Total expenses"))
    check("'Add: Share of profit...' strips leading 'Add:'",
          norm("Add: Share of profit of associates and joint ventures")
          == "share of profit of associates and joint ventures",
          norm("Add: Share of profit of associates and joint ventures"))
    check("mid-string 'less' NOT stripped ('Fees, less rebates')",
          norm("Fees, less rebates") == "fees less rebates", norm("Fees, less rebates"))
    check("'Greater China (1)' strips '(1)'", norm("Greater China (1)") == "greater china",
          norm("Greater China (1)"))
    check("punctuation -> spaces + ws collapse",
          norm("  Loans/advances  to   customers ") == "loans advances to customers",
          norm("  Loans/advances  to   customers "))
    check("norm(None) -> ''", norm(None) == "")
    check("no-alpha detected", skip_reason("2,345", norm("2,345")) == "no-alpha")
    check("note skip", skip_reason("Note 3", norm("Note 3")) == "note")
    check("date skip '31 December 2025'",
          skip_reason("31 December 2025", norm("31 December 2025")) == "date/period",
          str(skip_reason("31 December 2025", norm("31 December 2025"))))
    check("real concept NOT skipped",
          skip_reason("Net interest income", norm("Net interest income")) is None)


# ===========================================================================
# 2) LOAD DICTIONARY
# ===========================================================================
def dictionary_tests(db: Path) -> None:
    print("\nload_dictionary")
    check("map_table_type_norm income statement",
          map_table_type_norm("unaudited_consolidated_income_statement") == "income_statement")
    check("map_table_type_norm balance sheet",
          map_table_type_norm("unaudited_balance_sheets") == "balance_sheet")
    check("map_table_type_norm nsfr", map_table_type_norm("nsfr") == "nsfr")
    check("map_table_type_norm unknown -> '*'",
          map_table_type_norm("movements_in_level_3") == "*")

    concepts = load_concepts()
    check("dictionary parsed (>=30 concepts)", len(concepts) >= 30, str(len(concepts)))
    check("derived concepts present (ratios)",
          any(c["kind"] == "derived" for c in concepts))

    con = sqlite3.connect(db)
    ensure_schema(con)
    summ = load_into_concept_map(con)
    check("wildcard rows inserted (>0)", summ["wildcard_rows_inserted"] > 0,
          str(summ["wildcard_rows_inserted"]))
    n_star = con.execute("SELECT COUNT(*) FROM concept_map WHERE table_type='*'").fetchone()[0]
    n_scoped = con.execute(
        "SELECT COUNT(*) FROM concept_map WHERE table_type NOT IN ('*','nsfr')").fetchone()[0]
    check("wildcard + scoped rows == wildcard_rows_total",
          n_star + n_scoped == summ["wildcard_rows_total"],
          f"{n_star}+{n_scoped} vs {summ['wildcard_rows_total']}")
    check("dictionary contributes >0 type-scoped rows (scoped_aliases)", n_scoped > 0,
          str(n_scoped))
    n_nsfr = con.execute("SELECT COUNT(*) FROM concept_map WHERE table_type='nsfr'").fetchone()[0]
    check("19 NSFR rows preserved", n_nsfr == 19, str(n_nsfr))
    check("NSFR rows carry table_type_norm='nsfr'",
          con.execute("SELECT COUNT(*) FROM concept_map WHERE table_type='nsfr' "
                      "AND table_type_norm='nsfr'").fetchone()[0] == 19)
    # idempotent second load inserts nothing new
    summ2 = load_into_concept_map(con)
    check("dictionary load idempotent (0 new on re-run)",
          summ2["wildcard_rows_inserted"] == 0, str(summ2["wildcard_rows_inserted"]))

    # scoped (nsfr) beats wildcard for 'net stable funding ratio'
    resolve = build_lookup(con)
    check("scoped nsfr wins for 'net stable funding ratio'",
          resolve(norm("Net stable funding ratio"), "nsfr") == "nsfr_ratio",
          str(resolve(norm("Net stable funding ratio"), "nsfr")))
    check("wildcard used for same label in a non-nsfr table",
          resolve(norm("Net stable funding ratio"), "income_statement")
          == "reg.liquidity.nsfr_ratio",
          str(resolve(norm("Net stable funding ratio"), "income_statement")))
    check("'interest income' -> pnl.nii.interest_income",
          resolve(norm("Interest income"), "income_statement") == "pnl.nii.interest_income")

    # scoped_aliases: the SAME label resolves to different concepts depending on
    # the row's table type -- "ECL Stage 3 (SP)" is the P&L charge in an
    # income_statement table, a balance in a credit_quality/customer_loans one.
    check("'ecl stage 3 (sp)' in income_statement -> flow concept",
          resolve(norm("ECL Stage 3 (SP)"), "income_statement") == "pnl.provisions.stage3_sp",
          str(resolve(norm("ECL Stage 3 (SP)"), "income_statement")))
    check("'ecl stage 3 (sp)' in credit_quality -> stock concept",
          resolve(norm("ECL Stage 3 (SP)"), "credit_quality") == "bs.credit.allowances_stage3_sp",
          str(resolve(norm("ECL Stage 3 (SP)"), "credit_quality")))
    check("'ecl stage 3 (sp)' in customer_loans -> stock concept",
          resolve(norm("ECL Stage 3 (SP)"), "customer_loans") == "bs.credit.allowances_stage3_sp",
          str(resolve(norm("ECL Stage 3 (SP)"), "customer_loans")))
    check("'ecl stage 3 (sp)' in an unrelated table -> unmatched (no wildcard claims it)",
          resolve(norm("ECL Stage 3 (SP)"), "balance_sheet") is None,
          str(resolve(norm("ECL Stage 3 (SP)"), "balance_sheet")))
    con.close()

    # dictionary-lint: every concept declares `nature` (load_concepts() would
    # KeyError otherwise -- this just makes the guarantee an explicit, named test)
    print("\nnature field (accounting flow/stock classification)")
    valid_natures = {"flow", "stock", "ratio_flow", "ratio_point"}
    check("every concept has a nature in the allowed set",
          all(c.get("nature") in valid_natures for c in concepts),
          str([c["key"] for c in concepts if c.get("nature") not in valid_natures]))
    check("pnl.provisions.total is flow",
          next(c["nature"] for c in concepts if c["key"] == "pnl.provisions.total") == "flow")
    check("bs.credit.allowances_total is stock",
          next(c["nature"] for c in concepts if c["key"] == "bs.credit.allowances_total") == "stock")


def ambiguity_gate_tests(db: Path) -> None:
    """load_into_concept_map must NOT seed a wildcard alias for a label that's
    ambiguous in the observed corpus (seen under >=2 distinct real table_type_norm
    buckets) -- this is what would have silently let pnl.provisions.total also
    claim a Pillar3 balance row before this fix. Uses a synthetic dictionary +
    a synthetic corpus so the gate is tested in isolation from the real 40
    concepts."""
    print("\nload_dictionary ambiguity gate (synthetic corpus)")
    con = sqlite3.connect(db)
    ensure_schema(con)
    con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                "VALUES ('AMB','Synthetic Bank','financial_stmt','2025-12-31')")
    for tid, tt in [("t1", "unaudited_consolidated_income_statement"),
                    ("t2", "allowances_for_loans_and_other_assets")]:
        con.execute("INSERT INTO table_t(doc_id,table_id,table_title,table_type,unit) "
                    "VALUES ('AMB',?,?,?,'$m')", (tid, tt, tt))
        con.execute("INSERT INTO row_dim(doc_id,table_id,row_id,row_leaf_label,row_hierarchy) "
                    "VALUES ('AMB',?,1,'Total allowances',1)", (tid,))
    # a second label present under only ONE bucket -- the gate should leave it alone
    con.execute("INSERT INTO table_t(doc_id,table_id,table_title,table_type,unit) "
                "VALUES ('AMB','t3','income statement','unaudited_consolidated_income_statement','$m')")
    con.execute("INSERT INTO row_dim(doc_id,table_id,row_id,row_leaf_label,row_hierarchy) "
                "VALUES ('AMB','t3',1,'Interest income',1)")
    con.commit()

    synthetic_concepts = [
        dict(key="test.flow_concept", name="Test flow concept", kind="line_item",
             nature="flow", unit="currency", aliases=["Total allowances"],
             scoped_aliases={}, formula=None),
        dict(key="pnl.nii.interest_income", name="Interest income (gross)",
             kind="line_item", nature="flow", unit="currency",
             aliases=["Interest income"], scoped_aliases={}, formula=None),
    ]
    summ = load_into_concept_map(con, synthetic_concepts)
    check("ambiguous label ('Total allowances', seen under 2 buckets) skipped",
          any(a["label_norm"] == "total allowances" for a in summ["ambiguous_skipped"]),
          str(summ["ambiguous_skipped"]))
    check("unambiguous label ('Interest income', 1 bucket) still seeded",
          con.execute("SELECT concept_key FROM concept_map WHERE table_type='*' "
                      "AND label_norm='interest income'").fetchone()
          == ("pnl.nii.interest_income",))
    check("no wildcard row was inserted for the ambiguous label",
          con.execute("SELECT COUNT(*) FROM concept_map WHERE table_type='*' "
                      "AND label_norm='total allowances'").fetchone()[0] == 0)
    con.close()


def _cell_row(doc="D", tid="T", row_id=1, key="pnl.provisions.total", label="row",
             span="1H", inst="Bank", period="2025-12-31", seg="SEG_TOTAL",
             geo="GLOBAL", val=100.0) -> tuple:
    """Build one v_cell_sumsafe-shaped row tuple in the order
    _flow_as_at_flags/_as_at_magnitude_flags expect (see validate.py's
    _DOC_ID.._VALUE_NUM index constants)."""
    return (doc, tid, row_id, key, label, span, inst, period, seg, geo, val)


def nature_check_unit_tests() -> None:
    """Pure-function tests for concept.validate's two nature checks -- no DB
    fixture needed, exercises exactly the logic validate() wires up against
    v_cell_sumsafe."""
    print("\nvalidate() nature checks (pure-function units)")

    # (d1) flow_as_at ------------------------------------------------------
    nature_by_key = {"pnl.provisions.total": "flow", "bs.credit.allowances_total": "stock"}
    rows = [
        _cell_row(key="pnl.provisions.total", span="1H", label="Provisions charge"),
        _cell_row(key="pnl.provisions.total", span="as_at", label="Total allowances"),
        _cell_row(key="bs.credit.allowances_total", span="as_at", label="Total allowances"),
    ]
    checked, failed = _flow_as_at_flags(rows, nature_by_key)
    check("flow_as_at: only flow-concept rows counted (2, not the stock row)",
          checked == 2, str(checked))
    check("flow_as_at: catches the flow row stamped as_at",
          len(failed) == 1 and "pnl.provisions.total" in failed[0], str(failed))
    check("flow_as_at: a correctly-spanned flow row does not flag",
          not any("Provisions charge" in f for f in failed), str(failed))
    check("flow_as_at: a stock concept at as_at is fine (not counted, not flagged)",
          not any("bs.credit.allowances_total" in f for f in failed), str(failed))

    # (d2) as_at_magnitude ---------------------------------------------------
    rows2 = [
        _cell_row(key="pnl.provisions.total", span="as_at", val=6441.0),   # the real bug shape
        _cell_row(key="pnl.provisions.total", span="1H", val=458.0),
        _cell_row(key="bs.liabilities.customer_deposits", span="as_at", val=482837.0),
        _cell_row(key="bs.liabilities.customer_deposits", span="2Q", val=480000.0),  # within 2x -- fine
    ]
    checked2, failed2 = _as_at_magnitude_flags(rows2)
    check("as_at_magnitude: flags the >2x mismatch (6441 vs 458)",
          any("pnl.provisions.total" in f for f in failed2), str(failed2))
    check("as_at_magnitude: does NOT flag a within-tolerance pair (482837 vs 480000)",
          not any("bs.liabilities.customer_deposits" in f for f in failed2), str(failed2))
    check("as_at_magnitude: skips a group with >1 as_at candidate (ambiguous, not a clean pair)",
          _as_at_magnitude_flags([
              _cell_row(key="x", span="as_at", val=100.0),
              _cell_row(key="x", span="as_at", val=200.0),
              _cell_row(key="x", span="1H", val=999.0),
          ])[1] == [])


# ===========================================================================
# 3) DETERMINISTIC STAMP (real synthetic load) + 4) VALIDATE
# ===========================================================================
def stamp_and_validate_tests(db: Path) -> None:
    print("\nresolve_deterministic + validate (synthetic load)")

    # income statement, ARITHMETICALLY CONSISTENT (net = ii - ie; income = net + non-int)
    is_ok = GTable(
        title="Unaudited consolidated income statement",
        label_header="$m",
        columns=[GColumn(group="2025", leaf="$m")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Interest income", values=_cells("100")),
            GRow(row_id="2", row_type="data", level=1, label="Interest expense", values=_cells("30")),
            GRow(row_id="3", row_type="data", level=1, label="Net interest income", values=_cells("70")),
            GRow(row_id="4", row_type="data", level=1, label="Non-interest income", values=_cells("40")),
            GRow(row_id="5", row_type="data", level=1, label="Total income", values=_cells("110")),
            GRow(row_id="6", row_type="data", level=1, label="Net fee & commission income", values=_cells("25")),
            GRow(row_id="7", row_type="total", level=1, label="Total expenses", values=_cells("55")),
            GRow(row_id="8", row_type="data", level=1, label="EXPENSES1", values=_cells("55")),
            GRow(row_id="9", row_type="data", level=1, label="Subordinated term debts¹", values=_cells("12")),
            GRow(row_id="10", row_type="note", level=1, label="Note 1", values=_cells("")),
            GRow(row_id="11", row_type="data", level=1, label="31 December 2025", values=_cells("")),
        ],
    )
    # income statement, ARITHMETICALLY BROKEN (net != ii - ie) -> additive FAIL
    is_bad = GTable(
        title="Unaudited consolidated income statement bad",
        label_header="$m",
        columns=[GColumn(group="2024", leaf="$m")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Interest income", values=_cells("100")),
            GRow(row_id="2", row_type="data", level=1, label="Interest expense", values=_cells("30")),
            GRow(row_id="3", row_type="data", level=1, label="Net interest income", values=_cells("999")),
        ],
    )
    # uniqueness: two rows stamp the same concept in one table
    dup = GTable(
        title="Selected balance sheet items",
        label_header="$m",
        columns=[GColumn(group="2025", leaf="$m")],
        rows=[
            GRow(row_id="1", row_type="data", level=1, label="Total assets", values=_cells("500")),
            GRow(row_id="2", row_type="data", level=1, label="Total assets", values=_cells("500")),
        ],
    )

    con = sqlite3.connect(db)
    con.execute("INSERT INTO document(doc_id,institution,doc_family,doc_period) "
                "VALUES ('SYN','Synthetic Bank','financial_stmt','2025-12-31')")
    con.execute("INSERT INTO section(doc_id,section_id,section_no,section_title,"
                "section_level,parent_section,seq) VALUES ('SYN','s1','1','S',1,NULL,1)")
    con.commit()
    con.close()

    with tempfile.TemporaryDirectory() as td:
        parsed = Path(td) / "parsed.json"
        parsed.write_text(json.dumps(Extraction(tables=[is_ok, is_bad, dup]).model_dump()))
        load_units(str(db), "SYN", [dict(section_id="s1", pages=[1], parsed_path=str(parsed))])

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    ensure_schema(con)
    load_into_concept_map(con)
    det = resolve_deterministic(con)

    def key_of(label: str) -> str | None:
        row = con.execute("SELECT concept_key FROM row_dim WHERE doc_id='SYN' AND "
                          "row_leaf_label=? LIMIT 1", (label,)).fetchone()
        return row[0] if row else None

    check("'Interest income' stamped pnl.nii.interest_income",
          key_of("Interest income") == "pnl.nii.interest_income", str(key_of("Interest income")))
    check("'Net fee & commission income' -> fee_commission (&-fold)",
          key_of("Net fee & commission income") == "pnl.noninterest.fee_commission",
          str(key_of("Net fee & commission income")))
    check("'Total expenses' -> pnl.opex.total", key_of("Total expenses") == "pnl.opex.total")
    check("'EXPENSES1' -> pnl.opex.total (glued footnote norm)",
          key_of("EXPENSES1") == "pnl.opex.total", str(key_of("EXPENSES1")))
    check("'Subordinated term debts¹' unmatched (not a dictionary concept)",
          key_of("Subordinated term debts¹") is None)
    check("structural 'Note 1' left NULL", key_of("Note 1") is None)
    check("structural '31 December 2025' left NULL", key_of("31 December 2025") is None)
    check("deterministic report stamped >0", det["stamped"] > 0, str(det["stamped"]))
    check("deterministic report skipped-structural >=2 (note + date)",
          det["skipped_structural"] >= 2, str(det["skipped_structural"]))

    # audit log — one row per stamp, method deterministic conf 1.0
    n_log = con.execute("SELECT COUNT(*) FROM concept_resolution_log WHERE "
                        "method='deterministic'").fetchone()[0]
    check("audit log populated (method=deterministic)", n_log == det["stamped"],
          f"{n_log} vs {det['stamped']}")
    check("audit log confidence=1.0",
          con.execute("SELECT COUNT(*) FROM concept_resolution_log WHERE confidence=1.0")
          .fetchone()[0] == n_log)

    # IDEMPOTENCY — re-run stamps nothing new, does not re-log
    det2 = resolve_deterministic(con)
    check("idempotent re-run: 0 new stamps", det2["stamped"] == 0, str(det2["stamped"]))
    check("idempotent re-run: already-correct == first stamped",
          det2["already_correct"] >= det["stamped"], str(det2["already_correct"]))
    n_log2 = con.execute("SELECT COUNT(*) FROM concept_resolution_log").fetchone()[0]
    check("idempotent re-run: audit log unchanged", n_log2 == n_log, f"{n_log2} vs {n_log}")

    # VALIDATE
    val = validate(con)
    checks = {c["name"]: c for c in val["checks"]}
    add = checks["additive_identity"]
    check("additive identity ran (>=1 checked)", add["checked"] >= 1, str(add))
    check("additive identity caught the broken table (>=1 fail)", add["failed"] >= 1, str(add))
    check("additive flag names the broken concept",
          any("pnl.nii.net" in f for f in val["flags"]), str(val["flags"]))
    dupchk = checks["uniqueness_per_table"]
    check("uniqueness flagged the duplicate 'Total assets'",
          dupchk["failed"] >= 1 and any("bs.assets.total" in f for f in val["flags"]),
          str([f for f in val["flags"] if "dup" in f]))
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    check("PRAGMA foreign_key_check clean", fk == [], str(fk))
    con.close()


if __name__ == "__main__":
    normalize_tests()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "concept_v7.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.commit()
        con.close()
        dictionary_tests(db)
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "concept_amb_v7.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.commit()
        con.close()
        ambiguity_gate_tests(db)
    nature_check_unit_tests()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "concept_stamp_v7.db"
        con = sqlite3.connect(db)
        con.executescript(_SCHEMA.read_text())
        con.commit()
        con.close()
        stamp_and_validate_tests(db)
    print(f"\n{'ALL PASS' if _FAILS == 0 else str(_FAILS) + ' FAILED'}")
    raise SystemExit(1 if _FAILS else 0)
