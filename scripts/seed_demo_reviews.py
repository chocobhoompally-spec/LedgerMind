"""
LedgerMind — Demo Review Seeder
================================
Populates the database with realistic accountant decisions for Batch 1
and updates Batch 2 invoices to show auto-approvals based on those decisions.

This simulates the core demo story:
  Month 1 (Batch 1): Accountant reviews everything, leaves notes.
  Month 2 (Batch 2): Agent recalls Month 1 decisions, auto-approves known patterns.

Run with:
    python scripts/seed_demo_reviews.py

Safe to run multiple times — checks for existing reviews before inserting.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.models import (
    SessionLocal, Invoice, Vendor, Issue, AgentDecision,
    HumanReview, InvoiceStatus, ReviewAction, User,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_vendor_id(db, name: str) -> int:
    v = db.query(Vendor).filter_by(name=name).first()
    if not v:
        raise ValueError(f"Vendor not found: {name}")
    return v.id


def get_reviewer_id(db) -> int:
    """Return the accountant user's id."""
    user = db.query(User).filter_by(email="priya@ledgermind.demo").first()
    return user.id if user else None


def already_reviewed(db, invoice_id: int) -> bool:
    """Return True if this invoice already has a human review."""
    return db.query(HumanReview).filter_by(invoice_id=invoice_id).first() is not None


def add_review(db, invoice_id: int, action: str, note: str, reviewer_id: int):
    """Add a HumanReview and update invoice status. Skips if already reviewed."""
    if already_reviewed(db, invoice_id):
        return False

    status_map = {
        "APPROVE": InvoiceStatus.APPROVED,
        "REJECT":  InvoiceStatus.REJECTED,
        "HOLD":    InvoiceStatus.ON_HOLD,
    }

    review = HumanReview(
        invoice_id=invoice_id,
        reviewer_id=reviewer_id,
        action=ReviewAction(action),
        note=note,
        retained_to_memory=True,   # mark as retained for demo purposes
        created_at=datetime.now(timezone.utc),
    )
    db.add(review)

    inv = db.query(Invoice).filter_by(id=invoice_id).first()
    if inv:
        inv.status = status_map[action]

    return True


def set_auto_approved(db, invoice_id: int, reason: str, confidence: int = 88,
                      memories_used: list = None):
    """
    Update a Batch 2 invoice to AUTO_APPROVED and set a meaningful reason.
    Only updates if invoice is still FLAGGED.
    """
    inv = db.query(Invoice).filter_by(id=invoice_id).first()
    if not inv or inv.status != InvoiceStatus.FLAGGED:
        return False

    inv.status = InvoiceStatus.AUTO_APPROVED

    # Update (or create) the AgentDecision with a good reason
    dec = db.query(AgentDecision).filter_by(invoice_id=invoice_id).first()
    if dec:
        dec.decision = "AUTO_APPROVE"
        dec.reason = reason
        dec.confidence = confidence
        if memories_used:
            dec.memories_used = json.dumps(memories_used)
    else:
        dec = AgentDecision(
            invoice_id=invoice_id,
            decision="AUTO_APPROVE",
            reason=reason,
            confidence=confidence,
            memories_used=json.dumps(memories_used or []),
            created_at=datetime.now(timezone.utc),
        )
        db.add(dec)

    return True


def update_flag_reason(db, invoice_id: int, reason: str):
    """Update the agent reason on a still-FLAGGED invoice for demo clarity."""
    dec = db.query(AgentDecision).filter_by(invoice_id=invoice_id).first()
    if dec:
        dec.reason = reason


# ---------------------------------------------------------------------------
# Batch 1 — Human reviews
# ---------------------------------------------------------------------------

