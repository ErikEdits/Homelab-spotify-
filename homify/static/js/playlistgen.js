// „Playlist zusammenstellen“: Homify baut automatisch eine Playlist nach deiner Wahl

import { api, loadPlaylists } from "./api.js";
import { clear, cover, debounce, h, icon, modal, plural, toast } from "./ui.js";

const SOURCES = [
  ["song", "Ähnlich wie ein Song", "radio"],
  ["artist", "Ähnlich wie ein Künstler", "person"],
  ["genre", "Genre", "disc"],
  ["decade", "Jahrzehnt", "clock"],
  ["top", "Deine Top-Songs", "note"],
  ["liked", "Lieblingssongs gemischt", "heart"],
  ["new", "Neu hinzugefügt", "plus"],
  ["rediscover", "Wiederentdecken", "refresh"],
  ["random", "Zufall", "shuffle"],
];

const decadeName = (d) => (Number(d) < 2000 ? `Die ${String(d).slice(2)}er` : `Die ${d}er`);

/**
 * Dialog öffnen. preset: {source, value, label} – z. B. aus dem Menü eines Songs oder von der Künstlerseite.
 * Legt die Playlist an und öffnet sie.
 */
export async function generatePlaylistDialog(preset = {}) {
  const opts = await api("/playlists/generator");
  let source = preset.source || "random";
  let value = preset.value || "";
  let label = preset.label || "";
  let nameTouched = false;

  const nameInput = h("input", { class: "input", maxlength: "200", placeholder: "Name der Playlist" });
  nameInput.addEventListener("input", () => { nameTouched = !!nameInput.value.trim(); });
  const count = h("input", { type: "range", min: "10", max: "200", step: "5", value: "50", "aria-label": "Anzahl Songs" });
  const countOut = h("output", { class: "range-value" });
  const showCount = () => { countOut.textContent = `${count.value} Songs`; };
  count.addEventListener("input", showCount);
  showCount();
  const pub = h("input", { type: "checkbox" });
  const chips = h("div", { class: "gen-sources" });
  const detail = h("div", { class: "gen-detail" });

  const suggestion = () => ({
    song: label ? `Ähnlich wie ${label}` : "",
    artist: label ? `${label} & Ähnliches` : "",
    genre: value ? `${value} Mix` : "",
    decade: value ? decadeName(value) : "",
    top: "Meine Top-Songs", liked: "Lieblingssongs gemischt", new: "Neu in der Bibliothek",
    rediscover: "Wiederentdecken", random: "Zufallsmix",
  })[source];
  const suggest = () => { if (!nameTouched) nameInput.value = suggestion() || ""; };

  // Suche für „Ähnlich wie ein Song/Künstler“
  const picker = (kind) => {
    const input = h("input", { class: "input", type: "search", placeholder: kind === "song" ? "Song suchen …" : "Künstler suchen …" });
    const results = h("div", { class: "gen-results" });
    const chosen = h("div", { class: "muted", style: { fontSize: "13px", minHeight: "18px" } }, label ? ["Gewählt: ", h("strong", {}, label)] : "");
    input.addEventListener("input", debounce(async () => {
      const q = input.value.trim();
      clear(results);
      if (!q) return;
      const res = await api(`/search?q=${encodeURIComponent(q)}`).catch(() => ({ tracks: [], artists: [] }));
      const items = kind === "song" ? res.tracks.slice(0, 6) : res.artists.slice(0, 6);
      if (!items.length) results.append(h("p", { class: "muted", style: { fontSize: "13px" } }, "Nichts gefunden."));
      for (const it of items) {
        const row = h("button", { type: "button", class: "list-row" },
          cover(kind === "song" ? it.cover : it.cover, { size: 64, round: kind === "artist", fallback: kind === "song" ? "note" : "person" }),
          h("div", { style: { minWidth: 0, textAlign: "left" } },
            h("div", { class: "ellipsis" }, kind === "song" ? it.title : it.name),
            kind === "song" ? h("div", { class: "muted ellipsis", style: { fontSize: "12px" } }, it.artist) : null));
        row.addEventListener("click", () => {
          value = it.id;
          label = kind === "song" ? it.title : it.name;
          chosen.replaceChildren("Gewählt: ", h("strong", {}, label));
          clear(results);
          input.value = "";
          suggest();
        });
        results.append(row);
      }
    }, 250));
    return h("div", {}, input, results, chosen);
  };

  const hint = (text) => h("p", { class: "muted", style: { fontSize: "13px", margin: "4px 0 0" } }, text);
  const drawDetail = () => {
    clear(detail);
    if (source === "song" || source === "artist") detail.append(picker(source));
    else if (source === "genre") {
      if (!opts.genres.length) { detail.append(hint("In deiner Bibliothek sind noch keine Genres eingetragen.")); return; }
      if (!value) value = opts.genres[0].name;
      const sel = h("select", { class: "input", "aria-label": "Genre" },
        opts.genres.map((g) => h("option", { value: g.name, selected: g.name === value }, `${g.name} (${plural(g.count, "Song", "Songs")})`)));
      sel.addEventListener("change", () => { value = sel.value; suggest(); });
      detail.append(sel);
    } else if (source === "decade") {
      if (!opts.decades.length) { detail.append(hint("Deine Songs haben noch keine Jahreszahlen.")); return; }
      if (!value) value = String(opts.decades[0].value);
      const sel = h("select", { class: "input", "aria-label": "Jahrzehnt" },
        opts.decades.map((d) => h("option", { value: d.value, selected: String(d.value) === String(value) }, `${decadeName(d.value)} (${plural(d.count, "Song", "Songs")})`)));
      sel.addEventListener("change", () => { value = sel.value; suggest(); });
      detail.append(sel);
    } else if (source === "top") {
      detail.append(hint(opts.played ? "Deine meistgehörten Songs, der meistgehörte zuerst." : "Du hast noch nichts gehört – spiel erst ein paar Songs."));
    } else if (source === "liked") {
      detail.append(hint(opts.liked ? `Aus deinen ${opts.liked} Lieblingssongs, bunt gemischt.` : "Du hast noch keine Lieblingssongs (Herz-Symbol)."));
    } else if (source === "new") detail.append(hint("Die zuletzt dazugekommenen Songs, neueste zuerst."));
    else if (source === "rediscover") detail.append(hint("Songs, die du länger nicht gehört hast – Lieblingssongs zuerst."));
    else detail.append(hint(`Quer durch alle ${plural(opts.tracks, "Song", "Songs")}.`));
  };

  for (const [key, text, iconName] of SOURCES) {
    const chip = h("button", { type: "button", class: `gen-chip ${key === source ? "active" : ""}`, dataset: { source: key } }, icon(iconName, "sm"), text);
    chip.addEventListener("click", () => {
      if (key === source) return;
      source = key;
      value = "";
      label = "";
      chips.querySelectorAll(".gen-chip").forEach((c) => c.classList.toggle("active", c.dataset.source === key));
      drawDetail();
      suggest();
    });
    chips.append(chip);
  }
  drawDetail();
  suggest();

  const body = h("div", { class: "gen-dialog" },
    h("div", { class: "field" }, h("span", {}, "Grundlage"), chips),
    detail,
    h("label", { class: "field", style: { marginTop: "16px" } }, h("span", {}, "Länge"), h("div", { class: "range" }, count, countOut)),
    h("label", { class: "field" }, h("span", {}, "Name"), nameInput),
    h("label", { class: "check" }, pub, "Gleich für alle Homify-Benutzer veröffentlichen"));

  const created = await modal("Playlist zusammenstellen", body, [
    { label: "Abbrechen", value: null },
    { label: "Zusammenstellen", primary: true, action: async () => {
      if ((source === "song" || source === "artist") && !value) {
        toast(source === "song" ? "Bitte erst einen Song auswählen" : "Bitte erst einen Künstler auswählen", { error: true });
        return false;
      }
      try {
        return await api("/playlists/generate", { method: "POST", body: {
          source, value: String(value || ""), count: Number(count.value), name: nameInput.value.trim(), public: pub.checked,
        } });
      } catch (e) {
        toast(e.message, { error: true });
        return false;
      }
    } },
  ]);
  if (!created) return null;
  toast(`„${created.name}“ mit ${plural(created.track_count, "Song", "Songs")} erstellt${created.public ? " und veröffentlicht" : ""}`);
  await loadPlaylists().catch(() => {});
  location.hash = `#/playlist/${created.id}`;
  return created;
}
