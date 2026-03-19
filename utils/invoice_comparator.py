"""Invoice vs Algorithm comparison for prosecution fee calibration.

Compares structured data extracted from USPTO payment invoice PDFs against
the algorithm-calculated payment events from fee_schedule.py to identify
gaps, mismatches, and missing event codes.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional


def compare_invoice_to_algorithm(
    invoice_extractions: List[dict],
    algorithm_payments: List[dict],
) -> Dict[str, Any]:
    """Compare invoice-extracted fee data against algorithm-calculated payments.

    Args:
        invoice_extractions: List of Gemini-extracted records from PDFs.
            Each has: entity_status, fees (list of fee lines), total_amount.
        algorithm_payments: List of enriched payment dicts from
            calculate_payment_fees(). Each has: d, c, desc, status, cat,
            paid, large, delta.

    Returns:
        Comparison result dict with match counts, mismatches, totals, and
        human-readable notes.
    """
    notes: List[str] = []

    # ── Flatten invoice fees into a comparable list ──────────────
    invoice_fees: List[dict] = []
    invoice_entity_statuses: set = set()
    invoice_total = 0.0

    for ext in invoice_extractions:
        ent_status = (ext.get("entity_status") or "").upper()
        if ent_status in ("SMALL", "LARGE", "MICRO"):
            invoice_entity_statuses.add(ent_status)

        fees = ext.get("fees") or []
        if isinstance(fees, str):
            try:
                fees = json.loads(fees)
            except (json.JSONDecodeError, TypeError):
                fees = []

        for fee in fees:
            if not isinstance(fee, dict):
                continue
            amount = _parse_amount(fee.get("amount") or fee.get("item_total"))
            invoice_fees.append({
                "description": (fee.get("description") or "").strip(),
                "fee_code": fee.get("fee_code"),
                "amount": amount,
                "source_entity_status": ent_status,
            })

        ext_total = _parse_amount(ext.get("total_amount"))
        if ext_total:
            invoice_total += ext_total

    # ── Build algorithm summary ─────────────────────────────────
    algo_pay_events = [p for p in algorithm_payments if p.get("cat")]
    algo_entity_statuses: set = set()
    algo_total = 0.0

    for p in algo_pay_events:
        s = (p.get("status") or "").upper()
        if s in ("SMALL", "LARGE", "MICRO"):
            algo_entity_statuses.add(s)
        algo_total += p.get("paid", 0) or 0

    # ── Entity status comparison ────────────────────────────────
    status_matches = len(invoice_entity_statuses & algo_entity_statuses)
    status_mismatches = len(invoice_entity_statuses ^ algo_entity_statuses)
    if invoice_entity_statuses and algo_entity_statuses:
        if invoice_entity_statuses == algo_entity_statuses:
            notes.append(f"Entity status agrees: {', '.join(sorted(invoice_entity_statuses))}")
        else:
            notes.append(
                f"Entity status MISMATCH — invoice: {', '.join(sorted(invoice_entity_statuses))}, "
                f"algorithm: {', '.join(sorted(algo_entity_statuses))}"
            )

    # ── Fee count comparison ────────────────────────────────────
    missing_in_algorithm = 0
    missing_in_invoice = 0

    # Simple count-based comparison (detailed matching requires dates
    # which invoices may not always provide per fee line)
    inv_count = len(invoice_fees)
    algo_count = len(algo_pay_events)

    if inv_count > algo_count:
        missing_in_algorithm = inv_count - algo_count
        notes.append(
            f"Invoice has {missing_in_algorithm} more fee line(s) than algorithm "
            f"({inv_count} vs {algo_count}) — algorithm may be missing events"
        )
    elif algo_count > inv_count:
        missing_in_invoice = algo_count - inv_count
        notes.append(
            f"Algorithm has {missing_in_invoice} more event(s) than invoice "
            f"({algo_count} vs {inv_count}) — possible duplicates in algorithm"
        )
    else:
        notes.append(f"Fee count matches: {inv_count} items on both sides")

    # ── Dollar amount comparison ────────────────────────────────
    amount_difference = round(algo_total - invoice_total, 2)
    if invoice_total > 0:
        if abs(amount_difference) < 1.0:
            notes.append(f"Total amounts match: ${invoice_total:,.2f}")
        else:
            notes.append(
                f"Total amount DIFFERENCE: algorithm=${algo_total:,.2f}, "
                f"invoice=${invoice_total:,.2f}, diff=${amount_difference:+,.2f}"
            )

    # ── Invoice fee descriptions (for learning new patterns) ────
    inv_descriptions = [f.get("description", "") for f in invoice_fees if f.get("description")]
    if inv_descriptions:
        notes.append(f"Invoice fee descriptions: {'; '.join(inv_descriptions[:10])}")

    # ── Algorithm event codes summary ───────────────────────────
    algo_codes = [p.get("c", "") for p in algo_pay_events]
    if algo_codes:
        notes.append(f"Algorithm event codes: {', '.join(algo_codes)}")

    return {
        "status_matches": status_matches,
        "status_mismatches": status_mismatches,
        "missing_in_algorithm": missing_in_algorithm,
        "missing_in_invoice": missing_in_invoice,
        "total_algorithm_amount": round(algo_total, 2),
        "total_invoice_amount": round(invoice_total, 2),
        "amount_difference": amount_difference,
        "invoice_fee_count": inv_count,
        "algorithm_event_count": algo_count,
        "invoice_entity_statuses": sorted(invoice_entity_statuses),
        "algorithm_entity_statuses": sorted(algo_entity_statuses),
        "notes": "\n".join(notes),
    }


def _parse_amount(val) -> float:
    """Parse a dollar amount from various formats."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Remove $, commas, whitespace
        cleaned = val.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
    return 0.0
