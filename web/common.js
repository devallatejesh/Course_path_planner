// Shared helpers for every page: navigation bar, API calls, live event stream.
const NAV = [
  ["index.html", "Home"], ["planner.html", "Planner"], ["registrar.html", "Live Registrar"], ["data.html", "Datasets"],
];

function renderNav() {
  const here = location.pathname.split("/").pop() || "index.html";
  const el = document.createElement("nav");
  el.className = "nav";
  el.innerHTML = `<a class="logo" href="index.html"><span>CP</span> CoursePath Planner</a>
    <div class="links">${NAV.map(([h, t]) => `<a href="${h}" class="${h === here ? "active" : ""}">${t}</a>`).join("")}</div>
    <span class="live-dot" id="liveDot"><i></i><span>offline</span></span>`;
  document.body.prepend(el);
}

// Is server.py running (run.bat)? If not - e.g. on the hosted Vercel site - the SAME Python
// code runs inside the browser instead (browser-python.js loads Pyodide + browser_api.py).
const MODE = (async () => {
  try {
    const r = await fetch("/api/meta", { cache: "no-store" });
    if (r.ok && (r.headers.get("content-type") || "").includes("json")) return "server";
  } catch (e) { /* no server answering */ }
  await new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "browser-python.js";
    s.onload = ok;
    s.onerror = fail;
    document.head.appendChild(s);
  });
  return "browser";
})();

async function api(path, body) {
  if ((await MODE) === "browser") {
    const py = await BrowserPython.ready();
    const res = py.request(body === undefined ? "GET" : "POST", path, body);
    if (res.code >= 400) throw new Error((res.body && res.body.error) || "error " + res.code);
    return res.body;
  }
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// Real-time feed. With server.py: Server-Sent Events (the browser reconnects automatically).
// In browser mode: the same messages straight from the Python event bus running in this page.
function liveStream(onMessage) {
  const dot = document.getElementById("liveDot");
  const setDot = (on, text) => { dot.classList.toggle("on", on); dot.querySelector("span").textContent = text; };
  let es = null;
  MODE.then(async (mode) => {
    if (mode === "server") {
      es = new EventSource("/api/stream");
      es.onopen = () => setDot(true, "live");
      es.onerror = () => setDot(false, "reconnecting…");
      es.onmessage = (e) => onMessage(JSON.parse(e.data));
    } else {
      const py = await BrowserPython.ready();
      setDot(true, "live (in browser)");
      onMessage(py.hello());
      py.subscribe(onMessage);
    }
  });
  return { close() { if (es) es.close(); } };
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => Math.round(x * 100) + "%";
const AGENT_CLASS = { "Goal Agent": "a-goal", "Constraint Agent": "a-constraint", "Planning Agent": "a-planning", "Adaptation Agent": "a-adaptation" };

renderNav();
