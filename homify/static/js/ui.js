// UI-Hilfsfunktionen: DOM-Aufbau (XSS-sicher), Icons, Formatierung, Menüs, Dialoge, Toasts

const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V21h-6.5v-6h-5v6H3z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.6-4.6"/>',
  library: '<path d="M4 3.5v17M9 3.5v17M14 4.2l6 16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  heart: '<path d="M12 20.5 4.6 13.3A5 5 0 0 1 12 6.6a5 5 0 0 1 7.4 6.7z"/>',
  play: '<path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M6 4h4.2v16H6zM13.8 4H18v16h-4.2z" fill="currentColor" stroke="none"/>',
  next: '<path d="M5 5v14l10-7zM17 5h2.5v14H17z" fill="currentColor" stroke="none"/>',
  prev: '<path d="M19 5v14L9 12zM4.5 5H7v14H4.5z" fill="currentColor" stroke="none"/>',
  shuffle: '<path d="M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>',
  repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
  repeatOne: '<path d="m17 2 4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/><path d="M11 10.5h1.2V15"/>',
  volume: '<path d="M11 5 6 9H2.5v6H6l5 4z" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
  volumeLow: '<path d="M11 5 6 9H2.5v6H6l5 4z" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/>',
  mute: '<path d="M11 5 6 9H2.5v6H6l5 4z" fill="currentColor"/><path d="m22 9-6 6M16 9l6 6"/>',
  queue: '<path d="M3 6h13M3 12h9M3 18h7"/><path d="M16 18V9l5-1.5"/><circle cx="14" cy="18" r="2"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  cloud: '<path d="M7 18.5a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 8.6 4 4 0 0 1 17.5 18.5"/><path d="M12 11v9M9 17l3 3 3-3"/>',
  more: '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  note: '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  checkCircle: '<circle cx="12" cy="12" r="10" fill="currentColor" stroke="none"/><path d="m7.5 12 3 3 6-6" stroke="#000"/>',
  chevronLeft: '<path d="m15 18-6-6 6-6"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  radio: '<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  expand: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7L12 5M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7L12 19"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>',
  folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  grid: '<rect x="3" y="3" width="7.5" height="7.5" rx="1"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1"/>',
  addQueue: '<path d="M3 6h13M3 12h13M3 18h7M18 15v6M15 18h6"/>',
  playNext: '<path d="M3 6h9M3 12h9M3 18h9"/><path d="M15 8v8l6-4z" fill="currentColor"/>',
  person: '<circle cx="12" cy="7.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  album: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  mic: '<rect x="9" y="2.5" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5v3.5M8.5 21h7"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  sparkle: '<path d="M11 3.5l1.9 5.1 5.1 1.9-5.1 1.9L11 17.5l-1.9-5.1L4 10.5l5.1-1.9z"/><path d="M18.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>',
  wifi: '<path d="M2 8.8a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01"/>',
};

export function icon(name, cls = "") {
  const tpl = document.createElement("template");
  tpl.innerHTML = `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  return tpl.content.firstChild;
}

export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.replaceWith(icon(el.dataset.icon));
  });
}

export function logoSvg() {
  const tpl = document.createElement("template");
  tpl.innerHTML = `<svg viewBox="0 0 48 48" width="100%" height="100%" aria-hidden="true">
    <circle cx="24" cy="24" r="24" style="fill: var(--accent, #1ed760)"/>
    <path d="M12 24.5 24 14l12 10.5V35H28v-7h-8v7h-8z" fill="#000"/>
    <path d="M30.5 12.5c2.8 1.3 4.9 3.5 6 6.3M29 16.2c1.7.8 3 2.1 3.7 3.8" stroke="#000" stroke-width="2.2" fill="none" stroke-linecap="round"/>
  </svg>`;
  return tpl.content.firstChild;
}

/** Element erzeugen. Strings werden immer als Text eingefügt (kein HTML!). */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ---------------------------------------------------------------- Formatierung
export function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function fmtDuration(sec) {
  sec = Math.round(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h) return `${h} Std. ${m} Min.`;
  if (m) return `${m} Min. ${sec % 60} Sek.`;
  return `${sec} Sek.`;
}

export function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = Date.now(), diff = (now - d.getTime()) / 1000;
  if (diff < 60) return "gerade eben";
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  if (diff < 86400 * 7) return `vor ${Math.floor(diff / 86400)} Tag${diff >= 86400 * 2 ? "en" : ""}`;
  return d.toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "numeric" });
}

export function plural(n, one, many) { return `${n.toLocaleString("de-DE")} ${n === 1 ? one : many}`; }

export function fmtBytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function colorFor(text) {
  let hash = 0;
  for (const ch of String(text)) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue} 55% 42%)`;
}

