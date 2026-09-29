"""
LedgerMind -- memory/client.py
Hindsight client singleton + idempotent bank creation.

One bank per company. The bank_id is derived from the company name so it
is stable across restarts (no need to store it separately).

Usage:
    from memory.client import get_hindsight_client, get_or_create_bank
    client = get_hindsight_client()
    bank_id = get_or_create_bank(company_name="Acme Industries")
"""

import os
import logging
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HINDSIGHT_URL: str = os.getenv("HINDSIGHT_URL", "https://api.hindsight.vectorize.io")
HINDSIGHT_API_KEY: str = os.getenv("HINDSIGHT_API_KEY", "")

BANK_MISSION = (
    "I help an accountant review GST purchase invoices. "
    "I learn each vendor's habits and the accountant's judgment, "
    "so routine issues can be handled without repeated manual review. "
    "I remember every approval, rejection, and correction, and use that "
    "history to recommend decisions on new invoices."
)

BANK_DISPOSITION = {
    "skepticism": 4,   # cautious -- prefer flagging over auto-approving
    "literalism": 3,   # balanced
    "empathy": 2,      # business context over feelings
}

# Hard rules added as directives to the bank
BANK_DIRECTIVES = [
    "Never recommend approving an invoice that has an invalid GSTIN.",
    "Never recommend approving an invoice that is an exact duplicate.",
    "Always flag invoices from new vendors with no prior review history.",
    "When in doubt, FLAG -- a false alarm costs a minute, a wrong approval costs money.",
]


# ---------------------------------------------------------------------------
# Lazy singleton client
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_hindsight_client():
    """
    Return a cached Hindsight client instance.
    Raises RuntimeError if the API key is not configured.
    """
    try:
        from hindsight_client import Hindsight
    except ImportError as exc:
        raise ImportError(
            "hindsight-client is not installed. Run: pip install hindsight-client"
        ) from exc

    if not HINDSIGHT_API_KEY:
        raise RuntimeError(
            "HINDSIGHT_API_KEY is not set. Add it to your .env file."
        )

    client = Hindsight(
        base_url=HINDSIGHT_URL,
        api_key=HINDSIGHT_API_KEY,
        timeout=60.0,
    )
    log.info("Hindsight client initialised (url=%s)", HINDSIGHT_URL)
    return client


# ---------------------------------------------------------------------------
# Bank helpers
# ---------------------------------------------------------------------------

def _make_bank_id(company_name: str) -> str:
    """
    Derive a stable bank_id from a company name.
    e.g. "Acme Industries Pvt. Ltd." -> "ledgermind-acme-industries-pvt-ltd"
    """
    slug = (
        company_name.lower()
        .replace(".", "")
        .replace(",", "")
        .replace("  ", " ")
        .strip()
        .replace(" ", "-")
    )
    # Truncate to 60 chars to stay within API limits
    return f"ledgermind-{slug[:50]}"


def get_or_create_bank(company_name: str = "LedgerMind Demo") -> str:
    """
    Idempotently create (or retrieve) the Hindsight memory bank for a company.

    Returns:
        bank_id (str) -- stable identifier to pass to retain/recall/reflect calls.
    """
    client = get_hindsight_client()
    bank_id = _make_bank_id(company_name)

    try:
        client.create_bank(
            bank_id=bank_id,
            name=f"{company_name} - Invoice Review",
            mission=BANK_MISSION,
            disposition=BANK_DISPOSITION,
        )
        log.info("Created Hindsight bank: %s", bank_id)

        # Add hard-rule directives (best-effort -- don't crash if unsupported)
        _add_directives(client, bank_id)

    except Exception as exc:
        # 409 / "already exists" is expected on subsequent startups -- log and continue.
        msg = str(exc).lower()
        if "already" in msg or "exists" in msg or "409" in msg or "conflict" in msg:
            log.info("Bank already exists, reusing: %s", bank_id)
        else:
            # Unexpected error -- re-raise so the caller knows something is wrong.
            raise

    return bank_id


def _add_directives(client, bank_id: str) -> None:
    """Add safety directives to the bank (idempotent best-effort)."""
    try:
        existing = client.list_directives(bank_id=bank_id)
        existing_texts = {d.text if hasattr(d, "text") else str(d) for d in existing}
        for directive in BANK_DIRECTIVES:
            if directive not in existing_texts:
                # The directives API varies -- try the most common call pattern
                try:
                    client.banks.create_directive(
                        bank_id=bank_id,
                        body={"text": directive},
                    )
                except Exception:
                    pass  # directives are a best-effort enhancement
    except Exception:
        pass  # list_directives may not be available in all server versions


# ---------------------------------------------------------------------------
# Demo convenience
# ---------------------------------------------------------------------------

DEMO_BANK_ID = "ledgermind-demo"


def get_or_create_demo_bank() -> str:
    """Get the demo bank used in run_demo.py and tests."""
    client = get_hindsight_client()
    try:
        client.create_bank(
            bank_id=DEMO_BANK_ID,
            name="LedgerMind Demo - Invoice Review",
            mission=BANK_MISSION,
            disposition=BANK_DISPOSITION,
        )
        log.info("Created demo bank: %s", DEMO_BANK_ID)
    except Exception as exc:
        msg = str(exc).lower()
        if not ("already" in msg or "exists" in msg or "409" in msg or "conflict" in msg):
            raise
        log.info("Demo bank already exists: %s", DEMO_BANK_ID)
    return DEMO_BANK_ID
