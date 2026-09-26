"""uf5vmjt.lib.sections.shell — shell section + ghost-autocomplete bridge."""
from __future__ import annotations
import os
import socket
import subprocess
import time
from lib.core.auth import require_auth
from lib.core.config import COLOR_ACCENT, COLOR_ERR, COLOR_MUTED, COLOR_OK, SHELL_HISTORY_LIMIT, SHELL_TIMEOUT_SEC
from lib.core.events import log_event
from lib.core.ui import badge, find_component_dir



def list_path_binaries() -> list[str]:
    """All executable names on PATH, sorted. No duplicates."""
    found: set[str] = set()
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not os.path.isdir(folder):
            continue
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False) and os.access(entry.path, os.X_OK):
                            found.add(entry.name)
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(found)


def suggest_commands(text: str, cwd: str) -> list[str]:
    """Complete first token against PATH binaries, later tokens against cwd.

    Returns up to SHELL_SUGGEST_LIMIT matches. Pure function (testable).
    """
    text = text.strip()
    if not text:
        return []
    parts = text.split()
    if len(parts) <= 1 and not text.endswith((" ", "\t")):
        prefix = parts[0] if parts else ""
        cands = [b for b in SHELL_BUILTINS if b.startswith(prefix)]
        cands += [b for b in list_path_binaries() if b.startswith(prefix) and b not in cands]
        return cands[:SHELL_SUGGEST_LIMIT]
    frag = parts[-1]
    if "/" in frag:
        dirpart, tail = frag.rsplit("/", 1)
        if os.path.isabs(dirpart):
            folder = dirpart
        else:
            folder = os.path.normpath(os.path.join(cwd, os.path.expanduser(dirpart or ".")))
        prefix = dirpart + "/"
    else:
        folder, tail, prefix = cwd, frag, ""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.startswith(tail):
            continue
        full = os.path.join(folder, name)
        suffix = "/" if os.path.isdir(full) else ""
        out.append(prefix + name + suffix)
        if len(out) >= SHELL_SUGGEST_LIMIT:
            break
    return out


def resolve_cd(target: str, cwd: str) -> str | None:
    """Resolve a cd target (absolute, ~, relative) to a real dir or None."""
    target = os.path.expanduser(target.strip()) or os.path.expanduser("~")
    dest = target if os.path.isabs(target) else os.path.normpath(os.path.join(cwd, target))
    dest = os.path.abspath(dest)
    return dest if os.path.isdir(dest) else None


def shell_input_component(candidates: list[str], files: list[str], dirs: dict, cwd: str, key: str):
    """Render the ghost-autocomplete terminal input. Returns submitted cmd or None."""
    global _shell_component
    import streamlit.components.v1 as components

    if not SHELL_COMPONENT_DIR:
        raise FileNotFoundError("shell_input/index.html not found next to main.py")
    if _shell_component is None:
        _shell_component = components.declare_component("uf5_shell_input", path=SHELL_COMPONENT_DIR)
    return _shell_component(binaries=candidates, files=files, dirs=dirs, cwd=cwd, key=key, default=None)


def build_completion_tree(cwd: str, per_dir: int = 60, max_subs: int = 25) -> dict:
    """Shallow dir listings for client-side '/' completion: / + cwd + subdirs."""
    def snap(folder: str) -> list[str]:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return []
        out = []
        for name in names:
            if name.startswith("."):
                continue
            out.append(name + "/" if os.path.isdir(os.path.join(folder, name)) else name)
            if len(out) >= per_dir:
                break
        return out

    tree: dict[str, list[str]] = {}
    tree[os.path.normpath("/")] = snap("/")
    cwd_norm = os.path.normpath(cwd)
    tree[cwd_norm] = snap(cwd_norm)
    for name in tree[cwd_norm][:max_subs]:
        if name.endswith("/"):
            sub = os.path.normpath(os.path.join(cwd_norm, name))
            tree[sub] = snap(sub)
    return tree


def list_cwd_files(cwd: str) -> list[str]:
    """Entry names of cwd for client-side path completion (dirs get '/')."""
    try:
        names = sorted(os.listdir(cwd))
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith("."):
            continue
        out.append(name + "/" if os.path.isdir(os.path.join(cwd, name)) else name)
    return out


