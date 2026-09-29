"""
LedgerMind — Database Setup & Seeding

Creates all tables and seeds the database from the generated CSV files.
Also creates a demo company, an admin user, and an accountant user.

Run with:
    python -m server.setup                 # default: data/ folder
    python -m server.setup --data data/    # explicit path
    python -m server.setup --reset         # drop + recreate tables before seeding
"""

import argparse
import csv
import hashlib
import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path when run as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from server.models import (
    Base, SessionLocal, engine,
    Company, User, Vendor, PurchaseOrder,
    UserRole,
)

# ---------------------------------------------------------------------------
# Demo credentials
# ---------------------------------------------------------------------------

DEMO_COMPANY_NAME = "Acme Industries Pvt. Ltd."
DEMO_COMPANY_GSTIN = "27AABCA1234A1ZJ"
DEMO_COMPANY_STATE = "27"

DEMO_USERS = [
    {
        "name": "Admin User",
        "email": "admin@ledgermind.demo",
        "password": "admin123",
        "role": UserRole.ADMIN,
    },
    {
        "name": "Priya Sharma",
        "email": "priya@ledgermind.demo",
        "password": "accountant123",
        "role": UserRole.ACCOUNTANT,
    },
]


# ---------------------------------------------------------------------------
# Password hashing (bcrypt if available, else sha256 fallback for demo)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Demo fallback — not for production
        return hashlib.sha256(plain.encode()).hexdigest()


# ---------------------------------------------------------------------------
# DB creation
# ---------------------------------------------------------------------------

def create_tables(reset: bool = False):
    if reset:
        print("  Dropping all tables...")
        Base.metadata.drop_all(bind=engine)
    print("  Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("  Tables ready.")


# ---------------------------------------------------------------------------
# Seed company + users
# ---------------------------------------------------------------------------

def seed_company_and_users(db) -> Company:
    """Create the demo company and users. Idempotent — skips if already present."""
    company = db.query(Company).filter_by(name=DEMO_COMPANY_NAME).first()
    if not company:
        company = Company(
            name=DEMO_COMPANY_NAME,
            gstin=DEMO_COMPANY_GSTIN,
            state_code=DEMO_COMPANY_STATE,
            hindsight_bank_id=f"ledgermind-{DEMO_COMPANY_NAME.lower().replace(' ', '-')[:30]}",
        )
        db.add(company)
        db.flush()   # get company.id without committing
        print(f"  Created company: {company.name} (id={company.id})")
    else:
        print(f"  Company already exists: {company.name} (id={company.id})")

    for user_data in DEMO_USERS:
        existing = db.query(User).filter_by(email=user_data["email"]).first()
        if not existing:
            user = User(
                name=user_data["name"],
                email=user_data["email"],
                password_hash=hash_password(user_data["password"]),
                role=user_data["role"],
                company_id=company.id,
            )
            db.add(user)
            print(f"  Created user: {user.email} ({user.role.value})")
        else:
            print(f"  User already exists: {existing.email}")

    db.commit()
    return company


# ---------------------------------------------------------------------------
# Seed vendors from CSV
# ---------------------------------------------------------------------------

def seed_vendors(db, company: Company, data_dir: Path) -> dict[int, Vendor]:
    """Load vendors.csv and upsert into DB. Returns mapping: csv_id → Vendor."""
    csv_path = data_dir / "vendors.csv"
    if not csv_path.exists():
        print(f"  WARNING: {csv_path} not found — skipping vendor seed.")
        print("  Run:  python scripts/generate_data.py  to generate it first.")
        return {}

    vendor_map: dict[int, Vendor] = {}
    created = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_id = int(row["id"])
            existing = (
                db.query(Vendor)
                .filter_by(company_id=company.id, name=row["name"])
                .first()
            )
            if existing:
                vendor_map[csv_id] = existing
            else:
                vendor = Vendor(
                    company_id=company.id,
                    name=row["name"],
                    gstin=row["gstin"],
                    state_code=row["state_code"],
                )
                db.add(vendor)
                db.flush()
                vendor_map[csv_id] = vendor
                created += 1

    db.commit()
    print(f"  Vendors: {created} created, {len(vendor_map) - created} already existed "
          f"(total {len(vendor_map)})")
    return vendor_map


# ---------------------------------------------------------------------------
# Seed purchase orders from CSV
# ---------------------------------------------------------------------------

def seed_purchase_orders(
    db, company: Company,
    vendor_map: dict[int, Vendor],
    data_dir: Path,
) -> dict[str, PurchaseOrder]:
    """Load purchase_orders.csv and insert POs. Returns mapping: po_number → PurchaseOrder."""
    csv_path = data_dir / "purchase_orders.csv"
    if not csv_path.exists():
        print(f"  WARNING: {csv_path} not found — skipping PO seed.")
        return {}

    po_map: dict[str, PurchaseOrder] = {}
    created = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            po_num = row["po_number"]
            csv_vendor_id = int(row["vendor_id"])
            vendor = vendor_map.get(csv_vendor_id)
            if not vendor:
                continue

            existing = db.query(PurchaseOrder).filter_by(
                company_id=company.id,
                po_number=po_num,
            ).first()

            if existing:
                po_map[po_num] = existing
            else:
                po = PurchaseOrder(
                    company_id=company.id,
                    vendor_id=vendor.id,
                    po_number=po_num,
                    amount=int(row["amount_paise"]),
                    gst_rate=int(row["gst_rate_pct100"]),
                    po_date=row["po_date"],
                )
                db.add(po)
                db.flush()
                po_map[po_num] = po
                created += 1

    db.commit()
    print(f"  Purchase Orders: {created} created, {len(po_map) - created} already existed "
          f"(total {len(po_map)})")
    return po_map


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def setup(data_dir: Path = Path("data"), reset: bool = False):
    print("\nLedgerMind — DB Setup")
    print("=" * 40)

    # 1. Create tables
    create_tables(reset=reset)

    # 2. Seed reference data
    db = SessionLocal()
    try:
        company = seed_company_and_users(db)
        vendor_map = seed_vendors(db, company, data_dir)
        if vendor_map:
            seed_purchase_orders(db, company, vendor_map, data_dir)
    finally:
        db.close()

    print("=" * 40)
    print("Setup complete.\n")
    print("Login credentials:")
    for u in DEMO_USERS:
        print(f"  {u['email']:35s}  password: {u['password']}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LedgerMind database setup")
    parser.add_argument(
        "--data", type=str, default="data",
        help="Path to the data directory containing generated CSVs (default: data/)"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop and recreate all tables before seeding (WARNING: deletes all data)"
    )
    args = parser.parse_args()
    setup(data_dir=Path(args.data), reset=args.reset)
