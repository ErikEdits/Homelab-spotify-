// Einstellungen: die 100 Einstellungen (persönlich + Server) werden automatisch aus dem Schema gebaut,
// dazu Speicherort/NAS, spotDL, Zugriff, Benutzer, Sicherung, System

import { api, state } from "./api.js";
import { navigate } from "./components.js";
import { clear, confirmDialog, fmtBytes, fmtDate, fmtDuration, h, icon, modal, plural, toast } from "./ui.js";
import { eqPresets, resetSettings, saveSettings, setting, settingsSchema } from "./usersettings.js";

const USER_CATEGORIES = [
  ["Wiedergabe", "Klang, Überblenden, Qualität – gilt für dein Konto auf allen Geräten."],
  ["Equalizer", "Klang nach deinem Geschmack formen."],
  ["Aussehen", "Farben, Größe und Verhalten der Oberfläche."],
  ["Startseite", "Welche Reihen auf der Startseite erscheinen."],
  ["Suche & Bibliothek", "Spotify-Vorschläge, Sortierung und Mixe."],
];
const EQ_BANDS = [["eq_60", "60"], ["eq_150", "150"], ["eq_400", "400"], ["eq_1k", "1k"], ["eq_2k4", "2,4k"], ["eq_15k", "15k"]];

const sectionId = (title) => "set-" + title.toLowerCase().replace(/[^a-z0-9äöüß]+/g, "-").replace(/-+$/, "");

function section(title, desc, ...children) {
  return h("section", { class: "settings-section", id: sectionId(title) },
    h("h2", {}, title), desc ? h("p", { class: "muted" }, desc) : null, ...children);
}

function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", {}, hint) : null);
}

/** Zusatzblöcke (Knöpfe, Status …) – werden bei der Suche ausgeblendet. */
const extra = (...children) => h("div", { class: "set-extra" }, ...children);

// ================================================================ Bausteine für eine Einstellung
function fmtValue(s, v) {
  if (v === 0 && s.min === 0) {
    const m = /0 = ([^.]+)/.exec(s.help || "");
    if (m) return m[1].charAt(0).toUpperCase() + m[1].slice(1);
  }
  if (s.unit === "dB") return `${v > 0 ? "+" : ""}${v} dB`;
  return `${v}${s.unit ? ` ${s.unit}` : ""}`;
}

function control(s, value, save) {
  if (s.type === "bool") {
    const input = h("input", { type: "checkbox", role: "switch", checked: !!value, "aria-label": s.label });
    input.addEventListener("change", () => save(input.checked));
    return { el: h("label", { class: "switch" }, input, h("span", { class: "switch-track" })), set: (v) => { input.checked = !!v; } };
  }
  if (s.type === "select") {
    const sel = h("select", { class: "input", "aria-label": s.label },
      s.options.map((o) => h("option", { value: o.value, selected: String(o.value) === String(value) }, o.label)));
    sel.addEventListener("change", () => save(sel.value));
    return { el: sel, set: (v) => { sel.value = String(v); } };
  }
  if (s.type === "int") {
    const step = s.step || 1;
    if (s.min != null && s.max != null && (s.max - s.min) / step <= 60) {
      const out = h("output", { class: "range-value" });
      const range = h("input", { type: "range", min: s.min, max: s.max, step, value, "aria-label": s.label });
      const show = () => {
        out.textContent = fmtValue(s, Number(range.value));
        range.style.setProperty("--pct", `${((range.value - s.min) / (s.max - s.min)) * 100}%`);
      };
      range.addEventListener("input", show);
      range.addEventListener("change", () => save(Number(range.value)));
      show();
      return { el: h("div", { class: "range" }, range, out), set: (v) => { range.value = v; show(); } };
    }
    const num = h("input", { class: "input num", type: "number", min: s.min, max: s.max, step, value, "aria-label": s.label });
    num.addEventListener("change", () => save(Number(num.value)));
    return { el: h("div", { class: "num-wrap" }, num, s.unit ? h("span", { class: "muted" }, s.unit) : null), set: (v) => { num.value = v; } };
  }
  const input = h("input", {
    class: "input", type: s.type === "password" ? "password" : "text", value: value ?? "", "aria-label": s.label,
    autocomplete: s.type === "password" ? "new-password" : "off", spellcheck: "false", maxlength: "2000",
  });
  input.addEventListener("change", () => save(input.value));
  return { el: input, set: (v) => { input.value = v ?? ""; } };
}

const isDefault = (s, v) => (s.type === "password" ? !v : String(v) === String(s.default));

/** Eine Zeile: Bezeichnung + Hilfe links, Schalter/Regler rechts. Speichert sofort. */
function settingRow(s, value, saveFn) {
  let current = value;
  const row = h("div", { class: "set-row", dataset: { key: s.key, search: `${s.label} ${s.help} ${s.category} ${s.key}`.toLowerCase() } });
  const mark = () => row.classList.toggle("changed", !isDefault(s, current));
  const apply = async (v) => {
    try {
      current = await saveFn(v);
      ctl.set(current);
      mark();
      row.classList.remove("saved");
      void row.offsetWidth;
      row.classList.add("saved");
    } catch (e) {
      ctl.set(current);
      toast(e.message, { error: true });
    }
  };
  const ctl = control(s, value, apply);
  const reset = h("button", { class: "icon-btn set-reset", type: "button", title: "Standard wiederherstellen", "aria-label": "Standard wiederherstellen", onclick: () => apply(null) }, icon("refresh", "sm"));
  row.append(
    h("div", { class: "set-text" },
      h("div", { class: "set-label" }, s.label, s.restart ? h("span", { class: "badge orange" }, "nach Neustart") : null),
      s.help ? h("div", { class: "set-help" }, s.help) : null),
    h("div", { class: "set-control" }, reset, ctl.el));
  row._set = (v) => { current = v; ctl.set(v); mark(); };
  mark();
  return row;
}

const saveUser = (key) => async (v) => (await saveSettings({ [key]: v }))[key];

