const REPO = "https://github.com/leaf76/hermes-chrome";
const CMD_INSTALL =
  "curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash";
const CMD_INSTALL_ALT =
  "git clone https://github.com/leaf76/hermes-chrome.git && cd hermes-chrome && ./scripts/install.sh --dev";
const CMD_PAIR_ONLY = "hermes-chrome pair-open";
// If PATH shim not installed yet:
const CMD_PAIR_FALLBACK =
  "~/.hermes/hermes-chrome/scripts/hermes-chrome.sh pair-open";

const AI_INSTALL_PROMPT = [
  "Please set up the Hermes Chrome companion on my machine for Chrome automation:",
  "1. Detect my OS (macOS/Linux/Windows).",
  "2. Install the companion:",
  "   - macOS / Linux: run `curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash`",
  "   - Windows: `npx hermes-chrome-companion install` or clone https://github.com/leaf76/hermes-chrome.git and run `powershell -ExecutionPolicy Bypass -File .\\scripts\\install-windows.ps1`",
  "3. Ensure `~/.local/bin` is in my PATH (add `export PATH=\"$HOME/.local/bin:$PATH\"` to my shell profile if missing).",
  "4. Run `hermes-chrome doctor --skip-update` to verify companion + bridge (extension can be installed after).",
  "5. If I use Cursor, Claude Desktop, or Grok, check that MCP is registered (~/.hermes/run/hermes-chrome/mcp-snippet.json).",
  "6. Remind me to install the Hermes Chrome extension from Chrome Web Store and click the extension icon once to connect.",
].join("\n");

const els = {
  statusDot: document.getElementById("statusDot"),
  summaryTitle: document.getElementById("summaryTitle"),
  summaryDesc: document.getElementById("summaryDesc"),
  metaRow: document.getElementById("metaRow"),
  metaWorkspace: document.getElementById("metaWorkspace"),
  metaLast: document.getElementById("metaLast"),
  setupPanel: document.getElementById("setupPanel"),
  setupHeading: document.getElementById("setupHeading"),
  setupLead: document.getElementById("setupLead"),
  setupCmd: document.getElementById("setupCmd"),
  setupCmdLabel: document.getElementById("setupCmdLabel"),
  setupCmdAlt: document.getElementById("setupCmdAlt"),
  setupStep3: document.getElementById("setupStep3"),
  setupNote: document.getElementById("setupNote"),
  setupSteps: document.getElementById("setupSteps"),
  pairBlock: document.getElementById("pairBlock"),
  pairLead: document.getElementById("pairLead"),
  pairCmd: document.getElementById("pairCmd"),
  pairCmdBox: document.getElementById("pairCmdBox"),
  pairHint: document.getElementById("pairHint"),
  btnSidePanel: document.getElementById("btnSidePanel"),
  btnPrimary: document.getElementById("btnPrimary"),
  btnSecondary: document.getElementById("btnSecondary"),
  btnCopyCmd: document.getElementById("btnCopyCmd"),
  btnCopyPair: document.getElementById("btnCopyPair"),
  btnCopyAiPrompt: document.getElementById("btnCopyAiPrompt"),
  btnSetupGuide: document.getElementById("btnSetupGuide"),
  btnGuide: document.getElementById("btnGuide"),
  btnOptions: document.getElementById("btnOptions"),
  btnCopyMcp: document.getElementById("btnCopyMcp"),
  btnReconnect: document.getElementById("btnReconnect"),
  bridgeBadge: document.getElementById("bridgeBadge"),
  pollBadge: document.getElementById("pollBadge"),
  groupBadge: document.getElementById("groupBadge"),
  versionBadge: document.getElementById("versionBadge"),
  lastBadge: document.getElementById("lastBadge"),
  authBadge: document.getElementById("authBadge"),
  bridgeUrl: document.getElementById("bridgeUrl"),
  lastLine: document.getElementById("lastLine"),
  authLine: document.getElementById("authLine"),
  out: document.getElementById("out"),
};

