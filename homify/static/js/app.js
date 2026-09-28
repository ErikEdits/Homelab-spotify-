// Einstieg: Anmeldung, Router, Seitenleiste, Player-Leiste, Warteschlange, Vollbild-Player

import { api, loadPlaylists, on, prefs, setUnauthorizedHandler, state } from "./api.js";
import { artistLinks, likeButton, navigate, playlistSubtitle, trackMenu } from "./components.js";
import { downloads, downloadsView, refreshDownloads } from "./downloads.js";
import { lyricsView } from "./lyricsview.js";
import { player } from "./player.js";
import { generatePlaylistDialog } from "./playlistgen.js";
import { searchView } from "./search.js";
import { appsView, settingsView } from "./settings.js";
import {
  clear, closeMenu, cover, coverUrl, fmtTime, h, hydrateIcons, icon, logoSvg, makeSlider, mosaic, openMenu, toast,
} from "./ui.js";
import { applyAppearance, loadSettings, saveSettings, setting } from "./usersettings.js";
import {
  albumView, artistView, createPlaylist, emptyState, homeView, libraryView, likedView, mixView, playlistView,
} from "./views.js";

const $ = (id) => document.getElementById(id);
const START_PAGES = { home: "#/", search: "#/search", library: "#/library", liked: "#/liked", downloads: "#/downloads" };
const serverName = () => state.user?.server?.name || prefs.get("serverName", "Homify");

// ================================================================ Anmeldung
async function boot() {
  applyAppearance();  // zuletzt benutztes Design sofort (auch auf der Anmeldeseite)
  hydrateIcons();
  document.querySelectorAll(".brand-logo, .auth-logo").forEach((el) => el.append(logoSvg()));
  setUnauthorizedHandler(() => showAuth(false));
  try {
    state.user = await api("/auth/me");
  } catch {
    const setup = await api("/setup").catch(() => ({ needs_setup: false }));
    if (setup.name) prefs.set("serverName", setup.name);
    showAuth(setup.needs_setup);
    return;
  }
  prefs.set("serverName", serverName());
  await loadSettings().catch(() => {});
  startApp();
}

function showAuth(needsSetup) {
  $("app").hidden = true;
  $("auth").hidden = false;
  player.pause();
  const form = $("auth-form");
  const name = serverName();
  document.title = name;
  $("auth-title").textContent = needsSetup ? `Willkommen bei ${name}!` : `Bei ${name} anmelden`;
  $("auth-hint").textContent = needsSetup ? "Lege dein Admin-Konto an. Damit meldest du dich später auf allen Geräten an." : "";
  $("auth-confirm").hidden = !needsSetup;
  form.password.autocomplete = needsSetup ? "new-password" : "current-password";
  $("auth-submit").textContent = needsSetup ? "Konto erstellen" : "Anmelden";
  form.onsubmit = async (e) => {
    e.preventDefault();
    $("auth-error").textContent = "";
    const body = { username: form.username.value.trim(), password: form.password.value };
    if (needsSetup && body.password !== form.confirm.value) {
      $("auth-error").textContent = "Die Passwörter stimmen nicht überein.";
      return;
    }
    if (needsSetup && body.password.length < 4) {
      $("auth-error").textContent = "Das Passwort muss mindestens 4 Zeichen haben.";
      return;
    }
    try {
      await api(needsSetup ? "/setup" : "/auth/login", { method: "POST", body });
      location.hash = needsSetup ? "#/settings" : location.hash;
      location.reload();
    } catch (err) {
      $("auth-error").textContent = err.message;
    }
  };
  setTimeout(() => form.username.focus(), 50);
}

