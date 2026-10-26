# Wildcat Capital Portfolio Data: Cleaning and Validation Report

**To:** Portfolio Committee  **From:** BI Analytics  **Date:** October 26, 2026
**Scope:** Eight portfolio datasets, 555,176 rows (clients, advisors, securities, benchmarks, calendar, fees, monthly snapshots, transactions; 2020–2024).

## 1. Executive Summary and BI Readiness

The cleaned data is **structurally ready for BI loading**, but **not yet reliable for every business question**. All 13 table relationships join with no missing references, every record was kept, and all 15 review flags were independently confirmed. Cleaning did **not** correct the most serious problems, because the correct values are not in the data; they are flagged so reports can exclude them.

| Use case | Readiness |
|---|---|
| Client, advisor and AUM reporting (snapshots) | **Ready**, with flagged AUM rows reviewed |
| Fee reporting from one named source table | **Ready with caveat**: the fee sources disagree |
| Performance (returns, alpha) | **Use with caution**: returns appear to include flows |
| Dividend income | **Not ready**: every dividend amount is miscalculated |
| Holdings rebuilt from transactions | **Not ready**: negative positions |

## 2. Raw vs. Clean Quality Comparison

Figures come from the Part D validation (`data_quality_scorecard.csv`), which applies identical checks to both layers.

| Metric (all 8 tables) | Raw | Clean | What it means |
|---|---|---|---|
| Rows | 555,176 | 555,176 | No records removed |
| Completeness, keys, types, joins (13) | 100% | 100% | No missing fields (legitimate blanks excluded), duplicates, bad types or orphans |
| Number format (snapshots) | 99.937% | 100% | **Formatting only**: 26,955 return/alpha values set to six decimals (891 from scientific notation); values unchanged |
| Business-rule pass rate (transactions) | 96.05% | 96.05% | **Accuracy unchanged**: 63,654 transactions (21.3%) still break a confirmed rule |
| Suspicious rows (transactions / snapshots / clients) | 25.2% / 5.2% / 89.6% | 25.2% / 5.2% / 89.6% | Unproven issues, flagged for review |

**Formatting vs. accuracy:** The only measurable improvement is formatting. Accuracy is unchanged because original values were preserved and flags added. A flag identifies a problem; it does not fix it.

## 3. Major Cleaning Judgment Calls

**A. Dividend amounts: flagged and preserved, not corrected.**
All 53,826 dividend transactions record the full position value (shares × price) instead of a payout, so dividend income exceeds the client's entire average AUM in 3,209 of 10,947 client-years, which is impossible.
- *Decision:* keep the amounts and flag all 53,826 rows; replacing them would mean inventing financial figures. *Risk:* no dividend income or total return can be reported until the source supplies correct payouts.

**B. Negative holdings: no synthetic purchases created.**
For clients whose full history falls inside the extract, 9,828 sales sell more shares than the client held (confirmed errors), and 15,977 later trades occur on already-negative positions (suspicious). Another 41,267 similar rows belong to clients onboarded before 2020, whose earlier purchases may predate the extract (informational).
- *Decision:* keep every transaction; flag four separate groups so no defect is counted twice. *Risk:* positions cannot be rebuilt from transactions; use snapshots.

**C. Fee sources: both kept, neither declared authoritative.**
Advisory Fee transactions total **\$263.7M**, compared with **\$204.7M** in the fee table. Only **17 of 28,746** overlapping client-months agree within \$1, and only 5 of 7,653 for Advisory-type fees alone. Another 7,350 client-months have a snapshot but no fee record.
- *Decision:* keep both sources separate and never add them together; no documentation identifies the system of record. *Risk:* fee revenue is unreconciled, and totals depend on the table used. Fee AUM does match snapshot AUM in all 122,197 fee records.

## 4. Limitations and Committee Recommendations

**Key limitations**
- **AUM and returns:** 420 snapshots (and 386 fee records) show exactly \$1,000, and 330 month-to-month AUM changes exceed 3× or fall below one-third. 2,639 returns fall outside the assumed −50% to +100% range, and returns track raw AUM growth, suggesting deposits and withdrawals are included.
- **Clients and transactions:** 75 clients have an "Unknown" risk profile and default to the 60/40 benchmark; ZIP codes conflict with the state for 89% of clients. 2,410 transactions are dated after a client's last snapshot, 56,117 trades and dividends fall on weekends, and the calendar marks market holidays as trading days.
- **Assumed thresholds** (the return range, the 100 bps fee cap) are not official policy.

**Recommendations**
1. **Request corrected dividend payouts**; exclude flagged dividends from income and return reporting until then.
2. **Designate a fee system of record**, and have Finance explain the \$59.0M gap and the 7,350 months with no fee.
3. **Use snapshots for holdings and AUM**, excluding or footnoting \$1,000 and AUM-jump flags.
4. **Document the return methodology** before evaluating advisors on returns or alpha.
5. **Make every dashboard state its source table and flag exclusions**; keep post-departure transactions out of active-client metrics.
6. **Supply reference data:** a fee schedule, risk profiles for the 75 Unknown clients, verified addresses, and a holiday calendar.

---
*AI-assisted report — reviewed for factual accuracy on 2026-10-26 by Chloe Han*
