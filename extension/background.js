/**
 * Hermes Chrome — MV3 service worker (Chrome Web Store build)
 *
 * Local companion so Hermes / agent CLIs can operate daily Chrome without
 * hijacking the user's active tab. Default workspace is a Tab Group; more
 * Chrome ops can land here without renaming the product.
 *
 * Long-polls a local bridge (default http://127.0.0.1:19876).
 * When Native Messaging host is installed, connectNative auto-starts the bridge.
 * No remote analytics. No third-party network calls.
 */

const DEFAULT_BRIDGE = "http://127.0.0.1:19876";
// Keep in sync with bridge.py _MAX_RESULT_BYTES (base64 JSON over /v1/result).
const BRIDGE_RESULT_MAX_BYTES = 12 * 1024 * 1024;
const BRIDGE_FETCH_DEFAULT_MAX_BYTES = Math.max(
  1024,
  Math.floor((BRIDGE_RESULT_MAX_BYTES * 3) / 4) - 4096
);
const NATIVE_HOST_NAME = "com.leaf76.hermes_chrome";
const GROUP_TITLE = "Hermes";
const GROUP_COLOR = "blue";
const GROUP_KEY = "hermesChromeGroupId";
const AGENT_WINDOW_KEY = "hermesChromeAgentWindowId";
const DEFAULT_AGENT_WINDOW_WIDTH = 1280;
const DEFAULT_AGENT_WINDOW_HEIGHT = 840;
const SETTINGS_KEY = "settings";
const LAST_ACTIVITY_KEY = "lastActivity";
let polling = false;
let pollGeneration = 0;
let lastAuthErrorAt = 0;
let lastAutoPairAt = 0;
let autoPairFailStreak = 0;
const AUTO_PAIR_MIN_INTERVAL_MS = 2500;
const AUTO_PAIR_MAX_STREAK = 60; // ~2.5 min of attempts then slow down

/** @type {chrome.runtime.Port | null} */
let nativePort = null;
/** @type {{ ok: boolean|null, at: number, detail?: any, error?: string }} */
let nativeStatus = { ok: null, at: 0 };
let lastNativeEnsureAt = 0;
const NATIVE_ENSURE_MIN_INTERVAL_MS = 8000;

// Open side panel on extension action icon click
if (chrome.sidePanel?.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => {
      console.warn("setPanelBehavior failed:", err);
    });
}
if (chrome.action?.onClicked) {
  chrome.action.onClicked.addListener(async (tab) => {
    try {
      if (chrome.sidePanel?.open) {
        await chrome.sidePanel.open({ windowId: tab.windowId });
      }
    } catch (err) {
      console.warn("Failed to open side panel on click:", err);
    }
  });
}

/** Only http(s) for agent navigation / open (block javascript:/file:/etc). */
function assertHttpUrl(url, label = "url") {
  if (!url || typeof url !== "string") {
    throw new Error(`${label} required`);
  }
  let u;
  try {
    u = new URL(url);
  } catch {
    throw new Error(`${label} is not a valid URL`);
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    throw new Error(`${label} must be http(s); got ${u.protocol}`);
  }
  return u.href;
}

function isPrivateOrLocalHost(hostname) {
  const h = String(hostname || "")
    .toLowerCase()
    .replace(/^\[|\]$/g, "");
  if (!h) return true;
  if (h === "localhost" || h === "0.0.0.0" || h.endsWith(".localhost")) {
    return true;
  }
  // IPv4
  const m = h.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (m) {
    const a = m.slice(1).map(Number);
    if (a.some((n) => n > 255)) return true;
    const [b0, b1] = a;
    if (b0 === 10) return true;
    if (b0 === 127) return true;
    if (b0 === 0) return true;
    if (b0 === 169 && b1 === 254) return true;
    if (b0 === 172 && b1 >= 16 && b1 <= 31) return true;
    if (b0 === 192 && b1 === 168) return true;
    if (b0 === 100 && b1 >= 64 && b1 <= 127) return true; // CGNAT
    return false;
  }
  // IPv6 compressed forms we care about
  if (h === "::1" || h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe80")) {
    return true;
  }
  return false;
}

async function authHeaders(extra = {}) {
  const s = await getSettings();
  const headers = { ...extra };
  if (s.bridgeToken) {
    headers["X-Hermes-Chrome-Token"] = s.bridgeToken;
  }
  return headers;
}

async function assertTabInWorkspace(tabId, cmd = {}) {
  const settings = await getSettings();
  const allow =
    cmd.allowCrossWorkspace === true ||
    cmd.crossWorkspace === true ||
    settings.allowCrossWorkspace === true;
  if (allow) return;
  const groupId = await getStoredGroupId();
  if (!(await groupStillExists(groupId))) {
    throw new Error(
      "workspace group not running; start workspace first or set allowCrossWorkspace"
    );
  }
  const tab = await chrome.tabs.get(Number(tabId));
  if (tab.groupId !== groupId) {
    throw new Error(
      `tab ${tabId} is outside Hermes workspace (pass allowCrossWorkspace or enable in Options)`
    );
  }
}

async function noteActivity(kind, meta = {}) {
  try {
    const entry = {
      kind,
      at: Date.now(),
      version: chrome.runtime.getManifest().version,
      ...meta,
    };
    // Keep payload small for popup
    if (entry.bytes != null) entry.bytes = Number(entry.bytes);
    if (entry.url) entry.url = String(entry.url).slice(0, 200);
    if (entry.path) entry.path = String(entry.path).slice(0, 200);
    await chrome.storage.local.set({ [LAST_ACTIVITY_KEY]: entry });
  } catch {
    /* ignore */
  }
}

async function getLastActivity() {
  const r = await chrome.storage.local.get(LAST_ACTIVITY_KEY);
  return r[LAST_ACTIVITY_KEY] || null;
}

async function getSettings() {
  const r = await chrome.storage.local.get(SETTINGS_KEY);
  const s = r[SETTINGS_KEY] || {};
  return {
    bridgeUrl: (s.bridgeUrl || DEFAULT_BRIDGE).replace(/\/$/, ""),
    bridgeToken: typeof s.bridgeToken === "string" ? s.bridgeToken : "",
    groupTitle: s.groupTitle || GROUP_TITLE,
    groupColor: s.groupColor || GROUP_COLOR,
    pollingEnabled: s.pollingEnabled !== false,
    // Sensitive ops (eval/click/type/capture/fetch) stay in workspace unless opted in.
    allowCrossWorkspace: s.allowCrossWorkspace === true,
    // Cookie-aware fetch to private/link-local hosts (default deny).
    allowPrivateFetch: s.allowPrivateFetch === true,
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

async function getStoredAgentWindowId() {
  const r = await chrome.storage.session.get(AGENT_WINDOW_KEY);
  return r[AGENT_WINDOW_KEY] ?? null;
}

async function setStoredAgentWindowId(id) {
  if (id == null) await chrome.storage.session.remove(AGENT_WINDOW_KEY);
  else await chrome.storage.session.set({ [AGENT_WINDOW_KEY]: id });
}

async function windowStillExists(windowId) {
  if (windowId == null) return false;
  try {
    await chrome.windows.get(windowId);
    return true;
  } catch {
    return false;
  }
}

async function isDedicatedAgentWindow(windowId, groupId) {
  if (!(await windowStillExists(windowId))) return false;
  const winTabs = await chrome.tabs.query({ windowId });
  if (winTabs.length === 0) return true;
  return winTabs.every((t) => t.groupId === groupId);
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

function clampWindowSize(width, height) {
  const w = Number.isFinite(Number(width))
    ? Math.max(480, Math.min(3840, Math.round(Number(width))))
    : DEFAULT_AGENT_WINDOW_WIDTH;
  const h = Number.isFinite(Number(height))
    ? Math.max(360, Math.min(2160, Math.round(Number(height))))
    : DEFAULT_AGENT_WINDOW_HEIGHT;
  return { width: w, height: h };
}

async function ensureGroup(
  url,
  { separateWindow = false, width, height, updateExisting = true } = {}
) {
  const startUrl = url ? assertHttpUrl(url) : "chrome://newtab/";
  let groupId = await getStoredGroupId();
  let agentWinId = await getStoredAgentWindowId();

  if (separateWindow) {
    if (
      (await groupStillExists(groupId)) &&
      (await isDedicatedAgentWindow(agentWinId, groupId))
    ) {
      const tabs = (await tabsInGroup(groupId)).filter((t) => t.windowId === agentWinId);
      let tabId = tabs[0]?.id ?? null;
      if (url && updateExisting) {
        if (tabs.length > 0) {
          await chrome.tabs.update(tabs[0].id, { url: startUrl, active: false });
        } else {
          const tab = await chrome.tabs.create({
            windowId: agentWinId,
            url: startUrl,
            active: false,
          });
          await chrome.tabs.group({ tabIds: [tab.id], groupId });
          tabId = tab.id;
        }
      } else if (tabs.length === 0) {
        const tab = await chrome.tabs.create({
          windowId: agentWinId,
          url: startUrl,
          active: false,
        });
        await chrome.tabs.group({ tabIds: [tab.id], groupId });
        tabId = tab.id;
      }
      await styleGroup(groupId);
      return {
        groupId,
        windowId: agentWinId,
        separateWindow: true,
        created: false,
        tabId,
      };
    }

    // Create an unfocused dedicated agent window so user's working window stays untouched.
    const dims = clampWindowSize(width, height);
    const win = await chrome.windows.create({
      url: startUrl,
      focused: false,
      type: "normal",
      width: dims.width,
      height: dims.height,
    });
    const firstTab = (win.tabs && win.tabs[0]) || null;
    if (!firstTab) {
      throw new Error("failed to create tab in separate agent window");
    }
    groupId = await chrome.tabs.group({
      tabIds: [firstTab.id],
      createProperties: { windowId: win.id },
    });
    await styleGroup(groupId);
    await setStoredGroupId(groupId);
    await setStoredAgentWindowId(win.id);
    return {
      groupId,
      windowId: win.id,
      separateWindow: true,
      created: true,
      tabId: firstTab.id,
    };
  }

  if (await groupStillExists(groupId)) {
    const tabs = await tabsInGroup(groupId);
    let targetWinId = tabs[0]?.windowId ?? null;
    if (url && updateExisting) {
      if (tabs.length > 0) {
        await chrome.tabs.update(tabs[0].id, { url: startUrl, active: false });
      } else {
        const createOpts = { url: startUrl, active: false };
        if (await windowStillExists(agentWinId)) createOpts.windowId = agentWinId;
        const tab = await chrome.tabs.create(createOpts);
        await chrome.tabs.group({ tabIds: [tab.id], groupId });
        targetWinId = tab.windowId;
      }
    }
    await styleGroup(groupId);
    return {
      groupId,
      windowId: targetWinId,
      separateWindow: Boolean(agentWinId && targetWinId === agentWinId),
      created: false,
      tabId: tabs[0]?.id ?? null,
    };
  }

  // New workspace: allow chrome://newtab only when no URL given.
  await setStoredAgentWindowId(null);
  const tab = await chrome.tabs.create({ url: startUrl, active: false });
  groupId = await chrome.tabs.group({ tabIds: [tab.id] });
  await styleGroup(groupId);
  await setStoredGroupId(groupId);
  return {
    groupId,
    windowId: tab.windowId,
    separateWindow: false,
    created: true,
    tabId: tab.id,
  };
}

async function openInGroup(
  url,
  { newTab = false, separateWindow = false, width, height } = {}
) {
  const safeUrl = assertHttpUrl(url);
  const ensured = await ensureGroup(safeUrl, {
    separateWindow,
    width,
    height,
    updateExisting: !newTab,
  });
  if (ensured.created) {
    return {
      groupId: ensured.groupId,
      windowId: ensured.windowId,
      separateWindow: ensured.separateWindow,
      tabId: ensured.tabId,
      url: safeUrl,
      mode: ensured.separateWindow ? "new_window" : "new_tab",
    };
  }
  const tabs = await tabsInGroup(ensured.groupId);
  if (!newTab && tabs.length > 0) {
    return {
      groupId: ensured.groupId,
      windowId: tabs[0].windowId,
      separateWindow: ensured.separateWindow,
      tabId: tabs[0].id,
      url: safeUrl,
      mode: "navigate",
    };
  }
  const createOpts = { url: safeUrl, active: false };
  if (ensured.windowId != null && (await windowStillExists(ensured.windowId))) {
    createOpts.windowId = ensured.windowId;
  }
  const tab = await chrome.tabs.create(createOpts);
  await chrome.tabs.group({ tabIds: [tab.id], groupId: ensured.groupId });
  return {
    groupId: ensured.groupId,
    windowId: tab.windowId,
    separateWindow: ensured.separateWindow,
    tabId: tab.id,
    url: safeUrl,
    mode: "new_tab",
  };
}

/**
 * Send a one-shot message to the Native Messaging host.
 */
function sendNativeMessageAsync(msg) {
  return new Promise((resolve) => {
    if (!chrome.runtime.sendNativeMessage) {
      resolve({ ok: false, error: "nativeMessaging API unavailable" });
      return;
    }
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST_NAME, msg, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          resolve(response || { ok: true });
        }
      });
    } catch (e) {
      resolve({ ok: false, error: String(e?.message || e) });
    }
  });
}

