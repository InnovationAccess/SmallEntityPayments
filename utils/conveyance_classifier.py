"""Classify patent assignment conveyance text into standard categories.

Uses keyword matching on the conveyance_text field from USPTO PADX XML data
to categorize each assignment transaction.

Original (coarse) categories:
    ASSIGNMENT         — Standard ownership transfer
    SECURITY_INTEREST  — Patent used as loan collateral
    MERGER             — Corporate restructuring / name change
    RELEASE            — Releasing a security interest or lien
    LICENSE            — Granting usage rights
    GOVERNMENT_INTEREST — Federal funding obligations
    CORRECTION         — Fixing a previous filing
    OTHER              — Anything that doesn't match above

Normalized (fine-grained) categories:
    divestiture        — Patent assets sold by assignor to assignee
    employee           — Inventors assigning to their employer
    correction         — Typo/error fix in a prior recordation (no new rights)
    name_change        — Entity name or legal form change (no ownership change)
    address_change     — Address update only
    license            — License granted under patent assets
    license_termination — License terminated
    security           — Security interest granted (loan collateral)
    release            — Security interest fully terminated
    partial_release    — Subset of collateralized assets released
    merger             — Acquirer takes target's assets (actual ownership change)
    government         — Government interest / confirmatory license (Bayh-Dole)
    assignment_pending — Text-level assignment; needs BQ inventor join to resolve
                         employee vs divestiture
"""

import re

# ---------------------------------------------------------------------------
# Original coarse classifier (unchanged, used for conveyance_type column)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Normalized (fine-grained) classifier — order matters, first match wins
# ---------------------------------------------------------------------------

_NORMALIZED_PATTERNS: list[tuple[str, bool, re.Pattern]] = [
    # (normalized_type, review_flag, regex)
    # Most specific patterns first

    # Address change — before name_change since both can have "change"
    ("address_change", False, re.compile(
        r"change of (?:assignee )?address|assignee change of address|address change",
        re.IGNORECASE)),

    # Name change — excluding mergers (handled separately)
    ("name_change", False, re.compile(
        r"change of name|entity conversion|certificate of conversion|"
        r"(?<!\w)conversion(?!\w)|name change",
        re.IGNORECASE)),

    # Merger — includes "MERGER AND CHANGE OF NAME"
    ("merger", False, re.compile(r"merger", re.IGNORECASE)),

    # Government interest — confirmatory licenses, executive orders
    ("government", False, re.compile(
        r"confirmatory licen|government interest|executive order|"
        r"rights of the government|subject to licen",
        re.IGNORECASE)),

    # Partial release — before release
    ("partial_release", False, re.compile(r"partial release", re.IGNORECASE)),

    # Release of security
    ("release", False, re.compile(
        r"release of security|release by secured|"
        r"termination.*security|discharge|(?<!\w)release(?!\w)",
        re.IGNORECASE)),

    # License termination — before license
    ("license_termination", False, re.compile(
        r"license termination|termination of.*license",
        re.IGNORECASE)),

    # License
    ("license", False, re.compile(r"licen[cs]e|licensing", re.IGNORECASE)),

    # Security interest
    ("security", False, re.compile(
        r"security interest|security agreement|patent security|"
        r"intellectual property security|grant of security|"
        r"collateral|pledge|mortgage|(?<!\w)lien(?!\w)|secured party",
        re.IGNORECASE)),

    # Pure corrections (not nunc pro tunc, not corrective assignments)
    ("correction", False, re.compile(
        r"(?:corrective|correction|erron)(?!.*assign)",
        re.IGNORECASE)),

    # Explicit employee text
    ("employee", False, re.compile(
        r"employment agreement|employee agreement|employment contract",
        re.IGNORECASE)),

    # Court order — ambiguous, flag for review
    ("divestiture", True, re.compile(r"court order", re.IGNORECASE)),
]

# Catch-all assignment pattern (needs BQ inventor join to resolve)
_ASSIGNMENT_PATTERN = re.compile(
    r"assign|convey|transfer|sell|grant|nunc pro tunc",
    re.IGNORECASE)


def classify_conveyance_normalized(text: str | None) -> tuple[str, bool]:
    """Classify conveyance_text into a fine-grained normalized type.

    For assignment-type texts (e.g., "ASSIGNMENT OF ASSIGNOR'S INTEREST"),
    returns 'assignment_pending' because determining employee vs divestiture
    requires a BigQuery join against pfw_inventors. The ETL pipeline handles
    this post-load step.

    Args:
        text: The conveyance_text from a patent assignment record.

    Returns:
        Tuple of (normalized_type, review_flag).
    """
    if not text:
        return ("divestiture", True)

    # Check specific patterns first
    for category, review, pattern in _NORMALIZED_PATTERNS:
        if pattern.search(text):
            return (category, review)

    # General assignment keywords -> needs inventor matching
    if _ASSIGNMENT_PATTERN.search(text):
        return ("assignment_pending", False)

    # Unknown text -> flag for review
    return ("divestiture", True)
