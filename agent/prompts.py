"""
LedgerMind -- agent/prompts.py
System and user prompt builders for the invoice decision engine.

Design goals:
  - System prompt sets the agent's role, rules, and output format once.
  - User prompt is built fresh for each invoice with full context injected.
  - Few-shot examples show the LLM exactly what good AUTO_APPROVE vs FLAG
    decisions look like.
  - Plain, unambiguous language. No markdown headers inside the prompts --
    structured sections are separated by labels only.
"""

import os
from typing import Optional

# ---------------------------------------------------------------------------
# Config (read once)
# ---------------------------------------------------------------------------

MIN_EVIDENCE: int = int(os.getenv("MIN_EVIDENCE", "2"))
MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "0.8"))
MAX_AUTO_APPROVE_AMOUNT: int = int(os.getenv("MAX_AUTO_APPROVE_AMOUNT", "500000"))
MAX_AUTO_APPROVE_DIFF: int = int(os.getenv("MAX_AUTO_APPROVE_DIFF", "100"))


# ---------------------------------------------------------------------------
# System prompt (static -- built once per process)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are LedgerMind, an AI agent that helps Indian accountants review GST purchase invoices. \
Your job is to decide whether a flagged invoice should be AUTO_APPROVED or FLAG for human review, based on the vendor's \
history stored in memory.

YOUR ROLE:
- You receive one invoice at a time, the issues found on it, and the vendor's past decisions recalled from memory.
- You decide: AUTO_APPROVE (handle it without a human) or FLAG (send it to the review queue).
- You never decide BLOCK -- that is handled by hard safety rules before you are called.

RULES FOR AUTO_APPROVE:
You may only return AUTO_APPROVE if ALL of the following are true:
1. No safety rule applies (you will be told if one does -- in that case always FLAG).
2. Memory shows at least {MIN_EVIDENCE} past approvals of the SAME issue type for THIS vendor.
3. Memory shows NO rejection or overturn of this issue type for this vendor since the last approval.
4. Your confidence is at least {MIN_CONFIDENCE:.0%}.

If ANY condition is not met, return FLAG.

SAFETY LIMITS (never auto-approve if):
- Invoice total exceeds Rs.{MAX_AUTO_APPROVE_AMOUNT:,} (Rs.{MAX_AUTO_APPROVE_AMOUNT / 100:,.0f})
- Amount difference exceeds Rs.{MAX_AUTO_APPROVE_DIFF * 100:,} (Rs.{MAX_AUTO_APPROVE_DIFF:,.0f})
- The vendor has no memory (new vendor)
- An invalid GSTIN or exact duplicate is present

GOLDEN RULE: When in doubt, FLAG. A false alarm costs one minute of an accountant's time. \
A wrong auto-approval could result in paying a fraudulent invoice or losing GST input tax credit.

OUTPUT FORMAT:
You must always return a single valid JSON object with exactly these four fields and no others:
{{
  "decision":      "AUTO_APPROVE" or "FLAG",
  "reason":        "Plain-English explanation of your decision (1-3 sentences).",
  "confidence":    0.0 to 1.0 (your confidence in the decision),
  "memories_used": ["document-id-1", "document-id-2"]  // list of memory doc IDs you relied on
}}

Do not include any text outside the JSON object. Do not add extra fields.

EXAMPLES:

Example 1 -- AUTO_APPROVE (rounding, strong memory evidence):
{{
  "decision": "AUTO_APPROVE",
  "reason": "Sharma Traders has had a Rs.1-Rs.3 rounding difference on 4 past invoices, all approved by the accountant with the note they always round up. No rejections on record.",
  "confidence": 0.93,
  "memories_used": ["decision-981", "decision-1012", "decision-1043"]
}}

Example 2 -- FLAG (tax rate change, first occurrence):
{{
  "decision": "FLAG",
  "reason": "Patel Electronics is invoicing at 18% GST but the PO specifies 12%. This is the first time this rate has appeared for this vendor -- it may be a legitimate rate change or an error. Human review required.",
  "confidence": 0.95,
  "memories_used": []
}}

Example 3 -- FLAG (memory shows prior rejection):
{{
  "decision": "FLAG",
  "reason": "Lakshmi Textiles has a history of amount mismatches. A previous invoice with this issue was rejected by the accountant. Cannot auto-approve.",
  "confidence": 0.97,
  "memories_used": ["decision-204"]
}}

