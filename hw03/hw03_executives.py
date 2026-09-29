"""
HW03 - Specification B: Executive Events Pipeline (Form 8-K, Item 5.02)

Builds an executive events dataset from SEC EDGAR Form 8-K filings for
AAPL, MSFT, NVDA, JPM and WMT. For each company, every Item 5.02 8-K
("Departure of Directors or Certain Officers; Election of Directors;
Appointment of Certain Officers; Compensatory Arrangements of Certain
Officers") filed in the past 12 months is downloaded, converted to plain text
with BeautifulSoup, and the Item 5.02 section is parsed to extract one row per
executive event:

    - event type      (departure / appointment / both)
    - person's name
    - person's title
    - effective date  (YYYY-MM-DD)

"both" is used when the SAME person both leaves one role and takes another
(e.g. a CFO who steps down and becomes Vice Chair). Different people always
get separate rows. Fields that cannot be extracted are stored as NOT_FOUND.
Item 5.02 filings that only cover compensation (no departure/appointment)
produce no event rows and a note is printed.

Output is written to hw03/executive_events.csv (next to this script).

Run from the repository root:
    python hw03/hw03_executives.py
"""

import csv
import re
import time
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Required User-Agent - sent on EVERY request to SEC EDGAR.
HEADERS = {"User-Agent": "MIS3060 Villanova chan04@villanova.edu"}

# Companies and CIKs exactly as provided in the specification.
COMPANIES = [
    {"company": "Apple Inc.", "ticker": "AAPL", "cik": "0000320193"},
    {"company": "Microsoft Corporation", "ticker": "MSFT", "cik": "0000789019"},
    {"company": "NVIDIA Corporation", "ticker": "NVDA", "cik": "0001045810"},
    {"company": "JPMorgan Chase & Co.", "ticker": "JPM", "cik": "0000019617"},
    {"company": "Walmart Inc.", "ticker": "WMT", "cik": "0000104169"},
]

NOT_FOUND = "NOT_FOUND"

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"

OUTPUT_PATH = Path(__file__).resolve().parent / "executive_events.csv"
CSV_COLUMNS = [
    "company",
    "ticker",
    "cik",
    "filing_date",
    "event_type",
    "person_name",
    "title",
    "effective_date",
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
# Step 3 - Find Item 5.02 8-K filings from the past 12 months
# ---------------------------------------------------------------------------

def twelve_months_ago(today=None):
    today = today or date.today()
    try:
        return today.replace(year=today.year - 1)
    except ValueError:  # Feb 29 -> Feb 28
        return today.replace(year=today.year - 1, day=28)


def _item_502_filings_from_block(block, cutoff):
    """Return Item 5.02 8-Ks filed on/after `cutoff` from a submissions block."""
    results = []
    for i, form in enumerate(block.get("form", [])):
        if form != "8-K":
            continue
        items = [item.strip() for item in str(block["items"][i]).split(",")]
        if "5.02" not in items:
            continue
        filing_date = block["filingDate"][i]
        if date.fromisoformat(filing_date) < cutoff:
            continue
        results.append(
            {
                "accession": block["accessionNumber"][i],
                "filing_date": filing_date,
                "report_date": block.get("reportDate", [""] * len(block["form"]))[i] or "",
                "primary_doc": block["primaryDocument"][i],
            }
        )
    return results


def find_executive_filings(cik, cutoff):
    """Return all Item 5.02 8-Ks filed since `cutoff`, newest first."""
    data = sec_get(SUBMISSIONS_URL.format(cik=cik)).json()
    recent = data["filings"]["recent"]
    filings = _item_502_filings_from_block(recent, cutoff)

    # Very frequent filers can push part of the last 12 months out of the
    # "recent" block. If the oldest recent filing is still inside the window,
    # read the older submission pages that overlap it.
    recent_dates = recent.get("filingDate", [])
    if recent_dates and date.fromisoformat(min(recent_dates)) >= cutoff:
        for older in data["filings"].get("files", []):
            try:
                if older.get("filingTo") and date.fromisoformat(older["filingTo"]) < cutoff:
                    continue
                page = sec_get(SUBMISSIONS_PAGE_URL.format(name=older["name"])).json()
                filings.extend(_item_502_filings_from_block(page, cutoff))
            except (requests.RequestException, ValueError, KeyError) as exc:
                print(f"  WARNING: could not read older submissions file {older.get('name')}: {exc}")

    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings


# ---------------------------------------------------------------------------
# Step 4 - Download the 8-K and convert it to plain text
# ---------------------------------------------------------------------------

def filing_urls(cik, filing):
    """Primary 8-K document first, then the full submission text file as a fallback."""
    base = ARCHIVE_BASE.format(cik_int=str(int(cik)), acc_nodash=filing["accession"].replace("-", ""))
    urls = []
    if filing.get("primary_doc"):
        urls.append(base + filing["primary_doc"])
    urls.append(base + f"{filing['accession']}.txt")
    return urls


def html_to_text(html):
    """Strip HTML with BeautifulSoup and normalise whitespace/punctuation."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "ix:header"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def download_filing_text(cik, filing):
    """Return the 8-K's plain text, trying each candidate URL in turn."""
    last_error = None
    for url in filing_urls(cik, filing):
        try:
            raw = sec_get(url).text
            if url.endswith(".txt"):
                # Full submission: keep only the main 8-K <DOCUMENT>, not exhibits.
                docs = re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S | re.I)
                main = next((d for d in docs if re.search(r"<TYPE>\s*8-K", d, re.I)), raw)
                raw = main
            text = html_to_text(raw)
            if text:
                return text
        except requests.RequestException as exc:
            last_error = exc
    raise RuntimeError(f"could not download filing: {last_error}")


