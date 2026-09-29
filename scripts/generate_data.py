"""
LedgerMind — Synthetic Data Generator

Generates 3 months × ~50 invoices from 15 vendors, each with realistic
hidden habits. Designed so the AI agent has a clear learning curve to
demonstrate.

Usage:
    python scripts/generate_data.py
    python scripts/generate_data.py --months 3 --vendors 15 --seed 42

Outputs to data/:
    data/vendors.csv
    data/purchase_orders.csv
    data/invoices_month1.csv
    data/invoices_month2.csv
    data/invoices_month3.csv
"""

import argparse
import csv
import json
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Vendor registry — 15 vendors with hidden habits
# ---------------------------------------------------------------------------

@dataclass
class VendorSpec:
    id: int
    name: str
    gstin: str
    state_code: str
    habit: str          # internal tag for the generator
    notes: str          # what the agent should eventually learn

# All GSTINs below are syntactically valid (correct format + checksum).
VENDOR_SPECS = [
    VendorSpec(1,  "Sharma Traders",       "27AAPFS1234A1ZH", "27", "rounding",           "Rounds totals up by ₹1–₹3; safe to auto-approve"),
    VendorSpec(2,  "Kumar Steels",         "29AABCK5678B1ZF", "29", "duplicate",           "Occasionally sends duplicate invoices; always block"),
    VendorSpec(3,  "Reddy Logistics",      "36AACCR9012C1ZQ", "36", "credit_note_late",    "Credit notes arrive ~2 weeks late; hold, don't reject"),
    VendorSpec(4,  "Patel Electronics",    "24AABCP3456D1ZS", "24", "tax_rate_change",     "Moves from 12% to 18% GST in Month 2; flag once, then approve"),
    VendorSpec(5,  "Sri Sai Packaging",    "29AACSS7890E1Z1", "29", "clean",               "Always clean; fully trusted"),
    VendorSpec(6,  "Venkat Chemicals",     "36AABCV2345F1ZJ", "36", "tax_type_mismatch",   "Occasionally uses IGST on intra-state supply"),
    VendorSpec(7,  "Lakshmi Textiles",     "33AABCL6789G1Z9", "33", "amount_mismatch",     "Amount on invoice often differs from PO; flag every time"),
    VendorSpec(8,  "Global Tech Supplies", "07AABCG1234H1Z1", "07", "clean",               "Clean, reliable vendor"),
    VendorSpec(9,  "Sunrise Foods",        "06AABCS5678I1Z0", "06", "rounding",            "Small rounding differences; auto-approve after Month 1"),
    VendorSpec(10, "Metro Furniture",      "27AABCM9012J1ZG", "27", "missing_po",          "Occasionally invoices without a valid PO reference"),
    VendorSpec(11, "Apex Auto Parts",      "08AABCA3456K1ZM", "08", "clean",               "Clean, no issues"),
    VendorSpec(12, "Delta Pharma",         "29AABCD7890L1ZZ", "29", "invalid_gstin",       "Occasionally submits invoices with wrong GSTIN"),
    VendorSpec(13, "National Cables",      "19AABCN2345M1Z8", "19", "clean",               "Clean and reliable"),
    VendorSpec(14, "Pioneer Engineering",  "24AABCP6789N1ZP", "24", "tax_rate_mismatch",   "Sometimes invoices at wrong GST rate"),
    VendorSpec(15, "Horizon Plastics",     "27AABCH1234O1ZJ", "27", "clean",               "Clean vendor — will get a surprise issue in Month 3"),
]

# Buyer company state code (Maharashtra — for intra/inter-state logic)
BUYER_STATE_CODE = "27"

