#!/usr/bin/env python3
"""
01_profile.py - Part A: raw data-quality audit for the Wildcat Capital extracts.

What it does
------------
1. Reads every CSV in data/raw/ AS TEXT (dtype=str, keep_default_na=False), so
   values such as "", "None", "007" or " 12" are seen exactly as delivered.
2. Profiles every column of every table  -> reports/profile_raw.csv
3. Runs four categories of data-quality checks -> reports/dq_summary_raw.csv
      column_level   types, allowed values, ranges, PK uniqueness
      single_table   duplicates, near-duplicates, within-row contradictions
      referential    FK values with no matching PK in the parent table
      cross_table    business rules between facts and dimensions / facts
4. Classifies every finding as ERROR / WARN / INFO (see SEVERITY GUIDE below).

The raw files are only READ. Nothing in data/raw/ is renamed, cleaned or
overwritten, and the script refuses to write its reports inside data/raw/.

How to run (from the repository root, i.e. the hw04 folder)
-----------------------------------------------------------
    python scripts/01_profile.py
    python scripts/01_profile.py --raw-dir data/raw --out-dir reports

Relative paths are resolved against the repository root (the folder above
scripts/), so the script also works if VS Code launches it from another folder.

Severity guide
--------------
ERROR  Clearly wrong, or will break joins / calculations (unparseable numbers,
       duplicate primary keys, orphan foreign keys, impossible values).
WARN   Suspicious - needs investigation before cleaning (placeholders, outliers,
       rules that fail for some rows, numbers that do not reconcile).
INFO   Worth knowing, probably legitimate (structural blanks, value mappings,
       passed checks).

Where a rule rests on an ASSUMPTION rather than documented business logic, the
evidence text says "assumption" so it can be confirmed before Part B cleaning.

Status vs severity
------------------
PASS     a test that COULD have failed, and did not.
FINDING  INFO rows also hold descriptive observations (e.g. category sets or
         bands that were read off this data). Those cannot fail by
         construction, so they are deliberately NOT reported as PASS.

Severity decisions for the judgement calls
------------------------------------------
ERROR  Dividend income vs AUM: dividend amount always equals shares x price,
       and in many client-years the dividends exceed the client's whole
       average AUM (>100% yield). No real portfolio can do that, so this is a
       calculation defect, not just odd semantics.
ERROR  First oversell events for clients onboarded inside the data window:
       their whole history is in the extract and the table has no transfer-in
       type, so a Sell that takes holdings below zero means the ledger is
       internally inconsistent. Later rows on an already-negative position
       are consequences, kept as WARN so the defect is not double-counted.
WARN   Extreme return_ytd / active_alpha_ytd: implausible, but the source
       already flags them (is_return_suspect) and no return methodology is
       documented, so "impossible" cannot be shown.
WARN   Advisory Fee transactions vs fact_fees: they do not reconcile, but
       nothing documents that the two must match, and each looks plausible on
       its own scale.
WARN   aum = 1000.0: an exact repeated value that looks like a placeholder or
       floor, but there is no independent proof that it is wrong.

Audit reference date
--------------------
"Date in the future" checks compare against a FIXED reference date
(AUDIT_REFERENCE_DATE, overridable with --as-of), not the system clock, so a
rerun on another day gives identical reports.

Requires: Python 3.9+, pandas >= 1.5, numpy.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# 1. PATHS AND GLOBAL SETTINGS
# =============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_RAW_DIR = "data/raw"
DEFAULT_OUT_DIR = "reports"
PROFILE_FILE = "profile_raw.csv"
SUMMARY_FILE = "dq_summary_raw.csv"

# Fixed reference date for "date in the future" checks: the day the raw extracts
# were saved into data/raw/ (file timestamps 2026-10-21). No real past event can
# be dated after it. Override with --as-of; never use the system clock.
AUDIT_REFERENCE_DATE = "2026-10-21"
REF_DATE = pd.Timestamp(AUDIT_REFERENCE_DATE)  # replaced in main() when --as-of is given

# Strings that often mean "missing" even though the cell is not empty.
# Counted per column. "Unknown" is NOT here: it is handled as a placeholder.
NULL_TOKENS = {"NULL", "NONE", "NAN", "N/A", "NA", "#N/A", "NIL", "MISSING", "-", "?"}

SEVERITY_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}
STATUS_ORDER = {"FINDING": 0, "NOT_RUN": 1, "PASS": 2}
TYPED = {"int", "decimal", "date", "bool", "flag"}
NUMERIC = {"int", "decimal"}
TEXTUAL = {"text", "category", "code"}

INT_RE = r"^[+-]?\d+$"
DEC_RE = r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"  # allows 1e-06 (reported separately)
SCI_RE = r"[eE][+-]?\d+$"
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
DATE_FMT = "%Y-%m-%d"


# =============================================================================
# 2. TABLE SPECIFICATIONS  (edit here - the checking code is generic)
# =============================================================================
def col(dtype, *, nullable=False, allowed=None, allowed_basis="observed", multi_sep=None,
        placeholders=None, hard=(None, None), soft=(None, None), positive=False, pattern=None,
        unique=False, null_token_ok=False, no_future=False, soft_note="", note=""):
    """Describe the expectations for one column.

    dtype         int | decimal | date (YYYY-MM-DD) | bool (True/False) |
                  flag (0/1) | category | text | code (identifier kept as text)
    nullable      blank is acceptable (blank -> INFO instead of ERROR/WARN)
    allowed       list of valid values (anything else -> WARN)
    allowed_basis "observed" = list was read off this data (a clean result is only
                  descriptive -> INFO); "standard" = external standard such as
                  month names (a clean result is a real PASS)
    multi_sep     the cell holds a list, e.g. "CFP, CFA"; each item is checked
    placeholders  values that look like stand-ins for missing data (-> WARN)
    hard          (min, max) physically/logically impossible outside (-> ERROR)
    soft          (min, max) plausible range; outside is suspicious (-> WARN)
    positive      value must be > 0 (-> ERROR)
    pattern       regex the stripped value must match
    unique        non-PK column that must still be unique (-> ERROR)
    null_token_ok a string such as "None" is a real value here, not missing
    no_future     the date should not be after the audit reference date
    soft_note     why a soft-range result is WARN rather than ERROR (added to evidence)
    """
    return dict(dtype=dtype, nullable=nullable, allowed=allowed, allowed_basis=allowed_basis,
                multi_sep=multi_sep, placeholders=placeholders, hard=hard, soft=soft,
                positive=positive, pattern=pattern, unique=unique, null_token_ok=null_token_ok,
                no_future=no_future, soft_note=soft_note, note=note)


# Allowed-value lists below were taken from the values OBSERVED in the raw data
# (except MONTHS and DAYS, which are calendar standards). Because they were read
# off the data, a clean result is reported as a descriptive INFO finding, not a
# PASS. A value outside a list is flagged WARN (it may be a legitimate new
# category), never auto-classified as an error.
REGIONS = ["Northeast", "Mid-Atlantic", "Southeast", "Midwest", "West"]
RISK_PROFILES = ["Conservative", "Moderately Conservative", "Balanced",
                 "Moderately Aggressive", "Aggressive"]
CLIENT_TIERS = ["Retail", "HNW", "UHNW"]
ACCOUNT_TYPES = ["Taxable", "IRA", "Roth IRA", "Trust", "529"]
SECURITY_CLASSES = ["Equity", "Fixed Income", "Alternative", "Cash Equivalent"]
FEE_TYPES = ["Advisory", "Management", "Custodial", "Performance"]
SECURITY_TXN_TYPES = ["Buy", "Sell", "Dividend"]           # need security/shares/price
CASH_TXN_TYPES = ["Deposit", "Withdrawal", "Advisory Fee"]  # no security/shares/price
TXN_TYPES = SECURITY_TXN_TYPES + CASH_TXN_TYPES
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# 420 snapshots have aum exactly 1000.0, in runs between normal values. It may be
# a placeholder or a floor value; that is NOT confirmed, so findings stay WARN.
AUM_PLACEHOLDER = [1000.0]

TABLE_SPECS = {
    "dim_advisor": {
        "file": "dim_advisor.csv",
        "grain": "one row per advisor",
        "pk": ["advisor_id"],
        "columns": {
            "advisor_id": col("int", hard=(1, None)),
            "first_name": col("text"),
            "last_name": col("text"),
            "region": col("category", allowed=REGIONS),
            "years_experience": col("int", hard=(0, 70), soft=(0, 50)),
            "aum_target_mm": col("decimal", positive=True, soft=(1, 5000),
                                 note="AUM target in $ millions (assumed from name)"),
            "certifications": col("category", multi_sep=",", allowed=["CFA", "CFP", "CIMA", "None"],
                                  null_token_ok=True,
                                  note="'None' appears to mean 'no certification', not missing"),
        },
        "natural_keys": [],
        "near_dup_keys": [(["first_name", "last_name"], "WARN", "same advisor name")],
    },
    "dim_benchmark": {
        "file": "dim_benchmark.csv",
        "grain": "one row per benchmark",
        "pk": ["benchmark_id"],
        "columns": {
            "benchmark_id": col("int", hard=(1, None)),
            "name": col("text", unique=True),
            "asset_class": col("category"),
            "ticker_proxy": col("code", unique=True, pattern=r"^[A-Z0-9_]+$"),
        },
        "natural_keys": [],
        "near_dup_keys": [],
    },
    "dim_client": {
        "file": "dim_client.csv",
        "grain": "one row per client (current state)",
        "pk": ["client_id"],
        "columns": {
            "client_id": col("int", hard=(1, None)),
            "first_name": col("text"),
            "last_name": col("text"),
            "age": col("int", hard=(18, 120), soft=(18, 100)),
            "city": col("text"),
            "state": col("code", pattern=r"^[A-Z]{2}$"),
            "zip_code": col("code", pattern=r"^\d{5}$",
                            note="must stay text - leading zeros are significant"),
            "risk_profile": col("category", allowed=RISK_PROFILES, placeholders=["Unknown"]),
            "client_tier": col("category", allowed=CLIENT_TIERS),
            "primary_account_type": col("category", allowed=ACCOUNT_TYPES),
            "initial_aum": col("decimal", positive=True, soft=(1_000, 1_000_000_000)),
            "client_since": col("date", no_future=True, soft=("1950-01-01", None)),
            "advisor_id": col("int"),
            "is_active": col("bool"),
        },
        "natural_keys": [],
        "near_dup_keys": [
            (["first_name", "last_name", "zip_code"], "WARN", "same name and ZIP"),
            (["first_name", "last_name", "age", "state"], "WARN", "same name, age and state"),
            (["first_name", "last_name"], "INFO", "same name only (common names expected)"),
        ],
    },
    "dim_date": {
        "file": "dim_date.csv",
        "grain": "one row per calendar date",
        "pk": ["date_id"],
        "columns": {
            "date_id": col("int", hard=(1, None)),
            "date": col("date", unique=True),
            "year": col("int", hard=(1900, 2100)),
            "quarter": col("int", hard=(1, 4)),
            "month": col("int", hard=(1, 12)),
            "month_name": col("category", allowed=MONTHS, allowed_basis="standard"),
            "week_of_year": col("int", hard=(1, 53)),
            "day_of_week": col("category", allowed=DAYS, allowed_basis="standard"),
            "is_month_end": col("flag"),
            "is_quarter_end": col("flag"),
            "is_year_end": col("flag"),
            "is_trading_day": col("flag"),
        },
        "natural_keys": [],
        "near_dup_keys": [],
    },
    "dim_security": {
        "file": "dim_security.csv",
        "grain": "one row per security",
        "pk": ["security_id"],
        "columns": {
            "security_id": col("int", hard=(1, None)),
            "ticker": col("code", unique=True, pattern=r"^[A-Z]{1,5}$"),
            "name": col("text"),
            "asset_class": col("category", allowed=SECURITY_CLASSES),
            "sector": col("category", nullable=True, note="observed blank for every ETF"),
            "benchmark_id": col("int"),
            "is_etf": col("bool"),
            "expense_ratio_bps": col("decimal", nullable=True, hard=(0, 1000), soft=(0, 200),
                                     note="observed populated only for ETFs"),
        },
        "natural_keys": [],
        "near_dup_keys": [(["name"], "INFO", "same security name, different ticker")],
    },
    "fact_fees": {
        "file": "fact_fees.csv",
        "grain": "one row per client per fee month (per fee type?)",
        "pk": ["fee_id"],
        "columns": {
            "fee_id": col("int", hard=(1, None)),
            "client_id": col("int"),
            "advisor_id": col("int"),
            "fee_date": col("date", no_future=True),
            "fee_type": col("category", allowed=FEE_TYPES),
            "aum_basis": col("decimal", positive=True, placeholders=AUM_PLACEHOLDER),
            "bps_charged": col("decimal", hard=(0, 10_000), soft=(10, 100),
                               soft_note="WARN not ERROR: the 100 bps cap is an assumption; no fee "
                                         "schedule was provided",
                               note="soft max 100 bps/yr is an assumption - confirm fee schedule"),
            "amount": col("decimal", positive=True),
        },
        "natural_keys": [
            (["client_id", "fee_date", "fee_type"], "WARN", "same client, month and fee type"),
            (["client_id", "fee_date"], "INFO", "more than one fee row per client-month"),
        ],
        "near_dup_keys": [],
    },
    "fact_portfolio_snapshot": {
        "file": "fact_portfolio_snapshot.csv",
        "grain": "one row per client per month",
        "pk": ["snapshot_id"],
        "columns": {
            "snapshot_id": col("int", hard=(1, None)),
            "client_id": col("int"),
            "advisor_id": col("int"),
            "benchmark_id": col("int"),
            "snapshot_date": col("date", no_future=True),
            "year": col("int", hard=(1900, 2100)),
            "month": col("int", hard=(1, 12)),
            "aum": col("decimal", positive=True, placeholders=AUM_PLACEHOLDER),
            "return_ytd": col("decimal", hard=(-1, None), soft=(-0.5, 1.0),
                              soft_note="WARN not ERROR: implausible, but the source flags most of these "
                                        "rows (is_return_suspect; see extreme_returns_flagged) and no "
                                        "return methodology is documented",
                              note="decimal fraction (0.05 = 5%); a long-only portfolio cannot lose >100%"),
            "benchmark_return_ytd": col("decimal", hard=(-1, None), soft=(-0.5, 0.5)),
            "active_alpha_ytd": col("decimal", soft=(-0.5, 0.5),
                                    soft_note="WARN not ERROR: these are exactly the rows the source "
                                              "flags with is_return_suspect"),
            "cash_pct": col("decimal", hard=(0, 1), note="fraction of AUM held in cash"),
            "account_type": col("category", allowed=ACCOUNT_TYPES),
            "is_return_suspect": col("flag", note="source system's own suspect-return flag"),
        },
        "natural_keys": [(["client_id", "snapshot_date"], "ERROR", "one snapshot per client per month")],
        "near_dup_keys": [],
    },
    "fact_transactions": {
        "file": "fact_transactions.csv",  # delivered as 'fact_transactions (1).csv'
        "grain": "one row per transaction",
        "pk": ["txn_id"],
        "columns": {
            "txn_id": col("int", hard=(1, None)),
            "client_id": col("int"),
            "advisor_id": col("int"),
            "security_id": col("int", nullable=True, note="blank expected for cash transactions"),
            "txn_date": col("date", no_future=True),
            "txn_type": col("category", allowed=TXN_TYPES),
            "shares": col("decimal", nullable=True, positive=True),
            "price": col("decimal", nullable=True, positive=True),
            "amount": col("decimal", positive=True,
                          note="always positive - direction must come from txn_type"),
            "is_post_departure_txn": col("flag"),
        },
        "natural_keys": [],
        "near_dup_keys": [],
    },
}

# (child_table, child_column, parent_table, parent_column)
FOREIGN_KEYS = [
    ("dim_client", "advisor_id", "dim_advisor", "advisor_id"),
    ("dim_security", "benchmark_id", "dim_benchmark", "benchmark_id"),
    ("fact_fees", "client_id", "dim_client", "client_id"),
    ("fact_fees", "advisor_id", "dim_advisor", "advisor_id"),
    ("fact_fees", "fee_date", "dim_date", "date"),
    ("fact_portfolio_snapshot", "client_id", "dim_client", "client_id"),
    ("fact_portfolio_snapshot", "advisor_id", "dim_advisor", "advisor_id"),
    ("fact_portfolio_snapshot", "benchmark_id", "dim_benchmark", "benchmark_id"),
    ("fact_portfolio_snapshot", "snapshot_date", "dim_date", "date"),
    ("fact_transactions", "client_id", "dim_client", "client_id"),
    ("fact_transactions", "advisor_id", "dim_advisor", "advisor_id"),
    ("fact_transactions", "security_id", "dim_security", "security_id"),
    ("fact_transactions", "txn_date", "dim_date", "date"),
]

# First digit of a US ZIP code -> states that use it (USPS national areas).
ZIP_FIRST_DIGIT = {
    "0": {"CT", "MA", "ME", "NH", "NJ", "PR", "RI", "VT", "NY"},  # NY uses 005 too
    "1": {"DE", "NY", "PA"},
    "2": {"DC", "MD", "NC", "SC", "VA", "WV"},
    "3": {"AL", "FL", "GA", "MS", "TN"},
    "4": {"IN", "KY", "MI", "OH"},
    "5": {"IA", "MN", "MT", "ND", "SD", "WI"},
    "6": {"IL", "KS", "MO", "NE"},
    "7": {"AR", "LA", "OK", "TX"},
    "8": {"AZ", "CO", "ID", "NM", "NV", "UT", "WY"},
    "9": {"AK", "CA", "HI", "OR", "WA"},
}

# Client-tier AUM bands. INFERRED from this data (chosen so every client fits), so
# a clean result is descriptive, not a PASS. Confirm the official segmentation
# before using it as a cleaning rule.
TIER_BANDS = {"Retail": (0, 500_000), "HNW": (500_000, 5_000_000), "UHNW": (5_000_000, None)}


# =============================================================================
# 3. SMALL HELPERS
# =============================================================================
class SkipCheck(Exception):
    """Raised when a check cannot run because a table/column is missing."""


def pct(part, whole):
    return round(100.0 * float(part) / float(whole), 3) if whole else 0.0


def fmt_n(x):
    return f"{int(x):,}"


def trunc(v, width=40):
    v = str(v)
    return v if len(v) <= width else v[: width - 3] + "..."


def fmt_counts(vc, k=5, total=None):
    """'value' (count[, pct%]); ... for the top-k entries of a value_counts()."""
    parts = []
    for v, c in vc.head(k).items():
        extra = f", {pct(c, total)}%" if total else ""
        parts.append(f"'{trunc(v)}' ({int(c):,}{extra})")
    return "; ".join(parts)


def to_num(s):
    """Strict numeric parse of text: '12', '-0.5', '3.' -> float; anything else -> NaN."""
    t = s.astype(str).str.strip()
    return pd.to_numeric(t.where(t.str.match(DEC_RE)), errors="coerce")


def to_date(s):
    """Strict YYYY-MM-DD parse; anything else -> NaT."""
    t = s.astype(str).str.strip()
    return pd.to_datetime(t.where(t.str.match(DATE_RE)), format=DATE_FMT, errors="coerce")


def parse_ok(sv, dtype):
    """True where a stripped, non-blank value parses as `dtype`."""
    if dtype == "int":
        return sv.str.match(INT_RE)
    if dtype == "decimal":
        return sv.str.match(DEC_RE)
    if dtype == "date":
        return to_date(sv).notna()
    if dtype == "bool":
        return sv.isin(["True", "False"])
    if dtype == "flag":
        return sv.isin(["0", "1"])
    return pd.Series(True, index=sv.index)


def value_shape(s):
    """Letters -> A, digits -> 9 ('2020-01-05' -> '9999-99-99')."""
    return (s.str.replace(r"[A-Za-z]", "A", regex=True)
             .str.replace(r"\d", "9", regex=True))


def general_shape(s):
    """Shape with runs collapsed and sign ignored ('-12.50' -> '9.9')."""
    return (s.str.replace(r"^[+-](?=\d)", "", regex=True)
             .str.replace(r"[A-Za-z]+", "A", regex=True)
             .str.replace(r"\d+", "9", regex=True))


def is_blank(s):
    return s.astype(str).str.strip().eq("")


def sample_keys(df, mask, pk, k=5):
    if pk is None or not mask.any():
        return ""
    rows = df.loc[mask, pk].head(k)
    return "; ".join(", ".join(f"{c}={r[c]}" for c in pk) for _, r in rows.iterrows())


def range_bound(v, dtype):
    if v is None:
        return None
    return pd.Timestamp(v) if dtype == "date" else float(v)


def need(T, *names):
    for n in names:
        if n not in T:
            raise SkipCheck(f"table '{n}' not loaded")
    return [T[n] for n in names]


def need_cols(df, table, *cols):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SkipCheck(f"{table} missing column(s) {missing}")


# =============================================================================
# 4. FINDINGS LOG
# =============================================================================
class DQLog:
    COLUMNS = ["check_id", "category", "table", "column", "check", "status", "severity",
               "issue", "evidence", "affected_rows", "total_rows", "affected_pct",
               "sample_keys", "next_step"]

    def __init__(self):
        self.rows = []

    def add(self, category, table, column, check, severity, issue, evidence="",
            affected=None, total=None, sample="", next_step="", status="FINDING"):
        self.rows.append({
            "category": category, "table": table, "column": column, "check": check,
            "status": status, "severity": severity, "issue": issue, "evidence": evidence,
            "affected_rows": "" if affected is None else int(affected),
            "total_rows": "" if total is None else int(total),
            "affected_pct": "" if (affected is None or not total) else pct(affected, total),
            "sample_keys": sample, "next_step": next_step,
        })

    def result(self, category, table, column, check, affected, total, severity, issue,
               evidence="", sample="", next_step="", pass_issue=None, pass_evidence=""):
        """Log a finding if affected > 0, otherwise a PASS row (severity INFO)."""
        if affected:
            self.add(category, table, column, check, severity, issue, evidence,
                     affected, total, sample, next_step)
        else:
            self.add(category, table, column, check, "INFO",
                     pass_issue or f"Passed: {check}", pass_evidence or evidence,
                     0, total, status="PASS")

    def not_run(self, category, table, column, check, reason):
        self.add(category, table, column, check, "WARN", f"Check not run: {check}",
                 reason, status="NOT_RUN", next_step="Fix the missing input and rerun")

    def to_frame(self):
        df = pd.DataFrame(self.rows)
        if df.empty:
            return pd.DataFrame(columns=self.COLUMNS)
        df["_s"] = df["status"].map(STATUS_ORDER)
        df["_v"] = df["severity"].map(SEVERITY_ORDER)
        cat_order = {"file": 0, "column_level": 1, "single_table": 2, "referential": 3, "cross_table": 4}
        df["_c"] = df["category"].map(cat_order)
        df = df.sort_values(["_s", "_v", "_c", "table", "column"], kind="stable")
        df = df.drop(columns=["_s", "_v", "_c"]).reset_index(drop=True)
        df.insert(0, "check_id", [f"DQ{i:04d}" for i in range(1, len(df) + 1)])
        return df[self.COLUMNS]


def run_check(dq, category, table, column, check, fn, *args):
    """Run one rule; a missing input or a bug becomes a NOT_RUN row, not a crash."""
    try:
        fn(*args)
    except SkipCheck as e:
        dq.not_run(category, table, column, check, str(e))
    except Exception as e:  # keep the audit going; show the bug in the report
        dq.not_run(category, table, column, check, f"{type(e).__name__}: {e}")
        traceback.print_exc()


# =============================================================================
# 5. LOADING (read-only)
# =============================================================================
def resolve_file(table, spec, raw_dir, dq):
    exact = raw_dir / spec["file"]
    if exact.exists():
        return exact
    stem = Path(spec["file"]).stem
    candidates = sorted(raw_dir.glob(f"{stem}*.csv"))
    if len(candidates) == 1:
        dq.add("file", table, "", "file_name", "WARN",
               f"Expected file '{spec['file']}' not found; using '{candidates[0].name}'",
               "The name looks like a browser re-download copy. It was profiled as "
               f"{table}. The file was NOT renamed.",
               next_step="Confirm this is the correct/latest extract; rename only in the "
                         "cleaning step or in source control, never by this script")
        return candidates[0]
    if len(candidates) > 1:
        dq.add("file", table, "", "file_name", "ERROR",
               f"Ambiguous: several files match {stem}*.csv",
               ", ".join(p.name for p in candidates),
               next_step="Keep exactly one version in data/raw/")
        return None
    dq.add("file", table, "", "file_name", "ERROR", f"File for {table} not found",
           f"Looked for {spec['file']} and {stem}*.csv in {raw_dir}")
    return None


def load_tables(raw_dir, dq):
    tables, files = {}, {}
    used = set()
    for table, spec in TABLE_SPECS.items():
        path = resolve_file(table, spec, raw_dir, dq)
        if path is None:
            continue
        used.add(path.name)
        with open(path, "rb") as fh:
            has_bom = fh.read(3) == b"\xef\xbb\xbf"
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if has_bom:
            dq.add("file", table, "", "encoding", "INFO", "File starts with a UTF-8 BOM",
                   "Handled on read (utf-8-sig); other tools may show a stray character "
                   "in the first header")
        tables[table], files[table] = df, path.name
        print(f"  loaded {table:<26} {len(df):>9,} rows x {df.shape[1]:>2} cols  ({path.name})")
    for p in sorted(raw_dir.glob("*.csv")):
        if p.name not in used:
            dq.add("file", "", "", "unexpected_file", "INFO",
                   f"CSV in data/raw/ not covered by TABLE_SPECS: {p.name}",
                   next_step="Add a spec if it should be audited")
    return tables, files


def check_schema(table, df, spec, dq):
    expected = list(spec["columns"])
    actual = list(df.columns)
    padded = [c for c in actual if c != c.strip()]
    missing = [c for c in expected if c not in actual]
    extra = [c for c in actual if c not in expected]
    if padded:
        dq.add("column_level", table, ", ".join(padded), "header_whitespace", "ERROR",
               "Column header has leading/trailing spaces", f"{padded}",
               next_step="Strip header names in cleaning")
    if missing:
        dq.add("column_level", table, ", ".join(missing), "missing_columns", "ERROR",
               "Expected column(s) not in file", f"Expected {expected}; found {actual}")
    if extra:
        dq.add("column_level", table, ", ".join(extra), "unexpected_columns", "WARN",
               "Column(s) not described in TABLE_SPECS",
               "Profiled with no type expectations", next_step="Add them to the spec")
    if not (missing or extra or padded):
        dq.add("column_level", table, "", "schema", "INFO", "Passed: schema matches spec",
               f"{len(actual)} columns; grain: {spec.get('grain', '')}", status="PASS")


# =============================================================================
# 6. COLUMN PROFILE + COLUMN-LEVEL CHECKS
# =============================================================================
def profile_column(table, name, s, spec, key_cols, dq):
    n = len(s)
    dtype = spec["dtype"] if spec else "unspecified"
    is_key = name in key_cols
    stripped = s.str.strip()
    blank = s.eq("")
    ws_only = s.ne("") & stripped.eq("")
    empty = blank | ws_only
    nonblank = ~empty
    vals, sv = s[nonblank], stripped[nonblank]
    n_nb = len(vals)

    tok_mask = sv.str.upper().isin(NULL_TOKENS)
    lt_ws = vals.ne(sv)
    vc = vals.value_counts()
    shapes = value_shape(vals).value_counts()
    gshapes = general_shape(sv).value_counts()
    fail = ~parse_ok(sv, dtype) if dtype in TYPED else pd.Series(False, index=sv.index)

    # case / spacing variants: values that collapse to the same normalised text
    case_groups, case_example = 0, ""
    if dtype in TEXTUAL and n_nb:
        norm = sv.str.lower().str.replace(r"\s+", " ", regex=True)
        variants = vals.groupby(norm).nunique()
        variants = variants[variants > 1]
        case_groups = int(variants.size)
        if case_groups:
            k = variants.index[0]
            case_example = " vs ".join(f"'{v}'" for v in vals[norm == k].unique()[:3])

    sci = sv.str.contains(SCI_RE, regex=True) if dtype in NUMERIC else pd.Series(False, index=sv.index)
    mixed, mixed_detail, mixed_sev = False, "", "WARN"
    if dtype in TYPED and gshapes.size > 1:
        mixed, mixed_detail = True, "general shapes: " + fmt_counts(gshapes, 4, n_nb)
        mantissa = general_shape(sv.str.replace(SCI_RE, "", regex=True)).unique()
        if dtype == "decimal" and set(mantissa) <= {"9", "9.9", "9.", ".9"}:
            # same number, different spelling: integer vs decimal point and/or 1e-06 notation
            mixed_sev = "INFO"
            if sci.any():
                mixed_detail = (f"{fmt_n(sci.sum())} values in scientific notation, e.g. "
                                f"{fmt_counts(sv[sci].value_counts(), 3)} - valid numbers, but strict "
                                "loaders (e.g. SQL DECIMAL casts) may reject them; " + mixed_detail)
    elif case_groups:
        mixed, mixed_detail = True, f"{case_groups} value group(s) differ only by case/spacing, e.g. {case_example}"

    row = {
        "table": table, "column": name, "expected_type": dtype,
        "nullable": spec["nullable"] if spec else "", "is_key": is_key,
        "row_count": n,
        "null_or_blank_count": int(empty.sum()),
        "null_or_blank_pct": pct(empty.sum(), n),
        "empty_string_count": int(blank.sum()),
        "whitespace_only_count": int(ws_only.sum()),
        "null_token_count": int(tok_mask.sum()),
        "null_token_values": fmt_counts(sv[tok_mask].value_counts(), 5),
        "non_blank_count": n_nb,
        "distinct_count": int(vc.size),
        "distinct_pct_of_non_blank": pct(vc.size, n_nb),
        "is_unique_non_blank": bool(n_nb and vc.size == n_nb),
        "top_values": fmt_counts(vc, 5, n_nb),
        "dominant_shape": shapes.index[0] if len(shapes) else "",
        "dominant_shape_pct": pct(shapes.iloc[0], n_nb) if len(shapes) else 0.0,
        "shape_count": int(shapes.size),
        "top_shapes": fmt_counts(shapes, 3, n_nb),
        "parse_fail_count": int(fail.sum()),
        "parse_fail_pct_of_non_blank": pct(fail.sum(), n_nb),
        "parse_fail_examples": fmt_counts(sv[fail].value_counts(), 5),
        "leading_trailing_ws_count": int(lt_ws.sum()),
        "scientific_notation_count": int(sci.sum()),
        "mixed_format": mixed, "mixed_format_detail": mixed_detail,
        "min_length": int(vals.str.len().min()) if n_nb else "",
        "max_length": int(vals.str.len().max()) if n_nb else "",
        "numeric_min": "", "numeric_max": "", "numeric_mean": "", "numeric_median": "",
        "decimal_places_min": "", "decimal_places_max": "",
        "date_min": "", "date_max": "",
        "notes": spec["note"] if spec else "column not in spec",
    }

    x = d = None
    if dtype in NUMERIC and n_nb:
        x = to_num(sv)
        if x.notna().any():
            row.update(numeric_min=x.min(), numeric_max=x.max(),
                       numeric_mean=round(x.mean(), 6), numeric_median=x.median())
        dp = sv.str.replace(SCI_RE, "", regex=True).str.extract(r"\.(\d*)$")[0].fillna("").str.len()
        row.update(decimal_places_min=int(dp.min()), decimal_places_max=int(dp.max()))
    if dtype == "date" and n_nb:
        d = to_date(sv)
        if d.notna().any():
            row.update(date_min=d.min().date().isoformat(), date_max=d.max().date().isoformat())

    if spec:
        column_findings(table, name, s, spec, dtype, is_key, n, empty, ws_only, sv,
                        tok_mask, lt_ws, fail, mixed, mixed_detail, mixed_sev, x, d, vc, dq)
    return row


def column_findings(table, name, s, spec, dtype, is_key, n, empty, ws_only, sv,
                    tok_mask, lt_ws, fail, mixed, mixed_detail, mixed_sev, x, d, vc, dq):
    C = "column_level"
    ex = lambda m: fmt_counts(sv[m].value_counts(), 5)  # examples of offending values

    n_empty = int(empty.sum())
    if n_empty:
        if spec["nullable"]:
            dq.add(C, table, name, "blanks", "INFO", "Blank values (column is nullable)",
                   f"{fmt_n(n_empty)} blank; whether they are structural is tested by the "
                   "conditional single-table rules", n_empty, n)
        else:
            sev = "ERROR" if (is_key or dtype in TYPED) else "WARN"
            dq.add(C, table, name, "blanks", sev, "Blank values in a required column",
                   f"{fmt_n(n_empty)} blank ({fmt_n(ws_only.sum())} whitespace-only)",
                   n_empty, n, next_step="Find the source of the gaps before imputing anything")

    if tok_mask.any():
        sev = "INFO" if spec["null_token_ok"] else "WARN"
        msg = ("Text that looks like a null token is a real value here"
               if spec["null_token_ok"] else "Text null tokens (disguised missing values)")
        dq.add(C, table, name, "null_tokens", sev, msg, ex(tok_mask), int(tok_mask.sum()), n,
               next_step="Make sure downstream tools do not read these as NULL"
               if spec["null_token_ok"] else "Treat as missing in cleaning")

    if fail.any():
        dq.add(C, table, name, "type_parse", "ERROR",
               f"Values do not parse as {dtype}",
               f"{fmt_n(fail.sum())} of {fmt_n(len(sv))} non-blank values ({pct(fail.sum(), len(sv))}%); "
               f"examples: {ex(fail)}", int(fail.sum()), n,
               next_step="These break casts, sums and joins")

    if lt_ws.any():
        dq.add(C, table, name, "whitespace", "ERROR" if is_key else "WARN",
               "Leading/trailing whitespace", ex(lt_ws), int(lt_ws.sum()), n,
               next_step="Strip in cleaning" + (" - key column, joins will miss" if is_key else ""))

    if mixed:
        dq.add(C, table, name, "mixed_format", mixed_sev, "Inconsistent value formats",
               mixed_detail, next_step="Standardise to one format")

    allowed_ph = set(spec["placeholders"] or []) if dtype not in NUMERIC else set()
    if spec["allowed"]:
        ok_set = set(spec["allowed"]) | allowed_ph
        if spec["multi_sep"]:
            items = sv.str.split(spec["multi_sep"]).explode().str.strip()
            bad_items = items[~items.isin(ok_set)]
            bad = sv.index.isin(bad_items.index)
            bad = pd.Series(bad, index=sv.index)
        else:
            bad = ~sv.isin(ok_set)
        if bad.any():
            dq.add(C, table, name, "allowed_values", "WARN",
                   "Values outside the expected list",
                   f"{ex(bad)}; expected one of {sorted(spec['allowed'])}",
                   int(bad.sum()), n, next_step="Confirm whether these are new valid categories")
        elif spec["allowed_basis"] == "standard":
            dq.add(C, table, name, "allowed_values", "INFO",
                   "Passed: every value is in the standard list",
                   f"{sorted(spec['allowed'])}", 0, n, status="PASS")
        else:
            dq.add(C, table, name, "observed_value_set", "INFO",
                   "Observed category set (descriptive, not an independent validation)",
                   f"{fmt_counts(vc, 8, len(sv))}. The expected list was taken from this data, so "
                   "a clean result cannot fail by construction - confirm the official category list",
                   next_step="Confirm the valid categories with the data owner")

    if spec["placeholders"]:
        if dtype in NUMERIC and x is not None:
            ph = x.isin([float(p) for p in spec["placeholders"]])
        else:
            ph = sv.isin(spec["placeholders"])
        if ph.any():
            dq.add(C, table, name, "placeholder", "WARN", "Placeholder-looking values",
                   f"{ex(ph)} - looks like a stand-in, but there is no independent evidence that it "
                   "is wrong (kept WARN, not ERROR)",
                   int(ph.sum()), n, next_step="Confirm with source before treating as missing")

    if spec["pattern"]:
        bad = ~sv.str.match(spec["pattern"])
        if bad.any():
            dq.add(C, table, name, "pattern", "ERROR" if is_key else "WARN",
                   f"Values do not match pattern {spec['pattern']}", ex(bad), int(bad.sum()), n)

    if name in ("zip_code",) and len(sv):
        lead0 = sv.str.startswith("0")
        if lead0.any():
            dq.add(C, table, name, "leading_zeros", "INFO",
                   "Codes with leading zeros", f"{fmt_n(lead0.sum())} values start with 0 - "
                   "reading this column as a number would corrupt them",
                   int(lead0.sum()), n, next_step="Keep as text in every downstream step")

    # numeric / date ranges
    vals_for_range = x if dtype in NUMERIC else (d if dtype == "date" else None)
    if vals_for_range is not None:
        v = vals_for_range
        hmin, hmax = (range_bound(b, dtype) for b in spec["hard"])
        smin, smax = (range_bound(b, dtype) for b in spec["soft"])
        hard_bad = pd.Series(False, index=v.index)
        if hmin is not None:
            hard_bad |= v < hmin
        if hmax is not None:
            hard_bad |= v > hmax
        if spec["positive"]:
            hard_bad |= v <= 0
        if hard_bad.any():
            dq.add(C, table, name, "hard_range", "ERROR", "Impossible values",
                   f"allowed {spec['hard']}{' and > 0' if spec['positive'] else ''}; "
                   f"observed min {v[hard_bad].min()}, max {v[hard_bad].max()}; e.g. {ex(hard_bad)}",
                   int(hard_bad.sum()), n)
        soft_bad = pd.Series(False, index=v.index)
        if smin is not None:
            soft_bad |= v < smin
        if smax is not None:
            soft_bad |= v > smax
        soft_bad &= ~hard_bad
        if soft_bad.any():
            dq.add(C, table, name, "soft_range", "WARN", "Outside the plausible range",
                   f"plausible range {spec['soft']} (assumption); {fmt_n(soft_bad.sum())} rows, "
                   f"observed {v[soft_bad].min()} to {v[soft_bad].max()}; e.g. {ex(soft_bad)}"
                   + (f". {spec['soft_note']}" if spec["soft_note"] else ""),
                   int(soft_bad.sum()), n, next_step="Investigate before treating as outliers")
        if dtype == "date" and spec["no_future"]:
            fut = v > REF_DATE
            dq.result(C, table, name, "future_date", int(fut.sum()), n, "WARN",
                      f"Dates after the audit reference date {REF_DATE.date()}", ex(fut) if fut.any() else "",
                      pass_issue=f"Passed: no dates after the audit reference date {REF_DATE.date()}")

    if spec["unique"]:
        dup = sv.duplicated(keep=False)
        if dup.any():
            dq.add(C, table, name, "unique", "ERROR", "Column should be unique but has duplicates",
                   ex(dup), int(dup.sum()), n, next_step="Lookups on this column will fan out")

    if len(vc) == 1 and len(sv) > 1:
        dq.add(C, table, name, "constant", "INFO", "Column has a single distinct value",
               fmt_counts(vc, 1))


def check_primary_key(table, df, pk, dq):
    C = "column_level"
    missing = [c for c in pk if c not in df.columns]
    if missing:
        dq.not_run(C, table, ",".join(pk), "primary_key", f"PK column(s) missing: {missing}")
        return
    n = len(df)
    blanks = df[pk].apply(is_blank).any(axis=1)
    dup = df.duplicated(subset=pk, keep=False) & ~blanks
    dq.result(C, table, ",".join(pk), "primary_key_not_blank", int(blanks.sum()), n, "ERROR",
              "Primary key is blank", f"{fmt_n(blanks.sum())} rows",
              pass_issue="Passed: primary key never blank")
    dq.result(C, table, ",".join(pk), "primary_key_unique", int(dup.sum()), n, "ERROR",
              "Duplicate primary key values",
              f"{fmt_n(df.loc[dup, pk].drop_duplicates().shape[0])} key values repeat",
              sample_keys(df, dup, pk), next_step="Joins on this key will duplicate rows",
              pass_issue="Passed: primary key unique",
              pass_evidence=f"{fmt_n(n)} rows, {fmt_n(n)} distinct keys")
    if len(pk) == 1:
        ids = to_num(df[pk[0]]).dropna()
        if len(ids) and (ids == ids.round()).all():
            span = int(ids.max() - ids.min() + 1)
            gaps = span - ids.nunique()
            if gaps > 0:
                dq.add(C, table, pk[0], "key_gaps", "INFO", "Surrogate key sequence has gaps",
                       f"{fmt_n(gaps)} missing ids between {int(ids.min())} and {int(ids.max())} - "
                       "may indicate deleted or filtered rows", gaps, span)


# =============================================================================
# 7. SINGLE-TABLE CHECKS (generic)
# =============================================================================
def normalise(df):
    return df.apply(lambda c: c.str.strip().str.lower().str.replace(r"\s+", " ", regex=True))


def single_table_generic(table, df, spec, dq):
    S = "single_table"
    n, pk = len(df), [c for c in spec["pk"] if c in df.columns]
    exact = df.duplicated(keep=False)
    dq.result(S, table, "(all columns)", "exact_duplicate_rows", int(exact.sum()), n, "ERROR",
              "Exact duplicate rows", sample=sample_keys(df, exact, pk),
              pass_issue="Passed: no exact duplicate rows")
    non_pk = [c for c in df.columns if c not in pk]
    if pk and non_pk:
        same = df.duplicated(subset=non_pk, keep=False) & ~exact
        dq.result(S, table, "(all non-key columns)", "duplicate_except_key", int(same.sum()), n,
                  "WARN", "Rows identical except for the surrogate key (possible double load)",
                  sample=sample_keys(df, same, pk),
                  pass_issue="Passed: no rows identical apart from the key")
        nd = normalise(df[non_pk]).duplicated(keep=False) & ~df.duplicated(subset=non_pk, keep=False)
        dq.result(S, table, "(all non-key columns)", "near_duplicate_rows", int(nd.sum()), n,
                  "WARN", "Near-duplicates: rows identical after trimming/lower-casing",
                  sample=sample_keys(df, nd, pk),
                  pass_issue="Passed: no case/whitespace near-duplicates")
    for cols, sev, label in spec.get("natural_keys", []):
        if not set(cols) <= set(df.columns):
            dq.not_run(S, table, ",".join(cols), "natural_key", "column(s) missing")
            continue
        dup = df.duplicated(subset=cols, keep=False)
        dq.result(S, table, ",".join(cols), "natural_key_unique", int(dup.sum()), n, sev,
                  f"Duplicate business key ({label})",
                  f"{fmt_n(df.loc[dup, cols].drop_duplicates().shape[0])} key combinations repeat",
                  sample_keys(df, dup, pk), pass_issue=f"Passed: business key unique ({label})")
    for cols, sev, label in spec.get("near_dup_keys", []):
        if not set(cols) <= set(df.columns):
            continue
        dup = normalise(df[cols]).duplicated(keep=False)
        if dup.any():
            dq.add(S, table, ",".join(cols), "possible_duplicate_entity", sev,
                   f"Possible duplicate entities ({label})",
                   f"{fmt_n(df.loc[dup, cols].drop_duplicates().shape[0])} groups; "
                   "may be different real entities - review before merging",
                   int(dup.sum()), n, sample_keys(df, dup, pk))


# =============================================================================
# 8. SINGLE-TABLE CHECKS (within-row rules per table)
# =============================================================================
def full_mask(df, partial):
    return partial.reindex(df.index, fill_value=False).astype(bool)


def rules_dim_date(df, dq):
    t, S, n, pk = "dim_date", "single_table", len(df), ["date_id"]
    need_cols(df, t, "date")
    d = to_date(df["date"])
    ok = d.notna()
    sub = d[ok]
    derived = {
        "year": sub.dt.year.astype(str), "quarter": sub.dt.quarter.astype(str),
        "month": sub.dt.month.astype(str), "month_name": sub.dt.month_name(),
        "day_of_week": sub.dt.day_name(),
        "is_month_end": sub.dt.is_month_end.astype(int).astype(str),
        "is_quarter_end": sub.dt.is_quarter_end.astype(int).astype(str),
        "is_year_end": sub.dt.is_year_end.astype(int).astype(str),
    }
    for c, expected in derived.items():
        if c not in df.columns:
            continue
        bad = full_mask(df, df.loc[ok, c].str.strip() != expected)
        dq.result(S, t, c, f"{c}_matches_date", int(bad.sum()), n, "ERROR",
                  f"{c} contradicts the date column", sample=sample_keys(df, bad, pk),
                  pass_issue=f"Passed: {c} consistent with date")
    if "week_of_year" in df.columns:
        iso = sub.dt.isocalendar().week.astype(int).astype(str)
        bad = full_mask(df, df.loc[ok, "week_of_year"].str.strip() != iso)
        dq.result(S, t, "week_of_year", "week_matches_iso", int(bad.sum()), n, "WARN",
                  "week_of_year differs from ISO week (definition may differ - confirm)",
                  sample=sample_keys(df, bad, pk), pass_issue="Passed: week_of_year = ISO week")
    if "is_trading_day" in df.columns:
        flag = df["is_trading_day"].str.strip()
        weekend = full_mask(df, sub.dt.day_of_week >= 5)
        bad = weekend & (flag == "1")
        dq.result(S, t, "is_trading_day", "weekend_not_trading", int(bad.sum()), n, "ERROR",
                  "Weekend dates marked as trading days", sample=sample_keys(df, bad, pk),
                  pass_issue="Passed: no weekend marked as trading day")
        md = sub.dt.strftime("%m-%d")
        fixed_hol = full_mask(df, md.isin(["01-01", "07-04", "12-25"]) & (sub.dt.day_of_week < 5))
        hol_trading = fixed_hol & (flag == "1")
        weekday_closed = (~weekend) & ok & (flag == "0")
        dq.result(S, t, "is_trading_day", "holidays_not_trading", int(hol_trading.sum()), n, "WARN",
                  "Market holidays marked as trading days",
                  f"{fmt_n(hol_trading.sum())} weekday New Year's Day / July 4 / Christmas dates have "
                  f"is_trading_day=1; only {fmt_n(weekday_closed.sum())} weekdays in the whole calendar "
                  "are non-trading, so the flag looks like 'weekday', not the NYSE calendar",
                  sample_keys(df, hol_trading, pk),
                  next_step="Decide whether trading-day logic matters for any metric")
    srt = sub.sort_values()
    gaps = int((srt.diff().dt.days > 1).sum())
    dq.result(S, t, "date", "calendar_contiguous", gaps, n, "WARN", "Calendar has missing dates",
              f"{gaps} gaps", pass_issue="Passed: calendar is contiguous",
              pass_evidence=f"{srt.min().date()} to {srt.max().date()}, {fmt_n(len(srt))} days")
    if "date_id" in df.columns:
        ids = to_num(df["date_id"])
        order_ok = (ids[ok].rank().values == d[ok].rank().values).all()
        if not order_ok:
            dq.add(S, t, "date_id", "date_id_order", "INFO", "date_id is not in date order")


def rules_dim_security(df, dq):
    t, S, n, pk = "dim_security", "single_table", len(df), ["security_id"]
    need_cols(df, t, "is_etf", "sector", "expense_ratio_bps")
    etf = df["is_etf"].str.strip() == "True"
    no_etf = df["is_etf"].str.strip() == "False"
    exp_blank, sec_blank = is_blank(df["expense_ratio_bps"]), is_blank(df["sector"])
    for mask, sev, issue in [
        (etf & exp_blank, "WARN", "ETF with no expense ratio"),
        (no_etf & ~exp_blank, "WARN", "Non-ETF with an expense ratio"),
        (no_etf & sec_blank, "WARN", "Non-ETF security with blank sector"),
        (etf & ~sec_blank, "INFO", "ETF with a sector populated"),
    ]:
        dq.result(S, t, "is_etf / sector / expense_ratio_bps", issue.lower().replace(" ", "_"),
                  int(mask.sum()), n, sev, issue, sample=sample_keys(df, mask, pk),
                  pass_issue=f"Passed: no '{issue}'")
    if (etf == sec_blank).all() and (etf == ~exp_blank).all():
        dq.add(S, t, "sector, expense_ratio_bps", "structural_blanks", "INFO",
               "Blanks are structural, not missing data",
               f"sector is blank for exactly the {fmt_n(etf.sum())} ETFs; expense_ratio_bps is blank "
               f"for exactly the {fmt_n(no_etf.sum())} non-ETFs",
               next_step="Do not impute; use a label such as 'Fund (no sector)' if needed")


def rules_dim_client(df, dq):
    t, S, n, pk = "dim_client", "single_table", len(df), ["client_id"]
    need_cols(df, t, "client_tier", "initial_aum")
    aum, tier = to_num(df["initial_aum"]), df["client_tier"].str.strip()
    bad = pd.Series(False, index=df.index)
    for tr, (lo, hi) in TIER_BANDS.items():
        m = tier == tr
        out = aum < lo
        if hi is not None:
            out |= aum >= hi
        bad |= m & out
    bands = "; ".join(f"{tr}: {aum[tier == tr].min():,.0f}-{aum[tier == tr].max():,.0f}" for tr in TIER_BANDS)
    if bad.any():
        dq.add(S, t, "client_tier, initial_aum", "tier_matches_aum_band", "WARN",
               "client_tier inconsistent with the inferred initial_aum bands",
               f"inferred bands {TIER_BANDS}; observed {bands}", int(bad.sum()), n, sample_keys(df, bad, pk))
    else:
        dq.add(S, t, "client_tier, initial_aum", "tier_aum_bands_observed", "INFO",
               "Observed: initial_aum ranges do not overlap between tiers (descriptive, not a verified rule)",
               f"Observed {bands}. Bands {TIER_BANDS} were INFERRED from this data, so every client fits "
               "by construction",
               next_step="Confirm the official tier segmentation before using it to clean or derive tiers")
    if {"zip_code", "state"} <= set(df.columns):
        z, st = df["zip_code"].str.strip(), df["state"].str.strip()
        valid = z.str.match(r"^\d{5}$")
        known = st.isin(set().union(*ZIP_FIRST_DIGIT.values()))
        expected = z.str[0].map(ZIP_FIRST_DIGIT)
        mism = valid & known & pd.Series([s not in e if isinstance(e, set) else False
                                          for s, e in zip(st, expected)], index=df.index)
        dq.result(S, t, "zip_code, state", "zip_matches_state", int(mism.sum()), n, "WARN",
                  "ZIP code's first digit does not belong to the state",
                  f"{fmt_n(mism.sum())} of {fmt_n(valid.sum())} clients ({pct(mism.sum(), valid.sum())}%). "
                  "City names also look synthetic - addresses may be generated test data",
                  sample_keys(df, mism, pk),
                  next_step="Do not use ZIP/city for geographic analysis without a reference check")


def rules_fact_portfolio_snapshot(df, dq):
    t, S, n, pk = "fact_portfolio_snapshot", "single_table", len(df), ["snapshot_id"]
    need_cols(df, t, "snapshot_date", "client_id")
    d = to_date(df["snapshot_date"])
    for c, exp in [("year", d.dt.year), ("month", d.dt.month)]:
        if c in df.columns:
            bad = d.notna() & (to_num(df[c]) != exp)
            dq.result(S, t, c, f"{c}_matches_snapshot_date", int(bad.sum()), n, "ERROR",
                      f"{c} contradicts snapshot_date", sample=sample_keys(df, bad, pk),
                      pass_issue=f"Passed: {c} consistent with snapshot_date")
    not_first = d.notna() & (d.dt.day != 1)
    dq.result(S, t, "snapshot_date", "snapshot_on_first_of_month", int(not_first.sum()), n, "WARN",
              "Snapshot not dated the 1st (grain appears to be month-start)",
              sample=sample_keys(df, not_first, pk),
              pass_issue="Passed: all snapshots dated the 1st of the month",
              pass_evidence="Note: whether the 1st means start-of-month or prior month-end is not documented")

    need_cols(df, t, "return_ytd", "benchmark_return_ytd", "active_alpha_ytd", "aum", "is_return_suspect")
    r, b, a = to_num(df["return_ytd"]), to_num(df["benchmark_return_ytd"]), to_num(df["active_alpha_ytd"])
    diff = (r - b - a).abs()
    bad = diff > 1e-5
    dq.result(S, t, "active_alpha_ytd", "alpha_equals_return_minus_benchmark", int(bad.sum()), n, "ERROR",
              "active_alpha_ytd != return_ytd - benchmark_return_ytd",
              f"max abs difference {diff.max():.2e}", sample_keys(df, bad, pk),
              pass_issue="Passed: active_alpha_ytd = return_ytd - benchmark_return_ytd",
              pass_evidence=f"max rounding difference {diff.max():.1e}")

    flag = df["is_return_suspect"].str.strip()
    aum = to_num(df["aum"])
    flagged = flag == "1"
    alpha_rule = a.abs() > 0.5
    mism = flagged != alpha_rule
    n_clients = df.loc[flagged, "client_id"].nunique()
    if mism.any():
        dq.add(S, t, "is_return_suspect", "suspect_flag_vs_alpha", "WARN",
               "is_return_suspect does not consistently match |active_alpha_ytd| > 0.5",
               f"{fmt_n(flagged.sum())} flagged rows; {fmt_n((flagged & ~alpha_rule).sum())} flagged with "
               f"|alpha| <= 0.5, {fmt_n((~flagged & alpha_rule).sum())} unflagged with |alpha| > 0.5",
               int(mism.sum()), n, sample_keys(df, mism, pk))
    else:
        dq.add(S, t, "is_return_suspect", "suspect_flag_vs_alpha", "INFO",
               "Observed: is_return_suspect = 1 exactly when |active_alpha_ytd| > 0.5",
               f"{fmt_n(flagged.sum())} flagged rows across {fmt_n(n_clients)} clients; the flag and the "
               "|alpha| > 0.5 test agree on every row. The flag is a return/alpha marker only - it says "
               "nothing about AUM values. The 0.5 threshold is observed, not documented",
               int(flagged.sum()), n,
               next_step="Use the flag for alpha outliers only; review AUM values separately")
    smin, smax = TABLE_SPECS[t]["columns"]["return_ytd"]["soft"]
    extreme = (r < smin) | (r > smax)
    unflagged = extreme & ~flagged
    dq.result(S, t, "is_return_suspect", "extreme_returns_flagged", int(unflagged.sum()), int(extreme.sum()),
              "WARN", f"return_ytd outside {smin}..{smax} but NOT flagged by is_return_suspect",
              f"{fmt_n(unflagged.sum())} of {fmt_n(extreme.sum())} extreme-return rows are unflagged because "
              "their alpha is within +/-0.5 (the benchmark moved too). Expected under the alpha rule, but "
              "the flag cannot be used as an extreme-return filter", sample_keys(df, unflagged, pk),
              next_step="Filter extreme returns on return_ytd itself, not on the flag",
              pass_issue="Passed: every extreme return is flagged")

    s = df.assign(_d=d, _aum=aum).sort_values(["client_id", "_d"])
    prev = s.groupby("client_id")["_aum"].shift()
    ratio = s["_aum"] / prev
    jump_s = (ratio > 3) | (ratio < 1 / 3)
    ph_cur, ph_prev = s["_aum"].isin(AUM_PLACEHOLDER), prev.isin(AUM_PLACEHOLDER)
    into_ph = jump_s & ph_cur & ~ph_prev
    out_ph = jump_s & ph_prev & ~ph_cur
    other = jump_s & ~ph_cur & ~ph_prev
    jump = full_mask(df, jump_s)
    dq.result(S, t, "aum", "aum_month_over_month_jump", int(jump.sum()), n, "WARN",
              "AUM changes by more than 3x (or less than 1/3) month over month",
              f"{fmt_n(into_ph.sum())} collapses INTO aum = {AUM_PLACEHOLDER[0]:,.0f} (median prior AUM "
              f"${prev[into_ph].median():,.0f}); {fmt_n(out_ph.sum())} recoveries out of it; "
              f"{fmt_n(other.sum())} other jumps. Large deposits/withdrawals could make some legitimate",
              sample_keys(df, jump, pk), next_step="Compare against deposits/withdrawals in fact_transactions")

    per = s.dropna(subset=["_d"]).groupby("client_id")["_d"].agg(["min", "max", "count"])
    expected_months = ((per["max"].dt.year - per["min"].dt.year) * 12
                       + per["max"].dt.month - per["min"].dt.month + 1)
    missing = (expected_months - per["count"]).clip(lower=0)
    dq.result(S, t, "snapshot_date", "no_month_gaps_per_client", int(missing.sum()), int(expected_months.sum()),
              "WARN", "Missing snapshot months between a client's first and last snapshot",
              f"{fmt_n((missing > 0).sum())} clients have gaps",
              pass_issue="Passed: every client has a continuous monthly series")

    if "benchmark_id" in df.columns:
        k = df.groupby(["benchmark_id", "snapshot_date"])["benchmark_return_ytd"].nunique()
        bad_k = int((k > 1).sum())
        dq.result(S, t, "benchmark_return_ytd", "one_benchmark_return_per_month", bad_k, len(k), "ERROR",
                  "Different benchmark returns for the same benchmark and month",
                  pass_issue="Passed: one benchmark return per benchmark-month")


def rules_fact_fees(df, dq):
    t, S, n, pk = "fact_fees", "single_table", len(df), ["fee_id"]
    need_cols(df, t, "aum_basis", "bps_charged", "amount", "fee_date")
    expected = to_num(df["aum_basis"]) * to_num(df["bps_charged"]) / 10_000 / 12
    diff = (expected - to_num(df["amount"])).abs()
    bad = diff > 0.011
    dq.result(S, t, "amount", "amount_equals_aum_x_bps_div_12", int(bad.sum()), n, "WARN",
              "amount != aum_basis x bps_charged / 10,000 / 12",
              f"max difference ${diff.max():,.2f}", sample_keys(df, bad, pk),
              pass_issue="Passed (INFERRED formula): amount = aum_basis x bps / 10,000 / 12",
              pass_evidence=f"Formula is INFERRED from the data (max rounding diff ${diff.max():.3f}); "
                            "it implies bps_charged is an ANNUAL rate charged monthly")
    d = to_date(df["fee_date"])
    nf = d.notna() & (d.dt.day != 1)
    dq.result(S, t, "fee_date", "fee_on_first_of_month", int(nf.sum()), n, "INFO",
              "Fee not dated the 1st of the month", pass_issue="Passed: all fees dated the 1st")


def rules_fact_transactions(df, dq):
    t, S, n, pk = "fact_transactions", "single_table", len(df), ["txn_id"]
    need_cols(df, t, "txn_type", "security_id", "shares", "price", "amount")
    typ = df["txn_type"].str.strip()
    sec_cols = ["security_id", "shares", "price"]
    any_blank = df[sec_cols].apply(is_blank).any(axis=1)
    any_filled = (~df[sec_cols].apply(is_blank)).any(axis=1)
    m1 = typ.isin(SECURITY_TXN_TYPES) & any_blank
    m2 = typ.isin(CASH_TXN_TYPES) & any_filled
    dq.result(S, t, "security_id, shares, price", "security_txn_has_security", int(m1.sum()), n, "ERROR",
              f"{'/'.join(SECURITY_TXN_TYPES)} missing security_id, shares or price",
              sample=sample_keys(df, m1, pk),
              pass_issue="Passed: every Buy/Sell/Dividend has security, shares and price")
    dq.result(S, t, "security_id, shares, price", "cash_txn_has_no_security", int(m2.sum()), n, "WARN",
              f"{'/'.join(CASH_TXN_TYPES)} carry security fields",
              sample=sample_keys(df, m2, pk),
              pass_issue="Passed: cash transactions have no security fields",
              pass_evidence=f"So the {fmt_n((typ.isin(CASH_TXN_TYPES)).sum())} blanks in security_id/shares/"
                            "price are structural, not missing")
    sp = to_num(df["shares"]) * to_num(df["price"])
    amt = to_num(df["amount"])
    tol = 0.05
    bs = typ.isin(["Buy", "Sell"])
    bad = bs & ((sp - amt).abs() > tol)
    dq.result(S, t, "amount", "trade_amount_equals_shares_x_price", int(bad.sum()), int(bs.sum()), "WARN",
              "Buy/Sell amount != shares x price", f"tolerance ${tol}", sample_keys(df, bad, pk),
              pass_issue="Passed: Buy/Sell amount = shares x price")
    dv = typ == "Dividend"
    dv_full = dv & ((sp - amt).abs() <= tol)
    if dv_full.any():
        dq.add(S, t, "amount", "dividend_equals_position_value", "WARN",
               "Dividend amount equals shares x price (the full position value)",
               f"{fmt_n(dv_full.sum())} of {fmt_n(dv.sum())} dividends; median dividend "
               f"${amt[dv].median():,.0f} vs median Buy ${amt[typ == 'Buy'].median():,.0f}. A dividend is "
               "normally a small % of position value, so 'price' may be a per-share value not a payout. "
               "WARN here because the row alone cannot prove it; see the cross-table dividend-vs-AUM check",
               int(dv_full.sum()), int(dv.sum()), sample_keys(df, dv_full, pk),
               next_step="Confirm dividend semantics before using amount in income/return metrics")
    if amt.notna().all() and (amt > 0).all():
        dq.add(S, t, "amount", "sign_convention", "INFO",
               "All amounts are positive; direction (in/out) is implied by txn_type",
               fmt_counts(typ.value_counts(), 6, n),
               next_step="Apply a sign by txn_type before summing net flows")


ROW_RULES = {
    "dim_date": rules_dim_date,
    "dim_security": rules_dim_security,
    "dim_client": rules_dim_client,
    "fact_portfolio_snapshot": rules_fact_portfolio_snapshot,
    "fact_fees": rules_fact_fees,
    "fact_transactions": rules_fact_transactions,
}


# =============================================================================
# 9. REFERENTIAL INTEGRITY
# =============================================================================
def check_fk(T, dq, child, ccol, parent, pcol):
    R = "referential"
    cdf, pdf_ = need(T, child, parent)
    need_cols(cdf, child, ccol)
    need_cols(pdf_, parent, pcol)
    c = cdf[ccol]
    nonblank = ~is_blank(c)
    parent_vals = set(pdf_[pcol])
    orphan = nonblank & ~c.isin(parent_vals)
    ev = ""
    if orphan.any():
        norm_parent = {v.strip().lstrip("0") for v in parent_vals}
        rescued = c[orphan].str.strip().str.lstrip("0").isin(norm_parent).sum()
        ev = (f"{fmt_n(c[orphan].nunique())} distinct orphan values, e.g. "
              f"{fmt_counts(c[orphan].value_counts(), 5)}; {fmt_n(rescued)} would match after "
              "trimming spaces/leading zeros")
    blank_note = f"{fmt_n((~nonblank).sum())} blank FK values excluded" if (~nonblank).any() else ""
    pk = TABLE_SPECS[child]["pk"]
    dq.result(R, child, ccol, f"fk_{ccol}_to_{parent}.{pcol}", int(orphan.sum()), len(c), "ERROR",
              f"{child}.{ccol} values with no match in {parent}.{pcol}", "; ".join(x for x in [ev, blank_note] if x),
              sample_keys(cdf, orphan, pk), next_step="Rows will drop out of inner joins",
              pass_issue=f"Passed: every {child}.{ccol} exists in {parent}.{pcol}",
              pass_evidence=blank_note)
    if pdf_[pcol].duplicated().any():
        dq.add(R, parent, pcol, f"parent_key_unique_{pcol}", "ERROR",
               f"Join key {parent}.{pcol} is not unique - joins from {child} will fan out")


def unused_dimension_members(T, dq):
    parents = {}
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        if child in T and parent in T and ccol in T[child].columns:
            parents.setdefault((parent, pcol), set()).update(T[child][ccol])
    for (parent, pcol), used in parents.items():
        if pcol not in T[parent].columns:
            continue
        unused = ~T[parent][pcol].isin(used)
        if unused.any():
            vals = T[parent].loc[unused, pcol]
            dq.add("referential", parent, pcol, "unused_dimension_members", "INFO",
                   "Dimension members never referenced by any table",
                   f"{fmt_n(unused.sum())} of {fmt_n(len(unused))}, e.g. {', '.join(vals.head(8))}",
                   int(unused.sum()), len(unused))


# =============================================================================
# 10. CROSS-TABLE BUSINESS RULES
# =============================================================================
def x_advisor_consistency(T, dq):
    (cl,) = need(T, "dim_client")
    for fact in ["fact_fees", "fact_portfolio_snapshot", "fact_transactions"]:
        if fact not in T:
            continue
        f = T[fact]
        need_cols(f, fact, "client_id", "advisor_id")
        m = f[["client_id", "advisor_id"]].merge(cl[["client_id", "advisor_id"]], on="client_id",
                                                 how="left", suffixes=("", "_dim"))
        bad = pd.Series(((m["advisor_id"] != m["advisor_id_dim"]) & m["advisor_id_dim"].notna()).values,
                        index=f.index)
        dq.result("cross_table", fact, "advisor_id", "advisor_matches_dim_client", int(bad.sum()), len(f),
                  "WARN", f"{fact}.advisor_id differs from the client's advisor in dim_client",
                  "could be advisor reassignment history (dim_client holds current state only)",
                  sample_keys(f, bad, TABLE_SPECS[fact]["pk"]),
                  pass_issue=f"Consistent: {fact}.advisor_id always equals dim_client.advisor_id",
                  pass_evidence="OBSERVATION, not a documented rule: no client changes advisor in this "
                                "extract. dim_client holds current state only, so past reassignments "
                                "cannot be ruled out")


def x_account_type(T, dq):
    snap, cl = need(T, "fact_portfolio_snapshot", "dim_client")
    need_cols(snap, "fact_portfolio_snapshot", "account_type")
    m = snap[["client_id", "account_type"]].merge(cl[["client_id", "primary_account_type"]],
                                                   on="client_id", how="left")
    bad = pd.Series((m["account_type"] != m["primary_account_type"]).values, index=snap.index)
    dq.result("cross_table", "fact_portfolio_snapshot", "account_type", "account_type_matches_client",
              int(bad.sum()), len(snap), "WARN",
              "Snapshot account_type differs from dim_client.primary_account_type",
              sample=sample_keys(snap, bad, ["snapshot_id"]),
              pass_issue="Consistent: snapshot account_type always equals the client's primary_account_type",
              pass_evidence="OBSERVATION, not a documented rule: every client shows one account type, "
                            "matching dim_client. Whether clients can hold several accounts is unknown")


def x_risk_benchmark(T, dq):
    snap, cl, bm = need(T, "fact_portfolio_snapshot", "dim_client", "dim_benchmark")
    m = snap[["client_id", "benchmark_id"]].merge(cl[["client_id", "risk_profile"]], on="client_id", how="left")
    names = dict(zip(bm["benchmark_id"], bm["name"]))
    mapping = m.groupby("risk_profile")["benchmark_id"].agg(lambda s: sorted(s.unique()))
    def label(b):
        return f"{b} ({names.get(b, '?')})"
    txt = "; ".join(rp + " -> " + ", ".join(label(b) for b in bs) for rp, bs in mapping.items())
    multi = mapping[mapping.apply(len) > 1]
    dq.result("cross_table", "fact_portfolio_snapshot", "benchmark_id", "one_benchmark_per_risk_profile",
              int(m["risk_profile"].isin(multi.index).sum()), len(m), "WARN",
              "A risk profile is benchmarked against more than one benchmark", txt,
              pass_issue="Passed: each risk profile maps to exactly one benchmark",
              pass_evidence=txt)
    unk = m["risk_profile"].isin(TABLE_SPECS["dim_client"]["columns"]["risk_profile"]["placeholders"] or [])
    if unk.any():
        dq.add("cross_table", "fact_portfolio_snapshot", "benchmark_id", "unknown_risk_profile_benchmark",
               "WARN", "Clients with risk_profile 'Unknown' still receive a benchmark",
               f"{fmt_n(m.loc[unk, 'client_id'].nunique())} clients / {fmt_n(unk.sum())} snapshots use "
               f"benchmark(s) {sorted(m.loc[unk, 'benchmark_id'].unique())} - looks like a default assignment",
               int(unk.sum()), len(m), next_step="Confirm the risk profile before reporting alpha for these clients")
    var = snap.groupby("client_id")["benchmark_id"].nunique()
    dq.result("cross_table", "fact_portfolio_snapshot", "benchmark_id", "benchmark_stable_per_client",
              int((var > 1).sum()), len(var), "INFO", "Client's benchmark changes over time",
              pass_issue="Passed: each client keeps one benchmark")


def x_security_benchmark(T, dq):
    sec, bm = need(T, "dim_security", "dim_benchmark")
    m = sec.merge(bm[["benchmark_id", "asset_class"]], on="benchmark_id", how="left", suffixes=("", "_bm"))
    ct = m.groupby(["asset_class", "asset_class_bm"]).size()
    txt = "; ".join(f"{a} -> {b} ({c})" for (a, b), c in ct.items())
    dq.add("cross_table", "dim_security", "benchmark_id", "security_vs_benchmark_asset_class", "INFO",
           "Security asset_class to benchmark asset_class mapping (review for sense)", txt)


def x_fees_vs_snapshot(T, dq):
    fee, snap = need(T, "fact_fees", "fact_portfolio_snapshot")
    X = "cross_table"
    s = snap[["snapshot_id", "client_id", "advisor_id", "snapshot_date", "aum"]].drop_duplicates(
        ["client_id", "snapshot_date"])
    j = fee[["fee_id", "client_id", "advisor_id", "fee_date", "aum_basis"]].merge(
        s, left_on=["client_id", "fee_date"], right_on=["client_id", "snapshot_date"],
        how="left", suffixes=("", "_snap"))
    j.index = fee.index
    nosnap = j["snapshot_id"].isna()
    dq.result(X, "fact_fees", "fee_date", "fee_has_snapshot", int(nosnap.sum()), len(fee), "WARN",
              "Fee with no portfolio snapshot for the same client and month",
              sample=sample_keys(fee, nosnap, ["fee_id"]),
              pass_issue="Passed: every fee has a snapshot for the same client-month")
    matched = ~nosnap
    aum_diff = matched & ((to_num(j["aum_basis"]) - to_num(j["aum"])).abs() > 0.005)
    dq.result(X, "fact_fees", "aum_basis", "aum_basis_equals_snapshot_aum", int(aum_diff.sum()), int(matched.sum()),
              "WARN", "Fee aum_basis differs from the snapshot AUM for that month",
              sample=sample_keys(fee, aum_diff, ["fee_id"]),
              pass_issue="Passed: aum_basis always equals the same-month snapshot aum",
              pass_evidence="So fact_fees inherits every AUM problem in fact_portfolio_snapshot")
    adv = matched & (j["advisor_id"] != j["advisor_id_snap"])
    dq.result(X, "fact_fees", "advisor_id", "fee_advisor_equals_snapshot_advisor", int(adv.sum()), int(matched.sum()),
              "WARN", "Fee advisor differs from the snapshot advisor",
              pass_issue="Passed: fee advisor = snapshot advisor")
    ph = to_num(fee["aum_basis"]).isin(AUM_PLACEHOLDER)
    if ph.any():
        dq.add(X, "fact_fees", "aum_basis, amount", "fee_on_placeholder_aum", "WARN",
               "Fees calculated on the placeholder-looking AUM value",
               f"{fmt_n(ph.sum())} fees use aum_basis {AUM_PLACEHOLDER}; total charged "
               f"${to_num(fee.loc[ph, 'amount']).sum():,.2f}. If 1000.0 is a placeholder the true fee "
               "for those months is unknown; that is not confirmed, so WARN",
               int(ph.sum()), len(fee), sample_keys(fee, ph, ["fee_id"]),
               next_step="Exclude or flag these fees in revenue metrics")
    fk = fee[["client_id", "fee_date"]].drop_duplicates()
    sj = snap[["snapshot_id", "client_id", "snapshot_date", "aum"]].merge(
        fk, left_on=["client_id", "snapshot_date"], right_on=["client_id", "fee_date"], how="left")
    sj.index = snap.index
    nofee = sj["fee_date"].isna()
    if nofee.any():
        ev = (f"{fmt_n(nofee.sum())} client-months across {fmt_n(snap.loc[nofee, 'client_id'].nunique())} clients "
              f"and {fmt_n(snap.loc[nofee, 'snapshot_date'].nunique())} months have AUM but no fee "
              "(scattered, not one bad month). Could be fee waivers, billing cycles or missing records")
    else:
        ev = ""
    dq.result(X, "fact_portfolio_snapshot", "snapshot_date", "snapshot_has_fee", int(nofee.sum()), len(snap),
              "WARN", "Snapshot month with no fee charged", ev, sample_keys(snap, nofee, ["snapshot_id"]),
              next_step="Ask whether fees are ever waived/skipped before treating as missing revenue",
              pass_issue="Passed: every snapshot month has a fee")


def x_activity_before_client_since(T, dq):
    (cl,) = need(T, "dim_client")
    since = dict(zip(cl["client_id"], to_date(cl["client_since"])))
    for fact, dcol, by_month in [("fact_portfolio_snapshot", "snapshot_date", True),
                                 ("fact_fees", "fee_date", True),
                                 ("fact_transactions", "txn_date", False)]:
        if fact not in T:
            continue
        f = T[fact]
        d = to_date(f[dcol])
        sc = pd.to_datetime(f["client_id"].map(since))
        if by_month:
            bad = d.dt.to_period("M") < sc.dt.to_period("M")
        else:
            bad = d < sc
        bad = bad.fillna(False).astype(bool)
        dq.result("cross_table", fact, dcol, "activity_not_before_client_since", int(bad.sum()), len(f), "WARN",
                  f"{fact} activity dated before the client's client_since",
                  sample=sample_keys(f, bad, TABLE_SPECS[fact]["pk"]),
                  pass_issue=f"Passed: no {fact} activity before client_since")


def x_client_lifecycle(T, dq):
    cl, snap = need(T, "dim_client", "fact_portfolio_snapshot")
    X = "cross_table"
    sd = to_date(snap["snapshot_date"])
    last = sd.groupby(snap["client_id"]).max()
    gmax = sd.max()
    c = cl.set_index("client_id")
    active = c["is_active"].str.strip() == "True"
    last_c = last.reindex(c.index)
    a_bad = active & (last_c < gmax)
    i_bad = ~active & (last_c >= gmax)
    dq.result(X, "dim_client", "is_active", "active_client_has_latest_snapshot", int(a_bad.sum()), int(active.sum()),
              "WARN", "Active client whose snapshots stop before the last month",
              pass_issue="Passed: every active client has a snapshot in the latest month",
              pass_evidence=f"latest snapshot month {gmax.date()}")
    dq.result(X, "dim_client", "is_active", "inactive_client_snapshots_stop", int(i_bad.sum()), int((~active).sum()),
              "WARN", "Inactive client still has a snapshot in the latest month",
              pass_issue="Passed: inactive clients' snapshots stop before the latest month",
              pass_evidence=(f"{fmt_n((~active).sum())} inactive clients; last snapshots between "
                             f"{last_c[~active].min().date()} and {last_c[~active].max().date()}. "
                             "No departure date column exists - last snapshot is the only proxy"))
    no_snap = last_c.isna()
    dq.result(X, "dim_client", "client_id", "client_has_snapshots", int(no_snap.sum()), len(c), "WARN",
              "Client with no portfolio snapshots", pass_issue="Passed: every client has snapshots")

    lastm = (last + pd.offsets.MonthEnd(0))
    if "fact_fees" in T:
        f = T["fact_fees"]
        after = (to_date(f["fee_date"]) > pd.to_datetime(f["client_id"].map(lastm))).fillna(False)
        dq.result(X, "fact_fees", "fee_date", "fee_not_after_last_snapshot", int(after.sum()), len(f), "WARN",
                  "Fee after the client's last snapshot month",
                  pass_issue="Passed: no fees after a client's last snapshot")
    if "fact_transactions" in T:
        tx = T["fact_transactions"]
        need_cols(tx, "fact_transactions", "is_post_departure_txn")
        after = (to_date(tx["txn_date"]) > pd.to_datetime(tx["client_id"].map(lastm))).fillna(False)
        flag = tx["is_post_departure_txn"].str.strip() == "1"
        inactive_ids = set(c.index[~active])
        tx_inactive = tx["client_id"].isin(inactive_ids)
        dq.result(X, "fact_transactions", "txn_date", "txn_after_client_departure", int(after.sum()), len(tx),
                  "WARN", "Transactions dated after the client's last snapshot month (last snapshot used as "
                          "a PROXY for departure)",
                  "No departure date exists in the data, so 'departure' here means 'after the last "
                  f"snapshot'. {fmt_n(after.sum())} txns for {fmt_n(tx.loc[after, 'client_id'].nunique())} clients, "
                  f"{fmt_n((after & tx_inactive).sum())} of them for is_active=False clients; dates "
                  f"{to_date(tx.loc[after, 'txn_date']).min().date() if after.any() else ''} to "
                  f"{to_date(tx.loc[after, 'txn_date']).max().date() if after.any() else ''}",
                  sample_keys(tx, after, ["txn_id"]),
                  next_step="Decide whether to exclude these from AUM/flow metrics",
                  pass_issue="Passed: no transactions after a client's last snapshot")
        m1, m2 = after & ~flag, flag & ~after
        dq.result(X, "fact_transactions", "is_post_departure_txn", "post_departure_flag_consistent",
                  int(m1.sum() + m2.sum()), len(tx), "WARN",
                  "is_post_departure_txn disagrees with the last-snapshot rule",
                  f"after-last-snapshot but flag=0: {fmt_n(m1.sum())}; flag=1 but not after: {fmt_n(m2.sum())}",
                  pass_issue="Consistent: is_post_departure_txn = 1 exactly for txns after the client's "
                             "last snapshot month",
                  pass_evidence=f"{fmt_n(flag.sum())} flagged rows. The flag agrees with the last-snapshot "
                                "PROXY; neither is a recorded departure date, so agreement does not confirm "
                                "when clients actually left")


def x_initial_aum(T, dq):
    cl, snap = need(T, "dim_client", "fact_portfolio_snapshot")
    s = snap.assign(_d=to_date(snap["snapshot_date"]), _a=to_num(snap["aum"])).sort_values("_d")
    first = s.groupby("client_id").first()
    window_start = s["_d"].min()
    c = cl.set_index("client_id")
    j = first.join(c[["initial_aum", "client_since"]], how="inner")
    ratio = j["_a"] / to_num(j["initial_aum"])
    onboard = to_date(j["client_since"]) >= window_start
    bad = onboard & ((ratio < 0.5) | (ratio > 2))
    dq.result("cross_table", "dim_client", "initial_aum", "initial_aum_vs_first_snapshot", int(bad.sum()),
              int(onboard.sum()), "WARN",
              "For clients onboarded inside the data window, first snapshot AUM differs >2x from initial_aum",
              f"ratio range {ratio[onboard].min():.2f}-{ratio[onboard].max():.2f} for in-window clients; "
              f"e.g. client_id {', '.join(j.index[bad][:5])}",
              next_step="Check whether these first snapshots are placeholders",
              pass_issue="Passed: initial_aum is close to the first snapshot AUM for newly onboarded clients")


def x_advisory_fee_reconciliation(T, dq):
    tx, fee = need(T, "fact_transactions", "fact_fees")
    af = tx[tx["txn_type"].str.strip() == "Advisory Fee"]
    a = to_num(af["amount"]).groupby([af["client_id"], af["txn_date"].str[:7]]).sum()
    f = to_num(fee["amount"]).groupby([fee["client_id"], fee["fee_date"].str[:7]]).sum()
    both = pd.concat([a.rename("txn"), f.rename("fee")], axis=1)
    overlap = both.dropna()
    agree = int(((overlap["txn"] - overlap["fee"]).abs() <= 1).sum())
    disagree = len(overlap) - agree
    ay = to_num(af["amount"]).groupby([af["client_id"], af["txn_date"].str[:4]]).sum()
    fy = to_num(fee["amount"]).groupby([fee["client_id"], fee["fee_date"].str[:4]]).sum()
    yr = pd.concat([ay.rename("txn"), fy.rename("fee")], axis=1).dropna()
    yr_ratio = (yr["txn"] / yr["fee"]).quantile([0.1, 0.5, 0.9])
    ev = (f"client-months with 'Advisory Fee' txns: {fmt_n(a.size)}; with fact_fees rows: {fmt_n(f.size)}; "
          f"both: {fmt_n(len(overlap))}; amounts agree within $1: {fmt_n(agree)} ({pct(agree, len(overlap))}%). "
          f"Totals: txns ${a.sum():,.0f} vs fact_fees ${f.sum():,.0f}. Per client-year the txn/fee ratio is "
          f"{yr_ratio[0.1]:.2f} / {yr_ratio[0.5]:.2f} / {yr_ratio[0.9]:.2f} (p10/median/p90), so it is not a "
          "simple billing-cycle offset. WARN not ERROR: nothing documents that the two must match, and "
          "each looks plausible on its own scale")
    dq.result("cross_table", "fact_transactions / fact_fees", "amount", "advisory_fee_txn_reconciles_to_fees",
              disagree, len(overlap), "WARN",
              "'Advisory Fee' transactions do not reconcile to fact_fees", ev,
              next_step="Ask which table is the system of record for fee revenue; do not add them together",
              pass_issue="Passed: Advisory Fee transactions reconcile to fact_fees", pass_evidence=ev)


def x_positions(T, dq):
    tx, cl = need(T, "fact_transactions", "dim_client")
    X = "cross_table"
    t = tx[tx["txn_type"].str.strip().isin(["Buy", "Sell"])].copy()
    is_sell = t["txn_type"].str.strip() == "Sell"
    t["_signed"] = np.where(is_sell, -1, 1) * to_num(t["shares"])
    t["_d"], t["_id"] = to_date(t["txn_date"]), to_num(t["txn_id"])
    # Same-day Buys are applied before Sells so intraday ordering never creates a false oversell.
    t["_ord"] = is_sell.astype(int)
    t = t.sort_values(["client_id", "security_id", "_d", "_ord", "_id"])
    t["_pos"] = t.groupby(["client_id", "security_id"])["_signed"].cumsum()
    t["_prev"] = t["_pos"] - t["_signed"]
    eps = 1e-6
    sell = t["txn_type"].str.strip() == "Sell"
    first_oversell = sell & (t["_prev"] >= -eps) & (t["_pos"] < -eps)   # position crosses below zero
    on_negative = t["_prev"] < -eps                                     # any Buy/Sell on a position already < 0
    since = to_date(cl.set_index("client_id")["client_since"])
    window_start = to_date(tx["txn_date"]).min()
    in_window = t["client_id"].map(since) >= window_start
    sells, trades = int(sell.sum()), len(t)
    pairs = lambda m: fmt_n(t.loc[m, ["client_id", "security_id"]].drop_duplicates().shape[0])
    groups = [
        (in_window, "clients onboarded inside the data window", "in-window", "ERROR", "WARN",
         "ERROR: these clients' whole history is in the extract and the table has no transfer-in type, so "
         "every share held must come from a Buy here - the ledger is internally inconsistent"),
        (~in_window, "clients onboarded before the data window", "pre-window", "INFO", "INFO",
         "INFO: earlier purchases may simply pre-date the extract"),
    ]
    for scope, who, tag, sev_first, sev_next, why in groups:
        m1, m2 = first_oversell & scope, on_negative & scope
        dq.result(X, "fact_transactions", "shares", f"oversell_first_event ({tag})", int(m1.sum()), sells,
                  sev_first, f"Sell takes a position below zero for the first time - {who}",
                  f"{fmt_n(m1.sum())} initial oversell events across {pairs(m1)} client-security pairs "
                  f"(same-day Buys applied first). {why}", sample_keys(t, m1, ["txn_id"]),
                  next_step="Holdings cannot be rebuilt from transactions alone; use snapshots for AUM",
                  pass_issue=f"Passed: no oversells for {who}")
        dq.result(X, "fact_transactions", "shares", f"txn_on_negative_position ({tag})", int(m2.sum()), trades,
                  sev_next, f"Buy/Sell on a position that is already negative - {who}",
                  f"{fmt_n(m2.sum())} later transactions ({fmt_n((m2 & sell).sum())} Sells, "
                  f"{fmt_n((m2 & ~sell).sum())} Buys) on {pairs(m2)} pairs. Consequences of the first "
                  "oversell, reported separately so the defect is not double-counted",
                  sample_keys(t, m2, ["txn_id"]),
                  pass_issue=f"Passed: no transactions on negative positions for {who}")
    dv = tx[tx["txn_type"].str.strip() == "Dividend"]
    buys = t[t["txn_type"].str.strip() == "Buy"].groupby(["client_id", "security_id"])["_d"].min()
    j = dv[["client_id", "security_id"]].merge(buys.rename("_first_buy").reset_index(),
                                                on=["client_id", "security_id"], how="left")
    first_buy = pd.Series(j["_first_buy"].values, index=dv.index)
    no_pos = first_buy.isna() | (to_date(dv["txn_date"]) < first_buy)
    no_pos &= (dv["client_id"].map(since) >= window_start)
    dq.result(X, "fact_transactions", "security_id", "dividend_after_purchase", int(no_pos.sum()), len(dv), "WARN",
              "Dividend on a security the client had not bought yet (in-window clients)",
              f"{fmt_n(no_pos.sum())} dividends for clients whose full history is in the data; "
              f"{fmt_n(first_buy[no_pos].isna().sum())} are on securities the client never bought at all",
              sample=sample_keys(dv, no_pos, ["txn_id"]),
              next_step="Supports the view that transactions are not a complete holdings ledger",
              pass_issue="Passed: dividends only on held securities")


def x_dividend_vs_aum(T, dq):
    tx, snap = need(T, "fact_transactions", "fact_portfolio_snapshot")
    dv = tx[tx["txn_type"].str.strip() == "Dividend"]
    div = to_num(dv["amount"]).groupby([dv["client_id"], dv["txn_date"].str[:4]]).sum()
    aum = to_num(snap["aum"])
    keep = aum.notna() & ~aum.isin(AUM_PLACEHOLDER)
    avg = aum[keep].groupby([snap.loc[keep, "client_id"], snap.loc[keep, "snapshot_date"].str[:4]]).mean()
    j = pd.concat([div.rename("div"), avg.rename("aum")], axis=1).dropna()
    yld = j["div"] / j["aum"]
    bad = yld > 1
    sample = "; ".join(f"client_id={c}, year={y}" for c, y in j.index[bad][:5])
    dq.result("cross_table", "fact_transactions", "amount", "dividend_income_vs_aum", int(bad.sum()), len(j),
              "ERROR", "Client-year dividend income exceeds the client's average AUM (>100% yield)",
              f"{fmt_n(bad.sum())} of {fmt_n(len(j))} client-years; dividend/AUM median {yld.median():.1%}, "
              f"p90 {yld.quantile(0.9):.1%}, max {yld.max():.0%}. aum = {AUM_PLACEHOLDER[0]:,.0f} months excluded "
              "from the average. ERROR: no portfolio can pay out more than its whole value in dividends in a "
              "year, and every dividend amount equals shares x price - the dividend amounts are miscalculated",
              sample, next_step="Exclude dividend amounts from income/return metrics until the source "
                                "explains them",
              pass_issue="Passed: no client-year has dividends above its average AUM")


def x_returns_vs_flows(T, dq):
    """Exploratory: do month-to-month returns move with AUM including or excluding external flows?"""
    snap, tx = need(T, "fact_portfolio_snapshot", "fact_transactions")
    s = pd.DataFrame({"client_id": snap["client_id"], "_d": to_date(snap["snapshot_date"]),
                      "_aum": to_num(snap["aum"]), "_r": to_num(snap["return_ytd"])})
    s = s.sort_values(["client_id", "_d"])
    g = s.groupby("client_id")
    s["_pd"], s["_paum"], s["_pr"] = g["_d"].shift(), g["_aum"].shift(), g["_r"].shift()
    # consecutive months in the same calendar year (return_ytd compounds within a year)
    ok = (s["_pd"].notna() & (s["_d"].dt.year == s["_pd"].dt.year)
          & (s["_d"].dt.month - s["_pd"].dt.month == 1) & (s["_paum"] > 0)
          & ~s["_aum"].isin(AUM_PLACEHOLDER) & ~s["_paum"].isin(AUM_PLACEHOLDER))
    s = s[ok]
    month_ret = (1 + s["_r"]) / (1 + s["_pr"]) - 1
    raw_growth = s["_aum"] / s["_paum"] - 1

    fl = tx[tx["txn_type"].str.strip().isin(["Deposit", "Withdrawal"])]
    sign = np.where(fl["txn_type"].str.strip() == "Deposit", 1, -1)
    flow = (to_num(fl["amount"]) * sign).groupby([fl["client_id"], to_date(fl["txn_date"]).dt.to_period("M")]).sum()

    # Snapshots are dated the 1st, but whether a row describes the start or the end of that month is
    # undocumented, so both alignments of "flows between two snapshots" are tested.
    parts, quiet = [], pd.Series(True, index=s.index)
    for label, period in [("flows dated in the earlier snapshot's month", s["_pd"].dt.to_period("M")),
                          ("flows dated in the later snapshot's month", s["_d"].dt.to_period("M"))]:
        key = pd.MultiIndex.from_arrays([s["client_id"], period])
        net = pd.Series(flow.reindex(key).values, index=s.index)
        quiet &= net.isna()
        net = net.fillna(0)
        material = net.abs() > 0.05 * s["_paum"]
        adj_growth = (s["_aum"] - s["_paum"] - net) / s["_paum"]
        e_raw = (month_ret - raw_growth)[material].abs().median()
        e_adj = (month_ret - adj_growth)[material].abs().median()
        within = ((month_ret - raw_growth).abs() < 1e-4)[material].mean()
        parts.append(f"{label}: {fmt_n(material.sum())} months with flows > 5% of AUM; median |return - "
                     f"raw AUM growth| {e_raw:.1e} vs |return - flow-adjusted growth| {e_adj:.4f}; return "
                     f"within 0.0001 of raw AUM growth in {within:.1%} of them")
    e_quiet = (month_ret - raw_growth)[quiet].abs().median()
    dq.add("cross_table", "fact_portfolio_snapshot", "return_ytd", "returns_vs_external_flows", "INFO",
           "Exploratory: do returns move with deposits/withdrawals? (no conclusion drawn)",
           f"{fmt_n(len(s))} consecutive same-year month pairs (aum = {AUM_PLACEHOLDER[0]:,.0f} excluded). "
           f"Months with no flows either side: median |return - AUM growth| {e_quiet:.1e}. "
           + "; ".join(parts) + ". Returns that track RAW AUM growth even in large-flow months suggest "
           "deposits/withdrawals are included in return_ytd (not a time-weighted return). The return "
           "methodology is undocumented, so this is reported as an observation, not a defect",
           len(s), len(snap),
           next_step="Ask how return_ytd is calculated (time-weighted? net of flows?) before using it")


def x_fee_rate_vs_tier(T, dq):
    """Exploratory: observed bps_charged ranges by client tier. No fee schedule was provided."""
    fee, cl = need(T, "fact_fees", "dim_client")
    m = fee[["client_id", "bps_charged", "fee_type"]].merge(cl[["client_id", "client_tier"]],
                                                           on="client_id", how="left")
    b = to_num(m["bps_charged"])
    st = b.groupby(m["client_tier"]).quantile([0, 0.01, 0.5, 0.99, 1]).unstack()
    st = st.sort_values(0.5)
    txt = "; ".join(f"{tr}: {r[0]:.2f} / {r[0.01]:.2f} / {r[0.5]:.2f} / {r[0.99]:.2f} / {r[1]:.2f}"
                    for tr, r in st.iterrows())
    overlaps = [f"{a} max {st.loc[a, 1]:.2f} > {c} min {st.loc[c, 0]:.2f}"
                for a, c in zip(st.index[:-1], st.index[1:]) if st.loc[a, 1] > st.loc[c, 0]]
    by_type = b.groupby(m["fee_type"]).median()
    dq.add("cross_table", "fact_fees", "bps_charged", "fee_rate_vs_client_tier", "INFO",
           "Exploratory: observed fee rate (bps) ranges by client tier - ranges INFERRED, not a fee schedule",
           f"min / p1 / median / p99 / max by tier: {txt}. "
           + (f"Ranges overlap: {'; '.join(overlaps)}. " if overlaps else "Ranges do not overlap. ")
           + "Median bps by fee_type: " + ", ".join(f"{k} {v:.2f}" for k, v in by_type.items())
           + " (rate appears to follow tier, not fee_type). These ranges were read from this data, so "
           "they describe the pattern and cannot validate individual rows",
           next_step="Request the official fee schedule before flagging rates as wrong")


def x_trading_days(T, dq):
    tx, dd = need(T, "fact_transactions", "dim_date")
    flag = tx["txn_date"].map(dict(zip(dd["date"], dd["is_trading_day"].str.strip())))
    closed = flag == "0"
    typ = tx["txn_type"].str.strip()
    for types, sev, label in [(["Buy", "Sell"], "WARN", "Trades"),
                              (["Dividend"], "WARN", "Dividends"),
                              (CASH_TXN_TYPES, "INFO", "Cash movements / fees")]:
        m = closed & typ.isin(types)
        dq.result("cross_table", "fact_transactions", "txn_date", f"{label.lower()}_on_trading_days",
                  int(m.sum()), int(typ.isin(types).sum()), sev,
                  f"{label} dated on non-trading days (weekends per dim_date)",
                  "transaction dates look uniformly random across the week - may be synthetic or "
                  "booking-date rather than trade-date", sample_keys(tx, m, ["txn_id"]),
                  pass_issue=f"Passed: {label.lower()} only on trading days")


def x_date_coverage(T, dq):
    (dd,) = need(T, "dim_date")
    d = to_date(dd["date"])
    parts = [f"dim_date {d.min().date()} to {d.max().date()}"]
    for fact, dcol in [("fact_fees", "fee_date"), ("fact_portfolio_snapshot", "snapshot_date"),
                       ("fact_transactions", "txn_date")]:
        if fact in T:
            fd = to_date(T[fact][dcol])
            parts.append(f"{fact}.{dcol} {fd.min().date()} to {fd.max().date()}")
    dq.add("cross_table", "dim_date", "date", "date_coverage", "INFO", "Date ranges by table", "; ".join(parts))
    dq.add("cross_table", "dim_date", "date_id", "date_id_unused", "INFO",
           "Facts store the date string, not date_id; joins to dim_date must use dim_date.date",
           "no fact table has a date_id column")


CROSS_RULES = [
    ("fact_* / dim_client", "advisor_id", "advisor_consistency", x_advisor_consistency),
    ("fact_portfolio_snapshot", "account_type", "account_type_consistency", x_account_type),
    ("fact_portfolio_snapshot", "benchmark_id", "risk_profile_benchmark", x_risk_benchmark),
    ("dim_security", "benchmark_id", "security_benchmark_mapping", x_security_benchmark),
    ("fact_fees / fact_portfolio_snapshot", "", "fees_vs_snapshot", x_fees_vs_snapshot),
    ("fact_*", "", "activity_before_client_since", x_activity_before_client_since),
    ("dim_client / facts", "", "client_lifecycle", x_client_lifecycle),
    ("dim_client", "initial_aum", "initial_aum_vs_snapshot", x_initial_aum),
    ("fact_transactions / fact_fees", "", "advisory_fee_reconciliation", x_advisory_fee_reconciliation),
    ("fact_transactions", "shares", "positions", x_positions),
    ("fact_transactions / fact_portfolio_snapshot", "amount", "dividend_vs_aum", x_dividend_vs_aum),
    ("fact_portfolio_snapshot / fact_transactions", "return_ytd", "returns_vs_flows", x_returns_vs_flows),
    ("fact_fees / dim_client", "bps_charged", "fee_rate_vs_tier", x_fee_rate_vs_tier),
    ("fact_transactions", "txn_date", "trading_days", x_trading_days),
    ("dim_date", "", "date_coverage", x_date_coverage),
]


# =============================================================================
# 11. MAIN
# =============================================================================
def resolve_dir(p):
    p = Path(p)
    return (p if p.is_absolute() else REPO_ROOT / p).resolve()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Profile and audit the raw Wildcat Capital CSVs (read-only).")
    ap.add_argument("--raw-dir", default=DEFAULT_RAW_DIR, help="folder with the raw CSVs (default data/raw)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="folder for the reports (default reports)")
    ap.add_argument("--as-of", default=AUDIT_REFERENCE_DATE,
                    help=f"audit reference date for future-date checks, YYYY-MM-DD (default {AUDIT_REFERENCE_DATE})")
    return ap.parse_args(argv)


def main(argv=None):
    global REF_DATE
    args = parse_args(argv)
    REF_DATE = pd.Timestamp(args.as_of)
    raw_dir, out_dir = resolve_dir(args.raw_dir), resolve_dir(args.out_dir)
    if not raw_dir.is_dir():
        sys.exit(f"Raw data folder not found: {raw_dir}")
    if out_dir == raw_dir or raw_dir in out_dir.parents:
        sys.exit("Refusing to write reports inside the raw data folder.")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Raw data : {raw_dir}\nReports  : {out_dir}\nAs-of    : {REF_DATE.date()}\n")
    dq = DQLog()
    T, files = load_tables(raw_dir, dq)

    print("\nProfiling columns and running column / single-table checks ...")
    fk_cols = {}
    for child, ccol, _, _ in FOREIGN_KEYS:
        fk_cols.setdefault(child, set()).add(ccol)
    profile_rows = []
    for table, df in T.items():
        spec = TABLE_SPECS[table]
        check_schema(table, df, spec, dq)
        keys = set(spec["pk"]) | fk_cols.get(table, set())
        for c in df.columns:
            row = profile_column(table, c, df[c], spec["columns"].get(c), keys, dq)
            row["file_name"] = files[table]
            profile_rows.append(row)
        check_primary_key(table, df, spec["pk"], dq)
        run_check(dq, "single_table", table, "", "generic_duplicates", single_table_generic, table, df, spec, dq)
        if table in ROW_RULES:
            run_check(dq, "single_table", table, "", "row_rules", ROW_RULES[table], df, dq)

    print("Checking referential integrity ...")
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        run_check(dq, "referential", child, ccol, f"fk_{ccol}_to_{parent}", check_fk,
                  T, dq, child, ccol, parent, pcol)
    run_check(dq, "referential", "", "", "unused_dimension_members", unused_dimension_members, T, dq)

    print("Checking cross-table business rules ...")
    for table, column, name, fn in CROSS_RULES:
        run_check(dq, "cross_table", table, column, name, fn, T, dq)

    profile = pd.DataFrame(profile_rows)
    front = ["file_name", "table", "column"]
    profile = profile[front + [c for c in profile.columns if c not in front]]
    summary = dq.to_frame()
    profile.to_csv(out_dir / PROFILE_FILE, index=False, encoding="utf-8-sig")
    summary.to_csv(out_dir / SUMMARY_FILE, index=False, encoding="utf-8-sig")

    findings = summary[summary["status"] == "FINDING"]
    print(f"\nWrote {out_dir / PROFILE_FILE}  ({len(profile)} columns profiled)")
    print(f"Wrote {out_dir / SUMMARY_FILE}  ({len(summary)} rows: "
          f"{len(findings)} findings, {(summary['status'] == 'PASS').sum()} passed checks, "
          f"{(summary['status'] == 'NOT_RUN').sum()} not run)\n")
    print("Findings by severity:")
    print(findings.groupby("severity").size().reindex(["ERROR", "WARN", "INFO"], fill_value=0).to_string())
    print("\nFindings by table and severity:")
    if len(findings):
        print(pd.crosstab(findings["table"], findings["severity"]).to_string())
    top = findings[findings["severity"].isin(["ERROR", "WARN"])]
    if len(top):
        print("\nERROR / WARN findings:")
        for _, r in top.iterrows():
            rows = f" [{r['affected_rows']:,} rows]" if r["affected_rows"] != "" else ""
            where = f"{r['table']}.{r['column']}" if r["column"] else r["table"]
            print(f"  {r['severity']:<5} {where}: {r['issue']}{rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
