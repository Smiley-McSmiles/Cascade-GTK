/* Cascade web interface.  It mirrors the GTK app: the same cards, the same roll-down filter bar, the same rows
   inside an expanded torrent and the same schema-generated preferences, all talking to the same daemon. */
"use strict";

// ------------------------------------------------------------------ tiny DOM helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style") el.setAttribute("style", v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (s, r = document) => r.querySelector(s);
function setText(el, t) { t = String(t); if (el.textContent !== t) el.textContent = t; }

// ------------------------------------------------------------------ icons (16px symbolic, like Adwaita's)
const P = (d) => `<path fill="currentColor" d="${d}"/>`;
const ST = (d, w = 1.7) => `<path fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" d="${d}"/>`;
const ICONS = {
  add: P("M7.25 2h1.5v5.25H14v1.5H8.75V14h-1.5V8.75H2v-1.5h5.25z"),
  down: P("M3.3 5.3 8 10l4.7-4.7 1.06 1.06L8 12.12 2.24 6.36z"),
  filter: P("M1.5 2h13a.5.5 0 0 1 .39.81L10 9v4.5a.5.5 0 0 1-.28.45l-3 1.5A.5.5 0 0 1 6 15V9L1.11 2.81A.5.5 0 0 1 1.5 2z"),
  search: P("M6.5 1a5.5 5.5 0 0 1 4.38 8.82l3.65 3.65-1.06 1.06-3.65-3.65A5.5 5.5 0 1 1 6.5 1zm0 1.5a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"),
  select: P("M8 1a7 7 0 1 1 0 14A7 7 0 0 1 8 1zm0 1.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zm2.97 2.97 1.06 1.06L7 11.56 4 8.53l1.06-1.06L7 9.44z"),
  slow: P("M6.5 4C3.74 4 1.5 6.24 1.5 9v.5h10V9c0-2.76-2.24-5-5-5zm6.25 2.5c-.69 0-1.25.56-1.25 1.25v1h-.03c.02.08.03.17.03.25v1.5H1.25a.75.75 0 0 0 0 1.5h.5l-.4 1.2a.5.5 0 0 0 .47.8h1.36a.5.5 0 0 0 .47-.33L4 11.5h5l.35 1.17a.5.5 0 0 0 .48.33h1.34a.5.5 0 0 0 .47-.7L11.2 11.5h.3c.83 0 1.5-.67 1.5-1.5V9h1.25c.69 0 1.25-.56 1.25-1.25v-.5c0-.41-.34-.75-.75-.75z"),
  menu: P("M1 3h14v1.5H1zm0 4.25h14v1.5H1zM1 11.5h14V13H1z"),
  pause: P("M3.5 2h3v12h-3zm6 0h3v12h-3z"),
  play: P("M4 2.2v11.6a.5.5 0 0 0 .76.43l9.4-5.8a.5.5 0 0 0 0-.86l-9.4-5.8A.5.5 0 0 0 4 2.2z"),
  folder: P("M1.5 2.5h4.4l1.6 1.6h7c.55 0 1 .45 1 1V13c0 .55-.45 1-1 1h-13c-.55 0-1-.45-1-1V3.5c0-.55.45-1 1-1zM2 6v6.5h12V6z"),
  link: ST("M6.6 9.4l2.8-2.8M7.2 4.6l1.2-1.2a2.5 2.5 0 0 1 3.5 3.5l-1.2 1.2M8.8 11.4l-1.2 1.2a2.5 2.5 0 0 1-3.5-3.5l1.2-1.2"),
  more: '<circle cx="8" cy="3" r="1.5" fill="currentColor"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/><circle cx="8" cy="13" r="1.5" fill="currentColor"/>',
  trash: P("M6 1h4l.5 1.5H14V4H2V2.5h3.5zM3 5h10l-.75 9.1c-.05.5-.47.9-.98.9H4.73c-.51 0-.93-.4-.98-.9z"),
  info: P("M8 1a7 7 0 1 1 0 14A7 7 0 0 1 8 1zm0 1.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM7.25 7h1.5v4.5h-1.5zM8 4.25a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8z"),
  files: P("M3.5 1h5.8L13 4.7V14c0 .55-.45 1-1 1H3.5c-.55 0-1-.45-1-1V2c0-.55.45-1 1-1zM4 2.5v11h7.5V5.5H8.5v-3z"),
  users: P("M5.5 2a2.75 2.75 0 1 1 0 5.5 2.75 2.75 0 0 1 0-5.5zM11 3a2.25 2.25 0 1 1 0 4.5A2.25 2.25 0 0 1 11 3zM1 13c0-2.5 2-4 4.5-4S10 10.5 10 13v1H1zm10.6 1v-1c0-1.3-.4-2.4-1.1-3.2.2 0 .3-.05.5-.05 2.2 0 4 1.3 4 3.3v.95z"),
  server: P("M2.5 1.5h11c.55 0 1 .45 1 1v3.5c0 .55-.45 1-1 1h-11c-.55 0-1-.45-1-1V2.5c0-.55.45-1 1-1zm0 7.5h11c.55 0 1 .45 1 1v3.5c0 .55-.45 1-1 1h-11c-.55 0-1-.45-1-1V10c0-.55.45-1 1-1zM4 3.5v1.5h1.5V3.5zM4 11v1.5h1.5V11z"),
  grid: P("M2 2h5v5H2zm7 0h5v5H9zm-7 7h5v5H2zm7 0h5v5H9z"),
  cog: '<circle cx="8" cy="8" r="2.6" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
    [0, 45, 90, 135, 180, 225, 270, 315].map(a => `<rect x="7" y="0.6" width="2" height="3.4" rx=".6" fill="currentColor" transform="rotate(${a} 8 8)"/>`).join("") +
    '<circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/>',
  download: P("M7.25 1h1.5v7.4l2.7-2.7 1.06 1.06L8 11.27 3.5 6.76 4.56 5.7l2.7 2.7zM2 12.5h12V14H2z"),
  upload: P("M8 1l4.5 4.5-1.06 1.06-2.69-2.69V10h-1.5V3.87L4.56 6.56 3.5 5.5zM2 9h1.5v3.5h9V9H14v4c0 .55-.45 1-1 1H3c-.55 0-1-.45-1-1z"),
  queue: P("M5 2.5h9V4H5zm0 4.75h9v1.5H5zM5 12h9v1.5H5zM1.5 1.5h2v3h-2zm0 4.75h2v3h-2zm0 4.75h2v3h-2z"),
  refresh: ST("M13 8a5 5 0 1 1-1.46-3.54M13 2v3.5H9.5"),
  bang: P("M8 1a1.5 1.5 0 0 0-1.5 1.6l.45 6.95a1.05 1.05 0 0 0 2.1 0L9.5 2.6A1.5 1.5 0 0 0 8 1zm0 11a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"),
  error: P("M7.13 1.5a1 1 0 0 1 1.74 0l6.5 11.5a1 1 0 0 1-.87 1.5H1.5a1 1 0 0 1-.87-1.5zM7.25 5.5v4h1.5v-4zM8 11a.9.9 0 1 0 0 1.8.9.9 0 0 0 0-1.8z"),
  receive: ST("M2 5.5h11M10 2.5l3 3-3 3M14 10.5H3M6 7.5l-3 3 3 3"),
  copy: P("M5.5 1h7c.83 0 1.5.67 1.5 1.5v8c0 .83-.67 1.5-1.5 1.5h-7C4.67 12 4 11.33 4 10.5v-8C4 1.67 4.67 1 5.5 1zm0 1.5v8h7v-8zM2 5h1v6.5c0 .83.67 1.5 1.5 1.5H11v1H4.5A2.5 2.5 0 0 1 2 11.5z"),
  close: P("M3.7 2.64 8 6.94l4.3-4.3 1.06 1.06L9.06 8l4.3 4.3-1.06 1.06L8 9.06l-4.3 4.3-1.06-1.06L6.94 8l-4.3-4.3z"),
  check: P("M13.47 3.47l1.06 1.06L6 13.06 1.47 8.53l1.06-1.06L6 10.94z"),
  drive: P("M2.5 3h11c.55 0 1 .45 1 1v8c0 .55-.45 1-1 1h-11c-.55 0-1-.45-1-1V4c0-.55.45-1 1-1zM3 9v2.5h10V9zm7.75.6a.65.65 0 1 1 0 1.3.65.65 0 0 1 0-1.3z"),
  nodes: P("M6.5 1h3v3h-.75v2.25h4.5V9H14v3h-3V9h.75V7.75h-7V9h.75v3H2.5V9h.75V6.25h4.5V4H6.5z"),
  port: P("M3 3h10c.55 0 1 .45 1 1v7c0 .55-.45 1-1 1h-2v1.5H5V12H3c-.55 0-1-.45-1-1V4c0-.55.45-1 1-1zm1 2v1.5h1.5V5zm2.5 0v1.5H8V5zM9 5v1.5h1.5V5z"),
  vpn: P("M8 1a4 4 0 0 1 4 4v1.5h.5c.55 0 1 .45 1 1V14c0 .55-.45 1-1 1h-9c-.55 0-1-.45-1-1V7.5c0-.55.45-1 1-1H4V5a4 4 0 0 1 4-4zm0 1.5A2.5 2.5 0 0 0 5.5 5v1.5h5V5A2.5 2.5 0 0 0 8 2.5z"),
  web: ST("M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM1.5 8h13M8 1.5c1.8 1.8 2.6 4 2.6 6.5S9.8 12.7 8 14.5M8 1.5C6.2 3.3 5.4 5.5 5.4 8s.8 4.7 2.6 6.5", 1.4),
  sortdesc: P("M1 3h9v1.5H1zm0 4.25h6v1.5H1zM1 11.5h3V13H1zm11.25-9h1.5v8.4l1.7-1.7 1.06 1.06L13 13.77 9.99 10.76l1.06-1.06 1.2 1.2z"),
  sortasc: P("M1 3h3v1.5H1zm0 4.25h6v1.5H1zM1 11.5h9V13H1zm12-9.27 3.01 3.01-1.06 1.06-1.2-1.2v8.9h-1.5V5.6l-1.7 1.7L9.49 6.24z"),
  label: P("M2.5 1A1.5 1.5 0 0 0 1 2.5v4.38c0 .4.16.78.44 1.06l6.62 6.62a1.5 1.5 0 0 0 2.12 0l3.88-3.88a1.5 1.5 0 0 0 0-2.12L7.44 1.44A1.5 1.5 0 0 0 6.38 1zM4.5 3a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z"),
  minus: P("M2 7.25h12v1.5H2z"),
  edit: P("M11.3 1.3a1 1 0 0 1 1.4 0l2 2a1 1 0 0 1 0 1.4L6 13.4 2 14l.6-4zM3.9 10.5l-.3 1.9 1.9-.3 6.9-6.9-1.6-1.6z"),
  up: P("M8 3.2l5.76 5.76-1.06 1.06L8 5.32 3.3 10.02 2.24 8.96z"),
  app: ST("M4 2l4 3 4-3M4 6.5l4 3 4-3M4 11l4 3 4-3", 2),
  globe: "",
};
ICONS.globe = ICONS.web;
function icon(name, cls) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 16 16");
  s.setAttribute("class", "icon" + (cls ? " " + cls : ""));
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}
function iconBtn(name, title, onclick, cls) {
  return h("button", { class: "btn flat icon-only " + (cls || ""), title, "aria-label": title, onclick }, icon(name));
}

