# LedgerMind — Implementation Plan

**Project:** LedgerMind: A GST Invoice Agent That Learns Your Accountant's Judgment  
**Submission:** Tonight  
**Team Size:** 4 people  
**Stack:** Python · FastAPI · Streamlit · SQLite · Hindsight (memory) · Groq (LLM)

---

## Team Roles at a Glance

| Person | Role | Core Responsibility |
|--------|------|---------------------|
| **Person 1** | Backend & Data Lead | Database models, GST rule checks, GSTIN validation, synthetic data generator |
| **Person 2** | Agent & Memory Lead | Hindsight client, recall/retain/reflect, LLM decision engine, prompts |
| **Person 3** | API & Auth Lead | FastAPI routes, JWT auth, upload pipeline, review endpoints |
| **Person 4** | Frontend & Integration Lead | Streamlit UI, all pages, charts, end-to-end wiring, demo script |

---

## Person 1 — Backend & Data Lead

### Responsibilities
Set up the foundation everything else runs on: the database, all deterministic GST checks, and the synthetic dataset.

### Tasks

#### 1. Database Models (`server/models.py`)
Define all SQLAlchemy (or raw SQLite) models exactly as specified in the README:
- `Company` — id, name, gstin, state_code, hindsight_bank_id
- `User` — id, name, email, password_hash, role (ACCOUNTANT/ADMIN), company_id
- `Vendor` — id, company_id, name, gstin, state_code
- `PurchaseOrder` — id, company_id, vendor_id, po_number, amount, gst_rate, po_date
- `Invoice` — id, company_id, vendor_id, invoice_number, invoice_date, po_number, vendor_gstin, taxable_amount, gst_rate, cgst, sgst, igst, total_amount, status, batch_id
- `UploadBatch` — id, company_id, period, uploaded_by, memory_enabled, uploaded_at
- `Issue` — id, invoice_id, type, details (JSON)
- `AgentDecision` — id, invoice_id, decision, reason, confidence, memories_used (JSON), safety_rule_applied, created_at
- `HumanReview` — id, invoice_id, reviewer_id, action, note, retained_to_memory, created_at

Key constraints:
- All monetary amounts stored as **integer paise** (never floats)
- Unique constraint on `(company_id, vendor_id, invoice_number, batch_id)` on Invoice
- Invoice status enum: UPLOADED → CHECKED → CLEAN / AUTO_APPROVED / FLAGGED / BLOCKED / APPROVED / REJECTED / ON_HOLD / OVERTURNED

#### 2. GST Rule Checker (`server/checks/rules.py`)
Implement all 9 deterministic checks (no AI involved here):

| Check | Logic |
|-------|-------|
| `ROUNDING` | `abs(total - (taxable + tax)) between ₹0.01 and ROUNDING_LIMIT` |
| `AMOUNT_MISMATCH` | Invoice amount vs PO amount differs by more than rounding limit |
| `DUPLICATE` | Same vendor + same invoice number, OR same vendor + same amount + same date |
| `INVALID_GSTIN` | Fails format or checksum (see below) |
| `GSTIN_MISMATCH` | Valid GSTIN but differs from vendor's registered GSTIN |
| `TAX_RATE_MISMATCH` | Invoice GST rate ≠ PO GST rate |
| `TAX_TYPE_MISMATCH` | IGST on intra-state, or CGST+SGST on inter-state |
| `MISSING_PO` | Referenced PO number does not exist |
| `PENDING_CREDIT_NOTE` | Invoice expected credit note that hasn't arrived |

#### 3. GSTIN Validation (`server/checks/gstin.py`)
- Validate 15-character format: `[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}`
- Implement checksum verification
- Return clear error codes for format vs checksum failures

#### 4. Safety Rules (`server/checks/safety.py`)
Enforce before the AI is ever called:
- `INVALID_GSTIN` → always BLOCKED
- `GSTIN_MISMATCH` → always BLOCKED
- Exact duplicate (same vendor + same invoice number) → always BLOCKED
- Invoice total > `MAX_AUTO_APPROVE_AMOUNT` → always FLAGGED
- Amount diff > `MAX_AUTO_APPROVE_DIFF` → always FLAGGED
- New vendor (no memory) → always FLAGGED

