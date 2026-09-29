"""
LedgerMind -- memory/reflect.py
Generate a plain-English vendor profile from Hindsight memory.

Called when:
  - The Vendor Memory page loads (Person 4's UI)
  - A monthly summary is generated
"""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class VendorProfile:
    """AI-synthesised profile of what the agent has learned about a vendor."""
    vendor_name: str
    text: str                   # plain-English narrative from Hindsight reflect()
    based_on: list[str]         # source memory IDs / snippets
    error: Optional[str] = None

    @property
    def is_available(self) -> bool:
        return self.error is None and bool(self.text)

    def __str__(self):
        return self.text if self.is_available else f"[Profile unavailable: {self.error}]"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_client():
    from memory.client import get_hindsight_client
    return get_hindsight_client()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reflect_vendor_profile(
    bank_id: str,
    vendor_name: str,
    budget: str = "low",
) -> VendorProfile:
    """
    Ask Hindsight to synthesise everything it has learned about a vendor.

    Args:
        bank_id:     Hindsight bank ID for this company.
        vendor_name: Human-readable vendor name, e.g. "Sharma Traders".
        budget:      Hindsight reflect budget -- "low" | "mid" | "high".
                     "low" is fast and cheap; use "mid" for richer profiles.

    Returns:
        VendorProfile with a plain-English text block and source references.
        On failure, returns a VendorProfile with error set (never raises).
    """
    client = _get_client()
    query = (
        f"What should an accountant know about {vendor_name}'s invoices? "
        f"Summarise any recurring issues, past decisions, and whether this vendor "
        f"is generally trustworthy."
    )

    try:
        response = client.reflect(
            bank_id=bank_id,
            query=query,
            budget=budget,
        )

        text = getattr(response, "text", "") or ""
        based_on = []

        # Extract source references if available
        raw_sources = getattr(response, "based_on", []) or []
        for src in raw_sources:
            if isinstance(src, str):
                based_on.append(src)
            elif hasattr(src, "text"):
                based_on.append(src.text[:120])
            else:
                based_on.append(str(src)[:120])

        if not text:
            text = f"No review history found yet for {vendor_name}."

        log.info("Reflected vendor profile for %s (%d source memories)", vendor_name, len(based_on))
        return VendorProfile(vendor_name=vendor_name, text=text, based_on=based_on)

    except Exception as exc:
        log.error("Reflect failed for vendor %s: %s", vendor_name, exc)
        return VendorProfile(
            vendor_name=vendor_name,
            text="",
            based_on=[],
            error=str(exc),
        )


def reflect_no_memory_message(vendor_name: str) -> VendorProfile:
    """
    Return a default profile for vendors with no Hindsight bank (memory OFF mode).
    """
    return VendorProfile(
        vendor_name=vendor_name,
        text=(
            f"Memory is disabled. No profile available for {vendor_name}. "
            "Enable memory and review at least two invoices to build a profile."
        ),
        based_on=[],
    )
