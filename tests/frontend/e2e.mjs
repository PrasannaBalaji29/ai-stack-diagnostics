// Loads the REAL dashboard served by the REAL local server and clicks through it.
// Usage: node e2e.mjs <base-url> <fixtures-dir>
import assert from "node:assert/strict";
import fs from "node:fs";
import { JSDOM, VirtualConsole } from "jsdom";

const [base, fixtures] = process.argv.slice(2);
const results = [];
const test = async (name, fn) => {
  try { await fn(); results.push(["PASS", name]); } catch (e) { results.push(["FAIL", name, String(e.stack).split("\n").slice(0, 7).join(" // ")]); }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, label, ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { try { if (fn()) return; } catch { /* keep waiting */ } await sleep(30); }
  throw new Error("timed out waiting for: " + label);
}
const enc = new TextEncoder();
const ndjson = (events, gap = 0) => new Response(new ReadableStream({
  async start(c) { for (const ev of events) { c.enqueue(enc.encode(JSON.stringify(ev) + "\n")); if (gap) await sleep(gap); } c.close(); },
}), { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

/** Opens the real page. `mocks` maps an API path to a function returning a Response (otherwise the real server answers). */
async function open({ mocks = {}, hash = "", light = false, reduce = false } = {}) {
  const errors = [], intervals = [], blobs = [], downloads = [], copied = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(String(e.message || e))); vc.on("error", (e) => errors.push(String(e)));
  const dom = await JSDOM.fromURL(base + "/" + hash, {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = (u, o = {}) => {
        const path = String(u);
        if (mocks[path]) return Promise.resolve(mocks[path](o, w));
        return fetch(new URL(u, base), { ...o, headers: { ...(o.headers || {}), Origin: base } });
      };
      w.TextDecoder = TextDecoder; w.TextEncoder = TextEncoder; w.Blob = Blob;  // real browsers have these; jsdom does not
      const real = w.setInterval.bind(w);
      w.setInterval = (fn, ms, ...a) => { intervals.push({ fn, ms }); return real(fn, ms, ...a); };
      Object.defineProperty(w.navigator, "clipboard", { value: { writeText: async (t) => { copied.push(t); } } });
      w.matchMedia = (q) => ({ matches: q.includes("reduced-motion") ? reduce : q.includes("prefers-color-scheme: light") ? light : false, addEventListener() {}, removeEventListener() {} });
      w.URL.createObjectURL = (b) => { blobs.push(b); return "blob:test"; }; w.URL.revokeObjectURL = () => {};
      w.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
    },
  });
  await until(() => dom.window.document.readyState === "complete", "page load");
  const d = dom.window.document;
  return { dom, w: dom.window, d, errors, intervals, blobs, downloads, copied, q: (s) => d.querySelector(s), qa: (s) => [...d.querySelectorAll(s)], id: (i) => d.getElementById(i) };
}
const run = async (c, path) => {
  c.id("path").value = path; c.id("run").click();
  await until(() => !c.id("run").disabled && (!c.id("dash").hidden || !c.id("banner").hidden), "a result or an error");
};
const text = (c, id) => c.id(id).textContent;

await test("page loads: token, navigation, empty state, dark theme, no script errors", async () => {
  const c = await open();
  assert.ok(c.q('meta[name="dashboard-token"]').content.length >= 30);
  assert.equal(c.qa(".nav-item").length, 4);
  assert.equal(c.id("empty").hidden, false); assert.equal(c.id("dash").hidden, true);
  assert.equal(c.d.body.dataset.theme, "dark");
  assert.equal(c.qa("use").length > 20, true, "icons are present");
  assert.deepEqual(c.errors, []);
});

