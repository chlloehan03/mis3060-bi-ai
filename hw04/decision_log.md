# HW4 Part B — Data Cleaning Decision Log

## Purpose

This log documents the cleaning decisions based on the Part A data quality audit (`reports/dq_summary_raw.csv`). The goal is to prepare reliable BI datasets while preserving original values when the correct replacements cannot be verified. All raw files will remain unchanged.

Audit IDs are cited with their audit check name, because IDs are assigned by position and would shift if the audit were rerun with changes.

**Classifications used:**
- **Confirmed Error:** the audit shows the values cannot be correct.
- **Suspicious:** the values look wrong, or conflict with other data, but are not proven wrong.
- **Structural/Legitimate:** blanks or unusual-looking values that are valid by design.
- **Informational:** observations, inferred patterns, or format notes. Nothing is wrong with the values themselves.

## 1. Financial Data Quality Issues

### Decision 1: Miscalculated Dividend Amounts
**Audit IDs:** DQ0001 (`dividend_income_vs_aum`), DQ0014 (`dividend_equals_position_value`)  
**Table/Column:** `fact_transactions.amount`  
**Dimension:** Accuracy, Consistency  
**Classification:** Confirmed Error

**Evidence:** Dividend income exceeds the client's average AUM in 3,209 of 10,947 client-years (29.31%). No portfolio can pay out more than its whole value in dividends in a year. All 53,826 dividend transactions have amounts equal to shares × price (the full position value), so the defect is in how every dividend amount was calculated. The 3,209 client-years are the supporting evidence, not the scope.

**Decision:** Flag all 53,826 dividend transactions as having miscalculated amounts and preserve their original values. Exclude them from dividend-income and return calculations.

**Justification:** The correct payouts are not in the data, so replacing the amounts would introduce invented financial values.

**Risk:** No dividend income can be reported until the source provides correct payout amounts.

### Decision 2: Negative Reconstructed Holdings
**Audit IDs:** DQ0002 (`oversell_first_event (in-window)`), DQ0020 (`txn_on_negative_position (in-window)`), DQ0056 (`oversell_first_event (pre-window)`), DQ0057 (`txn_on_negative_position (pre-window)`)  
**Table/Column:** `fact_transactions.shares`  
**Dimension:** Accuracy, Consistency  
**Classification:** Confirmed Error (in-window initial oversells); Suspicious (in-window later negative-position transactions); Informational (pre-window findings)

**Evidence:**
- **In-window clients:** 9,828 Sells take a position below zero for the first time, across 8,729 client-security pairs. These clients' whole history is in the extract, and there is no transfer-in transaction type, so these oversells are internally inconsistent.
- **In-window follow-on transactions:** 15,977 later Buy/Sell transactions occur on positions that are already negative. They are consequences of the first oversell.
- **Pre-window clients:** 15,693 initial oversell events and 25,574 later negative-position transactions. Their earlier purchases may simply predate the extract.

**Decision:** Preserve all original transactions and do not create hypothetical purchases or alter share quantities. Flag rows by scope:
- The 9,828 in-window initial oversells as confirmed errors.
- The 15,977 in-window later negative-position transactions as suspicious consequences.
- The 15,693 + 25,574 pre-window rows as informational, so they can be excluded from holdings reconstruction.

**Justification:** Transaction-based holdings are unreliable, but deleting records would further distort client activity. Separate flags avoid counting the same defect twice.

**Risk:** Holdings cannot be rebuilt from transactions; position analysis must rely on snapshots. Pre-window flags may cover some rows that are valid.

### Decision 3: Extreme Returns and Alpha
**Audit IDs:** DQ0007 (`soft_range`, `active_alpha_ytd`), DQ0009 (`soft_range`, `return_ytd`), DQ0013 (`extreme_returns_flagged`), DQ0047 (`suspect_flag_vs_alpha`), DQ0055 (`returns_vs_external_flows`)  
**Table/Column:** `fact_portfolio_snapshot.return_ytd`, `active_alpha_ytd`, `is_return_suspect`  
**Dimension:** Accuracy, Validity  
**Classification:** Suspicious

**Evidence:**
- **Extreme values:** the audit found 2,639 return values outside −0.5 to 1.0 and 3,875 alpha values outside ±0.5.
- **Existing flag:** `is_return_suspect` equals 1 exactly when |alpha| > 0.5, so 249 of the extreme returns are not flagged by it.
- **Cash flows:** in months with flows above 5% of AUM, `return_ytd` is within 0.0001 of raw AUM growth in 100% of cases. This suggests deposits and withdrawals are included in the return.

