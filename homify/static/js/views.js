// Seiten: Startseite, Bibliothek, Album, Künstler, Playlist, Lieblingssongs, Mixe

import { api, emit, loadPlaylists, on, state } from "./api.js";
import {
  addToPlaylistDialog, albumCard, albumMenu, artistCard, card, mixCard, navigate, playAlbum,
  playArtist, playButton, playlistCard, shelf, trackList,
} from "./components.js";
import { player } from "./player.js";
import { generatePlaylistDialog } from "./playlistgen.js";
import {
  clear, confirmDialog, cover, debounce, fmtDuration, h, icon, mosaic, openMenu, plural, prompt, toast,
} from "./ui.js";
import { confirmDelete, setting } from "./usersettings.js";

const show = (key) => setting(key) !== false;

// ---------------------------------------------------------------- Hilfen
function inner(...children) { return h("div", { class: "view-inner" }, ...children); }

function hero({ coverEl, type, title, meta = [], desc }) {
  return h("div", { class: "hero" }, coverEl,
    h("div", { class: "hero-text" },
      h("div", { class: "hero-type" }, type),
      h("h1", { class: `hero-title ${title.length > 28 ? "long" : ""}` }, title),
      desc ? h("div", { class: "hero-desc" }, desc) : null,
      h("div", { class: "hero-meta" }, meta.filter(Boolean).map((m, i) => i ? [h("span", { class: "dot" }), m] : m))));
}

/** Großer Play-Button, der zu Pause wird, wenn gerade dieser Kontext läuft. */
function contextPlayButton(contextId, onPlay) {
  const btn = h("button", { class: "big-play", "aria-label": "Abspielen" }, icon("play"));
  const sync = () => {
    const active = player.context?.id === contextId && player.playing;
    btn.replaceChildren(icon(active ? "pause" : "play"));
  };
  btn.addEventListener("click", () => {
    if (player.context?.id === contextId && player.current) player.toggle();
    else onPlay();
  });
  sync();
  const off1 = on("state", sync), off2 = on("track", sync);
  btn._cleanup = () => { off1(); off2(); };
  return btn;
}

function shuffleButton(onShuffle) {
  const btn = h("button", { class: `icon-btn ${player.shuffle ? "active" : ""}`, title: "Zufallswiedergabe", "aria-label": "Zufallswiedergabe" }, icon("shuffle"));
  btn.addEventListener("click", () => {
    if (!player.shuffle) player.toggleShuffle();
    onShuffle();
  });
  const off = on("mode", () => {
    if (!btn.isConnected) { off(); return; }
    btn.classList.toggle("active", player.shuffle);
  });
  return btn;
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return "Gute Nacht";
  if (hour < 11) return "Guten Morgen";
  if (hour < 18) return "Guten Tag";
  return "Guten Abend";
}

export function emptyState(iconName, title, text, action) {
  return h("div", { class: "empty" }, h("div", { class: "icon-big" }, icon(iconName)), h("h2", {}, title),
    text ? h("p", {}, text) : null, action || null);
}

