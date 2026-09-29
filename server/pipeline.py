"""
LedgerMind -- server/pipeline.py
The upload pipeline and review workflow.

Two public functions:
    process_batch(csv_path, period, memory_enabled, po_csv_path, db, bank_id)
        --> reads a CSV of invoices, runs checks, runs AI decisions, saves to DB
        --> returns BatchSummary

    review_invoice(invoice_id, action, note, reviewer_name, db, bank_id)
        --> saves a HumanReview, updates invoice status, retains to memory
        --> returns updated Invoice
"""

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Summary dataclass returned by process_batch
# ---------------------------------------------------------------------------

@dataclass
class BatchSummary:
    batch_id: int
    period: str
    total: int = 0
    clean: int = 0
    auto_approved: int = 0
    flagged: int = 0
    blocked: int = 0
    skipped: int = 0          # rows that failed parsing
    errors: list[str] = field(default_factory=list)

    def __str__(self):
        return (
            f"Month {self.period}: {self.total} invoices | "
            f"{self.clean} clean | "
            f"{self.auto_approved} auto-approved | "
            f"{self.flagged} flagged | "
            f"{self.blocked} blocked | "
            f"{self.skipped} skipped"
        )


# ---------------------------------------------------------------------------
# Row parser
# ---------------------------------------------------------------------------

REQUIRED_COLS = {
    "invoice_number", "invoice_date", "vendor_id",
    "vendor_gstin", "taxable_amount_paise", "gst_rate_pct100",
    "cgst_paise", "sgst_paise", "igst_paise", "total_amount_paise",
}

def _parse_row(row: dict) -> tuple[Optional[dict], Optional[str]]:
    """
    Parse and validate one CSV row.
    Returns (parsed_dict, None) on success or (None, error_message) on failure.
    """
    missing = REQUIRED_COLS - row.keys()
    if missing:
        return None, f"Missing columns: {missing}"

    try:
        parsed = {
            "invoice_number":     row["invoice_number"].strip(),
            "invoice_date":       row["invoice_date"].strip(),
            "vendor_id":          int(row["vendor_id"]),
            "vendor_gstin":       row["vendor_gstin"].strip(),
            "taxable_amount":     int(row["taxable_amount_paise"]),
            "gst_rate":           int(row["gst_rate_pct100"]),
            "cgst":               int(row["cgst_paise"]),
            "sgst":               int(row["sgst_paise"]),
            "igst":               int(row["igst_paise"]),
            "total_amount":       int(row["total_amount_paise"]),
            "po_number":          row.get("po_number", "").strip() or None,
        }
        if not parsed["invoice_number"]:
            return None, "invoice_number is empty"
        return parsed, None
    except (ValueError, KeyError) as e:
        return None, f"Parse error: {e}"


# ---------------------------------------------------------------------------
# process_batch
# ---------------------------------------------------------------------------

