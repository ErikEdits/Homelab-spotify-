// Downloads über spotDL: Warteschlange, Status-Abfrage, Download-Seite

import { api, emit, on, state } from "./api.js";
import { navigate } from "./components.js";
import { player } from "./player.js";
import { clear, confirmDialog, cover, fmtDate, h, icon, remoteCover, toast } from "./ui.js";
import { confirmDelete } from "./usersettings.js";

export const downloads = { jobs: [], active: 0, byQuery: new Map() };
let timer = null;
let fastUntil = 0;
const known = new Map(); // id -> status (für „fertig“-Meldungen)

export async function refreshDownloads() {
  try {
    const data = await api("/downloads");
    downloads.jobs = data.jobs;
    downloads.active = data.active;
    downloads.byQuery = new Map(data.jobs.map((j) => [j.query, j]));
    for (const job of data.jobs) {
      const prev = known.get(job.id);
      if (prev && prev !== job.status && ["done", "partial"].includes(job.status)) {
        toast(`„${job.title}“ ist jetzt in deiner Bibliothek`);
        emit("library-changed");
      } else if (prev && prev !== job.status && job.status === "error") {
        toast(`Download fehlgeschlagen: ${job.title}`, { error: true });
      }
      known.set(job.id, job.status);
    }
    emit("downloads", downloads);
  } catch { /* offline o. nicht angemeldet */ }
  schedule();
}

function schedule() {
  clearTimeout(timer);
  const scanning = downloads.jobs.some((j) => ["done", "partial"].includes(j.status) && !j.track_ids?.length && Date.now() / 1000 - (j.finished_at || 0) < 120);
  const fast = downloads.active > 0 || scanning || Date.now() < fastUntil;
  timer = setTimeout(refreshDownloads, fast ? 2000 : 30000);
}

export async function startDownload({ query, kind = "track", title = "", subtitle = "", image = "", spotify_ids = [], total = 0 }) {
  if (state.user && !state.user.can_download) {
    toast("Du darfst leider nichts herunterladen – frag den Admin.", { error: true });
    return null;
  }
  try {
    const job = await api("/downloads", { method: "POST", body: { query, kind, title, subtitle, image, spotify_ids, total } });
    toast(`„${title || query}“ wird geholt …`);
    fastUntil = Date.now() + 15000;
    await refreshDownloads();
    return job;
  } catch (e) {
    toast(e.message, { error: true });
    return null;
  }
}

export async function playTrackIds(ids, context = null) {
  if (!ids?.length) return;
  const tracks = await api("/tracks/lookup", { method: "POST", body: { ids } });
  if (tracks.length) player.playTracks(tracks, 0, context);
}

// ---------------------------------------------------------------- Button mit Download-Status
/**
 * Zeigt je nach Zustand: „Holen“ → „Wartet“ → „Lädt“ → „Abspielen“.
 * libraryId: falls der Song schon in der Bibliothek ist.
 */
export function downloadButton(item, { libraryId = null, label = "Holen", big = false } = {}) {
  const host = h("div", { class: "sp-action" });
  const render = () => {
    clear(host);
    const job = downloads.byQuery.get(item.query);
    if (libraryId) {
      host.append(h("button", { class: `btn btn-small ${big ? "btn-primary" : "btn-outline"}`, onclick: (e) => { e.stopPropagation(); playTrackIds([libraryId]); } },
        icon("play", "sm"), "In Bibliothek"));
      return;
    }
    if (job && ["queued", "running"].includes(job.status)) {
      const pct = job.total ? ` ${job.done}/${job.total}` : "";
      host.append(h("span", { class: "badge green" }, h("span", { class: "spinner" }), job.status === "queued" ? "Wartet" : `Lädt${pct}`));
      return;
    }
    if (job && ["done", "partial"].includes(job.status)) {
      if (job.track_ids?.length) {
        host.append(h("button", { class: "btn btn-small btn-primary", onclick: (e) => { e.stopPropagation(); playTrackIds(job.track_ids); } }, icon("play", "sm"), "Abspielen"));
      } else {
        host.append(h("span", { class: "badge green" }, h("span", { class: "spinner" }), "Wird eingelesen"));
      }
      return;
    }
    const btn = h("button", { class: `btn btn-small ${big ? "btn-primary" : "btn-outline"}`, onclick: async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      await startDownload(item);
      render();
    } }, icon("download", "sm"), job?.status === "error" ? "Nochmal versuchen" : label);
    host.append(btn);
  };
  render();
  const off = on("downloads", () => {
    if (!host.isConnected) { off(); return; }
    render();
  });
  return host;
}

// ---------------------------------------------------------------- Download-Seite
const STATUS = {
  queued: ["Wartet", ""], running: ["Lädt", "green"], done: ["Fertig", "green"], partial: ["Teilweise", "orange"],
  error: ["Fehler", "red"], cancelled: ["Abgebrochen", ""],
};

