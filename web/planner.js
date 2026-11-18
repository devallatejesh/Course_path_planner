// Planner page: sends student input to the agents, animates their conversation,
// draws the roadmap, and reacts live when the Adaptation Agent re-plans.
let META = null, VIEW = null, SEATS = {}, BUSY = false;
const pref = {};                       // elective code -> "on" | "no"
const $ = (id) => document.getElementById(id);

async function init() {
  META = await api("/api/meta");
  $("student").innerHTML = META.students.map((s) =>
    `<option value="${s.id}">${esc(s.name)} · finished sem ${s.completed_semesters}</option>`).join("")
    + `<option value="new">+ New student (semester 1)</option>`;
  $("student").onchange = onStudent;
  onStudent();
  $("chips").innerHTML = META.requirements.elective_pool.map((c) =>
    `<button type="button" class="chip" data-c="${c}" title="${esc(META.courses[c].name)}: click once = want, twice = avoid, three times = neutral">${c} ${esc(META.courses[c].name)}</button>`).join("");
  $("chips").onclick = (e) => {
    const b = e.target.closest(".chip"); if (!b) return;
    const c = b.dataset.c;
    pref[c] = pref[c] === "on" ? "no" : pref[c] === "no" ? undefined : "on";
    b.className = "chip " + (pref[c] || "");
    b.setAttribute("aria-label", `${c} ${pref[c] === "on" ? "wanted" : pref[c] === "no" ? "avoided" : "neutral"}`);
  };
  $("run").onclick = run;
  liveStream(onLive);
}

function onStudent() {
  const v = $("student").value;
  $("newName").style.display = v === "new" ? "" : "none";
  const s = META.students.find((x) => x.id === v);
  $("goal").value = s ? s.goal : "I want to become a machine learning engineer and graduate on time.";
}

async function run() {
  if (BUSY) return;
  BUSY = true; $("run").disabled = true;
  const sid = $("student").value;
  const body = {
    student_id: sid === "new" ? null : sid, name: $("name").value, goal: $("goal").value,
    options: {
      workload: $("workload").value || null, target_semesters: +$("target").value || null,
      prefer: Object.keys(pref).filter((k) => pref[k] === "on"), avoid: Object.keys(pref).filter((k) => pref[k] === "no"),
    },
  };
  try {
    $("trace").innerHTML = ""; $("banner").className = "banner";
    const view = await api("/api/plan", body);
    await playTrace(view.trace, true);
    VIEW = view; render();
  } catch (e) { alert("Error: " + e.message); }
  BUSY = false; $("run").disabled = false;
}

// ---- agent conversation, played back step by step ---------------------------
async function playTrace(items, reset) {
  const agents = [...document.querySelectorAll(".agent")];
  if (reset) agents.forEach((a) => a.classList.remove("active", "done"));
  const box = $("trace");
  if (box.querySelector("p.muted")) box.innerHTML = "";
  for (const m of items) {
    agents.forEach((a) => {
      if (a.dataset.a === m.agent) a.classList.add("active");
      else if (a.classList.contains("active")) { a.classList.remove("active"); a.classList.add("done"); }
    });
    const d = document.createElement("div");
    d.className = `msg ${m.kind} ${AGENT_CLASS[m.agent] || ""}`;
    d.innerHTML = `<span class="ms">${m.ms} ms</span><b>${esc(m.agent)}</b><div>${esc(m.text)}</div>`;
    box.appendChild(d); box.scrollTop = box.scrollHeight;
    await new Promise((r) => setTimeout(r, m.kind === "handoff" ? 260 : 110));
  }
  agents.forEach((a) => { if (a.classList.contains("active")) { a.classList.remove("active"); a.classList.add("done"); } });
}

