"""uf5vmjt.lib.core.events — in-memory event stream + Go sidecar log tail."""
from __future__ import annotations
import datetime
import json
import os
import time
from lib.core.config import GO_LOG_PATH, GO_LOG_TAIL_BYTES



def _now_tag() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def log_event(level: str, message: str, source: str = "main") -> None:
    """Append one line to the in-memory event stream (used by logs section)."""
    import streamlit as st

    events = st.session_state.setdefault("uf5_events", [])
    events.append({"ts": _now_tag(), "t": time.time(),
                   "level": level, "source": source, "msg": message})
    del events[:-500]


def ingest_go_events() -> int:
    """Tail JSON-lines from the Go sidecar log into the event stream.

    Expected line shape: {"level": "info", "msg": "..."}. Returns lines added.
    First ingest per session starts at the last 32 KB, not at byte zero: the
    offset is lost on reboot while the file may persist, and replaying hours
    of stale lines would flood the stream.
    """
    import streamlit as st

    if not os.path.isfile(GO_LOG_PATH):
        return 0
    try:
        offset = int(st.session_state.get("uf5_go_offset", 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    first = "uf5_go_offset" not in st.session_state
    added = 0
    try:
        with open(GO_LOG_PATH, "rb") as f:
            if first:
                f.seek(0, os.SEEK_END)
                end = f.tell()
                f.seek(max(0, end - GO_LOG_TAIL_BYTES))
                if end > GO_LOG_TAIL_BYTES:
                    f.readline()  # drop the partial first line
            else:
                f.seek(offset)
            for raw in f:
                try:
                    obj = json.loads(raw.decode(errors="replace"))
                except ValueError:
                    continue
                log_event(str(obj.get("level", "info")), str(obj.get("msg", "")), source="go")
                added += 1
            st.session_state["uf5_go_offset"] = f.tell()
    except OSError:
        return 0
    return added


