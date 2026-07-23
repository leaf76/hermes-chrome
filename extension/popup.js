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
      setBadge(authBadge, null, "n/a");
      if (authLine) authLine.textContent = "";
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

refresh();