# Base PO amounts per vendor (rupees) — varied and realistic
BASE_PO_AMOUNTS_RUPEES = {
    1:  [45000, 52000, 38000],
    2:  [120000, 95000, 145000],
    3:  [25000, 31000, 28000],
    4:  [75000, 82000, 70000],
    5:  [18000, 22000, 20000],
    6:  [35000, 41000, 38000],
    7:  [65000, 78000, 72000],
    8:  [110000, 125000, 98000],
    9:  [12000, 15000, 13000],
    10: [55000, 62000, 58000],
    11: [88000, 92000, 85000],
    12: [42000, 48000, 45000],
    13: [32000, 37000, 34000],
    14: [67000, 71000, 69000],
    15: [24000, 28000, 26000],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rupees_to_paise(rupees: float) -> int:
    """Convert rupees (float) to integer paise."""
    return round(rupees * 100)

def paise_to_rupees(paise: int) -> float:
    return paise / 100

def random_date_in_month(rng: random.Random, year: int, month: int) -> date:
    """Return a random business day within a calendar month."""
    # Generate day 1–28 to stay safe for all months
    day = rng.randint(1, 28)
    return date(year, month, day)

def compute_gst(taxable_paise: int, gst_rate_pct100: int, vendor_state: str, buyer_state: str):
    """
    Given a taxable amount and a GST rate (percentage × 100), compute
    CGST, SGST, IGST based on whether the supply is intra- or inter-state.
    Returns (cgst, sgst, igst) all in paise.
    """
    tax_total = (taxable_paise * gst_rate_pct100) // 10000   # integer paise
    if vendor_state == buyer_state:
        # Intra-state: CGST + SGST (split evenly)
        half = tax_total // 2
        return half, tax_total - half, 0
    else:
        # Inter-state: IGST only
        return 0, 0, tax_total

def invoice_number(vendor_id: int, seq: int, month: int) -> str:
    return f"INV-{vendor_id:02d}-M{month}-{seq:03d}"

def po_number(vendor_id: int, seq: int) -> str:
    return f"PO-{vendor_id:02d}-{seq:03d}"


# ---------------------------------------------------------------------------
# Data model for output rows
# ---------------------------------------------------------------------------

@dataclass
class PORow:
    po_id: int
    vendor_id: int
    po_number: str
    amount_paise: int
    gst_rate_pct100: int  # e.g. 1800 for 18%
    po_date: str          # ISO

@dataclass
class InvoiceRow:
    invoice_id: int
    vendor_id: int
    invoice_number: str
    invoice_date: str
    po_number: str
    vendor_gstin: str          # as printed on invoice (may be wrong for some vendors)
    taxable_amount_paise: int
    gst_rate_pct100: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int
    total_amount_paise: int
    status: str = "UPLOADED"
    month: int = 1


# ---------------------------------------------------------------------------
# Invoice generators per habit type
# ---------------------------------------------------------------------------

def make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """No issues — clean invoice matching the PO exactly."""
    taxable = po.amount_paise
    rate = po.gst_rate_pct100
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


def make_rounding_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """Total is rounded up by ₹1–₹3 (100–300 paise)."""
    taxable = po.amount_paise
    rate = po.gst_rate_pct100
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    correct_total = taxable + cgst + sgst + igst
    rounded_up = correct_total + rng.randint(100, 300)   # ₹1–₹3
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=rounded_up, month=month,
    )


def make_duplicate_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month, existing_invoices):
    """Randomly re-send a past invoice number (exact duplicate)."""
    if existing_invoices:
        original = rng.choice(existing_invoices)
        return InvoiceRow(
            invoice_id=inv_id, vendor_id=vendor.id,
            invoice_number=original.invoice_number,   # same number → exact duplicate
            invoice_date=inv_date,
            po_number=original.po_number,
            vendor_gstin=vendor.gstin,
            taxable_amount_paise=original.taxable_amount_paise,
            gst_rate_pct100=original.gst_rate_pct100,
            cgst_paise=original.cgst_paise,
            sgst_paise=original.sgst_paise,
            igst_paise=original.igst_paise,
            total_amount_paise=original.total_amount_paise,
            month=month,
        )
    # Fallback: clean invoice if no history
    return make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month)


def make_tax_rate_change_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """
    Month 1+: 12% GST.
    Month 2+: 18% GST (the PO still says 12% — TAX_RATE_MISMATCH).
    Month 3:  same as Month 2 but by now the agent should know it's legitimate.
    """
    if month == 1:
        rate = 1200  # 12%
    else:
        rate = 1800  # 18% — PO is still 1200, so TAX_RATE_MISMATCH fires
    taxable = po.amount_paise
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


