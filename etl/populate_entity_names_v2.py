#!/usr/bin/env python3
"""Populate (or refresh) the entity_names table from v2 data tables.

Aggregates all unique entity names and their occurrence counts from:
  - patent_file_wrapper_v2: first_applicant_name, first_inventor_name
  - patent_assignments_v2: assignor_name, assignee_name
  - maintenance_fee_events_v2: (no names, but used for patent coverage)

Run after any data load into the v2 tables:
  python etl/populate_entity_names_v2.py
"""

import json
import subprocess
import sys

PROJECT = "uspto-data-app"
DATASET = "uspto_data"


def run_bq(query: str, *, timeout: int = 600) -> str:
    result = subprocess.run(
        ["bq", "query", f"--project_id={PROJECT}", "--location=us-west1",
         "--nouse_legacy_sql", query],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main() -> None:
    print("Populating entity_names table from v2 data...")

    run_bq(f"""
    CREATE OR REPLACE TABLE `{DATASET}.entity_names`
    CLUSTER BY entity_name
    AS
    WITH all_names AS (
      -- Patent file wrapper: applicants and inventors
      SELECT first_applicant_name AS entity_name
      FROM `{DATASET}.patent_file_wrapper_v2`
      WHERE first_applicant_name IS NOT NULL
      UNION ALL
      SELECT first_inventor_name AS entity_name
      FROM `{DATASET}.patent_file_wrapper_v2`
      WHERE first_inventor_name IS NOT NULL
        AND first_inventor_name != first_applicant_name
      UNION ALL
      -- Assignment records: assignees
      SELECT assignee_name AS entity_name
      FROM `{DATASET}.patent_assignments_v2`
      WHERE assignee_name IS NOT NULL
      UNION ALL
      -- Assignment records: assignors
      SELECT assignor_name AS entity_name
      FROM `{DATASET}.patent_assignments_v2`
      WHERE assignor_name IS NOT NULL
    )
    SELECT entity_name, COUNT(*) AS frequency
    FROM all_names
    GROUP BY entity_name
    """)

    # Verify row count
    result = subprocess.run(
        ["bq", "query", f"--project_id={PROJECT}", "--location=us-west1",
         "--format=json", "--nouse_legacy_sql",
         f"SELECT COUNT(*) AS cnt FROM `{DATASET}.entity_names`"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        rows = json.loads(result.stdout)
        print(f"entity_names populated: {rows[0]['cnt']} unique names")
    else:
        print("Table created but could not verify count.")

    print("Done.")


if __name__ == "__main__":
    main()
