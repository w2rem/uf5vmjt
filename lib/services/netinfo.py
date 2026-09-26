"""uf5vmjt.lib.services.netinfo — geo, cluster, disambiguate, keepalive probe."""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from lib.core.config import CLUSTER_TTL_SEC, FLAG_CDN, GEO_TTL_SEC, GEO_URL
from lib.core.events import log_event



def fetch_json(url: str, timeout: float = 8) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "uf5vmjt/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode(errors="replace"))


def get_geo() -> dict:
    """Fetch api.ip.sb/geoip, cached in session for GEO_TTL_SEC."""
    import streamlit as st

    now = time.monotonic()
    cached = st.session_state.get("uf5_geo") or {}
    ts = float(st.session_state.get("uf5_geo_ts", 0) or 0)
    if cached and now - ts < GEO_TTL_SEC:
        return cached
    try:
        data = fetch_json(GEO_URL)
        st.session_state["uf5_geo"] = data
        st.session_state["uf5_geo_ts"] = now
        log_event("ok", "geo resolved via api.ip.sb/geoip", source="net")
        return data
    except Exception as e:  # noqa: BLE001
        log_event("warn", f"geo fetch failed: {e}", source="net")
        return cached


def app_host() -> str:
    """Public host of this app from the browser URL (empty when local/headless)."""
    try:
        import streamlit as st

        url = st.context.url
    except Exception:  # noqa: BLE001
        return ""
    if not url:
        return ""
    return urlparse(url).hostname or ""


def get_cluster() -> dict:
    """Resolve /api/v2/app/disambiguate for this host, cached CLUSTER_TTL_SEC."""
    import streamlit as st

    now = time.monotonic()
    cached = st.session_state.get("uf5_cluster") or {}
    ts = float(st.session_state.get("uf5_cluster_ts", 0) or 0)
    if cached and now - ts < CLUSTER_TTL_SEC:
        return cached
    host = app_host()
    if not host:
        return {}
    try:
        data = fetch_json(f"https://{host}/api/v2/app/disambiguate")
        st.session_state["uf5_cluster"] = data
        st.session_state["uf5_cluster_ts"] = now
        log_event("ok", f"cluster resolved: {data.get('cluster', '?')}", source="net")
        return data
    except Exception as e:  # noqa: BLE001
        log_event("warn", f"disambiguate failed: {e}", source="net")
        return cached


def probe_disambiguate(host: str, token: str) -> tuple[int, dict]:
    """GET /api/v2/app/disambiguate. Returns (http_code, body).

    -1 on transport error. Token goes into the Cookie header, never logs.
    Never raises.
    """
    headers = {"User-Agent": "uf5vmjt/1.0", "Accept": "application/json"}
    if token:
        headers["Cookie"] = f"streamlit_session={token}"
    req = urllib.request.Request(f"https://{host}/api/v2/app/disambiguate",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            try:
                return r.status, json.loads(r.read().decode(errors="replace"))
            except ValueError:
                return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")
            return e.code, json.loads(body) if body else {}
        except (ValueError, OSError):
            return e.code, {}
    except Exception:  # noqa: BLE001
        return -1, {}


def ensure_keepalive_config() -> None:
    """Probe app visibility once per session, then fix the keepalive config.

    Anonymous 404 -> warn the operator to set STREAMLIT_SESSION_TOKEN.
    Resolved app -> default UP_EVERY to 10m when unset (child inherits it).
    Never raises.
    """
    import streamlit as st

    if st.session_state.get("uf5_visibility_checked"):
        return
    st.session_state["uf5_visibility_checked"] = True
    host = app_host()
    if not host:
        return
    token = (os.environ.get("STREAMLIT_SESSION_TOKEN")
             or os.environ.get("STREAMLIT_SESSION") or "")
    code, data = probe_disambiguate(host, token)
    if code == 200:
        # Feed the geo card cache — render_geo_cluster() reuses it, no refetch.
        st.session_state["uf5_cluster"] = data
        st.session_state["uf5_cluster_ts"] = time.monotonic()
        if not os.environ.get("UP_EVERY"):
            os.environ["UP_EVERY"] = "10"
            log_event("debug", "UP_EVERY defaulted to 10 min", source="net")
        every = (os.environ.get("UP_EVERY") or "10").strip()
        display = f"{every} min" if every.isdigit() else every
        log_event("ok", f"app visible on {data.get('cluster', '?')} — "
                        f"keepalive every {display}",
                  source="net")
    elif code == 404 and not token:
        log_event("warn", "app invisible anonymously (disambiguate 404) — "
                          "if private, set STREAMLIT_SESSION_TOKEN to the owner "
                          "streamlit_session cookie value", source="net")
    elif code == 404:
        log_event("warn", "disambiguate 404 even with session — "
                          "token expired or app gone", source="net")
    elif code == -1:
        log_event("warn", "disambiguate unreachable — keepalive defaults apply",
                  source="net")


def flag_url(country_code: str) -> str:
    """CDN URL of a detailed SVG flag, or empty when unknown."""
    code = (country_code or "").strip().lower()
    if len(code) != 2 or not code.isalpha():
        return ""
    return f"{FLAG_CDN}/{code}.svg"


def shard_color(cluster: str) -> str:
    """Deterministic muted accent per shard (e.g. shard-4)."""
    palette = ["#4A7FA5", "#5A9E6F", "#8A7FB5", "#B5894A", "#5FA8A0", "#A56A7F"]
    idx = sum(ord(c) for c in cluster) % len(palette)
    return palette[idx]


