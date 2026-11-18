"""
CoursePath Planner - web server (Python standard library only, nothing to install).

  python server.py            then open  http://localhost:8000

What runs here:
  * the website (files in web/)
  * a JSON API that runs the four agents
  * a simulated live registrar: seat counts start from each course's REAL typical enrolment
    (UIUC grade data); seats fill up, sections open, courses get cancelled.
    Every change is pushed to the browser in real time (Server-Sent Events), and the
    Adaptation Agent automatically re-plans any student whose next semester is affected.
"""
import json
import os
import queue
import random
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if not os.path.exists(os.path.join(HERE, "data", "courses.csv")):
    # first run: download the real public datasets and build the files the agents read
    import build_data
    import download_data
    download_data.main()
    build_data.main()

from agents import Orchestrator  # noqa: E402
from catalog import Catalog  # noqa: E402

WEB = os.path.join(HERE, "web")
PORT = int(os.environ.get("PORT", 8000))
HOST = os.environ.get("HOST", "127.0.0.1")   # Render sets HOST=0.0.0.0 so the server is reachable online
# Web pages hosted elsewhere (the Vercel site) may call this server's /api from the browser.
ALLOWED_ORIGINS = {"https://coursepathplanner.vercel.app"} | {
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}
LOCK = threading.RLock()          # agents and plan states are shared between threads


# ---------------------------------------------------------------------------
# Event bus -> every open browser tab gets a queue
# ---------------------------------------------------------------------------
class Bus:
    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, msg):
        data = json.dumps(msg)
        with self.lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass


BUS = Bus()


