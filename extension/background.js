/**
 * Hermes Chrome — MV3 service worker (Chrome Web Store build)
 *
 * Local companion so Hermes / agent CLIs can operate daily Chrome without
 * hijacking the user's active tab. Default workspace is a Tab Group; more
 * Chrome ops can land here without renaming the product.
 *
 * Long-polls a user-run local bridge (default http://127.0.0.1:19876).
 * No remote analytics. No third-party network calls.
 */

const DEFAULT_BRIDGE = "http://127.0.0.1:19876";
const GROUP_TITLE = "Hermes";
const GROUP_COLOR = "blue";
const GROUP_KEY = "hermesChromeGroupId";
const SETTINGS_KEY = "settings";
let polling = false;
let pollGeneration = 0;

async function getSettings() {
  const r = await chrome.storage.local.get(SETTINGS_KEY);
  const s = r[SETTINGS_KEY] || {};
  return {
    bridgeUrl: (s.bridgeUrl || DEFAULT_BRIDGE).replace(/\/$/, ""),
    groupTitle: s.groupTitle || GROUP_TITLE,
    groupColor: s.groupColor || GROUP_COLOR,
    pollingEnabled: s.pollingEnabled !== false,
  };
}

async function getBridgeBase() {
  const s = await getSettings();
  return s.bridgeUrl || DEFAULT_BRIDGE;
}

async function getStoredGroupId() {
  const r = await chrome.storage.session.get(GROUP_KEY);
  return r[GROUP_KEY] ?? null;
}

async function setStoredGroupId(id) {
  if (id == null) await chrome.storage.session.remove(GROUP_KEY);
  else await chrome.storage.session.set({ [GROUP_KEY]: id });
}

async function groupStillExists(groupId) {
  if (groupId == null) return false;
  try {
    await chrome.tabGroups.get(groupId);
    return true;
  } catch {
    return false;
  }
}

async function tabsInGroup(groupId) {
  const tabs = await chrome.tabs.query({});
  return tabs.filter((t) => t.groupId === groupId);
}

async function styleGroup(groupId) {
  const s = await getSettings();
  try {
    await chrome.tabGroups.update(groupId, {
      title: s.groupTitle,
      color: s.groupColor,
      collapsed: false,
    });
  } catch {
    /* ignore */
  }
}

async function ensureGroup(url) {
  let groupId = await getStoredGroupId();
  if (await groupStillExists(groupId)) {
    if (url) {
      const tabs = await tabsInGroup(groupId);
      if (tabs.length > 0) {
        await chrome.tabs.update(tabs[0].id, { url, active: false });
      } else {
        const tab = await chrome.tabs.create({ url, active: false });
        await chrome.tabs.group({ tabIds: [tab.id], groupId });
      }
    }
    await styleGroup(groupId);
    return { groupId, created: false };
  }

  const startUrl = url || "chrome://newtab/";
  const tab = await chrome.tabs.create({ url: startUrl, active: false });
  groupId = await chrome.tabs.group({ tabIds: [tab.id] });
  await styleGroup(groupId);
  await setStoredGroupId(groupId);
  return { groupId, created: true, tabId: tab.id };
}

async function openInGroup(url, { newTab = false } = {}) {
  const { groupId } = await ensureGroup(null);
  const tabs = await tabsInGroup(groupId);
  if (!newTab && tabs.length > 0) {
    await chrome.tabs.update(tabs[0].id, { url, active: false });
    return { groupId, tabId: tabs[0].id, mode: "navigate" };
  }
  const tab = await chrome.tabs.create({ url, active: false });
  await chrome.tabs.group({ tabIds: [tab.id], groupId });
  return { groupId, tabId: tab.id, mode: "new_tab" };
}

async function status() {
  const settings = await getSettings();
  const groupId = await getStoredGroupId();
  const exists = await groupStillExists(groupId);
  let bridgeOk = false;
  try {
    const res = await fetch(`${settings.bridgeUrl}/v1/health`, {
      cache: "no-store",
    });
    bridgeOk = res.ok;
  } catch {
    bridgeOk = false;
  }
  if (!exists) {
    return {
      running: false,
      groupId: null,
      tabs: [],
      bridgeUrl: settings.bridgeUrl,
      bridgeOk,
      polling: polling && settings.pollingEnabled,
      extension: "hermes-chrome",
      version: chrome.runtime.getManifest().version,
    };
  }
  const tabs = await tabsInGroup(groupId);
  let group = null;
  try {
    group = await chrome.tabGroups.get(groupId);
  } catch {
    /* ignore */
  }
  return {
    running: true,
    groupId,
    title: group?.title ?? settings.groupTitle,
    color: group?.color ?? settings.groupColor,
    collapsed: group?.collapsed ?? false,
    tabs: tabs.map((t) => ({
      id: t.id,
      url: t.url,
      title: t.title,
      active: t.active,
    })),
    bridgeUrl: settings.bridgeUrl,
    bridgeOk,
    polling: polling && settings.pollingEnabled,
    extension: "hermes-chrome",
    version: chrome.runtime.getManifest().version,
  };
}

