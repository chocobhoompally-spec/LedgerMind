# LedgerMind — Demo Build Plan (Due Tonight)

**Stack:** Python · Streamlit · SQLite · Hindsight (memory) · Groq (LLM)  
No FastAPI. No JWT. Streamlit calls Python functions directly.

---

## What Judges Score

1. Fake data with realistic vendor habits
2. 5 GST checks + safety rules
3. Hindsight retain, recall, reflect
4. AI decisions with plain-English reasons
5. 3 screens: Upload, Review, Vendor Memory
6. Memory ON/OFF switch and learning curve chart

---

## Team Roles

| Person | Role | What they own |
|--------|------|---------------|
| **Person 1** | Data & Checks | Synthetic data generator, 5 GST checks, safety rules, database |
| **Person 2** | Memory & AI | Hindsight client, retain/recall/reflect, LLM decision engine |
| **Person 3** | Pipeline & Demo | Upload→check→decide→save in plain Python, demo script, demo video |
| **Person 4** | UI | 3 Streamlit screens + learning curve chart |

---

## Person 1 — Data & Checks

### Database (`server/models.py`)
Simple SQLite. Tables:

- **Vendor** — id, name, gstin, state_code
- **PurchaseOrder** — id, vendor_id, po_number, amount, gst_rate, po_date
- **Invoice** — id, vendor_id, invoice_number, invoice_date, po_number, vendor_gstin, taxable_amount, gst_rate, cgst, sgst, igst, total_amount, status, batch_id, memory_enabled
- **UploadBatch** — id, period, memory_enabled, uploaded_at
- **Issue** — id, invoice_id, type, details (JSON)
- **AgentDecision** — id, invoice_id, decision, reason, confidence, memories_used (JSON), safety_rule_applied, created_at
- **HumanReview** — id, invoice_id, action, note, created_at

Rules:
- All amounts as **integer paise** (₹100.50 = `10050`)
- Invoice status: `UPLOADED → CHECKED → CLEAN / AUTO_APPROVED / FLAGGED / BLOCKED / APPROVED / REJECTED / ON_HOLD / OVERTURNED`

### 5 GST Checks (`server/checks/rules.py`)
Plain Python. Returns a list of `Issue` objects.

| Check | Code | Logic |
|-------|------|-------|
| Rounding difference | `ROUNDING` | `abs(total - (taxable + tax))` between ₹0.01 and `ROUNDING_LIMIT` (default ₹10) |
| Duplicate invoice | `DUPLICATE` | Same vendor + same invoice number, OR same vendor + same amount + same date |
| Invalid GSTIN | `INVALID_GSTIN` | Fails 15-char format or checksum |
| Tax rate mismatch | `TAX_RATE_MISMATCH` | Invoice GST rate ≠ PO GST rate |
| Amount mismatch | `AMOUNT_MISMATCH` | Invoice amount differs from PO by more than rounding limit |

GSTIN format: `[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]` — validate format and checksum.

### Safety Rules (`server/checks/safety.py`)
Enforced in code **before** the AI is called. These can never be auto-approved.

| Rule | Result |
|------|--------|
| `INVALID_GSTIN` | BLOCKED |
| `DUPLICATE` (exact: same vendor + same invoice number) | BLOCKED |
| Invoice total above `MAX_AUTO_APPROVE_AMOUNT` (default ₹5,00,000) | FLAGGED |
| Amount diff above `MAX_AUTO_APPROVE_DIFF` (default ₹100) | FLAGGED |
| New vendor (no memory yet) | FLAGGED |

### Synthetic Data (`scripts/generate_data.py`)
15 vendors, 3 months, ~50 invoices per month. `--seed 42` for reproducibility.

| Vendor | Hidden habit | What the agent should learn |
|--------|-------------|------------------------------|
| Sharma Traders | Rounds up by ₹1–₹3 | Normal; auto-approve after Month 1 |
| Kumar Steels | Occasionally sends duplicates | Always block |
| Reddy Logistics | Credit notes arrive late | Hold, don't reject |
| Patel Electronics | Tax rate changes 12%→18% in Month 2 | Flag once, then auto-approve |
| Sri Sai Packaging | Clean, no issues | Trusted |
| Venkat Chemicals | Amount mismatches | Flag every time |
| Lakshmi Textiles | Wrong GSTIN occasionally | Always block |
| 8 more vendors | Mixed or clean patterns | |

