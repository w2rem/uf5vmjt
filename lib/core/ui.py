"""uf5vmjt.lib.core.ui — style injection, cards, badges, icons, component locator."""
from __future__ import annotations
import os
import re
from lib.core.config import COLOR_ACCENT, COLOR_ACCENT_PALE, COLOR_ACCENT_SOFT, COLOR_BG, COLOR_BORDER, COLOR_MUTED, COLOR_PANEL, COLOR_TEXT, ICON_SVGS



def inject_style() -> None:
    import streamlit as st

    st.markdown(
        f"""<style>
        .stApp {{ background: {COLOR_BG}; color: {COLOR_TEXT};
                 font-variant-numeric: tabular-nums; }}
        .uf5-card {{ background: {COLOR_PANEL}; border: 1px solid {COLOR_BORDER};
                     border-radius: 14px; padding: 16px 18px; margin-bottom: 14px;
                     box-shadow: 0 1px 2px rgba(47,62,70,.05), 0 8px 24px -12px rgba(47,62,70,.18); }}
        .uf5-title {{ font-size: 13px; font-weight: 600; letter-spacing: .04em;
                      text-transform: uppercase; color: {COLOR_MUTED}; margin-bottom: 8px; }}
        .uf5-big {{ font-size: 22px; font-weight: 700; color: {COLOR_TEXT}; }}
        .uf5-muted {{ color: {COLOR_MUTED}; font-size: 13px; }}
        .uf5-badge {{ display: inline-block; padding: 3px 12px; border-radius: 999px;
                      font-size: 13px; font-weight: 600; color: #fff; }}
        .uf5-ver {{ display: flex; align-items: center; gap: 12px; }}
        .uf5-ver b {{ font-size: 15px; }}
        .uf5-ver svg {{ flex: none; }}
        .uf5-ver .uf5-ver-name {{ flex: 1; min-width: 0; font-size: 15px; font-weight: 600;
                                  white-space: nowrap; overflow: hidden;
                                  text-overflow: ellipsis; }}
        .uf5-ver .uf5-badge {{ flex: none; }}
        /* Compact versions row (5+ badges): tighter type, no wrap. */
        .uf5-ver-compact {{ gap: 10px; }}
        .uf5-ver-compact .uf5-ver-name {{ font-size: 13px; }}
        .uf5-ver-compact .uf5-badge {{ font-size: 11px; padding: 2px 10px;
                                       white-space: nowrap; }}
        /* Sidebar: soft studio panel, both testids covered. */
        section[data-testid="stSidebar"], div[data-testid="stSidebarContent"] {{
            background: linear-gradient(180deg, #FFFFFF 0%, {COLOR_PANEL} 78%);
        }}
        /* Sidebar nav cards below. */
        /* Sidebar nav: option cards only — the widget group label is not
           an option and stays hidden even under display overrides. */
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:not(:has(input)) {{
            display: none !important;
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input) > div:first-child {{
            display: none;
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input) {{
            display: flex !important; align-items: center; gap: 12px;
            padding: 8px 12px 8px 8px !important; border-radius: 14px;
            border: 1px solid transparent;
            font-size: 15px; font-weight: 700; color: {COLOR_TEXT};
            transition: background .22s ease, border-color .22s ease,
                        box-shadow .22s ease, transform .22s ease;
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input):hover {{
            background: #FFFFFF; border-color: {COLOR_BORDER};
            transform: translateX(3px);
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked) {{
            background: linear-gradient(135deg, {COLOR_ACCENT} 0%, #5E93B5 100%);
            color: #fff; border-color: transparent;
            box-shadow: 0 8px 20px -8px rgba(74,127,165,.7);
        }}
        /* Active text stays white: Streamlit nests the caption in its own
           containers that ignore inherited color. */
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked) p,
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked) div {{
            color: #fff !important;
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked)::after {{
            content: "›"; margin-left: auto; font-size: 20px; font-weight: 700;
            opacity: .85; line-height: 1;
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input)::before {{
            content: ""; width: 36px; height: 36px; flex: none;
            border-radius: 11px; background-color: {COLOR_ACCENT_PALE};
            background-size: 19px; background-repeat: no-repeat;
            background-position: center;
            transition: background-color .22s ease;
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input):nth-of-type(1)::before {{
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%234A7FA5' stroke-width='2.4' stroke-linecap='round'%3E%3Cpath d='M5 20v-6M11 20V5M17 20v-9'/%3E%3C/svg%3E");
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input):nth-of-type(2)::before {{
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%234A7FA5' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='4' width='18' height='16' rx='3'/%3E%3Cpath d='M7 10l3 3-3 3M12 16h5'/%3E%3C/svg%3E");
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input):nth-of-type(3)::before {{
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%234A7FA5' stroke-width='2.4' stroke-linecap='round'%3E%3Cpath d='M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01'/%3E%3C/svg%3E");
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked)::before {{
            background-color: rgba(255,255,255,.22);
        }}
        section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked)::before {{
            filter: brightness(0) invert(1);
        }}
        /* Sidebar lock: quiet ghost button. */
        section[data-testid="stSidebar"] div[data-testid="stButton"] button {{
            background: transparent; border: 1px solid {COLOR_BORDER};
            color: {COLOR_MUTED}; font-weight: 700; border-radius: 10px;
            transition: background .2s ease, color .2s ease;
        }}
        section[data-testid="stSidebar"] div[data-testid="stButton"] button:hover {{
            background: {COLOR_ACCENT_PALE}; color: {COLOR_TEXT};
            border-color: {COLOR_ACCENT_SOFT};
        }}
        /* Water-fill CPU bars: fresh nodes animate 0 -> value every tick. */
        .uf5-row {{ display: flex; align-items: flex-end; gap: 10px; }}
        .uf5-col {{ flex: 1; display: flex; flex-direction: column; align-items: center; }}
        .uf5-track {{ width: 100%; max-width: 56px; height: 200px;
                      background: {COLOR_ACCENT_PALE}; border-radius: 8px;
                      position: relative; overflow: hidden; }}
        .uf5-fill {{ position: absolute; bottom: 0; left: 0; right: 0;
                     background: linear-gradient(to top, {COLOR_ACCENT}, {COLOR_ACCENT_SOFT});
                     border-radius: 8px; transform-box: fill-box; transform-origin: bottom;
                     animation: uf5fill .9s cubic-bezier(.22,.8,.3,1) backwards;
                     transition: height .9s cubic-bezier(.22,.8,.3,1); }}
        .uf5-cap {{ margin-top: 6px; font-size: 11px; color: {COLOR_MUTED}; }}
        .uf5-val {{ font-size: 11px; font-weight: 700; color: {COLOR_TEXT}; margin-bottom: 2px; }}
        @keyframes uf5fill {{ from {{ transform: scaleY(0); }} to {{ transform: scaleY(1); }} }}
        /* Drop ghost: gray zone marking how much a bar fell, decrease-only. */
        .uf5-ghost {{ position: absolute; left: 0; right: 0;
                     background: #B9C4CC; opacity: .8; border-radius: 8px 8px 0 0;
                     transition: bottom .9s cubic-bezier(.22,.8,.3,1),
                                 height .9s cubic-bezier(.22,.8,.3,1),
                                 opacity 1.6s ease; }}
        /* Canvas tabs: larger labels, breathing room. */
        button[data-testid="stTab"] {{ font-size: 15px; font-weight: 600; }}
        /* Instrument-panel metrics: hero numbers, quiet labels. */
        div[data-testid="stMetricValue"] {{ font-size: 32px; font-weight: 700;
            font-variant-numeric: tabular-nums; }}
        div[data-testid="stMetricLabel"] {{ font-size: 12px; letter-spacing: .05em;
            text-transform: uppercase; color: {COLOR_MUTED}; }}
        div[data-testid="stMetric"] {{ background: {COLOR_PANEL};
            border: 1px solid {COLOR_BORDER}; border-radius: 12px; padding: 10px 14px;
            box-shadow: 0 1px 2px rgba(47,62,70,.05); }}
        @media (prefers-reduced-motion: reduce) {{
            .uf5-fill, .uf5-memseg, .uf5-ghost {{ animation: none !important;
                transition: none !important; }}
        }}
        /* Section transition: veil covers the render, then lifts. */
        .uf5-veil {{ position: fixed; inset: 0; z-index: 9999;
                     background: {COLOR_BG}; pointer-events: none;
                     display: flex; flex-direction: column; align-items: center;
                     justify-content: center; gap: 16px;
                     animation: uf5veil 1s ease forwards; }}
        .uf5-veil-line {{ width: 180px; height: 3px; border-radius: 999px;
                          background: {COLOR_ACCENT_PALE}; overflow: hidden;
                          position: relative; }}
        .uf5-veil-line::after {{ content: ""; position: absolute; inset: 0;
                                 width: 40%; border-radius: 999px;
                                 background: {COLOR_ACCENT};
                                 animation: uf5sweep .8s ease-in-out infinite; }}
        .uf5-veil-name {{ font-size: 14px; font-weight: 800;
                          letter-spacing: .34em; text-transform: uppercase;
                          color: {COLOR_MUTED}; padding-left: .34em;
                          animation: uf5veilname .8s ease backwards; }}
        @keyframes uf5veilname {{
            from {{ opacity: 0; transform: translateY(6px); }}
            to {{ opacity: 1; transform: none; }}
        }}
        .uf5-veil-line {{ width: 180px; height: 3px; border-radius: 999px;
                          background: {COLOR_ACCENT_PALE}; overflow: hidden;
                          position: relative; }}
        .uf5-veil-line::after {{ content: ""; position: absolute; inset: 0;
                                 width: 40%; border-radius: 999px;
                                 background: {COLOR_ACCENT};
                                 animation: uf5sweep .8s ease-in-out infinite; }}
        @keyframes uf5veil {{
            0% {{ opacity: 1; visibility: visible; }}
            62% {{ opacity: 1; visibility: visible; }}
            100% {{ opacity: 0; visibility: hidden; }}
        }}
        @keyframes uf5sweep {{
            0% {{ left: -40%; }} 100% {{ left: 100%; }}
        }}
        /* Skeleton shimmer: staged placeholders while data resolves. */
        .uf5-sk {{ border-radius: 8px; background: linear-gradient(
                     100deg, {COLOR_ACCENT_PALE} 40%, #FFFFFF 50%, {COLOR_ACCENT_PALE} 60%);
                   background-size: 200% 100%;
                   animation: uf5shimmer 1.2s ease-in-out infinite; }}
        @keyframes uf5shimmer {{
            0% {{ background-position: 180% 0; }} 100% {{ background-position: -80% 0; }}
        }}
        /* Content entrance: soft rise on every full render; main() may
           override the animation-name with a directional slide. */
        div[data-testid="stMainBlock"] {{ animation: uf5enter .5s ease backwards; }}
        @keyframes uf5enter {{ from {{ opacity: 0; transform: translateY(10px); }}
                              to {{ opacity: 1; transform: none; }} }}
        @keyframes uf5enterR {{ from {{ opacity: 0; transform: translateX(30px); }}
                               to {{ opacity: 1; transform: none; }} }}
        @keyframes uf5enterL {{ from {{ opacity: 0; transform: translateX(-30px); }}
                               to {{ opacity: 1; transform: none; }} }}
        @media (prefers-reduced-motion: reduce) {{
            .uf5-veil, .uf5-veil-name,
            div[data-testid="stMainBlock"] {{ animation: none !important; }}
        }}
        /* Memory stacked bar: one 0..max track, animated segment widths. */
        .uf5-memtrack {{ display: flex; height: 44px; background: {COLOR_ACCENT_PALE};
                         border-radius: 999px; overflow: hidden; }}
        .uf5-memseg {{ height: 100%; transition: width .9s cubic-bezier(.22,.8,.3,1);
                       animation: uf5fillx .9s cubic-bezier(.22,.8,.3,1) backwards;
                       transform-origin: left; }}
        @keyframes uf5fillx {{ from {{ transform: scaleX(0); }} to {{ transform: scaleX(1); }} }}
        .uf5-legend {{ display: flex; gap: 16px; margin-top: 8px; font-size: 13px;
                       color: {COLOR_MUTED}; }}
        .uf5-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
                    margin-right: 6px; }}
        /* Geo card: own chrome-free card, flag stretched across it. */
        .uf5-geocard {{ position: relative; overflow: hidden;
                        background: {COLOR_PANEL}; border: 1px solid {COLOR_BORDER};
                        border-radius: 14px; padding: 16px 18px; margin-bottom: 14px;
                        box-shadow: 0 1px 2px rgba(47,62,70,.05), 0 8px 24px -12px rgba(47,62,70,.18); }}
        .uf5-geocard-bg {{ position: absolute; top: 0; right: 0; display: block;
                           height: 100%; width: auto; max-width: 62%;
                           object-fit: contain; object-position: right center;
                           opacity: .28; pointer-events: none; user-select: none;
                           -webkit-mask-image: linear-gradient(to right, transparent 0%, black 45%);
                           mask-image: linear-gradient(to right, transparent 0%, black 45%); }}
        .uf5-geo-fg {{ position: relative; }}
        .uf5-geo-ip {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
        .uf5-flag {{ width: 30px; height: 22px; object-fit: cover; border-radius: 5px;
                     box-shadow: 0 1px 4px rgba(0,0,0,.25); }}
        /* Check rows: flag + name/sub + latency badge, geocard-adjacent. */
        .uf5-checkrow {{ display: flex; align-items: center; gap: 12px;
                         background: {COLOR_PANEL}; border: 1px solid {COLOR_BORDER};
                         border-radius: 12px; padding: 10px 14px; margin-bottom: 8px; }}
        .uf5-checkrow-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; }}
        .uf5-checkrow-main b {{ font-size: 14px; white-space: nowrap; overflow: hidden;
                                text-overflow: ellipsis; }}
        .uf5-checkrow-main .uf5-muted {{ font-size: 12px; white-space: nowrap; overflow: hidden;
                                         text-overflow: ellipsis; }}
        .uf5-checkrow .uf5-badge {{ flex: none; white-space: nowrap; }}
        </style>""",
        unsafe_allow_html=True,
    )


