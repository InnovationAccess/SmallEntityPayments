#!/usr/bin/env python3
"""Parse USPTO Patent Assignment XML (PASYR/PASDL) into denormalized JSONL for BigQuery.

Handles both annual backfile (PASYR) and daily update (PASDL) XML files.
The XML schema is the same for both — root element <us-patent-assignments>.

Denormalization: one assignment with 3 assignors × 2 patents = 6 output rows.
Each row includes the assignment record info, one assignor, the first assignee,
and one patent/application document.

Usage:
    python parse_assignments_xml_v2.py <input.zip_or_xml> <output.jsonl.gz> [min_year]

Input can be:
  - A ZIP file containing one or more XML files
  - A bare XML file

Output schema matches patent_assignments_v2 BigQuery table.
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


def parse_assignment(elem, source_file: str, min_year: int) -> list[dict]:
    """Parse a single <patent-assignment> element into denormalized rows.

    Returns a list of dicts (one per assignor × patent/application), or empty
    list if filtered out.
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
    purge_indicator = extract_text(rec, "purge-indicator") or None
    page_count_str = extract_text(rec, "page-count")
    try:
        page_count = int(page_count_str) if page_count_str else None
    except ValueError:
        page_count = None
    correspondent_name = extract_text(rec, "correspondent/name") or None
    conveyance_text = extract_text(rec, "conveyance-text") or None

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

    # --- Assignees (take first one for denormalization) ---
    assignee_name = None
    assignee_city = None
    assignee_state = None
    assignee_country = None
    assignee_postcode = None
    for ae in elem.findall(".//patent-assignees/patent-assignee"):
        n = extract_text(ae, "name")
        if n:
            assignee_name = n
            assignee_city = extract_text(ae, "city") or None
            assignee_state = extract_text(ae, "state") or None
            assignee_country = extract_text(ae, "country-name") or None
            assignee_postcode = extract_text(ae, "postcode") or None
            break

    # --- Patent properties (documents covered by this assignment) ---
    documents = []
    for prop in elem.findall(".//patent-properties/patent-property"):
        doc_id = prop.find("document-id")
        if doc_id is None:
            continue
        doc_country = extract_text(doc_id, "country") or None
        doc_number_raw = extract_text(doc_id, "doc-number")
        doc_kind = extract_text(doc_id, "kind") or None
        inv_title = extract_text(prop, "invention-title") or None

        if not doc_number_raw:
            continue

        # Normalize US patent/application numbers
        if doc_country in ("US", None, ""):
            doc_number = normalize_patent_number(doc_number_raw) or doc_number_raw
        else:
            doc_number = doc_number_raw

        documents.append({
            "doc_country": doc_country,
            "doc_number": doc_number,
            "doc_kind": doc_kind,
            "invention_title": inv_title,
        })

    if not documents:
        return []

    # --- Cross-product: assignors × documents ---
    rows = []
    for assignor in assignors:
        for doc in documents:
            rows.append({
                "reel_no": reel_no,
                "frame_no": frame_no,
                "reel_frame": reel_frame,
                "recorded_date": recorded_date,
                "last_update_date": last_update_date,
                "purge_indicator": purge_indicator,
                "page_count": page_count,
                "correspondent_name": correspondent_name,
                "conveyance_text": conveyance_text,
                "assignor_name": assignor["name"],
                "assignor_execution_date": assignor["execution_date"],
                "assignee_name": assignee_name,
                "assignee_city": assignee_city,
                "assignee_state": assignee_state,
                "assignee_country": assignee_country,
                "assignee_postcode": assignee_postcode,
                "doc_country": doc["doc_country"],
                "doc_number": doc["doc_number"],
                "doc_kind": doc["doc_kind"],
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
