// Wiedergabe: Warteschlange, Zufall, Wiederholen, Überblenden/lückenlos (zwei Audio-Elemente),
// Lautstärke angleichen (ReplayGain), Equalizer (Web Audio), Autoplay, Media Session
//
// Klangkette (Web Audio – am PC immer, am Handy nur mit Equalizer, weil Android/iOS sonst im
// Hintergrund stottern können):
//   Deck → Pegel (Angleichen, darf leise Songs anheben) → Blende (Überblenden/Pausieren, läuft auf dem
//   Audio-Takt) → Lautstärke → Equalizer → Limiter (nur aktiv, wenn etwas angehoben wird) → Ausgang
// Ohne Web Audio regelt das Audio-Element selbst die Lautstärke (dann ohne Anheben).

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
const MAX_BOOST_DB = 12;     // leise Songs höchstens so weit anheben
const PEAK_CEILING_DB = -1;  // beim Anheben Spitzen unter -1 dBTP halten (wie Spotify „Normal“)
const LIMIT_DB = -1;         // Limiter-Schwelle, wenn etwas angehoben wird
const LIMIT_RATIO = 20;
const PAUSE_FADE_MS = 250;
const GAPLESS_OVERLAP = 0.06; // lückenlos: Songs überlappen minimal – kein Knacks, keine Pause
const SEEK_DIP = 0.012;       // Spulen: ganz kurz aus- und wieder einblenden (kein Knacksen)
const TRIM_MIN = 0.5;         // Stille erst ab dieser Länge kürzen
const TRIM_KEEP = 0.15;       // … und so viel davon stehen lassen (natürliche Atempause)
const VOLUME_EXP = 2.5;       // Lautstärke-Regler folgt dem Gehör (50 % ≈ -15 dB statt -6 dB)

// Handys und Tablets (auch Android-Tablets und iPads, die sich als Mac ausgeben)
const IS_MOBILE = navigator.userAgentData?.mobile === true
  || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
  || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