/**
 * Ask the OS-registered Native Messaging host to ensure the local bridge is up.
 * No-op (with status error) when the companion is not installed.
 */
function ensureNativeCompanion(force = false) {
  const now = Date.now();
  if (!force && now - lastNativeEnsureAt < NATIVE_ENSURE_MIN_INTERVAL_MS) {
    return nativeStatus;
  }
  lastNativeEnsureAt = now;

  if (!chrome.runtime.connectNative) {
    nativeStatus = {
      ok: false,
      at: now,
      error: "nativeMessaging API unavailable",
    };
    return nativeStatus;
  }

  try {
    if (!nativePort) {
      nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);
      nativePort.onMessage.addListener((msg) => {
        nativeStatus = {
          ok: !!(msg && (msg.ok === true || msg.service === "hermes-chrome-bridge")),
          at: Date.now(),
          detail: msg,
        };
        noteActivity("native_host", {
          ok: nativeStatus.ok,
          event: msg?.event || msg?.action || "message",
        }).catch(() => {});
      });
      nativePort.onDisconnect.addListener(() => {
        const err = chrome.runtime.lastError?.message || "disconnected";
        // "Specified native messaging host not found" = companion not installed
        nativeStatus = {
          ok: false,
          at: Date.now(),
          error: err,
        };
        nativePort = null;
      });
    }
    nativePort.postMessage({ action: "ensure" });
  } catch (e) {
    nativeStatus = {
      ok: false,
      at: Date.now(),
      error: String(e?.message || e),
    };
    nativePort = null;
  }
  return nativeStatus;
}

async function status() {
  const settings = await getSettings();
  const groupId = await getStoredGroupId();
  const exists = await groupStillExists(groupId);
  let bridgeOk = false;
  let bridgeAuth = null;
  let pairingOpen = null;
  let bridgeHealth = null;
  try {
    const headers = await authHeaders();
    const res = await fetch(`${settings.bridgeUrl}/v1/health`, {
      cache: "no-store",
      headers,
    });
    bridgeOk = res.ok;
    if (res.ok) {
      bridgeHealth = await res.json();
      bridgeAuth = !!bridgeHealth.auth;
      pairingOpen = !!bridgeHealth.pairing_open;
    }
  } catch {
    bridgeOk = false;
  }
  // If bridge is down, nudge native host (may auto-start it).
  if (!bridgeOk) {
    ensureNativeCompanion(false);
  }
  const lastActivity = await getLastActivity();
  const tokenSet = !!(settings.bridgeToken && settings.bridgeToken.length);
  const authReady = !bridgeAuth || tokenSet;
  const native = {
    host: NATIVE_HOST_NAME,
    ok: nativeStatus.ok,
    at: nativeStatus.at,
    error: nativeStatus.error || null,
    detail: nativeStatus.detail
      ? {
          service: nativeStatus.detail.service,
          auth: nativeStatus.detail.auth,
          pairing_open: nativeStatus.detail.pairing_open,
          host_version: nativeStatus.detail.host_version,
        }
      : null,
  };
  const base = {
    bridgeUrl: settings.bridgeUrl,
    bridgeOk,
    bridgeAuth,
    pairingOpen,
    tokenSet,
    authReady,
    polling: polling && settings.pollingEnabled,
    extension: "hermes-chrome",
    version: chrome.runtime.getManifest().version,
    lastActivity,
    allowCrossWorkspace: settings.allowCrossWorkspace,
    nativeHost: native,
  };
  if (!exists) {
    return {
      running: false,
      groupId: null,
      tabs: [],
      ...base,
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
    ...base,
  };
}

async function stopGroup({ closeTabs = true } = {}) {
  const groupId = await getStoredGroupId();
  const exists = await groupStillExists(groupId);
  if (!exists) {
    await setStoredGroupId(null);
    await setStoredAgentWindowId(null);
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
  await setStoredAgentWindowId(null);
  return { stopped: true, closed };
}

/** Match GC/NQ continuous titles; never treat MGC as GC.
 * Title wins over stale ?symbol= in the URL (TV SPA often leaves old query).
 * If the title already names another product (e.g. 6E1!, NQ1!), do not
 * trust a leftover GC/NQ symbol= in the URL.
 */
function classifyTvTab(title, url) {
  const ti = title || "";
  const u = url || "";
  const isMgc = /^MGC/i.test(ti) || /symbol=MGC|[?&/]MGC/i.test(u);
  const titleGC = /^(GC1!|GC!)/i.test(ti);
  const titleNQ = /^(NQ1!|NQ!|MNQ)/i.test(ti);
  // e.g. GC1!, NQ1!, 6E1!, ES1! — title already shows a product
  const titleOtherProduct =
    !titleGC &&
    !titleNQ &&
    !isMgc &&
    /^[A-Z][A-Z0-9]*\d*!/i.test(ti.trim());
  const urlGC =
    /symbol=GC1|symbol=GC!|symbol=GC%21|COMEX%3AGC|COMEX:GC|CME%3AGC/i.test(u);
  const urlNQ =
    /symbol=NQ1|symbol=NQ!|symbol=NQ%21|CME%3ANQ|CME:NQ|symbol=MNQ/i.test(u);

  let isGC = false;
  let isNQ = false;
  if (titleGC) {
    isGC = true;
  } else if (titleNQ) {
    isNQ = true;
  } else if (!titleOtherProduct) {
    // Title not a product yet (loading / "Chart") — URL may help
    if (urlGC && !urlNQ) isGC = true;
    else if (urlNQ && !urlGC) isNQ = true;
  }
  if (isMgc) isGC = false;
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
    if (!isChart && !(t.url || "").includes("tradingview.com")) continue;
    if (!isChart && !isGC && !isNQ) continue;
    // Prefer title-matched product tabs first (avoid stale URL false GC/NQ)
    if (isGC && !gc) gc = t;
    if (isNQ && !nq) nq = t;
    if (!fallback && isChart) fallback = t;
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
 * Ensure TradingView chart interval is 3m or 5m (prefer preferred).
 * Best-effort DOM clicks; TV UI changes over time.
 * Returns { ok, before, after, changed, method }.
 */
async function ensureTradingViewTimeframe(tabId, opts = {}) {
  const preferred = String(opts.preferred || opts.prefer || "5");
  const allowed = Array.isArray(opts.allowed) && opts.allowed.length
    ? opts.allowed.map(String)
    : ["3", "5"];
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      args: [preferred, allowed],
      func: (preferredTf, allowedTfs) => {
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

        const normalize = (raw) => {
          const s = String(raw || "")
            .toLowerCase()
            .replace(/\s+/g, "")
            .replace("minutes", "m")
            .replace("minute", "m")
            .replace("min", "m");
          if (s === "3" || s === "3m" || s.includes("3m") || s === "3minutes") return "3";
          if (s === "5" || s === "5m" || s.includes("5m") || s === "5minutes") return "5";
          // bare number
          const m = s.match(/^(\d+)m?$/);
          if (m) return m[1];
          return null;
        };

        const readActiveTf = () => {
          const selectors = [
            "#header-toolbar-intervals button[aria-checked='true']",
            "#header-toolbar-intervals [role='radio'][aria-checked='true']",
            "[data-name='time-interval-select'] button[aria-checked='true']",
            "button[class*='isActive'][data-value]",
            "#header-toolbar-intervals button.isActive",
            "#header-toolbar-intervals button[class*='active']",
          ];
          for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (!el) continue;
            const v =
              el.getAttribute("data-value") ||
              el.getAttribute("data-interval") ||
              el.getAttribute("aria-label") ||
              el.textContent;
            const n = normalize(v);
            if (n) return n;
          }
          // fallback: any interval toolbar label that looks selected
          const bar = document.querySelector("#header-toolbar-intervals");
          if (bar) {
            const pressed = bar.querySelector(
              "button[aria-pressed='true'], [aria-selected='true']"
            );
            if (pressed) {
              const n = normalize(
                pressed.getAttribute("data-value") ||
                  pressed.getAttribute("aria-label") ||
                  pressed.textContent
              );
              if (n) return n;
            }
          }
          return null;
        };

        const clickTf = async (tf) => {
          // 1) Direct data-value button if already visible
          const direct = document.querySelector(
            `#header-toolbar-intervals [data-value='${tf}'], ` +
              `[data-name='time-interval-select'] [data-value='${tf}'], ` +
              `button[data-value='${tf}']`
          );
          if (direct) {
            direct.click();
            return "direct";
          }

          // 2) Open interval menu then pick
          const openers = [
            "#header-toolbar-intervals button",
            "#header-toolbar-intervals [data-role='button']",
            "[data-name='time-interval-select'] button",
            "button#header-toolbar-intervals",
          ];
          let opened = false;
          for (const sel of openers) {
            const btn = document.querySelector(sel);
            if (btn) {
              btn.click();
              opened = true;
              break;
            }
          }
          if (!opened) {
            // try the whole toolbar area
            const bar = document.querySelector("#header-toolbar-intervals");
            if (bar) {
              bar.click();
              opened = true;
            }
          }
          await sleep(250);

          const menuItem =
            document.querySelector(`[data-value='${tf}']`) ||
            Array.from(
              document.querySelectorAll(
                "[role='menuitem'], [role='option'], button, div[data-value]"
              )
            ).find((el) => {
              const t = (el.getAttribute("data-value") || el.textContent || "").trim();
              return (
                t === tf ||
                t === `${tf}m` ||
                t === `${tf} minute` ||
                t === `${tf} minutes` ||
                t.toLowerCase() === `${tf} minutes`
              );
            });
          if (menuItem) {
            menuItem.click();
            return "menu";
          }
          return null;
        };

        return (async () => {
          const before = readActiveTf();
          if (before && allowedTfs.map(String).includes(String(before))) {
            return {
              ok: true,
              before,
              after: before,
              changed: false,
              method: "already",
            };
          }
          const target = allowedTfs.map(String).includes(String(preferredTf))
            ? String(preferredTf)
            : String(allowedTfs[0] || "5");
          const method = await clickTf(target);
          await sleep(600);
          const after = readActiveTf();
          const ok =
            (after && allowedTfs.map(String).includes(String(after))) ||
            method === "direct" ||
            method === "menu";
          return {
            ok: !!ok,
            before,
            after: after || target,
            changed: true,
            method: method || "failed",
            preferred: target,
          };
        })();
      },
    });
    return (res && res.result) || { ok: false, method: "no_result" };
  } catch (e) {
    return { ok: false, method: "script_error", error: String(e && e.message ? e.message : e) };
  }
}

