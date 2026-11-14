"use strict";

/* ------------------------------------------------------------------ helpers */
const TOKEN = document.querySelector('meta[name="token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const SERVICES = [
  [744, "Debug / notify", false],
  [2121, "FTP", false],
  [3232, "Klog", false],
  [9090, "Payload loader", true],
  [9020, "Payload (alt)", true],
  [9021, "Payload (alt 2)", true],
];
const PORT_PRESETS = [9090, 9020, 9021];

async function api(path, { method = "GET", body, headers = {}, raw = false } = {}) {
  const opts = { method, headers: { "X-Token": TOKEN, ...headers } };
  if (body !== undefined) {
    if (raw) {
      opts.body = body;
    } else {
      opts.body = JSON.stringify(body);
      opts.headers["Content-Type"] = "application/json";
    }
  }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

async function withBusy(btn, fn) {
  if (btn.classList.contains("busy")) return;
  btn.classList.add("busy");
  btn.disabled = true;
  try { return await fn(); }
  finally { btn.classList.remove("busy"); btn.disabled = false; }
}

/* ------------------------------------------------------------- log + toasts */
const logEl = $("#log");
const logText = [];

function log(text, kind = "") {
  const time = new Date().toLocaleTimeString([], { hour12: false });
  logText.push(`[${time}] ${text}`);
  const row = el("div", { class: `log-row ${kind}` }, el("span", { class: "t" }, time), el("span", {}, text));
  logEl.append(row);
  while (logEl.children.length > 300) logEl.firstChild.remove();
  logEl.scrollTop = logEl.scrollHeight;
}

function toast(text, kind = "ok") {
  const t = el("div", { class: `toast ${kind}` }, text);
  $("#toasts").append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, 3500);
}

function report(ok, text) {
  log(text, ok ? "ok" : "err");
  toast(text, ok ? "ok" : "err");
}

function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = el("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function confirmDialog(title, body, yesLabel = "Confirm") {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-body").textContent = body;
  $("#confirm-yes").textContent = yesLabel;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "yes"), { once: true });
    dlg.showModal();
  });
}

$("#log-clear").addEventListener("click", () => { logEl.replaceChildren(); logText.length = 0; });
$("#log-save").addEventListener("click", () => {
  if (!logText.length) return log("Nothing to save yet", "warn");
  download("ps4-toolkit-log.txt", logText.join("\n") + "\n");
});

/* ----------------------------------------------------------------- settings */
let settings = {};

async function loadSettings() {
  settings = await api("/api/settings");
  return settings;
}

const pushSettings = debounce(async (patch) => {
  try { settings = await api("/api/settings", { method: "POST", body: patch }); }
  catch (e) { log(e.message, "err"); }
}, 300);

function saveSetting(patch) {
  Object.assign(settings, patch);
  pushSettings(patch);
}

async function refreshRecent() {
  try { settings = await api("/api/settings"); renderRecentIps(); } catch { /* ignore */ }
}

/* -------------------------------------------------------------------- tabs */
function showTab(name) {
  for (const tab of $$(".tab")) {
    const on = tab.dataset.tab === name;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", String(on));
  }
  for (const panel of $$(".panel")) {
    panel.hidden = panel.id !== `panel-${name}`;
  }
}
const TAB_NAMES = ["notify", "payload", "klog", "tools"];
$$(".tab").forEach((t) => t.addEventListener("click", () => rememberTab(t.dataset.tab)));

function rememberTab(name) {
  showTab(name);
  if (TAB_NAMES.includes(name) && settings.last_tab !== name) saveSetting({ last_tab: name });
}

// Ctrl/Cmd+1..4 jumps to a section, unless the user is typing in a text field
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
  const n = Number(e.key);
  if (n >= 1 && n <= TAB_NAMES.length) { e.preventDefault(); rememberTab(TAB_NAMES[n - 1]); }
});

