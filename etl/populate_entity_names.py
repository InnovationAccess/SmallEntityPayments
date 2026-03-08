#!/usr/bin/env python3
"""Populate (or refresh) the entity_names table.

Aggregates all unique entity names and their occurrence counts from
patent_file_wrapper.applicants and patent_assignments.assignees into
the pre-computed entity_names lookup table.

Run after any data load into the source tables:
  python etl/populate_entity_names.py
"""

import json
import subprocess
import sys

PROJECT = "uspto-data-app"
DATASET = "uspto_data"


def run_bq(query: str, *, timeout: int = 600) -> str:
    result = subprocess.run(
        ["bq", "query", f"--project_id={PROJECT}", "--nouse_legacy_sql", query],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main() -> None:
    print("Populating entity_names table...")

    run_bq(f"""
    CREATE OR REPLACE TABLE `{DATASET}.entity_names`
    CLUSTER BY entity_name
    AS
    WITH all_names AS (
      SELECT app.name AS entity_name
      FROM `{DATASET}.patent_file_wrapper`, UNNEST(applicants) AS app
      WHERE app.name IS NOT NULL
      UNION ALL
      SELECT asgn.name AS entity_name
      FROM `{DATASET}.patent_assignments`, UNNEST(assignees) AS asgn
      WHERE asgn.name IS NOT NULL
    )
    SELECT entity_name, COUNT(*) AS frequency
    FROM all_names
    GROUP BY entity_name
    """)

    # Verify row count.
    result = subprocess.run(
        ["bq", "query", f"--project_id={PROJECT}", "--format=json",
         "--nouse_legacy_sql",
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