// ---------------------------------------------------------------- Startseite
export async function homeView() {
  const data = await api(`/home?limit=${Number(setting("home_items") || 12)}`);
  const root = inner();
  root.append(h("h1", { class: "greeting" }, greeting()));

  if (!data.stats.tracks) {
    const scanning = data.scan?.running;
    root.append(emptyState("note", scanning ? "Deine Bibliothek wird eingelesen …" : "Noch keine Musik da",
      scanning ? "Das kann beim ersten Mal etwas dauern. Die Seite aktualisiert sich gleich."
        : "Lege in den Einstellungen deinen Musikordner fest (z. B. \\\\NAS\\Musik) oder suche oben nach Songs, um sie über Spotify zu holen.",
      h("div", { class: "row-actions", style: { justifyContent: "center" } },
        state.user?.is_admin ? h("a", { class: "btn btn-primary", href: "#/settings" }, "Einstellungen öffnen") : null,
        h("a", { class: "btn btn-outline", href: "#/search" }, "Songs suchen"))));
    if (scanning) setTimeout(() => { if (location.hash === "" || location.hash === "#/") navigate("#/"); }, 4000);
    return { el: root, color: "#1e3264" };
  }

  // Schnellzugriff (wie bei Spotify oben auf der Startseite)
  const quick = h("div", { class: "quick-grid" });
  const addQuick = (coverEl, name, href, onPlay) => {
    const el = h("div", { class: "quick", role: "link", tabindex: "0", onclick: () => navigate(href) },
      coverEl, h("span", { class: "ellipsis" }, name), onPlay ? playButton(onPlay) : null);
    quick.append(el);
  };
  addQuick(h("div", { class: "cover liked" }, icon("heart")), "Lieblingssongs", "#/liked", async () => {
    const tracks = await api("/likes");
    if (tracks.length) player.playTracks(tracks, 0, { type: "liked", id: "liked", name: "Lieblingssongs", href: "#/liked" });
    else toast("Noch keine Lieblingssongs");
  });
  for (const p of data.playlists.slice(0, 3)) {
    addQuick(mosaic(p.covers, { size: 128 }), p.name, `#/playlist/${p.id}`, () => api(`/playlists/${p.id}`).then((pl) =>
      player.playTracks(pl.tracks, 0, { type: "playlist", id: p.id, name: p.name, href: `#/playlist/${p.id}` })));
  }
  for (const a of data.recent_albums.slice(0, 7 - quick.children.length)) {
    addQuick(cover(a.cover, { size: 128 }), a.name, `#/album/${a.id}`, () => playAlbum(a.id));
  }
  if (quick.children.length < 8) {
    for (const a of data.new_albums.slice(0, 8 - quick.children.length)) {
      addQuick(cover(a.cover, { size: 128 }), a.name, `#/album/${a.id}`, () => playAlbum(a.id));
    }
  }
  if (show("home_quick")) root.append(quick);

  const mixes = [
    { id: "random:", name: "Zufallsmix", subtitle: "Quer durch deine Bibliothek", covers: data.discover.map((a) => a.cover).filter(Boolean).slice(0, 4) },
    ...data.mixes,
  ];
  root.append(
    show("home_recent") ? shelf("Zuletzt gehört", data.recent_albums.map((a) => albumCard(a))) : null,
    show("home_mixes") ? shelf("Deine Mixe", mixes.map(mixCard)) : null,
    show("home_new") ? shelf("Neu in deiner Bibliothek", data.new_albums.map((a) => albumCard(a)), { href: "#/library?tab=albums&sort=added" }) : null,
  );
  if (show("home_top_tracks") && data.top_tracks.length) {
    root.append(h("section", { class: "section" },
      h("div", { class: "section-head" }, h("h2", {}, "Deine Top-Songs")),
      trackList(data.top_tracks, { context: { type: "top", id: "top", name: "Deine Top-Songs", href: "#/" } })));
  }
  root.append(
    show("home_artists") ? shelf("Deine Künstler", data.top_artists.map(artistCard), { href: "#/library?tab=artists" }) : null,
    show("home_playlists") ? shelf("Deine Playlists", data.playlists.map(playlistCard), { href: "#/library?tab=playlists" }) : null,
    show("home_playlists") ? shelf("Von anderen geteilt", (data.shared || []).map(playlistCard), { href: "#/library?tab=playlists" }) : null,
    show("home_discover") ? shelf("Entdecken", data.discover.map((a) => albumCard(a))) : null,
    h("p", { class: "subtle", style: { marginTop: "24px", fontSize: "12px" } },
      `${plural(data.stats.tracks, "Song", "Songs")} · ${plural(data.stats.albums, "Album", "Alben")} · ${plural(data.stats.artists, "Künstler", "Künstler")} · ${fmtDuration(data.stats.duration)}`),
  );
  const firstColor = data.recent_albums[0]?.color || data.new_albums[0]?.color || "#1e3264";
  return { el: root, color: firstColor };
}

