// Hosted (Vercel) version: run the project's own Python code in the browser with Pyodide.
// Used only when no server.py is answering. Locally, run.bat + server.py work exactly as before.
window.BrowserPython = (() => {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  const FILES = ["server.py", "agents.py", "catalog.py", "browser_api.py",
    "data/courses.csv", "data/grade_history.csv", "data/career_outcomes.csv", "data/requirements.json",
    "data/tracks.json", "data/students.json", "data/sources.json"];
  let starting = null;

  function loader(text) {
    let el = document.getElementById("pyLoader");
    if (!el) {
      el = document.createElement("div");
      el.id = "pyLoader";
      el.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;padding:10px 16px;" +
        "border-radius:10px;background:#1f2937;color:#fff;font:14px system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.25)";
      document.body.appendChild(el);
    }
    if (text) el.textContent = text; else el.remove();
  }

  function loadScript(src) {
    return new Promise((ok, fail) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = ok;
      s.onerror = () => fail(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  async function start() {
    loader("Starting the Python agents in your browser… (first visit takes a few seconds)");
    await loadScript(PYODIDE + "pyodide.js");
    const py = await loadPyodide({ indexURL: PYODIDE });
    py.FS.mkdirTree("/app/data");
    await Promise.all(FILES.map(async (f) => {
      const r = await fetch("/" + f, { cache: "no-cache" });
      if (!r.ok) throw new Error("missing " + f);
      py.FS.writeFile("/app/" + f, await r.text());
    }));
    py.runPython("import sys; sys.path.insert(0, '/app')");
    const mod = py.pyimport("browser_api");

    const listeners = new Set();
    const flush = () => {
      for (const m of JSON.parse(mod.drain())) {
        const msg = JSON.parse(m);
        listeners.forEach((fn) => fn(msg));
      }
    };
    // the live registrar: same 2.5 s step as server.registrar_loop()
    setInterval(() => { try { mod.tick(); flush(); } catch (e) { console.warn("registrar", e); } }, 2500);
    loader(null);

    return {
      request(method, path, body) {
        const out = JSON.parse(mod.request(method, path, body === undefined ? "" : JSON.stringify(body)));
        setTimeout(flush, 0);          // deliver any re-plan events after the answer, like the real server
        return out;
      },
      hello() { return JSON.parse(mod.hello()); },
      subscribe(fn) { listeners.add(fn); },
    };
  }

  return {
    ready() {
      if (!starting) {
        starting = start().catch((e) => {
          loader("Could not start Python in the browser: " + e.message + ". Please reload.");
          throw e;
        });
      }
      return starting;
    },
  };
})();