def run_shell_command(cmd: str, cwd: str) -> dict:
    """Run cmd with a timeout. Returns {cmd, cwd, rc, out, err, ms}. Never raises."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd or None, capture_output=True, text=True,
            timeout=SHELL_TIMEOUT_SEC, executable="/bin/bash",
        )
        return {"cmd": cmd, "cwd": cwd, "rc": proc.returncode,
                "out": proc.stdout or "", "err": proc.stderr or "",
                "ms": int((time.monotonic() - t0) * 1000)}
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "cwd": cwd, "rc": 124, "out": "",
                "err": f"timeout after {SHELL_TIMEOUT_SEC}s", "ms": SHELL_TIMEOUT_SEC * 1000}
    except Exception as e:  # noqa: BLE001
        return {"cmd": cmd, "cwd": cwd, "rc": 127, "out": "", "err": str(e)[:500],
                "ms": int((time.monotonic() - t0) * 1000)}


def render_shell() -> None:
    """Terminal runner: ghost-autocomplete input, persistent cwd, styled history.

    Completion is client-side inside the custom component (ghost text after a
    pause, Tab to accept/list, live rescan per keystroke). Python only sees
    the submitted command on Enter.
    """
    import streamlit as st

    if not require_auth():
        return
    st.session_state.setdefault("uf5_shell_hist", [])
    st.session_state.setdefault("uf5_cwd", os.getcwd())
    st.session_state.setdefault("uf5_binaries", None)
    st.session_state.setdefault("uf5_input_nonce", 0)
    cwd = st.session_state["uf5_cwd"]
    if st.session_state["uf5_binaries"] is None:
        st.session_state["uf5_binaries"] = list_path_binaries()
    try:
        user_host = f"{os.getlogin()}@{socket.gethostname()}"
    except OSError:
        user_host = socket.gethostname()

    st.markdown(
        f'<div style="font-family:monospace;font-size:13px;margin-bottom:8px;">'
        f'<span style="color:{COLOR_OK};font-weight:700">{user_host}</span>'
        f'<span style="color:{COLOR_MUTED}">:</span>'
        f'<span style="color:{COLOR_ACCENT};font-weight:700">{cwd}</span>'
        f'<span style="color:{COLOR_MUTED}">$ · timeout {SHELL_TIMEOUT_SEC}s</span></div>',
        unsafe_allow_html=True,
    )
    component_ok = True
    try:
        submitted = shell_input_component(
            st.session_state["uf5_binaries"], list_cwd_files(cwd),
            build_completion_tree(cwd), cwd,
            key=f"uf5_shell_in_{st.session_state['uf5_input_nonce']}",
        )
    except Exception as e:  # noqa: BLE001
        component_ok = False
        log_event("error", f"shell component failed ({type(e).__name__}: {e}) — "
                           "ship components/shell_input/index.html next to main.py")
        # Basic fallback WITH chips: chips render before the form so a click
        # may legally preset the input value.
        st.session_state.setdefault("uf5_draft", "")
        draft = st.session_state["uf5_draft"].strip()
        if draft:
            matches = suggest_commands(draft, cwd)
            if matches:
                st.caption("suggestions from this host (click to fill, Enter to run):")
                cols = st.columns(min(len(matches), 6))
                parts = draft.split()
                for i, cand in enumerate(matches[:6]):
                    label = cand if len(cand) <= 18 else cand[:17] + "…"
                    if cols[i % len(cols)].button(f"`{label}`", key=f"uf5_s_{i}_{label}"):
                        base = parts[:-1] if len(parts) > 1 else []
                        full = " ".join(base + [cand]) + ("" if cand.endswith("/") else " ")
                        st.session_state["uf5_cmd"] = full
                        st.session_state["uf5_draft"] = full
                        st.rerun()
                if len(matches) > 6:
                    st.caption(f"+{len(matches) - 6} more — keep typing")
        st.caption("component unavailable — basic input:")
        with st.form("uf5_shell_form", clear_on_submit=True):
            submitted = st.text_input("Command", label_visibility="collapsed", key="uf5_cmd")
            if not st.form_submit_button("Run ⏎", width="stretch"):
                submitted = None

    if submitted and submitted.strip():
        cmd = submitted.strip()
        st.session_state["uf5_draft"] = cmd
        verb = cmd.split()[0]
        if verb == "cd":
            dest = resolve_cd(cmd[2:].strip(), cwd)
            if dest:
                st.session_state["uf5_cwd"] = dest
                log_event("info", f"shell cd -> {dest}")
            else:
                st.session_state["uf5_shell_hist"].append(
                    {"cmd": cmd, "cwd": cwd, "rc": 1,
                     "out": "", "err": f"no such directory: {cmd[2:].strip()}", "ms": 0})
        elif verb == "clear":
            st.session_state["uf5_shell_hist"] = []
        elif verb == "history":
            for i, h in enumerate(st.session_state["uf5_shell_hist"]):
                st.caption(f"{i + 1}: {h['cmd']}")
        elif verb == "help":
            st.caption("builtins: cd <dir> (absolute, ~, relative, persistent) · "
                       "clear · history · help · any host binary")
        else:
            entry = run_shell_command(cmd, cwd)
            st.session_state["uf5_shell_hist"].append(entry)
            del st.session_state["uf5_shell_hist"][:-SHELL_HISTORY_LIMIT]
            level = "ok" if entry["rc"] == 0 else "error"
            log_event(level, f"shell exit={entry['rc']} ms={entry['ms']}: {cmd[:120]}")
        st.session_state["uf5_input_nonce"] += 1
        st.rerun()

    for entry in reversed(st.session_state["uf5_shell_hist"]):
        color = COLOR_OK if entry["rc"] == 0 else COLOR_ERR
        meta = f"{entry['ms']} ms · {entry['cwd']}"
        with st.container(border=True):
            st.markdown(f"`$ {entry['cmd']}`  {badge('exit ' + str(entry['rc']), color)} "
                        f"<span class='uf5-muted'>{meta}</span>",
                        unsafe_allow_html=True)
            if entry["out"].strip():
                st.code(entry["out"].rstrip(), language="text")
            if entry["err"].strip():
                st.code(entry["err"].rstrip(), language="text")


SHELL_BUILTINS = ("cd", "clear", "history", "help", "exit")

SHELL_SUGGEST_LIMIT = 12

SHELL_COMPONENT_DIR = find_component_dir("shell_input")

_shell_component = None