#### 5. Synthetic Data Generator (`scripts/generate_data.py`)
Generate 3 months × ~50 invoices from 15 vendors with hidden habits:

| Vendor | Hidden habit |
|--------|-------------|
| Sharma Traders | Rounds up by ₹1–₹3 |
| Kumar Steels | Occasionally sends duplicates |
| Reddy Logistics | Credit notes ~2 weeks late |
| Patel Electronics | Tax rate changes 12% → 18% in Month 2 |
| Sri Sai Packaging | Clean, no issues |
| Venkat Chemicals | Wrong tax type (IGST vs CGST+SGST) sometimes |
| Lakshmi Textiles | Real amount mismatches |
| 8 more vendors | Mixed patterns |

- Use `--seed 42` for reproducibility
- In Month 3, plant a new issue on a previously trusted vendor
- Output to `data/` as CSV and JSON

#### 6. Database Setup Script (`server/setup.py`)
- Create all tables
- Create the demo company and user accounts (accountant + admin)
- Seed vendors from generated data

### Deliverables
- [ ] `server/models.py`
- [ ] `server/checks/rules.py`
- [ ] `server/checks/gstin.py`
- [ ] `server/checks/safety.py`
- [ ] `scripts/generate_data.py`
- [ ] `server/setup.py`
- [ ] Unit tests for all checks in `server/tests/test_checks.py`

---

## Person 2 — Agent & Memory Lead

### Responsibilities
Build the AI brain: the Hindsight memory client, the LLM decision engine, and all prompt engineering. This is the core of what makes LedgerMind different from a plain rule checker.

### Tasks

#### 1. Hindsight Client Setup (`memory/client.py`)
- Initialize the Hindsight client using `HINDSIGHT_URL` and `HINDSIGHT_API_KEY` from env
- `create_bank(bank_id, name, mission, disposition)` — one bank per company
- Add directives (hard rules), e.g. "Never recommend approving an invoice with an invalid GSTIN"
- Handle the case where the bank already exists (idempotent setup)

```python
client = Hindsight(base_url=HINDSIGHT_URL, api_key=HINDSIGHT_API_KEY)
client.create_bank(
    bank_id="ledgermind-{company_id}",
    name="{company_name} - Invoice Review",
    mission="...",
    disposition={"skepticism": 4, "literalism": 3, "empathy": 2},
)
```

#### 2. Retain Decisions (`memory/retain.py`)
Implement `retain_decision(invoice, issues, action, note, reviewer)`:
- Retain **accountant decisions**: approve, reject, hold — with the note
- Retain **corrections**: when an auto-approval is overturned (most valuable)
- Retain **agent actions**: what was auto-approved and why
- Retain **vendor facts**: explicit facts told by the accountant

```python
client.retain(
    bank_id=bank_id,
    content="Sharma Traders INV-1043 had ₹1 rounding. Accountant APPROVED: 'They always round up'.",
    context="invoice review decision",
    timestamp=invoice.invoice_date,
    document_id=f"decision-{invoice.id}",
    metadata={"vendor_id": vendor_id, "issue_type": "ROUNDING", "decision": "APPROVED"},
)
```

#### 3. Recall Vendor History (`memory/recall.py`)
Implement `recall_vendor_history(bank_id, vendor, issue_types)`:
- Query: `"Past decisions about {vendor.name} {issue_type} issues"`
- Types: `["world", "experience", "observation"]`
- Budget: `"mid"`
- Return structured list of memories for the LLM to use

#### 4. Reflect for Vendor Profiles (`memory/reflect.py`)
Implement `reflect_vendor_profile(bank_id, vendor)`:
- Query: `"What should an accountant know about {vendor.name}'s invoices?"`
- Budget: `"low"`
- Returns plain-English vendor profile for the Vendor Memory page

#### 5. Groq LLM Client (`agent/llm.py`)
- Initialize Groq client with `GROQ_API_KEY`
- Model: `openai/gpt-oss-120b` (configurable via `LLM_MODEL`)
- Implement retry logic up to `LLM_MAX_RETRIES` (default 3)
- On all retries exhausted: return safe fallback `{"decision": "FLAG", "reason": "AI unavailable"}`
- Validate that returned JSON has required fields before accepting