In Month 3: plant a new issue on a previously clean trusted vendor.  
Output invoices and POs to `data/` as CSV.

### DB Setup (`server/setup.py`)
- Create all tables
- Load vendors and POs from generated CSV

### Deliverables
- [ ] `server/models.py`
- [ ] `server/checks/rules.py`
- [ ] `server/checks/safety.py`
- [ ] `scripts/generate_data.py`
- [ ] `server/setup.py`
- [ ] `data/` folder with generated CSVs

---

## Person 2 — Memory & AI

### Hindsight Client (`memory/client.py`)
- Init from `HINDSIGHT_URL` and `HINDSIGHT_API_KEY` in `.env`
- `get_or_create_bank()` — idempotent, one bank for the demo company

```python
client.create_bank(
    bank_id="ledgermind-demo",
    name="LedgerMind Demo - Invoice Review",
    mission=(
        "I help an accountant review GST purchase invoices. "
        "I learn each vendor's habits and the accountant's judgment, "
        "so routine issues can be handled without repeated manual review."
    ),
    disposition={"skepticism": 4, "literalism": 3, "empathy": 2},
)
```

### Retain Decisions (`memory/retain.py`)
`retain_decision(invoice, issues, action, note)` — called after every human review and auto-approval.

Retain four things:
- Accountant approvals/rejections/holds (with the note)
- Corrections/overturns (most valuable — stops the mistake repeating)
- Agent auto-approvals (so the agent records its own behavior)
- Vendor facts (things the accountant tells the agent directly)

```python
client.retain(
    bank_id="ledgermind-demo",
    content="Sharma Traders INV-1043 had ₹1 rounding. Accountant APPROVED: 'They always round up'.",
    context="invoice review decision",
    timestamp=invoice.invoice_date,
    document_id=f"decision-{invoice.id}",
    metadata={"vendor_id": str(vendor_id), "issue_type": "ROUNDING", "decision": "APPROVED"},
)
```

### Recall Vendor History (`memory/recall.py`)
`recall_vendor_history(vendor, issue_types)` — called before every AI decision.

```python
memories = client.recall(
    bank_id="ledgermind-demo",
    query=f"Past decisions about {vendor.name} {issue_type} issues",
    types=["world", "experience", "observation"],
    budget="mid",
)
```

### Reflect for Vendor Profiles (`memory/reflect.py`)
`reflect_vendor_profile(vendor)` — called when the Vendor Memory screen loads.

```python
profile = client.reflect(
    bank_id="ledgermind-demo",
    query=f"What should an accountant know about {vendor.name}'s invoices?",
    budget="low",
)
```

### Groq LLM Client (`agent/llm.py`)
- Init with `GROQ_API_KEY`, model from `LLM_MODEL` env var (`openai/gpt-oss-120b`)
- Retry up to `LLM_MAX_RETRIES` (default 3) on invalid output
- Safe fallback if all retries fail: `{"decision": "FLAG", "reason": "AI unavailable", "confidence": 0.0, "memories_used": []}`

### Decision Engine (`agent/decide.py`)
`decide(invoice, issues, vendor, memory_enabled)` — the function Person 3 calls.

```
If memory_enabled is False:
    → FLAG every invoice that has any issue

If memory_enabled is True:
    1. Safety rules check (in code, before AI)
       → BLOCK or FLAG immediately if triggered
    2. Recall memories for this vendor + issue types
    3. Call LLM with invoice + issues + memories
    4. Post-check: if AI says AUTO_APPROVE but safety rule applies → override to FLAG
```

Auto-approve only if ALL are true:
- No safety rule applies
- At least `MIN_EVIDENCE` (default 2) past approvals of this issue type for this vendor
- No rejection or overturn of this issue type since last approval
- AI confidence ≥ `MIN_CONFIDENCE` (default 0.8)

LLM must return:
```json
{
  "decision": "AUTO_APPROVE",
  "reason": "Sharma Traders has had ₹1–₹3 rounding on 4 past invoices, all approved.",
  "memories_used": ["decision-981", "decision-1012"],
  "confidence": 0.92
}
```

