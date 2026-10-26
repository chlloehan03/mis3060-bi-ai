# HW4: Cleaning the Wildcat Capital Data

## Part A — Data Quality Audit

Created `scripts/01_profile.py` to audit eight Wildcat Capital datasets, covering 77 columns. The script evaluates column-level quality, single-table rules, referential integrity, and cross-table business rules.

The audit generated two reports:
- `reports/profile_raw.csv`
- `reports/dq_summary_raw.csv`

### Audit Results

The final audit identified:
- 2 ERROR findings
- 22 WARN findings
- 34 INFO findings
- 113 PASS checks

All 13 foreign-key relationships passed. The original CSV files remained unchanged.

### Claude Code Review

Claude Code reviewed the profiling script and identified issues involving severity classifications, inferred business rules, and several audit calculations.

**Issues flagged and corrections made:**

- Revised severity classifications to distinguish confirmed defects from suspicious values.
- Corrected the interpretation of `is_return_suspect`.
- Reclassified inferred tier bands and observed allowed-value lists as informational findings.
- Clarified assumptions involving client departures, advisor relationships, and fee calculations.
- Corrected oversell event counting and expanded AUM-jump detection.
- Added exploratory checks for dividend income relative to AUM, returns versus cash flows, and fee rates by client tier.
- Replaced the dynamic audit date with a fixed reference date for reproducibility.

The revised script was rerun and the reports regenerated. Claude Code confirmed that all eight datasets were profiled, all 13 relationships were checked, and the original CSV files were unchanged.


### Part B: Data Cleaning Decisions

Created `decision_log.md` to document 18 cleaning decisions based on the findings from Part A. Each decision includes supporting audit evidence, the data quality dimension, the proposed action, business justification, and potential risks.

Claude Code reviewed the decision log against the Part A audit results and identified areas requiring clarification, including confirmed errors versus suspicious values, flagging criteria, and treatment of transactions occurring after a client's last portfolio snapshot. These findings were addressed before proceeding to Part C.

The final decision log served as the foundation for developing the cleaning script in Part C.


### Part C: Cleaning and Audit

Claude Desktop was used to draft `02_clean.py` based on the documented Part B cleaning decisions. The script successfully generated eight cleaned datasets, 15 quality flags, and audit documentation. A total of 26,955 values were reformatted without changing their numeric meaning, and no rows were removed.

Claude Code reviewed the script and independently verified the cleaned outputs against the raw data. The review identified three minor improvements involving raw-file verification order, assertion reporting, and clarification of transaction sign conventions. All three were implemented, and the script was rerun successfully. The final review confirmed that all assertions passed and the original raw files remained unchanged.


### Part D: Validation

Created `scripts/03_validate.py` to validate the raw and cleaned layers using the same checks. Results are summarized in `reports/validation_summary.md`.

- **Referential integrity:** All 13 relationships passed in both layers (26 of 26 checks), with 0 orphan rows. The 101,528 blank `security_id` values all belong to cash transactions, as expected.
- **Flags:** All 15 cleaning flags were verified. Each contains only 0/1 values, matches its decision-log count, and agrees row for row with the rule recomputed in the validation script.
- **Scorecard:** `reports/data_quality_scorecard.csv` has 16 rows (8 tables × raw and clean). Raw and clean scores are identical except numeric format consistency. The only cleaning change was reformatting 26,955 return/alpha values, which are numerically identical. Flagged records are not counted as repaired.
- **Reconciliations:** `fact_fees.aum_basis` matched the snapshot AUM for all 122,197 fee rows. Advisory Fee transactions matched `fact_fees` in only 17 of 28,746 client-months, and neither table is treated as the authoritative fee source.

**Unresolved confirmed errors:** 63,654 `fact_transactions` rows (21.319%) remain confirmed errors in both layers. They are flagged, not corrected:
- All 53,826 dividend amounts are miscalculated.
- 9,828 of 23,123 in-window Sells (42.503%) are first oversells.

These keep the `fact_transactions` business-rule pass rate at 96.05%.

### Claude Code Review

Claude Code independently reviewed Part D. It reran `03_validate.py` on a separate copy and reproduced all nine outputs byte for byte. It then recomputed the 13 relationships, 30 business rules, flags, both reconciliations, IQR outliers, and the 16-row scorecard from scratch, and all 149 checks agreed. The review found no calculation errors. It noted that the business-rule pass rate is row-weighted, so the confirmed-error row percentage was added to the validation summary.


### Part E: Portfolio Committee Cleaning Report

Completed `reports/cleaning_report.md`, a two-page report summarizing the data cleaning process, raw-versus-clean quality comparisons, three major cleaning decisions, and remaining limitations for the Wildcat Capital portfolio committee.

Claude Code independently reviewed the report against the validation outputs and identified four required corrections involving monetary formatting, numerical explanations, and report length. These corrections were implemented and verified. The final review confirmed that all numerical claims were accurate and the report met the two-page printed-length requirement.