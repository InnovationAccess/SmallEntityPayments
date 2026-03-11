#!/usr/bin/env python3
"""Parse USPTO ODP Patent File Wrapper (PFW/PTFWPRE) JSON into JSONL for BigQuery.

Streams large JSON files from ZIP archives using ijson, extracting patent
application records. Outputs THREE gzipped JSONL files:
  1. pfw_biblio.jsonl.gz     — patent_file_wrapper_v2 rows (one per application)
  2. pfw_transactions.jsonl.gz — pfw_transactions rows (one per event per application)
  3. pfw_continuity.jsonl.gz  — pfw_continuity rows (if continuity data present)

Usage:
    python parse_pfw.py <input.zip> <output_dir> [min_year]

The ZIP contains one JSON file per year (e.g. 2026.json, 2025.json).
Each JSON file has structure:
    {
      "count": N,
      "patentFileWrapperDataBag": [ ...records... ]
    }
"""

import gzip
import json
import os
import sys
import zipfile
from pathlib import Path

import ijson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.patent_number import normalize_patent_number

# Map entity status values
ENTITY_STATUS_MAP = {
    "small": "Small",
    "small entity": "Small",
    "micro": "Micro",
    "micro entity": "Micro",
    "regular undiscounted": "Large",
}

# Map citation categories
CATEGORY_MAP = {
    "cited by examiner": "examiner",
    "cited by applicant": "applicant",
    "cited by third party": "third_party",
    "imported from a related application": "related",
}


def map_entity_status(raw: str) -> str:
    """Convert PFW businessEntityStatusCategory to canonical form."""
    return ENTITY_STATUS_MAP.get((raw or "").strip().lower(), "Unknown")


def parse_date(s: str) -> str | None:
    """Parse a date string, returning ISO format or None."""
    if not s or not s.strip():
        return None
    d = s.strip()
    # Validate basic format
    if len(d) >= 8 and d.replace("-", "").isdigit():
        # Reject obviously invalid dates (0000-00-00 etc.)
        digits = d.replace("-", "")
        if digits[:4] == "0000" or digits[4:6] == "00" or digits[6:8] == "00":
            return None
        return d  # Already ISO or yyyymmdd
    return None


def parse_biblio(record: dict, source_file: str) -> dict:
    """Extract bibliographic fields from a PFW record for patent_file_wrapper_v2."""
    meta = record.get("applicationMetaData") or {}
    entity_data = meta.get("entityStatusData") or {}

    application_number = (record.get("applicationNumberText") or "").strip() or None
    patent_number = normalize_patent_number(
        (meta.get("patentNumber") or "").strip()
    )
    invention_title = (meta.get("inventionTitle") or "").strip() or None
    filing_date = parse_date(meta.get("filingDate"))
    effective_filing_date = parse_date(meta.get("effectiveFilingDate"))
    grant_date = parse_date(meta.get("grantDate"))
    entity_status = map_entity_status(
        entity_data.get("businessEntityStatusCategory", "")
    )
    small_entity_indicator = entity_data.get("smallEntityStatusIndicator")

    application_type = (meta.get("applicationTypeCode") or "").strip() or None
    application_type_category = (meta.get("applicationTypeCategory") or "").strip() or None
    application_status_code = meta.get("applicationStatusCode")
    application_status = (meta.get("applicationStatusDescriptionText") or "").strip() or None
    first_inventor_name = (meta.get("firstInventorName") or "").strip() or None
    first_applicant_name = (meta.get("firstApplicantName") or "").strip() or None
    examiner_name = (meta.get("examinerNameText") or "").strip() or None
    group_art_unit = str(meta.get("groupArtUnitNumber", "")).strip() or None
    cpc_codes = meta.get("cpcClassificationBag") or []
    uspc_class = (meta.get("class") or "").strip() or None
    uspc_subclass = (meta.get("subclass") or "").strip() or None
    customer_number = meta.get("customerNumber")
    earliest_pub_number = (meta.get("earliestPublicationNumber") or "").strip() or None
    earliest_pub_date = parse_date(meta.get("earliestPublicationDate"))
    national_stage = meta.get("nationalStageIndicator")
    fitf_raw = meta.get("firstInventorToFileIndicator")
    first_inventor_to_file = fitf_raw == "Y" if fitf_raw else None

    return {
        "application_number": application_number,
        "patent_number": patent_number,
        "invention_title": invention_title,
        "filing_date": filing_date,
        "effective_filing_date": effective_filing_date,
        "grant_date": grant_date,
        "entity_status": entity_status,
        "small_entity_indicator": small_entity_indicator,
        "application_type": application_type,
        "application_type_category": application_type_category,
        "application_status_code": application_status_code,
        "application_status": application_status,
        "first_inventor_name": first_inventor_name,
        "first_applicant_name": first_applicant_name,
        "examiner_name": examiner_name,
        "group_art_unit": group_art_unit,
        "cpc_codes": cpc_codes if cpc_codes else [],
        "uspc_class": uspc_class,
        "uspc_subclass": uspc_subclass,
        "customer_number": customer_number,
        "earliest_publication_number": earliest_pub_number,
        "earliest_publication_date": earliest_pub_date,
        "national_stage_indicator": national_stage,
        "first_inventor_to_file": first_inventor_to_file,
        "source_file": source_file,
    }


