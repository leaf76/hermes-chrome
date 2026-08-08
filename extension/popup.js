const out = document.getElementById("out");
const bridgeBadge = document.getElementById("bridgeBadge");
const pollBadge = document.getElementById("pollBadge");
const groupBadge = document.getElementById("groupBadge");
const versionBadge = document.getElementById("versionBadge");
const lastBadge = document.getElementById("lastBadge");
const authBadge = document.getElementById("authBadge");
const bridgeUrl = document.getElementById("bridgeUrl");
const lastLine = document.getElementById("lastLine");
const authLine = document.getElementById("authLine");
const setupCard = document.getElementById("setupCard");
const setupKicker = document.getElementById("setupKicker");
const setupTitle = document.getElementById("setupTitle");
const setupBody = document.getElementById("setupBody");
const setupCmd = document.getElementById("setupCmd");
const setupHint = document.getElementById("setupHint");

const CMD_INSTALL_AGENT =
  "./scripts/hermes-chrome.sh install-for-agent\n" +
  "# Windows:\n" +
  "# powershell -ExecutionPolicy Bypass -File .\\scripts\\install-windows.ps1";
const CMD_PAIR =
  "./scripts/hermes-chrome.sh pair-open\n" +
  "# then press Pair in this popup";
const REPO = "https://github.com/leaf76/hermes-chrome";

function setBadge(el, ok, text) {
  if (!el) return;
  el.textContent = text;
  el.className = "badge " + (ok === true ? "ok" : ok === false ? "bad" : "warn");
}

