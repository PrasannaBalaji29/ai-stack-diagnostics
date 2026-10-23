"use strict";
(() => {
  /* ======================= setup ======================= */
  const $ = (id) => document.getElementById(id);
  const meta = (name) => (document.querySelector(`meta[name="${name}"]`) || {}).content || "";
  const TOKEN = meta("dashboard-token");
  const DEFAULT_PATH = meta("default-path");
  const SVG_NS = "http://www.w3.org/2000/svg";
  const RING = 2 * Math.PI * 54;
  const MAX_LOGS = 500;
  const MAX_RECENT = 8;
  const MAX_TOASTS = 4;
  const VIEWS = ["diagnostics", "history", "settings", "about"];
  const SEVERITIES = ["critical", "error", "warning", "info", "ok"];
  const CHECKS = {
    compat: { label: "Compatibility", sub: "Python version and conflicts", icon: "shield" },
    python_env: { label: "Environment", sub: "Packages vs requirements", icon: "box" },
    imports: { label: "Imports", sub: "Code vs installed libraries", icon: "code" },
    env_keys: { label: "Settings", sub: "API keys in .env", icon: "key" },
    ollama: { label: "Ollama", sub: "Local model server", icon: "server" },
    ports: { label: "Ports", sub: "Services and conflicts", icon: "network" },
    gpu: { label: "GPU", sub: "GPU, driver and ML libraries", icon: "cpu" },
  };
  const STATUS_LABEL = { ok: "OK", info: "INFO", warning: "WARNING", error: "ERROR", critical: "CRITICAL", skipped: "NOT NEEDED", queued: "QUEUED", scanning: "SCANNING" };
  const LOG_LEVEL = { ok: "INFO", info: "INFO", warning: "WARN", error: "ERROR", critical: "ERROR", queued: "INFO", skipped: "INFO", scanning: "INFO" };
  const RESULT_LABEL = { healthy: "Healthy", "needs attention": "Needs attention", broken: "Broken" };
  const DEFAULT_SETTINGS = { theme: "dark", interval: 10, limit: 30, motion: "auto" };

  const state = {
    running: false, live: false, pollTimer: null, tickTimer: null, nextAt: 0,
    last: null, lastPath: "", selected: null, tiles: {}, order: [], allChecks: Object.keys(CHECKS), done: 0,
    logs: [], tab: "summary", view: "diagnostics", system: null, settings: { ...DEFAULT_SETTINGS }, history: [],
  };

  /* ======================= helpers (all data goes in as TEXT) ======================= */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    return node;
  }
  function icon(name) {
    const s = svgEl("svg", { class: "ic", "aria-hidden": "true" });
    s.appendChild(svgEl("use", { href: `#i-${name}` }));
    return s;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(window.localStorage.getItem(key) || "null");
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (_) { /* storage can be blocked: everything still works for this visit */ }
    return null;
  }
  const pad = (n) => String(n).padStart(2, "0");
  function toDate(value) { const d = value ? new Date(value) : new Date(); return Number.isNaN(d.getTime()) ? new Date() : d; }
  const clock = (value) => { const d = toDate(value); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  const stamp = (value) => { const d = toDate(value); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${clock(value)}`; };
  const chip = (cls, status, text) => { const c = el("span", cls, text !== undefined ? text : (STATUS_LABEL[status] || status)); c.dataset[cls === "sev" ? "sev" : "s"] = status; return c; };
  const label = (name) => (CHECKS[name] ? CHECKS[name].label : name);
  const reduceMotion = () => state.settings.motion === "reduce" || !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  function showBanner(message) { const b = $("banner"); b.textContent = message; b.hidden = false; }
  function hideBanner() { const b = $("banner"); b.hidden = true; b.textContent = ""; }

  function toast(kind, text) {
    const box = $("toasts");
    while (box.children.length >= MAX_TOASTS) box.removeChild(box.firstChild);
    const t = el("div", "toast", text); t.dataset.kind = kind; box.appendChild(t);
    window.setTimeout(() => { if (t.parentNode) t.parentNode.removeChild(t); }, 6500);
  }
  async function copyText(text, button) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {
      try {
        const area = el("textarea"); area.value = text; area.setAttribute("readonly", ""); area.className = "sr";
        document.body.appendChild(area); area.select(); ok = document.execCommand("copy"); document.body.removeChild(area);
      } catch (_) { ok = false; }
    }
    const old = button.lastChild.textContent;
    button.lastChild.textContent = ok ? "Copied" : "Select and copy";
    window.setTimeout(() => { button.lastChild.textContent = old; }, 1600);
  }
  async function api(path, body) {
    return fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-Dashboard-Token": TOKEN },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }
  async function readJson(res) { try { return await res.json(); } catch (_) { return null; } }

  /* ======================= settings, theme, views ======================= */
  function loadSettings() {
    const saved = store("asd.settings") || {};
    const s = { ...DEFAULT_SETTINGS };
    if (["dark", "light", "system"].includes(saved.theme)) s.theme = saved.theme;
    if ([5, 10, 30].includes(saved.interval)) s.interval = saved.interval;
    if ([10, 30, 100].includes(saved.limit)) s.limit = saved.limit;
    if (["auto", "reduce"].includes(saved.motion)) s.motion = saved.motion;
    state.settings = s;
  }
  const saveSettings = () => store("asd.settings", state.settings);
  function applyTheme() {
    const wantsLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    const resolved = state.settings.theme === "system" ? (wantsLight ? "light" : "dark") : state.settings.theme;
    document.body.dataset.theme = resolved;
    document.body.dataset.motion = reduceMotion() ? "reduce" : "full";
    $("theme-light").setAttribute("aria-pressed", String(resolved === "light"));
    $("theme-dark").setAttribute("aria-pressed", String(resolved === "dark"));
    for (const t of ["dark", "light", "system"]) $(`set-theme-${t}`).setAttribute("aria-pressed", String(state.settings.theme === t));
    $("set-interval").value = String(state.settings.interval);
    $("set-limit").value = String(state.settings.limit);
    $("set-motion").value = state.settings.motion;
  }
  function setTheme(theme) { state.settings.theme = theme; saveSettings(); applyTheme(); }

  function show(view) {
    if (!VIEWS.includes(view)) view = "diagnostics";
    state.view = view;
    for (const v of VIEWS) $(`view-${v}`).hidden = v !== view;
    for (const b of document.querySelectorAll(".nav-item")) {
      if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    }
    try { if (window.location.hash !== `#${view}`) window.history.replaceState(null, "", `#${view}`); } catch (_) { /* not important */ }
    if (view === "history") renderHistoryTable();
  }

  /* ======================= history (saved in this browser only) ======================= */
  function loadHistory() { const h = store("asd.history"); state.history = Array.isArray(h) ? h.filter((r) => r && typeof r.path === "string") : []; }
  function addHistory(result, path) {
    const issues = (result.counts.critical || 0) + (result.counts.error || 0) + (result.counts.warning || 0);
    state.history.push({ t: result.checked_at || new Date().toISOString(), path, status: result.status, score: result.score, issues, ms: result.elapsed_ms });
    while (state.history.length > state.settings.limit) state.history.shift();
    store("asd.history", state.history);
  }
  function rememberPath(path) {
    const list = (store("asd.recent") || []).filter((p) => p !== path);
    list.unshift(path); store("asd.recent", list.slice(0, MAX_RECENT)); renderRecent();
  }
  function renderRecent() {
    const list = $("recent"); clear(list);
    for (const p of store("asd.recent") || []) { const o = el("option"); o.value = p; list.appendChild(o); }
  }
  function renderHistoryTable() {
    const body = $("history-body"); clear(body);
    const rows = [...state.history].reverse();
    $("history-empty").hidden = rows.length > 0; $("history-table").hidden = rows.length === 0;
    for (const r of rows) {
      const tr = el("tr");
      tr.appendChild(el("td", "mono", stamp(r.t)));
      const proj = el("td", "mono trunc", r.path); proj.title = r.path; tr.appendChild(proj);
      const st = el("td"); st.appendChild(chip("st", r.status, RESULT_LABEL[r.status] || r.status)); tr.appendChild(st);
      tr.appendChild(el("td", "mono", String(r.score)));
      tr.appendChild(el("td", "mono", String(r.issues)));
      tr.appendChild(el("td", "mono", `${r.ms} ms`));
      const act = el("td"); const again = el("button", "btn btn-sm"); again.type = "button";
      again.append(icon("refresh"), el("span", "", "Run again"));
      again.addEventListener("click", () => { $("path").value = r.path; show("diagnostics"); run(false); });
      act.appendChild(again); tr.appendChild(act); body.appendChild(tr);
    }
  }

  /* ======================= logs ======================= */
  function log(level, text, when) {
    state.logs.push({ t: when || new Date().toISOString(), level, text: String(text) });
    while (state.logs.length > MAX_LOGS) state.logs.shift();
    renderLogs();
  }
  function renderLogs() {
    const list = $("log-list"); clear(list);
    for (const e of state.logs.slice(-250)) {
      const li = el("li"); li.dataset.l = e.level;
      li.append(el("span", "time", `[${clock(e.t)}]`), el("span", "lvl", `[${e.level}]`), el("span", "msg", e.text));
      list.appendChild(li);
    }
    const panel = $("panel-logs"); panel.scrollTop = panel.scrollHeight;
  }
  function downloadLog() {
    const text = state.logs.map((e) => `[${stamp(e.t)}] [${e.level}] ${e.text}`).join("\n") + "\n";
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = el("a"); a.href = url; a.download = `ai-stack-diagnostics-${stamp().replace(/[: ]/g, "-")}.txt`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  /* ======================= rendering the diagnostics view ======================= */
  function animateScore(node, to) {
    const from = Number.parseInt(node.textContent, 10) || 0;
    if (reduceMotion() || from === to || typeof window.requestAnimationFrame !== "function") { node.textContent = String(to); return; }
    const start = performance.now(), span = 900;
    const frame = (now) => {
      const p = Math.min(1, (now - start) / span);
      node.textContent = String(Math.round(from + (to - from) * (1 - Math.pow(1 - p, 3))));
      if (p < 1) window.requestAnimationFrame(frame); else node.textContent = String(to);
    };
    window.requestAnimationFrame(frame);
  }
  function showDash() { $("empty").hidden = true; $("dash").hidden = false; }

  function renderHealth(r) {
    const arc = $("gauge-arc");
    arc.style.setProperty("--dash", String(RING)); arc.style.setProperty("--offset", String(RING * (1 - r.score / 100)));
    animateScore($("score-value"), r.score);
    document.body.dataset.status = r.status === "needs attention" ? "attention" : r.status;
    $("status-text").textContent = RESULT_LABEL[r.status] || r.status;
    $("summary").textContent = r.summary;
    const counts = $("counts"); clear(counts);
    for (const sev of ["critical", "error", "warning"]) {
      const n = (r.counts || {})[sev] || 0; const p = el("span", "pill", `${n} ${sev}`); p.dataset.sev = sev; p.dataset.zero = String(n === 0); counts.appendChild(p);
    }
    const ran = r.checks_run || []; const passed = ran.filter((c) => ["ok", "info"].includes((r.by_check[c] || {}).status)).length;
    $("passed-line").textContent = `${passed} / ${ran.length} checks passed`;
    document.title = `${r.score}/100 ${RESULT_LABEL[r.status] || ""} \u00b7 AI Stack Diagnostics`;
  }

  function renderLayersHeader() {
    const total = state.allChecks.length || 7;
    $("layers-count").textContent = `${state.done} / ${total}`;
    $("layers-fill").style.setProperty("--w", `${Math.round((state.done / total) * 100)}%`);
  }
  function renderTiles() {
    const box = $("tiles"); clear(box);
    const byCheck = (state.last && state.lastPath === $("path").value.trim() ? state.last.by_check : null) || {};
    for (const name of state.allChecks) {
      const info = CHECKS[name] || { label: name, sub: "", icon: "cube" };
      const status = state.tiles[name] || "skipped";
      const hasDetail = !!byCheck[name] && !state.running;
      const tile = el("button", "tile"); tile.type = "button"; tile.dataset.check = name; tile.dataset.s = status;
      tile.setAttribute("aria-expanded", String(state.selected === name && hasDetail)); tile.disabled = !hasDetail;
      const top = el("div", "tile-top"); const ico = el("span", "tile-icon"); ico.appendChild(icon(info.icon));
      top.append(ico, chip("st", status));
      const foot = el("div", "tile-foot"); const text = el("div"); text.append(el("div", "tile-name", info.label), el("div", "tile-sub", info.sub));
      foot.append(text, icon("chev"));
      tile.append(top, foot);
      tile.addEventListener("click", () => { state.selected = state.selected === name ? null : name; renderTiles(); });
      box.appendChild(tile);
    }
    const detail = $("tile-detail"); clear(detail);
    const sel = state.selected && !state.running ? byCheck[state.selected] : null;
    detail.hidden = !sel;
    if (sel) {
      for (const f of sel.findings) {
        const item = el("div"); const head = el("div", "td-title"); head.append(chip("sev", f.severity, f.severity), el("span", "", f.title));
        item.append(head, el("div", "td-detail", f.detail));
        if (f.fix) item.appendChild(el("div", "td-detail", "Fix: " + f.fix));
        detail.appendChild(item);
      }
    }
    renderLayersHeader();
  }

  function renderFixes(r) {
    const notes = $("notes"); clear(notes);
    for (const n of r.notes || []) notes.appendChild(el("p", "note", n));
    const list = $("fixes"); clear(list);
    const n = (r.fix_first || []).length;
    const badge = $("fix-count"); badge.textContent = n === 0 ? "0 issues" : `${n} issue${n === 1 ? "" : "s"}`; badge.dataset.zero = String(n === 0);
    $("all-good").hidden = n !== 0; list.hidden = n === 0;
    (r.fix_first || []).forEach((step, i) => {
      const li = el("li", "fix"); li.style.setProperty("animation-delay", `${Math.min(i, 8) * 55}ms`);
      li.appendChild(el("div", "fix-num", String(step.step)));
      const body = el("div"); const title = el("div", "fix-title"); title.append(chip("sev", step.severity, step.severity), el("span", "", step.title));
      body.append(title, el("p", "fix-detail", step.detail));
      if (step.fix) {
        const row = el("div", "fix-cmd"); const code = el("code", "", step.fix);
        const btn = el("button", "copy"); btn.type = "button"; btn.append(icon("copy"), el("span", "", "Copy"));
        btn.addEventListener("click", () => copyText(step.fix, btn)); row.append(code, btn); body.appendChild(row);
      }
      li.appendChild(body); list.appendChild(li);
    });
  }

  function renderModules(r) {
    const body = $("modules-body"); clear(body);
    for (const name of r.all_checks || state.allChecks) {
      const info = CHECKS[name] || { label: name, icon: "cube" };
      const entry = (r.by_check || {})[name];
      const rows = entry ? entry.findings : [{ severity: "skipped", title: "Not used by this project", detail: "This project does not need this check, so it was skipped.", fix: null }];
      rows.forEach((f, rowIndex) => {
        const tr = el("tr", "row"); tr.tabIndex = 0; tr.setAttribute("aria-expanded", "false");
        const mod = el("td");
        if (rowIndex === 0) { const m = el("span", "mod"); m.append(icon(info.icon), el("span", "", info.label)); mod.appendChild(m); }  // name once per group
        else mod.setAttribute("aria-label", info.label);
        const st = el("td"); st.appendChild(chip("st", f.severity));
        tr.append(mod, st, el("td", "", f.title));
        const toggle = () => {
          const open = tr.getAttribute("aria-expanded") === "true";
          tr.setAttribute("aria-expanded", String(!open));
          if (open) { if (tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains("more")) tr.nextSibling.remove(); return; }
          const more = el("tr", "more"); const cell = el("td", "", f.detail + (f.fix ? `  Fix: ${f.fix}` : "")); cell.colSpan = 3; more.appendChild(cell); tr.after(more);
        };
        tr.addEventListener("click", toggle);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
        body.appendChild(tr);
      });
    }
  }

  function renderChart() {
    const svg = $("chart"); clear(svg);
    const path = $("path").value.trim();
    const limit = Number.parseInt($("history-range").value, 10) || 5;
    const pts = state.history.filter((h) => h.path === path).slice(-limit);
    const X0 = 40, X1 = 506, Y0 = 14, Y1 = 192;
    for (const v of [0, 25, 50, 75, 100]) {
      const y = Y1 - ((Y1 - Y0) * v) / 100;
      svg.appendChild(svgEl("line", { class: "gl", x1: X0, x2: X1, y1: y, y2: y }));
      const t = svgEl("text", { x: X0 - 8, y: y + 4, "text-anchor": "end" }); t.textContent = String(v); svg.appendChild(t);
    }
    $("chart-note").textContent = pts.length < 2 ? "Run the diagnosis again to build a trend." : "";
    if (pts.length === 0) return;
    const step = pts.length > 1 ? (X1 - X0) / (pts.length - 1) : 0;
    const coords = pts.map((p, i) => [pts.length > 1 ? X0 + i * step : (X0 + X1) / 2, Y1 - ((Y1 - Y0) * Math.max(0, Math.min(100, p.score))) / 100]);
    const line = coords.map((c) => c.join(",")).join(" ");
    svg.appendChild(svgEl("polygon", { class: "area", points: `${coords[0][0]},${Y1} ${line} ${coords[coords.length - 1][0]},${Y1}` }));
    svg.appendChild(svgEl("polyline", { class: "line", points: line }));
    coords.forEach((c, i) => {
      const dot = svgEl("circle", { class: "pt", cx: c[0], cy: c[1], r: 4.5 });
      const tip = svgEl("title"); tip.textContent = `${pts[i].score}/100 \u00b7 ${stamp(pts[i].t)}`; dot.appendChild(tip); svg.appendChild(dot);
      if (pts.length <= 10 || i % Math.ceil(pts.length / 8) === 0 || i === pts.length - 1) {
        const lbl = svgEl("text", { x: c[0], y: Y1 + 20, "text-anchor": "middle" }); lbl.textContent = `Run ${i + 1}`; svg.appendChild(lbl);
      }
    });
  }

  function dl(rows) {
    const d = el("dl", "dl");
    for (const [k, v] of rows) {
      d.appendChild(el("dt", "", k));
      const dd = el("dd");
      if (Array.isArray(v)) { const box = el("span", "chips"); if (v.length === 0) box.textContent = "none"; for (const item of v) box.appendChild(el("span", "pill", String(item))); dd.appendChild(box); } else dd.textContent = String(v);
      d.appendChild(dd);
    }
    return d;
  }
  function renderSummary(r) {
    const p = $("panel-summary"); clear(p);
    p.appendChild(dl([
      ["Status", RESULT_LABEL[r.status] || r.status], ["Score", `${r.score} / 100`], ["Summary", r.summary],
      ["Layers checked", `${(r.checks_run || []).length} of ${(r.all_checks || []).length}`],
      ["Problems", `${r.counts.critical} critical, ${r.counts.error} error, ${r.counts.warning} warning`],
      ["Scan time", `${r.elapsed_ms} ms`], ["Checked at", stamp(r.checked_at)], ["Project", r.project],
    ]));
  }
  function renderEnv(r) {
    const p = $("panel-env"); clear(p); const f = r.facts || {};
    p.appendChild(dl([
      ["Project folder", f.project || r.project], ["Virtual environment", f.has_venv ? (f.venv_name || "found") : "none found"],
      ["Venv Python", f.venv_python || "unknown"], ["Installed packages", f.installed_packages === null || f.installed_packages === undefined ? "unknown" : f.installed_packages],
      ["Python files scanned", f.python_files_scanned === undefined ? "unknown" : f.python_files_scanned],
      ["Detected stack", f.detected_stack || []], ["Other libraries", f.other_libraries || []],
    ]));
  }
  async function loadSystem() {
    const p = $("panel-sys");
    if (state.system) return;
    clear(p); p.appendChild(el("p", "muted", "Loading\u2026"));
    try {
      const res = await api("/api/system"); const data = await readJson(res);
      if (!res.ok || !data) throw new Error("bad reply");
      state.system = data; clear(p);
      p.appendChild(dl([
        ["Dashboard", data.dashboard], ["Python", `${data.implementation} ${data.python}`], ["Operating system", data.os], ["Machine", data.machine],
        ["Listening on", data.listening], ...Object.entries(data.packages || {}).map(([k, v]) => [k, v]),
      ]));
    } catch (_) { clear(p); p.appendChild(el("p", "muted", "System information could not be loaded.")); }
  }
  function selectTab(name) {
    state.tab = name;
    for (const t of ["summary", "logs", "env", "sys"]) {
      $(`tab-${t}`).setAttribute("aria-selected", String(t === name)); $(`panel-${t}`).hidden = t !== name;
    }
    if (name === "sys") loadSystem();
    if (name === "logs") renderLogs();
  }

  /* ======================= running a diagnosis (streamed) ======================= */
  function setBusy(on) { document.body.classList.toggle("scanning", on); $("run").disabled = on; $("browse").disabled = on; }

  function handleEvent(ev) {
    switch (ev.type) {
      case "scanned":
        state.allChecks = ev.all_checks && ev.all_checks.length ? ev.all_checks : state.allChecks; state.order = ev.checks || [];
        state.tiles = {}; for (const c of state.allChecks) state.tiles[c] = state.order.includes(c) ? "queued" : "skipped";
        state.done = 0; log("INFO", `Scan complete: ${state.order.length} of ${state.allChecks.length} checks apply to this project.`, ev.t); renderTiles(); break;
      case "check_start":
        state.tiles[ev.check] = "scanning"; log("INFO", `Checking ${label(ev.check).toLowerCase()}\u2026`, ev.t); renderTiles(); break;
      case "check_done":
        state.tiles[ev.check] = ev.status; state.done += 1;
        log(LOG_LEVEL[ev.status] || "INFO", `${label(ev.check)}: ${(STATUS_LABEL[ev.status] || ev.status).toLowerCase()} (${ev.ms} ms)`, ev.t); renderTiles(); break;
      case "error": throw new Error(String(ev.error || "The diagnosis failed."));
      case "result": finish(ev.result, ev.t); break;
      default: break; // "start" and anything newer: nothing to do
    }
  }
  function finish(result, when) {
    const path = $("path").value.trim();
    const previous = state.lastPath === path ? state.last : null;
    if (previous) {
      const key = (s) => `${s.check}|${s.title}`;
      const before = new Set((previous.fix_first || []).map(key)), after = new Set((result.fix_first || []).map(key));
      for (const s of previous.fix_first || []) if (!after.has(key(s))) toast("fixed", `Fixed: ${s.title}`);
      for (const s of result.fix_first || []) if (!before.has(key(s))) toast("new", `New problem: ${s.title}`);
    }
    state.last = result; state.lastPath = path; state.running = false;
    state.allChecks = result.all_checks || state.allChecks; state.tiles = {}; state.done = (result.checks_run || []).length;
    for (const c of state.allChecks) state.tiles[c] = result.by_check[c] ? result.by_check[c].status : "skipped";
    addHistory(result, path); rememberPath(path);
    showDash(); renderHealth(result); renderTiles(); renderFixes(result); renderModules(result); renderSummary(result); renderEnv(result); renderChart();
    log(result.status === "healthy" ? "INFO" : result.status === "broken" ? "ERROR" : "WARN",
      `Diagnostics completed: ${result.summary}`, when);
  }

  async function run(fromTimer) {
    if (state.running) return;
    const path = $("path").value.trim();
    if (!path) { showBanner("Enter the folder of a project first."); $("path").focus(); return; }
    state.running = true; setBusy(true); hideBanner(); state.logs = []; state.selected = null;
    log("INFO", `Starting diagnostics for ${path}`); showDash();
    state.tiles = {}; for (const c of state.allChecks) state.tiles[c] = "queued"; state.done = 0; renderTiles();
    try {
      const res = await api("/api/diagnose/stream", { path });
      if (!res.ok) { const data = await readJson(res); throw new Error((data && data.error) || `The server answered with an error (${res.status}).`); }
      if (res.body && typeof res.body.getReader === "function") {
        const reader = res.body.getReader(), decoder = new TextDecoder(); let buf = "";
        for (;;) {
          const { value, done } = await reader.read(); if (done) break;
          buf += decoder.decode(value, { stream: true });
          for (let i = buf.indexOf("\n"); i >= 0; i = buf.indexOf("\n")) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line) handleEvent(JSON.parse(line)); }
        }
        if (buf.trim()) handleEvent(JSON.parse(buf.trim()));
      } else {
        for (const line of (await res.text()).split("\n")) if (line.trim()) handleEvent(JSON.parse(line));
      }
      if (state.running) throw new Error("The diagnosis ended without a result.");
    } catch (e) {
      state.running = false; state.tiles = {}; state.done = 0;
      const message = e instanceof SyntaxError ? "The server sent a reply this page could not understand."
        : e instanceof TypeError ? "Cannot reach the dashboard server. Is it still running in your terminal?"
          : (e && e.message) || "The diagnosis failed.";
      showBanner(message); log("ERROR", message);
      if (state.last) { renderTiles(); } else { $("dash").hidden = true; $("empty").hidden = false; }
      if (!fromTimer) stopLive();
    } finally { state.running = false; setBusy(false); renderTiles(); }
  }

  async function browse() {
    const btn = $("browse"); btn.disabled = true; btn.lastChild.textContent = "Waiting\u2026"; toast("info", "A folder window opened on your computer. It may be behind this browser window.");
    try {
      const res = await api("/api/browse", {}); const data = await readJson(res);
      if (!res.ok || !data) showBanner((data && data.error) || "The folder window could not be opened.");
      else if (data.path) { $("path").value = data.path; hideBanner(); $("run").focus(); }
      else if (data.error) showBanner(data.error);
    } catch (_) { showBanner("Cannot reach the dashboard server. Is it still running in your terminal?"); }
    finally { btn.disabled = false; btn.lastChild.textContent = "Browse"; }
  }

  /* ======================= live mode ======================= */
  function setLiveUi(on) {
    state.live = on; $("live-toggle").setAttribute("aria-pressed", String(on)); $("live-chip").dataset.on = String(on);
    $("live-chip-text").textContent = on ? "LIVE" : "LIVE OFF"; if (!on) $("live-countdown").textContent = "";
  }
  function stopLive() {
    if (state.pollTimer) window.clearInterval(state.pollTimer);
    if (state.tickTimer) window.clearInterval(state.tickTimer);
    state.pollTimer = state.tickTimer = null; setLiveUi(false);
  }
  function startLive() {
    stopLive();
    const seconds = Number.parseInt($("live-interval").value, 10) || 10;
    setLiveUi(true); state.nextAt = Date.now() + seconds * 1000;
    state.pollTimer = window.setInterval(() => { state.nextAt = Date.now() + seconds * 1000; if (!document.hidden) run(true); }, seconds * 1000);
    state.tickTimer = window.setInterval(() => { $("live-countdown").textContent = `Next scan in ${Math.max(0, Math.ceil((state.nextAt - Date.now()) / 1000))}s.`; }, 1000);
    $("live-countdown").textContent = `Next scan in ${seconds}s.`;
    if (!state.last) run(false);
  }

  /* ======================= wiring ======================= */
  for (const b of document.querySelectorAll(".nav-item")) b.addEventListener("click", () => show(b.dataset.view));
  $("open-settings").addEventListener("click", () => show("settings"));
  $("theme-light").addEventListener("click", () => setTheme("light"));
  $("theme-dark").addEventListener("click", () => setTheme("dark"));
  for (const t of ["dark", "light", "system"]) $(`set-theme-${t}`).addEventListener("click", () => setTheme(t));
  $("set-interval").addEventListener("change", (e) => { state.settings.interval = Number.parseInt(e.target.value, 10); saveSettings(); $("live-interval").value = String(state.settings.interval); toast("fixed", "Saved"); });
  $("set-limit").addEventListener("change", (e) => { state.settings.limit = Number.parseInt(e.target.value, 10); while (state.history.length > state.settings.limit) state.history.shift(); store("asd.history", state.history); saveSettings(); toast("fixed", "Saved"); });
  $("set-motion").addEventListener("change", (e) => { state.settings.motion = e.target.value; saveSettings(); applyTheme(); toast("fixed", "Saved"); });
  $("set-clear-all").addEventListener("click", (e) => {
    const b = e.currentTarget;
    if (b.dataset.armed !== "true") { b.dataset.armed = "true"; b.textContent = "Click again to confirm"; window.setTimeout(() => { b.dataset.armed = "false"; b.textContent = "Clear all saved data"; }, 4000); return; }
    try { for (const k of Object.keys(window.localStorage)) if (k.startsWith("asd.")) window.localStorage.removeItem(k); } catch (_) { /* ignore */ }
    state.history = []; state.settings = { ...DEFAULT_SETTINGS }; renderRecent(); applyTheme(); renderChart(); renderHistoryTable();
    b.dataset.armed = "false"; b.textContent = "Clear all saved data"; toast("fixed", "All saved data cleared");
  });
  $("history-clear").addEventListener("click", () => { state.history = []; store("asd.history", []); renderHistoryTable(); renderChart(); toast("fixed", "History cleared"); });
  $("history-range").addEventListener("change", renderChart);
  $("run").addEventListener("click", () => run(false));
  $("browse").addEventListener("click", browse);
  $("path").addEventListener("keydown", (e) => { if (e.key === "Enter") run(false); });
  $("path").addEventListener("change", renderChart);
  $("live-toggle").addEventListener("click", () => { if (state.live) stopLive(); else startLive(); });
  $("live-interval").addEventListener("change", () => { if (state.live) startLive(); });
  $("log-download").addEventListener("click", downloadLog);
  $("log-clear").addEventListener("click", () => { state.logs = []; renderLogs(); });
  for (const t of ["summary", "logs", "env", "sys"]) $(`tab-${t}`).addEventListener("click", () => selectTab(t));
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((document.activeElement || {}).tagName || "");
    if (e.key === "/" && !typing) { e.preventDefault(); show("diagnostics"); $("path").focus(); }
    if (e.key === "Escape" && state.selected) { state.selected = null; renderTiles(); }
  });
  if (window.matchMedia) { try { window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { if (state.settings.theme === "system") applyTheme(); }); } catch (_) { /* older browsers */ } }

  /* ======================= start ======================= */
  loadSettings(); loadHistory(); applyTheme(); renderRecent(); renderChart(); selectTab("summary"); renderTiles();
  $("live-interval").value = String(state.settings.interval);
  show((window.location.hash || "").replace("#", "") || "diagnostics");
  const initial = DEFAULT_PATH || ((store("asd.recent") || [])[0] || "");
  $("path").value = initial;
  if (DEFAULT_PATH) run(false); else if (initial) $("path").select();
})();
