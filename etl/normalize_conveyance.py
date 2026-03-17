#!/usr/bin/env python3
"""Normalize conveyance_type in pat_assign_records.

One-time migration script that classifies 9.07M assignment records
into 12 normalized categories + review_flag for uncertain cases.

Steps:
  1. Rule-based classification for clear conveyance_text patterns (~700K records)
  2. Corporate assignor filter -> divestiture (all-corporate assignors)
  3. Inventor name matching -> employee vs divestiture (person assignors vs pfw_inventors)
  4. Explicit employment text fallback
  5. Remaining NULL -> divestiture + review_flag
  6. Backfill employer_assignment boolean
  7. Verification

Usage:
    python etl/normalize_conveyance.py [--dry-run] [--step N]
"""

import subprocess
import sys
import time

PROJECT = "uspto-data-app"
DATASET = "uspto_data"
LOCATION = "us-west1"


def run_bq(sql: str, label: str = "", timeout: int = 1800, dry_run: bool = False) -> str:
    """Execute a BigQuery SQL statement via bq CLI."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  {label}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if dry_run:
        print(f"  [DRY RUN] Would execute:\n{sql[:500]}...", file=sys.stderr)
        return ""

    start = time.time()
    result = subprocess.run(
        ["bq", "query",
         f"--project_id={PROJECT}",
         f"--location={LOCATION}",
         "--use_legacy_sql=false",
         "--max_rows=100",
         sql],
        capture_output=True, text=True, timeout=timeout,
    )
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"  ERROR ({elapsed:.1f}s): {result.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"  Done ({elapsed:.1f}s)", file=sys.stderr)
    if result.stdout.strip():
        print(result.stdout, file=sys.stderr)
    return result.stdout


# ---------------------------------------------------------------------------
# Step 1: Rule-based classification for non-assignment records
# ---------------------------------------------------------------------------

def step_1_address_change(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'address_change'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%CHANGE OF ADDRESS%'
    OR UPPER(conveyance_text) LIKE '%CHANGE OF ASSIGNEE ADDRESS%'
    OR UPPER(conveyance_text) LIKE '%ASSIGNEE CHANGE OF ADDRESS%'
    OR UPPER(conveyance_text) LIKE '%ADDRESS CHANGE%'
  )
""", label="Step 1a: address_change", dry_run=dry_run)


def step_1_name_change(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'name_change'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%CHANGE OF NAME%'
    OR UPPER(conveyance_text) LIKE '%ENTITY CONVERSION%'
    OR UPPER(conveyance_text) LIKE '%CERTIFICATE OF CONVERSION%'
    OR UPPER(conveyance_text) = 'CONVERSION'
    OR UPPER(conveyance_text) LIKE '%NAME CHANGE%'
  )
  AND UPPER(conveyance_text) NOT LIKE '%MERGER%'
""", label="Step 1b: name_change (excluding mergers)", dry_run=dry_run)


def step_1_merger(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'merger'
WHERE normalized_type IS NULL
  AND UPPER(conveyance_text) LIKE '%MERGER%'
""", label="Step 1c: merger", dry_run=dry_run)


def step_1_government(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'government'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%CONFIRMATORY LICENSE%'
    OR UPPER(conveyance_text) LIKE '%GOVERNMENT INTEREST%'
    OR UPPER(conveyance_text) LIKE '%EXECUTIVE ORDER%'
    OR UPPER(conveyance_text) LIKE '%RIGHTS OF THE GOVERNMENT%'
    OR UPPER(conveyance_text) LIKE '%SUBJECT TO LICENSE%'
  )
""", label="Step 1d: government", dry_run=dry_run)


def step_1_partial_release(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'partial_release'
WHERE normalized_type IS NULL
  AND UPPER(conveyance_text) LIKE '%PARTIAL RELEASE%'
""", label="Step 1e: partial_release", dry_run=dry_run)


def step_1_release(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'release'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%RELEASE OF SECURITY%'
    OR UPPER(conveyance_text) LIKE '%RELEASE BY SECURED%'
    OR (UPPER(conveyance_text) LIKE '%TERMINATION%' AND UPPER(conveyance_text) LIKE '%SECURITY%')
    OR UPPER(conveyance_text) LIKE '%DISCHARGE%'
    OR UPPER(conveyance_text) = 'RELEASE'
  )
