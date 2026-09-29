"""
HW03 - Specification A: Earnings Pipeline (Form 8-K, Item 2.02)

Builds an earnings history dataset from SEC EDGAR Form 8-K filings for
AAPL, MSFT, NVDA, JPM and WMT. For each company, the four most recent
Item 2.02 ("Results of Operations and Financial Condition") 8-Ks are located,
the earnings press release exhibit (EX-99.x) is downloaded and converted to
plain text with BeautifulSoup, and regular expressions extract:

    - reporting period   (e.g. "fourth quarter fiscal 2025")
    - quarterly revenue  (kept with its scale, e.g. "102.5 billion")
    - diluted EPS
    - net income

Any value that cannot be extracted is stored as NOT_FOUND. Output is written
to hw03/earnings_history.csv (next to this script).

Run from the repository root:
    python hw03/hw03_earnings.py
"""

import csv
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Required User-Agent - sent on EVERY request to SEC EDGAR.
HEADERS = {"User-Agent": "MIS3060 Villanova chan04@villanova.edu"}

# Companies and CIKs exactly as provided in the specification.
# fy_end_month is used only as a fallback to label the fiscal quarter when a
# press release states "quarter ended <date>" but not the quarter name/year.
COMPANIES = [
    {"company": "Apple Inc.", "ticker": "AAPL", "cik": "0000320193", "fy_end_month": 9},
    {"company": "Microsoft Corporation", "ticker": "MSFT", "cik": "0000789019", "fy_end_month": 6},
    {"company": "NVIDIA Corporation", "ticker": "NVDA", "cik": "0001045810", "fy_end_month": 1},
    {"company": "JPMorgan Chase & Co.", "ticker": "JPM", "cik": "0000019617", "fy_end_month": 12},
    {"company": "Walmart Inc.", "ticker": "WMT", "cik": "0000104169", "fy_end_month": 1},
]

FILINGS_PER_COMPANY = 4
NOT_FOUND = "NOT_FOUND"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"

OUTPUT_PATH = Path(__file__).resolve().parent / "earnings_history.csv"
CSV_COLUMNS = [
    "company",
    "ticker",
    "cik",
    "filing_date",
    "period",
    "revenue_reported",
    "eps_diluted",
    "net_income",
]

REQUEST_DELAY_SECONDS = 0.2  # stay well under SEC's 10 requests/second limit


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def sec_get(url):
    """GET a URL from SEC EDGAR with the required User-Agent header."""
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response


# ---------------------------------------------------------------------------
# Step 3 - Find Item 2.02 8-K filings
# ---------------------------------------------------------------------------

def _item_202_filings_from_block(block):
    """Return Item 2.02 8-K filings from a submissions 'recent'-style block."""
    results = []
    forms = block.get("form", [])
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        items = [item.strip() for item in str(block["items"][i]).split(",")]
        if "2.02" not in items:
            continue
        results.append(
            {
                "accession": block["accessionNumber"][i],
                "filing_date": block["filingDate"][i],
                "primary_doc": block["primaryDocument"][i],
            }
        )
    return results


def find_earnings_filings(cik, count=FILINGS_PER_COMPANY):
    """Return the `count` most recent 8-K filings whose items include 2.02."""
    data = sec_get(SUBMISSIONS_URL.format(cik=cik)).json()
    filings = _item_202_filings_from_block(data["filings"]["recent"])

    # Frequent filers (e.g. JPM files many 424B2s) may push older 8-Ks out of
    # the "recent" block; page through older submission files if needed.
    for older in data["filings"].get("files", []):
        if len(filings) >= count:
            break
        try:
            page = sec_get(SUBMISSIONS_PAGE_URL.format(name=older["name"])).json()
            filings.extend(_item_202_filings_from_block(page))
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"  WARNING: could not read older submissions file {older.get('name')}: {exc}")

    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings[:count]


# ---------------------------------------------------------------------------
# Step 4 - Locate and download the earnings press release exhibit
# ---------------------------------------------------------------------------

