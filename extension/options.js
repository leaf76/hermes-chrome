const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:19876",
  groupTitle: "Hermes Agent",
  groupColor: "blue",
  pollingEnabled: true,
};

async function load() {
  const s = await chrome.runtime.sendMessage({ type: "getSettings" });
  document.getElementById("bridgeUrl").value = s.bridgeUrl || DEFAULTS.bridgeUrl;
  document.getElementById("groupTitle").value = s.groupTitle || DEFAULTS.groupTitle;
  document.getElementById("groupColor").value = s.groupColor || DEFAULTS.groupColor;
  document.getElementById("pollingEnabled").checked = s.pollingEnabled !== false;
}

document.getElementById("save").onclick = async () => {
  const settings = {
    bridgeUrl: document.getElementById("bridgeUrl").value.trim() || DEFAULTS.bridgeUrl,
    groupTitle: document.getElementById("groupTitle").value.trim() || DEFAULTS.groupTitle,
    groupColor: document.getElementById("groupColor").value,
    pollingEnabled: document.getElementById("pollingEnabled").checked,
  };
  await chrome.runtime.sendMessage({ type: "setSettings", settings });
  document.getElementById("msg").textContent = "Saved. Polling restarted.";
};

document.getElementById("reset").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "setSettings", settings: DEFAULTS });
  await load();
  document.getElementById("msg").textContent = "Defaults restored.";
};

load();
