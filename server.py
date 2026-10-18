"""Jagajanin prototype — local demo server (Python stdlib only).

Serves the midwife app (/) and the tele-specialist console (/doctor.html), plus a small
telemedicine API. Stands in for a production backend (integrated with SATUSEHAT).

    python3 server.py            # http://localhost:8765  and  http://<LAN-IP>:8765
    python3 server.py --seed     # same, with a busy demo caseload from 5 Puskesmas (seed_demo.py)

    JJ_DATA=/some/dir PORT=8799 python3 server.py   # separate data folder, e.g. for recording

Demo only: data lands in ./data on this machine, unencrypted, with no auth.

Public demo (Cloud Run, one instance): only the app files are served, uploads are capped, the
oldest visitor cases and videos are evicted past a budget, and with --seed the demo caseload is
re-seeded (fresh timestamps) after RESEED_HOURS once nobody has posted for IDLE_MINUTES.
"""

import json
import mimetypes
import os
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("JJ_DATA") or ROOT / "data")
VIDEOS = DATA / "videos"
DEMO_SWEEPS = ROOT / "data" / "demo-sweeps"   # synthetic clips shown for seeded cases
CASES_FILE = DATA / "cases.json"
PORT = int(os.environ.get("PORT", 8765))
MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_MB", 30)) * 1024 * 1024
VIDEO_BUDGET_BYTES = int(os.environ.get("VIDEO_BUDGET_MB", 300)) * 1024 * 1024
MAX_LIVE_CASES = int(os.environ.get("MAX_LIVE_CASES", 300))
RESEED_MS = float(os.environ.get("RESEED_HOURS", 6)) * 3600_000
IDLE_MS = float(os.environ.get("IDLE_MINUTES", 30)) * 60_000

# Everything else in this folder (server code, local data) stays private.
STATIC = {"", "index.html", "app.js", "rules.js", "model.js", "i18n.js", "manifest.json", "icon.svg",
          "protocols.js", "doctor.html", "doctor.js", "sw.js"}

VIDEOS.mkdir(parents=True, exist_ok=True)
lock = threading.Lock()
state = {"seed": False, "seeded_at": 0, "last_post": 0}


def now_ms():
    return int(time.time() * 1000)


def reseed_if_stale():
    """Keep the seeded console looking live on a long-running instance. Runs on request, since
    Cloud Run gives an idle instance no CPU for background threads. Visitor cases are kept."""
    t = now_ms()
    if not state["seed"] or t - state["seeded_at"] < RESEED_MS or t - state["last_post"] < IDLE_MS:
        return
    from seed_demo import seed
    with lock:
        save_cases(seed(load_cases()))
        state["seeded_at"] = t


def trim_live_cases(cases):
    """Drop the oldest visitor cases (and their videos) past MAX_LIVE_CASES."""
    live = sorted((c for c in cases.values() if not c.get("seeded")), key=lambda c: c.get("receivedAt", 0))
    for c in live[:max(0, len(live) - MAX_LIVE_CASES)]:
        cases.pop(c["caseId"], None)
        for f in VIDEOS.glob(f"{c['caseId']}_*"):
            f.unlink(missing_ok=True)