/* ----------------------------------------------------------- connection / IP */
const ipInput = $("#ip");
let statusGen = 0;
let portsCheckedFor = "";  // port check runs once per address when it first answers

function setStatus(kind, text) {
  $("#status-pill").className = `pill ${kind}`;
  $("#status-text").textContent = text;
}

async function refreshStatus(silent = false) {
  const ip = ipInput.value.trim();
  const gen = ++statusGen;
  if (!ip) return setStatus("idle", "Enter an IP");
  if (!silent) setStatus("checking", "Checking…");
  $("#refresh-status").classList.add("spin");
  try {
    const r = await api(`/api/status?ip=${encodeURIComponent(ip)}`);
    if (gen !== statusGen) return;
    if (!r.valid) setStatus("idle", "Enter a valid IP");
    else setStatus(r.online ? "online" : "offline", r.online ? "Online" : "No reply");
    if (r.valid && r.online && gen === statusGen && portsCheckedFor !== ip) {
      portsCheckedFor = ip;
      runPortCheck();
    }
  } catch {
    if (gen === statusGen) setStatus("idle", "Unavailable");
  } finally {
    if (gen === statusGen) $("#refresh-status").classList.remove("spin");
  }
}

const refreshStatusSoon = debounce(() => refreshStatus(), 700);

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

function saveTypedIp() {
  const ip = ipInput.value.trim();
  if (!ip) return;
  if (!IPV4.test(ip)) {
    statusGen++;
    setStatus("idle", "Enter a valid IP address");
    return;
  }
  const recent = [ip, ...(settings.recent_ips || []).filter((x) => x !== ip)].slice(0, 6);
  saveSetting({ recent_ips: recent });
  renderRecentIps();
  statusGen++;
  setStatus("checking", "Checking…");
  refreshStatusSoon();
}

ipInput.addEventListener("change", saveTypedIp);
ipInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); ipInput.blur(); saveTypedIp(); }
});
$("#refresh-status").addEventListener("click", () => refreshStatus());

function renderRecentIps() {
  const box = $("#recent-ips");
  box.replaceChildren();
  for (const ip of settings.recent_ips || []) {
    const x = el("span", { class: "x", title: `Forget ${ip}`, role: "button", "aria-label": `Forget ${ip}` }, icon("x"));
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      saveSetting({ recent_ips: (settings.recent_ips || []).filter((v) => v !== ip) });
      renderRecentIps();
    });
    box.append(el("button", {
      class: "chip plain mono", type: "button",
      onclick: () => { ipInput.value = ip; saveTypedIp(); },
    }, ip, x));
  }
}

$("#clear-recent").addEventListener("click", () => {
  saveSetting({ recent_ips: [] });
  renderRecentIps();
  log("Cleared recent addresses", "muted");
});

function currentIp() {
  const ip = ipInput.value.trim();
  if (!ip) { log("Enter the PS4 IP address first", "warn"); ipInput.focus(); return null; }
  return ip;
}

/* ------------------------------------------------------------------- notify */
const message = $("#message");

function updateCount() {
  const n = message.value.trim().length;
  $("#char-count").textContent = `${n} character${n === 1 ? "" : "s"}`;
}
message.addEventListener("input", updateCount);
message.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendNotify(); }
});

function renderTemplates() {
  const box = $("#templates");
  box.replaceChildren();
  for (const text of settings.templates || []) {
    const chip = el("span", { class: "chip", tabindex: "0", role: "button", title: text });
    chip.append(el("span", { class: "label" }, text));
    const x = el("span", { class: "x", title: "Delete template", role: "button" }, icon("x"));
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      saveSetting({ templates: settings.templates.filter((t) => t !== text) });
      renderTemplates();
      log("Template deleted", "muted");
    });
    chip.append(x);
    const use = () => { message.value = text; updateCount(); message.focus(); };
    chip.addEventListener("click", use);
    chip.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); use(); } });
    box.append(chip);
  }
}