async function stopGroup({ closeTabs = true } = {}) {
  const groupId = await getStoredGroupId();
  const exists = await groupStillExists(groupId);
  if (!exists) {
    await setStoredGroupId(null);
    return { stopped: true, closed: 0 };
  }
  const tabs = await tabsInGroup(groupId);
  let closed = 0;
  if (closeTabs) {
    for (const t of tabs) {
      try {
        await chrome.tabs.remove(t.id);
        closed += 1;
      } catch {
        /* ignore */
      }
    }
  } else if (tabs.length) {
    await chrome.tabs.ungroup(tabs.map((t) => t.id));
  }
  await setStoredGroupId(null);
  return { stopped: true, closed };
}

async function handleCommand(cmd) {
  switch (cmd.action) {
    case "ping":
      return {
        pong: true,
        extension: "hermes-chrome",
        version: chrome.runtime.getManifest().version,
      };
    case "start":
      return await ensureGroup(cmd.url || null);
    case "open":
      if (!cmd.url) throw new Error("url required");
      return await openInGroup(cmd.url, { newTab: false });
    case "new_tab":
    case "new-tab":
      if (!cmd.url) throw new Error("url required");
      return await openInGroup(cmd.url, { newTab: true });
    case "status":
      return await status();
    case "stop":
      return await stopGroup({ closeTabs: cmd.closeTabs !== false });
    default:
      throw new Error(`unknown action: ${cmd.action}`);
  }
}

async function postResult(bridge, id, payload) {
  try {
    await fetch(`${bridge}/v1/result`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, ...payload }),
    });
  } catch (e) {
    console.warn("postResult failed", e);
  }
}

async function pollOnce(bridge) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 28000);
  try {
    const res = await fetch(`${bridge}/v1/poll?timeout=25`, {
      signal: ctrl.signal,
      cache: "no-store",
    });
    if (res.status === 204 || res.status === 404 || !res.ok) return;
    const cmd = await res.json();
    if (!cmd || !cmd.id) return;
    try {
      const data = await handleCommand(cmd);
      await postResult(bridge, cmd.id, { ok: true, data });
    } catch (e) {
      await postResult(bridge, cmd.id, {
        ok: false,
        error: String(e?.message || e),
      });
    }
  } catch {
    /* bridge down / abort */
  } finally {
    clearTimeout(timer);
  }
}

async function pollLoop(generation) {
  while (polling && generation === pollGeneration) {
    const settings = await getSettings();
    if (!settings.pollingEnabled) {
      await new Promise((r) => setTimeout(r, 1000));
      continue;
    }
    await pollOnce(settings.bridgeUrl);
    await new Promise((r) => setTimeout(r, 200));
  }
}

function startPolling() {
  pollGeneration += 1;
  const generation = pollGeneration;
  polling = true;
  pollLoop(generation);
  // Keep service worker eligible for wake-ups (MV3).
  try {
    chrome.alarms.create("hermes-chrome-poll", { periodInMinutes: 1 });
  } catch {
    /* ignore */
  }
}

function stopPolling() {
  polling = false;
  pollGeneration += 1;
  try {
    chrome.alarms.clear("hermes-chrome-poll");
  } catch {
    /* ignore */
  }
}

chrome.runtime.onInstalled.addListener(() => startPolling());
chrome.runtime.onStartup.addListener(() => startPolling());
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "hermes-chrome-poll") startPolling();
});
startPolling();

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[SETTINGS_KEY]) startPolling();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "status") {
    status().then(sendResponse);
    return true;
  }
  if (msg?.type === "reconnect") {
    startPolling();
    sendResponse({ ok: true });
    return false;
  }
  if (msg?.type === "getSettings") {
    getSettings().then(sendResponse);
    return true;
  }
  if (msg?.type === "setSettings") {
    chrome.storage.local
      .set({ [SETTINGS_KEY]: msg.settings || {} })
      .then(() => {
        startPolling();
        sendResponse({ ok: true });
      });
    return true;
  }
  return false;
});
