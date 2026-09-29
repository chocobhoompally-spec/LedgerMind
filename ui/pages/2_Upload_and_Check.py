"""
LedgerMind — ui/pages/2_Upload_and_Check.py
Upload invoices CSV + optional POs CSV, toggle memory, see per-invoice decisions.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from ui.helpers import api_get, api_post, show_sidebar

show_sidebar()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STATUS_BADGE = {
    "CLEAN":         "✅ Clean",
    "AUTO_APPROVED": "🟢 Auto-approved",
    "FLAGGED":       "🟡 Flagged",
    "BLOCKED":       "🔴 Blocked",
    "APPROVED":      "✅ Approved",
    "REJECTED":      "❌ Rejected",
    "ON_HOLD":       "⏸ On Hold",
    "OVERTURNED":    "↩ Overturned",
}

STATUS_ORDER = ["BLOCKED", "FLAGGED", "AUTO_APPROVED", "CLEAN", "APPROVED", "REJECTED", "ON_HOLD", "OVERTURNED"]


def _format_issues(issues: list[dict]) -> str:
    if not issues:
        return "—"
    parts = []
    for iss in issues:
        t = iss.get("type", "")
        d = iss.get("details", {})
        if t == "ROUNDING":
            diff = d.get("difference_paise", 0)
            parts.append(f"ROUNDING ₹{diff / 100:.2f}")
        elif t == "TAX_RATE_MISMATCH":
            inv = d.get("invoice_rate_pct", d.get("invoice_rate_pct100", 0))
            po  = d.get("po_rate_pct", d.get("po_rate_pct100", 0))
            parts.append(f"TAX_RATE {inv:.0f}%→{po:.0f}%")
        elif t == "AMOUNT_MISMATCH":
            diff = d.get("difference_paise", 0)
            parts.append(f"AMOUNT_MISMATCH ₹{diff / 100:.2f}")
        elif t == "DUPLICATE":
            parts.append(f"DUPLICATE ({d.get('duplicate_type', d.get('match_type', ''))})")
        elif t == "INVALID_GSTIN":
            parts.append(f"INVALID_GSTIN ({d.get('error', '')})")
        elif t == "GSTIN_MISMATCH":
            parts.append(f"GSTIN_MISMATCH")
        else:
            parts.append(t)
    return " · ".join(parts)


def _memories_summary(memories: list[str]) -> str:
    if not memories:
        return "—"
    n = len(memories)
    return f"{n} past decision{'s' if n > 1 else ''}"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("📤 Upload & Check")
st.caption("Upload a month's invoices and let the agent check them.")

# ---------------------------------------------------------------------------
# Upload form
# ---------------------------------------------------------------------------

with st.form("upload_form"):
    col1, col2 = st.columns(2)

    with col1:
        invoices_file = st.file_uploader(
            "Invoices CSV *",
            type=["csv"],
            help="Required. Must have columns: invoice_number, invoice_date, vendor_id, vendor_gstin, taxable_amount_paise, gst_rate_pct100, cgst_paise, sgst_paise, igst_paise, total_amount_paise",
        )
        period = st.text_input(
            "Period (YYYY-MM) *",
            placeholder="2026-07",
            help="The month this batch belongs to, e.g. 2026-07",
        )

    with col2:
        po_file = st.file_uploader(
            "Purchase Orders CSV (optional)",
            type=["csv"],
            help="Optional. Columns: po_number, amount_paise, gst_rate_pct100",
        )
        memory_enabled = st.toggle(
            "🧠 Memory ON — agent learns from decisions",
            value=True,
            help="When ON: agent recalls past decisions and can auto-approve familiar issues. When OFF: everything with issues gets flagged.",
        )
        if memory_enabled:
            st.success("Memory is **ON** — the agent will recall past decisions and auto-approve familiar patterns.")
        else:
            st.warning("Memory is **OFF** — all invoices with issues will be flagged for manual review.")

    submitted = st.form_submit_button("🚀 Upload & Check", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# Handle upload
# ---------------------------------------------------------------------------

if submitted:
    if not invoices_file:
        st.error("Please select an Invoices CSV file.")
        st.stop()
    if not period or len(period) != 7 or period[4] != "-":
        st.error("Period must be in YYYY-MM format, e.g. 2026-07.")
        st.stop()

    files = {"invoices_file": (invoices_file.name, invoices_file.getvalue(), "text/csv")}
    if po_file:
        files["po_file"] = (po_file.name, po_file.getvalue(), "text/csv")

    form_data = {
        "period": period,
        "memory_enabled": str(memory_enabled).lower(),
    }

    with st.spinner("Processing invoices… this may take a moment."):
        result = api_post("/api/batches", data=form_data, files=files, expect_status=201)

    if result:
        st.session_state.last_batch = result
        st.success(
            f"✅ Batch #{result['batch_id']} processed — "
            f"{result['total']} invoices: "
            f"{result['clean']} clean · "
            f"{result['auto_approved']} auto-approved · "
            f"{result['flagged']} flagged · "
            f"{result['blocked']} blocked"
        )
        if result.get("errors"):
            with st.expander(f"⚠️ {len(result['errors'])} rows skipped"):
                for e in result["errors"]:
                    st.text(e)

# ---------------------------------------------------------------------------
# Show results (from last_batch in session OR from current upload)
# ---------------------------------------------------------------------------

batch = st.session_state.get("last_batch")

if batch and batch.get("invoices"):
    invoices = batch["invoices"]
    st.divider()
    st.subheader(f"Results — Batch #{batch['batch_id']}  ·  Period: {batch['period']}")

    # Summary badges
    bc1, bc2, bc3, bc4, bc5 = st.columns(5)
    bc1.metric("Total",        batch["total"])
    bc2.metric("✅ Clean",      batch["clean"])
    bc3.metric("🟢 Auto-approved", batch["auto_approved"])
    bc4.metric("🟡 Flagged",   batch["flagged"])
    bc5.metric("🔴 Blocked",   batch["blocked"])

    # Filter controls
    st.markdown("**Filter results:**")
    fc1, fc2 = st.columns([1, 3])
    with fc1:
        status_options = ["All"] + [s for s in STATUS_ORDER if any(i["status"] == s for i in invoices)]
        filter_status = st.selectbox("Status", status_options)
    with fc2:
        vendor_names = sorted(set(i["vendor_name"] for i in invoices))
        filter_vendor = st.selectbox("Vendor", ["All vendors"] + vendor_names)

    filtered = invoices
    if filter_status != "All":
        filtered = [i for i in filtered if i["status"] == filter_status]
    if filter_vendor != "All vendors":
        filtered = [i for i in filtered if i["vendor_name"] == filter_vendor]

    # Results table
    rows = []
    for inv in filtered:
        rows.append({
            "Vendor":      inv["vendor_name"],
            "Invoice #":   inv["invoice_number"],
            "Date":        inv["invoice_date"],
            "Amount (₹)":  f"₹{inv['total_amount']:,.2f}",
            "Issues":      _format_issues(inv.get("issues", [])),
            "Decision":    STATUS_BADGE.get(inv["status"], inv["status"]),
            "Reason":      (inv.get("reason") or "")[:100],
            "Based on":    _memories_summary(inv.get("memories_used", [])),
        })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True, height=400)
    else:
        st.info("No invoices match the filter.")

    # ---------- Learning curve chart below the table ----------
    st.divider()
    st.subheader("📈 Learning Curve")

    with st.spinner("Loading learning curve…"):
        lc_data = api_get("/api/stats/learning-curve")

    if lc_data and lc_data.get("months"):
        months_lc = lc_data["months"]
        periods_lc = [m["period"] for m in months_lc]
        rates_lc   = [round(m["auto_handled_rate"] * 100, 1) for m in months_lc]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=periods_lc,
            y=rates_lc,
            name="Auto-handle Rate (%)",
            marker_color=["#22c55e" if m["memory_enabled"] else "#ef4444" for m in months_lc],
            text=[f"{r}%" for r in rates_lc],
            textposition="outside",
        ))
        fig.update_layout(
            xaxis_title="Month",
            yaxis_title="Auto-handle Rate (%)",
            yaxis=dict(range=[0, 110]),
            height=300,
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.2)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("🟩 Green = Memory ON · 🟥 Red = Memory OFF")
    else:
        st.info("Learning curve will appear after multiple batches are uploaded.")