$("#save-template").addEventListener("click", () => {
  const text = message.value.trim();
  if (!text) return log("Type a message first, then save it as a template", "warn");
  const list = (settings.templates || []).filter((t) => t !== text);
  list.unshift(text);
  saveSetting({ templates: list.slice(0, 20) });
  renderTemplates();
  log("Template saved", "ok");
});

function renderHistory() {
  const box = $("#history");
  box.replaceChildren();
  for (const text of settings.notify_history || []) {
    const chip = el("span", { class: "chip", tabindex: "0", role: "button", title: "Load this message" });
    chip.append(el("span", { class: "label" }, text));
    const use = () => { message.value = text; updateCount(); message.focus(); };
    chip.addEventListener("click", use);
    chip.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); use(); } });
    box.append(chip);
  }
}

$("#clear-history").addEventListener("click", () => {
  saveSetting({ notify_history: [] });
  renderHistory();
  log("Cleared recent messages", "muted");
});

async function sendNotify() {
  const ip = currentIp();
  const text = message.value.trim();
  if (!ip) return;
  if (!text) return log("Enter a message first", "warn");
  log(`Sending notification to ${ip}:744...`, "muted");
  await withBusy($("#send-notify"), async () => {
    try {
      const r = await api("/api/notify", { method: "POST", body: { ip, message: text } });
      report(true, r.message);
      const history = [text, ...(settings.notify_history || []).filter((t) => t !== text)].slice(0, 10);
      saveSetting({ notify_history: history });
      renderHistory();
      refreshRecent();
    } catch (e) { report(false, e.message); }
  });
}
$("#send-notify").addEventListener("click", sendNotify);

/* ------------------------------------------------------------------ payload */
let payloads = [];
let selected = "";
let sending = false;

function renderLibrary() {
  const list = $("#library");
  list.replaceChildren();
  for (const p of payloads) {
    const item = el("li", { class: `lib-item${p.name === selected ? " selected" : ""}`, tabindex: "0", role: "button" });
    const del = el("button", { class: "icon-btn", type: "button", title: "Remove from library", "aria-label": `Remove ${p.name}` }, icon("trash"));
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const r = await api(`/api/payloads?name=${encodeURIComponent(p.name)}`, { method: "DELETE" });
        payloads = r.payloads;
        if (selected === p.name) select("");
        renderLibrary();
        log(`Removed ${p.name} from the library`, "muted");
      } catch (err) { report(false, err.message); }
    });
    item.append(
      icon("file"),
      el("span", { class: "name" }, p.name),
      el("span", { class: "meta" }, formatBytes(p.size)),
      del,
    );
    const pick = () => select(p.name);
    item.addEventListener("click", pick);
    item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    list.append(item);
  }
  updateSendButton();
}

function select(name) {
  selected = name;
  saveSetting({ last_file: name });
  renderLibrary();
  setProgress(0, name ? `Ready to send ${name}` : "Pick a payload to send");
}

function updateSendButton() {
  $("#send-payload").disabled = sending || !selected;
}

function setProgress(pct, text, state = "") {
  const bar = $("#progress-bar");
  bar.style.width = `${pct}%`;
  bar.className = `bar ${state}`;
  if (text !== undefined) $("#progress-text").textContent = text;
}

async function uploadFiles(files) {
  let last = "";
  for (const file of files) {
    if (file.size === 0) { log(`${file.name} is empty`, "warn"); continue; }
    if (file.size > 64 * 1024 * 1024) { log(`${file.name} is larger than 64 MB`, "err"); continue; }
    try {
      const r = await api("/api/payloads", {
        method: "POST", raw: true, body: file,
        headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
      });
      payloads = r.payloads;
      last = r.name;
      log(`Added ${r.name} (${formatBytes(file.size)})`, "ok");
    } catch (e) { report(false, `Could not add ${file.name}: ${e.message}`); }
  }
  if (last) select(last); else renderLibrary();
}

