"""
hw03_timeline.py
Combine the executive events (Item 5.02) and earnings (Item 2.02) datasets
into one corporate events timeline.

For each executive event, find the nearest earnings filing for the same
company, measure the gap in days, and label the timing.

Output: hw03/corporate_events_timeline.csv
"""

from pathlib import Path

import pandas as pd

HW_DIR = Path(__file__).resolve().parent
EARNINGS_CSV = HW_DIR / "earnings_history.csv"
EVENTS_CSV = HW_DIR / "executive_events.csv"
OUTPUT_CSV = HW_DIR / "corporate_events_timeline.csv"

SAME_WEEK_DAYS = 7


def load_data():
    """Read both CSVs. Keep CIK as text so leading zeros are preserved."""
    earnings = pd.read_csv(EARNINGS_CSV, dtype={"cik": str})
    events = pd.read_csv(EVENTS_CSV, dtype={"cik": str})
    earnings["filing_date"] = pd.to_datetime(earnings["filing_date"])
    events["filing_date"] = pd.to_datetime(events["filing_date"])
    return earnings, events


def find_nearest_earnings(event_row, earnings):
    """Return the earnings row closest in time to this event (same ticker).

    Ties (equal distance before and after) go to the earlier earnings filing.
    Returns None if the company has no earnings filings.
    """
    company_earnings = earnings[earnings["ticker"] == event_row["ticker"]].copy()
    if company_earnings.empty:
        return None
    company_earnings["gap"] = (
        company_earnings["filing_date"] - event_row["filing_date"]
    ).dt.days.abs()
    company_earnings = company_earnings.sort_values(["gap", "filing_date"])
    return company_earnings.iloc[0]


def classify_timing(signed_days):
    """signed_days = earnings date - event date.
    Positive -> event came first; negative -> event came after earnings."""
    if abs(signed_days) <= SAME_WEEK_DAYS:
        return "same week"
    if signed_days > 0:
        return "before earnings"
    return "after earnings"


def build_timeline(earnings, events):
    rows = []
    for _, event in events.iterrows():
        row = event.to_dict()
        nearest = find_nearest_earnings(event, earnings)

        if nearest is None:
            row.update({
                "earnings_filing_date": pd.NaT,
                "period": "NOT_FOUND",
                "revenue_reported": "NOT_FOUND",
                "eps_diluted": "NOT_FOUND",
                "net_income": "NOT_FOUND",
                "days_to_nearest_earnings": pd.NA,
                "event_timing": "NOT_FOUND",
                "_signed_days": pd.NA,
            })
        else:
            signed_days = (nearest["filing_date"] - event["filing_date"]).days
            row.update({
                "earnings_filing_date": nearest["filing_date"],
                "period": nearest["period"],
                "revenue_reported": nearest["revenue_reported"],
                "eps_diluted": nearest["eps_diluted"],
                "net_income": nearest["net_income"],
                "days_to_nearest_earnings": abs(signed_days),
                "event_timing": classify_timing(signed_days),
                "_signed_days": signed_days,
            })
        rows.append(row)

    timeline = pd.DataFrame(rows)
    timeline = timeline.sort_values(["ticker", "filing_date"]).reset_index(drop=True)
    return timeline


def save_timeline(timeline):
    columns = [
        # executive event columns
        "company", "ticker", "cik", "filing_date", "event_type",
        "person_name", "title", "effective_date",
        # matched earnings columns (earnings filing_date renamed to avoid a clash)
        "earnings_filing_date", "period", "revenue_reported",
        "eps_diluted", "net_income",
        # new columns
        "days_to_nearest_earnings", "event_timing",
    ]
    out = timeline[columns].copy()
    out["filing_date"] = out["filing_date"].dt.strftime("%Y-%m-%d")
    out["earnings_filing_date"] = out["earnings_filing_date"].dt.strftime("%Y-%m-%d")
    out.to_csv(OUTPUT_CSV, index=False)
    print(f"Saved {len(out)} rows to hw03/{OUTPUT_CSV.name}\n")


def print_company_summary(timeline):
    print("=" * 78)
    print("EXECUTIVE EVENTS RELATIVE TO THE NEAREST EARNINGS ANNOUNCEMENT")
    print("=" * 78)
    for (company, ticker), group in timeline.groupby(["company", "ticker"], sort=False):
        print(f"\n{company} ({ticker}) - {len(group)} event(s)")
        for _, r in group.iterrows():
            event_date = r["filing_date"].strftime("%Y-%m-%d")
            if pd.isna(r["_signed_days"]):
                print(f"  {event_date} | {r['event_type']:<11} | {r['person_name']}"
                      f" - no earnings filing to compare")
                continue
            earn_date = r["earnings_filing_date"].strftime("%Y-%m-%d")
            days = int(r["days_to_nearest_earnings"])
            direction = "BEFORE" if r["_signed_days"] > 0 else "AFTER"
            if r["_signed_days"] == 0:
                direction = "SAME DAY AS"
            label = f"{direction} earnings ({earn_date}, {r['period']}), {days} days"
            if r["event_timing"] == "same week":
                label += "  [same week]"
            print(f"  {event_date} | {r['event_type']:<11} | "
                  f"{r['person_name']} ({r['title']})")
            print(f"      -> {label}")


def print_final_counts(timeline):
    counts = timeline["event_timing"].value_counts()
    before = int(counts.get("before earnings", 0))
    after = int(counts.get("after earnings", 0))
    same_week = int(counts.get("same week", 0))

    print("\n" + "=" * 78)
    print("FINAL COUNT - ALL FIVE COMPANIES")
    print("=" * 78)
    print(f"  Before earnings : {before}")
    print(f"  After earnings  : {after}")
    print(f"  Same week (<= {SAME_WEEK_DAYS} days either side): {same_week}")
    print(f"  Total events    : {len(timeline)}")

    # Also show the pure before/after split, ignoring the 7-day window
    signed = timeline["_signed_days"].dropna()
    print(f"\n  Direction only (same-week events folded in): "
          f"{int((signed > 0).sum())} before, {int((signed < 0).sum())} after, "
          f"{int((signed == 0).sum())} same day")


def main():
    earnings, events = load_data()
    timeline = build_timeline(earnings, events)
    save_timeline(timeline)
    print_company_summary(timeline)
    print_final_counts(timeline)


if __name__ == "__main__":
    main()