// ------------------------------------------------------------------ formatting (same as cascade/util.py)
const UNITS = ["B", "kB", "MB", "GB", "TB", "PB"];
function fmtSize(n) {
  if (n === null || n === undefined || n < 0) return "-";
  if (n < 1000) return `${Math.round(n)} B`;
  for (let i = 1; i < UNITS.length; i++) {
    n /= 1000;
    if (n < 999.95 || i === UNITS.length - 1) return `${n.toFixed(1)} ${UNITS[i]}`;
  }
}
const fmtSpeed = (b) => (!b || b <= 0 ? "0 B/s" : fmtSize(b) + "/s");
function fmtDuration(s, short) {
  if (s === null || s === undefined || s < 0) return "∞";
  s = Math.floor(s);
  if (s < 60) return `${s} s`;
  let m = Math.floor(s / 60); s %= 60;
  if (m < 60) return short || !s ? `${m} min` : `${m} min ${s} s`;
  let hh = Math.floor(m / 60); m %= 60;
  if (hh < 24) return short || !m ? `${hh} h` : `${hh} h ${m} min`;
  let d = Math.floor(hh / 24); hh %= 24;
  if (d < 365) return short || !hh ? `${d} d` : `${d} d ${hh} h`;
  return `${Math.floor(d / 365)} y ${d % 365} d`;
}
function fmtTime(ts) {
  if (!ts || ts <= 0) return "-";
  const d = new Date(ts * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
const fmtRatio = (r) => (r === null || r === undefined || r < 0 ? "∞" : r.toFixed(2));
const isMagnet = (s) => /^magnet:\?/i.test((s || "").trim());
const isUrl = (s) => /^https?:\/\//i.test((s || "").trim());

let STATE_LABELS = { downloading: "Downloading", seeding: "Seeding", paused: "Paused", queued: "Queued",
  checking: "Checking", metadata: "Fetching metadata", moving: "Moving", error: "Error" };
let FILTERS = [["all", "All Torrents"], ["downloading", "Downloading"], ["seeding", "Seeding"], ["completed", "Completed"],
  ["paused", "Paused"], ["queued", "Queued"], ["active", "Active"], ["checking", "Checking"], ["error", "Errors"]];
let SORTS = [["queue", "Queue order"], ["name", "Name"], ["added", "Date added"], ["progress", "Progress"],
  ["size", "Size"], ["down", "Download speed"], ["up", "Upload speed"], ["ratio", "Ratio"], ["eta", "Time left"]];
const STATE_ICONS = { downloading: "download", seeding: "upload", paused: "pause", queued: "queue", checking: "refresh",
  metadata: "receive", moving: "folder", error: "bang" };
const SORT_KEYS = {
  queue: (t) => [t.queue >= 0 ? t.queue : 1 << 30, -t.added], name: (t) => t.name.toLowerCase(), added: (t) => t.added,
  progress: (t) => t.progress, size: (t) => t.size, down: (t) => t.down, up: (t) => t.up, ratio: (t) => t.ratio,
  eta: (t) => (t.eta >= 0 ? t.eta : 1 << 40),
};

// ------------------------------------------------------------------ state
const S = {
  token: localStorage.getItem("cascade-token") || "", schema: null, settings: {}, session: {}, torrents: [],
  counts: {}, labels: {}, trackers: {}, filter: "all", labelFilter: null, trackerFilter: null, search: "",
  sort: "queue", sortDesc: false, selection: false, selected: new Set(), cards: new Map(), eventSeq: null,
  syncing: false, connected: false, settingsRev: null, n: 0, pendingMagnet: null,
};

// ------------------------------------------------------------------ API
class AuthError extends Error {}
async function api(method, params = {}) {
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers.Authorization = "Bearer " + S.token;
  let r;
  try {
    r = await fetch("/api/rpc", { method: "POST", headers, body: JSON.stringify({ method, params }) });
  } catch (e) {
    throw new Error("Cannot reach Cascade");
  }
  if (r.status === 401) throw new AuthError("not signed in");
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || "failed");
  return j.result;
}
function call(method, params, ok, msg) {
  return api(method, params).then((r) => { if (msg) toast(msg); if (ok) ok(r); syncSoon(); return r; })
    .catch((e) => { if (e instanceof AuthError) showLogin(); else toast(e.message); });
}

// ------------------------------------------------------------------ toasts, popovers, dialogs
function toast(msg, action, timeout = 3500) {
  const box = $("#toasts");
  const t = h("div", { class: "toast", role: "status" }, h("span", { text: msg }));
  if (action) t.append(h("button", { class: "btn", text: action.label, onclick: () => { action.cb(); t.remove(); } }));
  t.append(h("button", { class: "btn flat icon-only", "aria-label": "Close", onclick: () => t.remove() }, icon("close")));
  box.append(t);
  while (box.children.length > 3) box.firstChild.remove();
  setTimeout(() => t.remove(), timeout);
}
let openPopover = null;
function closePopover() { if (openPopover) { openPopover.remove(); openPopover = null; } }
function popover(anchor, content, align = "start") {
  closePopover();
  const pop = h("div", { class: "popover", role: "menu" }, content);
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  let left = align === "end" ? r.right - pw : r.left;
  left = Math.max(6, Math.min(left, window.innerWidth - pw - 6));
  let top = r.bottom + 6;
  if (top + ph > window.innerHeight - 6) top = Math.max(6, r.top - ph - 6);
  pop.style.left = left + "px";
  pop.style.top = top + "px";
  openPopover = pop;
  setTimeout(() => document.addEventListener("pointerdown", function off(e) {
    if (!pop.contains(e.target)) { closePopover(); document.removeEventListener("pointerdown", off, true); }
  }, true));
  return pop;
}
function menu(anchor, items, align) {
  const box = h("div");
  for (const it of items) {
    if (it === "-") { box.append(h("div", { class: "menu-sep" })); continue; }
    if (!it) continue;
    box.append(h("button", { class: "menu-item", role: "menuitem", disabled: it.disabled,
      onclick: () => { closePopover(); it.cb(); } }, it.icon ? icon(it.icon) : null, h("span", { text: it.label })));
  }
  return popover(anchor, box, align);
}
const dialogs = [];
function dialog({ title, width, height, start, end, body, onclose, cls }) {
  const bd = h("div", { class: "backdrop" });
  const dlg = h("div", { class: "dialog " + (cls || ""), role: "dialog", "aria-modal": "true", "aria-label": title,
    style: `--w:${width || 560}px;--h:${height || 720}px` });
  const close = () => { bd.remove(); const i = dialogs.indexOf(entry); if (i >= 0) dialogs.splice(i, 1); if (onclose) onclose(); };
  const entry = { close, el: dlg };
  const head = h("div", { class: "dhead" }, start || null, h("div", { class: "dtitle", text: title || "" }),
    h("div", { class: "end" }, end !== undefined ? end : iconBtn("close", "Close", close, "circular")));
  dlg.append(head, body);
  bd.append(dlg);
  bd.addEventListener("pointerdown", (e) => { if (e.target === bd) close(); });
  document.body.append(bd);
  dialogs.push(entry);
  return entry;
}
function alertDialog({ heading, body, extra, responses, onresponse }) {
  const bd = h("div", { class: "backdrop alert-backdrop" });
  const content = h("div", { class: "dbody" }, h("h2", { text: heading }), body ? h("p", { text: body }) : null, extra || null);
  const foot = h("div", { class: "dfoot" });
  const dlg = h("div", { class: "dialog alert", role: "alertdialog", style: "--w:400px" }, content, foot);
  const entry = { close: () => { bd.remove(); dialogs.splice(dialogs.indexOf(entry), 1); }, el: dlg };
  for (const [id, label, appearance] of responses) {
    foot.append(h("button", { class: appearance || "", text: label,
      onclick: () => { entry.close(); if (onresponse) onresponse(id); } }));
  }
  bd.append(dlg);
  document.body.append(bd);
  dialogs.push(entry);
  const inp = $("input", dlg);
  if (inp) inp.focus();
  return entry;
}
function askText(heading, placeholder, button, cb, text = "", body = "") {
  const inp = h("input", { class: "entry", placeholder, value: text, style: "width:100%" });
  const d = alertDialog({ heading, body, extra: inp, responses: [["cancel", "Cancel"], ["ok", button, "suggested"]],
    onresponse: (r) => { if (r === "ok" && inp.value.trim()) cb(inp.value.trim()); } });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && inp.value.trim()) { d.close(); cb(inp.value.trim()); } });
}

// ------------------------------------------------------------------ rows (the libadwaita row types)
function row(title, subtitle, { prefix, suffix, property, activatable, onclick, cls } = {}) {
  const sub = h("div", { class: "rsub" });
  if (subtitle) sub.textContent = subtitle; else sub.classList.add("hidden");
  const el = h("div", { class: "row" + (property ? " property" : "") + (activatable ? " activatable" : "") + (cls ? " " + cls : ""),
    onclick }, prefix ? h("div", { class: "prefix" }, prefix) : null,
    h("div", { class: "rtext" }, h("div", { class: "rtitle", text: title }), sub), suffix ? h("div", { class: "suffix" }, suffix) : null);
  el.setSub = (s) => { setText(sub, s || ""); sub.classList.toggle("hidden", !s); };
  el.setTitle = (s) => setText($(".rtitle", el), s);
  return el;
}
function switchEl(checked, onchange) {
  const inp = h("input", { type: "checkbox", checked, role: "switch" });
  inp.addEventListener("change", () => onchange(inp.checked));
  const lab = h("label", { class: "switch" }, inp, h("span"));
  lab.input = inp;
  return lab;
}
function switchRow(title, subtitle, checked, onchange) {
  const sw = switchEl(checked, onchange);
  const r = row(title, subtitle, { suffix: sw, activatable: true });
  r.addEventListener("click", (e) => { if (!sw.contains(e.target)) { sw.input.checked = !sw.input.checked; onchange(sw.input.checked); } });
  r.input = sw.input;
  return r;
}
function spinEl(value, { min = 0, max = 1e12, step = 1, digits = 0 }, onchange) {
  const inp = h("input", { type: "number", min, max, step, value: Number(value || 0).toFixed(digits), inputmode: "decimal" });
  let timer = null;
  const fire = () => { clearTimeout(timer); timer = setTimeout(() => {
    let v = parseFloat(inp.value); if (isNaN(v)) v = min; v = Math.max(min, Math.min(max, v));
    onchange(digits ? v : Math.round(v)); }, 500); };
  const bump = (d) => { let v = (parseFloat(inp.value) || 0) + d * step; v = Math.max(min, Math.min(max, v));
    inp.value = v.toFixed(digits); fire(); };
  inp.addEventListener("input", fire);
  const el = h("div", { class: "spin" }, inp, h("button", { class: "btn", "aria-label": "Decrease", onclick: () => bump(-1) }, icon("minus")),
    h("button", { class: "btn", "aria-label": "Increase", onclick: () => bump(1) }, icon("add")));
  el.set = (v) => { if (document.activeElement !== inp) inp.value = Number(v || 0).toFixed(digits); };
  return el;
}
function selectEl(options, value, onchange, flat) {
  const sel = h("select", { class: "select" + (flat ? " flat" : "") });
  for (const [k, label] of options) sel.append(h("option", { value: k, text: label }));
  sel.value = value;
  sel.addEventListener("change", () => onchange(sel.value));
  return sel;
}
function entryRow(title, value, onapply, { password, placeholder } = {}) {
  const inp = h("input", { type: password ? "password" : "text", placeholder: placeholder || title, value: value || "", "aria-label": title });
  const apply = h("button", { class: "btn suggested circular icon-only hidden", "aria-label": "Apply", onclick: () => { apply.classList.add("hidden"); onapply(inp.value); } }, icon("check"));
  inp.addEventListener("input", () => apply.classList.remove("hidden"));
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { apply.classList.add("hidden"); onapply(inp.value); } });
  const el = h("div", { class: "entry-row" }, inp, apply);
  el.input = inp;
  return el;
}
function expander(title, iconName, onopen) {
  const content = h("div", { class: "content" });
  const r = row(title, "", { prefix: icon(iconName), suffix: icon("down", "arrow"), activatable: true });
  const el = h("div", { class: "expander" }, r, h("div", { class: "ewrap" }, content));
  r.addEventListener("click", () => { el.classList.toggle("open"); if (el.classList.contains("open") && onopen) onopen(); });
  el.content = content;
  el.head = r;
  el.isOpen = () => el.classList.contains("open");
  el.open = () => { if (!el.isOpen()) { el.classList.add("open"); if (onopen) onopen(); } };
  return el;
}
function group(title, description, ...rows) {
  return h("div", { class: "group" }, (title || description) ? h("div", { class: "group-head" }, h("div", { class: "gtext" },
    title ? h("div", { class: "group-title", text: title }) : null, description ? h("div", { class: "group-desc", text: description }) : null)) : null,
  h("div", { class: "boxed-list" }, rows));
}