// ================================================================ Seite
export async function settingsView() {
  const schema = settingsSchema() || await api("/settings/schema");
  const all = schema.settings;
  const admin = !!state.user.is_admin;
  const userCount = all.filter((s) => s.scope === "user").length;
  const root = h("div", { class: "view-inner" });
  const wrap = h("div", { class: "settings" });
  root.append(wrap);

  const search = h("input", { class: "input", type: "search", placeholder: "Einstellungen durchsuchen …", "aria-label": "Einstellungen durchsuchen" });
  const nav = h("div", { class: "settings-nav" });
  const noHits = h("p", { class: "muted", hidden: true }, "Keine Einstellung gefunden.");
  wrap.append(
    h("h1", { class: "page-title" }, "Einstellungen"),
    h("p", { class: "muted settings-intro" }, admin
      ? `${schema.count} Einstellungen: ${userCount} persönliche (für dein Konto, auf allen Geräten) und ${schema.count - userCount} für den Server. Alles wird sofort gespeichert.`
      : `${userCount} persönliche Einstellungen – sie gelten für dein Konto auf allen Geräten und werden sofort gespeichert. Die ${schema.count - userCount} Server-Einstellungen kann ein Admin ändern.`),
    h("div", { class: "settings-search" }, icon("search", "sm"), search),
    nav, noHits);

  const navItems = [];
  const addSection = (el, title, group) => {
    wrap.append(el);
    navItems.push([title, el.id, group]);
  };

  // ---------------------------------------------------- Persönlich
  const userRows = {};
  let eq = null;
  for (const [cat, desc] of USER_CATEGORIES) {
    const box = section(cat, desc);
    for (const s of all.filter((x) => x.scope === "user" && x.category === cat)) {
      if (s.key.startsWith("eq_") && s.key !== "eq_enabled" && s.key !== "eq_preset") continue;
      let saveFn = saveUser(s.key);
      if (s.key === "eq_preset") {
        saveFn = async (v) => {
          const patch = { eq_preset: v ?? "flat" };
          const values = eqPresets()[patch.eq_preset];
          if (values) EQ_BANDS.forEach(([k], i) => { patch[k] = values[i]; });
          if (values && patch.eq_preset !== "flat") patch.eq_enabled = true;  // Preset wählen = Equalizer an
          const saved = await saveSettings(patch);
          userRows.eq_enabled?._set(saved.eq_enabled);
          eq?.sync();
          return saved.eq_preset;
        };
      }
      const row = settingRow(s, setting(s.key), saveFn);
      userRows[s.key] = row;
      box.append(row);
    }
    if (cat === "Equalizer") {
      eq = eqWidget(userRows);
      box.append(eq.el);
    }
    addSection(box, cat, "Persönlich");
  }
  const account = accountSection(userCount);
  addSection(account, "Konto", "Persönlich");

  const destroyers = [];
  if (admin) {
    const data = await api("/settings");
    const serverValues = data.settings;
    const sys = data.system;
    const banner = h("div", { class: "notice restart-banner", hidden: !sys.restart_needed }, icon("alert"),
      h("div", { class: "grow" }, h("strong", {}, "Neustart nötig. "), "Adresse oder Port wurden geändert – das wirkt erst nach einem Neustart von Homify. Danach brauchen die Apps ggf. die neue Adresse."),
      h("button", { class: "btn btn-small btn-primary", onclick: () => restartServer() }, "Jetzt neu starten"));
    const saveServer = (key) => async (v) => {
      const r = await api("/settings", { method: "PUT", body: { [key]: v } });
      Object.assign(serverValues, r.settings);
      Object.assign(sys, r.system);
      banner.hidden = !r.system.restart_needed;
      if (key === "server_name") document.querySelectorAll(".brand span:last-child").forEach((el) => { el.textContent = r.settings.server_name; });
      return r.settings[key];
    };
    const rowsFor = (cat) => all.filter((s) => s.scope === "server" && s.category === cat)
      .map((s) => settingRow(s, serverValues[s.key], saveServer(s.key)));

    wrap.append(h("div", { class: "settings-divider" }, h("h2", {}, "Server"), h("span", { class: "badge" }, "nur Admins")), banner);

    addSection(storageSection(sys, serverValues), "Speicherort der Musik", "Server");

    const lib = libraryExtras(sys, serverValues);
    destroyers.push(lib.destroy);
    addSection(section("Bibliothek & Scan", "Homify liest Titel, Künstler und Cover direkt aus den Dateien und findet neue Songs automatisch.",
      lib.top, ...rowsFor("Bibliothek & Scan"), lib.bottom), "Bibliothek & Scan", "Server");

    addSection(section("Streaming", "Formate, die ein Gerät nicht abspielen kann (z. B. WMA, ALAC, APE), wandelt Homify automatisch um.",
      ...rowsFor("Streaming"),
      extra(h("dl", { class: "kv" }, h("dt", {}, "Umwandlungs-Cache belegt"), h("dd", {}, `${sys.cache_mb} MB`)),
        h("div", { class: "row-actions" }, h("button", { class: "btn btn-small btn-outline", onclick: async () => { await api("/system/cache", { method: "DELETE" }); toast("Cache geleert"); } }, "Cache leeren")))),
    "Streaming", "Server");

    const tools = spotdlExtras(sys);
    destroyers.push(tools.destroy);
    addSection(section("Downloads (spotDL)",
      "Fehlt ein Song, holt Homify ihn mit spotDL: die Infos (Titel, Cover, Album) kommen von Spotify, das Audio von YouTube Music. spotDL läuft in einer eigenen Python-Umgebung und lässt sich hier mit einem Klick aktualisieren – wichtig, wenn Downloads plötzlich nicht mehr klappen.",
      tools.el, ...rowsFor("Downloads (spotDL)")), "Downloads (spotDL)", "Server");

    addSection(remoteSection(sys), "Zugriff von Handy, PC-App und unterwegs", "Server");
    addSection(await usersSection(serverValues), "Benutzer", "Server");
    addSection(section("Server & Sicherheit", null, ...rowsFor("Server & Sicherheit")), "Server & Sicherheit", "Server");
    addSection(backupSection(sys), "Sicherung & Umzug auf den Server", "Server");
    addSection(systemSection(sys), "System", "Server");
  }

  // Navigation + Suche
  let lastGroup = null;
  for (const [title, id, group] of navItems) {
    if (admin && group !== lastGroup) {
      nav.append(h("span", { class: "nav-group" }, group));
      lastGroup = group;
    }
    nav.append(h("button", { class: "chip", type: "button", onclick: () => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }) }, title));
  }
  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    wrap.classList.toggle("searching", !!q);
    let hits = 0;
    wrap.querySelectorAll(".settings-section").forEach((sec) => {
      let visible = 0;
      sec.querySelectorAll(".set-row").forEach((row) => {
        const show = !q || row.dataset.search.includes(q);
        row.hidden = !show;
        if (show) visible++;
      });
      const titleHit = q && sec.querySelector("h2")?.textContent.toLowerCase().includes(q);
      sec.hidden = !!q && !visible && !titleHit;
      if (titleHit) sec.querySelectorAll(".set-row").forEach((row) => { row.hidden = false; });
      hits += visible;
    });
    noHits.hidden = !q || hits > 0 || wrap.querySelector(".settings-section:not([hidden])");
  });

  return { el: root, color: "#2a2a2a", destroy: () => destroyers.forEach((fn) => fn?.()) };
}