def item_502_section(text):
    """Return just the Item 5.02 narrative (without its heading)."""
    start = re.search(r"Item\s*5\.02", text, re.I)
    section = text[start.end():] if start else text
    end = re.search(r"Item\s*(?!5\.02)\d\.\d\d|\bSIGNATURES?\b", section, re.I)
    if end:
        section = section[:end.start()]
    # Drop the standard heading, which itself contains "Departure"/"Appointment".
    section = re.sub(
        r"^[\s.:\-]*Departure of Directors or (?:Certain )?Officers[;,]?"
        r"(?:\s*Election of Directors[;,]?)?(?:\s*Appointment of Certain Officers[;,]?)?"
        r"(?:\s*(?:and\s+)?Compensatory Arrangements of Certain Officers)?\.?",
        " ",
        section,
        count=1,
        flags=re.I,
    )
    return section.strip()


# ---------------------------------------------------------------------------
# Step 5 - Extraction helpers
# ---------------------------------------------------------------------------

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Jan\\.?|Feb\\.?|Mar\\.?|Apr\\.?|Jun\\.?|Jul\\.?|Aug\\.?|Sept?\\.?|Oct\\.?|Nov\\.?|Dec\\.?"
)
DATE_RE = rf"(?:{MONTHS})\s+\d{{1,2}},?\s+20\d\d"

HONORIFICS = {"Mr", "Ms", "Mrs", "Dr", "Mses", "Messrs"}

