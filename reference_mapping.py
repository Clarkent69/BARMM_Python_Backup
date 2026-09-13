"""
namfrel_backup/reference_mapping.py
=====================================
Mirrors the Dataflow Reference queries (power-query/dataflow/reference/):
    78_QR_Ballot_Choice_Map.pq
    87_REF_QR_Ballot_Choice_Approved.pq
    88_REF_QR_District_Ballot_Position_Map.pq
    94_REF_QR_Verification_Scope.pq

Key rules:
- In Production, a missing scope file is BLOCKING (raises MissingProductionReference).
- In Development, missing files trigger clearly labelled fallbacks.
- Ballot position MUST come from the approved reference. Row order / TOTALIZATION_ORDER
  must NEVER be used as ballot position.
"""

from __future__ import annotations

import warnings

import pandas as pd

import config
from normalizers import normalize_headers, clean_text, to_whole_number_strict


class MissingProductionReference(RuntimeError):
    """
    Raised when a required Production reference file is absent.
    In Production this is always BLOCKING — the pipeline must not continue.
    """


# ---------------------------------------------------------------------------
# QR VERIFICATION SCOPE 
# ---------------------------------------------------------------------------

def load_qr_verification_scope() -> pd.DataFrame:
    """
    Load the list of contest codes that are expected to be verified via QR.

    Required column: CONTEST_CODE
    Optional columns: ACTIVE, SCOPE_STATUS, SOURCE_AUTHORITY

    Production rule: if the file is missing, raises MissingProductionReference.
    Development rule: falls back to DEV_FALLBACK_QR_SCOPE_CONTESTS with a warning.
    """
    path = config.REF_QR_SCOPE

    if path.exists():
        df = pd.read_csv(path, dtype=str)
        df = normalize_headers(df)

        if "CONTEST_CODE" not in df.columns:
            raise MissingProductionReference(
                "qr_verification_scope.csv is missing the required CONTEST_CODE column."
            )

        df["CONTEST_CODE"] = clean_text(df["CONTEST_CODE"])

        # Keep only active rows if ACTIVE column present
        if "ACTIVE" in df.columns:
            df = df[df["ACTIVE"].str.upper().isin(["TRUE", "1", "YES", "ACTIVE"])]

        df["SCOPE_STATUS"]    = df.get("SCOPE_STATUS", pd.Series("VERIFIED", index=df.index))
        df["SOURCE_AUTHORITY"] = df.get("SOURCE_AUTHORITY",
                                        pd.Series("FILE", index=df.index))
        df["IS_MOCK_SCOPE"]   = False
        return df[["CONTEST_CODE"]].drop_duplicates().reset_index(drop=True)

    # File is missing
    if config.ENVIRONMENT == "Production":
        raise MissingProductionReference(
            "BLOCKING: qr_verification_scope.csv is missing in Production. "
            "Production QR verification cannot proceed without an approved scope file."
        )

    # Development fallback
    warnings.warn(
        "[DEV FALLBACK] qr_verification_scope.csv not found. "
        f"Using mock scope: {config.DEV_FALLBACK_QR_SCOPE_CONTESTS}",
        stacklevel=2,
    )
    return pd.DataFrame({
        "CONTEST_CODE":  config.DEV_FALLBACK_QR_SCOPE_CONTESTS,
        "SCOPE_STATUS":  ["DEV_MOCK"] * len(config.DEV_FALLBACK_QR_SCOPE_CONTESTS),
        "IS_MOCK_SCOPE": [True]       * len(config.DEV_FALLBACK_QR_SCOPE_CONTESTS),
    })


# ---------------------------------------------------------------------------
# REGION-WIDE BALLOT CHOICE REFERENCE 
# ---------------------------------------------------------------------------

def load_region_wide_ballot_reference() -> pd.DataFrame:
    """
    Load comelec_ballot_choice_reference_verified.csv.

    Required columns: CONTEST_CODE, BALLOT_NO, CANONICAL_BALLOT_NAME, ENTRY_TYPE
    Used by the QR Ballot Choice Map to resolve region-wide ballot positions.
    """
    path = config.REF_COMELEC_BALLOT_VERIFIED
    if not path.exists():
        raise MissingProductionReference(
            f"BLOCKING: {path.name} is missing. "
            "Region-wide ballot positions cannot be resolved."
        )

    df = pd.read_csv(path, dtype=str)
    df = normalize_headers(df)

    required = ["CONTEST_CODE", "BALLOT_NO", "CANONICAL_BALLOT_NAME"]
    for col in required:
        if col not in df.columns:
            raise MissingProductionReference(
                f"comelec_ballot_choice_reference_verified.csv is missing column '{col}'."
            )

    df["CONTEST_CODE"]          = clean_text(df["CONTEST_CODE"])
    df["BALLOT_NO"]             = to_whole_number_strict(df["BALLOT_NO"])
    df["CANONICAL_BALLOT_NAME"] = clean_text(df["CANONICAL_BALLOT_NAME"])
    df["ENTRY_TYPE"]            = clean_text(df.get("ENTRY_TYPE", pd.Series(dtype=str)))

    # Drop rows where any key field is null
    df = df.dropna(subset=["CONTEST_CODE", "BALLOT_NO", "CANONICAL_BALLOT_NAME"])
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# DISTRICT BALLOT POSITION MAP  
# ---------------------------------------------------------------------------

