// Suche: eigene Bibliothek + Spotify-Vorschläge zum Holen per spotDL

import { api, ApiError, state } from "./api.js";
import { albumCard, artistCard, navigate, playlistCard, playButton, playAlbum, playArtist, shelf, trackList } from "./components.js";
import { downloadButton, playTrackIds } from "./downloads.js";
import { player } from "./player.js";
import { clear, colorFor, cover, debounce, fmtTime, h, icon, plural, remoteCover } from "./ui.js";

const LINK = /(open\.spotify\.com|spotify\.link|youtube\.com|youtu\.be)\//i;

export function searchView(_params, query) {
  const input = document.getElementById("search-input");
  const root = h("div", { class: "view-inner" });
  const localBox = h("div");
  const spotifyBox = h("div");
  root.append(localBox, spotifyBox);
  let seq = 0;
  let spotifyCtrl = null;

  const run = async (q, { immediate = false } = {}) => {
    const my = ++seq;
    const hash = `#/search${q ? `?q=${encodeURIComponent(q)}` : ""}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
    spotifyCtrl?.abort();
    if (!q) {
      clear(spotifyBox);
      await renderBrowse(localBox);
      return;
    }
    const isLink = LINK.test(q);
    if (isLink) {
      clear(localBox);
    } else {
      try {
        const res = await api(`/search?q=${encodeURIComponent(q)}`);
        if (my !== seq) return;
        renderLocal(localBox, res, q);
      } catch (e) {
        if (my !== seq) return;
        clear(localBox).append(h("p", { class: "error" }, e.message));
      }
    }
    renderSpotifyLoading(spotifyBox, isLink);
    const doSpotify = async () => {
      if (my !== seq) return;
      spotifyCtrl = new AbortController();
      try {
        const res = await api(`/spotify/search?q=${encodeURIComponent(q)}`, { signal: spotifyCtrl.signal });
        if (my !== seq) return;
        renderSpotify(spotifyBox, res, q);
      } catch (e) {
        if (e.name === "AbortError" || my !== seq) return;
        renderSpotifyError(spotifyBox, e, q);
      }
    };
    if (immediate || isLink) doSpotify();
    else setTimeout(doSpotify, 450);
  };

  const onInput = debounce(() => run(input.value.trim()), 250);
  input.value = query.get("q") || "";
  input.addEventListener("input", onInput);
  const onKey = (e) => { if (e.key === "Enter") run(input.value.trim(), { immediate: true }); };
  input.addEventListener("keydown", onKey);
  run(input.value.trim(), { immediate: true });
  if (!input.value && window.matchMedia("(hover: hover)").matches) setTimeout(() => input.focus(), 50);

  return {
    el: root, color: "#121212", search: true,
    destroy: () => {
      input.removeEventListener("input", onInput);
      input.removeEventListener("keydown", onKey);
      spotifyCtrl?.abort();
      seq++;
    },
  };
}

// ---------------------------------------------------------------- Stöbern (leere Suche)
async function renderBrowse(box) {
  const genres = await api("/genres").catch(() => []);
  clear(box);
  box.append(h("h2", { style: { fontSize: "24px", margin: "8px 0 16px" } }, "Alles durchsuchen"));
  const grid = h("div", { class: "genre-grid" });
  const tile = (name, color, coverId, href) => {
    const t = h("div", { class: "genre-tile", style: { background: color }, onclick: () => navigate(href) }, name);
    if (coverId) t.append(cover(coverId, { size: 160 }));
    return t;
  };
  grid.append(
    tile("Zufallsmix", "#e13300", null, "#/mix/random/"),
    tile("Lieblingssongs", "#5038a0", null, "#/liked"),
    tile("Downloads", "#0b6e3a", null, "#/downloads"),
  );
  for (const g of genres) grid.append(tile(g.name, colorFor(g.name), g.cover, `#/mix/genre/${encodeURIComponent(g.name)}`));
  box.append(grid,
    h("div", { class: "notice", style: { marginTop: "24px" } }, icon("info"),
      h("div", {}, h("strong", {}, "Tipp: "), "Such nach einem Song oder füge einen Spotify-Link ein (Song, Album, Playlist, Künstler). Ist er nicht in deiner Bibliothek, kannst du ihn mit einem Klick holen.")));
}

// ---------------------------------------------------------------- Eigene Bibliothek
function renderLocal(box, res, q) {
  clear(box);
  const hasAny = res.tracks.length || res.albums.length || res.artists.length || res.playlists.length;
  if (!hasAny) {
    box.append(h("div", { class: "notice" }, icon("search"),
      h("div", {}, h("strong", {}, `„${q}“ ist nicht in deiner Bibliothek. `), "Unten findest du passende Vorschläge von Spotify zum Holen.")));
    return;
  }
  const grid = h("div", { class: "search-results" });
  // Top-Ergebnis
  let top = null;
  const qn = q.toLowerCase();
  const artistHit = res.artists.find((a) => a.name.toLowerCase() === qn)
    || res.artists.find((a) => a.name.toLowerCase().startsWith(qn));
  if (artistHit) {
    top = h("div", { class: "top-result round", onclick: () => navigate(`#/artist/${artistHit.id}`) },
      cover(artistHit.cover, { size: 300, round: true, fallback: "person" }),
      h("div", { class: "tr-title" }, artistHit.name), h("span", { class: "badge" }, "Künstler"),
      playButton(() => playArtist(artistHit.id)));
  } else if (res.tracks[0]) {
    const t = res.tracks[0];
    top = h("div", { class: "top-result", onclick: () => player.playTracks([t], 0) },
      cover(t.cover, { size: 300 }),
      h("div", { class: "tr-title" }, t.title), h("div", { class: "muted" }, h("span", { class: "badge" }, "Song"), " ", t.artist),
      playButton(() => player.playTracks([t], 0)));
  } else if (res.albums[0]) {
    const a = res.albums[0];
    top = h("div", { class: "top-result", onclick: () => navigate(`#/album/${a.id}`) },
      cover(a.cover, { size: 300 }), h("div", { class: "tr-title" }, a.name),
      h("div", { class: "muted" }, h("span", { class: "badge" }, "Album"), " ", a.artist),
      playButton(() => playAlbum(a.id)));
  }
  if (top) grid.append(h("section", {}, h("div", { class: "section-head" }, h("h2", {}, "Top-Ergebnis")), top));
  if (res.tracks.length) {
    grid.append(h("section", {}, h("div", { class: "section-head" }, h("h2", {}, "Songs")),
      trackList(res.tracks.slice(0, 4), { album: false, context: { type: "search", id: `search:${q}`, name: `Suche „${q}“` } })));
  }
  box.append(grid);
  if (res.tracks.length > 4) {
    box.append(h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, "Alle Songs")),
      trackList(res.tracks, { context: { type: "search", id: `search:${q}`, name: `Suche „${q}“` } })));
  }
  box.append(
    shelf("Künstler", res.artists.map(artistCard)),
    shelf("Alben", res.albums.map((a) => albumCard(a))),
    shelf("Playlists", res.playlists.map(playlistCard)),
  );
}

