"""
Runs the SAME server code inside the visitor's browser (Pyodide), for the hosted (Vercel) version.

Vercel can only serve files and short-lived functions, so it cannot keep the planner states,
run the live-registrar loop or hold the Server-Sent-Events stream that server.py needs.
Instead the browser runs Python itself:

  * server.py is imported unchanged: the Catalog, the four agents (agents.py), the Registrar,
    STATES and every /api/... route are exactly the ones used by run.bat.
  * request()  calls server.Handler.do_GET / do_POST without a socket.
  * tick()     is one step of server.registrar_loop() - the page calls it every 2.5 seconds.
  * drain()    hands the event-bus messages to the page (instead of the /api/stream connection).
"""
import json
import queue
import random

import server

# The browser has no real threads: run "background" work (Adaptation Agent re-plans) immediately.
class _RunNow:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


server.threading.Thread = _RunNow

_STREAM = server.BUS.subscribe()      # this page's "connection" to the event bus
_RND = random.Random()


class _Call(server.Handler):
    """A server.Handler that answers one request in memory instead of over a socket."""

    def __init__(self, path, data):
        self.path, self._data = path, data
        self.code, self.result = 200, None

    def body(self):
        return self._data

    def send_json(self, obj, code=200):
        self.code, self.result = code, obj


def request(method, path, body_json=""):
    data = json.loads(body_json) if body_json else {}
    call = _Call(path, data)
    (call.do_POST if method == "POST" else call.do_GET)()
    return json.dumps({"code": call.code, "body": call.result})


def hello():
    with server.LOCK:
        return json.dumps({"type": "hello", "seats": server.REG.snapshot(), "feed": server.REG.feed})


def tick():
    """One step of server.registrar_loop()."""
    with server.LOCK:
        events = server.REG.tick(_RND)
    for e in events:
        server.react(e)
    server.BUS.publish({"type": "seats", "seats": server.REG.snapshot()})


def drain():
    out = []
    while True:
        try:
            out.append(_STREAM.get_nowait())
        except queue.Empty:
            return json.dumps(out)