# Capitalised words that are never part of a person's name in these filings.
STOP_WORDS = {
    # sentence starters / pronouns / prepositions
    "The", "On", "In", "As", "At", "By", "For", "Following", "Effective", "Upon", "Pursuant",
    "Also", "Each", "This", "These", "Such", "He", "She", "His", "Her", "They", "Their", "It",
    "Its", "We", "Our", "Additionally", "Further", "Prior", "Under", "With", "After", "Before",
    "During", "Until", "From", "To", "Item", "Items", "A", "An", "And", "Of", "If", "Both",
    "There", "That", "Any", "All", "No", "Not", "Other", "Mr", "Ms", "Mrs", "Dr", "Mses", "Messrs",
    # months / days
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep",
    "Sept", "Oct", "Nov", "Dec", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday",
    # corporate / role words
    "Inc", "Corporation", "Corp", "Company", "Co", "LLC", "Ltd", "Holdings", "Group", "N.A",
    "Chief", "Officer", "Officers", "Executive", "Executives", "Senior", "Vice", "President",
    "Chair", "Chairman", "Chairwoman", "Chairperson", "Director", "Directors", "Board",
    "Committee", "Counsel", "General", "Secretary", "Treasurer", "Controller", "Principal",
    "Accounting", "Financial", "Operating", "Technology", "Information", "Legal", "Administrative",
    "Human", "Resources", "People", "Marketing", "Operations", "Global", "Worldwide",
    "International", "Head", "Lead", "Independent", "Presiding", "Member", "Members", "Corporate",
    "Deputy", "Assistant", "Co-Chief", "CEO", "CFO", "COO", "CTO", "U.S", "US", "United", "States",
    "America", "Americas", "North", "Former", "Interim", "Acting", "Co-President", "Commercial",
    # company / business-unit words
    "Apple", "Microsoft", "NVIDIA", "Nvidia", "JPMorgan", "Chase", "Walmart", "Wal-Mart", "Sam's",
    "Club", "Bank", "Asset", "Wealth", "Management", "Investment", "Banking", "Consumer",
    "Community", "Retail", "Stores", "Services", "Cloud", "AI", "Gaming", "Devices", "Software",
    "Hardware", "Engineering", "Business", "Markets", "Payments", "Data", "Analytics",
    # document / legal words
    "Form", "Current", "Report", "Securities", "Exchange", "Act", "Commission", "Section", "Rule",
    "Regulation", "Annual", "Meeting", "Shareholders", "Stockholders", "Agreement", "Plan",
    "Letter", "Offer", "Stock", "Incentive", "Restricted", "Units", "Unit", "Performance", "Share",
    "Shares", "Compensation", "Audit", "Nominating", "Governance", "Risk", "Finance", "Public",
    "Responsibility", "Exhibit", "Press", "Release", "Fiscal", "Year", "Quarter", "Registrant",
    "SEC", "NYSE", "Nasdaq", "Delaware", "California", "Washington", "Arkansas", "New", "York",
    "Base", "Salary", "Bonus", "Cash", "Equity", "Award", "Awards", "Grant", "Program", "Policy",
    "Code", "Conduct", "Ethics", "Transition", "Retirement", "Separation", "Consulting",
    "Employment", "Proxy", "Statement", "Nominee", "Nominees", "Change", "Control", "Deferred",
    "Long-Term", "Short-Term", "Annual", "Term", "Related", "Party", "Transactions", "Inline",
    "XBRL", "Cover", "Page", "Signature", "Signatures", "Dated", "Date", "Name", "Title",
    "Departure", "Election", "Appointment", "Compensatory", "Arrangements", "Certain",
}

WORD_RE = re.compile(r"[A-Z][A-Za-z'\-]*\.?")
RUN_RE = re.compile(r"[A-Z][A-Za-z'\-]*\.?(?:\s+[A-Z][A-Za-z'\-]*\.?)*")
SUFFIXES = {"Jr", "Sr", "II", "III", "IV"}


def _bare(token):
    token = token.rstrip(".,")
    if token.endswith("'s"):
        token = token[:-2]
    elif token.endswith("'"):  # plural possessive: "Mr. Combs' resignation"
        token = token[:-1]
    return token


def _is_initial(token):
    return bool(re.fullmatch(r"[A-Z]\.?", token.rstrip(",")))


def find_names(sentence):
    """
    Return [(start, end, name, is_surname_only)] for people in a sentence.
    Full names are 2-4 capitalised tokens that are not stop words; a single
    token right after Mr./Ms./Dr. is returned as a surname-only reference.
    """
    found = []
    for run in RUN_RE.finditer(sentence):
        tokens = [(m.start() + run.start(), m.end() + run.start(), m.group())
                  for m in WORD_RE.finditer(run.group())]
        segment = []
        prev_honorific = False

        def flush(seg, after_honorific):
            # trim leading / trailing initials and keep suffixes attached
            while seg and _is_initial(seg[-1][2]) and _bare(seg[-1][2]) not in SUFFIXES:
                seg = seg[:-1]
            if len(seg) >= 2 and len(seg) <= 5:
                start, end = seg[0][0], seg[-1][1]
                name = " ".join(_bare(t[2]) if not _is_initial(t[2]) else t[2].rstrip(",") for t in seg)
                found.append((start, end, name, False))
            elif len(seg) == 1 and after_honorific and not _is_initial(seg[0][2]):
                found.append((seg[0][0], seg[0][1], _bare(seg[0][2]), True))

        seg_after_honorific = False
        for tok in tokens:
            bare = _bare(tok[2])
            is_middle_initial = re.fullmatch(r"[A-Z]\.", tok[2]) is not None
            if not is_middle_initial and (bare in STOP_WORDS or bare.upper() in {"CEO", "CFO", "COO"}):
                flush(segment, seg_after_honorific)
                segment = []
                seg_after_honorific = bare in HONORIFICS
                continue
            if not segment:
                seg_after_honorific = seg_after_honorific or prev_honorific
            segment.append(tok)
            # possessive or trailing comma ends a name
            if tok[2].endswith("'s") or tok[2].endswith("'") or (tok[2].endswith(".") and not _is_initial(tok[2])
                                          and bare not in SUFFIXES):
                flush(segment, seg_after_honorific)
                segment = []
                seg_after_honorific = False
        flush(segment, seg_after_honorific)
    found.sort(key=lambda f: f[0])
    return found


