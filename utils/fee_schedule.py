"""USPTO prosecution fee schedule — rates by period, entity size, and fee category.

Provides lookup functions that map (event_code, date, entity_status) → dollar amount.
All rates are from the official USPTO fee schedule, verified by patent expert.

Fee schedule references (37 CFR):
  processing_fee      — 1.17(i)(1)
  basic_filing         — 1.16(a)
  rce_first            — 1.17(e)(1)  1st RCE per application
  rce_second_plus      — 1.17(e)(2)  2nd+ RCE per application (introduced 2013)
  notice_of_appeal     — 1.17(b)(1)
  appeal_brief         — 1.17(b)(2)  eliminated post-2012
  ptab_oral_hearing    — 1.17(d)
  ids_fee              — 1.17(p)     conditional — only if filed after Final OA / NOA
  utility_issue_fee    — 1.18(a)
  terminal_disclaimer  — 1.20(d)
  petition_revival     — 1.17(m)
  extension_1mo        — 1.17(a)(1)
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

# ── Fee schedule periods (effective dates, ascending) ──────────────
FEE_PERIODS: List[date] = [
    date(2006, 10, 1),    # 0: FY 2007 CPI-U adjustment
    date(2011, 9, 26),    # 1: AIA 15% surcharge
    date(2013, 3, 19),    # 2: AIA implementation — micro entity introduced
    date(2018, 1, 16),    # 3: Comprehensive fee setting
    date(2020, 10, 2),    # 4: Post-AIA reset
    date(2022, 12, 29),   # 5: UAIA — Small 50%→60%, Micro 75%→80%
    date(2025, 1, 19),    # 6: Most recent increase
]

# ── Fee rates: FEE_RATES[period_index][category] = (large, small, micro) ──
# Pre-2013 (periods 0-1): micro entity didn't exist; micro = small.
# Pre-2013 (periods 0-1): no RCE tier distinction; rce_second_plus = rce_first.
FEE_RATES: Dict[int, Dict[str, Tuple[int, int, int]]] = {
    # ── Period 0: Oct 1, 2006 ──
    0: {
        "processing_fee":      (400, 200, 200),
        "basic_filing":        (300, 150, 150),
        "rce_first":           (790, 395, 395),
        "rce_second_plus":     (790, 395, 395),   # no distinction pre-2013
        "notice_of_appeal":    (500, 250, 250),
        "appeal_brief":        (500, 250, 250),
        "ptab_oral_hearing":   (1000, 500, 500),
        "ids_fee":             (180, 90, 90),
        "utility_issue_fee":   (1400, 700, 700),
        "terminal_disclaimer": (160, 80, 80),
        "petition_revival":    (1500, 750, 750),
        "extension_1mo":       (120, 60, 60),
    },
    # ── Period 1: Sep 26, 2011 ──
    1: {
        "processing_fee":      (400, 200, 200),
        "basic_filing":        (330, 165, 165),
        "rce_first":           (810, 405, 405),
        "rce_second_plus":     (810, 405, 405),   # no distinction pre-2013
        "notice_of_appeal":    (540, 270, 270),
        "appeal_brief":        (540, 270, 270),
        "ptab_oral_hearing":   (1080, 540, 540),
        "ids_fee":             (180, 90, 90),
        "utility_issue_fee":   (1510, 755, 755),
        "terminal_disclaimer": (160, 80, 80),
        "petition_revival":    (1500, 750, 750),
        "extension_1mo":       (130, 65, 65),
    },
    # ── Period 2: Mar 19, 2013 (AIA — micro entity introduced) ──
    2: {
        "processing_fee":      (400, 200, 100),
        "basic_filing":        (280, 140, 70),
        "rce_first":           (1200, 600, 300),
        "rce_second_plus":     (1700, 850, 425),
        "notice_of_appeal":    (800, 400, 200),
        "appeal_brief":        (0, 0, 0),          # eliminated
        "ptab_oral_hearing":   (1200, 600, 300),
        "ids_fee":             (180, 90, 45),
        "utility_issue_fee":   (960, 480, 240),
        "terminal_disclaimer": (160, 80, 40),
        "petition_revival":    (1600, 800, 400),
        "extension_1mo":       (200, 100, 50),
    },
    # ── Period 3: Jan 16, 2018 ──
    3: {
        "processing_fee":      (400, 200, 100),
        "basic_filing":        (300, 150, 75),
        "rce_first":           (1300, 650, 325),
        "rce_second_plus":     (1900, 950, 475),
        "notice_of_appeal":    (800, 400, 200),
        "appeal_brief":        (0, 0, 0),
        "ptab_oral_hearing":   (1300, 650, 325),
        "ids_fee":             (240, 120, 60),
        "utility_issue_fee":   (1000, 500, 250),
        "terminal_disclaimer": (160, 80, 40),
        "petition_revival":    (2000, 1000, 500),
        "extension_1mo":       (200, 100, 50),
    },
    # ── Period 4: Oct 2, 2020 ──
    4: {
        "processing_fee":      (400, 200, 100),
        "basic_filing":        (320, 160, 80),
        "rce_first":           (1360, 680, 340),
        "rce_second_plus":     (2260, 1130, 565),
        "notice_of_appeal":    (800, 400, 200),
        "appeal_brief":        (0, 0, 0),
        "ptab_oral_hearing":   (1300, 650, 325),
        "ids_fee":             (260, 130, 65),
        "utility_issue_fee":   (1200, 600, 300),
        "terminal_disclaimer": (170, 85, 42),
        "petition_revival":    (2100, 1050, 525),
        "extension_1mo":       (220, 110, 55),
    },
    # ── Period 5: Dec 29, 2022 (UAIA — discount shift) ──
    5: {
        "processing_fee":      (400, 160, 80),
        "basic_filing":        (320, 128, 64),
        "rce_first":           (1360, 544, 272),
        "rce_second_plus":     (2260, 904, 452),
        "notice_of_appeal":    (800, 320, 160),
        "appeal_brief":        (0, 0, 0),
        "ptab_oral_hearing":   (1300, 520, 260),
        "ids_fee":             (260, 104, 52),
        "utility_issue_fee":   (1200, 480, 240),
        "terminal_disclaimer": (170, 68, 34),
        "petition_revival":    (2100, 840, 420),
        "extension_1mo":       (220, 88, 44),
    },
    # ── Period 6: Jan 19, 2025 ──
    6: {
        "processing_fee":      (420, 168, 84),
        "basic_filing":        (350, 140, 70),
        "rce_first":           (1500, 600, 300),
        "rce_second_plus":     (2860, 1144, 572),
        "notice_of_appeal":    (845, 338, 169),
        "appeal_brief":        (0, 0, 0),
        "ptab_oral_hearing":   (1360, 544, 272),
        "ids_fee":             (280, 112, 56),
        "utility_issue_fee":   (1290, 516, 258),
        "terminal_disclaimer": (170, 68, 34),
        "petition_revival":    (2200, 880, 440),
        "extension_1mo":       (235, 94, 47),
    },
}

# ── PAY code → fee category mapping ───────────────────────────────
# Only codes that trigger actual payments. Everything else is PROC.
CODE_TO_CATEGORY: Dict[str, str] = {
    # RCE — resolved to rce_first or rce_second_plus at runtime
    "RCEX": "rce",
    "QRCE": "rce",
    # Issue Fee
    "IFEE": "utility_issue_fee",
    "IFEEHA": "utility_issue_fee",
    # Compound: P005 = issue fee + petition for revival
    "P005": "compound_p005",
    # Petition
    "P007": "processing_fee",      # $420 proxy (expert: 1.17(h) petition rate)
    "ODPET4": "petition_revival",
    # Appeal
    "N/AP": "notice_of_appeal",
    "N/AP-NOA": "notice_of_appeal",
    "APFC": "notice_of_appeal",
    "APOH": "ptab_oral_hearing",
    # Appeal Brief ($0 post-2012, $540 pre-2013)
    "AP.B": "appeal_brief",
    # Filing
    "A371": "basic_filing",
    "ADDFLFEE": "basic_filing",
    "FLFEE": "basic_filing",
    # Extension of time (1st month tier)
    "XT/G": "extension_1mo",
    "JA94": "extension_1mo",
    "JA95": "extension_1mo",
    "RXRQ/T": "extension_1mo",
    # IDS (conditional — only PAY if filed after CTFR/NOA)
    "IDS.": "ids_fee",
    "IDSPTA": "ids_fee",
    # Terminal Disclaimer
    "TDP": "terminal_disclaimer",
    # Processing / misc PAY
    "FEE.": "processing_fee",     # only counted if no other PAY within ±3 days
    "PFP": "processing_fee",
    "RETF": "processing_fee",
    "PMFP": "processing_fee",
}

# ── REV codes (reversals — subtract from totals) ──────────────────
REV_CODES: Dict[str, str] = {
    "MODPD28": "utility_issue_fee",
    "ODPD28": "utility_issue_fee",
    "RVIFEEHA": "utility_issue_fee",
    "VFEE": "utility_issue_fee",
}

# ── IDS trigger codes (IDS is only PAY if preceded by one of these) ──
IDS_TRIGGER_CODES: Set[str] = {"CTFR", "MS95", "NOA", "MAILNOA", "D.ISS"}

# ── RCE ordinal tracking codes (ABN9 counts for ordinal but is NOT PAY) ──
RCE_ORDINAL_CODES: Set[str] = {"RCEX", "QRCE", "ABN9"}

# ── AIA date: before this, no RCE tier distinction ────────────────
_AIA_RCE_TIER_DATE = date(2013, 3, 19)


# ── Lookup functions ──────────────────────────────────────────────

def get_period_index(payment_date: date) -> int:
    """Return the fee schedule period index for a given date.

    Uses the most recent period whose effective date ≤ payment_date.
    For dates before Oct 1, 2006, returns 0 (earliest available).
    """
    idx = 0
    for i, eff in enumerate(FEE_PERIODS):
        if payment_date >= eff:
            idx = i
        else:
            break
    return idx


def get_fee(category: str, payment_date: date, entity_status: str) -> int:
    """Return the dollar amount for a fee category/date/entity_status.

    Args:
        category: Fee category key (e.g., 'rce_first', 'utility_issue_fee')
        payment_date: Date the payment was made
        entity_status: 'LARGE', 'SMALL', or 'MICRO'

    Returns:
        Dollar amount as integer. Returns 0 if category unknown.
    """
    period = get_period_index(payment_date)
    rates = FEE_RATES.get(period, {}).get(category)
    if not rates:
        return 0

    large, small, micro = rates
    status = entity_status.upper() if entity_status else "LARGE"
    if status == "SMALL":
        return small
    elif status == "MICRO":
        # Micro didn't exist before AIA (period 2). Pre-AIA micro = small.
        if period < 2:
            return small
        return micro
    else:
        return large


def calculate_payment_fees(
    payments: List[dict],
    all_events: List[dict],
) -> List[dict]:
    """Enrich payment records with fee amounts.

    This is the main fee calculation engine. It applies all expert-verified
    rules: RCE ordinal counter, P005 compound fees, FEE. ±3-day dedup,
    IDS conditional check, same-category dedup, and REV code handling.

    Args:
        payments: List of payment dicts with keys: d (ISO date str),
                  c (event code), desc (description), status (entity status).
        all_events: Full list of events for this application (same structure
                    as payments, includes IDS trigger codes).

    Returns:
        Enriched payment list. Each dict gains:
          cat: fee category string or None (for PROC)
          paid: dollar amount paid at the entity's rate
          large: dollar amount at Large rate
          delta: large - paid (positive = underpayment)
    """
    if not payments:
        return payments

    # ── Step 1: Build RCE ordinal map ─────────────────────────────
    # Track distinct dates where RCE-related codes appear.
    # First date = ordinal 1, second distinct date = ordinal 2, etc.
    rce_dates_ordered: List[date] = []
    for ev in all_events:
        ev_date = _parse_date(ev.get("date") or ev.get("d"))
        ev_code = ev.get("code") or ev.get("c", "")
        if ev_date and ev_code in RCE_ORDINAL_CODES:
            if not rce_dates_ordered or rce_dates_ordered[-1] != ev_date:
                rce_dates_ordered.append(ev_date)

    def _rce_ordinal(d: date) -> int:
        """Return ordinal (1-based) for an RCE event on date d."""
        for i, rd in enumerate(rce_dates_ordered):
            if rd == d:
                return i + 1
        return 1

    # ── Step 2: Build IDS trigger timeline ────────────────────────
    # Walk events chronologically; set trigger_seen = True when we
    # encounter CTFR, MS95, NOA, MAILNOA, or D.ISS.
    ids_trigger_by_date: Dict[date, bool] = {}
    trigger_seen = False
    for ev in all_events:
        ev_date = _parse_date(ev.get("date") or ev.get("d"))
        ev_code = ev.get("code") or ev.get("c", "")
        if ev_date and ev_code in IDS_TRIGGER_CODES:
            trigger_seen = True
        if ev_date:
            ids_trigger_by_date[ev_date] = trigger_seen

    # ── Step 3: Build PAY date set for FEE. dedup ─────────────────
    # Collect dates of all PAY codes EXCEPT FEE. itself.
    other_pay_dates: Set[date] = set()
    for ev in all_events:
        ev_date = _parse_date(ev.get("date") or ev.get("d"))
        ev_code = ev.get("code") or ev.get("c", "")
        if ev_date and ev_code in CODE_TO_CATEGORY and ev_code != "FEE.":
            other_pay_dates.add(ev_date)

    def _fee_dot_has_adjacent(d: date) -> bool:
        """Check if any other PAY code exists within ±3 days of date d."""
        for delta_days in range(-3, 4):
            check = d + timedelta(days=delta_days)
            if check in other_pay_dates:
                return True
        return False

    # ── Step 4: Process each payment ──────────────────────────────
    seen_cat_date: Set[Tuple[str, date]] = set()
    result: List[dict] = []

    for pay in payments:
        pay_date = _parse_date(pay.get("d"))
        pay_code = pay.get("c", "")
        pay_status = pay.get("status", "LARGE")

        # Start with a copy, add fee fields
        enriched = dict(pay)
        enriched["cat"] = None
        enriched["paid"] = 0
        enriched["large"] = 0
        enriched["delta"] = 0

        if not pay_date:
            result.append(enriched)
            continue

        is_rev = pay_code in REV_CODES
        is_pay = pay_code in CODE_TO_CATEGORY

        if not is_pay and not is_rev:
            # PROC code — no fee
            result.append(enriched)
            continue

        # ── IDS conditional check ─────────────────────────────────
        if pay_code in ("IDS.", "IDSPTA"):
            if not ids_trigger_by_date.get(pay_date, False):
                # IDS filed before CTFR/NOA — free, treat as PROC
                result.append(enriched)
                continue

        # ── FEE. dedup check (±3-day window) ──────────────────────
        if pay_code == "FEE.":
            if _fee_dot_has_adjacent(pay_date):
                # Another PAY code nearby — FEE. is redundant
                result.append(enriched)
                continue

        # ── Determine fee category ────────────────────────────────
        if is_rev:
            category = REV_CODES[pay_code]
        else:
            raw_cat = CODE_TO_CATEGORY[pay_code]

            if raw_cat == "rce":
                ordinal = _rce_ordinal(pay_date)
                if pay_date < _AIA_RCE_TIER_DATE or ordinal == 1:
                    category = "rce_first"
                else:
                    category = "rce_second_plus"
            elif raw_cat == "compound_p005":
                category = "compound_p005"
            else:
                category = raw_cat

        # ── Dedup: same category + same date = 1 payment ──────────
        dedup_key = (category, pay_date)
        if dedup_key in seen_cat_date:
            # Duplicate — already counted this fee on this date
            result.append(enriched)
            continue
        seen_cat_date.add(dedup_key)

        # ── Compute dollar amounts ────────────────────────────────
        if category == "compound_p005":
            # P005 = issue fee + petition for revival
            issue_paid = get_fee("utility_issue_fee", pay_date, pay_status)
            petition_paid = get_fee("petition_revival", pay_date, pay_status)
            issue_large = get_fee("utility_issue_fee", pay_date, "LARGE")
            petition_large = get_fee("petition_revival", pay_date, "LARGE")
            amount_paid = issue_paid + petition_paid
            large_rate = issue_large + petition_large
        elif is_rev:
            amount_paid = -get_fee(category, pay_date, pay_status)
            large_rate = -get_fee(category, pay_date, "LARGE")
        else:
            amount_paid = get_fee(category, pay_date, pay_status)
            large_rate = get_fee(category, pay_date, "LARGE")

        delta = large_rate - amount_paid  # positive = underpayment

        enriched["cat"] = category
        enriched["paid"] = amount_paid
        enriched["large"] = large_rate
        enriched["delta"] = delta

        result.append(enriched)

    return result


# ── Helpers ───────────────────────────────────────────────────────

def _parse_date(val) -> Optional[date]:
    """Parse a date from various formats."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str) and len(val) >= 10:
        try:
            return date.fromisoformat(val[:10])
        except ValueError:
            return None
    return None
