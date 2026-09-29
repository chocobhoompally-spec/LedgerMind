"""
LedgerMind — Safety Rules

These rules are enforced in code BEFORE the AI is ever called.
No AI response can bypass them — they are checked both before and after
the LLM call in the decision engine.

Return value:
    A SafetyResult with:
      - blocked: True  → invoice must be set to BLOCKED  (no AI, no human needed to re-verify rule)
      - flagged: True  → invoice must be set to FLAGGED  (AI skipped, goes to review queue)
      - rule: the name of the triggered rule (for audit trail)
      - reason: human-readable explanation

Design decision: safety rules check the *presence* of certain issue types in
the already-computed issue list, plus a few numeric thresholds. They do not
re-run the GST checks.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Thresholds (paise)
# ---------------------------------------------------------------------------

# Invoices above this total amount (paise) are always FLAGGED, never auto-approved.
# Default: ₹5,00,000 = 50000000 paise
MAX_AUTO_APPROVE_AMOUNT: int = int(os.getenv("MAX_AUTO_APPROVE_AMOUNT", "500000")) * 100

# Amount differences above this (paise) are always FLAGGED.
# Default: ₹100 = 10000 paise
MAX_AUTO_APPROVE_DIFF: int = int(os.getenv("MAX_AUTO_APPROVE_DIFF", "100")) * 100


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class SafetyResult:
    """Outcome of safety rule evaluation for one invoice."""
    blocked: bool = False
    flagged: bool = False
    rule: Optional[str] = None
    reason: Optional[str] = None

    @property
    def triggered(self) -> bool:
        return self.blocked or self.flagged

    def __repr__(self):
        if not self.triggered:
            return "<SafetyResult: PASS>"
        outcome = "BLOCKED" if self.blocked else "FLAGGED"
        return f"<SafetyResult: {outcome} rule={self.rule!r}>"


# ---------------------------------------------------------------------------
# Individual safety rules
# (evaluated in order; first match wins)
# ---------------------------------------------------------------------------

def _rule_invalid_gstin(issue_types: set[str]) -> Optional[SafetyResult]:
    """Invalid GSTIN on the invoice → always BLOCKED."""
    if "INVALID_GSTIN" in issue_types:
        return SafetyResult(
            blocked=True,
            rule="INVALID_GSTIN",
            reason="Invoice carries an invalid GSTIN. Cannot process until corrected.",
        )
    return None


def _rule_gstin_mismatch(issue_types: set[str]) -> Optional[SafetyResult]:
    """GSTIN on invoice doesn't match vendor's registered GSTIN → always BLOCKED."""
    if "GSTIN_MISMATCH" in issue_types:
        return SafetyResult(
            blocked=True,
            rule="GSTIN_MISMATCH",
            reason=(
                "GSTIN on the invoice does not match the vendor's registered GSTIN. "
                "Cannot claim input tax credit until resolved."
            ),
        )
    return None


def _rule_exact_duplicate(issue_types: set[str], issue_details: list[dict]) -> Optional[SafetyResult]:
    """
    Exact duplicate (same vendor + same invoice number) → always BLOCKED.
    Soft duplicates (same amount + same date) are flagged, not blocked.
    """
    for issue in issue_details:
        if issue.get("type") == "DUPLICATE":
            if issue.get("details", {}).get("duplicate_type") == "exact":
                return SafetyResult(
                    blocked=True,
                    rule="EXACT_DUPLICATE",
                    reason=(
                        "This is an exact duplicate invoice "
                        "(same vendor + same invoice number). "
                        "Paying it again would be a double payment."
                    ),
                )
    return None


def _rule_soft_duplicate(issue_types: set[str], issue_details: list[dict]) -> Optional[SafetyResult]:
    """Soft duplicate (same vendor + same amount + same date) → FLAGGED."""
    for issue in issue_details:
        if issue.get("type") == "DUPLICATE":
            if issue.get("details", {}).get("duplicate_type") == "soft":
                return SafetyResult(
                    flagged=True,
                    rule="SOFT_DUPLICATE",
                    reason=(
                        "Possible duplicate invoice: another invoice from this vendor "
                        "has the same amount and date. Requires human confirmation."
                    ),
                )
    return None


def _rule_high_value(total_amount_paise: int) -> Optional[SafetyResult]:
    """Invoice total above MAX_AUTO_APPROVE_AMOUNT → always FLAGGED."""
    if total_amount_paise > MAX_AUTO_APPROVE_AMOUNT:
        amount_rupees = total_amount_paise / 100
        limit_rupees  = MAX_AUTO_APPROVE_AMOUNT / 100
        return SafetyResult(
            flagged=True,
            rule="HIGH_VALUE",
            reason=(
                f"Invoice total ₹{amount_rupees:,.2f} exceeds the auto-approve limit "
                f"of ₹{limit_rupees:,.2f}. High-value invoices always need human review."
            ),
        )
    return None


def _rule_large_amount_diff(issue_details: list[dict]) -> Optional[SafetyResult]:
    """Amount difference above MAX_AUTO_APPROVE_DIFF → always FLAGGED."""
    for issue in issue_details:
        if issue.get("type") == "AMOUNT_MISMATCH":
            diff_paise = issue.get("details", {}).get("difference_paise", 0)
            if diff_paise > MAX_AUTO_APPROVE_DIFF:
                diff_rupees   = diff_paise / 100
                limit_rupees  = MAX_AUTO_APPROVE_DIFF / 100
                return SafetyResult(
                    flagged=True,
                    rule="LARGE_AMOUNT_DIFF",
                    reason=(
                        f"Invoice amount differs from PO by ₹{diff_rupees:,.2f}, "
                        f"which exceeds the ₹{limit_rupees:,.2f} auto-approve threshold."
                    ),
                )
    return None


def _rule_new_vendor(has_memory: bool) -> Optional[SafetyResult]:
    """
    New vendor with no memory → always FLAGGED.
    `has_memory` should be True only if Hindsight has at least one past
    decision for this vendor in the current company's memory bank.
    """
    if not has_memory:
        return SafetyResult(
            flagged=True,
            rule="NEW_VENDOR",
            reason=(
                "This vendor has no review history yet. "
                "First invoices from any vendor always require human review "
                "to establish a baseline."
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_safety_rules(
    issue_list: list,           # list of CheckIssue or dicts with .type / ["type"]
    total_amount_paise: int,
    has_vendor_memory: bool,
) -> SafetyResult:
    """
    Apply all safety rules to a single invoice.

    Args:
        issue_list:          Output of run_all_checks() — list of CheckIssue objects
                             or plain dicts with "type" and "details" keys.
        total_amount_paise:  Invoice total in integer paise.
        has_vendor_memory:   True if this vendor has prior decisions in memory.

    Returns:
        SafetyResult — first matching rule wins.
        If no rule fires, returns SafetyResult(blocked=False, flagged=False).
    """
    # Normalise input: support both CheckIssue dataclasses and plain dicts
    issue_dicts: list[dict] = []
    issue_types: set[str] = set()

    for issue in issue_list:
        if hasattr(issue, "type") and hasattr(issue, "details"):
            # CheckIssue dataclass
            d = {"type": issue.type, "details": issue.details}
        elif isinstance(issue, dict):
            d = issue
        else:
            continue
        issue_dicts.append(d)
        issue_types.add(d.get("type", ""))

    # Evaluate rules in priority order (most severe first)
    checks = [
        _rule_invalid_gstin(issue_types),
        _rule_gstin_mismatch(issue_types),
        _rule_exact_duplicate(issue_types, issue_dicts),
        _rule_soft_duplicate(issue_types, issue_dicts),
        _rule_high_value(total_amount_paise),
        _rule_large_amount_diff(issue_dicts),
        _rule_new_vendor(has_vendor_memory),
    ]

    for result in checks:
        if result is not None:
            return result

    return SafetyResult()   # no rule triggered
