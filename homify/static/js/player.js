// Wiedergabe: Warteschlange, Zufall, Wiederholen, Umwandlung als Rückfallebene, Media Session

import { api, emit, prefs, streamUrl } from "./api.js";
import { coverUrl, toast } from "./ui.js";

let uidCounter = 0;
const withUid = (t) => ({ ...t, _uid: ++uidCounter });

function shuffleArray(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

class Player {
  constructor(audio) {
    this.audio = audio;
    this.items = [];        // Wiedergabereihenfolge
    this.original = [];     // Ursprüngliche Reihenfolge (für Zufall aus)
    this.index = -1;
    this.context = null;    // {type, id, name, href}
    this.shuffle = prefs.get("shuffle", false);
    this.repeat = prefs.get("repeat", "off"); // off | all | one
    this.quality = prefs.get("quality", "original");
    this.transcoding = false;
    this.reported = false;
    this.pendingSeek = null;
    this.loading = false;

    audio.volume = prefs.get("volume", 0.8);
    audio.muted = prefs.get("muted", false);

    audio.addEventListener("timeupdate", () => this._onTime());
    audio.addEventListener("play", () => this._state());
    audio.addEventListener("pause", () => this._state());
    audio.addEventListener("ended", () => this._onEnded());
    audio.addEventListener("error", () => this._onError());
    audio.addEventListener("waiting", () => { this.loading = true; emit("loading", true); });
    audio.addEventListener("playing", () => { this.loading = false; emit("loading", false); });
    audio.addEventListener("canplay", () => { this.loading = false; emit("loading", false); });
    audio.addEventListener("loadedmetadata", () => {
      if (this.pendingSeek != null) {
        try { audio.currentTime = this.pendingSeek; } catch { /* ignorieren */ }
        this.pendingSeek = null;
      }
      emit("time", this.time());
    });
    audio.addEventListener("volumechange", () => {
      prefs.set("volume", audio.volume);
      prefs.set("muted", audio.muted);
      emit("volume", { volume: audio.volume, muted: audio.muted });
    });
    this._setupMediaSession();
    window.addEventListener("beforeunload", () => this._persist());
    setInterval(() => this._persist(), 10000);
  }

  get current() { return this.items[this.index] || null; }
  get playing() { return !this.audio.paused; }
  time() { return { current: this.audio.currentTime || 0, duration: this.audio.duration || this.current?.duration || 0 }; }

  // ------------------------------------------------------------ Wiedergabe starten
  playTracks(tracks, startIndex = 0, context = null) {
    if (!tracks?.length) return;
    this.original = tracks.map(withUid);
    this.context = context;
    const start = this.original[Math.max(0, Math.min(startIndex, tracks.length - 1))];
    if (this.shuffle) {
      this.items = [start, ...shuffleArray(this.original.filter((t) => t !== start))];
      this.index = 0;
    } else {
      this.items = this.original.slice();
      this.index = this.items.indexOf(start);
    }
    this._load(true);
    emit("queue");
  }

  playNext(tracks) {
    if (!tracks?.length) return;
    if (!this.current) return this.playTracks(tracks);
    this.items.splice(this.index + 1, 0, ...tracks.map((t) => ({ ...withUid(t), _queued: true })));
    emit("queue");
  }

  addToQueue(tracks) {
    if (!tracks?.length) return;
    if (!this.current) return this.playTracks(tracks);
    let pos = this.index + 1;
    while (this.items[pos]?._queued) pos++;
    this.items.splice(pos, 0, ...tracks.map((t) => ({ ...withUid(t), _queued: true })));
    emit("queue");
  }

  removeFromQueue(uid) {
    const i = this.items.findIndex((t) => t._uid === uid);
    if (i < 0 || i === this.index) return;
    this.items.splice(i, 1);
    if (i < this.index) this.index--;
    emit("queue");
  }

  jumpTo(uid) {
    const i = this.items.findIndex((t) => t._uid === uid);
    if (i < 0) return;
    this.index = i;
    this._load(true);
    emit("queue");
  }

  toggle() {
    if (!this.current) return;
    if (this.audio.paused) {
      if (!this.audio.src) this._load(true);
      else this.audio.play().catch((e) => this._playFailed(e));
    } else {
      this.audio.pause();
    }
  }

  play() { if (this.audio.paused) this.toggle(); }
  pause() { if (!this.audio.paused) this.audio.pause(); }

  next(auto = false) {
    if (!this.items.length) return;
    if (this.index < this.items.length - 1) {
      this.index++;
      this._load(true);
    } else if (this.repeat === "all") {
      this.index = 0;
      if (this.shuffle) this.items = shuffleArray(this.items);
      this._load(true);
    } else if (auto) {
      // Ende der Warteschlange: anhalten
      this.audio.pause();
      this.audio.currentTime = 0;
      return;
    } else {
      // Manuell „Weiter“ am Ende: zurück an den Anfang, pausiert
      this.index = 0;
      this._load(false);
    }
    emit("queue");
  }

  prev() {
    if (!this.items.length) return;
    if (this.audio.currentTime > 3 || this.index === 0) {
      this.audio.currentTime = 0;
      return;
    }
    this.index--;
    this._load(true);
    emit("queue");
  }

  seek(seconds) {
    if (!this.current) return;
    const dur = this.audio.duration || this.current.duration || 0;
    const t = Math.max(0, Math.min(seconds, dur ? dur - 0.25 : seconds));
    if (this.audio.readyState >= 1) this.audio.currentTime = t;
    else this.pendingSeek = t;
    emit("time", this.time());
  }

  setVolume(v) {
    this.audio.volume = Math.max(0, Math.min(1, v));
    if (this.audio.volume > 0 && this.audio.muted) this.audio.muted = false;
  }

  toggleMute() { this.audio.muted = !this.audio.muted; }

  toggleShuffle() {
    this.shuffle = !this.shuffle;
    prefs.set("shuffle", this.shuffle);
    const cur = this.current;
    if (cur) {
      const queued = this.items.slice(this.index + 1).filter((t) => t._queued);
      if (this.shuffle) {
        const rest = this.original.filter((t) => t._uid !== cur._uid);
        this.items = [cur, ...queued, ...shuffleArray(rest)];
        this.index = 0;
      } else {
        const pos = this.original.findIndex((t) => t._uid === cur._uid);
        if (pos >= 0) {
          this.items = [...this.original.slice(0, pos + 1), ...queued, ...this.original.slice(pos + 1)];
          this.index = pos;
        } else {
          this.items = [cur, ...queued, ...this.original];
          this.index = 0;
        }
      }
    }
    emit("mode");
    emit("queue");
  }

  cycleRepeat() {
    this.repeat = this.repeat === "off" ? "all" : this.repeat === "all" ? "one" : "off";
    prefs.set("repeat", this.repeat);
    emit("mode");
  }

  setQuality(q) {
    this.quality = q;
    prefs.set("quality", q);
  }

  // ------------------------------------------------------------ intern
  _needsTranscode(track) {
    if (!track.mime) return true;
    const answer = this.audio.canPlayType(track.mime);
    return answer === "";
  }

  _load(autoplay, resumeAt = null) {
    const track = this.current;
    if (!track) return;
    this.transcoding = this._needsTranscode(track);
    this.reported = false;
    this.pendingSeek = resumeAt;
    this.audio.src = streamUrl(track, { transcode: this.transcoding, quality: this.quality });
    this.loading = true;
    emit("loading", true);
    emit("track", track);
    this._updateMediaSession();
    if (autoplay) this.audio.play().catch((e) => this._playFailed(e));
    this._prefetchNext();
  }

  _playFailed(err) {
    if (err?.name === "NotAllowedError") { this._state(); return; } // Autoplay blockiert – Nutzer muss klicken
    if (err?.name === "AbortError") return;
  }

  _prefetchNext() {
    // Nächsten Song vorwärmen: der Server wandelt ihn ggf. schon um
    const next = this.items[this.index + 1];
    if (!next || !this._needsTranscode(next)) return;
    clearTimeout(this._prefetchTimer);
    this._prefetchTimer = setTimeout(() => {
      fetch(streamUrl(next, { transcode: true, quality: this.quality }), { headers: { Range: "bytes=0-1" } }).catch(() => {});
    }, 4000);
  }

  _onError() {
    const track = this.current;
    if (!track || !this.audio.src) return;
    const code = this.audio.error?.code;
    if (!this.transcoding) {
      // Browser kann das Format doch nicht -> serverseitig umwandeln und an gleicher Stelle weiter
      const at = this.audio.currentTime || 0;
      this.transcoding = true;
      this.pendingSeek = at > 0 ? at : null;
      this.audio.src = streamUrl(track, { transcode: true, quality: this.quality });
      this.audio.play().catch((e) => this._playFailed(e));
      return;
    }
    this.loading = false;
    emit("loading", false);
    toast(`„${track.title}“ kann nicht abgespielt werden${code === 4 ? "" : " (Datei nicht erreichbar?)"}`, { error: true });
    if (this.items.length > 1 && this.index < this.items.length - 1) setTimeout(() => this.next(true), 800);
  }

  _onEnded() {
    if (this.repeat === "one") {
      this.audio.currentTime = 0;
      this.audio.play().catch(() => {});
      this.reported = false;
      return;
    }
    this.next(true);
  }

  _onTime() {
    const t = this.time();
    emit("time", t);
    const track = this.current;
    if (track && !this.reported && (t.current > 30 || (t.duration && t.current > t.duration * 0.5))) {
      this.reported = true;
      api("/history", { method: "POST", body: { track_id: track.id } }).catch(() => {});
    }
    if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && t.duration && isFinite(t.duration)) {
      try {
        navigator.mediaSession.setPositionState({ duration: t.duration, position: Math.min(t.current, t.duration), playbackRate: 1 });
      } catch { /* ignorieren */ }
    }
  }

  _state() {
    emit("state", this.playing);
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = this.playing ? "playing" : "paused";
  }

  _setupMediaSession() {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    const handlers = {
      play: () => this.play(),
      pause: () => this.pause(),
      previoustrack: () => this.prev(),
      nexttrack: () => this.next(),
      seekto: (d) => this.seek(d.seekTime),
      seekbackward: (d) => this.seek(this.audio.currentTime - (d.seekOffset || 10)),
      seekforward: (d) => this.seek(this.audio.currentTime + (d.seekOffset || 10)),
      stop: () => this.pause(),
    };
    for (const [action, fn] of Object.entries(handlers)) {
      try { ms.setActionHandler(action, fn); } catch { /* nicht unterstützt */ }
    }
  }

  _updateMediaSession() {
    const t = this.current;
    if (!t || !("mediaSession" in navigator) || typeof MediaMetadata === "undefined") return;
    const artwork = t.cover ? [96, 300, 640].map((s) => ({
      src: new URL(coverUrl(t.cover, s), location.href).href, sizes: `${s}x${s}`, type: "image/jpeg",
    })) : [];
    navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.artist, album: t.album, artwork });
  }

  // ------------------------------------------------------------ Speichern/Wiederherstellen
  _persist() {
    if (!this.items.length) return;
    const strip = (t) => {
      const { _uid, ...rest } = t;
      return rest;
    };
    const max = 1500;
    const start = Math.max(0, this.index - 200);
    prefs.set("session", {
      items: this.items.slice(start, start + max).map(strip),
      index: this.index - start,
      time: this.audio.currentTime || 0,
      context: this.context,
    });
  }

  restore() {
    const s = prefs.get("session", null);
    if (!s?.items?.length) return;
    this.items = s.items.map(withUid);
    this.original = this.items.slice();
    this.index = Math.min(Math.max(0, s.index || 0), this.items.length - 1);
    this.context = s.context || null;
    this._load(false, s.time || 0);
    emit("queue");
  }
}

export const player = new Player(document.getElementById("audio"));
