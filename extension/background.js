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

/** Match GC/NQ continuous titles; never treat MGC as GC. */
function classifyTvTab(title, url) {
  const ti = title || "";
  const u = url || "";
  const isMgc = /^MGC/i.test(ti) || /MGC/i.test(u);
  let isGC =
    /^(GC1!|GC!)/i.test(ti) ||
    /symbol=GC1|symbol=GC!|symbol=GC%21|COMEX%3AGC|COMEX:GC|CME%3AGC/i.test(u);
  if (isMgc) isGC = false;
  const isNQ =
    /^(NQ1!|NQ!|MNQ)/i.test(ti) ||
    /symbol=NQ1|symbol=NQ!|symbol=NQ%21|CME%3ANQ|CME:NQ|symbol=MNQ/i.test(u);
  const isChart = /tradingview\.com\/chart/i.test(u);
  return { isGC, isNQ, isChart, isMgc };
}

async function findTradingViewTab(prefer) {
  const mode = (prefer || "auto").toLowerCase();
  const tabs = await chrome.tabs.query({});
  let gc = null;
  let nq = null;
  let fallback = null;
  for (const t of tabs) {
    const { isGC, isNQ, isChart } = classifyTvTab(t.title, t.url);
    if (!isChart && !isGC && !isNQ) {
      // still allow title-only GC/NQ even if URL not chart (SPA)
      if (!isGC && !isNQ) continue;
    }
    if (!isChart && !(t.url || "").includes("tradingview.com")) continue;
    if (isGC && !gc) gc = t;
    else if (isNQ && !nq) nq = t;
    else if (!fallback && isChart) fallback = t;
  }
  if (mode === "gc") {
    if (!gc) throw new Error("no GC TradingView chart tab open");
    return gc;
  }
  if (mode === "nq") {
    if (!nq) throw new Error("no NQ TradingView chart tab open");
    return nq;
  }
  return gc || nq || fallback || null;
}

/**
 * Capture a tab viewport as PNG (any http/https page the user/CLI asks for).
 * Optional prefer=gc|nq is only a *finder hint* for gold workflows — not a product limit.
 */
async function captureTab(cmd) {
  const prefer = (cmd.prefer || cmd.product || "auto").toLowerCase();
  let tab = null;
  if (cmd.tabId) {
    tab = await chrome.tabs.get(Number(cmd.tabId));
  } else if (prefer === "active" || cmd.active) {
    const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = active;
  } else if (cmd.urlIncludes) {
    const tabs = await chrome.tabs.query({});
    const needle = String(cmd.urlIncludes).toLowerCase();
    tab = tabs.find((t) => (t.url || "").toLowerCase().includes(needle)) || null;
    if (!tab) throw new Error(`no tab matching urlIncludes=${cmd.urlIncludes}`);
  } else if (prefer === "gc" || prefer === "nq" || prefer === "auto") {
    // Optional title heuristics (e.g. futures symbols) — not site-locked
    tab = await findTradingViewTab(prefer);
    if (!tab && prefer === "auto") {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      tab = active;
    }
  } else {
    tab = await findTradingViewTab(prefer);
  }
  if (!tab) throw new Error("no matching tab to capture");

  await chrome.tabs.update(tab.id, { active: true });
  if (tab.windowId != null) {
    try {
      await chrome.windows.update(tab.windowId, { focused: false });
    } catch {
      /* avoid focus steal when possible */
    }
  }
  await new Promise((r) => setTimeout(r, Number(cmd.settleMs) || 400));

  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format: "png",
  });
  if (!dataUrl || !dataUrl.startsWith("data:image/png")) {
    throw new Error("captureVisibleTab returned empty/non-png");
  }
  const b64 = dataUrl.replace(/^data:image\/png;base64,/, "");
  const refreshed = await chrome.tabs.get(tab.id);
  return {
    tabId: tab.id,
    windowId: tab.windowId,
    title: refreshed.title || tab.title || "",
    url: refreshed.url || tab.url || "",
    prefer,
    mime: "image/png",
    encoding: "base64",
    pngBase64: b64,
    bytes: Math.floor((b64.length * 3) / 4),
  };
}

async function listTvTabs() {
  const tabs = await chrome.tabs.query({});
  const out = [];
  for (const t of tabs) {
    const c = classifyTvTab(t.title, t.url);
    if (c.isChart || c.isGC || c.isNQ) {
      out.push({
        id: t.id,
        title: t.title,
        url: t.url,
        active: t.active,
        groupId: t.groupId,
        ...c,
      });
    }
  }
  return { tabs: out };
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
    case "capture":
      return await captureTab(cmd);
    case "list_tv":
    case "list-tv":
      return await listTvTabs();
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