def load_district_ballot_position_map() -> pd.DataFrame:
    """
    Load qr_district_ballot_position_map.csv.

    Required columns: CONTEST_CODE, BALLOT_POSITION, CANDIDATE_CODE
    Optional: CANDIDATE_NAME, PARTY_CODE, MAPPING_STATUS, SOURCE_AUTHORITY, SOURCE_FILE

    CRITICAL RULE: Candidate row order, code order, or TOTALIZATION_ORDER are NEVER
    accepted as a substitute for ballot position in Production.
    In Development, if the file is missing, a clearly labelled mock fallback may be used.
    In Production, a missing file is BLOCKING.
    """
    path = config.REF_QR_DISTRICT_MAP

    if path.exists():
        df = pd.read_csv(path, dtype=str)
        df = normalize_headers(df)

        for col in ["CONTEST_CODE", "BALLOT_POSITION", "CANDIDATE_CODE"]:
            if col not in df.columns:
                raise MissingProductionReference(
                    f"qr_district_ballot_position_map.csv is missing column '{col}'."
                )

        df["CONTEST_CODE"]    = clean_text(df["CONTEST_CODE"])
        df["BALLOT_POSITION"] = to_whole_number_strict(df["BALLOT_POSITION"])
        df["CANDIDATE_CODE"]  = clean_text(df["CANDIDATE_CODE"])
        df["IS_MOCK_MAP"]     = False

        df = df.dropna(subset=["CONTEST_CODE", "BALLOT_POSITION", "CANDIDATE_CODE"])
        return df.reset_index(drop=True)

    # File is missing
    if config.ENVIRONMENT == "Production":
        raise MissingProductionReference(
            "BLOCKING: qr_district_ballot_position_map.csv is missing in Production. "
            "District QR ballot positions cannot be resolved."
        )

    # Development fallback
    warnings.warn(
        "[DEV FALLBACK] qr_district_ballot_position_map.csv not found. "
        "Using empty district map — district QR choices will be unmapped.",
        stacklevel=2,
    )
    return pd.DataFrame(columns=[
        "CONTEST_CODE", "BALLOT_POSITION", "CANDIDATE_CODE", "IS_MOCK_MAP"
    ])


# ---------------------------------------------------------------------------
# COMBINED QR BALLOT CHOICE MAP  
# ---------------------------------------------------------------------------

def build_qr_ballot_choice_map(
    candidates_master: pd.DataFrame,
    region_wide_ref:   pd.DataFrame,
    district_map:      pd.DataFrame,
) -> pd.DataFrame:
    """
    Combines region-wide ballot mappings and district ballot mappings into a
    single lookup table: QR_RACE_CODE + BALLOT_POSITION → CANDIDATE_CODE.

    This is the key reference used by verification.py to map raw QR rows
    into the same CONTEST_CODE + CANDIDATE_CODE space as the transmitted CSV.

    Output columns:
        CONTEST_CODE, BALLOT_POSITION, CANDIDATE_CODE, CHOICE_TYPE,
        MAPPING_SOURCE, IS_MOCK_MAP
    """

    rows = []

    # --- Region-wide from COMELEC verified reference ---
    if not region_wide_ref.empty:
        # Normalize candidate names for matching
        cand_norm = candidates_master.copy()
        cand_norm["_NAME_NORM"] = (
            cand_norm["CANDIDATE_NAME"]
            .str.upper()
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )

        ref_norm = region_wide_ref.copy()
        ref_norm["_NAME_NORM"] = (
            ref_norm["CANONICAL_BALLOT_NAME"]
            .str.upper()
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )

        merged = ref_norm.merge(
            cand_norm[["CONTEST_CODE", "CANDIDATE_CODE", "_NAME_NORM"]],
            on=["CONTEST_CODE", "_NAME_NORM"],
            how="left",
        )

        for _, row in merged.iterrows():
            rows.append({
                "CONTEST_CODE":    row["CONTEST_CODE"],
                "BALLOT_POSITION": row["BALLOT_NO"],
                "CANDIDATE_CODE":  row.get("CANDIDATE_CODE"),
                "CHOICE_TYPE":     row.get("ENTRY_TYPE", "REGION_WIDE"),
                "MAPPING_SOURCE":  "COMELEC_REGION_WIDE_VERIFIED",
                "IS_MOCK_MAP":     False,
            })

    # --- District-level from approved map file ---
    for _, row in district_map.iterrows():
        rows.append({
            "CONTEST_CODE":    row["CONTEST_CODE"],
            "BALLOT_POSITION": row["BALLOT_POSITION"],
            "CANDIDATE_CODE":  row["CANDIDATE_CODE"],
            "CHOICE_TYPE":     "DISTRICT",
            "MAPPING_SOURCE":  "APPROVED_DISTRICT_MAP",
            "IS_MOCK_MAP":     bool(row.get("IS_MOCK_MAP", False)),
        })

    if not rows:
        return pd.DataFrame(columns=[
            "CONTEST_CODE", "BALLOT_POSITION", "CANDIDATE_CODE",
            "CHOICE_TYPE", "MAPPING_SOURCE", "IS_MOCK_MAP",
        ])

    result = pd.DataFrame(rows)
    result["BALLOT_POSITION"] = to_whole_number_strict(
        result["BALLOT_POSITION"].astype(str)
    )
    # Drop unmapped entries (CANDIDATE_CODE is null) — QA will catch them separately
    result = result.dropna(subset=["CONTEST_CODE"])
    return result.sort_values(["CONTEST_CODE", "BALLOT_POSITION"]).reset_index(drop=True)
