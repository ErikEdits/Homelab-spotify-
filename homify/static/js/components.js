// Wiederverwendbare Bausteine: Songlisten, Karten, Regale, Kontextmenüs

import { api, emit, isLiked, loadPlaylists, on, setLiked, state } from "./api.js";
import { player } from "./player.js";
import { generatePlaylistDialog } from "./playlistgen.js";
import {
  confirmDialog, cover, coverUrl, fmtDate, fmtTime, h, icon, modal, mosaic, openMenu, plural, prompt, toast,
} from "./ui.js";
import { confirmDelete, setting } from "./usersettings.js";

export function navigate(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = hash;
}

export function artistLinks(track) {
  const artists = track.artists?.length ? track.artists : [{ id: "", name: track.artist }];
  const out = [];
  artists.forEach((a, i) => {
    if (i) out.push(", ");
    out.push(a.id ? h("a", { href: `#/artist/${a.id}`, onclick: (e) => e.stopPropagation() }, a.name) : a.name);
  });
  return out;
}

// ---------------------------------------------------------------- Like-Button
export function likeButton(track, cls = "") {
  const btn = h("button", { class: `icon-btn like-btn ${cls}`, "aria-label": "Zu Lieblingssongs", dataset: { likeId: track.id } }, icon("heart"));
  const sync = () => {
    const liked = isLiked(track);
    btn.classList.toggle("liked", liked);
    btn.title = liked ? "Aus Lieblingssongs entfernen" : "Zu Lieblingssongs hinzufügen";
  };
  sync();
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const liked = !isLiked(track);
    try {
      await setLiked(track.id, liked);
      track.liked = liked;
      toast(liked ? "Zu Lieblingssongs hinzugefügt" : "Aus Lieblingssongs entfernt");
    } catch (err) { toast(err.message, { error: true }); }
  });
  btn._sync = sync;
  return btn;
}

on("like", ({ id }) => {
  document.querySelectorAll(`[data-like-id="${CSS.escape(id)}"]`).forEach((b) => b._sync?.());
});

// ---------------------------------------------------------------- Playlist-Auswahl
export async function addToPlaylistDialog(tracks) {
  if (!tracks?.length) return;
  await loadPlaylists().catch(() => {});
  const list = h("div", { style: { display: "grid", gap: "2px", maxHeight: "50vh", overflow: "auto" } });
  let resolveChoice;
  const chosen = new Promise((r) => { resolveChoice = r; });
  const newBtn = h("button", { class: "list-row", type: "button", onclick: () => resolveChoice("new") },
    h("div", { class: "cover" }, icon("plus")), h("strong", {}, "Neue Playlist"));
  list.append(newBtn);
  for (const p of state.playlists.filter((x) => x.own !== false)) {  // nur eigene – fremde sind nicht bearbeitbar
    list.append(h("button", { class: "list-row", type: "button", onclick: () => resolveChoice(p) },
      mosaic(p.covers, { size: 96 }),
      h("div", { style: { textAlign: "left", minWidth: 0 } }, h("div", { class: "ellipsis" }, p.name),
        h("div", { class: "muted", style: { fontSize: "13px" } }, plural(p.track_count, "Song", "Songs")))));
  }
  const dialog = modal("Zur Playlist hinzufügen", list, []);
  const choice = await Promise.race([chosen, dialog.then(() => null)]);
  document.querySelector(".modal-backdrop")?.remove();
  if (!choice) return;
  try {
    let playlist = choice;
    if (choice === "new") {
      const name = await prompt("Neue Playlist", { label: "Name", value: tracks.length === 1 ? tracks[0].title : "Meine Playlist" });
      if (!name) return;
      playlist = await api("/playlists", { method: "POST", body: { name } });
    }
    const res = await api(`/playlists/${playlist.id}/tracks`, { method: "POST", body: { ids: tracks.map((t) => t.id) } });
    toast(`${plural(res.added, "Song", "Songs")} zu „${playlist.name}“ hinzugefügt`);
    await loadPlaylists();
    emit("playlist-changed", playlist.id);
  } catch (err) { toast(err.message, { error: true }); }
}