def card(title: str) -> None:
    import streamlit as st

    st.markdown(f'<div class="uf5-card"><div class="uf5-title">{title}</div>', unsafe_allow_html=True)


def card_end() -> None:
    import streamlit as st

    st.markdown("</div></div>", unsafe_allow_html=True)


def badge(text: str, color: str) -> str:
    return f'<span class="uf5-badge" style="background:{color}">{text}</span>'


def load_icon(name: str, size_px: int = 44, color: str = COLOR_ACCENT) -> str:
    """Bundled SVG icon, normalized to one size with accent recolor."""
    svg = ICON_SVGS.get(name, "")
    if not svg:
        return ""
    svg = re.sub(r'fill="#21\w{4}"', f'fill="{color}"', svg)
    svg = re.sub(r'fill="#23\w{4}"', f'fill="{color}"', svg)
    svg = re.sub(r'width="\d+"', f'width="{size_px}"', svg, count=1)
    svg = re.sub(r'height="\d+"', f'height="{size_px}"', svg, count=1)
    if 'width="' not in svg:
        svg = svg.replace("<svg", f'<svg width="{size_px}" height="{size_px}"', 1)
    return svg




def find_component_dir(name: str) -> str:
    """Locate components/<name>/index.html under this tree or cwd layouts.

    Walks up from this file so the lookup survives future moves; cwd
    layouts cover the deployed entry points. Empty when missing.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    cur = here
    for _ in range(4):
        cands.append(os.path.join(cur, "components", name))
        cur = os.path.dirname(cur)
    cands += [
        os.path.join(os.getcwd(), "uf5vmjt", "components", name),
        os.path.join(os.getcwd(), "components", name),
    ]
    seen = set()
    for cand in cands:
        cand = os.path.normpath(cand)
        if cand in seen:
            continue
        seen.add(cand)
        if os.path.isfile(os.path.join(cand, "index.html")):
            return cand
    return ""
