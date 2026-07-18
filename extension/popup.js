const out = document.getElementById("out");
const bridgeBadge = document.getElementById("bridgeBadge");
const pollBadge = document.getElementById("pollBadge");
const groupBadge = document.getElementById("groupBadge");
const versionBadge = document.getElementById("versionBadge");
const lastBadge = document.getElementById("lastBadge");
const bridgeUrl = document.getElementById("bridgeUrl");
const lastLine = document.getElementById("lastLine");

function setBadge(el, ok, text) {
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
  // Avoid dumping huge capture blobs if present in status
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
}

document.getElementById("btnRefresh").onclick = refresh;
document.getElementById("btnReconnect").onclick = async () => {
  await chrome.runtime.sendMessage({ type: "reconnect" });
  await refresh();
};
document.getElementById("btnOptions").onclick = () => {
  chrome.runtime.openOptionsPage();
};

refresh();
