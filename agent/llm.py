"""
LedgerMind -- agent/llm.py
Groq LLM client with JSON validation and retry logic.

The decision engine only accepts responses that contain all required fields.
If the LLM returns invalid JSON or missing fields, the call is retried up to
LLM_MAX_RETRIES times. If all retries are exhausted, a safe fallback is returned
that flags the invoice rather than auto-approving it.
"""

import json
import logging
import os
import time
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_TEMPERATURE: float = 0.1   # low temperature for deterministic structured output
LLM_TIMEOUT: int = 30          # seconds

# Safe fallback returned when all retries are exhausted
FALLBACK_RESPONSE = {
    "decision": "FLAG",
    "reason": "AI unavailable -- invoice flagged for human review as a precaution.",
    "confidence": 0.0,
    "memories_used": [],
}

REQUIRED_FIELDS = {"decision", "reason", "confidence", "memories_used"}
VALID_DECISIONS = {"AUTO_APPROVE", "FLAG", "BLOCK"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_response(data: dict) -> tuple[bool, str]:
    """
    Check that the LLM response has all required fields with correct types.
    Returns (is_valid, error_message).
    """
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        return False, f"Missing fields: {missing}"

    decision = data.get("decision", "")
    if decision not in VALID_DECISIONS:
        return False, f"Invalid decision '{decision}'. Must be one of {VALID_DECISIONS}"

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        return False, f"confidence must be a float between 0.0 and 1.0, got {confidence!r}"

    if not isinstance(data.get("memories_used"), list):
        return False, "memories_used must be a list"

    if not isinstance(data.get("reason"), str) or not data["reason"].strip():
        return False, "reason must be a non-empty string"

    return True, ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def call_llm(
    system_prompt: str,
    user_prompt: str,
    retries: int = LLM_MAX_RETRIES,
) -> dict:
    """
    Call the Groq LLM and return a validated decision dict.

    The response must be valid JSON containing:
        {
            "decision":      "AUTO_APPROVE" | "FLAG" | "BLOCK",
            "reason":        "...",
            "confidence":    0.0 - 1.0,
            "memories_used": ["doc-id-1", ...]
        }

    On all retries exhausted: returns FALLBACK_RESPONSE.
    Never raises -- the decision engine must always get a usable response.

    Args:
        system_prompt: The static system instructions (from agent/prompts.py).
        user_prompt:   The per-invoice context (invoice + issues + memories).
        retries:       Max attempts before fallback (default from env LLM_MAX_RETRIES).

    Returns:
        Validated dict with decision, reason, confidence, memories_used.
    """
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set -- returning fallback decision")
        result = dict(FALLBACK_RESPONSE)
        result["reason"] = "GROQ_API_KEY is not configured. Invoice flagged for human review."
        return result

    try:
        from groq import Groq
    except ImportError:
        log.error("groq package not installed. Run: pip install groq")
        return dict(FALLBACK_RESPONSE)

    client = Groq(api_key=GROQ_API_KEY)

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            log.debug("LLM call attempt %d/%d (model=%s)", attempt, retries, LLM_MODEL)
            completion = client.chat.completions.create(
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                timeout=LLM_TIMEOUT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )

            raw = completion.choices[0].message.content or ""
            log.debug("LLM raw response (attempt %d): %s", attempt, raw[:300])

            # Parse JSON
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = f"JSON parse error: {e}"
                log.warning("Attempt %d: %s", attempt, last_error)
                _backoff(attempt)
                continue

            # Validate fields
            valid, err = _validate_response(data)
            if not valid:
                last_error = f"Validation error: {err}"
                log.warning("Attempt %d: %s", attempt, last_error)
                _backoff(attempt)
                continue

            # Normalise types
            data["confidence"] = float(data["confidence"])
            data["memories_used"] = list(data["memories_used"])
            data["decision"] = str(data["decision"]).upper()

            log.info(
                "LLM decision: %s (confidence=%.2f, attempt=%d)",
                data["decision"], data["confidence"], attempt,
            )
            return data

        except Exception as exc:
            last_error = str(exc)
            log.warning("Attempt %d LLM error: %s", attempt, last_error)
            _backoff(attempt)

    # All retries exhausted
    log.error(
        "All %d LLM retries exhausted. Last error: %s. Returning safe fallback.",
        retries, last_error,
    )
    result = dict(FALLBACK_RESPONSE)
    result["reason"] = (
        f"AI service unavailable after {retries} attempts ({last_error}). "
        "Invoice flagged for human review as a precaution."
    )
    return result


def _backoff(attempt: int) -> None:
    """Brief exponential backoff between retries (capped at 4 s)."""
    wait = min(0.5 * (2 ** (attempt - 1)), 4.0)
    time.sleep(wait)
