
import base64
import json
from datetime import datetime, timezone

import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

st.set_page_config(page_title="Slitting Receiver Queue", layout="wide")


def qp(name, default=None):
    try:
        value = st.query_params.get(name, default)
    except Exception:
        value = default
    return value


def get_queue_url():
    url = qp("queue_url")
    if url:
        return str(url)
    try:
        return st.secrets["QUEUE_URL"]
    except Exception:
        repo = st.secrets.get("DISPLAY_GITHUB_REPO")
        branch = st.secrets.get("DISPLAY_GITHUB_BRANCH", "main")
        if repo:
            return f"https://raw.githubusercontent.com/{repo}/{branch}/queue.json"
        return None


def get_machine_id():
    return str(qp("machine", "M1"))


def sound_enabled():
    return str(qp("sound", "1")).lower() not in {"0", "false", "no", "off"}


def fullscreen_enabled():
    return str(qp("fullscreen", "1")).lower() not in {"0", "false", "no", "off"}


def fetch_queue(url: str):
    r = requests.get(url, timeout=20, headers={"Cache-Control": "no-cache, no-store, max-age=0"})
    r.raise_for_status()
    return r.json()


def _get_secret(name: str, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return default


def _headers(token: str):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_url(repo: str, path: str) -> str:
    return f"https://api.github.com/repos/{repo}/contents/{path}"


def _get_file(repo: str, path: str, branch: str, token: str):
    r = requests.get(_api_url(repo, path), headers=_headers(token), params={"ref": branch}, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub read failed ({r.status_code}): {r.text}")
    return r.json()


def _put_file(repo: str, path: str, branch: str, token: str, content_bytes: bytes, message: str):
    existing = _get_file(repo, path, branch, token)
    payload = {
        "message": message,
        "branch": branch,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
    }
    if existing and "sha" in existing:
        payload["sha"] = existing["sha"]
    r = requests.put(_api_url(repo, path), headers=_headers(token), json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub write failed ({r.status_code}): {r.text}")
    return r.json()


def _default_store():
    return {
        "version": 0,
        "machines": {
            "M1": {"queue": [], "history": []},
            "M2": {"queue": [], "history": []},
        },
    }


def _normalize_store(store):
    if not isinstance(store, dict):
        store = _default_store()
    store.setdefault("version", 0)
    store.setdefault("machines", {})
    for mid in ("M1", "M2"):
        store["machines"].setdefault(mid, {})
        machine = store["machines"][mid]
        machine.setdefault("queue", [])
        machine.setdefault("history", [])
    return store


def _load_store_for_write():
    token = _get_secret("DISPLAY_GITHUB_TOKEN")
    repo = _get_secret("DISPLAY_GITHUB_REPO")
    branch = _get_secret("DISPLAY_GITHUB_BRANCH", "main")
    queue_path = _get_secret("QUEUE_PATH", "queue.json")
    if not token or not repo:
        raise RuntimeError("Receiver app is missing DISPLAY_GITHUB_TOKEN and/or DISPLAY_GITHUB_REPO secrets")
    existing = _get_file(repo, queue_path, branch, token)
    if not existing:
        return _default_store(), repo, branch, token, queue_path
    decoded = base64.b64decode(existing["content"]).decode("utf-8")
    return _normalize_store(json.loads(decoded)), repo, branch, token, queue_path


def complete_current_job(machine_id: str):
    store, repo, branch, token, queue_path = _load_store_for_write()
    store = _normalize_store(store)
    queue = store["machines"][machine_id]["queue"]
    history = store["machines"][machine_id]["history"]
    if not queue:
        return False, "No active job"
    job = queue.pop(0)
    job["status"] = "completed"
    job["completed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    history.insert(0, job)
    store["version"] = int(store.get("version", 0)) + 1
    _put_file(
        repo=repo,
        path=queue_path,
        branch=branch,
        token=token,
        content_bytes=json.dumps(store, indent=2, ensure_ascii=False).encode("utf-8"),
        message=f"Complete job {job.get('queue_id', '')} on {machine_id}",
    )
    return True, job.get("payload", {}).get("job_code", "")


def fmt_dt(value):
    if not value:
        return "-"
    try:
        if isinstance(value, str) and value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


queue_url = get_queue_url()
machine_id = get_machine_id()
play_sound = sound_enabled()
fullscreen = fullscreen_enabled()

if not queue_url:
    st.title("Slitting Receiver Queue")
    st.error("Missing queue URL.")
    st.stop()

if st_autorefresh is not None:
    st_autorefresh(interval=2000, key="receiver_refresh")

if fullscreen:
    st.markdown(
        '''
        <style>
        header[data-testid="stHeader"] {display:none !important;}
        div[data-testid="stToolbar"] {display:none !important;}
        [data-testid="collapsedControl"] {display:none !important;}
        .block-container {padding-top:0.6rem !important; padding-bottom:1rem !important; max-width: 100% !important;}
        .hero {
            padding: 18px 22px;
            border-radius: 18px;
            background: linear-gradient(135deg, rgba(239,68,68,0.22), rgba(127,29,29,0.24));
            border: 1px solid rgba(255,255,255,0.10);
            margin-bottom: 16px;
        }
        .panel {
            padding: 14px 16px;
            border-radius: 16px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            margin-bottom: 14px;
        }
        .small-muted {
            opacity: 0.75;
            font-size: 0.9rem;
        }
        </style>
        ''',
        unsafe_allow_html=True,
    )

st.title(f"Receiver Queue — {machine_id}")

try:
    store = fetch_queue(queue_url)
except Exception as e:
    st.error(f"Could not load queue: {e}")
    st.stop()

store = _normalize_store(store)
machine_store = store["machines"].get(machine_id, {"queue": [], "history": []})
queue = machine_store.get("queue", [])
history = machine_store.get("history", [])
active_job = queue[0] if queue else None

current_key = f"{machine_id}|{store.get('version', 0)}|{len(queue)}|{active_job.get('queue_id', '') if active_job else ''}"
previous_key = st.session_state.get("last_queue_key")
st.session_state["last_queue_key"] = current_key
beep_now = play_sound and previous_key not in (None, current_key)

if beep_now:
    components.html(
        '''
        <script>
        try {
          const ctx = new (window.AudioContext || window.webkitAudioContext)();
          const o = ctx.createOscillator();
          const g = ctx.createGain();
          o.type = 'sine';
          o.frequency.value = 950;
          o.connect(g);
          g.connect(ctx.destination);
          g.gain.setValueAtTime(0.001, ctx.currentTime);
          g.gain.exponentialRampToValueAtTime(0.23, ctx.currentTime + 0.02);
          g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.42);
          o.start();
          o.stop(ctx.currentTime + 0.42);
        } catch(e) {}
        </script>
        ''',
        height=0,
    )

top1, top2, top3, top4 = st.columns(4)
top1.metric("Machine", machine_id)
top2.metric("Queue", len(queue))
top3.metric("History", len(history))
top4.metric("Version", store.get("version", 0))

mode = st.segmented_control("View", ["Active Job", "Queue", "History"], default="Active Job")

if mode == "Active Job":
    if not active_job:
        st.success("No active job.")
    else:
        payload = active_job.get("payload", {})
        summary = payload.get("summary", {})

        st.markdown(
            f'''
            <div class="hero">
                <div class="small-muted">Current active job</div>
                <h1 style="margin: 0.2rem 0 0.5rem 0;">{payload.get("job_code", "-")}</h1>
                <div class="small-muted">Coil {payload.get("coil_number", "-")} · Material {payload.get("material", "-")} · Thickness {payload.get("thickness", "-")}</div>
            </div>
            ''',
            unsafe_allow_html=True,
        )

        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("Cut Plan", f'{payload.get("cut_plan", 1)}x')
        a2.metric("Coil Width", payload.get("coil_width", "-"))
        a3.metric("Coil Kg", payload.get("coil_kg", "-"))
        a4.metric("Slitting Kg", summary.get("slitting_kg", payload.get("slitting_kg", "-")))
        a5.metric("Remaining", summary.get("remaining", "-"))

        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Waste Kg", summary.get("waste_kg", "-"))
        b2.metric("Waste %", summary.get("waste_pct", "-"))
        b3.metric("Sent At", fmt_dt(active_job.get("sent_at")))
        b4.metric("Next Jobs", max(len(queue) - 1, 0))

        st.subheader("Job Details")
        st.dataframe(payload.get("rows", []), use_container_width=True, hide_index=True)

        c1, c2 = st.columns([1, 4])
        if c1.button("Complete", type="primary", use_container_width=True):
            try:
                ok, job_code = complete_current_job(machine_id)
                if ok:
                    st.success(f"Completed {job_code}")
                    st.rerun()
                else:
                    st.warning(job_code)
            except Exception as e:
                st.error(f"Complete failed: {e}")
        c2.caption("Completing the current job moves it to History and shows the next queued job automatically.")

elif mode == "Queue":
    st.subheader("Orders in Queue")
    if not queue:
        st.info("Queue is empty.")
    else:
        rows = []
        for idx, item in enumerate(queue, start=1):
            payload = item.get("payload", {})
            rows.append(
                {
                    "Position": idx,
                    "Job Code": payload.get("job_code", ""),
                    "Coil": payload.get("coil_number", ""),
                    "Thickness": payload.get("thickness", ""),
                    "Material": payload.get("material", ""),
                    "Coil Width": payload.get("coil_width", ""),
                    "Sent At": fmt_dt(item.get("sent_at")),
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

else:
    st.subheader("Completed Jobs History")
    if not history:
        st.info("No history yet.")
    else:
        rows = []
        for item in history:
            payload = item.get("payload", {})
            rows.append(
                {
                    "Job Code": payload.get("job_code", ""),
                    "Coil": payload.get("coil_number", ""),
                    "Thickness": payload.get("thickness", ""),
                    "Material": payload.get("material", ""),
                    "Completed At": fmt_dt(item.get("completed_at")),
                    "Sent At": fmt_dt(item.get("sent_at")),
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
