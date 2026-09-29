"""
LedgerMind -- server/tests/test_pipeline.py
Integration tests for the pipeline and FastAPI routes.

Uses an in-memory SQLite database and mocks all external services
(Hindsight, Groq) so no real API keys are needed.

Run with:
    pytest server/tests/test_pipeline.py -v
"""

import csv
import io
import json
import sys
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# ---------------------------------------------------------------------------
# In-memory SQLite fixtures
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# In-memory SQLite fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db():
    """Create a fresh in-memory SQLite DB for each test."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from server.models import Base, Company, User, Vendor, PurchaseOrder, UserRole

    # Use a named in-memory DB so all connections share the same data
    test_engine = create_engine(
        "sqlite:///file::memory:?cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()

    # Seed minimal data
    company = Company(
        id=1, name="Test Co", gstin="27AABCA1234A1ZJ",
        state_code="27", hindsight_bank_id="test-bank",
    )
    session.add(company)

    import hashlib
    admin = User(
        id=1, name="Admin", email="admin@test.com",
        password_hash=hashlib.sha256(b"admin123").hexdigest(),
        role=UserRole.ADMIN, company_id=1,
    )
    session.add(admin)

    vendor = Vendor(id=1, company_id=1, name="Sharma Traders",
                    gstin="27AAPFS1234A1ZH", state_code="27")
    session.add(vendor)

    vendor2 = Vendor(id=2, company_id=1, name="Kumar Steels",
                     gstin="29AABCK5678B1ZF", state_code="29")
    session.add(vendor2)

    po = PurchaseOrder(
        id=1, company_id=1, vendor_id=1, po_number="PO-001",
        amount=118000, gst_rate=1800, po_date="2026-07-01",
    )
    session.add(po)

    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


@pytest.fixture(scope="function")
def app_client(db):
    """FastAPI test client with DB dependency overridden to use the test DB."""
    from fastapi.testclient import TestClient
    from server.main import app
    from server.models import get_db
    from sqlalchemy.orm import sessionmaker

    # Get the engine the test session is bound to
    test_engine = db.get_bind()
    TestSession = sessionmaker(bind=test_engine)

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.clear()


def _auth_headers(client):
    """Login and return auth headers."""
    resp = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "admin123"})
    token = resp.json().get("token", "")
    return {"Authorization": f"Bearer {token}"}


def _make_invoice_csv(rows: list[dict]) -> str:
    """Write invoice rows to a temp CSV file, return its path."""
    fieldnames = [
        "invoice_id", "vendor_id", "invoice_number", "invoice_date",
        "po_number", "vendor_gstin", "taxable_amount_paise", "gst_rate_pct100",
        "cgst_paise", "sgst_paise", "igst_paise", "total_amount_paise", "status", "month",
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return f.name


CLEAN_ROW = {
    "invoice_id": 1, "vendor_id": 1, "invoice_number": "INV-001",
    "invoice_date": "2026-07-15", "po_number": "PO-001",
    "vendor_gstin": "27AAPFS1234A1ZH",
    "taxable_amount_paise": 100000, "gst_rate_pct100": 1800,
    "cgst_paise": 9000, "sgst_paise": 9000, "igst_paise": 0,
    "total_amount_paise": 118000, "status": "UPLOADED", "month": 1,
}

ROUNDING_ROW = {**CLEAN_ROW, "invoice_number": "INV-002", "total_amount_paise": 118200}  # +Rs.2

DUPLICATE_ROW = {**CLEAN_ROW, "invoice_number": "INV-001"}  # exact duplicate

INVALID_GSTIN_ROW = {**CLEAN_ROW, "invoice_number": "INV-003", "vendor_gstin": "BADGSTIN12345"}


# ===========================================================================
# 1. Pipeline tests
# ===========================================================================

class TestProcessBatch:

    def _run(self, db, rows, memory_enabled=False, bank_id="test-bank"):
        """Helper: write rows to CSV and run process_batch."""
        from server.pipeline import process_batch
        csv_path = _make_invoice_csv(rows)
        try:
            with patch("agent.decide._vendor_has_memory", return_value=True), \
                 patch("memory.recall.recall_vendor_history") as mock_recall, \
                 patch("agent.llm.call_llm") as mock_llm, \
                 patch("memory.retain.retain_decision", return_value=True):
                mock_recall.return_value = MagicMock(
                    approval_count=3, rejection_count=0,
                    has_approvals=True, has_rejections=False,
                    raw_text="Approved before", is_empty=False, error=None,
                )
                mock_llm.return_value = {
                    "decision": "AUTO_APPROVE",
                    "reason": "Seen before.",
                    "confidence": 0.93,
                    "memories_used": ["doc-1"],
                }
                return process_batch(
                    csv_path=csv_path,
                    period="2026-07",
                    memory_enabled=memory_enabled,
                    db=db,
                    bank_id=bank_id,
                )
        finally:
            os.unlink(csv_path)

    def test_clean_invoice_status(self, db):
        summary = self._run(db, [CLEAN_ROW], memory_enabled=True)
        assert summary.total == 1
        assert summary.clean == 1
        assert summary.auto_approved == 0

    def test_rounding_issue_detected(self, db):
        summary = self._run(db, [ROUNDING_ROW], memory_enabled=True)
        assert summary.total == 1
        # With strong memory + LLM AUTO_APPROVE -> auto_approved
        assert summary.auto_approved == 1

    def test_rounding_memory_off_is_flagged(self, db):
        summary = self._run(db, [ROUNDING_ROW], memory_enabled=False)
        assert summary.total == 1
        assert summary.flagged == 1
        assert summary.auto_approved == 0

    def test_invalid_gstin_is_blocked(self, db):
        summary = self._run(db, [INVALID_GSTIN_ROW], memory_enabled=True)
        assert summary.total == 1
        assert summary.blocked == 1

    def test_exact_duplicate_is_blocked(self, db):
        # Process once first
        self._run(db, [CLEAN_ROW], memory_enabled=False)
        # Process again with same invoice number
        summary = self._run(db, [DUPLICATE_ROW], memory_enabled=True)
        assert summary.blocked >= 1

    def test_batch_record_created(self, db):
        from server.models import UploadBatch
        before = db.query(UploadBatch).count()
        self._run(db, [CLEAN_ROW])
        after = db.query(UploadBatch).count()
        assert after == before + 1

    def test_issue_records_saved(self, db):
        from server.models import Issue
        self._run(db, [ROUNDING_ROW], memory_enabled=False)
        issues = db.query(Issue).all()
        assert len(issues) >= 1
        types = [i.type for i in issues]
        assert "ROUNDING" in types

    def test_agent_decision_saved(self, db):
        from server.models import AgentDecision
        self._run(db, [CLEAN_ROW], memory_enabled=True)
        decisions = db.query(AgentDecision).all()
        assert len(decisions) >= 1

    def test_unknown_vendor_skipped(self, db):
        bad_row = {**CLEAN_ROW, "vendor_id": 9999, "invoice_number": "INV-BADVENDOR"}
        summary = self._run(db, [bad_row])
        assert summary.skipped >= 1

    def test_malformed_row_skipped(self, db):
        bad_row = {"invoice_number": "X", "invoice_date": "bad"}  # missing most cols
        summary = self._run(db, [bad_row])
        assert summary.skipped >= 1

    def test_multiple_rows_counted(self, db):
        rows = [
            CLEAN_ROW,
            {**ROUNDING_ROW, "invoice_number": "INV-005"},
        ]
        summary = self._run(db, rows, memory_enabled=False)
        assert summary.total == 2
        assert summary.clean == 1
        assert summary.flagged == 1


class TestReviewInvoice:

    def _setup_flagged_invoice(self, db):
        """Create a flagged invoice directly in the DB."""
        from server.models import Invoice, InvoiceStatus, AgentDecision, UploadBatch, Company

        batch = UploadBatch(company_id=1, period="2026-07", memory_enabled=True)
        db.add(batch)
        db.flush()

        inv = Invoice(
            company_id=1, vendor_id=1, invoice_number="REVIEW-001",
            invoice_date="2026-07-20", vendor_gstin="27AAPFS1234A1ZH",
            taxable_amount=100000, gst_rate=1800, cgst=9000, sgst=9000,
            igst=0, total_amount=118000, status=InvoiceStatus.FLAGGED,
            batch_id=batch.id,
        )
        db.add(inv)
        db.flush()
        return inv

    def test_approve_changes_status(self, db):
        from server.pipeline import review_invoice
        from server.models import Invoice, InvoiceStatus

        inv = self._setup_flagged_invoice(db)
        with patch("memory.retain.retain_decision", return_value=True):
            updated = review_invoice(
                invoice_id=inv.id, action="APPROVE",
                note="Looks good.", db=db, bank_id="test-bank",
            )
        assert updated.status == InvoiceStatus.APPROVED

    def test_reject_changes_status(self, db):
        from server.pipeline import review_invoice
        from server.models import InvoiceStatus

        inv = self._setup_flagged_invoice(db)
        with patch("memory.retain.retain_decision", return_value=True):
            updated = review_invoice(
                invoice_id=inv.id, action="REJECT",
                note="Wrong invoice.", db=db, bank_id="test-bank",
            )
        assert updated.status == InvoiceStatus.REJECTED

    def test_hold_changes_status(self, db):
        from server.pipeline import review_invoice
        from server.models import InvoiceStatus

        inv = self._setup_flagged_invoice(db)
        with patch("memory.retain.retain_decision", return_value=True):
            updated = review_invoice(
                invoice_id=inv.id, action="HOLD",
                note="Waiting for credit note.", db=db, bank_id="test-bank",
            )
        assert updated.status == InvoiceStatus.ON_HOLD

    def test_human_review_record_saved(self, db):
        from server.pipeline import review_invoice
        from server.models import HumanReview

        inv = self._setup_flagged_invoice(db)
        with patch("memory.retain.retain_decision", return_value=True):
            review_invoice(
                invoice_id=inv.id, action="APPROVE",
                note="OK.", db=db, bank_id="test-bank",
            )
        review = db.query(HumanReview).filter_by(invoice_id=inv.id).first()
        assert review is not None
        assert review.note == "OK."

    def test_retained_to_memory_set_on_success(self, db):
        from server.pipeline import review_invoice
        from server.models import HumanReview

        inv = self._setup_flagged_invoice(db)
        with patch("memory.retain.retain_decision", return_value=True):
            review_invoice(
                invoice_id=inv.id, action="APPROVE",
                note="OK.", db=db, bank_id="test-bank",
            )
        review = db.query(HumanReview).filter_by(invoice_id=inv.id).first()
        assert review.retained_to_memory is True

    def test_not_found_returns_none(self, db):
        from server.pipeline import review_invoice
        result = review_invoice(
            invoice_id=99999, action="APPROVE",
            note="test", db=db, bank_id="test-bank",
        )
        assert result is None


# ===========================================================================
# 2. FastAPI route tests
# ===========================================================================

class TestAuthRoutes:

    def test_login_success(self, app_client):
        resp = app_client.post("/api/auth/login",
                               json={"email": "admin@test.com", "password": "admin123"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["role"] == "ADMIN"

    def test_login_wrong_password(self, app_client):
        resp = app_client.post("/api/auth/login",
                               json={"email": "admin@test.com", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_email(self, app_client):
        resp = app_client.post("/api/auth/login",
                               json={"email": "nobody@test.com", "password": "x"})
        assert resp.status_code == 401

    def test_me_returns_user(self, app_client):
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/auth/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["email"] == "admin@test.com"

    def test_me_without_token_returns_401(self, app_client):
        resp = app_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, app_client):
        resp = app_client.get("/api/auth/me",
                              headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401


class TestInvoiceRoutes:

    def _seed_invoice(self, db, status="FLAGGED"):
        from server.models import Invoice, InvoiceStatus, UploadBatch
        batch = UploadBatch(company_id=1, period="2026-07", memory_enabled=True)
        db.add(batch)
        db.flush()
        inv = Invoice(
            company_id=1, vendor_id=1, invoice_number="ROUTE-001",
            invoice_date="2026-07-20", vendor_gstin="27AAPFS1234A1ZH",
            taxable_amount=100000, gst_rate=1800, cgst=9000, sgst=9000,
            igst=0, total_amount=118000, status=InvoiceStatus(status),
            batch_id=batch.id,
        )
        db.add(inv)
        db.commit()
        return inv

    def test_list_invoices(self, app_client, db):
        self._seed_invoice(db)
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/invoices", headers=headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_invoices_filter_by_status(self, app_client, db):
        self._seed_invoice(db, status="FLAGGED")
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/invoices?status=flagged", headers=headers)
        assert resp.status_code == 200
        for item in resp.json():
            assert item["status"] == "FLAGGED"

    def test_get_invoice_detail(self, app_client, db):
        inv = self._seed_invoice(db)
        headers = _auth_headers(app_client)
        resp = app_client.get(f"/api/invoices/{inv.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["invoice_number"] == "ROUTE-001"

    def test_get_invoice_not_found(self, app_client):
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/invoices/99999", headers=headers)
        assert resp.status_code == 404

    def test_review_invoice_approve(self, app_client, db):
        inv = self._seed_invoice(db, status="FLAGGED")
        headers = _auth_headers(app_client)
        with patch("memory.retain.retain_decision", return_value=True):
            resp = app_client.post(
                f"/api/invoices/{inv.id}/review",
                json={"action": "APPROVE", "note": "Looks good."},
                headers=headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "APPROVED"

    def test_review_invoice_invalid_action(self, app_client, db):
        inv = self._seed_invoice(db, status="FLAGGED")
        headers = _auth_headers(app_client)
        resp = app_client.post(
            f"/api/invoices/{inv.id}/review",
            json={"action": "DELETE", "note": "nope"},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_overturn_auto_approved(self, app_client, db):
        inv = self._seed_invoice(db, status="AUTO_APPROVED")
        headers = _auth_headers(app_client)
        with patch("memory.retain.retain_decision", return_value=True):
            resp = app_client.post(
                f"/api/invoices/{inv.id}/overturn",
                json={"note": "Agent was wrong."},
                headers=headers,
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "OVERTURNED"

    def test_overturn_flagged_invoice_fails(self, app_client, db):
        inv = self._seed_invoice(db, status="FLAGGED")
        headers = _auth_headers(app_client)
        resp = app_client.post(
            f"/api/invoices/{inv.id}/overturn",
            json={"note": "test"},
            headers=headers,
        )
        assert resp.status_code == 400


class TestVendorRoutes:

    def test_list_vendors(self, app_client):
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/vendors", headers=headers)
        assert resp.status_code == 200
        vendors = resp.json()
        assert any(v["name"] == "Sharma Traders" for v in vendors)

    def test_vendor_memory_returns_profile(self, app_client, db):
        headers = _auth_headers(app_client)
        from server.models import Vendor
        vendor = db.query(Vendor).first()
        mock_profile = MagicMock()
        mock_profile.__str__ = lambda self: "Sharma Traders rounds up by Rs.1-3."
        mock_profile.based_on = ["decision-1"]
        mock_profile.is_available = True
        with patch("memory.reflect.reflect_vendor_profile", return_value=mock_profile):
            resp = app_client.get(f"/api/vendors/{vendor.id}/memory", headers=headers)
        assert resp.status_code == 200
        assert "Sharma" in resp.json()["profile"]

    def test_add_vendor_fact(self, app_client, db):
        headers = _auth_headers(app_client)
        from server.models import Vendor
        vendor = db.query(Vendor).first()
        with patch("memory.retain.retain_vendor_fact", return_value=True):
            resp = app_client.post(
                f"/api/vendors/{vendor.id}/facts",
                json={"fact": "Moved to 18% GST from April"},
                headers=headers,
            )
        assert resp.status_code == 201


class TestStatsRoutes:

    def test_health_check(self, app_client):
        resp = app_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_learning_curve_empty(self, app_client):
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/stats/learning-curve", headers=headers)
        assert resp.status_code == 200
        assert "months" in resp.json()

    def test_get_settings_admin(self, app_client):
        headers = _auth_headers(app_client)
        resp = app_client.get("/api/settings", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "MIN_EVIDENCE" in data
        assert "MIN_CONFIDENCE" in data

    def test_patch_settings(self, app_client):
        headers = _auth_headers(app_client)
        resp = app_client.patch(
            "/api/settings",
            json={"MIN_EVIDENCE": 3},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["MIN_EVIDENCE"] == 3