**Decision:** Preserve reported values and the existing suspect flag. Add a separate extreme-return flag for `return_ytd < -0.5` or `return_ytd > 1.0` (2,639 rows). These thresholds are assumed, not documented. Document that returns appear to include external cash flows.

**Justification:** No return methodology is documented, so returns cannot be recalculated reliably.

**Risk:** The assumed thresholds may flag legitimate returns, and return-based performance metrics remain uncertain.

### Decision 4: Fee Reconciliation Differences
**Audit IDs:** DQ0024 (`advisory_fee_txn_reconciles_to_fees`)  
**Table/Column:** `fact_transactions.amount`, `fact_fees.amount`  
**Dimension:** Consistency, Accuracy  
**Classification:** Suspicious

**Evidence:** Only 17 of 28,746 overlapping client-months have Advisory Fee transaction amounts matching `fact_fees` within $1. Total Advisory Fee transactions are approximately $263.7 million versus $204.7 million in `fact_fees`.

**Decision:** Preserve both sources and report the discrepancy. Do not combine or overwrite fee amounts. Neither table is confirmed as the authoritative fee-revenue source. Any fee-revenue reporting must name the table it uses and note this limitation.

**Justification:** The two tables may represent different billing processes, and no documentation says which is the system of record.

**Risk:** Fee revenue cannot be considered reconciled, and totals will differ depending on the table used.

### Decision 5: Suspicious AUM Values and Changes
**Audit IDs:** DQ0005 (`placeholder`, `aum_basis`), DQ0008 (`placeholder`, `aum`), DQ0012 (`aum_month_over_month_jump`), DQ0016 (`fee_on_placeholder_aum`)  
**Table/Column:** `fact_portfolio_snapshot.aum`, `fact_fees.aum_basis`  
**Dimension:** Accuracy, Consistency  
**Classification:** Suspicious

**Evidence:** There are 420 snapshots and 386 fee records with AUM equal to $1,000. The audit also identified 330 month-over-month AUM changes greater than threefold or below one-third.

**Decision:** Preserve AUM values. Flag:
- Snapshots where `aum == 1000` (420 rows).
- Snapshots where AUM divided by the client's prior-month AUM is above 3 or below 1/3 (330 rows).
- The 386 `fact_fees` records whose `aum_basis` equals 1000.

Do not automatically replace $1,000 with missing values.

**Justification:** There is no independent evidence that $1,000 is wrong, and actual balances cannot be inferred reliably from neighboring months.

**Risk:** AUM and related fee calculations may remain inaccurate.

### Decision 6: Fee Rate Outliers and Inferred Rules
**Audit IDs:** DQ0006 (`soft_range`, `bps_charged`), DQ0043 (`tier_aum_bands_observed`), DQ0054 (`fee_rate_vs_client_tier`)  
**Table/Column:** `fact_fees.bps_charged`, `dim_client.client_tier`  
**Dimension:** Validity, Accuracy  
**Classification:** Suspicious (100.38 bps rate); Informational (inferred tier bands and fee-rate ranges)

**Evidence:** One fee rate is 100.38 bps, slightly above an assumed 100 bps threshold. Client-tier AUM bands and typical fee rates were inferred from the data, not an official fee schedule.

**Decision:** Preserve the rate and client tiers. Flag the rate outlier without applying inferred pricing or tier rules as corrections.

**Justification:** Observed patterns do not establish official business policies.

**Risk:** Actual fee or classification errors may remain undetected.

## 2. Client and Reference Data

### Decision 7: Unknown Risk Profiles and Benchmarks
**Audit IDs:** DQ0004 (`placeholder`, `risk_profile`), DQ0017 (`unknown_risk_profile_benchmark`), DQ0166 (`one_benchmark_per_risk_profile`)  
**Table/Column:** `dim_client.risk_profile`, `fact_portfolio_snapshot.benchmark_id`  
**Dimension:** Completeness, Accuracy  
**Classification:** Suspicious

**Evidence:** Seventy-five clients have an `Unknown` risk profile, and their 3,942 snapshots use benchmark 3 (60/40 Blend).

**Decision:** Preserve the `Unknown` category and benchmark assignments. Flag these clients for review rather than guessing their risk preferences.

**Justification:** Unsupported risk classifications could misrepresent client suitability and benchmark performance.

**Risk:** Risk-based segmentation and alpha comparisons may be unreliable for these clients.

### Decision 8: ZIP Code and State Mismatches
**Audit IDs:** DQ0010 (`zip_matches_state`), DQ0031 (`leading_zeros`)  
**Table/Column:** `dim_client.zip_code`, `state`  
**Dimension:** Validity, Consistency  
**Classification:** Suspicious (ZIP/state mismatches); Structural/Legitimate (leading zeros)