// ================================================================ Equalizer
function eqWidget(rows) {
  const sliders = {};
  const bands = h("div", { class: "eq-bands" });
  for (const [key, label] of EQ_BANDS) {
    const out = h("div", { class: "eq-val" });
    const r = h("input", { type: "range", class: "eq-slider", min: "-12", max: "12", step: "1", value: setting(key), "aria-label": `${label} Hz` });
    const show = () => { out.textContent = `${r.value > 0 ? "+" : ""}${r.value}`; };
    r.addEventListener("input", show);
    r.addEventListener("change", async () => {
      try {
        await saveSettings({ [key]: Number(r.value), eq_preset: "custom" });
        rows.eq_preset?._set("custom");
        sync();
      } catch (e) { toast(e.message, { error: true }); }
    });
    show();
    sliders[key] = { r, show };
    bands.append(h("div", { class: "eq-band" }, out, r, h("div", { class: "eq-label" }, label)));
  }
  // Presets als Knöpfe (wie bei Spotify): ein Klick stellt alle 6 Regler und schaltet den Equalizer ein
  const presetOptions = (settingsSchema()?.settings.find((x) => x.key === "eq_preset")?.options || []).filter((o) => o.value !== "custom");
  const chips = h("div", { class: "eq-presets" }, presetOptions.map((o) => h("button", {
    type: "button", class: "gen-chip", dataset: { preset: o.value }, onclick: async () => {
      const values = eqPresets()[o.value];
      const patch = { eq_preset: o.value };
      if (values) EQ_BANDS.forEach(([k], i) => { patch[k] = values[i]; });
      if (o.value !== "flat") patch.eq_enabled = true;
      try {
        await saveSettings(patch);
        rows.eq_preset?._set(o.value);
        rows.eq_enabled?._set(setting("eq_enabled"));
        sync();
      } catch (e) { toast(e.message, { error: true }); }
    } }, o.label.replace(/ \(.*\)$/, ""))));
  const el = h("div", { class: "set-row eq-row", dataset: { search: "equalizer eq bass höhen mitten 60 150 400 1k 2,4k 15k hz klang preset voreinstellung rock pop hip-hop jazz dance" } },
    h("div", { class: "eq-wrap" }, chips,
      h("div", { class: "eq-box" }, h("div", { class: "eq-scale" }, h("span", {}, "+12 dB"), h("span", {}, "0"), h("span", {}, "−12 dB")), bands)));
  const sync = () => {
    for (const [key] of EQ_BANDS) { sliders[key].r.value = setting(key); sliders[key].show(); }
    el.classList.toggle("off", !setting("eq_enabled"));
    const active = setting("eq_enabled") ? setting("eq_preset") : "flat";
    chips.querySelectorAll(".gen-chip").forEach((c) => c.classList.toggle("active", c.dataset.preset === active));
  };
  sync();
  rows.eq_enabled?.addEventListener("change", () => setTimeout(sync, 0));
  return { el, sync };
}

// ================================================================ Konto
function accountSection(userCount) {
  const oldPw = h("input", { type: "password", autocomplete: "current-password" });
  const newPw = h("input", { type: "password", autocomplete: "new-password", minlength: "4" });
  return section("Konto", `Angemeldet als ${state.user.username}${state.user.is_admin ? " (Admin)" : ""}.`,
    extra(
      h("div", { class: "grid-2" }, field("Altes Passwort", oldPw), field("Neues Passwort", newPw)),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-small btn-outline", onclick: async () => {
          try {
            await api("/auth/password", { method: "POST", body: { old_password: oldPw.value, new_password: newPw.value } });
            oldPw.value = newPw.value = "";
            toast("Passwort geändert");
          } catch (e) { toast(e.message, { error: true }); }
        } }, "Passwort ändern"),
        h("button", { class: "btn btn-small btn-outline", onclick: async () => {
          if (!await confirmDialog("Persönliche Einstellungen zurücksetzen?", `Alle ${userCount} persönlichen Einstellungen (Wiedergabe, Equalizer, Aussehen …) gehen zurück auf Standard. Playlists und Lieblingssongs bleiben.`, { okLabel: "Zurücksetzen", danger: true })) return;
          await resetSettings();
          toast("Zurückgesetzt");
          navigate("#/settings");
        } }, icon("refresh", "sm"), "Persönliche Einstellungen zurücksetzen"),
        h("button", { class: "btn btn-small btn-danger", onclick: async () => { await api("/auth/logout", { method: "POST" }); location.reload(); } }, icon("logout", "sm"), "Abmelden"))));
}

// ================================================================ Bibliothek (Admin)
function libraryExtras(sys, s) {
  const scanInfo = h("p", { class: "muted", style: { fontSize: "13px" } });
  const drawScan = (st) => {
    const stats = sys.stats;
    scanInfo.textContent = st.running
      ? `Scan läuft … ${st.checked}/${st.files} Dateien gelesen`
      : `${plural(stats.tracks, "Song", "Songs")} · ${plural(stats.albums, "Album", "Alben")} · ${fmtBytes(stats.size)} · ${fmtDuration(stats.duration)}`
        + (st.finished_at ? ` · letzter Scan ${new Date(st.finished_at * 1000).toLocaleTimeString("de-DE")}` : "")
        + (st.message ? ` · ${st.message}` : "");
  };
  drawScan(sys.scan);
  let timer;
  const poll = async () => {
    clearTimeout(timer);
    const st = await api("/status").catch(() => null);
    if (!st || !scanInfo.isConnected) return;
    if (!st.scan.running) sys.stats = (await api("/settings")).system.stats;
    drawScan(st.scan);
    if (st.scan.running) timer = setTimeout(poll, 1500);
  };
  if (sys.scan.running) setTimeout(poll, 500);

  const dirs = h("textarea", { rows: "2", placeholder: "D:\\Alte Musik\n/mnt/usb/musik" }, (s.music_dirs || []).join("\n"));
  const dupInfo = h("div", { class: "notice", hidden: !sys.duplicates, style: { alignItems: "center" } });
  const drawDups = (n) => {
    dupInfo.hidden = !n;
    dupInfo.replaceChildren(icon("info"), h("div", { class: "grow" },
      h("strong", {}, `${plural(n, "doppelte Datei", "doppelte Dateien")} ausgeblendet. `),
      "Jeder Song erscheint nur einmal – behalten wird die beste Version. Die anderen Dateien liegen noch im Speicherort."),
      h("button", { class: "btn btn-small btn-outline", onclick: async () => drawDups(await duplicatesDialog()) }, "Anzeigen"));
  };
  drawDups(sys.duplicates || 0);
  const top = extra(scanInfo, dupInfo,
    h("div", { class: "row-actions", style: { marginBottom: "12px" } },
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { drawScan(await api("/library/scan", { method: "POST" })); poll(); } }, icon("refresh", "sm"), "Jetzt scannen"),
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { drawScan(await api("/library/scan?full=true", { method: "POST" })); poll(); } }, "Alles neu einlesen")));
  const bottom = extra(h("details", { style: { marginTop: "12px" } },
    h("summary", { style: { fontWeight: 700, margin: "8px 0 12px" } }, "Zusätzliche Ordner (nur lesen)"),
    h("p", { class: "muted", style: { fontSize: "13px" } }, "Optional: weitere Ordner, deren Musik Homify nur abspielt (z. B. eine alte Sammlung). Neue Downloads landen immer im Speicherort."),
    field("Ordner (einer pro Zeile)", dirs),
    h("div", { class: "row-actions" },
      h("button", { class: "btn btn-small btn-outline", type: "button", onclick: async () => {
        const picked = await folderPicker();
        if (picked) dirs.value = (dirs.value.trim() ? dirs.value.trim() + "\n" : "") + picked;
      } }, icon("folder", "sm"), "Ordner wählen"),
      h("button", { class: "btn btn-small btn-primary", type: "button", onclick: async () => {
        try {
          await api("/settings", { method: "PUT", body: { music_dirs: dirs.value.split("\n").map((x) => x.trim()).filter(Boolean) } });
          toast("Gespeichert – Bibliothek wird neu eingelesen");
          poll();
        } catch (e) { toast(e.message, { error: true }); }
      } }, "Ordner speichern"))));
  return { top, bottom, destroy: () => clearTimeout(timer) };
}