// ---------------------------------------------------------------- Kontextmenü für Songs
export function trackMenu(event, track, { playlistId = null, entryId = null, onRemove = null } = {}) {
  const liked = isLiked(track);
  const items = [
    { title: `${track.title} · ${track.artist}` },
    { label: "Zur Warteschlange hinzufügen", icon: "addQueue", action: () => { player.addToQueue([track]); toast("Zur Warteschlange hinzugefügt"); } },
    { label: "Als Nächstes spielen", icon: "playNext", action: () => { player.playNext([track]); toast("Wird als Nächstes gespielt"); } },
    "sep",
    { label: "Zur Playlist hinzufügen …", icon: "plus", action: () => addToPlaylistDialog([track]) },
    { label: liked ? "Aus Lieblingssongs entfernen" : "Zu Lieblingssongs hinzufügen", icon: "heart",
      action: () => setLiked(track.id, !liked).then(() => toast(liked ? "Entfernt" : "Zu Lieblingssongs hinzugefügt")).catch((e) => toast(e.message, { error: true })) },
    playlistId && entryId ? { label: "Aus dieser Playlist entfernen", icon: "trash", action: async () => {
      if (!await confirmDelete(() => confirmDialog("Aus Playlist entfernen?", `„${track.title}“ wird aus dieser Playlist entfernt.`, { okLabel: "Entfernen", danger: true }))) return;
      await api(`/playlists/${playlistId}/entries/${entryId}`, { method: "DELETE" });
      toast("Aus Playlist entfernt");
      onRemove?.();
      loadPlaylists();
    } } : null,
    "sep",
    { label: "Song-Radio starten", icon: "radio", action: () => navigate(`#/mix/radio/${encodeURIComponent(track.id)}`) },
    { label: "Playlist aus diesem Song zusammenstellen", icon: "sparkle", action: () => generatePlaylistDialog({ source: "song", value: track.id, label: track.title }) },
    track.artists?.[0]?.id ? { label: "Zum Künstler", icon: "person", action: () => navigate(`#/artist/${track.artists[0].id}`) } : null,
    track.album_id ? { label: "Zum Album", icon: "album", action: () => navigate(`#/album/${track.album_id}`) } : null,
    "sep",
    state.user?.is_admin || state.user?.server?.allow_file_download !== false ? { label: "Datei aufs Gerät laden", icon: "download", action: () => {
      const a = h("a", { href: `/api/tracks/${encodeURIComponent(track.id)}/file`, download: "" });
      document.body.append(a); a.click(); a.remove();
    } } : null,
    { label: "Details", icon: "info", action: () => trackDetails(track) },
  ];
  openMenu(event, items);
}

function trackDetails(track) {
  const kbps = track.bitrate ? `${Math.round(track.bitrate / 1000)} kbit/s` : "–";
  const rows = [
    ["Titel", track.title], ["Interpret", track.artist], ["Album", track.album], ["Jahr", track.year || "–"],
    ["Genre", track.genre || "–"], ["Dauer", fmtTime(track.duration)], ["Format", (track.codec || "?").toUpperCase()],
    ["Bitrate", kbps], ["Abtastrate", track.sample_rate ? `${(track.sample_rate / 1000).toFixed(1)} kHz` : "–"],
    ["Hinzugefügt", fmtDate(track.added_at)],
  ];
  const dl = h("dl", { class: "kv" });
  for (const [k, v] of rows) dl.append(h("dt", {}, k), h("dd", {}, String(v ?? "")));
  modal("Songdetails", dl, [{ label: "Schließen", primary: true, value: true }]);
}

// ---------------------------------------------------------------- Songliste
/**
 * tracks: Liste von Songs
 * opts: { album: bool (Albumspalte), cover: bool, added: bool, numbered: "index"|"track", context,
 *         playlistId, onReorder, discs: bool }
 */