""", label="Step 1f: release", dry_run=dry_run)


def step_1_license_termination(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'license_termination'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%LICENSE TERMINATION%'
    OR UPPER(conveyance_text) LIKE '%TERMINATION OF LICENSE%'
    OR UPPER(conveyance_text) LIKE '%TERMINATION OF%LICENSE%'
  )
""", label="Step 1g: license_termination", dry_run=dry_run)


def step_1_license(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'license'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%LICENSE%'
    OR UPPER(conveyance_text) LIKE '%LICENSING%'
  )
""", label="Step 1h: license", dry_run=dry_run)


def step_1_security(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'security'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%SECURITY INTEREST%'
    OR UPPER(conveyance_text) LIKE '%SECURITY AGREEMENT%'
    OR UPPER(conveyance_text) LIKE '%PATENT SECURITY%'
    OR UPPER(conveyance_text) LIKE '%INTELLECTUAL PROPERTY SECURITY%'
    OR UPPER(conveyance_text) LIKE '%GRANT OF SECURITY%'
    OR UPPER(conveyance_text) LIKE '%COLLATERAL%'
    OR UPPER(conveyance_text) LIKE '%PLEDGE%'
    OR UPPER(conveyance_text) LIKE '%MORTGAGE%'
    OR UPPER(conveyance_text) = 'LIEN'
    OR UPPER(conveyance_text) LIKE '%SECURED PARTY%'
  )
""", label="Step 1i: security", dry_run=dry_run)


def step_1_correction(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'correction'
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%CORRECTIVE%'
    OR UPPER(conveyance_text) LIKE '%CORRECTION%'
    OR UPPER(conveyance_text) LIKE '%ERRON%'
  )
  AND UPPER(conveyance_text) NOT LIKE '%NUNC PRO TUNC%'
  AND UPPER(conveyance_text) NOT LIKE '%ASSIGNMENT%'
""", label="Step 1j: correction (pure corrections only, not corrective assignments)", dry_run=dry_run)


def step_1_court_order(dry_run: bool = False):
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'divestiture',
    review_flag = TRUE
WHERE normalized_type IS NULL
  AND UPPER(conveyance_text) LIKE '%COURT ORDER%'
""", label="Step 1k: court order -> divestiture + review", dry_run=dry_run)


# ---------------------------------------------------------------------------
# Step 2: Corporate assignor filter -> divestiture
# ---------------------------------------------------------------------------

def step_2_corporate_filter(dry_run: bool = False):
    """Mark assignments where ALL assignors are corporate entities as divestiture."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records` r
SET r.normalized_type = 'divestiture',
    r.employer_assignment = FALSE
WHERE r.normalized_type IS NULL
  AND r.reel_frame IN (
    SELECT reel_frame
    FROM (
      SELECT
        a.reel_frame,
        -- Count assignors that do NOT look like corporate entities
        COUNTIF(
          a.assignor_name IS NOT NULL
          AND NOT REGEXP_CONTAINS(
            UPPER(a.assignor_name),
            r'\\b(INC\\.?|INCORPORATED|CORP\\.?|CORPORATION|LLC|L\\.L\\.C\\.|'
            r'LTD\\.?|LIMITED|CO\\.|COMPANY|COMPANIES|LP|L\\.P\\.|LLP|L\\.L\\.P\\.|'
            r'GMBH|AG|S\\.A\\.|SA|PLC|P\\.L\\.C\\.|N\\.V\\.|NV|BV|B\\.V\\.|'
            r'KK|KABUSHIKI|PTY|PTE|S\\.R\\.L\\.|SRL|'
            r'FOUNDATION|TRUST|UNIVERSITY|UNIVERSITE|UNIVERSITAT|'
            r'INSTITUT|INSTITUTE|COLLEGE|SCHOOL|HOSPITAL|'
            r'NATIONAL|LABORATORIES|LABORATORY|TECHNOLOGIES|SYSTEMS|'
            r'GROUP|HOLDINGS|ENTERPRISES|INDUSTRIES|INTERNATIONAL|'
            r'THE REGENTS|THE TRUSTEES|THE BOARD|COUNCIL|ASSOCIATION|'
            r'MINISTRY|GOVERNMENT|DEPARTMENT|AGENCY|AUTHORITY|'
            r'COOPERATIVE|FEDERATION|CONSORTIUM)\\b'
          )
        ) AS non_corporate_count
      FROM `{DATASET}.pat_assign_assignors` a
      WHERE a.reel_frame IN (
        SELECT reel_frame FROM `{DATASET}.pat_assign_records`
        WHERE normalized_type IS NULL
      )
      GROUP BY a.reel_frame
    )
    WHERE non_corporate_count = 0
  )
""", label="Step 2: Corporate assignor filter -> divestiture", timeout=3600, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Step 3: Inventor name matching -> employee vs divestiture
# ---------------------------------------------------------------------------

