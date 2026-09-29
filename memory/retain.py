"""
LedgerMind -- memory/retain.py
Save every accountant decision and agent action to Hindsight memory.

Four things are retained:
  1. Accountant decisions (APPROVE / REJECT / HOLD) with their notes
  2. Corrections / overturns  (most valuable -- stops mistakes repeating)
  3. Agent auto-approvals     (so the agent records its own behaviour)
  4. Vendor facts             (explicit facts the accountant tells the agent)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_client():
    from memory.client import get_hindsight_client
    return get_hindsight_client()


def _fmt_amount(paise: int) -> str:
    """Format integer paise as a readable rupee string, e.g. 4830100 -> 'Rs.48,301'."""
    rupees = paise / 100
    return f"Rs.{rupees:,.2f}"


def _issue_summary(issues: list) -> str:
    """Produce a short human-readable summary of a list of issues."""
    if not issues:
        return "no issues found"
    parts = []
    for issue in issues:
        # Support both CheckIssue dataclasses and plain dicts
        if hasattr(issue, "type"):
            itype = issue.type
            details = issue.details if hasattr(issue, "details") else {}
        else:
            itype = issue.get("type", "UNKNOWN")
            details = issue.get("details", {})

        if itype == "ROUNDING":
            diff = details.get("difference_rupees", "?")
            parts.append(f"ROUNDING Rs.{diff}")
        elif itype == "AMOUNT_MISMATCH":
            diff = details.get("difference_rupees", "?")
            parts.append(f"AMOUNT_MISMATCH Rs.{diff}")
        elif itype == "DUPLICATE":
            dup_type = details.get("duplicate_type", "")
            parts.append(f"DUPLICATE ({dup_type})")
        elif itype == "TAX_RATE_MISMATCH":
            inv_r = details.get("invoice_rate_pct", "?")
            po_r = details.get("po_rate_pct", "?")
            parts.append(f"TAX_RATE {po_r}%→{inv_r}%")
        elif itype == "TAX_TYPE_MISMATCH":
            parts.append(f"TAX_TYPE_MISMATCH ({details.get('actual_tax_type', '?')})")
        else:
            parts.append(itype)
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def retain_decision(
    bank_id: str,
    invoice_id: int,
    invoice_number: str,
    invoice_date: str,
    vendor_name: str,
    vendor_id: int,
    total_amount_paise: int,
    issues: list,
    action: str,           # APPROVE | REJECT | HOLD | OVERTURN | AUTO_APPROVE
    note: Optional[str],
    reviewer: Optional[str] = None,
    confidence: Optional[float] = None,
    memories_used: Optional[list] = None,
) -> bool:
    """
    Retain a human or agent decision to Hindsight memory.

    Returns True on success, False on failure (failures are logged but not raised
    so that a Hindsight outage never blocks the review workflow).
    """
    client = _get_client()
    issue_text = _issue_summary(issues)
    amount_text = _fmt_amount(total_amount_paise)

    # Build a rich plain-English memory sentence
    if action == "AUTO_APPROVE":
        who = "Agent"
        conf_text = f" (confidence {confidence:.0%})" if confidence is not None else ""
        content = (
            f"{vendor_name} invoice {invoice_number} ({amount_text}) had {issue_text}. "
            f"Agent AUTO-APPROVED{conf_text}."
        )
        if note:
            content += f" Reason: {note}"
        if memories_used:
            content += f" Based on memories: {', '.join(memories_used)}."
    elif action == "OVERTURN":
        who = reviewer or "Accountant"
        content = (
            f"{vendor_name} invoice {invoice_number} ({amount_text}) was AUTO-APPROVED "
            f"by the agent but OVERTURNED by {who}."
        )
        if note:
            content += f" Note: {note}"
        content += (
            f" Issues: {issue_text}. "
            "This correction is important -- do not auto-approve this issue type for "
            f"{vendor_name} without further evidence."
        )
    else:
        who = reviewer or "Accountant"
        # Build a grammatically correct verb form
        action_verb_map = {
            "APPROVE": "APPROVED",
            "REJECT":  "REJECTED",
            "HOLD":    "PUT ON HOLD",
        }
        action_past = action_verb_map.get(action, f"{action}ED")
        content = (
            f"{vendor_name} invoice {invoice_number} ({amount_text}) had {issue_text}. "
            f"{who} {action_past}."
        )
        if note:
            content += f" Note: '{note}'."

    metadata: dict = {
        "vendor_id": str(vendor_id),
        "vendor_name": vendor_name,
        "invoice_id": str(invoice_id),
        "invoice_number": invoice_number,
        "action": action,
        "issue_types": json.dumps([
            (i.type if hasattr(i, "type") else i.get("type", ""))
            for i in issues
        ]),
    }
    if note:
        metadata["note"] = note[:200]   # trim for metadata field limits

    # Parse invoice_date for the timestamp
    try:
        ts = datetime.fromisoformat(invoice_date).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    try:
        client.retain(
            bank_id=bank_id,
            content=content,
            context="invoice review decision",
            timestamp=ts,
            document_id=f"decision-{invoice_id}",
            metadata=metadata,
        )
        log.info(
            "Retained decision: invoice=%s vendor=%s action=%s",
            invoice_number, vendor_name, action,
        )
        return True
    except Exception as exc:
        log.error("Failed to retain decision for invoice %s: %s", invoice_number, exc)
        return False


def retain_vendor_fact(
    bank_id: str,
    vendor_id: int,
    vendor_name: str,
    fact: str,
    told_by: Optional[str] = None,
) -> bool:
    """
    Retain an explicit fact about a vendor told by the accountant.
    e.g. "Patel Electronics moved to 18% GST from April 2026"

    Returns True on success, False on failure.
    """
    client = _get_client()
    who = f"Told by {told_by}. " if told_by else ""
    content = (
        f"Vendor fact about {vendor_name}: {fact}. "
        f"{who}"
        f"This is a verified fact provided directly by the accountant."
    )

    try:
        client.retain(
            bank_id=bank_id,
            content=content,
            context="vendor fact",
            document_id=f"vendor-fact-{vendor_id}-{int(datetime.now().timestamp())}",
            metadata={
                "vendor_id": str(vendor_id),
                "vendor_name": vendor_name,
                "type": "vendor_fact",
            },
        )
        log.info("Retained vendor fact for %s", vendor_name)
        return True
    except Exception as exc:
        log.error("Failed to retain vendor fact for %s: %s", vendor_name, exc)
        return False
