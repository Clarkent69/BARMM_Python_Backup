"""
namfrel_backup/dashboard_rollups.py
=====================================
Mirrors the Dataflow Derived dashboard queries:
    82_dashboard_verification_summary.pq
    83_dashboard_verification_by_precinct.pq
    84_dashboard_verification_precinct_summary.pq
    85_dashboard_reporting_by_precinct.pq
    86_dashboard_qr_timeline.pq

Key metric definitions (keep these SEPARATE — they measure different things):

    Reporting %     = reported expected precincts / total expected precincts
    QR coverage %   = canonical scanned precincts  / total expected precincts
    Match %         = matched comparable precincts  / comparable precincts
    Mismatch %      = mismatch comparable precincts / comparable precincts
"""

from __future__ import annotations
from datetime import timezone, timedelta
import pandas as pd
import config
from verification import STATUS_MATCHED, STATUS_MISMATCH, STATUS_CSV_ONLY, STATUS_QR_ONLY
PH_TZ = timezone(timedelta(hours=config.PHILIPPINE_TZ_OFFSET_HOURS))


# ---------------------------------------------------------------------------
# 85  dashboard_reporting_by_precinct
# ---------------------------------------------------------------------------

def build_reporting_by_precinct(
    precincts_master:  pd.DataFrame,
    results_snapshot:  pd.DataFrame,
    crosswalk:         pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    One row for every expected clustered precinct.

    Starts from precincts_master so unreported precincts are still visible
    (not dropped like they would be if we only grouped results_snapshot).
    """

    # Group results by precinct
    if not results_snapshot.empty:
        grp = (
            results_snapshot
            .groupby("PRECINCT_CODE", as_index=False)
            .agg(
                RESULT_ROW_COUNT      = ("PRECINCT_CODE", "count"),
                DISTINCT_CONTEST_COUNT= ("CONTEST_CODE",  "nunique"),
                FIRST_RECEPTION       = ("RECEPTION_DATETIME", "min"),
                LAST_RECEPTION        = ("RECEPTION_DATETIME", "max"),
            )
            .rename(columns={"PRECINCT_CODE": "ACM_ID"})
        )
    else:
        grp = pd.DataFrame(columns=[
            "ACM_ID", "RESULT_ROW_COUNT", "DISTINCT_CONTEST_COUNT",
            "FIRST_RECEPTION", "LAST_RECEPTION"
        ])

    # Left join to full expected precinct universe
    df = precincts_master.merge(grp, on="ACM_ID", how="left")

    # Merge crosswalk for district info (optional)
    if crosswalk is not None and not crosswalk.empty:
        xwalk_slim = crosswalk[["CLUSTER_ID", "DISTRICT_CODE", "DISTRICT_NAME"]].drop_duplicates()
        df = df.merge(
            xwalk_slim.rename(columns={"CLUSTER_ID": "ACM_ID"}),
            on="ACM_ID", how="left"
        )

    df["IS_REPORTED"]        = df["RESULT_ROW_COUNT"].notna()
    df["REPORTING_STATUS"]   = df["IS_REPORTED"].map({True: "REPORTED", False: "NOT_REPORTED"})
    df["EXPECTED_PRECINCT_COUNT"]  = 1
    df["REPORTED_PRECINCT_COUNT"]  = df["IS_REPORTED"].astype(int)
    df["UNREPORTED_PRECINCT_COUNT"]= (~df["IS_REPORTED"]).astype(int)

    df.rename(columns={
        "FIRST_RECEPTION": "FIRST_RECEPTION_DATETIME",
        "LAST_RECEPTION":  "LAST_RECEPTION_DATETIME",
        "ACM_ID":          "CLUSTERED_PRECINCT_ID",
    }, inplace=True)

    df["RECORD_SCOPE"]  = "EXPECTED_CLUSTERED_PRECINCT"
    df["ENVIRONMENT"]   = config.ENVIRONMENT

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 82  dashboard_verification_summary  (choice-level)
# ---------------------------------------------------------------------------

def build_verification_summary(
    verification_comparison: pd.DataFrame,
) -> pd.DataFrame:
    """
    Small choice-level summary table (4 rows: MATCHED, MISMATCH, CSV_ONLY, QR_ONLY).

    Scoped to IS_QR_VERIFICATION_SCOPE == True only.
    """
    scoped = verification_comparison[
        verification_comparison["IS_QR_VERIFICATION_SCOPE"] == True
    ].copy()

    statuses = [STATUS_MATCHED, STATUS_MISMATCH, STATUS_CSV_ONLY, STATUS_QR_ONLY]
    status_sort = {s: i+1 for i, s in enumerate(statuses)}

    total_rows         = len(scoped)
    total_csv_rows     = int(scoped["CSV_EXISTS"].sum())
    total_qr_rows      = int(scoped["QR_EXISTS"].sum())
    both_present_rows  = int((scoped["CSV_EXISTS"] & scoped["QR_EXISTS"]).sum())

    rows = []
    for status in statuses:
        subset = scoped[scoped["COMPARISON_STATUS"] == status]
        count  = len(subset)
        rows.append({
            "COMPARISON_STATUS":    status,
            "STATUS_SORT":          status_sort[status],
            "STATUS_COUNT":         count,
            "TOTAL_COMPARISON_ROWS":total_rows,
            "TOTAL_CSV_ROWS":       total_csv_rows,
            "TOTAL_IN_SCOPE_CSV":   total_csv_rows,
            "TOTAL_QR_ROWS":        total_qr_rows,
            "TOTAL_BOTH_PRESENT":   both_present_rows,
        })

    df = pd.DataFrame(rows)

    # Compute rates
    df["OVERALL_STATUS_RATE"] = _safe_div(df["STATUS_COUNT"], total_rows)
    df["MATCH_RATE"]    = _safe_div(
        df["STATUS_COUNT"].where(df["COMPARISON_STATUS"] == STATUS_MATCHED, 0),
        both_present_rows
    )
    df["MISMATCH_RATE"] = _safe_div(
        df["STATUS_COUNT"].where(df["COMPARISON_STATUS"] == STATUS_MISMATCH, 0),
        both_present_rows
    )
    df["QR_WITH_CSV_RATE"]  = _safe_div(both_present_rows, total_qr_rows)
    df["CSV_WITH_QR_RATE"]  = _safe_div(both_present_rows, total_csv_rows)
    df["OVERALL_STATUS_PCT"]= (df["OVERALL_STATUS_RATE"] * 100).round(2)
    df["MATCH_PCT"]         = (df["MATCH_RATE"]    * 100).round(2)
    df["MISMATCH_PCT"]      = (df["MISMATCH_RATE"] * 100).round(2)

    df["RECORD_SCOPE"] = "IN_SCOPE_CHOICE_LEVEL_VERIFICATION"
    df["ENVIRONMENT"]  = config.ENVIRONMENT

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 83  dashboard_verification_by_precinct  (precinct-level detail)
# ---------------------------------------------------------------------------

def build_verification_by_precinct(
    verification_comparison: pd.DataFrame,
) -> pd.DataFrame:
    """
    One verification row per clustered precinct (in-scope only).

    A precinct is MISMATCH if even one in-scope choice mismatches,
    appears only on one side, or has a missing vote value.
    """
    scoped = verification_comparison[
        verification_comparison["IS_QR_VERIFICATION_SCOPE"] == True
    ].copy()

    if scoped.empty:
        return pd.DataFrame(columns=[
            "CLUSTERED_PRECINCT_ID", "PRECINCT_STATUS", "RECORD_SCOPE"
        ])

    agg = scoped.groupby("CLUSTERED_PRECINCT_ID", as_index=False).agg(
        CSV_ROW_COUNT      = ("CSV_EXISTS",    "sum"),
        QR_ROW_COUNT       = ("QR_EXISTS",     "sum"),
        BOTH_PRESENT_COUNT = (
            "CSV_EXISTS",
            lambda x: int((x & scoped.loc[x.index, "QR_EXISTS"]).sum())
        ),
        MATCHED_ROW_COUNT  = ("IS_MATCHED",    "sum"),
        MISMATCH_ROW_COUNT = ("IS_MISMATCH",   "sum"),
        CSV_ONLY_ROW_COUNT = (
            "COMPARISON_STATUS",
            lambda x: int((x == STATUS_CSV_ONLY).sum())
        ),
        QR_ONLY_ROW_COUNT  = (
            "COMPARISON_STATUS",
            lambda x: int((x == STATUS_QR_ONLY).sum())
        ),
        TOTAL_ABS_VOTE_DIFF = (
            "VOTE_DIFFERENCE",
            lambda x: x.abs().sum()
        ),
        MAX_ABS_VOTE_DIFF   = (
            "VOTE_DIFFERENCE",
            lambda x: x.abs().max()
        ),
    )

    # Presence flags
    agg["HAS_CSV"] = agg["CSV_ROW_COUNT"] > 0
    agg["HAS_QR"]  = agg["QR_ROW_COUNT"]  > 0

    # Precinct status
    agg["PRECINCT_STATUS"] = agg.apply(_precinct_status, axis=1)
    agg["STATUS_SORT"]     = agg["PRECINCT_STATUS"].map({
        STATUS_MATCHED:  1, STATUS_MISMATCH: 2,
        STATUS_CSV_ONLY: 3, STATUS_QR_ONLY:  4,
    }).astype("Int64")

    # Rates
    agg["MATCH_RATE"]    = _safe_div_series(agg["MATCHED_ROW_COUNT"],  agg["BOTH_PRESENT_COUNT"])
    agg["MISMATCH_RATE"] = _safe_div_series(agg["MISMATCH_ROW_COUNT"], agg["BOTH_PRESENT_COUNT"])
    agg["QR_FOUND_IN_CSV_RATE"]  = _safe_div_series(agg["BOTH_PRESENT_COUNT"], agg["QR_ROW_COUNT"])
    agg["CSV_COVERED_BY_QR_RATE"]= _safe_div_series(agg["BOTH_PRESENT_COUNT"], agg["CSV_ROW_COUNT"])

    agg["RECORD_SCOPE"] = "IN_SCOPE_PRECINCT_VERIFICATION"
    agg["ENVIRONMENT"]  = config.ENVIRONMENT

    return agg.reset_index(drop=True)


def _precinct_status(row) -> str:
    has_csv = bool(row["HAS_CSV"])
    has_qr  = bool(row["HAS_QR"])
    if has_csv and not has_qr:
        return STATUS_CSV_ONLY
    if has_qr and not has_csv:
        return STATUS_QR_ONLY
    if has_csv and has_qr:
        problem = (
            row["MISMATCH_ROW_COUNT"] > 0
            or row["CSV_ONLY_ROW_COUNT"] > 0
            or row["QR_ONLY_ROW_COUNT"] > 0
        )
        return STATUS_MISMATCH if problem else STATUS_MATCHED
    return STATUS_CSV_ONLY


# ---------------------------------------------------------------------------
# 84  dashboard_verification_precinct_summary (QR coverage KPIs)
# ---------------------------------------------------------------------------

def build_verification_precinct_summary(
    precincts_master:        pd.DataFrame,
    er_canonical:            pd.DataFrame,
    verification_by_precinct:pd.DataFrame,
) -> pd.DataFrame:
    """
    One row per precinct verification status (MATCHED, MISMATCH, CSV_ONLY, QR_ONLY).

    Three denominators kept separate:
    - TOTAL_PRECINCTS        = all expected ACM_IDs  (QR coverage denominator)
    - TOTAL_SCANNED_PRECINCTS= distinct canonical clustered precinct IDs
    - TOTAL_COMPARABLE       = precincts where both CSV and QR are present
    """
    total_expected  = precincts_master["ACM_ID"].nunique()
    total_scanned   = er_canonical["clustered_precinct_id"].nunique()

    if not verification_by_precinct.empty:
        total_comparable = int(
            (verification_by_precinct["HAS_CSV"] & verification_by_precinct["HAS_QR"]).sum()
        )
        unseen = total_expected - total_scanned
    else:
        total_comparable = 0
        unseen = total_expected

    statuses = [STATUS_MATCHED, STATUS_MISMATCH, STATUS_CSV_ONLY, STATUS_QR_ONLY]
    rows = []
    for status in statuses:
        if not verification_by_precinct.empty:
            count = int((verification_by_precinct["PRECINCT_STATUS"] == status).sum())
        else:
            count = 0

        match_rate    = _safe_div(count, total_comparable) if status == STATUS_MATCHED    else None
        mismatch_rate = _safe_div(count, total_comparable) if status == STATUS_MISMATCH   else None
        qr_cov        = _safe_div(total_scanned, total_expected)

        rows.append({
            "VERIFICATION_STATUS":       status,
            "STATUS_SORT":               statuses.index(status) + 1,
            "PRECINCT_COUNT":            count,
            "TOTAL_PRECINCTS":           total_expected,
            "TOTAL_VERIFICATION_PRECINCTS": len(verification_by_precinct),
            "UNSEEN_PRECINCT_COUNT":     unseen,
            "TOTAL_SCANNED_PRECINCTS":   total_scanned,
            "TOTAL_COMPARABLE_PRECINCTS":total_comparable,
            "QR_COVERAGE_RATE":          round(qr_cov, 4),
            "QR_COVERAGE_PCT":           round(qr_cov * 100, 2),
            "MATCH_RATE":                round(match_rate,    4) if match_rate    is not None else None,
            "MISMATCH_RATE":             round(mismatch_rate, 4) if mismatch_rate is not None else None,
            "RECORD_SCOPE":              "PRECINCT_LEVEL_VERIFICATION_SUMMARY",
            "ENVIRONMENT":               config.ENVIRONMENT,
        })

    return pd.DataFrame(rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 86  dashboard_qr_timeline
# ---------------------------------------------------------------------------

def build_qr_timeline(
    er_canonical:     pd.DataFrame,
    precincts_master: pd.DataFrame,
) -> pd.DataFrame:
    """
    QR precinct coverage over time.

    RULE: Only the FIRST canonical receipt per clustered precinct is counted.
    Later resubmissions must not inflate cumulative coverage.
    Time is Philippine local time (UTC+8).
    """
    if er_canonical.empty:
        return pd.DataFrame(columns=[
            "TIME_BUCKET", "QR_RECEIVED_COUNT", "CUMULATIVE_QR_RECEIVED",
            "TOTAL_EXPECTED_PRECINCTS", "TIMELINE_INDEX", "RECORD_SCOPE", "ENVIRONMENT"
        ])

    df = er_canonical.copy()
    df = df[df["clustered_precinct_id"].notna() & df["received_at"].notna()].copy()

    # Convert to Philippine local time
    if hasattr(df["received_at"].dtype, "tz"):
        df["received_at_ph"] = df["received_at"].dt.tz_convert(PH_TZ)
    else:
        df["received_at_ph"] = pd.to_datetime(df["received_at"], utc=True).dt.tz_convert(PH_TZ)

    # Keep only first canonical receipt per precinct
    first_receipt = (
        df.groupby("clustered_precinct_id", as_index=False)["received_at_ph"]
        .min()
    )

    # Bucket to minute
    first_receipt["TIME_BUCKET"] = first_receipt["received_at_ph"].dt.floor("min")
    first_receipt["RECEIVED_DATE"]  = first_receipt["received_at_ph"].dt.date
    first_receipt["RECEIVED_HOUR"]  = first_receipt["received_at_ph"].dt.hour

    timeline = (
        first_receipt.groupby("TIME_BUCKET", as_index=False)
        .agg(
            QR_RECEIVED_COUNT = ("clustered_precinct_id", "count"),
            RECEIVED_DATE     = ("RECEIVED_DATE",          "first"),
            RECEIVED_HOUR     = ("RECEIVED_HOUR",          "first"),
        )
        .sort_values("TIME_BUCKET")
    )

    timeline["CUMULATIVE_QR_RECEIVED"]  = timeline["QR_RECEIVED_COUNT"].cumsum()
    timeline["TOTAL_EXPECTED_PRECINCTS"] = precincts_master["ACM_ID"].nunique()
    timeline["QR_COVERAGE_RATE"] = _safe_div_series(
        timeline["CUMULATIVE_QR_RECEIVED"], timeline["TOTAL_EXPECTED_PRECINCTS"]
    )
    timeline["QR_COVERAGE_PCT"]  = (timeline["QR_COVERAGE_RATE"] * 100).round(2)
    timeline["TIMELINE_INDEX"]   = range(1, len(timeline) + 1)
    timeline["REFRESH_TIMESTAMP_UTC"] = pd.Timestamp.utcnow()
    timeline["RECORD_SCOPE"]     = "FIRST_QR_RECEIPT_PER_CLUSTERED_PRECINCT"
    timeline["ENVIRONMENT"]      = config.ENVIRONMENT

    return timeline.reset_index(drop=True)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _safe_div(numerator, denominator) -> float:
    """Avoid ZeroDivisionError; return 0.0 when denominator is 0."""
    try:
        denom = int(denominator)
        return round(int(numerator) / denom, 4) if denom > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_div_series(num: pd.Series, denom: pd.Series) -> pd.Series:
    """Element-wise safe division for Series."""
    result = pd.Series(0.0, index=num.index)
    nonzero = denom.fillna(0) != 0
    result[nonzero] = num[nonzero] / denom[nonzero]
    return result.round(4)