/**
 * Capture a tab viewport as PNG for **any** http(s) page.
 *
 * Resolution order (generic by default — no site hardcoding):
 *   1. tabId
 *   2. prefer=active / active:true  (opt-in; privacy)
 *   3. urlIncludes / titleIncludes needles
 *   4. prefer=auto (default): first tab in Hermes workspace
 *   5. prefer=gc|nq: optional TradingView product finder (legacy helper only)
 *
 * Optional ensureTf: { preferred: "5", allowed: ["3","5"] } — TradingView helper only.
 */
async function captureTab(cmd) {
  const prefer = (cmd.prefer || cmd.product || "auto").toLowerCase();
  const settings = await getSettings();
  let tab = null;
  if (cmd.tabId) {
    await assertTabInWorkspace(cmd.tabId, cmd);
    tab = await chrome.tabs.get(Number(cmd.tabId));
  } else if (prefer === "active" || cmd.active) {
    // Capturing the user's active tab is opt-in (privacy).
    if (
      cmd.allowActiveCapture !== true &&
      cmd.allowCrossWorkspace !== true &&
      !settings.allowCrossWorkspace
    ) {
      throw new Error(
        "capture of active tab requires allowActiveCapture or allowCrossWorkspace"
      );
    }
    const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = active;
  } else if (cmd.urlIncludes || cmd.titleIncludes) {
    const tabs = await chrome.tabs.query({});
    const urlNeedle = cmd.urlIncludes
      ? String(cmd.urlIncludes).toLowerCase()
      : "";
    const titleNeedle = cmd.titleIncludes
      ? String(cmd.titleIncludes).toLowerCase()
      : "";
    tab =
      tabs.find((t) => {
        const u = (t.url || "").toLowerCase();
        const ti = (t.title || "").toLowerCase();
        if (urlNeedle && !u.includes(urlNeedle)) return false;
        if (titleNeedle && !ti.includes(titleNeedle)) return false;
        return !!(urlNeedle || titleNeedle);
      }) || null;
    if (!tab) {
      throw new Error(
        `no tab matching urlIncludes=${cmd.urlIncludes || ""} titleIncludes=${cmd.titleIncludes || ""}`
      );
    }
    await assertTabInWorkspace(tab.id, cmd);
  } else if (prefer === "gc" || prefer === "nq") {
    // Optional product finder (not the default product path).
    tab = await findTradingViewTab(prefer);
    if (tab) {
      await assertTabInWorkspace(tab.id, {
        ...cmd,
        allowCrossWorkspace:
          cmd.allowCrossWorkspace === true || settings.allowCrossWorkspace,
      });
    }
  } else {
    // prefer=auto (default) and any other non-TV prefer: Hermes workspace tab.
    const groupId = await getStoredGroupId();
    if (await groupStillExists(groupId)) {
      const wt = await tabsInGroup(groupId);
      tab = wt.find((t) => t.active) || wt[0] || null;
    }
    if (!tab) {
      throw new Error(
        "no workspace tab to capture — open a URL with start/open, or pass tabId / urlIncludes / prefer=active"
      );
    }
    await assertTabInWorkspace(tab.id, cmd);
  }
  if (!tab) throw new Error("no matching tab to capture");

  // Activate target so background pages paint (any site). Keep window unfocused when possible.
  await chrome.tabs.update(tab.id, { active: true });
  if (tab.windowId != null) {
    try {
      await chrome.windows.update(tab.windowId, { focused: false });
    } catch {
      /* avoid focus steal when possible */
    }
  }

  // Optional TradingView timeframe helper (only when caller requests ensureTf).
  let tfInfo = null;
  const ensureTf = cmd.ensureTf || cmd.ensureTimeframe || null;
  if (ensureTf) {
    tfInfo = await ensureTradingViewTimeframe(tab.id, ensureTf);
    const tfWait = Number(cmd.tfSettleMs);
    await new Promise((r) =>
      setTimeout(r, Number.isFinite(tfWait) && tfWait >= 0 ? tfWait : 1200)
    );
  }

  // Nudge page to paint after background wake (generic; safe no-op on most sites).
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: () => {
        try {
          window.dispatchEvent(new Event("focus"));
          window.dispatchEvent(new Event("resize"));
          document.dispatchEvent(new Event("visibilitychange"));
        } catch (_) {
          /* ignore */
        }
      },
    });
  } catch {
    /* scripting may be unavailable on some pages — settle still helps */
  }

  // Default settle: short for generic pages; callers can raise settleMs for heavy apps.
  const settle = Number(cmd.settleMs);
  const settleMs = Number.isFinite(settle) && settle >= 0 ? settle : 600;
  await new Promise((r) => setTimeout(r, settleMs));

  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format: "png",
  });
  if (!dataUrl || !dataUrl.startsWith("data:image/png")) {
    throw new Error("captureVisibleTab returned empty/non-png");
  }
  const b64 = dataUrl.replace(/^data:image\/png;base64,/, "");
  // Refresh title after settle (price/tick often updates).
  const refreshed = await chrome.tabs.get(tab.id);
  const out = {
    tabId: tab.id,
    windowId: tab.windowId,
    title: refreshed.title || tab.title || "",
    url: refreshed.url || tab.url || "",
    prefer,
    settleMs,
    timeframe: tfInfo,
    mime: "image/png",
    encoding: "base64",
    pngBase64: b64,
    bytes: Math.floor((b64.length * 3) / 4),
  };
  await noteActivity("capture", {
    url: out.url,
    title: (out.title || "").slice(0, 120),
    bytes: out.bytes,
    tabId: out.tabId,
    prefer,
  });
  return out;
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

/** Generic tab list for agents (not TV-only). */
async function listTabs(cmd = {}) {
  // Default: workspace only. Pass allTabs:true or groupOnly:false for every tab.
  const effectiveGroupOnly = !(
    cmd.allTabs === true ||
    cmd.groupOnly === false ||
    cmd.workspaceOnly === false
  );
  const urlIncludes = cmd.urlIncludes
    ? String(cmd.urlIncludes).toLowerCase()
    : null;
  const titleIncludes = cmd.titleIncludes
    ? String(cmd.titleIncludes).toLowerCase()
    : null;
  const limit = Math.max(1, Math.min(Number(cmd.limit) || 200, 500));

  let groupId = await getStoredGroupId();
  if (effectiveGroupOnly) {
    if (!(await groupStillExists(groupId))) {
      return { tabs: [], groupId: null, count: 0, groupOnly: true };
    }
  } else if (!(await groupStillExists(groupId))) {
    groupId = null;
  }

  const tabs = await chrome.tabs.query({});
  const out = [];
  for (const t of tabs) {
    if (effectiveGroupOnly && t.groupId !== groupId) continue;
    const url = t.url || "";
    const title = t.title || "";
    if (urlIncludes && !url.toLowerCase().includes(urlIncludes)) continue;
    if (titleIncludes && !title.toLowerCase().includes(titleIncludes)) continue;
    out.push({
      id: t.id,
      windowId: t.windowId,
      groupId: t.groupId,
      url,
      title,
      active: t.active,
      pinned: t.pinned,
      status: t.status,
      inWorkspace: groupId != null && t.groupId === groupId,
    });
    if (out.length >= limit) break;
  }
  return {
    tabs: out,
    count: out.length,
    groupId,
    filters: {
      groupOnly: effectiveGroupOnly,
      urlIncludes: urlIncludes || null,
      titleIncludes: titleIncludes || null,
      limit,
    },
  };
}

/**
 * Navigate an existing tab without stealing focus (active:false default).
 * Resolves tab by tabId, else workspace first tab, else create in group.
 */