// ------------------------------------------------------------------ the shell (header bar, filter bar, list, status bar)
function buildShell() {
  const app = $("#app");
  app.innerHTML = "";
  const addBtn = h("button", { class: "btn", title: "Open a torrent file", onclick: () => $("#file-input").click() }, icon("add"));
  const addMenu = h("button", { class: "btn", title: "More ways to add", onclick: (e) => menu(e.currentTarget.parentElement, [
    { label: "Open Torrent File…", cb: () => $("#file-input").click() },
    { label: "Add Magnet Link or URL…", cb: () => magnetDialog() },
    { label: "Paste from Clipboard", cb: () => pasteClipboard() }, "-",
    { label: "Create Torrent…", cb: () => createDialog() }]) }, icon("down"));
  S.filterBtn = h("button", { class: "btn flat icon-only toggle", title: "Filter and sort", "aria-pressed": "false",
    onclick: () => toggleFilterBar() }, icon("filter"));
  S.titleT = h("div", { class: "t", text: "Cascade" });
  S.titleS = h("div", { class: "s" });
  S.searchBtn = h("button", { class: "btn flat icon-only toggle", title: "Search (Ctrl+K)", "aria-pressed": "false", onclick: () => toggleSearch() }, icon("search"));
  S.selectBtn = h("button", { class: "btn flat icon-only toggle hide-narrow", title: "Select torrents", "aria-pressed": "false", onclick: () => setSelection(!S.selection) }, icon("select"));
  S.slowBtn = h("button", { class: "btn flat icon-only toggle", title: "Slow mode: use the alternative speed limits", "aria-pressed": "false",
    onclick: () => call("alt_speed.set", { on: S.slowBtn.getAttribute("aria-pressed") !== "true" }, (r) => toast(r ? "Slow mode on" : "Slow mode off")) }, icon("slow"));
  const menuBtn = h("button", { class: "btn flat icon-only", title: "Main menu", onclick: (e) => menu(e.currentTarget, [
    { label: "Resume All", cb: () => call("torrents.resume", { ids: "all" }, null, "Resumed every torrent") },
    { label: "Pause All", cb: () => call("torrents.pause", { ids: "all" }, null, "Paused every torrent") }, "-",
    { label: "Statistics", cb: () => statsDialog() },
    { label: "Open Magnet Links Here", cb: () => registerMagnetHandler() }, "-",
    { label: "Preferences", cb: () => prefsDialog() },
    { label: "Keyboard Shortcuts", cb: () => shortcutsDialog() },
    { label: "About Cascade", cb: () => aboutDialog() }, "-",
    { label: "Sign Out", cb: () => signOut() }], "end") }, icon("menu"));
  const header = h("header", { class: "headerbar" },
    h("div", { class: "start" }, h("div", { class: "split" }, addBtn, addMenu), S.filterBtn),
    h("div", { class: "title" }, S.titleT, S.titleS),
    h("div", { class: "end" }, S.searchBtn, S.selectBtn, S.slowBtn, menuBtn));

  S.searchInput = h("input", { class: "entry", type: "search", placeholder: "Search torrents",
    oninput: () => { S.search = S.searchInput.value.trim().toLowerCase(); render(); } });
  S.searchBar = h("div", { class: "searchbar hidden" }, S.searchInput);

  S.chips = {};
  const chips = h("div", { class: "chips", role: "tablist" });
  for (const [k, title] of FILTERS) {
    const c = h("span", { class: "count", text: "0" });
    const b = h("button", { class: "chip" + (k === S.filter ? " active" : ""), role: "tab", onclick: () => { S.filter = k; render(); } },
      h("span", { text: title }), c);
    b.count = c;
    S.chips[k] = b;
    chips.append(b);
  }
  S.labelSel = selectEl([["__all", "All labels"]], "__all", (v) => { S.labelFilter = v === "__all" ? null : (v === "__none" ? "" : v); render(); }, true);
  S.trackerSel = selectEl([["__all", "All trackers"]], "__all", (v) => { S.trackerFilter = v === "__all" ? null : v; render(); }, true);
  S.sortSel = selectEl(SORTS, S.sort, (v) => { S.sort = v; render(); api("settings.set", { values: { "ui.sort": v } }).catch(() => {}); }, true);
  S.orderBtn = h("button", { class: "btn flat icon-only", title: "Reverse order", onclick: () => {
    S.sortDesc = !S.sortDesc; updateOrderBtn(); render(); api("settings.set", { values: { "ui.sort_desc": S.sortDesc } }).catch(() => {}); } }, icon("sortasc"));
  S.filterBar = h("div", { class: "rolldown" }, h("div", {}, h("div", { class: "filter-bar" }, chips,
    h("div", { class: "filter-row" }, h("span", { class: "dd" }, icon("label"), S.labelSel), h("span", { class: "dd" }, icon("server"), S.trackerSel),
      h("span", { class: "dd" }, icon("sortdesc"), S.sortSel), S.orderBtn,
      h("button", { class: "btn flat", text: "Reset", onclick: () => resetFilters() })))));

  S.banner = h("div", { class: "banner hidden" }, h("span"), h("button", { class: "btn", onclick: () => prefsDialog(S.bannerPage) }));
  S.list = h("ul", { class: "torrent-list" });
  S.pages = {
    list: h("div", { class: "clamp" }, S.list),
    empty: h("div", { class: "status-page" }, icon("app", "big"), h("h1", { text: "No Torrents Yet" }),
      h("p", { text: "Drop a .torrent file or a magnet link here, or click a magnet link once Cascade handles them (main menu → Open Magnet Links Here)" }),
      h("div", { class: "actions" }, h("button", { class: "btn pill suggested", text: "Open Torrent File…", onclick: () => $("#file-input").click() }),
        h("button", { class: "btn pill", text: "Add Magnet Link…", onclick: () => magnetDialog() }))),
    nomatch: h("div", { class: "status-page" }, icon("search", "big"), h("h1", { text: "No Matching Torrents" }), h("p", { text: "Try another filter or search" }),
      h("div", { class: "actions" }, h("button", { class: "btn pill", text: "Show All Torrents", onclick: () => resetFilters() }))),
  };
  S.main = h("main", {}, S.pages.list, S.pages.empty, S.pages.nomatch);

  S.selLabel = h("div", { class: "center", text: "No torrents selected" });
  S.actionBar = h("div", { class: "actionbar hidden" },
    iconBtn("play", "Resume", () => bulk("torrents.resume")), iconBtn("pause", "Pause", () => bulk("torrents.pause")),
    iconBtn("refresh", "Verify data", () => bulk("torrents.recheck")),
    iconBtn("label", "Set label", (e) => menu(e.currentTarget, [{ label: "No Label", cb: () => bulk("torrents.set_options", { options: { label: "" } }) },
      ...Object.keys(S.labels).filter(Boolean).sort().map((l) => ({ label: l, cb: () => bulk("torrents.set_options", { options: { label: l } }) })),
      "-", { label: "New Label…", cb: () => askText("New Label", "Label name", "Set", (n) => bulk("torrents.set_options", { options: { label: n } })) }])),
    iconBtn("queue", "Queue", (e) => menu(e.currentTarget, [["top", "Move to Top"], ["up", "Move Up"], ["down", "Move Down"], ["bottom", "Move to Bottom"]]
      .map(([w, l]) => ({ label: l, cb: () => bulk("torrents.queue", { where: w }) })))),
    S.selLabel,
    h("button", { class: "btn destructive icon-only", title: "Remove", onclick: () => S.selected.size && confirmRemove([...S.selected]) }, icon("trash")),
    h("button", { class: "btn", text: "Select All", onclick: () => selectAll() }));

  const item = (name, title) => { const lbl = h("span"); const el = h("span", { class: "item", title }, icon(name), lbl); el.lbl = lbl; return el; };
  S.stSpeed = h("button", { class: "btn flat numeric", title: "Statistics", onclick: () => statsDialog() }, "↓ 0 B/s   ↑ 0 B/s");
  S.stFree = item("drive", "Free space in the download folder");
  S.stDht = item("nodes", "DHT nodes");
  S.stPort = item("port", "Listening port");
  S.stIface = item("vpn", "Bound network interface");
  S.stSlow = h("span", { class: "slow hidden", title: "Slow mode is on" }, icon("slow"));
  const footer = h("footer", { class: "statusbar" }, S.stSpeed, h("span", { class: "spacer" }),
    h("span", { class: "extra" }, S.stFree, S.stDht, S.stPort, S.stIface), S.stSlow);
  app.append(header, S.searchBar, S.filterBar, S.banner, S.main, S.actionBar, footer);
  updateOrderBtn();
}
function updateOrderBtn() { S.orderBtn.replaceChildren(icon(S.sortDesc ? "sortdesc" : "sortasc")); }
function toggleFilterBar(force) {
  const open = force !== undefined ? force : !S.filterBar.classList.contains("open");
  S.filterBar.classList.toggle("open", open);
  S.filterBtn.setAttribute("aria-pressed", open ? "true" : "false");
}
function toggleSearch(force) {
  const open = force !== undefined ? force : S.searchBar.classList.contains("hidden");
  S.searchBar.classList.toggle("hidden", !open);
  S.searchBtn.setAttribute("aria-pressed", open ? "true" : "false");
  if (open) S.searchInput.focus(); else { S.searchInput.value = ""; S.search = ""; render(); }
}
function resetFilters() {
  S.filter = "all"; S.labelFilter = null; S.trackerFilter = null; S.labelSel.value = "__all"; S.trackerSel.value = "__all";
  toggleSearch(false); render();
}

// ------------------------------------------------------------------ status pages: connecting, sign-in
function statusOnly(node) {
  const app = $("#app");
  app.innerHTML = "";
  app.append(h("main", {}, node));
}
function showConnecting() {
  statusOnly(h("div", { class: "status-page" }, h("div", { class: "spinner" }), h("h1", { text: "Starting Cascade…" })));
}
function showLogin(msg) {
  S.connected = false;
  const pw = h("input", { class: "entry", type: "password", placeholder: "Password", autocomplete: "current-password", style: "height:42px" });
  const err = h("p", { class: "dim", text: msg || "" });
  const go = async () => {
    try {
      const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw.value }) });
      const j = await r.json();
      if (!j.ok) { err.textContent = j.error; pw.select(); return; }
      S.token = j.result.token;
      localStorage.setItem("cascade-token", S.token);
      start();
    } catch (e) { err.textContent = "Cannot reach Cascade"; }
  };
  pw.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  statusOnly(h("div", { class: "status-page" }, icon("app", "big"), h("h1", { text: "Cascade" }),
    h("p", { text: "Sign in with the web interface password" }),
    h("div", { class: "login-box" }, pw, h("button", { class: "btn pill suggested", text: "Sign In", onclick: go }), err)));
  setTimeout(() => pw.focus(), 50);
}
async function signOut() {
  try { await fetch("/api/logout", { method: "POST", headers: { Authorization: "Bearer " + S.token } }); } catch (e) { /* offline */ }
  S.token = "";
  localStorage.removeItem("cascade-token");
  location.reload();
}

