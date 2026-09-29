# Validation
## Known Answer Check - Earnings
| Check | Official Source | Your CSV | Match? |
|---|---|---|---|
| [Apple] [Q4FY2025] Revenue | $102.5 B | $102.5 B | Yes |
| [Apple] [Q4FY2025] EPS Diluted | $1.85 | $1.85 | Yes |


## Known Answer Check - Executive Events
| Check | News Source Confirms? | Notes |
|---|---|---|
| Person name and title | Yes | Walmart did announce John R. Furner as their new President and CEO on November 14, 2025. |
| Event type (departure/appointment) | Yes | It is confirmed that Furner was appointed as President and CEO following McMillon's departure. |
| Effective date | Yes | The appointment was effective on February 1, 2026. |

## Cross Validation: Earings via Yahoo Finance
| Metric | From 8-K text extraction | From yfinance | Match? |
|---|---|---|---|
| Revenue | $102.5 B | $102.5 B| Yes |
| Net Income | $27,466 M | $27,466 M | Yes |

## Pipeline Integrity Checks
| Check | Expected | Actual | Pass/Fail |
|---|---|---|---|
| `earnings_history.csv` row count | Up to 20 (5 companies × 4 quarters) | 20 | Pass |
| `executive_events.csv` row count | At least 0 (document actual) | 29 | Pass |
| `corporate_events_timeline.csv` created | Yes | Yes | Pass |
| Rows with all three fields `"NOT_FOUND"` | 0 (investigate if > 0) | 0 | Pass |