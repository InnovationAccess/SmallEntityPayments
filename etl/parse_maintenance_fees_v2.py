#!/usr/bin/env python3
"""Parse USPTO PTMNFEE2 fixed-width maintenance fee events into JSONL for BigQuery.

Parses the fixed-width ASCII format (59 chars per record) from PTMNFEE2 ZIP files.

Usage:
    python parse_maintenance_fees_v2.py <input.zip> <output.jsonl.gz>

Field layout (per official PTMNFEE2 documentation, April 2025):
  Position 1-13:  Patent Number (alphanumeric, right-justified, leading zeros)
  Position 14:    Delimiter (space)
  Position 15-22: Application Number (2-digit series + 6-digit number)
  Position 23:    Delimiter (space)
  Position 24:    Small Entity Status (Y=small, M=micro, N=large, blank=unknown)
  Position 25:    Delimiter (space)
  Position 26-33: Application Filing Date (yyyymmdd)
  Position 34:    Delimiter (space)
  Position 35-42: Grant Issue Date (yyyymmdd)
  Position 43:    Delimiter (space)
  Position 44-51: Event Entry Date (yyyymmdd)
  Position 52:    Delimiter (space)
  Position 53-57: Event Code (alphanumeric with trailing spaces)
  Position 58-59: Line terminator (CR/LF)
"""

import gzip
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.patent_number import normalize_patent_number

# Entity status mapping
ENTITY_MAP = {
    "Y": "Small",
    "M": "Micro",
    "N": "Large",
}


def parse_date(s: str) -> str | None:
    """Convert yyyymmdd to yyyy-mm-dd, or None if invalid."""
    s = s.strip()
    if not s or len(s) != 8 or not s.isdigit():
        return None
    year, month, day = s[:4], s[4:6], s[6:8]
    # Reject obviously invalid dates (0000-00-00, month=00, day=00)
    if year == "0000" or month == "00" or day == "00":
        return None
    return f"{year}-{month}-{day}"


def parse_line(line: bytes, source_file: str) -> dict | None:
    """Parse a single fixed-width record into a dict."""
    try:
        text = line.decode("ascii", errors="replace")
    except Exception:
        return None

    if len(text) < 57:
        return None

    raw_patent = text[0:13].strip()
    app_number = text[14:22].strip()
    entity_char = text[23:24].strip()
    filing_date_raw = text[25:33].strip()
    grant_date_raw = text[34:42].strip()
    event_date_raw = text[43:51].strip()
    event_code = text[52:57].strip()

    patent_number = normalize_patent_number(raw_patent)
    if not patent_number:
        return None

    entity_status = ENTITY_MAP.get(entity_char, "Unknown")

    return {
        "patent_number": patent_number,
        "application_number": app_number or None,
        "entity_status": entity_status,
        "filing_date": parse_date(filing_date_raw),
        "grant_date": parse_date(grant_date_raw),
        "event_date": parse_date(event_date_raw),
        "event_code": event_code or None,
        "source_file": source_file,
    }


def parse_zip(zip_path: str, output_path: str):
    """Parse a PTMNFEE2 ZIP into gzipped JSONL."""
    zf = zipfile.ZipFile(zip_path)

    # Find the main data file (MaintFeeEvents_*.txt, not the Desc file)
    data_files = [n for n in zf.namelist()
                  if n.startswith("MaintFeeEvents") and "Desc" not in n and n.endswith(".txt")]
    if not data_files:
        print(f"Error: No MaintFeeEvents data file found in {zip_path}", file=sys.stderr)
        sys.exit(1)

    data_file = data_files[0]
    print(f"Parsing {data_file} from {zip_path}...", file=sys.stderr)

    if not output_path.endswith(".gz"):
        output_path += ".gz"

    count = 0
    skipped = 0

    with zf.open(data_file) as fin, \
         gzip.open(output_path, "wt", encoding="utf-8") as fout:

        for line in fin:
            row = parse_line(line, data_file)
            if row:
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
                if count % 1000000 == 0:
                    print(f"  {count:,} records...", file=sys.stderr)
            else:
                skipped += 1

    print(f"\nTotal: {count:,} records written, {skipped:,} skipped", file=sys.stderr)
    print(f"Output: {output_path}", file=sys.stderr)
    return count, skipped


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.zip> <output.jsonl.gz>")
        sys.exit(1)

    zip_path = sys.argv[1]
    output = sys.argv[2]

    if not Path(zip_path).exists():
        print(f"Error: {zip_path} not found")
        sys.exit(1)

    parse_zip(zip_path, output)
