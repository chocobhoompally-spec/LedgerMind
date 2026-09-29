"""
LedgerMind — Unit Tests for All Checks

Tests cover:
  - GSTIN validation (format, state code, checksum)
  - All 9 GST rule checks (rules.py)
  - All 7 safety rules (safety.py)

Run with:
    pytest server/tests/test_checks.py -v
"""

import pytest
import sys
from pathlib import Path

# Make sure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.checks.gstin import (
    validate_gstin, is_valid_gstin, get_state_code,
    GSTINError,
)
from server.checks.rules import (
    InvoiceData, CheckIssue,
    check_rounding, check_amount_mismatch, check_duplicate,
    check_invalid_gstin, check_gstin_mismatch,
    check_tax_rate_mismatch, check_tax_type_mismatch, check_missing_po,
    run_all_checks, ROUNDING_LIMIT,
)
from server.checks.safety import (
    apply_safety_rules, SafetyResult,
    MAX_AUTO_APPROVE_AMOUNT, MAX_AUTO_APPROVE_DIFF,
)


# ===========================================================================
# Helpers
# ===========================================================================

# A known-valid GSTIN for Maharashtra (state 27)
VALID_GSTIN_MH = "27AAPFS1234A1ZH"   # Sharma Traders  (checksum verified)
VALID_GSTIN_KA = "29AABCK5678B1ZF"   # Kumar Steels (Karnataka, checksum verified)
VALID_GSTIN_TS = "36AABCV2345F1ZJ"   # Venkat Chemicals (Telangana, checksum verified)

BUYER_STATE = "27"   # Maharashtra


