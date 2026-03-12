#!/usr/bin/env python3
"""Parse USPTO Patent Assignment XML (PASYR/PASDL) into denormalized JSONL for BigQuery.

v3 — fixes the doc_number ambiguity from v2 by splitting into three separate fields:
  application_number  (kind=X0 or empty)
  publication_number  (kind=A1 with 10-digit num, A2, P1, P4)
  patent_number       (kind=B1, B2, S1, P2, P3, or A1 with 7-digit num pre-2001)

Also adds:
  - All assignees (v2 only captured first)
  - Assignee address fields (address_1, address_2)
  - Correspondent address fields
  - Conveyance type classification
  - Filing date, publication date, grant date per document
  - employer_assignment column (NULL for now — to be populated from UPAD later)

Denormalization: one assignment with 3 assignors × 2 assignees × 2 patent-properties
= 12 output rows.

Usage:
    python parse_assignments_xml_v3.py <input.zip_or_xml> <output.jsonl.gz> [min_year]

Input can be:
  - A ZIP file containing one or more XML files
  - A bare XML file

Output schema matches patent_assignments_v3 BigQuery table.
"""

import gzip
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import iterparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.patent_number import normalize_patent_number
from utils.conveyance_classifier import classify_conveyance


def parse_date(raw: str) -> str | None:
    """Convert YYYYMMDD to YYYY-MM-DD, or None if invalid.

    BigQuery DATE supports 0001-01-01 to 9999-12-31, but we reject
    years < 1700 or > 2100 as clearly erroneous data (e.g. 0000-01-01).
    """
    raw = (raw or "").strip()
    if len(raw) != 8 or not raw.isdigit():
        return None
    year = int(raw[:4])
    if year < 1700 or year > 2100:
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def extract_text(elem, path: str) -> str:
    """Get text from an XML element at the given path."""
    child = elem.find(path)
    return (child.text or "").strip() if child is not None else ""


def _classify_doc_id(kind: str, doc_number: str) -> str:
    """Determine whether a document-id is application, publication, or grant.

    Returns one of: 'application', 'publication', 'grant'.

    Kind code reference (USPTO PADX v2):
      X0        — Application number
      B1, B2    — Utility patent grant
      S1        — Design patent grant
      P2, P3    — Plant patent grant
      A1        — AMBIGUOUS: pre-2001 utility grant OR post-2001 publication
      A2        — Second publication of application
      P1        — Pre-2001 plant grant OR post-2001 plant application pub
      P4        — Second plant application publication

    For A1 ambiguity: 7-digit alphanumeric = grant, 10+ digit = publication.
    """
    if kind in ("X0", ""):
        return "application"
    if kind.startswith("B") or kind == "S1" or kind in ("P2", "P3"):
        return "grant"
    if kind == "A2" or kind == "P4":
        return "publication"
    if kind == "A1" or kind == "P1":
        # Disambiguate by doc number length:
        # Patent numbers are typically 7 digits; publication numbers are 10+
        clean = doc_number.strip().replace(",", "").replace(" ", "")
        if len(clean) >= 10:
            return "publication"
        return "grant"
    # Unknown kind — try to classify by number format
    clean = doc_number.strip().replace(",", "").replace(" ", "")
    if len(clean) >= 10:
        return "publication"
    if clean.isdigit() and len(clean) <= 8:
        return "application"
    return "grant"