// ---------------------------------------------------------------- Bibliothek
export async function libraryView(_params, query) {
  const tab = query.get("tab") || setting("library_tab") || "playlists";
  const defaultSort = (t) => (t === "albums" ? setting("album_sort") || "name" : setting("song_sort") || "title");
  const sort = query.get("sort") || defaultSort(tab);
  const root = inner(h("h1", { class: "page-title" }, "Deine Bibliothek"));
  const tabs = h("div", { class: "toolbar" });
  const setQuery = (params) => {
    const q = new URLSearchParams(query);
    for (const [k, v] of Object.entries(params)) q.set(k, v);
    navigate(`#/library?${q.toString()}`);
  };
  for (const [key, label] of [["playlists", "Playlists"], ["albums", "Alben"], ["artists", "Künstler"], ["songs", "Songs"]]) {
    tabs.append(h("button", { class: `chip ${tab === key ? "active" : ""}`, onclick: () => setQuery({ tab: key, sort: defaultSort(key) }) }, label));
  }
  tabs.append(h("div", { class: "spacer" }));
  const filter = h("input", { class: "input", type: "search", placeholder: "In Bibliothek filtern", "aria-label": "Filtern" });
  tabs.append(filter);
  root.append(tabs);
  const body = h("div");
  root.append(body);

  const applyFilter = (items, getText, render) => {
    const draw = () => {
      const f = filter.value.trim().toLowerCase();
      clear(body);
      const list = f ? items.filter((i) => getText(i).toLowerCase().includes(f)) : items;
      if (!list.length) body.append(emptyState("search", "Nichts gefunden", f ? `Keine Treffer für „${filter.value}“` : ""));
      else body.append(render(list));
    };
    filter.addEventListener("input", debounce(draw, 150));
    draw();
  };

  if (tab === "playlists") {
    await loadPlaylists();
    const items = [{ special: "liked" }, { special: "downloads" }, ...state.playlists];
    applyFilter(items, (p) => p.name || (p.special === "liked" ? "Lieblingssongs" : "Downloads"), (list) => h("div", { class: "grid" },
      list.map((p) => p.special === "liked"
        ? card({ title: "Lieblingssongs", subtitle: "Playlist", coverEl: h("div", { class: "cover liked" }, icon("heart")), href: "#/liked" })
        : p.special === "downloads"
          ? card({ title: "Downloads", subtitle: "Von Spotify holen", coverEl: h("div", { class: "cover downloads" }, icon("download")), href: "#/downloads" })
          : playlistCard(p))));
    root.querySelector(".toolbar").append(
      h("button", { class: "btn btn-small btn-outline", onclick: () => generatePlaylistDialog() }, icon("sparkle", "sm"), "Zusammenstellen"),
      h("button", { class: "btn btn-small btn-primary", onclick: createPlaylist }, icon("plus"), "Neue Playlist"));
    const shared = (await api("/playlists/public").catch(() => [])).filter((p) => !p.followed);
    if (shared.length) {
      root.append(h("section", { class: "section", style: { marginTop: "32px" } },
        h("div", { class: "section-head" }, h("h2", {}, "Von anderen geteilt"),
          h("span", { class: "muted", style: { fontSize: "13px" } }, "Veröffentlichte Playlists der anderen Benutzer – zum Hören, Folgen oder Kopieren")),
        h("div", { class: "grid" }, shared.map(playlistCard))));
    }
  } else if (tab === "albums") {
    const sortSel = h("select", { class: "input", "aria-label": "Sortierung" },
      [["name", "Name"], ["artist", "Künstler"], ["added", "Zuletzt hinzugefügt"], ["year", "Jahr"]].map(([v, l]) => h("option", { value: v, selected: v === sort }, l)));
    sortSel.addEventListener("change", () => setQuery({ sort: sortSel.value }));
    tabs.insertBefore(sortSel, filter);
    const albums = await api(`/albums?sort=${encodeURIComponent(sort)}&limit=5000`);
    applyFilter(albums, (a) => `${a.name} ${a.artist}`, (list) => h("div", { class: "grid" }, list.slice(0, 600).map((a) => albumCard(a))));
  } else if (tab === "artists") {
    const artists = await api("/artists?limit=10000");
    applyFilter(artists, (a) => a.name, (list) => h("div", { class: "grid" }, list.slice(0, 600).map(artistCard)));
  } else {
    const sortSel = h("select", { class: "input", "aria-label": "Sortierung" },
      [["title", "Titel"], ["artist", "Künstler"], ["album", "Album"], ["added", "Zuletzt hinzugefügt"]].map(([v, l]) => h("option", { value: v, selected: v === sort }, l)));
    sortSel.addEventListener("change", () => setQuery({ sort: sortSel.value }));
    tabs.insertBefore(sortSel, filter);
    filter.placeholder = "Songs suchen …";
    const tracks = [];
    let total = Infinity, loading = false;
    const listHost = h("div");
    const sentinel = h("div", { style: { height: "40px" } });
    const info = h("p", { class: "muted" });
    body.append(info, listHost, sentinel);
    const context = { type: "library", id: "library", name: "Deine Songs", href: "#/library?tab=songs" };
    const loadMore = async () => {
      if (loading || tracks.length >= total) return;
      loading = true;
      const page = await api(`/tracks?sort=${encodeURIComponent(sort)}&limit=300&offset=${tracks.length}`);
      total = page.total;
      tracks.push(...page.items);
      clear(listHost).append(trackList(tracks, { context, added: sort === "added" }));
      info.textContent = plural(total, "Song", "Songs");
      loading = false;
    };
    filter.addEventListener("input", debounce(async () => {
      const f = filter.value.trim();
      if (!f) { clear(listHost).append(trackList(tracks, { context })); sentinel.hidden = false; return; }
      sentinel.hidden = true;
      const res = await api(`/search?q=${encodeURIComponent(f)}`);
      clear(listHost).append(res.tracks.length ? trackList(res.tracks, { context: { ...context, id: "filter" } }) : emptyState("search", "Nichts gefunden", ""));
    }, 250));
    await loadMore();
    const io = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) loadMore(); }, { rootMargin: "600px" });
    io.observe(sentinel);
    return { el: root, color: "#2a2a2a", destroy: () => io.disconnect() };
  }
  return { el: root, color: "#2a2a2a" };
}

