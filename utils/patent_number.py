#!/usr/bin/env python3
"""Patent number normalization utility.

Normalizes patent numbers from various formats into a canonical form:
  7654321       → 7654321
  US7654321     → 7654321
  US7,654,321   → 7654321
  US7654321B2   → 7654321  (strip kind code)
  RE49123       → RE49123  (preserve reissue prefix)
  D987654       → D987654  (preserve design prefix)
  PP33456       → PP33456  (preserve plant patent prefix)

Used by all parsers and API endpoints to ensure consistent patent number
format across the entire system.
"""

import re

# Patent type prefixes that must be preserved
_TYPE_PREFIXES = ("RE", "D", "PP", "H", "T", "AI")

# Kind code pattern: one or two uppercase letters optionally followed by a digit
# Examples: B1, B2, A, A1, S, S1, E, E1
_KIND_CODE_RE = re.compile(r"[A-Z][A-Z0-9]?$")


def normalize_patent_number(raw: str) -> str | None:
    """Normalize a patent number to canonical form.

    Args:
        raw: Raw patent number string in any common format.

    Returns:
        Normalized patent number string, or None if input is empty/invalid.
    """
    if not raw:
        return None

    s = str(raw).strip().upper()
    if not s:
        return None

    # Strip "US" country prefix
    if s.startswith("US"):
        s = s[2:]

    # Strip commas
    s = s.replace(",", "")

    # Strip leading/trailing whitespace again after prefix removal
    s = s.strip()

    if not s:
        return None

    # Check for type prefix (RE, D, PP, H, T, AI)
    prefix = ""
    for p in sorted(_TYPE_PREFIXES, key=len, reverse=True):
        if s.startswith(p):
            prefix = p
            s = s[len(p):]
            break

    # Strip kind code suffix (e.g., B1, B2, A, A1, S, S1)
    # Only strip if what remains after stripping is still numeric
    if _KIND_CODE_RE.search(s):
        candidate = _KIND_CODE_RE.sub("", s)
        if candidate and candidate.isdigit():
            s = candidate

    # Strip leading zeros from numeric portion (utility patents)
    if s.isdigit():
        s = s.lstrip("0") or "0"

    if not s:
        return None

    return prefix + s


def normalize_doc_number(doc_number: str, country: str = "US") -> str | None:
    """Normalize a document number from XML citation data.

    Handles the doc-number field from PTBLXML citations, which may
    include country prefix or kind codes.

    Args:
        doc_number: The doc-number from XML.
        country: The country code (default "US"). Non-US citations return None.

    Returns:
        Normalized patent number, or None for non-US or invalid.
    """
    if country and country.upper() != "US":
        return None
    return normalize_patent_number(doc_number)


if __name__ == "__main__":
    # Quick self-test
    tests = [
        ("7654321", "7654321"),
        ("US7654321", "7654321"),
        ("US7,654,321", "7654321"),
        ("US7654321B2", "7654321"),
        ("RE49123", "RE49123"),
        ("D987654", "D987654"),
        ("PP33456", "PP33456"),
        ("H001234", "H1234"),
        ("0007654321", "7654321"),
        ("US12564117B2", "12564117"),
        ("D192388S", "D192388"),
        ("RE49123E1", "RE49123"),
        ("", None),
        (None, None),
    ]
    passed = 0
    for raw, expected in tests:
        result = normalize_patent_number(raw)
        status = "PASS" if result == expected else "FAIL"
        if status == "FAIL":
            print(f"  {status}: normalize_patent_number({raw!r}) = {result!r}, expected {expected!r}")
        else:
            passed += 1
    print(f"{passed}/{len(tests)} tests passed")