### Prompts (`agent/prompts.py`)
- System prompt: role, rules, required JSON output format
- User prompt: invoice fields, issues found, recalled memories, safety limits
- Include: "When in doubt, FLAG — a false alarm costs a minute, a wrong approval costs money"

### Deliverables
- [ ] `memory/client.py`
- [ ] `memory/retain.py`
- [ ] `memory/recall.py`
- [ ] `memory/reflect.py`
- [ ] `agent/llm.py`
- [ ] `agent/decide.py`
- [ ] `agent/prompts.py`

---

## Person 3 — Pipeline & Demo

### Upload Pipeline (`server/pipeline.py`)
One function: `process_batch(csv_path, po_csv_path, period, memory_enabled)`.

```
For each invoice row in the CSV:
    1. Parse and validate row → skip with clear error if malformed
    2. Save Invoice to DB, status = UPLOADED
    3. Run 5 GST checks → save Issues → status = CHECKED
    4. Run safety rules
       → if triggered: save AgentDecision, set status BLOCKED or FLAGGED, next invoice
    5. If memory ON and not blocked:
       → recall memories → call decide() → save AgentDecision
       → status = AUTO_APPROVED or FLAGGED
    6. If memory OFF and not blocked:
       → status = FLAGGED (if issues) or CLEAN (if none)
    7. If no issues: status = CLEAN

Return: {total, clean, auto_approved, flagged, blocked}
```

Also implement `review_invoice(invoice_id, action, note)`:
- `action`: APPROVE / REJECT / HOLD / OVERTURN
- Save HumanReview to DB
- Update invoice status
- Call `retain_decision()` to save to memory

### Demo Script (`scripts/run_demo.py`)
```bash
python scripts/run_demo.py --memory on
python scripts/run_demo.py --memory off
```

- **Month 1:** Upload → almost everything flagged → simulate accountant approving/rejecting with notes → retain all decisions
- **Month 2:** Upload → auto-approvals appear for known vendors → accountant reviews only new issues
- **Month 3:** Upload → most routine issues auto-approved → "surprise" invoice on clean vendor gets flagged: *"Unusual — this vendor has had no issues in 3 months."*

Print after each month:
```
Month 1: 50 invoices | 18 with issues | 0 auto-approved  | 18 flagged | 2 blocked
Month 2: 52 invoices | 17 with issues | 9 auto-approved  |  8 flagged | 0 blocked
Month 3: 48 invoices | 16 with issues | 13 auto-approved |  3 flagged | 0 blocked
```

End with Memory ON vs OFF comparison on Month 3 data.

### Demo Video (3–5 min)
1. Run `generate_data.py` → show the CSV
2. Run `run_demo.py --memory on` → show the table improving month over month
3. Open Streamlit → upload Month 3 invoices → show auto-approvals with reasons
4. Open Review Queue → approve a flagged invoice with a note
5. Open Vendor Memory → show what the agent learned about Sharma Traders
6. Switch Memory OFF → rerun → everything is flagged again (**the money shot**)

### Deliverables
- [ ] `server/pipeline.py`
- [ ] `scripts/run_demo.py`
- [ ] Demo video (3–5 min)

---

## Person 4 — UI

### App Entry (`ui/app.py`)
- Sidebar: Upload & Check / Review Queue / Vendor Memory
- Call `server/setup.py` on first run to init DB
- Session state: current batch_id, memory_enabled

### Upload & Check Screen (`ui/pages/2_Upload_and_Check.py`)
- Invoices CSV uploader
- POs CSV uploader
- **Memory ON/OFF toggle** — make this prominent, judges will look for it
- Upload button → calls `process_batch()` from `server/pipeline.py`

Results table:

| Vendor | Invoice # | Amount | Issues | Decision | Reason |
|--------|-----------|--------|--------|----------|--------|
| Sharma Traders | INV-1043 | ₹48,301 | ROUNDING ₹1 | 🟢 Auto-approved | Approved 4 times before |
| Kumar Steels | INV-0892 | ₹1,20,000 | DUPLICATE | 🔴 Blocked | Safety rule: exact duplicate |
| Patel Electronics | INV-2201 | ₹75,000 | TAX_RATE 12%→18% | 🟡 Flagged | New issue type for this vendor |