def parse_transactions(record: dict, source_file: str) -> list[dict]:
    """Extract transaction events from a PFW record for pfw_transactions."""
    application_number = (record.get("applicationNumberText") or "").strip()
    if not application_number:
        return []

    rows = []
    for event in record.get("eventDataBag") or []:
        event_date = parse_date(event.get("eventDate"))
        event_code = (event.get("eventCode") or "").strip() or None
        event_description = (event.get("eventDescriptionText") or "").strip() or None
        rows.append({
            "application_number": application_number,
            "event_date": event_date,
            "event_code": event_code,
            "event_description": event_description,
            "source_file": source_file,
        })
    return rows


def parse_continuity(record: dict, source_file: str) -> list[dict]:
    """Extract continuity data from a PFW record for pfw_continuity."""
    application_number = (record.get("applicationNumberText") or "").strip()
    if not application_number:
        return []

    rows = []
    for cont in record.get("parentContinuityBag") or []:
        rows.append({
            "application_number": application_number,
            "claim_parentage_type_code": (cont.get("claimParentageTypeCode") or "").strip() or None,
            "claim_parentage_description": (cont.get("claimParentageTypeCodeDescriptionText") or "").strip() or None,
            "parent_application_number": (cont.get("parentApplicationNumberText") or "").strip() or None,
            "parent_filing_date": parse_date(cont.get("parentApplicationFilingDate")),
            "child_application_number": (cont.get("childApplicationNumberText") or "").strip() or None,
            "parent_patent_number": normalize_patent_number(
                (cont.get("parentPatentNumber") or "").strip()
            ),
            "parent_status_code": cont.get("parentApplicationStatusCode"),
            "parent_status_description": (cont.get("parentApplicationStatusDescriptionText") or "").strip() or None,
            "source_file": source_file,
        })
    return rows


def process_year_file(zf: zipfile.ZipFile, filename: str,
                      biblio_out, txn_out, cont_out, min_year: int):
    """Process a single year JSON file from the ZIP, streaming with ijson."""
    stem = Path(filename).stem
    try:
        year = int(stem)
    except ValueError:
        year = None

    if year is not None and year < min_year:
        print(f"  Skipping {filename}: year {year} < min_year {min_year}",
              file=sys.stderr)
        return 0, 0, 0

    label = f"year {year}" if year else stem
    print(f"  Processing {filename} ({label})...", file=sys.stderr)

    biblio_count = 0
    txn_count = 0
    cont_count = 0

    with zf.open(filename) as f:
        records = ijson.items(f, "patentFileWrapperDataBag.item")
        for record in records:
            app_num = (record.get("applicationNumberText") or "").strip()
            if not app_num:
                continue

            # Biblio
            biblio = parse_biblio(record, filename)
            if biblio["application_number"]:
                biblio_out.write(json.dumps(biblio, ensure_ascii=False) + "\n")
                biblio_count += 1

            # Transactions
            txns = parse_transactions(record, filename)
            for txn in txns:
                txn_out.write(json.dumps(txn, ensure_ascii=False) + "\n")
                txn_count += 1

            # Continuity (not typically in bulk PTFWPRE, but handle if present)
            conts = parse_continuity(record, filename)
            for cont in conts:
                cont_out.write(json.dumps(cont, ensure_ascii=False) + "\n")
                cont_count += 1

            if biblio_count % 100000 == 0 and biblio_count > 0:
                print(f"    {biblio_count:,} apps, {txn_count:,} events...",
                      file=sys.stderr)

    print(f"  {filename}: {biblio_count:,} apps, {txn_count:,} events, "
          f"{cont_count:,} continuity records", file=sys.stderr)
    return biblio_count, txn_count, cont_count


def parse_zip(zip_path: str, output_dir: str, min_year: int = 2001):
    """Parse a PTFWPRE/PFW ZIP file into 3 JSONL output files."""
    zf = zipfile.ZipFile(zip_path)
    os.makedirs(output_dir, exist_ok=True)

    # Get year files sorted (newest first for progress visibility)
    year_files = sorted(
        [n for n in zf.namelist() if n.endswith(".json")],
        reverse=True,
    )
    print(f"ZIP contains {len(year_files)} files: {year_files}", file=sys.stderr)

    zip_stem = Path(zip_path).stem
    biblio_path = os.path.join(output_dir, f"pfw_biblio_{zip_stem}.jsonl.gz")
    txn_path = os.path.join(output_dir, f"pfw_transactions_{zip_stem}.jsonl.gz")
    cont_path = os.path.join(output_dir, f"pfw_continuity_{zip_stem}.jsonl.gz")

    total_biblio = 0
    total_txn = 0
    total_cont = 0

    with gzip.open(biblio_path, "wt", encoding="utf-8") as biblio_out, \
         gzip.open(txn_path, "wt", encoding="utf-8") as txn_out, \
         gzip.open(cont_path, "wt", encoding="utf-8") as cont_out:

        for filename in year_files:
            b, t, c = process_year_file(zf, filename, biblio_out, txn_out, cont_out, min_year)
            total_biblio += b
            total_txn += t
            total_cont += c

    print(f"\nTotal: {total_biblio:,} biblio, {total_txn:,} transactions, "
          f"{total_cont:,} continuity records", file=sys.stderr)
    print(f"Output files:", file=sys.stderr)
    print(f"  {biblio_path}", file=sys.stderr)
    print(f"  {txn_path}", file=sys.stderr)
    print(f"  {cont_path}", file=sys.stderr)
    return total_biblio, total_txn, total_cont


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.zip> <output_dir> [min_year]")
        sys.exit(1)

    input_zip = sys.argv[1]
    output_dir = sys.argv[2]
    min_year = int(sys.argv[3]) if len(sys.argv) > 3 else 2001

    if not Path(input_zip).exists():
        print(f"Error: {input_zip} not found")
        sys.exit(1)

    parse_zip(input_zip, output_dir, min_year)
