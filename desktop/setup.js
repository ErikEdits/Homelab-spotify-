// Einrichtungsbildschirm der Windows-App
(async () => {
  const params = new URLSearchParams(location.search);
  const $ = (id) => document.getElementById(id);
  const api = window.homifyDesktop;

  if (params.get("mode") === "connecting") {
    $("connecting").hidden = false;
    return;
  }
  const form = $("form");
  form.hidden = false;
  const cfg = await api.config();
  $("primary").value = cfg.primary || "";
  $("fallback").value = cfg.fallback || "";
  const status = $("status");
  const show = (text, cls = "") => { status.textContent = text; status.className = `status ${cls}`; };

  if (params.get("mode") === "error") {
    $("title").textContent = "Homify-Server nicht erreichbar";
    show(`${params.get("error") || "Keine Verbindung"}. Bist du im Heimnetz bzw. ist Tailscale an?`, "error");
    $("save").textContent = "Erneut verbinden";
  } else if (!cfg.primary && !cfg.fallback) {
    $("primary").focus();
  }

  $("test").addEventListener("click", async () => {
    const urls = [$("primary").value, $("fallback").value].filter((u) => u.trim());
    if (!urls.length) return show("Bitte eine Adresse eintragen.", "error");
    show("Teste …");
    const results = [];
    for (const u of urls) {
      const err = await api.test(u);
      results.push(`${u.trim()}: ${err ? err : "OK"}`);
    }
    const ok = results.some((r) => r.endsWith(": OK"));
    show(results.join(" · "), ok ? "ok" : "error");
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const primary = $("primary").value;
    const fallback = $("fallback").value;
    if (!primary.trim() && !fallback.trim()) return show("Bitte eine Adresse eintragen.", "error");
    show("Verbinde …");
    await api.save(primary, fallback);
  });
})();
