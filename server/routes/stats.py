"""
LedgerMind -- server/routes/stats.py
Learning curve metrics and app settings.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.models import get_db, Invoice, UploadBatch, HumanReview, InvoiceStatus
from server.routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["stats"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MonthMetrics(BaseModel):
    period: str
    total_invoices: int
    invoices_with_issues: int
    auto_handled: int
    auto_handled_rate: float    # 0.0 - 1.0
    human_reviews: int
    overturned: int
    overturn_rate: float
    blocked: int
    memory_enabled: bool

class LearningCurveResponse(BaseModel):
    months: list[MonthMetrics]

class SettingsOut(BaseModel):
    ROUNDING_LIMIT: int
    MIN_EVIDENCE: int
    MIN_CONFIDENCE: float
    MAX_AUTO_APPROVE_AMOUNT: int
    MAX_AUTO_APPROVE_DIFF: int
    LLM_MODEL: str
    LLM_MAX_RETRIES: int

class SettingsPatch(BaseModel):
    ROUNDING_LIMIT: Optional[int] = None
    MIN_EVIDENCE: Optional[int] = None
    MIN_CONFIDENCE: Optional[float] = None
    MAX_AUTO_APPROVE_AMOUNT: Optional[int] = None
    MAX_AUTO_APPROVE_DIFF: Optional[int] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/stats/learning-curve", response_model=LearningCurveResponse)
def learning_curve(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Return per-month metrics for the learning curve chart.
    Groups by UploadBatch.period.
    """
    from server.models import AgentDecision

    batches = db.query(UploadBatch).order_by(UploadBatch.period).all()
    months = []

    for batch in batches:
        invoices = db.query(Invoice).filter_by(batch_id=batch.id).all()
        total = len(invoices)
        if total == 0:
            continue

        clean = sum(1 for i in invoices if i.status == InvoiceStatus.CLEAN)
        # Still sitting as AUTO_APPROVED (not yet overturned)
        auto_approved = sum(1 for i in invoices if i.status == InvoiceStatus.AUTO_APPROVED)
        # Invoices that went through the human review queue
        flagged = sum(1 for i in invoices if i.status in (
            InvoiceStatus.FLAGGED, InvoiceStatus.APPROVED,
            InvoiceStatus.REJECTED, InvoiceStatus.ON_HOLD,
        ))
        blocked = sum(1 for i in invoices if i.status == InvoiceStatus.BLOCKED)
        # OVERTURNED = was auto-approved but a human corrected it;
        # counts as both "originally auto-handled" and "required human review"
        overturned = sum(1 for i in invoices if i.status == InvoiceStatus.OVERTURNED)

        invoices_with_issues = total - clean
        # auto_handled = currently auto-approved + overturned (both were originally
        # auto-approved; the rate tracks how many the agent tried to handle on its own)
        auto_handled = auto_approved + overturned
        # human_reviews = flagged queue + blocked + overturned (human had to step in)
        human_reviews = flagged + blocked + overturned
        auto_rate = auto_handled / invoices_with_issues if invoices_with_issues > 0 else 0.0
        # overturn_rate = fraction of original auto-approvals that were later corrected
        original_auto = auto_approved + overturned  # total ever auto-approved
        overturn_rate = overturned / original_auto if original_auto > 0 else 0.0

        months.append(MonthMetrics(
            period=batch.period,
            total_invoices=total,
            invoices_with_issues=invoices_with_issues,
            auto_handled=auto_handled,
            auto_handled_rate=round(auto_rate, 3),
            human_reviews=human_reviews,
            overturned=overturned,
            overturn_rate=round(overturn_rate, 3),
            blocked=blocked,
            memory_enabled=batch.memory_enabled,
        ))

    return LearningCurveResponse(months=months)


@router.get("/settings", response_model=SettingsOut)
def get_settings(current_user=Depends(require_admin)):
    return SettingsOut(
        ROUNDING_LIMIT=int(os.getenv("ROUNDING_LIMIT", "10")),
        MIN_EVIDENCE=int(os.getenv("MIN_EVIDENCE", "2")),
        MIN_CONFIDENCE=float(os.getenv("MIN_CONFIDENCE", "0.8")),
        MAX_AUTO_APPROVE_AMOUNT=int(os.getenv("MAX_AUTO_APPROVE_AMOUNT", "500000")),
        MAX_AUTO_APPROVE_DIFF=int(os.getenv("MAX_AUTO_APPROVE_DIFF", "100")),
        LLM_MODEL=os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
        LLM_MAX_RETRIES=int(os.getenv("LLM_MAX_RETRIES", "3")),
    )


@router.patch("/settings", response_model=SettingsOut)
def update_settings(body: SettingsPatch, current_user=Depends(require_admin)):
    """
    Update runtime settings via environment variables.
    In production this would persist to a config store.
    For the demo, we update os.environ in-process.
    """
    mapping = {
        "ROUNDING_LIMIT": body.ROUNDING_LIMIT,
        "MIN_EVIDENCE": body.MIN_EVIDENCE,
        "MIN_CONFIDENCE": body.MIN_CONFIDENCE,
        "MAX_AUTO_APPROVE_AMOUNT": body.MAX_AUTO_APPROVE_AMOUNT,
        "MAX_AUTO_APPROVE_DIFF": body.MAX_AUTO_APPROVE_DIFF,
    }
    for key, val in mapping.items():
        if val is not None:
            os.environ[key] = str(val)

    return get_settings(current_user)