export async function downloadsView() {
  const input = h("input", { class: "input", placeholder: "Spotify-Link (Song, Album, Playlist) oder „Künstler - Titel“", "aria-label": "Link oder Suchbegriff" });
  const form = h("form", { class: "dl-form", onsubmit: async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    if (/open\.spotify\.com|spotify\.link/.test(q)) { navigate(`#/search?q=${encodeURIComponent(q)}`); return; }
    const job = await startDownload({ query: q, kind: "search", title: q, subtitle: "Suchbegriff" });
    if (job) input.value = "";
  } }, input, h("button", { class: "btn btn-primary", type: "submit" }, icon("download", "sm"), "Holen"));
  const list = h("div");
  const openLogs = new Set();
  const draw = () => {
    clear(list);
    if (!downloads.jobs.length) {
      list.append(h("div", { class: "empty" }, h("div", { class: "icon-big" }, icon("cloud")), h("h2", {}, "Noch keine Downloads"),
        h("p", {}, "Suche oben nach einem Song oder füge einen Spotify-Link ein. Homify holt ihn per spotDL auf dein NAS.")));
      return;
    }
    for (const job of downloads.jobs) {
      const [label, color] = STATUS[job.status] || [job.status, ""];
      const running = job.status === "running";
      const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
      const actions = h("div", { class: "dl-actions" });
      if (job.track_ids?.length) {
        actions.append(h("button", { class: "btn btn-small btn-primary", onclick: () => playTrackIds(job.track_ids, { type: "download", name: job.title }) }, icon("play", "sm"), "Abspielen"));
      }
      if (["queued", "running"].includes(job.status)) {
        actions.append(h("button", { class: "icon-btn", title: "Abbrechen", "aria-label": "Abbrechen", onclick: async () => { await api(`/downloads/${job.id}/cancel`, { method: "POST" }); refreshDownloads(); } }, icon("close")));
      } else {
        if (["error", "partial", "cancelled"].includes(job.status)) {
          actions.append(h("button", { class: "icon-btn", title: "Nochmal versuchen", "aria-label": "Nochmal versuchen", onclick: async () => { await api(`/downloads/${job.id}/retry`, { method: "POST" }); fastUntil = Date.now() + 10000; refreshDownloads(); } }, icon("refresh")));
        }
        actions.append(h("button", { class: "icon-btn", title: "Aus Liste entfernen", "aria-label": "Entfernen", onclick: async () => {
          if (!await confirmDelete(() => confirmDialog("Aus der Liste entfernen?", `„${job.title}“ verschwindet aus der Download-Liste. Geholte Songs bleiben in der Bibliothek.`, { okLabel: "Entfernen" }))) return;
          await api(`/downloads/${job.id}`, { method: "DELETE" });
          refreshDownloads();
        } }, icon("trash")));
      }
      if (job.log?.length) {
        actions.append(h("button", { class: "icon-btn", title: "Protokoll", "aria-label": "Protokoll", onclick: () => { openLogs.has(job.id) ? openLogs.delete(job.id) : openLogs.add(job.id); draw(); } }, icon("list")));
      }
      const el = h("div", { class: "dl-job" },
        job.image ? remoteCover(job.image) : cover(null, { fallback: "cloud" }),
        h("div", { style: { minWidth: 0 } },
          h("div", { class: "dl-title ellipsis" }, job.title),
          h("div", { class: "dl-sub ellipsis" }, [job.subtitle, job.kind === "album" ? "Album" : job.kind === "playlist" ? "Playlist" : job.kind === "artist" ? "Künstler" : null].filter(Boolean).join(" · ")),
          h("div", { class: "dl-msg" }, h("span", { class: `badge ${color}` }, running ? h("span", { class: "spinner" }) : null, label),
            " ", job.total > 1 ? `${job.done}/${job.total} Songs · ` : "", job.message || fmtDate(job.finished_at || job.created_at)),
          running ? h("div", { class: `progress ${job.total ? "" : "indeterminate"}` }, h("div", { style: { width: `${pct}%` } })) : null),
        actions,
        openLogs.has(job.id) ? h("div", { class: "log" }, job.log.join("\n")) : null);
      list.append(el);
    }
    if (downloads.jobs.some((j) => !["queued", "running"].includes(j.status))) {
      list.append(h("button", { class: "btn btn-small btn-outline", style: { marginTop: "12px" }, onclick: async () => { await api("/downloads", { method: "DELETE" }); refreshDownloads(); } }, "Erledigte entfernen"));
    }
  };
  draw();
  const off = on("downloads", draw);
  fastUntil = Date.now() + 5000;
  refreshDownloads();
  const root = h("div", { class: "view-inner" },
    h("h1", { class: "page-title" }, "Downloads"),
    h("p", { class: "muted", style: { marginTop: "-12px", marginBottom: "20px" } },
      "Songs, die du nicht hast, holt Homify über spotDL (Spotify-Infos + Audio von YouTube Music) direkt in deinen Musikordner."),
    form, list);
  return { el: root, color: "#0b6e3a", destroy: off };
}
