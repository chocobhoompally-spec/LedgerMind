"""
LedgerMind — ui/pages/3_Review_Queue.py
Review queue: approve / reject / hold flagged invoices; overturn auto-approvals.
"""

import streamlit as st
import pandas as pd
from ui.app import api_get, api_post, _show_sidebar

_show_sidebar()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STATUS_BADGE = {
    "FLAGGED": "🟡 Flagged",
    "BLOCKED": "🔴 Blocked",
    "AUTO_APPROVED": "🟢 Auto-approved",
    "ON_HOLD": "⏸ On Hold",
}

ISSUE_LABELS = {
    "ROUNDING": "Rounding diff",
    "DUPLICATE": "Duplicate",
    "INVALID_GSTIN": "Invalid GSTIN",
    "GSTIN_MISMATCH": "GSTIN mismatch",
    "TAX_RATE_MISMATCH": "Tax rate mismatch",
    "AMOUNT_MISMATCH": "Amount mismatch",
    "TAX_TYPE_MISMATCH": "Tax type mismatch",
    "MISSING_PO": "Missing PO",
    "PENDING_CREDIT_NOTE": "Pending credit note",
}


def _fmt_issues(issues: list[dict]) -> str:
    if not issues:
        return "No issues"
    parts = []
    for iss in issues:
        t = iss.get("type", "")
        d = iss.get("details", {})
        label = ISSUE_LABELS.get(t, t)
        if t == "ROUNDING":
            diff = d.get("difference_paise", 0)
            parts.append(f"{label}: ₹{diff / 100:.2f}")
        elif t == "TAX_RATE_MISMATCH":
            inv_r = d.get("invoice_rate_pct100", 0)
            po_r  = d.get("po_rate_pct100", 0)
            parts.append(f"{label}: {inv_r / 100:.0f}% → {po_r / 100:.0f}%")
        elif t == "AMOUNT_MISMATCH":
            diff = d.get("difference_paise", 0)
            parts.append(f"{label}: ₹{diff / 100:.2f}")
        else:
            parts.append(label)
    return " · ".join(parts)


def _do_review(invoice_id: int, action: str, note: str) -> bool:
    result = api_post(f"/api/invoices/{invoice_id}/review", {"action": action, "note": note})
    return result is not None


def _do_overturn(invoice_id: int, note: str) -> bool:
    result = api_post(f"/api/invoices/{invoice_id}/overturn", {"note": note})
    return result is not None


# ---------------------------------------------------------------------------
# Fetch invoices
# ---------------------------------------------------------------------------

def _load_invoices(statuses: list[str]) -> list[dict]:
    all_invoices = []
    for s in statuses:
        data = api_get("/api/invoices", params={"status": s, "page_size": 200})
        if data:
            all_invoices.extend(data)
    return all_invoices


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("🔍 Review Queue")
st.caption("Review flagged and blocked invoices. Approve, reject, or hold each one.")

# ---------------------------------------------------------------------------
# Filter bar
# ---------------------------------------------------------------------------

col_f1, col_f2, col_f3 = st.columns([1, 1, 2])
with col_f1:
    show_flagged  = st.checkbox("🟡 Flagged",       value=True)
    show_blocked  = st.checkbox("🔴 Blocked",       value=True)
with col_f2:
    show_on_hold  = st.checkbox("⏸ On Hold",        value=True)
    show_auto_app = st.checkbox("🟢 Auto-approved", value=False,
                                help="Show auto-approved invoices so you can overturn them")

if st.button("🔄 Refresh Queue"):
    st.rerun()

# Build status list to fetch
statuses_to_fetch = []
if show_flagged:  statuses_to_fetch.append("FLAGGED")
if show_blocked:  statuses_to_fetch.append("BLOCKED")
if show_on_hold:  statuses_to_fetch.append("ON_HOLD")
if show_auto_app: statuses_to_fetch.append("AUTO_APPROVED")

if not statuses_to_fetch:
    st.info("Select at least one status filter above.")
    st.stop()

with st.spinner("Loading queue…"):
    invoices = _load_invoices(statuses_to_fetch)

if not invoices:
    st.success("🎉 Queue is empty! No invoices need your attention right now.")
    st.stop()

st.markdown(f"**{len(invoices)} invoice(s) in queue**")

# ---------------------------------------------------------------------------
# Vendor filter
# ---------------------------------------------------------------------------

vendor_names = sorted(set(i["vendor_name"] for i in invoices))
filter_vendor = st.selectbox("Filter by vendor", ["All vendors"] + vendor_names)
if filter_vendor != "All vendors":
    invoices = [i for i in invoices if i["vendor_name"] == filter_vendor]

st.divider()

# ---------------------------------------------------------------------------
# Render each invoice card
# ---------------------------------------------------------------------------

