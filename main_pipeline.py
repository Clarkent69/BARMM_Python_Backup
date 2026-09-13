"""
namfrel_backup/main_pipeline.py
================================
Orchestrator — runs the full NAMFREL Python backup pipeline.

Usage:
    python main_pipeline.py

Environment:
    Edit config.py to change ENVIRONMENT ("Development" or "Production"),
    INPUT_ROOT, and OUTPUT_ROOT before running.

Pipeline Phases:
    Phase 1 — Core Cleaning      (raw CSVs → master tables)
    Phase 2 — Reference Mapping  (ballot maps, QR scope)
    Phase 3 — Verification       (Full Outer Join CSV vs QR)
    Phase 4 — Dashboard Rollups  (6 dashboard summary tables)
    Phase 5 — QA Gatekeeper      (20+ logic checks)
    Phase 6 — Publication Gate   (BLOCKED / READY_WITH_WARNINGS / READY)
    Phase 7 — Export             (write 10+ CSVs to OUTPUT_ROOT)
"""

from __future__ import annotations
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import config
import schema_contracts
import core_cleaning
import reference_mapping
import verification
import dashboard_rollups
import qa_gatekeeper
from normalizers import MissingRequiredColumn
from reference_mapping import MissingProductionReference
from verification import CartesianJoinError


# ---------------------------------------------------------------------------
# CONSOLE HELPERS
# ---------------------------------------------------------------------------

try:
    from colorama import init as _colorama_init, Fore, Style
    _colorama_init(autoreset=True)
    _HAS_COLOR = True
except ImportError:
    _HAS_COLOR = False

def _c(text: str, color: str = "") -> str:
    if not _HAS_COLOR:
        return text
    colors = {
        "green":  Fore.GREEN,
        "red":    Fore.RED,
        "yellow": Fore.YELLOW,
        "cyan":   Fore.CYAN,
        "bold":   Style.BRIGHT,
        "reset":  Style.RESET_ALL,
    }
    return colors.get(color, "") + text + Style.RESET_ALL


def _banner():
    line = "=" * 72
    print(_c(line, "cyan"))
    print(_c("  NAMFREL BARMM Parliamentary Election -- Python Backup Pipeline", "bold"))
    print(_c(f"  Environment : {config.ENVIRONMENT}", "cyan"))
    print(_c(f"  Input Root  : {config.INPUT_ROOT}", "cyan"))
    print(_c(f"  Output Root : {config.OUTPUT_ROOT}", "cyan"))
    print(_c(f"  Run started : {datetime.now(timezone.utc).isoformat()}", "cyan"))
    print(_c(line, "cyan"))
    print()


def _phase(num: int, name: str):
    print(_c(f"\n[PHASE {num}] {name}", "bold"))
    print(_c("-" * 50, "cyan"))


def _ok(msg: str):
    print(_c(f"  [OK]  {msg}", "green"))


def _warn(msg: str):
    print(_c(f"  [!!]  {msg}", "yellow"))


def _fail(msg: str):
    print(_c(f"  [XX]  {msg}", "red"))