async function navigateTab(cmd) {
  const url = assertHttpUrl(cmd.url);
  const activate = cmd.active === true;
  let tabId = cmd.tabId != null ? Number(cmd.tabId) : null;

  if (tabId != null && Number.isFinite(tabId)) {
    await assertTabInWorkspace(tabId, cmd);
    const updated = await chrome.tabs.update(tabId, {
      url,
      active: activate,
    });
    return {
      tabId: updated.id,
      windowId: updated.windowId,
      url: updated.pendingUrl || updated.url || url,
      mode: "navigate",
      active: !!updated.active,
    };
  }

  const ensured = await ensureGroup(url, {
    separateWindow: cmd.separateWindow === true,
    width: cmd.width,
    height: cmd.height,
  });
  return {
    groupId: ensured.groupId,
    tabId: ensured.tabId,
    windowId: ensured.windowId,
    separateWindow: ensured.separateWindow,
    url,
    mode: ensured.created && ensured.separateWindow ? "new_window" : "navigate",
    active: false,
  };
}

function jsonSafe(value, depth = 0) {
  if (depth > 4) return "[MaxDepth]";
  if (value == null) return value;
  const t = typeof value;
  if (t === "string" || t === "number" || t === "boolean") return value;
  if (t === "bigint") return String(value);
  if (t === "function" || t === "symbol" || t === "undefined") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.slice(0, 50).map((v) => jsonSafe(v, depth + 1));
  }
  if (t === "object") {
    const out = {};
    let n = 0;
    for (const k of Object.keys(value)) {
      if (n >= 40) {
        out._truncated = true;
        break;
      }
      try {
        out[k] = jsonSafe(value[k], depth + 1);
        n += 1;
      } catch {
        out[k] = "[Unserializable]";
      }
    }
    return out;
  }
  return String(value);
}

/** Lightweight DOM read via expression (MAIN world). Local bridge only. */
async function evalInTab(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  await assertTabInWorkspace(tabId, cmd);
  const expression = cmd.expression || cmd.code;
  if (!expression || typeof expression !== "string") {
    throw new Error("expression required");
  }
  if (expression.length > 8000) throw new Error("expression too long (max 8000)");

  // Default MAIN world for DOM/page execution; ISOLATED when requested.
  const world =
    cmd.world === "ISOLATED" ? "ISOLATED" : "MAIN";

  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world,
    args: [expression],
    func: (expr) => {
      try {
        // Expression-only eval (not statements) for safer agent probes.
        // eslint-disable-next-line no-new-func
        const value = Function(`"use strict"; return (${expr});`)();
        return { ok: true, value };
      } catch (e) {
        return { ok: false, error: String(e && e.message ? e.message : e) };
      }
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) {
    const rawErr = result.error || "eval failed";
    if (/unsafe-eval|Content Security Policy/i.test(rawErr)) {
      throw new Error(
        `${rawErr} (CSP blocks unsafe-eval on this page; use dom_query, click, editor_get, or editor_set instead)`
      );
    }
    throw new Error(rawErr);
  }
  return {
    tabId,
    world,
    value: jsonSafe(result.value),
  };
}

/**
 * Click element by CSS selector and/or visible text/aria-label (CSP-safe static func).
 * Uses MAIN world execution without touching window focus.
 */
async function clickInTab(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  await assertTabInWorkspace(tabId, cmd);
  const selector = cmd.selector ? String(cmd.selector) : "";
  const exactText = cmd.text != null && String(cmd.text).trim() !== "" ? String(cmd.text).trim() : "";
  const textIncludes =
    cmd.textIncludes != null && String(cmd.textIncludes).trim() !== ""
      ? String(cmd.textIncludes).trim()
      : "";
  if (!selector && !exactText && !textIncludes) {
    throw new Error("selector, text, or textIncludes required");
  }
  const index = Math.max(0, Number(cmd.index) || 0);
  const visibleOnly =
    cmd.visibleOnly !== undefined ? Boolean(cmd.visibleOnly) : Boolean(exactText || textIncludes);
  const mouseEvents = cmd.mouseEvents === true;
  const doScroll = cmd.scroll !== false;

  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [selector, exactText, textIncludes, index, visibleOnly, mouseEvents, doScroll],
    func: (sel, exact, substr, idx, visOnly, fireMouse, scroll) => {
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        const cs = getComputedStyle(el);
        return cs.display !== "none" && cs.visibility !== "hidden" && cs.opacity !== "0";
      };
      const labelsFor = (el) => [
        el.innerText || "",
        el.textContent || "",
        el.getAttribute ? el.getAttribute("aria-label") || "" : "",
        el.getAttribute ? el.getAttribute("title") || "" : "",
        el.getAttribute ? el.getAttribute("data-name") || "" : "",
        typeof el.value === "string" ? el.value : "",
      ];

      let candidates;
      if (sel) {
        candidates = Array.from(document.querySelectorAll(sel));
      } else {
        candidates = Array.from(
          document.querySelectorAll(
            'button,a,input[type="button"],input[type="submit"],summary,[role="button"],[role="tab"],[role="menuitem"],[data-name],[aria-label],[title]'
          )
        );
      }

      const exactLower = exact ? exact.toLowerCase() : "";
      const subLower = substr ? substr.toLowerCase() : "";

      const filterList = (list) =>
        list.filter((el) => {
          if (visOnly && !isVisible(el)) return false;
          if (!exactLower && !subLower) return true;
          const labels = labelsFor(el).map((s) => String(s).replace(/\s+/g, " ").trim().toLowerCase());
          if (exactLower && !labels.some((s) => s === exactLower)) return false;
          if (subLower && !labels.some((s) => s.includes(subLower))) return false;
          return true;
        });

      let matches = filterList(candidates);
      if (matches.length === 0 && !sel && (exactLower || subLower)) {
        // Fallback to any DOM leaf element when custom widgets don't use button roles.
        const all = Array.from(document.querySelectorAll("*"));
        matches = filterList(all).filter((el) => {
          if (!el.children || el.children.length === 0) return true;
          return !Array.from(el.children).some((c) => filterList([c]).length > 0);
        });
      }

      if (matches.length === 0) {
        const desc = [
          sel ? `selector=${sel}` : "",
          exact ? `text=${exact}` : "",
          substr ? `textIncludes=${substr}` : "",
        ]
          .filter(Boolean)
          .join(", ");
        return { ok: false, error: `no element matching ${desc}` };
      }

      const el = matches[Math.min(idx, matches.length - 1)];
      if (scroll && typeof el.scrollIntoView === "function") {
        try {
          el.scrollIntoView({ block: "nearest", inline: "nearest" });
        } catch {
          /* ignore */
        }
      }
      if (fireMouse) {
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
          el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      el.click();
      return {
        ok: true,
        tag: el.tagName,
        id: el.id || null,
        ariaLabel: el.getAttribute ? el.getAttribute("aria-label") || null : null,
        title: el.getAttribute ? el.getAttribute("title") || null : null,
        text: (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120),
        matchedCount: matches.length,
      };
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) throw new Error(result.error || "click failed");
  return { tabId, selector: selector || null, ...result };
}

/**
 * Inspect visible/matching DOM elements without unsafe-eval (CSP-safe static func).
 */
async function domQueryInTab(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  await assertTabInWorkspace(tabId, cmd);

  const selector = cmd.selector ? String(cmd.selector) : "";
  const exactText = cmd.text != null && String(cmd.text).trim() !== "" ? String(cmd.text).trim() : "";
  const textIncludes =
    cmd.textIncludes != null && String(cmd.textIncludes).trim() !== ""
      ? String(cmd.textIncludes).trim()
      : "";
  const ariaLabel =
    cmd.ariaLabel != null && String(cmd.ariaLabel).trim() !== "" ? String(cmd.ariaLabel).trim() : "";
  const visibleOnly = cmd.visibleOnly !== false;
  const limit = Math.min(100, Math.max(1, Number(cmd.limit) || 25));
  const textMax = Math.min(2000, Math.max(40, Number(cmd.textMax) || 200));

  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [selector, exactText, textIncludes, ariaLabel, visibleOnly, limit, textMax],
    func: (sel, exact, substr, aria, visOnly, cap, txtCap) => {
      const isVisible = (el) => {
        if (!el || !el.getBoundingClientRect) return false;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        const cs = getComputedStyle(el);
        return cs.display !== "none" && cs.visibility !== "hidden" && cs.opacity !== "0";
      };
      const defaultPool =
        'button,a,input,textarea,select,[role="button"],[role="tab"],[role="menuitem"],[role="textbox"],[data-name],[aria-label],[title],[contenteditable="true"]';
      let pool = Array.from(document.querySelectorAll(sel || defaultPool));

      const exactLower = exact ? exact.toLowerCase() : "";
      const subLower = substr ? substr.toLowerCase() : "";
      const ariaLower = aria ? aria.toLowerCase() : "";

      const matchEl = (el) => {
        const vis = isVisible(el);
        if (visOnly && !vis) return false;
        const ariaVal = (el.getAttribute && el.getAttribute("aria-label")) || "";
        const titleVal = (el.getAttribute && el.getAttribute("title")) || "";
        const dataName = (el.getAttribute && el.getAttribute("data-name")) || "";
        const ph = (el.getAttribute && el.getAttribute("placeholder")) || "";
        const txt = String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
        const val = typeof el.value === "string" ? el.value : "";

        if (ariaLower && !ariaVal.toLowerCase().includes(ariaLower)) return false;
        if (exactLower) {
          const hit = [txt, val, ariaVal, titleVal, dataName, ph].some(
            (s) => s.trim().toLowerCase() === exactLower
          );
          if (!hit) return false;
        }
        if (subLower) {
          const hit = [txt, val, ariaVal, titleVal, dataName, ph].some((s) =>
            s.toLowerCase().includes(subLower)
          );
          if (!hit) return false;
        }
        return true;
      };

      let matches = pool.filter(matchEl);
      if (matches.length === 0 && !sel && (exactLower || subLower || ariaLower)) {
        const all = Array.from(document.querySelectorAll("*"));
        matches = all.filter(matchEl).filter((el) => {
          if (!el.children || el.children.length === 0) return true;
          return !Array.from(el.children).some((c) => matchEl(c));
        });
      }

      const items = matches.slice(0, cap).map((el, i) => {
        const r = el.getBoundingClientRect();
        const item = {
          index: i,
          tag: el.tagName,
          visible: isVisible(el),
          rect: {
            x: Math.round(r.x),
            y: Math.round(r.y),
            w: Math.round(r.width),
            h: Math.round(r.height),
          },
        };
        if (el.id) item.id = el.id;
        const role = el.getAttribute && el.getAttribute("role");
        if (role) item.role = role;
        const dataName = el.getAttribute && el.getAttribute("data-name");
        if (dataName) item.dataName = dataName;
        const ariaVal = el.getAttribute && el.getAttribute("aria-label");
        if (ariaVal) item.ariaLabel = ariaVal.slice(0, txtCap);
        const titleVal = el.getAttribute && el.getAttribute("title");
        if (titleVal) item.title = titleVal.slice(0, txtCap);
        const nameAttr = el.getAttribute && el.getAttribute("name");
        if (nameAttr) item.name = nameAttr;
        const txt = String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
        if (txt) item.text = txt.slice(0, txtCap);
        if (typeof el.value === "string" && el.value) item.value = el.value.slice(0, txtCap);
        if (el.disabled === true || (el.getAttribute && el.getAttribute("aria-disabled") === "true")) {
          item.disabled = true;
        }
        return item;
      });

      return {
        ok: true,
        totalMatched: matches.length,
        count: items.length,
        truncated: matches.length > items.length,
        items,
      };
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) throw new Error(result.error || "dom_query failed");
  return { tabId, ...result };
}