for inv in invoices:
    inv_id     = inv["invoice_id"]
    status     = inv["status"]
    badge      = STATUS_BADGE.get(status, status)
    is_blocked = status == "BLOCKED"
    is_auto    = status == "AUTO_APPROVED"

    with st.container(border=True):
        # Header row
        hc1, hc2, hc3, hc4 = st.columns([3, 2, 2, 1])
        hc1.markdown(f"**{inv['vendor_name']}** · `{inv['invoice_number']}`")
        hc2.markdown(f"📅 {inv['invoice_date']}")
        hc3.markdown(f"💰 ₹{inv['total_amount']:,.2f}")
        hc4.markdown(badge)

        # Agent's reason
        reason = inv.get("reason") or ""
        if reason:
            st.markdown(f"🤖 **Agent:** {reason}")

        # Fetch full detail for issues + memories
        detail = api_get(f"/api/invoices/{inv_id}")
        if detail:
            issues = detail.get("issues", [])
            memories = []
            agent_dec = detail.get("agent_decision")
            if agent_dec:
                memories = agent_dec.get("memories_used", [])
                confidence = agent_dec.get("confidence")

            if issues:
                st.markdown(f"⚠️ **Issues:** {_fmt_issues(issues)}")
            if memories:
                st.markdown(f"🧠 **Based on:** {len(memories)} past decision(s) — `{'`, `'.join(memories[:3])}`{'…' if len(memories) > 3 else ''}")
            if agent_dec and agent_dec.get("confidence") is not None:
                conf = agent_dec["confidence"]
                st.progress(min(conf, 1.0), text=f"Confidence: {conf * 100:.0f}%")

        # Past reviews
        if detail and detail.get("reviews"):
            with st.expander("📜 Review History"):
                for rev in detail["reviews"]:
                    st.markdown(f"- **{rev['action']}** — {rev.get('note', '')} _(retained: {'✅' if rev['retained_to_memory'] else '❌'})_")

        # Action section
        st.markdown("---")

        if is_auto:
            # Overturn flow
            with st.form(key=f"overturn_{inv_id}"):
                note = st.text_area(
                    "Note (required — explain why this approval was wrong)",
                    key=f"note_ov_{inv_id}",
                    height=80,
                    placeholder="e.g. This vendor usually doesn't round up this much — please investigate.",
                )
                if st.form_submit_button("↩ Overturn Auto-approval", type="primary"):
                    if not note.strip():
                        st.error("A note is required when overturning.")
                    else:
                        with st.spinner("Overturning…"):
                            ok = _do_overturn(inv_id, note.strip())
                        if ok:
                            st.success(f"↩ Invoice {inv['invoice_number']} overturned and sent back to review.")
                            st.rerun()

        elif not is_blocked:
            # Approve / Reject / Hold flow for FLAGGED and ON_HOLD
            with st.form(key=f"review_{inv_id}"):
                note = st.text_area(
                    "Note (required — your reasoning helps the agent learn)",
                    key=f"note_{inv_id}",
                    height=80,
                    placeholder="e.g. Sharma Traders always rounds up by ₹1–₹3, this is expected.",
                )
                rc1, rc2, rc3 = st.columns(3)
                approve = rc1.form_submit_button("✅ Approve", use_container_width=True, type="primary")
                reject  = rc2.form_submit_button("❌ Reject",  use_container_width=True)
                hold    = rc3.form_submit_button("⏸ Hold",    use_container_width=True)

                action = None
                if approve: action = "APPROVE"
                if reject:  action = "REJECT"
                if hold:    action = "HOLD"

                if action:
                    if not note.strip():
                        st.error("A note is required. Your reasoning is retained to memory.")
                    else:
                        with st.spinner(f"{action.title()}ing…"):
                            ok = _do_review(inv_id, action, note.strip())
                        if ok:
                            st.success(f"✅ Invoice {inv['invoice_number']} {action.lower()}d and retained to memory.")
                            st.rerun()

        else:
            # BLOCKED — can only reject or hold
            st.error("🔴 This invoice is **blocked** by a safety rule and cannot be auto-approved.")
            with st.form(key=f"review_blocked_{inv_id}"):
                note = st.text_area(
                    "Note (required)",
                    key=f"note_bl_{inv_id}",
                    height=80,
                    placeholder="e.g. GSTIN is wrong, vendor needs to reissue the invoice.",
                )
                bc1, bc2 = st.columns(2)
                reject  = bc1.form_submit_button("❌ Reject",  use_container_width=True, type="primary")
                hold    = bc2.form_submit_button("⏸ Hold",    use_container_width=True)

                action = None
                if reject: action = "REJECT"
                if hold:   action = "HOLD"

                if action:
                    if not note.strip():
                        st.error("A note is required.")
                    else:
                        with st.spinner(f"{action.title()}ing…"):
                            ok = _do_review(inv_id, action, note.strip())
                        if ok:
                            st.success(f"Invoice {inv['invoice_number']} {action.lower()}d.")
                            st.rerun()

    st.write("")  # spacing between cards
