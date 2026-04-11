
import base64
import json
import html
from collections import Counter
from datetime import datetime, timezone

import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

st.set_page_config(page_title="Slitting Receiver", layout="wide")

READ_INTERVAL_MS = 3000

def qp(name, default=None):
    try:
        return st.query_params.get(name, default)
    except Exception:
        return default

def get_machine_id():
    return str(qp("machine", "M1"))

def sound_enabled():
    return str(qp("sound", "1")).lower() not in {"0", "false", "no", "off"}

def fullscreen_enabled():
    return str(qp("fullscreen", "1")).lower() not in {"0", "false", "no", "off"}

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

def _default_store():
    return {
        "version": 0,
        "machines": {
            "M1": {"queue": [], "history": []},
            "M2": {"queue": [], "history": []},
        },
    }

def _normalize_machine(value):
    if isinstance(value, dict):
        value.setdefault("queue", [])
        value.setdefault("history", [])
        if not isinstance(value["queue"], list):
            value["queue"] = []
        if not isinstance(value["history"], list):
            value["history"] = []
        return value
    if isinstance(value, list):
        return {"queue": value, "history": []}
    return {"queue": [], "history": []}

def _normalize_store(store):
    if not isinstance(store, dict):
        store = _default_store()
    store.setdefault("version", 0)
    store.setdefault("machines", {})
    if not isinstance(store["machines"], dict):
        store["machines"] = {}
    for mid in ("M1", "M2"):
        store["machines"][mid] = _normalize_machine(store["machines"].get(mid))
    return store

def _fetch_store_read():
    token = _get_secret("DISPLAY_GITHUB_TOKEN")
    repo = _get_secret("DISPLAY_GITHUB_REPO")
    branch = _get_secret("DISPLAY_GITHUB_BRANCH", "main")
    queue_path = _get_secret("QUEUE_PATH", "queue.json")
    if not token or not repo:
        raise RuntimeError("Receiver app is missing DISPLAY_GITHUB_TOKEN and/or DISPLAY_GITHUB_REPO secrets")
    r = requests.get(_api_url(repo, queue_path), headers=_headers(token), params={"ref": branch}, timeout=12)
    if r.status_code == 404:
        return _default_store()
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub read failed ({r.status_code}): {r.text}")
    payload = r.json()
    decoded = base64.b64decode(payload["content"]).decode("utf-8")
    return _normalize_store(json.loads(decoded))

def _fetch_store_write_context():
    token = _get_secret("DISPLAY_GITHUB_TOKEN")
    repo = _get_secret("DISPLAY_GITHUB_REPO")
    branch = _get_secret("DISPLAY_GITHUB_BRANCH", "main")
    queue_path = _get_secret("QUEUE_PATH", "queue.json")
    if not token or not repo:
        raise RuntimeError("Receiver app needs DISPLAY_GITHUB_TOKEN and DISPLAY_GITHUB_REPO secrets for write actions")
    r = requests.get(_api_url(repo, queue_path), headers=_headers(token), params={"ref": branch}, timeout=12)
    if r.status_code == 404:
        store = _default_store()
    else:
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub read failed ({r.status_code}): {r.text}")
        payload = r.json()
        decoded = base64.b64decode(payload["content"]).decode("utf-8")
        store = _normalize_store(json.loads(decoded))
    return store, repo, branch, token, queue_path