/**
 * Fetch a URL using the browser cookie jar (credentials: include).
 * Returns base64 body + response meta. Size-capped for bridge safety.
 */
async function fetchUrl(cmd) {
  const url = assertHttpUrl(cmd.url);
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("invalid url");
  }
  const settings = await getSettings();
  const allowPrivate =
    cmd.allowPrivate === true || settings.allowPrivateFetch === true;
  if (!allowPrivate && isPrivateOrLocalHost(parsed.hostname)) {
    throw new Error(
      `refusing private/local host for fetch_url: ${parsed.hostname} (enable allowPrivateFetch in Options or pass allowPrivate)`
    );
  }
  // Block obvious cloud metadata hosts even if DNS later rebinds.
  const host = parsed.hostname.toLowerCase();
  if (
    host === "metadata.google.internal" ||
    host === "metadata" ||
    host.endsWith(".internal")
  ) {
    if (!allowPrivate) {
      throw new Error(`refusing metadata/internal host: ${host}`);
    }
  }
  const maxBytes = Math.max(
    1024,
    Math.min(
      Number(cmd.maxBytes) || BRIDGE_FETCH_DEFAULT_MAX_BYTES,
      BRIDGE_FETCH_DEFAULT_MAX_BYTES
    )
  );
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), Number(cmd.timeoutMs) || 60000);
  try {
    const res = await fetch(url, {
      method: "GET",
      credentials: "include",
      redirect: "follow",
      cache: "no-store",
      signal: ctrl.signal,
    });
    // After redirects, re-check final host.
    try {
      const finalHost = new URL(res.url || url).hostname;
      if (!allowPrivate && isPrivateOrLocalHost(finalHost)) {
        throw new Error(
          `refusing redirect to private/local host: ${finalHost}`
        );
      }
    } catch (e) {
      if (String(e.message || e).includes("refusing")) throw e;
    }
    const cl = res.headers.get("content-length");
    if (cl && Number(cl) > maxBytes) {
      throw new Error(
        `content-length ${cl} exceeds maxBytes ${maxBytes}`
      );
    }
    const buf = await res.arrayBuffer();
    if (buf.byteLength > maxBytes) {
      throw new Error(
        `body ${buf.byteLength} exceeds maxBytes ${maxBytes}`
      );
    }
    // base64 without huge stack: chunked
    const bytes = new Uint8Array(buf);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(
        null,
        bytes.subarray(i, i + chunk)
      );
    }
    const bodyBase64 = btoa(binary);
    const out = {
      ok: true,
      status: res.status,
      finalUrl: res.url || url,
      contentType: res.headers.get("content-type"),
      contentDisposition: res.headers.get("content-disposition"),
      contentLength: buf.byteLength,
      bodyBase64,
      encoding: "base64",
    };
    await noteActivity("fetch_url", {
      url: out.finalUrl,
      bytes: out.contentLength,
      status: out.status,
      contentType: out.contentType,
    });
    return out;
  } finally {
    clearTimeout(timer);
  }
}

/** List http(s) links/images on a tab (lightweight inventory). */
async function listPageAssets(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  await assertTabInWorkspace(tabId, cmd);
  const limit = Math.max(1, Math.min(Number(cmd.limit) || 200, 500));
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [limit],
    func: (lim) => {
      const abs = (u) => {
        try {
          return new URL(u, location.href).href;
        } catch {
          return null;
        }
      };
      const links = [];
      const images = [];
      for (const a of document.querySelectorAll("a[href]")) {
        const href = abs(a.getAttribute("href"));
        if (!href || !/^https?:/i.test(href)) continue;
        links.push({
          href,
          text: (a.innerText || a.textContent || "").trim().slice(0, 120),
          download: a.getAttribute("download"),
        });
        if (links.length >= lim) break;
      }
      for (const img of document.querySelectorAll("img[src]")) {
        const src = abs(img.getAttribute("src"));
        if (!src || !/^https?:/i.test(src)) continue;
        images.push({
          src,
          alt: (img.getAttribute("alt") || "").slice(0, 80),
        });
        if (images.length >= lim) break;
      }
      return {
        pageUrl: location.href,
        title: document.title || "",
        links,
        images,
      };
    },
  });
  return (res && res.result) || { links: [], images: [] };
}

/** Type into element by CSS selector (supports React inputs, optional clear first). */
async function typeInTab(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  await assertTabInWorkspace(tabId, cmd);
  const selector = cmd.selector;
  const text = cmd.text != null ? String(cmd.text) : "";
  if (!selector || typeof selector !== "string") {
    throw new Error("selector required");
  }
  if (text.length > 1000000) throw new Error("text too long (max 1000000)");
  const clear = cmd.clear !== false;
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [selector, text, clear],
    func: (sel, value, doClear) => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, error: `no element matching ${sel}` };
      el.focus();
      if ("value" in el) {
        const next = doClear ? value : String(el.value || "") + value;
        const proto = Object.getPrototypeOf(el);
        const setter = proto && Object.getOwnPropertyDescriptor(proto, "value")?.set;
        if (setter) setter.call(el, next);
        else el.value = next;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      } else if (el.isContentEditable) {
        if (doClear) el.textContent = "";
        el.textContent = (doClear ? "" : el.textContent || "") + value;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        return { ok: false, error: "element is not editable" };
      }
      return { ok: true, tag: el.tagName, id: el.id || null };
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) throw new Error(result.error || "type failed");
  return { tabId, selector, typed: text.length, ...result };
}

/**
 * Shared CSP-safe editor driver for Monaco, CodeMirror, and standard editable elements.
 * Supports mode="get" and mode="set" without duplicating discovery logic.
 */
