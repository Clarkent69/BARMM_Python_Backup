"""
Central configuration for the NAMFREL Python backup.

Edit INPUT_ROOT to point to your local folder containing the raw CSVs
that NAMFREL normally downloads from SharePoint before loading into Fabric.
Edit OUTPUT_ROOT to point to wherever you want the cleaned CSVs written.

If uploading back to SharePoint via Microsoft Graph or the REST client,
set SHAREPOINT_SITE_URL and SHAREPOINT_CLEANED_FOLDER.
"""

from pathlib import Path

ENVIRONMENT = "Production"

BASE_DIR = Path(__file__).resolve().parent
INPUT_ROOT   = BASE_DIR.parent / "FOLDER NAME" / ENVIRONMENT
OUTPUT_ROOT  = BASE_DIR.parent / "FOLDER NAME" / ENVIRONMENT / "Cleaned"

# ---------------------------------------------------------------------------
# RAW SOURCE FILE PATHS
# ---------------------------------------------------------------------------
RAW_DIR                 = INPUT_ROOT / "Raw Sources"
RAW_PRECINCTS           = RAW_DIR / "precincts.csv"
RAW_CANDIDATES          = RAW_DIR / "candidates.csv"
RAW_CONTESTS            = RAW_DIR / "contest.csv"
RAW_PARTIES             = RAW_DIR / "parties.csv"
RAW_RESULTS             = RAW_DIR / "results.csv"
RAW_MOBILE_ER           = RAW_DIR / "Mobile App" / "election_returns.csv"
RAW_MOBILE_QR           = RAW_DIR / "Mobile App" / "qr_results.csv"

# ---------------------------------------------------------------------------
# REFERENCE FILE PATHS
# ---------------------------------------------------------------------------
REF_DIR                 = INPUT_ROOT / "Reference"
REF_VERIFIED_DIR        = REF_DIR / "Verified"

REF_COMELEC_BALLOT_VERIFIED = REF_VERIFIED_DIR / "comelec_ballot_choice_reference_verified.csv"
REF_QR_DISTRICT_MAP         = REF_VERIFIED_DIR / "qr_district_ballot_position_map.csv"
REF_QR_SCOPE                = REF_VERIFIED_DIR / "qr_verification_scope.csv"

# ---------------------------------------------------------------------------
# OUTPUT FILE PATHS  (mirrors Datasets/<ENV>/Cleaned/ on SharePoint)
# ---------------------------------------------------------------------------
OUT_PRECINCTS_MASTER                = OUTPUT_ROOT / "precincts_master.csv"
OUT_CANDIDATES_MASTER               = OUTPUT_ROOT / "candidates_master.csv"
OUT_CONTESTS_MASTER                 = OUTPUT_ROOT / "contests_master.csv"
OUT_PARTIES_MASTER                  = OUTPUT_ROOT / "parties_master.csv"
OUT_RESULTS_SNAPSHOT                = OUTPUT_ROOT / "results_snapshot.csv"
OUT_MOBILE_ER                       = OUTPUT_ROOT / "mobile_election_returns.csv"
OUT_MOBILE_ER_CANONICAL             = OUTPUT_ROOT / "mobile_election_returns_canonical.csv"
OUT_MOBILE_QR                       = OUTPUT_ROOT / "mobile_qr_results.csv"
OUT_MOBILE_QR_CANONICAL             = OUTPUT_ROOT / "mobile_qr_results_canonical.csv"
OUT_VERIFICATION_COMPARISON         = OUTPUT_ROOT / "verification_comparison.csv"
OUT_DASHBOARD_REPORTING             = OUTPUT_ROOT / "dashboard_reporting_by_precinct.csv"
OUT_DASHBOARD_VERIF_SUMMARY         = OUTPUT_ROOT / "dashboard_verification_summary.csv"
OUT_DASHBOARD_VERIF_BY_PRECINCT     = OUTPUT_ROOT / "dashboard_verification_by_precinct.csv"
OUT_DASHBOARD_VERIF_PRECINCT_SUM    = OUTPUT_ROOT / "dashboard_verification_precinct_summary.csv"
OUT_DASHBOARD_QR_TIMELINE           = OUTPUT_ROOT / "dashboard_qr_timeline.csv"
OUT_QA_FINAL_SUMMARY                = OUTPUT_ROOT / "qa_final_summary.csv"
OUT_QA_PUBLICATION_GATE             = OUTPUT_ROOT / "qa_publication_gate.csv"

# TIMEZONE
PHILIPPINE_TZ_OFFSET_HOURS = 8      # UTC+8
DEV_FALLBACK_QR_SCOPE_CONTESTS = ["01295000", "01807001"]