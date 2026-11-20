const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:19876",
  bridgeToken: "",
  groupTitle: "Hermes",
  groupColor: "blue",
  pollingEnabled: true,
  allowCrossWorkspace: false,
  allowPrivateFetch: false,
};

function isLoopbackBridgeUrl(raw) {
  try {
    const u = new URL(String(raw || "").trim());
    if (u.protocol !== "http:" || u.username || u.password) return false;
    const h = u.hostname.toLowerCase();
    return h === "127.0.0.1" || h === "localhost" || h === "[::1]" || h === "::1";
  } catch {
    return false;
  }
}

function setTokenHint(tokenLen) {
  const el = document.getElementById("tokenHint");
  if (!el) return;
  const len = Number(tokenLen) || 0;
  if (len > 0) {
    el.textContent = `Token set (${len} chars). Leave field blank and Save to keep; type a new token to replace.`;
  } else {
    el.textContent = "Not set — Pair with bridge or paste from bridge.env";
  }
}

async function load() {
  try {
    const s = await chrome.runtime.sendMessage({ type: "getSettings" });
    if (!s || s.ok === false) return;
    document.getElementById("bridgeUrl").value = s.bridgeUrl || DEFAULTS.bridgeUrl;
    // Do not put full token in a sticky visible field after load — show empty with hint.
    document.getElementById("bridgeToken").value = "";
    document.getElementById("bridgeToken").placeholder = s.tokenSet
      ? "••••••••  (saved — paste to replace)"
      : "Paste token or use Pair";
    setTokenHint(s.tokenLen || (s.tokenSet ? 16 : 0));
    document.getElementById("groupTitle").value = s.groupTitle || DEFAULTS.groupTitle;
    document.getElementById("groupColor").value = s.groupColor || DEFAULTS.groupColor;
    document.getElementById("pollingEnabled").checked = s.pollingEnabled !== false;
    document.getElementById("allowCrossWorkspace").checked = !!s.allowCrossWorkspace;
    document.getElementById("allowPrivateFetch").checked = !!s.allowPrivateFetch;
  } catch (e) {
    const msg = document.getElementById("msg");
    if (msg) msg.textContent = `Failed to load settings: ${e?.message || e}`;
  }
}

document.getElementById("pair").onclick = async () => {
  const msg = document.getElementById("msg");
  msg.textContent = "Pairing…";
  try {
    const r = await chrome.runtime.sendMessage({ type: "pair" });
    if (r && r.ok) {
      msg.textContent = r.hint || "Paired. Token saved.";
      await load();
    } else {
      msg.textContent =
        (r && r.error) ||
        "Pair failed. Run: hermes-chrome pair-open  then try again within 5 minutes.";
    }
  } catch (e) {
    msg.textContent = `Pair error: ${e?.message || e}`;
  }
};

document.getElementById("save").onclick = async () => {
  const msg = document.getElementById("msg");
  const bridgeUrl =
    document.getElementById("bridgeUrl").value.trim() || DEFAULTS.bridgeUrl;
  if (!isLoopbackBridgeUrl(bridgeUrl)) {
    msg.textContent =
      "✗ Bridge URL must be http://127.0.0.1, http://localhost, or http://[::1].";
    return;
  }
  const tokenInput = document.getElementById("bridgeToken").value;
  const pollingEnabled = document.getElementById("pollingEnabled").checked;
  const settings = {
    bridgeUrl,
    groupTitle:
      document.getElementById("groupTitle").value.trim() || DEFAULTS.groupTitle,
    groupColor: document.getElementById("groupColor").value,
    pollingEnabled,
    allowCrossWorkspace: document.getElementById("allowCrossWorkspace").checked,
    allowPrivateFetch: document.getElementById("allowPrivateFetch").checked,
  };
  // Only overwrite token when user typed something.
  if (tokenInput.length > 0) {
    settings.bridgeToken = tokenInput.trim();
  }
  try {
    const r = await chrome.runtime.sendMessage({ type: "setSettings", settings });
    if (r && r.ok === false) {
      msg.textContent = `✗ Save failed: ${r.error || "unknown error"}`;
      return;
    }
    msg.textContent = pollingEnabled
      ? "Saved. Polling restarted."
      : "Saved. Background polling paused.";
    document.getElementById("bridgeToken").value = "";
    await load();
  } catch (e) {
    msg.textContent = `✗ Save error: ${e?.message || e}`;
  }
};

document.getElementById("test").onclick = async () => {
  const msg = document.getElementById("msg");
  msg.textContent = "Testing connection…";
  try {
    const s = await chrome.runtime.sendMessage({ type: "status" });
    if (s && s.bridgeOk) {
      if (s.bridgeAuth && !s.tokenSet) {
        msg.textContent = "⚠️ Bridge reachable, but Token is required. Click 'Pair with bridge' or paste token.";
      } else {
        msg.textContent = `✓ Connected! Bridge online (${s.bridgeUrl}) · Polling: ${s.polling ? "active" : "idle"} · Workspace: ${s.running ? s.title || "Hermes" : "ready"}`;
      }
    } else {
      msg.textContent = "✗ Bridge unreachable. Make sure companion is running or click 'Pair with bridge'.";
    }
  } catch (e) {
    msg.textContent = `✗ Test error: ${e?.message || e}`;
  }
};

document.getElementById("copyMcp").onclick = async () => {
  const btn = document.getElementById("copyMcp");
  const msg = document.getElementById("msg");
  try {
    const r = await chrome.runtime.sendMessage({ type: "getMcpConfig" });
    if (r && r.json) {
      try {
        await navigator.clipboard.writeText(r.json);
        const origLabel = btn.dataset.origLabel || btn.textContent;
        btn.dataset.origLabel = origLabel;
        btn.textContent = "Copied!";
        msg.textContent = "Copied MCP JSON to clipboard. Paste into Cursor (~/.cursor/mcp.json) or Claude Desktop.";
        setTimeout(() => {
          btn.textContent = btn.dataset.origLabel || origLabel;
        }, 2000);
      } catch {
        msg.textContent = "Failed to write clipboard. Please copy manually from ~/.hermes/run/hermes-chrome/mcp-snippet.json";
      }
    }
  } catch (e) {
    msg.textContent = `Failed to load MCP config: ${e?.message || e}`;
  }
};

document.getElementById("reset").onclick = async () => {
  const msg = document.getElementById("msg");
  try {
    await chrome.runtime.sendMessage({ type: "setSettings", settings: DEFAULTS });
    await load();
    msg.textContent = "Defaults restored (token cleared).";
  } catch (e) {
    msg.textContent = `Reset error: ${e?.message || e}`;
  }
};

load();
