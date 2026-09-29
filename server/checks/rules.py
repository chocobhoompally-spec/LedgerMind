"""
LedgerMind — GST Rule Checker

Deterministic checks run on every invoice before the AI is consulted.
The AI never decides *whether* an issue exists — only what to do about it.

All amounts are in integer paise (₹1 = 100 paise).

Returns a list of Issue-like dicts:
    [{"type": "ROUNDING", "details": {"difference_paise": 100}}, ...]

Keeping this as plain Python (no DB session required) so it is easy to unit-test
and can be called from both the pipeline and the demo script.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from server.checks.gstin import validate_gstin

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (read from environment with sensible defaults)
# ---------------------------------------------------------------------------

# Maximum rounding difference (paise) before it becomes a real mismatch.
# Default: ₹10 = 1000 paise
ROUNDING_LIMIT: int = int(os.getenv("ROUNDING_LIMIT", "10")) * 100  # paise

# Maximum amount difference (paise) between invoice and PO before flagging.
MAX_AUTO_APPROVE_DIFF: int = int(os.getenv("MAX_AUTO_APPROVE_DIFF", "100")) * 100


# ---------------------------------------------------------------------------
# Issue dataclass (mirrors the DB model, but no SQLAlchemy dependency here)
# ---------------------------------------------------------------------------

@dataclass
class CheckIssue:
    """A single GST issue found on an invoice."""
    type: str           # IssueType string, e.g. "ROUNDING"
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "details": self.details}


# ---------------------------------------------------------------------------
# Invoice input dataclass
# Keeps the rule checker decoupled from the ORM layer.
# ---------------------------------------------------------------------------

@dataclass
class InvoiceData:
    """
    Plain data container for a single invoice row.
    Amounts are in integer paise.
    Rates are percentage × 100 (e.g. 18% → 1800).
    """
    invoice_number:  str
    invoice_date:    str            # YYYY-MM-DD
    vendor_id:       int
    vendor_gstin:    str            # as printed on the invoice
    registered_gstin: str           # from the Vendor table
    taxable_amount:  int            # paise
    gst_rate:        int            # percentage × 100
    cgst:            int            # paise
    sgst:            int            # paise
    igst:            int            # paise
    total_amount:    int            # paise
    po_number:       Optional[str]  = None
    po_amount:       Optional[int]  = None   # paise; None if PO not found
    po_gst_rate:     Optional[int]  = None   # from PO; None if PO not found
    vendor_state_code: Optional[str] = None  # buyer's registered state code
    buyer_state_code:  Optional[str] = None  # buyer company's state code

    # Used for duplicate detection — caller provides existing invoices for this vendor.
    # Each entry: {"invoice_number": str, "total_amount": int, "invoice_date": str}
    existing_invoices: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_rounding(inv: InvoiceData) -> list[CheckIssue]:
    """
    ROUNDING — total differs from (taxable + tax) by between 1 paise and ROUNDING_LIMIT.
    A difference of 0 is clean; a difference > ROUNDING_LIMIT is AMOUNT_MISMATCH territory.
    """
    computed_tax = inv.cgst + inv.sgst + inv.igst
    computed_total = inv.taxable_amount + computed_tax
    difference = abs(inv.total_amount - computed_total)

    if 0 < difference <= ROUNDING_LIMIT:
        return [CheckIssue(
            type="ROUNDING",
            details={
                "invoice_total_paise": inv.total_amount,
                "computed_total_paise": computed_total,
                "difference_paise": difference,
                "difference_rupees": round(difference / 100, 2),
            }
        )]
    return []


def check_amount_mismatch(inv: InvoiceData) -> list[CheckIssue]:
    """
    AMOUNT_MISMATCH — invoice total differs from PO amount by more than ROUNDING_LIMIT.
    Only runs when a matching PO exists.
    """
    if inv.po_amount is None:
        return []   # MISSING_PO check handles the absent PO case

    difference = abs(inv.total_amount - inv.po_amount)
    if difference > ROUNDING_LIMIT:
        return [CheckIssue(
            type="AMOUNT_MISMATCH",
            details={
                "invoice_total_paise": inv.total_amount,
                "po_amount_paise": inv.po_amount,
                "difference_paise": difference,
                "difference_rupees": round(difference / 100, 2),
            }
        )]
    return []


def check_duplicate(inv: InvoiceData) -> list[CheckIssue]:
    """
    DUPLICATE — same vendor already has:
      (a) the exact same invoice number (exact duplicate), OR
      (b) the same total amount on the same date (likely duplicate).
    """
    issues = []
    for existing in inv.existing_invoices:
        # Exact duplicate: same vendor is implied (caller filters by vendor_id)
        if existing["invoice_number"] == inv.invoice_number:
            issues.append(CheckIssue(
                type="DUPLICATE",
                details={
                    "duplicate_type": "exact",
                    "matched_invoice_number": existing["invoice_number"],
                    "reason": "Same vendor, same invoice number",
                }
            ))
            break   # one exact match is enough

        # Soft duplicate: same amount, same date
        if (existing["total_amount"] == inv.total_amount
                and existing["invoice_date"] == inv.invoice_date):
            issues.append(CheckIssue(
                type="DUPLICATE",
                details={
                    "duplicate_type": "soft",
                    "matched_invoice_number": existing["invoice_number"],
                    "matched_date": existing["invoice_date"],
                    "matched_amount_paise": existing["total_amount"],
                    "reason": "Same vendor, same amount, same date",
                }
            ))
            break

    return issues


def check_invalid_gstin(inv: InvoiceData) -> list[CheckIssue]:
    """
    INVALID_GSTIN — the GSTIN printed on the invoice fails format or checksum.
    """
    result = validate_gstin(inv.vendor_gstin)
    if not result.valid:
        return [CheckIssue(
            type="INVALID_GSTIN",
            details={
                "gstin": inv.vendor_gstin,
                "error": result.error.value if result.error else "UNKNOWN",
                "error_message": result.error_message,
            }
        )]
    return []


def check_gstin_mismatch(inv: InvoiceData) -> list[CheckIssue]:
    """
    GSTIN_MISMATCH — invoice carries a valid GSTIN that differs from the
    one registered for this vendor in our database.
    """
    # If the invoice GSTIN is itself invalid, that's already captured by check_invalid_gstin.
    if not validate_gstin(inv.vendor_gstin).valid:
        return []

    if inv.vendor_gstin.strip().upper() != inv.registered_gstin.strip().upper():
        return [CheckIssue(
            type="GSTIN_MISMATCH",
            details={
                "invoice_gstin": inv.vendor_gstin,
                "registered_gstin": inv.registered_gstin,
                "reason": "GSTIN on invoice differs from vendor's registered GSTIN",
            }
        )]
    return []


def check_tax_rate_mismatch(inv: InvoiceData) -> list[CheckIssue]:
    """
    TAX_RATE_MISMATCH — GST rate on the invoice differs from the rate on the PO.
    Only runs when a matching PO exists.
    """
    if inv.po_gst_rate is None:
        return []

    if inv.gst_rate != inv.po_gst_rate:
        return [CheckIssue(
            type="TAX_RATE_MISMATCH",
            details={
                "invoice_rate": inv.gst_rate,       # e.g. 1800 = 18%
                "po_rate": inv.po_gst_rate,
                "invoice_rate_pct": inv.gst_rate / 100,
                "po_rate_pct": inv.po_gst_rate / 100,
                "reason": (
                    f"Invoice charges {inv.gst_rate / 100:.0f}% GST "
                    f"but PO specifies {inv.po_gst_rate / 100:.0f}%"
                ),
            }
        )]
    return []


def check_tax_type_mismatch(inv: InvoiceData) -> list[CheckIssue]:
    """
    TAX_TYPE_MISMATCH — wrong split of CGST/SGST vs IGST based on state codes.
    - Intra-state transaction: vendor and buyer are in the same state → must use CGST + SGST
    - Inter-state transaction: different states → must use IGST

    Only runs when both state codes are known.
    """
    if not inv.vendor_state_code or not inv.buyer_state_code:
        return []

    intra_state = inv.vendor_state_code == inv.buyer_state_code
    uses_igst   = inv.igst > 0
    uses_cgst_sgst = inv.cgst > 0 or inv.sgst > 0

    if intra_state and uses_igst and not uses_cgst_sgst:
        return [CheckIssue(
            type="TAX_TYPE_MISMATCH",
            details={
                "vendor_state": inv.vendor_state_code,
                "buyer_state": inv.buyer_state_code,
                "transaction_type": "intra-state",
                "expected_tax_type": "CGST + SGST",
                "actual_tax_type": "IGST",
                "reason": "Intra-state supply must use CGST + SGST, not IGST",
            }
        )]

    if not intra_state and uses_cgst_sgst and not uses_igst:
        return [CheckIssue(
            type="TAX_TYPE_MISMATCH",
            details={
                "vendor_state": inv.vendor_state_code,
                "buyer_state": inv.buyer_state_code,
                "transaction_type": "inter-state",
                "expected_tax_type": "IGST",
                "actual_tax_type": "CGST + SGST",
                "reason": "Inter-state supply must use IGST, not CGST + SGST",
            }
        )]

    return []


def check_missing_po(inv: InvoiceData) -> list[CheckIssue]:
    """
    MISSING_PO — invoice references a PO number, but that PO doesn't exist.
    """
    if inv.po_number and inv.po_amount is None:
        return [CheckIssue(
            type="MISSING_PO",
            details={
                "po_number": inv.po_number,
                "reason": f"PO '{inv.po_number}' referenced on invoice was not found",
            }
        )]
    return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_all_checks(inv: InvoiceData) -> list[CheckIssue]:
    """
    Run all GST checks on a single invoice.

    Returns a (possibly empty) list of CheckIssue objects.
    Checks are run in a fixed order so issue lists are deterministic.
    """
    issues: list[CheckIssue] = []

    issues.extend(check_invalid_gstin(inv))
    issues.extend(check_gstin_mismatch(inv))
    issues.extend(check_missing_po(inv))
    issues.extend(check_rounding(inv))
    issues.extend(check_amount_mismatch(inv))
    issues.extend(check_duplicate(inv))
    issues.extend(check_tax_rate_mismatch(inv))
    issues.extend(check_tax_type_mismatch(inv))

    return issues