// ------------------------------------------------------------------ syncing
let syncTimer = null;
function syncSoon() { clearTimeout(syncTimer); syncTimer = setTimeout(sync, 250); }
async function sync() {
  if (S.syncing) return;
  S.syncing = true;
  try {
    const d = await api("sync", { since: S.eventSeq || 0, client: "web" });
    onSync(d);
  } catch (e) {
    if (e instanceof AuthError) { showLogin(); return; }
    if (S.connected) toast("Lost the connection: " + e.message);
  } finally {
    S.syncing = false;
    clearTimeout(syncTimer);
    if (S.connected) syncTimer = setTimeout(sync, 1500);
  }
}
async function loadSettings() {
  S.settings = await api("settings.get");
  S.sort = S.settings["ui.sort"] || "queue";
  S.sortDesc = !!S.settings["ui.sort_desc"];
  if (S.sortSel) { S.sortSel.value = S.sort; updateOrderBtn(); }
  const th = S.settings["app.theme"];
  if (th === "light" || th === "dark") document.documentElement.dataset.theme = th;
  else delete document.documentElement.dataset.theme;
}
function onSync(d) {
  S.n++;
  const s = d.session;
  S.session = s;
  const first = S.eventSeq === null;
  if (s.settings_rev !== S.settingsRev) { if (S.settingsRev !== null) loadSettings().then(render); S.settingsRev = s.settings_rev; }
  S.torrents = d.torrents;
  S.counts = d.counts; S.labels = d.labels; S.trackers = d.trackers;
  const seen = new Set();
  for (const t of d.torrents) {
    seen.add(t.id);
    let c = S.cards.get(t.id);
    if (!c) { c = new Card(t); S.cards.set(t.id, c); } else c.update(t);
  }
  for (const [id, c] of S.cards) if (!seen.has(id)) { c.el.remove(); S.cards.delete(id); S.selected.delete(id); }
  render();
  updateStatus(s);
  if (S.n % 2 === 0) for (const c of S.cards.values()) if (c.expanded) c.details.refresh();
  if (!first) for (const ev of d.events || []) onEvent(ev);
  S.eventSeq = s.event_seq;
}
function onEvent(ev) {
  if (ev.kind === "finished") toast(ev.message);
  else if (ev.kind === "torrent_error") toast(ev.message, { label: "Show", cb: () => revealTorrent(ev.id) }, 8000);
  else if (ev.kind === "metadata") toast(ev.message, { label: "Choose Files", cb: () => revealTorrent(ev.id, "files") }, 8000);
  else if (ev.level === "error" || ev.kind === "limit" || (ev.level === "warning" && ["interface", "webui", "ipfilter", "port"].includes(ev.kind))) toast(ev.message, null, 6000);
}
function revealTorrent(id, section) {
  const c = S.cards.get(id);
  if (!c) return;
  resetFilters();
  c.setExpanded(true);
  if (section) c.details.exp[section].open();
  c.el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function matches(t, f) {
  if (f === "all") return true;
  if (f === "downloading") return t.state === "downloading" || t.state === "metadata";
  if (f === "completed") return t.finished;
  if (f === "active") return t.down > 0 || t.up > 0;
  return t.state === f;
}
function visible(t) {
  if (!matches(t, S.filter)) return false;
  if (S.labelFilter !== null && t.label !== S.labelFilter) return false;
  if (S.trackerFilter && t.tracker !== S.trackerFilter) return false;
  if (S.search && !t.name.toLowerCase().includes(S.search)) return false;
  return true;
}
function cmp(a, b) {
  const k = SORT_KEYS[S.sort] || SORT_KEYS.queue;
  const ka = k(a), kb = k(b);
  let r = Array.isArray(ka) ? (ka[0] - kb[0]) || (ka[1] - kb[1]) : (ka > kb) - (ka < kb);
  if (!r) return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
  return S.sortDesc ? -r : r;
}
function render() {
  if (!S.list) return;
  const shown = S.torrents.filter(visible).sort(cmp);
  // reorder only when needed: appendChild moves nodes, so walk in order
  let i = 0;
  for (const t of shown) {
    const el = S.cards.get(t.id).el;
    if (S.list.children[i] !== el) S.list.insertBefore(el, S.list.children[i] || null);
    i++;
  }
  while (S.list.children.length > shown.length) S.list.lastChild.remove();
  for (const [k, b] of Object.entries(S.chips)) {
    b.classList.toggle("active", k === S.filter);
    setText(b.count, S.counts[k] || 0);
  }
  const labels = Object.keys(S.labels).filter(Boolean).sort();
  syncOptions(S.labelSel, [["__all", "All labels"], ["__none", "No label"], ...labels.map((l) => [l, l])],
    S.labelFilter === null ? "__all" : (S.labelFilter === "" ? "__none" : S.labelFilter));
  const trackers = Object.keys(S.trackers).sort();
  syncOptions(S.trackerSel, [["__all", "All trackers"], ...trackers.map((t) => [t, t])], S.trackerFilter || "__all");
  const total = S.counts.all || 0;
  const filtered = S.filter !== "all" || S.labelFilter !== null || S.trackerFilter || S.search;
  let sub;
  if (filtered) {
    const names = [];
    if (S.filter !== "all") names.push(Object.fromEntries(FILTERS)[S.filter]);
    if (S.labelFilter !== null) names.push(S.labelFilter || "No label");
    if (S.trackerFilter) names.push(S.trackerFilter);
    if (S.search) names.push(`“${S.search}”`);
    sub = `${names.join(", ")} · ${shown.length} of ${total}`;
  } else {
    sub = `${total} torrent${total === 1 ? "" : "s"}` + (S.counts.downloading ? ` · ${S.counts.downloading} downloading` : "");
  }
  setText(S.titleS, sub);
  S.filterBtn.classList.toggle("filter-active", !!filtered);
  const page = !S.torrents.length ? "empty" : (!shown.length ? "nomatch" : "list");
  for (const [k, el] of Object.entries(S.pages)) el.classList.toggle("hidden", k !== page);
  document.title = S.session.down || S.session.up ? `↓ ${fmtSpeed(S.session.down)} ↑ ${fmtSpeed(S.session.up)} · Cascade` : "Cascade";
}
function syncOptions(sel, options, value) {
  const key = options.map((o) => o[0]).join("\u0001");
  if (sel.dataset.key !== key) {
    sel.replaceChildren(...options.map(([k, l]) => h("option", { value: k, text: l })));
    sel.dataset.key = key;
  }
  if (options.some((o) => o[0] === value)) sel.value = value;
}
function updateStatus(s) {
  setText(S.stSpeed, `↓ ${fmtSpeed(s.down)}   ↑ ${fmtSpeed(s.up)}`);
  setText(S.stFree.lbl, s.free_space >= 0 ? fmtSize(s.free_space) + " free" : "?");
  setText(S.stDht.lbl, s.dht_nodes);
  S.stDht.classList.toggle("hidden", S.settings["net.dht"] === false);
  setText(S.stPort.lbl, s.port);
  S.stPort.title = s.info.port;
  S.stPort.classList.toggle("success", !!s.incoming);
  const iface = s.interface || {};
  S.stIface.classList.toggle("hidden", !iface.name);
  if (iface.name) {
    setText(S.stIface.lbl, iface.name);
    S.stIface.title = s.info.interface;
    S.stIface.classList.toggle("success", !!iface.usable);
    S.stIface.classList.toggle("error", !iface.usable);
  }
  S.stSlow.classList.toggle("hidden", !s.alt_speed);
  S.slowBtn.setAttribute("aria-pressed", s.alt_speed ? "true" : "false");
  const paused = s.paused || [];
  const span = S.banner.firstChild, btn = S.banner.lastChild;
  if (paused.includes("killswitch")) {
    setText(span, `Paused: ${iface.name || "the interface"} is down. Nothing is sent over your normal connection`);
    setText(btn, "Network Settings"); S.bannerPage = "connection"; S.banner.classList.remove("hidden");
  } else if (paused.includes("schedule")) {
    const nxt = (s.schedule || {}).next_change;
    setText(span, "Paused by the schedule" + (nxt ? ` for ${fmtDuration(nxt, true)}` : ""));
    setText(btn, "Edit Schedule"); S.bannerPage = "speed"; S.banner.classList.remove("hidden");
  } else S.banner.classList.add("hidden");
}

// ------------------------------------------------------------------ selection mode
function setSelection(on) {
  S.selection = on;
  S.selectBtn.setAttribute("aria-pressed", on ? "true" : "false");
  S.actionBar.classList.toggle("hidden", !on);
  if (!on) { S.selected.clear(); S.anchor = null; }
  for (const c of S.cards.values()) { c.check.classList.toggle("hidden", !on); c.check.checked = S.selected.has(c.id); }
  updateSelLabel();
}
// Ctrl+click toggles one card, Shift+click selects the range from the last clicked card
function selectClick(card, how) {
  if (!S.selection) setSelection(true);
  const order = [...S.list.children];
  const a = S.anchor ? order.indexOf(S.anchor.el) : -1, b = order.indexOf(card.el);
  if (how === "range" && a >= 0 && b >= 0) {
    for (const el of order.slice(Math.min(a, b), Math.max(a, b) + 1)) {
      const c = [...S.cards.values()].find((x) => x.el === el);
      if (c) { S.selected.add(c.id); c.check.checked = true; }
    }
  } else {
    if (S.selected.has(card.id)) S.selected.delete(card.id); else S.selected.add(card.id);
    card.check.checked = S.selected.has(card.id);
    S.anchor = card;
  }
  updateSelLabel();
}
function updateSelLabel() { setText(S.selLabel, S.selected.size ? `${S.selected.size} selected` : "No torrents selected"); }
function selectAll() {
  const vis = S.torrents.filter(visible).map((t) => t.id);
  const all = vis.every((id) => S.selected.has(id));
  for (const id of vis) { if (all) S.selected.delete(id); else S.selected.add(id); }
  for (const c of S.cards.values()) c.check.checked = S.selected.has(c.id);
  updateSelLabel();
}
function bulk(method, extra = {}) {
  if (!S.selected.size) { toast("Select some torrents first"); return; }
  call(method, Object.assign({ ids: [...S.selected] }, extra));
}

// ------------------------------------------------------------------ a torrent card
function subtitleFor(t) {
  if (t.error) return t.error;
  const n = t.peers + t.seeds;
  if (t.state === "metadata") return `Fetching metadata from ${n} peer${n === 1 ? "" : "s"}`;
  if (t.state === "checking") return `Checking files · ${(t.progress * 100).toFixed(1)}%`;
  if (t.state === "moving") return "Moving files…";
  const size = t.progress < 1 ? `${fmtSize(t.done)} of ${fmtSize(t.size)}` : fmtSize(t.size);
  const parts = [];
  if (t.state === "downloading") {
    parts.push(`${size} · ${(t.progress * 100).toFixed(1)}%`);
    if (t.eta >= 0) parts.push(fmtDuration(t.eta, true) + " left");
    parts.push(`${n} of ${Math.max(t.seeds_total, 0) + Math.max(t.peers_total, 0)} peers`);
  } else if (t.state === "seeding") {
    parts.push(size, "ratio " + fmtRatio(t.ratio), t.peers ? `uploading to ${t.peers}` : "seeding");
  } else {
    parts.push(size + (t.progress < 1 ? ` · ${(t.progress * 100).toFixed(1)}%` : ""), t.state === "queued" ? "Queued" : "Paused");
  }
  return parts.join(" · ");
}
function speedsFor(t) {
  const out = [];
  if (t.down > 0) out.push("↓ " + fmtSpeed(t.down));
  if (t.up > 0) out.push("↑ " + fmtSpeed(t.up));
  return out.join("   ");
}
const isPaused = (t) => t.state === "paused" || t.state === "queued" || t.state === "error" || (t.paused && !t.auto_managed);

class Card {
  constructor(t) {
    this.id = t.id;
    this.t = t;
    this.expanded = false;
    this.details = null;
    this.check = h("input", { type: "checkbox", class: "check hidden", "aria-label": "Select",
      onchange: () => { if (this.check.checked) S.selected.add(this.id); else S.selected.delete(this.id); updateSelLabel(); } });
    if (S.selection) this.check.classList.remove("hidden");
    this.iconBox = h("div", { class: "state-icon" });
    this.name = h("div", { class: "name" });
    this.chip = h("span", { class: "label-chip hidden" });
    this.bar = h("div");
    this.sub = h("div", { class: "sub" });
    this.speed = h("div", { class: "speed" });
    this.header = h("button", { class: "header", "aria-expanded": "false",
      onmousedown: (e) => { if (e.shiftKey) e.preventDefault(); },   // no text selection on Shift+click
      onclick: (e) => {
        const ctrl = e.ctrlKey || e.metaKey;
        if (ctrl || e.shiftKey || S.selection) { selectClick(this, e.shiftKey ? "range" : "toggle"); return; }
        this.setExpanded(!this.expanded); } },
    this.iconBox, h("div", { class: "mid" }, h("div", { class: "trow" }, this.name, this.chip), h("div", { class: "progress" }, this.bar),
      h("div", { class: "srow" }, this.sub, this.speed)), icon("down", "arrow"));
    this.toggleBtn = h("button", { class: "btn flat circular icon-only toggle", onclick: () => {
      call(isPaused(this.t) ? "torrents.resume" : "torrents.pause", { ids: [this.id] }); } });
    this.revealInner = h("div");
    this.el = h("li", { class: "card" }, h("div", { class: "top" }, this.check, this.header, this.toggleBtn), h("div", { class: "reveal" }, this.revealInner));
    this.update(t, true);
  }
  update(t, force) {
    const old = this.t;
    this.t = t;
    if (force || old.name !== t.name) { setText(this.name, t.name); this.name.title = t.name; }
    if (force || old.state !== t.state) {
      this.el.className = "card state-" + t.state + (this.expanded ? " expanded" : "");
      this.iconBox.replaceChildren(icon(STATE_ICONS[t.state] || "download"));
      this.iconBox.title = STATE_LABELS[t.state] || t.state;
    }
    const paused = isPaused(t);
    if (force || this._paused !== paused) {
      this._paused = paused;
      this.toggleBtn.replaceChildren(icon(paused ? "play" : "pause"));
      this.toggleBtn.title = paused ? "Resume" : "Pause";
    }
    this.bar.style.width = (t.progress * 100).toFixed(2) + "%";
    setText(this.sub, subtitleFor(t));
    setText(this.speed, speedsFor(t));
    setText(this.chip, t.label || "");
    this.chip.classList.toggle("hidden", !t.label);
    if (this.details) this.details.updateSummary(t);
  }
  setExpanded(on) {
    this.expanded = on;
    if (on && !this.details) { this.details = new Details(this); this.revealInner.append(this.details.el); this.details.updateSummary(this.t); }
    this.el.classList.toggle("expanded", on);
    this.header.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) this.details.refresh();
  }
}

// ------------------------------------------------------------------ the expanded part of a card
const PRIO_LABELS = [["0", "Skip"], ["1", "Low"], ["4", "Normal"], ["7", "High"]];
const prioKey = (p) => (p === 0 ? "0" : p < 4 ? "1" : p < 7 ? "4" : "7");
const TRACKER_STATUS = { working: "Working", updating: "Announcing…", error: "Error", waiting: "Not contacted yet" };