const dropzone = $("#dropzone");
$("#file-input").addEventListener("change", (e) => { uploadFiles([...e.target.files]); e.target.value = ""; });
["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("over"); }));
dropzone.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));
// Stop the window from navigating to a file that is dropped outside the zone.
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

const portInput = $("#port");
portInput.addEventListener("input", () => {
  const n = parseInt(portInput.value, 10);
  if (n >= 1 && n <= 65535) saveSetting({ port: n });
});
for (const p of PORT_PRESETS) {
  $("#port-presets").append(el("button", {
    class: "chip plain mono", type: "button",
    onclick: () => { portInput.value = p; saveSetting({ port: p }); },
  }, String(p)));
}
$("#notify-after").addEventListener("change", (e) => saveSetting({ notify_after: e.target.checked }));

async function sendPayload() {
  const ip = currentIp();
  if (!ip || !selected || sending) return;
  const port = parseInt(portInput.value, 10);
  if (!(port >= 1 && port <= 65535)) return log("Port must be a number between 1 and 65535", "err");

  sending = true;
  updateSendButton();
  $("#send-payload").classList.add("busy");
  setProgress(0, "Connecting…");
  log(`Sending ${selected} to ${ip}:${port}...`, "muted");
  try {
    const { job } = await api("/api/payload/send", {
      method: "POST",
      body: { ip, port, name: selected, notify_after: $("#notify-after").checked },
    });
    let state;
    do {
      await new Promise((r) => setTimeout(r, 150));
      state = await api(`/api/job?id=${job}`);
      const pct = state.total ? (state.sent / state.total) * 100 : 0;
      if (state.state === "running") {
        setProgress(pct, `${formatBytes(state.sent)} / ${formatBytes(state.total)}  (${Math.round(pct)}%)`);
      }
    } while (state.state === "running");

    report(state.ok, state.message);
    setProgress(state.ok ? 100 : 0, state.ok ? "Done" : "Failed", state.ok ? "ok" : "err");
    if (state.note) log(state.note.message, state.note.ok ? "ok" : "warn");
    refreshRecent();
  } catch (e) {
    report(false, e.message);
    setProgress(0, "Failed", "err");
  } finally {
    sending = false;
    $("#send-payload").classList.remove("busy");
    updateSendButton();
  }
}
$("#send-payload").addEventListener("click", sendPayload);

/* --------------------------------------------------------------------- klog */
const klogOut = $("#klog-out");
const klogFilter = $("#klog-filter");
const klogLines = [];
let klogRunning = false;
let klogNext = 0;
let klogTimer = null;

function klogKind(line) {
  const s = line.toLowerCase();
  if (/error|fail|panic/.test(s)) return "err";
  if (s.includes("warn")) return "warn";
  return "";
}

function klogMatches(line) {
  const needle = klogFilter.value.trim().toLowerCase();
  return !needle || line.toLowerCase().includes(needle);
}

function klogRow(line) {
  const kind = klogKind(line);
  return el("div", kind ? { class: kind } : {}, line || " ");
}

function addKlog(lines) {
  const frag = document.createDocumentFragment();
  for (const line of lines) {
    klogLines.push(line);
    if (klogMatches(line)) frag.append(klogRow(line));
  }
  if (klogLines.length > 20000) klogLines.splice(0, klogLines.length - 20000);
  klogOut.append(frag);
  while (klogOut.children.length > 4000) klogOut.firstChild.remove();
  if ($("#klog-autoscroll").checked) klogOut.scrollTop = klogOut.scrollHeight;
}

function rerenderKlog() {
  const frag = document.createDocumentFragment();
  for (const line of klogLines.slice(-4000)) if (klogMatches(line)) frag.append(klogRow(line));
  klogOut.replaceChildren(frag);
  if ($("#klog-autoscroll").checked) klogOut.scrollTop = klogOut.scrollHeight;
}
klogFilter.addEventListener("input", debounce(rerenderKlog, 200));

