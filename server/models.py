"""
LedgerMind — Database Models
All monetary amounts are stored as integer paise (₹100.50 = 10050).
Dates are stored as ISO strings (YYYY-MM-DD).
"""

import json
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    ForeignKey, UniqueConstraint, Text, DateTime, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import enum
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///ledgermind.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    ACCOUNTANT = "ACCOUNTANT"
    ADMIN = "ADMIN"


class InvoiceStatus(str, enum.Enum):
    UPLOADED       = "UPLOADED"
    CHECKED        = "CHECKED"
    CLEAN          = "CLEAN"
    AUTO_APPROVED  = "AUTO_APPROVED"
    FLAGGED        = "FLAGGED"
    BLOCKED        = "BLOCKED"
    APPROVED       = "APPROVED"
    REJECTED       = "REJECTED"
    ON_HOLD        = "ON_HOLD"
    OVERTURNED     = "OVERTURNED"


class ReviewAction(str, enum.Enum):
    APPROVE   = "APPROVE"
    REJECT    = "REJECT"
    HOLD      = "HOLD"
    OVERTURN  = "OVERTURN"


class IssueType(str, enum.Enum):
    ROUNDING            = "ROUNDING"
    AMOUNT_MISMATCH     = "AMOUNT_MISMATCH"
    DUPLICATE           = "DUPLICATE"
    INVALID_GSTIN       = "INVALID_GSTIN"
    GSTIN_MISMATCH      = "GSTIN_MISMATCH"
    TAX_RATE_MISMATCH   = "TAX_RATE_MISMATCH"
    TAX_TYPE_MISMATCH   = "TAX_TYPE_MISMATCH"
    MISSING_PO          = "MISSING_PO"
    PENDING_CREDIT_NOTE = "PENDING_CREDIT_NOTE"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Company(Base):
    """One row per business using LedgerMind."""
    __tablename__ = "companies"

    id                 = Column(Integer, primary_key=True, index=True)
    name               = Column(String(255), nullable=False)
    gstin              = Column(String(15), nullable=True)
    state_code         = Column(String(2), nullable=True)
    hindsight_bank_id  = Column(String(100), nullable=True)

    vendors     = relationship("Vendor",       back_populates="company")
    users       = relationship("User",         back_populates="company")
    batches     = relationship("UploadBatch",  back_populates="company")
    pos         = relationship("PurchaseOrder", back_populates="company")

    def __repr__(self):
        return f"<Company id={self.id} name={self.name!r}>"


class User(Base):
    """Accountant or admin user."""
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(255), nullable=False)
    email         = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role          = Column(SAEnum(UserRole), nullable=False, default=UserRole.ACCOUNTANT)
    company_id    = Column(Integer, ForeignKey("companies.id"), nullable=False)

    company = relationship("Company", back_populates="users")
    reviews = relationship("HumanReview", back_populates="reviewer")

    def __repr__(self):
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


class Vendor(Base):
    """A supplier that sends invoices."""
    __tablename__ = "vendors"

    id         = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name       = Column(String(255), nullable=False)
    gstin      = Column(String(15), nullable=True)
    state_code = Column(String(2), nullable=True)

    company  = relationship("Company", back_populates="vendors")
    invoices = relationship("Invoice",       back_populates="vendor")
    pos      = relationship("PurchaseOrder", back_populates="vendor")

    def __repr__(self):
        return f"<Vendor id={self.id} name={self.name!r}>"


class PurchaseOrder(Base):
    """A purchase order raised by the buyer before an invoice arrives."""
    __tablename__ = "purchase_orders"

    id         = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    vendor_id  = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    po_number  = Column(String(50), nullable=False, index=True)
    # Amount in paise (integer). ₹100.50 → 10050
    amount     = Column(Integer, nullable=False)
    gst_rate   = Column(Integer, nullable=False)   # percentage × 100  e.g. 1800 = 18%
    po_date    = Column(String(10), nullable=False) # ISO date YYYY-MM-DD

    company = relationship("Company", back_populates="pos")
    vendor  = relationship("Vendor",  back_populates="pos")

    def __repr__(self):
        return f"<PurchaseOrder id={self.id} po_number={self.po_number!r}>"


class UploadBatch(Base):
    """One upload event — a month's worth of invoices."""
    __tablename__ = "upload_batches"

    id             = Column(Integer, primary_key=True, index=True)
    company_id     = Column(Integer, ForeignKey("companies.id"), nullable=False)
    period         = Column(String(7), nullable=False)  # YYYY-MM
    uploaded_by    = Column(Integer, ForeignKey("users.id"), nullable=True)
    memory_enabled = Column(Boolean, nullable=False, default=True)
    uploaded_at    = Column(DateTime, nullable=False,
                            default=lambda: datetime.now(timezone.utc))

    company  = relationship("Company",    back_populates="batches")
    invoices = relationship("Invoice",    back_populates="batch")

    def __repr__(self):
        return f"<UploadBatch id={self.id} period={self.period!r}>"


