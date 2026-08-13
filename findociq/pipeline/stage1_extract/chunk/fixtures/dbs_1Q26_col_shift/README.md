# Fixture — DBS 1Q26 trading update, pre-repair column shift

Frozen input for `test_column_bands.py`, `test_column_repair.py` and
`test_number_scoping.py`. Ground truth: `docs/specs/2026-07-29-column-band-validator.md`.

These three checks originally read an absolute scratchpad path on a since-retired
laptop (`/private/tmp/claude-501/...`), so they were unrunnable anywhere else. The
obvious repoint — at `outputs/fs/dbs_1Q26/audit/DBS_1Q26_trading_update/` — does not
work for two of them: that tracked artifact is the **post-repair** extraction, so
`validate_column_bands` correctly returns no issues and the positive-detection and
repair checks have nothing to fire on.

So this directory is the **pre-repair** state, derived from that tracked artifact with
exactly one defect reintroduced, in `selected_balance_sheet_items_m_p6`:

    both 'Constant-currency change' rows — the two '% chg' values move one column left
    printed bands [3,5]  ->  extracted slots [2,4]

which reproduces the spec's ground truth verbatim:

    col-shift: 'Constant-currency change' printed bands [3,5] -> extracted slots [2,4]

The other three unit dirs are byte-identical copies of the tracked artifact — they are
the negative/control cases (correctly extracted units that must NOT be flagged).

`meta.json` is copied unchanged from the audit dirs. The PDF the geometry is read from
stays the real one: `data/sources/financial_statements/DBS_1Q26_trading_update.pdf`.

To regenerate after a re-extraction, re-copy the four units and re-apply the shift
above; do not hand-edit.