class Details {
  constructor(card) {
    this.card = card;
    this.id = card.id;
    this.loading = false;
    this.optionsLoaded = false;
    this.fileRows = new Map();
    this.peerRows = new Map();
    this.trackerRows = new Map();
    const t = card.t;
    this.pauseBtn = h("button", { class: "btn flat", onclick: () => card.toggleBtn.click() });
    const actions = h("div", { class: "details-actions" }, this.pauseBtn,
      h("button", { class: "btn flat", onclick: () => copy(this.card.t.save_path, "Folder path copied") }, icon("folder"), "Copy Path"),
      h("button", { class: "btn flat", onclick: () => api("torrents.magnet", { id: this.id }).then((m) => copy(m, "Magnet link copied")).catch((e) => toast(e.message)) }, icon("link"), "Copy Magnet"),
      h("span", { class: "spacer" }),
      iconBtn("more", "More actions", (e) => menu(e.currentTarget, [
        { label: "Force Start", cb: () => call("torrents.force_start", { ids: [this.id] }) },
        { label: "Verify Data", cb: () => call("torrents.recheck", { ids: [this.id] }) },
        { label: "Ask Trackers for Peers", cb: () => call("torrents.reannounce", { ids: [this.id] }, null, "Announced") }, "-",
        { label: "Move to Top of Queue", cb: () => call("torrents.queue", { ids: [this.id], where: "top" }) },
        { label: "Move Up in Queue", cb: () => call("torrents.queue", { ids: [this.id], where: "up" }) },
        { label: "Move Down in Queue", cb: () => call("torrents.queue", { ids: [this.id], where: "down" }) },
        { label: "Move to Bottom of Queue", cb: () => call("torrents.queue", { ids: [this.id], where: "bottom" }) }, "-",
        { label: "Move Data To…", cb: () => folderPicker("Move Data To", this.card.t.save_path, (p) => call("torrents.move", { ids: [this.id], path: p }, null, "Moving…")) },
        { label: "Save .torrent File…", cb: () => exportTorrent(this.id) },
        { label: "Copy Info Hash", cb: () => copy(this.id, "Info hash copied") }], "end")),
      iconBtn("trash", "Remove…", () => confirmRemove([this.id]), "destructive-text"));
    this.tiles = {};
    const tiles = h("div", { class: "tiles" });
    for (const [k, cap] of [["downloaded", "Downloaded"], ["uploaded", "Uploaded"], ["ratio", "Ratio"], ["down", "Download speed"],
      ["up", "Upload speed"], ["eta", "Time left"], ["seeds", "Seeds"], ["peers", "Peers"], ["availability", "Availability"],
      ["active", "Active time"], ["seeding", "Seeding time"], ["size", "Size"]]) {
      const v = h("div", { class: "val", text: "-" });
      tiles.append(h("div", { class: "tile" }, h("div", { class: "cap", text: cap }), v));
      this.tiles[k] = v;
    }
    this.exp = {};
    const list = h("div", { class: "boxed-list" });
    for (const [k, title, ic] of [["info", "Information", "info"], ["files", "Files", "files"], ["peers", "Peers", "users"],
      ["trackers", "Trackers", "server"], ["pieces", "Pieces", "grid"], ["options", "Options", "cog"]]) {
      const e = expander(title, ic, () => this.refresh());
      this.exp[k] = e;
      list.append(e);
    }
    this.buildInfo(); this.buildPieces(); this.buildPeers(); this.buildTrackers(); this.buildOptions();
    this.el = h("div", { class: "details" }, actions, tiles, list);
  }
  buildInfo() {
    this.info = {};
    for (const [k, title, cp] of [["hash_v1", "Info hash v1", 1], ["hash_v2", "Info hash v2", 1], ["total_size", "Total size"],
      ["pieces", "Pieces"], ["num_files", "Files"], ["save_path", "Saved in", 1], ["added", "Added"], ["completed", "Completed"],
      ["created", "Created"], ["comment", "Comment"], ["private", "Private"], ["wasted", "Wasted"],
      ["current_tracker", "Current tracker"], ["next_announce", "Next announce"]]) {
      const v = h("div", { class: "value" });
      const r = row(title, "", { suffix: [v, cp ? iconBtn("copy", "Copy", () => copy(v.textContent, "Copied")) : null] });
      this.exp.info.content.append(r);
      this.info[k] = [r, v];
    }
  }
  buildPieces() {
    this.canvas = h("canvas", { class: "pieces" });
    const legend = h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--accent-bg)" }), "Downloaded"),
      h("span", {}, h("i", { style: "background:var(--success-bg)" }), "Downloading"), h("span", {}, h("i", { style: "background:var(--fg-a15)" }), "Missing"));
    this.exp.pieces.content.append(h("div", { style: "padding:12px;display:flex;flex-direction:column;gap:8px" }, this.canvas, legend));
  }
  buildPeers() {
    this.peersEmpty = row("No peers connected", "");
    this.peerAdd = entryRow("Connect to a peer (address:port)", "", (v) => { if (v.trim()) call("peers.add", { id: this.id, address: v.trim() }, () => { this.peerAdd.input.value = ""; }, `Connecting to ${v.trim()}`); });
    this.exp.peers.content.append(this.peersEmpty, this.peerAdd);
  }
  buildTrackers() {
    this.sourcesRow = row("DHT · PeX · LSD", "");
    this.trackerAdd = entryRow("Add a tracker URL", "", (v) => { if (v.trim()) call("trackers.add", { id: this.id, urls: [v.trim()] }, () => { this.trackerAdd.input.value = ""; this.refresh(); }); });
    this.exp.trackers.content.append(this.sourcesRow, this.trackerAdd);
  }
  buildOptions() {
    const c = this.exp.options.content;
    this.opt = {};
    const set = (k, v) => { if (!this.building) call("torrents.set_options", { ids: [this.id], options: { [k]: v } }); };
    for (const [k, title, sub, max] of [["download_limit", "Download limit", "KiB/s · 0 = no limit", 1e7], ["upload_limit", "Upload limit", "KiB/s · 0 = no limit", 1e7],
      ["max_connections", "Maximum connections", "0 = the global setting", 65535], ["max_uploads", "Upload slots", "0 = the global setting", 10000]]) {
      const sp = spinEl(0, { min: 0, max }, (v) => set(k, v));
      this.opt[k] = sp;
      c.append(row(title, sub, { suffix: sp }));
    }
    for (const [k, title, sub] of [["sequential", "Download pieces in order"], ["first_last", "First and last pieces first"],
      ["super_seeding", "Super seeding", "Helps spread a torrent you are the first seed of"], ["auto_managed", "Managed by the queue"]]) {
      const r = switchRow(title, sub, false, (v) => set(k, v));
      this.opt[k] = r;
      c.append(r);
    }
    this.ratioSpin = spinEl(0, { min: 0, max: 1000, step: 0.05, digits: 2 }, (v) => set("ratio_limit", v));
    this.ratioRow = row("Share ratio", "", { suffix: this.ratioSpin });
    this.ratioMode = selectEl([["0", "Use the global setting"], ["1", "Never stop"], ["2", "Stop at a ratio"]], "0", (v) => {
      this.ratioRow.classList.toggle("hidden", v !== "2");
      set("ratio_limit", v === "0" ? null : (v === "1" ? -1 : 2));
    });
    c.append(row("Seeding limit", "", { suffix: this.ratioMode }), this.ratioRow);
    this.labelEntry = entryRow("Label", "", (v) => set("label", v.trim()));
    const dl = h("datalist", { id: "labels-" + this.id });
    this.labelEntry.input.setAttribute("list", dl.id);
    this.labelList = dl;
    c.append(this.labelEntry, dl);
  }
  updateSummary(t) {
    const T = this.tiles;
    setText(T.downloaded, fmtSize(t.downloaded)); setText(T.uploaded, fmtSize(t.uploaded)); setText(T.ratio, fmtRatio(t.ratio));
    setText(T.down, fmtSpeed(t.down)); setText(T.up, fmtSpeed(t.up));
    setText(T.eta, t.eta >= 0 ? fmtDuration(t.eta, true) : (t.finished ? "Done" : "∞"));
    setText(T.seeds, `${t.seeds} (${Math.max(0, t.seeds_total)})`); setText(T.peers, `${t.peers} (${Math.max(0, t.peers_total)})`);
    setText(T.size, t.size ? fmtSize(t.size) : "Unknown");
    const p = isPaused(t);
    if (this._p !== p) { this._p = p; this.pauseBtn.replaceChildren(icon(p ? "play" : "pause"), p ? "Resume" : "Pause"); }
  }
  async refresh() {
    if (this.loading) return;
    const sections = ["info", "peers"];
    for (const k of ["files", "trackers", "pieces"]) if (this.exp[k].isOpen()) sections.push(k);
    if (!this.trackerRows.size && !sections.includes("trackers")) sections.push("trackers");
    if (!this.optionsLoaded || (this.exp.options.isOpen() && !this.exp.options.contains(document.activeElement))) sections.push("options");
    this.loading = true;
    try {
      const d = await api("torrents.details", { id: this.id, sections });
      if (d.info) this.applyInfo(d.info);
      if (d.files) this.applyFiles(d.files);
      if (d.peers) this.applyPeers(d.peers);
      if (d.trackers) this.applyTrackers(d.trackers, d.sources || {});
      if (d.pieces) this.applyPieces(d.pieces);
      if (d.options) this.applyOptions(d.options);
    } catch (e) { /* the torrent may just have been removed */ } finally { this.loading = false; }
  }
  applyInfo(i) {
    const T = this.tiles;
    setText(T.availability, i.availability >= 0 ? i.availability.toFixed(2) : "-");
    setText(T.active, fmtDuration(i.active_time, true));
    setText(T.seeding, i.seeding_time ? fmtDuration(i.seeding_time, true) : "-");
    const vals = {
      hash_v1: i.hash_v1 || "-", hash_v2: i.hash_v2 || "-", total_size: i.total_size >= 0 ? fmtSize(i.total_size) : "Unknown",
      pieces: i.pieces ? `${i.pieces} × ${fmtSize(i.piece_size)}` : "-", num_files: i.num_files ? String(i.num_files) : "-",
      save_path: i.save_path + (i.move_to ? "  →  " + i.move_to : ""), added: fmtTime(i.added), completed: fmtTime(i.completed),
      created: fmtTime(i.created) + (i.creator ? " by " + i.creator : ""), comment: i.comment || "-", private: i.private ? "Yes" : "No",
      wasted: fmtSize(i.wasted), current_tracker: i.current_tracker || "-",
      next_announce: i.next_announce >= 0 ? fmtDuration(i.next_announce, true) : "-",
    };
    for (const [k, [r, v]] of Object.entries(this.info)) {
      setText(v, vals[k]);
      r.classList.toggle("hidden", (k === "hash_v2" && !i.hash_v2) || (k === "hash_v1" && !i.hash_v1));
    }
    const hsh = i.hash_v1 || i.hash_v2;
    this.exp.info.head.setSub(`${hsh.slice(0, 12)}… · ${vals.total_size}${i.private ? " · private" : ""}`);
    this.exp.files.head.setSub(i.num_files ? `${i.num_files} file${i.num_files === 1 ? "" : "s"}` : "Waiting for metadata");
  }
  applyFiles(files) {
    const c = this.exp.files.content;
    for (const f of files.slice(0, 1000)) {
      let r = this.fileRows.get(f.index);
      if (!r) {
        const parts = f.path.split("/");
        const chk = h("input", { type: "checkbox", class: "check", "aria-label": "Download this file" });
        const sel = selectEl(PRIO_LABELS, "4", (v) => { chk.checked = v !== "0"; this.setPrio(f.index, +v); }, true);
        chk.addEventListener("change", () => { sel.value = chk.checked ? "4" : "0"; this.setPrio(f.index, chk.checked ? 4 : 0); });
        const lvl = h("div", { class: "level" }, h("div"));
        r = row(parts[parts.length - 1], "", { prefix: chk, suffix: [lvl, sel] });
        r.folder = parts.slice(0, -1).join("/");
        r.chk = chk; r.sel = sel; r.lvl = lvl.firstChild;
        c.append(r);
        this.fileRows.set(f.index, r);
      }
      const frac = f.size ? f.done / f.size : 1;
      r.setSub(`${r.folder ? r.folder + " · " : ""}${fmtSize(f.done)} of ${fmtSize(f.size)} · ${Math.round(frac * 100)}%`);
      r.lvl.style.width = (frac * 100).toFixed(1) + "%";
      if (r.prio !== f.priority) { r.prio = f.priority; r.chk.checked = f.priority > 0; r.sel.value = prioKey(f.priority); }
    }
    if (files.length > 1000 && !this.moreFiles) { this.moreFiles = row(`${files.length - 1000} more files`, "Use “cascade files” in a terminal to see them all"); c.append(this.moreFiles); }
  }
  setPrio(index, prio) { const r = this.fileRows.get(index); if (r) r.prio = prio; call("torrents.set_files", { id: this.id, priorities: { [index]: prio } }); }
  applyPeers(peers) {
    this.exp.peers.head.setSub(peers.length ? `${peers.length} connected` : "None connected");
    if (!this.exp.peers.isOpen()) return;
    const seen = new Set();
    for (const p of peers.slice(0, 300)) {
      const key = `${p.ip}:${p.port}`;
      seen.add(key);
      let r = this.peerRows.get(key);
      if (!r) {
        const sp = h("div", { class: "value caption", style: "white-space:pre;text-align:right" });
        r = row(p.ip.includes(":") ? `[${p.ip}]:${p.port}` : key, "", { suffix: sp });
        r.sp = sp;
        this.exp.peers.content.insertBefore(r, this.peersEmpty);
        this.peerRows.set(key, r);
      }
      r.setSub(`${p.client} · ${p.flags || "-"} · ${Math.round(p.progress * 100)}%${p.utp ? " · µTP" : ""}`);
      setText(r.sp, `↓ ${fmtSpeed(p.down)}\n↑ ${fmtSpeed(p.up)}`);
    }
    for (const [k, r] of this.peerRows) if (!seen.has(k)) { r.remove(); this.peerRows.delete(k); }
    this.peersEmpty.classList.toggle("hidden", peers.length > 0);
  }
  applyTrackers(trackers, sources) {
    const working = trackers.filter((t) => t.status === "working").length;
    this.exp.trackers.head.setSub(trackers.length ? `${trackers.length} tracker${trackers.length === 1 ? "" : "s"} · ${working} working` : "No trackers (DHT and PeX only)");
    const seen = new Set();
    for (const t of trackers) {
      seen.add(t.url);
      let r = this.trackerRows.get(t.url);
      if (!r) {
        r = row(t.url, "", { suffix: [
          iconBtn("refresh", "Reannounce to this tracker now", () => call("trackers.reannounce", { id: this.id, url: t.url }, () => setTimeout(() => this.refresh(), 1500), "Announcing to " + (t.url.split("/")[2] || t.url))),
          iconBtn("minus", "Remove", () => call("trackers.remove", { id: this.id, urls: [t.url] }, () => this.refresh()))] });
        this.exp.trackers.content.insertBefore(r, this.sourcesRow);
        this.trackerRows.set(t.url, r);
      }
      const bits = [TRACKER_STATUS[t.status] || t.status];
      if (t.seeds >= 0) bits.push(`${t.seeds} seeds · ${Math.max(0, t.peers)} peers`);
      if (t.downloaded >= 0) bits.push(`${t.downloaded} downloads`);
      if (t.next_announce >= 0 && t.status !== "updating") bits.push("next in " + fmtDuration(t.next_announce, true));
      if (t.message) bits.push(t.message);
      r.setSub(bits.join(" · "));
      r.classList.toggle("error", t.status === "error");
    }
    for (const [u, r] of this.trackerRows) if (!seen.has(u)) { r.remove(); this.trackerRows.delete(u); }
    const on = ["dht", "pex", "lsd"].filter((k) => sources[k]).map((k) => (k === "pex" ? "PeX" : k.toUpperCase()));
    this.sourcesRow.setSub(on.length ? "Also finding peers through " + on.join(", ") : "DHT, PeX and LSD are off for this torrent");
  }
  applyPieces(p) {
    this.exp.pieces.head.setSub(`${p.have} of ${p.count} · ${fmtSize(p.piece_size)} each`);
    const cv = this.canvas, dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth || 600, hh = 26;
    cv.width = Math.round(w * dpr); cv.height = Math.round(hh * dpr);
    const ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, hh);
    if (!p.count) return;
    const bits = atob(p.bitfield || "");
    const have = (i) => { const b = i >> 3; return b < bits.length && (bits.charCodeAt(b) & (0x80 >> (i & 7))); };
    const dl = new Set(p.downloading || []);
    const cs = getComputedStyle(document.documentElement);
    const accent = cs.getPropertyValue("--accent-bg").trim(), green = cs.getPropertyValue("--success-bg").trim();
    const per = p.count / w;
    for (let x = 0; x < w; x++) {
      const a = Math.floor(x * per), b = Math.max(a + 1, Math.floor((x + 1) * per));
      let n = 0, active = false;
      for (let i = a; i < Math.min(b, p.count); i++) { if (have(i)) n++; else if (dl.has(i)) active = true; }
      if (n) { ctx.globalAlpha = 0.25 + 0.75 * n / (b - a); ctx.fillStyle = accent; ctx.fillRect(x, 0, 1, hh); }
      else if (active) { ctx.globalAlpha = 0.8; ctx.fillStyle = green; ctx.fillRect(x, 0, 1, hh); }
    }
  }
  applyOptions(o) {
    this.building = true;
    for (const k of ["download_limit", "upload_limit", "max_connections", "max_uploads"]) this.opt[k].set(o[k] || 0);
    for (const k of ["sequential", "first_last", "super_seeding", "auto_managed"]) this.opt[k].input.checked = !!o[k];
    const rl = o.ratio_limit;
    this.ratioMode.value = rl === null || rl === undefined ? "0" : (rl < 0 ? "1" : "2");
    this.ratioRow.classList.toggle("hidden", !(rl !== null && rl !== undefined && rl >= 0));
    if (rl !== null && rl >= 0) this.ratioSpin.set(rl);
    if (document.activeElement !== this.labelEntry.input) this.labelEntry.input.value = o.label || "";
    this.labelList.replaceChildren(...Object.keys(S.labels).filter(Boolean).map((l) => h("option", { value: l })));
    const bits = [];
    if (o.download_limit || o.upload_limit) bits.push("speed limited");
    if (o.sequential) bits.push("in order");
    if (o.super_seeding) bits.push("super seeding");
    if (!o.auto_managed) bits.push("not queued");
    const s = bits.join(", ");
    this.exp.options.head.setSub(s ? s[0].toUpperCase() + s.slice(1) : "Default settings");
    this.building = false;
    this.optionsLoaded = true;
  }
}

