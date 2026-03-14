#!/usr/bin/env python3
"""Parse USPTO ODP Patent File Wrapper (PFW/PTFWPRE) JSON into JSONL for BigQuery.

Streams large JSON files from ZIP archives using ijson, extracting patent
application records. Outputs 14 gzipped JSONL files covering ALL fields
in the PTFWPRE schema:

  1.  pfw_biblio         → patent_file_wrapper_v2  (one per application)
  2.  pfw_transactions   → pfw_transactions        (one per event)
  3.  pfw_continuity     → pfw_continuity           (parent continuity)
  4.  pfw_applicants     → pfw_applicants            (all applicants)
  5.  pfw_inventors      → pfw_inventors             (all inventors)
  6.  pfw_child_cont     → pfw_child_continuity      (child continuity)
  7.  pfw_foreign_priority → pfw_foreign_priority    (foreign priority claims)
  8.  pfw_publications   → pfw_publications          (publication metadata)
  9.  pfw_pta_summary    → pfw_patent_term_adjustment (PTA summary)
  10. pfw_pta_history    → pfw_pta_history            (PTA event history)
  11. pfw_correspondence → pfw_correspondence_address (app-level address)
  12. pfw_attorneys      → pfw_attorneys              (attorneys of record)
  13. pfw_doc_metadata   → pfw_document_metadata      (pgpub/grant doc metadata)
  14. pfw_embedded_assign → pfw_embedded_assignments  (embedded assignment chain)

Usage:
    python parse_pfw.py <input.zip> <output_dir> [min_year]
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


def map_entity_status(raw: str) -> str:
    """Convert PFW businessEntityStatusCategory to canonical form."""
    return ENTITY_STATUS_MAP.get((raw or "").strip().lower(), "Unknown")


def parse_date(s: str) -> str | None:
    """Parse a date string, returning ISO format or None."""
    if not s or not s.strip():
        return None
    d = s.strip()
    if len(d) >= 8 and d.replace("-", "").isdigit():
        digits = d.replace("-", "")
        if digits[:4] == "0000" or digits[4:6] == "00" or digits[6:8] == "00":
            return None
        return d
    return None


def _str(val) -> str | None:
    """Safely extract a stripped string or None."""
    return (val or "").strip() or None if isinstance(val, str) else (str(val).strip() or None if val is not None else None)


def _as_list(val) -> list:
    """Ensure a value is a list. Wraps dicts/scalars in a list, returns [] for None."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return [val]
    return []


