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
const LAST_ACTIVITY_KEY = "lastActivity";
let polling = false;
let pollGeneration = 0;

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
  const lastActivity = await getLastActivity();
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
      lastActivity,
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
    lastActivity,
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
 * Capture a tab viewport as PNG (any http/https page the user/CLI asks for).
 * Optional prefer=gc|nq is only a *finder hint* for gold workflows — not a product limit.
 * Optional ensureTf: { preferred: "5", allowed: ["3","5"] } forces 3m/5m before shot.
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
    tab = await findTradingViewTab(prefer);
    if (!tab && prefer === "auto") {
      const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
      tab = active;
    }
  } else {
    tab = await findTradingViewTab(prefer);
  }
  if (!tab) throw new Error("no matching tab to capture");

  // Activate so TradingView resumes live feed on background tabs.
  await chrome.tabs.update(tab.id, { active: true });
  if (tab.windowId != null) {
    try {
      await chrome.windows.update(tab.windowId, { focused: false });
    } catch {
      /* avoid focus steal when possible */
    }
  }

  // Ensure 3m/5m when requested (gold pipeline).
  let tfInfo = null;
  const ensureTf = cmd.ensureTf || cmd.ensureTimeframe || null;
  if (ensureTf) {
    tfInfo = await ensureTradingViewTimeframe(tab.id, ensureTf);
    // Extra paint time after TF switch (new series load).
    const tfWait = Number(cmd.tfSettleMs);
    await new Promise((r) =>
      setTimeout(r, Number.isFinite(tfWait) && tfWait >= 0 ? tfWait : 1200)
    );
  }

  // Nudge chart page to process ticks / paint the latest closed bar.
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

  // Default settle longer than generic UI capture: TV needs time after tab wake.
  const settle = Number(cmd.settleMs);
  const settleMs = Number.isFinite(settle) && settle >= 0 ? settle : 2000;
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
  const groupOnly = Boolean(cmd.groupOnly || cmd.workspaceOnly);
  const urlIncludes = cmd.urlIncludes
    ? String(cmd.urlIncludes).toLowerCase()
    : null;
  const titleIncludes = cmd.titleIncludes
    ? String(cmd.titleIncludes).toLowerCase()
    : null;
  const limit = Math.max(1, Math.min(Number(cmd.limit) || 200, 500));

  let groupId = await getStoredGroupId();
  if (groupOnly) {
    if (!(await groupStillExists(groupId))) {
      return { tabs: [], groupId: null, count: 0 };
    }
  } else if (!(await groupStillExists(groupId))) {
    groupId = null;
  }

  const tabs = await chrome.tabs.query({});
  const out = [];
  for (const t of tabs) {
    if (groupOnly && t.groupId !== groupId) continue;
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
      groupOnly,
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
  const url = cmd.url;
  if (!url) throw new Error("url required");
  const activate = cmd.active === true;
  let tabId = cmd.tabId != null ? Number(cmd.tabId) : null;

  if (tabId != null && Number.isFinite(tabId)) {
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

  const { groupId } = await ensureGroup(null);
  const tabs = await tabsInGroup(groupId);
  if (tabs.length > 0) {
    const updated = await chrome.tabs.update(tabs[0].id, {
      url,
      active: activate,
    });
    return {
      groupId,
      tabId: updated.id,
      windowId: updated.windowId,
      url: updated.pendingUrl || updated.url || url,
      mode: "navigate",
      active: !!updated.active,
    };
  }
  const tab = await chrome.tabs.create({ url, active: activate });
  await chrome.tabs.group({ tabIds: [tab.id], groupId });
  return {
    groupId,
    tabId: tab.id,
    windowId: tab.windowId,
    url: tab.pendingUrl || tab.url || url,
    mode: "new_tab",
    active: !!tab.active,
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
  const expression = cmd.expression || cmd.code;
  if (!expression || typeof expression !== "string") {
    throw new Error("expression required");
  }
  if (expression.length > 8000) throw new Error("expression too long (max 8000)");

  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
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
    throw new Error(result.error || "eval failed");
  }
  return {
    tabId,
    value: jsonSafe(result.value),
  };
}

/** Click element by CSS selector (active:false — no window focus). */
async function clickInTab(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  const selector = cmd.selector;
  if (!selector || typeof selector !== "string") {
    throw new Error("selector required");
  }
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [selector],
    func: (sel) => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, error: `no element matching ${sel}` };
      el.click();
      return {
        ok: true,
        tag: el.tagName,
        id: el.id || null,
        text: (el.innerText || el.textContent || "").slice(0, 120),
      };
    },
  });
  const result = (res && res.result) || { ok: false, error: "no_result" };
  if (!result.ok) throw new Error(result.error || "click failed");
  return { tabId, selector, ...result };
}

/**
 * Fetch a URL using the browser cookie jar (credentials: include).
 * Returns base64 body + response meta. Size-capped for bridge safety.
 */
async function fetchUrl(cmd) {
  const url = cmd.url;
  if (!url || typeof url !== "string") throw new Error("url required");
  if (!/^https?:\/\//i.test(url)) {
    throw new Error("only http(s) URLs allowed for fetch_url");
  }
  const maxBytes = Math.max(
    1024,
    Math.min(Number(cmd.maxBytes) || 50 * 1024 * 1024, 80 * 1024 * 1024)
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

/** Type into element by CSS selector (optional clear first). */
async function typeInTab(cmd) {
  const tabId = Number(cmd.tabId);
  if (!Number.isFinite(tabId)) throw new Error("tabId required");
  const selector = cmd.selector;
  const text = cmd.text != null ? String(cmd.text) : "";
  if (!selector || typeof selector !== "string") {
    throw new Error("selector required");
  }
  if (text.length > 20000) throw new Error("text too long (max 20000)");
  const clear = cmd.clear !== false;
  const [res] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [selector, text, clear],
    func: (sel, value, doClear) => {
      const el = document.querySelector(sel);
      if (!el) return { ok: false, error: `no element matching ${sel}` };
      el.focus();
      if (doClear && "value" in el) el.value = "";
      if ("value" in el) {
        el.value = doClear ? value : String(el.value || "") + value;
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
    case "navigate":
      return await navigateTab(cmd);
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

function extensionIdentityQuery() {
  const version = chrome.runtime.getManifest().version;
  return `version=${encodeURIComponent(version)}&extension=${encodeURIComponent(
    "hermes-chrome"
  )}`;
}

async function postHello(bridge) {
  try {
    await fetch(`${bridge}/v1/hello`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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

async function pollOnce(bridge) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 28000);
  try {
    const res = await fetch(
      `${bridge}/v1/poll?timeout=25&${extensionIdentityQuery()}`,
      {
        signal: ctrl.signal,
        cache: "no-store",
      }
    );
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
  // Best-effort identity push so /v1/health shows extension_connected soon.
  getBridgeBase().then((b) => postHello(b)).catch(() => {});
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