function copy(text, msg) {
  const done = () => toast(msg || "Copied");
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = h("textarea", { style: "position:fixed;opacity:0" }, text);
  document.body.append(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("Cannot copy"); }
  ta.remove();
}
function exportTorrent(id) {
  api("torrents.export", { id }).then((r) => {
    const bin = atob(r.data), arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    const a = h("a", { href: URL.createObjectURL(new Blob([arr], { type: "application/x-bittorrent" })), download: r.name + ".torrent" });
    document.body.append(a); a.click(); a.remove();
  }).catch((e) => toast(e.message));
}
function confirmRemove(ids) {
  const names = ids.map((i) => (S.cards.get(i) || {}).t).filter(Boolean).map((t) => t.name);
  if (!names.length) return;
  const go = (data) => call("torrents.remove", { ids, delete_data: data }, () => { if (S.selection) setSelection(false); },
    `Removed ${names.length === 1 ? names[0] : names.length + " torrents"}`);
  if (S.settings["app.confirm_remove"] === false) { go(false); return; }
  alertDialog({ heading: `Remove ${names.length === 1 ? "“" + names[0] + "”" : names.length + " Torrents"}?`,
    body: "The downloaded files are kept unless you delete them too.",
    responses: [["cancel", "Cancel"], ["data", "Delete Files Too", "destructive"], ["remove", "Remove", "suggested"]],
    onresponse: (r) => { if (r === "remove" || r === "data") go(r === "data"); } });
}

// ------------------------------------------------------------------ adding torrents
function readFileB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1] || "");
    r.onerror = () => rej(r.error);
    r.readAsDataURL(file);
  });
}
async function addFiles(files) {
  for (const f of files) {
    if (!/\.torrent$/i.test(f.name)) { toast(`${f.name} is not a .torrent file`); continue; }
    addSource({ data: await readFileB64(f) });
  }
}
function addText(text) {
  const items = (text || "").split(/\s+/).filter((w) => isMagnet(w) || isUrl(w));
  if (!items.length) { toast("No magnet links or URLs found"); return; }
  if (items.length === 1) { addSource(isMagnet(items[0]) ? { magnet: items[0] } : { url: items[0] }); return; }
  // several at once: add them straight away with the default options
  Promise.allSettled(items.map((it) => api("torrents.add", Object.assign(isMagnet(it) ? { magnet: it } : { url: it }, { options: {} }))))
    .then((res) => { toast(`Added ${res.filter((r) => r.status === "fulfilled").length} of ${items.length} torrents`); syncSoon(); });
}
async function addSource(src) {
  if (S.settings["add.show_dialog"] === false) { call("torrents.add", Object.assign({}, src, { options: {} }), added); return; }
  try { addDialog(src, await api("torrents.inspect", src)); } catch (e) { toast("Cannot add: " + e.message); }
}
function added(r) { toast(r.duplicate ? `“${r.name}” is already in the list (trackers merged)` : `Added “${r.name}”`); }
function magnetDialog(text = "") {
  const ta = h("textarea", { class: "text", placeholder: "magnet:?xt=urn:btih:…\nhttps://…/file.torrent", style: "min-height:120px" }, text);
  alertDialog({ heading: "Add Magnet Links", body: "Paste one or more magnet links or web addresses of .torrent files, one per line",
    extra: ta, responses: [["cancel", "Cancel"], ["ok", "Add", "suggested"]], onresponse: (r) => { if (r === "ok") addText(ta.value); } });
  ta.focus();
}
async function pasteClipboard() {
  try {
    const t = await navigator.clipboard.readText();
    const found = t.split(/\s+/).filter((w) => isMagnet(w) || isUrl(w));
    if (found.length) magnetDialog(found.join("\n")); else magnetDialog();
  } catch (e) { magnetDialog(); }
}
function addDialog(src, info) {
  if (info.data && src.url) src = { data: info.data };
  const s = S.settings;
  let savePath = s["downloads.save_path"];
  const pathRow = row("Save to", savePath, { property: true });
  const free = h("span", { class: "dim caption" });
  const showFree = (p) => api("fs.free_space", { path: p }).then((n) => setText(free, n >= 0 ? fmtSize(n) + " free" : "")).catch(() => {});
  showFree(savePath);
  $(".suffix", pathRow) || pathRow.append(h("div", { class: "suffix" }));
  pathRow.lastChild.append(free, iconBtn("folder", "Choose a folder", () => folderPicker("Save Torrent To", savePath, (p) => { savePath = p; pathRow.setSub(p); showFree(p); })));
  const label = entryRow("Label", "", () => {});
  label.lastChild.classList.add("hidden");
  const dl = h("datalist", { id: "add-labels" }, ...Object.keys(S.labels).filter(Boolean).map((l) => h("option", { value: l })));
  label.input.setAttribute("list", "add-labels");
  const sw = {};
  const rows = [["paused", "Add paused", s["add.start_paused"]], ["sequential", "Download pieces in order", s["add.sequential"]],
    ["first_last", "First and last pieces first", s["add.first_last"]], ["top_of_queue", "Put it at the top of the queue", s["add.top_of_queue"]]];
  if (info.has_metadata) rows.push(["skip_check", "The files are already complete", false, "Skip checking: only for data you know is complete"]);
  else rows.push(["pause_after_metadata", "Choose files when the metadata arrives", s["add.magnet_choose_files"]]);
  const optRows = rows.map(([k, title, v, sub]) => { const r = switchRow(title, sub, !!v, () => {}); sw[k] = r.input; return r; });
  const head = row(info.name, info.has_metadata ? `${fmtSize(info.size)} · ${info.files.length} file${info.files.length === 1 ? "" : "s"}${info.private ? " · private" : ""}`
    : "Magnet link · the file list arrives with the metadata", { prefix: icon(info.has_metadata ? "app" : "link") });
  const body = h("div", { class: "dbody" }, group("", "", head, info.duplicate ? row("Already in your list", "Adding it again only merges its trackers into the existing torrent", { prefix: icon("error") }) : null,
    info.comment ? row("Comment", info.comment, { property: true }) : null), group("Options", "", pathRow, label, dl, ...optRows));
  const checks = [];
  let filesDesc = null;
  const addBtn = h("button", { class: "btn suggested", text: "Add" });
  if (info.has_metadata && info.files.length) {
    const total = info.files.reduce((a, f) => a + f.size, 0);
    const upd = () => {
      const chosen = info.files.filter((f, i) => i >= checks.length || checks[i].checked).reduce((a, f) => a + f.size, 0);
      setText(filesDesc, `${fmtSize(chosen)} of ${fmtSize(total)} selected`);
      addBtn.disabled = !checks.some((c) => c.checked);
    };
    const fileRows = info.files.slice(0, 2000).map((f) => {
      const parts = f.path.split("/");
      const c = h("input", { type: "checkbox", class: "check", checked: true, onchange: upd });
      checks.push(c);
      const r = row(parts[parts.length - 1], (parts.length > 1 ? parts.slice(0, -1).join("/") + " · " : "") + fmtSize(f.size), { prefix: c, activatable: true });
      r.addEventListener("click", (e) => { if (e.target !== c) { c.checked = !c.checked; upd(); } });
      return r;
    });
    const g = group("Files", " ", ...fileRows);
    filesDesc = $(".group-desc", g);
    $(".group-head", g).append(h("button", { class: "btn flat", text: "All", onclick: () => { checks.forEach((c) => (c.checked = true)); upd(); } }),
      h("button", { class: "btn flat", text: "None", onclick: () => { checks.forEach((c) => (c.checked = false)); upd(); } }));
    body.append(g);
    upd();
  }
  const d = dialog({ title: "Add Torrent", width: 560, height: 720, body, start: h("button", { class: "btn", text: "Cancel", onclick: () => d.close() }), end: addBtn });
  addBtn.addEventListener("click", () => {
    const opts = {};
    for (const [k, inp] of Object.entries(sw)) opts[k] = inp.checked;
    if (label.input.value.trim()) opts.label = label.input.value.trim();
    if (savePath !== s["downloads.save_path"]) opts.save_path = savePath;
    if (checks.length) {
      const n = Math.max(...info.files.map((f) => f.index)) + 1;
      const pr = new Array(n).fill(4);
      info.files.slice(0, checks.length).forEach((f, i) => { pr[f.index] = checks[i].checked ? 4 : 0; });
      opts.file_priorities = pr;
    }
    addBtn.disabled = true;
    api("torrents.add", Object.assign({}, src, { options: opts })).then((r) => { d.close(); added(r); syncSoon(); })
      .catch((e) => { addBtn.disabled = false; toast("Cannot add: " + e.message); });
  });
}