def split_sentences(text):
    """Split on sentence punctuation without breaking on Mr., Inc., initials, etc."""
    protected = re.sub(
        r"\b(Mr|Ms|Mrs|Dr|Mses|Messrs|Inc|Co|Corp|Ltd|Jr|Sr|No|St|U\.S|N\.A|[A-Z])\.",
        lambda m: m.group(1) + "<DOT>",
        text,
    )
    protected = re.sub(r"\b(" + MONTHS.replace("\\.?", "") + r")\.", r"\1<DOT>", protected)
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(\"])", protected)
    return [p.replace("<DOT>", ".").strip() for p in parts if p.strip()]


DEPARTURE_RE = re.compile(
    r"\bretire[sd]?\b|\bretiring\b|\bretirement\b(?!\s+(?:plan|savings|benefit|program|contribution|account))"
    r"|\bresign(?:s|ed|ing|ation)?\b|\bstep(?:s|ped|ping)?\s+down\b"
    r"|\bdepart(?:s|ed|ing|ure)?\b|\bwill\s+leave\b|\bto\s+leave\b|\bhas\s+left\b"
    r"|\bleaving\s+the\s+Company\b|\bseparat(?:e|ed|ion)\s+from\s+(?:the\s+Company|[A-Z])"
    r"|\btermination\s+of\s+(?:his|her|their)\s+employment\b"
    r"|\bnot\s+(?:to\s+)?(?:stand|seek|be\s+nominated)\s+for\s+re-?election\b"
    r"|\btransition(?:s|ed|ing)?\s+(?:out\s+)?(?:of|from)\s+(?:his|her|their|the)\s+(?:role|position)",
    re.I,
)
APPOINTMENT_RE = re.compile(
    r"\bappoint(?:s|ed|ing|ment)?\b|(?<!re-)(?<!re)\belect(?:s|ed|ing|ion)?\b(?!\s+to\s+(?:defer|receive))"
    r"|\bnamed\b(?!\s+executive)|\bpromot(?:ed|es|ion)\b|\bhired\b"
    r"|\bwill\s+(?:succeed|become|serve\s+as|assume|join)\b|(?<!continue )\bto\s+(?:succeed|serve\s+as|become)\b"
    r"|\bwill\s+transition\s+to\s+the\s+role\s+of\b"
    r"|\bsucceeds\b|\bassume(?:s|d)?\s+the\s+(?:role|position|title)\b",
    re.I,
)
# Appointment keywords where the person comes BEFORE the keyword.
APPOINT_SUBJECT_FIRST = re.compile(r"will|to\s|succeeds|assume", re.I)
PASSIVE_BEFORE = re.compile(r"(?:was|were|has\s+been|have\s+been|had\s+been|will\s+be|is|to\s+be|being|be)\s+$", re.I)
NOUN_OF = re.compile(r"^(?:appointment|election|resignation|retirement|departure|promotion|separation)\s*$", re.I)

TITLE_KEYWORDS = re.compile(
    r"Chief|Officer|President|Chair|Director|Counsel|Controller|Treasurer|Secretary|"
    r"Head\b|\b(?:CEO|CFO|COO|CAO|CTO|CIO|CLO|CHRO|VP|SVP|EVP)s?\b|\bmember of the Board\b|\bdirector\b",
    re.I,
)


