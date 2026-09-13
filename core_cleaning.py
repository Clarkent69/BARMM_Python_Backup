from __future__ import annotations
import re
from datetime import timezone, timedelta
import pandas as pd
import config
from normalizers import (
    normalize_headers,
    to_whole_number_strict,
    clean_text,
    build_contest_candidate_key,
    ensure_columns,
    MissingRequiredColumn,
)

PH_TZ = timezone(timedelta(hours=config.PHILIPPINE_TZ_OFFSET_HOURS))

# Known BARMM region-wide contest codes – used to classify contest scope
REGION_WIDE_CONTEST_CODES = {
    "01295000", "01395000", "01495000",
    "01595000", "01695000", "01795000",
}

# ---------------------------------------------------------------------------
# PRECINCTS MASTER  
# ---------------------------------------------------------------------------

def clean_precincts() -> pd.DataFrame:
    """
    Clean precincts.csv → precincts_master.

    Output columns (in stable order):
        ACM_ID, REGION, PROVINCE, MUNICIPALITY, BARANGAY,
        POLLING_PLACE, CLUSTERED_PRECINCT, REGISTERED_VOTERS
    """
    df = pd.read_csv(config.RAW_PRECINCTS, dtype=str)
    df = normalize_headers(df)

    # Alias COMELEC standard headers if present
    header_aliases = {
        "PRV_NAME": "PROVINCE",
        "MUN_NAME": "MUNICIPALITY",
        "BRGY_NAME": "BARANGAY",
        "POLLPLACE": "POLLING_PLACE",
        "CLUSTERED_PREC": "CLUSTERED_PRECINCT"
    }
    df = df.rename(columns=header_aliases)
    df = ensure_columns(df, "precincts")

    # Clean identifiers
    df["ACM_ID"]       = clean_text(df["ACM_ID"])
    df["PROVINCE"]     = clean_text(df["PROVINCE"])
    df["MUNICIPALITY"] = clean_text(df["MUNICIPALITY"])
    df["BARANGAY"]     = clean_text(df["BARANGAY"])

    if "REGION" in df.columns:
        df["REGION"]   = clean_text(df["REGION"])
    if "POLLING_PLACE" in df.columns:
        df["POLLING_PLACE"] = clean_text(df["POLLING_PLACE"])
    if "CLUSTERED_PRECINCT" in df.columns:
        df["CLUSTERED_PRECINCT"] = clean_text(df["CLUSTERED_PRECINCT"])

    df["REGISTERED_VOTERS"] = to_whole_number_strict(df["REGISTERED_VOTERS"])

    # Return in stable column order
    stable_cols = [
        "ACM_ID", "REGION", "PROVINCE", "MUNICIPALITY", "BARANGAY",
        "POLLING_PLACE", "CLUSTERED_PRECINCT", "REGISTERED_VOTERS",
    ]
    available = [c for c in stable_cols if c in df.columns]
    return df[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# CANDIDATES MASTER 
# ---------------------------------------------------------------------------

# Specific name repair described in the Dataflow README
_MISSING_CLOSE_PAREN = re.compile(r"^(.*\([^)]+)$")


def _repair_candidate_name(name: str | None) -> str | None:
    """Append a closing parenthesis when a name has an unmatched opening paren."""
    if name and _MISSING_CLOSE_PAREN.match(name):
        return name + ")"
    return name


def clean_candidates() -> pd.DataFrame:
    """
    Clean candidates.csv → candidates_master.

    Adds CONTEST_CANDIDATE_KEY = CONTEST_CODE | CANDIDATE_CODE.
    """
    df = pd.read_csv(config.RAW_CANDIDATES, dtype=str)
    df = normalize_headers(df)
    df = ensure_columns(df, "candidates")

    df["CONTEST_CODE"]   = clean_text(df["CONTEST_CODE"])
    df["CANDIDATE_CODE"] = clean_text(df["CANDIDATE_CODE"])
    df["CANDIDATE_NAME"] = clean_text(df["CANDIDATE_NAME"])

    if "PARTIES_CODE" in df.columns:
        df["PARTIES_CODE"] = clean_text(df["PARTIES_CODE"])

    # Mechanical name repair (one specific case in DEV data)
    df["CANDIDATE_NAME"] = df["CANDIDATE_NAME"].map(_repair_candidate_name)

    df["CONTEST_CANDIDATE_KEY"] = build_contest_candidate_key(df)

    stable_cols = [
        "CONTEST_CODE", "CANDIDATE_CODE", "CANDIDATE_NAME",
        "PARTIES_CODE", "CONTEST_CANDIDATE_KEY",
    ]
    available = [c for c in stable_cols if c in df.columns]
    return df[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# CONTESTS MASTER  
# ---------------------------------------------------------------------------

def clean_contests() -> pd.DataFrame:
    """
    Clean contest.csv → contests_master.

    Classifies region-wide vs local-district scope.
    NOTE: NUM_SEATS is reference data only — not confirmed as max voter selections.
    """
    df = pd.read_csv(config.RAW_CONTESTS, dtype=str)
    df = normalize_headers(df)
    df = ensure_columns(df, "contests")

    df["CONTEST_CODE"] = clean_text(df["CONTEST_CODE"])
    df["CONTEST_NAME"] = clean_text(df["CONTEST_NAME"])

    if "NUM_SEATS" in df.columns:
        df["NUM_SEATS"] = to_whole_number_strict(df["NUM_SEATS"])

    # Classify scope
    df["CONTEST_SCOPE"] = df["CONTEST_CODE"].map(
        lambda c: "Region-wide" if c in REGION_WIDE_CONTEST_CODES else "Local district"
    )

    stable_cols = ["CONTEST_CODE", "CONTEST_NAME", "NUM_SEATS", "CONTEST_SCOPE"]
    available   = [c for c in stable_cols if c in df.columns]
    return df[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# PARTIES MASTER  
# ---------------------------------------------------------------------------

def clean_parties() -> pd.DataFrame:
    """Clean parties.csv → parties_master."""
    df = pd.read_csv(config.RAW_PARTIES, dtype=str)
    df = normalize_headers(df)
    df = ensure_columns(df, "parties")

    df["PARTIES_CODE"] = clean_text(df["PARTIES_CODE"])
    df["PARTIES_NAME"] = clean_text(df["PARTIES_NAME"])
    if "PARTIES_ALIAS" in df.columns:
        df["PARTIES_ALIAS"] = clean_text(df["PARTIES_ALIAS"])

    stable_cols = ["PARTIES_CODE", "PARTIES_NAME", "PARTIES_ALIAS"]
    available   = [c for c in stable_cols if c in df.columns]
    return df[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# RESULTS SNAPSHOT 
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%m-%d-%Y:%H:%M:%S",   
    "%Y-%m-%d %H:%M:%S", 
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M", 
    "%Y-%m-%dT%H:%M:%S", 
    "%Y-%m-%d"
]


def _parse_reception_date(series: pd.Series) -> pd.Series:
    """Try multiple datetime formats; unparseable values become NaT."""
    result = pd.Series(pd.NaT, index=series.index)
    for fmt in _DATE_FORMATS:
        mask = result.isna() & series.notna()
        try:
            parsed = pd.to_datetime(series[mask], format=fmt, errors="coerce")
            result[mask] = parsed
        except Exception:
            pass
    return result


def clean_results() -> pd.DataFrame:
    df = pd.read_csv(config.RAW_RESULTS, dtype=str)
    df = normalize_headers(df)

    if "PARTY_CODE" in df.columns and "PARTIES_CODE" not in df.columns:
        df = df.rename(columns={"PARTY_CODE": "PARTIES_CODE"})

    df = ensure_columns(df, "results")

    for col in ["PRECINCT_CODE", "CONTEST_CODE", "CANDIDATE_CODE"]:
        df[col] = clean_text(df[col])

    if "PARTIES_CODE" in df.columns:
        df["PARTIES_CODE"] = clean_text(df["PARTIES_CODE"])

    # Strict integer coercion
    for col in ["VOTES_AMOUNT", "NUMBER_VOTERS", "UNDER_VOTES", "OVER_VOTES"]:
        df[col] = to_whole_number_strict(df[col])

    if "TOTALIZATION_ORDER" in df.columns:
        df["TOTALIZATION_ORDER"] = to_whole_number_strict(df["TOTALIZATION_ORDER"])

    # Composite key
    df["CONTEST_CANDIDATE_KEY"] = build_contest_candidate_key(df)

    # Parse reception date into datetime
    df["RECEPTION_DATETIME"] = _parse_reception_date(df["RECEPTION_DATE"])

    # Source metadata
    df["SOURCE_FILE"] = config.RAW_RESULTS.name
    df["ENVIRONMENT"] = config.ENVIRONMENT

    stable_cols = [
        "PRECINCT_CODE", "CONTEST_CODE", "CANDIDATE_CODE", "PARTIES_CODE",
        "VOTES_AMOUNT", "TOTALIZATION_ORDER", "NUMBER_VOTERS", "UNDER_VOTES",
        "OVER_VOTES", "RECEPTION_DATE", "RECEPTION_DATETIME",
        "CONTEST_CANDIDATE_KEY", "SOURCE_FILE", "ENVIRONMENT",
    ]
    available = [c for c in stable_cols if c in df.columns]
    return df[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# MOBILE ELECTION RETURNS 
# ---------------------------------------------------------------------------

def clean_mobile_ers() -> pd.DataFrame:
    """
    Clean election_returns.csv -> mobile_election_returns.

    Preserves ALL rows (including duplicates/resubmissions) in this table.
    Canonical filtering happens in clean_mobile_er_canonical().
    """
    df = pd.read_csv(config.RAW_MOBILE_ER, dtype=str)
    df = normalize_headers(df)  # all columns become UPPER_SNAKE_CASE

    # Required fields check (now using uppercase names)
    for req_col in ["ER_ID", "CLUSTERED_PRECINCT_ID", "IS_DUPLICATE", "RECEIVED_AT"]:
        if req_col not in df.columns:
            raise MissingRequiredColumn(
                f"[mobile_er] Required column '{req_col}' is missing."
            )

    # Clean IDs
    df["ER_ID"]                 = clean_text(df["ER_ID"])
    df["CLUSTERED_PRECINCT_ID"] = clean_text(df["CLUSTERED_PRECINCT_ID"])

    # Parse duplicate flag
    df["IS_DUPLICATE"] = df["IS_DUPLICATE"].str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )

    # Parse timestamp
    df["RECEIVED_AT"] = pd.to_datetime(df["RECEIVED_AT"], errors="coerce", utc=True)

    # Optional numeric fields
    for num_col in ["REGISTERED_VOTERS", "BALLOTS_COUNTED"]:
        if num_col in df.columns:
            df[num_col] = to_whole_number_strict(df[num_col])

    # Add pipeline metadata
    env_status = "PRODUCTION" if config.ENVIRONMENT == "Production" else "DEVELOPMENT_TEST"
    df["ENVIRONMENT"]      = config.ENVIRONMENT
    df["DATA_STATUS"]      = env_status
    df["IS_CANONICAL"]     = False
    df["SUBMISSION_STATUS"] = "ALL_RECORDS"

    # Expose lowercase aliases for internal join compatibility
    df["er_id"]                 = df["ER_ID"]
    df["clustered_precinct_id"] = df["CLUSTERED_PRECINCT_ID"]
    df["is_duplicate"]          = df["IS_DUPLICATE"]
    df["received_at"]           = df["RECEIVED_AT"]

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# MOBILE ELECTION RETURNS 
# ---------------------------------------------------------------------------

def clean_mobile_er_canonical(mobile_ers: pd.DataFrame) -> pd.DataFrame:
    """
    Filter mobile ERs to canonical submissions only (is_duplicate == False).

    Returns mobile_election_returns_canonical.
    One clustered_precinct_id should appear AT MOST ONCE here.
    QA_Mobile_Multiple_Canonical_ER_Per_Precinct checks this.
    """
    canonical = mobile_ers[mobile_ers["is_duplicate"] == False].copy()
    canonical["IS_CANONICAL"]      = True
    canonical["SUBMISSION_STATUS"] = "CANONICAL"
    canonical["clustered_precinct_id"] = clean_text(canonical["clustered_precinct_id"])
    return canonical.reset_index(drop=True)


# ---------------------------------------------------------------------------
# MOBILE QR RESULTS 
# ---------------------------------------------------------------------------

def clean_mobile_qr() -> pd.DataFrame:
    """
    Clean qr_results.csv -> mobile_qr_results.
    """
    df = pd.read_csv(config.RAW_MOBILE_QR, dtype=str)
    df = normalize_headers(df)  # all columns become UPPER_SNAKE_CASE

    for req_col in ["ER_ID", "DISTRICT_CODE", "CANDIDATE_NO", "VOTES"]:
        if req_col not in df.columns:
            raise MissingRequiredColumn(
                f"[mobile_qr] Required column '{req_col}' is missing."
            )

    df["ER_ID"]         = clean_text(df["ER_ID"])
    df["DISTRICT_CODE"] = clean_text(df["DISTRICT_CODE"])
    df["CANDIDATE_NO"]  = to_whole_number_strict(df["CANDIDATE_NO"])
    df["VOTES"]         = to_whole_number_strict(df["VOTES"])

    # Verification-ready aliases
    df["QR_RACE_CODE"]    = df["DISTRICT_CODE"]
    df["BALLOT_POSITION"] = df["CANDIDATE_NO"]
    df["QR_VOTES"]        = df["VOTES"]

    # Lowercase aliases for join compatibility
    df["er_id"]         = df["ER_ID"]
    df["district_code"] = df["DISTRICT_CODE"]
    df["candidate_no"]  = df["CANDIDATE_NO"]
    df["votes"]         = df["VOTES"]

    df["ENVIRONMENT"]           = config.ENVIRONMENT
    df["VERIFICATION_SOURCE"]   = "QR_PRE_TRANSMISSION"
    df["DATA_STATUS"]           = (
        "PRODUCTION" if config.ENVIRONMENT == "Production" else "DEVELOPMENT_TEST"
    )

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# MOBILE QR RESULTS
# ---------------------------------------------------------------------------

def clean_mobile_qr_canonical(
    mobile_qr: pd.DataFrame,
    er_canonical: pd.DataFrame,
) -> pd.DataFrame:
    """
    Inner join QR results to canonical ERs.

    QR rows whose er_id is not in the canonical ER set are excluded.
    The join brings in clustered_precinct_id and ER metadata.

    Returns mobile_qr_results_canonical.
    """
    er_cols = ["er_id", "clustered_precinct_id", "received_at",
               "IS_CANONICAL", "SUBMISSION_STATUS", "ENVIRONMENT"]
    er_slim = er_canonical[[c for c in er_cols if c in er_canonical.columns]].copy()
    er_slim  = er_slim.add_prefix("ER_").rename(
        columns={"ER_er_id": "er_id"}
    )

    canonical_qr = mobile_qr.merge(er_slim, on="er_id", how="inner")

    if "ER_clustered_precinct_id" in canonical_qr.columns:
        canonical_qr["clustered_precinct_id"] = canonical_qr["ER_clustered_precinct_id"]

    canonical_qr["IS_CANONICAL"]      = True
    canonical_qr["SUBMISSION_STATUS"] = "CANONICAL"

    return canonical_qr.reset_index(drop=True)