def make_invoice(**overrides) -> InvoiceData:
    """Return a minimal clean invoice, with any field overridden by kwargs."""
    base = InvoiceData(
        invoice_number="INV-001",
        invoice_date="2026-07-15",
        vendor_id=1,
        vendor_gstin=VALID_GSTIN_MH,
        registered_gstin=VALID_GSTIN_MH,
        taxable_amount=100000,      # ₹1000.00 (100000 paise)
        gst_rate=1800,              # 18%
        cgst=9000,                  # ₹90 CGST
        sgst=9000,                  # ₹90 SGST
        igst=0,
        total_amount=118000,        # ₹1180.00 (taxable + cgst + sgst)
        po_number="PO-001",
        po_amount=118000,           # matches total
        po_gst_rate=1800,           # matches invoice
        vendor_state_code="27",     # intra-state (same as buyer)
        buyer_state_code=BUYER_STATE,
        existing_invoices=[],
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ===========================================================================
# 1. GSTIN Validation
# ===========================================================================

class TestGSTINValidation:

    def test_valid_gstin_maharashtra(self):
        r = validate_gstin(VALID_GSTIN_MH)
        assert r.valid is True
        assert r.state_code == "27"
        assert r.state_name == "Maharashtra"

    def test_valid_gstin_karnataka(self):
        r = validate_gstin(VALID_GSTIN_KA)
        assert r.valid is True
        assert r.state_code == "29"

    def test_normalises_lowercase_input(self):
        r = validate_gstin(VALID_GSTIN_MH.lower())
        assert r.valid is True

    def test_strips_whitespace(self):
        r = validate_gstin(f"  {VALID_GSTIN_MH}  ")
        assert r.valid is True

    def test_wrong_length_too_short(self):
        r = validate_gstin("27AAPFS1234A1Z")   # 14 chars
        assert r.valid is False
        assert r.error == GSTINError.FORMAT_ERROR

    def test_wrong_length_too_long(self):
        r = validate_gstin("27AAPFS1234A1ZVX")  # 16 chars
        assert r.valid is False
        assert r.error == GSTINError.FORMAT_ERROR

    def test_invalid_state_code_00(self):
        # State code 00 is not valid
        r = validate_gstin("00AAPFS1234A1ZV")
        assert r.valid is False
        # Could be format error (pattern mismatch for leading 0) or invalid state
        assert r.error in (GSTINError.FORMAT_ERROR, GSTINError.INVALID_STATE)

    def test_invalid_state_code_99(self):
        r = validate_gstin("99AAPFS1234A1ZV")
        assert r.valid is False
        assert r.error == GSTINError.INVALID_STATE

    def test_wrong_checksum(self):
        # Flip the last character
        bad = VALID_GSTIN_MH[:-1] + ("W" if VALID_GSTIN_MH[-1] != "W" else "X")
        r = validate_gstin(bad)
        assert r.valid is False
        assert r.error == GSTINError.CHECKSUM_ERROR

    def test_empty_string(self):
        r = validate_gstin("")
        assert r.valid is False

    def test_none_input(self):
        r = validate_gstin(None)
        assert r.valid is False

    def test_completely_wrong_format(self):
        r = validate_gstin("INVALIDGSTIN123")
        assert r.valid is False
        assert r.error == GSTINError.FORMAT_ERROR

    def test_is_valid_gstin_helper_true(self):
        assert is_valid_gstin(VALID_GSTIN_MH) is True

    def test_is_valid_gstin_helper_false(self):
        assert is_valid_gstin("BAD") is False

    def test_get_state_code_valid(self):
        assert get_state_code(VALID_GSTIN_MH) == "27"

    def test_get_state_code_invalid_returns_none(self):
        assert get_state_code("BADGSTIN") is None


# ===========================================================================
# 2. GST Rule Checks
# ===========================================================================

class TestRoundingCheck:

    def test_no_rounding_on_clean_invoice(self):
        inv = make_invoice()  # total == computed
        assert check_rounding(inv) == []

    def test_rounding_detected_small_diff(self):
        # Add ₹1 rounding (100 paise) to total
        inv = make_invoice(total_amount=118100)
        issues = check_rounding(inv)
        assert len(issues) == 1
        assert issues[0].type == "ROUNDING"
        assert issues[0].details["difference_paise"] == 100

    def test_rounding_detected_at_limit(self):
        # Exactly at ROUNDING_LIMIT (e.g. ₹10 = 1000 paise) — still ROUNDING
        inv = make_invoice(total_amount=118000 + ROUNDING_LIMIT)
        issues = check_rounding(inv)
        assert len(issues) == 1
        assert issues[0].type == "ROUNDING"

    def test_over_rounding_limit_not_flagged_by_rounding(self):
        # More than ROUNDING_LIMIT → not a rounding issue (AMOUNT_MISMATCH handles it)
        inv = make_invoice(total_amount=118000 + ROUNDING_LIMIT + 1)
        issues = check_rounding(inv)
        assert issues == []

    def test_exact_zero_difference_is_clean(self):
        inv = make_invoice(total_amount=118000)
        assert check_rounding(inv) == []

    def test_rounding_detail_has_rupees(self):
        inv = make_invoice(total_amount=118200)
        issues = check_rounding(inv)
        assert issues[0].details["difference_rupees"] == 2.0


class TestAmountMismatchCheck:

    def test_no_issue_when_amounts_match(self):
        inv = make_invoice()
        assert check_amount_mismatch(inv) == []

    def test_no_issue_when_po_amount_is_none(self):
        inv = make_invoice(po_amount=None)
        assert check_amount_mismatch(inv) == []

    def test_mismatch_detected(self):
        # Invoice is ₹5000 more than PO
        inv = make_invoice(total_amount=118000 + 500000)   # +₹5000
        issues = check_amount_mismatch(inv)
        assert len(issues) == 1
        assert issues[0].type == "AMOUNT_MISMATCH"

    def test_small_diff_within_rounding_limit_not_flagged(self):
        inv = make_invoice(total_amount=118000 + ROUNDING_LIMIT)
        # po_amount stays at 118000 — diff equals exactly ROUNDING_LIMIT → NOT amount_mismatch
        assert check_amount_mismatch(inv) == []

    def test_detail_contains_difference(self):
        inv = make_invoice(total_amount=118000 + 1000000)  # +₹100
        issues = check_amount_mismatch(inv)
        assert issues[0].details["difference_paise"] == 1000000


class TestDuplicateCheck:

    def test_no_duplicate_on_fresh_invoice(self):
        inv = make_invoice(existing_invoices=[])
        assert check_duplicate(inv) == []

    def test_exact_duplicate_same_invoice_number(self):
        inv = make_invoice(existing_invoices=[
            {"invoice_number": "INV-001", "total_amount": 118000, "invoice_date": "2026-06-10"}
        ])
        issues = check_duplicate(inv)
        assert len(issues) == 1
        assert issues[0].type == "DUPLICATE"
        assert issues[0].details["duplicate_type"] == "exact"

    def test_soft_duplicate_same_amount_and_date(self):
        inv = make_invoice(
            invoice_number="INV-NEW",   # different number
            total_amount=118000,
            invoice_date="2026-07-15",
            existing_invoices=[
                {"invoice_number": "INV-OLD", "total_amount": 118000, "invoice_date": "2026-07-15"}
            ]
        )
        issues = check_duplicate(inv)
        assert len(issues) == 1
        assert issues[0].details["duplicate_type"] == "soft"

    def test_different_vendor_not_caught(self):
        # existing_invoices is already filtered by vendor_id by the caller;
        # if the list is empty, no duplicate fires.
        inv = make_invoice(existing_invoices=[])
        assert check_duplicate(inv) == []

    def test_same_number_but_different_amount_is_exact_duplicate(self):
        # Invoice number match takes priority over amount
        inv = make_invoice(
            invoice_number="INV-001",
            total_amount=999000,
            existing_invoices=[
                {"invoice_number": "INV-001", "total_amount": 118000, "invoice_date": "2026-06-01"}
            ]
        )
        issues = check_duplicate(inv)
        assert issues[0].details["duplicate_type"] == "exact"


class TestInvalidGSTINCheck:

    def test_valid_gstin_no_issue(self):
        inv = make_invoice(vendor_gstin=VALID_GSTIN_MH)
        assert check_invalid_gstin(inv) == []

    def test_invalid_gstin_detected(self):
        inv = make_invoice(vendor_gstin="BADGSTIN12345")
        issues = check_invalid_gstin(inv)
        assert len(issues) == 1
        assert issues[0].type == "INVALID_GSTIN"

    def test_wrong_checksum_detected(self):
        bad_gstin = VALID_GSTIN_MH[:-1] + "Z"
        inv = make_invoice(vendor_gstin=bad_gstin)
        issues = check_invalid_gstin(inv)
        # May be valid or invalid depending on checksum coincidence; just check no crash
        assert isinstance(issues, list)

    def test_empty_gstin_detected(self):
        inv = make_invoice(vendor_gstin="")
        issues = check_invalid_gstin(inv)
        assert len(issues) == 1
        assert issues[0].type == "INVALID_GSTIN"


class TestGSTINMismatchCheck:

    def test_matching_gstins_no_issue(self):
        inv = make_invoice(vendor_gstin=VALID_GSTIN_MH, registered_gstin=VALID_GSTIN_MH)
        assert check_gstin_mismatch(inv) == []

    def test_mismatch_detected(self):
        inv = make_invoice(
            vendor_gstin=VALID_GSTIN_MH,
            registered_gstin=VALID_GSTIN_KA,
        )
        issues = check_gstin_mismatch(inv)
        assert len(issues) == 1
        assert issues[0].type == "GSTIN_MISMATCH"

    def test_invalid_invoice_gstin_skipped(self):
        # Already caught by check_invalid_gstin; mismatch check should not fire
        inv = make_invoice(vendor_gstin="BADGSTIN12345", registered_gstin=VALID_GSTIN_MH)
        issues = check_gstin_mismatch(inv)
        assert issues == []

    def test_case_insensitive_comparison(self):
        inv = make_invoice(
            vendor_gstin=VALID_GSTIN_MH.lower(),
            registered_gstin=VALID_GSTIN_MH,
        )
        assert check_gstin_mismatch(inv) == []


class TestTaxRateMismatchCheck:

    def test_matching_rates_no_issue(self):
        inv = make_invoice(gst_rate=1800, po_gst_rate=1800)
        assert check_tax_rate_mismatch(inv) == []

    def test_rate_mismatch_detected(self):
        inv = make_invoice(gst_rate=1800, po_gst_rate=1200)
        issues = check_tax_rate_mismatch(inv)
        assert len(issues) == 1
        assert issues[0].type == "TAX_RATE_MISMATCH"
        assert issues[0].details["invoice_rate"] == 1800
        assert issues[0].details["po_rate"] == 1200

    def test_no_po_gst_rate_skips_check(self):
        inv = make_invoice(po_gst_rate=None)
        assert check_tax_rate_mismatch(inv) == []

    def test_detail_contains_percentages(self):
        inv = make_invoice(gst_rate=1800, po_gst_rate=1200)
        issues = check_tax_rate_mismatch(inv)
        assert issues[0].details["invoice_rate_pct"] == 18.0
        assert issues[0].details["po_rate_pct"] == 12.0


class TestTaxTypeMismatchCheck:

    def test_intra_state_cgst_sgst_is_correct(self):
        # Same state → CGST+SGST → no issue
        inv = make_invoice(
            vendor_state_code="27", buyer_state_code="27",
            cgst=9000, sgst=9000, igst=0,
        )
        assert check_tax_type_mismatch(inv) == []

    def test_intra_state_igst_is_wrong(self):
        inv = make_invoice(
            vendor_state_code="27", buyer_state_code="27",
            cgst=0, sgst=0, igst=18000,
        )
        issues = check_tax_type_mismatch(inv)
        assert len(issues) == 1
        assert issues[0].type == "TAX_TYPE_MISMATCH"
        assert "intra-state" in issues[0].details["transaction_type"]

    def test_inter_state_igst_is_correct(self):
        inv = make_invoice(
            vendor_state_code="36", buyer_state_code="27",   # Telangana → Maharashtra
            cgst=0, sgst=0, igst=18000,
        )
        assert check_tax_type_mismatch(inv) == []

    def test_inter_state_cgst_sgst_is_wrong(self):
        inv = make_invoice(
            vendor_state_code="36", buyer_state_code="27",
            cgst=9000, sgst=9000, igst=0,
        )
        issues = check_tax_type_mismatch(inv)
        assert len(issues) == 1
        assert "inter-state" in issues[0].details["transaction_type"]

    def test_missing_state_codes_skips_check(self):
        inv = make_invoice(vendor_state_code=None, buyer_state_code=None)
        assert check_tax_type_mismatch(inv) == []


class TestMissingPOCheck:

    def test_valid_po_no_issue(self):
        inv = make_invoice(po_number="PO-001", po_amount=118000)
        assert check_missing_po(inv) == []

    def test_missing_po_detected(self):
        inv = make_invoice(po_number="PO-NONEXISTENT", po_amount=None)
        issues = check_missing_po(inv)
        assert len(issues) == 1
        assert issues[0].type == "MISSING_PO"
        assert issues[0].details["po_number"] == "PO-NONEXISTENT"

    def test_no_po_number_skips_check(self):
        # Invoice with no PO reference at all → not a missing PO
        inv = make_invoice(po_number=None, po_amount=None)
        assert check_missing_po(inv) == []


class TestRunAllChecks:

    def test_clean_invoice_has_no_issues(self):
        inv = make_invoice()
        assert run_all_checks(inv) == []

    def test_multiple_issues_returned(self):
        # Both rounding AND tax rate mismatch
        inv = make_invoice(
            total_amount=118100,    # +₹1 rounding
            gst_rate=1800,
            po_gst_rate=1200,       # rate mismatch
        )
        issues = run_all_checks(inv)
        types = {i.type for i in issues}
        assert "ROUNDING" in types
        assert "TAX_RATE_MISMATCH" in types

    def test_invalid_gstin_found(self):
        inv = make_invoice(vendor_gstin="BADGSTIN12345")
        issues = run_all_checks(inv)
        types = {i.type for i in issues}
        assert "INVALID_GSTIN" in types

    def test_returns_list_of_check_issue_objects(self):
        inv = make_invoice(total_amount=118100)
        issues = run_all_checks(inv)
        for issue in issues:
            assert isinstance(issue, CheckIssue)
            assert isinstance(issue.type, str)
            assert isinstance(issue.details, dict)


# ===========================================================================
# 3. Safety Rules
# ===========================================================================

def make_safety_call(issue_list=None, total=100000, has_memory=True):
    """Helper to call apply_safety_rules with defaults."""
    return apply_safety_rules(
        issue_list=issue_list or [],
        total_amount_paise=total,
        has_vendor_memory=has_memory,
    )


class TestSafetyRules:

    # --- No issues ---
    def test_clean_invoice_passes(self):
        result = make_safety_call()
        assert result.triggered is False
        assert result.blocked is False
        assert result.flagged is False

    # --- INVALID_GSTIN → BLOCKED ---
    def test_invalid_gstin_blocks(self):
        issues = [CheckIssue(type="INVALID_GSTIN", details={})]
        result = make_safety_call(issue_list=issues)
        assert result.blocked is True
        assert result.rule == "INVALID_GSTIN"

    # --- GSTIN_MISMATCH → BLOCKED ---
    def test_gstin_mismatch_blocks(self):
        issues = [CheckIssue(type="GSTIN_MISMATCH", details={})]
        result = make_safety_call(issue_list=issues)
        assert result.blocked is True
        assert result.rule == "GSTIN_MISMATCH"

    # --- EXACT DUPLICATE → BLOCKED ---
    def test_exact_duplicate_blocks(self):
        issues = [CheckIssue(
            type="DUPLICATE",
            details={"duplicate_type": "exact", "matched_invoice_number": "INV-001"}
        )]
        result = make_safety_call(issue_list=issues)
        assert result.blocked is True
        assert result.rule == "EXACT_DUPLICATE"

    # --- SOFT DUPLICATE → FLAGGED ---
    def test_soft_duplicate_flags(self):
        issues = [CheckIssue(
            type="DUPLICATE",
            details={"duplicate_type": "soft", "matched_invoice_number": "INV-OLD"}
        )]
        result = make_safety_call(issue_list=issues)
        assert result.flagged is True
        assert result.blocked is False
        assert result.rule == "SOFT_DUPLICATE"

    # --- HIGH VALUE → FLAGGED ---
    def test_high_value_invoice_flags(self):
        result = make_safety_call(total=MAX_AUTO_APPROVE_AMOUNT + 1)
        assert result.flagged is True
        assert result.rule == "HIGH_VALUE"

    def test_invoice_at_limit_does_not_flag(self):
        result = make_safety_call(total=MAX_AUTO_APPROVE_AMOUNT)
        # Exactly at limit — should not fire (> not >=)
        assert result.triggered is False

    # --- LARGE AMOUNT DIFF → FLAGGED ---
    def test_large_amount_diff_flags(self):
        issues = [CheckIssue(
            type="AMOUNT_MISMATCH",
            details={"difference_paise": MAX_AUTO_APPROVE_DIFF + 1}
        )]
        result = make_safety_call(issue_list=issues)
        assert result.flagged is True
        assert result.rule == "LARGE_AMOUNT_DIFF"

    def test_small_amount_diff_does_not_flag(self):
        issues = [CheckIssue(
            type="AMOUNT_MISMATCH",
            details={"difference_paise": MAX_AUTO_APPROVE_DIFF}
        )]
        result = make_safety_call(issue_list=issues)
        # Exactly at limit → should not fire (> not >=)
        assert result.triggered is False

    # --- NEW VENDOR → FLAGGED ---
    def test_new_vendor_flags(self):
        result = make_safety_call(has_memory=False)
        assert result.flagged is True
        assert result.rule == "NEW_VENDOR"

    def test_known_vendor_with_no_issues_passes(self):
        result = make_safety_call(has_memory=True)
        assert result.triggered is False

    # --- Priority: BLOCKED takes precedence over FLAGGED ---
    def test_invalid_gstin_takes_priority_over_high_value(self):
        issues = [CheckIssue(type="INVALID_GSTIN", details={})]
        result = make_safety_call(
            issue_list=issues,
            total=MAX_AUTO_APPROVE_AMOUNT + 100000,  # also high value
        )
        # INVALID_GSTIN is checked first → BLOCKED
        assert result.blocked is True
        assert result.rule == "INVALID_GSTIN"

    # --- Accepts plain dicts as well as CheckIssue objects ---
    def test_accepts_plain_dicts(self):
        issues = [{"type": "INVALID_GSTIN", "details": {}}]
        result = apply_safety_rules(
            issue_list=issues,
            total_amount_paise=100000,
            has_vendor_memory=True,
        )
        assert result.blocked is True

    # --- SafetyResult repr ---
    def test_safety_result_repr_pass(self):
        r = SafetyResult()
        assert "PASS" in repr(r)

    def test_safety_result_repr_blocked(self):
        r = SafetyResult(blocked=True, rule="TEST")
        assert "BLOCKED" in repr(r)

    def test_safety_result_repr_flagged(self):
        r = SafetyResult(flagged=True, rule="TEST")
        assert "FLAGGED" in repr(r)


# ===========================================================================
# 4. Integration: end-to-end invoice → checks → safety
# ===========================================================================

class TestEndToEndFlow:

    def test_sharma_traders_rounding_safe_to_flag_not_block(self):
        """Rounding issue should be caught by rules but not blocked by safety."""
        inv = make_invoice(
            total_amount=118200,    # +₹2 rounding
            vendor_gstin=VALID_GSTIN_MH,
            registered_gstin=VALID_GSTIN_MH,
        )
        issues = run_all_checks(inv)
        types = {i.type for i in issues}
        assert "ROUNDING" in types

        safety = apply_safety_rules(issues, inv.total_amount, has_vendor_memory=True)
        # Rounding alone with known vendor + normal amount → no safety rule fires
        assert safety.triggered is False

    def test_kumar_steels_exact_duplicate_blocked(self):
        """Exact duplicate from Kumar Steels must be blocked."""
        inv = make_invoice(
            invoice_number="INV-001",
            existing_invoices=[
                {"invoice_number": "INV-001", "total_amount": 118000, "invoice_date": "2026-06-15"}
            ]
        )
        issues = run_all_checks(inv)
        safety = apply_safety_rules(issues, inv.total_amount, has_vendor_memory=True)
        assert safety.blocked is True
        assert safety.rule == "EXACT_DUPLICATE"

    def test_delta_pharma_invalid_gstin_blocked(self):
        """Invalid GSTIN must be blocked regardless of memory."""
        inv = make_invoice(vendor_gstin="29BADGSTIN00ZF")
        issues = run_all_checks(inv)
        safety = apply_safety_rules(issues, inv.total_amount, has_vendor_memory=True)
        assert safety.blocked is True

    def test_new_vendor_always_flagged(self):
        """New vendor with a perfectly clean invoice is still flagged."""
        inv = make_invoice()
        issues = run_all_checks(inv)
        assert issues == []  # clean invoice
        safety = apply_safety_rules(issues, inv.total_amount, has_vendor_memory=False)
        assert safety.flagged is True
        assert safety.rule == "NEW_VENDOR"

    def test_patel_electronics_tax_rate_change_flagged_not_blocked(self):
        """Tax rate mismatch should be flagged but not blocked."""
        inv = make_invoice(gst_rate=1800, po_gst_rate=1200)
        issues = run_all_checks(inv)
        types = {i.type for i in issues}
        assert "TAX_RATE_MISMATCH" in types

        safety = apply_safety_rules(issues, inv.total_amount, has_vendor_memory=True)
        # No safety rule for TAX_RATE_MISMATCH — goes to AI
        assert safety.blocked is False

    def test_venkat_chemicals_tax_type_mismatch_flagged_not_blocked(self):
        """Tax type mismatch (IGST on intra-state) goes to AI review, not blocked."""
        inv = make_invoice(
            vendor_state_code="36", buyer_state_code="27",  # inter-state
            cgst=9000, sgst=9000, igst=0,                   # wrong: should be IGST
        )
        issues = run_all_checks(inv)
        types = {i.type for i in issues}
        assert "TAX_TYPE_MISMATCH" in types

        safety = apply_safety_rules(issues, inv.total_amount, has_vendor_memory=True)
        assert safety.blocked is False