def _int(val) -> int | None:
    """Safely extract an integer or None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _extract_person_address(person: dict, name_key: str) -> dict:
    """Extract name parts + first correspondence address from an applicant/inventor bag entry.

    Both applicantBag and inventorBag have identical sub-structure for names and addresses.
    name_key is 'applicantNameText' for applicants, 'inventorNameText' for inventors.
    """
    addr = {}
    addr_bag = _as_list(person.get("correspondenceAddressBag"))
    if addr_bag and isinstance(addr_bag[0], dict):
        a = addr_bag[0]
        addr = {
            "address_name_line_1": _str(a.get("nameLineOneText")),
            "address_name_line_2": _str(a.get("nameLineTwoText")),
            "address_city": _str(a.get("cityName")),
            "address_region": _str(a.get("geographicRegionName")),
            "address_region_code": _str(a.get("geographicRegionCode")),
            "address_country_code": _str(a.get("countryCode")),
            "address_country_name": _str(a.get("countryName")),
        }
    else:
        addr = {
            "address_name_line_1": None,
            "address_name_line_2": None,
            "address_city": None,
            "address_region": None,
            "address_region_code": None,
            "address_country_code": None,
            "address_country_name": None,
        }

    return {
        name_key.replace("Text", "").replace("applicantName", "applicant_name").replace("inventorName", "inventor_name"):
            _str(person.get(name_key)),
        "first_name": _str(person.get("firstName")),
        "middle_name": _str(person.get("middleName")),
        "last_name": _str(person.get("lastName")),
        "name_prefix": _str(person.get("namePrefix")),
        "name_suffix": _str(person.get("nameSuffix")),
        "preferred_name": _str(person.get("preferredName")),
        "country_code": _str(person.get("countryCode")),
        **addr,
    }


# ─── Extraction Functions ─────────────────────────────────────────


def parse_biblio(record: dict, source_file: str) -> dict:
    """Extract bibliographic fields from a PFW record for patent_file_wrapper_v2."""
    meta = record.get("applicationMetaData") or {}
    entity_data = meta.get("entityStatusData") or {}

    fitf_raw = meta.get("firstInventorToFileIndicator")
    first_inventor_to_file = fitf_raw == "Y" if fitf_raw else None

    return {
        "application_number": _str(record.get("applicationNumberText")),
        "patent_number": normalize_patent_number((meta.get("patentNumber") or "").strip()),
        "invention_title": _str(meta.get("inventionTitle")),
        "filing_date": parse_date(meta.get("filingDate")),
        "effective_filing_date": parse_date(meta.get("effectiveFilingDate")),
        "grant_date": parse_date(meta.get("grantDate")),
        "entity_status": map_entity_status(entity_data.get("businessEntityStatusCategory", "")),
        "small_entity_indicator": entity_data.get("smallEntityStatusIndicator"),
        "application_type": _str(meta.get("applicationTypeCode")),
        "application_type_category": _str(meta.get("applicationTypeCategory")),
        "application_status_code": meta.get("applicationStatusCode"),
        "application_status": _str(meta.get("applicationStatusDescriptionText")),
        "first_inventor_name": _str(meta.get("firstInventorName")),
        "first_applicant_name": _str(meta.get("firstApplicantName")),
        "examiner_name": _str(meta.get("examinerNameText")),
        "group_art_unit": str(meta.get("groupArtUnitNumber", "")).strip() or None,
        "cpc_codes": meta.get("cpcClassificationBag") or [],
        "uspc_class": _str(meta.get("class")),
        "uspc_subclass": _str(meta.get("subclass")),
        "customer_number": meta.get("customerNumber"),
        "earliest_publication_number": _str(meta.get("earliestPublicationNumber")),
        "earliest_publication_date": parse_date(meta.get("earliestPublicationDate")),
        "national_stage_indicator": meta.get("nationalStageIndicator"),
        "first_inventor_to_file": first_inventor_to_file,
        # NEW fields
        "docket_number": _str(meta.get("docketNumber")),
        "application_confirmation_number": _int(meta.get("applicationConfirmationNumber")),
        "application_status_date": parse_date(meta.get("applicationStatusDate")),
        "application_type_label": _str(meta.get("applicationTypeLabelName")),
        "pct_publication_number": _str(meta.get("pctPublicationNumber")),
        "pct_publication_date": parse_date(meta.get("pctPublicationDate")),
        "intl_registration_number": _str(meta.get("internationalRegistrationNumber")),
        "intl_registration_pub_date": parse_date(meta.get("internationalRegistrationPublicationDate")),
        "uspc_symbol": _str(meta.get("uspcSymbolText")),
        "last_ingestion_datetime": _str(record.get("lastIngestionDateTime")),
        "source_file": source_file,
    }


def parse_transactions(record: dict, source_file: str) -> list[dict]:
    """Extract transaction events from eventDataBag for pfw_transactions."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for event in _as_list(record.get("eventDataBag")):
        if not isinstance(event, dict):
            continue
        rows.append({
            "application_number": app_num,
            "event_date": parse_date(event.get("eventDate")),
            "event_code": _str(event.get("eventCode")),
            "event_description": _str(event.get("eventDescriptionText")),
            "source_file": source_file,
        })
    return rows


