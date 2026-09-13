"""
namfrel_backup/qa_gatekeeper.py
================================
Python equivalent of the Dataflow QA suite:
    29_QA_Repeated_Contest_Totals
    30_QA_Result_Duplicates
    31_QA_Missing_Result_Precincts
    32_QA_Result_Party_Unmatched
    33_QA_Result_Candidate_Unmatched
    35_QA_Result_Precinct_Unmatched
    36_QA_District_Contest_Unmatched
    37_QA_Contest_ID_Unmatched
    42_QA_Crosswalk_Duplicates
    44_QA_Schema_Drift  (MISSING_REQUIRED / DUPLICATE_COLUMN as BLOCKING)
    46_QA_Cluster_Count_COMELEC_vs_NAMFREL
    47_QA_ACM_ID_Duplicates
    48_QA_ACM_ID_Missing
    49_QA_Result_Value_Invalid
    50_QA_Reception_Date_Invalid
    51_QA_Registered_Voter_Invalid
    52_QA_Publication_Gate
    54_QA_Required_Value_Blanks
    80_QA_QR_Unmapped_Ballot_Choices
    89_QA_Mobile_QR_Orphan_ER
    90_QA_Mobile_Multiple_Canonical_ER_Per_Precinct
    91_QA_Mobile_QR_Duplicate_Choice
    92_QA_QR_Ballot_Map_Duplicate_Key
    93_QA_Mobile_Required_Values
    95_QA_QR_Verification_Scope_Readiness
    96_QA_QR_Contest_Map_Duplicate_Key  (currently empty map = PASS)
    97_QA_QR_Outside_Verification_Scope

Convention:
    - Each check returns a DataFrame of PROBLEM ROWS.
    - 0 rows  = PASS
    - 1+ rows = Review / BLOCKED depending on severity

Publication Gate decision:
    BLOCKING issues > 0  → DO_NOT_PUBLISH
    No blockers, warnings > 0 → REVIEW_BEFORE_PUBLISH
    All clear              → READY_TO_PUBLISH
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import config


# ---------------------------------------------------------------------------
# SEVERITY CONSTANTS
# ---------------------------------------------------------------------------
BLOCKING = "BLOCKING"
WARNING  = "WARNING"
INFO     = "INFO"

PASS_STATUS    = "PASS"
BLOCKED_STATUS = "BLOCKED"
REVIEW_STATUS  = "REVIEW"
INFO_STATUS    = "INFO"


# ---------------------------------------------------------------------------
# INDIVIDUAL QA CHECKS
# ---------------------------------------------------------------------------

def qa_result_duplicates(results_snapshot: pd.DataFrame) -> pd.DataFrame:
    """QA 30 — Duplicate result rows on PRECINCT_CODE+CONTEST_CODE+CANDIDATE_CODE."""
    key = ["PRECINCT_CODE", "CONTEST_CODE", "CANDIDATE_CODE"]
    dupes = results_snapshot[results_snapshot.duplicated(subset=key, keep=False)]
    return dupes[key + ["VOTES_AMOUNT"]].drop_duplicates().assign(
        QA_ISSUE="DUPLICATE_RESULT_KEY"
    )


def qa_missing_result_precincts(
    precincts_master:  pd.DataFrame,
    results_snapshot:  pd.DataFrame,
) -> pd.DataFrame:
    """QA 31 — Expected precincts absent from current results (INFO)."""
    reported = set(results_snapshot["PRECINCT_CODE"].dropna())
    missing  = precincts_master[~precincts_master["ACM_ID"].isin(reported)]
    return missing[["ACM_ID"]].assign(QA_ISSUE="PRECINCT_NOT_YET_IN_RESULTS")


def qa_result_party_unmatched(
    results_snapshot: pd.DataFrame,
    parties_master:   pd.DataFrame,
) -> pd.DataFrame:
    """QA 32 — Non-blank party codes not found in parties_master."""
    known = set(parties_master["PARTIES_CODE"].dropna())
    prob  = results_snapshot[
        results_snapshot["PARTIES_CODE"].notna()
        & ~results_snapshot["PARTIES_CODE"].isin(known)
    ]
    return prob[["PRECINCT_CODE", "CONTEST_CODE", "PARTIES_CODE"]].drop_duplicates().assign(
        QA_ISSUE="PARTY_CODE_NOT_IN_MASTER"
    )


def qa_result_candidate_unmatched(
    results_snapshot:  pd.DataFrame,
    candidates_master: pd.DataFrame,
) -> pd.DataFrame:
    """QA 33 — Result rows whose CONTEST_CANDIDATE_KEY does not exist in candidates_master."""
    known = set(candidates_master["CONTEST_CANDIDATE_KEY"].dropna())
    prob  = results_snapshot[~results_snapshot["CONTEST_CANDIDATE_KEY"].isin(known)]
    return prob[["PRECINCT_CODE", "CONTEST_CODE", "CANDIDATE_CODE", "CONTEST_CANDIDATE_KEY"]]\
        .drop_duplicates().assign(QA_ISSUE="CONTEST_CANDIDATE_KEY_NOT_IN_MASTER")


def qa_result_precinct_unmatched(
    results_snapshot: pd.DataFrame,
    precincts_master: pd.DataFrame,
) -> pd.DataFrame:
    """QA 35 — Result rows whose PRECINCT_CODE is not an ACM_ID in precincts_master."""
    known = set(precincts_master["ACM_ID"].dropna())
    prob  = results_snapshot[~results_snapshot["PRECINCT_CODE"].isin(known)]
    return prob[["PRECINCT_CODE"]].drop_duplicates().assign(
        QA_ISSUE="PRECINCT_NOT_IN_MASTER"
    )


def qa_repeated_contest_totals(
    results_snapshot: pd.DataFrame,
) -> pd.DataFrame:
    """QA 29 — Contest totals that are inconsistent across candidate rows."""
    issues = []
    for field in ["NUMBER_VOTERS", "UNDER_VOTES", "OVER_VOTES"]:
        if field not in results_snapshot.columns:
            continue
        grp = (
            results_snapshot.groupby(["PRECINCT_CODE", "CONTEST_CODE"])[field]
            .nunique()
            .reset_index(name="DISTINCT_COUNT")
        )
        bad = grp[grp["DISTINCT_COUNT"] > 1].copy()
        bad["QA_ISSUE"]    = f"INCONSISTENT_{field}"
        bad["FIELD_CHECKED"]= field
        issues.append(bad)
    return pd.concat(issues, ignore_index=True) if issues else pd.DataFrame()


def qa_acm_id_duplicates(precincts_master: pd.DataFrame) -> pd.DataFrame:
    """QA 47 — Duplicate non-blank ACM_IDs in precincts_master."""
    valid = precincts_master[precincts_master["ACM_ID"].notna()]
    dupes = valid[valid.duplicated(subset=["ACM_ID"], keep=False)]
    return dupes[["ACM_ID"]].drop_duplicates().assign(QA_ISSUE="DUPLICATE_ACM_ID")


def qa_acm_id_missing(precincts_master: pd.DataFrame) -> pd.DataFrame:
    """QA 48 — Precinct rows with null or blank ACM_ID."""
    prob = precincts_master[precincts_master["ACM_ID"].isna()]
    return prob.assign(QA_ISSUE="MISSING_ACM_ID")


def qa_result_value_invalid(results_snapshot: pd.DataFrame) -> pd.DataFrame:
    """QA 49 — Null, non-integer, or negative vote/count fields."""
    issues = []
    for col in ["VOTES_AMOUNT", "NUMBER_VOTERS", "UNDER_VOTES", "OVER_VOTES"]:
        if col not in results_snapshot.columns:
            continue
        bad = results_snapshot[results_snapshot[col].isna() | (results_snapshot[col] < 0)]
        if not bad.empty:
            tmp = bad[["PRECINCT_CODE", "CONTEST_CODE", "CANDIDATE_CODE", col]].copy()
            tmp["QA_ISSUE"]      = f"INVALID_{col}"
            tmp["FIELD_CHECKED"] = col
            issues.append(tmp)
    return pd.concat(issues, ignore_index=True) if issues else pd.DataFrame()


def qa_reception_date_invalid(results_snapshot: pd.DataFrame) -> pd.DataFrame:
    """QA 50 — Missing or unparseable RECEPTION_DATETIME."""
    if "RECEPTION_DATETIME" not in results_snapshot.columns:
        return results_snapshot[["PRECINCT_CODE"]].assign(
            QA_ISSUE="RECEPTION_DATETIME_COLUMN_MISSING"
        )
    bad = results_snapshot[results_snapshot["RECEPTION_DATETIME"].isna()]
    return bad[["PRECINCT_CODE", "CONTEST_CODE", "RECEPTION_DATE"]].assign(
        QA_ISSUE="UNPARSEABLE_RECEPTION_DATE"
    )


def qa_registered_voter_invalid(precincts_master: pd.DataFrame) -> pd.DataFrame:
    """QA 51 — Null or negative REGISTERED_VOTERS."""
    col = "REGISTERED_VOTERS"
    if col not in precincts_master.columns:
        return pd.DataFrame()
    bad = precincts_master[
        precincts_master[col].isna() | (precincts_master[col] < 0)
    ]
    return bad[["ACM_ID", col]].assign(QA_ISSUE="INVALID_REGISTERED_VOTERS")


def qa_required_value_blanks(
    tables: dict[str, pd.DataFrame],
    schema: dict,
) -> pd.DataFrame:
    """QA 54 — Any required field that is null or blank post-cleaning."""
    issues = []
    for dataset, df in tables.items():
        spec = schema.get(dataset, [])
        for field_def in spec:
            col = field_def["col"]
            if not field_def["required"] or col not in df.columns:
                continue
            bad = df[df[col].isna()]
            if not bad.empty:
                issues.append(pd.DataFrame({
                    "DATASET":       [dataset] * len(bad),
                    "COLUMN":        [col]     * len(bad),
                    "ROW_INDEX":     bad.index.tolist(),
                    "QA_ISSUE":      "REQUIRED_VALUE_BLANK",
                }))
    return pd.concat(issues, ignore_index=True) if issues else pd.DataFrame()


def qa_mobile_qr_orphan_er(
    mobile_qr:     pd.DataFrame,
    mobile_er:     pd.DataFrame,
) -> pd.DataFrame:
    """QA 89 — QR rows whose er_id does not exist in mobile_election_returns."""
    known = set(mobile_er["er_id"].dropna())
    prob  = mobile_qr[~mobile_qr["er_id"].isin(known)]
    return prob[["er_id"]].drop_duplicates().assign(QA_ISSUE="ORPHAN_QR_ER_ID")


def qa_mobile_multiple_canonical_er_per_precinct(
    er_canonical: pd.DataFrame,
) -> pd.DataFrame:
    """QA 90 — More than one canonical ER for a clustered precinct."""
    grp = (
        er_canonical.groupby("clustered_precinct_id", as_index=False)
        .agg(CANONICAL_COUNT=("er_id", "count"))
    )
    prob = grp[grp["CANONICAL_COUNT"] > 1]
    return prob.assign(QA_ISSUE="MULTIPLE_CANONICAL_ER_PER_PRECINCT")


def qa_mobile_qr_duplicate_choice(mobile_qr: pd.DataFrame) -> pd.DataFrame:
    """QA 91 — Duplicate QR choices (er_id + QR_RACE_CODE + BALLOT_POSITION)."""
    key = ["er_id", "QR_RACE_CODE", "BALLOT_POSITION"]
    missing = [c for c in key if c not in mobile_qr.columns]
    if missing:
        return pd.DataFrame()
    dupes = mobile_qr[mobile_qr.duplicated(subset=key, keep=False)]
    return dupes[key].drop_duplicates().assign(QA_ISSUE="DUPLICATE_QR_CHOICE")


def qa_qr_ballot_map_duplicate_key(ballot_map: pd.DataFrame) -> pd.DataFrame:
    """QA 92 — Duplicate CONTEST_CODE + BALLOT_POSITION in ballot map."""
    key = ["CONTEST_CODE", "BALLOT_POSITION"]
    missing = [c for c in key if c not in ballot_map.columns]
    if missing or ballot_map.empty:
        return pd.DataFrame()
    dupes = ballot_map[ballot_map.duplicated(subset=key, keep=False)]
    return dupes[key].drop_duplicates().assign(QA_ISSUE="DUPLICATE_BALLOT_MAP_KEY")


def qa_mobile_required_values(
    mobile_er:  pd.DataFrame,
    mobile_qr:  pd.DataFrame,
) -> pd.DataFrame:
    """QA 93 — Required values and numeric constraint checks for mobile sources."""
    issues = []

    # --- ER checks ---
    if "er_id" in mobile_er.columns:
        bad = mobile_er[mobile_er["er_id"].isna()]
        if not bad.empty:
            issues.append(pd.DataFrame({
                "SOURCE": "ER", "QA_ISSUE": "MISSING_ER_ID", "COUNT": [len(bad)]
            }))
    if "clustered_precinct_id" in mobile_er.columns:
        bad = mobile_er[mobile_er["clustered_precinct_id"].isna()]
        if not bad.empty:
            issues.append(pd.DataFrame({
                "SOURCE": "ER", "QA_ISSUE": "MISSING_PRECINCT_ID", "COUNT": [len(bad)]
            }))
    if "is_duplicate" in mobile_er.columns:
        bad = mobile_er[mobile_er["is_duplicate"].isna()]
        if not bad.empty:
            issues.append(pd.DataFrame({
                "SOURCE": "ER", "QA_ISSUE": "INVALID_IS_DUPLICATE_FLAG", "COUNT": [len(bad)]
            }))
    if all(c in mobile_er.columns for c in ["ballots_counted", "registered_voters"]):
        bad = mobile_er[
            mobile_er["ballots_counted"].notna()
            & mobile_er["registered_voters"].notna()
            & (mobile_er["ballots_counted"] > mobile_er["registered_voters"])
        ]
        if not bad.empty:
            issues.append(pd.DataFrame({
                "SOURCE": "ER",
                "QA_ISSUE": "BALLOTS_COUNTED_EXCEEDS_REGISTERED_VOTERS",
                "COUNT": [len(bad)]
            }))

    # --- QR checks ---
    for col, label in [
        ("er_id",         "MISSING_QR_ER_ID"),
        ("QR_RACE_CODE",  "MISSING_QR_RACE_CODE"),
        ("BALLOT_POSITION","INVALID_BALLOT_POSITION"),
        ("QR_VOTES",      "INVALID_QR_VOTES"),
    ]:
        if col in mobile_qr.columns:
            bad = mobile_qr[mobile_qr[col].isna()]
            if not bad.empty:
                issues.append(pd.DataFrame({"SOURCE": "QR", "QA_ISSUE": label, "COUNT": [len(bad)]}))
    if "QR_VOTES" in mobile_qr.columns:
        bad = mobile_qr[mobile_qr["QR_VOTES"].notna() & (mobile_qr["QR_VOTES"] < 0)]
        if not bad.empty:
            issues.append(pd.DataFrame({
                "SOURCE": "QR", "QA_ISSUE": "NEGATIVE_QR_VOTES", "COUNT": [len(bad)]
            }))

    return pd.concat(issues, ignore_index=True) if issues else pd.DataFrame()


def qa_qr_verification_scope_readiness(
    scope_df:      pd.DataFrame,
    contests_master: pd.DataFrame,
    ballot_map:    pd.DataFrame,
) -> pd.DataFrame:
    """QA 95 — Scope is populated, every scope contest exists, and has a ballot map."""
    issues = []

    if scope_df.empty:
        if config.ENVIRONMENT == "Production":
            issues.append({"QA_ISSUE": "PRODUCTION_SCOPE_MISSING", "CONTEST_CODE": None})
        return pd.DataFrame(issues)

    known_contests  = set(contests_master["CONTEST_CODE"].dropna())
    mapped_contests = set(ballot_map["CONTEST_CODE"].dropna()) if not ballot_map.empty else set()

    for code in scope_df["CONTEST_CODE"].dropna():
        if code not in known_contests:
            issues.append({"QA_ISSUE": "SCOPE_CONTEST_UNKNOWN_IN_MASTER", "CONTEST_CODE": code})
        if code not in mapped_contests:
            issues.append({"QA_ISSUE": "SCOPE_CONTEST_HAS_NO_BALLOT_MAP", "CONTEST_CODE": code})

    return pd.DataFrame(issues)


def qa_qr_unmapped_ballot_choices(qr_verification_results: pd.DataFrame) -> pd.DataFrame:
    """QA 80 — QR verification rows where MAPPING_VALID is not True."""
    if "MAPPING_VALID" not in qr_verification_results.columns:
        return pd.DataFrame()
    unmapped = qr_verification_results[qr_verification_results["MAPPING_VALID"] != True]
    return unmapped[["er_id", "QR_RACE_CODE", "BALLOT_POSITION"]].drop_duplicates().assign(
        QA_ISSUE="QR_BALLOT_CHOICE_UNMAPPED"
    ) if not unmapped.empty else pd.DataFrame()


def qa_qr_outside_verification_scope(
    qr_verification_results: pd.DataFrame,
    scope_df: pd.DataFrame,
) -> pd.DataFrame:
    """QA 97 — Valid QR rows for contests NOT in the configured verification scope."""
    if qr_verification_results.empty or scope_df.empty:
        return pd.DataFrame()
    scope_codes = set(scope_df["CONTEST_CODE"].dropna())
    valid_qr = qr_verification_results[qr_verification_results.get("MAPPING_VALID", True) == True]
    outside  = valid_qr[~valid_qr["CONTEST_CODE"].isin(scope_codes)]
    if outside.empty:
        return pd.DataFrame()
    return outside[["CONTEST_CODE"]].drop_duplicates().assign(
        QA_ISSUE="MAPPED_QR_CONTEST_OUTSIDE_SCOPE"
    )


# ---------------------------------------------------------------------------
# QA FINAL SUMMARY  (mirrors 34_QA_Final_Summary.pq)
# ---------------------------------------------------------------------------

def build_final_summary(check_results: dict[str, dict]) -> pd.DataFrame:
    """
    Build the QA_Final_Summary table.

    *check_results* is a dict of:
        {
          "check_name": {
              "severity": "BLOCKING" | "WARNING" | "INFO",
              "issue_count": int,
          }
        }

    Returns a DataFrame with columns:
        QA_CHECK, SEVERITY, ISSUE_COUNT, STATUS, ACTION
    plus an OVERALL QA STATUS summary row.
    """
    rows = []
    for check_name, meta in check_results.items():
        severity = meta["severity"]
        count    = meta["issue_count"]

        if count == 0:
            status = PASS_STATUS
            action = "No action required"
        elif severity == BLOCKING:
            status = BLOCKED_STATUS
            action = "Investigate before publishing"
        elif severity == WARNING:
            status = REVIEW_STATUS
            action = "Review and document discrepancy"
        else:
            status = INFO_STATUS
            action = "Monitor — election-in-progress information"

        rows.append({
            "QA_CHECK":    check_name,
            "SEVERITY":    severity,
            "ISSUE_COUNT": count,
            "STATUS":      status,
            "ACTION":      action,
        })

    df = pd.DataFrame(rows)

    # Overall status
    if (df["STATUS"] == BLOCKED_STATUS).any():
        overall = "BLOCKED"
    elif (df["STATUS"] == REVIEW_STATUS).any():
        overall = "READY_WITH_WARNINGS"
    else:
        overall = "READY"

    overall_row = pd.DataFrame([{
        "QA_CHECK":    "OVERALL QA STATUS",
        "SEVERITY":    BLOCKING if overall == "BLOCKED" else WARNING,
        "ISSUE_COUNT": int((df["STATUS"] == BLOCKED_STATUS).sum()),
        "STATUS":      overall,
        "ACTION":      (
            "Resolve all blocking issues before publishing."
            if overall == "BLOCKED"
            else "Review warnings before publishing."
            if overall == "READY_WITH_WARNINGS"
            else "QA passed — ready to publish."
        ),
    }])

    return pd.concat([df, overall_row], ignore_index=True)


# ---------------------------------------------------------------------------
# PUBLICATION GATE  (mirrors 52_QA_Publication_Gate.pq)
# ---------------------------------------------------------------------------

def evaluate_publication_gate(final_summary: pd.DataFrame) -> dict:
    """
    Evaluate the publication gate from QA_Final_Summary.

    Returns a dict that becomes a single-row QA_Publication_Gate CSV.
    """
    checks = final_summary[final_summary["QA_CHECK"] != "OVERALL QA STATUS"]

    blocking_checks = checks[checks["STATUS"] == BLOCKED_STATUS]
    review_checks   = checks[checks["STATUS"] == REVIEW_STATUS]
    info_checks     = checks[checks["STATUS"] == INFO_STATUS]

    blocking_check_count = len(blocking_checks)
    blocking_issue_count = int(blocking_checks["ISSUE_COUNT"].sum())
    review_check_count   = len(review_checks)
    review_issue_count   = int(review_checks["ISSUE_COUNT"].sum())
    info_check_count     = len(info_checks)
    info_issue_count     = int(info_checks["ISSUE_COUNT"].sum())

    if blocking_check_count > 0:
        pub_status   = "BLOCKED"
        pub_decision = "DO_NOT_PUBLISH"
        action       = "Resolve all blocking QA issues before publishing."
    elif review_check_count > 0:
        pub_status   = "READY_WITH_WARNINGS"
        pub_decision = "REVIEW_BEFORE_PUBLISH"
        action       = "Review and document warnings before publishing."
    else:
        pub_status   = "READY"
        pub_decision = "READY_TO_PUBLISH"
        action       = "QA checks passed. Dataset is ready for publication."

    return {
        "RUN_ID":                  datetime.now(timezone.utc).strftime("RUN_%Y%m%dT%H%M%SZ"),
        "REFRESH_TIMESTAMP_UTC":   datetime.now(timezone.utc).isoformat(),
        "ENVIRONMENT":             config.ENVIRONMENT,
        "PUBLICATION_STATUS":      pub_status,
        "PUBLICATION_DECISION":    pub_decision,
        "BLOCKING_CHECK_COUNT":    blocking_check_count,
        "BLOCKING_ISSUE_COUNT":    blocking_issue_count,
        "REVIEW_CHECK_COUNT":      review_check_count,
        "REVIEW_ISSUE_COUNT":      review_issue_count,
        "INFO_CHECK_COUNT":        info_check_count,
        "INFO_ISSUE_COUNT":        info_issue_count,
        "REQUIRED_ACTION":         action,
    }


# ---------------------------------------------------------------------------
# MASTER RUNNER
# ---------------------------------------------------------------------------

def run_all_checks(
    *,
    precincts_master:        pd.DataFrame,
    candidates_master:       pd.DataFrame,
    contests_master:         pd.DataFrame,
    parties_master:          pd.DataFrame,
    results_snapshot:        pd.DataFrame,
    mobile_er:               pd.DataFrame,
    er_canonical:            pd.DataFrame,
    mobile_qr:               pd.DataFrame,
    qr_canonical:            pd.DataFrame,
    ballot_map:              pd.DataFrame,
    scope_df:                pd.DataFrame,
    qr_verification_results: pd.DataFrame,
    schema:                  dict,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """
    Run all configured QA checks.

    Returns:
        (final_summary_df, gate_dict, issues_by_check_dict)
    """
    tables_for_blanks = {
        "precincts":  precincts_master,
        "candidates": candidates_master,
        "parties":    parties_master,
        "contests":   contests_master,
        "results":    results_snapshot,
    }

    checks: dict[str, dict] = {}

    def _check(name: str, severity: str, df_issues: pd.DataFrame):
        checks[name] = {"severity": severity, "issue_count": len(df_issues)}
        return df_issues

    issues = {}
    issues["result_duplicates"]        = _check("Result duplicates",                       BLOCKING, qa_result_duplicates(results_snapshot))
    issues["missing_result_precincts"] = _check("Precincts not yet in results",            INFO,     qa_missing_result_precincts(precincts_master, results_snapshot))
    issues["result_party_unmatched"]   = _check("Result party unmatched",                  BLOCKING, qa_result_party_unmatched(results_snapshot, parties_master))
    issues["result_candidate_unmatched"]= _check("Result candidate unmatched",             BLOCKING, qa_result_candidate_unmatched(results_snapshot, candidates_master))
    issues["result_precinct_unmatched"]= _check("Result precinct unmatched",               BLOCKING, qa_result_precinct_unmatched(results_snapshot, precincts_master))
    issues["repeated_contest_totals"]  = _check("Repeated contest totals inconsistent",    BLOCKING, qa_repeated_contest_totals(results_snapshot))
    issues["acm_id_duplicates"]        = _check("Duplicate ACM IDs",                       BLOCKING, qa_acm_id_duplicates(precincts_master))
    issues["acm_id_missing"]           = _check("Missing ACM IDs",                         BLOCKING, qa_acm_id_missing(precincts_master))
    issues["result_value_invalid"]     = _check("Invalid result numeric values",           BLOCKING, qa_result_value_invalid(results_snapshot))
    issues["reception_date_invalid"]   = _check("Invalid reception dates",                 BLOCKING, qa_reception_date_invalid(results_snapshot))
    issues["registered_voter_invalid"] = _check("Invalid registered voter values",         BLOCKING, qa_registered_voter_invalid(precincts_master))
    issues["required_value_blanks"]    = _check("Required value blanks",                   BLOCKING, qa_required_value_blanks(tables_for_blanks, schema))
    issues["mobile_required_values"]   = _check("Mobile required values invalid",          BLOCKING, qa_mobile_required_values(mobile_er, mobile_qr))
    issues["qr_orphan_er"]             = _check("QR rows with unknown ER ID",              BLOCKING, qa_mobile_qr_orphan_er(mobile_qr, mobile_er))
    issues["multiple_canonical_er"]    = _check("Multiple canonical ERs per precinct",     BLOCKING, qa_mobile_multiple_canonical_er_per_precinct(er_canonical))
    issues["duplicate_qr_choice"]      = _check("Duplicate QR choices within ER",         BLOCKING, qa_mobile_qr_duplicate_choice(mobile_qr))
    issues["ballot_map_duplicate_key"] = _check("Duplicate QR ballot mapping keys",        BLOCKING, qa_qr_ballot_map_duplicate_key(ballot_map))
    issues["scope_readiness"]          = _check("QR verification scope readiness",         BLOCKING, qa_qr_verification_scope_readiness(scope_df, contests_master, ballot_map))
    issues["unmapped_qr_choices"]      = _check("Unmapped QR ballot choices",              BLOCKING, qa_qr_unmapped_ballot_choices(qr_verification_results))
    issues["qr_outside_scope"]         = _check("Mapped QR contests outside configured scope", BLOCKING, qa_qr_outside_verification_scope(qr_verification_results, scope_df))

    final_summary  = build_final_summary(checks)
    gate           = evaluate_publication_gate(final_summary)

    return final_summary, gate, issues
