// Brücke zwischen Homify-Oberfläche und der Windows-App (window.homifyDesktop)
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("homifyDesktop", {
  // aus der Weboberfläche
  openSettings: () => ipcRenderer.send("homify:open-settings"),
  updateState: (json) => ipcRenderer.send("homify:state", String(json)),
  // nur für den Einrichtungsbildschirm (wird im Hauptprozess geprüft)
  config: () => ipcRenderer.invoke("homify:config"),
  test: (url) => ipcRenderer.invoke("homify:test", String(url || "")),
  save: (primary, fallback) => ipcRenderer.invoke("homify:save", { primary: String(primary || ""), fallback: String(fallback || "") }),
  retry: () => ipcRenderer.invoke("homify:retry"),
});