function formatLast(act) {
  if (!act || !act.kind) return { badge: "—", line: "" };
  const ago = act.at ? Math.max(0, Math.round((Date.now() - act.at) / 1000)) : null;
  const agoS = ago == null ? "" : ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)}m ago`;
  const bits = [act.kind];
  if (act.bytes != null) bits.push(`${act.bytes}B`);
  if (act.status != null) bits.push(`HTTP ${act.status}`);
  return {
    badge: act.kind,
    line: [bits.join(" · "), act.url || act.title || "", agoS].filter(Boolean).join(" — "),
  };
}

function nativeHostMissing(s) {
  const nh = s.nativeHost || {};
  const err = nh.error || "";
  return !!(err && /not found|specified native messaging host/i.test(err));
}

/**
 * Show a prominent setup card when the product is not ready.
 * Healthy (bridge online + auth ready) hides the card.
 */
function updateSetupCard(s) {
  if (!setupCard) return;

  const healthy =
    !!s.bridgeOk &&
    (!s.bridgeAuth || (!!s.tokenSet && !!s.authReady));

  if (healthy) {
    setupCard.hidden = true;
    setupCard.classList.remove("setup-warn", "setup-need");
    return;
  }

  setupCard.hidden = false;
  setupCard.classList.remove("setup-warn", "setup-need");

  if (!s.bridgeOk) {
    const nh = s.nativeHost || {};
    const nhErr = nh.error || "";
    if (nativeHostMissing(s)) {
      setupCard.classList.add("setup-need");
      if (setupKicker) setupKicker.textContent = "Setup required";
      if (setupTitle) setupTitle.textContent = "Companion not installed";
      if (setupBody) {
        setupBody.textContent =
          "This extension is only the browser half. Chrome cannot open a control port from an extension alone. Install the local companion once (bridge + Native Messaging host), then reload and click the icon.";
      }
      if (setupCmd) setupCmd.textContent = CMD_INSTALL_AGENT;
      if (setupHint) {
        setupHint.textContent =
          "Clone " + REPO + " · needs real Python 3 · then Reload extension.";
      }
    } else if (nh.ok === false && nhErr) {
      setupCard.classList.add("setup-warn");
      if (setupKicker) setupKicker.textContent = "Bridge offline";
      if (setupTitle) setupTitle.textContent = "Native host reported an error";
      if (setupBody) {
        setupBody.textContent =
          "Companion may be installed but the bridge is not up. Try Reconnect, or re-run the companion install.";
      }
      if (setupCmd) {
        setupCmd.textContent =
          "Native host: " + nhErr + "\n\n" + CMD_INSTALL_AGENT;
      }
      if (setupHint) setupHint.textContent = "Click Reconnect after the bridge starts.";
    } else {
      setupCard.classList.add("setup-warn");
      if (setupKicker) setupKicker.textContent = "Bridge offline";
      if (setupTitle) setupTitle.textContent = "Local bridge not on :19876";
      if (setupBody) {
        setupBody.textContent =
          "Agents talk to a local bridge, not to the Chrome Web Store. Install the companion once if you have not, then click Reconnect.";
      }
      if (setupCmd) setupCmd.textContent = CMD_INSTALL_AGENT;
      if (setupHint) {
        setupHint.textContent =
          "After install: Reload extension → click icon → wait for Bridge online.";
      }
    }
    return;
  }

  // Bridge up, auth not ready
  if (!s.bridgeAuth) {
    setupCard.classList.add("setup-warn");
    if (setupKicker) setupKicker.textContent = "Auth off";
    if (setupTitle) setupTitle.textContent = "Bridge auth disabled";
    if (setupBody) {
      setupBody.textContent =
        "ALLOW_NO_AUTH is set. Not recommended for daily Chrome. Prefer token + Pair.";
    }
    if (setupCmd) setupCmd.textContent = "";
    if (setupHint) setupHint.textContent = "";
    return;
  }

  setupCard.classList.add("setup-need");
  if (setupKicker) setupKicker.textContent = "Pair required";
  if (setupTitle) setupTitle.textContent = "Link extension to bridge";
  if (setupBody) {
    setupBody.textContent = s.pairingOpen
      ? "Pairing window is open. Press Pair below (or paste the token in Options)."
      : "Open a short pairing window from the CLI, then press Pair in this popup.";
  }
  if (setupCmd) {
    setupCmd.textContent = s.pairingOpen
      ? "# Pairing open — press Pair in this popup"
      : CMD_PAIR;
  }
  if (setupHint) {
    setupHint.textContent = s.pairingOpen
      ? "If Pair fails, paste token from ~/.hermes/run/hermes-chrome/bridge.env in Options."
      : "Token lives in ~/.hermes/run/hermes-chrome/bridge.env (chmod 600).";
  }
}

async function refresh() {
  const s = await chrome.runtime.sendMessage({ type: "status" });
  // Avoid dumping secrets / huge capture blobs
  const view = { ...s };
  if (view.lastActivity && view.lastActivity.pngBase64) {
    view.lastActivity = { ...view.lastActivity, pngBase64: "[omitted]" };
  }
  out.textContent = JSON.stringify(view, null, 2);
  bridgeUrl.textContent = s.bridgeUrl || "";
  setBadge(bridgeBadge, !!s.bridgeOk, s.bridgeOk ? "online" : "offline");
  setBadge(pollBadge, !!s.polling, s.polling ? "active" : "idle");
  setBadge(
    groupBadge,
    s.running ? true : null,
    s.running ? s.title || "Hermes" : "none"
  );
  const ver = s.version || chrome.runtime.getManifest().version;
  setBadge(versionBadge, true, ver || "?");
  const last = formatLast(s.lastActivity);
  setBadge(lastBadge, s.lastActivity ? true : null, last.badge);
  if (lastLine) lastLine.textContent = last.line;

  // Auth status
  if (authBadge) {
    if (!s.bridgeOk) {
      setBadge(authBadge, false, "no bridge");
      if (authLine) {
        if (nativeHostMissing(s)) {
          authLine.textContent =
            "Companion not installed — see Setup required above.";
        } else if (s.nativeHost && s.nativeHost.ok === false && s.nativeHost.error) {
          authLine.textContent =
            "Bridge down; native host: " + s.nativeHost.error;
        } else {
          authLine.textContent =
            "Local bridge not on :19876 — install companion or Reconnect.";
        }
      }
    } else if (!s.bridgeAuth) {
      setBadge(authBadge, false, "off (insecure)");
      if (authLine) {
        authLine.textContent =
          "Bridge auth disabled (ALLOW_NO_AUTH). Not recommended.";
      }
    } else if (s.tokenSet && s.authReady) {
      setBadge(authBadge, true, "ready");
      if (authLine) {
        authLine.textContent = s.pairingOpen
          ? "Token set · pairing window still open"
          : "Token set";
      }
    } else {
      setBadge(authBadge, false, "need pair");
      if (authLine) {
        authLine.textContent = s.pairingOpen
          ? "Click Pair (window open) or paste token in Options"
          : "Run: hermes-chrome.sh pair-open  then Pair";
      }
    }
  }

  updateSetupCard(s);
}

function openGuide() {
  const url = chrome.runtime.getURL("help.html");
  chrome.tabs.create({ url, active: true });
}

document.getElementById("btnRefresh").onclick = refresh;
document.getElementById("btnReconnect").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "reconnect" });
  await refresh();
};
document.getElementById("btnPair").onclick = async () => {
  const r = await chrome.runtime.sendMessage({ type: "pair" });
  if (authLine) {
    authLine.textContent = r && r.ok ? r.hint || "Paired" : r?.error || "Pair failed";
  }
  await refresh();
};
document.getElementById("btnOptions").onclick = () => {
  chrome.runtime.openOptionsPage();
};
document.getElementById("btnGuide").onclick = openGuide;
const btnSetupGuide = document.getElementById("btnSetupGuide");
if (btnSetupGuide) btnSetupGuide.onclick = openGuide;

refresh();