def make_tax_type_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """
    Venkat Chemicals (state 36, Telangana) sells to buyer in state 27 (Maharashtra)
    → inter-state → should use IGST.
    About 1/3 of invoices they wrongly use CGST+SGST (TAX_TYPE_MISMATCH).
    """
    taxable = po.amount_paise
    rate = po.gst_rate_pct100
    if rng.random() < 0.33:
        # Wrong: use CGST+SGST even though inter-state
        half = (taxable * rate // 10000) // 2
        cgst, sgst, igst = half, (taxable * rate // 10000) - half, 0
    else:
        cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


def make_amount_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """Lakshmi Textiles — invoice amount differs from PO by ₹500–₹5000."""
    delta_rupees = rng.randint(500, 5000)
    sign = rng.choice([-1, 1])
    taxable = po.amount_paise + sign * rupees_to_paise(delta_rupees)
    rate = po.gst_rate_pct100
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


def make_missing_po_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """Metro Furniture — references a PO that doesn't exist."""
    if rng.random() < 0.25:
        fake_po = f"PO-NONEXISTENT-{rng.randint(1000, 9999)}"
    else:
        fake_po = po.po_number
    taxable = po.amount_paise
    rate = po.gst_rate_pct100
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=fake_po, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


def make_invalid_gstin_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """Delta Pharma — about 20% of invoices have a garbled GSTIN."""
    if rng.random() < 0.20:
        # Corrupt the last few chars
        bad_gstin = vendor.gstin[:12] + "XXX"
    else:
        bad_gstin = vendor.gstin
    taxable = po.amount_paise
    rate = po.gst_rate_pct100
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=bad_gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


def make_tax_rate_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month):
    """Pioneer Engineering — 25% of invoices use wrong rate."""
    if rng.random() < 0.25:
        # Wrong rate (flip between 12% and 18%)
        rate = 1200 if po.gst_rate_pct100 != 1200 else 1800
    else:
        rate = po.gst_rate_pct100
    taxable = po.amount_paise
    cgst, sgst, igst = compute_gst(taxable, rate, vendor.state_code, BUYER_STATE_CODE)
    total = taxable + cgst + sgst + igst
    return InvoiceRow(
        invoice_id=inv_id, vendor_id=vendor.id,
        invoice_number=inv_num, invoice_date=inv_date,
        po_number=po.po_number, vendor_gstin=vendor.gstin,
        taxable_amount_paise=taxable, gst_rate_pct100=rate,
        cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst,
        total_amount_paise=total, month=month,
    )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

MONTHS = [
    (2026, 7),   # Month 1 — July 2026
    (2026, 8),   # Month 2 — August 2026
    (2026, 9),   # Month 3 — September 2026
]

# GST rates used when creating POs (percentage × 100)
GST_RATES = [500, 1200, 1800, 2800]   # 5%, 12%, 18%, 28%


def generate_pos(rng: random.Random, vendors: list[VendorSpec]) -> list[PORow]:
    """Create 3–4 POs per vendor (enough to cover 3 months of invoices)."""
    pos = []
    po_id = 1
    for vendor in vendors:
        base_amounts = BASE_PO_AMOUNTS_RUPEES[vendor.id]
        for i, base_rupees in enumerate(base_amounts):
            # Add some variation
            amount_rupees = base_rupees + rng.randint(-2000, 2000)
            amount_paise  = rupees_to_paise(amount_rupees)
            # Patel Electronics starts at 12%, rest pick from common rates
            if vendor.habit == "tax_rate_change":
                rate = 1200   # will change to 1800 in Month 2 on invoice side
            else:
                rate = rng.choice([1200, 1800])
            pos.append(PORow(
                po_id=po_id,
                vendor_id=vendor.id,
                po_number=po_number(vendor.id, i + 1),
                amount_paise=amount_paise,
                gst_rate_pct100=rate,
                po_date=date(2026, 6, rng.randint(1, 28)).isoformat(),
            ))
            po_id += 1
    return pos


def generate_month_invoices(
    rng: random.Random,
    vendors: list[VendorSpec],
    pos_by_vendor: dict[int, list[PORow]],
    month_idx: int,       # 0-based
    inv_id_start: int,
    all_previous_invoices: dict[int, list[InvoiceRow]],   # vendor_id → past invoices
) -> list[InvoiceRow]:
    """Generate ~50 invoices spread across all vendors for one month."""
    year, month = MONTHS[month_idx]
    invoices = []
    inv_id = inv_id_start

    # Each vendor sends 3–4 invoices per month (totals ~50)
    for vendor in vendors:
        vendor_pos = pos_by_vendor.get(vendor.id, [])
        if not vendor_pos:
            continue

        n_invoices = rng.randint(3, 4)
        vendor_history = all_previous_invoices.get(vendor.id, [])

        for seq in range(1, n_invoices + 1):
            po = rng.choice(vendor_pos)
            inv_num = invoice_number(vendor.id, seq + month_idx * 10, month_idx + 1)
            inv_date = random_date_in_month(rng, year, month).isoformat()

            habit = vendor.habit
            month_num = month_idx + 1

            # ---- Month 3 surprise: Horizon Plastics (vendor 15) ----
            # Plant an amount mismatch on a previously clean vendor in Month 3
            if vendor.id == 15 and month_num == 3 and seq == 1:
                inv = make_amount_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "clean":
                inv = make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "rounding":
                # ~60% chance of rounding issue; rest are clean
                if rng.random() < 0.6:
                    inv = make_rounding_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)
                else:
                    inv = make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "duplicate":
                # ~20% chance of duplicate
                if rng.random() < 0.2 and vendor_history:
                    inv = make_duplicate_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num, vendor_history)
                else:
                    inv = make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "credit_note_late":
                # Clean invoices (credit notes are handled separately in a real system;
                # here we generate clean invoices and note the pattern)
                inv = make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "tax_rate_change":
                inv = make_tax_rate_change_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "tax_type_mismatch":
                inv = make_tax_type_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "amount_mismatch":
                # ~70% of invoices have an amount mismatch
                if rng.random() < 0.7:
                    inv = make_amount_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)
                else:
                    inv = make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "missing_po":
                inv = make_missing_po_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "invalid_gstin":
                inv = make_invalid_gstin_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            elif habit == "tax_rate_mismatch":
                inv = make_tax_rate_mismatch_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            else:
                inv = make_clean_invoice(rng, inv_id, vendor, po, inv_num, inv_date, month_num)

            invoices.append(inv)
            inv_id += 1

    return invoices


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def write_vendors_csv(vendors: list[VendorSpec], output_dir: Path):
    path = output_dir / "vendors.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "name", "gstin", "state_code"])
        writer.writeheader()
        for v in vendors:
            writer.writerow({"id": v.id, "name": v.name, "gstin": v.gstin, "state_code": v.state_code})
    print(f"  Written {len(vendors)} vendors -> {path}")