async function runEditorInTab(cmd, mode) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  await assertTabInWorkspace(tabId, cmd);
  const selector = cmd.selector ? String(cmd.selector) : "";
  const modelIndex = cmd.modelIndex != null ? Math.max(0, Number(cmd.modelIndex) || 0) : null;
  const maxChars = Math.min(200000, Math.max(200, Number(cmd.maxChars) || 12000));
  const listModels = cmd.listModels === true;
  const doFocus = cmd.focus !== false;
  let nextText = "";
  if (mode === "set") {
    if (cmd.text == null) throw new Error("text required");
    nextText = String(cmd.text);
    if (nextText.length > 1000000) throw new Error("text too long (max 1000000)");
  }

  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [mode, selector, modelIndex, maxChars, listModels, nextText, doFocus],
    func: (opMode, sel, mIdx, cap, onlyList, newValue, focusEditor) => {
      try {
        const isVisible = (el) => {
          if (!el || !el.getBoundingClientRect) return false;
          const r = el.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0) return false;
          const cs = getComputedStyle(el);
          return cs.display !== "none" && cs.visibility !== "hidden";
        };

        const safeCall = (fn, thisArg) => {
          try {
            return fn.call(thisArg);
          } catch {
            return undefined;
          }
        };

        const isModelLike = (o) => {
          try {
            return Boolean(
              o &&
                typeof o === "object" &&
                typeof o.getValue === "function" &&
                typeof o.pushEditOperations === "function" &&
                typeof o.getFullModelRange === "function" &&
                (typeof o.isDisposed !== "function" || !o.isDisposed())
            );
          } catch {
            return false;
          }
        };

        const isEditorLike = (o) => {
          try {
            return Boolean(
              o &&
                typeof o === "object" &&
                typeof o.getModel === "function" &&
                (typeof o.setValue === "function" || typeof o.getValue === "function")
            );
          } catch {
            return false;
          }
        };

        const scanMonacoViaDom = () => {
          const foundEditors = [];
          const seenFound = new Set();
          const rootSel = sel || '.monaco-editor, [data-name="pine-dialog"]';
          const roots = Array.from(document.querySelectorAll(rootSel));
          for (const root of roots) {
            const seeds = [];
            let cur = root;
            for (let i = 0; i < 6 && cur; i += 1, cur = cur.parentElement) seeds.push(cur);
            const innerArea = root.querySelector && root.querySelector("textarea.inputarea");
            if (innerArea) seeds.push(innerArea);

            const visited = new WeakSet();
            const queue = [];
            for (const s of seeds) {
              let keys;
              try {
                keys = Object.keys(s);
              } catch {
                continue;
              }
              for (const k of keys) {
                if (
                  k.startsWith("__reactFiber$") ||
                  k.startsWith("__reactProps$") ||
                  k.startsWith("__reactInternalInstance$") ||
                  k.toLowerCase().includes("monaco") ||
                  k.toLowerCase().includes("editor")
                ) {
                  try {
                    const val = s[k];
                    if (val && typeof val === "object") queue.push({ obj: val, depth: 0 });
                  } catch {
                    /* ignore */
                  }
                }
              }
            }

            let steps = 0;
            while (queue.length > 0 && steps < 400) {
              const { obj, depth } = queue.shift();
              steps += 1;
              if (!obj || typeof obj !== "object" || depth > 8) continue;
              if (obj === window || obj === document || obj instanceof Node) continue;
              try {
                if (visited.has(obj)) continue;
                visited.add(obj);
              } catch {
                continue;
              }
              if (isEditorLike(obj)) {
                if (!seenFound.has(obj)) {
                  seenFound.add(obj);
                  foundEditors.push({ editor: obj, dom: root });
                }
                continue;
              }
              if (isModelLike(obj)) {
                if (!seenFound.has(obj)) {
                  seenFound.add(obj);
                  foundEditors.push({ model: obj, dom: root });
                }
                continue;
              }
              let keys;
              try {
                keys = Object.keys(obj);
              } catch {
                continue;
              }
              for (const k of keys) {
                if (k === "parent" || k === "_owner" || k === "stateNode") {
                  try {
                    if (k === "stateNode" && obj[k] instanceof Node) continue;
                  } catch {
                    continue;
                  }
                }
                if (
                  depth < 5 ||
                  k === "current" ||
                  k === "memoizedProps" ||
                  k === "memoizedState" ||
                  k === "return" ||
                  k === "child" ||
                  k === "sibling" ||
                  k === "editor" ||
                  k === "_editor" ||
                  k === "model" ||
                  k === "ref"
                ) {
                  try {
                    const next = obj[k];
                    if (
                      next &&
                      typeof next === "object" &&
                      next !== window &&
                      next !== document &&
                      !(next instanceof Node)
                    ) {
                      queue.push({ obj: next, depth: depth + 1 });
                    }
                  } catch {
                    /* ignore */
                  }
                }
              }
            }
          }
          return foundEditors;
        };

        const targets = [];
        const seenModels = new Set();
        const pushTarget = (entry) => {
          const model = entry.model || (entry.editor && safeCall(entry.editor.getModel, entry.editor));
          if (model && seenModels.has(model)) return;
          if (model) seenModels.add(model);
          targets.push({ ...entry, model: model || null });
        };

        const monaco = window.monaco && window.monaco.editor;
        if (monaco) {
          const editors = typeof monaco.getEditors === "function" ? monaco.getEditors() : [];
          for (const ed of editors) {
            const dom = typeof ed.getDomNode === "function" ? ed.getDomNode() : null;
            if (sel && dom && !(dom.matches(sel) || dom.closest(sel) || dom.querySelector(sel))) {
              continue;
            }
            const model = typeof ed.getModel === "function" ? ed.getModel() : null;
            if (model && typeof model.isDisposed === "function" && model.isDisposed()) continue;
            pushTarget({
              kind: "monaco-editor",
              editor: ed,
              model,
              dom,
              visible: dom ? isVisible(dom) : true,
              focused: typeof ed.hasTextFocus === "function" ? Boolean(safeCall(ed.hasTextFocus, ed)) : false,
            });
          }
          const models = typeof monaco.getModels === "function" ? monaco.getModels() : [];
          for (const m of models) {
            if (typeof m.isDisposed === "function" && m.isDisposed()) continue;
            pushTarget({ kind: "monaco-model", model: m, visible: true, focused: false });
          }
        }

        if (targets.length === 0) {
          for (const found of scanMonacoViaDom()) {
            pushTarget({
              kind: found.editor ? "monaco-react-editor" : "monaco-react-model",
              editor: found.editor || null,
              model: found.model || null,
              dom: found.dom,
              visible: found.dom ? isVisible(found.dom) : true,
              focused: false,
            });
          }
        }

        for (const cmDom of document.querySelectorAll(sel || ".cm-editor")) {
          const view = cmDom.cmView?.view || cmDom.querySelector(".cm-content")?.cmView?.view;
          if (view && view.state && view.state.doc) {
            targets.push({
              kind: "codemirror6",
              cm6: view,
              dom: cmDom,
              visible: isVisible(cmDom),
              focused: Boolean(view.hasFocus),
            });
          }
        }
        for (const cm5Dom of document.querySelectorAll(sel || ".CodeMirror")) {
          if (cm5Dom.CodeMirror && typeof cm5Dom.CodeMirror.getValue === "function") {
            targets.push({
              kind: "codemirror5",
              cm5: cm5Dom.CodeMirror,
              dom: cm5Dom,
              visible: isVisible(cm5Dom),
              focused:
                typeof cm5Dom.CodeMirror.hasFocus === "function"
                  ? Boolean(cm5Dom.CodeMirror.hasFocus())
                  : false,
            });
          }
        }

        const scoreTarget = (t) => {
          const lang =
            t.model && typeof t.model.getLanguageId === "function"
              ? safeCall(t.model.getLanguageId, t.model) || ""
              : "";
          return (
            (t.focused ? 100 : 0) +
            (t.visible ? 40 : 0) +
            (/pine/i.test(String(lang)) ? 30 : 0) +
            (t.editor ? 10 : 0)
          );
        };

        if (opMode === "get") {
          if (sel || targets.length === 0) {
            const areas = Array.from(
              document.querySelectorAll(sel || 'textarea:not(.inputarea), [contenteditable="true"]')
            );
            for (const el of areas) {
              if (!isVisible(el) && !sel) continue;
              targets.push({
                kind: el.tagName === "TEXTAREA" || el.tagName === "INPUT" ? "textarea" : "contenteditable",
                el,
                dom: el,
                visible: isVisible(el),
                focused: document.activeElement === el,
              });
            }
          }

          if (targets.length === 0) {
            const monacoDom = document.querySelector(sel || ".monaco-editor");
            if (monacoDom) {
              const lines = Array.from(monacoDom.querySelectorAll(".view-line")).map((n) => n.innerText || "");
              const raw = lines.join("\n");
              return {
                ok: true,
                kind: "monaco-dom-visible",
                language: null,
                lineCount: lines.length,
                charCount: raw.length,
                truncated: raw.length > cap,
                value: raw.slice(0, cap),
              };
            }
            return { ok: false, error: "no supported code editor found in page" };
          }

          const readEntry = (t) => {
            if (t.model) {
              const val = String(t.model.getValue());
              const lang =
                typeof t.model.getLanguageId === "function"
                  ? safeCall(t.model.getLanguageId, t.model)
                  : typeof t.model.getModeId === "function"
                    ? safeCall(t.model.getModeId, t.model)
                    : null;
              const uri = t.model.uri ? String(t.model.uri.toString ? t.model.uri.toString() : t.model.uri) : null;
              const lc =
                typeof t.model.getLineCount === "function" ? t.model.getLineCount() : val.split("\n").length;
              return { kind: t.kind, language: lang || null, uri, lineCount: lc, value: val };
            }
            if (t.editor && typeof t.editor.getValue === "function") {
              const val = String(safeCall(t.editor.getValue, t.editor) || "");
              return { kind: t.kind, language: null, uri: null, lineCount: val.split("\n").length, value: val };
            }
            if (t.cm6) {
              const val = String(t.cm6.state.doc.toString());
              return { kind: t.kind, language: null, uri: null, lineCount: t.cm6.state.doc.lines, value: val };
            }
            if (t.cm5) {
              const val = String(t.cm5.getValue());
              return { kind: t.kind, language: null, uri: null, lineCount: t.cm5.lineCount(), value: val };
            }
            const val = String(t.el && "value" in t.el ? t.el.value || "" : (t.el && t.el.textContent) || "");
            return { kind: t.kind, language: null, uri: null, lineCount: val.split("\n").length, value: val };
          };

          const enriched = targets.map((t, idx) => {
            const info = readEntry(t);
            return {
              idx,
              t,
              info,
              s: scoreTarget(t) + (info.value.length > 0 ? 10 : 0),
            };
          });

          if (onlyList) {
            return {
              ok: true,
              count: enriched.length,
              models: enriched.map((e) => ({
                index: e.idx,
                kind: e.info.kind,
                language: e.info.language,
                uri: e.info.uri,
                visible: Boolean(e.t.visible),
                focused: Boolean(e.t.focused),
                lineCount: e.info.lineCount,
                charCount: e.info.value.length,
                preview: e.info.value.slice(0, 160),
              })),
            };
          }

          const chosen =
            mIdx !== null
              ? enriched[Math.min(mIdx, enriched.length - 1)]
              : [...enriched].sort((a, b) => b.s - a.s)[0];
          const raw = chosen.info.value;
          return {
            ok: true,
            modelIndex: chosen.idx,
            modelCount: enriched.length,
            kind: chosen.info.kind,
            language: chosen.info.language,
            uri: chosen.info.uri,
            lineCount: chosen.info.lineCount,
            charCount: raw.length,
            truncated: raw.length > cap,
            value: raw.slice(0, cap),
          };
        }

        // opMode === "set"
        if (targets.length > 0) {
          const chosen =
            mIdx !== null
              ? targets[Math.min(mIdx, targets.length - 1)]
              : [...targets].sort((a, b) => scoreTarget(b) - scoreTarget(a))[0];

          if (chosen.model) {
            if (focusEditor && chosen.editor && typeof chosen.editor.focus === "function") {
              chosen.editor.focus();
            }
            if (
              typeof chosen.model.pushEditOperations === "function" &&
              typeof chosen.model.getFullModelRange === "function"
            ) {
              chosen.model.pushEditOperations(
                [],
                [{ range: chosen.model.getFullModelRange(), text: newValue }],
                () => null
              );
            } else if (chosen.editor && typeof chosen.editor.setValue === "function") {
              chosen.editor.setValue(newValue);
            } else {
              chosen.model.setValue(newValue);
            }
            if (chosen.editor && typeof chosen.editor.setPosition === "function") {
              chosen.editor.setPosition({ lineNumber: 1, column: 1 });
            }
            const after = String(chosen.model.getValue());
            const lc =
              typeof chosen.model.getLineCount === "function"
                ? chosen.model.getLineCount()
                : after.split("\n").length;
            return {
              ok: true,
              kind: chosen.kind,
              verified: after === newValue,
              lineCount: lc,
              charCount: after.length,
            };
          }
          if (chosen.editor && typeof chosen.editor.setValue === "function") {
            if (focusEditor && typeof chosen.editor.focus === "function") chosen.editor.focus();
            chosen.editor.setValue(newValue);
            const after =
              typeof chosen.editor.getValue === "function" ? String(chosen.editor.getValue()) : newValue;
            return {
              ok: true,
              kind: chosen.kind,
              verified: after === newValue,
              lineCount: after.split("\n").length,
              charCount: after.length,
            };
          }
          if (chosen.cm6) {
            if (focusEditor && typeof chosen.cm6.focus === "function") chosen.cm6.focus();
            chosen.cm6.dispatch({
              changes: { from: 0, to: chosen.cm6.state.doc.length, insert: newValue },
            });
            const after = String(chosen.cm6.state.doc.toString());
            return {
              ok: true,
              kind: chosen.kind,
              verified: after === newValue,
              lineCount: chosen.cm6.state.doc.lines,
              charCount: after.length,
            };
          }
          if (chosen.cm5) {
            if (focusEditor && typeof chosen.cm5.focus === "function") chosen.cm5.focus();
            chosen.cm5.setValue(newValue);
            const after = String(chosen.cm5.getValue());
            return {
              ok: true,
              kind: chosen.kind,
              verified: after === newValue,
              lineCount: chosen.cm5.lineCount(),
              charCount: after.length,
            };
          }
        }

        // Sealed Monaco textarea.inputarea synthetic paste fallback
        const monacoInput = document.querySelector(
          sel ? `${sel} textarea.inputarea, ${sel}` : ".monaco-editor textarea.inputarea"
        );
        if (monacoInput && monacoInput.tagName === "TEXTAREA") {
          monacoInput.focus();
          monacoInput.dispatchEvent(
            new KeyboardEvent("keydown", { key: "a", code: "KeyA", ctrlKey: true, bubbles: true })
          );
          try {
            document.execCommand("selectAll", false);
          } catch {
            /* ignore */
          }
          try {
            const dt = new DataTransfer();
            dt.setData("text/plain", newValue);
            monacoInput.dispatchEvent(
              new ClipboardEvent("paste", {
                bubbles: true,
                cancelable: true,
                clipboardData: dt,
              })
            );
            return {
              ok: true,
              kind: "monaco-paste-fallback",
              verified: false,
              lineCount: newValue.split("\n").length,
              charCount: newValue.length,
            };
          } catch {
            /* fall through */
          }
        }

        const fallbackEl = document.querySelector(
          sel || 'textarea:not(.inputarea), [contenteditable="true"]'
        );
        if (!fallbackEl) {
          return { ok: false, error: "no supported code editor or editable element found" };
        }
        fallbackEl.focus();
        if ("value" in fallbackEl) {
          const proto = Object.getPrototypeOf(fallbackEl);
          const setter = proto && Object.getOwnPropertyDescriptor(proto, "value")?.set;
          if (setter) setter.call(fallbackEl, newValue);
          else fallbackEl.value = newValue;
          fallbackEl.dispatchEvent(new Event("input", { bubbles: true }));
          fallbackEl.dispatchEvent(new Event("change", { bubbles: true }));
        } else if (fallbackEl.isContentEditable) {
          fallbackEl.textContent = newValue;
          fallbackEl.dispatchEvent(new Event("input", { bubbles: true }));
        }
        return {
          ok: true,
          kind: fallbackEl.tagName === "TEXTAREA" ? "textarea" : "contenteditable",
          verified: true,
          lineCount: newValue.split("\n").length,
          charCount: newValue.length,
        };
      } catch (err) {
        return {
          ok: false,
          error: `editor_${opMode} exception: ${String(
            err && (err.message || err.stack) ? err.message || err.stack : err
          ).slice(0, 300)}`,
        };
      }
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) throw new Error(result.error || `editor_${mode} failed`);
  return { tabId, ...result };
}

