"""
LedgerMind — ui/pages/1_Dashboard.py
Dashboard: monthly stats, learning curve chart, recent activity feed.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from ui.helpers import api_get, show_sidebar

show_sidebar()

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("📊 Dashboard")
st.caption("Agent learning progress and recent activity.")

# ---------------------------------------------------------------------------
# Fetch learning curve data
# ---------------------------------------------------------------------------

with st.spinner("Loading stats…"):
    data = api_get("/api/stats/learning-curve")

if data is None:
    st.stop()

months = data.get("months", [])

# ---------------------------------------------------------------------------
# Top-level metric cards (most recent month)
# ---------------------------------------------------------------------------

if months:
    latest = months[-1]
    total   = latest["total_invoices"]
    issues  = latest["invoices_with_issues"]
    auto    = latest["auto_handled"]
    human   = latest["human_reviews"]
    rate    = latest["auto_handled_rate"]
    period  = latest["period"]

    st.subheader(f"This Month — {period}")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Invoices",       total)
    c2.metric("With Issues",          issues)
    c3.metric("Auto-handled",         auto,  help="AUTO_APPROVED by the agent")
    c4.metric("Need Human Review",    human)
    c5.metric("Auto-handle Rate",     f"{rate * 100:.1f}%")

    if len(months) >= 2:
        prev_rate = months[-2]["auto_handled_rate"]
        delta = rate - prev_rate
        c5.metric("Auto-handle Rate", f"{rate * 100:.1f}%",
                  delta=f"{delta * 100:+.1f}pp vs last month")
else:
    st.info("No batch data yet. Upload invoices to get started.")

st.divider()

# ---------------------------------------------------------------------------
# Learning curve chart
# ---------------------------------------------------------------------------

st.subheader("📈 Learning Curve — Auto-handle Rate Over Time")

if len(months) == 0:
    st.info("Upload at least one month of invoices to see the chart.")
else:
    periods          = [m["period"] for m in months]
    auto_rates       = [round(m["auto_handled_rate"] * 100, 1) for m in months]
    totals           = [m["total_invoices"] for m in months]
    issues_list      = [m["invoices_with_issues"] for m in months]
    human_reviews    = [m["human_reviews"] for m in months]
    overturn_rates   = [round(m["overturn_rate"] * 100, 1) for m in months]
    memory_flags     = [m["memory_enabled"] for m in months]

    fig = go.Figure()

    # Auto-handle rate line
    fig.add_trace(go.Scatter(
        x=periods,
        y=auto_rates,
        mode="lines+markers+text",
        name="Auto-handle Rate (%)",
        line=dict(color="#22c55e", width=3),
        marker=dict(size=10),
        text=[f"{r}%" for r in auto_rates],
        textposition="top center",
    ))

    # Memory OFF reference line at 0%
    fig.add_trace(go.Scatter(
        x=periods,
        y=[0] * len(periods),
        mode="lines",
        name="Memory OFF (baseline)",
        line=dict(color="#ef4444", width=2, dash="dash"),
    ))

    # Overturn rate
    fig.add_trace(go.Scatter(
        x=periods,
        y=overturn_rates,
        mode="lines+markers",
        name="Overturn Rate (%)",
        line=dict(color="#f59e0b", width=2),
        marker=dict(size=8, symbol="diamond"),
        yaxis="y",
    ))

    fig.update_layout(
        xaxis_title="Month",
        yaxis_title="Rate (%)",
        yaxis=dict(range=[0, 105]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=400,
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,0.2)")

    st.plotly_chart(fig, use_container_width=True)

    # Monthly breakdown table
    with st.expander("📋 Monthly Breakdown Table"):
        df = pd.DataFrame({
            "Period":          periods,
            "Total Invoices":  totals,
            "With Issues":     issues_list,
            "Auto-handled":    [m["auto_handled"] for m in months],
            "Auto Rate":       [f"{r}%" for r in auto_rates],
            "Human Reviews":   human_reviews,
            "Overturned":      [m["overturned"] for m in months],
            "Overturn Rate":   [f"{r}%" for r in overturn_rates],
            "Blocked":         [m["blocked"] for m in months],
            "Memory":          ["✅ ON" if f else "❌ OFF" for f in memory_flags],
        })
        st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Recent activity — last 20 invoices across all statuses
# ---------------------------------------------------------------------------

st.subheader("🕐 Recent Activity")

with st.spinner("Loading recent invoices…"):
    recent = api_get("/api/invoices", params={"page": 1, "page_size": 20})

if recent:
    status_badge = {
        "CLEAN":         "✅ Clean",
        "AUTO_APPROVED": "🟢 Auto-approved",
        "FLAGGED":       "🟡 Flagged",
        "BLOCKED":       "🔴 Blocked",
        "APPROVED":      "✅ Approved",
        "REJECTED":      "❌ Rejected",
        "ON_HOLD":       "⏸ On Hold",
        "OVERTURNED":    "↩ Overturned",
    }

    rows = []
    for inv in recent:
        rows.append({
            "Invoice #":   inv["invoice_number"],
            "Vendor":      inv["vendor_name"],
            "Date":        inv["invoice_date"],
            "Amount (₹)":  f"₹{inv['total_amount']:,.2f}",
            "Issues":      inv["issue_count"],
            "Status":      status_badge.get(inv["status"], inv["status"]),
            "Reason":      (inv.get("reason") or "")[:80],
        })

    df_recent = pd.DataFrame(rows)
    st.dataframe(df_recent, use_container_width=True, hide_index=True)
else:
    st.info("No invoices yet.")

# ---------------------------------------------------------------------------
# Memory status indicator
# ---------------------------------------------------------------------------

st.divider()
last_batch = st.session_state.get("last_batch")
if last_batch:
    mem = last_batch.get("memory_enabled", True)
    if mem:
        st.success("🧠 Memory is **ON** for the last upload — agent is learning.")
    else:
        st.warning("🔕 Memory is **OFF** for the last upload — agent will not learn.")
else:
    st.caption("Memory status will appear after your first upload.")