**Evidence:** ZIP/state mismatches affect 2,229 of 2,500 clients (89.16%). Additionally, 228 ZIP codes begin with zero.

**Decision:** Keep ZIP codes as text and flag mismatches. Do not modify addresses without verified geographic data.

**Justification:** Automatic corrections could create incorrect client addresses.

**Risk:** Geographic analysis may remain unreliable.

### Decision 9: Possible Duplicate Entities
**Audit IDs:** DQ0044, DQ0045 (`possible_duplicate_entity`)  
**Table/Column:** `dim_client.first_name`, `last_name`; `dim_security.name`  
**Dimension:** Uniqueness  
**Classification:** Informational

**Evidence:** There are 50 shared-name client groups involving 107 records and three shared-name security groups involving six records. Primary keys are unique.

**Decision:** Leave records unchanged and do not merge entities based solely on names.

**Justification:** Different clients and securities may legitimately share names.

**Risk:** Genuine duplicates may remain until additional identifiers are verified.

### Decision 10: Structural Blanks and Null-Looking Values
**Audit IDs:** DQ0025 (`null_tokens`), DQ0033, DQ0034, DQ0039–DQ0041 (`blanks`), DQ0046 (`structural_blanks`), DQ0140 (`security_txn_has_security`), DQ0141 (`cash_txn_has_no_security`)  
**Table/Column:** `dim_advisor.certifications`, `dim_security.sector`, `expense_ratio_bps`; `fact_transactions.security_id`, `shares`, `price`  
**Dimension:** Completeness, Validity  
**Classification:** Structural/Legitimate

**Evidence:** Six advisors have the legitimate certification value `None`. There are 219 blank ETF sectors, 281 blank non-ETF expense ratios, and 101,528 cash transactions with appropriately blank security fields.

**Decision:** Preserve structural blanks and keep `None` as text. Do not impute values that do not apply. Part C must read all raw files as strings with `dtype=str, keep_default_na=False`, so that blanks, `None`, and leading-zero ZIP codes are not converted or lost on load.

**Justification:** These values reflect legitimate data structures rather than missing information.

**Risk:** Downstream tools may misinterpret blanks or null-looking text.

## 3. Cross-Table and Transaction Issues

### Decision 11: Missing Fee Records
**Audit IDs:** DQ0018 (`snapshot_has_fee`)  
**Table/Column:** `fact_fees.fee_date`, `fact_portfolio_snapshot.snapshot_date`  
**Dimension:** Completeness, Consistency  
**Classification:** Suspicious

**Evidence:** There are 7,350 client-months with portfolio snapshots but no corresponding fee records.

**Decision:** Preserve existing records and identify unmatched client-months in validation reports. Do not generate artificial fees.

**Justification:** Fee waivers, billing schedules, or missing data could explain the discrepancy.

**Risk:** Revenue analysis may be incomplete.

### Decision 12: Dividends Without Prior Purchases
**Audit IDs:** DQ0019 (`dividend_after_purchase`)  
**Table/Column:** `fact_transactions.security_id`, `txn_date`  
**Dimension:** Consistency, Accuracy  
**Classification:** Suspicious

**Evidence:** There are 8,444 dividend transactions without a recorded prior purchase for in-window clients, including 2,734 involving securities never purchased in the extract.

**Decision:** Flag affected dividends and preserve their original records.

**Justification:** Missing holdings history cannot be reconstructed reliably.

**Risk:** Dividend eligibility and transaction-derived holdings remain uncertain.

### Decision 13: Transactions After Last Snapshot
**Audit IDs:** DQ0021 (`txn_after_client_departure`), DQ0170 (`post_departure_flag_consistent`)  
**Table/Column:** `fact_transactions.txn_date`, `is_post_departure_txn`  
**Dimension:** Consistency  
**Classification:** Suspicious

**Evidence:** There are 2,410 transactions for 131 inactive clients after their last snapshot. `is_post_departure_txn` equals 1 for exactly these rows. The last snapshot is only a proxy for departure, not a verified departure date.

**Decision:** Preserve transactions and the existing flag. Do not delete them or classify them as invalid. Exclude rows with `is_post_departure_txn = 1` from active-client transaction metrics, and report them separately.

**Justification:** The cause of these transactions is unknown, and no departure date exists to confirm whether they are valid.

**Risk:** If some are valid, excluding them understates activity. If they are kept in active-client metrics, they would distort those metrics.

