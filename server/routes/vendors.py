"""
LedgerMind -- server/routes/vendors.py
Vendor listing, memory profiles, and vendor facts.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.models import get_db, Vendor
from server.routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/vendors", tags=["vendors"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class VendorOut(BaseModel):
    vendor_id: int
    name: str
    gstin: Optional[str]
    state_code: Optional[str]

class VendorMemoryOut(BaseModel):
    vendor_id: int
    vendor_name: str
    profile: str
    based_on: list[str]

class VendorFactRequest(BaseModel):
    fact: str

class CreateVendorRequest(BaseModel):
    name: str
    gstin: Optional[str] = None
    state_code: Optional[str] = None

class UpdateVendorRequest(BaseModel):
    gstin: Optional[str] = None
    state_code: Optional[str] = None
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_bank_id(db: Session) -> str:
    from server.models import Company
    company = db.query(Company).first()
    if company and company.hindsight_bank_id:
        return company.hindsight_bank_id
    return os.getenv("HINDSIGHT_BANK_ID", "ledgermind-demo")


def _get_company_id(db: Session) -> int:
    from server.models import Company
    company = db.query(Company).first()
    return company.id if company else 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[VendorOut])
def list_vendors(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    company_id = _get_company_id(db)
    vendors = db.query(Vendor).filter_by(company_id=company_id).all()
    return [
        VendorOut(vendor_id=v.id, name=v.name, gstin=v.gstin, state_code=v.state_code)
        for v in vendors
    ]


@router.get("/{vendor_id}/memory", response_model=VendorMemoryOut)
def get_vendor_memory(
    vendor_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vendor = db.query(Vendor).filter_by(id=vendor_id).first()
    if not vendor:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vendor not found"})

    bank_id = _get_bank_id(db)
    from memory.reflect import reflect_vendor_profile
    profile = reflect_vendor_profile(bank_id=bank_id, vendor_name=vendor.name)

    return VendorMemoryOut(
        vendor_id=vendor_id,
        vendor_name=vendor.name,
        profile=profile.text if profile.is_available else "",
        based_on=profile.based_on,
    )


@router.post("/{vendor_id}/facts", status_code=status.HTTP_201_CREATED)
def add_vendor_fact(
    vendor_id: int,
    body: VendorFactRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    vendor = db.query(Vendor).filter_by(id=vendor_id).first()
    if not vendor:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vendor not found"})

    bank_id = _get_bank_id(db)
    from memory.retain import retain_vendor_fact
    ok = retain_vendor_fact(
        bank_id=bank_id,
        vendor_id=vendor_id,
        vendor_name=vendor.name,
        fact=body.fact,
        told_by=current_user.name,
    )
    if not ok:
        raise HTTPException(503, detail={"code": "MEMORY_UNAVAILABLE", "message": "Could not save fact to memory"})

    return {"status": "ok", "message": f"Fact saved for {vendor.name}"}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VendorOut)
def create_vendor(
    body: CreateVendorRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    company_id = _get_company_id(db)
    vendor = Vendor(
        company_id=company_id,
        name=body.name,
        gstin=body.gstin,
        state_code=body.state_code,
    )
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return VendorOut(vendor_id=vendor.id, name=vendor.name, gstin=vendor.gstin, state_code=vendor.state_code)


@router.patch("/{vendor_id}", response_model=VendorOut)
def update_vendor(
    vendor_id: int,
    body: UpdateVendorRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    vendor = db.query(Vendor).filter_by(id=vendor_id).first()
    if not vendor:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vendor not found"})

    if body.gstin is not None:
        old_gstin = vendor.gstin
        vendor.gstin = body.gstin
        # Retain GSTIN change as a vendor fact
        if old_gstin != body.gstin:
            bank_id = _get_bank_id(db)
            from memory.retain import retain_vendor_fact
            retain_vendor_fact(
                bank_id=bank_id,
                vendor_id=vendor_id,
                vendor_name=vendor.name,
                fact=f"GSTIN changed from {old_gstin} to {body.gstin} by admin {current_user.name}",
            )
    if body.state_code is not None:
        vendor.state_code = body.state_code
    if body.name is not None:
        vendor.name = body.name

    db.commit()
    db.refresh(vendor)
    return VendorOut(vendor_id=vendor.id, name=vendor.name, gstin=vendor.gstin, state_code=vendor.state_code)
