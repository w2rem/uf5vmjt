"""uf5vmjt.lib.core.config — shared constants: palette, icons, limits, paths, env keys."""
from __future__ import annotations
import os



COLOR_BG = "#FFFFFF"

COLOR_PANEL = "#F4F6F8"

COLOR_BORDER = "#E2E7EB"

COLOR_TEXT = "#2F3E46"

COLOR_MUTED = "#6C7A86"

COLOR_ACCENT = "#4A7FA5"

COLOR_ACCENT_SOFT = "#A8C3D1"

COLOR_ACCENT_PALE = "#E8EDEF"

COLOR_OK = "#5A9E6F"

COLOR_WARN = "#C9A227"

COLOR_ERR = "#C26D6D"

COLOR_MEM_USED = "#4A7FA5"  # steel blue — application memory

COLOR_MEM_CACHE = "#A8C3D1"  # pale steel — page cache / reclaimable

COLOR_MEM_FREE = "#E8EDEF"  # pale gray — free


ICON_PYTHON = '''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><g fill="#211f1f" fill-rule="evenodd"><path d="M14.31.18l.9.2.73.26.59.3.45.32.34.34.25.34.16.33.1.3.04.26.02.2-.01.13V8.5l-.05.63-.13.55-.21.46-.26.38-.3.31-.33.25-.35.19-.35.14-.33.1-.3.07-.26.04-.21.02H8.83l-.69.05-.59.14-.5.22-.41.27-.33.32-.27.35-.2.36-.15.37-.1.35-.07.32-.04.27-.02.21v3.06H3.23l-.21-.03-.28-.07-.32-.12-.35-.18-.36-.26-.36-.36-.35-.46-.32-.59-.28-.73-.21-.88-.14-1.05L0 11.97l.06-1.22.16-1.04.24-.87.32-.71.36-.57.4-.44.42-.33.42-.24.4-.16.36-.1.32-.05.24-.01h.16l.06.01h8.16v-.83H6.24l-.01-2.75-.02-.37.05-.34.11-.31.17-.28.25-.26.31-.23.38-.2.44-.18.51-.15.58-.12.64-.1.71-.06.77-.04.84-.02 1.27.05 1.07.13zm-6.3 1.98l-.23.33-.08.41.08.41.23.34.33.22.41.09.41-.09.33-.22.23-.34.08-.41-.08-.41-.23-.33-.33-.22-.41-.09-.41.09-.33.22zM21.1 6.11l.28.06.32.12.35.18.36.27.36.35.35.47.32.59.28.73.21.88.14 1.04.05 1.23-.06 1.23-.16 1.04-.24.86-.32.71-.36.57-.4.45-.42.33-.42.24-.4.16-.36.09-.32.05-.24.02-.16-.01h-8.22v.82h5.84l.01 2.76.02.36-.05.34-.11.31-.17.29-.25.25-.31.24-.38.2-.44.17-.51.15-.58.13-.64.09-.71.07-.77.04-.84.01-1.27-.04-1.07-.14-.9-.2-.73-.25-.59-.3-.45-.33-.34-.34-.25-.34-.16-.33-.1-.3-.04-.25-.02-.2.01-.13v-5.34l.05-.64.13-.54.21-.46.26-.38.3-.32.33-.24.35-.2.35-.14.33-.1.3-.06.26-.04.21-.02.13-.01h5.84l.69-.05.59-.14.5-.21.41-.28.33-.32.27-.35.2-.36.15-.36.1-.35.07-.32.04-.28.02-.21V6.07h2.09l.14.01.21.03zm-6.47 14.25l-.23.33-.08.41.08.41.23.33.33.23.41.08.41-.08.33-.23.23-.33.08-.41-.08-.41-.23-.33-.33-.23-.41-.08-.41.08-.33.23z"/></g></svg>'''