def parse_continuity(record: dict, source_file: str) -> list[dict]:
    """Extract parent continuity from parentContinuityBag for pfw_continuity."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for cont in _as_list(record.get("parentContinuityBag")):
        if not isinstance(cont, dict):
            continue
        rows.append({
            "application_number": app_num,
            "claim_parentage_type_code": _str(cont.get("claimParentageTypeCode")),
            "claim_parentage_description": _str(cont.get("claimParentageTypeCodeDescriptionText")),
            "parent_application_number": _str(cont.get("parentApplicationNumberText")),
            "parent_filing_date": parse_date(cont.get("parentApplicationFilingDate")),
            "child_application_number": _str(cont.get("childApplicationNumberText")),
            "parent_patent_number": normalize_patent_number(
                (cont.get("parentPatentNumber") or "").strip()
            ),
            "parent_status_code": cont.get("parentApplicationStatusCode"),
            "parent_status_description": _str(cont.get("parentApplicationStatusDescriptionText")),
            "source_file": source_file,
        })
    return rows


def parse_applicants(record: dict, source_file: str) -> list[dict]:
    """Extract all applicants from applicationMetaData.applicantBag for pfw_applicants."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    meta = record.get("applicationMetaData") or {}
    rows = []
    for person in _as_list(meta.get("applicantBag")):
        if not isinstance(person, dict):
            continue
        row = {"application_number": app_num, "source_file": source_file}
        extracted = _extract_person_address(person, "applicantNameText")
        row.update(extracted)
        rows.append(row)
    return rows


def parse_inventors(record: dict, source_file: str) -> list[dict]:
    """Extract all inventors from applicationMetaData.inventorBag for pfw_inventors."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    meta = record.get("applicationMetaData") or {}
    rows = []
    for person in _as_list(meta.get("inventorBag")):
        if not isinstance(person, dict):
            continue
        row = {"application_number": app_num, "source_file": source_file}
        extracted = _extract_person_address(person, "inventorNameText")
        row.update(extracted)
        rows.append(row)
    return rows


def parse_child_continuity(record: dict, source_file: str) -> list[dict]:
    """Extract child continuity from childContinuityBag for pfw_child_continuity."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for child in _as_list(record.get("childContinuityBag")):
        if not isinstance(child, dict):
            continue
        fitf = child.get("firstInventorToFileIndicator")
        rows.append({
            "application_number": app_num,
            "child_application_number": _str(child.get("childApplicationNumberText")),
            "parent_application_number": _str(child.get("parentApplicationNumberText")),
            "child_filing_date": parse_date(child.get("childApplicationFilingDate")),
            "child_patent_number": normalize_patent_number(
                (child.get("childPatentNumber") or "").strip()
            ),
            "child_status_code": _int(child.get("childApplicationStatusCode")),
            "child_status_description": _str(child.get("childApplicationStatusDescriptionText")),
            "claim_parentage_type_code": _str(child.get("claimParentageTypeCode")),
            "claim_parentage_description": _str(child.get("claimParentageTypeCodeDescriptionText")),
            "first_inventor_to_file": fitf if isinstance(fitf, bool) else None,
            "source_file": source_file,
        })
    return rows


def parse_foreign_priority(record: dict, source_file: str) -> list[dict]:
    """Extract foreign priority claims from foreignPriorityBag for pfw_foreign_priority."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for fp in _as_list(record.get("foreignPriorityBag")):
        if not isinstance(fp, dict):
            continue
        rows.append({
            "application_number": app_num,
            "priority_country": _str(fp.get("ipOfficeName")),
            "priority_filing_date": parse_date(fp.get("filingDate")),
            "priority_application_number": _str(fp.get("applicationNumberText")),
            "source_file": source_file,
        })
    return rows


def parse_publications(record: dict, source_file: str) -> list[dict]:
    """Extract publications from parallel arrays in applicationMetaData for pfw_publications."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    meta = record.get("applicationMetaData") or {}
    dates = _as_list(meta.get("publicationDateBag"))
    seq_nums = _as_list(meta.get("publicationSequenceNumberBag"))
    categories = _as_list(meta.get("publicationCategoryBag"))
    max_len = max(len(dates), len(seq_nums), len(categories))
    if max_len == 0:
        return []
    rows = []
    for i in range(max_len):
        rows.append({
            "application_number": app_num,
            "publication_date": _str(dates[i]) if i < len(dates) else None,
            "publication_sequence_number": _str(seq_nums[i]) if i < len(seq_nums) else None,
            "publication_category": _str(categories[i]) if i < len(categories) else None,
            "source_file": source_file,
        })
    return rows