def process_batch(
    csv_path: str,
    period: str,
    memory_enabled: bool,
    db,
    bank_id: str,
    po_csv_path: Optional[str] = None,
    uploaded_by: Optional[int] = None,
) -> BatchSummary:
    """
    Main upload pipeline: CSV -> checks -> AI decision -> saved to DB.

    Args:
        csv_path:       Path to invoices CSV file.
        period:         Month string e.g. "2026-07".
        memory_enabled: Whether to use Hindsight memory + AI decisions.
        db:             SQLAlchemy session.
        bank_id:        Hindsight bank ID for this company.
        po_csv_path:    Optional path to POs CSV (supplements DB POs).
        uploaded_by:    Optional user ID who triggered the upload.

    Returns:
        BatchSummary with counts per decision type.
    """
    from server.models import (
        UploadBatch, Invoice, Issue as IssueModel,
        AgentDecision, InvoiceStatus,
    )
    from server.checks.rules import run_all_checks, InvoiceData
    from agent.decide import decide, InvoiceInput, VendorInput

    # ------------------------------------------------------------------
    # Load supplementary POs from CSV if provided
    # ------------------------------------------------------------------
    extra_pos: dict[str, dict] = {}
    if po_csv_path and os.path.exists(po_csv_path):
        with open(po_csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                po_num = row.get("po_number", "").strip()
                if po_num:
                    extra_pos[po_num] = row

    # ------------------------------------------------------------------
    # Create UploadBatch record
    # ------------------------------------------------------------------
    from server.models import Company
    company = db.query(Company).first()
    company_id = company.id if company else 1

    batch = UploadBatch(
        company_id=company_id,
        period=period,
        uploaded_by=uploaded_by,
        memory_enabled=memory_enabled,
        uploaded_at=datetime.now(timezone.utc),
    )
    db.add(batch)
    db.flush()   # get batch.id without committing

    summary = BatchSummary(batch_id=batch.id, period=period)

    # ------------------------------------------------------------------
    # Load all existing invoices for duplicate detection (per vendor)
    # ------------------------------------------------------------------
    from server.models import Invoice as InvoiceModel, Vendor
    existing_by_vendor: dict[int, list[dict]] = {}
    for inv in db.query(InvoiceModel).filter_by(company_id=company_id).all():
        existing_by_vendor.setdefault(inv.vendor_id, []).append({
            "invoice_number": inv.invoice_number,
            "total_amount": inv.total_amount,
            "invoice_date": inv.invoice_date,
        })

    # ------------------------------------------------------------------
    # Read CSV rows
    # ------------------------------------------------------------------
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        summary.errors.append(f"Could not read CSV: {e}")
        db.commit()
        return summary

    for row in rows:
        summary.total += 1
        parsed, err = _parse_row(row)

        if err:
            summary.skipped += 1
            summary.errors.append(f"Row {summary.total}: {err}")
            log.warning("Skipping row %d: %s", summary.total, err)
            continue

        vendor_id = parsed["vendor_id"]

        # Lookup vendor
        vendor = db.query(Vendor).filter_by(id=vendor_id).first()
        if not vendor:
            summary.skipped += 1
            summary.errors.append(f"Row {summary.total}: vendor_id={vendor_id} not found")
            continue

        # Lookup PO from DB or extra_pos CSV
        po_record = None
        po_amount = None
        po_gst_rate = None
        if parsed["po_number"]:
            from server.models import PurchaseOrder
            po_record = db.query(PurchaseOrder).filter_by(
                company_id=company_id,
                po_number=parsed["po_number"],
            ).first()
            if po_record:
                po_amount = po_record.amount
                po_gst_rate = po_record.gst_rate
            elif parsed["po_number"] in extra_pos:
                ep = extra_pos[parsed["po_number"]]
                try:
                    po_amount = int(ep["amount_paise"])
                    po_gst_rate = int(ep["gst_rate_pct100"])
                except (KeyError, ValueError):
                    pass

        # ---- Step 1: Save Invoice UPLOADED ----
        invoice = InvoiceModel(
            company_id=company_id,
            vendor_id=vendor_id,
            invoice_number=parsed["invoice_number"],
            invoice_date=parsed["invoice_date"],
            po_number=parsed["po_number"],
            vendor_gstin=parsed["vendor_gstin"],
            taxable_amount=parsed["taxable_amount"],
            gst_rate=parsed["gst_rate"],
            cgst=parsed["cgst"],
            sgst=parsed["sgst"],
            igst=parsed["igst"],
            total_amount=parsed["total_amount"],
            status=InvoiceStatus.UPLOADED,
            batch_id=batch.id,
        )
        db.add(invoice)
        db.flush()

        # ---- Step 2: Run GST checks -> CHECKED ----
        existing_invoices = existing_by_vendor.get(vendor_id, [])
        inv_data = InvoiceData(
            invoice_number=parsed["invoice_number"],
            invoice_date=parsed["invoice_date"],
            vendor_id=vendor_id,
            vendor_gstin=parsed["vendor_gstin"],
            registered_gstin=vendor.gstin or "",
            taxable_amount=parsed["taxable_amount"],
            gst_rate=parsed["gst_rate"],
            cgst=parsed["cgst"],
            sgst=parsed["sgst"],
            igst=parsed["igst"],
            total_amount=parsed["total_amount"],
            po_number=parsed["po_number"],
            po_amount=po_amount,
            po_gst_rate=po_gst_rate,
            vendor_state_code=vendor.state_code,
            buyer_state_code=_get_buyer_state(db, company_id),
            existing_invoices=existing_invoices,
        )
        check_issues = run_all_checks(inv_data)

        # Save Issue records
        for ci in check_issues:
            issue_rec = IssueModel(
                invoice_id=invoice.id,
                type=ci.type,
            )
            issue_rec.set_details(ci.details)
            db.add(issue_rec)

        invoice.status = InvoiceStatus.CHECKED
        db.flush()

        # ---- Step 3: AI/memory decision ----
        inv_input = InvoiceInput(
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            vendor_id=vendor_id,
            vendor_name=vendor.name,
            vendor_gstin=invoice.vendor_gstin,
            total_amount_paise=invoice.total_amount,
            po_number=invoice.po_number,
        )
        vendor_input = VendorInput(
            vendor_id=vendor.id,
            vendor_name=vendor.name,
            registered_gstin=vendor.gstin or "",
        )

        decision_result = decide(
            invoice=inv_input,
            issues=check_issues,
            vendor=vendor_input,
            bank_id=bank_id,
            memory_enabled=memory_enabled,
        )

        # Save AgentDecision
        agent_dec = AgentDecision(
            invoice_id=invoice.id,
            decision=decision_result.decision,
            reason=decision_result.reason,
            confidence=int(decision_result.confidence * 100),
            safety_rule_applied=decision_result.safety_rule_applied,
        )
        agent_dec.set_memories_used(decision_result.memories_used)
        db.add(agent_dec)

        # Update invoice status
        new_status = InvoiceStatus(decision_result.invoice_status)
        invoice.status = new_status
        db.flush()

        # If auto-approved, retain to memory
        if decision_result.decision == "AUTO_APPROVE" and memory_enabled:
            _retain_auto_approval(
                bank_id=bank_id,
                invoice=invoice,
                vendor=vendor,
                issues=check_issues,
                decision_result=decision_result,
            )

        # Count into summary
        _count(summary, decision_result.decision)

        # Add to existing for next iterations (duplicate detection)
        existing_by_vendor.setdefault(vendor_id, []).append({
            "invoice_number": invoice.invoice_number,
            "total_amount": invoice.total_amount,
            "invoice_date": invoice.invoice_date,
        })

    db.commit()
    log.info("Batch %d complete: %s", batch.id, summary)
    return summary


# ---------------------------------------------------------------------------
# review_invoice
# ---------------------------------------------------------------------------

def review_invoice(
    invoice_id: int,
    action: str,          # APPROVE | REJECT | HOLD | OVERTURN
    note: str,
    db,
    bank_id: str,
    reviewer_id: Optional[int] = None,
    reviewer_name: Optional[str] = "Accountant",
) -> Optional[object]:
    """
    Record an accountant decision on a flagged or blocked invoice.

    Steps:
        1. Load invoice + vendor
        2. Save HumanReview record
        3. Update invoice status
        4. Retain decision to Hindsight memory
        5. Mark retained_to_memory = True

    Returns the updated Invoice ORM object, or None if not found.
    """
    from server.models import (
        Invoice, HumanReview, Issue as IssueModel,
        InvoiceStatus, ReviewAction, Vendor,
    )
    from memory.retain import retain_decision

    invoice = db.query(Invoice).filter_by(id=invoice_id).first()
    if not invoice:
        log.warning("review_invoice: invoice_id=%d not found", invoice_id)
        return None

    vendor = db.query(Vendor).filter_by(id=invoice.vendor_id).first()
    vendor_name = vendor.name if vendor else "Unknown Vendor"

    # Map action -> new invoice status
    status_map = {
        "APPROVE":  InvoiceStatus.APPROVED,
        "REJECT":   InvoiceStatus.REJECTED,
        "HOLD":     InvoiceStatus.ON_HOLD,
        "OVERTURN": InvoiceStatus.OVERTURNED,
    }
    new_status = status_map.get(action.upper(), InvoiceStatus.FLAGGED)

    # Save HumanReview
    review = HumanReview(
        invoice_id=invoice_id,
        reviewer_id=reviewer_id,
        action=ReviewAction(action.upper()),
        note=note,
        retained_to_memory=False,
    )
    db.add(review)
    invoice.status = new_status
    db.flush()

    # Load issues for memory retention
    issues = db.query(IssueModel).filter_by(invoice_id=invoice_id).all()
    issue_list = [{"type": i.type, "details": i.get_details()} for i in issues]

    # Retain to Hindsight
    retained = retain_decision(
        bank_id=bank_id,
        invoice_id=invoice_id,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        vendor_name=vendor_name,
        vendor_id=invoice.vendor_id,
        total_amount_paise=invoice.total_amount,
        issues=issue_list,
        action=action.upper(),
        note=note,
        reviewer=reviewer_name,
    )

    review.retained_to_memory = retained
    db.commit()
    log.info(
        "review_invoice: invoice=%s action=%s retained=%s",
        invoice.invoice_number, action, retained,
    )
    return invoice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUYER_STATE_CACHE: dict[int, str] = {}

def _get_buyer_state(db, company_id: int) -> str:
    """Cache the buyer company's state code."""
    if company_id not in _BUYER_STATE_CACHE:
        from server.models import Company
        company = db.query(Company).filter_by(id=company_id).first()
        _BUYER_STATE_CACHE[company_id] = company.state_code if company else "27"
    return _BUYER_STATE_CACHE[company_id]


def _count(summary: BatchSummary, decision: str) -> None:
    if decision == "CLEAN":
        summary.clean += 1
    elif decision == "AUTO_APPROVE":
        summary.auto_approved += 1
    elif decision == "FLAG":
        summary.flagged += 1
    elif decision == "BLOCK":
        summary.blocked += 1


def _retain_auto_approval(bank_id, invoice, vendor, issues, decision_result) -> None:
    """Retain an agent auto-approval to Hindsight memory."""
    from memory.retain import retain_decision
    # issues here are CheckIssue dataclasses (from run_all_checks), whose .details is
    # always a dict.  Use get_details() if it's an ORM Issue object (whose .details is
    # a JSON string), falling back to parsing the string directly.
    def _get_details(i):
        if hasattr(i, "get_details"):
            return i.get_details()  # ORM Issue object -> dict
        if hasattr(i, "details"):
            d = i.details
            if isinstance(d, dict):
                return d
            if isinstance(d, str):
                import json
                try:
                    return json.loads(d)
                except (ValueError, TypeError):
                    return {}
        return {}

    issue_list = [{"type": i.type, "details": _get_details(i)} for i in issues]
    retain_decision(
        bank_id=bank_id,
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        vendor_name=vendor.name,
        vendor_id=vendor.id,
        total_amount_paise=invoice.total_amount,
        issues=issue_list,
        action="AUTO_APPROVE",
        note=decision_result.reason,
        confidence=decision_result.confidence,
        memories_used=decision_result.memories_used,
    )


def retry_unretained_reviews(db, bank_id: str) -> int:
    """
    On startup: retry retaining any HumanReview records where retained_to_memory=False.
    Returns number of successfully retried records.
    """
    from server.models import HumanReview, Invoice, Vendor, Issue as IssueModel
    from memory.retain import retain_decision

    unretained = db.query(HumanReview).filter_by(retained_to_memory=False).all()
    retried = 0
    for review in unretained:
        invoice = db.query(Invoice).filter_by(id=review.invoice_id).first()
        if not invoice:
            continue
        vendor = db.query(Vendor).filter_by(id=invoice.vendor_id).first()
        issues = db.query(IssueModel).filter_by(invoice_id=invoice.id).all()
        issue_list = [{"type": i.type, "details": i.get_details()} for i in issues]

        ok = retain_decision(
            bank_id=bank_id,
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            vendor_name=vendor.name if vendor else "Unknown",
            vendor_id=invoice.vendor_id,
            total_amount_paise=invoice.total_amount,
            issues=issue_list,
            action=review.action.value,
            note=review.note,
        )
        if ok:
            review.retained_to_memory = True
            retried += 1

    if retried:
        db.commit()
    return retried
