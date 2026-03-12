#!/usr/bin/env python3
"""Parse USPTO Patent Assignment XML (PASYR/PASDL) into normalized JSONL for BigQuery.

v4 — normalized output: instead of a single cross-product flat file, outputs
4 separate JSONL.gz files matching the 4 normalized BigQuery tables:

  pat_assign_records    — one row per assignment transaction
  pat_assign_assignors  — one row per assignor per assignment
  pat_assign_assignees  — one row per assignee per assignment
  pat_assign_documents  — one row per patent property per assignment

All tables are linked by reel_frame.

Reuses all XML parsing logic from v3 (parse_date, extract_text,
_classify_doc_id, assignor/assignee/document extraction).

Usage:
    python parse_assignments_xml_v4.py <input.zip_or_xml> <output_dir> [min_year]

Input can be:
  - A ZIP file containing one or more XML files
  - A bare XML file

Output: 4 gzipped JSONL files in output_dir:
  records_<basename>.jsonl.gz
  assignors_<basename>.jsonl.gz
  assignees_<basename>.jsonl.gz
  documents_<basename>.jsonl.gz
"""

import gzip
import json
import os
import sys
import zipfile
from pathlib import Path
from xml.etree.ElementTree import iterparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.patent_number import normalize_patent_number
from utils.conveyance_classifier import classify_conveyance

# Reuse v3 helpers directly
from etl.parse_assignments_xml_v3 import parse_date, extract_text, _classify_doc_id


def parse_assignment(elem, source_file: str, min_year: int) -> dict | None:
    """Parse a single <patent-assignment> element into normalized dicts.

    Returns a dict with keys 'record', 'assignors', 'assignees', 'documents',
    or None if filtered out.
    """
    # --- Assignment record fields ---
    rec = elem.find("assignment-record")
    if rec is None:
        return None

    reel_no_str = extract_text(rec, "reel-no")
    frame_no_str = extract_text(rec, "frame-no")
    try:
        reel_no = int(reel_no_str) if reel_no_str else None
    except ValueError:
        reel_no = None
    try:
        frame_no = int(frame_no_str) if frame_no_str else None
    except ValueError:
        frame_no = None

    if reel_no is not None and frame_no is not None:
        reel_frame = f"{reel_no}/{frame_no}"
    else:
        return None  # reel_frame is NOT NULL in schema

    recorded_date = parse_date(extract_text(rec, "recorded-date/date"))
    if not recorded_date:
        return None

    # Year filter
    try:
        year = int(recorded_date[:4])
    except ValueError:
        return None
    if year < min_year:
        return None

    last_update_date = parse_date(extract_text(rec, "last-update-date/date"))
    page_count_str = extract_text(rec, "page-count")
    try:
        page_count = int(page_count_str) if page_count_str else None
    except ValueError:
        page_count = None

    # Correspondent (law firm)
    correspondent_name = extract_text(rec, "correspondent/name") or None
    correspondent_detail = extract_text(rec, "correspondent/address-1") or None
    correspondent_address_1 = extract_text(rec, "correspondent/address-2") or None
    correspondent_address_2 = extract_text(rec, "correspondent/address-3") or None
    correspondent_address_3 = extract_text(rec, "correspondent/address-4") or None

    # Conveyance
    conveyance_text = extract_text(rec, "conveyance-text") or None
    conveyance_type = classify_conveyance(conveyance_text)

    record = {
        "reel_frame": reel_frame,
        "reel_no": reel_no,
        "frame_no": frame_no,
        "recorded_date": recorded_date,
        "last_update_date": last_update_date,
        "page_count": page_count,
        "conveyance_text": conveyance_text,
        "conveyance_type": conveyance_type,
        "employer_assignment": None,
        "correspondent_name": correspondent_name,
        "correspondent_detail": correspondent_detail,
        "correspondent_address_1": correspondent_address_1,
        "correspondent_address_2": correspondent_address_2,
        "correspondent_address_3": correspondent_address_3,
        "source_file": source_file,
    }

    # --- Assignors ---
    assignors = []
    for a in elem.findall(".//patent-assignors/patent-assignor"):
        name = extract_text(a, "name")
        if not name:
            continue
        exec_date = parse_date(extract_text(a, "execution-date/date"))
        assignors.append({
            "reel_frame": reel_frame,
            "assignor_name": name,
            "assignor_execution_date": exec_date,
        })
    # If no assignors, still emit a row (some records lack assignor info)
    if not assignors:
        assignors = [{
            "reel_frame": reel_frame,
            "assignor_name": None,
            "assignor_execution_date": None,
        }]

    # --- Assignees (capture ALL) ---
    assignees = []
    for ae in elem.findall(".//patent-assignees/patent-assignee"):
        n = extract_text(ae, "name")
        if n:
            assignees.append({
                "reel_frame": reel_frame,
                "assignee_name": n,
                "assignee_address_1": extract_text(ae, "address-1") or None,
                "assignee_address_2": extract_text(ae, "address-2") or None,
                "assignee_city": extract_text(ae, "city") or None,
                "assignee_state": extract_text(ae, "state") or None,
                "assignee_postcode": extract_text(ae, "postcode") or None,
                "assignee_country": extract_text(ae, "country-name") or None,
            })
    if not assignees:
        assignees = [{
            "reel_frame": reel_frame,
            "assignee_name": None,
            "assignee_address_1": None,
            "assignee_address_2": None,
            "assignee_city": None,
            "assignee_state": None,
            "assignee_postcode": None,
            "assignee_country": None,
        }]

    # --- Patent properties (documents covered by this assignment) ---
    documents = []
    for prop in elem.findall(".//patent-properties/patent-property"):
        inv_title = extract_text(prop, "invention-title") or None

        application_number = None
        filing_date = None
        publication_number = None
        publication_date = None
        patent_number = None
        grant_date = None

        for doc_id in prop.findall("document-id"):
            doc_country = extract_text(doc_id, "country") or None
            doc_number_raw = extract_text(doc_id, "doc-number")
            doc_kind = extract_text(doc_id, "kind") or ""
            doc_date_raw = extract_text(doc_id, "date")

            if not doc_number_raw:
                continue

            # Skip non-US documents
            if doc_country and doc_country not in ("US", ""):
                continue

            doc_number = normalize_patent_number(doc_number_raw) or doc_number_raw
            doc_date = parse_date(doc_date_raw)

            doc_type = _classify_doc_id(doc_kind, doc_number_raw)

            if doc_type == "application":
                application_number = doc_number
                filing_date = doc_date
            elif doc_type == "publication":
                publication_number = doc_number
                publication_date = doc_date
            elif doc_type == "grant":
                patent_number = doc_number
                grant_date = doc_date

        # Skip patent-properties with no document IDs at all
        if not application_number and not publication_number and not patent_number:
            continue

        documents.append({
            "reel_frame": reel_frame,
            "application_number": application_number,
            "filing_date": filing_date,
            "publication_number": publication_number,
            "publication_date": publication_date,
            "patent_number": patent_number,
            "grant_date": grant_date,
            "invention_title": inv_title,
        })

    if not documents:
        return None

    return {
        "record": record,
        "assignors": assignors,
        "assignees": assignees,
        "documents": documents,
    }


