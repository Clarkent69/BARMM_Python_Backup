# NAMFREL Python Backup Pipeline

A fully self-contained Python backup for the NAMFREL BARMM Parliamentary Election
data pipeline. Use this when Microsoft Fabric Dataflow Gen2 is unavailable.

---

## When to use this

Run this backup if:
- Microsoft Fabric Dataflow Gen2 fails or times out on election night.
- The Power BI Semantic Model cannot refresh because cleaned CSVs are missing.
- You need a fast local verification of CSV vs QR data without cloud dependency.

---

## Prerequisites

```bash
pip install -r requirements.txt
```

---

## Folder layout this pipeline expects

Place your downloaded SharePoint files under `Datasets/<Environment>/`:

```text
Datasets/
└── Development/                  ← or Production/
    ├── Raw Sources/
    │   ├── precincts.csv
    │   ├── candidates.csv
    │   ├── contest.csv
    │   ├── parties.csv
    │   ├── results.csv
    │   └── Mobile App/
    │       ├── election_returns.csv
    │       └── qr_results.csv
    └── Reference/
        └── Verified/
            ├── comelec_ballot_choice_reference_verified.csv
            ├── qr_district_ballot_position_map.csv
            └── qr_verification_scope.csv
```

---

## Configuration

Open `config.py` and set:

| Setting | Description |
|---|---|
| `INPUT_ROOT`  | Path to your `Datasets/<Environment>` folder |
| `OUTPUT_ROOT` | Where the cleaned CSVs will be written |

---

## Running the pipeline

```bash
cd namfrel_backup
python main_pipeline.py
```

The pipeline runs 7 phases and prints a live progress report with colors.

### Exit codes

| Code | Meaning |
|---:|---|
| 0 | `READY_TO_PUBLISH` — all QA checks passed |
| 1 | `READY_WITH_WARNINGS` — no blockers, but review warnings before publishing |
| 2 | `BLOCKED / DO_NOT_PUBLISH` — one or more blocking QA checks failed |
| 3 | Unexpected Python error — check the stack trace |

---

## Outputs

All files are written to `OUTPUT_ROOT` (mirrors `Datasets/<Env>/Cleaned/` on SharePoint):

| File | Description |
|---|---|
| `precincts_master.csv` | Cleaned precinct dimension |
| `candidates_master.csv` | Cleaned candidate dimension |
| `contests_master.csv` | Cleaned contest dimension |
| `parties_master.csv` | Cleaned party dimension |
| `results_snapshot.csv` | Cleaned transmitted election results |
| `mobile_election_returns.csv` | All mobile ER submissions (incl. duplicates) |
| `mobile_election_returns_canonical.csv` | Deduplicated canonical mobile ERs |
| `mobile_qr_results.csv` | All raw QR choices |
| `mobile_qr_results_canonical.csv` | QR choices linked to canonical ERs |
| `verification_comparison.csv` | Full Outer Join: CSV vs QR result per precinct+contest+candidate |
| `dashboard_reporting_by_precinct.csv` | Transmission reporting % per precinct |
| `dashboard_verification_summary.csv` | Choice-level MATCHED/MISMATCH/CSV_ONLY/QR_ONLY rates |
| `dashboard_verification_by_precinct.csv` | Precinct-level verification status |
| `dashboard_verification_precinct_summary.csv` | QR coverage & match/mismatch KPIs |
| `dashboard_qr_timeline.csv` | QR precinct coverage growth over time |
| `qa_final_summary.csv` | All QA check results (0 rows = PASS) |
| `qa_publication_gate.csv` | Final publication decision row |

---

## Critical rules (same as the Power Query pipeline)

- **Never guess** missing ballot positions, precinct codes, or candidate codes.
- **Never sum** `NUMBER_VOTERS`, `UNDER_VOTES`, or `OVER_VOTES` across candidate rows — use `precinct_contest_summary`.
- **Never use row order** as ballot position. Approved references only.
- In **Production**, missing `qr_verification_scope.csv` or `qr_district_ballot_position_map.csv` is always BLOCKING.
- `DO_NOT_PUBLISH` means do not upload to SharePoint and do not refresh Power BI.

---

## Module reference

| Module | Mirrors |
|---|---|
| `config.py` | `pEnvironment`, all SharePoint paths |
| `normalizers.py` | `fxNormalizeHeaders`, `fxToWholeNumberStrict`, `fxNormalizeGeo`, `fxEnsureColumns` |
| `schema_contracts.py` | `Schema_Expected.pq` |
| `core_cleaning.py` | Queries 15–19, 72–75 |
| `reference_mapping.py` | Queries 87, 88, 94, 78 |
| `verification_engine.py` | Queries 76, 79, 81, 28 |
| `dashboard_rollups.py` | Queries 82–86 |
| `qa_gatekeeper.py` | Queries 29–54, 80, 89–97 |
| `main_pipeline.py` | Orchestrator |
