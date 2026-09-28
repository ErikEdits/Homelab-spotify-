// Homify für Windows: eigenes Programmfenster für deinen Homify-Server
const { app, BrowserWindow, Menu, ipcMain, nativeImage, net, shell } = require("electron");
const fs = require("fs");
const path = require("path");

app.setAppUserModelId("de.homify.desktop");
// Medientasten der Tastatur und die Windows-Mediensteuerung an den Player geben
app.commandLine.appendSwitch("enable-features", "HardwareMediaKeyHandling,MediaSessionService");

const CONFIG_FILE = path.join(app.getPath("userData"), "homify.json");
const DEFAULT_PORT = 8484;

let config = loadConfig();
let win = null;
let base = null; // aktuell verbundener Server
let state = { playing: false, title: "" };

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
  } catch {
    return {};
  }
}

function saveConfig() {
  try {
    fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
    fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
  } catch (err) {
    console.error("Konfiguration nicht gespeichert:", err);
  }
}

/** „192.168.1.20“ -> „http://192.168.1.20:8484“ */
function normalize(input) {
  let url = String(input || "").trim();
  if (!url) return "";
  if (!/^https?:\/\//i.test(url)) {
    const [host, ...rest] = url.split("/");
    url = `http://${host.includes(":") ? host : `${host}:${DEFAULT_PORT}`}${rest.length ? "/" + rest.join("/") : ""}`;
  }
  return url.replace(/\/+$/, "");
}

function sameOrigin(a, b) {
  try {
    return new URL(a).origin === new URL(b).origin;
  } catch {
    return false;
  }
}

/** null = Homify antwortet, sonst Fehlertext */
async function check(url, timeout = 3500) {
  if (!url) return "keine Adresse";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await net.fetch(`${url}/api/setup`, { signal: ctrl.signal, cache: "no-store" });
    const text = await res.text();
    if (res.ok && text.includes("Homify")) return null;
    return res.ok ? "Dort läuft kein Homify" : `Server antwortet mit Fehler ${res.status}`;
  } catch (err) {
    return err.name === "AbortError" ? "Zeitüberschreitung" : err.message;
  } finally {
    clearTimeout(timer);
  }
}

function showSetup(params = {}) {
  base = null;
  win.setThumbarButtons([]);
  win.loadFile(path.join(__dirname, "setup.html"), { query: params });
}

async function connect() {
  const servers = [config.primary, config.fallback].filter(Boolean);
  if (!servers.length) return showSetup({});
  showSetup({ mode: "connecting" });
  let lastError = "";
  for (const url of servers) {
    const err = await check(url);
    if (!err) {
      base = url;
      await win.loadURL(url).catch(() => {});
      return;
    }
    lastError = err;
  }
  showSetup({ mode: "error", error: lastError });
}

// ------------------------------------------------------------------ Taskleiste
const icon = (name) => nativeImage.createFromPath(path.join(__dirname, "build", `${name}.png`));

function command(cmd) {
  if (!win || !base) return;
  win.webContents.executeJavaScript(`window.homifyNative && window.homifyNative(${JSON.stringify(cmd)}, 0)`).catch(() => {});
}

function updateThumbar() {
  if (!win || process.platform !== "win32") return;
  if (!state.title) {
    win.setThumbarButtons([]);
    return;
  }
  win.setThumbarButtons([
    { tooltip: "Zurück", icon: icon("prev"), click: () => command("prev") },
    state.playing
      ? { tooltip: "Pause", icon: icon("pause"), click: () => command("pause") }
      : { tooltip: "Wiedergabe", icon: icon("play"), click: () => command("play") },
    { tooltip: "Weiter", icon: icon("next"), click: () => command("next") },
  ]);
}

// ------------------------------------------------------------------ IPC
const fromSetupPage = (event) => (event.senderFrame?.url || "").startsWith("file:");

ipcMain.handle("homify:config", (event) => (fromSetupPage(event)
  ? { primary: config.primary || "", fallback: config.fallback || "", version: app.getVersion() } : {}));

ipcMain.handle("homify:test", (event, url) => (fromSetupPage(event) ? check(normalize(url)) : "nicht erlaubt"));

ipcMain.handle("homify:save", async (event, { primary, fallback }) => {
  if (!fromSetupPage(event)) return false;
  config.primary = normalize(primary);
  config.fallback = normalize(fallback);
  saveConfig();
  connect();
  return true;
});

ipcMain.handle("homify:retry", (event) => {
  if (fromSetupPage(event)) connect();
});

ipcMain.on("homify:open-settings", () => showSetup({}));

ipcMain.on("homify:state", (_event, json) => {
  try {
    const next = JSON.parse(json);
    const changed = next.playing !== state.playing || next.title !== state.title;
    state = next;
    if (changed) {
      updateThumbar();
      win?.setTitle(state.title ? `${state.title} · ${state.artist || ""} – Homify` : "Homify");
    }
  } catch {
    /* ignorieren */
  }
});

// ------------------------------------------------------------------ Fenster
function createWindow() {
  const bounds = config.bounds || { width: 1280, height: 820 };
  win = new BrowserWindow({
    ...bounds,
    minWidth: 380,
    minHeight: 520,
    title: "Homify",
    backgroundColor: "#121212",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "build", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      backgroundThrottling: false, // Musik läuft weiter, auch wenn minimiert
      spellcheck: false,
    },
  });
  if (config.maximized) win.maximize();

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith("file:") || (base && sameOrigin(url, base))) return;
    event.preventDefault();
    if (/^https?:/.test(url)) shell.openExternal(url);
  });
  win.webContents.on("did-fail-load", (_e, code, description, url, isMainFrame) => {
    if (isMainFrame && code !== -3 && !url.startsWith("file:")) {
      showSetup({ mode: "error", error: description });
    }
  });
  win.webContents.on("page-title-updated", (event) => {
    if (state.title) event.preventDefault();
  });

  const remember = () => {
    if (!win) return;
    config.maximized = win.isMaximized();
    if (!config.maximized && !win.isMinimized()) config.bounds = win.getBounds();
    saveConfig();
  };
  win.on("close", remember);
  win.on("closed", () => { win = null; });

  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: "Homify",
      submenu: [
        { label: "Server wechseln …", click: () => showSetup({}) },
        { label: "Neu verbinden", accelerator: "F5", click: () => connect() },
        { type: "separator" },
        { role: "toggleDevTools", label: "Entwicklerwerkzeuge" },
        { type: "separator" },
        { role: "quit", label: "Beenden" },
      ],
    },
    { role: "editMenu", label: "Bearbeiten" },
    {
      label: "Ansicht",
      submenu: [
        { role: "zoomIn", label: "Vergrößern" },
        { role: "zoomOut", label: "Verkleinern" },
        { role: "resetZoom", label: "Originalgröße" },
        { type: "separator" },
        { role: "togglefullscreen", label: "Vollbild" },
      ],
    },
  ]));

  connect();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.whenReady().then(createWindow);
  app.on("window-all-closed", () => app.quit());
  app.on("activate", () => { if (!win) createWindow(); });
}
