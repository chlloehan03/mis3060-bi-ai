## Specification A - Earnings Pipeline (Item 2.02)

Create a Python script named `hw03/hw03_earnings.py` that builds an earnings history dataset from SEC EDGAR Form 8-K filings for Apple Inc., Microsoft Corporation, NVIDIA Corporation, JPMorgan Chase & Co., and Walmart Inc.

## 1. Companies

Use the following companies and SEC CIK numbers exactly as provided:

- Apple Inc. — Ticker: AAPL — CIK: 0000320193
- Microsoft Corporation — Ticker: MSFT — CIK: 0000789019
- NVIDIA Corporation — Ticker: NVDA — CIK: 0001045810
- JPMorgan Chase & Co. — Ticker: JPM — CIK: 0000019617
- Walmart Inc. — Ticker: WMT — CIK: 0000104169

Do not look up or substitute different CIK numbers.

## 2. SEC User-Agent

Use the `requests` library for all HTTP requests.

Every HTTP request made to the SEC EDGAR system must include this User-Agent header:

`MIS3060 Villanova youremail@villanova.edu`

Make sure the User-Agent is included on every `requests.get()` call, not just the first request.

## 3. Find Earnings 8-K Filings

For each of the five companies, query the SEC EDGAR submissions API using:

`https://data.sec.gov/submissions/CIK{cik}.json`

Use the company's CIK in the URL.

From the submissions data, identify Form 8-K filings where the `items` field contains `"2.02"`, which represents Results of Operations and Financial Condition.

Select the four most recent qualifying Item 2.02 filings for each company. These should represent the most recent four quarters.

## 4. Retrieve the Earnings Press Release

For each selected 8-K filing:

1. Construct the appropriate SEC EDGAR filing index URL using the company's CIK, accession number, and filing information.
2. Identify the earnings press release exhibit associated with the filing.
3. Select the relevant `.htm` press release file.
4. Download the press release using `requests`.
5. Include the required SEC User-Agent header on this request.
6. Use BeautifulSoup to strip the HTML and convert the press release into plain text for extraction.

If the earnings press release exhibit cannot be found, the script must not crash. Print a warning and continue processing the next filing.

## 5. Extract Earnings Information

From the plain-text earnings press release, extract the following information:

- Quarterly revenue
- Diluted EPS
- Net income
- Reporting period

The reporting period should be represented in a form such as:

`fourth quarter fiscal 2024`

Revenue should be retained as the reported number, including its scale such as millions or billions where applicable.

Use pattern matching/regular expressions to identify the required values from the press release text.

If a required field cannot be extracted because the appropriate pattern does not match, store:

`NOT_FOUND`

Do not use a blank cell, empty string, or `None` for a failed extraction.

## 6. Print Each Extracted Row

As each filing is processed, print the extracted information using this format:

`[Ticker] | [Period] | Revenue: $X | EPS: $X | Net Income: $X`

For example, the output should follow this general structure:

`AAPL | fourth quarter fiscal 2025 | Revenue: $X | EPS: $X | Net Income: $X`

If a value cannot be extracted, print `NOT_FOUND` for that field.

## 7. Create the Earnings CSV

After processing all five companies and their four most recent qualifying filings, save the results to:

`hw03/earnings_history.csv`

The CSV must contain these columns in this order:

- `company`
- `ticker`
- `cik`
- `filing_date`
- `period`
- `revenue_reported`
- `eps_diluted`
- `net_income`

Each row should represent one qualifying 8-K filing.

The expected maximum number of rows is 20 (five companies × four filings).

## 8. Error Handling and Reliability

The script should continue processing if an individual filing cannot be parsed or if its earnings press release cannot be located.

A failure to extract a particular field should result in `NOT_FOUND` for that field rather than causing the entire script to stop.

Do not allow one problematic filing to prevent the remaining companies and filings from being processed.

After all filings have been processed, print a confirmation that `hw03/earnings_history.csv` was saved successfully.