Decision badges:
- ✅ `CLEAN`
- 🟢 `AUTO_APPROVED`
- 🟡 `FLAGGED`
- 🔴 `BLOCKED`

Show the **learning curve chart** below the table.

### Review Queue Screen (`ui/pages/3_Review_Queue.py`)
List all FLAGGED and BLOCKED invoices. For each:
- Vendor, invoice number, date, amount
- Issues found
- Agent's reason + memories used
- **✅ Approve / ❌ Reject / ⏸ Hold** buttons
- Note field (required)
- On submit: call `review_invoice()` from `server/pipeline.py`

Also show AUTO_APPROVED invoices with an **↩ Overturn** button.

Refresh queue after every action.

### Vendor Memory Screen (`ui/pages/4_Vendor_Memory.py`)
- Vendor dropdown
- On select: call `reflect_vendor_profile()` from `memory/reflect.py` → show in a highlighted box
- Table of past decisions from DB: invoice, issue type, action, note, date
- "Tell the agent a fact" input + submit → calls `retain()` directly

### Learning Curve Chart
On the Upload screen, below results. Pull from DB:
- X axis: Month 1, Month 2, Month 3
- Y axis: % of issues auto-handled
- Two lines: 🟢 Memory ON / 🔴 Memory OFF (flat at 0%)

Use Plotly. Make the difference visually obvious.

### Config Files
`.env.example`:
```
HINDSIGHT_URL=
HINDSIGHT_API_KEY=
GROQ_API_KEY=
LLM_MODEL=openai/gpt-oss-120b
LLM_MAX_RETRIES=3
DATABASE_URL=sqlite:///ledgermind.db
ROUNDING_LIMIT=10
MIN_EVIDENCE=2
MIN_CONFIDENCE=0.8
MAX_AUTO_APPROVE_AMOUNT=500000
MAX_AUTO_APPROVE_DIFF=100
```

Pin all versions in `requirements.txt`.

### Deliverables
- [ ] `ui/app.py`
- [ ] `ui/pages/2_Upload_and_Check.py`
- [ ] `ui/pages/3_Review_Queue.py`
- [ ] `ui/pages/4_Vendor_Memory.py`
- [ ] `requirements.txt`
- [ ] `.env.example`

---

## How the Pieces Connect

```
Person 1                    Person 2               Person 3             Person 4
────────────────────────────────────────────────────────────────────────────────
models.py                   memory/client.py
checks/rules.py    ──►      memory/retain.py  ──►  pipeline.py   ──►   Upload screen
checks/safety.py            memory/recall.py        run_demo.py          Review screen
generate_data.py            memory/reflect.py        demo video           Vendor Memory
setup.py                    agent/decide.py                              Chart
                            agent/llm.py
                            agent/prompts.py
```

**Order to avoid blockers:**
1. Person 1 defines `models.py` first — everyone imports from it
2. Person 2 and Person 3 work in parallel once models exist
3. Person 4 can stub pipeline calls while Person 3 builds it, then swap in real calls

---

## Shared Conventions

- Amounts: integer paise always (₹100.50 → `10050`)
- Dates: ISO format (`2026-07-15`)
- Config: read from `.env` via `python-dotenv`
- No secrets in git

---

## Tonight's Checklist

- [ ] Synthetic data generated (3 months, 15 vendors, seed 42)
- [ ] 5 GST checks working
- [ ] Safety rules enforced before AI is called
- [ ] Hindsight bank created; retain/recall/reflect working
- [ ] LLM returning valid JSON decisions with reasons
- [ ] Pipeline: CSV → issues → AI decision → saved to DB
- [ ] Upload screen with Memory ON/OFF toggle showing results
- [ ] Review queue: approve/reject with notes, decisions retained to memory
- [ ] Vendor Memory screen shows reflect() output
- [ ] Learning curve chart shows improvement month over month
- [ ] Demo script runs all 3 months end to end
- [ ] Memory ON vs OFF comparison works
- [ ] Demo video recorded
