#!/usr/bin/env python3
"""Parse USPTO PTFWPRE (Patent File Wrapper) JSON into JSONL for BigQuery.

Streams large JSON files from ZIP archives using ijson, extracting patent
application records. Outputs JSONL matching the patent_file_wrapper schema.

Usage:
    python parse_file_wrapper.py <input.zip> <output_dir> [min_year]
    python parse_file_wrapper.py <input.zip> -            [min_year]  # stdout

The ZIP contains one JSON file per year (e.g. 2026.json, 2025.json).
Each JSON file has structure:
    {
      "count": N,
      "patentFileWrapperDataBag": [ ...records... ]
    }

Each record maps to one patent_file_wrapper row.
"""

import gzip
import json
import os
import sys
import zipfile
from pathlib import Path

import ijson


# Map PTFWPRE entity status to our schema values
ENTITY_STATUS_MAP = {
    "small": "SMALL",
    "micro": "MICRO",
    "regular undiscounted": "LARGE",
}


def map_entity_status(raw: str) -> str:
    """Convert PTFWPRE businessEntityStatusCategory to SMALL/MICRO/LARGE."""
    return ENTITY_STATUS_MAP.get((raw or "").strip().lower(), "UNKNOWN")


def extract_applicant(app: dict) -> dict:
    """Extract applicant fields from a PTFWPRE applicantBag item."""
    name = (app.get("applicantNameText") or "").strip()
    if not name:
        return None

    # Get address from first correspondenceAddressBag entry
    city = ""
    state = ""
    country = ""
    street_address = ""
    addrs = app.get("correspondenceAddressBag") or []
    if addrs:
        addr = addrs[0]
        city = (addr.get("cityName") or "").strip()
        state = (addr.get("geographicRegionCode") or "").strip()
        country = (addr.get("countryCode") or "").strip()
        # Street address from addressLineOneText if present
        street_address = (addr.get("addressLineOneText") or "").strip()

    return {
        "name": name,
        "street_address": street_address or None,
        "city": city or None,
        "state": state or None,
        "country": country or None,
        "entity_type": None,  # Set at record level, not per-applicant
    }


def parse_record(record: dict) -> dict:
    """Parse a single PTFWPRE record into a patent_file_wrapper row."""
    meta = record.get("applicationMetaData") or {}

    application_number = (record.get("applicationNumberText") or "").strip() or None
    patent_number = (meta.get("patentNumber") or "").strip() or None
    invention_title = (meta.get("inventionTitle") or "").strip() or None
    grant_date = (meta.get("grantDate") or "").strip() or None

    # Entity status (applies to all applicants on this filing)
    entity_data = meta.get("entityStatusData") or {}
    entity_type = map_entity_status(
        entity_data.get("businessEntityStatusCategory", "")
    )

    # Extract applicants
    applicants = []
    for app in meta.get("applicantBag") or []:
        parsed = extract_applicant(app)
        if parsed:
            parsed["entity_type"] = entity_type
            applicants.append(parsed)

    return {
        "patent_number": patent_number,
        "application_number": application_number,
        "invention_title": invention_title,
        "grant_date": grant_date,
        "applicants": applicants,
    }


def process_year_file(zf: zipfile.ZipFile, filename: str, fout, min_year: int):
    """Process a single year JSON file from the ZIP, streaming with ijson."""
    # Extract year from filename (e.g. "2025.json" -> 2025)
    # Special files like "no_filing_date.json" are always processed
    stem = Path(filename).stem
    try:
        year = int(stem)
    except ValueError:
        year = None

    if year is not None and year < min_year:
        print(f"  Skipping {filename}: year {year} < min_year {min_year}",
              file=sys.stderr)
        return 0

    label = f"year {year}" if year else stem
    print(f"  Processing {filename} ({label})...", file=sys.stderr)
    count = 0

    with zf.open(filename) as f:
        # Stream records using ijson
        records = ijson.items(f, "patentFileWrapperDataBag.item")
        for record in records:
            row = parse_record(record)
            if row["application_number"]:  # Must have at least an app number
                line = json.dumps(row, ensure_ascii=False) + "\n"
                fout.write(line)
                count += 1

                if count % 100000 == 0:
                    print(f"    {count:,} records...", file=sys.stderr)

    print(f"  {filename}: {count:,} records", file=sys.stderr)
    return count


def parse_zip(zip_path: str, output_path: str, min_year: int = 2001):
    """Parse a PTFWPRE ZIP file into JSONL output."""
    zf = zipfile.ZipFile(zip_path)

    # Get year files sorted (newest first for progress visibility)
    year_files = sorted(
        [n for n in zf.namelist() if n.endswith(".json")],
        reverse=True,
    )
    print(f"ZIP contains {len(year_files)} files: {year_files}", file=sys.stderr)

    use_stdout = (output_path == "-")
    total = 0

    if use_stdout:
        for filename in year_files:
            total += process_year_file(zf, filename, sys.stdout, min_year)
    else:
        # Write gzipped JSONL
        if not output_path.endswith(".gz"):
            output_path += ".gz"
        with gzip.open(output_path, "wt", encoding="utf-8") as fout:
            for filename in year_files:
                total += process_year_file(zf, filename, fout, min_year)

    print(f"\nTotal: {total:,} records written to {output_path}", file=sys.stderr)
    return total


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.zip> <output.jsonl[.gz]> [min_year]")
        print(f"       {sys.argv[0]} <input.zip> -  [min_year]  # stdout")
        sys.exit(1)

    input_zip = sys.argv[1]
    output = sys.argv[2]
    min_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2001

    if not Path(input_zip).exists():
        print(f"Error: {input_zip} not found")
        sys.exit(1)

    parse_zip(input_zip, output, min_year)
