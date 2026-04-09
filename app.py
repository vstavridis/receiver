import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Slitting Queue Receiver", layout="wide")

DEFAULT_REFRESH_SECONDS = 3


def get_queue_url():
    qp = st.query_params
    url_from_qp = qp.get("queue_url")
    if url_from_qp:
        return str(url_from_qp)
    try:
        return st.secrets["QUEUE_URL"]
    except Exception:
        return None


def get_machine_id():
    qp = st.query_params
    return str(qp.get("machine", "M1"))


def sound_enabled():
    qp = st.query_params
    return str(qp.get("sound", "1")).lower() not in {"0", "false", "no", "off"}


def fullscreen_enabled():
    qp = st.query_params
    return str(qp.get("fullscreen", "1")).lower() not in {"0", "false", "no", "off"}


def fetch_queue(url: str):
    r = requests.get(url, timeout=20, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.json()


queue_url = get_queue_url()
machine_id = get_machine_id()
play_sound_flag = sound_enabled()
fullscreen = fullscreen_enabled()

if not queue_url:
    st.title("Slitting Queue Receiver")
    st.error("Missing queue URL.")
    st.markdown(
        '''
Add one of these:

**Option 1 — Streamlit secret**
```toml
QUEUE_URL = "https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/queue.json"
```

**Option 2 — URL parameter**
```text
?machine=M1&queue_url=https://raw.githubusercontent.com/YOUR-USER/YOUR-REPO/main/queue.json
```
'''
    )
    st.stop()

if fullscreen:
    st.markdown(
        '''
    <style>
    header[data-testid="stHeader"] {display:none !important;}
    div[data-testid="stToolbar"] {display:none !important;}
    section[data-testid="stSidebar"] {display:none !important;}
    [data-testid="collapsedControl"] {display:none !important;}
    .block-container {padding-top:0.6rem !important; max-width:100% !important;}
    .big-card {
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 16px;
        padding: 16px 18px;
        background: rgba(255,255,255,0.04);
        margin-bottom: 14px;
    }
    </style>
    ''',
        unsafe_allow_html=True,
    )

st.title(f"Receiver Queue — {machine_id}")

refresh_seconds = st.sidebar.number_input("Refresh every (seconds)", min_value=1, max_value=60, value=DEFAULT_REFRESH_SECONDS)
st.sidebar.write(f"Machine: **{machine_id}**")
st.sidebar.write(f"Queue URL: `{queue_url}`")
st.sidebar.write(f"Sound: **{'On' if play_sound_flag else 'Off'}**")

try:
    queue = fetch_queue(queue_url)
except Exception as e:
    st.error(f"Could not load queue: {e}")
    st.stop()

jobs = queue.get("machines", {}).get(machine_id, [])
pending_jobs = [j for j in jobs if j.get("status") in {"pending", "acknowledged"}]
current_job = pending_jobs[0] if pending_jobs else None

queue_version = queue.get("version", 0)
current_queue_key = f"{machine_id}:{queue_version}:{len(pending_jobs)}:{(current_job or {}).get('queue_id', '')}"

previous_queue_key = st.session_state.get("last_queue_key")
should_beep = play_sound_flag and previous_queue_key not in (None, current_queue_key)
st.session_state["last_queue_key"] = current_queue_key

if should_beep:
    components.html(
        '''
    <script>
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = 'sine';
      o.frequency.value = 880;
      o.connect(g);
      g.connect(ctx.destination);
      g.gain.setValueAtTime(0.001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.01);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
      o.start();
      o.stop(ctx.currentTime + 0.35);
    } catch(e) {}
    </script>
    ''',
        height=0,
    )

c1, c2, c3, c4 = st.columns(4)
c1.metric("Queue Version", str(queue_version))
c2.metric("Jobs in Queue", str(len(pending_jobs)))
c3.metric("Machine", machine_id)
c4.metric("Sound", "On" if play_sound_flag else "Off")

if not current_job:
    st.success("No pending jobs.")
else:
    payload = current_job.get("payload", {})
    summary = payload.get("summary", {})

    st.markdown('<div class="big-card">', unsafe_allow_html=True)
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Job Code", payload.get("job_code", "-"))
    t2.metric("Status", current_job.get("status", "-"))
    t3.metric("Coil", payload.get("coil_number", "-"))
    t4.metric("Cut Plan", f"{payload.get('cut_plan', 1)}x")
    st.markdown('</div>', unsafe_allow_html=True)

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Thickness", str(payload.get("thickness", "-")))
    a2.metric("Material", str(payload.get("material", "-")))
    a3.metric("Coil Width", str(payload.get("coil_width", "-")))
    a4.metric("Waste Kg", str(summary.get("waste_kg", "-")))

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Coil Kg", str(payload.get("coil_kg", "-")))
    b2.metric("Slitting Kg", str(summary.get("slitting_kg", payload.get("slitting_kg", "-"))))
    b3.metric("Remaining", str(summary.get("remaining", "-")))
    b4.metric("Sent At", str(current_job.get("sent_at", "-")))

    rows = payload.get("rows", [])
    st.subheader("Current Job Details")
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if len(pending_jobs) > 1:
        st.subheader("Next Jobs in Queue")
        preview = []
        for job in pending_jobs[1:]:
            p = job.get("payload", {})
            preview.append(
                {
                    "queue_id": job.get("queue_id", ""),
                    "job_code": p.get("job_code", ""),
                    "coil_number": p.get("coil_number", ""),
                    "thickness": p.get("thickness", ""),
                    "material": p.get("material", ""),
                    "status": job.get("status", ""),
                    "sent_at": job.get("sent_at", ""),
                }
            )
        st.dataframe(preview, use_container_width=True, hide_index=True)

components.html(
    f'''
<script>
setTimeout(function(){{
    window.location.reload();
}}, {int(refresh_seconds * 1000)});
</script>
''',
    height=0,
)
