const out = document.getElementById("out");
const bridgeBadge = document.getElementById("bridgeBadge");
const pollBadge = document.getElementById("pollBadge");
const groupBadge = document.getElementById("groupBadge");
const bridgeUrl = document.getElementById("bridgeUrl");

function setBadge(el, ok, text) {
  el.textContent = text;
  el.className = "badge " + (ok === true ? "ok" : ok === false ? "bad" : "warn");
}

async function refresh() {
  const s = await chrome.runtime.sendMessage({ type: "status" });
  out.textContent = JSON.stringify(s, null, 2);
  bridgeUrl.textContent = s.bridgeUrl || "";
  setBadge(bridgeBadge, !!s.bridgeOk, s.bridgeOk ? "online" : "offline");
  setBadge(pollBadge, !!s.polling, s.polling ? "active" : "idle");
  setBadge(
    groupBadge,
    s.running ? true : null,
    s.running ? s.title || "Hermes" : "none"
  );
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