# Specification B — Executive Events Pipeline (Item 5.02)

Create a Python script named `hw03/hw03_executives.py` that builds an executive events dataset from SEC EDGAR Form 8-K filings for Apple Inc., Microsoft Corporation, NVIDIA Corporation, JPMorgan Chase & Co., and Walmart Inc.

## 1. Companies

Use the following companies and SEC CIK numbers exactly as provided:

- Apple Inc. — Ticker: AAPL — CIK: 0000320193
- Microsoft Corporation — Ticker: MSFT — CIK: 0000789019
- NVIDIA Corporation — Ticker: NVDA — CIK: 0001045810
- JPMorgan Chase & Co. — Ticker: JPM — CIK: 0000019617
- Walmart Inc. — Ticker: WMT — CIK: 0000104169

Do not look up or substitute different CIK numbers.

## 2. SEC User-Agent

Use the `requests` library for all HTTP requests.

Every HTTP request made to the SEC EDGAR system must include this User-Agent header:

`MIS3060 Villanova youremail@villanova.edu`

Make sure the User-Agent is included on every `requests.get()` call, not just the first request.

## 3. Find Executive Event Filings

For each of the five companies, query the SEC EDGAR submissions API using:

`https://data.sec.gov/submissions/CIK{cik}.json`

Use the company's CIK in the URL.

From the submissions data, identify Form 8-K filings where the `items` field contains `"5.02"`, which represents Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers.

Only include qualifying filings whose `filingDate` falls within the past 12 months.

## 4. Retrieve and Process the 8-K Filings

For each matching Item 5.02 filing:

1. Construct the appropriate SEC EDGAR filing URL using the company's CIK and accession number.
2. Download the full 8-K filing text.
3. Include the required SEC User-Agent header on every HTTP request.
4. Use BeautifulSoup to strip HTML and convert the filing into plain text.
5. Process the plain text to identify the executive event information.

The script must continue processing if an individual filing cannot be downloaded or parsed rather than crashing and stopping the entire pipeline.

## 5. Extract Executive Event Information

From each 8-K filing, extract:

- Event type
- Person's full name
- Person's title
- Effective date of the change

The event type must be represented as one of:

- `departure`
- `appointment`
- `both`

If a filing reports both a departure and an appointment, identify both events.

## 6. Handle Multiple Events

A single filing may contain multiple executive events.

If one filing reports multiple events, create a separate row in the output dataset for each individual event.

For example, if a filing reports that one executive departed and another executive was appointed, create two separate rows:

- One row for the departure
- One row for the appointment

Do not combine multiple people or events into one row.

## 7. Print Each Extracted Event

As each event is processed, print it using this format:

`[Ticker] | [Date] | [Event Type] | [Name] | [Title]`

For example:

`AAPL | 2026-05-15 | departure | Jane Smith | Chief Financial Officer`

If a required field cannot be reliably extracted, handle the missing value without crashing the script.

## 8. Handle Companies With No Executive Events

If a company has no qualifying Item 5.02 filings during the past 12 months, print:

`[Ticker]: No executive events in past 12 months`

This is valid data and must not be treated as an error.

The script should continue processing the remaining companies.

## 9. Create the Executive Events CSV

After processing all five companies, save the results to:

`hw03/executive_events.csv`

The CSV must contain these columns in this order:

- `company`
- `ticker`
- `cik`
- `filing_date`
- `event_type`
- `person_name`
- `title`
- `effective_date`

Each row should represent one individual executive event.

If a filing contains multiple events, each event must receive its own row.

## 10. Error Handling and Reliability

The script should continue processing if an individual filing cannot be downloaded, parsed, or extracted.

A company with zero qualifying Item 5.02 filings should produce the required no-events message rather than causing the script to fail.

The script should not stop processing the remaining companies because of one problematic filing.

After all five companies have been processed, print a confirmation that:

`hw03/executive_events.csv`

was saved successfully.