"""SEC EDGAR API integration for 10-K filing discovery and download."""

import re
import time
import logging
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "PatentAnalyzer/1.0 (uzi@ilvrge.com)",
    "Accept-Encoding": "gzip, deflate",
}
RATE_LIMIT = 0.15  # seconds between EDGAR requests

# ── Company name normalization ──────────────────────────────────────

_LOWER_WORDS = {"of", "the", "and", "in", "for", "on", "at", "to", "by", "a", "an", "de", "del"}
_SUFFIX_MAP = {
    "INC": "Inc.", "CORP": "Corp.", "CO": "Co.", "LTD": "Ltd.",
    "LLC": "LLC", "LP": "LP", "PLC": "PLC",
    "HOLDINGS": "Holdings", "GROUP": "Group", "INTERNATIONAL": "International",
}


def normalize_company_name(name: str) -> str:
    """Convert ALL-CAPS SEC name to proper title case.

    E.g. 'QUALCOMM INC/DE' -> 'Qualcomm Inc.'
    """
    if not name:
        return name
    # Strip state suffix like /DE, /NY
    name = re.sub(r"/[A-Z]{2}$", "", name.strip())
    # If already has lowercase, return as-is
    if any(c.islower() for c in name):
        return name
    words = name.split()
    result = []
    for i, word in enumerate(words):
        upper = word.upper()
        if upper in _SUFFIX_MAP:
            result.append(_SUFFIX_MAP[upper])
        elif i > 0 and upper in _LOWER_WORDS:
            result.append(word.lower())
        else:
            result.append(word.capitalize())
    return " ".join(result)


# ── Ticker / CIK lookup ────────────────────────────────────────────

_tickers_cache = None


def _load_tickers() -> dict:
    """Load SEC company_tickers.json (cached)."""
    global _tickers_cache
    if _tickers_cache is not None:
        return _tickers_cache
    url = "https://www.sec.gov/files/company_tickers.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    _tickers_cache = resp.json()
    time.sleep(RATE_LIMIT)
    return _tickers_cache


def lookup_cik(ticker: str) -> dict | None:
    """Find CIK and company title for a ticker symbol.

    Returns {'cik_str': '...', 'title': '...', 'ticker': '...'} or None.
    """
    data = _load_tickers()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return {
                "cik_str": str(entry["cik_str"]),
                "title": entry.get("title", ""),
                "ticker": entry.get("ticker", ""),
            }
    return None


# ── Submissions / filing lookup ─────────────────────────────────────

def _get_submissions(cik: str) -> dict:
    """Fetch EDGAR submissions JSON for a CIK."""
    cik_padded = cik.zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    time.sleep(RATE_LIMIT)
    return resp.json()


def get_latest_10k(cik: str) -> dict | None:
    """Find the most recent 10-K or 10-K/A filing for a CIK.

    Returns {'filing_date': '...', 'filing_url': '...', 'accession_number': '...', 'form': '...'}
    or None if not found.
    """
    data = _get_submissions(cik)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    for i, form in enumerate(forms):
        if form in ("10-K", "10-K/A"):
            acc_no = accessions[i]
            acc_no_dashes = acc_no.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_no_dashes}/{primary_docs[i]}"
            )
            return {
                "filing_date": dates[i],
                "filing_url": filing_url,
                "accession_number": acc_no,
                "form": form,
            }
    return None


def fetch_proxy_statement(cik: str) -> str | None:
    """Find the most recent DEF 14A proxy statement URL for a CIK."""
    data = _get_submissions(cik)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    for i, form in enumerate(forms):
        if form in ("DEF 14A", "DEF14A"):
            acc_no = accessions[i]
            acc_no_dashes = acc_no.replace("-", "")
            return (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_no_dashes}/{primary_docs[i]}"
            )
    return None


def get_filings_by_type(cik: str, form_type: str, limit: int = 5) -> list[dict]:
    """Get recent filings of a specific form type for a CIK.

    Returns list of {'filing_date': '...', 'filing_url': '...'}.
    """
    data = _get_submissions(cik)
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    results = []
    for i, form in enumerate(forms):
        if form == form_type or form.replace(" ", "") == form_type.replace(" ", ""):
            acc_no = accessions[i]
            acc_no_dashes = acc_no.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_no_dashes}/{primary_docs[i]}"
            )
            results.append({"filing_date": dates[i], "filing_url": url})
            if len(results) >= limit:
                break
    return results