export async function createPlaylist() {
  const name = await prompt("Neue Playlist", { label: "Name", value: `Meine Playlist Nr. ${state.playlists.length + 1}`, okLabel: "Erstellen" });
  if (!name) return;
  try {
    const p = await api("/playlists", { method: "POST", body: { name } });
    await loadPlaylists();
    navigate(`#/playlist/${p.id}`);
  } catch (e) { toast(e.message, { error: true }); }
}

// ---------------------------------------------------------------- Album
export async function albumView({ id }) {
  const a = await api(`/albums/${encodeURIComponent(id)}`);
  const context = { type: "album", id: a.id, name: a.name, href: `#/album/${a.id}` };
  const playBtn = contextPlayButton(a.id, () => player.playTracks(a.tracks, 0, context));
  const root = inner(
    hero({
      coverEl: cover(a.cover, { size: 640, cls: "hero-cover", fallback: "album" }),
      type: a.track_count === 1 ? "Single" : "Album",
      title: a.name,
      meta: [h("a", { class: "link", href: `#/artist/${a.artist_id}` }, h("strong", {}, a.artist)), a.year || null,
        plural(a.track_count, "Song", "Songs"), fmtDuration(a.duration)],
    }),
    h("div", { class: "actionbar" }, playBtn,
      shuffleButton(() => player.playTracks(a.tracks, Math.floor(Math.random() * a.tracks.length), context)),
      h("button", { class: "icon-btn", title: "Zur Playlist hinzufügen", "aria-label": "Zur Playlist hinzufügen", onclick: () => addToPlaylistDialog(a.tracks) }, icon("plus")),
      h("button", { class: "icon-btn", "aria-label": "Mehr", onclick: (e) => albumMenu(e, a) }, icon("more"))),
    trackList(a.tracks, { album: false, showCover: false, numbered: "track", discs: true, context }),
    a.genre ? h("p", { class: "subtle", style: { marginTop: "16px", fontSize: "12px" } }, a.genre) : null,
    shelf(`Mehr von ${a.artist}`, a.more.map((x) => albumCard(x, { subtitle: String(x.year || "") }))),
  );
  return { el: root, color: a.color, title: a.name, destroy: () => playBtn._cleanup() };
}

