#!/usr/bin/env python3
"""
HW4 Part D - Validating the Wildcat Capital Data
================================================

Validates BOTH the raw extract (data/raw/) and the cleaned output (data/clean/)
with the same checks, so the two layers can be compared fairly.

What this script does
---------------------
1. Loads all 16 tables as TEXT (dtype=str, keep_default_na=False). The raw
   transaction file is 'fact_transactions (1).csv'; the clean one is
   'fact_transactions.csv'. Inputs are never modified (SHA-256 checked before
   and after the run).
2. Referential integrity: check_referential_integrity() runs on all 13
   relationships (including the 3 date joins to dim_date.date) for raw and clean.
3. Business rules: bounds, sign convention, categories, dates, structural blanks,
   cross-field consistency and the Part B decision-log findings. Each rule is
   labelled with
     - classification : Confirmed error | Suspicious | Structural | Informational
     - basis          : Documented (decision log) | Logical/standard | Observed in data | Assumed threshold
4. Flag validation (clean only): all 15 flag_* columns exist, are 0/1, match the
   decision-log counts AND match the rule recomputed independently here.
5. Cross-table reconciliations (no source is assumed authoritative):
     R1 Advisory Fee transactions vs fact_fees (client-month)
     R2 fact_fees.aum_basis vs fact_portfolio_snapshot.aum (client-month),
        including the snapshot months with no fee record (Decision 11)
6. IQR outlier detection on unbounded numeric columns (outliers are NOT errors).
7. reports/data_quality_scorecard.csv - exactly 16 rows (8 tables x raw/clean).

Outputs (all in reports/; nothing in data/ is written):
    data_quality_scorecard.csv               16-row scorecard
    validation_referential_integrity.csv     26 rows (13 relationships x 2 layers)
    validation_rules.csv                     every rule x 2 layers
    validation_flags.csv                     15 clean flags
    validation_reconciliation.csv            reconciliation summaries
    validation_recon_advisory_fee_detail.csv client-month detail for R1 (clean layer)
    validation_snapshot_months_without_fee.csv  Decision 11 list (clean layer)
    validation_outliers.csv                  IQR results
    validation_summary.md                    denominators, labels, assumptions, ambiguities

Run:  python scripts/03_validate.py
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# 1. PATHS AND CONSTANTS
# =============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
HW_DIR = SCRIPT_DIR.parent
RAW_DIR = HW_DIR / "data" / "raw"
CLEAN_DIR = HW_DIR / "data" / "clean"
REPORTS_DIR = HW_DIR / "reports"

# Fixed reference date for "no future date" checks - the same one Part A used
# (01_profile.py AUDIT_REFERENCE_DATE), so results do not change with the clock.
REFERENCE_DATE = pd.Timestamp("2026-10-21")

TABLES = ["dim_advisor", "dim_benchmark", "dim_client", "dim_date", "dim_security",
          "fact_fees", "fact_portfolio_snapshot", "fact_transactions"]
RAW_FILE_CANDIDATES = {t: [f"{t}.csv"] for t in TABLES}
RAW_FILE_CANDIDATES["fact_transactions"] = ["fact_transactions (1).csv", "fact_transactions.csv"]
CLEAN_FILE = {t: f"{t}.csv" for t in TABLES}

PRIMARY_KEYS = {"dim_advisor": "advisor_id", "dim_benchmark": "benchmark_id", "dim_client": "client_id",
                "dim_date": "date_id", "dim_security": "security_id", "fact_fees": "fee_id",
                "fact_portfolio_snapshot": "snapshot_id", "fact_transactions": "txn_id"}

# Data type of every ORIGINAL business column (used for type validity).
# text = any string is valid; zip = 5 digits kept as text; state = 2 capital letters.
COLUMN_TYPES = {
    "dim_advisor": {"advisor_id": "int", "first_name": "text", "last_name": "text", "region": "text",
                    "years_experience": "int", "aum_target_mm": "decimal", "certifications": "text"},
    "dim_benchmark": {"benchmark_id": "int", "name": "text", "asset_class": "text", "ticker_proxy": "text"},
    "dim_client": {"client_id": "int", "first_name": "text", "last_name": "text", "age": "int",
                   "city": "text", "state": "state", "zip_code": "zip", "risk_profile": "text",
                   "client_tier": "text", "primary_account_type": "text", "initial_aum": "decimal",
                   "client_since": "date", "advisor_id": "int", "is_active": "bool"},
    "dim_date": {"date_id": "int", "date": "date", "year": "int", "quarter": "int", "month": "int",
                 "month_name": "text", "week_of_year": "int", "day_of_week": "text", "is_month_end": "flag",
                 "is_quarter_end": "flag", "is_year_end": "flag", "is_trading_day": "flag"},
    "dim_security": {"security_id": "int", "ticker": "text", "name": "text", "asset_class": "text",
                     "sector": "text", "benchmark_id": "int", "is_etf": "bool", "expense_ratio_bps": "decimal"},
    "fact_fees": {"fee_id": "int", "client_id": "int", "advisor_id": "int", "fee_date": "date",
                  "fee_type": "text", "aum_basis": "decimal", "bps_charged": "decimal", "amount": "decimal"},
    "fact_portfolio_snapshot": {"snapshot_id": "int", "client_id": "int", "advisor_id": "int",
                                "benchmark_id": "int", "snapshot_date": "date", "year": "int", "month": "int",
                                "aum": "decimal", "return_ytd": "decimal", "benchmark_return_ytd": "decimal",
                                "active_alpha_ytd": "decimal", "cash_pct": "decimal", "account_type": "text",
                                "is_return_suspect": "flag"},
    "fact_transactions": {"txn_id": "int", "client_id": "int", "advisor_id": "int", "security_id": "int",
                          "txn_date": "date", "txn_type": "text", "shares": "decimal", "price": "decimal",
                          "amount": "decimal", "is_post_departure_txn": "flag"},
}

# (child table, child column, parent table, parent column) - same 13 as 01_profile.py
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

# Flags created in Part C, with the counts in decision_log.md, and the rule in
# this script that recomputes each one independently.
EXPECTED_FLAGS = {
    ("fact_transactions", "flag_dividend_amount_miscalculated"): (53_826, "T08"),
    ("fact_transactions", "flag_oversell_first_in_window"): (9_828, "T10"),
    ("fact_transactions", "flag_txn_on_negative_position_in_window"): (15_977, "T11"),
    ("fact_transactions", "flag_oversell_first_pre_window"): (15_693, "T12"),
    ("fact_transactions", "flag_txn_on_negative_position_pre_window"): (25_574, "T13"),
    ("fact_transactions", "flag_dividend_without_prior_purchase"): (8_444, "T14"),
    ("fact_transactions", "flag_weekend_trade_or_dividend"): (56_117, "T15"),
    ("fact_portfolio_snapshot", "flag_extreme_return"): (2_639, "P13"),
    ("fact_portfolio_snapshot", "flag_aum_equals_1000"): (420, "P14"),
    ("fact_portfolio_snapshot", "flag_aum_mom_jump"): (330, "P15"),
    ("fact_fees", "flag_aum_basis_equals_1000"): (386, "F08"),
    ("fact_fees", "flag_bps_above_100"): (1, "F09"),
    ("dim_client", "flag_risk_profile_unknown"): (75, "C10"),
    ("dim_client", "flag_zip_state_mismatch"): (2_229, "C11"),
    ("dim_client", "flag_initial_aum_mismatch"): (1, "C12"),
}

# Observed value sets (read off the data in Part A - descriptive, not official)
REGIONS = {"Northeast", "Mid-Atlantic", "Southeast", "Midwest", "West"}
RISK_PROFILES = {"Conservative", "Moderately Conservative", "Balanced", "Moderately Aggressive",
                 "Aggressive", "Unknown"}
CLIENT_TIERS = {"Retail", "HNW", "UHNW"}
ACCOUNT_TYPES = {"Taxable", "IRA", "Roth IRA", "Trust", "529"}
SECURITY_CLASSES = {"Equity", "Fixed Income", "Alternative", "Cash Equivalent"}
FEE_TYPES = {"Advisory", "Management", "Custodial", "Performance"}
CERTIFICATIONS = {"CFA", "CFP", "CIMA", "None"}
TRADE_TYPES = ["Buy", "Sell"]
SECURITY_TXN_TYPES = ["Buy", "Sell", "Dividend"]
CASH_TXN_TYPES = ["Deposit", "Withdrawal", "Advisory Fee"]
TXN_TYPES = set(SECURITY_TXN_TYPES + CASH_TXN_TYPES)
NULL_TOKENS = {"NULL", "NAN", "N/A", "NA", "#N/A", "NIL", "MISSING", "-", "?"}  # 'None' handled per column

ZIP_FIRST_DIGIT = {  # USPS national areas (same table as Parts A and C)
    "0": {"CT", "MA", "ME", "NH", "NJ", "PR", "RI", "VT", "NY"}, "1": {"DE", "NY", "PA"},
    "2": {"DC", "MD", "NC", "SC", "VA", "WV"}, "3": {"AL", "FL", "GA", "MS", "TN"},
    "4": {"IN", "KY", "MI", "OH"}, "5": {"IA", "MN", "MT", "ND", "SD", "WI"},
    "6": {"IL", "KS", "MO", "NE"}, "7": {"AR", "LA", "OK", "TX"},
    "8": {"AZ", "CO", "ID", "NM", "NV", "UT", "WY"}, "9": {"AK", "CA", "HI", "OR", "WA"},
}

# Thresholds (labelled as assumptions where the decision log says so)
AUM_PLACEHOLDER = 1000.0
RETURN_SOFT = (-0.5, 1.0)        # Decision 3 - assumed
BPS_SOFT_MAX = 100.0             # Decision 6 - assumed
BPS_HARD_MAX = 10_000.0          # logical: 10,000 bps = 100% of AUM
AGE_RANGE = (18, 120)            # assumed plausible client age
YEARS_EXP_RANGE = (0, 70)        # assumed plausible career length
EXPENSE_RATIO_MAX = 1000.0       # assumed (10% per year)
FEE_RECON_TOLERANCE = 1.00       # $ - same tolerance as Part A (DQ0024)
AUM_RECON_TOLERANCE = 0.01       # $ - one cent
IQR_K = 1.5

INT_RE = r"^[+-]?\d+$"
DEC_RE = r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
EPS = 1e-6


# =============================================================================
# 2. HELPERS
# =============================================================================
class ValidationError(RuntimeError):
    pass


def check(cond, msg):
    """Assertion that still runs under `python -O`."""
    if not bool(cond):
        raise AssertionError(msg)


def blank(s):
    return s.astype(str).str.strip().eq("")


def to_num(s):
    t = s.astype(str).str.strip()
    return pd.to_numeric(t.where(t.str.match(DEC_RE)), errors="coerce")


def to_date(s):
    t = s.astype(str).str.strip()
    return pd.to_datetime(t.where(t.str.match(DATE_RE)), format="%Y-%m-%d", errors="coerce")


def pct(part, whole, nd=3):
    return round(100.0 * part / whole, nd) if whole else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sample(df, mask, k=5):
    pk = PRIMARY_KEYS.get(df.attrs.get("table", ""), None)
    if pk is None or not mask.any():
        return ""
    return "; ".join(f"{pk}={v}" for v in df.loc[mask, pk].head(k))


def type_valid(s, dtype):
    """True where a NON-BLANK value parses as dtype (blanks are judged by completeness instead)."""
    t = s.astype(str).str.strip()
    if dtype == "int":
        return t.str.match(INT_RE)
    if dtype == "decimal":
        return t.str.match(DEC_RE)
    if dtype == "date":
        return to_date(t).notna()
    if dtype == "bool":
        return t.isin(["True", "False"])
    if dtype == "flag":
        return t.isin(["0", "1"])
    if dtype == "zip":
        return t.str.fullmatch(r"\d{5}")
    if dtype == "state":
        return t.str.fullmatch(r"[A-Z]{2}")
    return pd.Series(True, index=s.index)


def safe_output(name):
    p = (REPORTS_DIR / name).resolve()
    check(p.parent == REPORTS_DIR.resolve(), f"outputs must go to reports/: {p}")
    check(p.name != "cleaning_report.md", "never overwrite the Part E report")
    return p


# =============================================================================
# 3. LOAD
# =============================================================================
def resolve(folder, candidates, table):
    found = [folder / c for c in candidates if (folder / c).is_file()]
    if not found:
        raise ValidationError(f"Missing input for {table} in {folder}: looked for {candidates}")
    if len(found) > 1:
        raise ValidationError(f"Ambiguous input for {table}: {[f.name for f in found]} all exist in {folder}")
    return found[0]


def load_layer(layer):
    folder = RAW_DIR if layer == "raw" else CLEAN_DIR
    if not folder.is_dir():
        hint = " Run scripts/02_clean.py first." if layer == "clean" else ""
        raise ValidationError(f"Folder not found: {folder}.{hint}")
    out, files = {}, {}
    for t in TABLES:
        cands = RAW_FILE_CANDIDATES[t] if layer == "raw" else [CLEAN_FILE[t]]
        path = resolve(folder, cands, t)
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8")
        missing = set(COLUMN_TYPES[t]) - set(df.columns)
        check(not missing, f"{layer}/{path.name}: missing expected columns {sorted(missing)}")
        orig = list(COLUMN_TYPES[t])
        check(list(df.columns[:len(orig)]) == orig, f"{layer}/{path.name}: original columns out of order")
        extra = list(df.columns[len(orig):])
        if layer == "raw":
            check(not extra, f"raw/{path.name}: unexpected extra columns {extra}")
        else:
            check(all(c.startswith("flag_") for c in extra), f"clean/{path.name}: non-flag extra columns {extra}")
        df.attrs["table"] = t
        out[t], files[t] = df, path
        print(f"  {layer:<5} {t:<25} {len(df):>8,} rows  {len(df.columns):>2} cols  <- {path.name}")
    return out, files


# =============================================================================
# 4. REFERENTIAL INTEGRITY
# =============================================================================
def check_referential_integrity(fact_df, fact_key, dim_df, dim_key):
    """Check that every non-blank fact_df[fact_key] exists in dim_df[dim_key].

    Returns a dict with:
      total_rows          rows in fact_df
      null_key_rows       rows whose key is blank (not tested for a match)
      null_key_rate_pct   null_key_rows / total_rows
      checked_rows        rows with a non-blank key  (= denominator of the match rate)
      matched_rows        checked rows whose key exists in the dimension
      match_rate_pct      matched_rows / checked_rows (None when nothing to check)
      orphan_rows         checked rows whose key is NOT in the dimension
      distinct_orphan_keys number of distinct orphan key values
      orphan_key_sample   up to 5 orphan key values
      dim_key_unique      True when the dimension key is unique (a valid 'one' side)
    Keys are compared as stripped text, so '01' and '1' are different keys.
    """
    for df, col, name in ((fact_df, fact_key, "fact"), (dim_df, dim_key, "dimension")):
        if col not in df.columns:
            raise ValidationError(f"check_referential_integrity: column '{col}' not in {name} table")
    fk = fact_df[fact_key].astype(str).str.strip()
    dk = dim_df[dim_key].astype(str).str.strip()
    is_null = fk.eq("")
    checked = ~is_null
    matched = checked & fk.isin(set(dk[dk.ne("")]))
    orphan = checked & ~matched
    n, n_checked = len(fk), int(checked.sum())
    orphan_keys = fk[orphan].unique()
    return {
        "total_rows": n,
        "null_key_rows": int(is_null.sum()),
        "null_key_rate_pct": pct(int(is_null.sum()), n),
        "checked_rows": n_checked,
        "matched_rows": int(matched.sum()),
        "match_rate_pct": pct(int(matched.sum()), n_checked),
        "orphan_rows": int(orphan.sum()),
        "distinct_orphan_keys": int(len(orphan_keys)),
        "orphan_key_sample": "; ".join(map(str, orphan_keys[:5])),
        "dim_key_unique": bool(dk.is_unique),
    }


def run_referential(T, layer):
    rows = []
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        r = check_referential_integrity(T[child], ccol, T[parent], pcol)
        note = ""
        if r["null_key_rows"]:
            # Only fact_transactions.security_id is allowed to be blank - and only for cash txns.
            nulls = blank(T[child][ccol])
            structural = (child == "fact_transactions" and ccol == "security_id"
                          and T[child].loc[nulls, "txn_type"].isin(CASH_TXN_TYPES).all())
            note = ("blank keys are structural (cash transactions have no security)" if structural
                    else "UNEXPECTED blank keys")
        status = "PASS" if r["orphan_rows"] == 0 and r["dim_key_unique"] and (not r["null_key_rows"] or
                                                                               note.startswith("blank keys are")) else "FAIL"
        rows.append({"layer": layer, "relationship": f"{child}.{ccol} -> {parent}.{pcol}",
                     "child_table": child, "child_column": ccol, "parent_table": parent,
                     "parent_column": pcol, **r, "status": status, "note": note})
    check(len(rows) == 13, "13 relationships must be checked")
    return rows


# =============================================================================
# 5. BUSINESS RULES
# =============================================================================
class Rules:
    """Collects rule results. A rule = (applicable rows, failing rows) on one table."""

    SCORED = {"Confirmed error": "business_rule", "Structural": "business_rule",
              "Suspicious": "suspicious", "Informational": "not_scored"}

    def __init__(self, T, layer):
        self.T, self.layer, self.rows, self.masks = T, layer, [], {}

    def add(self, rid, table, column, rule, category, classification, basis, applicable, fail,
            decision="", note=""):
        df = self.T[table]
        applicable = applicable.reindex(df.index).fillna(False).astype(bool)
        fail = (fail.reindex(df.index).fillna(False).astype(bool)) & applicable
        n_app, n_fail = int(applicable.sum()), int(fail.sum())
        self.masks[rid] = fail
        self.rows.append({
            "layer": self.layer, "rule_id": rid, "table": table, "column": column, "rule": rule,
            "category": category, "classification": classification, "basis": basis,
            "scored_as": self.SCORED[classification], "applicable_rows": n_app, "failed_rows": n_fail,
            "passed_rows": n_app - n_fail, "pass_rate_pct": pct(n_app - n_fail, n_app),
            "fail_rate_pct": pct(n_fail, n_app), "sample_failing_keys": sample(df, fail),
            "decision_ref": decision, "note": note})


def positions(T):
    """Rebuild Buy/Sell share positions (same definition as Part A DQ0002/DQ0020/DQ0056/DQ0057).
    Same-day Buys are applied before Sells; ties by txn_id."""
    tx, cl = T["fact_transactions"], T["dim_client"]
    t = tx.loc[tx["txn_type"].isin(TRADE_TYPES), ["txn_id", "client_id", "security_id", "txn_date",
                                                  "txn_type", "shares"]].copy()
    sell = t["txn_type"].eq("Sell")
    t["_q"] = np.where(sell, -1.0, 1.0) * to_num(t["shares"])
    t["_d"] = to_date(t["txn_date"])
    t["_k"] = sell.astype(int)
    t["_id"] = to_num(t["txn_id"])
    t = t.sort_values(["client_id", "security_id", "_d", "_k", "_id"], kind="mergesort")
    t["_after"] = t.groupby(["client_id", "security_id"])["_q"].cumsum()
    t["_before"] = t["_after"] - t["_q"]
    first_oversell = t["txn_type"].eq("Sell") & (t["_before"] >= -EPS) & (t["_after"] < -EPS)
    on_negative = t["_before"] < -EPS
    window_start = to_date(tx["txn_date"]).min()
    since = to_date(cl.set_index("client_id")["client_since"])
    in_window = t["client_id"].map(since) >= window_start
    first_buy = t[t["txn_type"].eq("Buy")].groupby(["client_id", "security_id"])["_d"].min()
    return t, first_oversell, on_negative, in_window, since, window_start, first_buy


def build_rules(T, layer):
    R = Rules(T, layer)
    adv, bm, cl, dd, sec = (T[t] for t in ["dim_advisor", "dim_benchmark", "dim_client", "dim_date", "dim_security"])
    fee, sn, tx = T["fact_fees"], T["fact_portfolio_snapshot"], T["fact_transactions"]
    ALL = lambda df: pd.Series(True, index=df.index)  # noqa: E731

    # ---------------- dim_advisor
    ye = to_num(adv["years_experience"])
    R.add("A01", "dim_advisor", "years_experience", f"years_experience between {YEARS_EXP_RANGE}", "bounds",
          "Confirmed error", "Assumed threshold", ALL(adv), ~ye.between(*YEARS_EXP_RANGE))
    R.add("A02", "dim_advisor", "aum_target_mm", "aum_target_mm > 0", "bounds", "Confirmed error",
          "Logical/standard", ALL(adv), ~(to_num(adv["aum_target_mm"]) > 0))
    R.add("A03", "dim_advisor", "region", "region in observed set", "category", "Informational",
          "Observed in data", ALL(adv), ~adv["region"].isin(REGIONS))
    certs_ok = adv["certifications"].map(lambda v: all(x.strip() in CERTIFICATIONS for x in v.split(",")))
    R.add("A04", "dim_advisor", "certifications", "each certification in {CFA, CFP, CIMA, None}; 'None' is a "
          "real value", "category", "Informational", "Observed in data", ALL(adv), ~certs_ok, "Decision 10")

    # ---------------- dim_benchmark
    R.add("B01", "dim_benchmark", "name", "benchmark name unique", "uniqueness", "Confirmed error",
          "Logical/standard", ALL(bm), bm["name"].duplicated(keep=False))
    R.add("B02", "dim_benchmark", "ticker_proxy", "ticker_proxy unique", "uniqueness", "Confirmed error",
          "Logical/standard", ALL(bm), bm["ticker_proxy"].duplicated(keep=False))

    # ---------------- dim_client
    age = to_num(cl["age"])
    R.add("C01", "dim_client", "age", f"age between {AGE_RANGE}", "bounds", "Confirmed error",
          "Assumed threshold", ALL(cl), ~age.between(*AGE_RANGE))
    R.add("C02", "dim_client", "initial_aum", "initial_aum > 0", "bounds", "Confirmed error",
          "Logical/standard", ALL(cl), ~(to_num(cl["initial_aum"]) > 0))
    since = to_date(cl["client_since"])
    R.add("C03", "dim_client", "client_since", f"valid date and not after {REFERENCE_DATE.date()}", "date",
          "Confirmed error", "Logical/standard", ALL(cl), since.isna() | (since > REFERENCE_DATE))
    R.add("C04", "dim_client", "is_active", "is_active is True/False", "category", "Confirmed error",
          "Logical/standard", ALL(cl), ~cl["is_active"].isin(["True", "False"]))
    R.add("C05", "dim_client", "risk_profile", "risk_profile in observed set", "category", "Informational",
          "Observed in data", ALL(cl), ~cl["risk_profile"].isin(RISK_PROFILES))
    R.add("C06", "dim_client", "client_tier", "client_tier in observed set", "category", "Informational",
          "Observed in data", ALL(cl), ~cl["client_tier"].isin(CLIENT_TIERS))
    R.add("C07", "dim_client", "primary_account_type", "account type in observed set", "category",
          "Informational", "Observed in data", ALL(cl), ~cl["primary_account_type"].isin(ACCOUNT_TYPES))
    R.add("C08", "dim_client", "zip_code", "zip_code is 5-digit text (leading zeros kept)", "validity",
          "Confirmed error", "Logical/standard", ALL(cl), ~cl["zip_code"].str.fullmatch(r"\d{5}"),
          "Decision 8", note=f"{int(cl['zip_code'].str.startswith('0').sum())} ZIPs start with 0")
    known = cl["state"].isin(set().union(*ZIP_FIRST_DIGIT.values())) & cl["zip_code"].str.fullmatch(r"\d{5}")
    zmis = known & pd.Series([s not in ZIP_FIRST_DIGIT.get(z[:1], set()) for s, z in zip(cl["state"], cl["zip_code"])],
                             index=cl.index)
    R.add("C10", "dim_client", "risk_profile", "risk_profile is not 'Unknown'", "completeness", "Suspicious",
          "Documented (decision log)", ALL(cl), cl["risk_profile"].eq("Unknown"), "Decision 7")
    R.add("C11", "dim_client", "zip_code, state", "ZIP first digit belongs to the state (USPS areas)",
          "consistency", "Suspicious", "Documented (decision log)", known, zmis, "Decision 8")
    s = sn.assign(_d=to_date(sn["snapshot_date"]), _a=to_num(sn["aum"])).sort_values("_d", kind="mergesort")
    first_aum = s.groupby("client_id")["_a"].first()
    win_s = s["_d"].min()
    ratio = cl["client_id"].map(first_aum) / to_num(cl["initial_aum"])
    in_win_cl = since >= win_s
    R.add("C12", "dim_client", "initial_aum", "in-window client's first snapshot AUM within 0.5x-2x of "
          "initial_aum", "consistency", "Suspicious", "Documented (decision log)", in_win_cl,
          (ratio < 0.5) | (ratio > 2), "Decision 15")

    # ---------------- dim_date
    d = to_date(dd["date"])
    cal_ok = (d.notna() & (to_num(dd["year"]) == d.dt.year) & (to_num(dd["quarter"]) == d.dt.quarter)
              & (to_num(dd["month"]) == d.dt.month) & dd["month_name"].eq(d.dt.month_name())
              & dd["day_of_week"].eq(d.dt.day_name())
              & (to_num(dd["week_of_year"]) == d.dt.isocalendar().week.astype(float)))
    R.add("D01", "dim_date", "date", "date is valid and unique", "date", "Confirmed error", "Logical/standard",
          ALL(dd), d.isna() | dd["date"].duplicated(keep=False))
    R.add("D02", "dim_date", "year..week_of_year", "calendar attributes agree with date (ISO week)",
          "consistency", "Confirmed error", "Logical/standard", ALL(dd), ~cal_ok)
    end_ok = (dd["is_month_end"].eq(d.dt.is_month_end.astype(int).astype(str))
              & dd["is_quarter_end"].eq(d.dt.is_quarter_end.astype(int).astype(str))
              & dd["is_year_end"].eq(d.dt.is_year_end.astype(int).astype(str)))
    R.add("D03", "dim_date", "is_month_end/quarter/year_end", "period-end flags agree with date",
          "consistency", "Confirmed error", "Logical/standard", ALL(dd), ~end_ok)
    weekday = ~dd["day_of_week"].isin(["Saturday", "Sunday"])
    holiday = dd["date"].str[5:].isin(["01-01", "07-04", "12-25"]) & weekday
    R.add("D04", "dim_date", "is_trading_day", "fixed-date market holidays (Jan 1, Jul 4, Dec 25 on weekdays) "
          "are not trading days", "consistency", "Suspicious", "Documented (decision log)", holiday,
          dd["is_trading_day"].eq("1"), "Decision 14",
          note="floating NYSE holidays are not tested; calendar flag behaves like 'weekday'")
    R.add("D05", "dim_date", "is_trading_day", "is_trading_day = 1 exactly on weekdays", "consistency",
          "Informational", "Observed in data", ALL(dd), dd["is_trading_day"].eq("1") != weekday)

    # ---------------- dim_security
    etf = sec["is_etf"].eq("True")
    R.add("S01", "dim_security", "ticker", "ticker unique, 1-5 capital letters", "validity", "Confirmed error",
          "Logical/standard", ALL(sec), sec["ticker"].duplicated(keep=False) | ~sec["ticker"].str.fullmatch(r"[A-Z]{1,5}"))
    R.add("S02", "dim_security", "asset_class", "asset_class in observed set", "category", "Informational",
          "Observed in data", ALL(sec), ~sec["asset_class"].isin(SECURITY_CLASSES))
    R.add("S03", "dim_security", "is_etf", "is_etf is True/False", "category", "Confirmed error",
          "Logical/standard", ALL(sec), ~sec["is_etf"].isin(["True", "False"]))
    R.add("S04", "dim_security", "sector", "sector blank if and only if ETF", "structural", "Structural",
          "Observed in data", ALL(sec), blank(sec["sector"]) != etf, "Decision 10")
    R.add("S05", "dim_security", "expense_ratio_bps", "expense_ratio_bps blank if and only if non-ETF",
          "structural", "Structural", "Observed in data", ALL(sec), blank(sec["expense_ratio_bps"]) != ~etf,
          "Decision 10")
    er = to_num(sec["expense_ratio_bps"])
    R.add("S06", "dim_security", "expense_ratio_bps", f"expense ratio between 0 and {EXPENSE_RATIO_MAX:.0f} bps",
          "bounds", "Confirmed error", "Assumed threshold", ~blank(sec["expense_ratio_bps"]),
          ~er.between(0, EXPENSE_RATIO_MAX))

    # ---------------- fact_fees
    fd = to_date(fee["fee_date"])
    basis, bps, famt = to_num(fee["aum_basis"]), to_num(fee["bps_charged"]), to_num(fee["amount"])
    R.add("F01", "fact_fees", "fee_date", f"valid date, not after {REFERENCE_DATE.date()}", "date",
          "Confirmed error", "Logical/standard", ALL(fee), fd.isna() | (fd > REFERENCE_DATE))
    R.add("F02", "fact_fees", "fee_date", "fee dated the 1st of the month (monthly grain)", "date",
          "Informational", "Observed in data", ALL(fee), fd.dt.day != 1)
    R.add("F03", "fact_fees", "fee_type", "fee_type in observed set", "category", "Informational",
          "Observed in data", ALL(fee), ~fee["fee_type"].isin(FEE_TYPES))
    R.add("F04", "fact_fees", "aum_basis", "aum_basis > 0", "bounds", "Confirmed error", "Logical/standard",
          ALL(fee), ~(basis > 0))
    R.add("F05", "fact_fees", "amount", "fee amount > 0 (positive sign convention)", "sign", "Confirmed error",
          "Logical/standard", ALL(fee), ~(famt > 0))
    R.add("F06", "fact_fees", "bps_charged", f"bps_charged between 0 and {BPS_HARD_MAX:.0f}", "bounds",
          "Confirmed error", "Logical/standard", ALL(fee), ~bps.between(0, BPS_HARD_MAX))
    R.add("F07", "fact_fees", "amount", "amount = aum_basis x bps_charged / 10,000 / 12 (within $0.01)",
          "consistency", "Informational", "Observed in data", ALL(fee),
          (basis * bps / 10_000 / 12 - famt).abs() > 0.0100001,
          note="formula inferred from the data (monthly charge of an annual bps rate); not documented")
    R.add("F08", "fact_fees", "aum_basis", "aum_basis is not 1000", "bounds", "Suspicious",
          "Documented (decision log)", ALL(fee), basis.eq(AUM_PLACEHOLDER), "Decision 5")
    R.add("F09", "fact_fees", "bps_charged", f"bps_charged <= {BPS_SOFT_MAX:.0f}", "bounds", "Suspicious",
          "Assumed threshold", ALL(fee), bps.gt(BPS_SOFT_MAX), "Decision 6")
    R.add("F10", "fact_fees", "client_id, fee_date", "one fee row per client-month", "uniqueness",
          "Informational", "Observed in data", ALL(fee), fee.duplicated(["client_id", "fee_date"], keep=False))

    # ---------------- fact_portfolio_snapshot
    sd = to_date(sn["snapshot_date"])
    aum, r = to_num(sn["aum"]), to_num(sn["return_ytd"])
    b, a = to_num(sn["benchmark_return_ytd"]), to_num(sn["active_alpha_ytd"])
    R.add("P01", "fact_portfolio_snapshot", "snapshot_date", f"valid date, not after {REFERENCE_DATE.date()}",
          "date", "Confirmed error", "Logical/standard", ALL(sn), sd.isna() | (sd > REFERENCE_DATE))
    R.add("P02", "fact_portfolio_snapshot", "year, month", "year and month agree with snapshot_date",
          "consistency", "Confirmed error", "Logical/standard", ALL(sn),
          (to_num(sn["year"]) != sd.dt.year) | (to_num(sn["month"]) != sd.dt.month))
    R.add("P03", "fact_portfolio_snapshot", "snapshot_date", "snapshot dated the 1st (monthly grain)", "date",
          "Informational", "Observed in data", ALL(sn), sd.dt.day != 1)
    R.add("P04", "fact_portfolio_snapshot", "client_id, snapshot_date", "one snapshot per client-month",
          "uniqueness", "Confirmed error", "Logical/standard", ALL(sn),
          sn.duplicated(["client_id", "snapshot_date"], keep=False))
    R.add("P05", "fact_portfolio_snapshot", "aum", "aum > 0", "bounds", "Confirmed error", "Logical/standard",
          ALL(sn), ~(aum > 0))
    R.add("P06", "fact_portfolio_snapshot", "return_ytd", "return_ytd >= -1 (cannot lose more than 100%)",
          "bounds", "Confirmed error", "Logical/standard", ALL(sn), ~(r >= -1),
          note="assumes long-only accounts without leverage")
    R.add("P07", "fact_portfolio_snapshot", "active_alpha_ytd", "alpha = return_ytd - benchmark_return_ytd "
          "(within 0.00001)", "consistency", "Confirmed error", "Logical/standard", ALL(sn), (r - b - a).abs() > 1e-5)
    R.add("P08", "fact_portfolio_snapshot", "cash_pct", "cash_pct between 0 and 1", "bounds", "Confirmed error",
          "Logical/standard", ALL(sn), ~to_num(sn["cash_pct"]).between(0, 1))
    R.add("P09", "fact_portfolio_snapshot", "account_type", "account_type in observed set", "category",
          "Informational", "Observed in data", ALL(sn), ~sn["account_type"].isin(ACCOUNT_TYPES))
    R.add("P10", "fact_portfolio_snapshot", "is_return_suspect", "is_return_suspect is 0/1", "flag",
          "Confirmed error", "Logical/standard", ALL(sn), ~sn["is_return_suspect"].isin(["0", "1"]))
    R.add("P11", "fact_portfolio_snapshot", "is_return_suspect", "is_return_suspect = 1 exactly when "
          "|alpha| > 0.5", "flag", "Informational", "Observed in data", ALL(sn),
          sn["is_return_suspect"].eq("1") != (a.abs() > 0.5), "Decision 3")
    nb = sn.groupby(["benchmark_id", "snapshot_date"])["benchmark_return_ytd"].transform(
        lambda x: to_num(x).nunique())
    R.add("P12", "fact_portfolio_snapshot", "benchmark_return_ytd", "one benchmark return per benchmark-month",
          "consistency", "Confirmed error", "Logical/standard", ALL(sn), nb > 1)
    R.add("P13", "fact_portfolio_snapshot", "return_ytd", f"return_ytd within {RETURN_SOFT}", "bounds",
          "Suspicious", "Assumed threshold", ALL(sn), (r < RETURN_SOFT[0]) | (r > RETURN_SOFT[1]), "Decision 3")
    R.add("P14", "fact_portfolio_snapshot", "aum", "aum is not 1000", "bounds", "Suspicious",
          "Documented (decision log)", ALL(sn), aum.eq(AUM_PLACEHOLDER), "Decision 5")
    s2 = sn.assign(_d=sd, _a=aum).sort_values(["client_id", "_d"], kind="mergesort")
    jr = s2["_a"] / s2.groupby("client_id")["_a"].shift()
    jump = ((jr > 3) | (jr < 1 / 3)).reindex(sn.index).fillna(False)
    has_prev = s2.groupby("client_id").cumcount().gt(0).reindex(sn.index)
    R.add("P15", "fact_portfolio_snapshot", "aum", "aum within 1/3x-3x of the client's previous month",
          "consistency", "Suspicious", "Documented (decision log)", has_prev, jump, "Decision 5")
    unknown_ids = set(cl.loc[cl["risk_profile"].eq("Unknown"), "client_id"])
    R.add("P16", "fact_portfolio_snapshot", "benchmark_id", "client has a known risk profile (benchmark "
          "assignment meaningful)", "consistency", "Suspicious", "Documented (decision log)", ALL(sn),
          sn["client_id"].isin(unknown_ids), "Decision 7")
    cl_adv = cl.set_index("client_id")["advisor_id"]
    R.add("P17", "fact_portfolio_snapshot", "advisor_id", "advisor_id equals dim_client.advisor_id",
          "consistency", "Informational", "Observed in data", ALL(sn), sn["advisor_id"] != sn["client_id"].map(cl_adv),
          note="assumes one advisor per client for the whole period (no history table)")

    # ---------------- fact_transactions
    td = to_date(tx["txn_date"])
    typ = tx["txn_type"]
    sh, pr, amt = to_num(tx["shares"]), to_num(tx["price"]), to_num(tx["amount"])
    is_sec, is_cash = typ.isin(SECURITY_TXN_TYPES), typ.isin(CASH_TXN_TYPES)
    sec_cols_blank = tx[["security_id", "shares", "price"]].apply(blank)
    R.add("T01", "fact_transactions", "txn_date", f"valid date, not after {REFERENCE_DATE.date()}", "date",
          "Confirmed error", "Logical/standard", ALL(tx), td.isna() | (td > REFERENCE_DATE))
    R.add("T02", "fact_transactions", "txn_type", "txn_type in observed set", "category", "Informational",
          "Observed in data", ALL(tx), ~typ.isin(TXN_TYPES))
    R.add("T03", "fact_transactions", "amount", "amount > 0 for every type (direction comes from txn_type)",
          "sign", "Confirmed error", "Documented (decision log)", ALL(tx), ~(amt > 0), "Decision 17")
    R.add("T04", "fact_transactions", "security_id, shares, price", "Buy/Sell/Dividend have security_id, "
          "shares and price", "structural", "Structural", "Logical/standard", is_sec, sec_cols_blank.any(axis=1),
          "Decision 10")
    R.add("T05", "fact_transactions", "security_id, shares, price", "Deposit/Withdrawal/Advisory Fee have "
          "blank security_id, shares and price", "structural", "Structural", "Logical/standard", is_cash,
          ~sec_cols_blank.all(axis=1), "Decision 10")
    R.add("T06", "fact_transactions", "shares, price", "shares > 0 and price > 0 where present", "sign",
          "Confirmed error", "Logical/standard", is_sec, ~((sh > 0) & (pr > 0)))
    R.add("T07", "fact_transactions", "amount", "Buy/Sell amount = shares x price (within $0.01)",
          "consistency", "Confirmed error", "Logical/standard", typ.isin(TRADE_TYPES),
          (sh * pr - amt).abs() > 0.0100001)
    R.add("T08", "fact_transactions", "amount", "Dividend amount is a payout, not the full position value "
          "(amount != shares x price)", "consistency", "Confirmed error", "Documented (decision log)",
          typ.eq("Dividend"), (sh * pr - amt).abs() <= 0.0100001, "Decision 1")
    R.add("T09", "fact_transactions", "is_post_departure_txn", "is_post_departure_txn is 0/1", "flag",
          "Confirmed error", "Logical/standard", ALL(tx), ~tx["is_post_departure_txn"].isin(["0", "1"]))

    t, first, neg, inw, since_map, win_t, first_buy = positions(T)
    sells = t["txn_type"].eq("Sell")
    full = lambda m: m.reindex(tx.index, fill_value=False)  # noqa: E731
    R.add("T10", "fact_transactions", "shares", "no Sell takes an in-window client's position below zero",
          "consistency", "Confirmed error", "Documented (decision log)", full(sells & inw), full(first & inw),
          "Decision 2")
    R.add("T11", "fact_transactions", "shares", "no Buy/Sell on an already-negative position (in-window)",
          "consistency", "Suspicious", "Documented (decision log)", full(inw), full(neg & inw), "Decision 2")
    R.add("T12", "fact_transactions", "shares", "first oversell, pre-window client (purchases may predate "
          "extract)", "consistency", "Informational", "Documented (decision log)", full(sells & ~inw),
          full(first & ~inw), "Decision 2")
    R.add("T13", "fact_transactions", "shares", "Buy/Sell on negative position, pre-window client",
          "consistency", "Informational", "Documented (decision log)", full(~inw), full(neg & ~inw), "Decision 2")
    dv = typ.eq("Dividend")
    key = pd.MultiIndex.from_frame(tx[["client_id", "security_id"]])
    fb = pd.Series(first_buy.reindex(key).values, index=tx.index)
    div_inw = dv & (tx["client_id"].map(since_map) >= win_t)
    R.add("T14", "fact_transactions", "security_id", "dividend only after a recorded purchase (in-window clients)",
          "consistency", "Suspicious", "Documented (decision log)", div_inw, fb.isna() | (td < fb), "Decision 12")
    dow = tx["txn_date"].map(dict(zip(dd["date"], dd["day_of_week"])))
    weekend = dow.isin(["Saturday", "Sunday"])
    R.add("T15", "fact_transactions", "txn_date", "Buy/Sell/Dividend dated on a weekday", "date", "Suspicious",
          "Documented (decision log)", is_sec, weekend, "Decision 14")
    R.add("T16", "fact_transactions", "txn_date", "Deposit/Withdrawal/Advisory Fee dated on a weekday", "date",
          "Informational", "Documented (decision log)", is_cash, weekend, "Decision 14",
          note="cash movements may be booked on weekends")
    last = sd.groupby(sn["client_id"]).max() + pd.offsets.MonthEnd(0)
    after_last = td > pd.to_datetime(tx["client_id"].map(last))
    R.add("T17", "fact_transactions", "txn_date", "transaction not after the client's last snapshot month",
          "date", "Suspicious", "Documented (decision log)", ALL(tx), after_last, "Decision 13",
          note="last snapshot is a proxy for departure; no departure date exists")
    R.add("T18", "fact_transactions", "is_post_departure_txn", "is_post_departure_txn = 1 exactly for txns "
          "after the last snapshot month", "flag", "Informational", "Observed in data", ALL(tx),
          tx["is_post_departure_txn"].eq("1") != after_last, "Decision 13")
    R.add("T19", "fact_transactions", "advisor_id", "advisor_id equals dim_client.advisor_id", "consistency",
          "Informational", "Observed in data", ALL(tx), tx["advisor_id"] != tx["client_id"].map(cl_adv),
          note="assumes one advisor per client for the whole period")
    return R


# =============================================================================
# 6. FLAG VALIDATION (clean layer)
# =============================================================================
def validate_flags(T_raw, T_clean, rules_clean):
    rows = []
    for t in TABLES:
        check(not [c for c in T_raw[t].columns if c.startswith("flag_")], f"raw {t} must not contain flag_ columns")
        found = [c for c in T_clean[t].columns if c.startswith("flag_")]
        expected = [f for (tt, f) in EXPECTED_FLAGS if tt == t]
        check(sorted(found) == sorted(expected), f"clean {t}: flags {found} != expected {expected}")
    for (t, flag), (exp_n, rid) in EXPECTED_FLAGS.items():
        col = T_clean[t][flag]
        valid01 = bool(col.isin(["0", "1"]).all())
        on = col.eq("1")
        rule = rules_clean.masks[rid]
        agree = bool(on.equals(rule))
        rows.append({"table": t, "flag": flag, "values_0_1": valid01, "rows_flagged": int(on.sum()),
                     "expected_rows_decision_log": exp_n, "count_matches": int(on.sum()) == exp_n,
                     "recomputed_rule": rid, "rule_failed_rows": int(rule.sum()),
                     "rows_flag_without_rule": int((on & ~rule).sum()),
                     "rows_rule_without_flag": int((rule & ~on).sum()), "agrees_with_rule": agree,
                     "status": "PASS" if valid01 and agree and int(on.sum()) == exp_n else "FAIL"})
    check(len(rows) == 15, "15 flags must be validated")
    return rows


def compare_layers(T_raw, T_clean):
    """Clean business columns must equal raw text, except the Decision 16 columns, which must be numerically equal."""
    out = []
    for t in TABLES:
        cols = list(COLUMN_TYPES[t])
        r, c = T_raw[t][cols], T_clean[t][cols]
        check(len(r) == len(c) and r[PRIMARY_KEYS[t]].equals(c[PRIMARY_KEYS[t]]), f"{t}: raw/clean rows differ")
        diff = r.ne(c)
        changed = [k for k in cols if diff[k].any()]
        for k in changed:
            check(t == "fact_portfolio_snapshot" and k in ("return_ytd", "active_alpha_ytd"),
                  f"{t}.{k}: undocumented change between raw and clean")
            check((to_num(r.loc[diff[k], k]) == to_num(c.loc[diff[k], k])).all(),
                  f"{t}.{k}: changed values are not numerically identical")
        out.append((t, int(diff.to_numpy().sum()), changed))
    return out


# =============================================================================
# 7. RECONCILIATIONS
# =============================================================================
def recon_advisory_fees(T, layer):
    """R1: Advisory Fee transactions vs fact_fees, summed per client-month.
    Two variants because it is not documented which fee rows the transactions represent."""
    tx, fee = T["fact_transactions"], T["fact_fees"]
    af = tx[tx["txn_type"].eq("Advisory Fee")]
    txn = to_num(af["amount"]).groupby([af["client_id"], af["txn_date"].str[:7]]).sum().rename("txn_amount")
    txn_count = af.groupby([af["client_id"], af["txn_date"].str[:7]]).size().rename("txn_rows")
    summaries, detail = [], None
    for variant, fsub in [("all fee types", fee), ("fee_type = Advisory only", fee[fee["fee_type"].eq("Advisory")])]:
        f = to_num(fsub["amount"]).groupby([fsub["client_id"], fsub["fee_date"].str[:7]]).sum().rename("fee_amount")
        j = pd.concat([txn, txn_count, f], axis=1)
        j.index.names = ["client_id", "month"]
        both = j["txn_amount"].notna() & j["fee_amount"].notna()
        diff = j["txn_amount"] - j["fee_amount"]
        match = both & (diff.abs() <= FEE_RECON_TOLERANCE)
        ratio = (j["txn_amount"] / j["fee_amount"])[both]
        j["difference_txn_minus_fee"] = diff
        j["status"] = np.select([match, both, j["txn_amount"].notna()],
                                ["match", "mismatch", "transactions only"], "fee table only")
        n_both = int(both.sum())
        summaries.append({
            "layer": layer, "reconciliation": "R1 Advisory Fee transactions vs fact_fees", "variant": variant,
            "grain": "client-month", "tolerance": f"${FEE_RECON_TOLERANCE:.2f}",
            "left_records": int(j["txn_amount"].notna().sum()), "right_records": int(j["fee_amount"].notna().sum()),
            "compared_records": n_both, "matched": int(match.sum()), "mismatched": n_both - int(match.sum()),
            "match_rate_pct": pct(int(match.sum()), n_both), "left_only": int((j["status"] == "transactions only").sum()),
            "right_only": int((j["status"] == "fee table only").sum()),
            "left_total": round(float(j["txn_amount"].sum()), 2), "right_total": round(float(j["fee_amount"].sum()), 2),
            "total_difference_left_minus_right": round(float(j["txn_amount"].sum() - j["fee_amount"].sum()), 2),
            "median_abs_difference": round(float(diff[both].abs().median()), 2),
            "max_abs_difference": round(float(diff[both].abs().max()), 2),
            "ratio_p10_median_p90": " / ".join(f"{q:.2f}" for q in ratio.quantile([0.1, 0.5, 0.9])),
            "note": "left = Advisory Fee transactions (several per month are summed), right = fact_fees; "
                    "neither is assumed authoritative"})
        if variant == "all fee types":
            detail = j.reset_index()
            detail["txn_rows"] = detail["txn_rows"].astype("Int64")
    return summaries, detail


def recon_aum_basis(T, layer):
    """R2: fee aum_basis vs portfolio snapshot aum for the same client-month; also lists snapshot months
    that have no fee row (Decision 11)."""
    fee, sn = T["fact_fees"], T["fact_portfolio_snapshot"]
    j = fee[["fee_id", "client_id", "fee_date", "aum_basis"]].merge(
        sn[["snapshot_id", "client_id", "snapshot_date", "aum"]], how="outer",
        left_on=["client_id", "fee_date"], right_on=["client_id", "snapshot_date"], indicator=True)
    both = j["_merge"].eq("both")
    diff = to_num(j["aum_basis"]) - to_num(j["aum"])
    match = both & (diff.abs() <= AUM_RECON_TOLERANCE)
    n_both = int(both.sum())
    placeholder_both = int((both & to_num(j["aum"]).eq(AUM_PLACEHOLDER) & to_num(j["aum_basis"]).eq(AUM_PLACEHOLDER)).sum())
    summary = {
        "layer": layer, "reconciliation": "R2 fact_fees.aum_basis vs fact_portfolio_snapshot.aum",
        "variant": "same client_id and month (fee_date = snapshot_date)", "grain": "client-month",
        "tolerance": f"${AUM_RECON_TOLERANCE:.2f}", "left_records": len(fee), "right_records": len(sn),
        "compared_records": n_both, "matched": int(match.sum()), "mismatched": n_both - int(match.sum()),
        "match_rate_pct": pct(int(match.sum()), n_both),
        "left_only": int(j["_merge"].eq("left_only").sum()), "right_only": int(j["_merge"].eq("right_only").sum()),
        "left_total": round(float(to_num(fee["aum_basis"]).sum()), 2),
        "right_total": round(float(to_num(sn["aum"]).sum()), 2),
        "total_difference_left_minus_right": round(float(diff[both].sum()), 2),
        "median_abs_difference": round(float(diff[both].abs().median()), 2),
        "max_abs_difference": round(float(diff[both].abs().max()), 2), "ratio_p10_median_p90": "",
        "note": (f"left = fee rows, right = snapshots; right_only = snapshot client-months with no fee "
                 f"(Decision 11). {placeholder_both} matched pairs are both 1000, so agreement does not "
                 "validate those balances")}
    unmatched = j.loc[j["_merge"].eq("right_only"), ["snapshot_id", "client_id", "snapshot_date", "aum"]]
    return summary, unmatched.sort_values(["client_id", "snapshot_date"]).reset_index(drop=True)


# =============================================================================
# 8. IQR OUTLIERS
# =============================================================================
IQR_COLUMNS = [  # unbounded above: money amounts with no natural maximum
    ("fact_portfolio_snapshot", "aum", None, "flag_aum_equals_1000"),
    ("fact_fees", "amount", None, "flag_aum_basis_equals_1000"),
    ("fact_transactions", "amount", ["Buy", "Sell"], None),
    ("dim_client", "initial_aum", None, None),
]


def iqr_outliers(T, layer):
    rows = []
    for t, col, types, flag in IQR_COLUMNS:
        df = T[t] if types is None else T[t][T[t]["txn_type"].isin(types)]
        v = to_num(df[col]).dropna()
        for scale in ("linear", "log10"):
            x = np.log10(v[v > 0]) if scale == "log10" else v
            q1, med, q3 = x.quantile([0.25, 0.5, 0.75])   # pandas default: linear interpolation
            iqr = q3 - q1
            lo, hi = q1 - IQR_K * iqr, q3 + IQR_K * iqr
            low, high = x < lo, x > hi
            n_out = int((low | high).sum())
            back = (lambda z: 10 ** z) if scale == "log10" else (lambda z: z)
            flagged_out = ""
            if flag and flag in df.columns:
                flagged_out = int(df.loc[x.index[low | high], flag].eq("1").sum())
            rows.append({
                "layer": layer, "table": t, "column": col, "subset": "txn_type in " + "/".join(types) if types else "all rows",
                "scale": scale, "n": len(x), "q1": round(float(back(q1)), 4), "median": round(float(back(med)), 4),
                "q3": round(float(back(q3)), 4), "iqr": round(float(iqr), 4),
                "lower_fence": round(float(back(lo)), 4), "upper_fence": round(float(back(hi)), 4),
                "low_outliers": int(low.sum()), "high_outliers": int(high.sum()), "outliers": n_out,
                "outlier_pct": pct(n_out, len(x)), "min": round(float(v.min()), 4), "max": round(float(v.max()), 4),
                "outliers_also_flagged": flagged_out,
                "note": ("IQR on log10 values (fences converted back to $); iqr column is in log10 units"
                         if scale == "log10" else "IQR on raw values")})
    return rows


# =============================================================================
# 9. SCORECARD
# =============================================================================
def structural_blank_allowance(T, t):
    """Cells where a blank is legitimate by design (Decision 10) - excluded from completeness."""
    df = T[t]
    allow = pd.DataFrame(False, index=df.index, columns=list(COLUMN_TYPES[t]))
    if t == "dim_security":
        etf = df["is_etf"].eq("True")
        allow["sector"] = etf
        allow["expense_ratio_bps"] = ~etf
    if t == "fact_transactions":
        cash = df["txn_type"].isin(CASH_TXN_TYPES)
        for c in ("security_id", "shares", "price"):
            allow[c] = cash
    return allow


def scorecard_row(T, t, layer, files, ri_rows, rules, flags_rows):
    df = T[t]
    cols = list(COLUMN_TYPES[t])            # original business columns only (flags excluded)
    sub = df[cols]
    is_blank = sub.apply(blank)
    null_tok = sub.apply(lambda s: s.str.strip().str.upper().isin(NULL_TOKENS))
    missing = is_blank | null_tok           # 'None' is not a null token here (real certification value)
    allow = structural_blank_allowance(T, t)
    expected_cells = int((~allow).to_numpy().sum())
    structural_blanks = int((allow & is_blank).to_numpy().sum())
    populated_expected = int((~missing & ~allow).to_numpy().sum())

    typed = [c for c in cols if COLUMN_TYPES[t][c] != "text"]
    tv_den = tv_ok = num_den = num_plain = 0
    for c in typed:
        nb = ~is_blank[c]
        tv_den += int(nb.sum())
        tv_ok += int((type_valid(sub[c], COLUMN_TYPES[t][c]) & nb).sum())
        if COLUMN_TYPES[t][c] in ("int", "decimal"):
            num_den += int(nb.sum())
            num_plain += int((nb & ~sub[c].str.contains(r"[eE]", regex=True)).sum())

    pk = df[PRIMARY_KEYS[t]]
    pk_unique = int(pk[~blank(pk)].nunique())

    tr = [r for r in rules.rows if r["table"] == t]
    biz = [r for r in tr if r["scored_as"] == "business_rule"]
    b_app = sum(r["applicable_rows"] for r in biz)
    b_pass = sum(r["passed_rows"] for r in biz)
    susp_mask = pd.Series(False, index=df.index)
    conf_mask = pd.Series(False, index=df.index)
    for r in tr:
        if r["scored_as"] == "suspicious":
            susp_mask |= rules.masks[r["rule_id"]]
        if r["classification"] == "Confirmed error":
            conf_mask |= rules.masks[r["rule_id"]]

    ri = [r for r in ri_rows if r["child_table"] == t]
    ri_checked = sum(r["checked_rows"] for r in ri)
    ri_matched = sum(r["matched_rows"] for r in ri)
    flags = [f for f in flags_rows if f["table"] == t] if layer == "clean" else []

    return {
        "layer": layer, "table": t, "file": files[t].name, "rows": len(df), "business_columns": len(cols),
        "flag_columns": len([c for c in df.columns if c.startswith("flag_")]),
        "completeness_pct": pct(populated_expected, expected_cells),
        "completeness_denominator": f"{expected_cells:,} required cells (rows x business columns minus "
                                    f"{int(allow.to_numpy().sum()):,} structural-blank cells)",
        "structural_blank_cells_excluded": structural_blanks,
        "pk_uniqueness_pct": pct(pk_unique, len(df)),
        "type_validity_pct": pct(tv_ok, tv_den),
        "type_validity_denominator": f"{tv_den:,} non-blank cells in {len(typed)} typed columns",
        "numeric_format_consistency_pct": pct(num_plain, num_den),
        "business_rule_pass_pct": pct(b_pass, b_app),
        "business_rule_denominator": f"{b_app:,} row-checks across {len(biz)} Confirmed-error/Structural rules",
        "confirmed_error_rows": int(conf_mask.sum()),
        "confirmed_error_row_pct": pct(int(conf_mask.sum()), len(df)),
        "suspicious_rows": int(susp_mask.sum()),
        "suspicious_row_pct": pct(int(susp_mask.sum()), len(df)),
        "referential_match_pct": pct(ri_matched, ri_checked) if ri else "N/A",
        "referential_denominator": (f"{ri_checked:,} non-blank FK values across {len(ri)} relationship(s)"
                                    if ri else "no outgoing foreign keys"),
        "flags_valid": ("N/A (raw has no flags)" if layer == "raw" else
                        ("N/A (no flags)" if not flags else
                         f"{sum(f['status'] == 'PASS' for f in flags)}/{len(flags)} PASS")),
    }


# =============================================================================
# 10. SUMMARY MARKDOWN
# =============================================================================
def write_summary(score, ri_df, rules_df, flags_df, recon_df, out_df, layer_diffs):
    L = []
    w = L.append
    w("# Part D Validation Summary\n")
    w("Generated by `scripts/03_validate.py`. Raw and clean layers are validated with identical checks.\n")
    w("## Labels\n")
    w("* **Confirmed error** - violating the rule is a definite defect (logical/standard rules, or the decision "
      "log calls it a confirmed error). Scored in the business-rule pass rate.")
    w("* **Structural** - blank-pattern rules (e.g. cash transactions have no security). A legitimate structural "
      "blank PASSES; only a wrong pattern fails. Scored in the business-rule pass rate.")
    w("* **Suspicious** - looks wrong or conflicts with other data but is not proven wrong. Reported as "
      "suspicious rows, NOT counted as business-rule failures.")
    w("* **Informational** - observed patterns and category sets read off the data. Reported only.")
    w("* **Basis** - Documented (decision log) / Logical/standard / Observed in data / Assumed threshold. "
      "Assumed thresholds: age 18-120, years_experience 0-70, expense ratio <= 1000 bps, return_ytd -0.5..1.0, "
      "bps <= 100, $1 fee tolerance, $0.01 AUM tolerance.\n")
    w("## Scorecard denominators\n")
    w("* **completeness_pct** = populated required cells / required cells. Required cells = rows x original business "
      "columns minus structural-blank cells (ETF sector, non-ETF expense ratio, security_id/shares/price on cash "
      "transactions). Literal `None` in certifications counts as populated. Flag columns are excluded so adding "
      "flags cannot raise the score.")
    w("* **pk_uniqueness_pct** = distinct non-blank primary keys / rows.")
    w("* **type_validity_pct** = non-blank cells that parse as their type / non-blank cells in typed (non-text) "
      "columns. Scientific notation is a valid number, so it does not lower type validity.")
    w("* **numeric_format_consistency_pct** = numeric cells in plain (non-scientific) notation / numeric cells. "
      "This is the only metric the cleaning step was designed to change (Decision 16).")
    w("* **business_rule_pass_pct** = passing row-checks / applicable row-checks, over Confirmed-error and "
      "Structural rules (row-weighted; each rule contributes its applicable rows).")
    w("* **confirmed_error_row_pct** = rows failing at least one Confirmed-error rule / rows. This is row-based, "
      "unlike the row-weighted business_rule_pass_pct, so rules that pass on every row cannot dilute it.")
    w("* **suspicious_row_pct** = rows failing at least one Suspicious rule / rows.")
    w("* **referential_match_pct** = matched non-blank FK values / non-blank FK values across the table's outgoing "
      "relationships. Blank security_id on cash transactions is structural and excluded (reported as null-key "
      "rate). Tables with no outgoing FK show N/A.")
    w("* Flags do not change any score: clean rows that are flagged still fail the same rules as raw.\n")
    w("## Scorecard\n")
    cols = ["layer", "table", "completeness_pct", "pk_uniqueness_pct", "type_validity_pct",
            "numeric_format_consistency_pct", "business_rule_pass_pct", "confirmed_error_rows", "suspicious_row_pct",
            "referential_match_pct", "flags_valid"]
    w("| " + " | ".join(cols) + " |")
    w("|" + "---|" * len(cols))
    for _, r in score[cols].iterrows():
        w("| " + " | ".join(str(r[c]) for c in cols) + " |")

    # Confirmed-error rows: row-based view of the Confirmed-error rules, from the computed scorecard and rules.
    w("\n### Confirmed-error rows\n")
    hit = score[score["confirmed_error_rows"] > 0]
    hit_tables = list(dict.fromkeys(hit["table"]))
    if not hit_tables:
        w("`confirmed_error_row_pct` (from `data_quality_scorecard.csv`) is 0.0 for every table in both layers.")
    else:
        same = all(score.loc[(score.layer == "raw") & (score.table == t), ["confirmed_error_rows"]].values.tolist()
                   == score.loc[(score.layer == "clean") & (score.table == t), ["confirmed_error_rows"]].values.tolist()
                   for t in hit_tables)
        w("`confirmed_error_row_pct` (from `data_quality_scorecard.csv`) is 0.0 for every table except "
          + ", ".join(f"`{t}`" for t in hit_tables) + ". "
          + ("It is the same in both layers, because flags mark errors without correcting them.\n" if same else
             "It differs between layers - see the table below.\n"))
        ce_cols = ["layer", "table", "rows", "confirmed_error_rows", "confirmed_error_row_pct", "business_rule_pass_pct"]
        w("| " + " | ".join(ce_cols) + " |")
        w("|" + "---|" * len(ce_cols))
        for _, r in hit[ce_cols].iterrows():
            w(f"| {r['layer']} | {r['table']} | {r['rows']:,} | {r['confirmed_error_rows']:,} | "
              f"{r['confirmed_error_row_pct']} | {r['business_rule_pass_pct']} |")
        # How each failing Confirmed-error rule's rows are described (wording only; counts come from rules_df).
        subjects = {"T08": ("dividends", "have amounts equal to the full position value"),
                    "T10": ("in-window Sells", "are first oversells")}
        clean_rules = rules_df[rules_df["layer"] == "clean"]
        for t in hit_tables:
            row = score[(score.layer == "clean") & (score.table == t)].iloc[0]
            tr = clean_rules[clean_rules["table"] == t]
            fail = tr[(tr["classification"] == "Confirmed error") & (tr["failed_rows"] > 0)]
            disjoint = int(fail["failed_rows"].sum()) == int(row["confirmed_error_rows"])
            w(f"\nIn `{t}`, the {row['confirmed_error_rows']:,} rows come from {len(fail)} Confirmed-error rule(s) "
              f"(`validation_rules.csv`)" + (", which do not overlap (no row fails more than one):" if disjoint else
                                              "; some rows fail more than one:"))
            for _, f in fail.iterrows():
                noun, verb = subjects.get(f["rule_id"], ("applicable rows", f"fail: {f['rule']}"))
                ref = f" ({f['decision_ref']})" if f["decision_ref"] else ""
                w(f"* **{f['rule_id']}{ref}:** {f['failed_rows']:,} of {f['applicable_rows']:,} {noun} "
                  f"({f['fail_rate_pct']:.3f}%) {verb}.")
            biz = tr[tr["scored_as"] == "business_rule"]
            clean_pass = int((biz["failed_rows"] == 0).sum())
            pct_err = float(row["confirmed_error_row_pct"])
            w(f"\nThe {row['business_rule_pass_pct']}% business-rule pass rate is row-weighted across {len(biz)} rules, "
              f"{clean_pass} of which pass on every row. So about 1 in {round(100 / pct_err)} rows still carries a "
              "confirmed, unresolved error.")
    w("\n## Raw vs clean\n")
    for t, n, changed in layer_diffs:
        if n:
            w(f"* `{t}`: {n:,} cells differ ({', '.join(changed)}); all numerically identical (format only).")
    w("* All other business cells are identical, so every score except numeric format consistency is the same "
      "for raw and clean. This is expected: Part C preserved values and added flags rather than correcting data.\n")
    w("## Referential integrity\n")
    w(f"{int((ri_df['status'] == 'PASS').sum())} of {len(ri_df)} relationship checks pass (13 per layer). "
      f"Orphan rows: {int(ri_df['orphan_rows'].sum())}.\n")
    w("## Reconciliations\n")
    for _, r in recon_df[recon_df["layer"] == "clean"].iterrows():
        w(f"* **{r['reconciliation']}** ({r['variant']}): {r['compared_records']:,} compared, "
          f"{r['matched']:,} matched, {r['mismatched']:,} mismatched ({r['match_rate_pct']}% match); "
          f"left-only {r['left_only']:,}, right-only {r['right_only']:,}. {r['note']}")
    w("\n## Outliers (IQR, k = 1.5)\n")
    w("Outliers are values far from the middle of the distribution. They are leads for review, not proof of "
      "error: AUM and transaction sizes are right-skewed and multi-modal (Retail/HNW/UHNW tiers), so large "
      "legitimate balances fall above the linear upper fence. The log10 rows show the same test on a log scale.\n")
    w("## Flags\n")
    w(f"{int((flags_df['status'] == 'PASS').sum())} of {len(flags_df)} flags pass (0/1 values, decision-log count, "
      "and identical to the rule recomputed in this script).\n")
    w("## Ambiguities and assumptions\n")
    for a in [
        "No official Part D instructions file was available; requirements were taken from the assignment prompt.",
        "R1 compares against all fact_fees rows (as Part A did) and against fee_type = Advisory only, because "
        "it is undocumented which fee rows the Advisory Fee transactions represent.",
        "R2 matches on client_id and month; fee_date and snapshot_date are both the 1st, so exact date equality is used.",
        "Business-rule pass rate uses only Confirmed-error and Structural rules. Suspicious findings are shown "
        "separately so unproven issues do not count as defects.",
        "In-window oversells (Decision 2) and dividend amounts (Decision 1) are Confirmed errors, so "
        "fact_transactions scores below 100% in BOTH layers - flags do not fix them.",
        "Holiday check covers only Jan 1, Jul 4 and Dec 25 (as in Part A); floating holidays are not tested.",
        "The fee amount formula (F07) and one-advisor-per-client rules (P17/T19) are inferred from the data and "
        "are not scored.",
        f"Future-date checks use the fixed Part A reference date {REFERENCE_DATE.date()}.",
        "IQR uses pandas' default linear-interpolation quantiles.",
    ]:
        w(f"* {a}")
    safe_output("validation_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")


# =============================================================================
# 11. MAIN
# =============================================================================
def write_csv(df, name):
    df.to_csv(safe_output(name), index=False, encoding="utf-8", lineterminator="\n")
    print(f"  wrote reports/{name}  ({len(df):,} rows)")


def main():
    print("=" * 78 + "\nHW4 Part D - 03_validate.py\n" + f"hw04 folder: {HW_DIR}\n" + "=" * 78)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[1] Loading inputs (as text)")
    T_raw, F_raw = load_layer("raw")
    T_clean, F_clean = load_layer("clean")
    guarded = list(F_raw.values()) + list(F_clean.values()) + [
        p for p in [HW_DIR / "decision_log.md", SCRIPT_DIR / "01_profile.py", SCRIPT_DIR / "02_clean.py",
                    REPORTS_DIR / "dq_summary_raw.csv", REPORTS_DIR / "cleaning_report.md"] if p.is_file()]
    hashes = {p: sha256(p) for p in guarded}

    print("\n[2] Raw vs clean layer consistency")
    layer_diffs = compare_layers(T_raw, T_clean)
    for t, n, changed in layer_diffs:
        print(f"  {t:<25} rows equal | changed business cells {n:>6,} {changed if changed else ''}")

    print("\n[3] Referential integrity (13 relationships x 2 layers)")
    ri_rows = run_referential(T_raw, "raw") + run_referential(T_clean, "clean")
    ri_df = pd.DataFrame(ri_rows)
    for _, r in ri_df.iterrows():
        print(f"  {r['layer']:<5} {r['relationship']:<62} match {r['match_rate_pct']:>7}% | orphans "
              f"{r['orphan_rows']:>3} ({r['distinct_orphan_keys']} keys) | null {r['null_key_rate_pct']:>6}% | {r['status']}")
    check(ri_df["match_rate_pct"].between(0, 100).all(), "match rates must be within 0-100")

    print("\n[4] Business rules")
    R_raw, R_clean = build_rules(T_raw, "raw"), build_rules(T_clean, "clean")
    rules_df = pd.DataFrame(R_raw.rows + R_clean.rows)
    for _, r in rules_df[rules_df["layer"] == "clean"].iterrows():
        mark = "ok " if r["failed_rows"] == 0 else "!! "
        print(f"  {mark}{r['rule_id']} {r['table']:<24} {r['classification']:<15} fail {r['failed_rows']:>7,} / "
              f"{r['applicable_rows']:>7,}  {r['rule'][:70]}")
    raw_fail = rules_df[rules_df.layer == "raw"].set_index("rule_id")["failed_rows"]
    clean_fail = rules_df[rules_df.layer == "clean"].set_index("rule_id")["failed_rows"]
    check(raw_fail.equals(clean_fail), "rule results differ between raw and clean although values were preserved")
    print("  (raw results identical to clean for every rule)")

    print("\n[5] Flag validation (clean)")
    flags_df = pd.DataFrame(validate_flags(T_raw, T_clean, R_clean))
    for _, r in flags_df.iterrows():
        print(f"  {r['status']}  {r['table']:<24} {r['flag']:<42} {r['rows_flagged']:>6,} "
              f"(decision log {r['expected_rows_decision_log']:>6,}; = rule {r['recomputed_rule']}: {r['agrees_with_rule']})")

    print("\n[6] Cross-table reconciliations")
    recon_rows, detail, unmatched = [], None, None
    for layer, T in (("raw", T_raw), ("clean", T_clean)):
        s1, d1 = recon_advisory_fees(T, layer)
        s2, u2 = recon_aum_basis(T, layer)
        recon_rows += s1 + [s2]
        if layer == "clean":
            detail, unmatched = d1, u2
    recon_df = pd.DataFrame(recon_rows)
    num_cols = ["compared_records", "matched", "left_only", "right_only"]
    check(recon_df[recon_df.layer == "raw"][num_cols].reset_index(drop=True).equals(
        recon_df[recon_df.layer == "clean"][num_cols].reset_index(drop=True)), "reconciliation differs by layer")
    for _, r in recon_df[recon_df.layer == "clean"].iterrows():
        print(f"  {r['reconciliation']} [{r['variant']}]")
        print(f"     compared {r['compared_records']:,} | matched {r['matched']:,} | mismatched {r['mismatched']:,} | "
              f"match {r['match_rate_pct']}% | left-only {r['left_only']:,} | right-only {r['right_only']:,}")
        print(f"     totals left ${r['left_total']:,.2f} vs right ${r['right_total']:,.2f} | median |diff| "
              f"${r['median_abs_difference']:,.2f} | max |diff| ${r['max_abs_difference']:,.2f}")

    print("\n[7] IQR outliers (clean values; raw values are identical)")
    out_df = pd.DataFrame(iqr_outliers(T_clean, "clean"))
    raw_out = pd.DataFrame(iqr_outliers(T_raw, "raw"))
    check(out_df["outliers"].equals(raw_out["outliers"]), "IQR results differ between layers")
    for _, r in out_df.iterrows():
        print(f"  {r['table']}.{r['column']} ({r['subset']}, {r['scale']}): Q1 {r['q1']:,.2f}  Q3 {r['q3']:,.2f}  "
              f"fences [{r['lower_fence']:,.2f}, {r['upper_fence']:,.2f}]  outliers {r['outliers']:,} ({r['outlier_pct']}%)")
    check(len(out_df) >= 4, "IQR must cover at least two columns")

    print("\n[8] Scorecard")
    # ri_rows holds both layers - each scorecard row uses only its own layer's relationships
    score =pd.DataFrame([scorecard_row(T, t, layer, F, [r for r in ri_rows if r["layer"] == layer], R,
                                        flags_df.to_dict("records"))
                          for layer, T, F, R in (("raw", T_raw, F_raw, R_raw), ("clean", T_clean, F_clean, R_clean))
                          for t in TABLES])
    check(len(score) == 16 and not score.duplicated(["layer", "table"]).any(), "scorecard must have 16 unique rows")
    for c in ["completeness_pct", "pk_uniqueness_pct", "type_validity_pct", "numeric_format_consistency_pct",
              "business_rule_pass_pct", "suspicious_row_pct"]:
        check(pd.to_numeric(score[c]).between(0, 100).all(), f"{c} must be within 0-100")
    no_inflation = ["completeness_pct", "pk_uniqueness_pct", "type_validity_pct", "business_rule_pass_pct",
                    "confirmed_error_rows", "suspicious_rows", "referential_match_pct"]
    r_s, c_s = score[score.layer == "raw"].reset_index(drop=True), score[score.layer == "clean"].reset_index(drop=True)
    check(r_s[no_inflation].equals(c_s[no_inflation]), "clean scores differ from raw on value-based metrics")
    show = ["layer", "table", "completeness_pct", "pk_uniqueness_pct", "type_validity_pct",
            "numeric_format_consistency_pct", "business_rule_pass_pct", "suspicious_row_pct", "referential_match_pct"]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(score[show].to_string(index=False))

    print("\n[9] Writing reports")
    write_csv(score, "data_quality_scorecard.csv")
    write_csv(ri_df, "validation_referential_integrity.csv")
    write_csv(rules_df, "validation_rules.csv")
    write_csv(flags_df, "validation_flags.csv")
    write_csv(recon_df, "validation_reconciliation.csv")
    write_csv(detail, "validation_recon_advisory_fee_detail.csv")
    write_csv(unmatched, "validation_snapshot_months_without_fee.csv")
    write_csv(pd.concat([out_df, raw_out], ignore_index=True), "validation_outliers.csv")
    write_summary(score, ri_df, rules_df, flags_df, recon_df, out_df, layer_diffs)
    print("  wrote reports/validation_summary.md")

    back = pd.read_csv(safe_output("data_quality_scorecard.csv"))
    check(len(back) == 16, "written scorecard must have exactly 16 rows")
    for p, h in hashes.items():
        check(sha256(p) == h, f"INPUT FILE CHANGED during validation: {p}")
    print(f"\nInputs unchanged ({len(hashes)} files, SHA-256 identical before and after).")
    print("DONE - all structural assertions passed. Findings are reported, not asserted away.")


if __name__ == "__main__":
    try:
        main()
    except (ValidationError, AssertionError) as exc:
        print(f"\nVALIDATION STOPPED: {exc}", file=sys.stderr)
        sys.exit(1)