function setKlogButton(running) {
  const btn = $("#klog-toggle");
  btn.querySelector("span").textContent = running ? "Stop" : "Start";
  btn.querySelector("use").setAttribute("href", running ? "#i-stop" : "#i-play");
  btn.classList.toggle("primary", !running);
  btn.classList.toggle("danger", running);
  btn.disabled = false;
}

async function klogPoll() {
  try {
    const r = await api(`/api/klog?since=${klogNext}`);
    klogNext = r.next;
    if (r.lines.length) addKlog(r.lines);
    const live = r.state === "connecting" || r.state === "connected";
    const failed = r.message.startsWith("Could not") || r.message.startsWith("Connection");
    $("#klog-status").textContent = (r.state === "connected" ? "● " : "") + r.message;
    $("#klog-status").style.color = r.state === "connected" ? "var(--ok)" : failed ? "var(--err)" : "";
    if (live) {
      klogTimer = setTimeout(klogPoll, 400);
    } else {
      klogRunning = false;
      setKlogButton(false);
      log(`Klog: ${r.message}`, failed ? "err" : "muted");
    }
  } catch {
    klogTimer = setTimeout(klogPoll, 1500);
  }
}

$("#klog-toggle").addEventListener("click", async () => {
  if (klogRunning) {
    $("#klog-toggle").disabled = true;
    try { await api("/api/klog/stop", { method: "POST", body: {} }); } catch (e) { log(e.message, "err"); }
    return;
  }
  const ip = currentIp();
  if (!ip) return;
  const port = parseInt($("#klog-port").value, 10);
  if (!(port >= 1 && port <= 65535)) return log("Klog port must be a number between 1 and 65535", "err");
  try {
    const r = await api("/api/klog/start", { method: "POST", body: { ip, port } });
    klogNext = r.next;
    klogRunning = true;
    setKlogButton(true);
    $("#klog-status").textContent = `Connecting to ${ip}:${port}…`;
    $("#klog-status").style.color = "";
    clearTimeout(klogTimer);
    klogPoll();
  } catch (e) { report(false, e.message); }
});

$("#klog-clear").addEventListener("click", () => { klogLines.length = 0; klogOut.replaceChildren(); });
$("#klog-save").addEventListener("click", () => {
  if (!klogLines.length) return log("No klog output to save yet", "warn");
  download("ps4-klog.txt", klogLines.join("\n") + "\n");
  log("Klog saved", "ok");
});
$("#klog-port").addEventListener("input", () => {
  const n = parseInt($("#klog-port").value, 10);
  if (n >= 1 && n <= 65535) saveSetting({ klog_port: n });
});

/* -------------------------------------------------------------------- tools */
const portCells = {};
for (const [port, name] of SERVICES) {
  const cell = el("div", { class: "port" },
    el("span", { class: "dot" }),
    el("span", { class: "num" }, String(port)),
    el("span", { class: "what" }, name),
    el("span", { class: "state" }),
  );
  portCells[port] = cell;
  $("#ports-grid").append(cell);
}

function setPortState(port, state) {
  const cell = portCells[port];
  cell.className = `port ${state || ""}`;
  cell.querySelector(".state").textContent = { open: "open", closed: "closed", checking: "checking…", skipped: "skipped" }[state] || "";
}