// ---------------------------------------------------------------- Künstler
export async function artistView({ id }) {
  const a = await api(`/artists/${encodeURIComponent(id)}`);
  const playBtn = contextPlayButton(a.id, () => playArtist(a.id));
  const popularHost = h("div");
  let expanded = false;
  const context = { type: "artist", id: a.id, name: a.name, href: `#/artist/${a.id}` };
  const drawPopular = () => {
    clear(popularHost).append(trackList(expanded ? a.popular : a.popular.slice(0, 5), { album: true, context }));
    if (a.popular.length > 5) popularHost.append(h("button", { class: "btn btn-small btn-outline", style: { marginTop: "12px" }, onclick: () => { expanded = !expanded; drawPopular(); } }, expanded ? "Weniger anzeigen" : "Mehr anzeigen"));
  };
  drawPopular();
  const root = inner(
    hero({
      coverEl: cover(a.cover, { size: 640, cls: "hero-cover round", fallback: "person", round: true }),
      type: "Künstler", title: a.name,
      meta: [plural(a.track_count, "Song", "Songs"), a.album_count ? plural(a.album_count, "Album", "Alben") : null],
    }),
    h("div", { class: "actionbar" }, playBtn,
      shuffleButton(async () => {
        const tracks = await api(`/artists/${a.id}/tracks`);
        player.playTracks(tracks, Math.floor(Math.random() * tracks.length), context);
      }),
      h("button", { class: "btn btn-outline btn-small", onclick: () => generatePlaylistDialog({ source: "artist", value: a.id, label: a.name }) }, icon("sparkle", "sm"), "Playlist zusammenstellen"),
      h("a", { class: "btn btn-outline btn-small", href: `#/search?q=${encodeURIComponent(a.name)}` }, icon("cloud", "sm"), "Mehr auf Spotify finden")),
    h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, "Beliebt")), popularHost),
    shelf("Diskografie", a.albums.map((x) => albumCard(x, { subtitle: [x.year || null, x.track_count === 1 ? "Single" : "Album"].filter(Boolean).join(" • ") }))),
    shelf("Enthalten auf", a.appears_on.map((x) => albumCard(x))),
  );
  return { el: root, color: a.color, title: a.name, destroy: () => playBtn._cleanup() };
}