export function trackList(tracks, opts = {}) {
  const {
    album: albumColumn = true, showCover = true, added = false, numbered = "index", context = null,
    playlistId = null, onReorder = null, discs = false, onChange = null,
  } = opts;
  const album = albumColumn && setting("list_album_column") !== false;
  const wrap = h("div", { class: `tracklist ${album ? "" : "no-album"} ${added ? "with-added" : ""}` });
  const head = h("div", { class: "tl-head" },
    h("div", { style: { textAlign: "right" } }, "#"), h("div", {}, "Titel"),
    album ? h("div", {}, "Album") : null, added ? h("div", {}, "Hinzugefügt") : null,
    h("div"), h("div", { style: { display: "flex", justifyContent: "flex-end" } }, icon("clock", "sm")), h("div"));
  wrap.append(head);

  const play = (index) => player.playTracks(tracks, index, context);
  let lastDisc = null;
  const multiDisc = discs && new Set(tracks.map((t) => t.disc_no || 1)).size > 1;

  tracks.forEach((track, index) => {
    if (multiDisc && (track.disc_no || 1) !== lastDisc) {
      lastDisc = track.disc_no || 1;
      wrap.append(h("div", { class: "tl-disc" }, icon("disc"), `CD ${lastDisc}`));
    }
    const num = numbered === "track" ? (track.track_no || index + 1) : index + 1;
    const row = h("div", { class: "tl-row", dataset: { id: track.id }, tabindex: "0" },
      h("div", { class: "num" },
        h("span", { class: "n" }, String(num)),
        h("span", { class: "eq" }, h("i"), h("i"), h("i")),
        h("span", { class: "play-icon" }, icon("play"))),
      h("div", { class: "title-cell" },
        showCover ? cover(track.cover, { size: 64 }) : null,
        h("div", { class: "title-text" },
          h("div", { class: "t-title" }, track.title),
          h("div", { class: "t-artists" }, artistLinks(track)))),
      album ? h("div", { class: "t-album" }, track.album_id ? h("a", { href: `#/album/${track.album_id}`, onclick: (e) => e.stopPropagation() }, track.album) : track.album) : null,
      added ? h("div", { class: "t-added" }, fmtDate(track.entry_added || track.liked_at || track.added_at)) : null,
      likeButton(track),
      h("div", { class: "t-duration" }, fmtTime(track.duration)),
      h("button", { class: "icon-btn more-btn", "aria-label": "Mehr", onclick: (e) => { e.stopPropagation(); trackMenu(e, track, { playlistId, entryId: track.entry_id, onRemove: () => { row.remove(); onChange?.(); } }); } }, icon("more")),
    );
    row.querySelector(".num").addEventListener("click", (e) => { e.stopPropagation(); play(index); });
    row.addEventListener("dblclick", () => play(index));
    row.addEventListener("click", () => {
      if (setting("single_click_play") || window.matchMedia("(hover: none), (max-width: 720px)").matches) play(index);
      else {
        wrap.querySelectorAll(".tl-row.selected").forEach((r) => r.classList.remove("selected"));
        row.classList.add("selected");
      }
    });
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") play(index); });
    row.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      trackMenu(e, track, { playlistId, entryId: track.entry_id, onRemove: () => { row.remove(); onChange?.(); } });
    });

    if (onReorder) {
      row.draggable = true;
      row.addEventListener("dragstart", (e) => {
        row.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(index));
        wrap._dragIndex = index;
      });
      row.addEventListener("dragend", () => row.classList.remove("dragging"));
      row.addEventListener("dragover", (e) => {
        e.preventDefault();
        const r = row.getBoundingClientRect();
        const before = e.clientY < r.top + r.height / 2;
        row.classList.toggle("drop-before", before);
        row.classList.toggle("drop-after", !before);
      });
      row.addEventListener("dragleave", () => row.classList.remove("drop-before", "drop-after"));
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        const before = row.classList.contains("drop-before");
        row.classList.remove("drop-before", "drop-after");
        const from = wrap._dragIndex;
        let to = index + (before ? 0 : 1);
        if (from === undefined || from === to || from + 1 === to) return;
        const moved = tracks.splice(from, 1)[0];
        if (from < to) to--;
        tracks.splice(to, 0, moved);
        onReorder(tracks);
      });
    }
    wrap.append(row);
  });
  return wrap;
}

// ---------------------------------------------------------------- Karten & Regale
export function playButton(onPlay, cls = "card-play") {
  return h("button", { class: cls, "aria-label": "Abspielen", onclick: (e) => { e.stopPropagation(); onPlay(); } }, icon("play"));
}

export function card({ title, subtitle, coverEl, href, onPlay, round = false }) {
  const el = h("div", { class: `card ${round ? "round" : ""}`, role: "link", tabindex: "0" },
    h("div", { class: "card-cover-wrap" }, coverEl, onPlay ? playButton(onPlay) : null),
    h("div", { class: "card-title ellipsis" }, title),
    subtitle ? h("div", { class: "card-sub" }, subtitle) : null);
  const go = () => navigate(href);
  el.addEventListener("click", go);
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  return el;
}

export async function playAlbum(albumId, shuffle = false) {
  const album = await api(`/albums/${albumId}`);
  if (shuffle && !player.shuffle) player.toggleShuffle();
  player.playTracks(album.tracks, 0, { type: "album", id: album.id, name: album.name, href: `#/album/${album.id}` });
}

