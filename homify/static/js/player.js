// Wiedergabe: Warteschlange, Zufall, Wiederholen, Überblenden/lückenlos (zwei Audio-Elemente),
// Lautstärke angleichen (ReplayGain), Equalizer (Web Audio), Autoplay, Media Session

import { api, emit, on, prefs, streamUrl } from "./api.js";
import { coverUrl, toast } from "./ui.js";
import { setting } from "./usersettings.js";

let uidCounter = 0;
const withUid = (t) => ({ ...t, _uid: ++uidCounter });

const EQ_BANDS = [
  [60, "lowshelf", "eq_60"], [150, "peaking", "eq_150"], [400, "peaking", "eq_400"],
  [1000, "peaking", "eq_1k"], [2400, "peaking", "eq_2k4"], [15000, "highshelf", "eq_15k"],
];
// Zielpegel relativ zu ReplayGain (-18 LUFS): ergibt etwa -23 / -14 / -11 LUFS (wie bei Spotify)
const LEVEL_DB = { quiet: -5, normal: 4, loud: 7 };
const ASSUMED_GAIN_DB = -8;  // Songs ohne Messwert: typischer Pegel heutiger Musik
const PAUSE_FADE_MS = 250;

function shuffleArray(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** Intelligenter Zufall: möglichst nie zweimal derselbe Künstler direkt hintereinander. */
function spreadArtists(a) {
  for (let i = 1; i < a.length; i++) {
    if (a[i].artist !== a[i - 1].artist) continue;
    for (let j = i + 1; j < a.length; j++) {
      if (a[j].artist !== a[i - 1].artist) { [a[i], a[j]] = [a[j], a[i]]; break; }
    }
  }
  return a;
}

class Player {
  constructor(decks) {
    this.decks = decks;     // zwei Audio-Elemente: eins spielt, eins lädt den nächsten Song vor
    this.active = 0;
    this.items = [];        // Wiedergabereihenfolge
    this.original = [];     // Ursprüngliche Reihenfolge (für Zufall aus)
    this.index = -1;
    this.context = null;    // {type, id, name, href}
    this.shuffle = prefs.get("shuffle", false);
    this.repeat = prefs.get("repeat", "off"); // off | all | one
    this.volume = prefs.get("volume", 0.8);
    this.muted = prefs.get("muted", false);
    this.transcoding = false;
    this.reported = false;
    this.pendingSeek = null;
    this.loading = false;
    this.preloaded = null;  // {uid, deck, transcoding}
    this.fade = null;       // laufende Überblendung {old}
    this.pausing = false;
    this.ctx = null;        // Web-Audio (nur wenn der Equalizer benutzt wird)

    for (const d of decks) {
      d._fade = 1;
      d._track = null;
      this._bind(d);
    }
    this._setupMediaSession();
    on("settings", ({ keys }) => this._onSettings(keys || []));
    window.addEventListener("beforeunload", () => this._persist());
    setInterval(() => this._persist(), 10000);
  }

  get audio() { return this.decks[this.active]; }
  get other() { return this.decks[1 - this.active]; }
  get current() { return this.items[this.index] || null; }
  get playing() { return !this.audio.paused && !this.pausing; }
  get quality() {
    const c = navigator.connection;
    const mobile = !!c && (c.type === "cellular" || c.saveData === true);
    return setting(mobile ? "quality_mobile" : "quality_wifi") || "original";
  }
  time() { return { current: this.audio.currentTime || 0, duration: this.audio.duration || this.current?.duration || 0 }; }

  _bind(d) {
    const is = () => d === this.audio;
    d.addEventListener("timeupdate", () => { if (is()) this._onTime(); });
    d.addEventListener("play", () => { if (is()) this._state(); });
    d.addEventListener("pause", () => { if (is()) this._state(); });
    d.addEventListener("ended", () => { if (is()) this._onEnded(); });
    d.addEventListener("error", () => { if (is()) this._onError(); else this._onPreloadError(d); });
    d.addEventListener("waiting", () => { if (is()) { this.loading = true; emit("loading", true); } });
    const ready = () => { if (is()) { this.loading = false; emit("loading", false); } };
    d.addEventListener("playing", ready);
    d.addEventListener("canplay", ready);
    d.addEventListener("seeked", () => { if (is()) emit("seeked"); });
    d.addEventListener("loadedmetadata", () => {
      if (!is()) return;
      if (this.pendingSeek != null) {
        try { d.currentTime = this.pendingSeek; } catch { /* ignorieren */ }
        this.pendingSeek = null;
      }
      emit("time", this.time());
    });
  }

  // ------------------------------------------------------------ Wiedergabe starten
  playTracks(tracks, startIndex = 0, context = null) {
    if (!tracks?.length) return;
    this._finishCrossfade();
    this.original = tracks.map(withUid);
    this.context = context;
    const start = this.original[Math.max(0, Math.min(startIndex, tracks.length - 1))];
    if (this.shuffle) {
      this.items = [start, ...this._shuffled(this.original.filter((t) => t !== start))];
      this.index = 0;
    } else {
      this.items = this.original.slice();
      this.index = this.items.indexOf(start);
    }
    this._unlockAudio();
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
    this._finishCrossfade();
    this.index = i;
    this._load(true);
    emit("queue");
  }

  toggle() {
    if (!this.current) return;
    if (this.playing) this.pause();
    else this.play();
  }

  play() {
    if (!this.current) return;
    this._unlockAudio();
    if (!this.audio.src) { this._load(true); return; }
    const d = this.audio;
    this.pausing = false;
    if (setting("fade_pause")) {
      if (d.paused) d._fade = 0;
      this._animate(PAUSE_FADE_MS, (p, from) => { d._fade = from + (1 - from) * p; }, null, d._fade);
    } else {
      this._stopAnimation();
      d._fade = 1;
    }
    this._applyVolume();
    if (d.paused) d.play().catch((e) => this._playFailed(e));
    else this._state();
  }

  pause() {
    const d = this.audio;
    if (d.paused || this.pausing) return;
    this._finishCrossfade();
    if (!setting("fade_pause")) { d.pause(); return; }
    this.pausing = true;
    this._state();
    this._animate(PAUSE_FADE_MS, (p, from) => { d._fade = from * (1 - p); }, () => {
      this.pausing = false;
      d.pause();
      d._fade = 1;
      this._applyVolume();
    }, d._fade);
  }

  next(auto = false) {
    if (!this.items.length) return;
    this._finishCrossfade();
    if (this.index < this.items.length - 1) {
      this.index++;
      this._load(true);
    } else if (this.repeat === "all") {
      this.index = 0;
      if (this.shuffle) this.items = this._shuffled(this.items);
      this._load(true);
    } else if (auto) {
      // Ende der Warteschlange: Autoplay (ähnliche Songs) oder anhalten
      if (setting("autoplay")) {
        this._appendAutoplay().then((added) => {
          if (added && this.index < this.items.length - 1) this.next(true);
          else this._stopAtEnd();
        });
        return;
      }
      this._stopAtEnd();
      return;
    } else {
      // Manuell „Weiter“ am Ende: zurück an den Anfang, pausiert
      this.index = 0;
      this._load(false);
    }
    emit("queue");
  }

  _stopAtEnd() {
    this.audio.pause();
    try { this.audio.currentTime = 0; } catch { /* ignorieren */ }
  }

  prev() {
    if (!this.items.length) return;
    this._finishCrossfade();
    const threshold = Number(setting("restart_threshold") ?? 3);
    if ((threshold > 0 && this.audio.currentTime > threshold) || this.index === 0) {
      this.seek(0);
      return;
    }
    this.index--;
    this._load(true);
    emit("queue");
  }

  seek(seconds) {
    if (!this.current) return;
    this._finishCrossfade();
    const dur = this.audio.duration || this.current.duration || 0;
    const t = Math.max(0, Math.min(seconds, dur ? dur - 0.25 : seconds));
    if (this.audio.readyState >= 1) this.audio.currentTime = t;
    else this.pendingSeek = t;
    emit("time", this.time());
  }

  seekBy(delta) { this.seek(this.audio.currentTime + delta); }

  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, v));
    if (this.volume > 0 && this.muted) this.muted = false;
    this._volumeChanged();
  }

  toggleMute() {
    this.muted = !this.muted;
    this._volumeChanged();
  }

  _volumeChanged() {
    prefs.set("volume", this.volume);
    prefs.set("muted", this.muted);
    this._applyVolume();
    emit("volume", { volume: this.volume, muted: this.muted });
  }

  toggleShuffle() {
    this.shuffle = !this.shuffle;
    prefs.set("shuffle", this.shuffle);
    const cur = this.current;
    if (cur) {
      const queued = this.items.slice(this.index + 1).filter((t) => t._queued);
      if (this.shuffle) {
        const rest = this.original.filter((t) => t._uid !== cur._uid);
        this.items = [cur, ...queued, ...this._shuffled(rest)];
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

  // ------------------------------------------------------------ intern: Laden
  _shuffled(list) {
    const out = shuffleArray(list);
    return setting("shuffle_smart") ? spreadArtists(out) : out;
  }

  _needsTranscode(track) {
    if (!track.mime) return true;
    return this.audio.canPlayType(track.mime) === "";
  }

  _url(track, transcode) {
    return streamUrl(track, { transcode, quality: this.quality });
  }

  _rate() {
    const r = parseFloat(setting("playback_rate"));
    return r > 0 ? r : 1;
  }

  _load(autoplay, resumeAt = null) {
    const track = this.current;
    if (!track) return;
    this.reported = false;
    this.pendingSeek = resumeAt;
    this.pausing = false;
    const pre = this.preloaded;
    this.preloaded = null;
    let deck;
    if (pre && pre.uid === track._uid && pre.deck === this.other && pre.deck.getAttribute("src")) {
      deck = pre.deck;  // vorgeladen -> startet ohne Pause
      this.transcoding = pre.transcoding;
    } else {
      deck = this.fade ? this.other : this.audio;
      if (!this.fade) this._unloadOther();
      this.transcoding = this._needsTranscode(track);
      deck.src = this._url(track, this.transcoding);
    }
    if (deck !== this.audio) {
      const old = this.audio;
      this.active = this.decks.indexOf(deck);
      if (!this.fade) { old.pause(); this._unload(old); }
    }
    deck._track = track;
    deck._fade = this.fade ? 0 : 1;
    deck.defaultPlaybackRate = deck.playbackRate = this._rate();
    this._applyVolume();
    this.loading = deck.readyState < 3;
    emit("loading", this.loading);
    emit("track", track);
    this._updateMediaSession();
    if (autoplay) {
      this.ctx?.resume?.().catch(() => {});
      deck.play().catch((e) => this._playFailed(e));
    }
    if (!setting("gapless") && !(setting("crossfade") > 0)) this._prefetchNext();
  }

  _unload(d) {
    if (d.getAttribute("src")) {
      d.pause();
      d.removeAttribute("src");
      d.load();
    }
    d._track = null;
    d._fade = 1;
  }

  _unloadOther() {
    if (this.preloaded?.deck === this.other) this.preloaded = null;
    this._unload(this.other);
  }

  _peekNext() {
    if (this.index < this.items.length - 1) return this.items[this.index + 1];
    if (this.repeat === "all" && !this.shuffle) return this.items[0];
    return null;
  }

  /** Nächsten Song im zweiten Audio-Element vorladen (lückenlos + Überblenden). */
  _maybePreload(remaining) {
    if (this.fade || this.repeat === "one") return;
    if (!setting("gapless") && !(setting("crossfade") > 0)) return;
    if (remaining > 25) return;
    const next = this._peekNext();
    if (this.preloaded && this.preloaded.uid === next?._uid) return;
    if (!next) { if (this.preloaded) this._unloadOther(); return; }
    const deck = this.other;
    const transcoding = this._needsTranscode(next);
    deck.src = this._url(next, transcoding);
    deck.preload = "auto";
    deck.load();
    deck._track = next;
    deck._fade = 1;
    deck.defaultPlaybackRate = deck.playbackRate = this._rate();
    this.preloaded = { uid: next._uid, deck, transcoding };
  }

  _onPreloadError(d) {
    const pre = this.preloaded;
    if (!pre || pre.deck !== d) return;
    if (!pre.transcoding && d._track) {
      pre.transcoding = true;  // Format geht im Browser nicht -> umgewandelt vorladen
      d.src = this._url(d._track, true);
      d.load();
    } else {
      this.preloaded = null;
      this._unload(d);
    }
  }

  _prefetchNext() {
    // Nächsten Song vorwärmen: der Server wandelt ihn ggf. schon um
    const next = this.items[this.index + 1];
    if (!next || !this._needsTranscode(next)) return;
    clearTimeout(this._prefetchTimer);
    this._prefetchTimer = setTimeout(() => {
      fetch(this._url(next, true), { headers: { Range: "bytes=0-1" } }).catch(() => {});
    }, 4000);
  }

  // ------------------------------------------------------------ intern: Überblenden & Lautstärke
  _crossfade(seconds) {
    const old = this.audio;
    if (this.index < this.items.length - 1) this.index++;
    else if (this.repeat === "all" && !this.shuffle) this.index = 0;
    else return;
    this.fade = { old };
    this._load(true);
    const fresh = this.audio;
    this._animate(seconds * 1000, (p) => {
      old._fade = 1 - p;
      fresh._fade = p;
    }, () => this._finishCrossfade());
    emit("queue");
  }

  _finishCrossfade() {
    if (!this.fade) return;
    const { old } = this.fade;
    this.fade = null;
    this._stopAnimation();
    if (old !== this.audio) this._unload(old);
    this.audio._fade = 1;
    this._applyVolume();
  }

  _animate(ms, step, done, from = 1) {
    this._stopAnimation();
    const start = performance.now();
    const tick = () => {
      const p = Math.min(1, (performance.now() - start) / ms);
      step(p, from);
      this._applyVolume();
      if (p >= 1) {
        this._stopAnimation();
        done?.();
      }
    };
    this._animTimer = setInterval(tick, 40);
    tick();
  }

  _stopAnimation() {
    clearInterval(this._animTimer);
    this._animTimer = null;
  }

  _gainFactor(track) {
    const mode = setting("normalize");
    if (!track || !mode || mode === "off") return 1;
    let gain = mode === "album" ? (track.album_gain ?? track.gain) : (track.gain ?? track.album_gain);
    if (gain == null) gain = ASSUMED_GAIN_DB;
    const db = gain + (LEVEL_DB[setting("normalize_level")] ?? LEVEL_DB.normal);
    return Math.min(1, Math.pow(10, db / 20));
  }

  _applyVolume() {
    const base = this.muted ? 0 : this.volume;
    for (const d of this.decks) {
      const v = Math.max(0, Math.min(1, base * this._gainFactor(d._track) * (d._fade ?? 1)));
      if (d._gain && this.ctx) {
        if (d.volume !== 1) d.volume = 1;
        d._gain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.015);
      } else {
        d.volume = v;
      }
    }
  }

  // ------------------------------------------------------------ intern: Equalizer (Web Audio)
  _unlockAudio() {
    if (setting("eq_enabled") && !this.ctx) this._applyEq(true);
    if (this.ctx?.state === "suspended") this.ctx.resume().catch(() => {});
  }

  _ensureGraph() {
    if (this.ctx) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    try {
      const ctx = new AC();
      const pre = ctx.createGain();
      const filters = EQ_BANDS.map(([freq, type]) => {
        const f = ctx.createBiquadFilter();
        f.type = type;
        f.frequency.value = freq;
        if (type === "peaking") f.Q.value = 1.0;
        return f;
      });
      let node = pre;
      for (const f of filters) { node.connect(f); node = f; }
      node.connect(ctx.destination);
      for (const d of this.decks) {
        const gain = ctx.createGain();
        ctx.createMediaElementSource(d).connect(gain);
        gain.connect(pre);
        d._gain = gain;
      }
      this.ctx = ctx;
      this.eqPre = pre;
      this.eqFilters = filters;
      return true;
    } catch (e) {
      console.warn("Equalizer nicht verfügbar", e);
      return false;
    }
  }

  _applyEq(fromGesture = false) {
    const enabled = !!setting("eq_enabled");
    if (!this.ctx) {
      if (!enabled) return;
      // Browser erlauben Web Audio erst nach einer Nutzeraktion -> sonst beim ersten Abspielen
      if (!fromGesture && navigator.userActivation && !navigator.userActivation.hasBeenActive) return;
      if (!this._ensureGraph()) return;
    }
    const gains = EQ_BANDS.map(([, , key]) => (enabled ? Number(setting(key)) || 0 : 0));
    const now = this.ctx.currentTime;
    this.eqFilters.forEach((f, i) => f.gain.setTargetAtTime(gains[i], now, 0.03));
    // Anhebungen nicht übersteuern lassen: Vorverstärkung um die größte Anhebung absenken
    const boost = Math.max(0, ...gains);
    this.eqPre.gain.setTargetAtTime(Math.pow(10, -boost / 20), now, 0.03);
    this._applyVolume();
  }

  _onSettings(keys) {
    if (keys.some((k) => k.startsWith("eq_"))) this._applyEq();
    if (keys.some((k) => k.startsWith("normalize"))) this._applyVolume();
    if (keys.includes("playback_rate")) {
      for (const d of this.decks) d.defaultPlaybackRate = d.playbackRate = this._rate();
    }
    if (keys.includes("gapless") || keys.includes("crossfade")) {
      if (!setting("gapless") && !(setting("crossfade") > 0) && !this.fade) this._unloadOther();
    }
  }

  // ------------------------------------------------------------ intern: Ereignisse
  _playFailed(err) {
    if (err?.name === "NotAllowedError") { this._state(); return; } // Autoplay blockiert – Nutzer muss klicken
    if (err?.name === "AbortError") return;
  }

  _onError() {
    const track = this.current;
    const d = this.audio;
    if (!track || !d.getAttribute("src")) return;
    const code = d.error?.code;
    if (!this.transcoding) {
      // Browser kann das Format doch nicht -> serverseitig umwandeln und an gleicher Stelle weiter
      const at = d.currentTime || 0;
      this.transcoding = true;
      this.pendingSeek = at > 0 ? at : null;
      d.src = this._url(track, true);
      d.play().catch((e) => this._playFailed(e));
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

  async _appendAutoplay() {
    if (this._autoplayPending) return this._autoplayPending;
    const seed = this.current;
    if (!seed) return false;
    this._autoplayPending = (async () => {
      try {
        const limit = Number(setting("mix_size") || 60);
        const tracks = await api(`/mix/radio?value=${encodeURIComponent(seed.id)}&limit=${limit}`);
        const recent = new Set(this.items.slice(-500).map((t) => t.id));
        const fresh = tracks.filter((t) => !recent.has(t.id)).map((t) => ({ ...withUid(t), _autoplay: true }));
        if (!fresh.length) return false;
        this.items.push(...fresh);
        this.original.push(...fresh);
        emit("queue");
        return true;
      } catch {
        return false;
      } finally {
        this._autoplayPending = null;
      }
    })();
    return this._autoplayPending;
  }

  _onTime() {
    const t = this.time();
    emit("time", t);
    const track = this.current;
    const countAfter = Number(setting("count_play_after") || 30);
    if (track && !this.reported && t.current >= Math.min(countAfter, t.duration ? t.duration * 0.5 : countAfter)) {
      this.reported = true;
      if (!setting("private_session")) api("/history", { method: "POST", body: { track_id: track.id } }).catch(() => {});
    }
    if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && t.duration && isFinite(t.duration)) {
      try {
        navigator.mediaSession.setPositionState({ duration: t.duration, position: Math.min(t.current, t.duration), playbackRate: this.audio.playbackRate || 1 });
      } catch { /* ignorieren */ }
    }
    if (!track || !t.duration || !isFinite(t.duration)) return;
    const remaining = t.duration - t.current;
    // Autoplay: kurz vor Ende der Warteschlange ähnliche Songs anhängen (damit Überblenden klappt)
    if (setting("autoplay") && this.repeat === "off" && this.index === this.items.length - 1
        && remaining < 30 && this._autoplayFor !== track._uid) {
      this._autoplayFor = track._uid;
      this._appendAutoplay();
    }
    this._maybePreload(remaining);
    const cf = Number(setting("crossfade") || 0);
    if (cf > 0 && !this.fade && !this.pausing && this.repeat !== "one" && !this.audio.paused
        && t.duration > cf * 2 + 2 && remaining <= cf && remaining > 0.3 && this._peekNext()) {
      this._crossfade(cf);
    }
  }

  _state() {
    emit("state", this.playing);
    if ("mediaSession" in navigator) navigator.mediaSession.playbackState = this.playing ? "playing" : "paused";
  }

  _setupMediaSession() {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    const step = () => Number(setting("seek_step") || 10);
    const handlers = {
      play: () => this.play(),
      pause: () => this.pause(),
      previoustrack: () => this.prev(),
      nexttrack: () => this.next(),
      seekto: (d) => this.seek(d.seekTime),
      seekbackward: (d) => this.seekBy(-(d.seekOffset || step())),
      seekforward: (d) => this.seekBy(d.seekOffset || step()),
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
    if (!setting("resume_session")) return;
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

export const player = new Player([document.getElementById("audio"), document.getElementById("audio2")]);
