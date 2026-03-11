#!/usr/bin/env python3
"""Parse USPTO Patent Grant Bibliographic XML (PTBLXML) for forward citations.

Extracts US patent-to-patent citation relationships from weekly grant XML files.
The XML files are concatenated individual XML documents (each with its own
<?xml version="1.0"?> declaration), NOT valid XML as a whole.

Strategy: wrap contents in a synthetic root element, then iterparse on
<us-patent-grant> end events. For older DTD versions (pre-2007), also handle
<patent-grant> tags.

Usage:
    python parse_ptblxml.py <input.zip> <output.jsonl.gz>

Output schema matches forward_citations BigQuery table:
  cited_patent_number, citing_patent_number, citing_grant_date,
  citing_application_number, citing_filing_date, citation_category,
  citing_kind_code, cited_kind_code, source_file
"""

import gzip
import json
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import iterparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.patent_number import normalize_patent_number

# Category normalization
CATEGORY_MAP = {
    "cited by examiner": "examiner",
    "cited by applicant": "applicant",
    "cited by third party": "third_party",
    "imported from a related application": "related",
}


def extract_text(elem, path: str) -> str:
    """Get text from an XML element at the given path."""
    child = elem.find(path)
    return (child.text or "").strip() if child is not None else ""


def parse_date(raw: str) -> str | None:
    """Convert yyyymmdd to yyyy-mm-dd, or None if invalid."""
    raw = (raw or "").strip()
    if len(raw) < 8 or not raw[:8].isdigit():
        return None
    d = raw[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def parse_grant(grant_elem, source_file: str) -> list[dict]:
    """Parse a single <us-patent-grant> or <patent-grant> element.

    Returns a list of forward citation dicts (one per US patent citation).
    """
    biblio = grant_elem.find("us-bibliographic-data-grant")
    if biblio is None:
        biblio = grant_elem.find("bibliographic-data-grant")
    if biblio is None:
        return []

    # --- Citing patent info ---
    pub_ref = biblio.find("publication-reference/document-id")
    if pub_ref is None:
        return []
    citing_patent_raw = extract_text(pub_ref, "doc-number")
    citing_kind = extract_text(pub_ref, "kind") or None
    citing_grant_date = parse_date(extract_text(pub_ref, "date"))
    citing_country = extract_text(pub_ref, "country")

    # Only process US patents
    if citing_country and citing_country != "US":
        return []

    citing_patent = normalize_patent_number(citing_patent_raw)
    if not citing_patent or not citing_grant_date:
        return []

    # --- Citing application info ---
    app_ref = biblio.find("application-reference/document-id")
    citing_app_number = None
    citing_filing_date = None
    if app_ref is not None:
        citing_app_number = extract_text(app_ref, "doc-number") or None
        citing_filing_date = parse_date(extract_text(app_ref, "date"))

    # --- References cited ---
    # Handle both DTD v4.2+ (<us-references-cited>/<us-citation>)
    # and older DTD v2.5 (<references-cited>/<citation>)
    citations_container = biblio.find("us-references-cited")
    citation_tag = "us-citation"
    if citations_container is None:
        citations_container = biblio.find("references-cited")
        citation_tag = "citation"
    if citations_container is None:
        return []

    rows = []
    for cit in citations_container.findall(citation_tag):
        # Skip NPL citations
        patcit = cit.find("patcit")
        if patcit is None:
            continue

        doc_id = patcit.find("document-id")
        if doc_id is None:
            continue

        cited_country = extract_text(doc_id, "country")
        # Only keep US patent citations
        if cited_country != "US":
            continue

        cited_doc_raw = extract_text(doc_id, "doc-number")
        cited_kind = extract_text(doc_id, "kind") or None

        cited_patent = normalize_patent_number(cited_doc_raw)
        if not cited_patent:
            continue

        # Category
        category_raw = extract_text(cit, "category").lower()
        category = CATEGORY_MAP.get(category_raw, category_raw or "unknown")

        rows.append({
            "cited_patent_number": cited_patent,
            "citing_patent_number": citing_patent,
            "citing_grant_date": citing_grant_date,
            "citing_application_number": citing_app_number,
            "citing_filing_date": citing_filing_date,
            "citation_category": category,
            "citing_kind_code": citing_kind,
            "cited_kind_code": cited_kind,
            "source_file": source_file,
        })

    return rows


def parse_xml_file(xml_data: bytes, source_file: str, fout) -> tuple[int, int]:
    """Parse a single PTBLXML XML file (concatenated documents).

    Strategy: strip all <?xml ...?> and <!DOCTYPE ...> declarations,
    wrap in a synthetic <root>, then iterparse.
    """
    count = 0
    grants_seen = 0
    skipped = 0

    # Decode to string for processing
    try:
        text = xml_data.decode("utf-8", errors="replace")
    except Exception:
        text = xml_data.decode("latin-1", errors="replace")

    # Strip XML declarations and DOCTYPE declarations
    text = re.sub(r'<\?xml[^?]*\?>', '', text)
    text = re.sub(r'<!DOCTYPE[^>]*(?:\[[^\]]*\])?\s*>', '', text, flags=re.DOTALL)

    # Wrap in synthetic root
    wrapped = b"<root>" + text.encode("utf-8", errors="replace") + b"</root>"

    try:
        stream = BytesIO(wrapped)
        context = iterparse(stream, events=("end",))
        for event, elem in context:
            if elem.tag in ("us-patent-grant", "patent-grant"):
                grants_seen += 1
                rows = parse_grant(elem, source_file)
                if rows:
                    for row in rows:
                        fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                        count += 1
                else:
                    skipped += 1

                # Free memory
                elem.clear()

                if grants_seen % 1000 == 0:
                    print(f"    {grants_seen:,} grants, {count:,} citations...",
                          file=sys.stderr, end="\r")
    except Exception as e:
        print(f"\nError parsing {source_file}: {e}", file=sys.stderr)

    print(f"  {source_file}: {count:,} citations from {grants_seen:,} grants "
          f"({skipped:,} without US citations)", file=sys.stderr)
    return count, skipped


def parse_zip(zip_path: str, output_path: str):
    """Parse a PTBLXML ZIP file into gzipped JSONL."""
    if not output_path.endswith(".gz"):
        output_path += ".gz"

    zf = zipfile.ZipFile(zip_path)

    # Find the main XML data file (ipgb*.xml, not rpt.html or lst.txt)
    xml_files = [n for n in zf.namelist()
                 if n.endswith(".xml") and "rpt" not in n.lower()]
    if not xml_files:
        print(f"Error: No XML data file found in {zip_path}", file=sys.stderr)
        sys.exit(1)

    total_count = 0
    total_skipped = 0

    with gzip.open(output_path, "wt", encoding="utf-8") as fout:
        for xml_file in sorted(xml_files):
            print(f"\nProcessing {xml_file} from {zip_path}...", file=sys.stderr)
            xml_data = zf.read(xml_file)
            print(f"  XML size: {len(xml_data) / 1024 / 1024:.1f} MB", file=sys.stderr)

            c, s = parse_xml_file(xml_data, xml_file, fout)
            total_count += c
            total_skipped += s

    print(f"\nTotal: {total_count:,} citation rows written, "
          f"{total_skipped:,} grants without US citations", file=sys.stderr)
    print(f"Output: {output_path}", file=sys.stderr)
    return total_count, total_skipped


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.zip> <output.jsonl.gz>")
        sys.exit(1)

    zip_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(zip_path).exists():
        print(f"Error: {zip_path} not found")
        sys.exit(1)

    parse_zip(zip_path, output_path)