// ---------------------------------------------------------------- Cover
export function coverUrl(id, size = 300) {
  return id ? `/api/covers/${encodeURIComponent(id)}?size=${size}` : "";
}

export function cover(id, { size = 300, cls = "", fallback = "note", round = false } = {}) {
  const el = h("div", { class: `cover ${cls} ${round ? "round" : ""}` });
  if (id) {
    el.style.backgroundImage = `url("${coverUrl(id, size)}")`;
  } else {
    el.append(icon(fallback));
  }
  return el;
}

export function mosaic(ids, { size = 300, cls = "", fallback = "note" } = {}) {
  const list = (ids || []).filter(Boolean);
  if (list.length < 4) return cover(list[0], { size, cls, fallback });
  const el = h("div", { class: `cover mosaic ${cls}` });
  for (const id of list.slice(0, 4)) {
    const cell = h("div");
    cell.style.backgroundImage = `url("${coverUrl(id, Math.round(size / 2))}")`;
    el.append(cell);
  }
  return el;
}

export function remoteCover(url, cls = "") {
  const el = h("div", { class: `cover ${cls}` });
  if (url && /^https:\/\//.test(url)) el.style.backgroundImage = `url("${url.replace(/"/g, "%22")}")`;
  else el.append(icon("note"));
  return el;
}

// ---------------------------------------------------------------- Toast
let toastTimer;
export function toast(message, { error = false, ms = 2600 } = {}) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

// ---------------------------------------------------------------- Kontextmenü
let menuCleanup = null;

export function closeMenu() {
  if (menuCleanup) { menuCleanup(); menuCleanup = null; }
}

/**
 * items: [{label, icon, action, danger}] | "sep" | {title}
 * anchor: MouseEvent oder Element
 */
export function openMenu(anchor, items) {
  closeMenu();
  const root = document.getElementById("menu-root");
  const backdrop = h("div", { class: "backdrop" });
  const menu = h("div", { class: "menu", role: "menu" });
  const mobile = window.matchMedia("(max-width: 720px)").matches;
  if (mobile) {
    menu.classList.add("sheet");
    backdrop.style.background = "rgba(0,0,0,.6)";
  }
  for (const item of items.filter(Boolean)) {
    if (item === "sep") { menu.append(h("hr")); continue; }
    if (item.title) { menu.append(h("div", { class: "menu-title ellipsis" }, item.title)); continue; }
    const btn = h("button", { role: "menuitem", style: item.danger ? { color: "var(--danger)" } : undefined },
      item.icon ? icon(item.icon) : null, h("span", { class: "ellipsis" }, item.label));
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      closeMenu();
      item.action?.();
    });
    menu.append(btn);
  }
  backdrop.addEventListener("click", closeMenu);
  backdrop.addEventListener("contextmenu", (e) => { e.preventDefault(); closeMenu(); });
  root.append(backdrop, menu);

  if (!mobile) {
    let x, y;
    if (anchor instanceof MouseEvent && anchor.type === "contextmenu") { x = anchor.clientX; y = anchor.clientY; }
    else {
      const target = anchor instanceof Event ? anchor.currentTarget || anchor.target : anchor;
      const r = target.getBoundingClientRect();
      x = r.right; y = r.bottom + 4;
    }
    const mr = menu.getBoundingClientRect();
    if (x + mr.width > innerWidth - 8) x = Math.max(8, x - mr.width);
    if (y + mr.height > innerHeight - 8) y = Math.max(8, innerHeight - mr.height - 8);
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
  }
  const onKey = (e) => { if (e.key === "Escape") closeMenu(); };
  document.addEventListener("keydown", onKey);
  menuCleanup = () => { backdrop.remove(); menu.remove(); document.removeEventListener("keydown", onKey); };
}

