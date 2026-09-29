"""
LedgerMind — ui/pages/5_Admin.py
Admin-only page: manage vendors and update safety/agent settings.
"""

import streamlit as st
import pandas as pd
from ui.helpers import api_get, api_post, api_patch, is_admin, show_sidebar

show_sidebar()

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("⚙️ Admin")
st.caption("Manage vendors and configure agent safety limits. Admin access only.")

tab_vendors, tab_settings = st.tabs(["🏢 Vendors", "🔧 Settings"])

# ============================================================
# TAB 1 — VENDORS
# ============================================================

with tab_vendors:
    st.subheader("🏢 Vendor Management")

    # ---- Existing vendors ----
    with st.spinner("Loading vendors…"):
        vendors = api_get("/api/vendors")

    if vendors:
        rows = [
            {
                "ID":          v["vendor_id"],
                "Name":        v["name"],
                "GSTIN":       v.get("gstin") or "—",
                "State Code":  v.get("state_code") or "—",
            }
            for v in vendors
        ]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No vendors found.")

    st.divider()

    # ---- Edit existing vendor ----
    if vendors:
        st.subheader("✏️ Edit Vendor")
        vendor_map = {f"{v['name']} (ID {v['vendor_id']})": v for v in vendors}
        selected_label = st.selectbox("Select vendor to edit", list(vendor_map.keys()), key="edit_vendor_select")
        selected = vendor_map[selected_label]

        with st.form("edit_vendor_form"):
            new_name       = st.text_input("Name",       value=selected["name"])
            new_gstin      = st.text_input("GSTIN",      value=selected.get("gstin") or "")
            new_state_code = st.text_input("State Code", value=selected.get("state_code") or "",
                                           help="2-digit state code, e.g. 27 for Maharashtra")
            edit_submitted = st.form_submit_button("💾 Save Changes", type="primary")

        if edit_submitted:
            payload = {}
            if new_name.strip()       != selected["name"]:
                payload["name"] = new_name.strip()
            if new_gstin.strip()      != (selected.get("gstin") or ""):
                payload["gstin"] = new_gstin.strip() or None
            if new_state_code.strip() != (selected.get("state_code") or ""):
                payload["state_code"] = new_state_code.strip() or None

            if not payload:
                st.info("No changes detected.")
            else:
                with st.spinner("Updating vendor…"):
                    result = api_patch(f"/api/vendors/{selected['vendor_id']}", payload)
                if result:
                    st.success(f"✅ Vendor **{result['name']}** updated.")
                    if "gstin" in payload:
                        st.info("🧠 GSTIN change has been retained to memory automatically.")
                    st.rerun()

    st.divider()

    # ---- Add new vendor ----
    st.subheader("➕ Add New Vendor")
    with st.form("add_vendor_form"):
        add_name       = st.text_input("Vendor Name *",  placeholder="Sharma Traders")
        add_gstin      = st.text_input("GSTIN",          placeholder="27AABCS1429B1ZB")
        add_state_code = st.text_input("State Code",     placeholder="27",
                                       help="2-digit state code, e.g. 27 for Maharashtra")
        add_submitted  = st.form_submit_button("➕ Add Vendor", type="primary")

    if add_submitted:
        if not add_name.strip():
            st.error("Vendor name is required.")
        else:
            payload = {
                "name": add_name.strip(),
                "gstin": add_gstin.strip() or None,
                "state_code": add_state_code.strip() or None,
            }
            with st.spinner("Creating vendor…"):
                result = api_post("/api/vendors", payload, expect_status=201)
            if result:
                st.success(f"✅ Vendor **{result['name']}** created with ID {result['vendor_id']}.")
                st.rerun()

# ============================================================
# TAB 2 — SETTINGS
# ============================================================

with tab_settings:
    st.subheader("🔧 Agent Safety Settings")
    st.markdown(
        "These thresholds control when the agent escalates to human review. "
        "Changes take effect immediately (in-process; restart the server to persist across restarts)."
    )

    with st.spinner("Loading settings…"):
        settings = api_get("/api/settings")

    if not settings:
        st.error("Could not load settings.")
        st.stop()

    st.divider()

    with st.form("settings_form"):
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**Rule Thresholds**")

            rounding_limit = st.number_input(
                "Rounding Limit (₹)",
                min_value=1,
                max_value=1000,
                value=settings["ROUNDING_LIMIT"],
                help="Max rounding difference (₹) before it becomes an AMOUNT_MISMATCH. Default: ₹10",
            )
            max_amount = st.number_input(
                "Max Auto-approve Amount (₹)",
                min_value=1000,
                max_value=10_000_000,
                value=settings["MAX_AUTO_APPROVE_AMOUNT"],
                step=10000,
                help="Invoices above this amount always go to human review. Default: ₹5,00,000",
            )
            max_diff = st.number_input(
                "Max Auto-approve Diff (₹)",
                min_value=1,
                max_value=100_000,
                value=settings["MAX_AUTO_APPROVE_DIFF"],
                help="Amount differences above this (₹) always go to human review. Default: ₹100",
            )

        with col_b:
            st.markdown("**AI Confidence Thresholds**")

            min_evidence = st.number_input(
                "Min Evidence (past approvals needed)",
                min_value=1,
                max_value=20,
                value=settings["MIN_EVIDENCE"],
                help="Minimum past approvals of an issue type before the agent can auto-approve. Default: 2",
            )
            min_confidence = st.slider(
                "Min Confidence",
                min_value=0.5,
                max_value=1.0,
                value=float(settings["MIN_CONFIDENCE"]),
                step=0.05,
                help="Minimum AI confidence score to auto-approve. Default: 0.8",
            )

            st.divider()
            st.markdown("**LLM Config** *(read-only)*")
            st.text_input("Model",       value=settings["LLM_MODEL"],       disabled=True)
            st.text_input("Max Retries", value=str(settings["LLM_MAX_RETRIES"]), disabled=True)

        save_settings = st.form_submit_button("💾 Save Settings", use_container_width=True, type="primary")

    if save_settings:
        payload = {
            "ROUNDING_LIMIT":           int(rounding_limit),
            "MIN_EVIDENCE":             int(min_evidence),
            "MIN_CONFIDENCE":           float(min_confidence),
            "MAX_AUTO_APPROVE_AMOUNT":  int(max_amount),
            "MAX_AUTO_APPROVE_DIFF":    int(max_diff),
        }
        with st.spinner("Saving settings…"):
            result = api_patch("/api/settings", payload)
        if result:
            st.success("✅ Settings updated.")
            st.markdown("**Updated values:**")
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            sc1.metric("Rounding Limit",    f"₹{result['ROUNDING_LIMIT']}")
            sc2.metric("Max Amount",        f"₹{result['MAX_AUTO_APPROVE_AMOUNT']:,}")
            sc3.metric("Max Diff",          f"₹{result['MAX_AUTO_APPROVE_DIFF']}")
            sc4.metric("Min Evidence",      result["MIN_EVIDENCE"])
            sc5.metric("Min Confidence",    f"{result['MIN_CONFIDENCE'] * 100:.0f}%")

    st.divider()
    st.markdown("**⚠️ Note:** Changes to `LLM_MODEL` and `LLM_MAX_RETRIES` require editing the `.env` file and restarting the server.")
