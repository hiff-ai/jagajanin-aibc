"""Smoke-test the demo server: static allowlist, input checks, upload caps, eviction and idle re-seed.

    python3 tests/test_server.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"
ok_all = True


def req(method, path, body=None, ctype="application/json"):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": ctype} if data is not None else {})
    try:
        with urllib.request.urlopen(r, timeout=10) as res:
            raw = res.read()
            return res.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(name, cond, extra=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


data_dir = tempfile.mkdtemp(prefix="jj_test_")
env = dict(os.environ, JJ_DATA=data_dir, PORT=str(PORT), RESEED_HOURS="0.0005", IDLE_MINUTES="0.02",
           MAX_VIDEO_MB="1", VIDEO_BUDGET_MB="1", MAX_LIVE_CASES="2")
srv = subprocess.Popen([sys.executable, "server.py", "--seed"], cwd=APP, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    for _ in range(50):
        try:
            if req("GET", "/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)

    # static allowlist
    for p, want in [("/", 200), ("/?lang=en", 200), ("/index.html", 200), ("/doctor.html?lang=en", 200),
                    ("/app.js", 200), ("/sw.js", 200), ("/manifest.json", 200), ("/icon.svg", 200),
                    ("/server.py", 404), ("/seed_demo.py", 404), ("/Dockerfile", 404), ("/data/cases.json", 404),
                    ("/data/", 404), ("/../server.py", 404), ("/%2e%2e/server.py", 404)]:
        s, _ = req("GET", p)
        check(f"GET {p} -> {want}", s == want, s)
    s, _ = req("HEAD", "/server.py")
    check("HEAD /server.py -> 404", s == 404, s)

    s, raw = req("GET", "/healthz")
    check("healthz ok", s == 200 and json.loads(raw)["ok"], raw[:60])

    s, raw = req("GET", "/api/cases")
    cases = json.loads(raw)
    seeded = [c for c in cases if c.get("seeded")]
    check("seeded caseload present", len(seeded) >= 60, len(seeded))
    sid = seeded[0]["caseId"]
    old_received = {c["caseId"]: c["receivedAt"] for c in seeded}

    # visitor cases
    for i in range(3):
        s, _ = req("POST", "/api/cases", {"caseId": f"TEST-{i}", "visit": {"age": 30}, "seeded": True})
        check(f"POST case TEST-{i}", s == 200, s)
        time.sleep(0.01)
    s, raw = req("GET", "/api/cases")
    ids = {c["caseId"]: c for c in json.loads(raw)}
    check("visitor case cap keeps newest 2", "TEST-0" not in ids and "TEST-1" in ids and "TEST-2" in ids)
    check("visitor cannot mark a case seeded", not ids["TEST-2"].get("seeded"))
    s, _ = req("POST", "/api/cases", {"caseId": sid, "visit": {}})
    check("overwriting a seeded case is refused", s == 409, s)
    s, _ = req("POST", "/api/cases", ["not", "a", "dict"])
    check("non-object case rejected", s == 400, s)
    s, _ = req("POST", "/api/cases", b"{bad json", )
    check("bad JSON rejected", s == 400, s)

    # videos
    blob = os.urandom(600 * 1024)
    s, _ = req("POST", "/api/cases/TEST-2/videos/1", blob, "video/webm")
    check("upload sweep 1", s == 200, s)
    s, raw = req("GET", "/api/videos/TEST-2/1")
    check("download sweep 1", s == 200 and len(raw) == len(blob), s)
    s, _ = req("POST", "/api/cases/TEST-2/videos/7", blob, "video/webm")
    check("sweep 7 rejected", s == 400, s)
    s, _ = req("POST", "/api/cases/NOPE/videos/1", blob, "video/webm")
    check("video for unknown case rejected", s == 404, s)
    try:
        s, _ = req("POST", "/api/cases/TEST-2/videos/2", os.urandom(2 * 1024 * 1024), "video/webm")
    except urllib.error.URLError:
        s = "closed early"
    check("oversize video rejected", s in (400, "closed early"), s)
    req("POST", "/api/cases/TEST-2/videos/2", blob, "video/webm")
    vids = sorted(os.listdir(os.path.join(data_dir, "videos")))
    check("video budget evicts oldest", vids == ["TEST-2_2.webm"], vids)
    s, raw = req("GET", f"/api/videos/{sid}/3")
    check("seeded case serves synthetic sweep", s == 200 and len(raw) > 1000, s)

    # validation + ack, including on a seeded case
    s, _ = req("POST", f"/api/cases/{sid}/validation", {"decision": "rujuk", "note": "test"})
    check("validate seeded case", s == 200, s)
    s, _ = req("POST", "/api/cases/TEST-2/validation", {"decision": "aman"})
    check("validate visitor case", s == 200, s)
    s, _ = req("POST", "/api/cases/TEST-2/ack", {"doneActions": ["a"]})
    check("ack visitor case", s == 200, s)
    s, _ = req("POST", "/api/cases/TEST-2/validation", b'"x"')
    check("non-object validation rejected", s == 400, s)

    # re-seed after idle: timestamps refresh, visitor cases survive
    time.sleep(2.0)
    s, raw = req("GET", "/api/cases")
    after = {c["caseId"]: c for c in json.loads(raw)}
    refreshed = sum(1 for k, v in old_received.items() if k in after and after[k]["receivedAt"] > v)
    check("seeded cases re-seeded with fresh timestamps", refreshed >= len(old_received) - 1, refreshed)
    check("same seeded case IDs after re-seed", set(old_received) <= set(after))
    check("visitor cases kept through re-seed", "TEST-2" in after and after["TEST-2"].get("validation"))

    # busy: a POST resets the idle clock, so no re-seed mid-session
    req("POST", "/api/cases/TEST-2/ack", {"doneActions": []})
    before = {c["caseId"]: c["receivedAt"] for c in json.loads(req("GET", "/api/cases")[1]) if c.get("seeded")}
    time.sleep(0.3)
    later = {c["caseId"]: c["receivedAt"] for c in json.loads(req("GET", "/api/cases")[1]) if c.get("seeded")}
    check("no re-seed while someone is active", before == later)
finally:
    srv.terminate()
    out = srv.communicate(timeout=5)[0].decode()
    print("--- server log tail ---")
    print("\n".join(out.splitlines()[-6:]))
print("ALL PASSED" if ok_all else "SOME FAILED")
sys.exit(0 if ok_all else 1)
