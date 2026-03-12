"""Classify patent assignment conveyance text into standard categories.

Uses keyword matching on the conveyance_text field from USPTO PADX XML data
to categorize each assignment transaction.

Categories:
    ASSIGNMENT         — Standard ownership transfer
    SECURITY_INTEREST  — Patent used as loan collateral
    MERGER             — Corporate restructuring / name change
    RELEASE            — Releasing a security interest or lien
    LICENSE            — Granting usage rights
    GOVERNMENT_INTEREST — Federal funding obligations
    CORRECTION         — Fixing a previous filing
    OTHER              — Anything that doesn't match above
"""

import re

# Patterns are checked in order — first match wins.
# Each tuple is (category, compiled_regex).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    # CORRECTION must come before ASSIGNMENT because corrective assignments
    # contain the word "assignment" but should be classified as corrections.
    ("CORRECTION", re.compile(
        r"correct|nunc pro tunc|erron", re.IGNORECASE
    )),
    # RELEASE must come before SECURITY_INTEREST because releases of
    # security interests contain the word "security".
    ("RELEASE", re.compile(
        r"release|termination of|lien release|discharge", re.IGNORECASE
    )),
    ("SECURITY_INTEREST", re.compile(
        r"security agreement|security interest|collateral|pledge|lien|"
        r"mortgage|secured party", re.IGNORECASE
    )),
    ("MERGER", re.compile(
        r"merger|change of name|name change|conversion|"
        r"certificate of .*(?:merger|conversion)", re.IGNORECASE
    )),
    ("LICENSE", re.compile(
        r"licen[cs]e|licensing", re.IGNORECASE
    )),
    ("GOVERNMENT_INTEREST", re.compile(
        r"government interest|subject to licen|government rights|"
        r"confirmatory licen|rights of the government", re.IGNORECASE
    )),
    ("ASSIGNMENT", re.compile(
        r"assign|convey|transfer|sell|grant", re.IGNORECASE
    )),
]


def classify_conveyance(text: str | None) -> str:
    """Classify a conveyance_text string into a standard category.

    Args:
        text: The conveyance_text from a patent assignment record.

    Returns:
        One of: ASSIGNMENT, SECURITY_INTEREST, MERGER, RELEASE, LICENSE,
        GOVERNMENT_INTEREST, CORRECTION, OTHER.
    """
    if not text:
        return "OTHER"

    for category, pattern in _PATTERNS:
        if pattern.search(text):
            return category

    return "OTHER"
