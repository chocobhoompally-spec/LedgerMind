"""
LedgerMind -- agent/decide.py
The decision engine: the function Person 3's pipeline calls for every invoice.

Decision flow:
    Memory OFF  -->  FLAG everything with issues, CLEAN if none
    Memory ON:
        Layer 1: Safety rules (in code, before AI)
                 --> BLOCK / FLAG immediately if triggered
        Layer 2: Recall vendor memory
        Layer 3: LLM decision
                 --> Post-check: AI can never override a safety rule

Auto-approve only if ALL are true:
    - No safety rule applies
    - Memory has >= MIN_EVIDENCE past approvals for this issue type / vendor
    - No rejection or overturn since last approval
    - AI confidence >= MIN_CONFIDENCE

Public API (called by pipeline and demo script):
    result = decide(invoice_data, issues, vendor_data, bank_id, memory_enabled)
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_EVIDENCE: int = int(os.getenv("MIN_EVIDENCE", "2"))
MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "0.8"))
MAX_AUTO_APPROVE_AMOUNT: int = int(os.getenv("MAX_AUTO_APPROVE_AMOUNT", "500000")) * 100  # paise
MAX_AUTO_APPROVE_DIFF: int = int(os.getenv("MAX_AUTO_APPROVE_DIFF", "100")) * 100          # paise


# ---------------------------------------------------------------------------
# Input/output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class InvoiceInput:
    """Minimal invoice fields needed by the decision engine."""
    invoice_id: int
    invoice_number: str
    invoice_date: str          # ISO date YYYY-MM-DD
    vendor_id: int
    vendor_name: str
    vendor_gstin: str
    total_amount_paise: int
    po_number: Optional[str] = None


@dataclass
class VendorInput:
    """Vendor fields needed by the decision engine."""
    vendor_id: int
    vendor_name: str
    registered_gstin: str


@dataclass
class DecisionResult:
    """
    The outcome returned by decide().
    This maps directly to what gets saved as an AgentDecision in the DB.
    """
    decision: str              # "AUTO_APPROVE" | "FLAG" | "BLOCK" | "CLEAN"
    reason: str
    confidence: float          # 0.0 - 1.0
    memories_used: list[str] = field(default_factory=list)
    safety_rule_applied: Optional[str] = None
    memory_enabled: bool = True

    @property
    def invoice_status(self) -> str:
        """Map decision to the corresponding Invoice status string."""
        mapping = {
            "AUTO_APPROVE": "AUTO_APPROVED",
            "FLAG":         "FLAGGED",
            "BLOCK":        "BLOCKED",
            "CLEAN":        "CLEAN",
        }
        return mapping.get(self.decision, "FLAGGED")

    def __repr__(self):
        return (
            f"<DecisionResult {self.decision} "
            f"confidence={self.confidence:.2f} "
            f"safety={self.safety_rule_applied!r}>"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _issue_types(issues: list) -> list[str]:
    """Extract issue type strings from a list of CheckIssue objects or dicts."""
    types = []
    for issue in issues:
        if hasattr(issue, "type"):
            types.append(str(issue.type))
        elif isinstance(issue, dict):
            types.append(str(issue.get("type", "")))
    return types


def _check_safety(issues: list, total_paise: int, has_memory: bool) -> Optional[DecisionResult]:
    """
    Run safety rules. Returns a DecisionResult if a rule fires, else None.
    Imports safety.py at call time to keep the module import-order clean.
    """
    from server.checks.safety import apply_safety_rules
    result = apply_safety_rules(
        issue_list=issues,
        total_amount_paise=total_paise,
        has_vendor_memory=has_memory,
    )
    if not result.triggered:
        return None

    decision = "BLOCK" if result.blocked else "FLAG"
    return DecisionResult(
        decision=decision,
        reason=result.reason or f"Safety rule: {result.rule}",
        confidence=1.0,   # safety rules are deterministic -- full confidence
        safety_rule_applied=result.rule,
    )


def _meets_auto_approve_criteria(
    recall_result,
    issue_types: list[str],
    llm_confidence: float,
) -> tuple[bool, str]:
    """
    Check whether memory evidence is strong enough to auto-approve.

    Returns (can_approve: bool, reason: str).
    """
    # 1. Sufficient evidence
    if recall_result.approval_count < MIN_EVIDENCE:
        return False, (
            f"Only {recall_result.approval_count} past approval(s) found "
            f"(need {MIN_EVIDENCE}). Flagging for human review."
        )

    # 2. No recent rejections / overturns
    if recall_result.has_rejections:
        return False, (
            "Memory contains a rejection or overturn for this issue type. "
            "Cannot auto-approve until a human reviews this again."
        )

    # 3. AI confidence threshold
    if llm_confidence < MIN_CONFIDENCE:
        return False, (
            f"AI confidence {llm_confidence:.0%} is below the minimum "
            f"{MIN_CONFIDENCE:.0%}. Flagging for human review."
        )

    return True, ""


# ---------------------------------------------------------------------------
# Main decision function
# ---------------------------------------------------------------------------

def decide(
    invoice: InvoiceInput,
    issues: list,
    vendor: VendorInput,
    bank_id: str,
    memory_enabled: bool = True,
) -> DecisionResult:
    """
    Make an AI-assisted decision for a single invoice.

    Args:
        invoice:        Invoice fields (InvoiceInput or compatible object/dict).
        issues:         List of CheckIssue objects from run_all_checks().
        vendor:         Vendor fields (VendorInput or compatible object/dict).
        bank_id:        Hindsight bank ID for this company.
        memory_enabled: If False, FLAG everything with issues, CLEAN if none.

    Returns:
        DecisionResult with decision, reason, confidence, memories_used, safety_rule_applied.
    """
    # Support plain dicts in addition to dataclasses (for demo script convenience)
    if isinstance(invoice, dict):
        invoice = InvoiceInput(**{k: invoice[k] for k in InvoiceInput.__dataclass_fields__ if k in invoice})
    if isinstance(vendor, dict):
        vendor = VendorInput(**{k: vendor[k] for k in VendorInput.__dataclass_fields__ if k in vendor})

    inv_id  = invoice.invoice_number
    vname   = vendor.vendor_name
    itypes  = _issue_types(issues)

    # ------------------------------------------------------------------
    # Memory OFF: simple rule -- flag anything with issues
    # ------------------------------------------------------------------
    if not memory_enabled:
        if not issues:
            log.info("[%s] Memory OFF, no issues -> CLEAN", inv_id)
            return DecisionResult(
                decision="CLEAN",
                reason="No issues found.",
                confidence=1.0,
                memory_enabled=False,
            )
        log.info("[%s] Memory OFF, %d issues -> FLAG", inv_id, len(issues))
        return DecisionResult(
            decision="FLAG",
            reason=(
                f"Memory is disabled. {len(issues)} issue(s) found: "
                f"{', '.join(itypes)}. Manual review required."
            ),
            confidence=1.0,
            memory_enabled=False,
        )

    # ------------------------------------------------------------------
    # No issues at all -> CLEAN
    # ------------------------------------------------------------------
    if not issues:
        log.info("[%s] No issues -> CLEAN", inv_id)
        return DecisionResult(
            decision="CLEAN",
            reason="No issues found.",
            confidence=1.0,
        )

    # ------------------------------------------------------------------
    # Layer 1: Safety rules (before AI -- deterministic)
    # ------------------------------------------------------------------
    # Check if vendor has ANY memory (approximation: recall and see if non-empty)
    has_memory = _vendor_has_memory(bank_id, vname, vendor.vendor_id)

    safety_result = _check_safety(issues, invoice.total_amount_paise, has_memory)
    if safety_result is not None:
        log.info(
            "[%s] Safety rule '%s' -> %s",
            inv_id, safety_result.safety_rule_applied, safety_result.decision,
        )
        return safety_result

    # ------------------------------------------------------------------
    # Layer 2: Recall vendor memory
    # ------------------------------------------------------------------
    from memory.recall import recall_vendor_history
    recall_result = recall_vendor_history(
        bank_id=bank_id,
        vendor_name=vname,
        vendor_id=vendor.vendor_id,
        issue_types=itypes,
        budget="mid",
    )

    if recall_result.error:
        # Hindsight unavailable -- safe fallback
        log.warning("[%s] Recall failed (%s) -> FLAG", inv_id, recall_result.error)
        return DecisionResult(
            decision="FLAG",
            reason=(
                "Memory service temporarily unavailable. "
                "Invoice flagged for human review as a precaution."
            ),
            confidence=0.0,
        )

    # ------------------------------------------------------------------
    # Layer 3: LLM decision
    # ------------------------------------------------------------------
    from agent.prompts import build_user_prompt, get_system_prompt
    from agent.llm import call_llm

    user_prompt = build_user_prompt(
        vendor_name=vname,
        vendor_gstin=invoice.vendor_gstin,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        total_amount_paise=invoice.total_amount_paise,
        issues=issues,
        recalled_memories=recall_result.raw_text,
        po_number=invoice.po_number,
    )

    llm_response = call_llm(
        system_prompt=get_system_prompt(),
        user_prompt=user_prompt,
    )

    llm_decision    = llm_response.get("decision", "FLAG").upper()
    llm_reason      = llm_response.get("reason", "No reason provided.")
    llm_confidence  = float(llm_response.get("confidence", 0.0))
    llm_memories    = list(llm_response.get("memories_used", []))

    # ------------------------------------------------------------------
    # Post-check: AI cannot override safety rules
    # If LLM says AUTO_APPROVE but evidence criteria not met -> FLAG
    # ------------------------------------------------------------------
    if llm_decision == "AUTO_APPROVE":
        can_approve, override_reason = _meets_auto_approve_criteria(
            recall_result, itypes, llm_confidence
        )
        if not can_approve:
            log.info(
                "[%s] LLM said AUTO_APPROVE but criteria not met: %s -> FLAG",
                inv_id, override_reason,
            )
            return DecisionResult(
                decision="FLAG",
                reason=override_reason,
                confidence=llm_confidence,
                memories_used=llm_memories,
            )

    # Final safety post-check: AI can never approve a safety-rule invoice
    safety_post = _check_safety(issues, invoice.total_amount_paise, has_memory=True)
    if safety_post and llm_decision == "AUTO_APPROVE":
        log.warning(
            "[%s] AI returned AUTO_APPROVE but safety rule '%s' applies -> override to %s",
            inv_id, safety_post.safety_rule_applied, safety_post.decision,
        )
        return DecisionResult(
            decision=safety_post.decision,
            reason=safety_post.reason,
            confidence=1.0,
            safety_rule_applied=safety_post.safety_rule_applied,
            memories_used=llm_memories,
        )

    log.info(
        "[%s] Final decision: %s (confidence=%.2f)",
        inv_id, llm_decision, llm_confidence,
    )
    return DecisionResult(
        decision=llm_decision,
        reason=llm_reason,
        confidence=llm_confidence,
        memories_used=llm_memories,
    )


# ---------------------------------------------------------------------------
# Helper: lightweight "does this vendor have any memory?" check
# ---------------------------------------------------------------------------

def _vendor_has_memory(bank_id: str, vendor_name: str, vendor_id: int) -> bool:
    """
    Quick check: does Hindsight have at least one memory about this vendor?
    Uses a low-budget recall. Returns False on any error (treats unknown as new vendor).
    """
    try:
        from memory.recall import recall_vendor_history
        result = recall_vendor_history(
            bank_id=bank_id,
            vendor_name=vendor_name,
            vendor_id=vendor_id,
            budget="low",
        )
        return not result.is_empty and result.error is None
    except Exception as exc:
        log.warning("Could not check vendor memory for %s: %s", vendor_name, exc)
        return False