def seed_batch1_reviews(db):
    """
    Add realistic accountant decisions for all Batch 1 invoices.
    Each decision has a note that explains the vendor's pattern.
    """
    reviewer_id = get_reviewer_id(db)
    added = 0

    # ------- Sharma Traders (id=1): always rounds up by ₹1-₹3 -------
    # id=1  INV-01-M1-001 ROUNDING ₹1.12  — already APPROVED, skip
    # id=3  INV-01-M1-003 ROUNDING+AMOUNT_MISMATCH
    if add_review(db, 3, "APPROVE",
                  "Sharma Traders always rounds up by ₹1–₹3 due to their billing system. "
                  "Amount difference is within their usual freight charge. Approved.",
                  reviewer_id): added += 1

    # id=2  INV-01-M1-002 AMOUNT_MISMATCH only
    if add_review(db, 2, "APPROVE",
                  "Standard freight surcharge from Sharma Traders. They add ₹100–₹200 "
                  "per shipment which isn't in the PO. Acceptable — approve.",
                  reviewer_id): added += 1

    # ------- Kumar Steels (id=2): occasional duplicates, amount variances -------
    # ids 4-7 AMOUNT_MISMATCH
    for inv_id, note in [
        (4, "Kumar Steels consistently adds a ₹350 handling charge not in PO. "
            "Verified with vendor — this is their standard rate. Approve."),
        (5, "Same handling charge as INV-02-M1-001. Approved."),
        (6, "Kumar Steels handling charge applies here too. Approved."),
        (7, "Last of this batch from Kumar Steels. Same pattern. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Reddy Logistics (id=3): amount variance + credit notes -------
    for inv_id, note in [
        (8,  "Reddy Logistics invoices include fuel surcharge not in PO. "
             "This is a known industry practice — we've confirmed with them. Approve."),
        (9,  "Fuel surcharge invoice from Reddy Logistics. Consistent with prior month. Approve."),
        (10, "Same fuel surcharge pattern. Approved."),
        (11, "Reddy Logistics final invoice for July. Fuel surcharge applies. Approve."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Patel Electronics (id=4): amount variance -------
    for inv_id, note in [
        (12, "Patel Electronics adds insurance + packaging to invoice. "
             "Not in PO but consistent every month. Approved."),
        (13, "Insurance + packaging surcharge from Patel Electronics. Approved."),
        (14, "Same pattern as previous Patel Electronics invoices. Approved."),
        (15, "Patel Electronics July batch complete. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Sri Sai Packaging (id=5): minor amount differences -------
    for inv_id, note in [
        (16, "Sri Sai Packaging invoice amount slightly above PO due to GST adjustment. "
             "Small variance — approved."),
        (17, "Standard slight variance from Sri Sai Packaging. Approved."),
        (18, "Sri Sai Packaging consistent pattern — minor variance approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Venkat Chemicals (id=6): amount mismatch + tax type mismatch -------
    # These are genuine issues — put on hold pending clarification
    for inv_id, note in [
        (19, "Venkat Chemicals charged IGST on what should be CGST+SGST (intra-state). "
             "AND amount doesn't match PO. Escalated to Venkat — awaiting correction."),
        (20, "Amount mismatch on Venkat Chemicals. On hold pending vendor correction."),
        (21, "Venkat Chemicals again using wrong tax type. On hold."),
        (22, "Same recurring issue with Venkat Chemicals. Held for vendor to re-issue."),
    ]:
        if add_review(db, inv_id, "HOLD", note, reviewer_id): added += 1

    # ------- Lakshmi Textiles (id=7): amount mismatches -------
    for inv_id, note in [
        (23, "Lakshmi Textiles invoice amount Rs.709 more than PO. "
             "Called vendor — they cannot explain. Rejected."),
        (24, "Lakshmi Textiles amount mismatch Rs.856. No valid reason provided. Rejected."),
        (25, "Another unexplained amount mismatch from Lakshmi Textiles. Rejected."),
        (26, "Lakshmi Textiles consistently overcharging. Rejected and asked to reissue."),
    ]:
        if add_review(db, inv_id, "REJECT", note, reviewer_id): added += 1

    # ------- Global Tech Supplies (id=8): amount mismatches -------
    for inv_id, note in [
        (27, "Global Tech Supplies adds extended warranty charge to invoice. "
             "Not in PO but pre-agreed verbally with procurement. Approve."),
        (28, "Extended warranty charge from Global Tech Supplies. Approved."),
        (29, "Same pattern. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Sunrise Foods (id=9): rounding + amount mismatch -------
    for inv_id, note in [
        (30, "Sunrise Foods rounds up by ₹1–₹3 (billing system). "
             "Small freight addition also included. Both are normal — approved."),
        (31, "Same rounding + freight pattern from Sunrise Foods. Approved."),
        (32, "Sunrise Foods consistent with previous — approved."),
        (33, "Final Sunrise Foods invoice for July. Same pattern. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Metro Furniture (id=10): amount mismatches -------
    for inv_id, note in [
        (34, "Metro Furniture assembly + delivery charges not in PO. "
             "Confirmed with procurement — this is expected. Approve."),
        (35, "Assembly + delivery surcharge from Metro Furniture. Approved."),
        (36, "Same pattern. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Apex Auto Parts (id=11): amount mismatches -------
    for inv_id, note in [
        (37, "Apex Auto Parts adds quality certification fee per batch. "
             "Pre-agreed — not in PO but valid. Approved."),
        (38, "Quality cert fee from Apex Auto Parts. Approved."),
        (39, "Same pattern. Approved."),
        (40, "Final Apex Auto Parts July invoice. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Delta Pharma (id=12): amount mismatch + INVALID_GSTIN (already BLOCKED) -------
    # id=43 is already BLOCKED (INVALID_GSTIN) — skip
    for inv_id, note in [
        (41, "Delta Pharma cold chain surcharge not in PO. "
             "Valid per their contract addendum. Approved."),
        (42, "Cold chain surcharge from Delta Pharma. Approved."),
        (44, "Same cold chain pattern. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # id=43 BLOCKED already — add review to document it
    if add_review(db, 43, "REJECT",
                  "Invalid GSTIN on this invoice. Asked Delta Pharma to reissue with correct GSTIN.",
                  reviewer_id):
        # Override status back to BLOCKED (reject shouldn't override block for safety display)
        inv = db.query(Invoice).filter_by(id=43).first()
        if inv:
            inv.status = InvoiceStatus.BLOCKED
        added += 1

    # ------- National Cables (id=13): amount mismatches -------
    for inv_id, note in [
        (45, "National Cables adds cable testing charges. "
             "Confirmed with engineering team — standard practice. Approved."),
        (46, "Cable testing charges from National Cables. Approved."),
        (47, "Same pattern — approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Pioneer Engineering (id=14): amount mismatch + TAX_RATE_MISMATCH -------
    for inv_id, note in [
        (48, "Pioneer Engineering project management surcharge. Pre-agreed. Approved."),
        (49, "Same surcharge. Approved."),
        (50, "Pioneer Engineering changed GST rate from 12% to 18% on services component. "
             "Called their accounts dept — this is correct per revised GST schedule. Approved. "
             "Note: future invoices at 18% are correct."),
        (51, "Same tax rate change as INV-14-M1-003. Confirmed correct — approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    # ------- Horizon Plastics (id=15): amount mismatches -------
    for inv_id, note in [
        (52, "Horizon Plastics mould maintenance charge not in PO. "
             "Pre-agreed with procurement. Approved."),
        (53, "Mould maintenance charge from Horizon Plastics. Approved."),
        (54, "Same pattern. Approved."),
    ]:
        if add_review(db, inv_id, "APPROVE", note, reviewer_id): added += 1

    db.commit()
    return added


# ---------------------------------------------------------------------------
# Batch 2 — Auto-approvals based on "recalled memory"
# ---------------------------------------------------------------------------

def seed_batch2_auto_approvals(db):
    """
    Update Batch 2 invoices: known vendor patterns → AUTO_APPROVED with reasons.
    New/different issues stay FLAGGED with better explanatory reasons.
    """
    updated = 0

    # ---- Sharma Traders: rounding invoices → AUTO_APPROVE ----
    # ids 55, 58 have ROUNDING issue
    if set_auto_approved(db, 55,
        "Auto-approved: Sharma Traders has a ₹1–₹3 rounding pattern approved 3 times "
        "in July (INV-01-M1-001, INV-01-M1-002, INV-01-M1-003). "
        "Accountant confirmed: 'They always round up — billing system quirk.'",
        confidence=91,
        memories_used=["decision-1", "decision-2", "decision-3"]
    ): updated += 1

    if set_auto_approved(db, 58,
        "Auto-approved: Same rounding pattern from Sharma Traders seen and approved "
        "in all July invoices. No rejections on record for this vendor.",
        confidence=89,
        memories_used=["decision-1", "decision-3"]
    ): updated += 1

    # Sharma Traders pure amount mismatch (freight charge) — auto-approve
    if set_auto_approved(db, 56,
        "Auto-approved: Sharma Traders freight surcharge pattern approved twice in July. "
        "Accountant note: 'standard rate, not in PO but consistent every month.'",
        confidence=87,
        memories_used=["decision-2"]
    ): updated += 1

    if set_auto_approved(db, 57,
        "Auto-approved: Sharma Traders freight surcharge — same as approved in July batch.",
        confidence=86,
        memories_used=["decision-2", "decision-3"]
    ): updated += 1

    # ---- Kumar Steels: handling charge pattern ----
    # ids 60-63 AMOUNT_MISMATCH (same handling charge approved 4× in July)
    kumar_ids = []
    from server.models import Vendor
    kumar = db.query(Vendor).filter_by(name="Kumar Steels").first()
    if kumar:
        batch2_kumar = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=kumar.id, status="FLAGGED"
        ).all()
        for inv in batch2_kumar:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if issues == ["AMOUNT_MISMATCH"]:
                kumar_ids.append(inv.id)

    for inv_id in kumar_ids[:3]:   # approve first 3, leave any duplicate flagged
        if set_auto_approved(db, inv_id,
            "Auto-approved: Kumar Steels Rs.350 handling charge approved 4 times in July. "
            "Accountant confirmed: 'standard rate — verified with vendor directly.'",
            confidence=85,
            memories_used=["decision-4", "decision-5", "decision-6", "decision-7"]
        ): updated += 1

    # Kumar Steels DUPLICATE stays FLAGGED — update reason to be explanatory
    from server.models import Issue as IssueModel
    for issue in db.query(IssueModel).filter_by(type='DUPLICATE').all():
        inv = db.query(Invoice).filter_by(id=issue.invoice_id).first()
        if inv and inv.batch_id == 2:
            update_flag_reason(db, inv.id,
                "Flagged: possible duplicate detected (same vendor + amount + date pattern). "
                "Kumar Steels sent duplicates in previous batches — always requires human review.")

    # ---- Reddy Logistics: fuel surcharge ----
    reddy = db.query(Vendor).filter_by(name="Reddy Logistics").first()
    if reddy:
        batch2_reddy = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=reddy.id, status="FLAGGED"
        ).all()
        for inv in batch2_reddy:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if issues == ["AMOUNT_MISMATCH"]:
                if set_auto_approved(db, inv.id,
                    "Auto-approved: Reddy Logistics fuel surcharge approved 4 times in July. "
                    "Accountant confirmed: 'known industry practice, consistent every month.'",
                    confidence=84,
                    memories_used=["decision-8", "decision-9", "decision-10"]
                ): updated += 1

    # ---- Patel Electronics: tax rate change is NEW in batch 2 → keep FLAGGED ----
    patel = db.query(Vendor).filter_by(name="Patel Electronics").first()
    if patel:
        batch2_patel = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=patel.id, status="FLAGGED"
        ).all()
        for inv in batch2_patel:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if "TAX_RATE_MISMATCH" in issues:
                update_flag_reason(db, inv.id,
                    "Flagged: Patel Electronics is now invoicing at 18% GST but PO specifies 12%. "
                    "This rate change is new — requires human confirmation before auto-approving.")
            elif issues == ["AMOUNT_MISMATCH"]:
                if set_auto_approved(db, inv.id,
                    "Auto-approved: Patel Electronics insurance + packaging surcharge "
                    "approved 4 times in July. Pattern confirmed by accountant.",
                    confidence=83,
                    memories_used=["decision-12", "decision-13"]
                ): updated += 1

    # ---- Sri Sai Packaging: minor amount variance ----
    srisai = db.query(Vendor).filter_by(name="Sri Sai Packaging").first()
    if srisai:
        batch2_srisai = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=srisai.id, status="FLAGGED"
        ).all()
        for inv in batch2_srisai:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if issues == ["AMOUNT_MISMATCH"]:
                if set_auto_approved(db, inv.id,
                    "Auto-approved: Sri Sai Packaging minor GST adjustment variance — "
                    "approved 3 times in July. Small, consistent, low-risk.",
                    confidence=82,
                    memories_used=["decision-16", "decision-17"]
                ): updated += 1

    # ---- Venkat Chemicals: tax type mismatch → always flag ----
    venkat = db.query(Vendor).filter_by(name="Venkat Chemicals").first()
    if venkat:
        batch2_venkat = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=venkat.id, status="FLAGGED"
        ).all()
        for inv in batch2_venkat:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if "TAX_TYPE_MISMATCH" in issues:
                update_flag_reason(db, inv.id,
                    "Flagged: Venkat Chemicals has a recurring tax type mismatch (IGST on "
                    "intra-state supply). This was put on hold in July pending correction — "
                    "cannot auto-approve until vendor resolves the issue.")
            else:
                update_flag_reason(db, inv.id,
                    "Flagged: Venkat Chemicals amount mismatch. Previous invoices in July "
                    "were put on hold for correction — monitoring this vendor closely.")

    # ---- Lakshmi Textiles: amount mismatches were REJECTED → flag again ----
    lakshmi = db.query(Vendor).filter_by(name="Lakshmi Textiles").first()
    if lakshmi:
        batch2_lakshmi = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=lakshmi.id, status="FLAGGED"
        ).all()
        for inv in batch2_lakshmi:
            update_flag_reason(db, inv.id,
                "Flagged: Lakshmi Textiles amount mismatches were rejected in July — "
                "vendor was asked to reissue. Previous rejections on record. "
                "Requires human review.")

    # ---- Sunrise Foods: rounding + amount variance → auto-approve ----
    sunrise = db.query(Vendor).filter_by(name="Sunrise Foods").first()
    if sunrise:
        batch2_sunrise = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=sunrise.id, status="FLAGGED"
        ).all()
        for inv in batch2_sunrise:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if set_auto_approved(db, inv.id,
                "Auto-approved: Sunrise Foods rounding + small freight variance approved "
                "4 times in July. Accountant confirmed: 'consistent billing pattern.'",
                confidence=86,
                memories_used=["decision-30", "decision-31", "decision-32"]
            ): updated += 1

    # ---- Global Tech Supplies: warranty charge ----
    global_tech = db.query(Vendor).filter_by(name="Global Tech Supplies").first()
    if global_tech:
        batch2_global = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=global_tech.id, status="FLAGGED"
        ).all()
        for inv in batch2_global:
            if set_auto_approved(db, inv.id,
                "Auto-approved: Global Tech Supplies extended warranty charge approved "
                "3 times in July. Pre-agreed with procurement.",
                confidence=84,
                memories_used=["decision-27", "decision-28"]
            ): updated += 1

    # ---- Metro Furniture: assembly + delivery ----
    metro = db.query(Vendor).filter_by(name="Metro Furniture").first()
    if metro:
        batch2_metro = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=metro.id, status="FLAGGED"
        ).all()
        for inv in batch2_metro:
            if set_auto_approved(db, inv.id,
                "Auto-approved: Metro Furniture assembly + delivery charges approved "
                "3 times in July. Confirmed with procurement team.",
                confidence=83,
                memories_used=["decision-34", "decision-35"]
            ): updated += 1

    # ---- Apex Auto Parts: quality cert fee ----
    apex = db.query(Vendor).filter_by(name="Apex Auto Parts").first()
    if apex:
        batch2_apex = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=apex.id, status="FLAGGED"
        ).all()
        for inv in batch2_apex:
            if set_auto_approved(db, inv.id,
                "Auto-approved: Apex Auto Parts quality certification fee approved "
                "4 times in July. Pre-agreed per contract.",
                confidence=85,
                memories_used=["decision-37", "decision-38", "decision-39"]
            ): updated += 1

    # ---- Delta Pharma batch 2 ----
    delta = db.query(Vendor).filter_by(name="Delta Pharma").first()
    if delta:
        batch2_delta = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=delta.id, status="FLAGGED"
        ).all()
        for inv in batch2_delta:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if "INVALID_GSTIN" in issues:
                update_flag_reason(db, inv.id,
                    "Flagged: Invalid GSTIN on this Delta Pharma invoice. "
                    "A previous invoice (July) was blocked for the same reason. "
                    "Vendor must reissue with correct GSTIN before processing.")
            elif issues == ["AMOUNT_MISMATCH"]:
                if set_auto_approved(db, inv.id,
                    "Auto-approved: Delta Pharma cold chain surcharge approved "
                    "3 times in July. Valid per contract addendum.",
                    confidence=83,
                    memories_used=["decision-41", "decision-42"]
                ): updated += 1

    # ---- National Cables: cable testing charge ----
    national = db.query(Vendor).filter_by(name="National Cables").first()
    if national:
        batch2_national = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=national.id, status="FLAGGED"
        ).all()
        for inv in batch2_national:
            if set_auto_approved(db, inv.id,
                "Auto-approved: National Cables testing charges approved 3 times in July. "
                "Standard practice confirmed by engineering team.",
                confidence=84,
                memories_used=["decision-45", "decision-46"]
            ): updated += 1

    # ---- Pioneer Engineering: tax rate ----
    pioneer = db.query(Vendor).filter_by(name="Pioneer Engineering").first()
    if pioneer:
        batch2_pioneer = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=pioneer.id, status="FLAGGED"
        ).all()
        for inv in batch2_pioneer:
            issues = [i.type.value for i in db.query(Issue).filter_by(invoice_id=inv.id).all()]
            if "TAX_RATE_MISMATCH" in issues:
                if set_auto_approved(db, inv.id,
                    "Auto-approved: Pioneer Engineering tax rate change from 12% to 18% "
                    "confirmed by accountant in July (INV-14-M1-003, INV-14-M1-004). "
                    "Accountant note: 'correct per revised GST schedule.' Approved.",
                    confidence=90,
                    memories_used=["decision-50", "decision-51"]
                ): updated += 1
            else:
                if set_auto_approved(db, inv.id,
                    "Auto-approved: Pioneer Engineering project management surcharge "
                    "approved in July. Pattern confirmed.",
                    confidence=82,
                    memories_used=["decision-48", "decision-49"]
                ): updated += 1

    # ---- Horizon Plastics: mould maintenance ----
    horizon = db.query(Vendor).filter_by(name="Horizon Plastics").first()
    if horizon:
        batch2_horizon = db.query(Invoice).filter_by(
            batch_id=2, vendor_id=horizon.id, status="FLAGGED"
        ).all()
        for inv in batch2_horizon:
            if set_auto_approved(db, inv.id,
                "Auto-approved: Horizon Plastics mould maintenance charge approved "
                "3 times in July. Pre-agreed with procurement.",
                confidence=83,
                memories_used=["decision-52", "decision-53"]
            ): updated += 1

    db.commit()
    return updated


# ---------------------------------------------------------------------------
# Enable memory for Batch 2 (to match the demo narrative)
# ---------------------------------------------------------------------------

def enable_batch2_memory(db):
    from server.models import UploadBatch
    batch2 = db.query(UploadBatch).filter_by(id=2).first()
    if batch2 and not batch2.memory_enabled:
        batch2.memory_enabled = True
        db.commit()
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\nLedgerMind — Seeding Demo Reviews")
    print("=" * 45)

    db = SessionLocal()
    try:
        # 1. Enable memory for batch 2
        if enable_batch2_memory(db):
            print("  ✓ Batch 2 memory_enabled set to True")
        else:
            print("  · Batch 2 memory already enabled (or not found)")

        # 2. Seed Batch 1 human reviews
        print("\n  Seeding Batch 1 human reviews...")
        added = seed_batch1_reviews(db)
        print(f"  ✓ Added {added} new human reviews to Batch 1")

        # 3. Seed Batch 2 auto-approvals
        print("\n  Seeding Batch 2 auto-approvals...")
        updated = seed_batch2_auto_approvals(db)
        print(f"  ✓ Updated {updated} Batch 2 invoices to AUTO_APPROVED")

        # 4. Summary
        from server.models import Invoice as InvoiceModel, HumanReview as HRModel
        from sqlalchemy import func
        print("\n  Database state after seeding:")
        statuses = db.query(InvoiceModel.status, func.count(InvoiceModel.id)).group_by(
            InvoiceModel.status
        ).all()
        for s, cnt in sorted(statuses, key=lambda x: str(x[0])):
            print(f"    {s.value if hasattr(s,'value') else s}: {cnt}")

        total_reviews = db.query(HRModel).count()
        print(f"\n    Total human reviews: {total_reviews}")

    finally:
        db.close()

    print("\n" + "=" * 45)
    print("Done. Demo data is ready.\n")
    print("Login credentials:")
    print("  admin@ledgermind.demo      password: admin123")
    print("  priya@ledgermind.demo      password: accountant123\n")


if __name__ == "__main__":
    main()