class Invoice(Base):
    """A single purchase invoice from a vendor."""
    __tablename__ = "invoices"

    id             = Column(Integer, primary_key=True, index=True)
    company_id     = Column(Integer, ForeignKey("companies.id"), nullable=False)
    vendor_id      = Column(Integer, ForeignKey("vendors.id"), nullable=False)
    invoice_number = Column(String(50), nullable=False)
    invoice_date   = Column(String(10), nullable=False)  # ISO date YYYY-MM-DD
    po_number      = Column(String(50), nullable=True)

    # As printed on the invoice (may differ from vendor's registered GSTIN)
    vendor_gstin   = Column(String(15), nullable=True)

    # All amounts in integer paise
    taxable_amount = Column(Integer, nullable=False)
    gst_rate       = Column(Integer, nullable=False)  # percentage × 100
    cgst           = Column(Integer, nullable=False, default=0)
    sgst           = Column(Integer, nullable=False, default=0)
    igst           = Column(Integer, nullable=False, default=0)
    total_amount   = Column(Integer, nullable=False)

    status   = Column(SAEnum(InvoiceStatus), nullable=False,
                      default=InvoiceStatus.UPLOADED)
    batch_id = Column(Integer, ForeignKey("upload_batches.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "company_id", "vendor_id", "invoice_number", "batch_id",
            name="uq_invoice_per_batch"
        ),
    )

    company   = relationship("Company",      foreign_keys=[company_id])
    vendor    = relationship("Vendor",       back_populates="invoices")
    batch     = relationship("UploadBatch",  back_populates="invoices")
    issues    = relationship("Issue",        back_populates="invoice",
                             cascade="all, delete-orphan")
    decisions = relationship("AgentDecision", back_populates="invoice",
                             cascade="all, delete-orphan")
    reviews   = relationship("HumanReview",  back_populates="invoice",
                             cascade="all, delete-orphan")

    def __repr__(self):
        return (f"<Invoice id={self.id} number={self.invoice_number!r} "
                f"status={self.status}>")


class Issue(Base):
    """A GST issue found on an invoice by the rule checker."""
    __tablename__ = "issues"

    id         = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    type       = Column(SAEnum(IssueType), nullable=False)
    # Free-form JSON details, e.g. {"difference_paise": 100, "po_rate": 1200, "invoice_rate": 1800}
    details    = Column(Text, nullable=True)

    invoice = relationship("Invoice", back_populates="issues")

    def get_details(self) -> dict:
        """Deserialise the JSON details field."""
        if self.details:
            return json.loads(self.details)
        return {}

    def set_details(self, data: dict):
        self.details = json.dumps(data)

    def __repr__(self):
        return f"<Issue id={self.id} type={self.type} invoice_id={self.invoice_id}>"


class AgentDecision(Base):
    """Audit record of the AI agent's decision for one invoice."""
    __tablename__ = "agent_decisions"

    id                  = Column(Integer, primary_key=True, index=True)
    invoice_id          = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    decision            = Column(String(20), nullable=False)   # AUTO_APPROVE | FLAG | BLOCK
    reason              = Column(Text, nullable=True)
    confidence          = Column(Integer, nullable=True)        # 0–100 (percentage)
    # JSON list of memory document IDs used
    memories_used       = Column(Text, nullable=True)
    safety_rule_applied = Column(String(50), nullable=True)
    created_at          = Column(DateTime, nullable=False,
                                 default=lambda: datetime.now(timezone.utc))

    invoice = relationship("Invoice", back_populates="decisions")

    def get_memories_used(self) -> list:
        if self.memories_used:
            return json.loads(self.memories_used)
        return []

    def set_memories_used(self, data: list):
        self.memories_used = json.dumps(data)

    def __repr__(self):
        return (f"<AgentDecision id={self.id} decision={self.decision!r} "
                f"invoice_id={self.invoice_id}>")


class HumanReview(Base):
    """An accountant's decision on a flagged or blocked invoice."""
    __tablename__ = "human_reviews"

    id                  = Column(Integer, primary_key=True, index=True)
    invoice_id          = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    reviewer_id         = Column(Integer, ForeignKey("users.id"), nullable=True)
    action              = Column(SAEnum(ReviewAction), nullable=False)
    note                = Column(Text, nullable=True)
    retained_to_memory  = Column(Boolean, nullable=False, default=False)
    created_at          = Column(DateTime, nullable=False,
                                 default=lambda: datetime.now(timezone.utc))

    invoice  = relationship("Invoice", back_populates="reviews")
    reviewer = relationship("User",    back_populates="reviews")

    def __repr__(self):
        return (f"<HumanReview id={self.id} action={self.action} "
                f"invoice_id={self.invoice_id}>")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def get_db():
    """Yield a database session; close it when done. For use with FastAPI Depends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
