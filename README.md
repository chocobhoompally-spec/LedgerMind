# 🧾 LedgerMind: A GST Invoice Agent That Learns Your Accountant's Judgment

LedgerMind checks purchase invoices for common GST issues and **remembers every decision your accountant makes**. Over time it learns each vendor's habits, handles routine issues on its own, and flags only what truly needs a human.

Existing tools find the problems. **LedgerMind learns which problems actually matter.**

Built with [Hindsight](https://github.com/vectorize-io/hindsight) agent memory by Vectorize.

---

## Table of Contents

1. [The Problem](#the-problem)
2. [Our Solution](#our-solution)
3. [How It Works](#how-it-works)
4. [Features](#features)
5. [Invoice Lifecycle](#invoice-lifecycle)
6. [How Hindsight Memory Is Used](#how-hindsight-memory-is-used)
7. [The Decision Engine](#the-decision-engine)
8. [GST Checks](#gst-checks)
9. [Safety Rules](#safety-rules)
10. [The Learning Curve](#the-learning-curve)
11. [Data Model](#data-model)
12. [Edge Cases & How They're Handled](#edge-cases--how-theyre-handled)
13. [Frontend Pages](#frontend-pages)
14. [API Reference](#api-reference)
15. [Synthetic Data](#synthetic-data)
16. [Security](#security)
17. [Tech Stack](#tech-stack)
18. [Project Structure](#project-structure)
19. [Getting Started](#getting-started)
20. [Configuration](#configuration)
21. [Testing](#testing)
22. [Demo Walkthrough](#demo-walkthrough)
23. [Known Limitations](#known-limitations)
24. [Roadmap](#roadmap)

---

## The Problem

Every month, businesses in India receive hundreds of purchase invoices from their vendors. Before these invoices can be paid and GST input tax credit can be claimed, an accountant has to check each one.

Most invoices have small issues:

- **Rounding differences**: the total is off by ₹1–₹5
- **Duplicate invoices**: the same bill sent twice
- **Invalid or mismatched GSTIN**: the vendor's GST number is wrong or doesn't match their records
- **Tax rate mismatches**: the invoice charges 18% GST when the purchase order says 12%
- **Amount mismatches**: the invoice doesn't match the purchase order

Existing GST software is good at *finding* these issues. But finding them is only half the job. For every flag, the accountant still has to decide: **Is this a real problem, or is it normal for this vendor?**

And that decision is made again and again, every month:

- Sharma Traders always rounds up by ₹1. The accountant approves it. Next month, the software flags it again.
- Reddy Logistics always sends credit notes two weeks late. The accountant waits. Next month, it's flagged again.
- Patel Electronics' tax rate changed last quarter, and the accountant confirmed it was correct. The software still flags it every month.

The software never learns. The accountant's judgment lives only in their head. When they are busy, on leave, or leave the company, that knowledge is gone.

**The result:** hours wasted re-reviewing harmless issues, real problems buried under noise, and new team members learning every vendor's habits from scratch.

---

## Our Solution

LedgerMind is an AI agent that **remembers the accountant's decisions** and learns each vendor's habits over time. It:

1. Checks every invoice for common GST issues
2. Recalls what happened with this vendor before
3. Auto-approves issues the accountant has already accepted as normal, and explains why
4. Flags only what is genuinely new or risky
5. Saves every new decision, so it gets smarter each month

**Month 1:** The agent flags almost everything, like any other tool.
**Month 3:** It handles most routine issues on its own and flags only the invoices that truly need a human, including unusual behavior from vendors it knows well.

---

## How It Works

```
 Upload invoices ──► Rule checks ──► Recall vendor memory ──► AI decision
                                                                   │
                        ┌──────────────────────────────────────────┤
                        ▼                                          ▼
              Auto-approved (with reason)              Flagged for accountant
                        │                                          │
                        │                                 Approve / Reject + note
                        │                                          │
                        └───────────────► Retain to memory ◄───────┘
                                                │
                                                ▼
                               Next month's decisions are smarter
```

1. **The accountant uploads** a month of invoices (CSV or JSON), along with purchase orders.
2. **The rule checker** (plain Python, no AI) finds every issue on every invoice.
3. **The agent recalls** this vendor's history from Hindsight: past issues, past decisions, and the accountant's notes.
4. **The AI decides** for each issue: auto-approve (seen and accepted before), flag (new or risky), or block (a safety rule applies). Every decision comes with a plain-English reason and the memories it relied on.
5. **The accountant reviews** only the flagged invoices and adds a short note.
6. **Every decision is retained** to memory, including corrections to the agent's own mistakes.

---

## Features

### For Accountants
- Upload a month of invoices and purchase orders in one step
- See each invoice's result: **Clean**, **Auto-approved**, **Flagged**, or **Blocked**
- Read *why* the agent made each decision, with links to the past decisions it used
- Approve, reject, or put flagged invoices on hold, with a short note
- **Overturn** any auto-approval. The agent learns from the correction.
- Open a **Vendor Memory** page to see everything the agent has learned about a vendor

### For Business Owners / Admins
- Dashboard with monthly stats: invoices processed, auto-handled rate, human reviews needed
- A **learning curve chart** showing the auto-handled rate month over month
- Manage vendors and their GSTINs
- Set safety limits (for example, never auto-approve invoices above ₹5,00,000)
- **Memory ON / OFF switch** to compare the agent with and without memory on the same data

### For the System
- Deterministic GST checks, so issues are never missed or invented by the AI
- A memory lookup before every decision
- Automatic retention of every human decision and correction
- A full audit trail: every decision stores the issues found, the memories recalled, the AI's reason, and who made the final call
- Retries and a safe fallback when the AI returns invalid output

---

## Invoice Lifecycle

Every invoice moves through these states:

| State | Meaning | What moves it forward |
|---|---|---|
| `UPLOADED` | Received, not yet checked | Rule checker runs |
| `CHECKED` | Issues found (or none) | Decision engine runs |
| `CLEAN` | No issues found | Terminal (ready to pay) |
| `AUTO_APPROVED` | Issues found, but memory shows they are normal for this vendor | Accountant can overturn |
| `FLAGGED` | Needs a human decision | Accountant reviews |
| `BLOCKED` | A safety rule applies (e.g. invalid GSTIN) | Accountant reviews; can never be auto-approved |
| `APPROVED` | Accountant approved | Terminal; decision retained |
| `REJECTED` | Accountant rejected | Terminal; decision retained |
| `ON_HOLD` | Waiting on something (e.g. a credit note) | Accountant resolves later |
| `OVERTURNED` | Accountant reversed an auto-approval | Correction retained; treated as `FLAGGED` |

---

## How Hindsight Memory Is Used

Memory is the core of LedgerMind. Without it, the agent is just another rule checker that flags the same things every month.

LedgerMind uses Hindsight's three core operations:

| Operation | When | What it does in LedgerMind |
|---|---|---|
| `retain()` | After every accountant decision or correction | Saves the decision, the issue, the vendor, and the accountant's note |
| `recall()` | Before deciding on any invoice | Finds this vendor's past issues and how they were handled |
| `reflect()` | On the Vendor Memory page and monthly summary | Produces a plain-English profile of the vendor's habits |

### One memory bank per business

Each business gets its own memory bank, so one company's vendor habits never leak into another's.

```python
from hindsight_client import Hindsight

client = Hindsight(base_url=HINDSIGHT_URL, api_key=HINDSIGHT_API_KEY)

client.create_bank(
    bank_id="ledgermind-acme-industries",
    name="Acme Industries - Invoice Review",
    mission=(
        "I help an accountant review GST purchase invoices. "
        "I learn each vendor's habits and the accountant's judgment, "
        "so routine issues can be handled without repeated manual review."
    ),
    disposition={"skepticism": 4, "literalism": 3, "empathy": 2},
)
```

The bank is also given **directives** (hard rules), such as *"Never recommend approving an invoice with an invalid GSTIN."*

### Retaining a decision

```python
client.retain(
    bank_id=bank_id,
    content=(
        "Sharma Traders invoice INV-1043 had a ₹1 rounding difference "
        "(invoice total ₹48,301 vs computed ₹48,300). "
        "Accountant APPROVED with note: 'They always round up, this is fine.'"
    ),
    context="invoice review decision",
    timestamp=invoice.invoice_date,
    document_id=f"decision-{invoice.id}",
    metadata={"vendor_id": "V003", "issue_type": "ROUNDING", "decision": "APPROVED"},
)
```

We retain four kinds of events:

1. **Accountant decisions**: approve, reject, or hold, with the note
2. **Corrections**: when the accountant overturns an auto-approval. These are the most valuable memories.
3. **Agent actions**: what the agent auto-approved and why, so it has a record of its own behavior
4. **Vendor facts**: things the accountant tells the agent directly (e.g. "Patel Electronics moved to 18% GST from April")

### Recalling before a decision

```python
memories = client.recall(
    bank_id=bank_id,
    query=f"Past decisions about {vendor.name} {issue.type} issues",
    types=["world", "experience", "observation"],
    budget="mid",
)
```

The recalled memories are passed to the LLM along with the issues found.

### Observations: learned vendor habits

After memories are retained, Hindsight automatically consolidates related facts into **observations**, for example *"Sharma Traders routinely rounds invoice totals up by ₹1–₹3; the accountant has approved this every time."* Each observation is backed by the source memories that support it.

These observations power the **Vendor Memory** page, so judges and users can see exactly what the agent has learned.

### Reflecting for vendor profiles

```python
profile = client.reflect(
    bank_id=bank_id,
    query=f"What should an accountant know about {vendor.name}'s invoices?",
    budget="low",
)
```

### Memory ON vs OFF

| | Memory OFF | Memory ON |
|---|---|---|
| Rounding difference from Sharma Traders | Flagged | Auto-approved: "Approved 4 times before; accountant said this is normal" |
| Tax rate change from Patel Electronics | Flagged every month | Auto-approved after the accountant confirmed the change |
| Duplicate from Kumar Steels | Flagged | Flagged, with context: "This vendor sent duplicates in March and May" |
| New issue from a trusted vendor | Flagged, no context | Flagged: "Unusual. This vendor has had no issues in 3 months." |

---

## The Decision Engine

Decisions happen in three layers. Each layer has one job.

### Layer 1: Rule checker (deterministic)
Plain Python finds every issue. The AI never decides *whether* an issue exists, only what it means. This makes the system predictable and testable.

### Layer 2: Safety rules (deterministic)
Some issues can never be auto-approved, whatever memory says. See [Safety Rules](#safety-rules).

### Layer 3: AI judgment (LLM + memory)
For everything else, the LLM receives:
- The invoice and the issues found
- The recalled memories for this vendor
- The safety limits

It must return structured JSON:

```json
{
  "decision": "AUTO_APPROVE",
  "reason": "Sharma Traders has had ₹1–₹3 rounding differences on 4 past invoices. The accountant approved all of them, noting 'they always round up'.",
  "memories_used": ["decision-INV-0981", "decision-INV-1012"],
  "confidence": 0.92
}
```

### Rules for auto-approval
An issue is auto-approved only if **all** of these are true:
- No safety rule applies
- Memory shows at least `MIN_EVIDENCE` (default 2) past approvals of the same issue type for this vendor
- Memory shows no rejection or overturn of that issue type for this vendor since the last approval
- The AI's confidence is at least `MIN_CONFIDENCE` (default 0.8)

Otherwise the invoice is flagged. **When in doubt, flag.** A false alarm costs a minute; a wrong approval costs money.

---

## GST Checks

| Check | Code | How it works |
|---|---|---|
| Rounding difference | `ROUNDING` | Invoice total differs from line items + tax by ₹0.01–₹`ROUNDING_LIMIT` (default ₹10) |
| Amount mismatch | `AMOUNT_MISMATCH` | Invoice amount differs from the purchase order by more than the rounding limit |
| Duplicate invoice | `DUPLICATE` | Same vendor + same invoice number, or same vendor + same amount + same date |
| Invalid GSTIN | `INVALID_GSTIN` | Fails the 15-character format or checksum |
| GSTIN mismatch | `GSTIN_MISMATCH` | Valid GSTIN, but different from the one on file for this vendor |
| Tax rate mismatch | `TAX_RATE_MISMATCH` | GST rate on the invoice differs from the rate on the purchase order |
| Wrong tax type | `TAX_TYPE_MISMATCH` | IGST charged on an intra-state purchase, or CGST+SGST on an inter-state one (based on state codes) |
| Missing PO | `MISSING_PO` | Invoice references a purchase order that doesn't exist |
| Pending credit note | `PENDING_CREDIT_NOTE` | Invoice is expected to be adjusted by a credit note that hasn't arrived |

### GSTIN format
A GSTIN has 15 characters: a 2-digit state code, the 10-character PAN, an entity number, the letter `Z`, and a checksum character. LedgerMind validates both the format and the checksum.

---

## Safety Rules

These issues are **always** `BLOCKED` or `FLAGGED`, never auto-approved:

| Rule | Result |
|---|---|
| Invalid GSTIN | `BLOCKED` |
| GSTIN doesn't match vendor on file | `BLOCKED` |
| Exact duplicate (same vendor + same invoice number) | `BLOCKED` |
| Invoice total above `MAX_AUTO_APPROVE_AMOUNT` | `FLAGGED` |
| Amount difference above `MAX_AUTO_APPROVE_DIFF` | `FLAGGED` |
| New vendor (no memory yet) | `FLAGGED` |

Safety rules are checked in code **before** the AI is called, so a bad AI response can never bypass them.

---

## The Learning Curve

LedgerMind tracks these numbers every month:

| Metric | Meaning |
|---|---|
| **Auto-handled rate** | % of invoices with issues that were auto-approved correctly |
| **Human reviews** | Number of invoices the accountant had to look at |
| **Overturn rate** | % of auto-approvals the accountant later reversed (lower is better) |
| **Time saved (estimate)** | Human reviews avoided × average review time |

Results on our 3-month synthetic dataset:

| Month | Invoices | With issues | Auto-handled | Human reviews | Overturned |
|---|---|---|---|---|---|
| 1 | _fill in_ | _fill in_ | _fill in_ | _fill in_ | _fill in_ |
| 2 | _fill in_ | _fill in_ | _fill in_ | _fill in_ | _fill in_ |
| 3 | _fill in_ | _fill in_ | _fill in_ | _fill in_ | _fill in_ |

---

## Data Model

```
Company
 ├─ id
 ├─ name
 ├─ gstin
 ├─ state_code
 └─ hindsight_bank_id

User
 ├─ id
 ├─ name
 ├─ email (unique)
 ├─ password_hash
 ├─ role: ACCOUNTANT | ADMIN
 └─ company_id → Company

Vendor
 ├─ id
 ├─ company_id → Company
 ├─ name
 ├─ gstin
 └─ state_code

PurchaseOrder
 ├─ id
 ├─ company_id → Company
 ├─ vendor_id → Vendor
 ├─ po_number
 ├─ amount
 ├─ gst_rate
 └─ po_date

Invoice
 ├─ id
 ├─ company_id → Company
 ├─ vendor_id → Vendor
 ├─ invoice_number
 ├─ invoice_date
 ├─ po_number
 ├─ vendor_gstin           (as printed on the invoice)
 ├─ taxable_amount
 ├─ gst_rate
 ├─ cgst / sgst / igst
 ├─ total_amount
 ├─ status                 (see lifecycle)
 ├─ batch_id → UploadBatch
 └─ UNIQUE(company_id, vendor_id, invoice_number, batch_id)

UploadBatch
 ├─ id
 ├─ company_id → Company
 ├─ period                 (e.g. 2026-07)
 ├─ uploaded_by → User
 ├─ memory_enabled         (ON / OFF for this run)
 └─ uploaded_at

Issue
 ├─ id
 ├─ invoice_id → Invoice
 ├─ type                   (ROUNDING, DUPLICATE, ...)
 └─ details (JSON)

AgentDecision                             ← audit trail
 ├─ id
 ├─ invoice_id → Invoice
 ├─ decision               (AUTO_APPROVE | FLAG | BLOCK)
 ├─ reason
 ├─ confidence
 ├─ memories_used (JSON)
 ├─ safety_rule_applied
 └─ created_at

HumanReview
 ├─ id
 ├─ invoice_id → Invoice
 ├─ reviewer_id → User
 ├─ action                 (APPROVE | REJECT | HOLD | OVERTURN)
 ├─ note
 ├─ retained_to_memory     (true once saved to Hindsight)
 └─ created_at
```

Hindsight holds the **learned knowledge**. The database holds the **raw records and audit trail**. If Hindsight is temporarily unavailable, `retained_to_memory = false` lets a background job retry later.

---

## Edge Cases & How They're Handled

| Edge case | Handling |
|---|---|
| **Same file uploaded twice** | Detected by file hash; the user is warned and can cancel |
| **Duplicate invoice across months** | Checked against all past batches, not just the current one |
| **New vendor with no memory** | Always flagged. The first few decisions build its memory. |
| **Vendor changes GSTIN** | Blocked until an admin updates the vendor record; the change is retained as a vendor fact |
| **Accountant contradicts an earlier decision** | Both are retained with timestamps. Recent decisions weigh more, and a recent rejection stops auto-approval of that issue type. |
| **Accountant overturns an auto-approval** | Invoice returns to `FLAGGED`; the correction is retained so the mistake isn't repeated |
| **LLM returns invalid JSON or fails a tool call** | Retried up to `LLM_MAX_RETRIES`; if still invalid, the invoice is flagged with reason "AI unavailable" |
| **LLM suggests approving a safety-rule issue** | Impossible: safety rules are enforced in code before and after the AI call |
| **Hindsight is unavailable** | The agent runs in Memory OFF mode (everything with issues is flagged); decisions queue for retention |
| **Invoice with multiple issues** | Every issue must be individually approvable for auto-approval; otherwise flagged |
| **Tax rate legitimately changed** | Flagged the first time; after the accountant approves with a note, later invoices at the new rate are auto-approved |
| **Credit note arrives late** | Invoice put `ON_HOLD`; when the credit note arrives, the hold is resolved and the delay pattern is retained |
| **Missing or malformed fields in upload** | Row rejected with a clear error; the rest of the file still processes |
| **Very large upload** | Processed in batches; progress shown in the UI |
| **Multiple businesses** | Separate Hindsight memory banks; one company's data never informs another's decisions |
| **Money precision** | All amounts stored as integer paise, never floats |
| **Dates** | Stored in ISO format; months grouped by invoice date, not upload date |

---

## Frontend Pages

| Page | Description |
|---|---|
| **Login** | Email and password |
| **Dashboard** | This month's stats, the learning curve chart, and recent activity |
| **Upload & Check** | Upload invoices and POs; choose Memory ON/OFF; see results per invoice |
| **Review Queue** | Flagged and blocked invoices with the agent's reasons; approve, reject, or hold with a note |
| **Invoice Detail** | Issues found, the agent's decision and reason, memories used, and full history |
| **Vendor Memory** | Everything the agent has learned about a vendor, generated by `reflect()`, with the supporting observations |
| **Vendors** (admin) | Manage vendors and GSTINs |
| **Settings** (admin) | Safety limits and thresholds |

### Each invoice result shows
- Vendor, invoice number, date, and amount
- Issues found (e.g. `ROUNDING ₹1`, `TAX_RATE 12% → 18%`)
- **Decision:** `Clean`, `Auto-approved`, `Flagged`, or `Blocked`
- **Reason:** a plain-English sentence explaining why
- **Based on:** the past decisions the agent relied on

---

## API Reference

All endpoints require authentication. Admin-only endpoints are marked 🔒.

### Auth
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Log in and receive a JWT |
| `GET` | `/api/auth/me` | Current user info |

### Uploads & Invoices
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/batches` | Upload invoices and POs (`memory_enabled` flag) |
| `GET` | `/api/batches/:id` | Batch status and results |
| `GET` | `/api/invoices?status=flagged` | List invoices by status |
| `GET` | `/api/invoices/:id` | Invoice detail, issues, decision, and memories used |

### Reviews
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/invoices/:id/review` | Approve, reject, or hold with a note |
| `POST` | `/api/invoices/:id/overturn` | Reverse an auto-approval |

### Vendors & Memory
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/vendors` | List vendors |
| `GET` | `/api/vendors/:id/memory` | What the agent has learned (reflect + observations) |
| `POST` | `/api/vendors/:id/facts` | Tell the agent a fact about a vendor |
| `POST` | `/api/vendors` 🔒 | Add a vendor |
| `PATCH` | `/api/vendors/:id` 🔒 | Update a vendor (e.g. new GSTIN) |

### Stats & Settings
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/stats/learning-curve` | Monthly metrics for the chart |
| `GET` | `/api/settings` 🔒 | Current safety limits |
| `PATCH` | `/api/settings` 🔒 | Update safety limits |

### Error format

```json
{ "error": { "code": "NOT_FOUND", "message": "Invoice not found" } }
```

| Status | Meaning |
|---|---|
| `400` | Invalid input or validation failure |
| `401` | Missing or invalid JWT |
| `403` | Not allowed (e.g. accountant hitting an admin route) |
| `404` | Resource not found |
| `409` | Conflict (e.g. file already uploaded) |
| `503` | AI or memory service temporarily unavailable |

### Example: review a flagged invoice
```json
POST /api/invoices/1043/review
{
  "action": "APPROVE",
  "note": "They always round up by a rupee or two, this is fine."
}
```

### Example: invoice decision
```json
GET /api/invoices/1043
{
  "invoice_number": "ST/2026/0412",
  "vendor": "Sharma Traders",
  "total_amount": "48301.00",
  "issues": [{ "type": "ROUNDING", "difference": "1.00" }],
  "decision": "AUTO_APPROVE",
  "reason": "Sharma Traders has had ₹1–₹3 rounding differences on 4 past invoices, all approved by the accountant.",
  "memories_used": ["decision-981", "decision-1012"],
  "status": "AUTO_APPROVED"
}
```

---

## Synthetic Data

All data in this repo is synthetic but designed to look real. The generator creates **15 vendors** over **3 months** (about 50 invoices per month), each vendor with a hidden habit:

| Vendor | Hidden habit | What the agent should learn |
|---|---|---|
| Sharma Traders | Rounds totals up by ₹1–₹3 | Normal; auto-approve |
| Kumar Steels | Occasionally sends duplicates | Always flag duplicates |
| Reddy Logistics | Credit notes arrive ~2 weeks late | Hold, don't reject |
| Patel Electronics | Tax rate moves from 12% to 18% in Month 2 | Approved once, then normal |
| Sri Sai Packaging | Clean, no issues | Trusted |
| Venkat Chemicals | Wrong tax type (IGST vs CGST+SGST) now and then | Flag every time |
| Lakshmi Textiles | Real amount mismatches | Never auto-approve |
| …and 8 more | Mixed patterns | |

**Planted surprise:** in Month 3, a trusted vendor sends an invoice with a brand-new issue. The agent should flag it as unusual for that vendor.

```bash
python scripts/generate_data.py --months 3 --vendors 15 --seed 42
```

The same seed always produces the same dataset, so results are reproducible.

---

## Security

- Passwords hashed with bcrypt, never stored in plain text
- Role checks enforced server-side on every protected route
- All uploads validated server-side before processing
- Each company's data and memory bank are fully isolated
- API keys (Groq, Hindsight) kept in environment variables, never committed
- JWT tokens expire after 24 hours
- Every decision, human or AI, is logged with who made it and why

---

## Tech Stack

| Layer | Choice |
|---|---|
| Memory | [Hindsight](https://hindsight.vectorize.io/) (Hindsight Cloud, `hindsight-client`) |
| LLM | Groq (`openai/gpt-oss-120b`) |
| Backend | Python, FastAPI |
| Frontend | Streamlit |
| Database | SQLite (dev) / PostgreSQL (prod) |
| Charts | Plotly |
| Auth | JWT |

---

## Project Structure

```
ledgermind/
├── server/
│   ├── main.py                # FastAPI app
│   ├── models.py              # database models
│   ├── checks/
│   │   ├── rules.py           # GST checks
│   │   ├── gstin.py           # GSTIN format + checksum
│   │   └── safety.py          # safety rules
│   ├── agent/
│   │   ├── decide.py          # decision engine (LLM + memory)
│   │   ├── prompts.py         # LLM prompts
│   │   └── llm.py             # Groq client with retries
│   ├── memory/
│   │   ├── client.py          # Hindsight client setup
│   │   ├── retain.py          # saving decisions and facts
│   │   ├── recall.py          # vendor history lookup
│   │   └── reflect.py         # vendor profiles
│   ├── routes/
│   │   ├── auth.py
│   │   ├── batches.py
│   │   ├── invoices.py
│   │   ├── vendors.py
│   │   └── stats.py
│   └── tests/
├── ui/
│   ├── app.py                 # Streamlit entry
│   └── pages/
│       ├── 1_Dashboard.py
│       ├── 2_Upload_and_Check.py
│       ├── 3_Review_Queue.py
│       └── 4_Vendor_Memory.py
├── scripts/
│   ├── generate_data.py       # synthetic dataset
│   └── run_demo.py            # replay 3 months end to end
├── data/                      # generated invoices and POs
├── docs/
│   └── architecture.png
├── .env.example
└── README.md
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- A [Hindsight Cloud](https://ui.hindsight.vectorize.io) account (or a self-hosted Hindsight server)
- A [Groq](https://groq.com/) API key

### Setup
```bash
# 1. Clone
git clone <repo-url>
cd ledgermind

# 2. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# add your HINDSIGHT_URL, HINDSIGHT_API_KEY, GROQ_API_KEY

# 4. Generate synthetic data
python scripts/generate_data.py --months 3 --vendors 15 --seed 42

# 5. Set up the database and memory bank
python -m server.setup

# 6. Run the backend
uvicorn server.main:app --reload

# 7. Run the UI (new terminal)
streamlit run ui/app.py
```

Open http://localhost:8501.

### Replay the full 3-month demo
```bash
python scripts/run_demo.py --memory on
python scripts/run_demo.py --memory off   # compare
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `HINDSIGHT_URL` | – | Hindsight API URL |
| `HINDSIGHT_API_KEY` | – | Hindsight API key |
| `GROQ_API_KEY` | – | Groq API key |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model used for decisions |
| `LLM_MAX_RETRIES` | `3` | Retries when the AI returns invalid output |
| `DATABASE_URL` | `sqlite:///ledgermind.db` | Database connection string |
| `JWT_SECRET` | – | Secret for signing tokens |
| `ROUNDING_LIMIT` | `10` | Max ₹ difference treated as rounding |
| `MIN_EVIDENCE` | `2` | Past approvals needed before auto-approving an issue type |
| `MIN_CONFIDENCE` | `0.8` | Minimum AI confidence to auto-approve |
| `MAX_AUTO_APPROVE_AMOUNT` | `500000` | Invoices above this (₹) are always flagged |
| `MAX_AUTO_APPROVE_DIFF` | `100` | Differences above this (₹) are always flagged |

---

## Testing

```bash
pytest
```

Key test cases:
- Every GST check catches its issue and ignores clean invoices
- GSTIN validation accepts valid GSTINs and rejects bad format or checksum
- Safety rules block issues even when the AI suggests approval
- Invalid AI output is retried, then safely falls back to `FLAGGED`
- A new vendor is always flagged
- Auto-approval requires `MIN_EVIDENCE` past approvals
- A recent rejection stops auto-approval of that issue type
- An overturn is retained and changes the next decision
- Memory OFF mode flags every invoice with issues
- The same data seed produces the same dataset
- Amounts use integer paise with no floating-point errors

---

## Demo Walkthrough

1. **Month 1, Memory ON:** Upload 50 invoices. Almost every invoice with an issue is flagged, because the agent knows nothing yet. The accountant reviews them and leaves short notes.
2. **Month 2:** Upload new invoices. The agent auto-approves routine issues it has seen accepted before, and explains each one ("approved because you accepted this rounding twice before").
3. **Month 3:** Only a handful of invoices need a human. The dashboard chart shows the auto-handled rate climbing.
4. **The surprise:** A trusted vendor sends an invoice with a new issue. The agent flags it: *"Unusual. This vendor has had no issues in 3 months."*
5. **Vendor Memory:** Open Sharma Traders and see what the agent has learned, backed by real past decisions.
6. **Memory OFF:** Rerun Month 3 with memory off. Everything is flagged again. That difference is the value of memory.

---

## Known Limitations

- Uses structured invoice data (CSV/JSON); reading scanned PDF invoices is not supported yet
- Tested on synthetic data only
- Does not yet match invoices against GSTR-2B filings from the GST portal
- Decisions depend on the quality of the accountant's notes; vague notes lead to weaker learning
- The agent learns from one accountant's judgment per company; conflicting reviewers are handled by recency, not by role

---

## Roadmap

### Core
- [ ] PDF and image invoice reading (OCR)
- [ ] GSTR-2B matching: flag invoices the vendor hasn't filed
- [ ] Tally and Zoho Books import / export
- [ ] Email notifications for flagged invoices
- [ ] Bulk approve for low-risk flags

### Smarter Memory
- [ ] Learn per-reviewer trust (senior accountant decisions weigh more)
- [ ] Mental models for company-wide policies (e.g. "we always accept rounding under ₹5")
- [ ] Explain changes in vendor behavior over time ("this vendor's error rate doubled this quarter")
- [ ] Onboarding mode: new accountants read the agent's vendor profiles to get up to speed

### Trust & Transparency
- [ ] Monthly report: what the agent learned and what it auto-approved
- [ ] Confidence calibration dashboard (how often high-confidence decisions were overturned)
- [ ] Undo window before auto-approved invoices are marked ready to pay

### Operations
- [ ] Multi-user teams with roles (reviewer, approver, auditor)
- [ ] Rate limiting and a full audit log viewer
- [ ] Mobile-friendly review queue