/** Liste der ausgeblendeten doppelten Dateien – einzeln oder alle löschen. Gibt die neue Anzahl zurück. */
async function duplicatesDialog() {
  let data = await api("/duplicates");
  const list = h("div", { style: { maxHeight: "55vh", overflow: "auto" } });
  const fmtQ = (codec, bitrate) => [String(codec || "?").toUpperCase(), bitrate ? `${Math.round(bitrate / 1000)} kbit/s` : null].filter(Boolean).join(" ");
  const remove = async (body) => {
    const r = await api("/duplicates/delete", { method: "POST", body });
    if (r.errors.length) toast(`Nicht alles gelöscht: ${r.errors[0]}`, { error: true, ms: 6000 });
    else toast(`${plural(r.deleted, "Datei", "Dateien")} gelöscht`);
    data = await api("/duplicates");
    draw();
  };
  const draw = () => {
    clear(list);
    if (!data.items.length) { list.append(h("p", { class: "muted" }, "Keine doppelten Dateien.")); return; }
    for (const d of data.items) {
      list.append(h("div", { class: "dup-row" },
        h("div", { style: { minWidth: 0, flex: 1 } },
          h("div", { class: "ellipsis" }, h("strong", {}, d.title), ` · ${d.artist}`),
          h("div", { class: "muted ellipsis", style: { fontSize: "12px" }, title: d.path }, `Doppelt: ${d.rel || d.path} (${fmtQ(d.codec, d.bitrate)})`),
          h("div", { class: "muted ellipsis", style: { fontSize: "12px" }, title: d.kept_path || "" }, `Behalten: ${d.kept_rel || d.kept_path || "–"} (${fmtQ(d.kept_codec, d.kept_bitrate)})`)),
        h("button", { class: "icon-btn", title: "Diese doppelte Datei löschen", "aria-label": "Löschen", onclick: async () => {
          if (await confirmDialog("Doppelte Datei löschen?", `${d.path} wird vom Speicherort gelöscht. Die behaltene Version bleibt.`, { okLabel: "Löschen", danger: true })) remove({ paths: [d.path] });
        } }, icon("trash"))));
    }
  };
  draw();
  await modal("Doppelte Dateien", h("div", {},
    h("p", { class: "muted", style: { fontSize: "13px", marginTop: 0 } }, "Diese Dateien sind Kopien von Songs, die schon in der Bibliothek sind (gleicher Titel, Interpret und Länge). Sie werden nicht angezeigt. Löschen spart Platz – die behaltene Version bleibt immer erhalten."),
    list), [
    { label: "Alle doppelten löschen", danger: true, action: async () => {
      if (await confirmDialog("Alle doppelten Dateien löschen?", `${plural(data.count, "Datei wird", "Dateien werden")} vom Speicherort gelöscht. Die behaltenen Versionen bleiben.`, { okLabel: "Alle löschen", danger: true })) await remove({ all: true });
      return false;
    } },
    { label: "Schließen", primary: true, value: true },
  ]);
  return data.count;
}

// ================================================================ spotDL (Admin)
function spotdlExtras(sys) {
  const info = h("div");
  const logBox = h("div", { class: "log", hidden: true });
  const installBtn = h("button", { class: "btn btn-small btn-primary" }, icon("download", "sm"), sys.spotdl_installed ? "spotDL aktualisieren" : "spotDL installieren");
  const draw = (tools) => {
    clear(info).append(h("dl", { class: "kv" },
      h("dt", {}, "spotDL"), h("dd", {}, sys.spotdl_installed ? (sys.spotdl?.spotdl || "installiert") : h("span", { class: "badge red" }, "nicht installiert")),
      h("dt", {}, "yt-dlp"), h("dd", {}, sys.spotdl?.["yt-dlp"] || "–"),
      h("dt", {}, "ffmpeg"), h("dd", {}, sys.ffmpeg || h("span", { class: "badge red" }, "nicht gefunden")),
      h("dt", {}, "Speichert nach"), h("dd", {}, sys.storage.label)));
    if (tools?.log?.length) {
      logBox.hidden = false;
      logBox.textContent = tools.log.join("\n");
      logBox.scrollTop = logBox.scrollHeight;
    }
    installBtn.disabled = !!tools?.running;
    if (tools?.running) installBtn.replaceChildren(h("span", { class: "spinner" }), "Läuft …");
  };
  draw(sys.tools);
  let timer;
  const poll = async () => {
    clearTimeout(timer);
    const st = await api("/status").catch(() => null);
    if (!st || !info.isConnected) return;
    draw(st.tools);
    if (st.tools.running) { timer = setTimeout(poll, 1200); return; }
    Object.assign(sys, (await api("/settings")).system);
    draw(st.tools);
    installBtn.replaceChildren(icon("download", "sm"), sys.spotdl_installed ? "spotDL aktualisieren" : "spotDL installieren");
    installBtn.disabled = false;
    toast(st.tools.ok ? "spotDL ist bereit" : "spotDL-Installation fehlgeschlagen – siehe Protokoll", { error: !st.tools.ok });
  };
  installBtn.addEventListener("click", async () => {
    await api("/system/spotdl", { method: "POST" });
    installBtn.disabled = true;
    installBtn.replaceChildren(h("span", { class: "spinner" }), "Läuft …");
    poll();
  });
  if (sys.tools?.running) poll();
  return {
    el: extra(info, h("div", { class: "row-actions", style: { marginBottom: "12px" } }, installBtn), logBox, cookieBlock(sys.youtube_cookies || {})),
    destroy: () => clearTimeout(timer),
  };
}