const dbToGain = (db) => Math.pow(10, db / 20);
/** Regler-Stellung (0–1) -> Verstärkung, wie das Ohr Lautstärke empfindet. */
export const perceptualVolume = (v) => (v <= 0 ? 0 : Math.pow(Math.min(1, v), VOLUME_EXP));
/** Gleich laute Überblendung (Equal Power): rein sin, raus cos – keine Delle in der Mitte. */
export function fadeCurve(from, to, p) {
  const k = to >= from ? Math.sin(p * Math.PI / 2) : 1 - Math.cos(p * Math.PI / 2);
  return from + (to - from) * k;
}
/** Makeup-Gain, den ein DynamicsCompressor (Web-Audio-Spezifikation) automatisch draufrechnet. */
export function compressorMakeupDb(thresholdDb, ratio) {
  const fullRangeDb = thresholdDb + (0 - thresholdDb) / ratio;  // Kennlinie bei 0 dBFS (Knie 0)
  return -0.6 * fullRangeDb;
}

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
    if (prefs.get("volume_curve", 0) < 1) {
      // früher linear: Regler so umrechnen, dass es nach dem Update genauso laut bleibt
      this.volume = Math.pow(Math.max(0, Math.min(1, this.volume)), 1 / VOLUME_EXP);
      prefs.set("volume", this.volume);
      prefs.set("volume_curve", 1);
    }
    this.muted = prefs.get("muted", false);
    this.transcoding = false;
    this.reported = false;
    this.pendingSeek = null;
    this.loading = false;
    this.preloaded = null;  // {uid, deck, transcoding}
    this.fade = null;       // laufende Überblendung {old}
    this.pausing = false;
    this.ctx = null;        // Web-Audio-Klangkette (am PC immer, am Handy nur mit Equalizer)
    this.canOpus = decks[0].canPlayType('audio/ogg; codecs="opus"') !== "";

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
      if (!is()) {
        // vorgeladener Song: gleich hinter die Stille am Anfang springen
        if (d._startAt) { try { d.currentTime = d._startAt; } catch { /* ignorieren */ } }
        return;
      }
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
      this._fadeDecks([[d, d.paused ? 0 : d._fade, 1]], PAUSE_FADE_MS);
    } else {
      this._stopAnimation();
      this._setFade(d, 1);
    }
    this._applyVolume();
    if (d.paused) d.play().catch((e) => this._playFailed(e));
    else this._state();
  }

  pause() {
    const d = this.audio;
    if (d.paused || this.pausing) return;
    this._finishCrossfade();
    this._cancelTransition();
    if (!setting("fade_pause")) { d.pause(); return; }
    this.pausing = true;
    this._state();
    this._fadeDecks([[d, d._fade, 0]], PAUSE_FADE_MS, () => {
      this.pausing = false;
      d.pause();
      this._setFade(d, 1);
      this._applyVolume();
    });
  }

  next(auto = false) {
    if (!this.items.length) return;
    if (!auto) this._reportSkip();
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
    const d = this.audio;
    const dur = d.duration || this.current.duration || 0;
    const t = Math.max(0, Math.min(seconds, dur ? dur - 0.25 : seconds));
    this._cancelTransition();
    if (d.readyState < 1) { this.pendingSeek = t; emit("time", this.time()); return; }
    if (this.ctx && d._fader && !d.paused && !this.pausing && !document.hidden) {
      // Kurz ausblenden, springen, wieder einblenden – sonst knackt es an der Schnittstelle
      const g = d._fader.gain;
      const now = this.ctx.currentTime;
      const token = (this._seekToken = {});
      g.cancelScheduledValues(now);
      g.setValueAtTime(g.value, now);
      g.linearRampToValueAtTime(0, now + SEEK_DIP);
      clearTimeout(this._seekTimer);
      this._seekTimer = setTimeout(() => {
        if (this._seekToken !== token || d !== this.audio) return;
        d.currentTime = t;
        let done = false;
        const back = () => {
          if (done || this._seekToken !== token) return;  // nur einmal, und nur für das letzte Spulen
          done = true;
          const at = this.ctx.currentTime;
          g.cancelScheduledValues(at);
          g.setValueAtTime(0, at);
          g.linearRampToValueAtTime(d._fade ?? 1, at + 0.04);
        };
        d.addEventListener("seeked", back, { once: true });
        setTimeout(back, 400);  // Sicherheitsnetz, falls „seeked“ ausbleibt
      }, SEEK_DIP * 1000 + 3);
    } else {
      d.currentTime = t;
    }
    emit("time", this.time());
  }

  seekBy(delta) { this.seek(this.audio.currentTime + delta); }

  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, Number(v) || 0));
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
    return streamUrl(track, { transcode, quality: this.quality, opus: this.canOpus });
  }

  _rate() {
    const r = parseFloat(setting("playback_rate"));
    return r > 0 ? r : 1;
  }

  _load(autoplay, resumeAt = null) {
    const track = this.current;
    if (!track) return;
    this.reported = false;
    this.pausing = false;
    this._cancelTransition();
    const pre = this.preloaded;
    this.preloaded = null;
    const wasPlaying = !this.audio.paused && !!this.audio.getAttribute("src");
    let deck;
    if (pre && pre.uid === track._uid && pre.deck === this.other && pre.deck.getAttribute("src")) {
      deck = pre.deck;  // vorgeladen (und schon hinter der Stille) -> startet ohne Pause
      this.transcoding = pre.transcoding;
      this.pendingSeek = resumeAt ?? (deck.readyState >= 1 ? null : deck._startAt ?? null);
    } else {
      // Mit Web Audio beim Weiterschalten das andere Deck nehmen, damit der alte Song kurz
      // ausblenden kann statt mitten in der Welle abzureißen (sonst knackt es)
      const quickSwitch = !this.fade && !!this.ctx && wasPlaying;
      deck = this.fade || quickSwitch ? this.other : this.audio;
      if (!this.fade) this._unloadOther();
      this.transcoding = this._needsTranscode(track);
      deck.src = this._url(track, this.transcoding);
      this.pendingSeek = resumeAt ?? this._startOffset(track);
    }
    deck._startAt = null;
    if (deck !== this.audio) {
      const old = this.audio;
      this.active = this.decks.indexOf(deck);
      if (!this.fade) this._retire(old);
    }
    deck._track = track;
    this._setFade(deck, this.fade ? 0 : 1);
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
    d._retiring = null;
    if (d.getAttribute("src")) {
      d.pause();
      d.removeAttribute("src");
      d.load();
    }
    d._track = null;
    d._startAt = null;
    this._setFade(d, 1);
  }

  /** Altes Deck beim Weiterschalten: mit Web Audio ganz kurz ausblenden, dann entladen. */
  _retire(d) {
    if (!this.ctx || !d._fader || d.paused) { this._unload(d); return; }
    const token = {};
    d._retiring = token;
    const g = d._fader.gain;
    const now = this.ctx.currentTime;
    g.cancelScheduledValues(now);
    g.setValueAtTime(g.value, now);
    g.linearRampToValueAtTime(0, now + 0.03);
    setTimeout(() => {
      if (d._retiring === token && d !== this.audio && this.fade?.old !== d && this.preloaded?.deck !== d) {
        this._unload(d);
      }
    }, 60);
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
    deck._retiring = null;
    deck._startAt = this._startOffset(next);
    deck.src = this._url(next, transcoding);
    deck.preload = "auto";
    deck.load();
    deck._track = next;
    this._setFade(deck, 1);
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
    const from = old._fade ?? 1;
    this._load(true);
    const fresh = this.audio;
    this._fadeDecks([[old, from, 0], [fresh, 0, 1]], seconds * 1000, () => this._finishCrossfade());
    emit("queue");
  }

  _finishCrossfade() {
    if (!this.fade) return;
    const { old } = this.fade;
    this.fade = null;
    this._stopAnimation();
    if (old !== this.audio) this._unload(old);
    this._setFade(this.audio, 1);
    this._applyVolume();
  }

  /** Blende sofort setzen (0 = stumm, 1 = voll). */
  _setFade(d, value) {
    d._fade = value;
    if (this.ctx && d._fader) {
      const g = d._fader.gain;
      const now = this.ctx.currentTime;
      g.cancelScheduledValues(now);
      g.setValueAtTime(value, now);
    }
  }

  /**
   * Blenden [[Deck, von, bis], …] in ms – gleich laut (Equal Power). Mit Web Audio läuft die Kurve auf dem
   * Audio-Takt: butterweich und auch dann exakt, wenn der Browser Timer im Hintergrund drosselt.
   */
  _fadeDecks(list, ms, done) {
    this._stopAnimation();
    const graph = !!this.ctx;
    if (graph) {
      const seconds = Math.max(0.005, ms / 1000);
      const now = this.ctx.currentTime;
      const n = Math.max(2, Math.min(2048, Math.ceil(seconds * 200)));
      for (const [d, from, to] of list) {
        if (!d._fader) continue;
        const curve = new Float32Array(n);
        for (let i = 0; i < n; i++) curve[i] = fadeCurve(from, to, i / (n - 1));
        const g = d._fader.gain;
        g.cancelScheduledValues(now);
        try {
          g.setValueCurveAtTime(curve, now, seconds);
        } catch {
          g.setValueAtTime(from, now);
          g.linearRampToValueAtTime(to, now + seconds);
        }
      }
    }
    const start = performance.now();
    const tick = () => {
      const p = Math.min(1, (performance.now() - start) / ms);
      for (const [d, from, to] of list) d._fade = fadeCurve(from, to, p);
      if (!graph) this._applyVolume();
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

  /**
   * Lautstärke angleichen: Verstärkung in dB für diesen Song. Wie bei Spotify: ganze Alben behalten ihre
   * Dynamik („Pro Album“ im Album), sonst pro Song. Leise Songs werden mit Web Audio angehoben – bei
   * „Normal“/„Leise“ nur so weit, dass die echten Spitzen unter -1 dBTP bleiben, bei „Laut“ mit Limiter.
   */
  _levelDb(track) {
    const mode = setting("normalize");
    if (!track || !mode || mode === "off") return 0;
    const albumCtx = mode === "album" && this.context?.type === "album" && track.album_gain != null;
    let gain = albumCtx ? track.album_gain : (track.gain ?? track.album_gain);
    const peak = albumCtx ? (track.album_peak ?? track.peak) : track.peak;
    const level = setting("normalize_level");
    if (gain == null) gain = ASSUMED_GAIN_DB;
    let db = gain + (LEVEL_DB[level] ?? LEVEL_DB.normal);
    if (db > 0) {
      if (!this.ctx) db = 0;  // das Audio-Element kann nicht über 100 %
      else if (level === "loud") db = Math.min(db, MAX_BOOST_DB);
      else db = Math.min(db, MAX_BOOST_DB, peak != null ? Math.max(0, PEAK_CEILING_DB - peak) : 0);
    }
    return db;
  }

  _applyVolume() {
    const master = this.muted ? 0 : perceptualVolume(this.volume);
    if (this.ctx && this.master) {
      const now = this.ctx.currentTime;
      this.master.gain.setTargetAtTime(master, now, 0.015);
      for (const d of this.decks) {
        if (d.volume !== 1) d.volume = 1;
        d._level?.gain.setTargetAtTime(dbToGain(this._levelDb(d._track)), now, 0.015);
      }
      this._updateLimiter();
      return;
    }
    for (const d of this.decks) {
      d.volume = Math.max(0, Math.min(1, master * dbToGain(this._levelDb(d._track)) * (d._fade ?? 1)));
    }
  }

  /** Limiter nur einschalten, wenn etwas angehoben wird (Equalizer oder leiser Song) – sonst unverfälscht. */
  _updateLimiter() {
    if (!this.limiter) return;
    const boosting = (this._eqBoost || 0) > 0 || this.decks.some((d) => d._track && this._levelDb(d._track) > 0);
    if (boosting === this._limiting) return;
    this._limiting = boosting;
    const now = this.ctx.currentTime;
    this.limiter.threshold.setTargetAtTime(boosting ? LIMIT_DB : 0, now, 0.05);
    this.limiter.ratio.setTargetAtTime(boosting ? LIMIT_RATIO : 1, now, 0.05);
    // der Kompressor legt automatisch Pegel drauf – genau den wieder abziehen, sonst wird es lauter
    this.makeup.gain.setTargetAtTime(dbToGain(boosting ? -compressorMakeupDb(LIMIT_DB, LIMIT_RATIO) : 0), now, 0.05);
  }

  // ------------------------------------------------------------ intern: Klangkette (Web Audio)
  /** Web Audio am PC immer (Anheben, Limiter, weiche Blenden); am Handy nur mit Equalizer. */
  _wantGraph() { return !!setting("eq_enabled") || !IS_MOBILE; }

  /** Browser erlauben Web Audio erst, nachdem jemand auf der Seite geklickt/getippt hat. */
  _mayStartAudio() { return !navigator.userActivation || navigator.userActivation.hasBeenActive; }

  _unlockAudio() {
    if (!this.ctx && this._wantGraph() && this._mayStartAudio() && this._ensureGraph()) this._applyEq();
    if (this.ctx?.state === "suspended") this.ctx.resume().catch(() => {});
  }

  _ensureGraph() {
    if (this.ctx) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    try {
      let ctx;
      try {
        ctx = new AC({ latencyHint: "playback" });  // größere Puffer: keine Aussetzer, wenn der PC zu tun hat
      } catch {
        ctx = new AC();
      }
      const master = ctx.createGain();
      const pre = ctx.createGain();
      master.connect(pre);
      const filters = EQ_BANDS.map(([freq, type]) => {
        const f = ctx.createBiquadFilter();
        f.type = type;
        f.frequency.value = freq;
        if (type === "peaking") f.Q.value = 1.0;
        return f;
      });
      let node = pre;
      for (const f of filters) { node.connect(f); node = f; }
      // Limiter am Ende: fängt Spitzen ab, wenn etwas angehoben wird – laut, ohne zu verzerren
      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = 0;
      limiter.knee.value = 0;
      limiter.ratio.value = 1;
      limiter.attack.value = 0.002;
      limiter.release.value = 0.15;
      const makeup = ctx.createGain();
      node.connect(limiter);
      limiter.connect(makeup);
      makeup.connect(ctx.destination);
      for (const d of this.decks) {
        const level = ctx.createGain();
        const fader = ctx.createGain();
        ctx.createMediaElementSource(d).connect(level);
        level.connect(fader);
        fader.connect(master);
        fader.gain.value = d._fade ?? 1;
        d._level = level;
        d._fader = fader;
      }
      this.ctx = ctx;
      this.master = master;
      this.eqPre = pre;
      this.eqFilters = filters;
      this.limiter = limiter;
      this.makeup = makeup;
      this._limiting = false;
      return true;
    } catch (e) {
      console.warn("Web Audio nicht verfügbar", e);
      return false;
    }
  }

  _applyEq() {
    const enabled = !!setting("eq_enabled");
    if (!this.ctx) {
      // Equalizer am Handy eingeschaltet: Klangkette jetzt aufbauen (sonst beim ersten Abspielen)
      if (!enabled || !this._mayStartAudio() || !this._ensureGraph()) return;
      this.ctx.resume?.().catch(() => {});
    }
    const gains = EQ_BANDS.map(([, , key]) => (enabled ? Number(setting(key)) || 0 : 0));
    const now = this.ctx.currentTime;
    this.eqFilters.forEach((f, i) => f.gain.setTargetAtTime(gains[i], now, 0.03));
    // Anhebungen nicht übersteuern lassen: halb vorab absenken, den Rest fängt der Limiter
    this._eqBoost = Math.max(0, ...gains);
    this.eqPre.gain.setTargetAtTime(dbToGain(-this._eqBoost / 2), now, 0.03);
    this._applyVolume();
  }

  _onSettings(keys) {
    if (keys.some((k) => k.startsWith("eq_"))) this._applyEq();
    if (keys.some((k) => k.startsWith("normalize"))) this._applyVolume();
    if (keys.includes("gapless") || keys.includes("crossfade")) this._cancelTransition();
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

  /** Früh weitergeschaltet (bevor der Song als gehört zählt): der Feed schlägt ihn dann seltener vor. */
  _reportSkip() {
    const track = this.current;
    const at = this.time().current;
    if (!track || this.reported || at < 1 || setting("private_session")) return;
    api("/history/skip", { method: "POST", body: { track_id: track.id } }).catch(() => {});
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
    const endAt = this._endAt(track, t.duration);  // ohne die Stille am Ende
    const remaining = endAt - t.current;
    // Autoplay: kurz vor Ende der Warteschlange ähnliche Songs anhängen (damit Überblenden klappt)
    if (setting("autoplay") && this.repeat === "off" && this.index === this.items.length - 1
        && remaining < 30 && this._autoplayFor !== track._uid) {
      this._autoplayFor = track._uid;
      this._appendAutoplay();
    }
    this._maybePreload(remaining);
    if (this.fade || this.pausing || this.repeat === "one" || this.audio.paused || !this._peekNext()) return;
    const cf = Number(setting("crossfade") || 0);
    if (cf > 0) {
      // Überblenden über echte Musik, nicht über die Stille am Ende
      if (endAt > cf * 2 + 2 && remaining <= cf && remaining > 0.3) this._crossfade(cf);
      return;
    }
    if (setting("gapless")) this._scheduleTransition(track, remaining);
  }

  // ------------------------------------------------------------ intern: Übergänge ohne Pause
  /** Stille kürzen – nicht bei Alben in Original-Reihenfolge (dort gehören die Pausen zum Album). */
  _trimSilence() {
    if (!setting("gapless") && !(Number(setting("crossfade")) > 0)) return false;
    return this.context?.type !== "album" || this.shuffle;
  }

  _startOffset(track) {
    const lead = Number(track?.lead_in) || 0;
    return this._trimSilence() && lead >= TRIM_MIN ? Math.max(0, lead - TRIM_KEEP) : null;
  }

  _endAt(track, duration) {
    const tail = Number(track?.tail) || 0;
    if (!this._trimSilence() || tail < TRIM_MIN || duration < tail + 10) return duration;
    return duration - tail + TRIM_KEEP;
  }

  /** Lückenlos: kurz vor dem Ende den vorgeladenen Song starten und minimal überlappen lassen. */
  _scheduleTransition(track, remaining) {
    const pre = this.preloaded;
    const next = this._peekNext();
    if (!pre || !next || pre.uid !== next._uid || pre.deck.readyState < 3) return;
    if (remaining > 1.5 || remaining < 0 || this._transitionFor === track._uid) return;
    this._transitionFor = track._uid;
    const rate = this.audio.playbackRate || 1;
    clearTimeout(this._transitionTimer);
    this._transitionTimer = setTimeout(() => {
      // Timer kam zu spät (Browser drosselt im Hintergrund)? Dann hat „ended“ schon weitergeschaltet.
      if (this.current?._uid !== track._uid || this.fade || this.pausing || this.audio.paused) return;
      this._crossfade(GAPLESS_OVERLAP);
    }, Math.max(0, (remaining - GAPLESS_OVERLAP) / rate) * 1000);
  }

  _cancelTransition() {
    clearTimeout(this._transitionTimer);
    this._transitionTimer = null;
    this._transitionFor = null;
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