ICON_TAILSCALE = '''<svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">  <path d="M3.5 17.5C5.43299 17.5 6.99999 15.933 6.99999 14C6.99999 12.067 5.43299 10.5 3.5 10.5C1.567 10.5 0 12.067 0 14C0 15.933 1.567 17.5 3.5 17.5Z" fill="#232222"/>  <path d="M14 17.5C15.933 17.5 17.5 15.933 17.5 14C17.5 12.067 15.933 10.5 14 10.5C12.067 10.5 10.5 12.067 10.5 14C10.5 15.933 12.067 17.5 14 17.5Z" fill="#232222"/>  <path d="M14 28C15.933 28 17.5 26.433 17.5 24.5C17.5 22.567 15.933 21 14 21C12.067 21 10.5 22.567 10.5 24.5C10.5 26.433 12.067 28 14 28Z" fill="#232222"/>  <path d="M24.5 17.5C26.433 17.5 28 15.933 28 14C28 12.067 26.433 10.5 24.5 10.5C22.567 10.5 21 12.067 21 14C21 15.933 22.567 17.5 24.5 17.5Z" fill="#232222"/>  <g opacity="0.4">    <path d="M3.5 28C5.43299 28 6.99999 26.433 6.99999 24.5C6.99999 22.567 5.43299 21 3.5 21C1.567 21 0 22.567 0 24.5C0 26.433 1.567 28 3.5 28Z" fill="#232222"/>    <path d="M24.5 28C26.433 28 28 26.433 28 24.5C28 22.567 26.433 21 24.5 21C22.567 21 21 22.567 21 24.5C21 26.433 22.567 28 24.5 28Z" fill="#232222"/>    <path d="M3.5 6.99999C5.43299 6.99999 6.99999 5.43299 6.99999 3.5C6.99999 1.567 5.43299 0 3.5 0C1.567 0 0 1.567 0 3.5C0 5.43299 1.567 6.99999 3.5 6.99999Z" fill="#232222"/>    <path d="M14 6.99999C15.933 6.99999 17.5 5.43299 17.5 3.5C17.5 1.567 15.933 0 14 0C12.067 0 10.5 1.567 10.5 3.5C10.5 5.43299 12.067 6.99999 14 6.99999Z" fill="#232222"/>    <path d="M24.5 6.99999C26.433 6.99999 28 5.43299 28 3.5C28 1.567 26.433 0 24.5 0C22.567 0 21 1.567 21 3.5C21 5.43299 22.567 6.99999 24.5 6.99999Z" fill="#232222"/>  </g></svg>'''