def trim_videos():
    """Evict the oldest uploaded videos once the store passes VIDEO_BUDGET_BYTES."""
    files = sorted(VIDEOS.glob("*"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= VIDEO_BUDGET_BYTES:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def load_cases():
    try:
        return json.loads(CASES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cases(cases):
    tmp = CASES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cases, ensure_ascii=False, indent=1))
    tmp.replace(CASES_FILE)


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def end_headers(self):
        # Keep the app shell fresh during the demo; the service worker handles offline.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("too large")
        return self.rfile.read(length)

    def static_allowed(self):
        if self.path.split("?")[0].lstrip("/") in STATIC:
            return True
        self.send_json({"error": "not found"}, 404)
        return False

    def do_HEAD(self):
        if self.static_allowed():
            super().do_HEAD()

    # GET /healthz · /api/cases · /api/cases/<id> · /api/videos/<id>/<n>
    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts == ["healthz"]:
            with lock:
                n = len(load_cases())
            return self.send_json({"ok": True, "cases": n})
        if parts[:2] == ["api", "cases"]:
            if len(parts) == 2:
                reseed_if_stale()
            with lock:
                cases = load_cases()
            if len(parts) == 2:
                return self.send_json(list(cases.values()))
            c = cases.get(parts[2])
            return self.send_json(c) if c else self.send_json({"error": "not found"}, 404)
        if parts[:2] == ["api", "videos"] and len(parts) == 4:
            cid, n = parts[2], parts[3]
            if not (SAFE_ID.match(cid) and n.isdigit()):
                return self.send_json({"error": "bad id"}, 400)
            matches = list(VIDEOS.glob(f"{cid}_{n}.*"))
            if not matches:
                with lock:
                    seeded = load_cases().get(cid, {}).get("seeded")
                matches = list(DEMO_SWEEPS.glob(f"sweep_{n}.*")) if seeded else []
            if not matches:
                return self.send_json({"error": "not found"}, 404)
            return self.send_file(matches[0])
        if self.static_allowed():
            super().do_GET()

    def send_file(self, path):
        # Range support so <video> can seek.
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            self.wfile.write(f.read(end - start + 1))

    # POST /api/cases · /api/cases/<id>/videos/<n> · /api/cases/<id>/validation · /api/cases/<id>/ack
    def do_POST(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        state["last_post"] = now_ms()
        try:
            if parts == ["api", "cases"]:
                case = json.loads(self.read_body(2 * 1024 * 1024))
                if not isinstance(case, dict):
                    return self.send_json({"error": "bad case"}, 400)
                cid = case.get("caseId", "")
                if not isinstance(cid, str) or not SAFE_ID.match(cid):
                    return self.send_json({"error": "bad caseId"}, 400)
                with lock:
                    cases = load_cases()
                    prev = cases.get(cid, {})
                    if prev.get("seeded"):
                        return self.send_json({"error": "seeded case is read-only"}, 409)
                    case.pop("seeded", None)
                    case["receivedAt"] = prev.get("receivedAt") or int(time.time() * 1000)
                    case["uploaded"] = prev.get("uploaded", [])
                    case["validation"] = prev.get("validation")
                    cases[cid] = case
                    trim_live_cases(cases)
                    save_cases(cases)
                return self.send_json({"ok": True, "receivedAt": case["receivedAt"]})

            if len(parts) >= 4 and parts[:2] == ["api", "cases"]:
                cid = parts[2]
                if not SAFE_ID.match(cid):
                    return self.send_json({"error": "bad caseId"}, 400)

                if parts[3] == "videos" and len(parts) == 5 and parts[4].isdigit():
                    n = int(parts[4])
                    data = self.read_body(MAX_VIDEO_BYTES)   # read first so a refusal reaches the client
                    if not 1 <= n <= 6:
                        return self.send_json({"error": "bad sweep number"}, 400)
                    with lock:
                        if cid not in load_cases():
                            return self.send_json({"error": "not found"}, 404)
                    ctype = (self.headers.get("Content-Type") or "").split(";")[0]
                    ext = {"video/webm": ".webm", "video/mp4": ".mp4"}.get(ctype, ".bin")
                    for old in VIDEOS.glob(f"{cid}_{n}.*"):
                        old.unlink(missing_ok=True)
                    (VIDEOS / f"{cid}_{n}{ext}").write_bytes(data)
                    trim_videos()
                    with lock:
                        cases = load_cases()
                        if cid in cases:
                            cases[cid]["uploaded"] = sorted(set(cases[cid].get("uploaded", [])) | {n})
                            save_cases(cases)
                    return self.send_json({"ok": True})

                if parts[3] in ("validation", "ack") and len(parts) == 4:
                    body = json.loads(self.read_body(256 * 1024))
                    if not isinstance(body, dict):
                        return self.send_json({"error": "bad body"}, 400)
                    with lock:
                        cases = load_cases()
                        if cid not in cases:
                            return self.send_json({"error": "not found"}, 404)
                        now = int(time.time() * 1000)
                        if parts[3] == "validation":
                            cases[cid]["validation"] = {**body, "at": now}
                        else:
                            if cases[cid].get("validation"):
                                cases[cid]["validation"]["ackAt"] = now
                                cases[cid]["validation"]["doneActions"] = body.get("doneActions", [])
                        save_cases(cases)
                    return self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json({"error": str(e)}, 400)
        return self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    if "--seed" in sys.argv:
        from seed_demo import seed
        with lock:
            cases = seed(load_cases())
            save_cases(cases)
        state.update(seed=True, seeded_at=now_ms())
        print(f"Seeded demo caseload: {sum(1 for c in cases.values() if c.get('seeded'))} cases from 5 Puskesmas")
    print(f"Jagajanin on http://localhost:{PORT}  (midwife: /  ·  doctor: /doctor.html)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
