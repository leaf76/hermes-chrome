const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:19876",
  bridgeToken: "",
  groupTitle: "Hermes",
  groupColor: "blue",
  pollingEnabled: true,
  allowCrossWorkspace: false,
  allowPrivateFetch: false,
};

function setTokenHint(token) {
  const el = document.getElementById("tokenHint");
  if (!el) return;
  if (token && token.length) {
    el.textContent = `Token set (${token.length} chars). Leave field blank and Save to keep; clear field then type to replace.`;
  } else {
    el.textContent = "Not set — Pair with bridge or paste from bridge.env";
  }
}

async function load() {
  const s = await chrome.runtime.sendMessage({ type: "getSettings" });
  document.getElementById("bridgeUrl").value = s.bridgeUrl || DEFAULTS.bridgeUrl;
  // Do not put full token in a sticky visible field after load — show empty with hint.
  document.getElementById("bridgeToken").value = "";
  document.getElementById("bridgeToken").placeholder = s.tokenSet
    ? "••••••••  (saved — paste to replace)"
    : "Paste token or use Pair";
  setTokenHint(s.tokenSet ? "x".repeat(16) : "");
  document.getElementById("groupTitle").value = s.groupTitle || DEFAULTS.groupTitle;
  document.getElementById("groupColor").value = s.groupColor || DEFAULTS.groupColor;
  document.getElementById("pollingEnabled").checked = s.pollingEnabled !== false;
  document.getElementById("allowCrossWorkspace").checked = !!s.allowCrossWorkspace;
  document.getElementById("allowPrivateFetch").checked = !!s.allowPrivateFetch;
}

document.getElementById("pair").onclick = async () => {
  const msg = document.getElementById("msg");
  msg.textContent = "Pairing…";
  const r = await chrome.runtime.sendMessage({ type: "pair" });
  if (r && r.ok) {
    msg.textContent = r.hint || "Paired. Token saved.";
    await load();
  } else {
    msg.textContent =
      (r && r.error) ||
      "Pair failed. Run: hermes-chrome.sh pair-open  then try again within 5 minutes.";
  }
};

document.getElementById("save").onclick = async () => {
  const tokenInput = document.getElementById("bridgeToken").value;
  const settings = {
    bridgeUrl:
      document.getElementById("bridgeUrl").value.trim() || DEFAULTS.bridgeUrl,
    groupTitle:
      document.getElementById("groupTitle").value.trim() || DEFAULTS.groupTitle,
    groupColor: document.getElementById("groupColor").value,
    pollingEnabled: document.getElementById("pollingEnabled").checked,
    allowCrossWorkspace: document.getElementById("allowCrossWorkspace").checked,
    allowPrivateFetch: document.getElementById("allowPrivateFetch").checked,
  };
  // Only overwrite token when user typed something (including explicit clear via space? require length)
  if (tokenInput.length > 0) {
    settings.bridgeToken = tokenInput.trim();
  }
  await chrome.runtime.sendMessage({ type: "setSettings", settings });
  document.getElementById("msg").textContent = "Saved. Polling restarted.";
  document.getElementById("bridgeToken").value = "";
  await load();
};

document.getElementById("reset").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "setSettings", settings: DEFAULTS });
  await load();
  document.getElementById("msg").textContent = "Defaults restored (token cleared).";
};

load();