/** YouTube-Cookies hochladen (Datei oder eingefügter Text) – hilft, wenn YouTube Downloads blockiert. */
function cookieBlock(initial) {
  const box = h("div", { class: "cookie-box" });
  const file = h("input", { type: "file", accept: ".txt,text/plain", hidden: true });
  const setRow = (path) => document.querySelector('.set-row[data-key="spotdl_cookie_file"]')?._set(path);
  const saved = async (st) => {
    setRow(st.path);
    draw(st);
    const failed = (await api("/downloads").catch(() => ({ jobs: [] }))).jobs.filter((j) => ["error", "partial"].includes(j.status));
    toast(st.logged_in ? `Gespeichert: ${st.count} YouTube-Cookies` : "Gespeichert – aber ohne YouTube-Anmeldung, bitte angemeldet exportieren",
      { error: !st.logged_in, ms: 5000 });
    if (st.logged_in && failed.length && await confirmDialog("Fehlgeschlagene Downloads neu starten?",
      `${plural(failed.length, "Download ist", "Downloads sind")} fehlgeschlagen. Mit den Cookies nochmal versuchen?`, { okLabel: "Neu starten" })) {
      for (const j of failed) await api(`/downloads/${j.id}/retry`, { method: "POST" }).catch(() => {});
      toast(`${plural(failed.length, "Download", "Downloads")} neu gestartet`);
    }
  };
  const upload = async (text) => {
    try { await saved(await api("/settings/youtube-cookies", { method: "POST", body: { text } })); }
    catch (e) { toast(e.message, { error: true, ms: 7000 }); }
  };
  file.addEventListener("change", async () => {
    const f = file.files[0];
    file.value = "";
    if (f) await upload(await f.text());
  });
  const paste = async () => {
    const ta = h("textarea", { class: "input", rows: "8", placeholder: "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t…", spellcheck: "false", style: { fontFamily: "monospace", fontSize: "12px" } });
    const text = await modal("Cookie-Text einfügen", h("div", {},
      h("p", { class: "muted", style: { fontSize: "13px", marginTop: 0 } }, "Den kompletten Inhalt der cookies.txt hier einfügen (Strg + V). Homify behält nur die YouTube-Cookies."), ta), [
      { label: "Abbrechen", value: null },
      { label: "Speichern", primary: true, action: () => ta.value.trim() || false },
    ]);
    if (text) await upload(text);
  };
  const draw = (st) => {
    const stateEl = !st.exists
      ? h("span", { class: "badge" }, "keine hinterlegt")
      : st.logged_in
        ? h("span", { class: "badge green" }, icon("check", "sm"), "angemeldet")
        : h("span", { class: "badge red" }, "ohne Anmeldung");
    box.replaceChildren(
      h("div", { class: "set-label" }, "YouTube-Cookies", stateEl),
      h("p", { class: "set-help", style: { margin: "4px 0 10px" } },
        st.exists
          ? `${plural(st.count, "YouTube-Cookie", "YouTube-Cookies")} · Stand ${fmtDate(st.updated)}. Scheitern Downloads wieder mit „YouTube verlangt eine Bestätigung“, sind sie abgelaufen – einfach neu hochladen.`
          : "Nur nötig, wenn Downloads mit „YouTube verlangt eine Bestätigung“ scheitern. Dann lädt spotDL wie ein angemeldeter YouTube-Nutzer."),
      h("div", { class: "row-actions" },
        h("button", { class: "btn btn-small btn-primary", type: "button", onclick: () => file.click() }, icon("download", "sm"), "Cookie-Datei hochladen"),
        h("button", { class: "btn btn-small btn-outline", type: "button", onclick: paste }, "Text einfügen"),
        st.exists ? h("button", { class: "btn btn-small btn-outline", type: "button", onclick: async () => {
          const r = await api("/settings/youtube-cookies", { method: "DELETE" });
          setRow(r.path);
          draw(r);
          toast("Cookies entfernt");
        } }, "Entfernen") : null,
        file),
      h("details", { class: "help" }, h("summary", {}, "So bekommst du die Datei"),
        h("ol", {},
          h("li", {}, "In Chrome die Erweiterung „Get cookies.txt LOCALLY“ installieren (in Firefox: „cookies.txt“)."),
          h("li", {}, "Im normalen Fenster (nicht privat) bei youtube.com angemeldet sein – am besten mit einem Zweitkonto."),
          h("li", {}, "Auf youtube.com die Erweiterung anklicken → „Export All Cookies“. Die Datei landet im Download-Ordner."),
          h("li", {}, "Hier „Cookie-Datei hochladen“ klicken und die Datei auswählen. Fertig."),
          h("li", {}, "Danach bei Google nicht abmelden – sonst werden die Cookies ungültig.")),
        h("p", { class: "muted", style: { fontSize: "12px" } }, "Homify speichert nur die YouTube-Cookies (keine Google-/Gmail-Cookies) und nur auf dem Server. Die Datei wie ein Passwort behandeln und nicht weitergeben.")));
  };
  draw(initial);
  return box;
}

// ================================================================ System (Admin)
async function restartServer() {
  if (!await confirmDialog("Homify neu starten?", "Die Wiedergabe wird kurz unterbrochen.", { okLabel: "Neu starten" })) return;
  await api("/system/restart", { method: "POST" });
  toast("Homify startet neu …", { ms: 6000 });
  const wait = async () => { try { await api("/setup"); location.reload(); } catch { setTimeout(wait, 1000); } };
  setTimeout(wait, 2500);
}

function systemSection(sys) {
  const ld = sys.loudness || {};
  return section("System", null, extra(
    h("dl", { class: "kv" },
      h("dt", {}, "Homify-Version"), h("dd", {}, sys.version),
      h("dt", {}, "Rechner"), h("dd", {}, `${sys.hostname} (${sys.platform})`),
      h("dt", {}, "ffmpeg"), h("dd", {}, sys.ffmpeg || "nicht gefunden"),
      h("dt", {}, "Klang analysiert"), h("dd", {}, ld.running ? `läuft … noch ${ld.remaining} Songs` : ld.remaining ? `${ld.remaining} Songs warten auf die Analyse` : "alle Songs")),
    h("div", { class: "row-actions" },
      h("button", { class: "btn btn-small btn-outline", onclick: restartServer }, icon("refresh", "sm"), "Neu starten"),
      h("button", { class: "btn btn-small btn-danger", onclick: async () => {
        if (!await confirmDialog("Homify beenden?", "Der Server wird gestoppt. Danach ist Homify auf keinem Gerät mehr erreichbar, bis du es wieder startest.", { okLabel: "Beenden", danger: true })) return;
        await api("/system/shutdown", { method: "POST" });
        toast("Homify wurde beendet.", { ms: 8000 });
      } }, "Homify beenden"))));
}

// ================================================================ Benutzer (Admin)
async function usersSection(serverValues) {
  const box = section("Benutzer", "Jeder Benutzer hat eigene Playlists, Lieblingssongs, Verlauf und persönliche Einstellungen. Die Bibliothek teilen sich alle.");
  const list = h("div");
  box.append(extra(list));
  const draw = async () => {
    const users = await api("/users");
    clear(list);
    for (const u of users) {
      const dl = h("input", { type: "checkbox", checked: !!u.can_download || !!u.is_admin, disabled: !!u.is_admin });
      dl.addEventListener("change", () => api(`/users/${u.id}`, { method: "PATCH", body: { can_download: dl.checked } }).then(() => toast("Gespeichert")));
      list.append(h("div", { class: "user-row" }, icon("user"),
        h("div", { class: "grow" }, h("strong", {}, u.username), u.is_admin ? h("span", { class: "badge green", style: { marginLeft: "8px" } }, "Admin") : null),
        h("label", { class: "check", style: { margin: 0, fontSize: "13px" } }, dl, "darf herunterladen"),
        h("button", { class: "icon-btn", title: "Passwort zurücksetzen", "aria-label": "Passwort zurücksetzen", onclick: async () => {
          const pw = h("input", { class: "input", type: "password", minlength: "4" });
          const ok = await modal(`Neues Passwort für ${u.username}`, h("label", { class: "field" }, h("span", {}, "Passwort"), pw), [
            { label: "Abbrechen", value: null }, { label: "Speichern", primary: true, submit: true, action: () => pw.value.length >= 4 || false }]);
          if (ok) { await api(`/users/${u.id}`, { method: "PATCH", body: { password: pw.value } }); toast("Passwort geändert"); }
        } }, icon("lock")),
        u.id !== state.user.id ? h("button", { class: "icon-btn", title: "Löschen", "aria-label": "Löschen", onclick: async () => {
          if (await confirmDialog("Benutzer löschen?", `${u.username} und seine Playlists werden gelöscht.`, { okLabel: "Löschen", danger: true })) {
            await api(`/users/${u.id}`, { method: "DELETE" });
            draw();
          }
        } }, icon("trash")) : null));
    }
  };
  await draw();
  const name = h("input", { placeholder: "Name", maxlength: "64" });
  const pw = h("input", { type: "password", placeholder: "Passwort (mind. 4 Zeichen)" });
  const admin = h("input", { type: "checkbox" });
  const canDl = h("input", { type: "checkbox", checked: serverValues.new_users_can_download !== false });
  box.append(extra(
    h("div", { class: "grid-2", style: { marginTop: "16px" } }, field("Neuer Benutzer", name), field("Passwort", pw)),
    h("label", { class: "check" }, admin, "Admin (darf Einstellungen ändern)"),
    h("label", { class: "check" }, canDl, "Darf Songs herunterladen"),
    h("button", { class: "btn btn-small btn-outline", onclick: async () => {
      try {
        await api("/users", { method: "POST", body: { username: name.value, password: pw.value, is_admin: admin.checked, can_download: canDl.checked } });
        name.value = pw.value = "";
        admin.checked = false;
        toast("Benutzer angelegt");
        draw();
      } catch (e) { toast(e.message, { error: true }); }
    } }, icon("plus", "sm"), "Benutzer anlegen")));
  return box;
}

