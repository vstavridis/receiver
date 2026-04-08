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


def get_machine() -> str:
    qp = st.query_params
    machine = qp.get("machine", DEFAULT_MACHINE)
    if isinstance(machine, list):
        machine = machine[0] if machine else DEFAULT_MACHINE
    machine = str(machine).strip().upper() or DEFAULT_MACHINE
    return machine


def get_refresh_seconds() -> int:
    qp = st.query_params
    raw = qp.get("refresh", DEFAULT_REFRESH_SECONDS)
    if isinstance(raw, list):
        raw = raw[0] if raw else DEFAULT_REFRESH_SECONDS
    try:
        return max(2, int(raw))
    except Exception:
        return DEFAULT_REFRESH_SECONDS


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

manifest_url = st.secrets.get("MANIFEST_URL", "").strip()
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
      .status-card {
        border: 1px solid rgba(128,128,128,.25);
        border-radius: 12px;
        padding: 0.8rem 1rem;
        margin-bottom: 0.6rem;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if not manifest_url:
    st.error("Missing MANIFEST_URL in Streamlit secrets.")
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

job, job_error = resolve_job(manifest or {}, machine)

if job_error:
    st.title(APP_TITLE)
    st.markdown(f"### Receiver: {machine}")
    st.warning(job_error)
    st.markdown(
        f"<div class='small-muted'>Checked at {now_utc_iso()} · refresh every {refresh_seconds}s</div>",
        unsafe_allow_html=True,
    )
    time.sleep(refresh_seconds)
    st.rerun()

pdf_url = str(job["pdf_url"]).strip()
version = job.get("version") or manifest.get("version") or int(time.time())
job_code = str(job.get("job_code", "")).strip()
sent_at = str(job.get("sent_at", "")).strip()
message = str(job.get("message", "")).strip()

info_left, info_right = st.columns([3, 1])
with info_left:
    st.markdown(f"**Receiver:** {machine}")
    if job_code:
        st.markdown(f"**Job:** {job_code}")
    if message:
        st.markdown(message)
with info_right:
    st.markdown(f"**Version:** {version}")
    if sent_at:
        st.markdown(f"**Sent:** {sent_at}")

render_pdf(pdf_url, version)

time.sleep(refresh_seconds)
st.rerun()
