"""
namfrel_backup/normalizers.py
==============================
Pure utility / helper functions.

These mirror the Dataflow infrastructure queries:
  - fxNormalizeHeaders  (07_fxNormalizeHeaders.pq)
  - fxToWholeNumberStrict (14_fxToWholeNumberStrict.pq)
  - fxNormalizeGeo      (03_fxNormalizeGeo.pq)
  - fxEnsureColumns     (04_fxEnsureColumns.pq)

No election business logic lives here — only mechanical text / type helpers.
"""

from __future__ import annotations

import re
import pandas as pd
from schema_contracts import SCHEMA


# ---------------------------------------------------------------------------
# HEADER NORMALIZATION  (mirrors 07_fxNormalizeHeaders.pq)
# ---------------------------------------------------------------------------
_PUNCT_TO_UNDERSCORE = re.compile(r"[\s\-/\t\r\n]+")
_MULTI_UNDERSCORE    = re.compile(r"_+")


def normalize_header(raw: str) -> str:
    """
    Convert a single raw column header to UPPER_SNAKE_CASE.

    Examples
    --------
    >>> normalize_header("Candidate Code")  →  'CANDIDATE_CODE'
    >>> normalize_header("  party-name  ")  →  'PARTY_NAME'
    """
    s = str(raw).strip().upper()
    s = _PUNCT_TO_UNDERSCORE.sub("_", s)
    s = _MULTI_UNDERSCORE.sub("_", s)
    return s.strip("_")


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Apply normalize_header to every column name of *df* (in-place rename)."""
    df.columns = [normalize_header(c) for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# STRICT INTEGER PARSING  (mirrors 14_fxToWholeNumberStrict.pq)
# ---------------------------------------------------------------------------

def to_whole_number_strict(series: pd.Series) -> pd.Series:
    """
    Coerce a pandas Series to nullable integer (Int64).

    Rules (matching the Power Query function exactly):
    - Null / blank → pd.NA
    - Numeric string → parse; if fractional part exists → pd.NA
    - Non-numeric text → pd.NA
    - Negative numbers are left as-is (QA checks catch them separately)

    Returns pd.Series with dtype Int64 (nullable).
    """
    def _convert(val):
        if pd.isna(val) or str(val).strip() == "":
            return pd.NA
        try:
            f = float(str(val).strip().replace(",", ""))
            if f != int(f):          # has fractional part
                return pd.NA
            return int(f)
        except (ValueError, TypeError):
            return pd.NA

    return series.map(_convert).astype("Int64")


# ---------------------------------------------------------------------------
# GEOGRAPHIC TEXT NORMALIZATION  (mirrors 03_fxNormalizeGeo.pq)
# ---------------------------------------------------------------------------
_CTRL_CHARS   = re.compile(r"[\x00-\x1f\x7f]")
_NON_ALPHANUM = re.compile(r"[^A-Z0-9\s]")
_MULTI_SPACE  = re.compile(r"\s+")

# Known general name normalisations from the PQ source
_GEO_OVERRIDES: dict[str, str] = {
    "CITY OF COTABATO": "COTABATO CITY",
    "SPECIAL GEOGRAPHIC AREAS": "SPECIAL GEOGRAPHIC AREA",
}


def normalize_geo_text(val) -> str:
    """
    Normalize a geographic name for cross-source matching.

    This function ONLY standardizes text. It does NOT resolve unknown PSGC codes.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    s = _CTRL_CHARS.sub(" ", s)
    s = s.upper()
    s = _NON_ALPHANUM.sub(" ", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return _GEO_OVERRIDES.get(s, s)


# ---------------------------------------------------------------------------
# COLUMN CONTRACT ENFORCEMENT  (mirrors 04_fxEnsureColumns.pq)
# ---------------------------------------------------------------------------

class MissingRequiredColumn(RuntimeError):
    """Raised when a required column is absent. Publication MUST be blocked."""


def ensure_columns(df: pd.DataFrame, dataset_name: str) -> pd.DataFrame:
    """
    Enforce schema contracts from schema_contracts.SCHEMA.

    - Required column missing  → raises MissingRequiredColumn (BLOCKING)
    - Optional column missing  → adds column with all pd.NA values (WARNING-level)

    Returns the DataFrame with any added optional columns.
    """
    spec = SCHEMA.get(dataset_name)
    if spec is None:
        raise ValueError(f"No schema defined for dataset '{dataset_name}'")

    for field in spec:
        col  = field["col"]
        req  = field["required"]
        if col not in df.columns:
            if req:
                raise MissingRequiredColumn(
                    f"[{dataset_name}] Required column '{col}' is missing. "
                    "Dataflow equivalent would have errored here."
                )
            else:
                df[col] = pd.NA
    return df


# ---------------------------------------------------------------------------
# COMPOSITE KEY BUILDER
# ---------------------------------------------------------------------------

def build_contest_candidate_key(df: pd.DataFrame,
                                contest_col: str = "CONTEST_CODE",
                                candidate_col: str = "CANDIDATE_CODE") -> pd.Series:
    """
    Builds CONTEST_CANDIDATE_KEY = CONTEST_CODE + '|' + CANDIDATE_CODE.

    Mirrors the key created in candidates_master and results_snapshot.
    """
    return df[contest_col].astype(str) + "|" + df[candidate_col].astype(str)


# ---------------------------------------------------------------------------
# CLEAN TEXT FIELD
# ---------------------------------------------------------------------------

def clean_text(series: pd.Series) -> pd.Series:
    """Strip whitespace from a string series; convert blank strings to pd.NA."""
    cleaned = series.astype(str).str.strip()
    return cleaned.where(cleaned != "", other=pd.NA)


# ---------------------------------------------------------------------------
# KNOWN GEO ALIAS MAP  (mirrors 02_Geo_Name_Alias_Map.pq)
# Only VERIFIED aliases are applied automatically.
# NEEDS_REVIEW aliases are intentionally excluded.
# ---------------------------------------------------------------------------
GEO_ALIAS_MAP: dict[str, str] = {
    "TAGOLOAN": "TAGOLOAN II",
    # Add other verified aliases here as they are confirmed.
}


def apply_geo_aliases(geo_name: str) -> str:
    """Apply verified geographic name aliases. Never guesses."""
    return GEO_ALIAS_MAP.get(geo_name.upper().strip(), geo_name)