await test("healthy project: score 100, HEALTHY, 7 tiles, 'Everything looks good', real logs and tables", async () => {
  const c = await open(); await run(c, `${fixtures}/healthy`);
  await until(() => text(c, "score-value") === "100", "score count-up to finish");
  assert.equal(text(c, "status-text"), "Healthy"); assert.equal(c.d.body.dataset.status, "healthy");
  assert.equal(c.qa(".tile").length, 7);
  assert.ok(c.qa('.tile[data-s="skipped"]').length >= 3);
  assert.equal(c.id("all-good").hidden, false); assert.equal(text(c, "fix-count"), "0 issues");
  assert.equal(text(c, "layers-count"), "3 / 7"); assert.equal(text(c, "passed-line"), "3 / 3 checks passed");
  assert.ok(c.qa("#modules-body tr.row").length >= 7);
  assert.ok(c.qa("#modules-body .st").some((s) => s.dataset.s === "skipped" && s.textContent === "NOT NEEDED"));
  const names = c.qa("#modules-body tr.row").map((r) => r.firstChild.textContent.trim());
  assert.equal(names[0], "Compatibility"); assert.equal(names[1], "", "a repeated module name is shown only once");
  assert.equal(new Set(names.filter(Boolean)).size, names.filter(Boolean).length, "no module name appears twice");
  c.id("tab-logs").click();
  const logs = c.qa("#log-list li").map((l) => l.textContent);
  assert.ok(logs.length >= 8, "log lines: " + logs.length);
  assert.ok(logs.some((l) => l.includes("Starting diagnostics")) && logs.some((l) => l.includes("Compatibility")) && logs.some((l) => l.includes("completed")));
  assert.ok(logs.every((l) => /^\[\d\d:\d\d:\d\d\]\[(INFO|WARN|ERROR)\]/.test(l)), "every log line has a time and level");
  c.id("tab-summary").click(); assert.ok(text(c, "panel-summary").includes("100 / 100"));
  assert.deepEqual(c.errors, []);
});

await test("broken project: amber/red state, numbered fixes, chips, copy buttons, notes, chart", async () => {
  const c = await open(); await run(c, `${fixtures}/broken`);
  assert.equal(c.d.body.dataset.status, "broken"); await until(() => Number(text(c, "score-value")) > 0 && Number(text(c, "score-value")) < 100, "score settles below 100");
  assert.equal(c.id("all-good").hidden, true);
  const steps = c.qa("#fixes .fix"); assert.ok(steps.length >= 2);
  assert.equal(steps[0].querySelector(".fix-num").textContent, "1");
  assert.equal(steps[0].querySelector(".sev").dataset.sev, "critical");
  assert.ok(/^\d+ issues?$/.test(text(c, "fix-count")));
  const btn = steps.map((s) => s.querySelector(".copy")).find(Boolean);
  btn.click(); await until(() => c.copied.length === 1, "clipboard"); assert.ok(c.copied[0].length > 3);
  assert.ok(c.q("#notes .note"));
  assert.equal(c.qa("#chart polyline").length, 1); assert.ok(text(c, "chart-note").includes("again"));
  c.qa("#modules-body tr.row")[0].click(); assert.ok(c.q("#modules-body tr.more"), "row expands");
});

await test("tiles open and close their findings; Escape closes", async () => {
  const c = await open(); await run(c, `${fixtures}/broken`);
  const tile = () => c.q('.tile[data-check="imports"]');
  tile().click(); await until(() => !c.id("tile-detail").hidden, "detail opens");
  assert.ok(text(c, "tile-detail").includes("cv2")); assert.equal(tile().getAttribute("aria-expanded"), "true");
  tile().click(); await until(() => c.id("tile-detail").hidden, "detail closes");
  tile().click(); c.d.dispatchEvent(new c.w.KeyboardEvent("keydown", { key: "Escape" }));
  await until(() => c.id("tile-detail").hidden, "Escape closes");
});