### Decision 14: Non-Trading-Day Transactions and Calendar Flags
**Audit IDs:** DQ0011 (`holidays_not_trading`), DQ0022 (`trades_on_trading_days`), DQ0023 (`dividends_on_trading_days`), DQ0058 (`cash movements / fees_on_trading_days`)  
**Table/Column:** `dim_date.is_trading_day`, `fact_transactions.txn_date`  
**Dimension:** Validity, Consistency  
**Classification:** Suspicious (holiday flags, weekend trades and dividends); Informational (weekend cash movements)

**Evidence:** Nine market holidays are marked as trading days. There are 40,630 weekend Buy/Sell transactions, 15,487 weekend dividends, and 28,829 weekend cash movements or fees.

**Decision:** Preserve transaction dates and flag weekend-dated trades and dividends. Leave the holiday calendar unchanged until its intended definition is verified.

**Justification:** Dates may represent booking rather than execution dates.

**Risk:** Trading-day performance analysis may remain unreliable.

### Decision 15: Initial AUM Discrepancy
**Audit IDs:** DQ0015 (`initial_aum_vs_first_snapshot`)  
**Table/Column:** `dim_client.initial_aum`, `fact_portfolio_snapshot.aum`  
**Dimension:** Accuracy, Consistency  
**Classification:** Suspicious

**Evidence:** The audit flagged one client whose first snapshot AUM differs substantially from their recorded initial AUM.

**Decision:** Preserve both amounts and flag the discrepancy for review.

**Justification:** Differences may reflect timing or client activity, and the correct value is unknown.

**Risk:** Onboarding asset-growth analysis may be inaccurate for this client.

## 4. Formatting and BI Compatibility

### Decision 16: Scientific Notation
**Audit IDs:** DQ0037, DQ0038 (`mixed_format`)  
**Table/Column:** `fact_portfolio_snapshot.active_alpha_ytd`, `return_ytd`  
**Dimension:** Consistency, Type Validity  
**Classification:** Informational

**Evidence:** There are 756 alpha values and 135 return values written in scientific notation. All are valid numeric representations. Both columns have at most six decimal places (`profile_raw.csv`, `decimal_places_max` = 6).

**Decision:** Write both columns in fixed six-decimal notation (e.g. `2e-05` becomes `0.000020`) without changing any value. Part C must confirm that no value needs more than six decimal places before writing.

**Justification:** Consistent formats improve compatibility with BI tools.

**Risk:** If any value needed more than six decimal places, fixed formatting would round it. The Part C check guards against this.

### Decision 17: Filename, Sign Conventions, and BI Relationships
**Audit IDs:** DQ0003 (`file_name`), DQ0048 (`sign_convention`), DQ0049, DQ0050 (`unused_dimension_members`), DQ0051 (`date_coverage`), DQ0052 (`date_id_unused`)  
**Table/Column:** `fact_transactions` filename and `amount`; `dim_date.date`; unused dimension members  
**Dimension:** Consistency, Referential Integrity  
**Classification:** Suspicious (filename); Informational (sign convention, unused members, date joins)

**Evidence:** The transaction extract is named `fact_transactions (1).csv`. Transaction amounts are positive regardless of direction. One benchmark and one calendar date are unused, and fact tables join to `dim_date.date`, not `date_id`.

**Decision:** Preserve the raw filename but output `fact_transactions.csv` in the cleaned folder. Retain original amount signs, document transaction-type direction, preserve unused dimension records, and use the correct date joins.

**Justification:** Standardized output names and documented relationships support reproducible BI analysis without changing source meaning.

**Risk:** Incorrect sign interpretation or joins could distort downstream metrics.

### Decision 18: Observed Categorical Values
**Audit IDs:** DQ0026–DQ0030, DQ0032, DQ0035, DQ0036, DQ0042 (`observed_value_set`), DQ0053 (`security_vs_benchmark_asset_class`)  
**Table/Column:** Categorical fields in advisor, client, security, benchmark, fee, snapshot, and transaction tables  
**Dimension:** Validity, Consistency  
**Classification:** Informational

**Evidence:** The audit identified observed category sets and security-to-benchmark mappings, but these were not independently verified against official business definitions.

**Decision:** Preserve existing categories and mappings. Do not recode values based solely on categories inferred from the data.

**Justification:** Data-derived patterns are not sufficient evidence for changing business classifications.

**Risk:** Unrecognized category errors may remain until official definitions are available.

## Cleaning Principles for Part C

All eight raw datasets will remain unchanged. Cleaned outputs will retain original records and financial values unless a correction is independently justified. Confirmed errors and suspicious values will be flagged, structural blanks preserved, and numeric formats standardized. Every transformation will be logged with affected row counts and before/after evidence. Any future row removals must be quarantined and reconciled against the original row counts.

Unresolved financial and business-rule uncertainties will be disclosed in the final cleaning report.