// ------------------------------------------------------------------ folder picker (folders on the Cascade machine)
function folderPicker(title, start, cb) {
  let cur = null;
  const pathLbl = h("div", { class: "group-desc", style: "overflow-wrap:anywhere" });
  const list = h("div", { class: "boxed-list folder-list" });
  const choose = h("button", { class: "btn suggested", text: "Select" });
  const load = (p) => api("fs.list", { path: p }).then((r) => {
    cur = r.path;
    setText(pathLbl, r.path + (r.free >= 0 ? `  ·  ${fmtSize(r.free)} free` : ""));
    const rows = [];
    if (r.parent) rows.push(row("..", "Parent folder", { prefix: icon("up"), activatable: true, onclick: () => load(r.parent) }));
    for (const dname of r.dirs) rows.push(row(dname, "", { prefix: icon("folder"), activatable: true, onclick: () => load(r.path.replace(/\/$/, "") + "/" + dname) }));
    if (!r.dirs.length) rows.push(row("No folders here", ""));
    list.replaceChildren(...rows);
  }).catch((e) => toast(e.message));
  const newBtn = h("button", { class: "btn flat", text: "New Folder…", onclick: () => askText("New Folder", "Folder name", "Create", (n) => {
    const p = cur.replace(/\/$/, "") + "/" + n;
    // creating happens through a harmless move target check: the daemon creates folders when it uses them
    cur = p; setText(pathLbl, p + "  ·  will be created"); list.replaceChildren(row("New folder", p));
  }) });
  const body = h("div", { class: "dbody" }, h("div", { class: "group-head" }, h("div", { class: "gtext" }, h("div", { class: "group-title", text: "Location on the Cascade computer" }), pathLbl), newBtn), list);
  const d = dialog({ title, width: 520, height: 600, body, start: h("button", { class: "btn", text: "Cancel", onclick: () => d.close() }), end: choose });
  choose.addEventListener("click", () => { d.close(); cb(cur); });
  load(start || "");
}

// ------------------------------------------------------------------ preferences (generated from the same schema as GTK)
function dependsOk(dep, values) {
  if (!dep) return true;
  const v = values[dep.key];
  if ("in" in dep) return dep.in.includes(v);
  if ("not" in dep) return v !== dep.not;
  return !!v;
}
async function prefsDialog(pageId) {
  let values;
  try { values = await api("settings.get"); } catch (e) { toast(e.message); return; }
  const schema = S.schema;
  const deps = [];
  const setters = {};
  const infos = {};
  const set = (key, value) => {
    if (values[key] === value) return;
    values[key] = value;
    applyDeps();
    api("settings.set", { values: { [key]: value } }).then((r) => {
      for (const [k, e] of Object.entries(r.errors || {})) toast(`${(SPEC[k] || {}).title || k}: ${e}`);
      const n = r.notices || {};
      if (n.webui_password) alertDialog({ heading: "Web Interface Password", body: "Sign in with this password from now on:", extra: h("p", { class: "heading", style: "user-select:all;font-size:1.3em", text: n.webui_password }), responses: [["ok", "Done", "suggested"]] });
      loadSettings();
    }).catch((e) => toast(e.message));
  };
  const SPEC = {};
  const tabs = h("div", { class: "prefs-tabs", role: "tablist" });
  const pages = {};
  const body = h("div", { class: "dbody" });
  const searchIn = h("input", { class: "entry", type: "search", placeholder: "Search preferences" });
  const search = h("div", { class: "prefs-search" }, searchIn);
  for (const p of schema.pages) {
    const pg = h("div", { class: "hidden" });
    for (const g of p.groups) {
      const rows = [];
      for (const r of g.rows) {
        const el = makePrefRow(r, values, set, setters, infos, SPEC);
        if (!el) continue;
        el.dataset.search = (r.title + " " + (r.subtitle || "") + " " + g.title + " " + (r.key || "")).toLowerCase();
        if (r.depends) deps.push([el, r.depends]);
        rows.push(el);
      }
      if (rows.length) pg.append(group(g.title, g.description, ...rows));
    }
    pages[p.id] = pg;
    body.append(pg);
    tabs.append(h("button", { role: "tab", "data-page": p.id, onclick: () => show(p.id) }, icon(PAGE_ICONS[p.id] || "cog"), h("span", { text: p.title })));
  }
  function show(id) {
    for (const [k, el] of Object.entries(pages)) el.classList.toggle("hidden", k !== id);
    for (const b of tabs.children) b.classList.toggle("active", b.dataset.page === id);
    body.scrollTop = 0;
  }
  function applyDeps() {
    for (const [el, d] of deps) {
      const ok = dependsOk(d, values);
      if ("in" in d || "not" in d) el.classList.toggle("hidden", !ok); else el.classList.toggle("disabled", !ok);
    }
  }
  searchIn.addEventListener("input", () => {
    const q = searchIn.value.trim().toLowerCase();
    tabs.classList.toggle("hidden", !!q);
    for (const pg of Object.values(pages)) {
      pg.classList.toggle("hidden", !q && pg !== pages[current()]);
      for (const g of pg.children) {
        let any = false;
        for (const r of $(".boxed-list", g).children) { const hit = !q || (r.dataset.search || "").includes(q); r.style.display = hit ? "" : "none"; any = any || hit; }
        g.style.display = any ? "" : "none";
      }
    }
  });
  const current = () => ([...tabs.children].find((b) => b.classList.contains("active")) || tabs.firstChild).dataset.page;
  const wrap = h("div", { style: "display:flex;flex-direction:column;min-height:0;flex:1" }, search, tabs, body);
  const timer = setInterval(async () => {
    try {
      const v = await api("settings.get");
      for (const [k, fn] of Object.entries(setters)) if (JSON.stringify(v[k]) !== JSON.stringify(values[k])) { values[k] = v[k]; fn(v[k]); }
      applyDeps();
      updateInfos(infos);
    } catch (e) { /* ignore */ }
  }, 2000);
  dialog({ title: "Preferences", width: 640, height: 760, body: wrap, onclose: () => clearInterval(timer) });
  show(pageId && pages[pageId] ? pageId : schema.pages[0].id);
  applyDeps();
  updateInfos(infos);
}
const PAGE_ICONS = { general: "cog", downloads: "download", connection: "port", speed: "receive", queue: "queue", remote: "web", advanced: "info" };
function updateInfos(infos) {
  const s = S.session || {};
  const info = Object.assign({}, s.info || {});
  const w = s.webui || {};
  info.webui = w.running ? (w.urls || []).join("  ") : (w.error || "Off");
  info.cli = info.cli || "";
  for (const [k, r] of Object.entries(infos)) if (k in info) r.setSub(info[k]);
}
function makePrefRow(r, values, set, setters, infos, SPEC) {
  if (r.kind === "action") {
    if (r.client && r.client !== "web") return null;
    return row(r.title, r.subtitle, { suffix: h("button", { class: "btn" + (r.style === "destructive" ? " destructive" : ""), text: r.button, onclick: () => runAction(r) }) });
  }
  if (r.kind === "info") {
    const el = row(r.title, "…", { property: true, suffix: r.info === "webui" ? iconBtn("copy", "Copy", () => copy(((S.session.webui || {}).urls || []).slice(-1)[0] || "", "Address copied")) : null });
    infos[r.info] = el;
    return el;
  }
  SPEC[r.key] = r;
  const key = r.key, v = values[key];
  const unitSub = (r.subtitle || "") + (r.unit ? ((r.subtitle ? " · " : "") + r.unit) : "");
  switch (r.type) {
    case "bool": {
      const el = switchRow(r.title, r.subtitle, !!v, (x) => {
        if (key === "webui.enabled" && !x) {
          alertDialog({ heading: "Turn Off the Web Interface?", body: "This page will stop working. Turn it back on from the Cascade app or with “cascade webui on”.",
            responses: [["cancel", "Cancel"], ["off", "Turn Off", "destructive"]], onresponse: (resp) => { if (resp === "off") set(key, false); else el.input.checked = true; } });
          return;
        }
        set(key, x);
      });
      setters[key] = (x) => { el.input.checked = !!x; };
      return el;
    }
    case "int": case "float": {
      const sp = spinEl(v, { min: r.min ?? 0, max: r.max ?? 1e12, step: r.step || 1, digits: r.digits || 0 }, (x) => set(key, x));
      setters[key] = (x) => sp.set(x);
      return row(r.title, unitSub, { suffix: sp });
    }
    case "choice": {
      const sel = selectEl(r.choices, v, (x) => set(key, x));
      setters[key] = (x) => { sel.value = x; };
      return row(r.title, r.subtitle, { suffix: sel });
    }
    case "str": case "password": case "secret": case "file": {
      const isSecret = r.type === "secret";
      const el = entryRow(isSecret ? `${r.title} (${v ? "set" : "not set"})` : r.title, isSecret ? "" : (v || ""), (x) => set(key, x),
        { password: r.type !== "str" && r.type !== "file", placeholder: r.placeholder ? `${r.title}: ${r.placeholder}` : r.title });
      if (r.subtitle) el.title = r.subtitle;
      setters[key] = (x) => { if (document.activeElement !== el.input) { if (isSecret) el.input.placeholder = `${r.title} (${x ? "set" : "not set"})`; else el.input.value = x || ""; } };
      return el;
    }
    case "path": {
      const el = row(r.title, v || "Not set", { property: true });
      const sfx = h("div", { class: "suffix" }, iconBtn("folder", "Choose…", () => folderPicker(r.title, values[key], (p) => { set(key, p); el.setSub(p); })),
        r.default === "" ? iconBtn("close", "Clear", () => { set(key, ""); el.setSub("Not set"); }) : null);
      el.append(sfx);
      setters[key] = (x) => el.setSub(x || "Not set");
      return el;
    }
    case "interface": {
      const sel = h("select", { class: "select" });
      const fill = (list, cur) => {
        const opts = [["", "Any interface"]];
        for (const i of list) if (!i.loopback) opts.push([i.name, `${i.name} (${i.vpn ? "VPN · " : ""}${i.addresses.length ? i.addresses.join(", ") : (i.up ? "up" : "down")})`]);
        if (cur && !opts.some((o) => o[0] === cur)) opts.push([cur, `${cur} (not connected)`]);
        opts.push(["__custom__", "Other…"]);
        sel.replaceChildren(...opts.map(([k, l]) => h("option", { value: k, text: l })));
        sel.value = cur || "";
      };
      const refresh = () => api("net.interfaces").then((l) => fill(l, values[key])).catch(() => {});
      fill([], v);
      refresh();
      sel.addEventListener("change", () => {
        if (sel.value === "__custom__") {
          sel.value = values[key] || "";
          askText("Network Interface", "tun0", "Use", (n) => { set(key, n); refresh(); }, "", "Type the name of a device that may not exist yet, such as tun0 or wg0. Transfers wait until it appears.");
        } else set(key, sel.value);
      });
      setters[key] = () => refresh();
      return row(r.title, r.subtitle, { suffix: [sel, iconBtn("refresh", "Refresh the list", refresh)] });
    }
    case "lines": {
      const ta = h("textarea", { class: "text" }, (v || []).join("\n"));
      const save = h("button", { class: "btn suggested", text: "Save", onclick: () => set(key, ta.value.split("\n").map((x) => x.trim()).filter(Boolean)) });
      const e = expander(r.title, "files");
      e.head.setSub(r.subtitle);
      e.content.append(h("div", { style: "padding:12px;display:flex;flex-direction:column;gap:8px;align-items:flex-end" }, ta, save));
      setters[key] = (x) => { if (document.activeElement !== ta) ta.value = (x || []).join("\n"); };
      return e;
    }
    case "schedule": {
      const g = scheduleGrid(v, (x) => set(key, x));
      setters[key] = (x) => g.set(x);
      return h("div", { class: "row", style: "display:block;padding:12px" }, g);
    }
  }
  return null;
}
function runAction(r) {
  const go = () => call("action.run", { action: r.action }, (res) => toast(res.message || "Done"));
  if (r.action === "settings.reset") {
    alertDialog({ heading: "Reset All Settings?", body: "Every preference goes back to its default. Your torrents are kept.",
      responses: [["cancel", "Cancel"], ["reset", "Reset", "destructive"]], onresponse: (x) => { if (x === "reset") { go(); dialogs.forEach((d) => d.close()); } } });
  } else go();
}