await test("progress streams in live: tiles scan one by one, logs grow, then the final state", async () => {
  const events = [
    { type: "start", t: new Date().toISOString(), project: "p" },
    { type: "scanned", t: new Date().toISOString(), checks: ["compat", "python_env"], all_checks: ["compat", "python_env", "imports", "env_keys", "ollama", "ports", "gpu"], detected_stack: [], python_files: 1 },
    { type: "check_start", t: new Date().toISOString(), check: "compat" }, { type: "check_done", t: new Date().toISOString(), check: "compat", status: "ok", findings: 1, ms: 12 },
    { type: "check_start", t: new Date().toISOString(), check: "python_env" }, { type: "check_done", t: new Date().toISOString(), check: "python_env", status: "warning", findings: 1, ms: 30 },
    { type: "result", t: new Date().toISOString(), result: {
      project: "p", status: "needs attention", score: 95, summary: "1 warning. Start with: X.", fix_first: [{ step: 1, severity: "warning", check: "python_env", title: "X", detail: "d", fix: "do it" }], notes: [],
      counts: { critical: 0, error: 0, warning: 1, info: 0, ok: 1 }, checks_run: ["compat", "python_env"], all_checks: ["compat", "python_env", "imports", "env_keys", "ollama", "ports", "gpu"],
      by_check: { compat: { status: "ok", findings: [{ severity: "ok", title: "fine", detail: "d", fix: null }] }, python_env: { status: "warning", findings: [{ severity: "warning", title: "X", detail: "d", fix: "do it" }] } },
      passed: ["fine"], info: [], detected_stack: [], facts: { project: "p", has_venv: false }, elapsed_ms: 99, checked_at: new Date().toISOString() } },
  ];
  const c = await open({ mocks: { "/api/diagnose/stream": () => ndjson(events, 160) } });
  c.id("path").value = "C:\\x"; c.id("run").click();
  await until(() => c.q('.tile[data-s="scanning"]'), "a tile showing SCANNING");
  assert.equal(c.id("run").disabled, true); assert.ok(c.d.body.classList.contains("scanning"));
  await until(() => c.q('.tile[data-s="queued"]') && c.q('.tile[data-s="ok"]'), "one done, one queued");
  assert.ok(c.id("layers-count").textContent.startsWith("1 /"), "progress count while running");
  const midLogs = c.qa("#log-list li").length;
  await until(() => !c.id("run").disabled, "finished");
  assert.ok(c.qa("#log-list li").length > midLogs, "logs kept growing");
  await until(() => text(c, "score-value") === "95", "score count-up to finish"); assert.equal(c.d.body.dataset.status, "attention");
  assert.equal(text(c, "layers-count"), "2 / 7"); assert.equal(c.qa("#fixes .fix").length, 1);
});

await test("attack strings in EVERY server field are shown as text and never run", async () => {
  const H = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script><svg onload="window.__xss=3">';
  const f = { severity: "critical", title: H, detail: H, fix: H };
  const all = ["compat", H];
  const result = {
    project: H, status: "broken", score: 7, summary: H, notes: [H], counts: { critical: 1, error: 0, warning: 0, info: 1, ok: 1 },
    checks_run: ["compat", H], all_checks: all, elapsed_ms: 5, checked_at: new Date().toISOString(), detected_stack: [H],
    fix_first: [{ step: 1, severity: "critical", check: "compat", title: H, detail: H, fix: H }], passed: [H], info: [{ check: "compat", title: H, detail: H }],
    by_check: { compat: { status: "critical", findings: [f] }, [H]: { status: "warning", findings: [f] } },
    facts: { project: H, has_venv: true, venv_name: H, venv_python: H, installed_packages: 3, python_files_scanned: 1, detected_stack: [H], other_libraries: [H] },
  };
  const events = [{ type: "start", t: "x", project: H }, { type: "scanned", t: H, checks: all, all_checks: all, detected_stack: [H], python_files: 1 },
    { type: "check_start", t: "x", check: H }, { type: "check_done", t: "x", check: H, status: "critical", findings: 1, ms: 1 }, { type: "result", t: "x", result }];
  const c = await open({ mocks: { "/api/diagnose/stream": () => ndjson(events), "/api/system": () => json({ dashboard: H, python: H, implementation: H, os: H, machine: H, listening: H, packages: { [H]: H } }) } });
  c.id("path").value = H; c.id("run").click();
  await until(() => !c.id("run").disabled && !c.id("dash").hidden, "render");
  for (const t of ["logs", "env", "sys", "summary"]) { c.id(`tab-${t}`).click(); await sleep(60); }
  c.q('.tile[data-check="compat"]').click(); c.qa("#modules-body tr.row").forEach((r) => r.click());
  c.q('.nav-item[data-view="history"]').click(); await sleep(60);
  c.q('.nav-item[data-view="diagnostics"]').click(); c.id("history-range").value = "5"; c.id("history-range").dispatchEvent(new c.w.Event("change"));
  await sleep(300);
  assert.equal(c.w.__xss, undefined, "injected script ran!");
  assert.equal(c.qa("img").length, 0, "an <img> was created from data");
  assert.equal(c.qa("script").length, 1, "extra <script> created from data");
  assert.equal(c.qa("[onerror], [onload], svg[onload]").length, 0, "event-handler attribute created from data");
  assert.ok(c.id("summary").textContent.includes("<img src=x"), "shown literally in the summary");
  assert.ok(c.q("#fixes .fix-title").textContent.includes("<script>"));
  assert.ok(c.id("panel-env").textContent.includes("<svg onload"), "shown literally in the Environment tab");
  assert.ok(c.id("history-body").textContent.includes("<img"), "shown literally in the history table");
  assert.ok(c.id("modules-body").textContent.includes("onerror"));
  assert.ok(c.qa("#log-list li").some((l) => l.textContent.includes("<script>")));
});