// ---------------------------------------------------------------- Spotify
function spotifyHead(title, sub) {
  return h("div", { class: "section-head" },
    h("h2", {}, icon("cloud"), title), sub ? h("span", { class: "muted", style: { fontSize: "13px" } }, sub) : null);
}

function renderSpotifyLoading(box, isLink) {
  clear(box);
  const rows = h("div");
  for (let i = 0; i < (isLink ? 3 : 5); i++) {
    rows.append(h("div", { class: "sp-row" }, h("div", { class: "cover skeleton" }),
      h("div", {}, h("div", { class: "skeleton", style: { height: "14px", width: "60%", marginBottom: "6px" } }),
        h("div", { class: "skeleton", style: { height: "12px", width: "35%" } }))));
  }
  box.append(h("div", { class: "spotify-box" }, spotifyHead(isLink ? "Link wird geladen …" : "Vorschläge von Spotify"), rows));
}

function renderSpotifyError(box, err, q) {
  clear(box);
  const content = h("div", { class: "spotify-box" }, spotifyHead("Von Spotify holen"));
  if (err instanceof ApiError && err.status === 503) {
    content.append(h("div", { class: "notice" }, icon("alert"), h("div", {},
      h("strong", {}, "spotDL ist noch nicht eingerichtet. "),
      state.user?.is_admin ? ["Installiere es unter ", h("a", { href: "#/settings", class: "link", style: { color: "var(--accent)" } }, "Einstellungen"), " (ein Klick)."] : "Bitte den Admin, es in den Einstellungen zu installieren.")));
  } else {
    content.append(h("div", { class: "notice" }, icon("alert"), h("div", {},
      h("strong", {}, "Spotify-Suche gerade nicht möglich. "), err.message,
      h("br"), "Du kannst den Begriff trotzdem direkt an spotDL geben – es sucht dann selbst den besten Treffer.")),
    h("div", { class: "row-actions" }, downloadButton({ query: q, kind: "search", title: q, subtitle: "Suchbegriff" }, { label: `„${q}“ holen`, big: true })));
  }
  box.append(content);
}

function spotifyTrackRow(t) {
  const item = {
    query: t.url, kind: "track", title: t.name, subtitle: t.artists.join(", "), image: t.image,
    spotify_ids: [t.id], total: 1,
  };
  return h("div", { class: "sp-row" },
    remoteCover(t.image),
    h("div", { style: { minWidth: 0 } },
      h("div", { class: "t-title" }, t.explicit ? h("span", { class: "t-flag" }, "E") : null, t.name),
      h("div", { class: "t-sub" }, t.artists.join(", "))),
    h("div", { class: "t-album" }, t.album),
    h("div", { class: "t-duration" }, fmtTime((t.duration_ms || 0) / 1000)),
    downloadButton(item, { libraryId: t.library_id }));
}

