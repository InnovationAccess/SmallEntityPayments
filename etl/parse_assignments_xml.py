#!/usr/bin/env python3
"""Parse USPTO Patent Assignment XML (PADX v2.0) into JSONL.

Streams large XML files using iterparse, extracting assignment records
with recorded_date >= min_year. Outputs JSONL with nested assignee arrays.

Usage:
    python parse_assignments_xml.py <input.xml> <output.jsonl> [min_year]
    python parse_assignments_xml.py <input.xml> - [min_year]  # stdout mode
"""

import json
import sys
import gzip
from pathlib import Path
from xml.etree.ElementTree import iterparse


def parse_date(raw: str) -> str:
    """Convert YYYYMMDD to YYYY-MM-DD."""
    raw = (raw or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def extract_text(elem, path: str) -> str:
    """Get text from an XML element at the given path."""
    child = elem.find(path)
    return (child.text or "").strip() if child is not None else ""


def parse_assignment(elem, min_year: int):
    """Parse a single <patent-assignment> element.

    Returns a list of dicts (one per patent-property), or empty list if filtered out.
    """
    # Get recorded date
    recorded_date_raw = extract_text(elem, ".//assignment-record/recorded-date/date")
    recorded_date = parse_date(recorded_date_raw)
    if not recorded_date:
        return []

    try:
        year = int(recorded_date[:4])
    except ValueError:
        return []
    if year < min_year:
        return []

    # Extract assignees
    assignees = []
    for ae in elem.findall(".//patent-assignees/patent-assignee"):
        name = extract_text(ae, "name")
        if not name:
            continue
        assignees.append({
            "name": name,
            "street_address": extract_text(ae, "address-1"),
            "city": extract_text(ae, "city"),
            "state": extract_text(ae, "state"),
            "country": extract_text(ae, "country-name"),
        })

    if not assignees:
        return []

    # Extract patent properties (each property = one patent/application)
    results = []
    for prop in elem.findall(".//patent-properties/patent-property"):
        patent_number = ""
        application_number = ""

        for doc_id in prop.findall("document-id"):
            kind = extract_text(doc_id, "kind")
            doc_num = extract_text(doc_id, "doc-number")
            if not doc_num:
                continue
            if kind in ("X0", ""):
                # Application number
                application_number = doc_num
            elif kind.startswith("B") or kind.startswith("A"):
                # Patent number (grant or pub)
                patent_number = doc_num

        if not patent_number and not application_number:
            continue

        results.append({
            "patent_number": patent_number or None,
            "application_number": application_number or None,
            "recorded_date": recorded_date,
            "assignees": assignees,
        })

    return results


def parse_file(input_path: str, output_path: str, min_year: int = 2016):
    """Parse PADX XML file to JSONL."""
    count = 0
    assignments_seen = 0
    use_stdout = (output_path == "-")

    if use_stdout:
        fout = sys.stdout
    elif output_path.endswith(".gz"):
        fout = gzip.open(output_path, "wt", encoding="utf-8")
    else:
        fout = open(output_path, "w")

    try:
        context = iterparse(input_path, events=("end",))
        for event, elem in context:
            if elem.tag == "patent-assignment":
                assignments_seen += 1
                records = parse_assignment(elem, min_year)
                for rec in records:
                    fout.write(json.dumps(rec) + "\n")
                    count += 1
                # Free memory
                elem.clear()
    except Exception as e:
        print(f"Error parsing {input_path}: {e}", file=sys.stderr)
    finally:
        if not use_stdout:
            fout.close()

    print(f"Parsed {input_path}: {count} records from {assignments_seen} assignments",
          file=sys.stderr)
    return count


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.xml> <output.jsonl> [min_year]")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]
    min_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2016

    if not Path(input_file).exists():
        print(f"Error: {input_file} not found")
        sys.exit(1)

    parse_file(input_file, output_file, min_year)