def _clean_title(raw):
    title = raw.strip(" ,;:.-\"")
    title = re.sub(r"\s+(?:since|from|during|in)\s+(?:\d{4}|" + MONTHS + r")\b.*$", "", title)
    title = re.sub(r"^(?:a|an|the|its|our|his|her|their)\s+", "", title, flags=re.I)
    title = re.sub(r"^(?:who\s+(?:has|had)\s+(?:served|been)\s+as\s+(?:the\s+|a\s+)?)", "", title, flags=re.I)
    title = re.sub(r"^(?:Company's|Corporation's|[A-Z][A-Za-z.&\-]*(?:\s[A-Z][A-Za-z.&\-]*)*'s)\s+", "", title)
    title = re.sub(r"\s+(?:of|at|for)\s+(?:the\s+)?(?:Company|Corporation|Registrant|firm|Firm)\b.*$", "", title)
    title = re.sub(r"\s+(?:of|at)\s+(?:Apple|Microsoft|NVIDIA|JPMorgan|Walmart)\b.*$", "", title)
    title = re.sub(r"^(?:current|currently|former|then|new|incoming|outgoing|sole)\s+", "", title, flags=re.I)
    title = re.sub(r"\b(Co-President|President|CEO|Officer)s\b", r"\1", title)  # "Co-Presidents"
    title = re.sub(r"\bU\.S\b(?!\.)", "U.S.", title).strip(" ,;:-\"")
    title = title.strip(" ,;:.-\"") if not title.endswith("U.S.") else title
    if re.fullmatch(r"(?:a\s+)?(?:member of the Board(?: of Directors)?|director|Board member)", title, re.I):
        return "Director"
    return title[:1].upper() + title[1:] if title else title


TITLE_STOP = (
    r"(?=,?\s+(?:effective|with\s+effect|and\s+will|and\s+has|and\s+as|to\s+succeed|succeeding|"
    r"upon|following|until|through|in\s+connection|replacing|who|which|where|while|when|after|before|"
    r"since|from\s+\d|in\s+(?:\d{4}|late|early|mid|" + MONTHS + r")|reporting|age|"
    r"on\s+(?:the|" + MONTHS + r"))\b|,\s+(?-i:[a-z]|\d)|\.(?!S\b)|[;(]|$)"
)


def _apposition_title(text, name_last):
    """'Jane Smith, Executive VP, Operations (the ...), will' / 'Kate Adams, who has served as X'."""
    m = re.search(
        re.escape(name_last) + r",\s+(?:age\s+\d+,\s+)?(?:who\s+(?:has|had)\s+(?:served|been)\s+as\s+)?(.{3,160}?)"
        r"(?=\s*\(|,\s+(?:or|will|has|have|had|notified|informed|announced|is|was|and)\b|"
        r",?\s+(?:will|has|notified|informed|announced|is|was)\b|,?\s+since\b|[.;]|$)",
        text,
    )
    if m and TITLE_KEYWORDS.search(m.group(1)):
        return _clean_title(m.group(1))
    return NOT_FOUND


def title_after_keyword(after_text):
    """Title that directly follows an appointment verb, unless a person's name comes first."""
    m = re.match(r"\s*(?:as\s+|to\s+)?(.{3,140}?)" + TITLE_STOP, after_text)
    if not m or not TITLE_KEYWORDS.search(m.group(1)):
        return NOT_FOUND
    names = find_names(m.group(1))  # full names and "Mr. Maestri"-style references
    if names and names[0][0] <= 5:
        return NOT_FOUND  # "appointed John Ternus, Apple's SVP ..., as CEO" - not his new title
    return _clean_title(m.group(1))


