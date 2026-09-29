"""
LedgerMind -- server/tests/test_agent.py
Unit tests for Person 2's agent and memory layer.

All external calls (Hindsight, Groq) are mocked so no real API keys are needed.

Run with:
    pytest server/tests/test_agent.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ===========================================================================
# Shared fixtures & helpers
# ===========================================================================

VALID_GSTIN = "27AAPFS1234A1ZH"

def _make_invoice(**kw):
    from agent.decide import InvoiceInput
    defaults = dict(
        invoice_id=1,
        invoice_number="INV-001",
        invoice_date="2026-07-15",
        vendor_id=1,
        vendor_name="Sharma Traders",
        vendor_gstin=VALID_GSTIN,
        total_amount_paise=118000,
        po_number="PO-001",
    )
    defaults.update(kw)
    return InvoiceInput(**defaults)


def _make_vendor(**kw):
    from agent.decide import VendorInput
    defaults = dict(
        vendor_id=1,
        vendor_name="Sharma Traders",
        registered_gstin=VALID_GSTIN,
    )
    defaults.update(kw)
    return VendorInput(**defaults)


def _make_issue(issue_type: str, details: dict = None):
    from server.checks.rules import CheckIssue
    return CheckIssue(type=issue_type, details=details or {})


def _make_recall_result(approvals=3, rejections=0, texts=None, error=None):
    from memory.recall import VendorRecallResult, RecalledMemory
    memories = []
    if texts:
        for t in texts:
            memories.append(RecalledMemory(text=t, memory_type="experience"))
    return VendorRecallResult(
        memories=memories,
        raw_text="\n".join(m.text for m in memories),
        has_approvals=approvals > 0,
        has_rejections=rejections > 0,
        approval_count=approvals,
        rejection_count=rejections,
        error=error,
    )


# ===========================================================================
# 1. agent/llm.py -- Groq client with retry logic
# ===========================================================================

class TestLLMClient:

    def _call(self, content: str, retries: int = 3):
        from agent.llm import call_llm
        mock_choice = MagicMock()
        mock_choice.message.content = content
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        with patch("agent.llm.GROQ_API_KEY", "fake-key"), \
             patch("groq.Groq") as MockGroq:
            MockGroq.return_value.chat.completions.create.return_value = mock_completion
            return call_llm("system prompt", "user prompt", retries=retries)

    def test_valid_response_returned(self):
        payload = json.dumps({
            "decision": "AUTO_APPROVE",
            "reason": "Seen before.",
            "confidence": 0.92,
            "memories_used": ["doc-1"],
        })
        result = self._call(payload)
        assert result["decision"] == "AUTO_APPROVE"
        assert result["confidence"] == 0.92
        assert result["memories_used"] == ["doc-1"]

    def test_flag_decision_returned(self):
        payload = json.dumps({
            "decision": "FLAG",
            "reason": "New issue.",
            "confidence": 0.95,
            "memories_used": [],
        })
        result = self._call(payload)
        assert result["decision"] == "FLAG"

    def test_invalid_json_triggers_fallback(self):
        from agent.llm import call_llm, FALLBACK_RESPONSE
        with patch("agent.llm.GROQ_API_KEY", "fake-key"), \
             patch("groq.Groq") as MockGroq, \
             patch("agent.llm._backoff"):  # skip sleep
            mock_choice = MagicMock()
            mock_choice.message.content = "not json at all"
            mock_completion = MagicMock()
            mock_completion.choices = [mock_choice]
            MockGroq.return_value.chat.completions.create.return_value = mock_completion
            result = call_llm("system", "user", retries=2)
        assert result["decision"] == "FLAG"
        assert result["confidence"] == 0.0

    def test_missing_field_triggers_retry_then_fallback(self):
        from agent.llm import call_llm
        payload = json.dumps({"decision": "AUTO_APPROVE"})  # missing fields
        with patch("agent.llm.GROQ_API_KEY", "fake-key"), \
             patch("groq.Groq") as MockGroq, \
             patch("agent.llm._backoff"):
            mock_choice = MagicMock()
            mock_choice.message.content = payload
            mock_completion = MagicMock()
            mock_completion.choices = [mock_choice]
            MockGroq.return_value.chat.completions.create.return_value = mock_completion
            result = call_llm("system", "user", retries=2)
        assert result["decision"] == "FLAG"

    def test_invalid_decision_value_falls_back(self):
        from agent.llm import call_llm
        payload = json.dumps({
            "decision": "YOLO",
            "reason": "bad",
            "confidence": 0.5,
            "memories_used": [],
        })
        with patch("agent.llm.GROQ_API_KEY", "fake-key"), \
             patch("groq.Groq") as MockGroq, \
             patch("agent.llm._backoff"):
            mock_choice = MagicMock()
            mock_choice.message.content = payload
            mock_completion = MagicMock()
            mock_completion.choices = [mock_choice]
            MockGroq.return_value.chat.completions.create.return_value = mock_completion
            result = call_llm("system", "user", retries=1)
        assert result["decision"] == "FLAG"

    def test_no_api_key_returns_fallback(self):
        from agent.llm import call_llm
        with patch("agent.llm.GROQ_API_KEY", ""):
            result = call_llm("system", "user")
        assert result["decision"] == "FLAG"
        assert result["confidence"] == 0.0

    def test_confidence_normalised_to_float(self):
        payload = json.dumps({
            "decision": "FLAG",
            "reason": "test",
            "confidence": 1,   # integer, not float
            "memories_used": [],
        })
        result = self._call(payload)
        assert isinstance(result["confidence"], float)


# ===========================================================================
# 2. agent/prompts.py -- prompt builders
# ===========================================================================

class TestPrompts:

    def test_system_prompt_contains_golden_rule(self):
        from agent.prompts import get_system_prompt
        prompt = get_system_prompt()
        assert "FLAG" in prompt
        assert "AUTO_APPROVE" in prompt
        assert "doubt" in prompt.lower()

    def test_system_prompt_contains_json_format(self):
        from agent.prompts import get_system_prompt
        prompt = get_system_prompt()
        assert "decision" in prompt
        assert "confidence" in prompt
        assert "memories_used" in prompt

    def test_user_prompt_contains_vendor_name(self):
        from agent.prompts import build_user_prompt
        prompt = build_user_prompt(
            vendor_name="Sharma Traders",
            vendor_gstin=VALID_GSTIN,
            invoice_number="INV-001",
            invoice_date="2026-07-15",
            total_amount_paise=118000,
            issues=[],
            recalled_memories="",
        )
        assert "Sharma Traders" in prompt

    def test_user_prompt_contains_invoice_number(self):
        from agent.prompts import build_user_prompt
        prompt = build_user_prompt(
            vendor_name="Test Vendor",
            vendor_gstin=VALID_GSTIN,
            invoice_number="INV-XYZ-999",
            invoice_date="2026-07-15",
            total_amount_paise=50000,
            issues=[],
            recalled_memories="",
        )
        assert "INV-XYZ-999" in prompt

    def test_user_prompt_contains_memories(self):
        from agent.prompts import build_user_prompt
        prompt = build_user_prompt(
            vendor_name="V",
            vendor_gstin=VALID_GSTIN,
            invoice_number="X",
            invoice_date="2026-07-15",
            total_amount_paise=1000,
            issues=[],
            recalled_memories="Past decision: approved INV-100 rounding Rs.1",
        )
        assert "Past decision" in prompt

    def test_user_prompt_shows_no_memory_message_when_empty(self):
        from agent.prompts import build_user_prompt
        prompt = build_user_prompt(
            vendor_name="NewVendor",
            vendor_gstin=VALID_GSTIN,
            invoice_number="X",
            invoice_date="2026-07-15",
            total_amount_paise=1000,
            issues=[],
            recalled_memories="",
        )
        assert "No prior decisions" in prompt or "NEW" in prompt

    def test_user_prompt_formats_rounding_issue(self):
        from agent.prompts import build_user_prompt
        issue = _make_issue("ROUNDING", {"difference_rupees": 2.0, "difference_paise": 200})
        prompt = build_user_prompt(
            vendor_name="V",
            vendor_gstin=VALID_GSTIN,
            invoice_number="X",
            invoice_date="2026-07-15",
            total_amount_paise=118200,
            issues=[issue],
            recalled_memories="",
        )
        assert "ROUNDING" in prompt

    def test_safety_note_included_when_provided(self):
        from agent.prompts import build_user_prompt
        prompt = build_user_prompt(
            vendor_name="V",
            vendor_gstin=VALID_GSTIN,
            invoice_number="X",
            invoice_date="2026-07-15",
            total_amount_paise=1000,
            issues=[],
            recalled_memories="",
            safety_note="Invoice total is near the auto-approve limit.",
        )
        assert "near the auto-approve limit" in prompt


# ===========================================================================
# 3. memory/retain.py -- retain decisions
# ===========================================================================

class TestRetain:

    def _mock_client(self):
        mock = MagicMock()
        return mock

    def test_retain_decision_approve(self):
        from memory.retain import retain_decision
        mock_client = self._mock_client()
        with patch("memory.retain._get_client", return_value=mock_client):
            result = retain_decision(
                bank_id="test-bank",
                invoice_id=1,
                invoice_number="INV-001",
                invoice_date="2026-07-15",
                vendor_name="Sharma Traders",
                vendor_id=1,
                total_amount_paise=118000,
                issues=[_make_issue("ROUNDING", {"difference_rupees": 1.0})],
                action="APPROVE",
                note="They always round up.",
            )
        assert result is True
        mock_client.retain.assert_called_once()
        call_kwargs = mock_client.retain.call_args.kwargs
        assert "Sharma Traders" in call_kwargs["content"]
        assert "APPROVED" in call_kwargs["content"]
        assert "They always round up" in call_kwargs["content"]

    def test_retain_decision_overturn(self):
        from memory.retain import retain_decision
        mock_client = self._mock_client()
        with patch("memory.retain._get_client", return_value=mock_client):
            result = retain_decision(
                bank_id="test-bank",
                invoice_id=2,
                invoice_number="INV-002",
                invoice_date="2026-07-16",
                vendor_name="Kumar Steels",
                vendor_id=2,
                total_amount_paise=120000,
                issues=[_make_issue("DUPLICATE", {"duplicate_type": "soft"})],
                action="OVERTURN",
                note="This was a genuine duplicate.",
            )
        assert result is True
        content = mock_client.retain.call_args.kwargs["content"]
        assert "OVERTURNED" in content
        assert "correction" in content.lower() or "do not auto-approve" in content.lower()

    def test_retain_decision_auto_approve_includes_confidence(self):
        from memory.retain import retain_decision
        mock_client = self._mock_client()
        with patch("memory.retain._get_client", return_value=mock_client):
            retain_decision(
                bank_id="test-bank",
                invoice_id=3,
                invoice_number="INV-003",
                invoice_date="2026-07-17",
                vendor_name="Sharma Traders",
                vendor_id=1,
                total_amount_paise=118100,
                issues=[_make_issue("ROUNDING", {"difference_rupees": 1.0})],
                action="AUTO_APPROVE",
                note="Approved 4 times before",
                confidence=0.93,
                memories_used=["decision-981", "decision-1012"],
            )
        content = mock_client.retain.call_args.kwargs["content"]
        assert "93%" in content or "0.93" in content or "AUTO-APPROVED" in content

    def test_retain_returns_false_on_error(self):
        from memory.retain import retain_decision
        mock_client = self._mock_client()
        mock_client.retain.side_effect = Exception("Network error")
        with patch("memory.retain._get_client", return_value=mock_client):
            result = retain_decision(
                bank_id="test-bank",
                invoice_id=99,
                invoice_number="INV-999",
                invoice_date="2026-07-15",
                vendor_name="Error Vendor",
                vendor_id=99,
                total_amount_paise=100,
                issues=[],
                action="APPROVE",
                note="test",
            )
        assert result is False

    def test_retain_vendor_fact(self):
        from memory.retain import retain_vendor_fact
        mock_client = self._mock_client()
        with patch("memory.retain._get_client", return_value=mock_client):
            result = retain_vendor_fact(
                bank_id="test-bank",
                vendor_id=4,
                vendor_name="Patel Electronics",
                fact="Moved to 18% GST from April 2026",
                told_by="Priya Sharma",
            )
        assert result is True
        content = mock_client.retain.call_args.kwargs["content"]
        assert "Patel Electronics" in content
        assert "18%" in content
        assert "Priya Sharma" in content


# ===========================================================================
# 4. memory/recall.py -- recall vendor history
# ===========================================================================

class TestRecall:

    def _mock_response(self, texts: list[str]):
        """Build a mock Hindsight recall response with given memory texts."""
        mock_result = MagicMock()
        mock_result.results = []
        for t in texts:
            m = MagicMock()
            m.text = t
            m.type = "experience"
            m.document_id = f"doc-{t[:10]}"
            mock_result.results.append(m)
        return mock_result

    def test_recall_returns_vendor_recall_result(self):
        from memory.recall import recall_vendor_history
        mock_client = MagicMock()
        mock_client.recall.return_value = self._mock_response([
            "Sharma Traders INV-100 ROUNDING Rs.1 APPROVED",
            "Sharma Traders INV-200 ROUNDING Rs.2 APPROVED",
            "Sharma Traders INV-300 ROUNDING Rs.1 AUTO-APPROVED",
        ])
        with patch("memory.recall._get_client", return_value=mock_client):
            result = recall_vendor_history(
                bank_id="test-bank",
                vendor_name="Sharma Traders",
                vendor_id=1,
                issue_types=["ROUNDING"],
            )
        assert result.approval_count == 3
        assert result.has_approvals is True
        assert result.has_rejections is False
        assert len(result.memories) == 3

    def test_recall_detects_rejection(self):
        from memory.recall import recall_vendor_history
        mock_client = MagicMock()
        mock_client.recall.return_value = self._mock_response([
            "Lakshmi Textiles INV-50 AMOUNT_MISMATCH REJECTED",
        ])
        with patch("memory.recall._get_client", return_value=mock_client):
            result = recall_vendor_history(
                bank_id="test-bank",
                vendor_name="Lakshmi Textiles",
                vendor_id=7,
            )
        assert result.has_rejections is True
        assert result.rejection_count == 1

    def test_recall_detects_overturn(self):
        from memory.recall import recall_vendor_history
        mock_client = MagicMock()
        mock_client.recall.return_value = self._mock_response([
            "Invoice auto-approved then overturned by accountant",
        ])
        with patch("memory.recall._get_client", return_value=mock_client):
            result = recall_vendor_history(
                bank_id="test-bank",
                vendor_name="Some Vendor",
                vendor_id=10,
            )
        assert result.has_rejections is True   # overturn counts as rejection

    def test_empty_recall_is_empty(self):
        from memory.recall import recall_vendor_history
        mock_client = MagicMock()
        mock_client.recall.return_value = self._mock_response([])
        with patch("memory.recall._get_client", return_value=mock_client):
            result = recall_vendor_history(
                bank_id="test-bank",
                vendor_name="New Vendor",
                vendor_id=99,
            )
        assert result.is_empty is True
        assert result.approval_count == 0

    def test_recall_error_returns_error_result(self):
        from memory.recall import recall_vendor_history
        mock_client = MagicMock()
        mock_client.recall.side_effect = Exception("Connection refused")
        with patch("memory.recall._get_client", return_value=mock_client):
            result = recall_vendor_history(
                bank_id="test-bank",
                vendor_name="Error Vendor",
                vendor_id=99,
            )
        assert result.error is not None
        assert "Connection refused" in result.error

    def test_recall_for_prompt_returns_string(self):
        from memory.recall import recall_for_prompt
        mock_client = MagicMock()
        mock_client.recall.return_value = self._mock_response(["Approved before"])
        with patch("memory.recall._get_client", return_value=mock_client):
            text = recall_for_prompt(
                bank_id="test-bank",
                vendor_name="Sharma Traders",
                vendor_id=1,
            )
        assert isinstance(text, str)
        assert "Approved before" in text


# ===========================================================================
# 5. memory/reflect.py -- vendor profiles
# ===========================================================================

class TestReflect:

    def test_reflect_returns_vendor_profile(self):
        from memory.reflect import reflect_vendor_profile
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "Sharma Traders routinely rounds up by Rs.1-Rs.3. All approvals on record."
        mock_resp.based_on = ["decision-100", "decision-200"]
        mock_client.reflect.return_value = mock_resp

        with patch("memory.reflect._get_client", return_value=mock_client):
            profile = reflect_vendor_profile(
                bank_id="test-bank",
                vendor_name="Sharma Traders",
            )

        assert profile.is_available is True
        assert "Sharma Traders" in profile.text
        assert profile.vendor_name == "Sharma Traders"
        assert len(profile.based_on) == 2

    def test_reflect_error_returns_profile_with_error(self):
        from memory.reflect import reflect_vendor_profile
        mock_client = MagicMock()
        mock_client.reflect.side_effect = Exception("Timeout")

        with patch("memory.reflect._get_client", return_value=mock_client):
            profile = reflect_vendor_profile(
                bank_id="test-bank",
                vendor_name="Error Vendor",
            )

        assert profile.is_available is False
        assert profile.error is not None
        assert "Timeout" in profile.error

    def test_no_memory_message(self):
        from memory.reflect import reflect_no_memory_message
        profile = reflect_no_memory_message("NewVendor")
        assert "NewVendor" in profile.text
        assert profile.is_available is True   # has text, no error

    def test_reflect_str(self):
        from memory.reflect import VendorProfile
        p = VendorProfile(vendor_name="V", text="Some text.", based_on=[])
        assert str(p) == "Some text."

    def test_reflect_str_error(self):
        from memory.reflect import VendorProfile
        p = VendorProfile(vendor_name="V", text="", based_on=[], error="Timeout")
        assert "unavailable" in str(p).lower()


# ===========================================================================
# 6. agent/decide.py -- decision engine
# ===========================================================================

class TestDecisionEngine:

    def _decide(self, invoice=None, issues=None, vendor=None, bank_id="test-bank",
                memory_enabled=True, recall_result=None, llm_response=None,
                has_memory=True):
        from agent.decide import decide

        invoice = invoice or _make_invoice()
        vendor  = vendor  or _make_vendor()
        issues  = issues  if issues is not None else []
        recall  = recall_result or _make_recall_result(approvals=3)
        llm     = llm_response or {
            "decision": "AUTO_APPROVE",
            "reason": "Approved before.",
            "confidence": 0.93,
            "memories_used": ["doc-1"],
        }

        with patch("agent.decide._vendor_has_memory", return_value=has_memory), \
             patch("memory.recall.recall_vendor_history", return_value=recall), \
             patch("agent.llm.call_llm", return_value=llm):
            return decide(invoice, issues, vendor, bank_id, memory_enabled)

    # --- Memory OFF ---

    def test_memory_off_no_issues_is_clean(self):
        result = self._decide(issues=[], memory_enabled=False)
        assert result.decision == "CLEAN"

    def test_memory_off_with_issues_is_flag(self):
        result = self._decide(
            issues=[_make_issue("ROUNDING", {"difference_paise": 100})],
            memory_enabled=False,
        )
        assert result.decision == "FLAG"
        assert result.memory_enabled is False

    def test_memory_off_multiple_issues_all_flagged(self):
        issues = [
            _make_issue("ROUNDING", {}),
            _make_issue("TAX_RATE_MISMATCH", {}),
        ]
        result = self._decide(issues=issues, memory_enabled=False)
        assert result.decision == "FLAG"

    # --- Memory ON, no issues ---

    def test_memory_on_no_issues_is_clean(self):
        result = self._decide(issues=[], memory_enabled=True)
        assert result.decision == "CLEAN"

    # --- Safety rules ---

    def test_invalid_gstin_always_blocked(self):
        issues = [_make_issue("INVALID_GSTIN", {"gstin": "BAD"})]
        result = self._decide(issues=issues, memory_enabled=True)
        assert result.decision == "BLOCK"
        assert result.safety_rule_applied == "INVALID_GSTIN"

    def test_exact_duplicate_always_blocked(self):
        issues = [_make_issue("DUPLICATE", {"duplicate_type": "exact", "matched_invoice_number": "INV-001"})]
        result = self._decide(issues=issues, memory_enabled=True)
        assert result.decision == "BLOCK"
        assert result.safety_rule_applied == "EXACT_DUPLICATE"

    def test_new_vendor_always_flagged(self):
        issues = [_make_issue("ROUNDING", {"difference_paise": 100})]
        result = self._decide(issues=issues, has_memory=False, memory_enabled=True)
        assert result.decision == "FLAG"
        assert result.safety_rule_applied == "NEW_VENDOR"

    def test_high_value_invoice_flagged(self):
        invoice = _make_invoice(total_amount_paise=60000000)  # Rs.6 lakh > Rs.5 lakh limit
        issues = [_make_issue("ROUNDING", {"difference_paise": 100})]
        result = self._decide(invoice=invoice, issues=issues)
        assert result.decision == "FLAG"
        assert result.safety_rule_applied == "HIGH_VALUE"

    # --- LLM returns AUTO_APPROVE with good evidence ---

    def test_auto_approve_with_strong_memory(self):
        issues = [_make_issue("ROUNDING", {"difference_paise": 100})]
        recall = _make_recall_result(
            approvals=4, rejections=0,
            texts=["Sharma Traders ROUNDING APPROVED"]*4,
        )
        result = self._decide(
            issues=issues,
            recall_result=recall,
            llm_response={
                "decision": "AUTO_APPROVE",
                "reason": "Approved 4 times before.",
                "confidence": 0.93,
                "memories_used": ["doc-1"],
            },
        )
        assert result.decision == "AUTO_APPROVE"
        assert result.confidence == 0.93

    # --- Insufficient evidence overrides AUTO_APPROVE ---

    def test_insufficient_evidence_overrides_auto_approve(self):
        issues = [_make_issue("ROUNDING", {"difference_paise": 100})]
        recall = _make_recall_result(approvals=1, rejections=0)  # only 1, need 2
        result = self._decide(
            issues=issues,
            recall_result=recall,
            llm_response={
                "decision": "AUTO_APPROVE",
                "reason": "Seems fine.",
                "confidence": 0.91,
                "memories_used": [],
            },
        )
        assert result.decision == "FLAG"
        assert "1" in result.reason   # mentions count

    # --- Rejection in memory overrides AUTO_APPROVE ---

    def test_rejection_in_memory_overrides_auto_approve(self):
        # Use a small amount difference (Rs.3 = 300 paise) that won't trigger
        # the LARGE_AMOUNT_DIFF safety rule (limit is Rs.100 = 10000 paise)
        issues = [_make_issue("ROUNDING", {"difference_paise": 300})]
        recall = _make_recall_result(
            approvals=3, rejections=1,
            texts=["Sharma Traders ROUNDING REJECTED by accountant"],
        )
        result = self._decide(
            issues=issues,
            recall_result=recall,
            llm_response={
                "decision": "AUTO_APPROVE",
                "reason": "Has been seen before.",
                "confidence": 0.85,
                "memories_used": [],
            },
        )
        assert result.decision == "FLAG"
        assert "rejection" in result.reason.lower() or "reject" in result.reason.lower()

    # --- Low confidence overrides AUTO_APPROVE ---

    def test_low_confidence_overrides_auto_approve(self):
        issues = [_make_issue("ROUNDING", {"difference_paise": 100})]
        recall = _make_recall_result(approvals=3, rejections=0)
        result = self._decide(
            issues=issues,
            recall_result=recall,
            llm_response={
                "decision": "AUTO_APPROVE",
                "reason": "Looks ok.",
                "confidence": 0.55,   # below MIN_CONFIDENCE=0.8
                "memories_used": [],
            },
        )
        assert result.decision == "FLAG"

    # --- Recall failure falls back safely ---

    def test_recall_failure_returns_flag(self):
        issues = [_make_issue("ROUNDING", {"difference_paise": 100})]
        error_recall = _make_recall_result(error="Connection refused")
        result = self._decide(
            issues=issues,
            recall_result=error_recall,
        )
        assert result.decision == "FLAG"
        assert "unavailable" in result.reason.lower() or "memory" in result.reason.lower()

    # --- LLM returns FLAG ---

    def test_llm_flag_respected(self):
        issues = [_make_issue("TAX_RATE_MISMATCH", {"invoice_rate_pct": 18, "po_rate_pct": 12})]
        recall = _make_recall_result(approvals=0, rejections=0)
        result = self._decide(
            issues=issues,
            recall_result=recall,
            llm_response={
                "decision": "FLAG",
                "reason": "First occurrence of this tax rate.",
                "confidence": 0.95,
                "memories_used": [],
            },
        )
        assert result.decision == "FLAG"

    # --- Safety post-check: AI cannot override INVALID_GSTIN even if somehow called ---

    def test_safety_post_check_prevents_ai_approving_blocked_invoice(self):
        """
        Even if somehow the LLM returns AUTO_APPROVE on an invoice with
        INVALID_GSTIN, the post-check must override it to BLOCK.
        """
        issues = [_make_issue("INVALID_GSTIN", {"gstin": "BAD"})]
        # Safety rules fire in Layer 1 so this scenario shouldn't happen in practice,
        # but we test that the post-check also catches it.
        # We directly test _check_safety to verify it catches INVALID_GSTIN.
        from agent.decide import _check_safety
        safety = _check_safety(issues, 118000, has_memory=True)
        assert safety is not None
        assert safety.decision == "BLOCK"

    # --- DecisionResult properties ---

    def test_decision_result_invoice_status_mapping(self):
        from agent.decide import DecisionResult
        assert DecisionResult(decision="AUTO_APPROVE", reason="", confidence=1.0).invoice_status == "AUTO_APPROVED"
        assert DecisionResult(decision="FLAG",         reason="", confidence=1.0).invoice_status == "FLAGGED"
        assert DecisionResult(decision="BLOCK",        reason="", confidence=1.0).invoice_status == "BLOCKED"
        assert DecisionResult(decision="CLEAN",        reason="", confidence=1.0).invoice_status == "CLEAN"

    def test_decision_result_repr(self):
        from agent.decide import DecisionResult
        r = DecisionResult(decision="FLAG", reason="test", confidence=0.9)
        assert "FLAG" in repr(r)
        assert "0.90" in repr(r)