export async function playArtist(artistId) {
  const tracks = await api(`/artists/${artistId}/tracks`);
  const artist = await api(`/artists/${artistId}`);
  player.playTracks(tracks, 0, { type: "artist", id: artistId, name: artist.name, href: `#/artist/${artistId}` });
}

export async function playPlaylist(id) {
  const pl = await api(`/playlists/${id}`);
  player.playTracks(pl.tracks, 0, { type: "playlist", id, name: pl.name, href: `#/playlist/${id}` });
}

export function albumCard(a, { subtitle } = {}) {
  const el = card({
    title: a.name, subtitle: subtitle ?? [a.year || null, a.artist].filter(Boolean).join(" • "),
    coverEl: cover(a.cover, { size: 300, fallback: "album" }), href: `#/album/${a.id}`,
    onPlay: () => playAlbum(a.id).catch((e) => toast(e.message, { error: true })),
  });
  el.addEventListener("contextmenu", (e) => { e.preventDefault(); albumMenu(e, a); });
  return el;
}

export function albumMenu(event, a) {
  const load = async () => (await api(`/albums/${a.id}`)).tracks;
  openMenu(event, [
    { title: a.name },
    { label: "Zur Warteschlange hinzufügen", icon: "addQueue", action: async () => { player.addToQueue(await load()); toast("Zur Warteschlange hinzugefügt"); } },
    { label: "Als Nächstes spielen", icon: "playNext", action: async () => { player.playNext(await load()); toast("Wird als Nächstes gespielt"); } },
    { label: "Zur Playlist hinzufügen …", icon: "plus", action: async () => addToPlaylistDialog(await load()) },
    a.artist_id ? { label: "Zum Künstler", icon: "person", action: () => navigate(`#/artist/${a.artist_id}`) } : null,
  ]);
}

export function artistCard(a) {
  return card({
    title: a.name, subtitle: "Künstler", round: true,
    coverEl: cover(a.cover, { size: 300, fallback: "person", round: true }), href: `#/artist/${a.id}`,
    onPlay: () => playArtist(a.id).catch((e) => toast(e.message, { error: true })),
  });
}

export function playlistSubtitle(p) {
  if (p.own === false) return `Von ${p.owner} · ${plural(p.track_count, "Song", "Songs")}`;
  return `${p.public ? "Veröffentlicht · " : ""}${plural(p.track_count, "Song", "Songs")}`;
}

export function playlistCard(p) {
  return card({
    title: p.name, subtitle: p.own === false || p.public ? playlistSubtitle(p) : p.description || playlistSubtitle(p),
    coverEl: mosaic(p.covers, { size: 300 }), href: `#/playlist/${p.id}`,
    onPlay: () => playPlaylist(p.id).catch((e) => toast(e.message, { error: true })),
  });
}

/** Cover der „Für dich gemacht“-Mixe: ein Albumcover mit farbigem Band und Namen (wie bei Spotify). */
function feedCover(m) {
  const el = h("div", { class: "cover feed-cover" });
  el.style.setProperty("--mix-color", m.color || "#535353");
  if (m.covers?.[0]) el.style.backgroundImage = `url("${coverUrl(m.covers[0], 300)}")`;
  el.append(h("span", { class: "feed-cover-label" }, m.name));
  return el;
}

export function mixCard(m) {
  const [kind, ...rest] = m.id.split(":");
  const value = rest.join(":");
  const href = `#/mix/${kind}/${encodeURIComponent(value)}`;
  return card({
    title: m.name, subtitle: m.subtitle || plural(m.track_count || 0, "Song", "Songs"),
    coverEl: m.feed ? feedCover(m) : mosaic(m.covers, { size: 300 }), href,
    onPlay: async () => {
      const tracks = await api(`/mix/${kind}?value=${encodeURIComponent(value)}&limit=${Number(setting("mix_size") || 60)}`);
      if (!tracks.length) { toast("Dieser Mix ist gerade leer"); return; }
      player.playTracks(tracks, 0, { type: "mix", id: `${kind}:${value}`, name: m.name, href });
    },
  });
}

export function shelf(title, items, { href, grid = false } = {}) {
  if (!items?.length) return document.createDocumentFragment();
  return h("section", { class: "section" },
    h("div", { class: "section-head" }, h("h2", {}, title), href ? h("a", { href }, "Alle anzeigen") : null),
    h("div", { class: grid ? "grid" : "shelf" }, items));
}