#### 6. Decision Engine (`agent/decide.py`)
This is the heart of the system. Implement `decide(invoice, issues, memories, settings)`:

```
Layer 1: Rules already ran (Person 1's work) — issues are known
Layer 2: Safety rules — check before calling AI
Layer 3: AI judgment — only if no safety rule applies
```

AI decision logic:
- Pass invoice details + issues + recalled memories to LLM
- LLM must return structured JSON:
  ```json
  {
    "decision": "AUTO_APPROVE",
    "reason": "...",
    "memories_used": ["decision-981"],
    "confidence": 0.92
  }
  ```
- Auto-approve only if ALL conditions met:
  - No safety rule applies
  - At least `MIN_EVIDENCE` (default 2) past approvals of this issue type for this vendor
  - No rejection/overturn since last approval
  - Confidence ≥ `MIN_CONFIDENCE` (default 0.8)
- Otherwise: FLAG
- **When in doubt, FLAG**

Handle Memory OFF mode: skip recall/retain, flag everything with issues.

#### 7. LLM Prompts (`agent/prompts.py`)
Write clear, structured prompts:
- System prompt: role, rules, output format requirement
- User prompt: invoice details, issues found, recalled memories, safety limits
- Instruct the LLM to always return valid JSON
- Include few-shot examples for AUTO_APPROVE vs FLAG decisions

### Deliverables
- [ ] `memory/client.py`
- [ ] `memory/retain.py`
- [ ] `memory/recall.py`
- [ ] `memory/reflect.py`
- [ ] `agent/llm.py`
- [ ] `agent/decide.py`
- [ ] `agent/prompts.py`
- [ ] Unit tests in `server/tests/test_agent.py` (mock Hindsight and Groq)

---

## Person 3 — API & Auth Lead

### Responsibilities
Build the FastAPI backend: all routes, JWT authentication, the upload pipeline, and the review workflow. This is the glue between the database, the agent, and the frontend.

### Tasks

#### 1. FastAPI App Entry (`server/main.py`)
- Initialize FastAPI app
- Register all routers
- CORS settings for Streamlit frontend
- Global error handler returning `{"error": {"code": "...", "message": "..."}}`
- Health check endpoint

#### 2. Auth Routes (`routes/auth.py`)
- `POST /api/auth/login` — verify email + bcrypt password, return JWT (24h expiry)
- `GET /api/auth/me` — return current user from JWT
- JWT middleware: decode token, attach user to request
- Role-based guards: `require_accountant`, `require_admin` decorators

#### 3. Upload & Batch Pipeline (`routes/batches.py`)
- `POST /api/batches` — main upload endpoint:
  1. Accept CSV/JSON invoices + POs, `memory_enabled` flag
  2. Compute file hash; reject with `409` if already uploaded
  3. Validate all rows; reject bad rows with clear errors, continue rest
  4. Create `UploadBatch` record
  5. For each invoice:
     - Save to DB with status `UPLOADED`
     - Run rule checks (Person 1) → save `Issue` records → status `CHECKED`
     - Run safety rules → if triggered, status `BLOCKED`/`FLAGGED`, save `AgentDecision`
     - If not blocked, recall memories (Person 2) → run AI decision → save `AgentDecision`
     - Update invoice status to `CLEAN`/`AUTO_APPROVED`/`FLAGGED`
  6. Return batch summary with per-invoice results
- `GET /api/batches/:id` — batch status and all invoice results
- Handle very large uploads in batches; return progress

#### 4. Invoice Routes (`routes/invoices.py`)
- `GET /api/invoices?status=flagged` — list invoices by status (with pagination)
- `GET /api/invoices/:id` — full detail: invoice + issues + agent decision + memories used + history
- `POST /api/invoices/:id/review` — accountant action:
  - Accepts `action` (APPROVE/REJECT/HOLD) + `note`
  - Creates `HumanReview` record
  - Updates invoice status
  - Calls retain (Person 2) to save decision to memory
  - Sets `retained_to_memory = true`
