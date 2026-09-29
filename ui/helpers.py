"""
LedgerMind — ui/helpers.py
Shared API helpers and sidebar function imported by all page files.

This module has NO module-level Streamlit widget calls so it is safe to import
from any page file without triggering set_page_config conflicts or navigation
re-entry.
"""

import os
import httpx
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


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


def show_sidebar():
    """Render the user info + sign-out button in the sidebar."""
    with st.sidebar:
        user = st.session_state.get("user", {})
        st.markdown(f"**{user.get('name', '')}** · {user.get('role', '').title()}")
        st.divider()
        if st.button("Sign Out", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