def write_pos_csv(pos: list[PORow], output_dir: Path):
    path = output_dir / "purchase_orders.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "po_id", "vendor_id", "po_number", "amount_paise", "gst_rate_pct100", "po_date"
        ])
        writer.writeheader()
        for po in pos:
            writer.writerow(asdict(po))
    print(f"  Written {len(pos)} purchase orders -> {path}")


def write_invoices_csv(invoices: list[InvoiceRow], month_idx: int, output_dir: Path):
    path = output_dir / f"invoices_month{month_idx + 1}.csv"
    fieldnames = [
        "invoice_id", "vendor_id", "invoice_number", "invoice_date",
        "po_number", "vendor_gstin",
        "taxable_amount_paise", "gst_rate_pct100",
        "cgst_paise", "sgst_paise", "igst_paise", "total_amount_paise",
        "status", "month",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for inv in invoices:
            writer.writerow(asdict(inv))
    print(f"  Written {len(invoices)} invoices -> {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LedgerMind synthetic data generator")
    parser.add_argument("--seed",    type=int, default=42,  help="Random seed (default 42)")
    parser.add_argument("--months",  type=int, default=3,   help="Number of months (default 3)")
    parser.add_argument("--vendors", type=int, default=15,  help="Number of vendors (default 15)")
    parser.add_argument("--output",  type=str, default="data", help="Output directory (default data/)")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # Slice vendor list if fewer requested
    vendors = VENDOR_SPECS[: args.vendors]
    num_months = min(args.months, len(MONTHS))

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nLedgerMind Data Generator")
    print(f"  Seed: {args.seed}  |  Vendors: {len(vendors)}  |  Months: {num_months}")
    print(f"  Output directory: {output_dir.resolve()}\n")

    # --- Vendors ---
    write_vendors_csv(vendors, output_dir)

    # --- Purchase Orders ---
    pos = generate_pos(rng, vendors)
    write_pos_csv(pos, output_dir)
    pos_by_vendor: dict[int, list[PORow]] = {}
    for po in pos:
        pos_by_vendor.setdefault(po.vendor_id, []).append(po)

    # --- Invoices (month by month) ---
    all_previous: dict[int, list[InvoiceRow]] = {}
    inv_id_counter = 1
    monthly_counts = []

    for m in range(num_months):
        month_invoices = generate_month_invoices(
            rng, vendors, pos_by_vendor,
            month_idx=m,
            inv_id_start=inv_id_counter,
            all_previous_invoices=all_previous,
        )
        write_invoices_csv(month_invoices, m, output_dir)
        monthly_counts.append(len(month_invoices))
        inv_id_counter += len(month_invoices)

        # Accumulate for duplicate detection in later months
        for inv in month_invoices:
            all_previous.setdefault(inv.vendor_id, []).append(inv)

    # --- Summary ---
    print(f"\nSummary:")
    for i, count in enumerate(monthly_counts):
        year, month = MONTHS[i]
        print(f"  Month {i + 1} ({year}-{month:02d}): {count} invoices")
    print(f"  Total invoices: {sum(monthly_counts)}")
    print(f"\nDone. Load into DB with:  python -m server.setup\n")


if __name__ == "__main__":
    main()
