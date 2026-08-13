"""Tests for workbook.save_cost_summary's doc-scoped merge.

Root cause (2026-07-27): two doc_ids sharing a bank_period run directory
(e.g. DBS_1Q22_pillar3 and DBS_1Q22_trading_update both write to
outputs/pillar3/dbs_1Q22/logs/cost_summary.json) silently overwrote each
other's cost record, because the old writer wrote one flat "calls" list for
whichever doc ran last. See docs/specs/2026-07-27-table-region-overlap-
attribution.md's blast-radius note and the dashboard Logs tab.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stage1_extract.excel import workbook as W  # noqa: E402


def _run_usage(calls):
    return {"calls": len(calls),
            "prompt": sum(c["prompt_tokens"] for c in calls),
            "output": sum(c["output_tokens"] for c in calls),
            "thinking": 0,
            "cost": sum(c["est_cost_usd"] for c in calls)}


def _call(ts, label, cost):
    return {"ts": ts, "label": label, "model": "gemini-3.5-flash",
            "image_used": False, "prompt_tokens": 100, "output_tokens": 50,
            "thinking_tokens": 0, "total_tokens": 150, "est_cost_usd": cost,
            "batch": False}


def test_second_doc_does_not_wipe_first_docs_calls(tmp_path=None):
    with tempfile.TemporaryDirectory() as d:
        summary_path = str(Path(d) / "cost_summary.json")

        calls_a = [_call("t1", "B_1_2_p11", 0.03)]
        W.save_cost_summary(calls_a, _run_usage(calls_a), "A_pillar3.xlsx",
                            summary_path=summary_path, document="DBS_1Q22_pillar3.pdf")

        calls_b = [_call("t2", "dividends_p1-4", 0.05)]
        W.save_cost_summary(calls_b, _run_usage(calls_b), "B_trading_update.xlsx",
                            summary_path=summary_path,
                            document="DBS_1Q22_trading_update.pdf")

        summary = json.loads(Path(summary_path).read_text())
        assert len(summary["calls"]) == 2, "second doc's write wiped the first doc's call"
        assert set(summary["by_document"]) == {
            "DBS_1Q22_pillar3.pdf", "DBS_1Q22_trading_update.pdf"}
        assert summary["by_document"]["DBS_1Q22_pillar3.pdf"]["est_cost_usd"] == 0.03
        assert summary["by_document"]["DBS_1Q22_trading_update.pdf"]["est_cost_usd"] == 0.05
        assert summary["totals"]["calls"] == 2
        assert round(summary["totals"]["est_cost_usd"], 5) == 0.08


def test_rerun_same_doc_does_not_double_count(tmp_path=None):
    with tempfile.TemporaryDirectory() as d:
        summary_path = str(Path(d) / "cost_summary.json")
        calls = [_call("t1", "B_1_2_p11", 0.03)]
        W.save_cost_summary(calls, _run_usage(calls), "A.xlsx",
                            summary_path=summary_path, document="doc_a.pdf")
        # same (document, ts, label) written again -> must not duplicate
        W.save_cost_summary(calls, _run_usage(calls), "A.xlsx",
                            summary_path=summary_path, document="doc_a.pdf")
        summary = json.loads(Path(summary_path).read_text())
        assert len(summary["calls"]) == 1
        assert summary["by_document"]["doc_a.pdf"]["calls"] == 1


def test_no_existing_file_writes_clean_single_doc_summary(tmp_path=None):
    with tempfile.TemporaryDirectory() as d:
        summary_path = str(Path(d) / "cost_summary.json")
        calls = [_call("t1", "l1", 0.01), _call("t2", "l2", 0.02)]
        W.save_cost_summary(calls, _run_usage(calls), "A.xlsx",
                            summary_path=summary_path, document="doc_a.pdf")
        summary = json.loads(Path(summary_path).read_text())
        assert summary["by_document"] == {"doc_a.pdf": {
            "calls": 2, "input_tokens": 200, "output_tokens": 100,
            "thinking_tokens": 0, "est_cost_usd": 0.03, "total_tokens": 300}}


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print("ALL PASS")
