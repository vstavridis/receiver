import base64
import json
import html
import time
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


def _queue_url():
    url = qp("queue_url")
    if url:
        return str(url)
    url = _get_secret("QUEUE_URL")
    if url:
        return url
    repo = _get_secret("DISPLAY_GITHUB_REPO")
    branch = _get_secret("DISPLAY_GITHUB_BRANCH", "main")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{branch}/queue.json"
    raise RuntimeError("Missing queue source")


def _fetch_store_read():
    url = _queue_url()
    sep = "&" if "?" in url else "?"
    fresh_url = f"{url}{sep}t={int(time.time() * 1000)}"
    r = requests.get(
        fresh_url,
        timeout=10,
        headers={
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    if r.status_code == 404:
        return _default_store()
    r.raise_for_status()
    return _normalize_store(r.json())


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
    return {"knife": 40.0, "rubber": 40.0, "spacer": 40.0, "tsonta": 80.0}.get(token_type, 40.0)


def _token_raw_width(meta, px_per_mm=8.5):
    mm_for_visual = 6.0 if 0 < meta["mm"] < 6 else meta["mm"]
    min_width = _token_min_width(meta["type"])
    return max(min_width, mm_for_visual * px_per_mm if mm_for_visual > 0 else min_width)


def _fit_token_widths(tokens, width_available, gap=10.0, px_per_mm=8.5):
    if not tokens:
        return []
    metas = [_parse_token(token) for token in tokens]
    mins = [_token_min_width(meta["type"]) for meta in metas]
    raws = [_token_raw_width(meta, px_per_mm=px_per_mm) for meta in metas]
    count = len(tokens)
    gap_total = gap * max(count - 1, 0)
    usable = max(width_available - gap_total, count * 20.0)
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
        widths = [max(32.0, forced) for _ in range(count)]
    return [{"meta": metas[i], "width": widths[i], "gap": gap} for i in range(count)]


def _html_token_palette():
    return {
        "spacer": {"fill": "#1f2937", "stroke": "#475569", "text": "#e5e7eb", "tag": "#94a3b8"},
        "rubber": {"fill": "#123a1d", "stroke": "#16a34a", "text": "#dcfce7", "tag": "#86efac"},
        "knife": {"fill": "#3f1d1d", "stroke": "#b91c1c", "text": "#fee2e2", "tag": "#fca5a5"},
        "tsonta": {"fill": "#3b2a13", "stroke": "#d97706", "text": "#fef3c7", "tag": "#fcd34d"},
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
        f"<span class='setup-token' style='width:{max(32, int(round(width)))}px;background:{palette['fill']};border-color:{palette['stroke']};color:{palette['text']}'>"
        f"<span class='token-label' style='color:{palette['tag']}'>{short_label}</span>"
        f"<span class='token-value'>{value_html}</span>"
        f"</span>"
    )


def _render_token_strip_html(tokens, width_available=1200, total_label=None, machine="M1"):
    if not tokens:
        return "<div class='setup-empty'>No setup yet</div>"
    layout = _fit_token_widths(tokens, width_available, gap=10.0, px_per_mm=8.5)
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
    machine = payload.get("machine", "M1")
    thickness = payload.get("thickness", "0.5")
    merged = _merged_widths(payload.get("rows", []))
    out = []
    for item in merged:
        width = str(item["width"])
        qty = int(item["qty"])
        rules = get_rules(machine, thickness)
        visual_male, visual_female = _build_visual_tokens(machine, width, rules, [], [])
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
    st_autorefresh(interval=1200, key=f"receiver_refresh_{machine_id}")

if fullscreen:
    st.markdown("""
    <style>
    header[data-testid="stHeader"] {display:none !important;}
    div[data-testid="stToolbar"] {display:none !important;}
    [data-testid="collapsedControl"] {display:none !important;}
    .block-container {padding-top:.35rem !important; padding-bottom:1rem !important; max-width: 100% !important;}
    .receiver-sticky-top{position:sticky;top:0;z-index:100;background:rgba(4,8,18,.96);backdrop-filter:blur(8px);padding-top:8px;padding-bottom:10px;margin-bottom:12px;border-bottom:1px solid rgba(255,255,255,.08)}
    .receiver-main-title{text-align:center;font-size:2rem;font-weight:700;margin:0}
    .hero{padding:18px 22px;border-radius:18px;background:linear-gradient(135deg, rgba(239,68,68,.22), rgba(127,29,29,.24));border:1px solid rgba(255,255,255,.10);margin-bottom:16px}
    .small-muted{opacity:.75;font-size:.9rem}
    .setup-preview-card{background:rgba(255,255,255,.03);border:1px solid rgba(148,163,184,.18);border-radius:16px;padding:18px 18px;margin:10px 0}
    .setup-preview-title{font-size:2rem;font-weight:700;margin-bottom:4px}
    .setup-preview-sub{font-size:1.05rem;opacity:.85;margin-bottom:12px}
    .setup-section-heading{font-weight:700;margin-top:12px;margin-bottom:8px;font-size:1.1rem}
    .setup-token-strip{display:flex;flex-wrap:nowrap;align-items:stretch;gap:10px;overflow:hidden;padding:4px 0 10px 0;width:100%}
    .setup-token{display:flex;flex-direction:column;justify-content:center;align-items:center;min-height:72px;border:1px solid;border-radius:12px;padding:8px 12px;box-sizing:border-box;line-height:1.05;white-space:nowrap;overflow:hidden}
    .setup-token .token-label{font-size:12px;font-weight:700}
    .setup-token .token-value{font-size:18px;font-weight:700;max-width:100%;text-overflow:ellipsis;overflow:hidden}
    .setup-token .token-stack{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
    .setup-token .stack-line{display:block;font-size:15px;line-height:1.05}
    .setup-total{font-size:14px;color:#cbd5e1;margin-top:2px}
    .setup-empty{font-size:12px;color:#94a3b8;padding:4px 0 8px 0}
    div[data-baseweb="tab-list"]{justify-content:center}
    </style>
    """, unsafe_allow_html=True)

store = _fetch_store_read()
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

center_col = st.columns([1, 2, 1])[1]
with center_col:
    mode = st.segmented_control("Menu", ["Active Job", "Queue", "History"], default="Active Job", label_visibility="collapsed")

top1, top2, top3 = st.columns(3)
top1.metric("Machine", machine_id)
top2.metric("Queue", len(queue))
top3.metric("History", len(history))

if mode == "Active Job":
    if not active_job:
        st.success("No active job.")
    else:
        payload = active_job.get("payload", {})
        summary = payload.get("summary", {})
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

        sections = _preview_sections(payload)
        active_qid = active_job.get("queue_id", "")
        state_key = f"preview_idx_{machine_id}"
        if st.session_state.get("preview_active_qid") != active_qid:
            st.session_state["preview_active_qid"] = active_qid
            st.session_state[state_key] = 0
        preview_idx = st.session_state.get(state_key, 0)
        preview_idx = max(0, min(preview_idx, max(len(sections) - 1, 0)))

        if sections:
            st.subheader("Setup Preview")
            nav_cols = st.columns([1, 16, 1])
            if nav_cols[0].button("◀", use_container_width=True, disabled=preview_idx <= 0):
                st.session_state[state_key] = max(0, preview_idx - 1)
                st.rerun()
            with nav_cols[1]:
                _render_setup_preview_one(sections[preview_idx])
            if nav_cols[2].button("▶", use_container_width=True, disabled=preview_idx >= len(sections) - 1):
                st.session_state[state_key] = min(len(sections) - 1, preview_idx + 1)
                st.rerun()

        if st.session_state.get("confirm_complete_queue_id") == active_job.get("queue_id"):
            st.warning(f"Complete job {payload.get('job_code', '-') }?")
            c1, c2, c3 = st.columns([1, 1, 4])
            if c1.button("Yes, Complete", type="primary", use_container_width=True):
                ok, job_code = complete_current_job(machine_id)
                st.session_state["confirm_complete_queue_id"] = None
                if ok:
                    st.success(f"Completed {job_code}")
                    st.rerun()
                else:
                    st.warning(job_code)
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

elif mode == "Queue":
    st.info("This rate-limit fix bundle keeps the receiver stable. Use your previous queue dialog version after this fix is deployed if you still want queue dialog actions.")
    st.dataframe([
        {
            "Job Code": item.get("payload", {}).get("job_code", ""),
            "Coil": item.get("payload", {}).get("coil_number", ""),
            "Thickness": item.get("payload", {}).get("thickness", ""),
            "Material": item.get("payload", {}).get("material", ""),
            "Sent At": fmt_dt(item.get("sent_at")),
        }
        for item in queue
    ], use_container_width=True, hide_index=True)

else:
    rows = [
        {
            "Job Code": item.get("payload", {}).get("job_code", ""),
            "Coil": item.get("payload", {}).get("coil_number", ""),
            "Thickness": item.get("payload", {}).get("thickness", ""),
            "Material": item.get("payload", {}).get("material", ""),
            "Completed At": fmt_dt(item.get("completed_at")),
            "Sent At": fmt_dt(item.get("sent_at")),
        }
        for item in history
    ]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No history yet.")
