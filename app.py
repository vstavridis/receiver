import time
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components

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

def get_sound_enabled():
    qp = st.query_params
    val = str(qp.get("sound", "1")).strip().lower()
    return val not in {"0", "false", "no", "off"}

def fetch_manifest(url: str) -> dict:
    r = requests.get(url, timeout=15, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.json()

def render_missing_config():
    st.title("Slitting Receiver")
    st.error("Missing manifest URL.")
    st.markdown("""
Add one of these:

**Option 1 — Streamlit secret**
```toml
MANIFEST_URL = "https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json"
```

**Option 2 — URL parameter**
```text
?machine=M1&manifest_url=https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/manifest.json
```
""")
    st.stop()

manifest_url = get_manifest_url()
machine_id = get_machine_id()
sound_enabled = get_sound_enabled()

if not manifest_url:
    render_missing_config()

refresh_seconds = st.sidebar.number_input("Refresh every (seconds)", min_value=1, max_value=60, value=DEFAULT_REFRESH_SECONDS)
fullscreen_mode = st.sidebar.checkbox("Fullscreen display mode", value=True)
show_sidebar = st.sidebar.checkbox("Show sidebar", value=False)
st.sidebar.write(f"Machine: **{machine_id}**")
st.sidebar.write(f"Manifest URL: `{manifest_url}`")
st.sidebar.write(f"Sound alert: **{'On' if sound_enabled else 'Off'}**")

try:
    manifest = fetch_manifest(manifest_url)
except Exception as e:
    st.title("Slitting Receiver")
    st.error(f"Could not load manifest: {e}")
    st.stop()

displays = manifest.get("displays", {})
display = displays.get(machine_id)

if fullscreen_mode:
    st.markdown("""
    <style>
    header[data-testid="stHeader"] {display:none !important;}
    div[data-testid="stToolbar"] {display:none !important;}
    section[data-testid="stSidebar"] {display:none !important;}
    [data-testid="collapsedControl"] {display:none !important;}
    .block-container {padding-top:0.4rem !important; padding-bottom:0 !important; max-width:100% !important;}
    iframe {border:none !important;}
    </style>
    """, unsafe_allow_html=True)

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

if not show_sidebar:
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

previous_version = st.session_state.get("last_seen_version")
play_sound = sound_enabled and previous_version not in (None, version)
st.session_state["last_seen_version"] = version

if play_sound:
    components.html("""
    <audio autoplay>
      <source src="https://actions.google.com/sounds/v1/alarms/beep_short.ogg" type="audio/ogg">
    </audio>
    """, height=0)

viewer_height = 980 if fullscreen_mode else 850
components.iframe(pdf_viewer_url, height=viewer_height, scrolling=True)

if not fullscreen_mode:
    st.link_button("Open PDF in browser", pdf_viewer_url)

components.html(f"""
<script>
setTimeout(function(){{
    window.location.reload();
}}, {int(refresh_seconds * 1000)});
</script>
""", height=0)
