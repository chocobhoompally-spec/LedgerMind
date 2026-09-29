"""
LedgerMind — ui/app.py
Main Streamlit entry point.

Uses st.navigation() so that:
  - The Admin page is only visible to ADMIN users (not shown in nav at all for accountants)
  - set_page_config is called once here, not in every page file
  - Login is handled before any page is rendered

Run with:
    streamlit run ui/app.py
"""

import os
import httpx
import streamlit as st

# Must be the very first Streamlit call.
# Wrapped in try/except because when page files (run via st.navigation) import
# this module, set_page_config will already have been called by app.py itself.
try:
    st.set_page_config(
        page_title="LedgerMind",
        page_icon="🧾",
        layout="wide",
        initial_sidebar_state="expanded",
    )
except st.errors.StreamlitAPIException:
    pass  # already set — safe to ignore

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "token": None,
        "user": None,
        "company": None,
        "last_batch": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()

# ---------------------------------------------------------------------------
# API helpers (imported by page files via: from ui.app import ...)
# ---------------------------------------------------------------------------

def _headers() -> dict:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def api_get(endpoint: str, params: dict | None = None) -> dict | list | None:
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
    try:
        if files is not None:
            r = httpx.post(
                f"{API_BASE}{endpoint}", headers=_headers(),
                data=data or {}, files=files, timeout=120,
            )
        else:
            r = httpx.post(
                f"{API_BASE}{endpoint}", headers=_headers(),
                json=data or {}, timeout=60,
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
    try:
        r = httpx.patch(
            f"{API_BASE}{endpoint}", headers=_headers(), json=data, timeout=30,
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
        st.error(f"API error [{err.get('code', r.status_code)}]: {err.get('message', str(body))}")
    except Exception:
        st.error(f"API returned status {r.status_code}: {r.text[:300]}")


def is_admin() -> bool:
    user = st.session_state.get("user")
    return user is not None and user.get("role") == "ADMIN"


# ---------------------------------------------------------------------------
# Sidebar user info + sign-out (injected into every page by _show_sidebar)
# ---------------------------------------------------------------------------

def _show_sidebar():
    with st.sidebar:
        user = st.session_state.get("user", {})
        st.markdown(f"**{user.get('name', '')}** · {user.get('role', '').title()}")
        st.divider()
        if st.button("Sign Out", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


# ---------------------------------------------------------------------------
# Login page (shown when not authenticated)
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
                    result = httpx.post(
                        f"{API_BASE}/api/auth/login",
                        json={"email": email, "password": password},
                        timeout=15,
                    )
                if result.status_code == 200:
                    data = result.json()
                    st.session_state.token = data["token"]
                    st.session_state.user  = {
                        "user_id": data["user_id"],
                        "name":    data["name"],
                        "email":   data["email"],
                        "role":    data["role"],
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
# Landing page (shown after login, before navigating to a sub-page)
# ---------------------------------------------------------------------------

def _landing_page():
    _show_sidebar()
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
# Navigation — only runs when app.py is the active Streamlit entry point.
# When page files import this module, this block is skipped entirely.
# ---------------------------------------------------------------------------

import pathlib as _pathlib
try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx
    _ctx = _get_ctx()
    _active_script = _pathlib.Path(_ctx.main_script_path).resolve() if _ctx else None
except Exception:
    _active_script = None

_this_script = _pathlib.Path(__file__).resolve()

if _active_script == _this_script:
    if not st.session_state.get("token"):
        # Not logged in: show only the login page (no sidebar nav)
        pg = st.navigation(
            [st.Page(_login_page, title="Login", icon="🔐", default=True)],
            position="hidden",
        )
        pg.run()
    else:
        # Logged in: build page list based on role
        _pages_dir = _pathlib.Path(__file__).parent / "pages"

        common_pages = [
            st.Page(_landing_page,                                    title="Home",           icon="🏠", default=True),
            st.Page(str(_pages_dir / "1_Dashboard.py"),               title="Dashboard",      icon="📊"),
            st.Page(str(_pages_dir / "2_Upload_and_Check.py"),        title="Upload & Check", icon="📤"),
            st.Page(str(_pages_dir / "3_Review_Queue.py"),            title="Review Queue",   icon="🔍"),
            st.Page(str(_pages_dir / "4_Vendor_Memory.py"),           title="Vendor Memory",  icon="🧠"),
        ]

        admin_pages = [
            st.Page(str(_pages_dir / "5_Admin.py"),                   title="Admin",          icon="⚙️"),
        ] if is_admin() else []

        pg = st.navigation(common_pages + admin_pages)
        pg.run()