async function folderPicker() {
  let current = "";
  const list = h("div", { style: { maxHeight: "50vh", overflow: "auto", display: "grid", gap: "2px" } });
  const pathLabel = h("div", { class: "muted ellipsis", style: { marginBottom: "8px", fontSize: "13px" } });
  const manual = h("input", { class: "input", placeholder: "oder Pfad eintippen, z. B. \\\\NAS\\Musik" });
  const load = async (path) => {
    try {
      const res = await api("/system/browse", { method: "POST", body: { path } });
      current = res.path;
      manual.value = res.path;
      pathLabel.textContent = res.path || "Laufwerke";
      clear(list);
      if (res.parent !== null && res.parent !== undefined && res.path) {
        list.append(h("button", { type: "button", class: "list-row", onclick: () => load(res.parent) }, icon("chevronLeft"), ".."));
      }
      for (const d of res.dirs) {
        const name = d.split(/[\\/]/).filter(Boolean).pop() || d;
        list.append(h("button", { type: "button", class: "list-row", onclick: () => load(d) }, icon("folder"), name));
      }
      if (!res.dirs.length) list.append(h("p", { class: "muted" }, "Keine Unterordner"));
    } catch (e) { toast(e.message, { error: true }); }
  };
  manual.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); load(manual.value.trim()); } });
  await load("");
  const result = await modal("Musikordner wählen", h("div", {}, pathLabel, list, h("div", { style: { marginTop: "12px" } }, manual),
    h("p", { class: "muted", style: { fontSize: "12px" } }, "NAS-Freigaben: Pfad wie \\\\NAS\\Musik eintippen und Enter drücken.")), [
    { label: "Abbrechen", value: null },
    { label: "Diesen Ordner nehmen", primary: true, action: () => manual.value.trim() || current || false },
  ]);
  return result || null;
}

// ================================================================ Speicherort
const STORAGE_MODES = [
  ["local", "Auf diesem Rechner (App-Ordner)", "Gut zum Testen – alles liegt im Homify-Ordner auf diesem Rechner."],
  ["nas", "NAS (z. B. UGREEN)", "Homify verbindet sich selbst per SMB mit dem NAS – kein Netzlaufwerk nötig."],
  ["folder", "Eigener Ordner", "Lokaler Ordner oder vom System eingebundenes Laufwerk, z. B. /mnt/nas/musik"],
];

