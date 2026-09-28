// Persönliche Einstellungen (pro Benutzer, auf allen Geräten gleich) – Teil der 100 Einstellungen.
// Werte kommen vom Server (/api/me/settings), werden im Browser zwischengespeichert und wirken sofort.

import { api, emit, prefs } from "./api.js";

let schema = prefs.get("settingsSchema", null);  // {settings: [...], eq_presets: {...}, count}
let values = prefs.get("settingsValues", {});
let byKey = new Map((schema?.settings || []).map((s) => [s.key, s]));

export const ACCENTS = {
  green: ["#1ed760", "#3be477"], blue: ["#4c8dff", "#6fa3ff"], purple: ["#a970ff", "#bd92ff"],
  pink: ["#ff5fa2", "#ff80b6"], orange: ["#ff8a3d", "#ffa263"], red: ["#ff5a5f", "#ff7c80"],
  teal: ["#1ecbe1", "#4dd8ea"], yellow: ["#f5c518", "#f8d24a"],
};

/** Aktueller Wert einer Benutzer-Einstellung (sonst der Standardwert aus dem Schema). */
export function setting(key) {
  if (key in values) return values[key];
  return byKey.get(key)?.default;
}

export function settingsSchema() { return schema; }
export function eqPresets() { return schema?.eq_presets || {}; }

export async function loadSettings() {
  const [s, v] = await Promise.all([
    api("/settings/schema").catch(() => schema),
    api("/me/settings").catch(() => values),
  ]);
  if (s) {
    schema = s;
    byKey = new Map(s.settings.map((x) => [x.key, x]));
    prefs.set("settingsSchema", s);
  }
  values = v || {};
  prefs.set("settingsValues", values);
  applyAppearance();
  emit("settings", { keys: Object.keys(values) });
  return values;
}

/**
 * Speichert Änderungen ({key: wert}, null = Standard). Wirkt sofort (auch vor der Server-Antwort),
 * damit Schalter ohne Verzögerung reagieren.
 */
export async function saveSettings(patch) {
  const keys = Object.keys(patch);
  const before = { ...values };
  for (const k of keys) values[k] = patch[k] === null ? byKey.get(k)?.default : patch[k];
  applyAppearance();
  emit("settings", { keys });
  try {
    values = await api("/me/settings", { method: "PUT", body: patch });
    prefs.set("settingsValues", values);
    applyAppearance();
    emit("settings", { keys });
    return values;
  } catch (e) {
    values = before;
    applyAppearance();
    emit("settings", { keys });
    throw e;
  }
}

export async function resetSettings() {
  values = await api("/me/settings", { method: "DELETE" });
  prefs.set("settingsValues", values);
  applyAppearance();
  emit("settings", { keys: Object.keys(values) });
  return values;
}

/** „Vor dem Löschen nachfragen“ berücksichtigen. */
export async function confirmDelete(ask) {
  if (!setting("confirm_delete")) return true;
  return ask();
}

// ---------------------------------------------------------------- Aussehen
export function applyAppearance() {
  const root = document.documentElement;
  root.dataset.theme = setting("theme") || "dark";
  const [accent, hover] = ACCENTS[setting("accent")] || ACCENTS.green;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-hover", hover);
  const scale = Number(setting("ui_scale") || 100) / 100;
  root.style.setProperty("--ui-scale", String(scale));
  document.body.style.zoom = scale === 1 ? "" : String(scale);
  root.dataset.cards = setting("card_size") || "medium";
  root.classList.toggle("compact-lists", !!setting("compact_lists"));
  root.classList.toggle("no-list-covers", setting("list_covers") === false);
  root.classList.toggle("reduce-motion", !!setting("reduce_motion"));
  root.classList.toggle("private-session", !!setting("private_session"));
  root.classList.toggle("static-colors", setting("dynamic_colors") === false);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = root.dataset.theme === "black" ? "#000000" : root.dataset.theme === "dim" ? "#141b2b" : "#121212";
}
