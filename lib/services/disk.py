"""uf5vmjt.lib.services.disk — filesystems, top consumers, disk panel."""
from __future__ import annotations
import os
import shutil
import time
from stat import S_ISDIR, S_ISLNK
from lib.core.config import COLOR_ACCENT, COLOR_ACCENT_PALE, DISK_SCAN_BUDGET_SEC, DISK_TOP_TTL_SEC, _PROCESS_CACHE, _PSEUDO_FS
from lib.services.sysinfo import fmt_gb



def list_filesystems() -> list[dict]:
    """Mounted real filesystems, deduped by device, with usage numbers."""
    mounts: list[tuple[str, str]] = []
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                dev, point, fstype = parts[0], parts[1], parts[2]
                if fstype in _PSEUDO_FS or point.startswith(("/proc", "/sys", "/dev")):
                    continue
                mounts.append((dev, point))
    except OSError:
        mounts = [("", "/")]
    seen: set[int] = set()
    out: list[dict] = []
    for _dev, point in mounts:
        try:
            dev_id = os.stat(point).st_dev
        except OSError:
            continue
        if dev_id in seen:
            continue
        seen.add(dev_id)
        try:
            usage = shutil.disk_usage(point)
        except OSError:
            continue
        if usage.total <= 0:
            continue
        out.append({"mount": point, "total": usage.total,
                    "used": usage.used, "free": usage.free})
    return out


def top_dirs(root: str = "/", limit: int = 8) -> tuple[list[tuple[str, int]], bool]:
    """Depth-first size scan of root's children. Returns (items, truncated).

    Bounded by DISK_SCAN_BUDGET_SEC; stays on one device; skips unreadable.
    Results cached in session for DISK_TOP_TTL_SEC (process cache fallback).
    """
    try:
        import streamlit as st

        store: dict = st.session_state
    except ImportError:
        store = _PROCESS_CACHE

    now = time.monotonic()
    cached = store.get("uf5_dutop")
    ts = float(store.get("uf5_dutop_ts", 0) or 0)
    if cached and now - ts < DISK_TOP_TTL_SEC:
        return cached, False
    try:
        root_dev = os.stat(root).st_dev
    except OSError:
        return [], False
    sizes: dict[str, int] = {}
    truncated = False
    t0 = time.monotonic()
    try:
        children = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return [], False
    for child in children:
        if time.monotonic() - t0 > DISK_SCAN_BUDGET_SEC:
            truncated = True
            break
        try:
            if child.is_symlink():
                continue
            total = 0
            stack = [child.path]
            while stack:
                if time.monotonic() - t0 > DISK_SCAN_BUDGET_SEC:
                    truncated = True
                    stack.clear()
                    break
                cur = stack.pop()
                try:
                    stt = os.lstat(cur)
                except OSError:
                    continue
                if stt.st_dev != root_dev:
                    continue
                total += stt.st_blocks * 512
                if S_ISDIR(stt.st_mode) and not S_ISLNK(stt.st_mode):
                    try:
                        with os.scandir(cur) as it:
                            stack.extend(e.path for e in it)
                    except OSError:
                        continue
            # Count the top-level dir itself.
            try:
                total += os.lstat(child.path).st_blocks * 512
            except OSError:
                pass
            if total > 0:
                sizes[child.name] = total
        except OSError:
            continue
    items = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    store["uf5_dutop"] = items
    store["uf5_dutop_ts"] = now
    return items, truncated


def render_disk_panel() -> None:
    """Filesystems as 0..max stacked tracks + top consumer bars."""
    import streamlit as st

    fs_list = list_filesystems()
    if not fs_list:
        st.caption("disk info unavailable")
        return
    for idx, fs in enumerate(fs_list):
        total, used, free = fs["total"], fs["used"], fs["free"]
        pct = used / total * 100
        if idx > 0:
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        st.markdown(f"<span class='uf5-muted'>filesystem</span>  **`{fs['mount']}`**",
                    unsafe_allow_html=True)
        bar = (
            f'<div class="uf5-memseg" title="used {fmt_gb(used)}" '
            f'style="width:{pct:.2f}%;background:{COLOR_ACCENT}"></div>'
            f'<div class="uf5-memseg" title="free {fmt_gb(free)}" '
            f'style="width:{100 - pct:.2f}%;background:{COLOR_ACCENT_PALE}"></div>'
        )
        st.markdown(f'<div class="uf5-memtrack">{bar}</div>', unsafe_allow_html=True)
        # Every card gets a delta line so all three share one height (symmetry).
        m1, m2, m3 = st.columns(3)
        m1.metric("Total", fmt_gb(total), "capacity", delta_color="off")
        m2.metric("Used", fmt_gb(used), f"{pct:.1f}%")
        m3.metric("Free", fmt_gb(free), f"{100 - pct:.1f}%")

    st.divider()
    st.markdown("**Top consumers**  <span class='uf5-muted'>depth-1 scan of `/`, "
                "cached 2 min</span>", unsafe_allow_html=True)
    items, truncated = top_dirs()
    if not items:
        st.caption("directory scan unavailable")
        return
    top = items[0][1] if items else 1
    rows = []
    for i, (name, size) in enumerate(items):
        opacity = round(1 - i * 0.07, 2)
        rows.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:7px 0;">'
            f'<div class="uf5-muted" style="width:26px;font-family:monospace;'
            f'font-size:12px;text-align:right;">{i + 1:02d}</div>'
            f'<div style="width:130px;text-align:right;font-family:monospace;'
            f'font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">/{name}</div>'
            f'<div class="uf5-memtrack" style="height:22px;flex:1;">'
            f'<div class="uf5-memseg" style="width:{size / top * 100:.2f}%;'
            f'background:{COLOR_ACCENT};opacity:{opacity}"></div></div>'
            f'<div style="width:90px;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;">{fmt_gb(size)}</div></div>'
        )
    st.markdown("".join(rows), unsafe_allow_html=True)
    if truncated:
        st.caption("scan hit the time budget — biggest entries shown first")


