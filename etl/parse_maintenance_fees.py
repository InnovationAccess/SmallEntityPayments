#!/usr/bin/env python3
"""Parse USPTO Maintenance Fee Events fixed-width file into CSV.

File format (59 chars per record):
  Positions 1-13:  Patent Number (13-char, right-justified, leading zeros)
  Position  14:    Space delimiter
  Positions 15-22: Application Number (2-digit series + 6-digit serial)
  Position  23:    Space delimiter
  Position  24:    Entity indicator (Y=Small, M=Micro, N=Large, U/blank=Unknown)
  Position  25:    Space delimiter
  Positions 26-33: Filing Date (YYYYMMDD)
  Position  34:    Space delimiter
  Positions 35-42: Grant Date (YYYYMMDD)
  Position  43:    Space delimiter
  Positions 44-51: Event Date (YYYYMMDD)
  Position  52:    Space delimiter
  Positions 53-57: Event Code (5-char)
  Positions 58-59: CRLF
"""

import csv
import sys
from pathlib import Path


def parse_date(raw: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD, or empty string if invalid."""
    raw = raw.strip()
    if len(raw) != 8 or not raw.isdigit():
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def derive_entity_status(indicator: str, event_code: str) -> str:
    """Derive entity status from indicator char and event code."""
    # Primary: use the indicator field
    if indicator == "Y":
        return "SMALL"
    if indicator == "M":
        return "MICRO"
    if indicator == "N":
        return "LARGE"
    # Fallback: derive from event code prefix
    if event_code.startswith("M1") or event_code.startswith("F17"):
        return "LARGE"
    if event_code.startswith("M2") or event_code.startswith("F27"):
        return "SMALL"
    if event_code.startswith("M3"):
        return "MICRO"
    return ""


def derive_fee_code(event_code: str) -> str:
    """Derive fee period from event code suffix."""
    code = event_code.strip()
    if len(code) < 4:
        return ""
    suffix = code[-2:]  # last 2 chars
    if suffix == "51":
        return "3.5_YEAR"
    if suffix == "52":
        return "7.5_YEAR"
    if suffix == "53":
        return "11.5_YEAR"
    return ""


def clean_patent_number(raw: str) -> str:
    """Strip leading zeros from patent number."""
    stripped = raw.strip().lstrip("0")
    return stripped if stripped else ""


def parse_file(input_path: str, output_path: str, min_year: int = 2016):
    """Parse fixed-width maintenance fee file to CSV.

    If output_path is "-", writes to stdout (no header) for piping to bq load.
    """
    count = 0
    skipped = 0
    use_stdout = (output_path == "-")

    with open(input_path, "r", encoding="ascii", errors="replace") as fin:
        if use_stdout:
            fout = sys.stdout
        else:
            fout = open(output_path, "w", newline="")

        try:
            writer = csv.writer(fout)
            if not use_stdout:
                writer.writerow([
                    "patent_number", "application_number", "event_code",
                    "event_date", "fee_code", "entity_status",
                ])

            for line in fin:
                if len(line.rstrip("\r\n")) < 57:
                    skipped += 1
                    continue

                patent_number_raw = line[0:13]
                app_number_raw = line[14:22]
                entity_indicator = line[23:24]
                event_date_raw = line[43:51]
                event_code_raw = line[52:57]

                event_date = parse_date(event_date_raw)
                if not event_date:
                    skipped += 1
                    continue

                try:
                    year = int(event_date[:4])
                except ValueError:
                    skipped += 1
                    continue
                if year < min_year:
                    skipped += 1
                    continue

                patent_number = clean_patent_number(patent_number_raw)
                app_number = app_number_raw.strip()
                event_code = event_code_raw.strip()
                entity_status = derive_entity_status(entity_indicator, event_code)
                fee_code = derive_fee_code(event_code)

                writer.writerow([
                    patent_number, app_number, event_code,
                    event_date, fee_code, entity_status,
                ])
                count += 1
        finally:
            if not use_stdout:
                fout.close()

    print(f"Parsed {count:,} rows (skipped {skipped:,})", file=sys.stderr)
    return count


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.txt> <output.csv> [min_year]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    min_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2016

    if not Path(input_file).exists():
        print(f"Error: {input_file} not found")
        sys.exit(1)

    parse_file(input_file, output_file, min_year)
