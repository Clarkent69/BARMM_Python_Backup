"""
namfrel_backup/verification_engine.py
=======================================
Mirrors the Dataflow Derived verification queries:
    76_verification_csv_results.pq
    79_verification_qr_results.pq
    81_verification_comparison.pq

This is the core of the election verification system.

It compares:
    Transmitted COMELEC CSV results  ←→  Pre-transmission mobile QR returns

at the grain of:
    CLUSTERED_PRECINCT_ID + CONTEST_CODE + CANDIDATE_CODE

CRITICAL SAFETY RULES (from 2026-09-13-production-hardening.md):
1. Both CSV and QR join keys must be strictly unique before the join.
   If duplicates exist, the pipeline stops with CartesianJoinError.
2. NEVER infer missing contest/candidate codes. Unmapped rows are QA failures.
3. The Full Outer Join is intentional: CSV_ONLY and QR_ONLY rows are real findings.
4. VOTE_DIFFERENCE = QR_VOTES - CSV_VOTES (positive = QR higher, negative = QR lower).
"""

from __future__ import annotations
import pandas as pd
import config
from normalizers import clean_text, to_whole_number_strict


# ---------------------------------------------------------------------------
# CUSTOM EXCEPTIONS
# ---------------------------------------------------------------------------

class CartesianJoinError(RuntimeError):
    """
    Raised when duplicate comparison keys are detected before the full outer join.
    A duplicate key would silently multiply vote rows — that is election-critical.
    """


# ---------------------------------------------------------------------------
# COMPARISON STATUS CONSTANTS & SORT ORDER
# ---------------------------------------------------------------------------

STATUS_MATCHED  = "MATCHED"
STATUS_MISMATCH = "MISMATCH"
STATUS_CSV_ONLY = "CSV_ONLY"
STATUS_QR_ONLY  = "QR_ONLY"

STATUS_SORT = {
    STATUS_MATCHED:  1,
    STATUS_MISMATCH: 2,
    STATUS_CSV_ONLY: 3,
    STATUS_QR_ONLY:  4,
}

# Reason codes (mirrors COMPARISON_REASON in the Dataflow)
REASON_VOTES_EQUAL               = "VOTES_EQUAL"
REASON_VOTES_DIFFER              = "VOTES_DIFFER"
REASON_MISSING_VOTE_VALUE        = "MISSING_VOTE_VALUE"
REASON_CSV_CHOICE_MISSING        = "CSV_CHOICE_MISSING"
REASON_QR_MISSING_IN_SCOPE       = "QR_CHOICE_MISSING_FOR_IN_SCOPE_CSV_ROW"
REASON_CSV_OUTSIDE_SCOPE         = "CSV_CONTEST_OUTSIDE_QR_SCOPE"


# ---------------------------------------------------------------------------
# PHASE 1: PREPARE CSV SIDE  (mirrors 76_verification_csv_results.pq)
# ---------------------------------------------------------------------------