/** @type {"refresh"|"pair"|"reconnect"|"guide"|"github"|"stopWorkspace"|"copyCmd"} */
let primaryAction = "refresh";
/** @type {"pair"|"reconnect"|"guide"|"github"|"refresh"|"stopWorkspace"|"copyCmd"|null} */
let secondaryAction = null;

function setBadge(el, ok, text) {
  if (!el) return;
  el.textContent = text;
  el.className = "badge " + (ok === true ? "ok" : ok === false ? "bad" : "warn");
}

function formatLast(act) {
  if (!act || !act.kind) return { badge: "—", line: "", short: "" };
  const ago = act.at ? Math.max(0, Math.round((Date.now() - act.at) / 1000)) : null;
  const agoS =
    ago == null ? "" : ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)}m ago`;
  const bits = [act.kind];
  if (act.bytes != null) bits.push(`${act.bytes}B`);
  if (act.status != null) bits.push(`HTTP ${act.status}`);
  return {
    badge: act.kind,
    line: [bits.join(" · "), act.url || act.title || "", agoS].filter(Boolean).join(" — "),
    short: [act.kind, agoS].filter(Boolean).join(" · "),
  };
}

function nativeHostMissing(s) {
  const nh = s.nativeHost || {};
  const err = nh.error || "";
  return !!(err && /not found|specified native messaging host/i.test(err));
}

function parseSemver(text) {
  const nums = String(text || "")
    .match(/\d+/g)
    ?.slice(0, 3)
    .map((n) => Number(n));
  if (!nums || !nums.length) return null;
  while (nums.length < 3) nums.push(0);
  return nums;
}

function cmpSemver(a, b) {
  const pa = parseSemver(a);
  const pb = parseSemver(b);
  if (!pa || !pb) return 0;
  for (let i = 0; i < 3; i++) {
    if (pa[i] !== pb[i]) return pa[i] - pb[i];
  }
  return 0;
}

function companionVersion(s) {
  const d = s.nativeHost && s.nativeHost.detail;
  return (d && d.host_version) || null;
}

/** @returns {"companion"|"extension"|null} */
function versionDrift(s) {
  const ext = s.version || chrome.runtime.getManifest().version;
  const host = companionVersion(s);
  if (!ext || !host) return null;
  const c = cmpSemver(host, ext);
  if (c < 0) return "companion";
  if (c > 0) return "extension";
  return null;
}

function isHealthy(s) {
  return !!s.bridgeOk && (!s.bridgeAuth || (!!s.tokenSet && !!s.authReady));
}

function setDot(kind) {
  if (!els.statusDot) return;
  els.statusDot.className = "status-dot" + (kind ? " " + kind : "");
}

function setPrimary(label, action) {
  primaryAction = action;
  if (els.btnPrimary) {
    els.btnPrimary.textContent = label;
    els.btnPrimary.hidden = false;
  }
}

function setSecondary(label, action) {
  secondaryAction = action;
  if (!els.btnSecondary) return;
  if (!label) {
    els.btnSecondary.hidden = true;
    secondaryAction = null;
    return;
  }
  els.btnSecondary.hidden = false;
  els.btnSecondary.textContent = label;
}

function showInstallSteps(opts) {
  const panel = els.setupPanel;
  if (!panel) return;
  panel.hidden = false;
  panel.classList.remove("is-warn", "is-error");
  if (opts.tone === "warn") panel.classList.add("is-warn");
  if (opts.tone === "error") panel.classList.add("is-error");

  if (els.setupHeading) els.setupHeading.textContent = opts.heading;
  if (els.setupLead) els.setupLead.innerHTML = opts.leadHtml;
  if (els.setupNote) els.setupNote.textContent = opts.note || "";
  if (els.setupSteps) els.setupSteps.hidden = false;
  if (els.pairBlock) els.pairBlock.hidden = true;

  if (els.setupCmd) els.setupCmd.textContent = CMD_INSTALL;
  if (els.setupCmdLabel) {
    els.setupCmdLabel.textContent =
      "macOS / Linux — paste in Terminal (GitHub installer; runtime not on npm):";
  }
  if (els.setupCmdAlt) {
    els.setupCmdAlt.hidden = false;
    els.setupCmdAlt.innerHTML =
      "Installs to <code class=\"inline\">~/.hermes/hermes-chrome</code> + PATH " +
      "<code class=\"inline\">hermes-chrome</code>. " +
      "Windows: <code class=\"inline\">npx hermes-chrome-companion install</code> or " +
      "<code class=\"inline\">.\\scripts\\install-windows.ps1</code> from a GitHub clone. " +
      "Optional thin npm wrapper only — not the runtime. MCP registers for Grok/Cursor/Claude when present.";
  }
  if (els.setupStep3) {
    els.setupStep3.textContent =
      opts.step3 ||
      "Reload this extension, click the icon once, then Pair if asked. Status should say Connected.";
  }
}

function showPairPanel(s) {
  const panel = els.setupPanel;
  if (!panel) return;
  panel.hidden = false;
  panel.classList.remove("is-error");
  panel.classList.add("is-warn");

  if (els.setupHeading) els.setupHeading.textContent = "Link this extension";
  if (els.setupLead) {
    els.setupLead.innerHTML = s.pairingOpen
      ? "The local companion is already running. A short pairing window is open — click <strong>Pair</strong> below."
      : "The local companion is running, but this extension still needs a one-time link (token).";
  }
  if (els.setupSteps) els.setupSteps.hidden = true;
  if (els.pairBlock) els.pairBlock.hidden = false;

  if (els.pairLead) {
    els.pairLead.innerHTML = s.pairingOpen
      ? "No Terminal step needed while pairing is open."
      : "Open Terminal and run <code class=\"inline\">hermes-chrome pair-open</code> " +
        "(or paste token from bridge.env into Options):";
  }
  if (els.pairCmdBox) els.pairCmdBox.hidden = !!s.pairingOpen;
  if (els.pairCmd) {
    els.pairCmd.textContent = CMD_PAIR_ONLY + "\n# or: " + CMD_PAIR_FALLBACK;
  }
  if (els.pairHint) {
    els.pairHint.innerHTML = s.pairingOpen
      ? "If Pair fails: Options → paste token from " +
        "<code class=\"inline\">~/.hermes/run/hermes-chrome/bridge.env</code>."
      : "CLI path after install.sh: <code class=\"inline\">hermes-chrome</code> " +
        "or <code class=\"inline\">~/.hermes/hermes-chrome/scripts/…</code>. " +
        "Or paste the token from " +
        "<code class=\"inline\">~/.hermes/run/hermes-chrome/bridge.env</code> in Options.";
  }
  if (els.setupNote) {
    els.setupNote.textContent =
      "MCP is optional (not npm): mcp_server.py under ~/.hermes/hermes-chrome — " +
      "install.sh registers Grok/Cursor/Claude when present.";
  }
}

function hideSetup() {
  if (els.setupPanel) {
    els.setupPanel.hidden = true;
    els.setupPanel.classList.remove("is-warn", "is-error");
  }
  if (els.pairBlock) els.pairBlock.hidden = true;
}

function updateUserFacing(s) {
  const last = formatLast(s.lastActivity);
  const workspaceName = s.running ? s.title || "Hermes" : null;

  if (els.metaWorkspace) {
    els.metaWorkspace.textContent = workspaceName
      ? "Workspace: " + workspaceName
      : "No agent workspace yet";
  }
  if (els.metaLast) {
    els.metaLast.textContent = last.short ? "Last: " + last.short : "No recent agent activity";
  }
  if (els.metaRow) els.metaRow.hidden = false;

  if (isHealthy(s)) {
    hideSetup();
    setDot("ok");
    if (els.summaryTitle) els.summaryTitle.textContent = "Connected";
    if (els.summaryDesc) {
      if (s.pairingOpen) {
        els.summaryDesc.textContent =
          "Local companion is running. Pairing window is still open (optional).";
      } else if (s.running) {
        els.summaryDesc.textContent =
          "Companion ready. Agent Tab Group is open — close it when the task is done (bridge stays up).";
      } else {
        els.summaryDesc.textContent =
          "Local companion is running. Agents can open a workspace when needed.";
      }
    }
    setPrimary("Refresh", "refresh");
    // Close agent workspace only (not the bridge)
    if (s.running) {
      setSecondary("Close workspace", "stopWorkspace");
    } else {
      setSecondary(null);
    }
    const drift = versionDrift(s);
    if (drift === "companion") {
      setDot("warn");
      if (els.summaryTitle) els.summaryTitle.textContent = "Update companion";
      if (els.summaryDesc) {
        els.summaryDesc.textContent =
          "This extension is newer than the local companion. Re-run the GitHub installer (same command as first install).";
      }
      showInstallSteps({
        tone: "warn",
        heading: "Update the local companion",
        leadHtml:
          "Chrome Web Store can update the extension automatically. The companion on this machine " +
          "needs the same one-liner again (git pull + re-register host).",
        note: "Does not replace your token. Restart the agent MCP session after updating.",
      });
      setPrimary("Copy Update Command", "copyCmd");
      setSecondary("Refresh", "refresh");
    } else if (drift === "extension") {
      setDot("warn");
      if (els.summaryTitle) els.summaryTitle.textContent = "Reload extension";
      if (els.summaryDesc) {
        els.summaryDesc.textContent =
          "Companion is newer than this extension. Update or reload Hermes Chrome from the Chrome Web Store.";
      }
      showInstallSteps({
        tone: "warn",
        heading: "Update the browser half",
        leadHtml:
          "Open the <a href=\"https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa\" target=\"_blank\" rel=\"noopener\">Chrome Web Store listing</a> " +
          "or chrome://extensions → Hermes Chrome → Reload.",
        note: "Unpacked installs: reload the extension from the repo extension/ folder.",
      });
      setPrimary("Refresh", "refresh");
      setSecondary("Open GitHub", "github");
    }
    return;
  }

  if (!s.bridgeOk) {
    const nh = s.nativeHost || {};
    const nhErr = nh.error || "";

    if (nativeHostMissing(s)) {
      setDot("bad");
      if (els.summaryTitle) els.summaryTitle.textContent = "Setup needed";
      if (els.summaryDesc) {
        els.summaryDesc.textContent =
          "This extension is only half. Install the companion from GitHub.";
      }
      showInstallSteps({
        tone: "error",
        heading: "Install companion (GitHub — runtime not on npm)",
        leadHtml:
          "The companion runtime is installed from <strong>GitHub</strong> (not npm). " +
          "Run the one-liner below to install to " +
          "<code class=\"inline\">~/.hermes/hermes-chrome</code> " +
          "(bridge on <code class=\"inline\">127.0.0.1:19876</code> + Native Host + optional MCP). " +
          "Optional: <code class=\"inline\">npx hermes-chrome-companion</code> runs the same installer.",
        note:
          "Needs Python 3 + git · Source: github.com/leaf76/hermes-chrome · " +
          "MCP is optional (same install, not a separate store).",
      });
      setPrimary("Copy Install Command", "copyCmd");
      setSecondary("Full guide", "guide");
      return;
    }

    if (nh.ok === false && nhErr) {
      setDot("warn");
      if (els.summaryTitle) els.summaryTitle.textContent = "Companion not responding";
      if (els.summaryDesc) {
        els.summaryDesc.textContent =
          "Companion may be installed but the local bridge is down.";
      }
      showInstallSteps({
        tone: "warn",
        heading: "Restart the companion",
        leadHtml:
          "Try <strong>Reconnect</strong>. If that fails, open your " +
          "<strong>GitHub clone</strong> of hermes-chrome and re-run install-for-agent.",
        note: "Technical: " + nhErr,
        step3: "After the bridge is up, click Reconnect here.",
      });
      setPrimary("Reconnect", "reconnect");
      setSecondary("Open GitHub", "github");
      return;
    }

    setDot("warn");
    if (els.summaryTitle) els.summaryTitle.textContent = "Local bridge offline";
    if (els.summaryDesc) {
      els.summaryDesc.textContent =
        "No companion on this Mac/PC yet — or it is not running.";
    }
    showInstallSteps({
      tone: "warn",
      heading: "Start or install from GitHub",
      leadHtml:
        "If you already cloned the repo, <code class=\"inline\">cd</code> there and run " +
        "install-for-agent (or click Reconnect). " +
        "Otherwise clone from GitHub first — <strong>not npm</strong>.",
      note: "Agents talk only to your local bridge, never to the Chrome Web Store.",
    });
    setPrimary("Reconnect", "reconnect");
    setSecondary("Open GitHub", "github");
    return;
  }

  if (!s.bridgeAuth) {
    setDot("warn");
    if (els.summaryTitle) els.summaryTitle.textContent = "Connected (auth off)";
    if (els.summaryDesc) {
      els.summaryDesc.textContent =
        "Bridge is up, but auth is disabled. Not recommended for daily Chrome.";
    }
    hideSetup();
    if (els.setupPanel) {
      els.setupPanel.hidden = false;
      els.setupPanel.classList.add("is-warn");
      if (els.setupHeading) els.setupHeading.textContent = "Security note";
      if (els.setupLead) {
        els.setupLead.textContent =
          "ALLOW_NO_AUTH is set on the bridge. Prefer token + Pair for normal use.";
      }
      if (els.setupSteps) els.setupSteps.hidden = true;
      if (els.pairBlock) els.pairBlock.hidden = true;
      if (els.setupNote) els.setupNote.textContent = "";
    }
    setPrimary("Refresh", "refresh");
    setSecondary(null);
    return;
  }

  // Need pair — bridge already installed somewhere
  setDot("warn");
  if (els.summaryTitle) els.summaryTitle.textContent = "Pair required";
  if (els.summaryDesc) {
    els.summaryDesc.textContent = s.pairingOpen
      ? "Bridge is up. Click Pair to finish linking."
      : "Bridge is up. Run pair-open from your GitHub clone, then click Pair.";
  }
  showPairPanel(s);
  setPrimary("Pair", "pair");
  setSecondary("Refresh", "refresh");
}

function updateTech(s) {
  const view = { ...s };
  if (view.lastActivity && view.lastActivity.pngBase64) {
    view.lastActivity = { ...view.lastActivity, pngBase64: "[omitted]" };
  }
  if (els.out) els.out.textContent = JSON.stringify(view, null, 2);
  if (els.bridgeUrl) els.bridgeUrl.textContent = s.bridgeUrl || "";

  setBadge(els.bridgeBadge, !!s.bridgeOk, s.bridgeOk ? "online" : "offline");
  setBadge(els.pollBadge, !!s.polling, s.polling ? "active" : "idle");
  setBadge(
    els.groupBadge,
    s.running ? true : null,
    s.running ? s.title || "Hermes" : "none"
  );
  const ver = s.version || chrome.runtime.getManifest().version;
  const hostVer = companionVersion(s);
  const drift = versionDrift(s);
  setBadge(els.versionBadge, drift ? false : true, hostVer ? `${ver} / ${hostVer}` : ver || "?");
  const last = formatLast(s.lastActivity);
  setBadge(els.lastBadge, s.lastActivity ? true : null, last.badge);
  if (els.lastLine) els.lastLine.textContent = last.line;

  if (!s.bridgeOk) {
    setBadge(els.authBadge, false, "no bridge");
    if (els.authLine) {
      if (nativeHostMissing(s)) {
        els.authLine.textContent = "Native host not found — install companion from GitHub.";
      } else if (s.nativeHost && s.nativeHost.ok === false && s.nativeHost.error) {
        els.authLine.textContent = "Native host: " + s.nativeHost.error;
      } else {
        els.authLine.textContent = "Bridge not reachable on :19876.";
      }
    }
  } else if (!s.bridgeAuth) {
    setBadge(els.authBadge, false, "off");
    if (els.authLine) els.authLine.textContent = "Auth disabled (ALLOW_NO_AUTH).";
  } else if (s.tokenSet && s.authReady) {
    setBadge(els.authBadge, true, "ready");
    if (els.authLine) {
      els.authLine.textContent = s.pairingOpen
        ? "Token set · pairing window still open"
        : "Token set";
    }
  } else {
    setBadge(els.authBadge, false, "need pair");
    if (els.authLine) {
      els.authLine.textContent = s.pairingOpen
        ? "Pairing open — use Pair"
        : "From GitHub clone: ./scripts/hermes-chrome.sh pair-open";
    }
  }
}

async function refresh() {
  const s = await chrome.runtime.sendMessage({ type: "status" });
  updateUserFacing(s);
  updateTech(s);
}

function openGuide() {
  chrome.tabs.create({ url: chrome.runtime.getURL("help.html"), active: true });
}

function openGithub() {
  chrome.tabs.create({ url: REPO, active: true });
}

async function runAction(action) {
  if (action === "refresh") {
    await refresh();
    return;
  }
  if (action === "reconnect") {
    await chrome.runtime.sendMessage({ type: "reconnect" });
    await refresh();
    return;
  }
  if (action === "pair") {
    const r = await chrome.runtime.sendMessage({ type: "pair" });
    if (els.authLine) {
      els.authLine.textContent =
        r && r.ok ? r.hint || "Paired" : (r && r.error) || "Pair failed";
    }
    if (els.summaryDesc && r && !r.ok) {
      els.summaryDesc.textContent =
        (r && r.error) || "Pair failed — open Technical details or paste token in Options.";
    }
    await refresh();
    return;
  }
  if (action === "copyCmd") {
    await copyText(els.setupCmd ? els.setupCmd.textContent : CMD_INSTALL, els.btnPrimary);
    return;
  }
  if (action === "guide") {
    openGuide();
    return;
  }
  if (action === "github") {
    openGithub();
    return;
  }
  if (action === "stopWorkspace") {
    const r = await chrome.runtime.sendMessage({
      type: "stopWorkspace",
      closeTabs: true,
    });
    if (els.summaryDesc) {
      els.summaryDesc.textContent =
        r && r.ok
          ? `Workspace closed (${r.closed ?? 0} tab(s)). Companion still running.`
          : (r && r.error) || "Could not close workspace.";
    }
    await refresh();
  }
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = prev || "Copy";
        btn.classList.remove("copied");
      }, 1500);
    }
  } catch (_) {
    if (btn) btn.textContent = "Select & copy";
  }
}

if (els.btnSidePanel) {
  els.btnSidePanel.onclick = async () => {
    try {
      const win = await chrome.windows.getCurrent();
      await chrome.runtime.sendMessage({ type: "open_side_panel", windowId: win.id });
      window.close();
    } catch {
      /* ignore */
    }
  };
}
if (els.btnPrimary) {
  els.btnPrimary.onclick = () => runAction(primaryAction);
}
if (els.btnSecondary) {
  els.btnSecondary.onclick = () => runAction(secondaryAction || "refresh");
}
if (els.btnGuide) els.btnGuide.onclick = openGuide;
if (els.btnSetupGuide) els.btnSetupGuide.onclick = openGuide;
if (els.btnOptions) {
  els.btnOptions.onclick = () => chrome.runtime.openOptionsPage();
}
if (els.btnCopyMcp) {
  els.btnCopyMcp.onclick = async () => {
    const r = await chrome.runtime.sendMessage({ type: "getMcpConfig" });
    if (r && r.json) {
      await copyText(r.json, els.btnCopyMcp);
    }
  };
}
if (els.btnReconnect) {
  els.btnReconnect.onclick = () => runAction("reconnect");
}
if (els.btnCopyCmd) {
  els.btnCopyCmd.onclick = () =>
    copyText(els.setupCmd ? els.setupCmd.textContent : CMD_INSTALL, els.btnCopyCmd);
}
if (els.btnCopyAiPrompt) {
  els.btnCopyAiPrompt.onclick = () =>
    copyText(AI_INSTALL_PROMPT, els.btnCopyAiPrompt);
}
if (els.btnCopyPair) {
  els.btnCopyPair.onclick = () =>
    copyText(els.pairCmd ? els.pairCmd.textContent : CMD_PAIR_ONLY, els.btnCopyPair);
}

refresh();
