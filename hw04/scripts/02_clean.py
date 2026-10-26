#!/usr/bin/env python3
"""
HW4 Part C - Cleaning the Wildcat Capital Data
==============================================

Implements the cleaning decisions in ``decision_log.md`` (Part B), which are based
on the Part A audit (``reports/dq_summary_raw.csv``).

What this script does
---------------------
* Reads the eight raw CSVs from ``data/raw/`` as TEXT
  (``dtype=str, keep_default_na=False``) so blanks, the literal value ``None`` and
  leading-zero ZIP codes survive loading (Decision 10).
* NEVER writes to ``data/raw/``. SHA-256 hashes of every raw file are taken before
  and after the run and must match.
* Keeps every row and every original business value, except for the one documented
  format change (Decision 16: scientific notation -> fixed six-decimal notation in
  ``return_ytd`` and ``active_alpha_ytd``, proven value-preserving with exact
  Decimal comparisons).
* Adds 0/1 flag columns (all named ``flag_*``) for the confirmed-error and
  suspicious records the decision log says to flag. Each flag's row count must
  equal the count in the decision log / Part A audit, or the script stops.
* Writes the cleaned tables to ``data/clean/`` (``fact_transactions (1).csv`` is
  written as ``fact_transactions.csv``, Decision 17).
* Writes the audit trail to ``data/audit/``:
    - ``cleaning_log.csv``       one row per step: rows affected, before/after examples, assertions
    - ``value_changes.csv``      one row per cell whose text actually changed
    - ``row_reconciliation.csv`` raw rows = clean rows + quarantined rows, per table
  and quarantine files only if a row is ever removed (none are, under the current log).
* Generates ``data/clean/README_clean.md`` (joins, BI data types, flags, limitations,
  ambiguous decisions) from the numbers computed in this run.

Run from anywhere (paths are resolved relative to this file):
    python scripts/02_clean.py
"""
from __future__ import annotations

import hashlib
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# 1. PATHS (relative to this script, so the run is reproducible on any machine)
# =============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
HW_DIR = SCRIPT_DIR.parent                      # .../hw04
RAW_DIR = HW_DIR / "data" / "raw"
CLEAN_DIR = HW_DIR / "data" / "clean"
AUDIT_DIR = HW_DIR / "data" / "audit"
QUARANTINE_DIR = AUDIT_DIR / "quarantine"
DECISION_LOG = HW_DIR / "decision_log.md"
DQ_SUMMARY = HW_DIR / "reports" / "dq_summary_raw.csv"

# =============================================================================
# 2. TABLE SPECS
# =============================================================================
# Accepted raw filenames, in order of preference. Only fact_transactions has an
# alternative name (the browser re-download copy flagged by DQ0003).
RAW_FILES = {
    "dim_advisor": ["dim_advisor.csv"],
    "dim_benchmark": ["dim_benchmark.csv"],
    "dim_client": ["dim_client.csv"],
    "dim_date": ["dim_date.csv"],
    "dim_security": ["dim_security.csv"],
    "fact_fees": ["fact_fees.csv"],
    "fact_portfolio_snapshot": ["fact_portfolio_snapshot.csv"],
    "fact_transactions": ["fact_transactions (1).csv", "fact_transactions.csv"],
}

PRIMARY_KEYS = {
    "dim_advisor": "advisor_id",
    "dim_benchmark": "benchmark_id",
    "dim_client": "client_id",
    "dim_date": "date_id",
    "dim_security": "security_id",
    "fact_fees": "fee_id",
    "fact_portfolio_snapshot": "snapshot_id",
    "fact_transactions": "txn_id",
}

# Expected raw headers (Part A "schema" checks passed with exactly these columns).
EXPECTED_COLUMNS = {
    "dim_advisor": ["advisor_id", "first_name", "last_name", "region", "years_experience",
                    "aum_target_mm", "certifications"],
    "dim_benchmark": ["benchmark_id", "name", "asset_class", "ticker_proxy"],
    "dim_client": ["client_id", "first_name", "last_name", "age", "city", "state", "zip_code",
                   "risk_profile", "client_tier", "primary_account_type", "initial_aum",
                   "client_since", "advisor_id", "is_active"],
    "dim_date": ["date_id", "date", "year", "quarter", "month", "month_name", "week_of_year",
                 "day_of_week", "is_month_end", "is_quarter_end", "is_year_end", "is_trading_day"],
    "dim_security": ["security_id", "ticker", "name", "asset_class", "sector", "benchmark_id",
                     "is_etf", "expense_ratio_bps"],
    "fact_fees": ["fee_id", "client_id", "advisor_id", "fee_date", "fee_type", "aum_basis",
                  "bps_charged", "amount"],
    "fact_portfolio_snapshot": ["snapshot_id", "client_id", "advisor_id", "benchmark_id",
                                "snapshot_date", "year", "month", "aum", "return_ytd",
                                "benchmark_return_ytd", "active_alpha_ytd", "cash_pct",
                                "account_type", "is_return_suspect"],
    "fact_transactions": ["txn_id", "client_id", "advisor_id", "security_id", "txn_date",
                          "txn_type", "shares", "price", "amount", "is_post_departure_txn"],
}

# Row counts the decision log / Part A audit report for each flag. The script
# stops if the data produce a different number, because the decision log was
# written for these exact findings.
EXPECTED = {
    "flag_dividend_amount_miscalculated": 53_826,          # Decision 1  (DQ0014)
    "flag_oversell_first_in_window": 9_828,                # Decision 2  (DQ0002)
    "flag_txn_on_negative_position_in_window": 15_977,     # Decision 2  (DQ0020)
    "flag_oversell_first_pre_window": 15_693,              # Decision 2  (DQ0056)
    "flag_txn_on_negative_position_pre_window": 25_574,    # Decision 2  (DQ0057)
    "flag_extreme_return": 2_639,                          # Decision 3  (DQ0009)
    "flag_aum_equals_1000": 420,                           # Decision 5  (DQ0008)
    "flag_aum_mom_jump": 330,                              # Decision 5  (DQ0012)
    "flag_aum_basis_equals_1000": 386,                     # Decision 5  (DQ0005/DQ0016)
    "flag_bps_above_100": 1,                               # Decision 6  (DQ0006)
    "flag_risk_profile_unknown": 75,                       # Decision 7  (DQ0004)
    "flag_zip_state_mismatch": 2_229,                      # Decision 8  (DQ0010)
    "flag_dividend_without_prior_purchase": 8_444,         # Decision 12 (DQ0019)
    "flag_weekend_trade_or_dividend": 40_630 + 15_487,     # Decision 14 (DQ0022 + DQ0023)
    "flag_initial_aum_mismatch": 1,                        # Decision 15 (DQ0015)
}

# Thresholds stated in the decision log (documented there as ASSUMED, not official).
RETURN_LOW, RETURN_HIGH = -0.5, 1.0     # Decision 3
AUM_PLACEHOLDER = 1000.0                # Decision 5
AUM_JUMP_UP, AUM_JUMP_DOWN = 3.0, 1 / 3  # Decision 5
BPS_CAP = 100.0                         # Decision 6
FIXED_DECIMALS = 6                      # Decision 16

TRADE_TYPES = ["Buy", "Sell"]
CASH_TXN_TYPES = ["Deposit", "Withdrawal", "Advisory Fee"]
WEEKEND_DAYS = ["Saturday", "Sunday"]

