"""
LedgerMind -- server/routes/batches.py
Upload invoices and query batch status.
"""

import hashlib
import io
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.models import get_db, UploadBatch, Invoice, InvoiceStatus
from server.routes.auth import get_current_user

router = APIRouter(prefix="/api/batches", tags=["batches"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class InvoiceResult(BaseModel):
    invoice_id: int
    invoice_number: str
    vendor_name: str
    invoice_date: str
    total_amount: float
    status: str
    issues: list[dict]
    decision: Optional[str]
    reason: Optional[str]
    confidence: Optional[float]
    memories_used: list[str]

class BatchResponse(BaseModel):
    batch_id: int
    period: str
    memory_enabled: bool
    total: int
    clean: int
    auto_approved: int
    flagged: int
    blocked: int
    skipped: int
    errors: list[str]
    invoices: list[InvoiceResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_bank_id(db: Session) -> str:
    from server.models import Company
    company = db.query(Company).first()
    if company and company.hindsight_bank_id:
        return company.hindsight_bank_id
    return os.getenv("HINDSIGHT_BANK_ID", "ledgermind-demo")


def _build_invoice_results(batch_id: int, db: Session) -> list[InvoiceResult]:
    from server.models import Invoice, Issue, AgentDecision, Vendor
    invoices = db.query(Invoice).filter_by(batch_id=batch_id).all()
    results = []
    for inv in invoices:
        vendor = db.query(Vendor).filter_by(id=inv.vendor_id).first()
        issues = db.query(Issue).filter_by(invoice_id=inv.id).all()
        decision = db.query(AgentDecision).filter_by(invoice_id=inv.id).first()

        results.append(InvoiceResult(
            invoice_id=inv.id,
            invoice_number=inv.invoice_number,
            vendor_name=vendor.name if vendor else "Unknown",
            invoice_date=inv.invoice_date,
            total_amount=inv.total_amount / 100,
            status=inv.status.value,
            issues=[{"type": i.type, "details": i.get_details()} for i in issues],
            decision=decision.decision if decision else None,
            reason=decision.reason if decision else None,
            confidence=decision.confidence / 100 if decision and decision.confidence is not None else None,
            memories_used=decision.get_memories_used() if decision else [],
        ))
    return results


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=BatchResponse)
async def upload_batch(
    invoices_file: UploadFile = File(..., description="Invoices CSV"),
    po_file: Optional[UploadFile] = File(None, description="Purchase Orders CSV (optional)"),
    period: str = Form(..., description="Month period e.g. 2026-07"),
    memory_enabled: bool = Form(True),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Upload a month's invoices.
    Accepts multipart/form-data with:
      - invoices_file: CSV of invoices
      - po_file: (optional) CSV of POs
      - period: YYYY-MM
      - memory_enabled: bool
    """
    inv_bytes = await invoices_file.read()

    # Write to temp files for pipeline
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tf:
        tf.write(inv_bytes)
        inv_tmp = tf.name

    po_tmp = None
    if po_file:
        po_bytes = await po_file.read()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tf:
            tf.write(po_bytes)
            po_tmp = tf.name

    try:
        bank_id = _get_bank_id(db)
        from server.pipeline import process_batch
        summary = process_batch(
            csv_path=inv_tmp,
            period=period,
            memory_enabled=memory_enabled,
            db=db,
            bank_id=bank_id,
            po_csv_path=po_tmp,
            uploaded_by=current_user.id,
        )
    finally:
        os.unlink(inv_tmp)
        if po_tmp:
            os.unlink(po_tmp)

    invoice_results = _build_invoice_results(summary.batch_id, db)

    return BatchResponse(
        batch_id=summary.batch_id,
        period=summary.period,
        memory_enabled=memory_enabled,
        total=summary.total,
        clean=summary.clean,
        auto_approved=summary.auto_approved,
        flagged=summary.flagged,
        blocked=summary.blocked,
        skipped=summary.skipped,
        errors=summary.errors,
        invoices=invoice_results,
    )


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    batch = db.query(UploadBatch).filter_by(id=batch_id).first()
    if not batch:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Batch not found"},
        )

    invoices = db.query(Invoice).filter_by(batch_id=batch_id).all()
    counts = {"CLEAN": 0, "AUTO_APPROVED": 0, "FLAGGED": 0, "BLOCKED": 0}
    for inv in invoices:
        counts[inv.status.value] = counts.get(inv.status.value, 0) + 1

    return BatchResponse(
        batch_id=batch.id,
        period=batch.period,
        memory_enabled=batch.memory_enabled,
        total=len(invoices),
        clean=counts.get("CLEAN", 0),
        auto_approved=counts.get("AUTO_APPROVED", 0),
        flagged=counts.get("FLAGGED", 0),
        blocked=counts.get("BLOCKED", 0),
        skipped=0,
        errors=[],
        invoices=_build_invoice_results(batch_id, db),
    )
