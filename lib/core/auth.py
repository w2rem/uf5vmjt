"""uf5vmjt.lib.core.auth — token wall, signed session cookie, lock button."""
from __future__ import annotations
import hashlib
import hmac
import os
import time
from lib.core.config import SESSION_COOKIE, SESSION_TTL_MIN_DEFAULT
from lib.core.ui import find_component_dir



def access_token_expected() -> str:
    """ACCESS_TOKEN from env, else st.secrets. Empty when unconfigured."""
    token = (os.environ.get("ACCESS_TOKEN") or "").strip()
    if token:
        return token
    try:
        import streamlit as st

        value = st.secrets.get("ACCESS_TOKEN", "")
    except Exception:  # noqa: BLE001
        return ""
    return str(value or "").strip()


def session_ttl_sec() -> int:
    """Session lifetime in seconds (SESSION_TTL_MIN, default 30, floor 1m)."""
    raw = (os.environ.get("SESSION_TTL_MIN") or "").strip()
    try:
        minutes = max(int(raw), 1) if raw else SESSION_TTL_MIN_DEFAULT
    except ValueError:
        minutes = SESSION_TTL_MIN_DEFAULT
    return minutes * 60


def sign_session(exp: int, key: str) -> str:
    """Mint '<exp>.<hmac>' with key — stateless, verified without storage."""
    sig = hmac.new(key.encode(), f"{SESSION_COOKIE}:{exp}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_session_exp(cookie_value: str, key: str) -> int:
    """Return the cookie expiry unix time, or 0 when bad/expired/tampered."""
    try:
        exp_s, sig = (cookie_value or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return 0
    want = hmac.new(key.encode(), f"{SESSION_COOKIE}:{exp}".encode(),
                    hashlib.sha256).hexdigest()
    try:
        ok = hmac.compare_digest(want.encode(), sig.encode())
    except (TypeError, ValueError):
        return 0
    return exp if ok and exp > int(time.time()) else 0


def read_session_cookie() -> str:
    """Our session cookie via st.context.cookies (read-only mapping)."""
    try:
        import streamlit as st

        return str(st.context.cookies.get(SESSION_COOKIE, "") or "")
    except Exception:  # noqa: BLE001
        return ""


def session_cookie_api(action: str, value: str = "", max_age: int = 0) -> bool:
    """Run one cookie action (set/clear). False when the bridge is missing —
    the tab session still works, only reload persistence is lost."""
    global _session_component
    import streamlit as st
    import streamlit.components.v1 as components

    if not SESSION_COMPONENT_DIR:
        st.caption("persistent login unavailable "
                   "(ship components/session_cookie/index.html next to main.py)")
        return False
    if _session_component is None:
        _session_component = components.declare_component("uf5_session_cookie",
                                                          path=SESSION_COMPONENT_DIR)
    _session_component(action=action, value=value, max_age=max_age,
                       name=SESSION_COOKIE, key="uf5_sess_cookie", default=None)
    return True


def is_authed(key: str) -> bool:
    """Fast path (tab session) then cookie path (reload/new tab)."""
    import streamlit as st

    now = int(time.time())
    try:
        until = int(st.session_state.get("uf5_authed_until", 0) or 0)
    except (TypeError, ValueError):
        until = 0
    if until > now:
        return True
    exp = valid_session_exp(read_session_cookie(), key)
    if exp > now:
        st.session_state["uf5_authed_until"] = exp
        return True
    return False


def require_auth() -> bool:
    """Gate for Shell/Logs. Renders the wall when unauthenticated."""
    import streamlit as st

    key = access_token_expected()
    if not key:
        st.warning("ACCESS_TOKEN not configured — Shell/Logs are open. "
                   "Set ACCESS_TOKEN to lock them.")
        return True
    if is_authed(key):
        return True
    st.markdown("**Restricted area** — Shell and Logs need the access token. "
                "Stats stays public.")
    # Form, not bare widgets: Enter inside the input submits the form, so
    # keyboard and mouse share one path (bare button ignored Enter entirely).
    with st.form("uf5_unlock_form", clear_on_submit=False):
        token = st.text_input("Access token", type="password", key="uf5_token_input")
        submit = st.form_submit_button("Unlock")
    if submit:
        if (token or "").strip() and _compare_tokens((token or "").strip(), key):
            exp = int(time.time()) + session_ttl_sec()
            st.session_state["uf5_authed_until"] = exp
            session_cookie_api("set", sign_session(exp, key), session_ttl_sec())
            st.rerun()
        time.sleep(1)
        st.error("wrong token")
    return False


def _compare_tokens(a: str, b: str) -> bool:
    """Constant-time compare on bytes (bytes form tolerates non-ASCII)."""
    try:
        return hmac.compare_digest(a.encode(), b.encode())
    except (TypeError, ValueError):
        return False


def render_lock_button() -> None:
    """Sidebar Lock: clears cookie + tab session. Only when wall + authed."""
    import streamlit as st

    key = access_token_expected()
    if not key or not is_authed(key):
        return
    if st.sidebar.button("Lock", key="uf5_lock", width="stretch"):
        st.session_state.pop("uf5_authed_until", None)
        try:
            st.session_state.pop("uf5_token_input", None)
        except Exception:  # noqa: BLE001
            pass  # widget keys are read-only outside callbacks
        session_cookie_api("clear")
        st.rerun()


SESSION_COMPONENT_DIR = find_component_dir("session_cookie")

_session_component = None