def _export(df: pd.DataFrame, path: Path, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    _ok(f"Exported {label}  ->  {path.name}  ({len(df):,} rows, {len(df.columns)} cols)")


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Run the full pipeline.

    Returns:
        0  if READY_TO_PUBLISH
        1  if READY_WITH_WARNINGS (exported but needs review)
        2  if BLOCKED / DO_NOT_PUBLISH
        3  on unexpected Python error
    """
    _banner()

    # -----------------------------------------------------------------------
    # PHASE 1 — CORE CLEANING
    # -----------------------------------------------------------------------
    _phase(1, "Core Cleaning -- Raw CSVs -> Master Tables")

    try:
        precincts_master  = core_cleaning.clean_precincts()
        _ok(f"precincts_master       {len(precincts_master):>7,} rows")

        candidates_master = core_cleaning.clean_candidates()
        _ok(f"candidates_master      {len(candidates_master):>7,} rows")

        contests_master   = core_cleaning.clean_contests()
        _ok(f"contests_master        {len(contests_master):>7,} rows")

        parties_master    = core_cleaning.clean_parties()
        _ok(f"parties_master         {len(parties_master):>7,} rows")

        results_snapshot  = core_cleaning.clean_results()
        _ok(f"results_snapshot       {len(results_snapshot):>7,} rows")

        mobile_available = config.RAW_MOBILE_ER.exists() and config.RAW_MOBILE_QR.exists()
        
        if mobile_available:
            mobile_er         = core_cleaning.clean_mobile_ers()
            _ok(f"mobile_election_returns{len(mobile_er):>7,} rows  "
                f"({mobile_er['is_duplicate'].sum()} duplicates)")

            er_canonical      = core_cleaning.clean_mobile_er_canonical(mobile_er)
            _ok(f"er_canonical           {len(er_canonical):>7,} rows")

            mobile_qr         = core_cleaning.clean_mobile_qr()
            _ok(f"mobile_qr_results      {len(mobile_qr):>7,} rows")

            qr_canonical      = core_cleaning.clean_mobile_qr_canonical(mobile_qr, er_canonical)
            _ok(f"qr_results_canonical   {len(qr_canonical):>7,} rows")
        else:
            _warn("Mobile scan files not found. Bypassing QR verification.")
            mobile_er    = pd.DataFrame(columns=["er_id", "clustered_precinct_id", "is_duplicate", "received_at", "IS_CANONICAL"])
            er_canonical = pd.DataFrame(columns=["er_id", "clustered_precinct_id", "is_duplicate", "received_at", "IS_CANONICAL"])
            mobile_qr    = pd.DataFrame(columns=["er_id", "district_code", "candidate_no", "votes"])
            qr_canonical = pd.DataFrame(columns=["er_id", "district_code", "candidate_no", "votes"])

    except MissingRequiredColumn as exc:
        _fail(f"BLOCKING — Missing required column: {exc}")
        return 2
    except FileNotFoundError as exc:
        _fail(f"BLOCKING — Raw input file not found: {exc}")
        return 2

    # -----------------------------------------------------------------------
    # PHASE 2 — REFERENCE MAPPING
    # -----------------------------------------------------------------------
    _phase(2, "Reference Mapping — Ballot Maps & QR Scope")

    try:
        scope_df       = reference_mapping.load_qr_verification_scope()
        _ok(f"QR verification scope  {len(scope_df):>7,} contests "
            f"{'[MOCK]' if scope_df.get('IS_MOCK_SCOPE', pd.Series([False])).any() else ''}")

        region_wide_ref = reference_mapping.load_region_wide_ballot_reference()
        _ok(f"Region-wide ballot ref {len(region_wide_ref):>7,} choices")

        district_map    = reference_mapping.load_district_ballot_position_map()
        _ok(f"District ballot map    {len(district_map):>7,} entries "
            f"{'[EMPTY-DEV FALLBACK]' if district_map.empty else ''}")

        ballot_map      = reference_mapping.build_qr_ballot_choice_map(
            candidates_master, region_wide_ref, district_map
        )
        _ok(f"Combined ballot map    {len(ballot_map):>7,} mappings")

    except MissingProductionReference as exc:
        _fail(f"BLOCKING (Production) — {exc}")
        return 2
    except FileNotFoundError as exc:
        _warn(f"Reference file not found — using fallback if available: {exc}")
        # Non-fatal at this stage; QA 95 will catch and block if needed
        ballot_map    = pd.DataFrame()
        scope_df      = pd.DataFrame({"CONTEST_CODE": config.DEV_FALLBACK_QR_SCOPE_CONTESTS})
        region_wide_ref = pd.DataFrame()

    # -----------------------------------------------------------------------
    # PHASE 3 — VERIFICATION
    # -----------------------------------------------------------------------
    _phase(3, "Verification -- Full Outer Join CSV vs QR")

    scope_codes = set(scope_df["CONTEST_CODE"].dropna())

    try:
        csv_side  = verification.build_csv_side(results_snapshot, er_canonical)
        _ok(f"CSV side built         {len(csv_side):>7,} rows")

        qr_side   = verification.build_qr_side(qr_canonical, ballot_map, scope_codes)
        _ok(f"QR side built          {len(qr_side):>7,} rows  "
            f"({int(qr_side.get('MAPPING_VALID', pd.Series(dtype=bool)).sum())} mapped)")

        verification = verification.run_verification(csv_side, qr_side, scope_df)
        vc_counts    = verification["COMPARISON_STATUS"].value_counts().to_dict()
        _ok(f"verification_comparison{len(verification):>7,} rows")
        for status, count in sorted(vc_counts.items()):
            print(f"       {status:<12}: {count:,}")

    except CartesianJoinError as exc:
        _fail(f"BLOCKING — Duplicate join keys detected (cartesian join prevented):\n  {exc}")
        return 2

    # Precinct contest summary (helper table)
    precinct_contest_summary = verification.build_precinct_contest_summary(results_snapshot)

    # -----------------------------------------------------------------------
    # PHASE 4 — DASHBOARD ROLLUPS
    # -----------------------------------------------------------------------
    _phase(4, "Dashboard Rollups -- Aggregated Summary Tables")

    try:
        dash_reporting         = dashboard_rollups.build_reporting_by_precinct(
            precincts_master, results_snapshot
        )
        _ok(f"dashboard_reporting_by_precinct             {len(dash_reporting):>7,} rows")

        dash_verif_summary     = dashboard_rollups.build_verification_summary(verification)
        _ok(f"dashboard_verification_summary              {len(dash_verif_summary):>7,} rows")

        dash_verif_by_precinct = dashboard_rollups.build_verification_by_precinct(verification)
        _ok(f"dashboard_verification_by_precinct          {len(dash_verif_by_precinct):>7,} rows")

        dash_precinct_summary  = dashboard_rollups.build_verification_precinct_summary(
            precincts_master, er_canonical, dash_verif_by_precinct
        )
        _ok(f"dashboard_verification_precinct_summary     {len(dash_precinct_summary):>7,} rows")

        dash_qr_timeline       = dashboard_rollups.build_qr_timeline(er_canonical, precincts_master)
        _ok(f"dashboard_qr_timeline                       {len(dash_qr_timeline):>7,} rows")

    except Exception as exc:
        _fail(f"Dashboard rollup error: {exc}")
        traceback.print_exc()
        return 3

    # -----------------------------------------------------------------------
    # PHASE 5 — QA GATEKEEPER
    # -----------------------------------------------------------------------
    _phase(5, "QA Gatekeeper -- Running All Logic Checks")

    final_summary, gate, issues = qa_gatekeeper.run_all_checks(
        precincts_master        = precincts_master,
        candidates_master       = candidates_master,
        contests_master         = contests_master,
        parties_master          = parties_master,
        results_snapshot        = results_snapshot,
        mobile_er               = mobile_er,
        er_canonical            = er_canonical,
        mobile_qr               = mobile_qr,
        qr_canonical            = qr_canonical,
        ballot_map              = ballot_map if not ballot_map.empty else pd.DataFrame(),
        scope_df                = scope_df,
        qr_verification_results = qr_side,
        schema                  = schema_contracts.SCHEMA,
    )

    # Print QA summary table
    print()
    print(f"  {'CHECK':<48} {'SEVERITY':<10} {'COUNT':>6}  STATUS")
    print("  " + "-" * 78)
    for _, row in final_summary.iterrows():
        status   = row["STATUS"]
        color    = "red" if status == "BLOCKED" else "yellow" if status == "REVIEW" else "green"
        name     = row["QA_CHECK"][:48]
        print(f"  {name:<48} {row['SEVERITY']:<10} {row['ISSUE_COUNT']:>6}  "
              + _c(status, color))

    # -----------------------------------------------------------------------
    # PHASE 6 — PUBLICATION GATE DECISION
    # -----------------------------------------------------------------------
    _phase(6, "Publication Gate Decision")

    pub_decision = gate["PUBLICATION_DECISION"]
    pub_status   = gate["PUBLICATION_STATUS"]

    if pub_decision == "READY_TO_PUBLISH":
        print(_c(f"\n  [OK]  {pub_status} -- {gate['REQUIRED_ACTION']}", "green"))
        exit_code = 0
    elif pub_decision == "REVIEW_BEFORE_PUBLISH":
        print(_c(f"\n  [!!]  {pub_status} -- {gate['REQUIRED_ACTION']}", "yellow"))
        exit_code = 1
    else:
        print(_c(f"\n  [XX]  {pub_status} -- {gate['REQUIRED_ACTION']}", "red"))
        print(_c(f"        {gate['BLOCKING_CHECK_COUNT']} blocking check(s), "
                 f"{gate['BLOCKING_ISSUE_COUNT']} issue row(s).", "red"))
        print(_c("        Outputs will still be written for diagnostic review.", "yellow"))
        exit_code = 2

    # -----------------------------------------------------------------------
    # PHASE 7 — EXPORT
    # -----------------------------------------------------------------------
    _phase(7, "Export -- Writing Cleaned CSVs to Output Folder")

    config.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    exports = [
        (precincts_master,         config.OUT_PRECINCTS_MASTER,             "precincts_master"),
        (candidates_master,        config.OUT_CANDIDATES_MASTER,            "candidates_master"),
        (contests_master,          config.OUT_CONTESTS_MASTER,              "contests_master"),
        (parties_master,           config.OUT_PARTIES_MASTER,               "parties_master"),
        (results_snapshot,         config.OUT_RESULTS_SNAPSHOT,             "results_snapshot"),
        (precinct_contest_summary, config.OUTPUT_ROOT / "precinct_contest_summary.csv", "precinct_contest_summary"),
        (mobile_er,                config.OUT_MOBILE_ER,                    "mobile_election_returns"),
        (er_canonical,             config.OUT_MOBILE_ER_CANONICAL,          "mobile_election_returns_canonical"),
        (mobile_qr,                config.OUT_MOBILE_QR,                    "mobile_qr_results"),
        (qr_canonical,             config.OUT_MOBILE_QR_CANONICAL,          "mobile_qr_results_canonical"),
        (verification,             config.OUT_VERIFICATION_COMPARISON,      "verification_comparison"),
        (dash_reporting,           config.OUT_DASHBOARD_REPORTING,          "dashboard_reporting_by_precinct"),
        (dash_verif_summary,       config.OUT_DASHBOARD_VERIF_SUMMARY,      "dashboard_verification_summary"),
        (dash_verif_by_precinct,   config.OUT_DASHBOARD_VERIF_BY_PRECINCT,  "dashboard_verification_by_precinct"),
        (dash_precinct_summary,    config.OUT_DASHBOARD_VERIF_PRECINCT_SUM, "dashboard_verification_precinct_summary"),
        (dash_qr_timeline,         config.OUT_DASHBOARD_QR_TIMELINE,        "dashboard_qr_timeline"),
        (final_summary,            config.OUT_QA_FINAL_SUMMARY,             "qa_final_summary"),
    ]

    for df, path, label in exports:
        try:
            _export(df, path, label)
        except Exception as exc:
            _fail(f"Failed to export {label}: {exc}")

    # Export publication gate as single-row CSV
    gate_df = pd.DataFrame([gate])
    _export(gate_df, config.OUT_QA_PUBLICATION_GATE, "qa_publication_gate")

    # -----------------------------------------------------------------------
    # RUN SUMMARY
    # -----------------------------------------------------------------------
    print()
    print(_c("=" * 72, "cyan"))
    print(_c("  RUN SUMMARY", "bold"))
    print(_c(f"  Run ID               : {gate['RUN_ID']}", "cyan"))
    print(_c(f"  Environment          : {gate['ENVIRONMENT']}", "cyan"))
    print(_c(f"  Blocking checks      : {gate['BLOCKING_CHECK_COUNT']} "
              f"({gate['BLOCKING_ISSUE_COUNT']} issue rows)", "cyan"))
    print(_c(f"  Review warnings      : {gate['REVIEW_CHECK_COUNT']} "
              f"({gate['REVIEW_ISSUE_COUNT']} issue rows)", "cyan"))
    print(_c(f"  Publication decision : {pub_decision}",
              "green" if exit_code == 0 else "yellow" if exit_code == 1 else "red"))
    print(_c(f"  Output folder        : {config.OUTPUT_ROOT}", "cyan"))
    print(_c("=" * 72, "cyan"))
    print()

    return exit_code


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Pipeline cancelled by user.")
        rc = 3
    except Exception as exc:
        print(_c(f"\n[FATAL ERROR] Unexpected exception: {exc}", "red"))
        traceback.print_exc()
        rc = 3
    sys.exit(rc)