def extract_title(fragment, name_last=None):
    """Find a job title in a sentence fragment. Returns NOT_FOUND if none."""
    patterns = [
        # "from his role as Senior Vice President and Chief Financial Officer"
        r"(?:role|position|post|roles|positions)\s+(?:as|of)\s+(.{3,140}?)" + TITLE_STOP,
        # "as Senior Vice President and Chief Financial Officer"
        r"\bas\s+(?:the\s+|its\s+|our\s+|a\s+|an\s+)?(.{3,140}?)" + TITLE_STOP,
        # "to the Board of Directors" -> Director
        r"\b(?:to|from|on)\s+(?:the\s+|its\s+|our\s+)?(?:Company's\s+)?(Board of Directors|Board)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, fragment, re.I):
            candidate = match.group(1)
            if re.fullmatch(r"Board(?: of Directors)?", candidate, re.I):
                return "Director"
            if TITLE_KEYWORDS.search(candidate):
                return _clean_title(candidate)
    # Apposition: "Jane Smith, Executive Vice President and Chief People Officer,"
    if name_last:
        title = _apposition_title(fragment, name_last)
        if title != NOT_FOUND:
            return title
    # Any bare title phrase
    m = re.search(
        r"((?:(?:Senior|Executive|Group|Corporate|Deputy|Assistant|Principal|Global)\s+)*"
        r"(?:Vice\s+)?(?:President|Chair(?:man|woman|person)?|Chief\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+Officer|"
        r"General\s+Counsel|Controller|Treasurer|Corporate\s+Secretary)"
        r"(?:(?:,\s+|\s+and\s+)(?:(?:Senior|Executive)\s+)*(?:Vice\s+)?(?:President|Chief\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+Officer|"
        r"General\s+Counsel|Chair(?:man)?|Corporate\s+Secretary|Treasurer|Controller))*)",
        fragment,
    )
    if m:
        return _clean_title(m.group(1))
    return NOT_FOUND


def to_iso(date_text):
    cleaned = re.sub(r"\s+", " ", date_text.replace(",", ", ")).replace(" ,", ",")
    cleaned = re.sub(r",\s*", ", ", cleaned).replace(".", "")
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned)
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return NOT_FOUND


EFFECTIVE_RE = re.compile(
    r"effective(?:\s+as\s+of)?(?:\s+the\s+(?:close|end|start|beginning)\s+of\s+business)?"
    r"(?:\s+on)?(?:\s+or\s+about)?\s+(" + DATE_RE + r")",
    re.I,
)
IMMEDIATE_RE = re.compile(r"effective\s+immediately|with\s+immediate\s+effect", re.I)


DEFINED_DATE_RE = re.compile(r"(" + DATE_RE + r")\s*\(the\s+\"([A-Z][A-Za-z ]*Date)\"\)")
DEFINED_REF_RE = re.compile(r"(?:effective|on|as of)(?:\s+as\s+of)?(?:\s+on)?\s+the\s+([A-Z][A-Za-z]*(?:\s[A-Z][A-Za-z]*)*\s?Date)\b")


def extract_effective_date(sentence, following, report_date, defined_dates=None):
    """Effective date for an event, from its sentence, then nearby sentences."""
    defined_dates = defined_dates or {}
    for text in [sentence] + following:
        m = EFFECTIVE_RE.search(text)
        if m:
            return to_iso(m.group(1))
        # "effective on the Transition Date" -> date defined elsewhere in the filing
        m = DEFINED_REF_RE.search(text)
        if m and m.group(1) in defined_dates:
            return defined_dates[m.group(1)]
        if IMMEDIATE_RE.search(text):
            return report_date or NOT_FOUND
    # "will retire on March 31, 2026" / "through January 31, 2026"
    m = re.search(r"\b(?:on|as of|through|until|at the close of business on)\s+(" + DATE_RE + r")", sentence)
    if m:
        return to_iso(m.group(1))
    m = re.search(DATE_RE, sentence)
    if m:
        return to_iso(m.group(0))
    return NOT_FOUND


# ---------------------------------------------------------------------------
# Step 5/6 - Turn the Item 5.02 text into individual events
# ---------------------------------------------------------------------------

def _pick_people(names, kw_start, kw_end, direction, sentence):
    """Nearest name in `direction`, plus names joined to it by 'and' ("A, 61, and B")."""
    before = [n for n in names if n[1] <= kw_start]
    after = [n for n in names if n[0] >= kw_end]
    if direction == "before":
        group, pool, step = (before[-1:], before, -1) if before else (after[:1], after, 1)
    else:
        group, pool, step = (after[:1], after, 1) if after else (before[-1:], before, -1)
    if not group:
        return []
    joiner = re.compile(r"\s*(?:,\s*(?:age\s+)?\d{1,3}\s*)?,?\s+and\s+$")
    i = pool.index(group[0])
    j = i + step
    if 0 <= j < len(pool):
        a, b = (pool[j], group[0]) if step == -1 else (group[0], pool[j])
        if joiner.fullmatch(" " + sentence[a[1]:b[0]]):
            group.append(pool[j])
    return group