def _clean_href(href, base_url):
    """Turn an index-page link (possibly an /ix?doc= viewer link) into a full URL."""
    if href.startswith("/ix?doc="):
        href = href[len("/ix?doc="):]
    if href.startswith("/"):
        return "https://www.sec.gov" + href
    if href.startswith("http"):
        return href
    return base_url + href


def find_press_release_url(cik, accession):
    """
    Use the filing index page to find the EX-99 press release (.htm).
    Preference: EX-99.1 > any EX-99 whose description mentions a press/earnings
    release > any other EX-99 .htm. Falls back to the index.json directory
    listing if the index page cannot be parsed. Returns None if not found.
    """
    cik_int = str(int(cik))
    acc_nodash = accession.replace("-", "")
    base_url = ARCHIVE_BASE.format(cik_int=cik_int, acc_nodash=acc_nodash)

    # --- Primary: the human-readable filing index page ---------------------
    index_url = f"{base_url}{accession}-index.htm"
    try:
        soup = BeautifulSoup(sec_get(index_url).text, "html.parser")
        candidates = []
        for table in soup.find_all("table", class_="tableFile"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue
                description = cells[1].get_text(" ", strip=True).lower()
                link = cells[2].find("a")
                doc_type = cells[3].get_text(strip=True).upper()
                if not link or not doc_type.startswith("EX-99"):
                    continue
                href = link.get("href", "")
                if not href.lower().endswith((".htm", ".html")):
                    continue
                if doc_type == "EX-99.1":
                    rank = 0
                elif "release" in description or "earnings" in description:
                    rank = 1
                else:
                    rank = 2
                candidates.append((rank, _clean_href(href, base_url)))
        if candidates:
            candidates.sort(key=lambda c: c[0])
            return candidates[0][1]
    except requests.RequestException as exc:
        print(f"  WARNING: could not load filing index {index_url}: {exc}")

    # --- Fallback: JSON directory listing, match exhibit-style file names ---
    try:
        listing = sec_get(base_url + "index.json").json()
        names = [item["name"] for item in listing["directory"]["item"]]
        pattern = re.compile(r"ex[-_]?99", re.IGNORECASE)
        htm_names = [n for n in names if n.lower().endswith((".htm", ".html")) and pattern.search(n)]
        if htm_names:
            htm_names.sort(key=lambda n: (not re.search(r"ex[-_]?99[-_.]?1(?!\d)", n, re.I), n))
            return base_url + htm_names[0]
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"  WARNING: could not load directory listing for {accession}: {exc}")

    return None


