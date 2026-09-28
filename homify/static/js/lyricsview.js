// Songtext-Ansicht: mitlaufend (LRC mit Zeitstempeln) oder als einfacher Text

import { api, on } from "./api.js";
import { artistLinks } from "./components.js";
import { player } from "./player.js";
import { clear, cover, h, icon } from "./ui.js";
import { setting } from "./usersettings.js";

const cache = new Map();  // track_id -> Songtext | null

async function fetchLyrics(id) {
  if (cache.has(id)) return cache.get(id);
  let data = null;
  try { data = await api(`/tracks/${encodeURIComponent(id)}/lyrics`); } catch (e) { if (e.status !== 404) throw e; }
  cache.set(id, data);
  return data;
}

export async function lyricsView() {
  const root = h("div", { class: "view-inner lyrics-page" });
  const host = h("div", { class: "lyrics" });
  root.append(host);
  let lines = [];
  let activeIndex = -1;
  let userScrolled = 0;
  let trackId = null;

  const highlight = ({ current }) => {
    if (!lines.length || !host.classList.contains("synced")) return;
    let i = -1;
    for (let k = 0; k < lines.length; k++) {
      if (lines[k].t <= current + 0.15) i = k; else break;
    }
    if (i === activeIndex) return;
    activeIndex = i;
    host.querySelectorAll(".lyrics-line").forEach((el, k) => {
      el.classList.toggle("active", k === i);
      el.classList.toggle("past", k < i);
    });
    const el = host.querySelectorAll(".lyrics-line")[i];
    // Mitscrollen – außer der Nutzer hat gerade selbst gescrollt
    if (el && Date.now() - userScrolled > 4000) el.scrollIntoView({ block: "center", behavior: "smooth" });
  };

  const render = async () => {
    const t = player.current;
    lines = [];
    activeIndex = -1;
    clear(host);
    host.className = "lyrics";
    if (!t) {
      host.append(h("div", { class: "empty" }, h("div", { class: "icon-big" }, icon("mic")), h("h2", {}, "Gerade läuft nichts"),
        h("p", {}, "Spiel einen Song ab – hier erscheint dann sein Songtext.")));
      return;
    }
    trackId = t.id;
    host.append(h("div", { class: "lyrics-head" }, cover(t.cover, { size: 160 }),
      h("div", { style: { minWidth: 0 } }, h("div", { class: "ellipsis", style: { fontWeight: 800, fontSize: "18px" } }, t.title),
        h("div", { class: "muted ellipsis" }, artistLinks(t)))));
    let data;
    try { data = await fetchLyrics(t.id); } catch (e) {
      host.append(h("p", { class: "error" }, e.message));
      return;
    }
    if (trackId !== t.id) return;
    if (!data?.lines?.length) {
      host.append(h("div", { class: "empty" }, h("div", { class: "icon-big" }, icon("mic")), h("h2", {}, "Kein Songtext gefunden"),
        h("p", {}, "Homify zeigt Songtexte aus .lrc-Dateien mit gleichem Namen neben dem Song oder aus den Tags der Datei. Neue Downloads bringen ihn mit, wenn „Songtexte mitladen“ an ist.")));
      return;
    }
    lines = data.lines;
    host.classList.add(data.synced ? "synced" : "plain");
    for (const line of lines) {
      if (!line.text) { host.append(h("div", { class: "lyrics-line gap" })); continue; }
      const el = h("p", { class: "lyrics-line" }, line.text);
      if (data.synced) el.addEventListener("click", () => { userScrolled = 0; player.seek(line.t); if (!player.playing) player.play(); });
      host.append(el);
    }
    highlight(player.time());
  };

  const view = document.getElementById("view");
  const onWheel = () => { userScrolled = Date.now(); };
  view.addEventListener("wheel", onWheel, { passive: true });
  view.addEventListener("touchmove", onWheel, { passive: true });
  const offs = [
    on("track", async () => {
      if (player.current?.id === trackId) return;
      render();
      const color = setting("dynamic_colors") !== false ? await trackColor(player.current) : null;
      document.getElementById("main").style.setProperty("--header-color", color || "#535353");
    }),
    on("time", highlight),
  ];
  await render();
  const color = setting("dynamic_colors") !== false ? await trackColor(player.current) : null;
  return {
    el: root, color: color || "#535353", title: "Songtext",
    destroy: () => {
      offs.forEach((off) => off());
      view.removeEventListener("wheel", onWheel);
      view.removeEventListener("touchmove", onWheel);
    },
  };
}

async function trackColor(track) {
  if (!track?.album_id) return null;
  try { return (await api(`/albums/${track.album_id}`)).color; } catch { return null; }
}