// ---- roadmap ----------------------------------------------------------------
function render(changes = {}) {
  const v = VIEW, p = v.plan, o = v.objective, st = v.student;
  const next = st.completed_semesters + 1;
  const hard = p.checks.filter((c) => !c.ok && !c.soft).length;
  const tracks = o.tracks.map(([t, w, label]) => `<span class="pill">${esc(label)} ${pct(w)}</span>`).join(" ");
  const passedList = Object.keys(st.passed);
  const rv = v.review.status;

  let html = `
    <div class="objective">
      <b>Objective:</b> ${tracks}
      <span class="pill warn">${o.workload} workload</span>
      <span class="pill">≤ ${o.max_credits} credits/sem</span>
      <span class="pill">target: sem ${o.target}</span>
      <span class="pill ok" title="US Census ACS earnings of related majors: ${esc(o.outlook.majors.join(", "))}">career data: median $${o.outlook.median.toLocaleString()} · ${(o.outlook.unemployment * 100).toFixed(1)}% unemployed</span>
      ${o.prefer.length ? `<span class="pill ok">wants ${o.prefer.join(", ")}</span>` : ""}
      ${o.avoid.length ? `<span class="pill bad">avoids ${o.avoid.join(", ")}</span>` : ""}
    </div>
    <div class="stats">
      <div class="stat"><b>${esc(st.name)}</b><span>${st.id}</span></div>
      <div class="stat"><b>${st.cgpa ?? "–"}</b><span>GPA (4.0)</span></div>
      <div class="stat"><b>${st.credits}</b><span>credit hours earned</span></div>
      <div class="stat"><b style="color:${p.delayed ? "var(--warn)" : "var(--ok)"}">Sem ${p.graduation_sem}</b><span>graduation ${p.delayed ? "(delayed)" : "(on target)"}</span></div>
      <div class="stat"><b>${p.nodes}</b><span>search states explored</span></div>
      <div class="stat"><b>v${v.version}</b><span>roadmap version</span></div>
    </div>`;

  if (st.completed_semesters > 0) {
    html += `<div class="sem locked" style="margin-bottom:12px"><div class="sem-head"><b>Completed: semesters 1–${st.completed_semesters}</b>
      <span class="small muted">${passedList.length} courses passed</span></div>
      <div class="small">${st.transcript.filter((t) => t.grade_point >= 0.67).map((t) => `<span class="tiny">${t.course} <b>${t.grade || ""}</b></span>`).join(" · ")}
      ${st.failed.length ? `<div style="margin-top:6px"><span class="pill bad">to retake: ${st.failed.join(", ")}</span></div>` : ""}</div></div>`;
  }

  html += `<div class="roadmap">` + p.semesters.map((s) => {
    const isNext = s.sem === next;
    return `<div class="sem ${isNext ? "next" : ""}">
      <div class="sem-head"><b>Semester ${s.sem} <span class="tiny muted">${s.term}</span></b>
        <span class="pill ${s.load === "heavy" ? "warn" : s.load === "light" ? "ok" : ""}">${s.credits} h · ${s.load}</span></div>
      <div class="tiny muted" style="margin:-4px 0 6px">expected GPA ≈ ${s.exp_gpa ?? "–"} (UIUC history)</div>
      ${isNext ? `<div class="tiny" style="color:var(--brand);margin:-4px 0 6px">● Registering now: real ${s.term === "Spring" ? "Spring 2027" : "Fall"} schedule + live seats</div>` : ""}
      ${s.courses.map((c) => course(c, s.sem, isNext, changes[c.code])).join("")}
    </div>`;
  }).join("") + `</div>
    <div class="legend">${["core", "project", "math", "science", "writing", "gened", "elective"].map((k) =>
      `<span><i style="background:var(--c-${k})"></i>${k}</span>`).join("")}<span>bar = real UIUC pass rate</span><span>hover a course → real prerequisite text · ✕ = drop it</span></div>`;

  html += `<div class="actions">
      <button class="btn" onclick="sendEvent({type:'complete_semester'})">⏭ Simulate end of semester ${next}</button>
      <button class="btn" onclick="sendEvent({type:'goal_change', goal: document.getElementById('goal').value})">✎ Apply changed goal</button>
      <button class="btn" onclick="fillRandom()">⚡ Demo: fill a seat in semester ${next}</button>
    </div>
    <div class="grid two">
      <div><h3>Guardrail validation <span class="pill ${hard ? "bad" : "ok"}">${hard ? hard + " failed" : "all hard rules pass"}</span></h3>
        <ul class="checks">${p.checks.map((c) => `<li class="${c.ok ? "ok" : c.soft ? "soft" : "bad"}">
          <span class="st">${c.ok ? "✓" : c.soft ? "!" : "✗"}</span><span>${esc(c.rule)}
          ${c.detail ? `<span class="tiny muted"> — ${esc(c.detail)}</span>` : ""}</span></li>`).join("")}</ul></div>
      <div><h3>Advisor review (human in the loop)</h3>
        <p class="small">Status: <span class="pill ${rv === "approved" ? "ok" : rv === "changes" ? "bad" : "warn"}">${rv}</span>
          ${v.review.note ? `<span class="tiny muted">${esc(v.review.note)}</span>` : ""}</p>
        <textarea id="note" placeholder="Advisor note (optional)" style="min-height:60px"></textarea>
        <div class="actions"><button class="btn ok" onclick="review('approved')">✓ Approve roadmap</button>
          <button class="btn" onclick="review('changes')">Request changes</button></div>
        <h3>Chosen electives</h3>
        <p class="small">${p.electives.map((e) => `<span class="pill ok">${e} ${esc(META.courses[e].name)}</span>`).join(" ")}</p>
      </div>
    </div>`;
  $("result").innerHTML = html;
}