def _put_store(store, repo, branch, token, queue_path, message):
    existing = requests.get(_api_url(repo, queue_path), headers=_headers(token), params={"ref": branch}, timeout=12)
    sha = None
    if existing.status_code == 200:
        sha = existing.json()["sha"]
    payload = {
        "message": message,
        "branch": branch,
        "content": base64.b64encode(json.dumps(store, indent=2, ensure_ascii=False).encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(_api_url(repo, queue_path), headers=_headers(token), json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub write failed ({r.status_code}): {r.text}")

def complete_current_job(machine_id: str):
    store, repo, branch, token, queue_path = _fetch_store_write_context()
    queue = store["machines"][machine_id]["queue"]
    history = store["machines"][machine_id]["history"]
    if not queue:
        return False, "No active job"
    job = queue.pop(0)
    job["status"] = "completed"
    job["completed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    history.insert(0, job)
    store["version"] = int(store.get("version", 0)) + 1
    _put_store(store, repo, branch, token, queue_path, f"Complete job {job.get('queue_id', '')} on {machine_id}")
    return True, job.get("payload", {}).get("job_code", "")

def prioritize_job(machine_id: str, queue_id: str):
    store, repo, branch, token, queue_path = _fetch_store_write_context()
    queue = store["machines"][machine_id]["queue"]
    idx = next((i for i, item in enumerate(queue) if item.get("queue_id") == queue_id), None)
    if idx is None:
        return False, "Job not found"
    if idx == 0:
        return True, "Already active"
    item = queue.pop(idx)
    queue.insert(0, item)
    store["version"] = int(store.get("version", 0)) + 1
    _put_store(store, repo, branch, token, queue_path, f"Prioritize job {queue_id} on {machine_id}")
    return True, item.get("payload", {}).get("job_code", "")

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

def to_float(v, default=0.0):
    try:
        if v is None:
            return default
        s = str(v).strip().replace(",", ".")
        if s == "":
            return default
        return float(s)
    except Exception:
        return default

def fmt_num(v):
    n = to_float(v, None)
    if n is None:
        return "-"
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.2f}".rstrip("0").rstrip(".")

def map_rule(machine, thickness):
    t = to_float(thickness, 0.0)
    if machine == "M1":
        if t <= 1.0:
            return 1.0
        if t <= 1.5:
            return 1.5
        return 2.0
    if t <= 1.0:
        return 1.0
    if t <= 1.5:
        return 1.5
    if t <= 2.0:
        return 2.0
    if t <= 2.5:
        return 2.5
    return 3.0

def get_rules(machine, thickness):
    rt = map_rule(machine, thickness)
    if machine == "M1":
        if rt == 1.0:
            return {"group": "M1-1", "tolerance": "Ø 1.00", "tsonta": "1.60 + 1.30", "knife": "10", "rubber": "10", "max_knives": "8"}
        if rt == 1.5:
            return {"group": "M1-1.5", "tolerance": "Ø 1.10", "tsonta": "1.60 + 1.10", "knife": "10", "rubber": "10", "max_knives": "6"}
        return {"group": "M1-2", "tolerance": "Ø 1.20", "tsonta": "1.60 + 1.00", "knife": "10", "rubber": "10", "max_knives": "8"}
    table = {
        1.0: ("LOW", "Ø 2.15", "1.80", "5"),
        1.5: ("HIGH", "Ø 2.20", "1.70", "8"),
        2.0: ("HIGH", "Ø 2.25", "1.60", "8"),
        2.5: ("HIGH", "Ø 2.30", "1.50", "8"),
        3.0: ("HIGH", "Ø 2.35", "1.40", "8"),
    }
    group, tolerance, tsonta, knife = table[rt]
    return {"group": group, "tolerance": tolerance, "tsonta": tsonta, "knife": knife, "rubber": knife, "max_knives": ""}

def _parse_token(token_text):
    t = str(token_text or "").strip()
    mm = 0.0
    token_type = "spacer"
    label = t
    if t.startswith("TS"):
        token_type = "tsonta"
        label = t.replace("TS", "").strip()
        for part in label.replace("+", " ").split():
            mm += to_float(part, 0.0)
    elif t.startswith("K"):
        token_type = "knife"
        label = t[1:].strip()
        mm = to_float(label, 0.0)
    elif t.startswith("R"):
        token_type = "rubber"
        label = t[1:].strip()
        mm = to_float(label, 0.0)
    elif t.startswith("S"):
        token_type = "spacer"
        label = t[1:].strip()
        mm = to_float(label, 0.0)
    return {"raw": t, "label": label or t, "mm": mm, "type": token_type}

def _build_visual_tokens(machine, width, rules, male_tokens, female_tokens):
    width_value = to_float(width, 0.0)
    male = []
    female = [f"K{rules.get('knife', '10')}"]
    if machine == "M1" and width_value > 50:
        male += [f"S24", f"R{rules.get('rubber', '10')}"]
        female += ["S12", "S2", f"R{rules.get('rubber', '10')}"]
    male += list(male_tokens or [])
    female += list(female_tokens or [])
    female += [f"TS {rules.get('tsonta', '')}", f"K{rules.get('knife', '10')}"]
    return male, female

def _token_min_width(token_type):
    return {"knife": 52.0, "rubber": 52.0, "spacer": 52.0, "tsonta": 98.0}.get(token_type, 52.0)

def _token_raw_width(meta, px_per_mm=11.5):
    mm_for_visual = 6.0 if 0 < meta["mm"] < 6 else meta["mm"]
    min_width = _token_min_width(meta["type"])
    return max(min_width, mm_for_visual * px_per_mm if mm_for_visual > 0 else min_width)

def _fit_token_widths(tokens, width_available, gap=14.0, px_per_mm=11.5):
    if not tokens:
        return []
    metas = [_parse_token(token) for token in tokens]
    mins = [_token_min_width(meta["type"]) for meta in metas]
    raws = [_token_raw_width(meta, px_per_mm=px_per_mm) for meta in metas]
    count = len(tokens)
    gap_total = gap * max(count - 1, 0)
    usable = max(width_available - gap_total, count * 30.0)
    min_total = sum(mins)
    raw_total = sum(raws)
    if raw_total <= usable:
        widths = raws
    elif min_total <= usable:
        extra_total = max(raw_total - min_total, 1.0)
        ratio = max(0.0, min(1.0, (usable - min_total) / extra_total))
        widths = [mins[i] + (raws[i] - mins[i]) * ratio for i in range(count)]
    else:
        forced = usable / max(count, 1)
        widths = [max(42.0, forced) for _ in range(count)]
    return [{"meta": metas[i], "width": widths[i], "gap": gap} for i in range(count)]

def _html_token_palette():
    return {
        "spacer": {"fill": "#1f2937", "stroke": "#475569", "text": "#e5e7eb", "tag": "#94a3b8"},
        "rubber": {"fill": "#123a1d", "stroke": "#16a34a", "text": "#dcfce7", "tag": "#86efac"},
        "knife": {"fill": "#3f1d1d", "stroke": "#ef4444", "text": "#fee2e2", "tag": "#fca5a5"},
        "tsonta": {"fill": "#3b2a13", "stroke": "#f59e0b", "text": "#fef3c7", "tag": "#fcd34d"},
    }

def _token_short_label(token_type):
    return {"spacer": "S", "rubber": "R", "knife": "K", "tsonta": "TS"}.get(token_type, "")

def _render_token_chip_html(token, width, machine="M1"):
    meta = _parse_token(token)
    palette = _html_token_palette().get(meta["type"], _html_token_palette()["spacer"])
    short_label = html.escape(_token_short_label(meta["type"]))
    value = html.escape(str(meta["label"]))
    if meta["type"] == "tsonta":
        values = [html.escape(part) for part in str(meta["label"]).replace("+", " ").split() if part]
        if machine == "M1":
            stack = "".join(f"<span class='stack-line'>{part}</span>" for part in values[:2])
        else:
            stack = f"<span class='stack-line'>{html.escape(values[0] if values else str(meta['label']))}</span>" if values else ""
        value_html = f"<span class='token-stack'>{stack}</span>"
    else:
        value_html = value
    return (
        f"<span class='setup-token' style='width:{max(42, int(round(width)))}px;background:{palette['fill']};border-color:{palette['stroke']};color:{palette['text']}'>"
        f"<span class='token-label' style='color:{palette['tag']}'>{short_label}</span>"
        f"<span class='token-value'>{value_html}</span>"
        f"</span>"
    )

def _render_token_strip_html(tokens, width_available=1400, total_label=None, machine="M1"):
    if not tokens:
        return "<div class='setup-empty'>No setup yet</div>"
    layout = _fit_token_widths(tokens, width_available, gap=14.0, px_per_mm=11.5)
    chips = "".join(_render_token_chip_html(token, item["width"], machine=machine) for token, item in zip(tokens, layout))
    total = sum(_parse_token(t)["mm"] for t in tokens)
    total_html = f"<div class='setup-total'>{html.escape(total_label or 'Total')}: {total:.2f} mm</div>" if total_label else ""
    return f"<div class='setup-token-strip'>{chips}</div>{total_html}"

def _merged_widths(rows):
    counter = Counter()
    for row in rows or []:
        if row.get("edge"):
            continue
        width = str(row.get("width", "")).strip()
        if not width:
            continue
        counter[width] += int(to_float(row.get("qty"), 0))
    return [{"width": width, "qty": qty} for width, qty in counter.items()]

def _preview_sections(payload):
    sections = payload.get("setup_preview")
    if isinstance(sections, list) and sections:
        return sections
    machine = payload.get("machine", "M1")
    thickness = payload.get("thickness", "0.5")
    merged = _merged_widths(payload.get("rows", []))
    out = []
    for item in merged:
        width = str(item["width"])
        qty = int(item["qty"])
        rules = get_rules(machine, thickness)
        male_tokens = []
        female_tokens = []
        visual_male, visual_female = _build_visual_tokens(machine, width, rules, male_tokens, female_tokens)
        out.append({
            "width": width,
            "qty": qty,
            "machine": machine,
            "thickness": thickness,
            "rules": rules,
            "male_tokens": visual_male,
            "female_tokens": visual_female,
        })
    return out

def _render_setup_preview_one(section):
    width = section.get("width", "")
    qty = section.get("qty", "")
    machine = section.get("machine", "M1")
    thickness = section.get("thickness", "")
    rules = section.get("rules", get_rules(machine, thickness))
    st.markdown(
        f"<div class='setup-preview-card'><div class='setup-preview-title'>WIDTH {html.escape(str(width))} x{html.escape(str(qty))}</div>"
        f"<div class='setup-preview-sub'>Machine: {html.escape(str(machine))} &nbsp;&nbsp; Thickness: {html.escape(str(thickness))}<br>"
        f"Tolerance: {html.escape(str(rules.get('tolerance', '')))}</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='setup-section-heading'>MALE</div>", unsafe_allow_html=True)
    st.markdown(_render_token_strip_html(section.get("male_tokens", []), total_label="Total", machine=machine), unsafe_allow_html=True)
    st.markdown("<div class='setup-section-heading'>FEMALE</div>", unsafe_allow_html=True)
    st.markdown(_render_token_strip_html(section.get("female_tokens", []), total_label="Total", machine=machine), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

machine_id = get_machine_id()
play_sound = sound_enabled()
fullscreen = fullscreen_enabled()

if st_autorefresh is not None:
    st_autorefresh(interval=READ_INTERVAL_MS, key=f"receiver_refresh_{machine_id}")

if fullscreen:
    st.markdown("""
    <style>
    header[data-testid="stHeader"] {display:none !important;}
    div[data-testid="stToolbar"] {display:none !important;}
    [data-testid="collapsedControl"] {display:none !important;}
    .block-container {padding-top:.2rem !important; padding-bottom:1rem !important; max-width:100% !important;}
    .receiver-sticky-top{position:sticky;top:0;z-index:100;background:transparent;padding-top:6px;padding-bottom:8px;margin-bottom:8px}
    .receiver-main-title{text-align:center;font-size:2rem;font-weight:700;margin:0}
    .hero{padding:18px 22px;border-radius:18px;background:linear-gradient(135deg, rgba(239,68,68,.22), rgba(127,29,29,.24));border:1px solid rgba(255,255,255,.10);margin-bottom:16px}
    .small-muted{opacity:.75;font-size:.95rem}
    .setup-preview-card{background:rgba(255,255,255,.03);border:1px solid rgba(148,163,184,.18);border-radius:18px;padding:22px 22px;margin:10px 0}
    .setup-preview-title{font-size:2.25rem;font-weight:700;margin-bottom:10px}
    .setup-preview-sub{font-size:1.15rem;opacity:.9;margin-bottom:18px}
    .setup-section-heading{font-weight:700;margin-top:16px;margin-bottom:10px;font-size:1.5rem}
    .setup-token-strip{display:flex;flex-wrap:nowrap;align-items:stretch;gap:14px;overflow:hidden;padding:6px 0 12px 0;width:100%}
    .setup-token{display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:96px;border:1px solid;border-radius:14px;padding:10px 16px;box-sizing:border-box;line-height:1.05;white-space:nowrap;overflow:hidden}
    .setup-token .token-label{font-size:14px;font-weight:700}
    .setup-token .token-value{font-size:28px;font-weight:700;max-width:100%;text-overflow:ellipsis;overflow:hidden}
    .setup-token .token-stack{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
    .setup-token .stack-line{display:block;font-size:22px;line-height:1.05}
    .setup-total{font-size:22px;color:#cbd5e1;margin-top:6px}
    .setup-empty{font-size:14px;color:#94a3b8;padding:4px 0 8px 0}
    div[data-baseweb="tab-list"]{justify-content:center;min-width:640px}
    div[data-baseweb="tab"]{flex:1;justify-content:center;font-size:1.02rem}
    div[data-baseweb="tab-highlight"]{height:100%}
    [data-testid="stDialog"] button[aria-label="Close"]{display:none !important;}
    </style>
    """, unsafe_allow_html=True)

try:
    store = _fetch_store_read()
except Exception as e:
    st.error(f"Could not load queue: {e}")
    st.stop()

machine_store = store["machines"].get(machine_id, {"queue": [], "history": []})
queue = machine_store.get("queue", [])
history = machine_store.get("history", [])
active_job = queue[0] if queue else None

current_key = f"{machine_id}|{len(queue)}|{active_job.get('queue_id', '') if active_job else ''}"
previous_key = st.session_state.get("last_queue_key")
st.session_state["last_queue_key"] = current_key
beep_now = play_sound and previous_key not in (None, current_key)

if beep_now:
    components.html("""
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
    """, height=0)

st.markdown(f"<div class='receiver-sticky-top'><div class='receiver-main-title'>Receiver — {html.escape(machine_id)}</div></div>", unsafe_allow_html=True)
center_col = st.columns([1, 3.2, 1])[1]
with center_col:
    mode = st.segmented_control("Menu", ["Active Job", "Queue", "History"], default="Active Job", label_visibility="collapsed")

if mode == "Active Job":
    if not active_job:
        st.success("No active job.")
    else:
        payload = active_job.get("payload", {})
        summary = payload.get("summary", {})
        sections = _preview_sections(payload)

        active_qid = active_job.get("queue_id", "")
        page_key = f"active_page_{machine_id}"
        if st.session_state.get("active_page_qid") != active_qid:
            st.session_state["active_page_qid"] = active_qid
            st.session_state[page_key] = 0
        max_page = len(sections)
        current_page = st.session_state.get(page_key, 0)
        current_page = max(0, min(current_page, max_page))

        nav_cols = st.columns([1, 18, 1], vertical_alignment="center")
        if nav_cols[0].button("◀", use_container_width=True, disabled=current_page <= 0):
            st.session_state[page_key] = max(0, current_page - 1)
            st.rerun()

        with nav_cols[1]:
            if current_page == 0:
                st.markdown(
                    f"""
                    <div class="hero">
                        <div class="small-muted">Current active job</div>
                        <h1 style="margin:.2rem 0 .5rem 0;">{html.escape(str(payload.get("job_code", "-")))}</h1>
                        <div class="small-muted">Coil {html.escape(str(payload.get("coil_number", "-")))} · Material {html.escape(str(payload.get("material", "-")))} · Thickness {html.escape(str(payload.get("thickness", "-")))}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                a1, a2, a3, a4, a5 = st.columns(5)
                a1.metric("Cut Plan", f'{payload.get("cut_plan", 1)}x')
                a2.metric("Coil Width", fmt_num(payload.get("coil_width", "-")))
                a3.metric("Coil Kg", fmt_num(payload.get("coil_kg", "-")))
                a4.metric("Material", str(payload.get("material", "-")))
                a5.metric("Remaining", fmt_num(summary.get("remaining", "-")))

                b1, b2, b3, b4, b5 = st.columns(5)
                b1.metric("Waste Kg", fmt_num(summary.get("waste_kg", "-")))
                b2.metric("Waste %", fmt_num(summary.get("waste_pct", "-")))
                b3.metric("Thickness", str(payload.get("thickness", "-")))
                b4.metric("Coil", str(payload.get("coil_number", "-")))
                b5.metric("Meters", fmt_num(payload.get("meters", summary.get("meters", "-"))))

                st.subheader("Job Details")
                st.dataframe(payload.get("rows", []), use_container_width=True, hide_index=True)
            else:
                st.subheader("Setup Preview")
                _render_setup_preview_one(sections[current_page - 1])

        if nav_cols[2].button("▶", use_container_width=True, disabled=current_page >= max_page):
            st.session_state[page_key] = min(max_page, current_page + 1)
            st.rerun()

        if st.session_state.get("confirm_complete_queue_id") == active_job.get("queue_id"):
            st.warning(f"Complete job {payload.get('job_code', '-') }?")
            c1, c2, c3 = st.columns([1, 1, 4])
            if c1.button("Yes, Complete", type="primary", use_container_width=True):
                try:
                    ok, job_code = complete_current_job(machine_id)
                    st.session_state["confirm_complete_queue_id"] = None
                    if ok:
                        st.success(f"Completed {job_code}")
                        st.rerun()
                    else:
                        st.warning(job_code)
                except Exception as e:
                    st.error(f"Complete failed: {e}")
            if c2.button("Cancel", use_container_width=True):
                st.session_state["confirm_complete_queue_id"] = None
                st.rerun()
            c3.caption("Confirm to move this job to History and load the next queued job.")
        else:
            bottom = st.columns([2, 3, 2])[1]
            with bottom:
                if st.button("Complete", type="primary", use_container_width=True):
                    st.session_state["confirm_complete_queue_id"] = active_job.get("queue_id")
                    st.rerun()

def _find_queue_job(qid):
    for item in queue:
        if item.get("queue_id") == qid:
            return item
    return None

if mode == "Queue":
    st.subheader("Orders in Queue")
    if not queue:
        st.info("Queue is empty.")
    else:
        header = st.columns([1.0, 1.4, 1.2, 0.8, 1.0, 1.0])
        for col, title in zip(header, ["Open", "Job Code", "Coil", "Thick", "Material", "Sent"]):
            col.caption(title)
        for item in queue:
            payload = item.get("payload", {})
            cols = st.columns([1.0, 1.4, 1.2, 0.8, 1.0, 1.0])
            if cols[0].button("Open", key=f"open_q_{item.get('queue_id')}"):
                st.session_state["queue_dialog_id"] = item.get("queue_id")
            cols[1].write(payload.get("job_code", ""))
            cols[2].write(payload.get("coil_number", ""))
            cols[3].write(payload.get("thickness", ""))
            cols[4].write(payload.get("material", ""))
            cols[5].write(fmt_dt(item.get("sent_at")))

        if st.session_state.get("queue_dialog_id"):
            job = _find_queue_job(st.session_state["queue_dialog_id"])
            if not job:
                st.session_state["queue_dialog_id"] = None
            elif hasattr(st, "dialog"):
                payload = job.get("payload", {})
                @st.dialog("Queue Job Details", width="large")
                def _queue_job_dialog():
                    sections = _preview_sections(payload)
                    qpage_key = f"queue_dialog_page_{job.get('queue_id')}"
                    current_page = st.session_state.get(qpage_key, 0)
                    current_page = max(0, min(current_page, len(sections)))

                    nav_cols = st.columns([1, 16, 1])
                    if nav_cols[0].button("◀", key=f"dlg_prev_{job.get('queue_id')}", use_container_width=True, disabled=current_page <= 0):
                        st.session_state[qpage_key] = max(0, current_page - 1)
                        st.rerun()

                    with nav_cols[1]:
                        if current_page == 0:
                            summary = payload.get("summary", {})
                            st.markdown(
                                f"""
                                <div class="hero">
                                    <div class="small-muted">Queued job</div>
                                    <h1 style="margin:.2rem 0 .5rem 0;">{html.escape(str(payload.get("job_code", "-")))}</h1>
                                    <div class="small-muted">Coil {html.escape(str(payload.get("coil_number", "-")))} · Material {html.escape(str(payload.get("material", "-")))} · Thickness {html.escape(str(payload.get("thickness", "-")))}</div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                            a1, a2, a3, a4, a5 = st.columns(5)
                            a1.metric("Cut Plan", f'{payload.get("cut_plan", 1)}x')
                            a2.metric("Coil Width", fmt_num(payload.get("coil_width", "-")))
                            a3.metric("Coil Kg", fmt_num(payload.get("coil_kg", "-")))
                            a4.metric("Material", str(payload.get("material", "-")))
                            a5.metric("Remaining", fmt_num(summary.get("remaining", "-")))

                            b1, b2, b3, b4, b5 = st.columns(5)
                            b1.metric("Waste Kg", fmt_num(summary.get("waste_kg", "-")))
                            b2.metric("Waste %", fmt_num(summary.get("waste_pct", "-")))
                            b3.metric("Thickness", str(payload.get("thickness", "-")))
                            b4.metric("Coil", str(payload.get("coil_number", "-")))
                            b5.metric("Meters", fmt_num(payload.get("meters", summary.get("meters", "-"))))

                            st.subheader("Job Details")
                            st.dataframe(payload.get("rows", []), use_container_width=True, hide_index=True)
                        elif sections:
                            _render_setup_preview_one(sections[current_page - 1])

                    if nav_cols[2].button("▶", key=f"dlg_next_{job.get('queue_id')}", use_container_width=True, disabled=current_page >= len(sections)):
                        st.session_state[qpage_key] = min(len(sections), current_page + 1)
                        st.rerun()

                    center = st.columns([2, 3, 2])[1]
                    with center:
                        if st.button("Priority", type="primary", use_container_width=True):
                            ok, msg = prioritize_job(machine_id, job.get("queue_id", ""))
                            st.session_state["queue_dialog_id"] = None
                            if ok:
                                st.success(f"Priority set for {msg}")
                                st.rerun()
                            else:
                                st.error(msg)
                _queue_job_dialog()
if mode == "History":
    st.subheader("Completed Jobs History")
    if not history:
        st.info("No history yet.")
    else:
        rows = []
        for item in history:
            payload = item.get("payload", {})
            rows.append({
                "Job Code": payload.get("job_code", ""),
                "Coil": payload.get("coil_number", ""),
                "Thickness": payload.get("thickness", ""),
                "Material": payload.get("material", ""),
                "Completed At": fmt_dt(item.get("completed_at")),
                "Sent At": fmt_dt(item.get("sent_at")),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