- `POST /api/invoices/:id/overturn` — reverse an auto-approval:
  - Only valid if current status is `AUTO_APPROVED`
  - Creates `HumanReview` with action `OVERTURN`
  - Sets invoice status to `OVERTURNED` → then treated as `FLAGGED`
  - Retains correction to memory (most important retention)

#### 5. Vendor Routes (`routes/vendors.py`)
- `GET /api/vendors` — list all vendors for the company
- `GET /api/vendors/:id/memory` — call `reflect_vendor_profile` (Person 2) + return observations
- `POST /api/vendors/:id/facts` — retain a vendor fact to memory
- `POST /api/vendors` 🔒 — create vendor (admin only)
- `PATCH /api/vendors/:id` 🔒 — update vendor GSTIN etc (admin only); retain change as vendor fact

#### 6. Stats Routes (`routes/stats.py`)
- `GET /api/stats/learning-curve` — monthly metrics:
  - Total invoices per month
  - Invoices with issues per month
  - Auto-handled count and rate
  - Human reviews needed
  - Overturn count and rate
  - Estimated time saved (human reviews avoided × avg review time)
- `GET /api/settings` 🔒 — return current config values
- `PATCH /api/settings` 🔒 — update safety limits

#### 7. Background Retry Job
- On startup, find all `HumanReview` records where `retained_to_memory = false`
- Retry retaining them to Hindsight
- This handles the case where Hindsight was temporarily unavailable

### Error Responses
All errors must follow:
```json
{ "error": { "code": "NOT_FOUND", "message": "Invoice not found" } }
```

| Status | When |
|--------|------|
| 400 | Invalid input |
| 401 | Missing/invalid JWT |
| 403 | Wrong role |
| 404 | Not found |
| 409 | File already uploaded |
| 503 | AI/memory unavailable |

### Deliverables
- [ ] `server/main.py`
- [ ] `server/routes/auth.py`
- [ ] `server/routes/batches.py`
- [ ] `server/routes/invoices.py`
- [ ] `server/routes/vendors.py`
- [ ] `server/routes/stats.py`
- [ ] Integration tests in `server/tests/test_routes.py`

---

## Person 4 — Frontend & Integration Lead

### Responsibilities
Build the Streamlit UI, wire it to the backend API, create the demo script, and make sure the full end-to-end flow works. Also owns the `.env` setup, `requirements.txt`, and project configuration.

### Tasks

#### 1. Streamlit App Entry (`ui/app.py`)
- Session state: JWT token, current user, company
- Login check: redirect to login if no token
- Sidebar navigation to all pages
- API helper: `api_get(endpoint)`, `api_post(endpoint, data)` with auth headers and error display

#### 2. Login Page
- Email + password form
- Call `POST /api/auth/login`
- Store JWT in session state
- Show error on bad credentials

#### 3. Dashboard Page (`ui/pages/1_Dashboard.py`)
- This month's stats: total invoices, auto-handled rate, human reviews needed
- **Learning curve chart** (Plotly): auto-handled rate month over month
- Recent activity feed: last 10 invoice decisions
- Memory ON/OFF toggle indicator

#### 4. Upload & Check Page (`ui/pages/2_Upload_and_Check.py`)
- File upload widgets: invoices CSV/JSON + POs CSV/JSON
- Memory ON/OFF toggle
- Upload button → `POST /api/batches`
- Progress indicator for large uploads
- Results table per invoice showing:
  - Vendor, invoice number, date, amount
  - Issues found (e.g. `ROUNDING ₹1`, `TAX_RATE 12%→18%`)
  - Decision badge: `✅ Clean` / `🟢 Auto-approved` / `🟡 Flagged` / `🔴 Blocked`
  - Reason (plain English)
  - "Based on" — past decisions used

#### 5. Review Queue Page (`ui/pages/3_Review_Queue.py`)
- List all FLAGGED and BLOCKED invoices
- Filter by vendor, issue type, date
- For each invoice card:
  - Vendor, invoice number, amount, issues
  - Agent's reason and memories used
  - Three buttons: **Approve** / **Reject** / **Hold**
  - Short note text field (required)
  - Submit → `POST /api/invoices/:id/review`