def parse_assignment(elem, source_file: str, min_year: int) -> list[dict]:
    """Parse a single <patent-assignment> element into denormalized rows.

    Returns a list of dicts (one per assignor × assignee × patent-property),
    or empty list if filtered out.
    """
    # --- Assignment record fields ---
    rec = elem.find("assignment-record")
    if rec is None:
        return []

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
        return []  # reel_frame is NOT NULL in schema

    recorded_date = parse_date(extract_text(rec, "recorded-date/date"))
    if not recorded_date:
        return []

    # Year filter
    try:
        year = int(recorded_date[:4])
    except ValueError:
        return []
    if year < min_year:
        return []

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

    # --- Assignors ---
    assignors = []
    for a in elem.findall(".//patent-assignors/patent-assignor"):
        name = extract_text(a, "name")
        if not name:
            continue
        exec_date = parse_date(extract_text(a, "execution-date/date"))
        assignors.append({
            "name": name,
            "execution_date": exec_date,
        })
    # If no assignors, still emit rows (some records lack assignor info)
    if not assignors:
        assignors = [{"name": None, "execution_date": None}]

    # --- Assignees (capture ALL, not just first) ---
    assignees = []
    for ae in elem.findall(".//patent-assignees/patent-assignee"):
        n = extract_text(ae, "name")
        if n:
            assignees.append({
                "name": n,
                "address_1": extract_text(ae, "address-1") or None,
                "address_2": extract_text(ae, "address-2") or None,
                "city": extract_text(ae, "city") or None,
                "state": extract_text(ae, "state") or None,
                "postcode": extract_text(ae, "postcode") or None,
                "country": extract_text(ae, "country-name") or None,
            })
    if not assignees:
        assignees = [{
            "name": None, "address_1": None, "address_2": None,
            "city": None, "state": None, "postcode": None, "country": None,
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
            "application_number": application_number,
            "filing_date": filing_date,
            "publication_number": publication_number,
            "publication_date": publication_date,
            "patent_number": patent_number,
            "grant_date": grant_date,
            "invention_title": inv_title,
        })

    if not documents:
        return []

    # --- Cross-product: assignors × assignees × documents ---
    rows = []
    for assignor in assignors:
        for assignee in assignees:
            for doc in documents:
                rows.append({
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
                    "assignor_name": assignor["name"],
                    "assignor_execution_date": assignor["execution_date"],
                    "assignee_name": assignee["name"],
                    "assignee_address_1": assignee["address_1"],
                    "assignee_address_2": assignee["address_2"],
                    "assignee_city": assignee["city"],
                    "assignee_state": assignee["state"],
                    "assignee_postcode": assignee["postcode"],
                    "assignee_country": assignee["country"],
                    "application_number": doc["application_number"],
                    "filing_date": doc["filing_date"],
                    "publication_number": doc["publication_number"],
                    "publication_date": doc["publication_date"],
                    "patent_number": doc["patent_number"],
                    "grant_date": doc["grant_date"],
                    "invention_title": doc["invention_title"],
                    "source_file": source_file,
                })

    return rows


def parse_xml_stream(stream, source_file: str, fout, min_year: int) -> tuple[int, int]:
    """Parse an XML stream, writing JSONL to fout. Returns (count, skipped)."""
    count = 0
    skipped = 0
    assignments_seen = 0

    try:
        context = iterparse(stream, events=("end",))
        for event, elem in context:
            if elem.tag == "patent-assignment":
                assignments_seen += 1
                rows = parse_assignment(elem, source_file, min_year)
                if rows:
                    for row in rows:
                        fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                        count += 1
                else:
                    skipped += 1

                # Free memory
                elem.clear()

                if assignments_seen % 100000 == 0:
                    print(f"  {source_file}: {assignments_seen:,} assignments, "
                          f"{count:,} rows...", file=sys.stderr)
    except Exception as e:
        print(f"Error parsing {source_file}: {e}", file=sys.stderr)

    print(f"  {source_file}: {count:,} rows from {assignments_seen:,} assignments "
          f"({skipped:,} skipped)", file=sys.stderr)
    return count, skipped


def parse_input(input_path: str, output_path: str, min_year: int = 1980):
    """Parse a ZIP or XML file into gzipped JSONL."""
    if not output_path.endswith(".gz"):
        output_path += ".gz"

    total_count = 0
    total_skipped = 0

    with gzip.open(output_path, "wt", encoding="utf-8") as fout:
        if input_path.endswith(".zip"):
            zf = zipfile.ZipFile(input_path)
            xml_files = sorted([n for n in zf.namelist() if n.endswith(".xml")])
            print(f"ZIP contains {len(xml_files)} XML files", file=sys.stderr)

            for xml_file in xml_files:
                print(f"\nProcessing {xml_file}...", file=sys.stderr)
                with zf.open(xml_file) as f:
                    c, s = parse_xml_stream(f, xml_file, fout, min_year)
                    total_count += c
                    total_skipped += s
        else:
            # Bare XML file
            print(f"Processing {input_path}...", file=sys.stderr)
            c, s = parse_xml_stream(input_path, Path(input_path).name, fout, min_year)
            total_count += c
            total_skipped += s

    print(f"\nTotal: {total_count:,} rows written, {total_skipped:,} assignments skipped",
          file=sys.stderr)
    print(f"Output: {output_path}", file=sys.stderr)
    return total_count, total_skipped


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.zip_or_xml> <output.jsonl.gz> [min_year]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]
    min_year = int(sys.argv[3]) if len(sys.argv) > 3 else 1980

    if not Path(input_path).exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    parse_input(input_path, output_path, min_year)