function storageSection(sys, s) {
  const info = sys.storage;
  const box = section("Speicherort der Musik",
    "Hier speichert Homify alle Songs – auch neue Downloads – und streamt sie von dort. Solange kein NAS eingestellt ist, bleibt alles im App-Ordner.");
  const status = h("div", { class: "storage-status" });
  const drawStatus = (i) => {
    const name = i.kind === "smb" ? "NAS" : i.mode === "folder" ? "Eigener Ordner" : "App-Ordner";
    status.replaceChildren(...[
      h("div", { class: "storage-head" }, icon(i.kind === "smb" ? "wifi" : "folder"),
        h("div", { style: { minWidth: 0 } },
          h("div", {}, h("strong", {}, `Aktuell: ${name} `), h("span", { class: `badge ${i.online ? "green" : "red"}` }, i.online ? "erreichbar" : "nicht erreichbar")),
          h("div", { class: "muted ellipsis", style: { fontSize: "13px" } },
            `${i.label} · ${plural(i.tracks, "Song", "Songs")}${i.free_bytes != null ? ` · ${fmtBytes(i.free_bytes)} frei` : ""}`))),
      !i.online ? h("p", { class: "error" }, i.message) : null].filter(Boolean));
  };
  drawStatus(info);

  let mode = s.storage_mode || "local";
  const inp = (value, attrs = {}) => h("input", { value: value ?? "", ...attrs });
  const nas = {
    host: inp(s.nas_host, { placeholder: "192.168.1.50 oder UGREEN-NAS" }),
    share: inp(s.nas_share, { placeholder: "z. B. Musik oder personal_folder" }),
    folder: inp(s.nas_folder, { placeholder: "z. B. Musik (leer = direkt in der Freigabe)" }),
    user: inp(s.nas_user, { autocomplete: "off" }),
    password: inp(s.nas_password, { type: "password", autocomplete: "new-password" }),
  };
  const pathInput = inp(s.storage_path, { placeholder: "/mnt/nas/musik oder D:\\Musik" });

  const choices = h("div", { class: "choice-list" });
  const nasBox = h("div", { class: "choice-details" },
    h("div", { class: "grid-2" },
      field("NAS-Adresse", nas.host, "IP-Adresse oder Name des NAS"),
      field("Freigegebener Ordner", nas.share, "Name der SMB-Freigabe")),
    h("div", { class: "grid-2" },
      field("Unterordner für Homify", nas.folder, "Wird angelegt, falls er fehlt"),
      h("div")),
    h("div", { class: "grid-2" }, field("Benutzername", nas.user), field("Passwort", nas.password)),
    h("details", { class: "help" }, h("summary", {}, "Hilfe für UGREEN-NAS"),
      h("ol", {},
        h("li", {}, "In UGOS Pro (Browser oder UGREEN-App) SMB aktivieren: Systemsteuerung → Dateidienste → SMB."),
        h("li", {}, "In der App „Dateien“ einen freigegebenen Ordner anlegen, z. B. „Musik“ – oder einen vorhandenen nehmen."),
        h("li", {}, "Dem Benutzer Lese- und Schreibrechte für diesen Ordner geben."),
        h("li", {}, "Die IP-Adresse des NAS steht in UGOS unter Systemsteuerung → Netzwerk (oder in deinem Router)."),
        h("li", {}, "Hier eintragen: Adresse, Freigabe = Name des freigegebenen Ordners, Benutzer + Passwort vom NAS.")),
      h("p", { class: "muted", style: { fontSize: "12px" } }, "Menünamen können je nach UGOS-Version leicht abweichen.")));
  const folderBox = h("div", { class: "choice-details" }, field("Ordner", pathInput),
    h("div", { class: "row-actions" }, h("button", { class: "btn btn-small btn-outline", type: "button", onclick: async () => {
      const picked = await folderPicker();
      if (picked) pathInput.value = picked;
    } }, icon("folder", "sm"), "Ordner wählen")));

  const transfer = h("input", { type: "checkbox", checked: info.tracks > 0 });
  const keepCopy = h("input", { type: "checkbox" });
  const transferBox = h("div", { class: "choice-details" },
    h("label", { class: "check" }, transfer, `Vorhandene Musik (${plural(info.tracks, "Song", "Songs")}) dorthin übertragen`),
    h("label", { class: "check" }, keepCopy, "Kopie am alten Ort behalten"),
    h("p", { class: "muted", style: { fontSize: "13px" } }, "Jede Datei wird kopiert und geprüft, erst dann am alten Ort gelöscht. Playlists und Lieblingssongs bleiben erhalten."));

  const result = h("p", { class: "muted", style: { fontSize: "13px", minHeight: "1em" } });
  const redraw = () => {
    choices.querySelectorAll(".choice").forEach((c) => c.classList.toggle("active", c.dataset.mode === mode));
    nasBox.hidden = mode !== "nas";
    folderBox.hidden = mode !== "folder";
    transferBox.hidden = !(mode !== (s.storage_mode || "local") || (mode === "nas" && nas.host.value !== s.nas_host)) || !info.tracks;
    result.textContent = "";
  };
  for (const [key, label, desc] of STORAGE_MODES) {
    const radio = h("input", { type: "radio", name: "storage-mode", value: key, checked: key === mode });
    radio.addEventListener("change", () => { mode = key; redraw(); });
    choices.append(h("label", { class: "choice", dataset: { mode: key } }, radio,
      h("div", {}, h("strong", {}, label), h("div", { class: "muted", style: { fontSize: "13px" } }, desc))));
  }
  const values = () => ({
    storage_mode: mode, storage_path: pathInput.value.trim(), nas_host: nas.host.value.trim(),
    nas_share: nas.share.value.trim(), nas_folder: nas.folder.value.trim(), nas_user: nas.user.value.trim(),
    nas_password: nas.password.value, transfer: transfer.checked, keep_copy: keepCopy.checked,
  });
  const testBtn = h("button", { class: "btn btn-small btn-outline", type: "button", onclick: async () => {
    result.replaceChildren(h("span", { class: "spinner" }), " Verbinde …");
    try {
      const r = await api("/storage/test", { method: "POST", body: values() });
      result.replaceChildren(h("span", { class: `badge ${r.ok ? "green" : "red"}` }, r.ok ? "OK" : "Fehler"), " ", r.message);
    } catch (e) { result.textContent = e.message; }
  } }, "Verbindung testen");
  const progress = h("div", { hidden: true });
  const applyBtn = h("button", { class: "btn btn-small btn-primary", type: "button", onclick: async () => {
    const v = values();
    const moving = !transferBox.hidden && v.transfer;
    if (moving && !await confirmDialog("Speicherort wechseln?",
      `Homify überträgt ${plural(info.tracks, "Song", "Songs")} an den neuen Ort${v.keep_copy ? "" : " und löscht sie danach am alten Ort"}. Das kann eine Weile dauern.`,
      { okLabel: "Übertragen" })) return;
    try {
      const r = await api("/storage/apply", { method: "POST", body: moving ? v : { ...v, transfer: false } });
      if (r.started) pollMigration();
      else { toast(r.message || "Gespeichert"); setTimeout(() => location.reload(), 800); }
    } catch (e) { result.textContent = e.message; toast(e.message, { error: true }); }
  } }, "Speicherort übernehmen");

  const pollMigration = async () => {
    const r = await api("/storage").catch(() => null);
    if (!r || !box.isConnected) return;
    const m = r.migration;
    progress.hidden = false;
    const pct = m.total_bytes ? Math.round((m.bytes / m.total_bytes) * 100) : 0;
    progress.replaceChildren(...[
      h("p", {}, h("strong", {}, m.running ? "Übertrage Musik …" : "Umzug beendet"), ` ${m.done + m.skipped + m.failed}/${m.total} Dateien · ${fmtBytes(m.bytes)} von ${fmtBytes(m.total_bytes)}`),
      h("div", { class: `progress ${m.total ? "" : "indeterminate"}` }, h("div", { style: { width: `${pct}%` } })),
      m.current ? h("p", { class: "muted ellipsis", style: { fontSize: "12px" } }, m.current) : null,
      m.message && !m.running ? h("p", {}, m.message) : null,
      m.errors?.length ? h("div", { class: "log" }, m.errors.join("\n")) : null,
      m.running ? h("button", { class: "btn btn-small btn-danger", onclick: () => api("/storage/cancel", { method: "POST" }) }, "Abbrechen") : null].filter(Boolean));
    applyBtn.disabled = m.running;
    if (m.running) setTimeout(pollMigration, 1000);
    else { drawStatus(r.storage); toast(m.message || "Fertig"); }
  };
  if (sys.migration?.running) pollMigration();

  box.append(extra(status, choices, nasBox, folderBox, transferBox, result,
    h("div", { class: "row-actions" }, testBtn, applyBtn), progress));
  redraw();
  return box;
}

// ================================================================ Zugriff & Tailscale
function remoteSection(sys) {
  const box = section("Zugriff von Handy, PC-App und unterwegs", null);
  const body = h("div");
  box.append(extra(body));
  const draw = (r) => {
    clear(body);
    body.append(h("p", { class: "muted" }, "Im Heimnetz (WLAN/LAN):"),
      h("div", { class: "url-list" }, sys.urls.length ? sys.urls.map((u) => h("div", {}, h("a", { href: u, target: "_blank", rel: "noopener" }, u))) : h("p", { class: "muted" }, "Keine Netzwerkadresse gefunden.")));
    body.append(h("p", { class: "muted", style: { marginTop: "16px" } }, "Unterwegs über Tailscale:"));
    if (!r.installed) {
      body.append(h("div", { class: "notice" }, icon("info"), h("div", {},
        h("strong", {}, "Tailscale ist auf diesem Rechner noch nicht installiert. "),
        "Tailscale verbindet deine Geräte sicher über das Internet – ohne Router-Einstellungen. ",
        "1. Auf diesem Rechner installieren (tailscale.com/download, unter Ubuntu macht das ubuntu/install.sh). ",
        "2. Auf dem Handy die Tailscale-App installieren und mit demselben Konto anmelden. ",
        "3. Diese Seite neu laden – hier erscheint dann die Adresse für unterwegs.")));
    } else if (!r.running) {
      body.append(h("p", { class: "error" }, r.message || "Tailscale ist nicht verbunden."));
    } else {
      body.append(h("div", { class: "url-list" }, r.urls.map((u) => h("div", {}, h("a", { href: u, target: "_blank", rel: "noopener" }, u),
        u.startsWith("https") ? h("span", { class: "badge green", style: { marginLeft: "8px" } }, "empfohlen für die Apps") : null))));
      if (!r.https_url) {
        const btn = h("button", { class: "btn btn-small btn-primary", onclick: async () => {
          btn.disabled = true;
          const res = await api("/remote/https", { method: "POST" }).catch((e) => ({ ok: false, message: e.message }));
          toast(res.message, { error: !res.ok, ms: 6000 });
          if (res.info) draw(res.info);
          btn.disabled = false;
        } }, icon("lock", "sm"), "HTTPS-Adresse einrichten");
        body.append(h("p", { class: "muted", style: { fontSize: "13px" } },
          "Tipp: Mit einer HTTPS-Adresse (https://…ts.net) funktionieren die Apps am zuverlässigsten – auch zuhause, solange Tailscale an ist."),
        h("div", { class: "row-actions" }, btn));
      }
    }
    body.append(h("p", { class: "muted", style: { fontSize: "13px", marginTop: "12px" } },
      "Die Adressen trägst du in der Android- und Windows-App ein (Seite ", h("a", { href: "#/apps", class: "link", style: { color: "var(--accent)" } }, "Apps"), ")."));
  };
  draw(sys.remote || {});
  return box;
}

