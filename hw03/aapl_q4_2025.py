"""
Get Q4 2025 revenue and net income for a ticker (default: AAPL) using yfinance.

Note: Apple's fiscal year differs from the calendar year.
  - "fiscal"   -> Apple FY2025 Q4 (Jul–Sep 2025, period ending ~Sep 27, 2025)
  - "calendar" -> Oct–Dec 2025 (Apple's FY2026 Q1, period ending ~Dec 27, 2025)

Install first:  pip install yfinance
"""

import yfinance as yf
import pandas as pd

TICKER = "AAPL"

# Choose which "Q4 2025" you mean: "fiscal" or "calendar"
MODE = "fiscal"

WINDOWS = {
    "fiscal":   ("2025-09-01", "2025-10-15"),
    "calendar": ("2025-12-01", "2026-01-15"),
}


def get_q4_2025(ticker: str, mode: str = "fiscal"):
    t = yf.Ticker(ticker)
    stmt = t.quarterly_income_stmt          # rows = line items, columns = period-end dates
    stmt.columns = pd.to_datetime(stmt.columns)

    start, end = WINDOWS[mode]
    cols = [c for c in stmt.columns if pd.Timestamp(start) <= c <= pd.Timestamp(end)]
    if not cols:
        raise ValueError(f"No quarter found between {start} and {end}. "
                         f"Available periods: {[c.date() for c in stmt.columns]}")
    col = cols[0]

    revenue = stmt.loc["Total Revenue", col]
    net_income = stmt.loc["Net Income", col]
    return col.date(), revenue, net_income


if __name__ == "__main__":
    period_end, rev, ni = get_q4_2025(TICKER, MODE)
    print(f"{TICKER} — Q4 2025 ({MODE}), period ending {period_end}")
    print(f"  Revenue:    ${rev/1e9:,.2f}B")
    print(f"  Net Income: ${ni/1e9:,.2f}B")
    print(f"  Net Margin: {ni/rev:.1%}")
