"""
LedgerMind — ui/pages/4_Vendor_Memory.py
Vendor memory: reflect() profile, past decisions table, add-a-fact form.
"""

import streamlit as st
import pandas as pd
from ui.app import api_get, api_post, _show_sidebar

_show_sidebar()

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("🧠 Vendor Memory")
st.caption("See what the agent has learned about each vendor, and teach it new facts.")

# ---------------------------------------------------------------------------
# Vendor selector
# ---------------------------------------------------------------------------

with st.spinner("Loading vendors…"):
    vendors_data = api_get("/api/vendors")

if not vendors_data:
    st.info("No vendors found. Upload invoices first, or add vendors in the Admin page.")
    st.stop()

vendor_map = {v["name"]: v for v in vendors_data}
vendor_names = sorted(vendor_map.keys())

selected_name = st.selectbox("Select a vendor", vendor_names)
vendor = vendor_map[selected_name]
vendor_id = vendor["vendor_id"]

st.markdown(f"**GSTIN:** `{vendor.get('gstin') or 'Not set'}` · **State:** `{vendor.get('state_code') or 'Not set'}`")
st.divider()

# ---------------------------------------------------------------------------
# Vendor memory profile (reflect)
# ---------------------------------------------------------------------------

col_mem, col_facts = st.columns([3, 2])

with col_mem:
    st.subheader("🤖 What the Agent Knows")

    with st.spinner(f"Reflecting on {selected_name}…"):
        memory_data = api_get(f"/api/vendors/{vendor_id}/memory")

    if memory_data:
        profile_text = memory_data.get("profile", "")
        based_on     = memory_data.get("based_on", [])

        if profile_text and profile_text.strip() not in ("None", "", "[]"):
            st.info(profile_text)
        else:
            st.warning(
                f"No memory yet for **{selected_name}**. "
                "Upload and review invoices to build up the agent's knowledge."
            )

        if based_on:
            with st.expander(f"📚 Based on {len(based_on)} observation(s)"):
                for obs in based_on:
                    st.markdown(f"- {obs}")
    else:
        st.warning("Could not load memory profile.")

# ---------------------------------------------------------------------------
# Past decisions table
# ---------------------------------------------------------------------------

st.divider()
st.subheader(f"📋 Past Decisions for {selected_name}")

with st.spinner("Loading invoice history…"):
    all_invoices = api_get("/api/invoices", params={"page_size": 200})

if all_invoices:
    vendor_invoices = [i for i in all_invoices if i["vendor_name"] == selected_name]

    if vendor_invoices:
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

        rows = []
        for inv in vendor_invoices:
            rows.append({
                "Invoice #":  inv["invoice_number"],
                "Date":       inv["invoice_date"],
                "Amount (₹)": f"₹{inv['total_amount']:,.2f}",
                "Issues":     inv["issue_count"],
                "Status":     STATUS_BADGE.get(inv["status"], inv["status"]),
                "Reason":     (inv.get("reason") or "")[:80],
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Quick stats
        total = len(vendor_invoices)
        auto  = sum(1 for i in vendor_invoices if i["status"] == "AUTO_APPROVED")
        approved = sum(1 for i in vendor_invoices if i["status"] == "APPROVED")
        rejected = sum(1 for i in vendor_invoices if i["status"] == "REJECTED")
        blocked  = sum(1 for i in vendor_invoices if i["status"] == "BLOCKED")

        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Total invoices", total)
        s2.metric("🟢 Auto-approved", auto)
        s3.metric("✅ Approved", approved)
        s4.metric("❌ Rejected", rejected)
        s5.metric("🔴 Blocked", blocked)
    else:
        st.info(f"No invoices found for {selected_name} yet.")
else:
    st.info("No invoices in the system yet.")

# ---------------------------------------------------------------------------
# Add a vendor fact
# ---------------------------------------------------------------------------

with col_facts:
    st.subheader("✏️ Tell the Agent a Fact")
    st.markdown(
        "Teach the agent something about this vendor that it can't learn from invoices alone. "
        "This is retained directly to memory."
    )

    with st.form(f"fact_form_{vendor_id}"):
        fact = st.text_area(
            "Fact",
            height=120,
            placeholder=(
                "e.g. This vendor always rounds up totals by ₹1–₹3 due to their billing system. "
                "This is expected and should be auto-approved."
            ),
        )
        submitted = st.form_submit_button("💾 Save Fact to Memory", use_container_width=True, type="primary")

    if submitted:
        if not fact.strip():
            st.error("Please enter a fact before submitting.")
        else:
            with st.spinner("Saving to memory…"):
                result = api_post(f"/api/vendors/{vendor_id}/facts", {"fact": fact.strip()}, expect_status=201)
            if result:
                st.success(f"✅ Fact saved to memory for **{selected_name}**.")
                st.info(f"💡 *\"{fact.strip()}\"*")

    st.divider()
    st.markdown("**💡 Example facts you can teach:**")
    examples = [
        "This vendor always rounds up by ₹1–₹3 (billing system quirk). Auto-approve.",
        "This vendor changed their GSTIN in July 2026. The new one is correct.",
        "Credit notes from this vendor always arrive 2–3 weeks late. Put on hold, don't reject.",
        "Tax rate changed from 12% to 18% starting August 2026. This is intentional.",
        "This vendor sometimes sends duplicate invoices by mistake. Always flag duplicates.",
    ]
    for ex in examples:
        if st.button(f"📋 {ex[:60]}…" if len(ex) > 60 else f"📋 {ex}", key=f"ex_{ex[:20]}"):
            st.session_state[f"prefill_fact_{vendor_id}"] = ex
            st.rerun()