def _record_event(record, kind, person, match, sentence, following, report_date,
                  defined_dates, resolve):
    """Fill record[kind] (and, for 'transition from X to Y', both kinds) for one person."""
    # Only borrow a date from the next sentences if they are still about
    # this person (no other full names introduced).
    nearby = []
    for nxt in following:
        others = {resolve(n) for n in find_names(nxt)} - {person, None}
        if others:
            break
        nearby.append(nxt)

    fragment = sentence[match.start():]
    title = NOT_FOUND
    if kind == "appointment":
        title = title_after_keyword(sentence[match.end():])
    if title == NOT_FOUND:
        title = extract_title(fragment)
    if title == NOT_FOUND:
        title = extract_title(sentence, name_last=person.split()[-1])
    date_source = fragment if (EFFECTIVE_RE.search(fragment) or IMMEDIATE_RE.search(fragment)
                               or DEFINED_REF_RE.search(fragment)) else sentence
    effective = extract_effective_date(date_source, nearby, report_date, defined_dates)

    # "will transition from his role as CEO to Executive Chair" = departure AND appointment
    if kind == "departure" and match.group(0).lower().startswith("transition"):
        t = re.search(r"(?:role|position)\s+as\s+(.{3,100}?)\s+to\s+(?:the\s+role\s+of\s+|become\s+)?"
                      r"(.{3,120}?)" + TITLE_STOP, fragment)
        if t and TITLE_KEYWORDS.search(t.group(1)) and TITLE_KEYWORDS.search(t.group(2)):
            title = _clean_title(t.group(1))
            record.setdefault("appointment", {"title": _clean_title(t.group(2)),
                                              "effective_date": effective})
    record[kind] = {"title": title, "effective_date": effective}


def extract_events(section, report_date):
    """
    Return a list of events: {event_type, person_name, title, effective_date}.
    One entry per person; a person with both a departure and an appointment
    in the same filing is reported once with event_type "both".
    """
    section = section.replace("U.S.", "U.S")  # keep "Walmart U.S." inside titles
    defined_dates = {term: to_iso(d) for d, term in DEFINED_DATE_RE.findall(section)}
    sentences = split_sentences(section)
    people = {}         # full name -> {"departure": {...}, "appointment": {...}, "order": n}
    known_names = []    # full names in order of first appearance
    last_person = None

    def core(name):
        return [t for t in name.split() if not _is_initial(t) and t not in SUFFIXES]

    # Pass 1: learn full names so "Mr. Parekh" / "Ms. Nora Johnson" / "John Furner"
    # can be matched to "Suzanne Nora Johnson" / "John R. Furner" later.
    for sentence in sentences:
        for _, _, name, surname_only in find_names(sentence):
            if not surname_only and name not in known_names:
                known_names.append(name)

    def canonical(name, surname_only):
        tokens = core(name)
        matches = []
        for known in known_names:
            k = core(known)
            if not tokens or not k:
                continue
            if k[-len(tokens):] == tokens or (not surname_only and len(tokens) >= 2
                                              and k[0] == tokens[0] and k[-1] == tokens[-1]):
                matches.append(known)
        if matches:
            return max(matches, key=len)  # longest form, e.g. "Suzanne Nora Johnson"
        return None if surname_only else name

    def resolve(entry):
        _, _, name, surname_only = entry
        return canonical(name, surname_only)  # None = unresolved fragment such as "Combs"

    # Pass 2: classify each keyword and attach it to a person.
    for idx, sentence in enumerate(sentences):
        names = find_names(sentence)
        following = sentences[idx + 1: idx + 3]

        hits = [("departure", m) for m in DEPARTURE_RE.finditer(sentence)]
        hits += [("appointment", m) for m in APPOINTMENT_RE.finditer(sentence)]
        hits.sort(key=lambda h: h[1].start())

        for kind, match in hits:
            keyword = match.group(0)
            prefix = sentence[:match.start()]
            suffix = sentence[match.end():]

            if kind == "appointment":
                if APPOINT_SUBJECT_FIRST.match(keyword) or PASSIVE_BEFORE.search(prefix):
                    direction = "before"
                elif re.match(r"\s+of\b", suffix) and NOUN_OF.match(keyword):
                    direction = "after"
                elif re.search(r"(?:his|her|their|'s)\s+$", prefix):
                    direction = "before"
                else:
                    direction = "after"
            else:
                if re.match(r"\s+(?:of|by)\b", suffix) and NOUN_OF.match(keyword):
                    direction = "after"
                else:
                    direction = "before"

            chosen = [p for p in (resolve(c) for c in
                                  _pick_people(names, match.start(), match.end(), direction, sentence)) if p]
            if chosen:
                persons = chosen
            elif last_person and re.search(r"\b(?:he|she|they|his|her)\b", sentence, re.I):
                persons = [last_person]
            else:
                continue
            last_person = persons[0]

            for person in persons:
                record = people.setdefault(person, {"order": len(people)})
                # Skip boilerplate like "in connection with Mr. Smith's appointment,"
                # if we already recorded that event for this person.
                if kind in record:
                    continue
                _record_event(record, kind, person, match, sentence, following,
                              report_date, defined_dates, resolve)

    # Title fallback: "Kate Adams, who has served as Apple's general counsel" elsewhere
    for person, record in people.items():
        for kind in ("departure", "appointment"):
            if kind in record and record[kind]["title"] == NOT_FOUND:
                record[kind]["title"] = _apposition_title(section, core(person)[-1])

    events = []
    for person, record in sorted(people.items(), key=lambda p: p[1]["order"]):
        has_dep, has_app = "departure" in record, "appointment" in record
        if not (has_dep or has_app):
            continue
        if has_dep and has_app:
            dep, app = record["departure"], record["appointment"]
            titles = [t for t in (dep["title"], app["title"]) if t != NOT_FOUND]
            if len(titles) == 2 and titles[0] != titles[1]:
                title = f"{titles[0]} -> {titles[1]}"
            else:
                title = titles[0] if titles else NOT_FOUND
            eff = app["effective_date"] if app["effective_date"] != NOT_FOUND else dep["effective_date"]
            events.append({"event_type": "both", "person_name": person, "title": title,
                           "effective_date": eff})
        else:
            kind = "departure" if has_dep else "appointment"
            events.append({"event_type": kind, "person_name": person,
                           "title": record[kind]["title"],
                           "effective_date": record[kind]["effective_date"]})
    return events