/** Read code/text from Monaco, CodeMirror, or standard editable elements (CSP-safe). */
async function editorGetInTab(cmd) {
  return await runEditorInTab(cmd, "get");
}

/** Replace code/text in Monaco, CodeMirror, or standard editable elements (CSP-safe). */
async function editorSetInTab(cmd) {
  return await runEditorInTab(cmd, "set");
}

/**
 * Wait for a tab to reach status=complete (or timeout). Never focuses the tab.
 * Resolves {status,title,timedOut}.
 */
function waitForTabComplete(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    let settled = false;
    const onUpdated = (updatedId, changeInfo) => {
      if (updatedId !== tabId || changeInfo.status !== "complete") return;
      // Small settle so load handlers / late DOM work finish before extraction.
      setTimeout(finish, 250);
    };
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        chrome.tabs.onUpdated.removeListener(onUpdated);
      } catch {
        /* ignore */
      }
      chrome.tabs
        .get(tabId)
        .then((tab) =>
          resolve({ status: tab.status || "unknown", title: tab.title || "", timedOut: true })
        )
        .catch(() => resolve({ status: "gone", title: "", timedOut: true }));
    }, Math.max(1000, timeoutMs));
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        chrome.tabs.onUpdated.removeListener(onUpdated);
      } catch {
        /* ignore */
      }
      chrome.tabs
        .get(tabId)
        .then((tab) =>
          resolve({ status: tab.status || "unknown", title: tab.title || "", timedOut: false })
        )
        .catch(() => resolve({ status: "gone", title: "", timedOut: false }));
    };
    chrome.tabs.onUpdated.addListener(onUpdated);
  });
}

/**
 * Composite read: navigate (inactive) → wait for load → extract
 * {title,url,text excerpt}. One bridge command instead of open+eval loops.
 */
async function readPage(cmd) {
  let tabId;
  if (cmd.tabId != null && Number.isFinite(Number(cmd.tabId))) {
    tabId = Number(cmd.tabId);
    await assertTabInWorkspace(tabId, cmd);
    if (cmd.url) {
      await chrome.tabs.update(tabId, { url: assertHttpUrl(cmd.url), active: false });
    }
  } else {
    if (!cmd.url) throw new Error("url or tabId required");
    const nav = await navigateTab({
      url: cmd.url,
      active: false,
      separateWindow: cmd.separateWindow === true,
      width: cmd.width,
      height: cmd.height,
    });
    tabId = nav.tabId;
  }
  const waitMs = Math.min(60000, Math.max(1000, Number(cmd.waitMs) || 15000));
  const waited = await waitForTabComplete(tabId, waitMs);
  const textMax = Math.min(20000, Math.max(200, Number(cmd.textMax) || 4000));
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [textMax],
    func: (cap) => {
      const main =
        document.querySelector("main") ||
        document.querySelector("article") ||
        document.querySelector('[role="main"]') ||
        document.body;
      const raw = String((main && (main.innerText || main.textContent)) || "");
      const text = raw.replace(/\s+/g, " ").trim().slice(0, cap);
      return {
        ok: true,
        title: document.title || "",
        url: location.href,
        text,
        textLength: raw.length,
      };
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) throw new Error(result.error || "read failed");
  return { ok: true, tabId, ...waited, ...result };
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
      return await ensureGroup(cmd.url || null, {
        separateWindow: cmd.separateWindow === true,
        width: cmd.width,
        height: cmd.height,
      });
    case "open":
      if (!cmd.url) throw new Error("url required");
      return await openInGroup(cmd.url, {
        newTab: cmd.newTab === true,
        separateWindow: cmd.separateWindow === true,
        width: cmd.width,
        height: cmd.height,
      });
    case "new_tab":
    case "new-tab":
      if (!cmd.url) throw new Error("url required");
      return await openInGroup(cmd.url, {
        newTab: true,
        separateWindow: cmd.separateWindow === true,
        width: cmd.width,
        height: cmd.height,
      });
    case "navigate":
      return await navigateTab(cmd);
    case "read_page":
    case "read-page":
      return await readPage(cmd);
    case "list_tabs":
    case "list-tabs":
      return await listTabs(cmd);
    case "eval":
    case "evaluate":
      return await evalInTab(cmd);
    case "click":
      return await clickInTab(cmd);
    case "type":
      return await typeInTab(cmd);
    case "dom_query":
    case "dom-query":
      return await domQueryInTab(cmd);
    case "editor_get":
    case "editor-get":
      return await editorGetInTab(cmd);
    case "editor_set":
    case "editor-set":
      return await editorSetInTab(cmd);
    case "fetch_url":
    case "fetch-url":
    case "save_url":
    case "save-url":
      return await fetchUrl(cmd);
    case "page_assets":
    case "page-assets":
    case "list_assets":
    case "list-assets":
      return await listPageAssets(cmd);
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
  const headers = await authHeaders({ "Content-Type": "application/json" });
  const res = await fetch(`${bridge}/v1/result`, {
    method: "POST",
    headers,
    body: JSON.stringify({ id, ...payload }),
  });
  if (!res.ok) {
    let msg = `postResult HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.error) msg = String(body.error);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
}

function extensionIdentityQuery() {
  const version = chrome.runtime.getManifest().version;
  return `version=${encodeURIComponent(version)}&extension=${encodeURIComponent(
    "hermes-chrome"
  )}`;
}

async function postHello(bridge) {
  try {
    const headers = await authHeaders({ "Content-Type": "application/json" });
    await fetch(`${bridge}/v1/hello`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        extension: "hermes-chrome",
        version: chrome.runtime.getManifest().version,
      }),
      cache: "no-store",
    });
  } catch {
    /* bridge down */
  }
}

async function pairWithBridge() {
  const settings = await getSettings();
  const bridge = settings.bridgeUrl || DEFAULT_BRIDGE;
  let res = null;
  let body = {};

  try {
    res = await fetch(`${bridge}/v1/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      cache: "no-store",
    });
    body = await res.json().catch(() => ({}));
  } catch {
    res = null;
  }

  // If pairing closed or bridge down, ask native host to re-open pairing / start bridge and retry
  if (!res || !res.ok || !body.ok || !body.token) {
    const nativeRes = await sendNativeMessageAsync({ action: "pair_open" });
    if (nativeRes && (nativeRes.ok || nativeRes.action === "pair_open")) {
      try {
        res = await fetch(`${bridge}/v1/pair`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          cache: "no-store",
        });
        body = await res.json().catch(() => ({}));
      } catch {
        res = null;
      }
    }
  }

  if (!res || !res.ok || !body.ok || !body.token) {
    throw new Error(body.error || (res ? `pair failed HTTP ${res.status}` : "bridge offline"));
  }
  const next = {
    ...settings,
    bridgeToken: String(body.token),
    bridgeUrl: (body.bridge || bridge).replace(/\/$/, ""),
  };
  await chrome.storage.local.set({ [SETTINGS_KEY]: next });
  autoPairFailStreak = 0;
  lastAuthErrorAt = 0;
  startPolling();
  return {
    ok: true,
    tokenSet: true,
    bridgeUrl: next.bridgeUrl,
    hint: "Token saved. Polling restarted.",
    auto: !!body.auto,
  };
}

/**
 * Auto-pair when token is missing (reload / first install).
 * Bridge keeps pairing_open while extension is disconnected.
 */
async function tryAutoPair(reason = "poll") {
  const settings = await getSettings();
  if (settings.bridgeToken) return true;
  if (!settings.pollingEnabled) return false;

  const now = Date.now();
  // Back off after many failures (still retry slowly).
  const minInterval =
    autoPairFailStreak >= AUTO_PAIR_MAX_STREAK
      ? 15000
      : AUTO_PAIR_MIN_INTERVAL_MS;
  if (now - lastAutoPairAt < minInterval) return false;
  lastAutoPairAt = now;

  const bridge = settings.bridgeUrl || DEFAULT_BRIDGE;
  try {
    // Touch health so bridge can auto-reopen pairing when disconnected.
    const hr = await fetch(`${bridge}/v1/health`, { cache: "no-store" });
    if (hr.ok) {
      const health = await hr.json().catch(() => ({}));
      if (health.auth === false) {
        // No token required on bridge.
        return true;
      }
      if (health.pairing_open === false) {
        autoPairFailStreak += 1;
        return false;
      }
    }
    await pairWithBridge();
    await noteActivity("auto_pair", { reason });
    return true;
  } catch {
    autoPairFailStreak += 1;
    return false;
  }
}

