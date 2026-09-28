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

  // Zugriff
  wrap.append(section("Zugriff von anderen Geräten",
    "Öffne eine dieser Adressen auf Handy, Laptop oder Fernseher im selben Netzwerk. Tipp: Im Handy-Browser „Zum Startbildschirm hinzufügen“ – dann wirkt Homify wie eine App.",
    h("div", { class: "url-list" }, sys.urls.length ? sys.urls.map((u) => h("div", {}, h("a", { href: u, target: "_blank", rel: "noopener" }, u))) : h("p", { class: "muted" }, "Keine Netzwerkadresse gefunden.")),
    h("p", { class: "muted", style: { fontSize: "13px", marginTop: "12px" } },
      "Von unterwegs: am sichersten über ein VPN wie Tailscale oder WireGuard (siehe README). Öffne den Port nicht ungeschützt ins Internet.")));

  // Bibliothek
  const dirs = h("textarea", { rows: "3", placeholder: "\\\\NAS\\Musik\nZ:\\Musik\n/mnt/nas/musik" }, (s.music_dirs || []).join("\n"));
  inputs.music_dirs = dirs;
  const dirStatus = h("div", {}, sys.music_dirs.map((d) => h("div", { style: { fontSize: "13px", display: "flex", gap: "8px", alignItems: "center" } },
    h("span", { class: `badge ${d.online ? "green" : "red"}` }, d.online ? "erreichbar" : "nicht erreichbar"), d.path)));
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
  wrap.append(section("Bibliothek (NAS / Musikordner)",
    "Ein Ordner pro Zeile. Unter Windows z. B. \\\\NAS\\Musik oder ein verbundenes Laufwerk wie Z:\\Musik, unter Ubuntu z. B. /mnt/nas/musik. Titel, Künstler und Cover liest Homify direkt aus den Dateien.",
    field("Musikordner", dirs), dirStatus,
    h("div", { class: "row-actions", style: { marginBottom: "12px" } }, browseBtn),
    field("Automatisch neu scannen alle … Minuten", text("scan_interval_minutes", { type: "number", min: "0", max: "1440" }), "0 = nur manuell"),
    scanInfo,
    h("div", { class: "row-actions" },
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { drawScan(await api("/library/scan", { method: "POST" })); pollScan(); } }, icon("refresh", "sm"), "Jetzt scannen"),
      h("button", { class: "btn btn-small btn-outline", onclick: async () => { drawScan(await api("/library/scan?full=true", { method: "POST" })); pollScan(); } }, "Alles neu einlesen"))));
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
      h("dt", {}, "Speichert nach"), h("dd", {}, sys.download_dir));
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
    field("Download-Ordner", text("download_dir", { placeholder: sys.download_dir }), "Leer = erster Musikordner. Muss für Homify beschreibbar sein."),
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
  wrap.append(h("div", { style: { position: "sticky", bottom: "0", padding: "16px 0", background: "linear-gradient(transparent, var(--panel) 40%)", zIndex: 3 } }, saveBtn));

  // Benutzer
  wrap.append(await usersSection());
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