# ── Filing text download & parsing ──────────────────────────────────

def fetch_filing_text(url: str, max_chars: int = 200_000) -> str:
    """Download a filing URL, strip HTML, return clean text."""
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    time.sleep(RATE_LIMIT)

    soup = BeautifulSoup(resp.content, "lxml")
    # Remove non-content tags
    for tag in soup.find_all(["script", "style", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text[:max_chars]


def extract_sections(text: str) -> dict:
    """Try to locate Item 1, Item 1A, and Item 7 from 10-K text.

    Returns {'item1': str, 'item1a': str, 'item7': str}.
    If sections not found, returns {'full_text': first 90K chars}.
    """
    sections = {}

    # Item 1 (Business)
    m1 = re.search(
        r"(?i)(?:^|\n)\s*(?:ITEM|Item)\s+1[\.\s:]+.*?(?:BUSINESS|Business)",
        text,
    )
    if m1:
        start = m1.start()
        sections["item1"] = text[start : start + 35_000]

    # Item 1A (Risk Factors)
    m1a = re.search(
        r"(?i)(?:^|\n)\s*(?:ITEM|Item)\s+1A[\.\s:]+.*?(?:RISK|Risk)",
        text,
    )
    if m1a:
        start = m1a.start()
        sections["item1a"] = text[start : start + 30_000]

    # Item 7 (MD&A)
    m7 = re.search(
        r"(?i)(?:^|\n)\s*(?:ITEM|Item)\s+7[\.\s:]+",
        text,
    )
    if m7:
        start = m7.start()
        sections["item7"] = text[start : start + 20_000]

    if not sections:
        sections["full_text"] = text[:90_000]

    return sections


# ── Date-based 10-K discovery ───────────────────────────────────────

def discover_10k_filers(date_str: str) -> list[dict]:
    """Find all 10-K filings on a given date via EDGAR full-text search.

    Args:
        date_str: 'YYYY-MM-DD' format

    Returns list of {ticker, company_name, cik, filing_date, filing_url, accession_number}.
    """
    url = "https://efts.sec.gov/LATEST/search-index"
    seen_accessions = set()
    results = []

    for page in range(20):  # up to 1000 results
        params = {
            "q": '"10-K"',
            "forms": "10-K",
            "dateRange": "custom",
            "startdt": date_str,
            "enddt": date_str,
            "from": page * 50,
            "size": 50,
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            time.sleep(RATE_LIMIT)
            data = resp.json()
        except Exception as e:
            log.warning("EFTS search page %d failed: %s", page, e)
            break

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            source = hit.get("_source", {})
            file_type = source.get("file_type", "")
            if file_type != "10-K":
                continue

            adsh = source.get("adsh", "")
            if adsh in seen_accessions:
                continue
            seen_accessions.add(adsh)

            # Extract ticker from display_names like "Company Name (TICK)"
            display_names = source.get("display_names", [])
            ticker = None
            company_name = None
            if display_names:
                company_name = display_names[0]
                ticker_match = re.search(r"\(([A-Z]{1,5})\)", display_names[0])
                if ticker_match:
                    ticker = ticker_match.group(1)

            if not ticker:
                continue  # Skip non-public companies without ticker

            # Look up CIK for this ticker
            cik_info = lookup_cik(ticker)
            if not cik_info:
                log.warning("No CIK found for ticker %s, skipping", ticker)
                continue

            cik = cik_info["cik_str"]
            company_title = cik_info["title"]

            # Get the actual 10-K filing URL
            filing_info = get_latest_10k(cik)
            if not filing_info:
                log.warning("No 10-K filing found for %s (CIK %s)", ticker, cik)
                continue

            results.append({
                "ticker": ticker,
                "company_name": normalize_company_name(company_title),
                "cik": cik,
                "filing_date": filing_info["filing_date"],
                "filing_url": filing_info["filing_url"],
                "accession_number": filing_info["accession_number"],
                "form": filing_info["form"],
            })

    log.info("Discovered %d 10-K filers for %s", len(results), date_str)
    return results