const ACTION_HISTORY_KEY = "hermes_action_history";
const MAX_ACTION_HISTORY = 80;

function getActionStorage() {
  return chrome.storage.session || chrome.storage.local;
}

function sanitizeResultForLog(action, data) {
  if (!data) return data;
  if (action === "capture" && typeof data === "object") {
    const img = data.image || data.dataUrl || data.data;
    if (img && typeof img === "string") {
      const preview = img.startsWith("data:") ? img : `data:image/png;base64,${img}`;
      return {
        ...data,
        imagePreview: preview,
      };
    }
  }
  return data;
}

function sanitizeParamsForLog(action, params) {
  if (!params || typeof params !== "object") return params;
  const copy = { ...params };
  delete copy.token;
  delete copy["X-Hermes-Chrome-Token"];
  if (action === "type" || action === "fill") {
    const selector = String(copy.selector || "").toLowerCase();
    if (copy.sensitive || selector.includes("password") || selector.includes("secret")) {
      copy.text = "••••••••";
    }
  }
  return copy;
}

async function recordActionStart(cmd) {
  const startTime = Date.now();
  const entry = {
    id: cmd.id,
    action: cmd.action,
    params: sanitizeParamsForLog(cmd.action, cmd),
    startTime,
    status: "running",
  };
  chrome.runtime.sendMessage({
    type: "hermes_action_event",
    phase: "start",
    entry,
  }).catch(() => {});

  try {
    const area = getActionStorage();
    const res = await area.get([ACTION_HISTORY_KEY]);
    const history = Array.isArray(res[ACTION_HISTORY_KEY]) ? res[ACTION_HISTORY_KEY] : [];
    history.unshift(entry);
    if (history.length > MAX_ACTION_HISTORY) {
      history.length = MAX_ACTION_HISTORY;
    }
    await area.set({ [ACTION_HISTORY_KEY]: history });
  } catch {
    /* ignore storage error */
  }
  return startTime;
}

async function recordActionEnd(cmd, ok, data, error, startTime) {
  const endTime = Date.now();
  const durationMs = Math.max(0, endTime - startTime);
  const update = {
    id: cmd.id,
    action: cmd.action,
    status: ok ? "success" : "error",
    endTime,
    durationMs,
    data: data ? sanitizeResultForLog(cmd.action, data) : undefined,
    error: error ? String(error) : undefined,
  };

  chrome.runtime.sendMessage({
    type: "hermes_action_event",
    phase: "end",
    update,
  }).catch(() => {});

  try {
    const area = getActionStorage();
    const res = await area.get([ACTION_HISTORY_KEY]);
    const history = Array.isArray(res[ACTION_HISTORY_KEY]) ? res[ACTION_HISTORY_KEY] : [];
    const idx = history.findIndex((item) => item.id === cmd.id);
    if (idx !== -1) {
      history[idx] = { ...history[idx], ...update };
      await area.set({ [ACTION_HISTORY_KEY]: history });
    }
  } catch {
    /* ignore */
  }
}

async function pollOnce(bridge) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 28000);
  try {
    // Ensure we have a token before long-poll when possible.
    const settings = await getSettings();
    if (!settings.bridgeToken) {
      await tryAutoPair("before_poll");
    }
    const headers = await authHeaders();
    const res = await fetch(
      `${bridge}/v1/poll?timeout=25&${extensionIdentityQuery()}`,
      {
        signal: ctrl.signal,
        cache: "no-store",
        headers,
      }
    );
    if (res.status === 401) {
      lastAuthErrorAt = Date.now();
      // Stale/wrong token or never paired — clear empty isn't enough; try pair.
      const s = await getSettings();
      if (!s.bridgeToken) {
        await tryAutoPair("401");
      } else {
        // Token rejected: clear and re-pair (bridge rotated token).
        await chrome.storage.local.set({
          [SETTINGS_KEY]: { ...s, bridgeToken: "" },
        });
        await tryAutoPair("401_stale");
      }
      return;
    }
    if (res.status === 204 || res.status === 404 || !res.ok) return;
    const cmd = await res.json();
    if (!cmd || !cmd.id) return;
    const startTime = await recordActionStart(cmd);
    try {
      const data = await handleCommand(cmd);
      await postResult(bridge, cmd.id, { ok: true, data });
      await recordActionEnd(cmd, true, data, null, startTime);
    } catch (e) {
      const errStr = String(e?.message || e);
      await postResult(bridge, cmd.id, {
        ok: false,
        error: errStr,
      });
      await recordActionEnd(cmd, false, null, errStr, startTime);
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
    if (!settings.bridgeToken) {
      const ok = await tryAutoPair("loop");
      if (!ok) {
        // Wait a bit before next auto-pair attempt (avoid tight loop).
        await new Promise((r) => setTimeout(r, AUTO_PAIR_MIN_INTERVAL_MS));
        continue;
      }
    }
    await pollOnce(settings.bridgeUrl);
    await new Promise((r) => setTimeout(r, 200));
  }
}

function startPolling() {
  pollGeneration += 1;
  const generation = pollGeneration;
  polling = true;
  autoPairFailStreak = 0;
  // Native host (if installed) starts the bridge; then auto-pair + hello.
  ensureNativeCompanion(true);
  (async () => {
    try {
      // Give host a moment to spawn bridge.py
      await new Promise((r) => setTimeout(r, 400));
      await tryAutoPair("start");
      const b = await getBridgeBase();
      await postHello(b);
    } catch {
      /* bridge down */
    }
  })();
  pollLoop(generation);
  // Keep service worker eligible for wake-ups (MV3).
  try {
    chrome.alarms.create("hermes-chrome-poll", { periodInMinutes: 1 });
  } catch {
    /* ignore */
  }
  try {
    chrome.alarms.create("hermes-chrome-native", { periodInMinutes: 2 });
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
  if (alarm.name === "hermes-chrome-native") ensureNativeCompanion(false);
});
startPolling();

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[SETTINGS_KEY]) startPolling();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "status") {
    status().then((s) => {
      s.authErrorRecent =
        lastAuthErrorAt > 0 && Date.now() - lastAuthErrorAt < 60000;
      sendResponse(s);
    });
    return true;
  }
  if (msg?.type === "reconnect") {
    ensureNativeCompanion(true);
    startPolling();
    sendResponse({ ok: true });
    return false;
  }
  if (msg?.type === "ensure_native") {
    const st = ensureNativeCompanion(true);
    sendResponse({ ok: true, nativeHost: st });
    return false;
  }
  if (msg?.type === "pair") {
    pairWithBridge()
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));
    return true;
  }
  if (msg?.type === "getMcpConfig") {
    const rootHint = "~/.hermes/hermes-chrome";
    const mcpConfig = {
      mcpServers: {
        "hermes-chrome": {
          command: "python3",
          args: [`${rootHint}/mcp_server.py`],
          env: {
            HERMES_CHROME_ROOT: rootHint,
          },
        },
      },
    };
    sendResponse({
      ok: true,
      json: JSON.stringify(mcpConfig, null, 2),
      cursorJson: JSON.stringify(mcpConfig, null, 2),
      claudeJson: JSON.stringify(mcpConfig, null, 2),
      grokToml: `[mcp_servers.hermes-chrome]\ncommand = "python3"\nargs = ["${rootHint}/mcp_server.py"]\nenabled = true\nstartup_timeout_sec = 45\ntool_timeout_sec = 120\n\n[mcp_servers.hermes-chrome.env]\nHERMES_CHROME_ROOT = "${rootHint}"`,
    });
    return false;
  }
  // Close agent workspace Tab Group (tabs by default). Does not stop the local bridge.
  if (msg?.type === "stop" || msg?.type === "stopWorkspace") {
    stopGroup({ closeTabs: msg?.closeTabs !== false })
      .then((r) => sendResponse({ ok: true, ...r }))
      .catch((e) => sendResponse({ ok: false, error: String(e?.message || e) }));
    return true;
  }
  if (msg?.type === "getSettings") {
    getSettings().then((s) => {
      // Never echo full token to popup JSON dumps — mask for UI.
      sendResponse({
        ...s,
        bridgeToken: s.bridgeToken || "",
        tokenSet: !!(s.bridgeToken && s.bridgeToken.length),
      });
    });
    return true;
  }
  if (msg?.type === "setSettings") {
    const incoming = msg.settings || {};
    getSettings().then((cur) => {
      const merged = {
        bridgeUrl: incoming.bridgeUrl ?? cur.bridgeUrl,
        bridgeToken:
          incoming.bridgeToken !== undefined
            ? String(incoming.bridgeToken || "")
            : cur.bridgeToken,
        groupTitle: incoming.groupTitle ?? cur.groupTitle,
        groupColor: incoming.groupColor ?? cur.groupColor,
        pollingEnabled:
          incoming.pollingEnabled !== undefined
            ? !!incoming.pollingEnabled
            : cur.pollingEnabled,
        allowCrossWorkspace:
          incoming.allowCrossWorkspace !== undefined
            ? !!incoming.allowCrossWorkspace
            : cur.allowCrossWorkspace,
        allowPrivateFetch:
          incoming.allowPrivateFetch !== undefined
            ? !!incoming.allowPrivateFetch
            : cur.allowPrivateFetch,
      };
      return chrome.storage.local.set({ [SETTINGS_KEY]: merged }).then(() => {
        startPolling();
        sendResponse({ ok: true, tokenSet: !!merged.bridgeToken });
      });
    });
    return true;
  }
  if (msg?.type === "get_action_history") {
    (async () => {
      try {
        const area = getActionStorage();
        const res = await area.get([ACTION_HISTORY_KEY]);
        sendResponse({ ok: true, history: res[ACTION_HISTORY_KEY] || [] });
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }
  if (msg?.type === "clear_action_history") {
    (async () => {
      try {
        const area = getActionStorage();
        await area.remove([ACTION_HISTORY_KEY]);
        chrome.runtime.sendMessage({ type: "hermes_action_history_cleared" }).catch(() => {});
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }
  if (msg?.type === "open_side_panel") {
    (async () => {
      try {
        if (chrome.sidePanel?.open) {
          let winId = msg.windowId;
          if (!winId) {
            const currentWin = await chrome.windows.getCurrent();
            winId = currentWin.id;
          }
          await chrome.sidePanel.open({ windowId: winId });
          sendResponse({ ok: true });
        } else {
          sendResponse({ ok: false, error: "sidePanel API not available" });
        }
      } catch (e) {
        sendResponse({ ok: false, error: String(e?.message || e) });
      }
    })();
    return true;
  }
  return false;
});