def step_3_create_staging(dry_run: bool = False):
    """Create staging table with inventor match statistics per reel_frame."""
    run_bq(f"""
CREATE OR REPLACE TABLE `{DATASET}._tmp_inventor_match` AS
WITH
-- Reel frames still needing classification
unclassified AS (
  SELECT reel_frame
  FROM `{DATASET}.pat_assign_records`
  WHERE normalized_type IS NULL
),

-- Person assignors (non-corporate) on unclassified records
person_assignors AS (
  SELECT DISTINCT
    a.reel_frame,
    a.assignor_name,
    -- Extract last name: text before comma (most assignor names are "LAST, FIRST")
    UPPER(TRIM(SPLIT(a.assignor_name, ',')[SAFE_OFFSET(0)])) AS assignor_last,
    -- Extract first name/initial: text after comma
    UPPER(TRIM(SPLIT(a.assignor_name, ',')[SAFE_OFFSET(1)])) AS assignor_first_part
  FROM `{DATASET}.pat_assign_assignors` a
  JOIN unclassified u ON u.reel_frame = a.reel_frame
  WHERE a.assignor_name IS NOT NULL
    AND NOT REGEXP_CONTAINS(
      UPPER(a.assignor_name),
      r'\\b(INC\\.?|INCORPORATED|CORP\\.?|CORPORATION|LLC|L\\.L\\.C\\.|'
      r'LTD\\.?|LIMITED|CO\\.|COMPANY|COMPANIES|LP|L\\.P\\.|LLP|L\\.L\\.P\\.|'
      r'GMBH|AG|S\\.A\\.|SA|PLC|P\\.L\\.C\\.|N\\.V\\.|NV|BV|B\\.V\\.|'
      r'KK|KABUSHIKI|PTY|PTE|S\\.R\\.L\\.|SRL|'
      r'FOUNDATION|TRUST|UNIVERSITY|UNIVERSITE|UNIVERSITAT|'
      r'INSTITUT|INSTITUTE|COLLEGE|SCHOOL|HOSPITAL|'
      r'NATIONAL|LABORATORIES|LABORATORY|TECHNOLOGIES|SYSTEMS|'
      r'GROUP|HOLDINGS|ENTERPRISES|INDUSTRIES|INTERNATIONAL|'
      r'THE REGENTS|THE TRUSTEES|THE BOARD|COUNCIL|ASSOCIATION|'
      r'MINISTRY|GOVERNMENT|DEPARTMENT|AGENCY|AUTHORITY|'
      r'COOPERATIVE|FEDERATION|CONSORTIUM)\\b'
    )
),

-- Application numbers linked to each reel_frame
doc_apps AS (
  SELECT DISTINCT d.reel_frame, d.application_number
  FROM `{DATASET}.pat_assign_documents` d
  JOIN unclassified u ON u.reel_frame = d.reel_frame
  WHERE d.application_number IS NOT NULL
),

-- Inventors for those applications (pre-joined for efficiency)
app_inventors AS (
  SELECT DISTINCT
    da.reel_frame,
    UPPER(i.inventor_name) AS inv_name_upper,
    UPPER(i.last_name) AS inv_last,
    UPPER(i.first_name) AS inv_first
  FROM `{DATASET}.pfw_inventors` i
  JOIN doc_apps da ON da.application_number = i.application_number
  WHERE i.last_name IS NOT NULL
),

-- Check each person-assignor against inventors
assignor_matches AS (
  SELECT
    pa.reel_frame,
    pa.assignor_name,
    CASE WHEN EXISTS (
      SELECT 1 FROM app_inventors ai
      WHERE ai.reel_frame = pa.reel_frame
        AND (
          -- Strategy 1: Last name match + first initial match
          (
            ai.inv_last = pa.assignor_last
            AND ai.inv_first IS NOT NULL
            AND pa.assignor_first_part IS NOT NULL
            AND LENGTH(pa.assignor_first_part) > 0
            AND LENGTH(ai.inv_first) > 0
            AND SUBSTR(pa.assignor_first_part, 1, 1) = SUBSTR(ai.inv_first, 1, 1)
          )
          -- Strategy 2: Full name match with format flip
          -- Assignor "LAST, FIRST" vs inventor "FIRST LAST"
          OR ai.inv_name_upper = CONCAT(
            COALESCE(pa.assignor_first_part, ''), ' ', pa.assignor_last
          )
          -- Strategy 3: Direct full-name match (same format)
          OR ai.inv_name_upper = UPPER(pa.assignor_name)
          -- Strategy 4: Last name only match (fuzzy fallback for typo resilience)
          -- Only used as a tiebreaker with other evidence
          OR (
            ai.inv_last = pa.assignor_last
            AND LENGTH(pa.assignor_last) >= 3
          )
        )
    ) THEN TRUE ELSE FALSE END AS matches_inventor
  FROM person_assignors pa
)

-- Aggregate: per reel_frame, how many assignors matched?
SELECT
  reel_frame,
  COUNT(*) AS total_person_assignors,
  COUNTIF(matches_inventor) AS matching_assignors
FROM assignor_matches
GROUP BY reel_frame
""", label="Step 3a: Create inventor match staging table", timeout=3600, dry_run=dry_run)


