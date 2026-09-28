// Einstellungen: Wiedergabe, Konto, Bibliothek/NAS, spotDL, Benutzer, System

import { api, state } from "./api.js";
import { player } from "./player.js";
import { clear, confirmDialog, fmtBytes, fmtDuration, h, icon, modal, plural, toast } from "./ui.js";

function section(title, desc, ...children) {
  return h("section", { class: "settings-section" }, h("h2", {}, title), desc ? h("p", { class: "muted" }, desc) : null, ...children);
}

function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", {}, hint) : null);
}

export async function settingsView() {
  const root = h("div", { class: "view-inner" });
  const wrap = h("div", { class: "settings" }, h("h1", { class: "page-title" }, "Einstellungen"));
  root.append(wrap);

  // ---------------------------------------------------- Wiedergabe (pro Gerät)
  const quality = h("select", { class: "input" },
    h("option", { value: "original", selected: player.quality === "original" }, "Original (beste Qualität)"),
    h("option", { value: "low", selected: player.quality === "low" }, "Datensparend (128 kbit/s – für unterwegs)"));
  quality.addEventListener("change", () => { player.setQuality(quality.value); toast("Gilt ab dem nächsten Song"); });
  wrap.append(section("Wiedergabe", "Diese Einstellung gilt nur für dieses Gerät.",
    field("Streaming-Qualität", quality, "Formate, die dein Browser nicht kann (z. B. WMA, ALAC, APE), wandelt Homify automatisch um.")));

  // ---------------------------------------------------- Konto
  const oldPw = h("input", { type: "password", autocomplete: "current-password" });
  const newPw = h("input", { type: "password", autocomplete: "new-password", minlength: "4" });
  wrap.append(section("Konto", `Angemeldet als ${state.user.username}${state.user.is_admin ? " (Admin)" : ""}.`,
    h("div", { class: "grid-2" }, field("Altes Passwort", oldPw), field("Neues Passwort", newPw)),
    h("div", { class: "row-actions" },
      h("button", { class: "btn btn-small btn-outline", onclick: async () => {
        try {
          await api("/auth/password", { method: "POST", body: { old_password: oldPw.value, new_password: newPw.value } });
          oldPw.value = newPw.value = "";
          toast("Passwort geändert");
        } catch (e) { toast(e.message, { error: true }); }
      } }, "Passwort ändern"),
      h("button", { class: "btn btn-small btn-danger", onclick: async () => { await api("/auth/logout", { method: "POST" }); location.reload(); } }, icon("logout", "sm"), "Abmelden"))));

  if (!state.user.is_admin) return { el: root, color: "#2a2a2a" };

  // ---------------------------------------------------- Admin-Bereich
  const data = await api("/settings");
  const s = data.settings;
  const sys = data.system;
  const inputs = {};
  const text = (key, attrs = {}) => (inputs[key] = h("input", { value: s[key] ?? "", ...attrs }));

  // Speicherort (App-Ordner / NAS / eigener Ordner)
  wrap.append(storageSection(sys, s));

  // Zugriff im Heimnetz + unterwegs (Tailscale)
  wrap.append(remoteSection(sys));

  // Bibliothek / Scan
  const dirs = h("textarea", { rows: "2", placeholder: "D:\\Alte Musik\n/mnt/usb/musik" }, (s.music_dirs || []).join("\n"));
  inputs.music_dirs = dirs;
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
  const browseBtn = h("button", { class: "btn btn-small btn-outline", type: "button", onclick: async () => {
    const picked = await folderPicker();
    if (picked) dirs.value = (dirs.value.trim() ? dirs.value.trim() + "\n" : "") + picked;
  } }, icon("folder", "sm"), "Ordner wählen");
  wrap.append(section("Bibliothek",
    "Homify liest Titel, Künstler und Cover direkt aus den Dateien und findet neue Songs automatisch.",
    scanInfo,
    h("div", { class: "row-actions", style: { marginBottom: "16px" } },
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { drawScan(await api("/library/scan", { method: "POST" })); pollScan(); } }, icon("refresh", "sm"), "Jetzt scannen"),
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { drawScan(await api("/library/scan?full=true", { method: "POST" })); pollScan(); } }, "Alles neu einlesen")),
    field("Automatisch neu scannen alle … Minuten", text("scan_interval_minutes", { type: "number", min: "0", max: "1440" }), "0 = nur manuell"),
    h("details", {}, h("summary", { style: { cursor: "pointer", fontWeight: 700, margin: "8px 0 12px" } }, "Zusätzliche Ordner (nur lesen)"),
      h("p", { class: "muted", style: { fontSize: "13px" } }, "Optional: weitere Ordner, deren Musik Homify nur abspielt (z. B. eine alte Sammlung). Neue Downloads landen immer im Speicherort oben."),
      field("Ordner (einer pro Zeile)", dirs),
      h("div", { class: "row-actions" }, browseBtn))));
  let scanTimer;
  const pollScan = async () => {
    clearTimeout(scanTimer);
    const st = await api("/status").catch(() => null);
    if (!st || !root.isConnected) return;
    if (!st.scan.running) sys.stats = (await api("/settings")).system.stats;
    drawScan(st.scan);
    if (st.scan.running) scanTimer = setTimeout(pollScan, 1500);
  };
  if (sys.scan.running) pollScan();

  // spotDL
  const spotdlInfo = h("div");
  const logBox = h("div", { class: "log", hidden: true });
  const installBtn = h("button", { class: "btn btn-small btn-primary" }, icon("download", "sm"), sys.spotdl_installed ? "spotDL aktualisieren" : "spotDL installieren");
  const drawTools = (installed, versions, tools) => {
    clear(spotdlInfo);
    const dl = h("dl", { class: "kv" },
      h("dt", {}, "spotDL"), h("dd", {}, installed ? (versions?.spotdl || "installiert") : h("span", { class: "badge red" }, "nicht installiert")),
      h("dt", {}, "yt-dlp"), h("dd", {}, versions?.["yt-dlp"] || "–"),
      h("dt", {}, "ffmpeg"), h("dd", {}, sys.ffmpeg || h("span", { class: "badge red" }, "nicht gefunden")),
      h("dt", {}, "Speichert nach"), h("dd", {}, sys.storage.label));
    spotdlInfo.append(dl);
    if (tools?.log?.length) {
      logBox.hidden = false;
      logBox.textContent = tools.log.join("\n");
      logBox.scrollTop = logBox.scrollHeight;
    }
    installBtn.disabled = !!tools?.running;
    if (tools?.running) installBtn.replaceChildren(h("span", { class: "spinner" }), "Läuft …");
  };
  drawTools(sys.spotdl_installed, sys.spotdl, sys.tools);
  let toolTimer;
  const pollTools = async () => {
    clearTimeout(toolTimer);
    const st = await api("/status").catch(() => null);
    if (!st || !root.isConnected) return;
    drawTools(sys.spotdl_installed, sys.spotdl, st.tools);
    if (st.tools.running) toolTimer = setTimeout(pollTools, 1200);
    else {
      const fresh = await api("/settings");
      Object.assign(sys, fresh.system);
      drawTools(sys.spotdl_installed, sys.spotdl, st.tools);
      installBtn.replaceChildren(icon("download", "sm"), sys.spotdl_installed ? "spotDL aktualisieren" : "spotDL installieren");
      installBtn.disabled = false;
      toast(st.tools.ok ? "spotDL ist bereit" : "spotDL-Installation fehlgeschlagen – siehe Protokoll", { error: !st.tools.ok });
    }
  };
  installBtn.addEventListener("click", async () => {
    await api("/system/spotdl", { method: "POST" });
    installBtn.disabled = true;
    installBtn.replaceChildren(h("span", { class: "spinner" }), "Läuft …");
    pollTools();
  });
  if (sys.tools?.running) pollTools();

  const format = h("select", {}, ["mp3", "m4a", "opus", "flac", "ogg"].map((f) => h("option", { value: f, selected: s.download_format === f }, f)));
  inputs.download_format = format;
  const bitrate = h("select", {}, ["auto", "disable", "128k", "192k", "256k", "320k"].map((b) => h("option", { value: b, selected: s.download_bitrate === b }, b)));
  inputs.download_bitrate = bitrate;
  const official = h("input", { type: "checkbox", checked: !!s.spotify_use_official_api });
  inputs.spotify_use_official_api = official;

  wrap.append(section("Downloads (spotDL)",
    "Fehlt ein Song, holt Homify ihn mit spotDL: die Infos (Titel, Cover, Album) kommen von Spotify, das Audio von YouTube Music. spotDL läuft in einer eigenen Python-Umgebung und lässt sich hier mit einem Klick aktualisieren – wichtig, wenn Downloads plötzlich nicht mehr klappen.",
    spotdlInfo, h("div", { class: "row-actions", style: { marginBottom: "12px" } }, installBtn), logBox,
    field("Dateinamen-Vorlage", text("output_template"), "Variablen: {artists} {artist} {title} {album} {album-artist} {track-number} {year} {output-ext}"),
    h("div", { class: "grid-2" },
      field("Format", format, "mp3 läuft überall"),
      field("Bitrate", bitrate, "auto = wie die Quelle")),
    field("Gleichzeitige Downloads (Threads)", text("download_threads", { type: "number", min: "1", max: "8" })),
    field("Cookie-Datei für YouTube (optional)", text("spotdl_cookie_file", { placeholder: "C:\\Users\\du\\cookies.txt" }), "Hilft, wenn YouTube „Bestätige, dass du kein Bot bist“ meldet."),
    field("Zusätzliche spotDL-Argumente (optional)", text("spotdl_extra_args", { placeholder: "--sponsor-block" })),
    h("details", {}, h("summary", { style: { cursor: "pointer", fontWeight: 700, margin: "8px 0 12px" } }, "Eigene Spotify-API-Zugangsdaten (optional)"),
      h("p", { class: "muted", style: { fontSize: "13px" } }, "Normalerweise nicht nötig. Falls Spotify-Anfragen fehlschlagen, kannst du unter developer.spotify.com eine eigene App anlegen und die Daten hier eintragen."),
      h("div", { class: "grid-2" }, field("Client ID", text("spotify_client_id")), field("Client Secret", text("spotify_client_secret", { type: "password" }))),
      h("label", { class: "check" }, official, "Offizielle Spotify-API verwenden"))));

  // System
  wrap.append(section("System", null,
    h("dl", { class: "kv" },
      h("dt", {}, "Homify-Version"), h("dd", {}, sys.version),
      h("dt", {}, "Rechner"), h("dd", {}, sys.hostname),
      h("dt", {}, "Umwandlungs-Cache"), h("dd", {}, `${sys.cache_mb} MB`)),
    field("Max. Größe des Umwandlungs-Caches (MB)", text("transcode_cache_mb", { type: "number", min: "100" })),
    field("Eigener ffmpeg-Pfad (optional)", text("ffmpeg_path", { placeholder: "leer = mitgeliefertes ffmpeg" })),
    field("Port", text("port", { type: "number", min: "1", max: "65535" }), "Änderung wirkt nach Neustart von Homify."),
    h("div", { class: "row-actions" },
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { await api("/system/cache", { method: "DELETE" }); toast("Cache geleert"); } }, "Cache leeren"),
      h("button", { class: "btn btn-small btn-outline", onclick: async () => {
        if (!await confirmDialog("Homify neu starten?", "Die Wiedergabe wird kurz unterbrochen.", { okLabel: "Neu starten" })) return;
        await api("/system/restart", { method: "POST" });
        toast("Homify startet neu …", { ms: 6000 });
        const wait = async () => { try { await api("/setup"); location.reload(); } catch { setTimeout(wait, 1000); } };
        setTimeout(wait, 2500);
      } }, icon("refresh", "sm"), "Neu starten"),
      h("button", { class: "btn btn-small btn-danger", onclick: async () => {
        if (!await confirmDialog("Homify beenden?", "Der Server wird gestoppt. Danach ist Homify auf keinem Gerät mehr erreichbar, bis du es wieder startest.", { okLabel: "Beenden", danger: true })) return;
        await api("/system/shutdown", { method: "POST" });
        toast("Homify wurde beendet.", { ms: 8000 });
      } }, "Homify beenden"))));

  // Speichern-Leiste
  const saveBtn = h("button", { class: "btn btn-primary", onclick: async () => {
    const values = {};
    for (const [key, el] of Object.entries(inputs)) {
      values[key] = el.type === "checkbox" ? el.checked : key === "music_dirs" ? el.value.split("\n").map((x) => x.trim()).filter(Boolean) : el.value;
    }
    try {
      await api("/settings", { method: "PUT", body: values });
      toast("Gespeichert");
      setTimeout(() => location.reload(), 600);
    } catch (e) { toast(e.message, { error: true }); }
  } }, "Einstellungen speichern");
  wrap.append(h("div", { style: { position: "sticky", bottom: "0", marginTop: "8px", padding: "16px 0", background: "linear-gradient(transparent, var(--panel) 40%)", zIndex: 3 } }, saveBtn));

  // Benutzer
  wrap.append(await usersSection());
  wrap.append(backupSection());
  return { el: root, color: "#2a2a2a", destroy: () => { clearTimeout(scanTimer); clearTimeout(toolTimer); } };
}

