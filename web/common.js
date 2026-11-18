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

// Where do the agents run? Three modes, tried in this order:
//   "server"  server.py answers on this same site (run.bat on your laptop, or the Render site itself)
//   "shared"  the shared online server.py on Render: every visitor sees the same live registrar
//   "browser" fallback: the SAME Python code runs inside this browser (Pyodide), private to you
const SHARED_API = (() => {
  try { return localStorage.getItem("coursepathApi") || ""; } catch (e) { return ""; }
})() || "https://coursepath-planner.onrender.com";
let API_BASE = "";

async function probe(base, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    const r = await fetch(base + "/api/meta", { cache: "no-store", signal: ctrl.signal });
    return r.ok && (r.headers.get("content-type") || "").includes("json");
  } catch (e) {
    return false;
  } finally { clearTimeout(timer); }
}

function notice(html) {
  let el = document.getElementById("modeNotice");
  if (!el) {
    el = document.createElement("div");
    el.id = "modeNotice";
    el.style.cssText = "margin:0 auto;max-width:1100px;padding:8px 14px;border-radius:10px;font:13px system-ui,sans-serif;" +
      "background:#3b2f0b;color:#fde68a;border:1px solid #6b5412;display:flex;gap:10px;align-items:center;flex-wrap:wrap";
    const nav = document.querySelector("nav.nav");
    (nav ? nav.parentNode : document.body).insertBefore(el, nav ? nav.nextSibling : document.body.firstChild);
  }
  el.innerHTML = html;
}

const MODE = (async () => {
  if (await probe("", 5000)) return "server";
  if (location.origin !== SHARED_API && (await probe(SHARED_API, 9000))) {
    API_BASE = SHARED_API;
    return "shared";
  }
  // The free shared server sleeps when nobody uses it. Wake it up, and use the browser copy meanwhile.
  notice("<span>⏳ The shared live server is waking up (free hosting sleeps when idle). For now you have a " +
         "<b>private copy running in your browser</b>.</span>");
  probe(SHARED_API, 90000).then((up) => {
    if (up) notice("<span>✅ The shared live server is ready.</span><button onclick='location.reload()' " +
                   "style='padding:4px 10px;border-radius:6px;border:0;cursor:pointer'>Reload to join everyone</button>");
  });
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
  const r = await fetch(API_BASE + path, opt);
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
    if (mode === "server" || mode === "shared") {
      const label = mode === "shared" ? "live · shared with everyone" : "live";
      es = new EventSource(API_BASE + "/api/stream");
      es.onopen = () => setDot(true, label);
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
