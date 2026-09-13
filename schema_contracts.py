"""
namfrel_backup/schema_contracts.py
===================================
Python equivalent of 05_Schema_Expected.pq and the semantic-layer schema assertions.

Each dataset has a list of column definitions:
    {
        "col":      Column name (UPPER_SNAKE_CASE canonical name)
        "required": True  → missing column is BLOCKING
                   False → missing column is added as all-null (WARNING)
        "dtype":    Pandas dtype hint used after loading. Not enforced here but
                    passed downstream to type-coercion helpers.
    }

These are the contracts used by:
  - ensure_columns()  in normalizers.py
  - QA_Required_Value_Blanks equivalent in qa_gatekeeper.py
  - Physical output column ordering
"""

SCHEMA: dict[str, list[dict]] = {

    # -----------------------------------------------------------------------
    # PRECINCTS
    # Source: precincts.csv  →  precincts_master
    # -----------------------------------------------------------------------
    "precincts": [
        {"col": "ACM_ID",                  "required": True,  "dtype": "str"},
        {"col": "PROVINCE",                "required": True,  "dtype": "str"},
        {"col": "MUNICIPALITY",            "required": True,  "dtype": "str"},
        {"col": "BARANGAY",                "required": True,  "dtype": "str"},
        {"col": "POLLING_PLACE",           "required": False, "dtype": "str"},
        {"col": "CLUSTERED_PRECINCT",      "required": False, "dtype": "str"},
        {"col": "REGISTERED_VOTERS",       "required": True,  "dtype": "Int64"},
        {"col": "REGION",                  "required": False, "dtype": "str"},
    ],

    # -----------------------------------------------------------------------
    # CANDIDATES
    # Source: candidates.csv  →  candidates_master
    # -----------------------------------------------------------------------
    "candidates": [
        {"col": "CONTEST_CODE",            "required": True,  "dtype": "str"},
        {"col": "CANDIDATE_CODE",          "required": True,  "dtype": "str"},
        {"col": "CANDIDATE_NAME",          "required": True,  "dtype": "str"},
        {"col": "PARTIES_CODE",            "required": False, "dtype": "str"},
        # CONTEST_CANDIDATE_KEY is computed by core_cleaning, not a raw source column
    ],

    # -----------------------------------------------------------------------
    # PARTIES
    # Source: parties.csv  →  parties_master
    # -----------------------------------------------------------------------
    "parties": [
        {"col": "PARTIES_CODE",            "required": True,  "dtype": "str"},
        {"col": "PARTIES_NAME",            "required": True,  "dtype": "str"},
        {"col": "PARTIES_ALIAS",           "required": False, "dtype": "str"},
    ],

    # -----------------------------------------------------------------------
    # CONTESTS
    # Source: contest.csv  →  contests_master
    # -----------------------------------------------------------------------
    "contests": [
        {"col": "CONTEST_CODE",            "required": True,  "dtype": "str"},
        {"col": "CONTEST_NAME",            "required": True,  "dtype": "str"},
        {"col": "NUM_SEATS",               "required": False, "dtype": "Int64"},
        {"col": "CONTEST_SCOPE",           "required": False, "dtype": "str"},
    ],

    # -----------------------------------------------------------------------
    # RESULTS
    # Source: results.csv  →  results_snapshot
    # -----------------------------------------------------------------------
    "results": [
        {"col": "PRECINCT_CODE",           "required": True,  "dtype": "str"},
        {"col": "CONTEST_CODE",            "required": True,  "dtype": "str"},
        {"col": "CANDIDATE_CODE",          "required": True,  "dtype": "str"},
        {"col": "PARTIES_CODE",            "required": False, "dtype": "str"},
        {"col": "VOTES_AMOUNT",            "required": True,  "dtype": "Int64"},
        {"col": "TOTALIZATION_ORDER",      "required": False, "dtype": "Int64"},
        {"col": "NUMBER_VOTERS",           "required": True,  "dtype": "Int64"},
        {"col": "UNDER_VOTES",             "required": True,  "dtype": "Int64"},
        {"col": "OVER_VOTES",              "required": True,  "dtype": "Int64"},
        {"col": "RECEPTION_DATE",          "required": True,  "dtype": "str"},
        # CONTEST_CANDIDATE_KEY and RECEPTION_DATETIME are computed by core_cleaning
    ],

    # -----------------------------------------------------------------------
    # MOBILE ELECTION RETURNS
    # Source: election_returns.csv  →  mobile_election_returns
    # -----------------------------------------------------------------------
    "mobile_er": [
        {"col": "er_id",                   "required": True,  "dtype": "str"},
        {"col": "clustered_precinct_id",   "required": True,  "dtype": "str"},
        {"col": "is_duplicate",            "required": True,  "dtype": "bool"},
        {"col": "received_at",             "required": True,  "dtype": "datetime64[ns]"},
        {"col": "election_date",           "required": False, "dtype": "str"},
        {"col": "registered_voters",       "required": False, "dtype": "Int64"},
        {"col": "ballots_counted",         "required": False, "dtype": "Int64"},
        {"col": "ENVIRONMENT",             "required": True,  "dtype": "str"},
        {"col": "IS_CANONICAL",            "required": True,  "dtype": "bool"},
        {"col": "SUBMISSION_STATUS",       "required": True,  "dtype": "str"},
    ],

    # -----------------------------------------------------------------------
    # MOBILE QR RESULTS
    # Source: qr_results.csv  →  mobile_qr_results
    # -----------------------------------------------------------------------
    "mobile_qr": [
        {"col": "er_id",                   "required": True,  "dtype": "str"},
        {"col": "district_code",           "required": True,  "dtype": "str"},
        {"col": "candidate_no",            "required": True,  "dtype": "Int64"},
        {"col": "votes",                   "required": True,  "dtype": "Int64"},
        {"col": "QR_RACE_CODE",            "required": True,  "dtype": "str"},
        {"col": "BALLOT_POSITION",         "required": True,  "dtype": "Int64"},
        {"col": "QR_VOTES",                "required": True,  "dtype": "Int64"},
        {"col": "ENVIRONMENT",             "required": True,  "dtype": "str"},
        {"col": "VERIFICATION_SOURCE",     "required": True,  "dtype": "str"},
    ],

    # -----------------------------------------------------------------------
    # VERIFICATION COMPARISON (output contract)
    # -----------------------------------------------------------------------
    "verification_comparison": [
        {"col": "FINAL_COMPARISON_KEY",    "required": True,  "dtype": "str"},
        {"col": "CLUSTERED_PRECINCT_ID",   "required": True,  "dtype": "str"},
        {"col": "CONTEST_CODE",            "required": True,  "dtype": "str"},
        {"col": "CANDIDATE_CODE",          "required": True,  "dtype": "str"},
        {"col": "CONTEST_CANDIDATE_KEY",   "required": True,  "dtype": "str"},
        {"col": "COMPARISON_STATUS",       "required": True,  "dtype": "str"},
        {"col": "COMPARISON_REASON",       "required": True,  "dtype": "str"},
        {"col": "STATUS_SORT",             "required": True,  "dtype": "Int64"},
        {"col": "IS_QR_VERIFICATION_SCOPE","required": True,  "dtype": "bool"},
        {"col": "CSV_EXISTS",              "required": True,  "dtype": "bool"},
        {"col": "QR_EXISTS",               "required": True,  "dtype": "bool"},
        {"col": "IS_MATCHED",              "required": True,  "dtype": "bool"},
        {"col": "IS_MISMATCH",             "required": True,  "dtype": "bool"},
        {"col": "CSV_VOTES",               "required": False, "dtype": "Int64"},
        {"col": "QR_VOTES",                "required": False, "dtype": "Int64"},
        {"col": "VOTE_DIFFERENCE",         "required": False, "dtype": "Int64"},
        {"col": "RECORD_SCOPE",            "required": True,  "dtype": "str"},
        {"col": "ENVIRONMENT",             "required": True,  "dtype": "str"},
    ],
}


def required_cols(dataset: str) -> list[str]:
    """Return only the required column names for a dataset."""
    return [c["col"] for c in SCHEMA.get(dataset, []) if c["required"]]


def all_cols(dataset: str) -> list[str]:
    """Return all expected column names for a dataset (required + optional)."""
    return [c["col"] for c in SCHEMA.get(dataset, [])]
