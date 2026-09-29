"""
LedgerMind -- server/routes/invoices.py
Invoice listing, detail, review (approve/reject/hold), and overturn.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.models import get_db, Invoice, InvoiceStatus
from server.routes.auth import get_current_user

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class IssueOut(BaseModel):
    type: str
    details: dict

class DecisionOut(BaseModel):
    decision: str
    reason: str
    confidence: Optional[float]
    memories_used: list[str]
    safety_rule_applied: Optional[str]

class ReviewOut(BaseModel):
    action: str
    note: Optional[str]
    retained_to_memory: bool
    created_at: str

class InvoiceListItem(BaseModel):
    invoice_id: int
    invoice_number: str
    vendor_name: str
    invoice_date: str
    total_amount: float
    status: str
    issue_count: int
    decision: Optional[str]
    reason: Optional[str]

class InvoiceDetail(BaseModel):
    invoice_id: int
    invoice_number: str
    vendor_id: int
    vendor_name: str
    vendor_gstin: str
    invoice_date: str
    po_number: Optional[str]
    taxable_amount: float
    gst_rate: float
    cgst: float
    sgst: float
    igst: float
    total_amount: float
    status: str
    batch_id: Optional[int]
    issues: list[IssueOut]
    agent_decision: Optional[DecisionOut]
    reviews: list[ReviewOut]

class ReviewRequest(BaseModel):
    action: str   # APPROVE | REJECT | HOLD
    note: str

class OverturnRequest(BaseModel):
    note: str


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_bank_id(db: Session) -> str:
    from server.models import Company
    company = db.query(Company).first()
    if company and company.hindsight_bank_id:
        return company.hindsight_bank_id
    return os.getenv("HINDSIGHT_BANK_ID", "ledgermind-demo")


def _invoice_detail(invoice_id: int, db: Session) -> InvoiceDetail:
    from server.models import Issue, AgentDecision, HumanReview, Vendor
    inv = db.query(Invoice).filter_by(id=invoice_id).first()
    if not inv:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Invoice not found"},
        )
    vendor = db.query(Vendor).filter_by(id=inv.vendor_id).first()
    issues = db.query(Issue).filter_by(invoice_id=invoice_id).all()
    dec = db.query(AgentDecision).filter_by(invoice_id=invoice_id).order_by(
        AgentDecision.created_at.desc()
    ).first()
    reviews = db.query(HumanReview).filter_by(invoice_id=invoice_id).order_by(
        HumanReview.created_at.desc()
    ).all()

    return InvoiceDetail(
        invoice_id=inv.id,
        invoice_number=inv.invoice_number,
        vendor_id=inv.vendor_id,
        vendor_name=vendor.name if vendor else "Unknown",
        vendor_gstin=inv.vendor_gstin or "",
        invoice_date=inv.invoice_date,
        po_number=inv.po_number,
        taxable_amount=inv.taxable_amount / 100,
        gst_rate=inv.gst_rate / 100,
        cgst=inv.cgst / 100,
        sgst=inv.sgst / 100,
        igst=inv.igst / 100,
        total_amount=inv.total_amount / 100,
        status=inv.status.value,
        batch_id=inv.batch_id,
        issues=[IssueOut(type=i.type, details=i.get_details()) for i in issues],
        agent_decision=DecisionOut(
            decision=dec.decision,
            reason=dec.reason or "",
            confidence=dec.confidence / 100 if dec.confidence is not None else None,
            memories_used=dec.get_memories_used(),
            safety_rule_applied=dec.safety_rule_applied,
        ) if dec else None,
        reviews=[
            ReviewOut(
                action=r.action.value,
                note=r.note,
                retained_to_memory=r.retained_to_memory,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in reviews
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[InvoiceListItem])
def list_invoices(
    status_filter: Optional[str] = Query(None, alias="status"),
    batch_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from server.models import Issue, AgentDecision, Vendor

    query = db.query(Invoice)
    if status_filter:
        try:
            query = query.filter(Invoice.status == InvoiceStatus(status_filter.upper()))
        except ValueError:
            raise HTTPException(400, detail={"code": "INVALID_STATUS", "message": f"Unknown status: {status_filter}"})
    if batch_id:
        query = query.filter(Invoice.batch_id == batch_id)

    total = query.count()
    invoices = query.offset((page - 1) * page_size).limit(page_size).all()

    result = []
    for inv in invoices:
        vendor = db.query(Vendor).filter_by(id=inv.vendor_id).first()
        issue_count = db.query(Issue).filter_by(invoice_id=inv.id).count()
        dec = db.query(AgentDecision).filter_by(invoice_id=inv.id).order_by(
            AgentDecision.created_at.desc()
        ).first()
        result.append(InvoiceListItem(
            invoice_id=inv.id,
            invoice_number=inv.invoice_number,
            vendor_name=vendor.name if vendor else "Unknown",
            invoice_date=inv.invoice_date,
            total_amount=inv.total_amount / 100,
            status=inv.status.value,
            issue_count=issue_count,
            decision=dec.decision if dec else None,
            reason=dec.reason if dec else None,
        ))
    return result


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _invoice_detail(invoice_id, db)


@router.post("/{invoice_id}/review", response_model=InvoiceDetail)
def review_invoice(
    invoice_id: int,
    body: ReviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    valid_actions = {"APPROVE", "REJECT", "HOLD"}
    if body.action.upper() not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ACTION", "message": f"action must be one of {valid_actions}"},
        )

    inv = db.query(Invoice).filter_by(id=invoice_id).first()
    if not inv:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Invoice not found"})

    if inv.status not in (InvoiceStatus.FLAGGED, InvoiceStatus.BLOCKED, InvoiceStatus.ON_HOLD):
        raise HTTPException(
            400,
            detail={"code": "INVALID_STATE", "message": f"Cannot review invoice with status {inv.status.value}"},
        )

    from server.pipeline import review_invoice as pipeline_review
    bank_id = _get_bank_id(db)
    updated = pipeline_review(
        invoice_id=invoice_id,
        action=body.action.upper(),
        note=body.note,
        db=db,
        bank_id=bank_id,
        reviewer_id=current_user.id,
        reviewer_name=current_user.name,
    )
    if not updated:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Invoice not found"})

    return _invoice_detail(invoice_id, db)


@router.post("/{invoice_id}/overturn", response_model=InvoiceDetail)
def overturn_invoice(
    invoice_id: int,
    body: OverturnRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    inv = db.query(Invoice).filter_by(id=invoice_id).first()
    if not inv:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Invoice not found"})

    if inv.status != InvoiceStatus.AUTO_APPROVED:
        raise HTTPException(
            400,
            detail={
                "code": "INVALID_STATE",
                "message": "Can only overturn AUTO_APPROVED invoices",
            },
        )

    from server.pipeline import review_invoice as pipeline_review
    bank_id = _get_bank_id(db)
    pipeline_review(
        invoice_id=invoice_id,
        action="OVERTURN",
        note=body.note,
        db=db,
        bank_id=bank_id,
        reviewer_id=current_user.id,
        reviewer_name=current_user.name,
    )
    return _invoice_detail(invoice_id, db)