def build_csv_side(
    results_snapshot: pd.DataFrame,
    er_canonical:     pd.DataFrame,
) -> pd.DataFrame:
    """
    Prepare the transmitted CSV side for the full outer join.

    The join expects CLUSTERED_PRECINCT_ID; ACM_ID == PRECINCT_CODE for clustered
    precincts, but we carry PRECINCT_CODE directly as the precinct key here
    and alias it to CLUSTERED_PRECINCT_ID in the comparison key.
    """
    df = results_snapshot.copy()

    # Normalize IDs
    df["CLUSTERED_PRECINCT_ID"] = clean_text(df["PRECINCT_CODE"])
    df["CSV_CONTEST_CODE"]      = clean_text(df["CONTEST_CODE"])
    df["CSV_CANDIDATE_CODE"]    = clean_text(df["CANDIDATE_CODE"])
    df["CSV_VOTES"]             = df["VOTES_AMOUNT"]   # already Int64

    # Unique comparison key
    df["COMPARISON_KEY"] = (
        df["CLUSTERED_PRECINCT_ID"] + "|"
        + df["CSV_CONTEST_CODE"]    + "|"
        + df["CSV_CANDIDATE_CODE"]
    )

    df["CSV_EXISTS"]            = True
    df["VERIFICATION_SOURCE"]   = "COMELEC_TRANSMITTED_CSV"
    df["ENVIRONMENT"]           = config.ENVIRONMENT

    keep_cols = [
        "COMPARISON_KEY",
        "CLUSTERED_PRECINCT_ID",
        "CSV_CONTEST_CODE",    # aliased below in comparison
        "CSV_CANDIDATE_CODE",  # aliased below in comparison
        "CONTEST_CANDIDATE_KEY",
        "CSV_VOTES",
        "CSV_EXISTS",
        "VERIFICATION_SOURCE",
        "ENVIRONMENT",
    ]
    return df[[c for c in keep_cols if c in df.columns]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# PHASE 2: PREPARE QR SIDE  (mirrors 79_verification_qr_results.pq)
# ---------------------------------------------------------------------------

def build_qr_side(
    qr_canonical:    pd.DataFrame,
    ballot_map:      pd.DataFrame,
    scope_contests:  set[str],
) -> pd.DataFrame:
    """
    Map canonical QR rows to contest + candidate codes using the ballot choice map.

    A QR row is MAPPING_VALID only when:
    - clustered_precinct_id is present
    - CONTEST_CODE is resolved (not null)
    - CANDIDATE_CODE is resolved (not null)
    - BALLOT_POSITION is present
    - QR_VOTES is present

    Unmapped rows are NOT silently dropped — they are kept with MAPPING_VALID = False
    for QA_QR_Unmapped_Ballot_Choices to detect.
    """
    qr = qr_canonical.copy()
    qr["QR_RACE_CODE"]   = clean_text(qr["QR_RACE_CODE"])
    qr["BALLOT_POSITION"]= to_whole_number_strict(qr["BALLOT_POSITION"].astype(str))

    # Join ballot map: CONTEST_CODE + BALLOT_POSITION → CANDIDATE_CODE
    bmap = ballot_map[["CONTEST_CODE", "BALLOT_POSITION", "CANDIDATE_CODE",
                        "CHOICE_TYPE", "MAPPING_SOURCE"]].copy()

    # Map QR_RACE_CODE → CONTEST_CODE via ballot map's CONTEST_CODE
    # (In the Dataflow, QR_Contest_Map handles overrides; we treat QR_RACE_CODE
    #  as CONTEST_CODE directly since QR_Contest_Map is currently empty)
    qr["_MATCH_CONTEST"]   = qr["QR_RACE_CODE"]
    qr["_MATCH_POSITION"]  = qr["BALLOT_POSITION"]

    merged = qr.merge(
        bmap.rename(columns={"CONTEST_CODE": "_MATCH_CONTEST"}),
        on=["_MATCH_CONTEST", "BALLOT_POSITION"],
        how="left",
    )

    merged["CONTEST_CODE"]   = merged["_MATCH_CONTEST"]
    merged["CANDIDATE_CODE"] = merged["CANDIDATE_CODE"]

    # Build CONTEST_CANDIDATE_KEY
    merged["CONTEST_CANDIDATE_KEY"] = (
        merged["CONTEST_CODE"].fillna("") + "|"
        + merged["CANDIDATE_CODE"].fillna("")
    )

    # Build comparison key (same grain as CSV side)
    precinct_col = "clustered_precinct_id"
    if "ER_clustered_precinct_id" in merged.columns:
        precinct_col = "ER_clustered_precinct_id"

    merged["CLUSTERED_PRECINCT_ID"] = clean_text(merged[precinct_col])

    merged["COMPARISON_KEY"] = (
        merged["CLUSTERED_PRECINCT_ID"].fillna("")     + "|"
        + merged["CONTEST_CODE"].fillna("")            + "|"
        + merged["CANDIDATE_CODE"].fillna("")
    )

    # Mapping validity (all key fields must be non-null)
    merged["MAPPING_VALID"] = (
        merged["CLUSTERED_PRECINCT_ID"].notna()
        & merged["CONTEST_CODE"].notna()
        & merged["CANDIDATE_CODE"].notna()
        & merged["BALLOT_POSITION"].notna()
        & merged["QR_VOTES"].notna()
    )

    merged["QR_EXISTS"]             = True
    merged["VERIFICATION_SOURCE"]   = "QR_PRE_TRANSMISSION"
    merged["ENVIRONMENT"]           = config.ENVIRONMENT

    return merged.reset_index(drop=True)


# ---------------------------------------------------------------------------
# PHASE 3: FULL OUTER JOIN — VERIFICATION COMPARISON  (mirrors 81_verification_comparison.pq)
# ---------------------------------------------------------------------------

def run_verification(
    csv_side:       pd.DataFrame,
    qr_side:        pd.DataFrame,
    scope_df:       pd.DataFrame,
) -> pd.DataFrame:
    """
    Execute the full outer join and produce verification_comparison.

    Steps:
    1. Assert uniqueness of COMPARISON_KEY on both sides → CartesianJoinError if violated.
    2. Full outer join on COMPARISON_KEY.
    3. Resolve CONTEST_CODE, CANDIDATE_CODE from whichever side has them.
    4. Determine IS_QR_VERIFICATION_SCOPE.
    5. Assign COMPARISON_STATUS and COMPARISON_REASON.
    6. Compute VOTE_DIFFERENCE = QR_VOTES - CSV_VOTES.
    7. Build FINAL_COMPARISON_KEY.
    8. Add STATUS_SORT, presence flags, and match/mismatch flags.
    """
    scope_codes = set(scope_df["CONTEST_CODE"].dropna().tolist())

    # --- 1. Pre-join key uniqueness assertion ---
    csv_valid = csv_side.dropna(subset=["COMPARISON_KEY"])
    _assert_unique_key(csv_valid, "COMPARISON_KEY", side="CSV")

    qr_valid = qr_side[qr_side["MAPPING_VALID"] == True].copy()
    _assert_unique_key(qr_valid, "COMPARISON_KEY", side="QR")

    # --- 2. Full outer join ---
    csv_renamed = csv_valid.rename(columns={
        "CSV_CONTEST_CODE":   "CONTEST_CODE",
        "CSV_CANDIDATE_CODE": "CANDIDATE_CODE",
    })
    qr_renamed  = qr_valid[[
        "COMPARISON_KEY", "CLUSTERED_PRECINCT_ID",
        "CONTEST_CODE", "CANDIDATE_CODE", "CONTEST_CANDIDATE_KEY",
        "QR_VOTES", "QR_EXISTS", "MAPPING_VALID",
    ]].rename(columns={
        "CLUSTERED_PRECINCT_ID": "QR_CLUSTERED_PRECINCT_ID",
    })

    merged = csv_renamed.merge(
        qr_renamed,
        on="COMPARISON_KEY",
        how="outer",
        suffixes=("_CSV", "_QR"),
    )

    # --- 3. Resolve unified precinct / contest / candidate ---
    merged["CLUSTERED_PRECINCT_ID"] = merged["CLUSTERED_PRECINCT_ID"].fillna(
        merged.get("QR_CLUSTERED_PRECINCT_ID", pd.NA)
    )
    merged["CONTEST_CODE"]    = _coalesce(merged, "CONTEST_CODE_CSV", "CONTEST_CODE_QR",
                                           "CONTEST_CODE")
    merged["CANDIDATE_CODE"]  = _coalesce(merged, "CANDIDATE_CODE_CSV", "CANDIDATE_CODE_QR",
                                           "CANDIDATE_CODE")

    # CONTEST_CANDIDATE_KEY
    contest_key_csv = merged.get("CONTEST_CANDIDATE_KEY", pd.Series(dtype=str))
    contest_key_qr  = merged.get("CONTEST_CANDIDATE_KEY_QR", pd.Series(dtype=str))
    merged["CONTEST_CANDIDATE_KEY"] = contest_key_csv.fillna(contest_key_qr)

    # --- 4. Presence flags ---
    merged["CSV_EXISTS"] = merged["CSV_EXISTS"].fillna(False)
    merged["QR_EXISTS"]  = merged["QR_EXISTS"].fillna(False)

    # --- 5. QR Scope flag ---
    merged["IS_QR_VERIFICATION_SCOPE"] = merged["CONTEST_CODE"].isin(scope_codes)

    # --- 6. Status & Reason ---
    merged["COMPARISON_STATUS"], merged["COMPARISON_REASON"] = zip(
        *merged.apply(_classify_row, axis=1)
    )

    # --- 7. Vote difference ---
    merged["VOTE_DIFFERENCE"] = (
        merged["QR_VOTES"].fillna(0).astype("Int64")
        - merged["CSV_VOTES"].fillna(0).astype("Int64")
    )
    # If either side was absent, vote difference should be null
    both_present = merged["CSV_EXISTS"] & merged["QR_EXISTS"]
    merged.loc[~both_present, "VOTE_DIFFERENCE"] = pd.NA

    # --- 8. Derived flags ---
    merged["IS_MATCHED"]  = merged["COMPARISON_STATUS"] == STATUS_MATCHED
    merged["IS_MISMATCH"] = merged["COMPARISON_STATUS"] == STATUS_MISMATCH
    merged["STATUS_SORT"] = merged["COMPARISON_STATUS"].map(STATUS_SORT).astype("Int64")

    # --- 9. Final comparison key ---
    merged["FINAL_COMPARISON_KEY"] = merged["COMPARISON_KEY"]

    # --- 10. Metadata ---
    merged["RECORD_SCOPE"] = "CLUSTERED_PRECINCT_CONTEST_CANDIDATE"
    merged["ENVIRONMENT"]  = config.ENVIRONMENT

    out_cols = [
        "FINAL_COMPARISON_KEY", "CLUSTERED_PRECINCT_ID",
        "CONTEST_CODE", "CANDIDATE_CODE", "CONTEST_CANDIDATE_KEY",
        "COMPARISON_STATUS", "COMPARISON_REASON", "STATUS_SORT",
        "IS_QR_VERIFICATION_SCOPE", "CSV_EXISTS", "QR_EXISTS",
        "IS_MATCHED", "IS_MISMATCH",
        "CSV_VOTES", "QR_VOTES", "VOTE_DIFFERENCE",
        "RECORD_SCOPE", "ENVIRONMENT",
    ]
    available = [c for c in out_cols if c in merged.columns]
    return merged[available].reset_index(drop=True)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _assert_unique_key(df: pd.DataFrame, key_col: str, side: str) -> None:
    """
    Raise CartesianJoinError if *key_col* has any duplicates in *df*.

    This prevents a hidden many-to-many join from multiplying vote rows.
    """
    dupes = df[df.duplicated(subset=[key_col], keep=False)]
    if not dupes.empty:
        n = dupes[key_col].nunique()
        sample = dupes[key_col].unique()[:5].tolist()
        raise CartesianJoinError(
            f"[{side} side] {n} COMPARISON_KEY value(s) are not unique before the "
            f"full outer join. This would silently multiply election result rows.\n"
            f"Sample duplicate keys: {sample}\n"
            f"Investigate QA_Result_Duplicates or QA_Mobile_QR_Duplicate_Choice."
        )


def _coalesce(df: pd.DataFrame, col_a: str, col_b: str, fallback: str) -> pd.Series:
    """Return col_a if non-null, else col_b, else fallback col if it exists."""
    a = df.get(col_a, pd.Series(dtype=str))
    b = df.get(col_b, pd.Series(dtype=str))
    c = df.get(fallback, pd.Series(dtype=str))
    return a.fillna(b).fillna(c)


def _classify_row(row) -> tuple[str, str]:
    """
    Determine COMPARISON_STATUS and COMPARISON_REASON for one merged row.

    Mirrors the conditional logic in 81_verification_comparison.pq.
    """
    csv_exists = bool(row.get("CSV_EXISTS", False))
    qr_exists  = bool(row.get("QR_EXISTS",  False))
    in_scope   = bool(row.get("IS_QR_VERIFICATION_SCOPE", False))

    csv_votes  = row.get("CSV_VOTES")
    qr_votes   = row.get("QR_VOTES")

    both = csv_exists and qr_exists

    if both:
        if pd.isna(csv_votes) or pd.isna(qr_votes):
            return STATUS_MISMATCH, REASON_MISSING_VOTE_VALUE
        if int(csv_votes) == int(qr_votes):
            return STATUS_MATCHED, REASON_VOTES_EQUAL
        return STATUS_MISMATCH, REASON_VOTES_DIFFER

    if csv_exists and not qr_exists:
        if in_scope:
            return STATUS_CSV_ONLY, REASON_QR_MISSING_IN_SCOPE
        return STATUS_CSV_ONLY, REASON_CSV_OUTSIDE_SCOPE

    if qr_exists and not csv_exists:
        return STATUS_QR_ONLY, REASON_CSV_CHOICE_MISSING

    # Should never reach here (both False = bad merge row)
    return STATUS_CSV_ONLY, "UNEXPECTED_STATE"


# ---------------------------------------------------------------------------
# PRECINCT CONTEST SUMMARY  (mirrors 28_precinct_contest_summary.pq)
# ---------------------------------------------------------------------------

def build_precinct_contest_summary(results_snapshot: pd.DataFrame) -> pd.DataFrame:
    """
    Produce one safe row per PRECINCT_CODE + CONTEST_CODE.

    Contest totals (NUMBER_VOTERS, UNDER_VOTES, OVER_VOTES, RECEPTION_DATETIME)
    repeat across every candidate row.  Summing them across candidate rows is WRONG.

    Logic:
    - For each (PRECINCT_CODE, CONTEST_CODE), count distinct values.
    - If exactly 1 distinct value → use it.
    - If > 1 distinct values → set to pd.NA (QA catches the inconsistency).
    """

    def safe_single(series: pd.Series):
        vals = series.dropna().unique()
        return vals[0] if len(vals) == 1 else pd.NA

    grp = results_snapshot.groupby(["PRECINCT_CODE", "CONTEST_CODE"], as_index=False)

    totals = grp.agg(
        N_DISTINCT_VOTERS    = ("NUMBER_VOTERS",    "nunique"),
        N_DISTINCT_UNDER     = ("UNDER_VOTES",      "nunique"),
        N_DISTINCT_OVER      = ("OVER_VOTES",       "nunique"),
        N_DISTINCT_DATETIME  = ("RECEPTION_DATETIME","nunique"),
    )

    # Fetch the single safe value for each field
    for raw_col, out_col in [
        ("NUMBER_VOTERS",     "NUMBER_VOTERS_SAFE"),
        ("UNDER_VOTES",       "UNDER_VOTES_SAFE"),
        ("OVER_VOTES",        "OVER_VOTES_SAFE"),
        ("RECEPTION_DATETIME","RECEPTION_DATETIME_SAFE"),
    ]:
        if raw_col in results_snapshot.columns:
            safe_vals = (
                results_snapshot.groupby(["PRECINCT_CODE", "CONTEST_CODE"])[raw_col]
                .apply(safe_single)
                .reset_index()
                .rename(columns={raw_col: out_col})
            )
            totals = totals.merge(safe_vals, on=["PRECINCT_CODE", "CONTEST_CODE"], how="left")

    return totals.reset_index(drop=True)
