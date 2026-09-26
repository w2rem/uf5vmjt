"""uf5vmjt.lib.sections.check — ad-hoc proxy check section.

Paste :// lines (no clash YAML) -> sidecar builds the sing-box batch,
runs it, polls Clash delays per probe and resolves egress geoip per
alive probe. Gated by ACCESS_TOKEN like Shell/Logs.

Everything live (poll + summary + rows + pages) renders INSIDE the
fragment, so cards update in real time without a full rerun. Results
render in uf5-geocard-styled rows with flag watermarks, paginated.
"""
from __future__ import annotations
import html
from lib.core.auth import require_auth
from lib.core.config import COLOR_ERR, COLOR_MUTED, COLOR_OK, COLOR_WARN
from lib.core.events import log_event
from lib.core.ui import badge, card, card_end
from lib.services.netinfo import flag_url
from lib.services.sidecar import worker_boot, worker_check_poll, worker_check_start


PAGE_SIZE = 25
POLL_EVERY_SEC = 4
# Gone misses (worker answered 404) condemn a run; transport misses never
# do — silence is not death (a throttled box times out localhost polls
# while the worker happily finishes). After a streak, poll every Nth tick
# to lighten the starved box instead of hammering it.
MISS_GONE_LIMIT = 3
MISS_BACKOFF_EVERY = 3


def _latency_badge(r: dict) -> str:
    if not r.get("done"):
        return badge("…", COLOR_MUTED)
    if not r.get("alive"):
        return badge("dead", COLOR_MUTED)
    try:
        ms = int(r.get("latency_ms") or 0)
    except (TypeError, ValueError):
        return badge("?", COLOR_MUTED)
    # Chained verdicts are approximations (relay latency subtracted).
    prefix = "~" if r.get("via") else ""
    if ms < 300:
        return badge(f"{prefix}{ms} ms", COLOR_OK)
    if ms < 800:
        return badge(f"{prefix}{ms} ms", COLOR_WARN)
    return badge(f"{prefix}{ms} ms", COLOR_ERR)


def _row_html(r: dict) -> str:
    geo = r.get("geo") or {}
    cc = str(geo.get("country_code", "") or "")
    flag = flag_url(cc)
    small = f'<img class="uf5-flag" src="{flag}" alt="{html.escape(cc)}"/>' if flag else ""
    name = html.escape(str(r.get("name") or r.get("tag") or "?"))
    ip = html.escape(str(geo.get("ip", "") or ""))
    place = html.escape(str(geo.get("city") or geo.get("country") or ""))
    org = html.escape(str(geo.get("org") or ""))
    asn = geo.get("asn") or "?"
    err = html.escape(str(r.get("error") or ""))
    sub = " · ".join(p for p in (ip, place, f"AS{asn} {org}".strip()) if p)
    if r.get("via"):
        via_txt = f"via {r.get('via')}"
        try:
            raw = int(r.get("chain_ms") or 0)
        except (TypeError, ValueError):
            raw = 0
        if raw:
            via_txt += f" · raw {raw} ms"
        sub = (sub + " · " if sub else "") + via_txt
    if r.get("done") and not r.get("alive") and err:
        sub = (sub + " · " if sub else "") + err
    return (
        f'<div class="uf5-checkrow">{small}'
        f'<span class="uf5-checkrow-main"><b>{name}</b>'
        f'<span class="uf5-muted">{sub}</span></span>'
        f'{_latency_badge(r)}</div>'
    )