def step_3_classify_employee(dry_run: bool = False):
    """Classify assignments where majority of assignors match inventors as employee."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records` r
SET r.normalized_type = 'employee',
    r.employer_assignment = TRUE
WHERE r.normalized_type IS NULL
  AND r.reel_frame IN (
    SELECT reel_frame FROM `{DATASET}._tmp_inventor_match`
    WHERE matching_assignors > 0
      AND (
        -- All match: definitive employee
        matching_assignors = total_person_assignors
        -- Majority match (>=50%): almost certainly employee, rest are typos
        OR (total_person_assignors > 1 AND matching_assignors * 2 >= total_person_assignors)
      )
  )
""", label="Step 3b: Majority/all assignors match inventors -> employee", dry_run=dry_run)


def step_3_classify_divestiture(dry_run: bool = False):
    """Classify assignments where zero assignors match inventors as divestiture."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records` r
SET r.normalized_type = 'divestiture',
    r.employer_assignment = FALSE
WHERE r.normalized_type IS NULL
  AND r.reel_frame IN (
    SELECT reel_frame FROM `{DATASET}._tmp_inventor_match`
    WHERE matching_assignors = 0
  )
""", label="Step 3c: Zero assignor-inventor matches -> divestiture", dry_run=dry_run)


def step_3_classify_partial(dry_run: bool = False):
    """Classify assignments with minority matches -> divestiture + review."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records` r
SET r.normalized_type = 'divestiture',
    r.employer_assignment = FALSE,
    r.review_flag = TRUE
WHERE r.normalized_type IS NULL
  AND r.reel_frame IN (
    SELECT reel_frame FROM `{DATASET}._tmp_inventor_match`
    WHERE matching_assignors > 0
      AND total_person_assignors > 1
      AND matching_assignors * 2 < total_person_assignors
  )
""", label="Step 3d: Minority match -> divestiture + review_flag", dry_run=dry_run)


def step_3_drop_staging(dry_run: bool = False):
    """Drop the staging table."""
    run_bq(f"""
DROP TABLE IF EXISTS `{DATASET}._tmp_inventor_match`
""", label="Step 3e: Drop staging table", dry_run=dry_run)


# ---------------------------------------------------------------------------
# Step 4: Fallback classification for remaining NULLs
# ---------------------------------------------------------------------------

def step_4_employment_text(dry_run: bool = False):
    """Explicit employment text -> employee."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'employee',
    employer_assignment = TRUE
WHERE normalized_type IS NULL
  AND (
    UPPER(conveyance_text) LIKE '%EMPLOYMENT AGREEMENT%'
    OR UPPER(conveyance_text) LIKE '%EMPLOYEE AGREEMENT%'
    OR UPPER(conveyance_text) LIKE '%EMPLOYMENT CONTRACT%'
  )
""", label="Step 4a: Explicit employment text -> employee", dry_run=dry_run)