// ---------------------------------------------------------------- Playlist
export async function playlistView({ id }) {
  const p = await api(`/playlists/${encodeURIComponent(id)}`);
  const own = p.own !== false;  // eigene Playlist oder veröffentlichte eines anderen Benutzers
  const context = { type: "playlist", id: p.id, name: p.name, href: `#/playlist/${p.id}` };
  const playBtn = contextPlayButton(p.id, () => player.playTracks(p.tracks, 0, context));
  const reload = () => { loadPlaylists(); navigate(`#/playlist/${p.id}`); };
  const listHost = h("div");
  const drawList = () => {
    clear(listHost);
    if (!p.tracks.length) {
      listHost.append(h("p", { class: "muted" }, own
        ? "Diese Playlist ist noch leer. Füge unten Songs hinzu oder nutze „Zur Playlist hinzufügen“ im Menü eines Songs."
        : "Diese Playlist ist leer."));
      return;
    }
    listHost.append(trackList(p.tracks, own ? {
      added: true, context, playlistId: p.id,
      onChange: () => emit("playlist-changed", p.id),
      onReorder: async (tracks) => {
        drawList();
        try {
          await api(`/playlists/${p.id}/order`, { method: "PUT", body: { entry_ids: tracks.map((t) => t.entry_id) } });
        } catch (e) { toast(e.message, { error: true }); }
      },
    } : { added: true, context }));
  };
  drawList();

  const rename = async () => {
    const name = await prompt("Playlist umbenennen", { label: "Name", value: p.name });
    if (!name) return;
    await api(`/playlists/${p.id}`, { method: "PATCH", body: { name } });
    reload();
  };
  const editDesc = async () => {
    const description = await prompt("Beschreibung", { label: "Beschreibung", value: p.description });
    if (description === null) return;
    await api(`/playlists/${p.id}`, { method: "PATCH", body: { description } });
    navigate(`#/playlist/${p.id}`);
  };
  const remove = async () => {
    if (!await confirmDelete(() => confirmDialog("Playlist löschen?", `„${p.name}“ wird gelöscht. Die Songs bleiben in deiner Bibliothek.`, { okLabel: "Löschen", danger: true }))) return;
    await api(`/playlists/${p.id}`, { method: "DELETE" });
    await loadPlaylists();
    navigate("#/library");
  };
  // Veröffentlichen: für alle Homify-Benutzer sichtbar (hören, folgen, kopieren – nicht bearbeiten)
  const togglePublic = async () => {
    if (p.public && !await confirmDialog("Nicht mehr veröffentlichen?",
      "Die anderen Benutzer sehen die Playlist dann nicht mehr – auch wenn sie ihr folgen.", { okLabel: "Privat machen" })) return;
    try {
      const r = await api(`/playlists/${p.id}`, { method: "PATCH", body: { public: !p.public } });
      toast(r.public ? "Veröffentlicht – alle Homify-Benutzer finden sie unter „Von anderen geteilt“" : "Die Playlist ist wieder privat");
      reload();
    } catch (e) { toast(e.message, { error: true }); }
  };
  const toggleFollow = async () => {
    try {
      await api(`/playlists/${p.id}/follow`, { method: p.followed ? "DELETE" : "PUT" });
      toast(p.followed ? "Du folgst der Playlist nicht mehr" : "Gefolgt – die Playlist steht jetzt in deiner Bibliothek");
      reload();
    } catch (e) { toast(e.message, { error: true }); }
  };
  const copy = async () => {
    try {
      const c = await api(`/playlists/${p.id}/copy`, { method: "POST" });
      toast(`„${c.name}“ ist jetzt deine eigene Playlist`);
      await loadPlaylists();
      navigate(`#/playlist/${c.id}`);
    } catch (e) { toast(e.message, { error: true }); }
  };

  const publishBtn = own
    ? h("button", { class: `btn btn-small ${p.public ? "btn-outline follow-btn following" : "btn-outline"}`, title: p.public ? "Für alle Homify-Benutzer sichtbar – klicken zum Privatmachen" : "Für alle Homify-Benutzer sichtbar machen", onclick: togglePublic },
      icon("globe", "sm"), p.public ? "Veröffentlicht" : "Veröffentlichen")
    : h("button", { class: `btn btn-small btn-outline follow-btn ${p.followed ? "following" : ""}`, onclick: toggleFollow }, p.followed ? "Gefolgt" : "Folgen");
  const menu = (e) => openMenu(e, own ? [
    { title: p.name },
    { label: "Zur Warteschlange hinzufügen", icon: "addQueue", action: () => { player.addToQueue(p.tracks); toast("Zur Warteschlange hinzugefügt"); } },
    { label: p.public ? "Nicht mehr veröffentlichen" : "Veröffentlichen", icon: "globe", action: togglePublic },
    { label: "Umbenennen", icon: "edit", action: rename },
    { label: "Beschreibung bearbeiten", icon: "edit", action: editDesc },
    { label: "Kopie erstellen", icon: "copy", action: copy },
    "sep",
    { label: "Playlist löschen", icon: "trash", danger: true, action: remove },
  ] : [
    { title: `${p.name} · von ${p.owner}` },
    { label: "Zur Warteschlange hinzufügen", icon: "addQueue", action: () => { player.addToQueue(p.tracks); toast("Zur Warteschlange hinzugefügt"); } },
    { label: p.followed ? "Nicht mehr folgen" : "Folgen", icon: "heart", action: toggleFollow },
    { label: "Als eigene Playlist kopieren", icon: "copy", action: copy },
  ]);

  // Songs hinzufügen (Suche in der Bibliothek) – nur bei eigenen Playlists
  let addSection = null;
  if (own) {
    const addInput = h("input", { class: "input", type: "search", placeholder: "Nach Songs für diese Playlist suchen", style: { maxWidth: "420px" } });
    const addResults = h("div");
    addInput.addEventListener("input", debounce(async () => {
      const q = addInput.value.trim();
      clear(addResults);
      if (!q) return;
      const res = await api(`/search?q=${encodeURIComponent(q)}`);
      for (const t of res.tracks.slice(0, 12)) {
        addResults.append(h("div", { class: "list-row" }, cover(t.cover, { size: 64 }),
          h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { class: "ellipsis" }, t.title), h("div", { class: "muted ellipsis", style: { fontSize: "13px" } }, t.artist)),
          h("button", { class: "btn btn-small btn-outline", onclick: async (e) => {
            e.stopPropagation();
            await api(`/playlists/${p.id}/tracks`, { method: "POST", body: { ids: [t.id] } });
            const fresh = await api(`/playlists/${p.id}`);
            p.tracks = fresh.tracks;
            drawList();
            loadPlaylists();
            toast("Hinzugefügt");
          } }, "Hinzufügen")));
      }
      if (!res.tracks.length) addResults.append(h("p", { class: "muted" }, "Nicht in deiner Bibliothek? ", h("a", { class: "link", href: `#/search?q=${encodeURIComponent(q)}`, style: { color: "var(--accent)" } }, "Auf Spotify suchen und holen")));
    }, 250));
    addSection = h("section", { class: "section", style: { marginTop: "40px" } },
      h("div", { class: "section-head" }, h("h2", {}, "Songs hinzufügen")), addInput, addResults);
  }

  const root = inner(
    hero({
      coverEl: mosaic(p.covers, { size: 640, cls: "hero-cover" }),
      type: p.public ? "Veröffentlichte Playlist" : "Playlist", title: p.name, desc: p.description || null,
      meta: [h("strong", {}, p.owner || state.user?.username || ""), plural(p.track_count, "Song", "Songs"), p.duration ? fmtDuration(p.duration) : null],
    }),
    h("div", { class: "actionbar" }, playBtn,
      shuffleButton(() => player.playTracks(p.tracks, Math.floor(Math.random() * p.tracks.length), context)),
      publishBtn,
      h("button", { class: "icon-btn", "aria-label": "Mehr", onclick: menu }, icon("more"))),
    listHost,
    addSection,
  );
  if (own) {
    root.querySelector(".hero-title").addEventListener("click", rename);
    root.querySelector(".hero-title").style.cursor = "pointer";
  }
  return { el: root, color: p.color, title: p.name, destroy: () => playBtn._cleanup() };
}

