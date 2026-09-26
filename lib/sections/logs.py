"""uf5vmjt.lib.sections.logs — logs section."""
from __future__ import annotations
import datetime
from lib.core.auth import require_auth
from lib.core.config import COLOR_ACCENT, COLOR_ERR, COLOR_MUTED, COLOR_OK, COLOR_WARN
from lib.core.events import ingest_go_events



def render_logs() -> None:
    import streamlit as st

    if not require_auth():
        return
    try:
        live = st.fragment(run_every=2)
    except TypeError:
        live = st.fragment

    @live
    def _live_logs() -> None:
        # Stale auto-timers from a previous section must render nothing.
        if st.session_state.get("uf5_section", "logs") != "logs":
            return
        ingest_go_events()
        events = st.session_state.get("uf5_events", [])
        sources = sorted({str(e.get("source", "?")) for e in events})

        c1, c2 = st.columns([3, 1])
        level_filter = c1.segmented_control(
            "Level", ["all", "debug", "info", "ok", "warn", "error"],
            default="all", key="uf5_log_level")
        if c2.button("Clear", width="stretch"):
            st.session_state["uf5_events"] = []
            st.rerun()

        # Selection lives in session state; the widget is created without a
        # default so Streamlit never sees default + API value together.
        # New services auto-join the selection; deselected ones stay out.
        if "uf5_log_sources" not in st.session_state:
            st.session_state["uf5_log_sources"] = list(sources)
            st.session_state["uf5_log_known_sources"] = list(sources)
        else:
            known = set(st.session_state.get("uf5_log_known_sources", []))
            fresh = [s for s in sources if s not in known]
            if fresh:
                current = list(st.session_state.get("uf5_log_sources", []))
                current += [s for s in fresh if s not in current]
                st.session_state["uf5_log_sources"] = current
                st.session_state["uf5_log_known_sources"] = sorted(known | set(sources))

        f1, f2 = st.columns(2)
        picked = set(f1.multiselect("Service", sources, key="uf5_log_sources"))
        order = f2.segmented_control("Order", ["Oldest first", "Newest first"],
                                     default="Oldest first",
                                     key="uf5_log_order")

        t1, t2 = st.columns(2)
        from_t = t1.time_input("From", value=datetime.time(0, 0),
                               key="uf5_log_from")
        to_t = t2.time_input("To", value=datetime.time(23, 59),
                             key="uf5_log_to")
        from_s, to_s = from_t.strftime("%H:%M"), to_t.strftime("%H:%M")
        wrap = from_s > to_s

        lines = []
        for e in events:
            if level_filter != "all" and e.get("level") != level_filter:
                continue
            if str(e.get("source", "?")) not in picked:
                continue
            stamp = e.get("t")
            if stamp:
                hm = datetime.datetime.fromtimestamp(stamp).strftime("%H:%M")
                inside = (hm >= from_s or hm <= to_s) if wrap else (from_s <= hm <= to_s)
                if not inside:
                    continue
            color = LEVEL_COLOR.get(e.get("level"), COLOR_MUTED)
            lines.append(
                f'<div style="font-family:monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;">'
                f'<span style="color:{COLOR_MUTED}">{e.get("ts", "?")}</span> '
                f'<span style="color:{color};font-weight:700">{str(e.get("level", "?")).upper():5s}</span> '
                f'<span style="color:{COLOR_MUTED}">[{e.get("source", "?")}]</span> '
                f'<span>{e.get("msg", "")}</span></div>'
            )
        if order == "Newest first":
            lines.reverse()
        st.caption(f"{len(lines)} of {len(events)} lines · live every 2s")
        st.markdown("\n".join(lines) if lines else
                    '<span class="uf5-muted">no events yet</span>', unsafe_allow_html=True)

    _live_logs()


LEVEL_COLOR = {"debug": COLOR_MUTED, "info": COLOR_ACCENT, "ok": COLOR_OK,
               "warn": COLOR_WARN, "error": COLOR_ERR}