# First digit of a US ZIP code -> states that use it. Copied unchanged from
# 01_profile.py so the flag reproduces the Part A finding (DQ0010) exactly.
ZIP_FIRST_DIGIT = {
    "0": {"CT", "MA", "ME", "NH", "NJ", "PR", "RI", "VT", "NY"},
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

DEC_RE = r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
EPS = 1e-6  # share-position tolerance, same as 01_profile.py


# =============================================================================
# 3. SMALL HELPERS
# =============================================================================
class CleaningError(RuntimeError):
    """Raised for missing inputs or anything that makes the run unsafe."""


def check(condition, message):
    """Assertion that still runs under `python -O` (plain `assert` would be skipped)."""
    if not bool(condition):
        raise AssertionError(message)


def to_num(s):
    """Strict numeric parse of text; blanks / non-numbers -> NaN (never used for output)."""
    t = s.astype(str).str.strip()
    return pd.to_numeric(t.where(t.str.match(DEC_RE)), errors="coerce")


def to_date(s):
    t = s.astype(str).str.strip()
    return pd.to_datetime(t.where(t.str.match(DATE_RE)), format="%Y-%m-%d", errors="coerce")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def full_mask(df, partial):
    """Expand a boolean Series computed on a subset back to the whole table."""
    return partial.reindex(df.index, fill_value=False).astype(bool)


def row_text(df, table, idx, cols):
    pk = PRIMARY_KEYS[table]
    vals = ", ".join(f"{c}={df.at[idx, c]!r}" for c in cols)
    return f"{pk}={df.at[idx, pk]}: {vals}"


def safe_output(path):
    """Refuse to write anywhere inside data/raw/ (requirement: never modify raw files)."""
    p = Path(path).resolve()
    check(RAW_DIR.resolve() not in p.parents and p != RAW_DIR.resolve(),
          f"Refusing to write inside the raw folder: {p}")
    return p


# =============================================================================
# 4. AUDIT TRAIL
# =============================================================================
class Audit:
    def __init__(self):
        self.log_rows = []
        self.change_rows = []
        self.n = 0

    def step(self, decision, audit_ids, table, column, action, classification, rule,
             affected, total, before, after, assertions, expected=None):
        self.n += 1
        step_id = f"C{self.n:02d}"
        before = before if isinstance(before, list) else [before]
        after = after if isinstance(after, list) else [after]
        print(f"\n[{step_id}] {decision} | {table}.{column}")
        print(f"   action     : {action}")
        print(f"   rule       : {rule}")
        exp_txt = f" (expected {expected:,})" if expected is not None else ""
        print(f"   affected   : {affected:,} of {total:,} rows{exp_txt}")
        for b, a in zip(before, after):
            print(f"   before     : {b}")
            print(f"   after      : {a}")
        for a in assertions:
            print(f"   assert OK  : {a}")
        self.log_rows.append({
            "step_id": step_id, "decision": decision, "audit_ids": audit_ids, "table": table,
            "column": column, "action": action, "classification": classification, "rule": rule,
            "rows_affected": affected, "total_rows": total,
            "expected_rows": "" if expected is None else expected,
            "before_example": " || ".join(before), "after_example": " || ".join(after),
            "assertions": " ; ".join(assertions), "status": "PASS",
        })
        return step_id

    def change(self, table, pk_col, pk, column, original, cleaned, step_id, decision):
        self.change_rows.append({
            "table": table, "row_id_column": pk_col, "row_id": pk, "column": column,
            "original_value": original, "cleaned_value": cleaned,
            "step_id": step_id, "decision": decision,
        })


# =============================================================================
# 5. LOAD
# =============================================================================
def resolve_raw_file(table):
    found = [RAW_DIR / name for name in RAW_FILES[table] if (RAW_DIR / name).is_file()]
    if not found:
        raise CleaningError(f"No raw file for '{table}' in {RAW_DIR}. Looked for: {RAW_FILES[table]}")
    if len(found) > 1:
        raise CleaningError(f"Ambiguous raw input for '{table}': {[f.name for f in found]} all exist. "
                            "Keep only the confirmed extract in data/raw/ and rerun.")
    return found[0]


def load_tables():
    if not RAW_DIR.is_dir():
        raise CleaningError(f"Raw data folder not found: {RAW_DIR}")
    for p in (DECISION_LOG, DQ_SUMMARY):
        if not p.is_file():
            raise CleaningError(f"Required Part A/B input missing: {p}")
    tables, sources, hashes = {}, {}, {}
    for table in RAW_FILES:
        path = resolve_raw_file(table)
        hashes[path] = sha256(path)
        # Decision 10: read everything as text so nothing is converted or lost on load.
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8")
        check(list(df.columns) == EXPECTED_COLUMNS[table],
              f"{path.name}: header {list(df.columns)} does not match expected {EXPECTED_COLUMNS[table]}")
        pk = PRIMARY_KEYS[table]
        check(df[pk].ne("").all() and df[pk].is_unique, f"{table}.{pk} must be non-blank and unique")
        check(all(df[c].map(type).eq(str).all() for c in df.columns), f"{table}: not every value loaded as str")
        tables[table], sources[table] = df, path
        print(f"Loaded {table:<25} {len(df):>8,} rows  {len(df.columns):>2} cols  <- {path.name}")
    return tables, sources, hashes


# =============================================================================
# 6. FLAG HELPER
# =============================================================================
def add_flag(audit, df, table, flag, mask, *, decision, audit_ids, classification, rule,
             evidence_cols, extra_assertions=()):
    check(flag.startswith("flag_"), f"{flag}: flag names must start with flag_")
    check(flag not in df.columns, f"{table}.{flag} already exists")
    mask = mask.reindex(df.index).fillna(False).astype(bool)
    before_snapshot = df[EXPECTED_COLUMNS[table]].copy()

    df[flag] = mask.astype("int8")

    n = int(mask.sum())
    expected = EXPECTED.get(flag)
    check(set(df[flag].unique()) <= {0, 1}, f"{flag} must contain only 0/1")
    check(int(df[flag].sum()) == n, f"{flag}: flag total differs from rule count")
    if expected is not None:
        check(n == expected, f"{flag}: {n:,} rows flagged but decision log / audit says {expected:,}. "
                             "The data or the rule changed - stop and review before cleaning.")
    check(df[EXPECTED_COLUMNS[table]].equals(before_snapshot), f"{flag}: business columns changed")

    ex = list(df.index[mask][:3])
    before = [f"{row_text(df, table, i, evidence_cols)} | {flag} not present" for i in ex] or ["(no rows)"]
    after = [f"{row_text(df, table, i, evidence_cols)} | {flag}=1" for i in ex] or ["(no rows)"]
    assertions = ["values are 0/1 only", f"sum({flag}) = {n:,} = rule count",
                  "original business columns unchanged"]
    if expected is not None:
        assertions.append(f"count matches decision log / Part A audit ({expected:,})")
    assertions += list(extra_assertions)
    audit.step(decision, audit_ids, table, flag, f"add 0/1 flag {flag} (values preserved)",
               classification, rule, n, len(df), before, after, assertions, expected)
    return mask


# =============================================================================
# 7. CLEANING STEPS (one function per decision group)
# =============================================================================
def step_filename(audit, T, sources):
    src = sources["fact_transactions"]
    audit.step("Decision 17 - filename", "DQ0003", "fact_transactions", "(file)",
               "write cleaned table as fact_transactions.csv; raw file left untouched",
               "Suspicious (filename)", "standard output name for the transaction extract", 0,
               len(T["fact_transactions"]), f"data/raw/{src.name}", "data/clean/fact_transactions.csv",
               [f"exactly one transaction extract found ({src.name})", "raw file is never renamed or modified"])


def step_dividends(audit, T):
    tx = T["fact_transactions"]
    typ = tx["txn_type"]
    div = typ.eq("Dividend")
    shares, price, amount = to_num(tx["shares"]), to_num(tx["price"]), to_num(tx["amount"])
    # Evidence behind Decision 1: every dividend amount equals shares x price.
    gap = (amount - shares * price).abs()[div]
    check(gap.notna().all() and (gap <= 0.01).all(),
          "Decision 1 evidence no longer holds: some dividend amounts differ from shares x price")
    add_flag(audit, tx, "fact_transactions", "flag_dividend_amount_miscalculated", div,
             decision="Decision 1 - miscalculated dividend amounts", audit_ids="DQ0001, DQ0014",
             classification="Confirmed Error",
             rule="txn_type = 'Dividend' (every dividend amount = shares x price, the full position value); "
                  "amount kept, exclude from dividend-income and return metrics",
             evidence_cols=["txn_type", "shares", "price", "amount"],
             extra_assertions=[f"|amount - shares x price| <= $0.01 for all {int(div.sum()):,} dividends"])


def compute_positions(T):
    """Rebuild Buy/Sell share positions exactly as 01_profile.py x_positions() does."""
    tx, cl = T["fact_transactions"], T["dim_client"]
    t = tx[tx["txn_type"].isin(TRADE_TYPES)].copy()
    is_sell = t["txn_type"].eq("Sell")
    t["_signed"] = np.where(is_sell, -1, 1) * to_num(t["shares"])
    check(t["_signed"].notna().all(), "Every Buy/Sell must have numeric shares")
    t["_d"], t["_id"] = to_date(t["txn_date"]), to_num(t["txn_id"])
    t["_ord"] = is_sell.astype(int)          # same-day Buys applied before Sells
    t = t.sort_values(["client_id", "security_id", "_d", "_ord", "_id"], kind="mergesort")
    t["_pos"] = t.groupby(["client_id", "security_id"])["_signed"].cumsum()
    t["_prev"] = t["_pos"] - t["_signed"]
    sell = t["txn_type"].eq("Sell")
    first_oversell = sell & (t["_prev"] >= -EPS) & (t["_pos"] < -EPS)
    on_negative = t["_prev"] < -EPS
    since = to_date(cl.set_index("client_id")["client_since"])
    window_start = to_date(tx["txn_date"]).min()
    in_window = t["client_id"].map(since) >= window_start
    return t, first_oversell, on_negative, in_window, since, window_start


def step_positions(audit, T):
    tx = T["fact_transactions"]
    t, first, neg, inw, since, window_start = compute_positions(T)
    check(not (first & neg).any(), "first-oversell and on-negative-position rows must not overlap")
    cols = ["client_id", "security_id", "txn_date", "txn_type", "shares"]
    common = dict(decision="Decision 2 - negative reconstructed holdings",
                  audit_ids="DQ0002, DQ0020, DQ0056, DQ0057", evidence_cols=cols)
    win = f"client_since >= {window_start.date()} (first transaction date)"
    add_flag(audit, tx, "fact_transactions", "flag_oversell_first_in_window", full_mask(tx, first & inw),
             classification="Confirmed Error",
             rule=f"Sell takes the client-security position below zero for the first time; in-window client ({win})",
             **common)
    add_flag(audit, tx, "fact_transactions", "flag_txn_on_negative_position_in_window", full_mask(tx, neg & inw),
             classification="Suspicious",
             rule=f"Buy/Sell on a position that is already negative; in-window client ({win})", **common)
    add_flag(audit, tx, "fact_transactions", "flag_oversell_first_pre_window", full_mask(tx, first & ~inw),
             classification="Informational",
             rule="Sell takes the position below zero for the first time; client onboarded before the window "
                  "(earlier purchases may predate the extract)", **common)
    add_flag(audit, tx, "fact_transactions", "flag_txn_on_negative_position_pre_window",
             full_mask(tx, neg & ~inw), classification="Informational",
             rule="Buy/Sell on an already-negative position; client onboarded before the window", **common)
    pos_flags = ["flag_oversell_first_in_window", "flag_txn_on_negative_position_in_window",
                 "flag_oversell_first_pre_window", "flag_txn_on_negative_position_pre_window"]
    check(tx[pos_flags].sum(axis=1).max() <= 1, "a transaction may carry at most one position flag")
    check(tx.loc[~tx["txn_type"].isin(TRADE_TYPES), pos_flags].sum().sum() == 0,
          "position flags may only be set on Buy/Sell rows")
    return t, since, window_start


def step_dividend_without_purchase(audit, T, t, since, window_start):
    tx = T["fact_transactions"]
    dv = tx[tx["txn_type"].eq("Dividend")]
    buys = t[t["txn_type"].eq("Buy")].groupby(["client_id", "security_id"])["_d"].min()
    j = dv[["client_id", "security_id"]].merge(buys.rename("_first_buy").reset_index(),
                                                on=["client_id", "security_id"], how="left")
    first_buy = pd.Series(j["_first_buy"].values, index=dv.index)
    no_pos = first_buy.isna() | (to_date(dv["txn_date"]) < first_buy)
    no_pos &= dv["client_id"].map(since) >= window_start
    never = int((first_buy.isna() & no_pos).sum())
    check(never == 2_734, f"Decision 12 evidence: expected 2,734 dividends on never-bought securities, got {never:,}")
    add_flag(audit, tx, "fact_transactions", "flag_dividend_without_prior_purchase", full_mask(tx, no_pos),
             decision="Decision 12 - dividends without prior purchases", audit_ids="DQ0019",
             classification="Suspicious",
             rule="Dividend dated before the client's first Buy of that security (or no Buy at all); "
                  "in-window clients only",
             evidence_cols=["client_id", "security_id", "txn_date", "txn_type"],
             extra_assertions=[f"{never:,} of the flagged dividends are on securities never bought (audit: 2,734)"])


def step_weekend(audit, T):
    tx, dd = T["fact_transactions"], T["dim_date"]
    dow = tx["txn_date"].map(dict(zip(dd["date"], dd["day_of_week"])))
    trading = tx["txn_date"].map(dict(zip(dd["date"], dd["is_trading_day"])))
    check(dow.notna().all(), "every txn_date must exist in dim_date.date")
    weekend = dow.isin(WEEKEND_DAYS)
    check(weekend.equals(trading.eq("0")),
          "weekend dates and is_trading_day = 0 no longer coincide - review Decision 14 definition")
    mask = weekend & tx["txn_type"].isin(TRADE_TYPES + ["Dividend"])
    n_trades = int((mask & tx["txn_type"].isin(TRADE_TYPES)).sum())
    n_div = int((mask & tx["txn_type"].eq("Dividend")).sum())
    check(n_trades == 40_630 and n_div == 15_487,
          f"Decision 14 evidence: expected 40,630 weekend trades and 15,487 weekend dividends, got {n_trades:,}/{n_div:,}")
    n_cash = int((weekend & tx["txn_type"].isin(CASH_TXN_TYPES)).sum())
    check(n_cash == 28_829, f"Decision 14 evidence: expected 28,829 weekend cash movements/fees, got {n_cash:,}")
    add_flag(audit, tx, "fact_transactions", "flag_weekend_trade_or_dividend", mask,
             decision="Decision 14 - weekend-dated trades and dividends", audit_ids="DQ0022, DQ0023",
             classification="Suspicious",
             rule="txn_type in (Buy, Sell, Dividend) and dim_date.day_of_week in (Saturday, Sunday); "
                  "dates preserved; weekend cash movements/fees not flagged (Informational, DQ0058)",
             evidence_cols=["txn_date", "txn_type"],
             extra_assertions=[f"{n_trades:,} trades + {n_div:,} dividends",
                               "Saturday/Sunday = is_trading_day 0 for every transaction date",
                               f"{n_cash:,} weekend cash movements/fees left unflagged (audit: 28,829)"])


def step_snapshot(audit, T):
    snap = T["fact_portfolio_snapshot"]
    r = to_num(snap["return_ytd"])
    check(r.notna().all(), "return_ytd must be numeric on every row")
    a = to_num(snap["active_alpha_ytd"])
    flagged = snap["is_return_suspect"].eq("1")
    extreme = (r < RETURN_LOW) | (r > RETURN_HIGH)
    unflagged = int((extreme & ~flagged).sum())
    check(flagged.equals(a.abs() > 0.5), "Decision 3 evidence: is_return_suspect no longer equals |alpha| > 0.5")
    check(unflagged == 249, f"Decision 3 evidence: expected 249 extreme returns not flagged by is_return_suspect, got {unflagged}")
    add_flag(audit, snap, "fact_portfolio_snapshot", "flag_extreme_return", extreme,
             decision="Decision 3 - extreme returns", audit_ids="DQ0009, DQ0013",
             classification="Suspicious",
             rule=f"return_ytd < {RETURN_LOW} or return_ytd > {RETURN_HIGH} (assumed thresholds); "
                  "return_ytd, active_alpha_ytd and is_return_suspect preserved",
             evidence_cols=["client_id", "snapshot_date", "return_ytd", "is_return_suspect"],
             extra_assertions=["is_return_suspect = (|active_alpha_ytd| > 0.5) on every row (unchanged)",
                               f"{unflagged} extreme returns are not covered by is_return_suspect (audit: 249)"])

    aum = to_num(snap["aum"])
    check(aum.notna().all(), "aum must be numeric on every row")
    add_flag(audit, snap, "fact_portfolio_snapshot", "flag_aum_equals_1000", aum.eq(AUM_PLACEHOLDER),
             decision="Decision 5 - suspicious AUM values", audit_ids="DQ0008",
             classification="Suspicious", rule="aum = 1000 (kept; not replaced with blank)",
             evidence_cols=["client_id", "snapshot_date", "aum"])

    d = to_date(snap["snapshot_date"])
    s = snap.assign(_d=d, _aum=aum).sort_values(["client_id", "_d"], kind="mergesort")
    check(not s.duplicated(["client_id", "_d"]).any(), "one snapshot per client per month expected")
    prev = s.groupby("client_id")["_aum"].shift()
    ratio = s["_aum"] / prev
    jump = full_mask(snap, (ratio > AUM_JUMP_UP) | (ratio < AUM_JUMP_DOWN))
    add_flag(audit, snap, "fact_portfolio_snapshot", "flag_aum_mom_jump", jump,
             decision="Decision 5 - suspicious AUM changes", audit_ids="DQ0012",
             classification="Suspicious",
             rule="aum / client's previous-month aum > 3 or < 1/3 (flag on the later month; first month never flagged)",
             evidence_cols=["client_id", "snapshot_date", "aum"])


def step_fees(audit, T):
    fee = T["fact_fees"]
    basis, bps = to_num(fee["aum_basis"]), to_num(fee["bps_charged"])
    check(basis.notna().all() and bps.notna().all(), "aum_basis and bps_charged must be numeric")
    add_flag(audit, fee, "fact_fees", "flag_aum_basis_equals_1000", basis.eq(AUM_PLACEHOLDER),
             decision="Decision 5 - fees on suspicious AUM", audit_ids="DQ0005, DQ0016",
             classification="Suspicious", rule="aum_basis = 1000 (fee amount kept)",
             evidence_cols=["client_id", "fee_date", "aum_basis", "amount"])
    add_flag(audit, fee, "fact_fees", "flag_bps_above_100", bps.gt(BPS_CAP),
             decision="Decision 6 - fee rate outlier", audit_ids="DQ0006",
             classification="Suspicious",
             rule="bps_charged > 100 (assumed cap; no fee schedule; inferred tier/fee rules NOT applied)",
             evidence_cols=["client_id", "fee_date", "bps_charged"])


def step_client(audit, T):
    cl, snap = T["dim_client"], T["fact_portfolio_snapshot"]

    unknown = cl["risk_profile"].eq("Unknown")
    ids = set(cl.loc[unknown, "client_id"])
    s_unknown = snap["client_id"].isin(ids)
    bms = sorted(snap.loc[s_unknown, "benchmark_id"].unique())
    check(int(s_unknown.sum()) == 3_942 and bms == ["3"],
          f"Decision 7 evidence: expected 3,942 snapshots all on benchmark 3, got {int(s_unknown.sum()):,} on {bms}")
    add_flag(audit, cl, "dim_client", "flag_risk_profile_unknown", unknown,
             decision="Decision 7 - unknown risk profiles", audit_ids="DQ0004, DQ0017",
             classification="Suspicious",
             rule="risk_profile = 'Unknown' (category and benchmark assignments preserved)",
             evidence_cols=["risk_profile", "client_tier"],
             extra_assertions=["these clients' 3,942 snapshots all use benchmark_id 3 (unchanged)"])

    z, st = cl["zip_code"], cl["state"]
    check(z.str.fullmatch(r"\d{5}").all(), "every zip_code must be 5-digit text")
    lead0 = int(z.str.startswith("0").sum())
    check(lead0 == 228, f"Decision 8: expected 228 leading-zero ZIP codes preserved, found {lead0}")
    known = st.isin(set().union(*ZIP_FIRST_DIGIT.values()))
    expected_states = z.str[0].map(ZIP_FIRST_DIGIT)
    mism = known & pd.Series([s not in e for s, e in zip(st, expected_states)], index=cl.index)
    add_flag(audit, cl, "dim_client", "flag_zip_state_mismatch", mism,
             decision="Decision 8 - ZIP/state mismatches", audit_ids="DQ0010, DQ0031",
             classification="Suspicious",
             rule="first digit of zip_code does not belong to state (USPS national areas); ZIP kept as text",
             evidence_cols=["state", "zip_code"],
             extra_assertions=[f"{lead0} leading-zero ZIP codes still present as text"])

    s = snap.assign(_d=to_date(snap["snapshot_date"]), _a=to_num(snap["aum"])).sort_values("_d", kind="mergesort")
    first = s.groupby("client_id").first()
    window_start = s["_d"].min()
    c = cl.set_index("client_id")
    j = first[["_a"]].join(c[["initial_aum", "client_since"]], how="inner")
    ratio = j["_a"] / to_num(j["initial_aum"])
    onboard = to_date(j["client_since"]) >= window_start
    bad_ids = set(j.index[onboard & ((ratio < 0.5) | (ratio > 2))])
    add_flag(audit, cl, "dim_client", "flag_initial_aum_mismatch", cl["client_id"].isin(bad_ids),
             decision="Decision 15 - initial AUM discrepancy", audit_ids="DQ0015",
             classification="Suspicious",
             rule=f"client onboarded on/after {window_start.date()} whose first snapshot aum is < 0.5x or > 2x "
                  "initial_aum (both amounts preserved)",
             evidence_cols=["client_since", "initial_aum"],
             extra_assertions=[f"flagged client_id(s): {', '.join(sorted(bad_ids))}"])


def step_fixed_decimals(audit, T):
    """Decision 16: scientific notation -> fixed 6-decimal text, proven exact with Decimal."""
    snap = T["fact_portfolio_snapshot"]
    pk = PRIMARY_KEYS["fact_portfolio_snapshot"]
    for col in ["return_ytd", "active_alpha_ytd"]:
        orig = snap[col].copy()
        try:
            dec = orig.map(Decimal)
        except InvalidOperation as exc:
            raise CleaningError(f"{col}: a value is not a valid number ({exc})") from exc
        places = dec.map(lambda d: max(0, -d.normalize().as_tuple().exponent))
        check(places.max() <= FIXED_DECIMALS,
              f"{col}: {int((places > FIXED_DECIMALS).sum())} values need more than {FIXED_DECIMALS} decimals; "
              "fixed formatting would round them - stop (Decision 16 guard)")
        cleaned = dec.map(lambda d: format(d, f".{FIXED_DECIMALS}f"))
        check((cleaned.map(Decimal) == dec).all(), f"{col}: a formatted value differs from the original")
        check((cleaned.astype(float) == orig.astype(float)).all(), f"{col}: float check failed")
        check(cleaned.str.fullmatch(r"-?\d+\.\d{6}").all(), f"{col}: not every value is fixed 6-decimal")
        changed = cleaned.ne(orig)
        sci = orig.str.contains(r"[eE]")
        snap[col] = cleaned
        ex = list(snap.index[changed & sci][:2]) + list(snap.index[changed & ~sci][:1])
        step_id = audit.step(
            "Decision 16 - scientific notation", "DQ0037, DQ0038",
            "fact_portfolio_snapshot", col, "reformat to fixed 6-decimal text (value unchanged)",
            "Informational", f"Decimal(value) formatted with {FIXED_DECIMALS} decimals, e.g. 2e-05 -> 0.000020",
            int(changed.sum()), len(snap),
            [f"{pk}={snap.at[i, pk]}: {col}={orig[i]!r}" for i in ex],
            [f"{pk}={snap.at[i, pk]}: {col}={cleaned[i]!r}" for i in ex],
            [f"max decimals needed = {int(places.max())} <= {FIXED_DECIMALS}",
             "Decimal(cleaned) == Decimal(original) for every row",
             f"{int(sci.sum()):,} scientific-notation values now fixed notation",
             f"{int((changed & ~sci).sum()):,} other values padded to 6 decimals",
             "every value matches -?d+.dddddd"])
        for i in snap.index[changed]:
            audit.change("fact_portfolio_snapshot", pk, snap.at[i, pk], col, orig[i], cleaned[i],
                         step_id, "Decision 16")


def step_preserve(audit, T):
    """Decisions whose outcome is 'preserve': verify the property and log 0 changes."""
    adv, sec, tx, cl, fee, dd = (T["dim_advisor"], T["dim_security"], T["fact_transactions"],
                                 T["dim_client"], T["fact_fees"], T["dim_date"])

    # Decision 4 - fee reconciliation (both sources kept, nothing combined)
    af_total = to_num(tx.loc[tx["txn_type"].eq("Advisory Fee"), "amount"]).sum()
    fee_total = to_num(fee["amount"]).sum()
    audit.step("Decision 4 - fee reconciliation", "DQ0024", "fact_transactions / fact_fees", "amount",
               "preserve both sources (no flag, no merge)", "Suspicious",
               "neither table is the confirmed system of record; reports must name the table used", 0, len(fee),
               f"Advisory Fee txns ${af_total:,.0f}; fact_fees ${fee_total:,.0f}", "unchanged",
               ["both amount columns unchanged (verified in final integrity check)"])

    # Decision 9 - possible duplicates: nothing merged
    audit.step("Decision 9 - possible duplicate entities", "DQ0044, DQ0045", "dim_client / dim_security",
               "first_name, last_name / name", "preserve (no merge)", "Informational",
               "shared names alone are not evidence of duplicates", 0, len(cl) + len(sec),
               "50 client name groups; 3 security name groups", "all records kept",
               [f"dim_client rows {len(cl):,}, dim_security rows {len(sec):,} unchanged"])

    # Decision 10 - structural blanks and the literal 'None'
    none_cert = int(adv["certifications"].eq("None").sum())
    etf = sec["is_etf"].eq("True")
    blank_sector, blank_exp = sec["sector"].eq(""), sec["expense_ratio_bps"].eq("")
    cash = tx["txn_type"].isin(CASH_TXN_TYPES)
    sec_blank = tx[["security_id", "shares", "price"]].eq("").all(axis=1)
    check(none_cert == 6, f"expected 6 'None' certifications, found {none_cert}")
    check(blank_sector.equals(etf) and int(blank_sector.sum()) == 219, "sector blank must equal the 219 ETFs")
    check(blank_exp.equals(~etf) and int(blank_exp.sum()) == 281, "expense_ratio_bps blank must equal the 281 non-ETFs")
    check(sec_blank.equals(cash) and int(cash.sum()) == 101_528, "security_id/shares/price blank must equal cash txns")
    audit.step("Decision 10 - structural blanks", "DQ0025, DQ0033, DQ0034, DQ0039-DQ0041, DQ0046, DQ0140, DQ0141",
               "dim_advisor / dim_security / fact_transactions",
               "certifications, sector, expense_ratio_bps, security_id, shares, price",
               "preserve blanks and 'None' text (no imputation)", "Structural/Legitimate",
               "read with dtype=str, keep_default_na=False; written back as-is", 0, len(tx),
               "certifications='None' x6; sector='' x219; expense_ratio_bps='' x281; cash txn security_id/shares/price=''",
               "identical in clean output",
               [f"{none_cert} 'None' certifications kept as text", "219 blank sectors = 219 ETFs",
                "281 blank expense ratios = 281 non-ETFs", "101,528 cash txns have blank security_id/shares/price"])

    # Decision 11 - missing fee records: deferred to validation reports (Part D)
    audit.step("Decision 11 - missing fee records", "DQ0018", "fact_fees", "fee_date",
               "preserve (no artificial fees)", "Suspicious",
               "unmatched client-months are reported in validation (Part D), not created or flagged here",
               0, len(fee), "7,350 snapshot client-months without a fee", "no rows added",
               [f"fact_fees row count unchanged ({len(fee):,})"])

    # Decision 13 - post-departure transactions: existing flag preserved
    post = int(tx["is_post_departure_txn"].eq("1").sum())
    check(post == 2_410, f"expected 2,410 is_post_departure_txn = 1, found {post:,}")
    audit.step("Decision 13 - transactions after last snapshot", "DQ0021, DQ0170", "fact_transactions",
               "is_post_departure_txn", "preserve existing flag and rows", "Suspicious",
               "exclude is_post_departure_txn = 1 from active-client metrics; report separately", 0, len(tx),
               f"is_post_departure_txn=1 on {post:,} rows", "unchanged",
               [f"{post:,} rows still flagged by the source flag"])

    # Decision 14 - holiday calendar unchanged
    hol = dd["date"].str[5:].isin(["01-01", "07-04", "12-25"]) & ~dd["day_of_week"].isin(WEEKEND_DAYS)
    n_hol = int((hol & dd["is_trading_day"].eq("1")).sum())
    check(n_hol == 9, f"expected 9 weekday holidays marked as trading days, found {n_hol}")
    audit.step("Decision 14 - holiday calendar", "DQ0011", "dim_date", "is_trading_day",
               "preserve calendar as-is", "Suspicious",
               "leave unchanged until the intended trading-day definition is verified", 0, len(dd),
               f"{n_hol} weekday market holidays have is_trading_day=1", "unchanged",
               [f"{n_hol} holiday rows still is_trading_day=1"])

    # Decision 17 - sign convention
    amt = to_num(tx["amount"])
    check((amt > 0).all(), "Decision 17 evidence: all transaction amounts should be positive")
    audit.step("Decision 17 - sign convention", "DQ0048", "fact_transactions", "amount",
               "preserve positive amounts; direction documented by txn_type", "Informational",
               "inflow: Deposit, Sell, Dividend; outflow: Withdrawal, Buy, Advisory Fee (apply sign in BI)",
               0, len(tx), "all amounts > 0", "unchanged", ["every amount > 0 (unchanged)"])

    # Decision 18 - categorical values
    audit.step("Decision 18 - observed categorical values", "DQ0026-DQ0030, DQ0032, DQ0035, DQ0036, DQ0042, DQ0053",
               "all", "categorical columns", "preserve (no recoding)", "Informational",
               "observed value sets are not official definitions", 0, sum(len(d) for d in T.values()),
               "categories as in raw", "unchanged", ["verified in final integrity check"])


# =============================================================================
# 8. FINAL CHECKS + OUTPUTS
# =============================================================================
def integrity_checks(audit, T, raw_copies):
    print("\n" + "=" * 78 + "\nFINAL INTEGRITY CHECKS\n" + "=" * 78)
    changes = pd.DataFrame(audit.change_rows,
                           columns=["table", "row_id_column", "row_id", "column", "original_value",
                                    "cleaned_value", "step_id", "decision"])
    for table, df in T.items():
        raw = raw_copies[table]
        cols = EXPECTED_COLUMNS[table]
        flags = [c for c in df.columns if c not in cols]
        check(list(df.columns) == cols + flags, f"{table}: original columns must come first, in raw order")
        check(all(f.startswith("flag_") for f in flags), f"{table}: only flag_* columns may be added")
        for f in flags:
            check(set(df[f].unique()) <= {0, 1}, f"{table}.{f}: must be 0/1")
        check(len(df) == len(raw) and df.index.equals(raw.index), f"{table}: row count/order changed")
        check(df[PRIMARY_KEYS[table]].equals(raw[PRIMARY_KEYS[table]]), f"{table}: primary keys changed")
        diff = df[cols].ne(raw[cols])
        n_diff = int(diff.to_numpy().sum())
        logged = changes[changes["table"] == table]
        check(n_diff == len(logged), f"{table}: {n_diff} changed cells but {len(logged)} logged in value_changes")
        if n_diff:
            changed_cols = set(diff.columns[diff.any()])
            check(changed_cols <= {"return_ytd", "active_alpha_ytd"},
                  f"{table}: undocumented columns changed: {changed_cols}")
        print(f"  {table:<25} rows {len(df):>8,} = raw {len(raw):>8,} | changed cells {n_diff:>6,} "
              f"(all logged) | flags added {len(flags)}")


def write_outputs(audit, T, sources, raw_copies):
    for d in (CLEAN_DIR, AUDIT_DIR):
        d.mkdir(parents=True, exist_ok=True)

    recon = []
    for table, df in T.items():
        out = safe_output(CLEAN_DIR / f"{table}.csv")
        df.to_csv(out, index=False, encoding="utf-8", lineterminator="\n")
        back = pd.read_csv(out, dtype=str, keep_default_na=False, na_filter=False)
        check(back.equals(df.astype(str)), f"{out.name}: file on disk does not round-trip exactly")
        quarantined = 0  # no decision in decision_log.md removes rows
        raw_n = len(raw_copies[table])
        check(raw_n == len(back) + quarantined, f"{table}: raw rows != clean rows + quarantined rows")
        recon.append({"table": table, "raw_file": sources[table].name, "clean_file": out.name,
                      "raw_rows": raw_n, "clean_rows": len(back), "quarantined_rows": quarantined,
                      "check": "raw_rows = clean_rows + quarantined_rows", "status": "PASS"})
    total_raw = sum(r["raw_rows"] for r in recon)
    total_clean = sum(r["clean_rows"] for r in recon)
    audit.step("Row reconciliation", "-", "all", "(rows)", "no rows removed; nothing quarantined",
               "-", "raw rows = clean rows + quarantined rows (per table)", 0, total_raw,
               f"raw rows {total_raw:,}", f"clean rows {total_clean:,}; quarantined 0",
               ["holds for every table", "written CSVs re-read as text equal the in-memory tables"])

    pd.DataFrame(recon).to_csv(safe_output(AUDIT_DIR / "row_reconciliation.csv"), index=False, lineterminator="\n")
    pd.DataFrame(audit.change_rows,
                 columns=["table", "row_id_column", "row_id", "column", "original_value", "cleaned_value",
                          "step_id", "decision"]).to_csv(
        safe_output(AUDIT_DIR / "value_changes.csv"), index=False, lineterminator="\n")
    pd.DataFrame(audit.log_rows).to_csv(safe_output(AUDIT_DIR / "cleaning_log.csv"), index=False,
                                        lineterminator="\n")
    return recon


# =============================================================================
# 9. README_clean.md
# =============================================================================
BI_TYPES = {
    "dim_advisor": {"advisor_id": "Whole number (PK)", "first_name": "Text", "last_name": "Text",
                    "region": "Text", "years_experience": "Whole number", "aum_target_mm": "Whole number ($ millions)",
                    "certifications": "Text ('None' is a real value, not NULL)"},
    "dim_benchmark": {"benchmark_id": "Whole number (PK)", "name": "Text", "asset_class": "Text",
                      "ticker_proxy": "Text"},
    "dim_client": {"client_id": "Whole number (PK)", "first_name": "Text", "last_name": "Text",
                   "age": "Whole number", "city": "Text", "state": "Text", "zip_code": "TEXT (leading zeros)",
                   "risk_profile": "Text", "client_tier": "Text", "primary_account_type": "Text ('529' is text)",
                   "initial_aum": "Fixed decimal (currency)", "client_since": "Date", "advisor_id": "Whole number (FK)",
                   "is_active": "True/False (text; cast to Boolean)"},
    "dim_date": {"date_id": "Whole number (not used by facts)", "date": "Date (join key)", "year": "Whole number",
                 "quarter": "Whole number", "month": "Whole number", "month_name": "Text",
                 "week_of_year": "Whole number", "day_of_week": "Text", "is_month_end": "0/1",
                 "is_quarter_end": "0/1", "is_year_end": "0/1", "is_trading_day": "0/1 (see limitations)"},
    "dim_security": {"security_id": "Whole number (PK)", "ticker": "Text", "name": "Text", "asset_class": "Text",
                     "sector": "Text (blank for ETFs)", "benchmark_id": "Whole number (FK)",
                     "is_etf": "True/False (text; cast to Boolean)", "expense_ratio_bps": "Decimal (blank for non-ETFs)"},
    "fact_fees": {"fee_id": "Whole number (PK)", "client_id": "Whole number (FK)", "advisor_id": "Whole number (FK)",
                  "fee_date": "Date", "fee_type": "Text", "aum_basis": "Fixed decimal (currency)",
                  "bps_charged": "Decimal", "amount": "Fixed decimal (currency)"},
    "fact_portfolio_snapshot": {"snapshot_id": "Whole number (PK)", "client_id": "Whole number (FK)",
                                "advisor_id": "Whole number (FK)", "benchmark_id": "Whole number (FK)",
                                "snapshot_date": "Date", "year": "Whole number", "month": "Whole number",
                                "aum": "Fixed decimal (currency)", "return_ytd": "Decimal (6 dp)",
                                "benchmark_return_ytd": "Decimal (6 dp)", "active_alpha_ytd": "Decimal (6 dp)",
                                "cash_pct": "Decimal", "account_type": "Text ('529' is text)",
                                "is_return_suspect": "0/1 (source flag)"},
    "fact_transactions": {"txn_id": "Whole number (PK)", "client_id": "Whole number (FK)",
                          "advisor_id": "Whole number (FK)", "security_id": "Whole number (FK; blank for cash txns)",
                          "txn_date": "Date", "txn_type": "Text", "shares": "Decimal (blank for cash txns)",
                          "price": "Decimal (blank for cash txns)", "amount": "Fixed decimal (currency, always positive)",
                          "is_post_departure_txn": "0/1 (source flag)"},
}

FLAG_DOCS = [
    ("fact_transactions", "flag_dividend_amount_miscalculated", "1", "Confirmed Error",
     "Dividend amount = shares x price (full position value). Exclude from dividend income and returns."),
    ("fact_transactions", "flag_oversell_first_in_window", "2", "Confirmed Error",
     "First Sell that takes an in-window client's position below zero."),
    ("fact_transactions", "flag_txn_on_negative_position_in_window", "2", "Suspicious",
     "Later Buy/Sell on an already-negative position (consequence of the first oversell)."),
    ("fact_transactions", "flag_oversell_first_pre_window", "2", "Informational",
     "First oversell for a client onboarded before the window (purchases may predate the extract)."),
    ("fact_transactions", "flag_txn_on_negative_position_pre_window", "2", "Informational",
     "Later Buy/Sell on a negative position, pre-window client."),
    ("fact_transactions", "flag_dividend_without_prior_purchase", "12", "Suspicious",
     "Dividend before the client's first Buy of the security (or none), in-window clients."),
    ("fact_transactions", "flag_weekend_trade_or_dividend", "14", "Suspicious",
     "Buy/Sell/Dividend dated Saturday or Sunday."),
    ("fact_portfolio_snapshot", "flag_extreme_return", "3", "Suspicious",
     "return_ytd < -0.5 or > 1.0 (assumed thresholds)."),
    ("fact_portfolio_snapshot", "flag_aum_equals_1000", "5", "Suspicious", "aum = 1000."),
    ("fact_portfolio_snapshot", "flag_aum_mom_jump", "5", "Suspicious",
     "aum > 3x or < 1/3 of the client's previous month."),
    ("fact_fees", "flag_aum_basis_equals_1000", "5", "Suspicious", "Fee calculated on aum_basis = 1000."),
    ("fact_fees", "flag_bps_above_100", "6", "Suspicious", "bps_charged > 100 (assumed cap)."),
    ("dim_client", "flag_risk_profile_unknown", "7", "Suspicious", "risk_profile = 'Unknown'."),
    ("dim_client", "flag_zip_state_mismatch", "8", "Suspicious", "ZIP first digit does not belong to the state."),
    ("dim_client", "flag_initial_aum_mismatch", "15", "Suspicious",
     "In-window client whose first snapshot AUM is < 0.5x or > 2x initial_aum."),
]


def max_decimals(s):
    dec = s[s.ne("")].str.extract(r"\.(\d+)$")[0].str.len()
    return int(dec.max()) if dec.notna().any() else 0


def write_readme(T, sources, recon, audit):
    L = []
    w = L.append
    w("# Cleaned Wildcat Capital Data (HW4 Part C)\n")
    w("Generated by `scripts/02_clean.py` from `data/raw/` following `decision_log.md`. "
      "Do not edit by hand - rerun the script instead.\n")
    w("## Files\n")
    w("| Clean file | Raw source | Raw rows | Clean rows | Quarantined | Flags added |")
    w("|---|---|---:|---:|---:|---:|")
    for r in recon:
        nflags = len([c for c in T[r['table']].columns if c.startswith("flag_")])
        w(f"| `{r['clean_file']}` | `{r['raw_file']}` | {r['raw_rows']:,} | {r['clean_rows']:,} | "
          f"{r['quarantined_rows']} | {nflags} |")
    w("\nNo rows were removed, so nothing was quarantined (raw rows = clean rows + 0 for every table). "
      "The raw files were not modified (SHA-256 verified before and after the run). "
      "`fact_transactions (1).csv` is written as `fact_transactions.csv` (Decision 17).\n")
    w("Audit trail: `data/audit/cleaning_log.csv` (every step, row counts, before/after examples, assertions), "
      f"`data/audit/value_changes.csv` ({len(audit.change_rows):,} changed cells), "
      "`data/audit/row_reconciliation.csv`.\n")

    w("## What changed\n")
    w("* **Values:** only `fact_portfolio_snapshot.return_ytd` and `active_alpha_ytd` were rewritten, from mixed "
      "scientific/variable decimals to fixed six-decimal text (e.g. `2e-05` -> `0.000020`). Every cell was "
      "checked with exact Decimal comparison; no value changed (Decision 16).")
    w("* **Flags:** 0/1 columns named `flag_*` were appended after the original columns. 1 = the row meets the "
      "rule; the business values on that row are unchanged.")
    w("* **Everything else** (amounts, shares, AUM, rates, categories, dates, blanks, `None`, ZIP codes) is "
      "identical to the raw text.\n")

    w("## Relationships (star schema)\n")
    w("| From (many) | To (one) | Notes |")
    w("|---|---|---|")
    rels = [
        ("fact_transactions.client_id", "dim_client.client_id", ""),
        ("fact_transactions.advisor_id", "dim_advisor.advisor_id", ""),
        ("fact_transactions.security_id", "dim_security.security_id",
         "blank for Deposit / Withdrawal / Advisory Fee (101,528 rows) - keep as blank, not 0"),
        ("fact_transactions.txn_date", "dim_date.date", "join on the date string, NOT date_id"),
        ("fact_portfolio_snapshot.client_id", "dim_client.client_id", ""),
        ("fact_portfolio_snapshot.advisor_id", "dim_advisor.advisor_id", ""),
        ("fact_portfolio_snapshot.benchmark_id", "dim_benchmark.benchmark_id", ""),
        ("fact_portfolio_snapshot.snapshot_date", "dim_date.date", "monthly grain, dated the 1st"),
        ("fact_fees.client_id", "dim_client.client_id", ""),
        ("fact_fees.advisor_id", "dim_advisor.advisor_id", ""),
        ("fact_fees.fee_date", "dim_date.date", "monthly grain, dated the 1st"),
        ("dim_client.advisor_id", "dim_advisor.advisor_id", "snowflake: client -> advisor"),
        ("dim_security.benchmark_id", "dim_benchmark.benchmark_id", "snowflake: security -> benchmark"),
    ]
    for a, b, n in rels:
        w(f"| `{a}` | `{b}` | {n} |")
    w("\nAll 13 relationships passed referential-integrity checks in Part A. In Power BI, only one relationship "
      "between two tables can be active: with several advisor/date paths, keep the fact-to-dimension links active "
      "and make the snowflake links (client -> advisor) inactive or use them with USERELATIONSHIP. Unused "
      "dimension members (benchmark 6, date 2024-12-31) were kept.\n")

    w("## Transaction sign convention\n")
    w("All `amount` values are positive. Direction comes from `txn_type`, and depends on the perspective:\n")
    w("* **Cash-balance perspective** (cash moving in or out of the account's cash balance): inflow = Deposit, "
      "Sell, Dividend; outflow = Withdrawal, Buy, Advisory Fee. Buy/Sell/Dividend directions apply only here - "
      "a Buy or Sell moves value between cash and securities inside the account.")
    w("* **Net external flows** (money entering or leaving the client's account, e.g. for AUM change analysis): "
      "count only Deposit (+) and Withdrawal (-). Buy, Sell, Dividend and Advisory Fee are not external flows.\n")
    w("Neither perspective is documented by the source; this is the Part C interpretation of DQ0048. Apply the "
      "sign in a measure before netting. Exclude `flag_dividend_amount_miscalculated = 1` from any dividend or "
      "income total.\n")

    w("## BI data types\n")
    w("Set these explicitly in Power Query / the BI tool; do not rely on auto-detect (it drops ZIP leading zeros "
      "and may treat `None` as null).\n")
    for table, types in BI_TYPES.items():
        df = T[table]
        w(f"### {table}\n")
        w("| Column | BI type | Max decimals observed |")
        w("|---|---|---:|")
        for c in EXPECTED_COLUMNS[table]:
            md = max_decimals(df[c]) if "decimal" in types[c].lower() or "currency" in types[c].lower() else ""
            w(f"| `{c}` | {types[c]} | {md} |")
        for f in [c for c in df.columns if c.startswith("flag_")]:
            w(f"| `{f}` | Whole number 0/1 (cleaning flag) | |")
        w("")

    w("## Flags added\n")
    w("| Table | Flag | Decision | Classification | Rows = 1 | Meaning |")
    w("|---|---|---:|---|---:|---|")
    for table, flag, dec, cls, meaning in FLAG_DOCS:
        w(f"| {table} | `{flag}` | {dec} | {cls} | {int(T[table][flag].sum()):,} | {meaning} |")
    w("\nThe four position flags are mutually exclusive. Source flags `is_return_suspect` (|alpha| > 0.5) and "
      "`is_post_departure_txn` were kept unchanged; exclude `is_post_departure_txn = 1` from active-client metrics "
      "(Decision 13).\n")

    w("## Remaining limitations\n")
    lims = [
        "**Dividend income cannot be reported** - all 53,826 dividend amounts are miscalculated and the correct "
        "payouts are not in the data (Decision 1).",
        "**Holdings cannot be rebuilt from transactions** - use `fact_portfolio_snapshot` for positions/AUM (Decision 2).",
        "**Returns appear to include deposits/withdrawals** (not time-weighted); no methodology is documented. The "
        "-0.5/1.0 extreme-return thresholds are assumptions (Decision 3).",
        "**Fee revenue is not reconciled** - Advisory Fee transactions (~$263.7M) and `fact_fees` (~$204.7M) "
        "disagree; never add them together and always name the source table (Decision 4).",
        "**AUM = 1000 and large month-over-month jumps are flagged, not corrected**; fees on those balances may be "
        "wrong (Decision 5).",
        "**Fee rate cap (100 bps) and tier bands are inferred**, not an official schedule (Decision 6).",
        "**75 clients have risk_profile 'Unknown'** and default to benchmark 3; their alpha is unreliable (Decision 7).",
        "**ZIP/state mismatches affect 89% of clients**; do not use geography without a reference check (Decision 8).",
        "**Possible duplicate clients/securities** (shared names) were not merged (Decision 9).",
        "**7,350 snapshot client-months have no fee record**; these are reported in validation (Part D), not "
        "created or flagged here (Decision 11).",
        "**Departure is a proxy** (last snapshot), not a recorded date (Decision 13).",
        "**`dim_date.is_trading_day` behaves like 'weekday'** - 9 market holidays are marked as trading days and "
        "were left unchanged; trades on those holidays are not flagged (Decision 14).",
        "**Categories are observed, not verified** against official definitions (Decision 18).",
        "Numeric columns other than `return_ytd`/`active_alpha_ytd` keep their raw text (e.g. `426254.0`); they "
        "parse to the same numbers, but are not padded to a fixed number of decimals.",
    ]
    for item in lims:
        w(f"* {item}")
    w("\n## Ambiguous decisions (interpretations made in Part C)\n")
    amb = [
        "Decision 2 says to flag pre-window rows as Informational; this was implemented as two flags "
        "(first oversell vs. later negative-position trades) to mirror the in-window split and DQ0056/DQ0057.",
        "Decision 7 says to 'flag these clients'; the flag is on `dim_client` only. Snapshots inherit it through "
        "`client_id` rather than getting a duplicated snapshot-level flag.",
        "Decision 14 says 'weekend-dated'; weekend = `dim_date.day_of_week` Saturday/Sunday, which is identical to "
        "`is_trading_day = 0` in this calendar (asserted).",
        "Decision 16 was applied to every value of both columns (not only the scientific-notation ones) so each "
        "column has one format. Three alpha values written `-0.0` become `-0.000000` (still zero; sign kept to "
        "avoid an undocumented change).",
        "Decision 5's AUM-jump flag sits on the later month of each jump; a client's first snapshot can never be "
        "flagged because it has no prior month.",
        "Decisions 4 and 11 call for reporting rather than row flags, so no flag columns were added for them.",
        "Boolean text (`True`/`False`) and source 0/1 flags were not recoded, because the decision log does not "
        "ask for it; cast them in the BI tool.",
    ]
    for item in amb:
        w(f"* {item}")
    path = safe_output(CLEAN_DIR / "README_clean.md")
    path.write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    return path


# =============================================================================
# 10. MAIN
# =============================================================================
def main():
    print("=" * 78)
    print("HW4 Part C - 02_clean.py")
    print(f"hw04 folder : {HW_DIR}")
    print("=" * 78)
    T, sources, hashes_before = load_tables()
    raw_copies = {t: df.copy() for t, df in T.items()}
    audit = Audit()

    step_filename(audit, T, sources)
    step_dividends(audit, T)
    t, since, window_start = step_positions(audit, T)
    step_dividend_without_purchase(audit, T, t, since, window_start)
    step_weekend(audit, T)
    step_snapshot(audit, T)
    step_fees(audit, T)
    step_client(audit, T)
    step_fixed_decimals(audit, T)
    step_preserve(audit, T)

    integrity_checks(audit, T, raw_copies)
    recon = write_outputs(audit, T, sources, raw_copies)
    # Rewrite the log so it includes the reconciliation step added in write_outputs().
    pd.DataFrame(audit.log_rows).to_csv(safe_output(AUDIT_DIR / "cleaning_log.csv"), index=False,
                                        lineterminator="\n")
    # Verify raw files BEFORE writing README_clean.md, which states that they were verified.
    for path, h in hashes_before.items():
        check(sha256(path) == h, f"RAW FILE CHANGED during the run: {path}")
    print("\nRaw files unchanged (SHA-256 identical before and after).")

    readme = write_readme(T, sources, recon, audit)

    print("\nOutputs written:")
    for table in T:
        print(f"  data/clean/{table}.csv")
    print(f"  data/clean/{readme.name}")
    for name in ("cleaning_log.csv", "value_changes.csv", "row_reconciliation.csv"):
        print(f"  data/audit/{name}")
    print(f"\n{len(audit.log_rows)} logged steps | {len(audit.change_rows):,} value changes | 0 rows removed")
    print("DONE - all assertions passed.")


if __name__ == "__main__":
    try:
        main()
    except (CleaningError, AssertionError) as exc:
        print(f"\nCLEANING STOPPED: {exc}", file=sys.stderr)
        sys.exit(1)