// ---------------------------------------------------------------- Lieblingssongs
export async function likedView() {
  const tracks = await api("/likes");
  const context = { type: "liked", id: "liked", name: "Lieblingssongs", href: "#/liked" };
  const playBtn = contextPlayButton("liked", () => player.playTracks(tracks, 0, context));
  const root = inner(
    hero({
      coverEl: h("div", { class: "cover liked hero-cover" }, icon("heart")),
      type: "Playlist", title: "Lieblingssongs",
      meta: [h("strong", {}, state.user?.username || ""), plural(tracks.length, "Song", "Songs")],
    }),
    tracks.length ? h("div", { class: "actionbar" }, playBtn,
      shuffleButton(() => player.playTracks(tracks, Math.floor(Math.random() * tracks.length), context))) : null,
    tracks.length ? trackList(tracks, { added: true, context })
      : emptyState("heart", "Songs, die dir gefallen", "Speichere Songs mit dem Herz-Symbol. Sie erscheinen dann hier."),
  );
  return { el: root, color: "#5038a0", destroy: () => playBtn._cleanup() };
}

// ---------------------------------------------------------------- Mixe & Radio
export async function mixView({ kind, value }) {
  value = decodeURIComponent(value || "");
  const tracks = await api(`/mix/${encodeURIComponent(kind)}?value=${encodeURIComponent(value)}&limit=${Number(setting("mix_size") || 60)}`);
  let title = "Zufallsmix", type = "Mix";
  if (kind === "genre") title = `${value} Mix`;
  if (kind === "radio") { title = tracks[0] ? `${tracks[0].title} Radio` : "Song-Radio"; type = "Radio"; }
  const id = `${kind}:${value}`;
  const context = { type: "mix", id, name: title, href: location.hash };
  const playBtn = contextPlayButton(id, () => player.playTracks(tracks, 0, context));
  const covers = [...new Set(tracks.map((t) => t.cover).filter(Boolean))];
  const root = inner(
    hero({
      coverEl: mosaic(covers, { size: 640, cls: "hero-cover" }), type, title,
      desc: kind === "radio" ? "Ähnliche Songs aus deiner Bibliothek (gleicher Künstler, Genre und Ära)." : null,
      meta: [plural(tracks.length, "Song", "Songs")],
    }),
    h("div", { class: "actionbar" }, playBtn,
      h("button", { class: "btn btn-outline btn-small", onclick: () => navigate(location.hash) }, icon("refresh", "sm"), "Neu mischen")),
    tracks.length ? trackList(tracks, { context }) : emptyState("note", "Keine Songs gefunden", ""),
  );
  return { el: root, color: "#1e3264", destroy: () => playBtn._cleanup() };
}