def parse_xml_stream(stream, source_file: str, writers: dict, min_year: int) -> dict:
    """Parse an XML stream, writing to 4 JSONL writers. Returns counts per table."""
    counts = {"records": 0, "assignors": 0, "assignees": 0, "documents": 0}
    skipped = 0
    assignments_seen = 0

    try:
        context = iterparse(stream, events=("end",))
        for event, elem in context:
            if elem.tag == "patent-assignment":
                assignments_seen += 1
                result = parse_assignment(elem, source_file, min_year)
                if result:
                    writers["records"].write(json.dumps(result["record"], ensure_ascii=False) + "\n")
                    counts["records"] += 1

                    for assignor in result["assignors"]:
                        writers["assignors"].write(json.dumps(assignor, ensure_ascii=False) + "\n")
                        counts["assignors"] += 1

                    for assignee in result["assignees"]:
                        writers["assignees"].write(json.dumps(assignee, ensure_ascii=False) + "\n")
                        counts["assignees"] += 1

                    for doc in result["documents"]:
                        writers["documents"].write(json.dumps(doc, ensure_ascii=False) + "\n")
                        counts["documents"] += 1
                else:
                    skipped += 1

                # Free memory
                elem.clear()

                if assignments_seen % 100000 == 0:
                    print(f"  {source_file}: {assignments_seen:,} assignments, "
                          f"{counts['records']:,} records...", file=sys.stderr)
    except Exception as e:
        print(f"Error parsing {source_file}: {e}", file=sys.stderr)

    print(f"  {source_file}: {counts['records']:,} records, "
          f"{counts['assignors']:,} assignors, "
          f"{counts['assignees']:,} assignees, "
          f"{counts['documents']:,} documents "
          f"from {assignments_seen:,} assignments "
          f"({skipped:,} skipped)", file=sys.stderr)
    return counts


def parse_input(input_path: str, output_dir: str, min_year: int = 2006) -> dict:
    """Parse a ZIP or XML file into 4 gzipped JSONL files.

    Returns dict of counts: {"records": N, "assignors": N, "assignees": N, "documents": N}
    """
    os.makedirs(output_dir, exist_ok=True)
    basename = Path(input_path).stem

    paths = {
        "records": os.path.join(output_dir, f"records_{basename}.jsonl.gz"),
        "assignors": os.path.join(output_dir, f"assignors_{basename}.jsonl.gz"),
        "assignees": os.path.join(output_dir, f"assignees_{basename}.jsonl.gz"),
        "documents": os.path.join(output_dir, f"documents_{basename}.jsonl.gz"),
    }

    total_counts = {"records": 0, "assignors": 0, "assignees": 0, "documents": 0}

    # Open all 4 writers simultaneously
    writers = {}
    file_handles = []
    try:
        for key, path in paths.items():
            fh = gzip.open(path, "wt", encoding="utf-8")
            file_handles.append(fh)
            writers[key] = fh

        if input_path.endswith(".zip"):
            zf = zipfile.ZipFile(input_path)
            xml_files = sorted([n for n in zf.namelist() if n.endswith(".xml")])
            print(f"ZIP contains {len(xml_files)} XML files", file=sys.stderr)

            for xml_file in xml_files:
                print(f"\nProcessing {xml_file}...", file=sys.stderr)
                with zf.open(xml_file) as f:
                    counts = parse_xml_stream(f, xml_file, writers, min_year)
                    for key in total_counts:
                        total_counts[key] += counts[key]
        else:
            # Bare XML file
            print(f"Processing {input_path}...", file=sys.stderr)
            counts = parse_xml_stream(input_path, Path(input_path).name, writers, min_year)
            for key in total_counts:
                total_counts[key] += counts[key]
    finally:
        for fh in file_handles:
            fh.close()

    print(f"\nTotal: records={total_counts['records']:,}, "
          f"assignors={total_counts['assignors']:,}, "
          f"assignees={total_counts['assignees']:,}, "
          f"documents={total_counts['documents']:,}",
          file=sys.stderr)
    print(f"Output directory: {output_dir}", file=sys.stderr)
    return total_counts


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.zip_or_xml> <output_dir> [min_year]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_dir = sys.argv[2]
    min_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2006

    if not Path(input_path).exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    parse_input(input_path, output_dir, min_year)