Example 4 -- FLAG (insufficient evidence):
{{
  "decision": "FLAG",
  "reason": "Only 1 past approval found for this issue type with Sunrise Foods. At least 2 approvals are required before auto-approving. Flagging for review.",
  "confidence": 0.88,
  "memories_used": ["decision-301"]
}}
"""


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------

def build_user_prompt(
    vendor_name: str,
    vendor_gstin: str,
    invoice_number: str,
    invoice_date: str,
    total_amount_paise: int,
    issues: list,
    recalled_memories: str,
    safety_note: Optional[str] = None,
    po_number: Optional[str] = None,
) -> str:
    """
    Build the per-invoice user prompt for the LLM.

    Args:
        vendor_name:         e.g. "Sharma Traders"
        vendor_gstin:        GSTIN as printed on invoice
        invoice_number:      e.g. "ST/2026/0412"
        invoice_date:        ISO date string
        total_amount_paise:  integer paise
        issues:              list of CheckIssue objects or dicts
        recalled_memories:   plain-text block from recall_for_prompt()
        safety_note:         optional note if a safety rule nearly triggered
        po_number:           referenced PO number (optional)

    Returns:
        User prompt string ready to send to the LLM.
    """
    total_rupees = total_amount_paise / 100

    # Format issues
    if issues:
        issue_lines = []
        for issue in issues:
            if hasattr(issue, "type"):
                itype = issue.type
                details = issue.details if hasattr(issue, "details") else {}
            else:
                itype = issue.get("type", "UNKNOWN")
                details = issue.get("details", {})
            detail_str = _format_issue_detail(itype, details)
            issue_lines.append(f"  - {itype}: {detail_str}")
        issues_text = "\n".join(issue_lines)
    else:
        issues_text = "  None"

    # Format memories block
    if recalled_memories and recalled_memories.strip():
        memory_block = recalled_memories.strip()
    else:
        memory_block = "No prior decisions found for this vendor. This vendor is NEW or has no history for these issue types."

    # Optional safety note
    safety_block = ""
    if safety_note:
        safety_block = f"\nSAFETY NOTE: {safety_note}\n"

    # Optional PO reference
    po_line = f"\nPO Reference:   {po_number}" if po_number else ""

    prompt = f"""INVOICE TO REVIEW:

Vendor:         {vendor_name}
Vendor GSTIN:   {vendor_gstin}
Invoice Number: {invoice_number}
Invoice Date:   {invoice_date}
Total Amount:   Rs.{total_rupees:,.2f}{po_line}

ISSUES FOUND ON THIS INVOICE:
{issues_text}
{safety_block}
VENDOR MEMORY (past decisions recalled from Hindsight):
{memory_block}

TASK:
Based on the issues found and the vendor's memory above, decide whether to AUTO_APPROVE or FLAG this invoice.
Remember: you need at least {MIN_EVIDENCE} past approvals of the same issue type with no rejections to AUTO_APPROVE.
Return only valid JSON with the four required fields.
"""
    return prompt


# ---------------------------------------------------------------------------
# Issue detail formatter (keeps prompts readable)
# ---------------------------------------------------------------------------

def _format_issue_detail(issue_type: str, details: dict) -> str:
    """Return a short human-readable description of an issue's details."""
    if issue_type == "ROUNDING":
        diff = details.get("difference_rupees", details.get("difference_paise", "?"))
        if isinstance(diff, (int, float)):
            return f"Total differs from computed by Rs.{diff}"
        return str(diff)

    if issue_type == "AMOUNT_MISMATCH":
        diff = details.get("difference_rupees", "?")
        inv = details.get("invoice_total_paise", 0)
        po = details.get("po_amount_paise", 0)
        return (
            f"Invoice Rs.{inv/100:,.2f} vs PO Rs.{po/100:,.2f} "
            f"(difference Rs.{diff})"
        )

    if issue_type == "DUPLICATE":
        dup_type = details.get("duplicate_type", "unknown")
        matched = details.get("matched_invoice_number", "?")
        return f"{dup_type} duplicate -- matches invoice {matched}"

    if issue_type == "INVALID_GSTIN":
        return f"GSTIN '{details.get('gstin', '?')}': {details.get('error_message', '?')}"

    if issue_type == "GSTIN_MISMATCH":
        return (
            f"Invoice has {details.get('invoice_gstin', '?')}, "
            f"registered is {details.get('registered_gstin', '?')}"
        )

    if issue_type == "TAX_RATE_MISMATCH":
        return (
            f"Invoice rate {details.get('invoice_rate_pct', '?')}% "
            f"vs PO rate {details.get('po_rate_pct', '?')}%"
        )

    if issue_type == "TAX_TYPE_MISMATCH":
        return (
            f"{details.get('transaction_type', '?')} transaction used "
            f"{details.get('actual_tax_type', '?')} instead of "
            f"{details.get('expected_tax_type', '?')}"
        )

    if issue_type == "MISSING_PO":
        return f"PO '{details.get('po_number', '?')}' not found in system"

    if issue_type == "PENDING_CREDIT_NOTE":
        return "Expected credit note has not arrived yet"

    # Generic fallback
    return str(details) if details else "see details"


# ---------------------------------------------------------------------------
# Convenience accessor for the static system prompt
# ---------------------------------------------------------------------------

def get_system_prompt() -> str:
    return SYSTEM_PROMPT