// ---------------------------------------------------------------- Dialoge
export function modal(title, body, actions = []) {
  return new Promise((resolve) => {
    const backdrop = h("div", { class: "modal-backdrop" });
    const close = (value) => { backdrop.remove(); document.removeEventListener("keydown", onKey); resolve(value); };
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    const actionBar = h("div", { class: "modal-actions" });
    for (const a of actions) {
      const btn = h("button", { class: `btn ${a.primary ? "btn-primary" : a.danger ? "btn-danger" : "btn-outline"}`, type: a.submit ? "submit" : "button" }, a.label);
      if (!a.submit) btn.addEventListener("click", async () => {
        if (!a.action) return close(a.value);
        const value = await a.action();
        if (value !== false) close(value);
      });
      actionBar.append(btn);
    }
    const form = h("form", { class: "modal", onsubmit: async (e) => {
      e.preventDefault();
      const submit = actions.find((a) => a.submit);
      if (!submit) return;
      const value = submit.action ? await submit.action() : true;
      if (value !== false) close(value);
    } },
      h("h2", {}, h("span", {}, title), h("button", { class: "icon-btn", type: "button", "aria-label": "Schließen", onclick: () => close(null) }, icon("close"))),
      body, actions.length ? actionBar : null);
    backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(null); });
    document.addEventListener("keydown", onKey);
    backdrop.append(form);
    document.body.append(backdrop);
    const first = form.querySelector("input, textarea, select");
    if (first) setTimeout(() => first.focus(), 30);
  });
}

export async function prompt(title, { label = "", value = "", placeholder = "", okLabel = "Speichern" } = {}) {
  const input = h("input", { class: "input", value, placeholder, maxlength: "200" });
  const body = h("label", { class: "field" }, label ? h("span", {}, label) : null, input);
  const result = await modal(title, body, [
    { label: "Abbrechen", value: null },
    { label: okLabel, primary: true, submit: true, action: () => input.value.trim() || false },
  ]);
  return result || null;
}

export async function confirmDialog(title, text, { okLabel = "OK", danger = false } = {}) {
  const result = await modal(title, h("p", { class: "muted" }, text), [
    { label: "Abbrechen", value: false },
    { label: okLabel, primary: !danger, danger, value: true },
  ]);
  return result === true;
}

// ---------------------------------------------------------------- Slider (Fortschritt/Lautstärke)
export function makeSlider(el, { onInput, onChange } = {}) {
  const fill = el.querySelector(".slider-fill");
  const thumb = el.querySelector(".slider-thumb");
  let dragging = false;
  let value = 0;
  const set = (v) => {
    value = Math.min(1, Math.max(0, v));
    fill.style.width = `${value * 100}%`;
    thumb.style.left = `${value * 100}%`;
  };
  const fromEvent = (e) => {
    const r = el.getBoundingClientRect();
    return (e.clientX - r.left) / r.width;
  };
  el.addEventListener("pointerdown", (e) => {
    dragging = true;
    el.classList.add("dragging");
    el.setPointerCapture(e.pointerId);
    set(fromEvent(e));
    onInput?.(value);
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    set(fromEvent(e));
    onInput?.(value);
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove("dragging");
    onChange?.(value);
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { set(value + 0.05); onChange?.(value); e.preventDefault(); }
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") { set(value - 0.05); onChange?.(value); e.preventDefault(); }
  });
  return { set: (v) => { if (!dragging) set(v); }, get dragging() { return dragging; } };
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
