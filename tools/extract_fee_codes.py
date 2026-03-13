"""
Phase 3 Prototype — Extract entity status and fee codes from USPTO payment PDFs.

Two extraction paths:
  1. pdfplumber: fast, for text-extractable PDFs
  2. Gemini Vision (via Vertex AI REST API): for image-based/scanned PDFs

Usage:
  python3 tools/extract_fee_codes.py /path/to/invoice.pdf
  python3 tools/extract_fee_codes.py /path/to/dir/   # processes all PDFs in dir
"""

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import pdfplumber
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GCP_PROJECT = "uspto-data-app"
GCP_LOCATION = "us-central1"
MODEL_ID = "gemini-2.5-flash"

GEMINI_PROMPT = """You are analyzing a USPTO patent payment document (PDF image).

Extract ALL of the following information as structured JSON:

1. **doc_type**: One of:
   - "FEE_WORKSHEET_SB06" — PTO/SB/06 or PTO-875 Fee Determination Record
   - "ELECTRONIC_FEE_TRANSMITTAL" — Electronic Patent Application Fee Transmittal
   - "ISSUE_FEE_PTO85B" — PTO-85B / Part B Fee(s) Transmittal / Issue Fee Payment
   - "ELECTRONIC_PAYMENT_RECEIPT" — Electronic Payment Receipt
   - "UNKNOWN" — none of the above

2. **application_number**: The patent application number (digits only, no slashes/commas)

3. **filing_date**: Filing date if shown

4. **entity_status**: Look for:
   - Checked checkboxes next to LARGE, SMALL, or MICRO
   - Text like "Filed as Small Entity" or "ENTITY STATUS: SMALL"
   - Column headers: if fees are in "SMALL ENTITY" column, entity_status = "SMALL"
   - Return: "SMALL", "LARGE", "MICRO", or null

5. **title**: Title of invention if shown

6. **fees**: Array of fee line items. For each fee:
   - **fee_code**: Numeric fee code (e.g. "2820", "1833") — null if not shown
   - **description**: Fee description
   - **amount**: Dollar amount per item
   - **quantity**: How many (default 1)
   - **item_total**: Line total if shown

   For SB06 forms with fee rates (not codes), extract the fee type + amount.

7. **total_amount**: Total payment if shown

8. **assignee_name**: Name of assignee if shown

9. **issue_fee_due**: For PTO-85B forms, issue fee due amount

10. **entity_status_evidence**: Brief quote proving the entity status

Return ONLY valid JSON. No markdown, no code fences, no explanation."""


# ---------------------------------------------------------------------------
# Gemini Vision extraction via Vertex AI REST API
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    """Get an access token from gcloud CLI."""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcloud auth failed: {result.stderr}")
    return result.stdout.strip()


def extract_with_gemini(pdf_path: str) -> dict:
    """
    Send PDF to Gemini Vision via Vertex AI REST API.
    Uses gcloud CLI for auth (avoids ADC setup).
    """
    token = _get_access_token()

    # Read and base64-encode the PDF
    pdf_bytes = Path(pdf_path).read_bytes()
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    url = (
        f"https://{GCP_LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{GCP_PROJECT}/locations/{GCP_LOCATION}/"
        f"publishers/google/models/{MODEL_ID}:generateContent"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": GEMINI_PROMPT},
                {
                    "inlineData": {
                        "mimeType": "application/pdf",
                        "data": pdf_b64,
                    }
                },
            ],
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
        },
    }

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if resp.status_code != 200:
        return {
            'doc_type': 'UNKNOWN',
            'error': f'Gemini API error {resp.status_code}: {resp.text[:500]}',
            'entity_status': None,
            'fees': [],
        }

    # Parse response
    resp_json = resp.json()
    candidates = resp_json.get("candidates", [])
    if not candidates:
        return {
            'doc_type': 'UNKNOWN',
            'error': 'No candidates in Gemini response',
            'entity_status': None,
            'fees': [],
        }

    text = candidates[0]["content"]["parts"][0]["text"].strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except json.JSONDecodeError:
                result = {
                    'doc_type': 'UNKNOWN',
                    'error': 'Could not parse Gemini response',
                    'raw_response': text[:1000],
                    'entity_status': None,
                    'fees': [],
                }
        else:
            result = {
                'doc_type': 'UNKNOWN',
                'error': 'No JSON in Gemini response',
                'raw_response': text[:1000],
                'entity_status': None,
                'fees': [],
            }

    return result


# ---------------------------------------------------------------------------
# pdfplumber text extraction (fast path for text-based PDFs)
# ---------------------------------------------------------------------------