// ------------------------------------------------------------------ the weekly schedule painter
function scheduleGrid(grid, onchange) {
  let mode = "1", cur = grid || "0".repeat(168), drag = null;
  const colors = { 0: "rgba(46,194,126,0.45)", 1: "var(--warning-bg)", 2: "var(--destructive-bg)" };
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const table = h("div", { class: "sched", style: "display:grid;grid-template-columns:40px repeat(24,1fr);gap:2px" });
  table.append(h("div"));
  for (let hr = 0; hr < 24; hr++) table.append(h("div", { class: "caption dim", text: hr % 3 === 0 ? String(hr).padStart(2, "0") : "" }));
  const cells = [];
  for (let d = 0; d < 7; d++) {
    table.append(h("div", { class: "caption", text: DAYS[d], style: "align-self:center" }));
    for (let hr = 0; hr < 24; hr++) {
      const c = h("div", { style: "height:24px;border-radius:4px;cursor:crosshair", "data-d": d, "data-h": hr });
      cells.push(c);
      table.append(c);
    }
  }
  const paint = () => {
    for (let i = 0; i < 168; i++) {
      const d = Math.floor(i / 24), hr = i % 24;
      let m = cur[i];
      if (drag) { const [d1, h1, d2, h2] = drag; if (d >= Math.min(d1, d2) && d <= Math.max(d1, d2) && hr >= Math.min(h1, h2) && hr <= Math.max(h1, h2)) m = mode; }
      cells[i].style.background = colors[m];
    }
  };
  const cellAt = (e) => { const el = document.elementFromPoint(e.clientX, e.clientY); return el && el.dataset && el.dataset.d !== undefined ? [+el.dataset.d, +el.dataset.h] : null; };
  table.addEventListener("pointerdown", (e) => { const c = cellAt(e); if (!c) return; drag = [c[0], c[1], c[0], c[1]]; table.setPointerCapture(e.pointerId); paint(); });
  table.addEventListener("pointermove", (e) => { if (!drag) return; const c = cellAt(e); if (c) { drag[2] = c[0]; drag[3] = c[1]; paint(); } });
  table.addEventListener("pointerup", () => {
    if (!drag) return;
    const [d1, h1, d2, h2] = drag;
    drag = null;
    const g = cur.split("");
    for (let d = Math.min(d1, d2); d <= Math.max(d1, d2); d++) for (let hr = Math.min(h1, h2); hr <= Math.max(h1, h2); hr++) g[d * 24 + hr] = mode;
    const ng = g.join("");
    if (ng !== cur) { cur = ng; onchange(cur); }
    paint();
  });
  const tools = h("div", { class: "linked" });
  for (const [m, label] of [["0", "Full Speed"], ["1", "Slow Mode"], ["2", "Paused"]]) {
    const b = h("button", { class: "btn toggle" + (m === mode ? " checked" : ""), onclick: () => { mode = m; for (const x of tools.children) x.classList.toggle("checked", x === b); } },
      h("span", { class: "dot", style: `background:${colors[m]}` }), label);
    tools.append(b);
  }
  const el = h("div", {}, h("div", { class: "sched-tools" }, tools), h("div", { class: "caption dim", style: "text-align:center;margin-bottom:10px", text: "Pick a mode, then drag across the hours to paint them" }), table);
  el.set = (g) => { if (!drag && g && g.length === 168) { cur = g; paint(); } };
  paint();
  return el;
}

// ------------------------------------------------------------------ create torrent, statistics, about, shortcuts
function createDialog() {
  let path = "";
  const src = row("File or folder on the Cascade computer", "Nothing chosen yet", { property: true });
  const pathEntry = entryRow("Or type a path", "", (v) => { path = v.trim(); src.setSub(path || "Nothing chosen yet"); });
  src.append(h("div", { class: "suffix" }, iconBtn("folder", "Choose a folder", () => folderPicker("Choose a Folder", "", (p) => { path = p; src.setSub(p); pathEntry.input.value = p; }))));
  const trackers = h("textarea", { class: "text", placeholder: "One tracker URL per line; an empty line starts a new tier" });
  const seeds = h("textarea", { class: "text", style: "min-height:60px", placeholder: "Web seeds (optional), one per line" });
  const comment = entryRow("Comment", "", () => {}); comment.lastChild.remove();
  const source = entryRow("Source tag (for private trackers)", "", () => {}); source.lastChild.remove();
  const priv = switchRow("Private torrent", "Peers only come from its trackers", false, () => {});
  const piece = selectEl([["0", "Automatic"], ...[16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384].map((k) => [String(k * 1024), k < 1024 ? `${k} KiB` : `${k / 1024} MiB`])], "0", () => {});
  const ver = selectEl([["hybrid", "Hybrid (v1 and v2)"], ["v1", "Version 1"], ["v2", "Version 2"]], "hybrid", () => {});
  const seed = switchRow("Start seeding it", "", true, () => {});
  const prog = h("div", { class: "progress hidden", style: "margin-top:12px" }, h("div", { style: "width:0" }));
  const createBtn = h("button", { class: "btn suggested", text: "Create" });
  const body = h("div", { class: "dbody" }, group("Content", "", src, pathEntry), group("Trackers", "", h("div", { style: "padding:8px" }, trackers)),
    group("Web Seeds", "", h("div", { style: "padding:8px" }, seeds)),
    group("Options", "", comment, source, priv, row("Piece size", "", { suffix: piece }), row("Format", "", { suffix: ver }), seed), prog);
  const d = dialog({ title: "Create Torrent", body, start: h("button", { class: "btn", text: "Cancel", onclick: () => d.close() }), end: createBtn });
  createBtn.addEventListener("click", async () => {
    if (!path) { toast("Choose a file or folder first"); return; }
    createBtn.disabled = true;
    prog.classList.remove("hidden");
    try {
      const job = await api("create.start", { path, trackers: trackers.value.split("\n"), web_seeds: seeds.value.split("\n").filter((x) => x.trim()),
        comment: comment.input.value, source: source.input.value, private: priv.input.checked, piece_size: +piece.value, version: ver.value, seed: seed.input.checked });
      for (;;) {
        const st = await api("create.status", { job });
        if (st.state === "done") {
          const res = st.result, bin = atob(res.data), arr = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
          const a = h("a", { href: URL.createObjectURL(new Blob([arr], { type: "application/x-bittorrent" })), download: res.name + ".torrent" });
          document.body.append(a); a.click(); a.remove();
          toast(`Created ${res.name}.torrent`);
          d.close(); syncSoon(); return;
        }
        if (st.state === "error") throw new Error(st.error);
        prog.firstChild.style.width = (st.progress * 100).toFixed(1) + "%";
        await new Promise((r) => setTimeout(r, 300));
      }
    } catch (e) { toast("Cannot create the torrent: " + e.message); createBtn.disabled = false; prog.classList.add("hidden"); }
  });
}
function statsDialog() {
  const cv = h("canvas", { class: "graph" });
  const grid = h("div", { class: "stats-grid" });
  const body = h("div", { class: "dbody" }, cv, h("div", { class: "caption", style: "margin-top:6px" }, h("span", { style: "color:var(--accent-bg)" }, "● "), "Download   ",
    h("span", { style: "color:var(--success-bg)" }, "● "), "Upload   · last 5 minutes"), grid);
  const draw = async () => {
    const s = S.session;
    grid.replaceChildren(h("div", { text: "This session" }), h("div", { text: `↓ ${fmtSize(s.session_down)}  ↑ ${fmtSize(s.session_up)}` }),
      h("div", { text: "All time" }), h("div", { text: `↓ ${fmtSize(s.alltime_down)}  ↑ ${fmtSize(s.alltime_up)}` }),
      h("div", { text: "Overall ratio" }), h("div", { text: (s.alltime_down ? s.alltime_up / s.alltime_down : 0).toFixed(2) }),
      h("div", { text: "Connected peers" }), h("div", { text: `${s.peers} · DHT ${s.dht_nodes} nodes` }),
      h("div", { text: "Running for" }), h("div", { text: fmtDuration(s.uptime, true) }));
    let hist = [];
    try { hist = (await api("stats.history")).slice(-300); } catch (e) { return; }
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, hh = cv.clientHeight;
    cv.width = w * dpr; cv.height = hh * dpr;
    const ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    if (hist.length < 2) return;
    const top = Math.max(1, ...hist.map((x) => Math.max(x[1], x[2])));
    const cs = getComputedStyle(document.documentElement);
    for (const [idx, col] of [[1, cs.getPropertyValue("--accent-bg")], [2, cs.getPropertyValue("--success-bg")]]) {
      ctx.beginPath();
      hist.forEach((x, i) => ctx.lineTo(i * w / (hist.length - 1), hh - 4 - (hh - 10) * x[idx] / top));
      ctx.strokeStyle = col.trim(); ctx.lineWidth = 1.6; ctx.stroke();
      ctx.lineTo(w, hh); ctx.lineTo(0, hh); ctx.closePath(); ctx.globalAlpha = 0.18; ctx.fillStyle = col.trim(); ctx.fill(); ctx.globalAlpha = 1;
    }
  };
  const timer = setInterval(draw, 2000);
  dialog({ title: "Statistics", width: 460, height: 460, body, onclose: () => clearInterval(timer) });
  draw();
}
function aboutDialog() {
  const s = S.session || {};
  dialog({ title: "About", width: 400, height: 560, body: h("div", { class: "dbody status-page", style: "min-height:0" },
    h("img", { src: "/static/icon.svg", width: 128, height: 128, alt: "" }), h("h1", { text: "Cascade" }), h("p", { class: "dim", text: "Smiley-McSmiles" }),
    h("p", { text: `Version ${s.version || ""} · libtorrent ${s.libtorrent || ""}` }),
    h("p", { text: "A BitTorrent client for GNOME with a command line and a web interface. MIT license." }),
    h("div", { class: "actions" }, h("a", { class: "btn pill", href: "https://github.com/Smiley-McSmiles/Cascade-GTK", target: "_blank", rel: "noopener", text: "Website" }))) });
}
function shortcutsDialog() {
  const rows = [["Ctrl+O", "Open torrent files"], ["Ctrl+U", "Add a magnet link or URL"], ["Ctrl+V", "Add a magnet link from the clipboard"],
    ["Ctrl+K", "Search"], ["Ctrl+Shift+F", "Filter and sort"], ["Ctrl+,", "Preferences"], ["Escape", "Close dialogs, search or selection"]];
  dialog({ title: "Keyboard Shortcuts", width: 440, height: 520, body: h("div", { class: "dbody" }, group("", "", ...rows.map(([k, t]) => row(t, "", { suffix: h("kbd", { class: "label-chip", text: k }) })))) });
}
function registerMagnetHandler() {
  if (!navigator.registerProtocolHandler) { toast("This browser cannot hand magnet links to web pages"); return; }
  try {
    navigator.registerProtocolHandler("magnet", location.origin + "/add?magnet=%s");
    toast("Your browser will ask to open magnet links with Cascade");
  } catch (e) {
    toast(window.isSecureContext ? "The browser refused: " + e.message : "Browsers only allow this over HTTPS or on localhost (turn on HTTPS in Preferences → Remote)", null, 7000);
  }
}

// ------------------------------------------------------------------ keyboard, paste, drag and drop
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || "");
  if (e.key === "Escape") {
    if (openPopover) { closePopover(); return; }
    if (dialogs.length) { dialogs[dialogs.length - 1].close(); return; }
    if (S.selection) { setSelection(false); return; }
    if (S.searchBar && !S.searchBar.classList.contains("hidden")) { toggleSearch(false); return; }
    if (S.filterBar && S.filterBar.classList.contains("open")) { toggleFilterBar(false); return; }
  }
  if (!S.connected) return;
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key === "k") { e.preventDefault(); toggleSearch(true); }
  else if (ctrl && e.key === "o") { e.preventDefault(); $("#file-input").click(); }
  else if (ctrl && e.key === "u") { e.preventDefault(); magnetDialog(); }
  else if (ctrl && e.shiftKey && (e.key === "F" || e.key === "f")) { e.preventDefault(); toggleFilterBar(); }
  else if (ctrl && e.key === ",") { e.preventDefault(); prefsDialog(); }
  else if (!typing && !dialogs.length && e.key === "/") { e.preventDefault(); toggleSearch(true); }
});
document.addEventListener("paste", (e) => {
  if (!S.connected || dialogs.length || /^(INPUT|TEXTAREA)$/.test((document.activeElement || {}).tagName || "")) return;
  const t = (e.clipboardData || window.clipboardData).getData("text");
  if (isMagnet(t) || isUrl(t)) { e.preventDefault(); addText(t); }
});
let dragDepth = 0;
document.addEventListener("dragenter", (e) => { if (!S.connected) return; e.preventDefault(); if (dragDepth++ === 0) document.body.append(h("div", { class: "drop-hint", id: "drop-hint", text: "Drop to add" })); });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; const x = $("#drop-hint"); if (x) x.remove(); } });
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  const x = $("#drop-hint"); if (x) x.remove();
  if (!S.connected) return;
  if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
  else addText(e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "");
});

// ------------------------------------------------------------------ start
async function start() {
  showConnecting();
  try {
    const sch = await api("settings.schema");
    S.schema = sch;
    STATE_LABELS = sch.states || STATE_LABELS;
    FILTERS = sch.filters.map((f) => [f[0], f[1]]);
    SORTS = sch.sort;
    await loadSettings();
  } catch (e) {
    if (e instanceof AuthError) { showLogin(); return; }
    statusOnly(h("div", { class: "status-page" }, icon("error", "big"), h("h1", { text: "Cannot Reach Cascade" }), h("p", { text: e.message }),
      h("div", { class: "actions" }, h("button", { class: "btn pill suggested", text: "Try Again", onclick: start }))));
    return;
  }
  S.cards.clear();
  S.eventSeq = null;
  buildShell();
  S.connected = true;
  await sync();
  const params = new URLSearchParams(location.search);
  const magnet = params.get("magnet");
  if (magnet) { history.replaceState(null, "", "/"); addText(magnet); }
}
document.addEventListener("DOMContentLoaded", async () => {
  const fi = $("#file-input");
  fi.addEventListener("change", () => { addFiles([...fi.files]); fi.value = ""; });
  try {
    const r = await (await fetch("/api/ping")).json();
    if (r.result.auth_required && !S.token) { showLogin(); return; }
  } catch (e) { /* start() shows the error */ }
  start();
});