function course(c, sem, isNext, change) {
  const seat = isNext ? seatText(c.code) : "";
  const passPct = Math.round((1 - c.risk) * 100);
  const color = passPct >= 96 ? "var(--ok)" : passPct >= 92 ? "var(--warn)" : "var(--bad)";
  const full = isNext && SEATS[c.code] && SEATS[c.code][2] !== "open";
  const badge = change ? `<span class="pill warn tiny">${change}</span>` : "";
  return `<div class="course cat-${c.category} ${change ? "changed" : ""} ${full ? "full" : ""}" title="${esc(c.prereq_text || "No prerequisites")}">
    <div class="t"><span><span class="code">${c.code}</span> ${badge}</span><span class="tiny muted">${c.credits} h
      ${c.locked ? "" : `<button class="x" title="Drop ${c.code}" aria-label="Drop ${c.code} from semester ${sem}" onclick="sendEvent({type:'drop', course:'${c.code}', semester:${sem}})">✕</button>`}</span></div>
    <div>${esc(c.name)}</div>
    <div class="why">${esc(c.reason)}</div>
    ${isNext ? `<div class="tiny seat" data-seat="${c.code}">${seat}</div>` : ""}
    <div class="riskbar" title="Expected pass chance ${passPct}%"><i style="width:${passPct}%;background:${color}"></i></div>
  </div>`;
}

function seatText(code) {
  const s = SEATS[code]; if (!s) return "";
  if (s[2] !== "open") return `<span style="color:var(--bad)">● ${s[2].toUpperCase()}</span>`;
  const left = s[1] - s[0];
  return `<span style="color:${left < 6 ? "var(--warn)" : "var(--muted)"}">● ${left} seats left (${s[0]}/${s[1]})</span>`;
}

// ---- events -----------------------------------------------------------------
function changeMap(diff) {
  const m = {};
  (diff || []).forEach((d) => { m[d.course] = d.change === "moved" ? `moved ${d.from}→${d.to}` : d.change; });
  return m;
}

async function sendEvent(ev) {
  if (!VIEW || BUSY) return;
  BUSY = true;
  try {
    const res = await api("/api/event", { plan_id: VIEW.plan_id, ...ev });
    await playTrace(res.trace, false);
    VIEW = res; render(changeMap(res.diff));
    showBanner(eventLabel(res.event), res.diff);
  } catch (e) { alert("Error: " + e.message); }
  BUSY = false;
}

function eventLabel(ev) {
  if (ev.type === "drop") return `You dropped ${ev.course} from semester ${ev.semester}.`;
  if (ev.type === "goal_change") return "Your goal changed.";
  if (ev.type === "complete_semester") return `Semester ${ev.semester} finished` + (ev.failed?.length ? ` — failed ${ev.failed.join(", ")}.` : " — all courses passed.");
  return ev.type;
}

function showBanner(reason, diff) {
  const b = $("banner");
  const moved = (diff || []).slice(0, 8).map((d) => `<b>${d.course}</b> ${d.change}${d.from ? " from S" + d.from : ""}${d.to ? " to S" + d.to : ""}`).join(", ");
  b.innerHTML = `🔄 <b>Adaptation Agent re-planned your roadmap.</b> ${esc(reason)}<br><span class="small">${diff?.length ? moved + (diff.length > 8 ? " …" : "") : "No courses had to move."}</span>`;
  b.className = "banner show";
}

async function review(decision) {
  VIEW = { ...VIEW, ...(await api("/api/review", { plan_id: VIEW.plan_id, decision, note: $("note").value })) };
  render();
}

async function fillRandom() {
  const next = VIEW.student.completed_semesters + 1;
  const sem = VIEW.plan.semesters.find((s) => s.sem === next);
  if (!sem) return;
  const c = sem.codes[Math.floor(Math.random() * sem.codes.length)];
  await api("/api/registrar", { course: c, action: "fill" });
}

// ---- live server push -------------------------------------------------------
function onLive(m) {
  if (m.seats) {
    SEATS = m.seats;
    document.querySelectorAll("[data-seat]").forEach((el) => { el.innerHTML = seatText(el.dataset.seat); });
  }
  if (m.type === "hello") m.feed.slice(-8).forEach(addFeed);
  if (m.type === "registrar") addFeed(m);
  if (m.type === "replan" && VIEW && m.plan_id === VIEW.plan_id) {
    addFeed({ kind: "replan", time: new Date().toLocaleTimeString(), text: "Your roadmap was re-planned automatically" });
    VIEW = { ...m.view, trace: m.trace };
    playTrace(m.trace, false);
    render(changeMap(m.diff));
    showBanner(m.reason, m.diff);
  }
}

function addFeed(m) {
  const box = $("feed");
  if (box.querySelector("p.muted")) box.innerHTML = "";
  const d = document.createElement("div");
  d.className = `msg ${m.kind}`;
  d.innerHTML = `<span class="ms">${esc(m.time)}</span>${esc(m.text)}`;
  box.prepend(d);
  while (box.children.length > 40) box.lastChild.remove();
}

init();
