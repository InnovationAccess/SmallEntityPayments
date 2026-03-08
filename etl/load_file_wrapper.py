#!/usr/bin/env python3
"""Load patent_file_wrapper data from BigQuery patents.publications.

Exports granted US patents (kind_code B1/B2) year by year from the public
patents.publications dataset, transforms into our schema, and loads into
our patent_file_wrapper table.

Fields extracted:
  - patent_number: from publication_number (e.g. "US-10326040-B1" -> "10326040")
  - application_number: from application_number (e.g. "US-201815934063-A" -> "15934063")
  - invention_title: from title_localized[0].text
  - grant_date: from grant_date (YYYYMMDD -> YYYY-MM-DD)
  - applicants: empty array (enriched later from assignments)
"""

import gzip
import json
import os
import subprocess
import sys
import tempfile


PROJECT = "uspto-data-app"
TABLE = "uspto_data.patent_file_wrapper"
MIN_YEAR = 2006
MAX_YEAR = 2025


def extract_patent_number(pub_number: str) -> str:
    """Extract patent number from publication_number like 'US-10326040-B1'."""
    parts = pub_number.split("-")
    if len(parts) >= 2:
        return parts[1]
    return ""


def extract_app_number(app_number: str) -> str:
    """Extract application number from format like 'US-201815934063-A'.

    The publications dataset uses a 12-digit format (YYYY + 8-digit app number).
    We strip the 4-digit year prefix to get the standard 8-digit format that
    matches PASYR and PTMNFEE2 data.
    """
    parts = app_number.split("-")
    if len(parts) >= 2:
        raw = parts[1]
        # Strip year prefix from 12-digit numbers → standard 8-digit
        if len(raw) == 12 and raw.isdigit():
            return raw[4:]
        return raw
    return ""


def format_date(date_int: int) -> str:
    """Convert YYYYMMDD int to YYYY-MM-DD string."""
    s = str(date_int)
    if len(s) != 8:
        return ""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def export_year(year: int) -> str:
    """Export one year of data from BQ, returning path to temp JSONL.gz file."""
    print(f"  Querying year {year}...")

    query = f"""
    SELECT
      publication_number,
      application_number,
      grant_date,
      title_localized
    FROM `patents-public-data.patents.publications`
    WHERE country_code = 'US'
      AND kind_code IN ('B1', 'B2')
      AND grant_date >= {year}0101
      AND grant_date <= {year}1231
    """

    # Run bq query and capture CSV output
    result = subprocess.run(
        ["bq", "query", f"--project_id={PROJECT}", "--format=json",
         "--nouse_legacy_sql", "--max_rows=500000", query],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode != 0:
        print(f"  ERROR querying year {year}: {result.stderr}", file=sys.stderr)
        return ""

    rows = json.loads(result.stdout)
    print(f"  Got {len(rows)} rows")

    # Transform and write JSONL.gz
    output_path = f"/tmp/fw_{year}.jsonl.gz"
    count = 0
    with gzip.open(output_path, "wt", encoding="utf-8") as f:
        for row in rows:
            patent_number = extract_patent_number(row.get("publication_number", ""))
            application_number = extract_app_number(row.get("application_number", ""))

            titles = row.get("title_localized", [])
            invention_title = ""
            if titles:
                for t in titles:
                    if t.get("language") == "en":
                        invention_title = t.get("text", "")
                        break
                if not invention_title and titles:
                    invention_title = titles[0].get("text", "")

            grant_date = format_date(int(row.get("grant_date", 0)))

            if not patent_number and not application_number:
                continue

            record = {
                "patent_number": patent_number or None,
                "application_number": application_number or None,
                "invention_title": invention_title,
                "grant_date": grant_date,
                "applicants": [],
            }
            f.write(json.dumps(record) + "\n")
            count += 1

    print(f"  Wrote {count} records to {output_path}")
    return output_path


def load_to_bq(jsonl_path: str) -> bool:
    """Load JSONL.gz file into BigQuery."""
    result = subprocess.run(
        ["bq", "load", f"--project_id={PROJECT}",
         "--source_format=NEWLINE_DELIMITED_JSON",
         TABLE, jsonl_path],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"  BQ load error: {result.stderr}", file=sys.stderr)
        return False
    return True


def main():
    total = 0
    for year in range(MIN_YEAR, MAX_YEAR + 1):
        print(f"\n=== Year {year} ===")
        jsonl_path = export_year(year)
        if not jsonl_path:
            continue

        print(f"  Loading into BigQuery...")
        if load_to_bq(jsonl_path):
            # Count records loaded
            with gzip.open(jsonl_path, "rt") as f:
                batch = sum(1 for _ in f)
            total += batch
            print(f"  Loaded. Running total: {total:,}")
        else:
            print(f"  FAILED to load year {year}")

        os.remove(jsonl_path)

    print(f"\n=== COMPLETE: {total:,} total records loaded ===")


if __name__ == "__main__":
    main()
