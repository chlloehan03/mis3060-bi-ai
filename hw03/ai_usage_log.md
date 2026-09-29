# Documentation
## Claude Prompts
1. Specification A: Use Specification A from my `hw03/specifications.md` to create the Python script `hw03/hw03_earnings.py`. 

2. Specification B: Use Specification B from my `hw03/specifications.md` to create the Python script `hw03/hw03_executives.py`.

3. Timeline: "Write a Python script that reads `hw03/earnings_history.csv` and `hw03/executive_events.csv`. Do the following:
1. For each executive event in the events table, calculate the number of days between the executive event's `filing_date` and the nearest earnings filing date for the same company in the earnings table. Call this `days_to_nearest_earnings`. 2. Add a column `event_timing` that categorizes each executive event as: `'before earnings'` if the event came before the nearest earnings filing, `'after earnings'` if it came after, or `'same week'` if within 7 days of an earnings filing. 3. Save the combined table to `hw03/corporate_events_timeline.csv` with all columns from both source tables plus `days_to_nearest_earnings` and `event_timing`. 4. Print a summary: for each company, list any executive events and whether they occurred before or after the nearest earnings announcement. 5. Print a final count: how many events occurred before vs. after an earnings announcement across all five companies."

## Iterations
After the initial run, Apple, NVIDIA, Walmart, and JPMorgan required iteration. The first executive event extraction was producing issues with names and titles. Some names were incomplete and others were missing titles. I then asked Claude to review and improve the extraction logic to then rerun the prompt. Microsoft, unlike the other companies, did not require iteration.


One thing I didn't think to specify was that Claude actually included logic to recognize 5.02 filings that were related to executive compensation but did not have a departure or appointment. The python script printed a note right under instead of processing an incorrect executive event. 