// ================================================================ App
let started = false;
function startApp() {
  if (started) return;
  started = true;
  $("auth").hidden = true;
  $("app").hidden = false;
  document.querySelectorAll(".brand span:last-child").forEach((el) => { el.textContent = serverName(); });
  // Einstellung „Beim Öffnen anzeigen“
  const start = START_PAGES[setting("start_page")];
  if (["", "#", "#/"].includes(location.hash) && start && start !== "#/") history.replaceState(null, "", start);
  setupPlayerBar();
  setupShell();
  setupNativeBridge();
  loadPlaylists().then(renderSidebar).catch(() => {});
  refreshDownloads();
  player.restore();
  window.addEventListener("hashchange", route);
  route();
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

// ================================================================ Router
const routes = [
  [/^#?\/?$/, homeView, "home"],
  [/^#\/search/, searchView, "search"],
  [/^#\/library/, libraryView, "library"],
  [/^#\/liked$/, likedView, "liked"],
  [/^#\/downloads$/, downloadsView, "downloads"],
  [/^#\/settings$/, settingsView, "settings"],
  [/^#\/apps$/, appsView, "apps"],
  [/^#\/lyrics$/, lyricsView, "lyrics"],
  [/^#\/album\/(?<id>[^/?]+)/, albumView, "album"],
  [/^#\/artist\/(?<id>[^/?]+)/, artistView, "artist"],
  [/^#\/playlist\/(?<id>\d+)/, playlistView, "playlist"],
  [/^#\/mix\/(?<kind>[a-z]+)\/?(?<value>[^?]*)/, mixView, "mix"],
];

let current = null;
let routeSeq = 0;
const scrollPositions = new Map();

async function route() {
  closeMenu();
  const hash = location.hash || "#/";
  const [path, qs] = hash.split("?");
  const query = new URLSearchParams(qs || "");
  const match = routes.find(([re]) => re.test(path));
  const view = $("view");
  const main = $("main");
  if (current) {
    scrollPositions.set(current.hash, view.scrollTop);
    try { current.destroy?.(); } catch (e) { console.error(e); }
    current = null;
  }
  const seq = ++routeSeq;
  const name = match?.[2] || "notfound";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  $("btn-lyrics").classList.toggle("active", name === "lyrics");
  $("topbar-search").hidden = name !== "search";
  if (name !== "search") $("search-input").value = "";
  renderSidebar();
  if (!match) {
    clear(view).append(h("div", { class: "view-inner" }, emptyState("alert", "Seite nicht gefunden", "", h("a", { class: "btn btn-primary", href: "#/" }, "Zur Startseite"))));
    return;
  }
  const params = path.match(match[0]).groups || {};
  let result;
  try {
    result = await match[1](params, query);
  } catch (err) {
    if (seq !== routeSeq) return;
    console.error(err);
    result = { el: h("div", { class: "view-inner" }, emptyState("alert", "Das hat nicht geklappt", err.message, h("button", { class: "btn btn-primary", onclick: route }, "Nochmal versuchen"))) };
  }
  if (seq !== routeSeq) { result?.destroy?.(); return; }
  current = { ...result, hash };
  main.style.setProperty("--header-color", setting("dynamic_colors") !== false ? (result.color || "#535353") : "#404040");
  clear(view);
  view.append(h("div", { class: "view-bg" }), result.el);
  document.title = result.title ? `${result.title} – ${serverName()}` : serverName();
  view.scrollTop = scrollPositions.get(hash) || 0;
  onScroll();
  highlightPlaying();
}

function onScroll() {
  $("topbar").classList.toggle("solid", $("view").scrollTop > 40);
}

// ================================================================ Gerüst
function setupShell() {
  $("view").addEventListener("scroll", onScroll, { passive: true });
  $("nav-back").addEventListener("click", () => history.back());
  $("nav-forward").addEventListener("click", () => history.forward());
  $("new-playlist").addEventListener("click", (e) => openMenu(e, [
    { label: "Neue Playlist", icon: "plus", action: createPlaylist },
    { label: "Playlist zusammenstellen …", icon: "sparkle", action: () => generatePlaylistDialog() },
  ]));
  $("user-btn").addEventListener("click", (e) => openMenu(e, [
    { title: state.user.username },
    { label: "Einstellungen", icon: "settings", action: () => navigate("#/settings") },
    { label: setting("private_session") ? "Private Sitzung beenden" : "Private Sitzung starten", icon: "lock", action: async () => {
      const enable = !setting("private_session");
      await saveSettings({ private_session: enable });
      toast(enable ? "Private Sitzung: nichts wird im Verlauf gespeichert" : "Private Sitzung beendet");
    } },
    { label: "Downloads", icon: "download", action: () => navigate("#/downloads") },
    { label: "Lieblingssongs", icon: "heart", action: () => navigate("#/liked") },
    { label: window.HomifyAndroid || window.homifyDesktop ? "Apps & Server-Adresse" : "Apps für Handy & PC", icon: "download", action: () => navigate("#/apps") },
    (window.HomifyAndroid?.openSettings || window.homifyDesktop?.openSettings)
      ? { label: "Server wechseln", icon: "wifi", action: () => (window.HomifyAndroid?.openSettings || window.homifyDesktop.openSettings)() } : null,
    "sep",
    { label: "Abmelden", icon: "logout", action: async () => { await api("/auth/logout", { method: "POST" }); player.pause(); location.reload(); } },
  ]));
  on("playlists", renderSidebar);
  on("playlist-changed", () => loadPlaylists());
  on("downloads", (d) => {
    $("downloads-indicator").hidden = !d.active;
    $("downloads-count").textContent = String(d.active);
  });
  on("library-changed", () => {
    if (["#/", "", "#/library", "#/liked"].includes(location.hash.split("?")[0])) route();
  });
  on("settings", ({ keys }) => {
    $("btn-lyrics").hidden = !setting("show_lyrics_button");
    $("user-btn").title = setting("private_session") ? "Konto – private Sitzung aktiv" : "Konto";
    renderTime(player.time());
    if (keys.includes("dynamic_colors") && player.current) fetchColor(player.current);
  });
  $("btn-lyrics").hidden = !setting("show_lyrics_button");
  $("user-btn").title = setting("private_session") ? "Konto – private Sitzung aktiv" : "Konto";
  document.addEventListener("keydown", onShortcut);
}

function toggleLyrics() {
  if (location.hash === "#/lyrics") history.back();
  else navigate("#/lyrics");
}

function renderSidebar() {
  const list = $("sidebar-list");
  if (!list) return;
  const hash = location.hash;
  clear(list);
  const item = (href, coverEl, name, sub) => h("a", { class: `side-item ${hash === href ? "active" : ""}`, href },
    coverEl, h("div", { class: "text", style: { minWidth: 0 } }, h("div", { class: "name ellipsis" }, name), h("div", { class: "sub ellipsis" }, sub)));
  list.append(
    item("#/liked", h("div", { class: "cover liked" }, icon("heart")), "Lieblingssongs", "Playlist"),
    item("#/downloads", h("div", { class: "cover downloads" }, icon("download")), "Downloads", downloads.active ? `${downloads.active} aktiv` : "Von Spotify holen"),
  );
  for (const p of state.playlists) {
    list.append(item(`#/playlist/${p.id}`, mosaic(p.covers, { size: 96 }), p.name, `Playlist · ${playlistSubtitle(p)}`));
  }
}

// ================================================================ Player-Leiste
let seekSlider, volumeSlider, fpSeek;

function setupPlayerBar() {
  seekSlider = makeSlider($("seek"), {
    onInput: (v) => { $("time-cur").textContent = fmtTime(v * player.time().duration); },
    onChange: (v) => player.seek(v * player.time().duration),
  });
  volumeSlider = makeSlider($("volume"), { onInput: (v) => player.setVolume(v), onChange: (v) => player.setVolume(v) });
  const renderVolume = ({ volume, muted }) => {
    volumeSlider.set(muted ? 0 : volume);
    $("btn-mute").replaceChildren(icon(muted || volume === 0 ? "mute" : volume < 0.5 ? "volumeLow" : "volume"));
  };
  renderVolume({ volume: player.volume, muted: player.muted });
  $("btn-lyrics").addEventListener("click", toggleLyrics);
  $("time-total").addEventListener("click", () => saveSettings({ remaining_time: !setting("remaining_time") }).catch(() => {}));

  $("btn-play").addEventListener("click", () => player.toggle());
  $("btn-next").addEventListener("click", () => player.next());
  $("btn-prev").addEventListener("click", () => player.prev());
  $("btn-shuffle").addEventListener("click", () => player.toggleShuffle());
  $("btn-repeat").addEventListener("click", () => player.cycleRepeat());
  $("btn-mute").addEventListener("click", () => player.toggleMute());
  $("btn-queue").addEventListener("click", toggleQueue);
  $("btn-fullscreen").addEventListener("click", openFullPlayer);
  $("np-cover").addEventListener("click", openFullPlayer);
  $("np").addEventListener("click", (e) => {
    // Handy: Tippen auf die Mini-Player-Leiste öffnet den Vollbild-Player
    if (window.matchMedia("(max-width: 720px)").matches && !e.target.closest(".like-btn")) {
      e.preventDefault();
      e.stopPropagation();
      openFullPlayer();
    }
  }, true);

  on("track", renderNowPlaying);
  on("state", renderPlayState);
  on("loading", (l) => $("player").classList.toggle("loading", l));
  on("time", renderTime);
  on("mode", renderModes);
  on("volume", renderVolume);
  on("queue", () => { if (!$("queue-panel").hidden) renderQueue(); });
  renderNowPlaying(null);
  renderModes();
  renderPlayState(false);
}

function renderNowPlaying() {
  const t = player.current;
  $("player").classList.toggle("empty", !t);
  const np = $("np");
  if (!t) {
    $("np-title").textContent = "";
    $("np-artist").textContent = "";
    $("np-cover").replaceChildren();
    $("np-cover").className = "np-cover cover";
    np.querySelector(".like-btn").hidden = true;
    return;
  }
  const coverEl = cover(t.cover, { size: 160, cls: "np-cover" });
  coverEl.id = "np-cover";
  coverEl.addEventListener("click", openFullPlayer);
  $("np-cover").replaceWith(coverEl);
  $("np-title").textContent = t.title;
  $("np-title").href = t.album_id ? `#/album/${t.album_id}` : "#/";
  $("np-artist").replaceChildren(...artistLinks(t));
  const like = likeButton(t);
  like.id = "np-like";
  $("np-like").replaceWith(like);
  document.title = `${t.title} · ${t.artist}`;
  fetchColor(t);
  highlightPlaying();
  if (!$("fullplayer").hidden) renderFullPlayer();
}

async function fetchColor(t) {
  // Farbe des Albums für mobile Player-Leiste / Vollbild-Player
  if (setting("dynamic_colors") === false || !t.album_id) {
    document.documentElement.style.setProperty("--np-color", "#535353");
    document.documentElement.style.setProperty("--fp-color", "#535353");
    return;
  }
  try {
    const a = await api(`/albums/${t.album_id}`);
    document.documentElement.style.setProperty("--np-color", a.color);
    document.documentElement.style.setProperty("--fp-color", a.color);
  } catch { /* egal */ }
}

function renderPlayState(playing) {
  $("btn-play").replaceChildren(icon(playing ? "pause" : "play"));
  $("btn-play").title = playing ? "Pause" : "Wiedergabe";
  document.body.classList.toggle("paused", !playing);
  const fp = document.querySelector("#fullplayer .play-btn");
  if (fp) fp.replaceChildren(icon(playing ? "pause" : "play"));
}

function renderTime({ current, duration }) {
  if (!seekSlider.dragging) {
    $("time-cur").textContent = fmtTime(current);
    seekSlider.set(duration ? current / duration : 0);
  }
  // Einstellung „Restzeit statt Gesamtdauer“
  const total = setting("remaining_time") && duration ? `-${fmtTime(Math.max(0, duration - current))}` : fmtTime(duration);
  $("time-total").textContent = total;
  $("mobile-progress").style.width = `${duration ? (current / duration) * 100 : 0}%`;
  if (fpSeek && !fpSeek.dragging) {
    fpSeek.set(duration ? current / duration : 0);
    const cur = document.querySelector("#fullplayer .t-cur");
    const tot = document.querySelector("#fullplayer .t-tot");
    if (cur) cur.textContent = fmtTime(current);
    if (tot) tot.textContent = total;
  }
}

function renderModes() {
  $("btn-shuffle").classList.toggle("active", player.shuffle);
  $("btn-repeat").classList.toggle("active", player.repeat !== "off");
  $("btn-repeat").replaceChildren(icon(player.repeat === "one" ? "repeatOne" : "repeat"));
  $("btn-repeat").title = { off: "Wiederholen", all: "Alle wiederholen", one: "Song wiederholen" }[player.repeat];
  document.querySelectorAll("#fullplayer [data-mode]").forEach((b) => {
    if (b.dataset.mode === "shuffle") b.classList.toggle("active", player.shuffle);
    if (b.dataset.mode === "repeat") {
      b.classList.toggle("active", player.repeat !== "off");
      b.replaceChildren(icon(player.repeat === "one" ? "repeatOne" : "repeat"));
    }
  });
}

// Aktuellen Song in allen Listen grün hervorheben
function highlightPlaying() {
  let style = document.getElementById("playing-style");
  if (!style) {
    style = document.createElement("style");
    style.id = "playing-style";
    document.head.append(style);
  }
  const t = player.current;
  if (!t) { style.textContent = ""; return; }
  const sel = `.tl-row[data-id="${CSS.escape(t.id)}"]`;
  style.textContent = `${sel} .t-title { color: var(--accent); } ${sel} .num .n { display: none; }
    ${sel} .num .eq { display: inline-flex; } ${sel}:hover .num .eq { display: none; }`;
}

// ================================================================ Warteschlange
function toggleQueue() {
  const panel = $("queue-panel");
  panel.hidden = !panel.hidden;
  $("btn-queue").classList.toggle("active", !panel.hidden);
  if (!panel.hidden) renderQueue();
}

function queueRow(t, { playing = false } = {}) {
  const row = h("div", { class: "list-row", style: playing ? { background: "var(--hover)" } : null },
    cover(t.cover, { size: 96 }),
    h("div", { style: { flex: 1, minWidth: 0 } },
      h("div", { class: "ellipsis", style: playing ? { color: "var(--accent)" } : null }, t.title),
      h("div", { class: "muted ellipsis", style: { fontSize: "13px" } }, t.artist)),
    !playing ? h("button", { class: "icon-btn", title: "Entfernen", "aria-label": "Entfernen", onclick: (e) => { e.stopPropagation(); player.removeFromQueue(t._uid); } }, icon("close")) : null);
  if (!playing) row.addEventListener("click", () => player.jumpTo(t._uid));
  row.addEventListener("contextmenu", (e) => { e.preventDefault(); trackMenu(e, t); });
  return row;
}

function renderQueue() {
  const panel = $("queue-panel");
  clear(panel);
  panel.append(h("div", { class: "queue-head" }, h("h3", {}, "Warteschlange"),
    h("button", { class: "icon-btn", "aria-label": "Schließen", onclick: toggleQueue }, icon("close"))));
  const cur = player.current;
  if (!cur) {
    panel.append(h("p", { class: "muted" }, "Hier erscheint, was als Nächstes läuft."));
    return;
  }
  panel.append(h("h3", { class: "muted", style: { fontSize: "14px" } }, "Läuft gerade"), queueRow(cur, { playing: true }));
  const upcoming = player.items.slice(player.index + 1);
  const queued = upcoming.filter((t) => t._queued);
  const rest = upcoming.filter((t) => !t._queued && !t._autoplay);
  const auto = upcoming.filter((t) => !t._queued && t._autoplay);
  const group = (title, list) => {
    if (!list.length) return;
    panel.append(h("h3", { class: "muted", style: { fontSize: "14px", marginTop: "20px" } }, title));
    list.slice(0, 100).forEach((t) => panel.append(queueRow(t)));
    if (list.length > 100) panel.append(h("p", { class: "muted" }, `… und ${list.length - 100} weitere`));
  };
  group("Als Nächstes in der Warteschlange", queued);
  group(player.context?.name ? `Als Nächstes von: ${player.context.name}` : "Als Nächstes", rest);
  group("Autoplay: ähnliche Songs", auto);
  if (!upcoming.length && setting("autoplay")) {
    panel.append(h("p", { class: "muted", style: { fontSize: "13px", marginTop: "20px" } }, "Autoplay ist an: Danach laufen ähnliche Songs aus deiner Bibliothek weiter."));
  }
}

// ================================================================ Vollbild-Player
function openFullPlayer() {
  if (!player.current) return;
  $("fullplayer").hidden = false;
  renderFullPlayer();
  history.pushState({ fullplayer: true }, "");
}

function closeFullPlayer() {
  if ($("fullplayer").hidden) return;
  $("fullplayer").hidden = true;
  fpSeek = null;
}

window.addEventListener("popstate", () => closeFullPlayer());

function renderFullPlayer() {
  const t = player.current;
  const fp = $("fullplayer");
  if (!t) { closeFullPlayer(); return; }
  const seek = h("div", { class: "slider" }, h("div", { class: "slider-fill" }), h("div", { class: "slider-thumb" }));
  const close = () => { if (history.state?.fullplayer) history.back(); else closeFullPlayer(); };
  clear(fp).append(
    h("div", { class: "fp-top" },
      h("button", { class: "icon-btn", "aria-label": "Schließen", onclick: close }, icon("chevronDown")),
      h("div", { class: "fp-context" }, h("div", {}, player.context ? "Wird gespielt von" : "Wird gespielt"),
        h("div", { class: "ellipsis", style: { fontWeight: 700 } }, player.context?.name || t.album)),
      h("button", { class: "icon-btn", "aria-label": "Mehr", onclick: (e) => trackMenu(e, t) }, icon("more"))),
    h("div", { class: "fp-body" },
      cover(t.cover, { size: 640, cls: "fp-cover" }),
      h("div", { class: "fp-meta" },
        h("div", { class: "grow" },
          h("div", { class: "fp-title" }, t.title),
          h("div", { class: "fp-artist" }, artistLinks(t))),
        likeButton(t)),
      h("div", { class: "fp-progress" }, seek,
        h("div", { class: "times" }, h("span", { class: "t-cur" }, "0:00"), h("span", { class: "t-tot" }, fmtTime(t.duration)))),
      h("div", { class: "fp-controls" },
        h("button", { class: `icon-btn ${player.shuffle ? "active" : ""}`, dataset: { mode: "shuffle" }, "aria-label": "Zufall", onclick: () => player.toggleShuffle() }, icon("shuffle")),
        h("button", { class: "icon-btn", "aria-label": "Zurück", onclick: () => player.prev() }, icon("prev")),
        h("button", { class: "play-btn", "aria-label": "Wiedergabe", onclick: () => player.toggle() }, icon(player.playing ? "pause" : "play")),
        h("button", { class: "icon-btn", "aria-label": "Weiter", onclick: () => player.next() }, icon("next")),
        h("button", { class: `icon-btn ${player.repeat !== "off" ? "active" : ""}`, dataset: { mode: "repeat" }, "aria-label": "Wiederholen", onclick: () => player.cycleRepeat() }, icon(player.repeat === "one" ? "repeatOne" : "repeat"))),
      h("div", { style: { display: "flex", justifyContent: "space-between", width: "100%" } },
        h("button", { class: "icon-btn", "aria-label": "Warteschlange", onclick: () => { close(); setTimeout(toggleQueue, 50); } }, icon("queue")),
        setting("show_lyrics_button") ? h("button", { class: "icon-btn", "aria-label": "Songtext", title: "Songtext", onclick: () => { close(); setTimeout(() => navigate("#/lyrics"), 50); } }, icon("mic")) : null,
        h("button", { class: "icon-btn", "aria-label": "Song-Radio", title: "Song-Radio", onclick: () => { close(); navigate(`#/mix/radio/${encodeURIComponent(t.id)}`); } }, icon("radio")))),
  );
  fp.querySelectorAll(".fp-meta a, .fp-context").forEach((a) => a.addEventListener("click", () => setTimeout(closeFullPlayer, 0)));
  fpSeek = makeSlider(seek, { onChange: (v) => player.seek(v * player.time().duration) });
  renderTime(player.time());
  // Wischen nach unten schließt (Handy)
  let startY = null;
  fp.ontouchstart = (e) => { startY = e.touches[0].clientY; };
  fp.ontouchend = (e) => {
    if (startY !== null && e.changedTouches[0].clientY - startY > 120 && !e.target.closest(".slider")) close();
    startY = null;
  };
}

// ================================================================ Android- und Windows-App
// Meldet Titel/Cover/Status an die App (Android: Benachrichtigung, Sperrbildschirm, Bluetooth;
// Windows: Taskleisten-Knöpfe) und nimmt deren Befehle über window.homifyNative entgegen.
function setupNativeBridge() {
  const native = window.HomifyAndroid || window.homifyDesktop;
  if (!native) return;
  document.documentElement.classList.add("in-app");
  let lastPush = 0;
  const push = () => {
    lastPush = Date.now();
    const t = player.current;
    const payload = t ? {
      title: t.title, artist: t.artist, album: t.album,
      artwork: t.cover ? new URL(coverUrl(t.cover, 640), location.href).href : "",
      playing: player.playing, position: player.audio.currentTime || 0,
      duration: player.time().duration || t.duration || 0,
    } : { title: "" };
    try { native.updateState(JSON.stringify(payload)); } catch { /* App zu alt */ }
  };
  on("track", push);
  on("state", push);
  on("time", () => { if (Date.now() - lastPush > 15000) push(); });
  on("seeked", push);
  window.homifyNative = (cmd, arg) => {
    if (cmd === "play") player.play();
    else if (cmd === "pause") player.pause();
    else if (cmd === "next") player.next();
    else if (cmd === "prev") player.prev();
    else if (cmd === "seek") player.seek((arg || 0) / 1000);
  };
}

// ================================================================ Tastenkürzel
function onShortcut(e) {
  if (e.key === "Escape") { closeFullPlayer(); return; }
  if (!setting("shortcuts")) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag) || e.target.isContentEditable) return;
  const mod = e.ctrlKey || e.metaKey;
  const step = Number(setting("seek_step") || 10);
  if (e.code === "Space") { e.preventDefault(); player.toggle(); }
  else if (mod && e.key === "ArrowRight") { e.preventDefault(); player.next(); }
  else if (mod && e.key === "ArrowLeft") { e.preventDefault(); player.prev(); }
  else if (mod && e.key === "ArrowUp") { e.preventDefault(); player.setVolume(player.volume + 0.1); }
  else if (mod && e.key === "ArrowDown") { e.preventDefault(); player.setVolume(player.volume - 0.1); }
  else if (e.shiftKey && e.key === "ArrowRight") { player.seekBy(step); }
  else if (e.shiftKey && e.key === "ArrowLeft") { player.seekBy(-step); }
  else if (e.key.toLowerCase() === "l" && !mod && setting("show_lyrics_button")) toggleLyrics();
  else if ((mod && e.key.toLowerCase() === "k") || e.key === "/") {
    e.preventDefault();
    navigate("#/search");
    setTimeout(() => $("search-input").focus(), 50);
  } else if (e.key.toLowerCase() === "s" && !mod) player.toggleShuffle();
  else if (e.key.toLowerCase() === "r" && !mod) player.cycleRepeat();
  else if (e.key.toLowerCase() === "m" && !mod) player.toggleMute();
}

window.addEventListener("unhandledrejection", (e) => {
  if (e.reason?.name === "AbortError") return;
  if (e.reason?.status === 401) return;
  if (e.reason?.message) toast(e.reason.message, { error: true });
});

boot();
