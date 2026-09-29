"""
LedgerMind -- server/main.py
FastAPI application entry point.

Run with:
    uvicorn server.main:app --reload
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.models import Base, engine

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Create tables on startup
# ---------------------------------------------------------------------------

Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LedgerMind API",
    description="GST Invoice Agent with Hindsight Memory",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS -- allow Streamlit frontend (localhost:8501) and any origin in dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": str(exc)}},
    )

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from server.routes.auth import router as auth_router
from server.routes.batches import router as batches_router
from server.routes.invoices import router as invoices_router
from server.routes.vendors import router as vendors_router
from server.routes.stats import router as stats_router

app.include_router(auth_router)
app.include_router(batches_router)
app.include_router(invoices_router)
app.include_router(vendors_router)
app.include_router(stats_router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok", "service": "LedgerMind API"}


# ---------------------------------------------------------------------------
# Startup: retry any unretained reviews
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    log.info("LedgerMind API starting up...")
    try:
        from server.models import SessionLocal, Company
        from server.pipeline import retry_unretained_reviews

        db = SessionLocal()
        try:
            company = db.query(Company).first()
            if company and company.hindsight_bank_id:
                retried = retry_unretained_reviews(db, company.hindsight_bank_id)
                if retried:
                    log.info("Startup: retried %d unretained reviews", retried)
        except Exception as e:
            log.warning("Startup retry failed (non-fatal): %s", e)
        finally:
            db.close()
    except Exception as e:
        log.warning("Startup hook error (non-fatal): %s", e)