# ---------------------------------------------------------------------------
# Step 7 - Printing
# ---------------------------------------------------------------------------

def print_event(row):
    print(
        f"{row['ticker']} | {row['filing_date']} | {row['event_type']} | "
        f"{row['person_name']} | {row['title']}"
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_filing(company, filing):
    """Return the event rows for one 8-K. Never raises."""
    try:
        text = download_filing_text(company["cik"], filing)
        section = item_502_section(text)
        events = extract_events(section, filing.get("report_date", ""))
    except Exception as exc:
        print(
            f"  WARNING: {company['ticker']} {filing['filing_date']} "
            f"({filing['accession']}) could not be processed: {exc}"
        )
        return []

    if not events:
        print(
            f"  NOTE: {company['ticker']} {filing['filing_date']} ({filing['accession']}): "
            f"no departure or appointment identified (e.g. compensation-only Item 5.02)."
        )
    rows = []
    for event in events:
        rows.append(
            {
                "company": company["company"],
                "ticker": company["ticker"],
                "cik": company["cik"],
                "filing_date": filing["filing_date"],
                "event_type": event.get("event_type") or NOT_FOUND,
                "person_name": event.get("person_name") or NOT_FOUND,
                "title": event.get("title") or NOT_FOUND,
                "effective_date": event.get("effective_date") or NOT_FOUND,
            }
        )
    return rows


def main():
    cutoff = twelve_months_ago()
    print(f"Looking for Item 5.02 8-K filings dated {cutoff.isoformat()} or later.")
    rows = []
    for company in COMPANIES:
        print(f"\n=== {company['company']} ({company['ticker']}) ===")
        try:
            filings = find_executive_filings(company["cik"], cutoff)
        except Exception as exc:
            print(f"  WARNING: could not retrieve submissions for {company['ticker']}: {exc}")
            continue

        if not filings:
            print(f"{company['ticker']}: No executive events in past 12 months")
            continue

        for filing in filings:
            for row in process_filing(company, filing):
                print_event(row)
                rows.append(row)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nhw03/executive_events.csv saved successfully ({len(rows)} rows).")


if __name__ == "__main__":
    main()