# Single-line on purpose: blank lines split markdown HTML blocks and
# shatter the svg (faces render, tape/paths go inert, badges misplace).
ICON_SAGERNET = '''<svg width="44" height="44" xmlns="http://www.w3.org/2000/svg" viewBox="150 90 725 815">  <defs>    <linearGradient id="bg25" x1="0" y1="0" x2="0" y2="1">      <stop offset="0" stop-color="#242F37"/>      <stop offset="1" stop-color="#0D1317"/>    </linearGradient>    <radialGradient id="spot25" cx="0.5" cy="0.5" r="0.5">      <stop offset="0" stop-color="#46565F" stop-opacity="0.45"/>      <stop offset="1" stop-color="#46565F" stop-opacity="0"/>    </radialGradient>    <filter id="soft25" x="-40%" y="-40%" width="180%" height="180%">      <feGaussianBlur stdDeviation="18"/>    </filter>    <linearGradient id="top25" gradientUnits="userSpaceOnUse" x1="330" y1="320" x2="700" y2="490">      <stop offset="0" stop-color="#445863"/>      <stop offset="1" stop-color="#394C57"/>    </linearGradient>    <linearGradient id="left25" gradientUnits="userSpaceOnUse" x1="269.5" y1="480" x2="512" y2="720">      <stop offset="0" stop-color="#26323A"/>      <stop offset="1" stop-color="#1F2A31"/>    </linearGradient>    <linearGradient id="right25" gradientUnits="userSpaceOnUse" x1="512" y1="650" x2="754.5" y2="500">      <stop offset="0" stop-color="#30404A"/>      <stop offset="1" stop-color="#374854"/>    </linearGradient>    <filter id="grain25" x="0" y="0" width="280" height="280" filterUnits="userSpaceOnUse">      <feTurbulence type="fractalNoise" baseFrequency="0.22" numOctaves="4" seed="17" result="n"/>      <feColorMatrix in="n" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0.45 0 0 0 -0.1"/>    </filter>    <filter id="grainD25" x="0" y="0" width="280" height="280" filterUnits="userSpaceOnUse">      <feTurbulence type="fractalNoise" baseFrequency="0.28" numOctaves="4" seed="41" result="n"/>      <feColorMatrix in="n" type="matrix" values="0 0 0 0 0.02  0 0 0 0 0.05  0 0 0 0 0.07  0.45 0 0 0 -0.1"/>    </filter>    <clipPath id="clipTopD"><path d="M512 262 754.5 402 512 542 269.5 402Z"/></clipPath>    <clipPath id="clipLeftD"><path d="M269.5 402 512 542 512 812 269.5 672Z"/></clipPath>    <clipPath id="clipRightD"><path d="M512 542 754.5 402 754.5 672 512 812Z"/></clipPath>  </defs>  <g transform="translate(-225.28 -273.28) scale(1.44)"><!-- deep cardboard faces -->  <path d="M512 262 754.5 402 512 542 269.5 402Z" fill="url(#top25)"/>  <path d="M269.5 402 512 542 512 812 269.5 672Z" fill="url(#left25)"/>  <path d="M512 542 754.5 402 754.5 672 512 812Z" fill="url(#right25)"/>  <!-- paper grain, foreshortened per face -->  <g clip-path="url(#clipTopD)"><g transform="matrix(0.866 0.5 0.866 -0.5 269.5 402)">    <rect width="280" height="280" filter="url(#grain25)" opacity="0.30"/>    <rect width="280" height="280" filter="url(#grainD25)" opacity="0.38"/>  </g></g>  <g clip-path="url(#clipLeftD)"><g transform="matrix(0.866 0.5 0 0.96428 269.5 402)">    <rect width="280" height="280" filter="url(#grain25)" opacity="0.20"/>    <rect width="280" height="280" filter="url(#grainD25)" opacity="0.34"/>  </g></g>  <g clip-path="url(#clipRightD)"><g transform="matrix(0.866 -0.5 0 0.96428 512 542)">    <rect width="280" height="280" filter="url(#grain25)" opacity="0.25"/>    <rect width="280" height="280" filter="url(#grainD25)" opacity="0.34"/>  </g></g>  <!-- lid flap seam: two lid halves, paper-edge catchlight -->  <path d="M390.75 472 633.25 332" stroke="#141E24" stroke-width="4" fill="none" opacity="0.9"/>  <path d="M390.75 472 633.25 332" stroke="#6E8794" stroke-width="2" fill="none" opacity="0.7" transform="translate(0 -3)"/>  <!-- soft ambient occlusion at junctions -->  <path d="M269.5 402 512 542" stroke="#0B141A" stroke-width="10" opacity="0.28" filter="url(#soft25)" fill="none"/>  <path d="M512 542 754.5 402" stroke="#0B141A" stroke-width="10" opacity="0.22" filter="url(#soft25)" fill="none"/>  <path d="M512 542 512 812" stroke="#060D11" stroke-width="9" opacity="0.30" filter="url(#soft25)" fill="none"/>  <!-- paper-edge highlights on the top edges -->  <path d="M512 262 754.5 402" stroke="#66808D" stroke-width="2.5" fill="none"/>  <path d="M512 262 269.5 402" stroke="#5A737F" stroke-width="2.5" fill="none"/>  <path d="M269.5 402 512 542 754.5 402" stroke="#4E6773" stroke-width="2" fill="none" opacity="0.9"/>  <path d="M512 542 512 812" stroke="#445963" stroke-width="2" fill="none" opacity="0.9"/>  <!-- original two-tone tape, aligned tails -->  <path d="M356.8 351.6 390.75 332 633.25 472 599.3 491.6Z" fill="#99AAB5"/>  <path d="M390.75 332 424.7 312.4 667.2 452.4 633.25 472Z" fill="#E1E8ED"/>  <path d="M599.3 491.6 633.25 472 633.25 592 599.3 611.6Z" fill="#8296A1"/>  <path d="M633.25 472 667.2 452.4 667.2 572.4 633.25 592Z" fill="#CCD6DD"/>  <!-- tape soft shadow onto the paper -->  <path d="M360 358 602.5 498" stroke="#000000" opacity="0.25" stroke-width="7" filter="url(#soft25)" fill="none"/>  </g></svg>'''


