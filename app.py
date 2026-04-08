import time
import json
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import streamlit as st

st.set_page_config(page_title="Slitting Receiver", layout="wide")

DEFAULT_REFRESH_SECONDS = 3


def get_manifest_url():
    qp = st.query_params
    url_from_qp = qp.get("manifest_url")
    if url_from_qp:
        return str(url_from_qp)
    try:
        return st.secrets["MANIFEST_URL"]
    except Exception:
        return None


def get_machine_id():
    qp = st.query_params
    machine = qp.get("machine", "M1")
    return str(machine)


def fetch_manifest(url: str) -> dict:
    r = requests.get(url, timeout=15, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.json()


def parse_ts(value):
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except Exception:
        return None


def render_missing_config():
    st.title("Slitting Receiver")
    st.error("Missing manifest URL.")
    st.markdown(
        """
Add one of these:

**Option 1 — Streamlit secret**
```toml
MANIFEST_URL = "https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json"
```

**Option 2 — URL parameter**
```text
?machine=M1&manifest_url=https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json
```
"""
    )
    st.stop()


manifest_url = get_manifest_url()
machine_id = get_machine_id()

if not manifest_url:
    render_missing_config()

refresh_seconds = st.sidebar.number_input("Refresh every (seconds)", min_value=1, max_value=60, value=DEFAULT_REFRESH_SECONDS)
st.sidebar.write(f"Machine: **{machine_id}**")
st.sidebar.write(f"Manifest URL: `{manifest_url}`")

placeholder = st.empty()

try:
    manifest = fetch_manifest(manifest_url)
except Exception as e:
    st.title("Slitting Receiver")
    st.error(f"Could not load manifest: {e}")
    st.stop()

displays = manifest.get("displays", {})
display = displays.get(machine_id)

st.title(f"Receiver — {machine_id}")

if not display:
    st.warning(f"No display entry found in manifest for machine '{machine_id}'.")
    st.json(manifest)
    st.stop()

pdf_url = display.get("pdf_url")
job_code = display.get("job_code", "")
version = display.get("version", "")
sent_at = display.get("sent_at", "")
note = display.get("note", "")
filename = display.get("filename", "document.pdf")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Machine", machine_id)
c2.metric("Version", str(version))
c3.metric("Job", str(job_code))
c4.metric("Sent at", sent_at or "-")

if note:
    st.info(note)

if not pdf_url:
    st.warning("This machine currently has no PDF URL assigned.")
    st.stop()

pdf_viewer_url = pdf_url
if "?" in pdf_viewer_url:
    pdf_viewer_url = f"{pdf_viewer_url}&v={quote(str(version))}"
else:
    pdf_viewer_url = f"{pdf_viewer_url}?v={quote(str(version))}"

st.components.v1.iframe(pdf_viewer_url, height=900, scrolling=True)
st.link_button("Open PDF in browser", pdf_viewer_url)

st.caption("This page auto-refreshes.")
time.sleep(refresh_seconds)
st.rerun()