def parse_pta(record: dict, source_file: str) -> dict | None:
    """Extract PTA summary from patentTermAdjustmentData for pfw_patent_term_adjustment."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return None
    pta = record.get("patentTermAdjustmentData")
    if not pta:
        return None
    return {
        "application_number": app_num,
        "a_delay_days": _int(pta.get("aDelayQuantity")),
        "b_delay_days": _int(pta.get("bDelayQuantity")),
        "c_delay_days": _int(pta.get("cDelayQuantity")),
        "overlap_days": _int(pta.get("overlappingDayQuantity")),
        "non_overlap_days": _int(pta.get("nonOverlappingDayQuantity")),
        "applicant_delay_days": _int(pta.get("applicantDayDelayQuantity")),
        "adjustment_total_days": _int(pta.get("adjustmentTotalQuantity")),
        "source_file": source_file,
    }


def parse_pta_history(record: dict, source_file: str) -> list[dict]:
    """Extract PTA history events from patentTermAdjustmentData.patentTermAdjustmentHistoryDataBag."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    pta = record.get("patentTermAdjustmentData")
    if not pta:
        return []
    rows = []
    for evt in _as_list(pta.get("patentTermAdjustmentHistoryDataBag")):
        if not isinstance(evt, dict):
            continue
        rows.append({
            "application_number": app_num,
            "event_sequence_number": _int(evt.get("eventSequenceNumber")),
            "event_date": parse_date(evt.get("eventDate")),
            "event_description": _str(evt.get("eventDescriptionText")),
            "pta_pte_code": _str(evt.get("ptaPTECode")),
            "ip_office_delay_days": _int(evt.get("ipOfficeDayDelayQuantity")),
            "applicant_delay_days": _int(evt.get("applicantDayDelayQuantity")),
            "originating_event_sequence": _int(evt.get("originatingEventSequenceNumber")),
            "source_file": source_file,
        })
    return rows


