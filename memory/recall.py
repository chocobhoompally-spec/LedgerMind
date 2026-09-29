"""
LedgerMind -- memory/recall.py
Retrieve past decisions for a vendor before the AI makes a new one.

Called by the decision engine (agent/decide.py) before every LLM call.
Returns a structured summary the LLM can reason over.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class RecalledMemory:
    """A single memory item returned from Hindsight."""
    text: str
    memory_type: str        # "world", "experience", "observation", etc.
    document_id: Optional[str] = None


@dataclass
class VendorRecallResult:
    """
    Structured result from recall_vendor_history().
    The decision engine uses this to count past approvals and check for rejections.
    """
    memories: list[RecalledMemory] = field(default_factory=list)
    raw_text: str = ""          # concatenated text for the LLM prompt
    has_approvals: bool = False  # at least one past APPROVE / AUTO_APPROVE found
    has_rejections: bool = False # at least one past REJECT / OVERTURN found
    approval_count: int = 0
    rejection_count: int = 0
    error: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return len(self.memories) == 0


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_client():
    from memory.client import get_hindsight_client
    return get_hindsight_client()


def _parse_memories(recall_response) -> list[RecalledMemory]:
    """Convert the Hindsight recall response into RecalledMemory objects."""
    memories = []
    if recall_response is None:
        return memories
    results = getattr(recall_response, "results", []) or []
    for r in results:
        text = getattr(r, "text", "") or str(r)
        mtype = getattr(r, "type", "unknown")
        doc_id = getattr(r, "document_id", None) or getattr(r, "chunk_id", None)
        memories.append(RecalledMemory(text=text, memory_type=mtype, document_id=doc_id))
    return memories


def _analyse_memories(memories: list[RecalledMemory]) -> tuple[int, int]:
    """
    Scan memory texts to count approvals and rejections.
    Returns (approval_count, rejection_count).
    """
    approval_keywords = {"approved", "auto-approved", "auto_approve", "approve"}
    rejection_keywords = {"rejected", "reject", "overturned", "overturn"}

    approvals = 0
    rejections = 0
    for m in memories:
        lower = m.text.lower()
        if any(k in lower for k in approval_keywords):
            approvals += 1
        if any(k in lower for k in rejection_keywords):
            rejections += 1
    return approvals, rejections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recall_vendor_history(
    bank_id: str,
    vendor_name: str,
    vendor_id: int,
    issue_types: Optional[list[str]] = None,
    budget: str = "mid",
) -> VendorRecallResult:
    """
    Recall past decisions about a vendor, optionally filtered by issue type.

    Args:
        bank_id:      Hindsight bank ID for this company.
        vendor_name:  Human-readable vendor name, e.g. "Sharma Traders".
        vendor_id:    Numeric vendor ID (used for logging).
        issue_types:  List of issue type strings, e.g. ["ROUNDING", "DUPLICATE"].
                      If None or empty, recalls all history for the vendor.
        budget:       Hindsight search budget -- "low" | "mid" | "high".

    Returns:
        VendorRecallResult with memories, counts, and concatenated text for the LLM.
    """
    client = _get_client()

    # Build the recall query
    if issue_types:
        issue_str = " and ".join(issue_types)
        query = f"Past decisions about {vendor_name} invoices with {issue_str} issues"
    else:
        query = f"Past invoice review decisions about {vendor_name}"

    try:
        response = client.recall(
            bank_id=bank_id,
            query=query,
            types=["world", "experience", "observation"],
            budget=budget,
        )
        memories = _parse_memories(response)
        approvals, rejections = _analyse_memories(memories)
        raw_text = "\n".join(m.text for m in memories) if memories else ""

        log.info(
            "Recalled %d memories for vendor=%s issues=%s approvals=%d rejections=%d",
            len(memories), vendor_name, issue_types, approvals, rejections,
        )

        return VendorRecallResult(
            memories=memories,
            raw_text=raw_text,
            has_approvals=approvals > 0,
            has_rejections=rejections > 0,
            approval_count=approvals,
            rejection_count=rejections,
        )

    except Exception as exc:
        log.error("Recall failed for vendor %s: %s", vendor_name, exc)
        return VendorRecallResult(error=str(exc))


def recall_for_prompt(
    bank_id: str,
    vendor_name: str,
    vendor_id: int,
    issue_types: Optional[list[str]] = None,
) -> str:
    """
    Convenience wrapper that returns only the plain-text block for injection
    into an LLM prompt. Returns an empty string if recall fails.
    """
    result = recall_vendor_history(
        bank_id=bank_id,
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        issue_types=issue_types,
    )
    if result.error or result.is_empty:
        return ""
    return result.raw_text
