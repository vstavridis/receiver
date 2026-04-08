import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
import streamlit as st
import streamlit.components.v1 as components

APP_TITLE = "Slitting Receiver"
DEFAULT_REFRESH_SECONDS = 3
DEFAULT_MACHINE = "M1"
REQUEST_TIMEOUT = 10


def get_query_param(name: str, default):
    qp = st.query_params
    value = qp.get(name, default)
    if isinstance(value, list):
        value = value[0] if value else default
    return value


def get_machine() -> str:
    machine = get_query_param("machine", DEFAULT_MACHINE)
    machine = str(machine).strip().upper() or DEFAULT_MACHINE
    return machine


def get_refresh_seconds() -> int:
    raw = get_query_param("refresh", DEFAULT_REFRESH_SECONDS)
    try:
        return max(2, int(raw))
    except Exception:
        return DEFAULT_REFRESH_SECONDS


def get_manifest_url() -> str:
    from_secret = str(st.secrets.get("MANIFEST_URL", "")).strip()
    if from_secret:
        return from_secret
    from_query = str(get_query_param("manifest_url", "")).strip()
    return from_query


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cache_busted(url: str, version: str | int | None) -> str:
    if not version:
        version = int(time.time())
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({'v': version})}"


def fetch_manifest(manifest_url: str) -> tuple[dict | None, str | None]:
    try:
        response = requests.get(manifest_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, f"Network error while reading manifest: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"Manifest is not valid JSON: {exc}"


def resolve_job(manifest: dict, machine: str) -> tuple[dict | None, str | None]:
    displays = manifest.get("displays")
    if not isinstance(displays, dict):
        return None, "Manifest does not contain a valid 'displays' object."

    job = displays.get(machine)
    if not isinstance(job, dict):
        return None, f"No job found for machine '{machine}'."

    pdf_url = str(job.get("pdf_url", "")).strip()
    if not pdf_url:
        return None, f"Machine '{machine}' has no pdf_url in manifest."

    return job, None


def render_pdf(pdf_url: str, version: str | int | None) -> None:
    safe_url = cache_busted(pdf_url, version)
    html = f"""
    <style>
      html, body {{
        margin: 0;
        padding: 0;
        background: #111;
        overflow: hidden;
      }}
      .frame-wrap {{
        position: fixed;
        inset: 0;
      }}
      iframe {{
        width: 100vw;
        height: 100vh;
        border: none;
        background: white;
      }}
    </style>
    <div class="frame-wrap">
      <iframe src="{safe_url}"></iframe>
    </div>
    """
    components.html(html, height=1080, scrolling=False)


st.set_page_config(page_title=APP_TITLE, layout="wide")

manifest_url = get_manifest_url()
machine = get_machine()
refresh_seconds = get_refresh_seconds()

st.markdown(
    """
    <style>
      [data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {
        visibility: hidden;
        height: 0;
      }
      .block-container {
        padding: 0.2rem 0.4rem 0.4rem 0.4rem;
        max-width: 100%;
      }
      .small-muted {
        color: #9aa0a6;
        font-size: 0.85rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if not manifest_url:
    st.title(APP_TITLE)
    st.error("Missing manifest URL.")
    st.markdown(
        """
Add one of these:

**Option 1 — Streamlit secret**
```toml
MANIFEST_URL = "https://your-domain.example.com/receiver-manifest.json"
```

**Option 2 — URL parameter**
```text
?machine=M1&manifest_url=https://your-domain.example.com/receiver-manifest.json
```
        """
    )
    st.stop()

manifest, manifest_error = fetch_manifest(manifest_url)

if manifest_error:
    st.title(APP_TITLE)
    st.markdown(f"### Receiver: {machine}")
    st.error(manifest_error)
    st.markdown(
        f"<div class='small-muted'>Checked at {now_utc_iso()} · refresh every {refresh_seconds}s</div>",
        unsafe_allow_html=True,
    )
    time.sleep(refresh_seconds)
    st.rerun()

job, job_error = resolve_job(manifest, machine)

if job_error:
    st.title(APP_TITLE)
    st.markdown(f"### Receiver: {machine}")
    st.warning(job_error)
    st.markdown(
        f"<div class='small-muted'>Manifest OK · checked at {now_utc_iso()} · refresh every {refresh_seconds}s</div>",
        unsafe_allow_html=True,
    )
    time.sleep(refresh_seconds)
    st.rerun()

pdf_url = str(job.get("pdf_url")).strip()
version = job.get("version") or manifest.get("version")
render_pdf(pdf_url, version)

col1, col2, col3, col4 = st.columns([1.1, 1.2, 1.4, 2.3])
with col1:
    st.caption(f"Machine: {machine}")
with col2:
    st.caption(f"Job: {job.get('job_code', '-')}")
with col3:
    st.caption(f"Version: {version}")
with col4:
    st.caption(f"Sent at: {job.get('sent_at', '-')}")
time.sleep(refresh_seconds)
st.rerun()