- Also list AUTO_APPROVED invoices with an **Overturn** button
- Refresh queue after each action

#### 6. Invoice Detail Page
- Accessible from any invoice in the queue or upload results
- Full invoice fields
- Issues list with type and details
- Agent decision: decision type, reason, confidence, memories_used
- Human review history
- Overturn button (if AUTO_APPROVED)

#### 7. Vendor Memory Page (`ui/pages/4_Vendor_Memory.py`)
- Vendor selector dropdown
- Call `GET /api/vendors/:id/memory`
- Display plain-English vendor profile (from `reflect()`)
- Show supporting observations (what Hindsight learned)
- Show past decisions table for that vendor
- "Tell the agent a fact" form → `POST /api/vendors/:id/facts`

#### 8. Admin Pages
- **Vendors page**: list, add, edit vendors and GSTINs
- **Settings page**: view and update safety limits (ROUNDING_LIMIT, MIN_EVIDENCE, MIN_CONFIDENCE, MAX_AUTO_APPROVE_AMOUNT, MAX_AUTO_APPROVE_DIFF)

#### 9. Demo Script (`scripts/run_demo.py`)
End-to-end replay of 3 months:
```bash
python scripts/run_demo.py --memory on
python scripts/run_demo.py --memory off  # compare
```
- Month 1: Upload all invoices, simulate accountant reviewing and approving/rejecting with notes
- Month 2: Upload, show auto-approvals kicking in
- Month 3: Upload, show high auto-handled rate + the "surprise" flag on the trusted vendor
- Print a summary table comparing memory ON vs OFF

#### 10. Project Configuration
- `requirements.txt` with pinned versions:
  - fastapi, uvicorn, sqlalchemy, bcrypt, python-jose, groq, hindsight-client, streamlit, plotly, pandas, pytest
- `.env.example` with all required variables
- `README.md` setup section verified against actual setup steps

### Deliverables
- [ ] `ui/app.py`
- [ ] `ui/pages/1_Dashboard.py`
- [ ] `ui/pages/2_Upload_and_Check.py`
- [ ] `ui/pages/3_Review_Queue.py`
- [ ] `ui/pages/4_Vendor_Memory.py`
- [ ] `scripts/run_demo.py`
- [ ] `requirements.txt`
- [ ] `.env.example`
- [ ] End-to-end smoke test

---

## Integration Points & Dependencies

```
Person 1 (models, checks) ──► Person 2 (agent uses Issue objects)
Person 1 (models, checks) ──► Person 3 (routes use models + checks)
Person 2 (agent, memory)  ──► Person 3 (routes call decide/retain/recall/reflect)
Person 3 (API)            ──► Person 4 (UI calls all API endpoints)
```

**Suggested order to avoid blockers:**
1. Person 1 defines models first (everyone depends on these)
2. Person 2 and Person 3 can work in parallel once models are done
3. Person 4 can mock the API while Person 3 builds it, then swap in real calls

---

## Shared Conventions

- All amounts in **integer paise** (₹100.50 = `10050`)
- Dates in **ISO format** (`2026-07-15`), grouped by invoice date not upload date
- Status values as string enums matching the lifecycle table
- Error format: `{"error": {"code": "...", "message": "..."}}`
- Config from environment variables via `os.getenv()` or `python-dotenv`
- No secrets committed; everything in `.env` (gitignored)

---

## Tonight's Milestone Checklist

- [ ] Database models defined and migrations run
- [ ] All 9 GST checks passing their unit tests
- [ ] GSTIN validation (format + checksum) working
- [ ] Safety rules enforced before AI is called
- [ ] Hindsight bank creation and retain/recall/reflect working
- [ ] LLM decision engine returning valid structured JSON
- [ ] Upload pipeline: CSV → issues → AI decision → DB
- [ ] Review endpoint: approve/reject/hold with memory retention
- [ ] Streamlit login + upload page functional
- [ ] Review queue showing flagged invoices with approve/reject actions
- [ ] Synthetic data generated for 3 months
- [ ] End-to-end: upload Month 1 → review → upload Month 2 → see auto-approvals
