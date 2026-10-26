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