async function usersSection() {
  const box = section("Benutzer", "Jeder Benutzer hat eigene Playlists, Lieblingssongs und Verlauf. Die Bibliothek teilen sich alle.");
  const list = h("div");
  box.append(list);
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
  box.append(h("div", { class: "grid-2", style: { marginTop: "16px" } }, field("Neuer Benutzer", name), field("Passwort", pw)),
    h("label", { class: "check" }, admin, "Admin (darf Einstellungen ändern)"),
    h("button", { class: "btn btn-small btn-outline", onclick: async () => {
      try {
        await api("/users", { method: "POST", body: { username: name.value, password: pw.value, is_admin: admin.checked, can_download: true } });
        name.value = pw.value = "";
        admin.checked = false;
        toast("Benutzer angelegt");
        draw();
      } catch (e) { toast(e.message, { error: true }); }
    } }, icon("plus", "sm"), "Benutzer anlegen"));
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
  box.append(status);

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
  const result = h("p", { class: "muted", style: { fontSize: "13px", minHeight: "1em" } });
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

  box.append(choices, nasBox, folderBox, transferBox, result,
    h("div", { class: "row-actions" }, testBtn, applyBtn), progress);
  redraw();
  return box;
}

// ================================================================ Zugriff & Tailscale
function remoteSection(sys) {
  const box = section("Zugriff von Handy, PC-App und unterwegs", null);
  const body = h("div");
  box.append(body);
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
      return;
    }
    if (!r.running) {
      body.append(h("p", { class: "error" }, r.message || "Tailscale ist nicht verbunden."));
      return;
    }
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
  };
  draw(sys.remote || {});
  box.append(h("p", { class: "muted", style: { fontSize: "13px", marginTop: "12px" } },
    "Die Adressen trägst du in der Android- und Windows-App ein (Seite ", h("a", { href: "#/apps", class: "link", style: { color: "var(--accent)" } }, "Apps"), ")."));
  return box;
}

// ================================================================ Sicherung
function backupSection() {
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
  return section("Sicherung & Umzug auf den Server",
    "Nimm Benutzer, Playlists, Lieblingssongs und Einstellungen vom Windows-Test-PC mit auf den Ubuntu-Homeserver: hier herunterladen, dort unter Einstellungen einspielen. Die Musik selbst liegt auf dem NAS bzw. wird über „Speicherort“ übertragen.",
    h("div", { class: "row-actions" },
      h("a", { class: "btn btn-small btn-outline", href: "/api/backup", download: "" }, icon("download", "sm"), "Sicherung herunterladen"),
      h("button", { class: "btn btn-small btn-outline", onclick: () => file.click() }, "Sicherung einspielen …"), file));
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