def step_4_remaining_nulls(dry_run: bool = False):
    """Everything still NULL -> divestiture + review_flag."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET normalized_type = 'divestiture',
    employer_assignment = FALSE,
    review_flag = TRUE
WHERE normalized_type IS NULL
""", label="Step 4b: Remaining NULL -> divestiture + review_flag", dry_run=dry_run)


# ---------------------------------------------------------------------------
# Step 5: Backfill employer_assignment for non-employee records
# ---------------------------------------------------------------------------

def step_5_backfill_employer(dry_run: bool = False):
    """Set employer_assignment = FALSE for all non-employee records still NULL."""
    run_bq(f"""
UPDATE `{DATASET}.pat_assign_records`
SET employer_assignment = FALSE
WHERE employer_assignment IS NULL
  AND normalized_type IS NOT NULL
  AND normalized_type != 'employee'
""", label="Step 5: Backfill employer_assignment = FALSE for non-employee", dry_run=dry_run)


# ---------------------------------------------------------------------------
# Step 6: Verification
# ---------------------------------------------------------------------------

def step_6_verify(dry_run: bool = False):
    """Print distribution of normalized_type values."""
    run_bq(f"""
SELECT
  normalized_type,
  COUNT(*) AS total,
  COUNTIF(review_flag) AS flagged,
  COUNTIF(employer_assignment) AS employer_true
FROM `{DATASET}.pat_assign_records`
GROUP BY normalized_type
ORDER BY total DESC
""", label="Step 6: Verification — normalized_type distribution")

    run_bq(f"""
SELECT
  COUNT(*) AS total_records,
  COUNTIF(normalized_type IS NULL) AS still_null,
  COUNTIF(employer_assignment IS NULL) AS employer_null,
  COUNTIF(review_flag) AS total_flagged
FROM `{DATASET}.pat_assign_records`
""", label="Step 6: Verification — completeness check")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    start_step = 0

    for arg in sys.argv[1:]:
        if arg.startswith("--step"):
            start_step = int(arg.split("=")[1]) if "=" in arg else int(sys.argv[sys.argv.index(arg) + 1])

    if dry_run:
        print("*** DRY RUN — no changes will be made ***\n", file=sys.stderr)

    steps = [
        # Step 1: Rule-based classification
        (1, "Rule-based: address_change", step_1_address_change),
        (2, "Rule-based: name_change", step_1_name_change),
        (3, "Rule-based: merger", step_1_merger),
        (4, "Rule-based: government", step_1_government),
        (5, "Rule-based: partial_release", step_1_partial_release),
        (6, "Rule-based: release", step_1_release),
        (7, "Rule-based: license_termination", step_1_license_termination),
        (8, "Rule-based: license", step_1_license),
        (9, "Rule-based: security", step_1_security),
        (10, "Rule-based: correction", step_1_correction),
        (11, "Rule-based: court_order", step_1_court_order),
        # Step 2: Corporate assignor filter
        (12, "Corporate assignor filter", step_2_corporate_filter),
        # Step 3: Inventor matching
        (13, "Create inventor match staging table", step_3_create_staging),
        (14, "Classify employee (majority match)", step_3_classify_employee),
        (15, "Classify divestiture (zero match)", step_3_classify_divestiture),
        (16, "Classify partial match -> review", step_3_classify_partial),
        (17, "Drop staging table", step_3_drop_staging),
        # Step 4: Fallback
        (18, "Employment text fallback", step_4_employment_text),
        (19, "Remaining NULLs -> divestiture + review", step_4_remaining_nulls),
        # Step 5: Backfill employer_assignment
        (20, "Backfill employer_assignment", step_5_backfill_employer),
        # Step 6: Verification
        (21, "Verification", step_6_verify),
    ]

    total_start = time.time()
    for step_num, desc, func in steps:
        if step_num < start_step:
            print(f"  Skipping step {step_num}: {desc}", file=sys.stderr)
            continue
        print(f"\n>>> Step {step_num}/{len(steps)}: {desc}", file=sys.stderr)
        func(dry_run=dry_run)

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  COMPLETE — total time: {total_elapsed/60:.1f} minutes", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)


if __name__ == "__main__":
    main()
