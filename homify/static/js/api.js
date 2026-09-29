// Kleiner Wrapper um fetch + gemeinsamer Zustand (Benutzer, Playlists, Likes)

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, { method = "GET", body, signal } = {}) {
  const opts = { method, credentials: "same-origin", signal, headers: {} };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, opts);
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Server nicht erreichbar", 0);
  }
  let data = null;
  if ((res.headers.get("content-type") || "").includes("json")) {
    try { data = await res.json(); } catch { data = null; }
  }
  if (res.status === 401 && !path.startsWith("/auth/") && !path.startsWith("/setup")) {
    onUnauthorized();
  }
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg || JSON.stringify(d)).join(", ");
    throw new ApiError(msg || `Fehler ${res.status}`, res.status);
  }
  return data;
}

// ---------------------------------------------------------------- Event-Bus
const listeners = new Map();
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event)?.delete(fn);
}
export function emit(event, data) {
  for (const fn of listeners.get(event) || []) {
    try { fn(data); } catch (e) { console.error(e); }
  }
}

// ---------------------------------------------------------------- Zustand
export const state = {
  user: null,
  playlists: [],
  likes: new Set(),
};

export async function loadPlaylists() {
  state.playlists = await api("/playlists");
  emit("playlists", state.playlists);
  return state.playlists;
}

export function isLiked(track) {
  return state.likes.has(track.id) || (track.liked && !state.likes.has("!" + track.id));
}

export async function setLiked(trackId, liked) {
  await api(`/likes/${encodeURIComponent(trackId)}`, { method: liked ? "PUT" : "DELETE" });
  if (liked) { state.likes.add(trackId); state.likes.delete("!" + trackId); }
  else { state.likes.delete(trackId); state.likes.add("!" + trackId); }
  emit("like", { id: trackId, liked });
}

export function streamUrl(track, { transcode = false, quality = "original", opus = false } = {}) {
  const params = new URLSearchParams();
  if (transcode) params.set("transcode", "1");
  if (quality && quality !== "original") params.set("quality", quality);
  // Gerät kann Opus: bei niedrigen Bitraten wandelt der Server dann in Opus statt MP3 um (klingt besser)
  if (opus && (transcode || (quality && quality !== "original"))) params.set("opus", "1");
  const qs = params.toString();
  return `/api/tracks/${encodeURIComponent(track.id)}/stream${qs ? "?" + qs : ""}`;
}

export const prefs = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(`homify.${key}`);
      return v === null ? fallback : JSON.parse(v);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`homify.${key}`, JSON.stringify(value)); } catch { /* privat/voll */ }
  },
};