# ---------------------------------------------------------------------------
# Live registrar simulation (seat counts for the upcoming registration window)
# ---------------------------------------------------------------------------
class Registrar:
    def __init__(self, cat):
        self.cat = cat
        self.feed = []
        self.reset()

    def reset(self):
        rnd = random.Random()
        self.seats = {}
        for code, c in self.cat.courses.items():
            cap = c["capacity"]          # real average enrolment per term from the UIUC GPA dataset
            start = rnd.uniform(0.6, 0.9) if c["category"] == "elective" else rnd.uniform(0.35, 0.7)
            self.seats[code] = {"capacity": cap, "enrolled": int(cap * start), "status": "open"}

    def status(self, code):
        return self.seats[code]["status"]

    def snapshot(self):
        return {c: [s["enrolled"], s["capacity"], s["status"]] for c, s in self.seats.items()}

    def apply(self, code, action, source="registrar"):
        """Change a course's state and return the event (or None)."""
        s = self.seats[code]
        name = self.cat.courses[code]["name"]
        if action == "fill":
            s["enrolled"], s["status"] = s["capacity"], "full"
            ev = ("full", f"{code} {name} is now FULL ({s['capacity']}/{s['capacity']})")
        elif action == "cancel":
            s["status"] = "cancelled"
            ev = ("cancelled", f"{code} {name} was CANCELLED for next semester")
        elif action == "reopen":
            extra = max(10, s["capacity"] // 6)
            s["capacity"] += extra
            s["status"] = "open"
            ev = ("reopened", f"New section opened for {code} {name} (+{extra} seats)")
        else:
            return None
        msg = {"type": "registrar", "kind": ev[0], "course": code, "text": ev[1],
               "source": source, "time": time.strftime("%H:%M:%S")}
        self.feed = (self.feed + [msg])[-40:]
        return msg

    def tick(self, rnd):
        """One step of simulated registration activity."""
        events = []
        open_codes = [c for c, s in self.seats.items() if s["status"] == "open"]
        for code in rnd.sample(open_codes, k=min(7, len(open_codes))):
            s = self.seats[code]
            elective = self.cat.courses[code]["category"] == "elective"
            step = max(1, s["capacity"] // 60)
            s["enrolled"] += step * rnd.choice([0, 1, 1, 2, 3] if elective else [-1, 0, 1, 1, 2])
            s["enrolled"] = max(0, s["enrolled"])
            if s["enrolled"] >= s["capacity"]:
                events.append(self.apply(code, "fill"))
        if rnd.random() < 0.015:
            pool = [c for c in open_codes if self.cat.courses[c]["category"] == "elective"]
            if pool:
                events.append(self.apply(rnd.choice(pool), "cancel"))
        full = [c for c, s in self.seats.items() if s["status"] == "full"]
        if full and rnd.random() < 0.05:
            events.append(self.apply(rnd.choice(full), "reopen"))
        return [e for e in events if e]


CAT = Catalog()
REG = Registrar(CAT)
ORCH = Orchestrator(CAT, REG)
STATES = {}                        # plan_id -> state (insertion order = oldest first)
MAX_PLANS = 300


def react(event):
    """Adaptation Agent: re-plan every student whose next semester contains the affected course."""
    BUS.publish(event)
    if event["kind"] not in ("full", "cancelled"):
        return
    with LOCK:
        for pid, st in list(STATES.items()):
            if ORCH.adaptation.affected(st, event["course"]):
                res = ORCH.adaptation.handle(st, {"type": "seat_full" if event["kind"] == "full" else "cancelled",
                                                   "course": event["course"]})
                BUS.publish({"type": "replan", "plan_id": pid, "reason": event["text"],
                             "diff": res["diff"], "trace": res["trace"], "view": ORCH.view(pid, st)})


def registrar_loop():
    rnd = random.Random()
    while True:
        time.sleep(2.5)
        try:
            with LOCK:
                events = REG.tick(rnd)
            for e in events:
                react(e)
            BUS.publish({"type": "seats", "seats": REG.snapshot()})
        except Exception as exc:          # keep the feed alive no matter what
            print("registrar error:", exc)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "application/javascript",
        ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
        ".csv": "text/csv; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # args[0] is the request line for normal logs, but an HTTPStatus for error logs
        first = str(args[0]) if args else ""
        if "/api/stream" not in first:
            sys.stderr.write("  %s\n" % (fmt % args))

    def do_HEAD(self):
        """Some browsers and tools probe with HEAD; answer it instead of logging an error."""
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        """CORS pre-flight: lets the Vercel pages send JSON POSTs to this server."""
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- helpers ----
    def cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS or origin.startswith(("http://localhost:", "http://127.0.0.1:")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        if path.startswith("/data/"):
            full = os.path.normpath(os.path.join(HERE, path.lstrip("/")))
            root = os.path.join(HERE, "data")
        else:
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            root = WEB
        if not full.startswith(root) or not os.path.isfile(full):
            return self.send_json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def stream(self):
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = BUS.subscribe()
        try:
            with LOCK:
                first = {"type": "hello", "seats": REG.snapshot(), "feed": REG.feed}
            self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            BUS.unsubscribe(q)
            self.close_connection = True

    # ---- routes ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/stream":
            return self.stream()
        if path == "/api/meta":
            return self.send_json(CAT.public())
        if path == "/api/stats":
            return self.send_json(CAT.stats())
        if path == "/api/seats":
            with LOCK:
                return self.send_json({"seats": REG.snapshot(), "feed": REG.feed})
        if path.startswith("/api/plan/"):
            pid = path.rsplit("/", 1)[-1]
            with LOCK:
                if pid not in STATES:
                    return self.send_json({"error": "unknown plan"}, 404)
                return self.send_json(ORCH.view(pid, STATES[pid]))
        return self.static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.body()
        except ValueError:
            return self.send_json({"error": "bad JSON"}, 400)
        if not isinstance(data, dict):
            return self.send_json({"error": "expected a JSON object"}, 400)
        try:
            if path == "/api/plan":
                return self.create_plan(data)
            if path == "/api/event":
                return self.event(data)
            if path == "/api/review":
                decision = data.get("decision", "approved")
                if decision not in ("approved", "changes"):
                    return self.send_json({"error": "decision must be 'approved' or 'changes'"}, 400)
                with LOCK:
                    st = STATES.get(data.get("plan_id"))
                    if st is None:
                        return self.send_json({"error": "unknown plan"}, 404)
                    st["review"] = {"status": decision, "note": str(data.get("note") or "")[:300],
                                    "time": time.strftime("%H:%M:%S")}
                    return self.send_json(ORCH.view(data["plan_id"], st))
            if path == "/api/registrar":
                code, action = data.get("course"), data.get("action")
                if code not in CAT.courses:
                    return self.send_json({"error": "unknown course"}, 400)
                if action not in ("fill", "cancel", "reopen"):
                    return self.send_json({"error": "action must be fill, cancel or reopen"}, 400)
                with LOCK:
                    ev = REG.apply(code, action, source="demo control")
                if ev:
                    threading.Thread(target=react, args=(ev,), daemon=True).start()
                    BUS.publish({"type": "seats", "seats": REG.snapshot()})
                return self.send_json({"ok": True, "event": ev})
            if path == "/api/registrar/reset":
                with LOCK:
                    REG.reset()
                    REG.feed = []
                BUS.publish({"type": "seats", "seats": REG.snapshot()})
                return self.send_json({"ok": True})
        except Exception:                     # never drop the connection: report the error instead
            traceback.print_exc()
            return self.send_json({"error": "internal error - see the server window"}, 500)
        return self.send_json({"error": "not found"}, 404)

    @staticmethod
    def clean_options(opts):
        """Accept only valid form options; silently ignore anything else."""
        if not isinstance(opts, dict):
            return {}
        out = {}
        if opts.get("workload") in ("light", "balanced", "heavy"):
            out["workload"] = opts["workload"]
        for key, lo, hi in (("max_credits", 9, 18), ("target_semesters", 1, 12)):
            try:
                if opts.get(key) not in (None, ""):
                    out[key] = max(lo, min(hi, int(opts[key])))
            except (TypeError, ValueError):
                pass
        pool = set(CAT.requirements["elective_pool"])
        for key in ("prefer", "avoid"):
            if isinstance(opts.get(key), list):
                codes = [c for c in opts[key] if c in pool]
                if codes:
                    out[key] = codes
        if opts.get("track") in CAT.tracks:
            out["track"] = opts["track"]
        return out

    def create_plan(self, data):
        sid = data.get("student_id")
        if sid and sid in CAT.students:
            student = CAT.students[sid]
        else:
            name = str(data.get("name") or "").strip()[:40] or "New Student"
            student = {"id": "NEW-" + uuid.uuid4().hex[:4].upper(), "name": name,
                       "completed_semesters": 0, "transcript": []}
        goal = str(data.get("goal") or student.get("goal") or "").strip()[:600]
        options = self.clean_options(data.get("options"))
        with LOCK:
            st = ORCH.new_state(student, goal, options)
            st["trace"] = ORCH.plan(st)
            pid = uuid.uuid4().hex[:8]
            STATES[pid] = st
            while len(STATES) > MAX_PLANS:          # public site: keep only the newest plans in memory
                STATES.pop(next(iter(STATES)))
            return self.send_json({**ORCH.view(pid, st), "trace": st["trace"]})

    def event(self, data):
        pid = data.get("plan_id")
        kind = data.get("type")
        with LOCK:
            st = STATES.get(pid)
            if st is None:
                return self.send_json({"error": "unknown plan"}, 404)
            if kind not in ("drop", "complete_semester", "goal_change", "seat_full", "cancelled"):
                return self.send_json({"error": "unknown event type"}, 400)
            ev = {"type": kind}
            if kind == "drop":
                try:
                    sem = int(data.get("semester"))
                except (TypeError, ValueError):
                    return self.send_json({"error": "drop needs a semester number"}, 400)
                sem_plan = next((s for s in st["plan"]["semesters"] if s["sem"] == sem), None)
                if not sem_plan or data.get("course") not in sem_plan["codes"]:
                    return self.send_json({"error": "that course is not planned in that semester"}, 400)
                ev.update(course=data["course"], semester=sem)
            elif kind in ("seat_full", "cancelled"):
                if data.get("course") not in CAT.courses:
                    return self.send_json({"error": "unknown course"}, 400)
                ev["course"] = data["course"]
            elif kind == "goal_change":
                goal = str(data.get("goal") or "").strip()[:600]
                if not goal:
                    return self.send_json({"error": "goal_change needs a goal"}, 400)
                ev["goal"] = goal
            res = ORCH.adaptation.handle(st, ev)
            if res is None:
                return self.send_json({"error": "unknown event"}, 400)
            return self.send_json({**ORCH.view(pid, st), "trace": res["trace"], "diff": res["diff"],
                                   "event": res["event"]})


def main():
    threading.Thread(target=registrar_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"CoursePath Planner running ->  http://localhost:{PORT}   (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
