# Eval questions — chat_report.py

Live-Gemini acceptance list for the chat-with-data app. Each entry is a
phrasing to type into the chat box, plus the spec sketch it should resolve
to (per `spec.build_system_prompt` / `validate_spec`), so a human can eyeball
the "Interpreted as: ..." line and the resulting chart/table against
expectation. **Not run as part of this task** — running it costs live
Gemini API calls and is a user-facing acceptance step (Step 5 of the brief).

Registry as of `findociq/db/final.db` (from `load_registry`):
- concepts: `asf_total`, `rsf_total`, `nsfr_ratio`, plus ~30 ASF/RSF line items
  (e.g. `asf_retail`, `rsf_hqla`, `rsf_perf_corp`, ...)
- institutions: United Overseas Bank Limited (UOB), Oversea-Chinese Banking
  Corporation Limited (OCBC), DBS Group Holdings Ltd (DBS)
- periods: 2023-09-30, 2023-12-31, 2024-09-30, 2024-12-31, 2025-09-30, 2025-12-31
- col_keys: `weighted`, `unw_no_maturity`, `unw_lt_6m`, `unw_6m_to_1y`, `unw_ge_1y`

1. **Baseline compare, weighted, line**
   "compare UOB vs DBS required stable funding through 2025"
   → `rsf_total`, institutions=[UOB, DBS], period_start=2023-09-30 (or
   earliest available), period_end=2025-12-31, column=weighted, chart=line

2. **Percent case (NSFR ratio)**
   "what's OCBC's NSFR ratio been since 2024?"
   → `nsfr_ratio`, institutions=[OCBC], period_start=2024-*, period_end=2025-12-31,
   column=weighted, chart=line — chart must render with `value_fmt="percent"`
   (%-suffixed axis/end-labels, no thousands formatting)

3. **Unweighted case**
   "show UOB's available stable funding from retail deposits, unweighted, under 6 months"
   → `asf_retail`, institutions=[UOB], column=`unw_lt_6m`, chart=line (or bar)

4. **All-institutions default (no bank named)**
   "chart total ASF over time"
   → `asf_total`, institutions=[] → resolved to all three (UOB, OCBC, DBS) per
   `validate_spec`'s "empty institutions -> all" rule, column=weighted

5. **Bar chart request**
   "bar chart of RSF from high quality liquid assets for all three banks, latest quarter"
   → `rsf_hqla`, institutions=[all], period_start=period_end=2025-12-31 (or the
   latest available period, since start==end after clamping), chart=bar

6. **Table request**
   "give me a table of NSFR ratio for every bank, every quarter"
   → `nsfr_ratio`, institutions=[all], full period range, chart=table — the
   Streamlit app should render `st.dataframe` instead of/alongside a chart

7. **Multi-concept (near MAX_CONCEPTS)**
   "compare asf_total, rsf_total, and nsfr_ratio for DBS since 2024"
   → concepts=[asf_total, rsf_total, nsfr_ratio] (3, under MAX_CONCEPTS=4),
   institutions=[DBS] — note only the first 2 charts get slotted into the
   downloadable slide (`charts[:2]` in chat_report.py); all requested concepts
   should still render inline via `st.image`.

8. **Alias/typo case**
   "hows UOBs rsf totall lookin the last 2 years"
   → should still resolve to `rsf_total`, institutions=[UOB] (via alias +
   fuzzy `_resolve_concept`/`difflib` matching despite "totall" typo and
   missing apostrophe/punctuation); if the LLM instead emits a garbled
   concept key the retry-once path in `nl_to_spec` should recover it or
   surface a `SpecError` with a "did you mean" suggestion — not a crash.

9. **Out-of-scope, must reject**
   "what was UOB's net interest margin last quarter"
   → `net_interest_margin` (or similar) is not in `reg.concepts` and has no
   plausible NSFR/ASF/RSF cousin for `difflib` to suggest → after the retry,
   `nl_to_spec` should raise `SpecError`, and the app must show
   `st.warning(...)` (not crash, not silently substitute a real concept).

10. **Ambiguous bank name, cross-institution**
    "how does OCBC's NSFR stack up against United Overseas Bank in Dec 2025"
    → institutions=[OCBC, UOB] (mixed alias + full legal name both resolved
    via `_resolve_institution`'s alias/case-insensitive lookup), concept=
    `nsfr_ratio`, period_start=period_end=2025-12-31, chart=line (single-point
    markers per `make_item_chart`'s `len(pts)==1` branch).

## Acceptance checklist per question
- [ ] "Interpreted as: ..." line matches the sketch above (or is a reasonable
      equivalent — e.g. a slightly different period_start is fine since
      periods are clamped, not rejected)
- [ ] Chart(s) render without exception; percent case shows `%`, not raw
      thousands
- [ ] Reject case (#9) shows a warning banner, not a traceback
- [ ] Download buttons for PPTX/PDF appear for every case except the pure
      reject case