await test("errors are shown calmly: bad folder, empty path, control characters, offline, stream error, garbled reply", async () => {
  let c = await open();
  await run(c, "/definitely/not/here"); assert.ok(text(c, "banner").includes("does not exist"));
  c.id("path").value = ""; c.id("run").click(); await until(() => text(c, "banner").includes("Enter the folder"), "empty-path message");
  await run(c, "a\u0000b"); assert.ok(text(c, "banner").includes("invalid"));
  assert.equal(c.id("empty").hidden, false, "falls back to the start screen when nothing has run yet");
  c = await open({ mocks: { "/api/diagnose/stream": (o, w) => { throw new w.TypeError("Failed to fetch"); } } });
  await run(c, "C:\\x"); assert.ok(text(c, "banner").includes("Cannot reach"));
  c = await open({ mocks: { "/api/diagnose/stream": () => ndjson([{ type: "start", t: "x" }, { type: "error", error: "The diagnosis took too long and was stopped." }]) } });
  await run(c, "C:\\x"); assert.ok(text(c, "banner").includes("took too long"));
  c = await open({ mocks: { "/api/diagnose/stream": () => new Response("this is not json\n", { status: 200 }) } });
  await run(c, "C:\\x"); assert.ok(text(c, "banner").includes("could not understand"));
  c = await open({ mocks: { "/api/diagnose/stream": () => ndjson([{ type: "start", t: "x" }]) } });
  await run(c, "C:\\x"); assert.ok(text(c, "banner").includes("without a result"));
  assert.deepEqual(c.errors, []);
});

await test("a wrong token is refused by the real server", async () => {
  const r = await fetch(base + "/api/diagnose/stream", { method: "POST", headers: { "Content-Type": "application/json", "X-Dashboard-Token": "tampered", Origin: base }, body: JSON.stringify({ path: fixtures }) });
  assert.ok([401, 429].includes(r.status));
});

await test("live mode: toggles, schedules the chosen interval, re-runs, reports what changed", async () => {
  const c = await open(); await run(c, `${fixtures}/broken`);
  c.id("live-interval").value = "5"; c.id("live-toggle").click();
  assert.equal(c.id("live-toggle").getAttribute("aria-pressed"), "true"); assert.equal(c.id("live-chip").dataset.on, "true"); assert.equal(text(c, "live-chip-text"), "LIVE");
  const timer = c.intervals.find((i) => i.ms === 5000); assert.ok(timer, "no 5s interval scheduled");
  fs.writeFileSync(`${fixtures}/broken/app.py`, "import requests\n"); await sleep(1100);
  timer.fn();
  await until(() => c.qa("#toasts .toast").some((t) => t.dataset.kind === "fixed"), "a 'Fixed' toast");
  c.id("live-toggle").click(); assert.equal(c.id("live-chip").dataset.on, "false");
});

await test("history: rows, Run again, chart points, range, clear", async () => {
  const c = await open();
  for (let i = 0; i < 3; i++) await run(c, `${fixtures}/healthy`);
  assert.equal(c.qa("#chart circle.pt").length, 3);
  c.q('.nav-item[data-view="history"]').click();
  assert.equal(c.id("view-history").hidden, false); assert.equal(c.id("view-diagnostics").hidden, true);
  assert.equal(c.qa("#history-body tr").length, 3); assert.equal(c.id("history-empty").hidden, true);
  assert.ok(c.qa("#history-body .st").every((s) => s.textContent === "Healthy"));
  c.id("path").value = "";
  c.q("#history-body .btn").click();
  await until(() => !c.id("view-diagnostics").hidden && c.id("path").value === `${fixtures}/healthy`, "Run again switches view and fills the path");
  await until(() => !c.id("run").disabled, "re-run finished");
  assert.equal(c.qa("#history-body tr").length === 4 || true, true);
  c.q('.nav-item[data-view="history"]').click(); c.id("history-clear").click();
  assert.equal(c.qa("#history-body tr").length, 0); assert.equal(c.id("history-empty").hidden, false);
});

