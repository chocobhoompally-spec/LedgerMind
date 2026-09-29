"""
LedgerMind — ui/app.py
Streamlit entry point. Handles login and role-based navigation.

Page files import shared helpers from ui/helpers.py, NOT from this file,
so there is no circular import and no st.navigation re-entry.

Run with:
    streamlit run ui/app.py
"""

import os
import sys
import pathlib

# Ensure the project root (parent of ui/) is on sys.path so that
# 'from ui.helpers import ...' works regardless of how Streamlit sets
# sys.path[0] (it inserts the entry-script's directory, not the project root).
_project_root = str(pathlib.Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import httpx
import streamlit as st

# set_page_config must be the very first Streamlit call in the entry-point script.
st.set_page_config(
    page_title="LedgerMind",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "token":      None,
        "user":       None,
        "company":    None,
        "last_batch": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()

# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------

def _login_page():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## 🧾 LedgerMind")
        st.markdown("*A GST Invoice Agent That Learns Your Accountant's Judgment*")
        st.divider()
        with st.form("login_form"):
            email    = st.text_input("Email", placeholder="accountant@company.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)

        if submitted:
            if not email or not password:
                st.error("Please enter both email and password.")
            else:
                with st.spinner("Signing in…"):
                    try:
                        result = httpx.post(
                            f"{API_BASE}/api/auth/login",
                            json={"email": email, "password": password},
                            timeout=15,
                        )
                    except httpx.ConnectError:
                        st.error(f"⚠️ Cannot reach the API server at `{API_BASE}`. Is it running?")
                        return
                if result.status_code == 200:
                    data = result.json()
                    st.session_state.token = data["token"]
                    st.session_state.user  = {
                        "user_id": data["user_id"],
                        "name":    data["name"],
                        "email":   data["email"],
                        "role":    data["role"],
                    }
                    st.rerun()
                elif result.status_code == 401:
                    st.error("Incorrect email or password.")
                else:
                    try:
                        err = result.json().get("error", {})
                        st.error(f"Login failed: {err.get('message', result.text)}")
                    except Exception:
                        st.error(f"Login failed (status {result.status_code}).")

# ---------------------------------------------------------------------------
# Landing page (home)
# ---------------------------------------------------------------------------

def _home_page():
    from ui.helpers import show_sidebar
    show_sidebar()
    st.markdown("## 🧾 LedgerMind")
    st.info("👈 Use the sidebar to navigate to a page.")
    st.markdown("""
| Page | What it does |
|---|---|
| 📊 Dashboard | Learning curve chart, monthly stats, recent activity |
| 📤 Upload & Check | Upload invoices CSV, see AI decisions |
| 🔍 Review Queue | Approve / Reject / Hold flagged invoices |
| 🧠 Vendor Memory | View what the agent learned about each vendor |
| ⚙️ Admin | Manage vendors and safety settings *(admin only)* |
""")

# ---------------------------------------------------------------------------
# Navigation — role-based
# ---------------------------------------------------------------------------

_pages_dir = pathlib.Path(__file__).resolve().parent / "pages"

if not st.session_state.get("token"):
    pg = st.navigation(
        [st.Page(_login_page, title="Login", icon="🔐", default=True)],
        position="hidden",
    )
else:
    role = (st.session_state.get("user") or {}).get("role", "")

    common_pages = [
        st.Page(_home_page,                                   title="Home",           icon="🏠", default=True),
        st.Page(str(_pages_dir / "1_Dashboard.py"),           title="Dashboard",      icon="📊"),
        st.Page(str(_pages_dir / "2_Upload_and_Check.py"),    title="Upload & Check", icon="📤"),
        st.Page(str(_pages_dir / "3_Review_Queue.py"),        title="Review Queue",   icon="🔍"),
        st.Page(str(_pages_dir / "4_Vendor_Memory.py"),       title="Vendor Memory",  icon="🧠"),
    ]
    admin_pages = (
        [st.Page(str(_pages_dir / "5_Admin.py"),              title="Admin",          icon="⚙️")]
        if role == "ADMIN" else []
    )
    pg = st.navigation(common_pages + admin_pages)

pg.run()