def _summary_card(run: dict) -> str:
    results = run.get("results") or []
    done_n = sum(1 for r in results if r.get("done"))
    alive = [r for r in results if r.get("alive")]
    lat = sorted(int(r.get("latency_ms") or 0) for r in alive if r.get("latency_ms"))
    med = lat[len(lat) // 2] if lat else 0
    ccs = sorted({str((r.get("geo") or {}).get("country_code") or "?") for r in alive})
    top_cc = ccs[0] if len(ccs) == 1 and ccs[0] != "?" else ""
    flag = flag_url(top_cc)
    big = f'<img class="uf5-geocard-bg" src="{flag}" alt=""/>' if flag else ""
    state = run.get("state", "?")
    state_badge = badge(state, COLOR_OK if state == "done" else COLOR_WARN)
    dead = done_n - len(alive)
    pending = max(0, int(run.get("total") or 0) - done_n)
    via_n = sum(1 for r in alive if r.get("via"))
    line2 = f"{len(alive)} alive · {dead} dead"
    if via_n:
        line2 += f" · {via_n} via chain"
    if pending:
        line2 += f" · {pending} running"
    if med:
        line2 += f" · median {med} ms"
    if len(ccs) > 1 or (ccs and ccs[0] != "?"):
        shown = ", ".join(c for c in ccs[:6] if c != "?") or "?"
        line2 += f" · {shown}"
    return (
        f'<div class="uf5-geocard">{big}<div class="uf5-geo-fg">'
        f'<div class="uf5-geo-ip"><span class="uf5-big">Check {html.escape(str(run.get("run_id", "")))}</span>'
        f"{state_badge}</div>"
        f"<div>{html.escape(line2)}</div>"
        f"</div></div>"
    )


def _filtered_sorted(run: dict, flt: str, srt: str) -> list:
    results = list(run.get("results") or [])
    if flt == "alive":
        results = [r for r in results if r.get("alive")]
    elif flt == "dead":
        results = [r for r in results if r.get("done") and not r.get("alive")]
    if srt == "latency":
        # Alive by latency, then pending, then measured dead.
        results.sort(key=lambda r: (0, int(r.get("latency_ms") or 0)) if r.get("alive")
                     else ((1, 0) if not r.get("done") else (2, 0)))
    else:
        results.sort(key=lambda r: int(r.get("index") or 0))
    return results


def render_check() -> None:
    import streamlit as st

    if not require_auth():
        return
    card("Proxy check")
    st.caption("Только :// строки (vless/vmess/trojan/ss/socks/http/hy2/tuic/wireguard), по одной на строку. Без clash YAML — невалидные строки отсеются с причиной.")
    st.text_area("Ссылки", key="uf5_check_input", height=220, label_visibility="collapsed",
                 placeholder="vless://…\ntrojan://…")
    # Sliders need width: two full-width rows. Fresh keys (the old
    # selectbox keys held seconds, out of slider range — stale state
    # would crash the widget).
    r1a, r1b = st.columns(2)
    with r1a:
        timeout_ms = st.slider("Таймаут замера", min_value=100, max_value=10000,
                               value=10000, step=20, format="%d мс",
                               key="uf5_check_timeout_ms")
    with r1b:
        parallel = st.select_slider("Одновременный батч", [10, 25, 50, 75, 100],
                                    value=25, key="uf5_check_batch",
                                    format_func=lambda v: f"{v} потоков")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        geo_parallel = st.selectbox("Geo-поток", [1, 2, 4, 8], index=2, key="uf5_check_geopar")
    with c2:
        dns = st.selectbox("DNS", ["auto", "google", "cloudflare", "yandex", "ali"],
                           index=0, key="uf5_check_dns",
                           format_func={"auto": "Авто", "google": "Google",
                                        "cloudflare": "Cloudflare", "yandex": "Yandex",
                                        "ali": "Alibaba"}.get)
    with c3:
        go = st.button("Проверить", key="uf5_check_go", use_container_width=True)
    if go:
        lines = [l for l in str(st.session_state.get("uf5_check_input") or "").splitlines() if l.strip()]
        if not lines:
            st.warning("Вставь хотя бы одну строку.")
        else:
            with st.spinner(f"sidecar разбирает {len(lines)} строк…"):
                start = worker_check_start(lines, timeout_ms=int(timeout_ms),
                                           parallel=int(parallel), geo_parallel=int(geo_parallel),
                                           dns=str(dns))
            if start.get("error"):
                st.error(start["error"])
                log_event("warn", f"check start failed: {start['error']}", source="check")
            else:
                accepted = start.get("accepted") or []
                st.session_state["uf5_check_run"] = {
                    "run_id": start["run_id"], "state": "running",
                    "total": len(accepted),
                    "results": [], "rejected": start.get("rejected") or [],
                }
                st.session_state["uf5_check_page"] = 0
                st.session_state["uf5_check_filter"] = "all"
                st.session_state["uf5_check_miss"] = 0
                st.session_state["uf5_check_gone"] = 0
                st.session_state["uf5_check_boot"] = worker_boot()
                log_event("ok", f"check started: {start['run_id']} "
                                f"({len(accepted)} probes, "
                                f"{len(start.get('rejected') or [])} rejected)", source="check")
                st.rerun()
    run = st.session_state.get("uf5_check_run")
    if not run:
        card_end()
        return

    # All interactive widgets live OUTSIDE the timed fragment: widgets
    # remounted every tick steal focus and drop clicks (the "freeze").
    # The fragment below is read-only: poll + progress + summary + rows.
    rejected = run.get("rejected") or []
    if rejected:
        with st.expander(f"Отсеяно: {len(rejected)}"):
            for r in rejected[:100]:
                st.caption(f"строка {r.get('index', '?')}: {r.get('reason', '?')}")

    f1, f2 = st.columns(2)
    with f1:
        st.selectbox("Показать", ["all", "alive", "dead"], key="uf5_check_filter",
                     format_func={"all": "Все", "alive": "Живые", "dead": "Мёртвые"}.get)
    with f2:
        st.selectbox("Порядок", ["latency", "input"], key="uf5_check_sort",
                     format_func={"latency": "Сначала быстрые", "input": "Как вставлено"}.get)

    try:
        live = st.fragment(run_every=POLL_EVERY_SEC)
    except TypeError:
        live = st.fragment

    @live
    def _live() -> None:
        if run.get("state") == "running":
            miss = int(st.session_state.get("uf5_check_miss") or 0)
            snap = {}
            if miss >= MISS_BACKOFF_EVERY and miss % MISS_BACKOFF_EVERY != 0:
                # Backoff tick: lighten the starved box, count it so the
                # cadence walks forward instead of stalling (else the skip
                # repeats forever on a frozen counter).
                st.session_state["uf5_check_miss"] = miss + 1
            else:
                snap = worker_check_poll(run["run_id"])
            if snap and snap.get("run_id"):
                st.session_state["uf5_check_miss"] = 0
                st.session_state["uf5_check_gone"] = 0
                run.update({k: snap.get(k, run.get(k)) for k in
                            ("state", "total", "results", "rejected", "detail")})
                run["done"] = snap.get("done", run.get("done", 0))
                if run.get("state") in ("done", "error"):
                    log_event("ok" if run["state"] == "done" else "warn",
                              f"check {run['run_id']}: {run['state']} "
                              f"({len(run.get('results') or [])} results)", source="check")
            elif snap.get("gone"):
                # Worker answered but forgot the run: restarted, expired,
                # or finished-before-restart without snapshot. Confirm via
                # boot id before condemning — a slow worker never lands here.
                gone = int(st.session_state.get("uf5_check_gone") or 0) + 1
                st.session_state["uf5_check_gone"] = gone
                if gone >= MISS_GONE_LIMIT:
                    boot_now = worker_boot()
                    boot_then = int(st.session_state.get("uf5_check_boot") or 0)
                    run["state"] = "error"
                    if boot_now and boot_then and boot_now != boot_then:
                        run["detail"] = "sidecar restarted, run lost — start again"
                    elif boot_now:
                        run["detail"] = "worker forgot the run (restart/expiry) — start again"
                    else:
                        run["detail"] = "sidecar down — wait for respawn, then start again"
            else:
                # Transport trouble (timeout/down): silence is not death.
                # Never condemn — the worker may be finishing the run
                # while this box is too starved to poll it. Keep polling
                # (with backoff above) until a snapshot or a 404 lands.
                miss = int(st.session_state.get("uf5_check_miss") or 0) + 1
                st.session_state["uf5_check_miss"] = miss
        done = sum(1 for r in run.get("results") or [] if r.get("done"))
        total = int(run.get("total") or 0)
        st.progress(min(1.0, done / total) if total else 0.0,
                    text=f"{run.get('state', '?')} — {done}/{total}")
        if run.get("state") == "error" and run.get("detail"):
            st.error(str(run["detail"]))
        if run.get("state") == "running" and int(st.session_state.get("uf5_check_miss") or 0) >= MISS_BACKOFF_EVERY:
            st.caption("sidecar отвечает медленно — опрос продолжается в фоне, результаты подтянутся")

        st.markdown(_summary_card(run), unsafe_allow_html=True)

        flt = st.session_state.get("uf5_check_filter", "all")
        srt = st.session_state.get("uf5_check_sort", "latency")
        results = _filtered_sorted(run, flt, srt)
        total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(max(0, int(st.session_state.get("uf5_check_page") or 0)), total_pages - 1)
        chunk = results[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        if not chunk:
            st.caption("Пока пусто — опрос идёт, страница обновится сама.")
        for r in chunk:
            st.markdown(_row_html(r), unsafe_allow_html=True)

    _live()

    # Pagination outside the fragment (same session keys the tick reads).
    flt = st.session_state.get("uf5_check_filter", "all")
    srt = st.session_state.get("uf5_check_sort", "latency")
    results = _filtered_sorted(run, flt, srt)
    total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(0, int(st.session_state.get("uf5_check_page") or 0)), total_pages - 1)
    p1, p2, p3 = st.columns([1, 1, 4])
    with p1:
        if st.button("← Назад", key="uf5_check_prev", disabled=page == 0,
                     use_container_width=True):
            st.session_state["uf5_check_page"] = page - 1
    with p2:
        if st.button("Вперёд →", key="uf5_check_next",
                     disabled=page >= total_pages - 1, use_container_width=True):
            st.session_state["uf5_check_page"] = page + 1
    with p3:
        st.caption(f"стр. {page + 1}/{total_pages} · всего {len(results)}")
    card_end()
