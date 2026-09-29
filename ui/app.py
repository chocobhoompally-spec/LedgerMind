"""
LedgerMind — ui/app.py
Main Streamlit entry point.

Handles:
  - Session state initialisation
  - Login form (POST /api/auth/login)
  - Sidebar navigation
  - Shared API helpers used by every page

Run with:
    streamlit run ui/app.py
"""

import os
import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Page config — only set when app.py itself is the entry point (not when
# imported by a page). Each page file sets its own page config.
# ---------------------------------------------------------------------------

if __name__ == "__main__" or not hasattr(st, "_is_running_with_streamlit"):
    try:
        st.set_page_config(
            page_title="LedgerMind",
            page_icon="🧾",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except Exception:
        pass  # already set by the page file that imported us

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "token": None,
        "user": None,          # dict from GET /api/auth/me
        "company": None,
        "last_batch": None,    # most recent BatchResponse dict
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    token = st.session_state.get("token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def api_get(endpoint: str, params: dict | None = None) -> dict | list | None:
    """
    GET {API_BASE}{endpoint}.
    Returns parsed JSON or None on error (error shown via st.error).
    """
    try:
        r = httpx.get(f"{API_BASE}{endpoint}", headers=_headers(), params=params, timeout=30)
        if r.status_code == 401:
            st.session_state.token = None
            st.session_state.user = None
            st.error("Session expired. Please log in again.")
            st.rerun()
        if not r.is_success:
            _show_api_error(r)
            return None
        return r.json()
    except httpx.ConnectError:
        st.error(f"⚠️ Cannot reach the API server at `{API_BASE}`. Is it running?")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


def api_post(endpoint: str, data: dict | None = None, files=None, expect_status: int = 200) -> dict | None:
    """
    POST {API_BASE}{endpoint}.
    Pass `files` for multipart uploads, `data` for JSON body.
    Returns parsed JSON or None on error.
    """
    try:
        if files is not None:
            r = httpx.post(
                f"{API_BASE}{endpoint}",
                headers=_headers(),
                data=data or {},
                files=files,
                timeout=120,
            )
        else:
            r = httpx.post(
                f"{API_BASE}{endpoint}",
                headers=_headers(),
                json=data or {},
                timeout=60,
            )
        if r.status_code == 401:
            st.session_state.token = None
            st.session_state.user = None
            st.error("Session expired. Please log in again.")
            st.rerun()
        if r.status_code not in (200, 201, expect_status):
            _show_api_error(r)
            return None
        return r.json()
    except httpx.ConnectError:
        st.error(f"⚠️ Cannot reach the API server at `{API_BASE}`. Is it running?")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


def api_patch(endpoint: str, data: dict) -> dict | None:
    """PATCH {API_BASE}{endpoint} with JSON body."""
    try:
        r = httpx.patch(
            f"{API_BASE}{endpoint}",
            headers=_headers(),
            json=data,
            timeout=30,
        )
        if r.status_code == 401:
            st.session_state.token = None
            st.session_state.user = None
            st.error("Session expired. Please log in again.")
            st.rerun()
        if not r.is_success:
            _show_api_error(r)
            return None
        return r.json()
    except httpx.ConnectError:
        st.error(f"⚠️ Cannot reach the API server at `{API_BASE}`. Is it running?")
        return None
    except Exception as e:
        st.error(f"Request failed: {e}")
        return None


def _show_api_error(r: httpx.Response):
    try:
        body = r.json()
        err = body.get("error", body)
        code = err.get("code", r.status_code)
        msg = err.get("message", str(body))
        st.error(f"API error [{code}]: {msg}")
    except Exception:
        st.error(f"API returned status {r.status_code}: {r.text[:300]}")


def is_admin() -> bool:
    user = st.session_state.get("user")
    return user is not None and user.get("role") == "ADMIN"

# ---------------------------------------------------------------------------
# Login form
# ---------------------------------------------------------------------------

def _show_login():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("## 🧾 LedgerMind")
        st.markdown("*A GST Invoice Agent That Learns Your Accountant's Judgment*")
        st.divider()
        with st.form("login_form"):
            email = st.text_input("Email", placeholder="accountant@company.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)

        if submitted:
            if not email or not password:
                st.error("Please enter both email and password.")
            else:
                with st.spinner("Signing in…"):
                    result = httpx.post(
                        f"{API_BASE}/api/auth/login",
                        json={"email": email, "password": password},
                        timeout=15,
                    )
                if result.status_code == 200:
                    data = result.json()
                    st.session_state.token = data["token"]
                    st.session_state.user = {
                        "user_id": data["user_id"],
                        "name": data["name"],
                        "email": data["email"],
                        "role": data["role"],
                    }
                    st.success(f"Welcome, {data['name']}!")
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
# Sidebar
# ---------------------------------------------------------------------------

def _show_sidebar():
    with st.sidebar:
        st.markdown("### 🧾 LedgerMind")
        user = st.session_state.get("user", {})
        st.markdown(f"**{user.get('name', '')}** · {user.get('role', '').title()}")
        st.divider()

        st.page_link("ui/pages/1_Dashboard.py",        label="📊 Dashboard",        icon="📊")
        st.page_link("ui/pages/2_Upload_and_Check.py", label="📤 Upload & Check",   icon="📤")
        st.page_link("ui/pages/3_Review_Queue.py",     label="🔍 Review Queue",     icon="🔍")
        st.page_link("ui/pages/4_Vendor_Memory.py",    label="🧠 Vendor Memory",    icon="🧠")

        if is_admin():
            st.page_link("ui/pages/5_Admin.py", label="⚙️ Admin", icon="⚙️")

        st.divider()
        if st.button("Sign Out", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if not st.session_state.get("token"):
    _show_login()
else:
    _show_sidebar()
    # Landing: redirect hint
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
