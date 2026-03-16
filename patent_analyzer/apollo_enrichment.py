"""Apollo.io email enrichment for board members and officers."""

import re
import time
import logging
import requests

log = logging.getLogger(__name__)

APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match"
RATE_LIMIT = 0.3  # seconds between Apollo calls

# Known ticker -> domain mappings for major companies
_KNOWN_DOMAINS = {
    "AAPL": "apple.com", "MSFT": "microsoft.com", "GOOG": "google.com",
    "GOOGL": "google.com", "AMZN": "amazon.com", "META": "meta.com",
    "NVDA": "nvidia.com", "TSLA": "tesla.com", "INTC": "intel.com",
    "AMD": "amd.com", "QCOM": "qualcomm.com", "AVGO": "broadcom.com",
    "IBM": "ibm.com", "ORCL": "oracle.com", "CRM": "salesforce.com",
    "ADBE": "adobe.com", "CSCO": "cisco.com", "TXN": "ti.com",
    "NFLX": "netflix.com", "PFE": "pfizer.com", "JNJ": "jnj.com",
    "MRK": "merck.com", "ABBV": "abbvie.com", "LLY": "lilly.com",
    "BMY": "bms.com", "GILD": "gilead.com", "AMGN": "amgen.com",
    "BIIB": "biogen.com", "REGN": "regeneron.com", "VRTX": "vrtx.com",
    "BA": "boeing.com", "LMT": "lockheedmartin.com", "GE": "ge.com",
    "CAT": "cat.com", "HON": "honeywell.com", "MMM": "3m.com",
    "XOM": "exxonmobil.com", "CVX": "chevron.com", "COP": "conocophillips.com",
    "WMT": "walmart.com", "HD": "homedepot.com", "COST": "costco.com",
    "DIS": "disney.com", "CMCSA": "comcast.com", "T": "att.com",
    "VZ": "verizon.com", "JPM": "jpmchase.com", "BAC": "bofa.com",
    "GS": "gs.com", "MS": "morganstanley.com", "C": "citi.com",
    "IDCC": "interdigital.com", "IRBT": "irobot.com",
}


def _infer_domain(company_name: str, ticker: str) -> str:
    """Infer company domain from ticker or company name."""
    if ticker and ticker.upper() in _KNOWN_DOMAINS:
        return _KNOWN_DOMAINS[ticker.upper()]

    # Clean company name: remove Inc., Corp., Ltd., etc.
    cleaned = company_name or ""
    for suffix in [
        "Inc.", "Corp.", "Co.", "Ltd.", "LLC", "LP", "PLC",
        "Holdings", "Group", "International", ",", "."
    ]:
        cleaned = cleaned.replace(suffix, "")
    cleaned = cleaned.strip().lower().replace(" ", "")
    return f"{cleaned}.com" if cleaned else "unknown.com"


def _parse_name(full_name: str) -> tuple:
    """Split name into (first_name, last_name) for Apollo matching.

    Handles initials like 'S. Douglas Hutcheson' -> ('Douglas', 'Hutcheson').
    """
    parts = full_name.strip().split()
    if len(parts) < 2:
        return (full_name, "")

    first = parts[0]
    # If first name looks like an initial (e.g. "S." or "J."), use second part
    if len(first) <= 2 or (len(first) == 2 and first.endswith(".")):
        if len(parts) >= 3:
            return (parts[1], parts[-1])

    return (parts[0], parts[-1])


def _get_apollo_key() -> str | None:
    """Load Apollo API key from Secret Manager."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = "projects/uspto-data-app/secrets/apollo-api-key/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        log.warning("Could not load Apollo API key from Secret Manager: %s", e)
        # Fallback to environment variable
        import os
        key = os.environ.get("APOLLO_API_KEY", "")
        if key:
            return key
        log.error("No Apollo API key available")
        return None


def _match_person(
    first_name: str,
    last_name: str,
    domain: str,
    company_name: str,
    api_key: str,
) -> dict:
    """Call Apollo people/match for one person.

    Returns {email, email_status} or {email: None}.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }
    body = {
        "first_name": first_name,
        "last_name": last_name,
        "domain": domain,
        "organization_name": company_name,
    }

    try:
        resp = requests.post(
            APOLLO_MATCH_URL, json=body, headers=headers, timeout=15
        )
        time.sleep(RATE_LIMIT)

        if resp.status_code != 200:
            log.warning(
                "Apollo returned %d for %s %s: %s",
                resp.status_code, first_name, last_name, resp.text[:200],
            )
            return {"email": None, "email_status": None}

        data = resp.json()
        person = data.get("person", {}) or {}
        return {
            "email": person.get("email"),
            "email_status": person.get("email_status"),
        }

    except Exception as e:
        log.warning("Apollo match failed for %s %s: %s", first_name, last_name, e)
        return {"email": None, "email_status": None}


def enrich_contacts(
    company_name: str,
    ticker: str,
    officers: dict,
) -> dict:
    """Look up email addresses for key contacts via Apollo.io.

    Enriches officers dict in-place with email/email_status fields.
    Only enriches secretary, general_counsel, board_chair, and up to 5 directors.

    Returns the updated officers dict.
    """
    api_key = _get_apollo_key()
    if not api_key:
        log.warning("Skipping Apollo enrichment — no API key")
        return officers

    domain = _infer_domain(company_name, ticker)
    log.info("Apollo enrichment for %s (domain: %s)", company_name, domain)

    # Build list of people to enrich (limit total API calls)
    people_to_enrich = []

    for role in ["secretary", "general_counsel", "board_chair"]:
        person = officers.get(role)
        if person and person.get("name"):
            people_to_enrich.append((role, person))

    # Add up to 5 directors
    for i, d in enumerate(officers.get("directors", [])[:5]):
        if d and d.get("name"):
            people_to_enrich.append((f"directors[{i}]", d))

    for role_key, person in people_to_enrich:
        first, last = _parse_name(person["name"])
        if not first or not last:
            continue

        result = _match_person(first, last, domain, company_name, api_key)
        person["email"] = result.get("email")
        person["email_status"] = result.get("email_status")

        if result.get("email"):
            log.info("  Found email for %s: %s (%s)",
                     person["name"], result["email"], result.get("email_status"))

    return officers