// ================================================================ Sicherung
function backupSection(sys) {
  const file = h("input", { type: "file", accept: ".zip,application/zip", hidden: true });
  file.addEventListener("change", async () => {
    const f = file.files[0];
    file.value = "";
    if (!f) return;
    if (!await confirmDialog("Sicherung einspielen?", "Benutzer, Playlists, Lieblingssongs, Verlauf und Einstellungen werden durch die Sicherung ersetzt. Der Speicherort (NAS) bleibt wie er ist.", { okLabel: "Einspielen", danger: true })) return;
    try {
      const res = await fetch("/api/backup/restore?keep_storage=true", { method: "POST", body: f, credentials: "same-origin", headers: { "content-type": "application/zip" } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Fehler beim Einspielen");
      toast("Sicherung eingespielt – bitte neu anmelden");
      setTimeout(() => location.reload(), 1200);
    } catch (e) { toast(e.message, { error: true }); }
  });
  const list = h("div", { class: "backup-list" });
  const drawList = (backups) => {
    clear(list);
    if (!backups?.length) { list.append(h("p", { class: "muted", style: { fontSize: "13px" } }, "Noch keine gespeicherten Sicherungen auf dem Server.")); return; }
    list.append(h("p", { class: "muted", style: { fontSize: "13px", margin: "12px 0 4px" } }, "Gespeicherte Sicherungen auf dem Server (data/backups):"));
    for (const b of backups) {
      list.append(h("div", { class: "list-row", style: { cursor: "default" } }, icon("album"),
        h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { class: "ellipsis" }, b.name),
          h("div", { class: "muted", style: { fontSize: "12px" } }, `${fmtDate(b.created)} · ${fmtBytes(b.size)}`)),
        h("a", { class: "btn btn-small btn-outline", href: `/api/backups/${encodeURIComponent(b.name)}`, download: "" }, icon("download", "sm"), "Laden")));
    }
  };
  drawList(sys.backups);
  return section("Sicherung & Umzug auf den Server",
    "Nimm Benutzer, Playlists, Lieblingssongs und Einstellungen vom Windows-Test-PC mit auf den Ubuntu-Homeserver: hier herunterladen, dort einspielen. Die Musik selbst liegt auf dem NAS bzw. wird über „Speicherort“ übertragen. Tägliche Sicherung und Anzahl stellst du unter „Server & Sicherheit“ ein.",
    extra(
      h("div", { class: "row-actions" },
        h("a", { class: "btn btn-small btn-outline", href: "/api/backup", download: "" }, icon("download", "sm"), "Sicherung herunterladen"),
        h("button", { class: "btn btn-small btn-outline", onclick: async () => {
          try {
            const r = await api("/backups", { method: "POST" });
            drawList(r.backups);
            toast("Sicherung erstellt");
          } catch (e) { toast(e.message, { error: true }); }
        } }, "Jetzt auf dem Server sichern"),
        h("button", { class: "btn btn-small btn-outline", onclick: () => file.click() }, "Sicherung einspielen …"), file),
      list));
}

// ================================================================ Apps (für alle Benutzer)
const RELEASES = "https://github.com/ErikEdits/Homelab-spotify-/releases/latest";

export async function appsView() {
  const info = await api("/server-info").catch(() => ({ lan: [], remote: [] }));
  const addresses = [...(info.remote || []), ...(info.lan || [])];
  const isLocal = /^(localhost|127\.|\[::1\])/.test(location.hostname);
  if (!isLocal && !addresses.includes(location.origin)) addresses.unshift(location.origin);
  const addrList = h("div", { class: "url-list" }, addresses.map((u) => h("div", { class: "addr-row" },
    h("code", {}, u),
    h("button", { class: "btn btn-small btn-outline", onclick: async () => {
      try { await navigator.clipboard.writeText(u); toast("Adresse kopiert"); } catch { toast(u); }
    } }, "Kopieren"))));
  const inApp = window.HomifyAndroid || window.homifyDesktop;
  const root = h("div", { class: "view-inner" }, h("div", { class: "settings" },
    h("h1", { class: "page-title" }, "Homify-Apps"),
    inApp ? h("div", { class: "notice" }, icon("check"), h("div", {}, "Du nutzt gerade die Homify-App. ",
      h("button", { class: "btn btn-small btn-outline", onclick: () => (window.HomifyAndroid?.openSettings || window.homifyDesktop?.openSettings)?.() }, "Server-Adresse ändern"))) : null,
    section("Server-Adresse", "Diese Adresse trägst du beim ersten Start der App ein. Die https://…ts.net-Adresse funktioniert zuhause und unterwegs (Tailscale muss auf dem Gerät an sein).", addrList),
    section("Android", "Echte App mit Wiedergabe im Hintergrund, Steuerung in der Benachrichtigung und auf dem Sperrbildschirm.",
      h("div", { class: "row-actions" }, h("a", { class: "btn btn-primary", href: `${RELEASES}/download/Homify.apk` }, icon("download", "sm"), "Android-App (APK) laden")),
      h("ol", { class: "steps" },
        h("li", {}, "Auf dem Handy auf den Knopf tippen und die Datei „Homify.apk“ öffnen."),
        h("li", {}, "Wenn Android fragt: „Installation aus dieser Quelle zulassen“ erlauben."),
        h("li", {}, "Homify öffnen, Server-Adresse von oben eintragen, anmelden – fertig."),
        h("li", {}, "Für unterwegs: die Tailscale-App installieren und anmelden."))),
    section("Windows", "Eigenes Programm mit Startmenü-Eintrag, Taskleisten-Icon und Medientasten.",
      h("div", { class: "row-actions" }, h("a", { class: "btn btn-primary", href: `${RELEASES}/download/Homify-Setup.exe` }, icon("download", "sm"), "Windows-App laden")),
      h("ol", { class: "steps" },
        h("li", {}, "„Homify-Setup.exe“ starten. Falls Windows warnt (unbekannter Herausgeber): „Weitere Informationen“ → „Trotzdem ausführen“."),
        h("li", {}, "Beim ersten Start die Server-Adresse eintragen."))),
    section("Website", "Geht immer ohne Installation: einfach die Server-Adresse im Browser öffnen.",
      h("p", { class: "muted", style: { fontSize: "13px" } }, h("a", { href: RELEASES, target: "_blank", rel: "noopener", class: "link" }, "Alle Versionen auf GitHub")))));
  return { el: root, color: "#1e3264" };
}