function renderSpotify(box, res, q) {
  clear(box);
  const content = h("div", { class: "spotify-box" });
  if (res.mode === "link") {
    const it = res.item;
    const kindLabel = { track: "Song", album: "Album", playlist: "Playlist", artist: "Künstler", link: "Link", youtube: "YouTube" }[it.kind] || "Link";
    const missing = it.tracks.filter((t) => !t.library_id);
    const allItem = {
      query: it.url, kind: it.kind === "youtube" || it.kind === "link" ? "track" : it.kind, title: it.title, subtitle: it.subtitle,
      image: it.image, spotify_ids: it.tracks.map((t) => t.id), total: it.kind === "artist" ? 0 : it.tracks.length,
    };
    const inLib = it.tracks.length - missing.length;
    content.append(spotifyHead("Spotify-Link"),
      h("div", { class: "sp-link-head" }, remoteCover(it.image),
        h("div", { style: { minWidth: 0 } },
          h("span", { class: "badge" }, kindLabel),
          h("h3", { class: "ellipsis" }, it.title),
          h("div", { class: "muted" }, [it.subtitle, it.tracks.length ? plural(it.tracks.length, "Song", "Songs") : null,
            inLib ? `${inLib} schon in deiner Bibliothek` : null].filter(Boolean).join(" · ")),
          h("div", { class: "row-actions", style: { marginTop: "12px" } },
            it.kind === "track" && it.tracks[0]
              ? downloadButton({ ...allItem, kind: "track" }, { libraryId: it.tracks[0].library_id, label: "Song holen", big: true })
              : missing.length || !it.tracks.length
                ? downloadButton(allItem, { label: it.kind === "artist" ? "Alle Songs des Künstlers holen" : it.tracks.length ? `Alle ${missing.length} fehlenden holen` : "Holen", big: true })
                : h("span", { class: "badge green" }, icon("check", "sm"), "Alles schon da"),
            inLib ? h("button", { class: "btn btn-small btn-outline", onclick: () => playTrackIds(it.tracks.map((t) => t.library_id).filter(Boolean)) }, icon("play", "sm"), "Vorhandene abspielen") : null))));
    if (it.kind !== "track") {
      const list = h("div");
      for (const t of it.tracks.slice(0, 300)) list.append(spotifyTrackRow(t));
      content.append(list);
      if (it.tracks.length > 300) content.append(h("p", { class: "muted" }, `… und ${it.tracks.length - 300} weitere`));
    }
    box.append(content);
    return;
  }

  const tracks = res.tracks || [];
  const missing = tracks.filter((t) => !t.library_id).length;
  content.append(spotifyHead(missing ? "Nicht dabei? Von Spotify holen" : "Vorschläge von Spotify",
    tracks.length ? `${missing} von ${tracks.length} nicht in deiner Bibliothek` : ""));
  if (!tracks.length) {
    content.append(h("p", { class: "muted" }, "Spotify hat nichts gefunden. Versuch es mit „Künstler - Titel“ oder füge einen Link ein."));
  }
  const list = h("div");
  for (const t of tracks) list.append(spotifyTrackRow(t));
  content.append(list);

  if (res.albums?.length) {
    const shelfEl = h("div", { class: "shelf", style: { marginTop: "16px" } });
    for (const a of res.albums) {
      const cardEl = h("div", { class: "card", onclick: () => navigate(`#/search?q=${encodeURIComponent(a.url)}`) },
        h("div", { class: "card-cover-wrap" }, remoteCover(a.image)),
        h("div", { class: "card-title ellipsis" }, a.name),
        h("div", { class: "card-sub" }, [a.year, a.artists.join(", ")].filter(Boolean).join(" • ")));
      shelfEl.append(cardEl);
    }
    content.append(h("div", { class: "section-head", style: { marginTop: "20px" } }, h("h2", { style: { fontSize: "18px" } }, "Alben von Spotify"),
      h("span", { class: "muted", style: { fontSize: "13px" } }, "Anklicken für alle Songs")), shelfEl);
  }
  if (res.artists?.length) {
    const shelfEl = h("div", { class: "shelf", style: { marginTop: "8px" } });
    for (const a of res.artists) {
      shelfEl.append(h("div", { class: "card round", onclick: () => navigate(`#/search?q=${encodeURIComponent(a.url)}`) },
        h("div", { class: "card-cover-wrap" }, remoteCover(a.image)),
        h("div", { class: "card-title ellipsis" }, a.name), h("div", { class: "card-sub" }, "Künstler")));
    }
    content.append(h("div", { class: "section-head", style: { marginTop: "20px" } }, h("h2", { style: { fontSize: "18px" } }, "Künstler auf Spotify")), shelfEl);
  }
  box.append(content);
}