def parse_correspondence_address(record: dict, source_file: str) -> list[dict]:
    """Extract top-level correspondence address from correspondenceAddressBag."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for addr in _as_list(record.get("correspondenceAddressBag")):
        if not isinstance(addr, dict):
            continue
        rows.append({
            "application_number": app_num,
            "name_line_1": _str(addr.get("nameLineOneText")),
            "name_line_2": _str(addr.get("nameLineTwoText")),
            "address_line_1": _str(addr.get("addressLineOneText")),
            "address_line_2": _str(addr.get("addressLineTwoText")),
            "city": _str(addr.get("cityName")),
            "region": _str(addr.get("geographicRegionName")),
            "region_code": _str(addr.get("geographicRegionCode")),
            "postal_code": _str(addr.get("postalCode")),
            "country_code": _str(addr.get("countryCode")),
            "country_name": _str(addr.get("countryName")),
            "source_file": source_file,
        })
    return rows


def parse_attorneys(record: dict, source_file: str) -> list[dict]:
    """Extract attorneys from recordAttorney (POA, attorney, and customer correspondence)."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    attorney_data = record.get("recordAttorney")
    if not attorney_data:
        return []
    rows = []

    # Power of Attorney
    for poa in _as_list(attorney_data.get("powerOfAttorneyBag")):
        if not isinstance(poa, dict):
            continue
        rows.append({
            "application_number": app_num,
            "role": "power_of_attorney",
            "first_name": _str(poa.get("firstName")),
            "middle_name": _str(poa.get("middleName")),
            "last_name": _str(poa.get("lastName")),
            "name_prefix": _str(poa.get("namePrefix")),
            "name_suffix": _str(poa.get("nameSuffix")),
            "preferred_name": _str(poa.get("preferredName")),
            "registration_number": _str(poa.get("registrationNumber")),
            "active_indicator": _str(poa.get("activeIndicator")),
            "practitioner_category": _str(poa.get("registeredPractitionerCategory")),
            "country_code": _str(poa.get("countryCode")),
            "patron_identifier": None,
            "organization_name": None,
            "source_file": source_file,
        })

    # Attorney of record
    for att in _as_list(attorney_data.get("attorneyBag")):
        if not isinstance(att, dict):
            continue
        rows.append({
            "application_number": app_num,
            "role": "attorney",
            "first_name": _str(att.get("firstName")),
            "middle_name": _str(att.get("middleName")),
            "last_name": _str(att.get("lastName")),
            "name_prefix": _str(att.get("namePrefix")),
            "name_suffix": _str(att.get("nameSuffix")),
            "preferred_name": None,
            "registration_number": _str(att.get("registrationNumber")),
            "active_indicator": _str(att.get("activeIndicator")),
            "practitioner_category": _str(att.get("registeredPractitionerCategory")),
            "country_code": None,
            "patron_identifier": None,
            "organization_name": None,
            "source_file": source_file,
        })

    # Customer number correspondence data
    for cust in _as_list(attorney_data.get("customerNumberCorrespondenceData")):
        if not isinstance(cust, dict):
            continue
        rows.append({
            "application_number": app_num,
            "role": "customer_correspondence",
            "first_name": None,
            "middle_name": None,
            "last_name": None,
            "name_prefix": None,
            "name_suffix": None,
            "preferred_name": None,
            "registration_number": None,
            "active_indicator": None,
            "practitioner_category": None,
            "country_code": None,
            "patron_identifier": _int(cust.get("patronIdentifier")),
            "organization_name": _str(cust.get("organizationStandardName")),
            "source_file": source_file,
        })

    return rows