def html_to_text(html):
    """Strip HTML with BeautifulSoup and normalise whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = text.replace("\xa0", " ").replace("’", "'")
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Step 5 - Regex extraction
# ---------------------------------------------------------------------------

ORDINALS = {"1": "first", "2": "second", "3": "third", "4": "fourth"}
QUARTER_WORD = r"(first|second|third|fourth)"
MONTHS = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)
SCALE = r"(billion|million|trillion)"
NUMBER = r"([\d,]+(?:\.\d+)?)"


def _full_year(year_text):
    year = int(year_text)
    return year + 2000 if year < 100 else year


def _period_from_text(text):
    """Find an explicitly stated fiscal quarter, e.g. 'fourth quarter fiscal 2025'."""
    patterns = [
        # "fourth quarter fiscal 2025", "third-quarter fiscal year 2026",
        # "fourth quarter of fiscal 2025", "third-quarter 2025"
        (rf"\b{QUARTER_WORD}[- ]quarter,? (?:of )?(?:fiscal )?(?:year )?(20\d\d)\b", "qy"),
        # "fiscal 2025 fourth quarter", "fiscal year 2026 first quarter"
        (rf"\bfiscal (?:year )?(20\d\d) {QUARTER_WORD}[- ]quarter\b", "yq"),
        # "Q3 FY26", "Q4 fiscal 2025", "Q2 FY 2026"
        (r"\bQ([1-4]) ?(?:FY|fiscal(?: year)?) ?'?(\d{2,4})\b", "Qy"),
    ]
    best = None
    for pattern, kind in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        if kind == "qy":
            quarter, year = match.group(1).lower(), match.group(2)
        elif kind == "yq":
            year, quarter = match.group(1), match.group(2).lower()
        else:
            quarter = ORDINALS[match.group(1)] 
            year = match.group(2)
        # Keep the earliest match in the document (headline / lead paragraph).
        if best is None or match.start() < best[0]:
            best = (match.start(), f"{quarter} quarter fiscal {_full_year(year)}")
    return best


def _period_from_quarter_end(text, fy_end_month):
    """Fallback: derive the fiscal quarter from 'quarter ended <Month> <day>, <year>'."""
    match = re.search(
        rf"(?:quarter|three months) ended ({MONTHS}) (\d{{1,2}}),? (20\d\d)", text, re.IGNORECASE
    )
    if not match:
        return None
    end_date = datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%B %d %Y")
    month, year = end_date.month, end_date.year
    # 52/53-week fiscal calendars can end a few days into the next month
    # (e.g. Apple's quarter ended July 1) - treat those as the prior month.
    if end_date.day <= 7:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    fiscal_year = year if month <= fy_end_month else year + 1
    fy_start_month = fy_end_month % 12 + 1
    quarter_number = (month - fy_start_month) % 12 // 3 + 1
    return f"{ORDINALS[str(quarter_number)]} quarter fiscal {fiscal_year}"


def extract_period(text, fy_end_month):
    # 1) A quarter stated in the headline / lead paragraph is most reliable.
    lead = _period_from_text(text[:2000])
    if lead:
        return lead[1]
    # 2) "quarter ended <date>" mapped onto the company's fiscal calendar.
    derived = _period_from_quarter_end(text, fy_end_month)
    if derived:
        return derived
    # 3) Any explicit mention anywhere in the release.
    anywhere = _period_from_text(text)
    return anywhere[1] if anywhere else NOT_FOUND


def _is_non_gaap(text, start):
    """True if the words just before a match mark it as a non-GAAP/adjusted figure."""
    preceding = text[max(0, start - 25):start].lower()
    return any(word in preceding for word in ("non-gaap", "adjusted", "managed", "segment"))


def extract_revenue(text):
    patterns = [
        # "quarterly revenue of $102.5 billion", "Revenue was $76.4 billion",
        # "Total revenue was $179.5 billion", "reported revenue of $46.4 billion",
        # "revenue for the third quarter ended October 26, 2025, of $57.0 billion"
        rf"\b(?:total |net |quarterly |record |reported |consolidated )?revenues?\b[^$%]{{0,90}}?\$\s?{NUMBER}\s*{SCALE}",
        # "net sales of $124.3 billion"
        rf"\b(?:total )?net sales\b[^$%]{{0,60}}?\$\s?{NUMBER}\s*{SCALE}",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _is_non_gaap(text, match.start()):
                continue
            return f"{match.group(1)} {match.group(2).lower()}"
    return NOT_FOUND


def extract_eps(text):
    patterns = [
        # "diluted earnings per share of $1.85", "Diluted earnings per share was $3.65"
        r"\bdiluted (?:earnings|net income) per (?:common )?share\b[^$]{0,80}?\$\s?(\d+\.\d{2})",
        # "GAAP earnings per diluted share for the quarter were $1.30"
        r"\bearnings per diluted (?:common )?share\b[^$]{0,80}?\$\s?(\d+\.\d{2})",
        # "GAAP EPS of $0.77", "Diluted EPS was $2.40"
        r"\b(?:GAAP |diluted )EPS\b[^$]{0,40}?\$\s?(\d+\.\d{2})",
        # "net income of $14.4 billion, or $5.07 per share"
        r"\$\s?(\d+\.\d{2}) per (?:diluted )?share",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _is_non_gaap(text, match.start()):
                continue
            return match.group(1)
    return NOT_FOUND


def extract_net_income(text):
    # Narrative sentences that state a scale: "Net income was $27.2 billion",
    # "reports third-quarter 2025 net income of $14.4 billion".
    narrative = (
        rf"\bnet income\b(?! per)[^$%]{{0,60}}?\$\s?{NUMBER}\s*{SCALE}"
    )
    for match in re.finditer(narrative, text, re.IGNORECASE):
        if _is_non_gaap(text, match.start()):
            continue
        return f"{match.group(1)} {match.group(2).lower()}"

    # Financial statement tables: "Net income $ 27,466" or
    # "Consolidated net income attributable to Walmart $ 6,146".
    unit = " million" if re.search(r"in millions", text, re.IGNORECASE) else ""
    table_patterns = [
        r"\bnet income attributable to (?!noncontrolling)[A-Za-z][\w.&' ]{0,30}?\s\$?\s?(\d{1,3}(?:,\d{3})+|\d+\.\d+)\b",
        r"\bnet income\s+\$?\s?(\d{1,3}(?:,\d{3})+|\d+\.\d+)\b",
    ]
    for pattern in table_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _is_non_gaap(text, match.start()):
                continue
            return f"{match.group(1)}{unit}"
    return NOT_FOUND


def extract_earnings(text, fy_end_month):
    """Run every extractor; each one independently falls back to NOT_FOUND."""
    results = {}
    extractors = {
        "period": lambda: extract_period(text, fy_end_month),
        "revenue_reported": lambda: extract_revenue(text),
        "eps_diluted": lambda: extract_eps(text),
        "net_income": lambda: extract_net_income(text),
    }
    for field, extractor in extractors.items():
        try:
            results[field] = extractor()
        except Exception as exc:  # one bad pattern must not sink the row
            print(f"  WARNING: error extracting {field}: {exc}")
            results[field] = NOT_FOUND
    return results


# ---------------------------------------------------------------------------
# Step 6 - Printing
# ---------------------------------------------------------------------------

def _money(value):
    return value if value == NOT_FOUND else f"${value}"


def print_row(row):
    print(
        f"{row['ticker']} | {row['period']} | "
        f"Revenue: {_money(row['revenue_reported'])} | "
        f"EPS: {_money(row['eps_diluted'])} | "
        f"Net Income: {_money(row['net_income'])}"
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_filing(company, filing):
    """Build one output row for a single 8-K filing. Never raises."""
    row = {
        "company": company["company"],
        "ticker": company["ticker"],
        "cik": company["cik"],
        "filing_date": filing["filing_date"],
        "period": NOT_FOUND,
        "revenue_reported": NOT_FOUND,
        "eps_diluted": NOT_FOUND,
        "net_income": NOT_FOUND,
    }
    try:
        release_url = find_press_release_url(company["cik"], filing["accession"])
        if release_url is None:
            print(
                f"  WARNING: {company['ticker']} {filing['filing_date']} "
                f"({filing['accession']}): earnings press release exhibit not found - skipping."
            )
            return None
        text = html_to_text(sec_get(release_url).text)
        row.update(extract_earnings(text, company["fy_end_month"]))
    except Exception as exc:
        print(
            f"  WARNING: {company['ticker']} {filing['filing_date']} "
            f"({filing['accession']}) could not be processed: {exc}"
        )
    return row


def main():
    rows = []
    for company in COMPANIES:
        print(f"\n=== {company['company']} ({company['ticker']}) ===")
        try:
            filings = find_earnings_filings(company["cik"])
        except Exception as exc:
            print(f"  WARNING: could not retrieve submissions for {company['ticker']}: {exc}")
            continue
        if not filings:
            print(f"  WARNING: no Item 2.02 8-K filings found for {company['ticker']}.")
            continue

        for filing in filings:
            row = process_filing(company, filing)
            if row is None:
                continue
            print_row(row)
            rows.append(row)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nhw03/earnings_history.csv saved successfully ({len(rows)} rows).")


if __name__ == "__main__":
    main()