def has_extractable_text(pdf_path: str) -> tuple:
    """Check if PDF has extractable text. Returns (has_text, full_text)."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = '\n'.join(
            page.extract_text() or '' for page in pdf.pages
        )
    return bool(full_text.strip()), full_text


def extract_with_pdfplumber(pdf_path: str, full_text: str) -> dict:
    """Extract from text-extractable PDF using pdfplumber (kept as fast path)."""
    upper = full_text[:3000].upper()

    # Detect doc type
    if "ELECTRONIC PAYMENT RECEIPT" in upper:
        doc_type = "ELECTRONIC_PAYMENT_RECEIPT"
    elif "ELECTRONIC PATENT APPLICATION FEE TRANSMITTAL" in upper:
        doc_type = "ELECTRONIC_FEE_TRANSMITTAL"
    elif "FEE(S) TRANSMITTAL" in upper and "PART B" in upper:
        doc_type = "ISSUE_FEE_PTO85B"
    elif any(k in upper for k in ["FEE DETERMINATION RECORD", "PTO/SB/06", "PTO-875"]):
        doc_type = "FEE_WORKSHEET_SB06"
    else:
        doc_type = "UNKNOWN"

    # Entity status
    entity_status = None
    entity_evidence = None
    for pattern, status, evidence in [
        (r'FILED AS SMALL ENTITY', 'SMALL', 'Filed as Small Entity'),
        (r'FILED AS MICRO ENTITY', 'MICRO', 'Filed as Micro Entity'),
        (r'ENTITY\s+STATUS\s*[:\s]+SMALL', 'SMALL', 'ENTITY STATUS: SMALL'),
        (r'ENTITY\s+STATUS\s*[:\s]+LARGE', 'LARGE', 'ENTITY STATUS: LARGE'),
        (r'ENTITY\s+STATUS\s*[:\s]+MICRO', 'MICRO', 'ENTITY STATUS: MICRO'),
    ]:
        if re.search(pattern, full_text.upper()):
            entity_status = status
            entity_evidence = evidence
            break

    # Application number
    app_number = None
    m = re.search(r'Application\s*(?:or Docket)?\s*Number\s*[:\s]*(\d[\d,/]*)', full_text, re.IGNORECASE)
    if m:
        app_number = m.group(1).replace(',', '').replace('/', '')

    # Fee codes from tables
    fees = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row:
                        continue
                    cells = [str(c).strip() if c else '' for c in row]
                    for cell in cells:
                        if re.match(r'^\d{3,5}$', cell):
                            amounts = [float(c.replace(',', ''))
                                       for c in cells
                                       if re.match(r'^[\d,]+\.\d{2}$', c.replace(',', ''))]
                            desc = [c for c in cells
                                    if len(c) > 5 and not c.replace(',', '').replace('.', '').isdigit()]
                            fees.append({
                                'fee_code': cell,
                                'description': desc[0] if desc else None,
                                'amount': amounts[0] if amounts else None,
                                'quantity': 1,
                                'item_total': amounts[-1] if len(amounts) > 1 else None,
                            })

    # Total
    total_amount = None
    m = re.search(r'TOTAL\s*(?:AMOUNT|IN USD|FEE\S* DUE)\s*[:\s$(]*\s*([\d,]+(?:\.\d+)?)',
                  full_text, re.IGNORECASE)
    if m:
        total_amount = float(m.group(1).replace(',', ''))

    return {
        'doc_type': doc_type,
        'application_number': app_number,
        'entity_status': entity_status,
        'entity_status_evidence': entity_evidence,
        'fees': fees,
        'total_amount': total_amount,
    }


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def extract_from_pdf(pdf_path: str) -> dict:
    """
    Extract entity status and fee codes from a USPTO payment PDF.
    Strategy: pdfplumber first (fast), then Gemini Vision for scanned docs.
    """
    path = Path(pdf_path)
    if not path.exists():
        return {'error': f'File not found: {pdf_path}'}

    has_text, full_text = has_extractable_text(str(path))

    if has_text:
        result = extract_with_pdfplumber(str(path), full_text)
        result['extraction_method'] = 'pdfplumber'
    else:
        result = extract_with_gemini(str(path))
        result['extraction_method'] = 'gemini_vision'

    result['file'] = path.name
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 extract_fee_codes.py <pdf_path_or_directory>")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_dir():
        pdfs = sorted(target.glob('*.pdf'))
        if not pdfs:
            print(f"No PDFs found in {target}")
            sys.exit(1)
        for pdf_path in pdfs:
            print(f"\n{'='*70}")
            print(f"FILE: {pdf_path.name}")
            print('='*70)
            result = extract_from_pdf(str(pdf_path))
            print(json.dumps(result, indent=2, default=str))
    else:
        result = extract_from_pdf(str(target))
        print(json.dumps(result, indent=2, default=str))