ICON_SVGS = {"python": ICON_PYTHON, "tailscale": ICON_TAILSCALE,
             "sagernet": ICON_SAGERNET}

GEO_URL = "https://api.ip.sb/geoip"

GEO_TTL_SEC = 300

CLUSTER_TTL_SEC = 600

GO_LOG_PATH = os.environ.get("UF5VMJT_GO_LOG", "/tmp/uf5vmjt-go.log").strip()

GO_LOG_TAIL_BYTES = 32768

DISK_TOP_TTL_SEC = 120

DISK_SCAN_BUDGET_SEC = 6.0

_PROCESS_CACHE: dict = {}

_PSEUDO_FS = {"proc", "sysfs", "devpts", "cgroup", "cgroup2", "tmpfs", "devtmpfs",
              "overlay", "shm", "mqueue", "debugfs", "tracefs", "fusectl", "configfs"}

FLAG_CDN = "https://cdn.jsdelivr.net/gh/hjnilsson/country-flags/svg"


BIN_TAILSCALE = "/tmp/bin/tailscale/tailscale"

SB_BIN_DIR = "/tmp/bin/sagernet"

WORKER_PORT = int(os.environ.get("WORKER_PORT", "6549") or 6549)

WORKER_BIN_ENV = "UF5VMJT_WORKER_BIN"

WORKER_ENV_KEYS = ("PG_DATABASE_URL", "STREAMLIT_SESSION_TOKEN", "STREAMLIT_SESSION",
                   "APP_HOST", "APP_URL", "UP_EVERY", "WORKER_PORT",
                   "WORKER_ID", "WORKER_DB_PATH", "WORKER_SNAPSHOT_MIN",
                   "WORKER_TAILSCALE_ENABLED",
                   "LAYERBASE_DATABASE_URL",
                   "TS_VERSION", "TS_KEY", "TS_CLIENT_ID", "TS_CLIENT_SECRET",
                   "TS_TAILNET", "TS_TAGS", "TS_HOSTNAME_BASE", "TS_HOST", "TS_SERVE_PORT",
                   "TS_EPHEMERAL", "TS_EXTRA_ARGS",
                   "SINGBOX_VERSION", "SINGBOX_BIN_DIR", "SINGBOX_GEOIP_URL",
                   "SESSION_TTL_MIN", "GOMAXPROCS", "GOMEMLIMIT",
                   "CHECK_PORT_MIN", "CHECK_PORT_MAX",
                   "TUNECFG_DIR", "TUNECFG_PROFILE", "TUNECFG_PARALLEL", "TUNECFG_GEO_PARALLEL",
                   "TUNECFG_DELAY_MS", "TUNECFG_MAX_SHARD", "TUNECFG_MAX_LINES",
                   "TUNECFG_MAX_RUNS", "TUNECFG_GOMAXPROCS", "TUNECFG_SAMPLE_SEC",
                   "MAXMIND_CITY_DB", "MAXMIND_ASN_DB",
                   "IPAPI_IS_KEY", "IPINFO_TOKEN", "IPQS_API_KEY", "ABUSEIPDB_API_KEY")

SHELL_TIMEOUT_SEC = 15

SHELL_HISTORY_LIMIT = 20

SESSION_COOKIE = "uf5sess"

SESSION_TTL_MIN_DEFAULT = 30