async function runPortCheck() {
  const ip = currentIp();
  if (!ip) return;
  const include = $("#include-payload").checked;
  for (const [port, , isPayload] of SERVICES) setPortState(port, isPayload && !include ? "skipped" : "checking");
  log(`Checking ports on ${ip}...`, "muted");
  await withBusy($("#check-ports"), async () => {
    try {
      const { results } = await api("/api/ports", { method: "POST", body: { ip, include_payload: include } });
      for (const [port, up] of Object.entries(results)) setPortState(port, up === null ? "skipped" : up ? "open" : "closed");
      const open = Object.entries(results).filter(([, up]) => up).map(([p]) => p);
      log(open.length ? `Open ports: ${open.join(", ")}` : "No open ports found", open.length ? "ok" : "warn");
    } catch (e) {
      for (const [port] of SERVICES) setPortState(port, "");
      report(false, e.message);
    }
  });
}
$("#check-ports").addEventListener("click", runPortCheck);

$("#open-ftp").addEventListener("click", async () => {
  const ip = currentIp();
  if (!ip) return;
  try { log((await api("/api/ftp", { method: "POST", body: { ip } })).message, "muted"); }
  catch (e) { report(false, e.message); }
});

$("#copy-ip").addEventListener("click", async () => {
  const ip = currentIp();
  if (!ip) return;
  try { await navigator.clipboard.writeText(ip); log(`Copied ${ip} to the clipboard`, "muted"); toast("Copied"); }
  catch { log("Could not copy to the clipboard", "err"); }
});

$("#reboot").addEventListener("click", async () => {
  const ip = currentIp();
  if (!ip) return;
  const yes = await confirmDialog("Reboot PS4", `Restart the PS4 at ${ip}? Anything running on it will be closed.`, "Reboot");
  if (!yes) return;
  log(`Sending reboot command to ${ip}:744...`, "muted");
  await withBusy($("#reboot"), async () => {
    try {
      const r = await api("/api/reboot", { method: "POST", body: { ip } });
      report(true, r.message);
      refreshRecent();
      setStatus("checking", "Restarting…");
      setTimeout(() => refreshStatus(), 15000);
    } catch (e) { report(false, e.message); }
  });
});

/* --------------------------------------------------------------------- boot */
let missedPings = 0;
let stopped = false;

async function heartbeat() {
  if (stopped) return;
  try {
    await api("/api/ping");
    missedPings = 0;
    $("#offline").hidden = true;
  } catch {
    if (++missedPings >= 3) $("#offline").hidden = false;
  }
}
setInterval(heartbeat, 4000);
window.addEventListener("pageshow", heartbeat);

// Tell the app this tab is going away. It quits a few seconds later unless a page comes back
// (which is what happens on a refresh).
window.addEventListener("pagehide", () => {
  if (stopped) return;
  try { fetch("/api/closing", { method: "POST", keepalive: true, headers: { "X-Token": TOKEN }, body: "{}" }); }
  catch { /* ignore */ }
});

$("#quit").addEventListener("click", async () => {
  const yes = await confirmDialog("Quit PS4 Toolkit", "This stops the toolkit and disconnects any live klog.", "Quit");
  if (!yes) return;
  stopped = true;
  try { await api("/api/quit", { method: "POST", body: {} }); } catch { /* it may drop the connection */ }
  $("#closed").hidden = false;
});
setInterval(() => { if (!document.hidden) refreshStatus(true); }, 20000);

(async function init() {
  try {
    await loadSettings();
  } catch (e) {
    log(`Could not load settings: ${e.message}`, "err");
  }
  ipInput.value = "";  // the address is not restored on reload; recent list still is
  portInput.value = settings.port || 9090;
  $("#klog-port").value = settings.klog_port || 3232;
  $("#notify-after").checked = !!settings.notify_after;
  selected = settings.last_file || "";
  renderRecentIps();
  renderTemplates();
  renderHistory();
  try { payloads = (await api("/api/payloads")).payloads; } catch { payloads = []; }
  if (!payloads.some((p) => p.name === selected)) selected = "";
  renderLibrary();
  if (selected) setProgress(0, `Ready to send ${selected}`);
  showTab(TAB_NAMES.includes(settings.last_tab) ? settings.last_tab : "notify");
  log("Ready.", "muted");
  refreshStatus();
})();
