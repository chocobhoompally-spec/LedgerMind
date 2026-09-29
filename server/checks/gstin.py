"""
LedgerMind — GSTIN Format & Checksum Validation

A GSTIN (Goods and Services Tax Identification Number) is a 15-character
alphanumeric identifier assigned to every GST-registered business in India.

Structure:
  [0-9]{2}         State code (01–37)
  [A-Z]{5}         First 5 letters of PAN
  [0-9]{4}         4 digits of PAN
  [A-Z]{1}         1 letter of PAN (entity type)
  [1-9A-Z]{1}      Registration count for that PAN in the state
  Z                Always the letter Z
  [0-9A-Z]{1}      Checksum character

Example: 27AAPFU0939F1ZV  (state 27 = Maharashtra)
"""

import re
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GSTIN_PATTERN = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
)

# Characters used in the checksum calculation (base-36 style, GST variant)
GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Valid Indian state/UT codes (01–38 + 97 for special/foreign)
VALID_STATE_CODES = {
    "01", "02", "03", "04", "05", "06", "07", "08", "09",
    "10", "11", "12", "13", "14", "15", "16", "17", "18", "19",
    "20", "21", "22", "23", "24", "25", "26", "27", "28", "29",
    "30", "31", "32", "33", "34", "35", "36", "37", "38", "97",
}

STATE_NAMES = {
    "01": "Jammu & Kashmir",  "02": "Himachal Pradesh",
    "03": "Punjab",           "04": "Chandigarh",
    "05": "Uttarakhand",      "06": "Haryana",
    "07": "Delhi",            "08": "Rajasthan",
    "09": "Uttar Pradesh",    "10": "Bihar",
    "11": "Sikkim",           "12": "Arunachal Pradesh",
    "13": "Nagaland",         "14": "Manipur",
    "15": "Mizoram",          "16": "Tripura",
    "17": "Meghalaya",        "18": "Assam",
    "19": "West Bengal",      "20": "Jharkhand",
    "21": "Odisha",           "22": "Chhattisgarh",
    "23": "Madhya Pradesh",   "24": "Gujarat",
    "25": "Daman & Diu",      "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra",      "28": "Andhra Pradesh (old)",
    "29": "Karnataka",        "30": "Goa",
    "31": "Lakshadweep",      "32": "Kerala",
    "33": "Tamil Nadu",       "34": "Puducherry",
    "35": "Andaman & Nicobar","36": "Telangana",
    "37": "Andhra Pradesh",   "38": "Ladakh",
    "97": "Other Territory",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class GSTINError(str, Enum):
    FORMAT_ERROR    = "FORMAT_ERROR"     # wrong length, invalid chars, pattern mismatch
    INVALID_STATE   = "INVALID_STATE"    # state code not in the official list
    CHECKSUM_ERROR  = "CHECKSUM_ERROR"   # format OK but checksum digit is wrong


@dataclass
class GSTINResult:
    valid: bool
    gstin: str
    error: GSTINError | None = None
    error_message: str | None = None
    state_code: str | None = None
    state_name: str | None = None

    def __bool__(self):
        return self.valid


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------

def _compute_checksum(gstin14: str) -> str:
    """
    Compute the expected checksum character for the first 14 characters
    of a GSTIN.

    Algorithm (as per GST specification):
      1. Convert each character to its index in GSTIN_CHARS.
      2. Multiply alternating values by 1 or 2 (starting with factor 1).
      3. For each product, compute (product // 36) + (product % 36).
      4. Sum all reduced values.
      5. Checksum = GSTIN_CHARS[36 - (total % 36)] if total % 36 != 0,
                    else GSTIN_CHARS[0].
    """
    total = 0
    for i, ch in enumerate(gstin14):
        value = GSTIN_CHARS.index(ch)
        product = value * (2 if i % 2 else 1)
        total += (product // 36) + (product % 36)

    remainder = total % 36
    check_index = (36 - remainder) % 36
    return GSTIN_CHARS[check_index]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_gstin(gstin: str) -> GSTINResult:
    """
    Validate a GSTIN string.

    Returns a GSTINResult with:
      - valid=True  if the GSTIN passes format, state code, and checksum checks
      - valid=False with a specific GSTINError otherwise

    Usage:
        result = validate_gstin("27AAPFU0939F1ZV")
        if result.valid:
            print(result.state_name)  # Maharashtra
        else:
            print(result.error, result.error_message)
    """
    if not gstin or not isinstance(gstin, str):
        return GSTINResult(
            valid=False,
            gstin=str(gstin),
            error=GSTINError.FORMAT_ERROR,
            error_message="GSTIN must be a non-empty string",
        )

    gstin = gstin.strip().upper()

    # --- Format check ---
    if not GSTIN_PATTERN.match(gstin):
        return GSTINResult(
            valid=False,
            gstin=gstin,
            error=GSTINError.FORMAT_ERROR,
            error_message=(
                f"GSTIN '{gstin}' does not match the required 15-character format "
                "[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]"
            ),
        )

    # --- State code check ---
    state_code = gstin[:2]
    if state_code not in VALID_STATE_CODES:
        return GSTINResult(
            valid=False,
            gstin=gstin,
            error=GSTINError.INVALID_STATE,
            error_message=f"State code '{state_code}' is not a valid Indian state/UT code",
        )

    # --- Checksum check ---
    expected_checksum = _compute_checksum(gstin[:14])
    if gstin[14] != expected_checksum:
        return GSTINResult(
            valid=False,
            gstin=gstin,
            error=GSTINError.CHECKSUM_ERROR,
            error_message=(
                f"GSTIN checksum is incorrect "
                f"(got '{gstin[14]}', expected '{expected_checksum}')"
            ),
        )

    return GSTINResult(
        valid=True,
        gstin=gstin,
        state_code=state_code,
        state_name=STATE_NAMES.get(state_code, "Unknown"),
    )


def is_valid_gstin(gstin: str) -> bool:
    """Convenience function — returns True/False only."""
    return validate_gstin(gstin).valid


def get_state_code(gstin: str) -> str | None:
    """Extract the 2-digit state code from a GSTIN, or None if invalid."""
    result = validate_gstin(gstin)
    return result.state_code if result.valid else None


# ---------------------------------------------------------------------------
# Quick smoke-test (run this file directly to check)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_cases = [
        ("27AAPFU0939F1ZV", True,  "Valid Maharashtra GSTIN"),
        ("29AABCT1332L1ZD", True,  "Valid Karnataka GSTIN"),
        ("07AAACR5055K1Z5", True,  "Valid Delhi GSTIN"),
        ("27AAPFU0939F1ZX", False, "Wrong checksum"),
        ("99AAPFU0939F1ZV", False, "Invalid state code"),
        ("27AAPFU0939F1Z",  False, "Too short (14 chars)"),
        ("27aapfu0939f1zv", False, "Lowercase — should normalise to upper"),
        ("INVALIDGSTIN123", False, "Completely wrong format"),
        ("",                False, "Empty string"),
    ]

    print(f"{'GSTIN':<20} {'Expected':<10} {'Result':<10} Description")
    print("-" * 70)
    for gstin, expected_valid, desc in test_cases:
        r = validate_gstin(gstin)
        status = "✓" if r.valid == expected_valid else "✗ FAIL"
        detail = r.state_name if r.valid else f"{r.error}: {r.error_message}"
        print(f"{gstin:<20} {str(expected_valid):<10} {status:<10} {desc}")
        if not r.valid:
            print(f"  {'':20} {'':10} {'':10} → {r.error}: {r.error_message}")
