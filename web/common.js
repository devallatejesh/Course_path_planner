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

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// Real-time feed from the server (Server-Sent Events). The browser reconnects automatically.
function liveStream(onMessage) {
  const dot = document.getElementById("liveDot");
  const es = new EventSource("/api/stream");
  es.onopen = () => { dot.classList.add("on"); dot.querySelector("span").textContent = "live"; };
  es.onerror = () => { dot.classList.remove("on"); dot.querySelector("span").textContent = "reconnecting…"; };
  es.onmessage = (e) => onMessage(JSON.parse(e.data));
  return es;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => Math.round(x * 100) + "%";
const AGENT_CLASS = { "Goal Agent": "a-goal", "Constraint Agent": "a-constraint", "Planning Agent": "a-planning", "Adaptation Agent": "a-adaptation" };

renderNav();