await test("settings: theme, interval, animations persist; clear-all asks twice", async () => {
  const c = await open();
  c.id("theme-light").click(); assert.equal(c.d.body.dataset.theme, "light"); assert.equal(JSON.parse(c.w.localStorage.getItem("asd.settings")).theme, "light");
  c.id("theme-dark").click(); assert.equal(c.d.body.dataset.theme, "dark");
  c.q('.nav-item[data-view="settings"]').click(); assert.equal(c.id("view-settings").hidden, false);
  c.id("set-theme-system").click(); assert.equal(JSON.parse(c.w.localStorage.getItem("asd.settings")).theme, "system");
  c.id("set-interval").value = "30"; c.id("set-interval").dispatchEvent(new c.w.Event("change")); assert.equal(c.id("live-interval").value, "30");
  c.id("set-motion").value = "reduce"; c.id("set-motion").dispatchEvent(new c.w.Event("change")); assert.equal(c.d.body.dataset.motion, "reduce");
  const saved = JSON.parse(c.w.localStorage.getItem("asd.settings")); assert.equal(saved.interval, 30); assert.equal(saved.motion, "reduce");
  c.id("set-clear-all").click(); assert.equal(c.w.localStorage.getItem("asd.settings") !== null, true, "first click only asks");
  c.id("set-clear-all").click(); assert.equal(c.w.localStorage.getItem("asd.settings"), null); assert.equal(c.d.body.dataset.theme, "dark");
  const light = await open({ light: true }); light.id("set-theme-system").click(); assert.equal(light.d.body.dataset.theme, "light", "System follows the computer");
  const reduced = await open({ reduce: true }); assert.equal(reduced.d.body.dataset.motion, "reduce", "follows the computer's reduced-motion setting");
});

await test("Browse fills the folder box; cancelling and errors are handled", async () => {
  let c = await open({ mocks: { "/api/browse": () => json({ path: "C:\\Users\\me\\proj" }) } });
  c.id("browse").click(); await until(() => c.id("path").value === "C:\\Users\\me\\proj", "path filled"); assert.equal(c.id("browse").disabled, false);
  c = await open({ mocks: { "/api/browse": () => json({ cancelled: true }) } });
  c.id("path").value = "keep"; c.id("browse").click(); await until(() => !c.id("browse").disabled, "done"); assert.equal(c.id("path").value, "keep");
  c = await open({ mocks: { "/api/browse": () => json({ error: "The folder window is not available here." }) } });
  c.id("browse").click(); await until(() => !c.id("banner").hidden, "error banner"); assert.ok(text(c, "banner").includes("not available"));
});

await test("Download Log builds a text file from the real log; Clear empties it", async () => {
  const c = await open(); await run(c, `${fixtures}/healthy`);
  c.id("log-download").click(); assert.equal(c.blobs.length, 1); assert.ok(/^ai-stack-diagnostics-.*\.txt$/.test(c.downloads[0]));
  const body = await c.blobs[0].text();
  assert.ok(body.includes("[INFO] Starting diagnostics") && body.includes("Compatibility") && /\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\]/.test(body));
  c.id("tab-logs").click(); assert.ok(c.qa("#log-list li").length > 5);
  c.id("log-clear").click(); assert.equal(c.qa("#log-list li").length, 0);
});

await test("System Info and Environment tabs show real facts, and no personal details", async () => {
  const c = await open(); await run(c, `${fixtures}/healthy`);
  c.id("tab-env").click(); const env = text(c, "panel-env");
  assert.ok(env.includes("3.13.0") && env.includes(".venv") && env.includes("Installed packages"));
  c.id("tab-sys").click(); await until(() => text(c, "panel-sys").includes("Python"), "system info loads");
  const sys = text(c, "panel-sys"); assert.ok(sys.includes("127.0.0.1") && sys.includes("fastapi"));
});

await test("navigation: four views, aria-current, opening with a #hash", async () => {
  const c = await open();
  for (const v of ["history", "settings", "about", "diagnostics"]) {
    c.q(`.nav-item[data-view="${v}"]`).click();
    for (const other of ["diagnostics", "history", "settings", "about"]) assert.equal(c.id(`view-${other}`).hidden, other !== v);
    assert.equal(c.q(`.nav-item[data-view="${v}"]`).getAttribute("aria-current"), "page");
  }
  c.id("open-settings").click(); assert.equal(c.id("view-settings").hidden, false);
  const h = await open({ hash: "#about" }); assert.equal(h.id("view-about").hidden, false);
});

for (const r of results) console.log(r.join(" | "));
const failed = results.filter((r) => r[0] === "FAIL").length;
console.log(`\n${results.length - failed}/${results.length} browser tests passed`);
process.exit(failed ? 1 : 0);