def parse_document_metadata(record: dict, source_file: str) -> list[dict]:
    """Extract document metadata from pgpubDocumentMetaData and grantDocumentMetaData."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for doc_type, key in [("pgpub", "pgpubDocumentMetaData"), ("grant", "grantDocumentMetaData")]:
        doc = record.get(key)
        if doc:
            rows.append({
                "application_number": app_num,
                "document_type": doc_type,
                "zip_file_name": _str(doc.get("zipFileName")),
                "product_identifier": _str(doc.get("productIdentifier")),
                "file_location_uri": _str(doc.get("fileLocationURI")),
                "file_create_datetime": _str(doc.get("fileCreateDateTime")),
                "xml_file_name": _str(doc.get("xmlFileName")),
                "source_file": source_file,
            })
    return rows


def parse_embedded_assignments(record: dict, source_file: str) -> list[dict]:
    """Extract embedded assignment chain from assignmentBag for pfw_embedded_assignments."""
    app_num = _str(record.get("applicationNumberText"))
    if not app_num:
        return []
    rows = []
    for asn in _as_list(record.get("assignmentBag")):
        if not isinstance(asn, dict):
            continue
        # Flatten assignor names
        assignor_names = ", ".join(
            n for a in (asn.get("assignorBag") or [])
            if isinstance(a, dict) and (n := _str(a.get("assignorName")))
        ) or None
        # Flatten assignee names
        assignee_names = ", ".join(
            n for a in (asn.get("assigneeBag") or [])
            if isinstance(a, dict) and (n := _str(a.get("assigneeNameText")))
        ) or None
        # First correspondent name
        corr_list = _as_list(asn.get("correspondenceAddress"))
        corr_name = _str(corr_list[0].get("correspondentNameText")) if corr_list and isinstance(corr_list[0], dict) else None

        rows.append({
            "application_number": app_num,
            "reel_frame": _str(asn.get("reelAndFrameNumber")),
            "reel_number": _int(asn.get("reelNumber")),
            "frame_number": _int(asn.get("frameNumber")),
            "page_count": _int(asn.get("pageTotalQuantity")),
            "document_uri": _str(asn.get("assignmentDocumentLocationURI")),
            "received_date": parse_date(asn.get("assignmentReceivedDate")),
            "recorded_date": parse_date(asn.get("assignmentRecordedDate")),
            "mailed_date": parse_date(asn.get("assignmentMailedDate")),
            "conveyance_text": _str(asn.get("conveyanceText")),
            "assignor_names": assignor_names,
            "assignee_names": assignee_names,
            "correspondent_name": corr_name,
            "source_file": source_file,
        })
    return rows


# ─── Processing ─────────────────────────────────────────────────────

# Output file keys (order matters for tuple unpacking)
OUTPUT_KEYS = [
    "biblio", "transactions", "continuity",
    "applicants", "inventors", "child_continuity", "foreign_priority",
    "publications", "pta_summary", "pta_history",
    "correspondence", "attorneys", "doc_metadata", "embedded_assignments",
]


def process_year_file(zf: zipfile.ZipFile, filename: str,
                      writers: dict, min_year: int) -> dict:
    """Process a single year JSON file from the ZIP, streaming with ijson.

    writers: dict mapping OUTPUT_KEYS to open gzip file handles.
    Returns: dict mapping OUTPUT_KEYS to row counts.
    """
    stem = Path(filename).stem
    try:
        year = int(stem)
    except ValueError:
        year = None

    if year is not None and year < min_year:
        print(f"  Skipping {filename}: year {year} < min_year {min_year}",
              file=sys.stderr)
        return {k: 0 for k in OUTPUT_KEYS}

    label = f"year {year}" if year else stem
    print(f"  Processing {filename} ({label})...", file=sys.stderr)

    counts = {k: 0 for k in OUTPUT_KEYS}

    def _write(key: str, rows):
        """Write rows to the appropriate output file."""
        out = writers[key]
        if isinstance(rows, dict):
            # Single row (e.g., PTA summary)
            out.write(json.dumps(rows, ensure_ascii=False) + "\n")
            counts[key] += 1
        elif isinstance(rows, list):
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                counts[key] += 1

    errors = 0

    def _safe_write(key: str, func, record, filename):
        """Call extraction function and write results, catching errors per-function."""
        nonlocal errors
        try:
            result = func(record, filename)
            if result is not None:
                _write(key, result)
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"    WARNING: {key} error (app={record.get('applicationNumberText','?')}): "
                      f"{type(e).__name__}: {e}", file=sys.stderr)

    with zf.open(filename) as f:
        records = ijson.items(f, "patentFileWrapperDataBag.item")
        for record in records:
            if not isinstance(record, dict):
                errors += 1
                continue
            app_num = (record.get("applicationNumberText") or "").strip()
            if not app_num:
                continue

            # 1. Biblio (always try — most important)
            try:
                biblio = parse_biblio(record, filename)
                if biblio["application_number"]:
                    _write("biblio", biblio)
            except Exception as e:
                errors += 1
                if errors <= 10:
                    print(f"    WARNING: biblio error (app={app_num}): {type(e).__name__}: {e}",
                          file=sys.stderr)

            # 2-14: Each extraction isolated — one failure doesn't affect others
            _safe_write("transactions", parse_transactions, record, filename)
            _safe_write("continuity", parse_continuity, record, filename)
            _safe_write("applicants", parse_applicants, record, filename)
            _safe_write("inventors", parse_inventors, record, filename)
            _safe_write("child_continuity", parse_child_continuity, record, filename)
            _safe_write("foreign_priority", parse_foreign_priority, record, filename)
            _safe_write("publications", parse_publications, record, filename)

            # PTA summary (returns dict or None)
            _safe_write("pta_summary", parse_pta, record, filename)

            _safe_write("pta_history", parse_pta_history, record, filename)
            _safe_write("correspondence", parse_correspondence_address, record, filename)
            _safe_write("attorneys", parse_attorneys, record, filename)
            _safe_write("doc_metadata", parse_document_metadata, record, filename)
            _safe_write("embedded_assignments", parse_embedded_assignments, record, filename)

            if counts["biblio"] % 100000 == 0 and counts["biblio"] > 0:
                print(f"    {counts['biblio']:,} apps, "
                      f"{counts['transactions']:,} events, "
                      f"{counts['applicants']:,} applicants, "
                      f"{counts['inventors']:,} inventors...",
                      file=sys.stderr)

    if errors > 0:
        print(f"  {filename}: {errors} record errors (skipped)", file=sys.stderr)
    print(f"  {filename}: {counts['biblio']:,} apps, "
          f"{counts['transactions']:,} events, "
          f"{counts['applicants']:,} applicants, "
          f"{counts['inventors']:,} inventors",
          file=sys.stderr)
    return counts


# File prefix for each output key
FILE_PREFIXES = {
    "biblio": "pfw_biblio",
    "transactions": "pfw_transactions",
    "continuity": "pfw_continuity",
    "applicants": "pfw_applicants",
    "inventors": "pfw_inventors",
    "child_continuity": "pfw_child_cont",
    "foreign_priority": "pfw_foreign_priority",
    "publications": "pfw_publications",
    "pta_summary": "pfw_pta_summary",
    "pta_history": "pfw_pta_history",
    "correspondence": "pfw_correspondence",
    "attorneys": "pfw_attorneys",
    "doc_metadata": "pfw_doc_metadata",
    "embedded_assignments": "pfw_embedded_assign",
}


def parse_zip(zip_path: str, output_dir: str, min_year: int = 2001) -> dict:
    """Parse a PTFWPRE/PFW ZIP file into 14 JSONL output files.

    Returns a dict mapping output keys to row counts.
    """
    zf = zipfile.ZipFile(zip_path)
    os.makedirs(output_dir, exist_ok=True)

    year_files = sorted(
        [n for n in zf.namelist() if n.endswith(".json")],
        reverse=True,
    )
    print(f"ZIP contains {len(year_files)} files: {year_files}", file=sys.stderr)

    zip_stem = Path(zip_path).stem

    # Build output file paths
    output_paths = {}
    for key, prefix in FILE_PREFIXES.items():
        output_paths[key] = os.path.join(output_dir, f"{prefix}_{zip_stem}.jsonl.gz")

    totals = {k: 0 for k in OUTPUT_KEYS}

    # Open all 14 gzip writers
    open_files = {}
    try:
        for key in OUTPUT_KEYS:
            open_files[key] = gzip.open(output_paths[key], "wt", encoding="utf-8")

        for filename in year_files:
            file_counts = process_year_file(zf, filename, open_files, min_year)
            for k in OUTPUT_KEYS:
                totals[k] += file_counts[k]
    finally:
        for fh in open_files.values():
            fh.close()

    print(f"\nTotal rows extracted:", file=sys.stderr)
    for key in OUTPUT_KEYS:
        if totals[key] > 0:
            print(f"  {key}: {totals[key]:,}", file=sys.stderr)
    print(f"\nOutput files ({len(output_paths)}):", file=sys.stderr)
    for key in OUTPUT_KEYS:
        print(f"  {output_paths[key]}", file=sys.stderr)

    return totals


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
