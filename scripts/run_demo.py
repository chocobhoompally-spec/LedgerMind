"""
LedgerMind -- scripts/run_demo.py
End-to-end 3-month demo: upload invoices, simulate accountant reviews,
show learning curve, and compare Memory ON vs OFF.

Usage:
    python scripts/run_demo.py --memory on
    python scripts/run_demo.py --memory off
    python scripts/run_demo.py --compare        # run both and show diff table
"""

import argparse
import os
import sys
from pathlib import Path

# Make sure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from server.models import Base, SessionLocal, engine, InvoiceStatus
from server.setup import setup
from server.pipeline import process_batch, review_invoice, BatchSummary

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data"
MONTHS = [
    {"period": "2026-07", "csv": DATA_DIR / "invoices_month1.csv", "label": "Month 1 (Jul 2026)"},
    {"period": "2026-08", "csv": DATA_DIR / "invoices_month2.csv", "label": "Month 2 (Aug 2026)"},
    {"period": "2026-09", "csv": DATA_DIR / "invoices_month3.csv", "label": "Month 3 (Sep 2026)"},
]
PO_CSV = DATA_DIR / "purchase_orders.csv"
BANK_ID = os.getenv("HINDSIGHT_BANK_ID", "ledgermind-demo")

# Simulated accountant decisions for Month 1 (vendor_name -> action, note)
MONTH1_REVIEWS = {
    "Sharma Traders":    ("APPROVE", "They always round up by Rs.1-3, this is normal."),
    "Kumar Steels":      ("REJECT",  "Duplicate invoice - do not pay."),
    "Reddy Logistics":   ("HOLD",    "Waiting for credit note, will resolve in 2 weeks."),
    "Patel Electronics": ("APPROVE", "Tax rate changed to 18% from this month, confirmed with vendor."),
    "Venkat Chemicals":  ("REJECT",  "Wrong tax type used - vendor must reissue with IGST."),
    "Lakshmi Textiles":  ("REJECT",  "Amount does not match PO - invoice is wrong."),
    "Metro Furniture":   ("APPROVE", "PO number was missing but verbally confirmed."),
    "Delta Pharma":      ("REJECT",  "GSTIN on invoice is invalid - vendor must reissue."),
    "Pioneer Engineering": ("APPROVE", "Tax rate discrepancy confirmed as their error, will fix next month."),
    "Sunrise Foods":     ("APPROVE", "Small rounding, acceptable."),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def _print_summary(label: str, summary: BatchSummary):
    print(f"\n{label}")
    print(f"  Total:        {summary.total}")
    print(f"  Clean:        {summary.clean}")
    print(f"  Auto-approved:{summary.auto_approved}")
    print(f"  Flagged:      {summary.flagged}")
    print(f"  Blocked:      {summary.blocked}")
    if summary.skipped:
        print(f"  Skipped:      {summary.skipped}")


def _simulate_reviews(db, summary: BatchSummary, month_num: int, bank_id: str, memory_enabled: bool):
    """Simulate accountant reviewing all flagged/blocked invoices."""
    from server.models import Invoice, Vendor

    flagged = (
        db.query(Invoice)
        .filter(
            Invoice.batch_id == summary.batch_id,
            Invoice.status.in_([InvoiceStatus.FLAGGED, InvoiceStatus.BLOCKED]),
        )
        .all()
    )

    reviewed = 0
    for inv in flagged:
        vendor = db.query(Vendor).filter_by(id=inv.vendor_id).first()
        vname = vendor.name if vendor else ""

        # Look up scripted decision or use a default
        action, note = MONTH1_REVIEWS.get(vname, ("APPROVE", "Reviewed and approved."))

        # In Month 2+, blocked invoices always get REJECT (safety rules)
        if inv.status == InvoiceStatus.BLOCKED and month_num > 1:
            action, note = "REJECT", "Safety rule violation - rejected."

        review_invoice(
            invoice_id=inv.id,
            action=action,
            note=note,
            db=db,
            bank_id=bank_id,
            reviewer_name="Priya Sharma (Demo)",
        )
        reviewed += 1

    if reviewed:
        print(f"  Accountant reviewed {reviewed} flagged/blocked invoices.")
    return reviewed


def _print_month_table(summaries: list):
    """Print a table of monthly stats."""
    print(f"\n{'Period':<14} {'Total':>6} {'Clean':>6} {'Auto':>6} {'Flagged':>8} {'Blocked':>8} {'Auto%':>7}")
    print("-" * 60)
    for label, s in summaries:
        issues = s.total - s.clean
        auto_pct = (s.auto_approved / issues * 100) if issues > 0 else 0
        print(
            f"{label:<14} {s.total:>6} {s.clean:>6} {s.auto_approved:>6} "
            f"{s.flagged:>8} {s.blocked:>8} {auto_pct:>6.0f}%"
        )


def _print_surprise(db, summary: BatchSummary):
    """Print the Month 3 surprise flag on Horizon Plastics."""
    from server.models import Invoice, Vendor, AgentDecision

    invoices = (
        db.query(Invoice)
        .filter(Invoice.batch_id == summary.batch_id)
        .all()
    )
    for inv in invoices:
        vendor = db.query(Vendor).filter_by(id=inv.vendor_id).first()
        if vendor and vendor.name == "Horizon Plastics" and inv.status == InvoiceStatus.FLAGGED:
            dec = db.query(AgentDecision).filter_by(invoice_id=inv.id).first()
            print(f"\n  *** SURPRISE FLAG on trusted vendor ***")
            print(f"  Vendor: {vendor.name}")
            print(f"  Invoice: {inv.invoice_number}")
            print(f"  Status: {inv.status.value}")
            if dec:
                print(f"  Agent reason: {dec.reason}")


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

def run_demo(memory_enabled: bool):
    mode = "ON" if memory_enabled else "OFF"
    _header(f"LedgerMind Demo -- Memory {mode}")

    # Fresh DB for each run
    print("\n  Setting up database...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    setup(data_dir=DATA_DIR, reset=False)

    db = SessionLocal()
    summaries = []

    try:
        for i, month in enumerate(MONTHS):
            month_num = i + 1
            _header(f"{month['label']} -- Memory {mode}")

            if not month["csv"].exists():
                print(f"  ERROR: {month['csv']} not found.")
                print("  Run:  python scripts/generate_data.py  first.")
                return

            print(f"  Uploading {month['csv'].name}...")
            summary = process_batch(
                csv_path=str(month["csv"]),
                period=month["period"],
                memory_enabled=memory_enabled,
                db=db,
                bank_id=BANK_ID,
                po_csv_path=str(PO_CSV) if PO_CSV.exists() else None,
            )
            _print_summary(month["label"], summary)
            summaries.append((month["period"], summary))

            # Simulate accountant reviews for Month 1
            # In Months 2 & 3, only review the remaining flagged items
            _simulate_reviews(db, summary, month_num, BANK_ID, memory_enabled)

            # In Month 3, highlight the surprise flag
            if month_num == 3:
                _print_surprise(db, summary)

        # Print learning curve table
        _header("Learning Curve Summary")
        _print_month_table(summaries)

        if memory_enabled:
            print("\n  Memory ON: auto-approval rate increases month-over-month.")
            print("  The agent learned vendor habits and reduced review workload.")
        else:
            print("\n  Memory OFF: everything with issues is flagged every month.")
            print("  No learning -- accountant must review the same issues repeatedly.")

    finally:
        db.close()

    return summaries


def run_comparison():
    """Run Memory ON and Memory OFF and print a side-by-side comparison."""
    _header("LedgerMind Demo -- Memory ON vs OFF Comparison")
    print("\n  Running Memory ON...")
    on_summaries = run_demo(memory_enabled=True)
    print("\n  Running Memory OFF...")
    off_summaries = run_demo(memory_enabled=False)

    if not on_summaries or not off_summaries:
        return

    _header("Comparison: Memory ON vs OFF (Month 3)")
    _, on = on_summaries[-1]
    _, off = off_summaries[-1]

    issues_on  = on.total  - on.clean
    issues_off = off.total - off.clean

    print(f"\n{'Metric':<28} {'Memory ON':>12} {'Memory OFF':>12}")
    print("-" * 54)
    print(f"{'Total invoices':<28} {on.total:>12} {off.total:>12}")
    print(f"{'Invoices with issues':<28} {issues_on:>12} {issues_off:>12}")
    print(f"{'Auto-approved':<28} {on.auto_approved:>12} {off.auto_approved:>12}")
    print(f"{'Flagged for review':<28} {on.flagged:>12} {off.flagged:>12}")
    print(f"{'Blocked':<28} {on.blocked:>12} {off.blocked:>12}")

    auto_pct_on  = (on.auto_approved  / issues_on  * 100) if issues_on  > 0 else 0
    auto_pct_off = (off.auto_approved / issues_off * 100) if issues_off > 0 else 0
    print(f"{'Auto-handled rate':<28} {auto_pct_on:>11.0f}% {auto_pct_off:>11.0f}%")
    print(f"\n  Memory saves ~{on.flagged - off.flagged if on.flagged < off.flagged else off.flagged - on.flagged} reviews per month in Month 3.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LedgerMind 3-month end-to-end demo")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--memory", choices=["on", "off"], default="on",
                       help="Run with memory ON or OFF (default: on)")
    group.add_argument("--compare", action="store_true",
                       help="Run both modes and show a comparison table")
    args = parser.parse_args()

    if args.compare:
        run_comparison()
    else:
        run_demo(memory_enabled=(args.memory == "on"))
