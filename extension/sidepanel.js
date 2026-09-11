/**
 * Hermes Chrome — Side Panel Client
 *
 * Provides:
 *   1. Action Inspector: Live timeline of CLI/MCP commands with duration & screenshot preview.
 *   2. Hermes Agent Chat: Streaming reasoning, thoughts, and messages via Gateway WebSocket.
 *   3. Workspace Manager: Inspect and control tabs in the Hermes Tab Group.
 */

// Configuration & State
const DEFAULT_GATEWAY_PORTS = [9119, 8642];
let activeGatewayPort = 9119;
let gatewaySocket = null;
let currentSessionId = null;
let gatewayConnecting = false;
let actionCount = 0;
const actionCardsMap = new Map(); // id -> cardElement

// DOM Elements
const el = {
  statusBridge: document.getElementById("status-bridge"),
  statusAgent: document.getElementById("status-agent"),
  navTabs: document.querySelectorAll(".nav-tab"),
  tabPanes: document.querySelectorAll(".tab-pane"),
  actionsBadge: document.getElementById("actions-badge"),
  actionsFeed: document.getElementById("actions-feed"),
  actionsEmpty: document.getElementById("actions-empty"),
  btnClearActions: document.getElementById("btn-clear-actions"),
  chatSessionSelect: document.getElementById("chat-session-select"),
  btnRefreshSessions: document.getElementById("btn-refresh-sessions"),
  currentSessionId: document.getElementById("current-session-id"),
  btnNewSession: document.getElementById("btn-new-session"),
  chatStream: document.getElementById("chat-stream"),
  btnScrollBottom: document.getElementById("btn-scroll-bottom"),
  chatEmpty: document.getElementById("chat-empty"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  btnSendChat: document.getElementById("btn-send-chat"),
  composerModelPill: document.getElementById("composer-model-pill"),
  modelPillName: document.getElementById("model-pill-name"),
  modelPillEffort: document.getElementById("model-pill-effort"),
  modelEffortPopover: document.getElementById("model-effort-popover"),
  btnCloseModelPopover: document.getElementById("btn-close-model-popover"),
  popoverCurrentEffortDesc: document.getElementById("popover-current-effort-desc"),
  effortSegmentedBar: document.getElementById("effort-segmented-bar"),
  popoverActiveProvider: document.getElementById("popover-active-provider"),
  modelSearchInput: document.getElementById("model-search-input"),
  modelPickerList: document.getElementById("model-picker-list"),
  customModelInput: document.getElementById("custom-model-input"),
  btnApplyCustomModel: document.getElementById("btn-apply-custom-model"),
  btnInjectContext: document.getElementById("btn-inject-context"),
  btnQuickSummarize: document.getElementById("btn-quick-summarize"),
  btnQuickCapture: document.getElementById("btn-quick-capture"),
  workspaceTabsList: document.getElementById("workspace-tabs-list"),
  workspaceBadge: document.getElementById("workspace-badge"),
  wsNewUrl: document.getElementById("ws-new-url"),
  btnWsOpen: document.getElementById("btn-ws-open"),
  btnRefreshWorkspace: document.getElementById("btn-refresh-workspace"),
  lightboxModal: document.getElementById("lightbox-modal"),
  lightboxClose: document.getElementById("lightbox-close"),
  lightboxImage: document.getElementById("lightbox-image"),
  lightboxCaption: document.getElementById("lightbox-caption"),
  btnSidepanelOptions: document.getElementById("btn-sidepanel-options"),
  bridgeWarningBanner: document.getElementById("bridge-warning-banner"),
  bannerText: document.getElementById("banner-text"),
  btnBannerPair: document.getElementById("btn-banner-pair"),
  btnBannerGuide: document.getElementById("btn-banner-guide"),
};

// Global Model & Reasoning State
const EFFORT_LABELS = {
  none: "Off",
  minimal: "Min",
  low: "Low",
  medium: "Med",
  high: "High",
  xhigh: "Max",
};

const EFFORT_DESCRIPTIONS = {
  none: "Off (no thinking)",
  minimal: "Minimal thinking",
  low: "Low effort",
  medium: "Medium effort",
  high: "High effort",
  xhigh: "Max effort",
};

let activeModel = "gemini-3.8-flash";
let activeProvider = "gemini";
let activeEffort = "high";
let hermesModelsData = null;
let isPromptRunning = false;

// Utilities
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatTime(timestamp) {
  const d = new Date(timestamp);
  return d.toTimeString().split(" ")[0];
}

// Smart Auto-Scroll State
let userIsScrollingUp = false;

function scrollChatToBottom(force = false) {
  if (!el.chatStream) return;
  if (force || !userIsScrollingUp) {
    el.chatStream.scrollTop = el.chatStream.scrollHeight;
  }
}

// Live Thinking Timer
let thinkingTimerInterval = null;
let promptStartTime = 0;

function startThinkingTimer(timerEl) {
  if (thinkingTimerInterval) clearInterval(thinkingTimerInterval);
  promptStartTime = Date.now();
  if (timerEl) timerEl.textContent = "0.0s";
  thinkingTimerInterval = setInterval(() => {
    if (!timerEl) return;
    const elapsed = ((Date.now() - promptStartTime) / 1000).toFixed(1);
    timerEl.textContent = `${elapsed}s`;
  }, 100);
}

function stopThinkingTimer() {
  if (thinkingTimerInterval) {
    clearInterval(thinkingTimerInterval);
    thinkingTimerInterval = null;
  }
  return promptStartTime > 0 ? ((Date.now() - promptStartTime) / 1000).toFixed(1) : "0.0";
}

// --------------------------------------------------------------------------
// Navigation
// --------------------------------------------------------------------------
function setupNavigation() {
  el.navTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const targetTab = tab.getAttribute("data-tab");
      el.navTabs.forEach((t) => t.classList.remove("active"));
      el.tabPanes.forEach((p) => p.classList.remove("active"));

      tab.classList.add("active");
      const targetPane = document.getElementById(`pane-${targetTab}`);
      if (targetPane) targetPane.classList.add("active");

      if (targetTab === "workspace") {
        refreshWorkspaceTabs();
      } else if (targetTab === "chat") {
        fetchHermesSessions().then(() => {
          loadHermesSession(el.chatSessionSelect ? el.chatSessionSelect.value : "__auto__");
        });
      }
    });
  });
}

// --------------------------------------------------------------------------
// Actions Timeline (CLI Operations Inspector)
// --------------------------------------------------------------------------
function createActionCard(entry) {
  const card = document.createElement("div");
  card.className = "action-card";
  card.id = `action-${entry.id}`;

  const actionName = (entry.action || "command").toLowerCase();
  const timeStr = formatTime(entry.startTime || Date.now());

  let targetDesc = "";
  if (entry.params?.selector) {
    targetDesc = `<span class="action-selector">${escapeHtml(entry.params.selector)}</span>`;
  } else if (entry.params?.url) {
    targetDesc = `<span class="action-url">${escapeHtml(entry.params.url)}</span>`;
  } else if (entry.params?.expr) {
    targetDesc = `<span class="action-selector">${escapeHtml(entry.params.expr)}</span>`;
  } else if (entry.params?.tabId) {
    targetDesc = `<span class="action-selector">Tab #${entry.params.tabId}</span>`;
  }

  const isRunning = entry.status === "running";
  const statusHtml = isRunning
    ? `<span class="status-tag running">● running</span>`
    : entry.status === "success"
    ? `<span class="status-tag success">✓ ${entry.durationMs || 0}ms</span>`
    : `<span class="status-tag error">✕ failed</span>`;

  let detailsHtml = "";
  if (entry.params || entry.data || entry.error) {
    const rawDetails = {
      params: entry.params,
      result: entry.data,
      error: entry.error,
    };
    detailsHtml = `
      <details class="action-details-toggle">
        <summary>Details</summary>
        <pre class="action-details-content">${escapeHtml(JSON.stringify(rawDetails, null, 2))}</pre>
      </details>
    `;
  }

  card.innerHTML = `
    <div class="action-card-header">
      <span class="action-badge ${escapeHtml(actionName)}">${escapeHtml(actionName)}</span>
      <div class="action-card-meta">
        <span class="action-time">${timeStr}</span>
        <span class="action-status-container">${statusHtml}</span>
      </div>
    </div>
    <div class="action-card-body">
      ${targetDesc || '<span class="text-muted">No target specified</span>'}
    </div>
    <div class="action-media-container"></div>
    ${detailsHtml}
  `;

  // Attach screenshot preview if available
  if (entry.data?.imagePreview) {
    attachThumbnail(card, entry.data.imagePreview, `Capture: ${entry.params?.url || "Tab"}`);
  }

  return card;
}

function attachThumbnail(card, src, caption) {
  const container = card.querySelector(".action-media-container");
  if (!container || container.hasChildNodes()) return;

  // SECURITY: Only allow data:image/ or blob: to prevent script or external leak
  if (typeof src !== "string" || (!src.startsWith("data:image/") && !src.startsWith("blob:"))) {
    return;
  }

  const thumb = document.createElement("div");
  thumb.className = "action-thumbnail";
  const img = document.createElement("img");
  img.src = src;
  img.alt = "Screenshot Thumbnail";
  thumb.appendChild(img);

  thumb.addEventListener("click", () => {
    openLightbox(src, caption);
  });
  container.appendChild(thumb);
}

function updateActionCard(update) {
  const card = actionCardsMap.get(update.id);
  if (!card) return;

  const statusContainer = card.querySelector(".action-status-container");
  if (statusContainer) {
    if (update.status === "success") {
      statusContainer.innerHTML = `<span class="status-tag success">✓ ${update.durationMs || 0}ms</span>`;
    } else {
      statusContainer.innerHTML = `<span class="status-tag error" title="${escapeHtml(update.error || "")}">✕ failed</span>`;
    }
  }

  if (update.data?.imagePreview) {
    attachThumbnail(card, update.data.imagePreview, `Screenshot (${update.durationMs || 0}ms)`);
  }

  // Update details accordion if present
  const detailsContent = card.querySelector(".action-details-content");
  if (detailsContent) {
    try {
      const existing = JSON.parse(detailsContent.textContent);
      existing.result = update.data;
      existing.error = update.error;
      detailsContent.textContent = JSON.stringify(existing, null, 2);
    } catch {
      /* ignore parse error */
    }
  }
}

function handleLiveActionEvent(msg) {
  if (el.actionsEmpty) {
    el.actionsEmpty.style.display = "none";
  }

  if (msg.phase === "start" && msg.entry) {
    const card = createActionCard(msg.entry);
    actionCardsMap.set(msg.entry.id, card);
    el.actionsFeed.insertBefore(card, el.actionsFeed.firstChild);
    actionCount += 1;
    el.actionsBadge.textContent = actionCount;
  } else if (msg.phase === "end" && msg.update) {
    updateActionCard(msg.update);
  }
}

async function loadActionHistory() {
  try {
    const response = await chrome.runtime.sendMessage({ type: "get_action_history" });
    if (response?.ok && Array.isArray(response.history)) {
      el.actionsFeed.innerHTML = "";
      actionCardsMap.clear();

      if (response.history.length === 0) {
        if (el.actionsEmpty) el.actionsFeed.appendChild(el.actionsEmpty);
        el.actionsBadge.textContent = "0";
        return;
      }

      actionCount = response.history.length;
      el.actionsBadge.textContent = actionCount;

      response.history.forEach((entry) => {
        const card = createActionCard(entry);
        actionCardsMap.set(entry.id, card);
        el.actionsFeed.appendChild(card);
      });
    }
  } catch {
    /* service worker starting */
  }
}

// --------------------------------------------------------------------------
// Lightbox Modal for Screenshots
// --------------------------------------------------------------------------
function openLightbox(src, caption) {
  // SECURITY: Only allow valid image data URL or blob
  if (typeof src !== "string" || (!src.startsWith("data:image/") && !src.startsWith("blob:"))) {
    return;
  }
  el.lightboxImage.src = src;
  el.lightboxCaption.textContent = caption || "";
  el.lightboxModal.classList.remove("hidden");
}

function closeLightbox() {
  el.lightboxModal.classList.add("hidden");
  el.lightboxImage.src = "";
}

el.lightboxClose.addEventListener("click", closeLightbox);
el.lightboxModal.addEventListener("click", (e) => {
  if (e.target === el.lightboxModal) closeLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !el.lightboxModal.classList.contains("hidden")) {
    closeLightbox();
  }
});

// Clear Actions
el.btnClearActions.addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "clear_action_history" });
  el.actionsFeed.innerHTML = "";
  actionCardsMap.clear();
  actionCount = 0;
  el.actionsBadge.textContent = "0";
  if (el.actionsEmpty) {
    el.actionsEmpty.style.display = "flex";
    el.actionsFeed.appendChild(el.actionsEmpty);
  }
});

// --------------------------------------------------------------------------
// Hermes Agent Gateway WebSocket Client
// --------------------------------------------------------------------------
let activeAssistantBubble = null;
let activeThinkingElement = null;

function updateAgentStatusPill(isBridgeOnline = null) {
  if (!el.statusAgent) return;
  if (isBridgeOnline === null) {
    isBridgeOnline = el.statusBridge?.classList.contains("online") || false;
  }
  if (gatewaySocket && gatewaySocket.readyState === WebSocket.OPEN) {
    el.statusAgent.className = "status-pill online";
    el.statusAgent.querySelector(".status-label").textContent = `Agent :${activeGatewayPort}`;
    el.statusAgent.title = `Hermes Agent Gateway online (ws://127.0.0.1:${activeGatewayPort}/api/ws)`;
  } else if (isBridgeOnline) {
    el.statusAgent.className = "status-pill online";
    el.statusAgent.querySelector(".status-label").textContent = "Agent Ready";
    el.statusAgent.title = "Hermes CLI is ready via Companion Bridge. Messages will execute directly.";
  } else {
    el.statusAgent.className = "status-pill offline";
    el.statusAgent.querySelector(".status-label").textContent = "Agent Offline";
    el.statusAgent.title = "Hermes Companion Bridge is offline. Start the companion bridge or run 'hermes serve'.";
  }
}

function connectGateway() {
  if (gatewayConnecting || (gatewaySocket && gatewaySocket.readyState === WebSocket.OPEN)) {
    return;
  }
  gatewayConnecting = true;
  el.statusAgent.className = "status-pill connecting";
  el.statusAgent.querySelector(".status-label").textContent = "Connecting...";

  const wsUrl = `ws://127.0.0.1:${activeGatewayPort}/api/ws`;
  try {
    gatewaySocket = new WebSocket(wsUrl);

    gatewaySocket.onopen = () => {
      gatewayConnecting = false;
      updateAgentStatusPill(true);

      // Request new or resume session
      sendJsonRpc("session.create", {});
    };

    gatewaySocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleGatewayMessage(msg);
      } catch {
        /* non-JSON frame */
      }
    };

    gatewaySocket.onclose = () => {
      gatewayConnecting = false;
      updateAgentStatusPill();

      // Try alternate port if 9119 failed
      if (activeGatewayPort === 9119) {
        activeGatewayPort = 8642;
      } else {
        activeGatewayPort = 9119;
      }
      setTimeout(connectGateway, 6000);
    };

    gatewaySocket.onerror = () => {
      gatewaySocket?.close();
    };
  } catch {
    gatewayConnecting = false;
    updateAgentStatusPill();
  }
}

function sendJsonRpc(method, params = {}) {
  if (!gatewaySocket || gatewaySocket.readyState !== WebSocket.OPEN) return false;
  const payload = {
    jsonrpc: "2.0",
    id: `req-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    method,
    params,
  };
  gatewaySocket.send(JSON.stringify(payload));
  return true;
}

function handleGatewayMessage(msg) {
  // JSON-RPC response
  if (msg.result?.session_id) {
    currentSessionId = msg.result.session_id;
    if (el.currentSessionId) {
      el.currentSessionId.textContent = currentSessionId.slice(0, 8);
      el.currentSessionId.title = currentSessionId;
    }
  }

  // Gateway streaming event notifications
  const event = msg.method || msg.event;
  const data = msg.params || msg.data;

  if (event === "gateway.ready") {
    if (data?.session_id) {
      currentSessionId = data.session_id;
      if (el.currentSessionId) {
        el.currentSessionId.textContent = currentSessionId.slice(0, 8);
      }
    }
  } else if (event === "message.start") {
    ensureAssistantBubble();
  } else if (event === "reasoning.delta" || event === "reasoning") {
    const text = typeof data === "string" ? data : data?.text || data?.delta || "";
    appendThinkingContent(text);
  } else if (event === "message.delta") {
    const text = typeof data === "string" ? data : data?.text || data?.delta || "";
    appendAssistantContent(text);
  } else if (event === "message.complete") {
    finalizeAssistantBubble();
  } else if (event?.startsWith("tool.")) {
    handleToolActivityEvent(event, data);
  }
}

function ensureAssistantBubble() {
  if (el.chatEmpty) el.chatEmpty.style.display = "none";

  activeAssistantBubble = document.createElement("div");
  activeAssistantBubble.className = "chat-bubble assistant";

  // Container for thinking
  activeThinkingElement = null;

  const contentDiv = document.createElement("div");
  contentDiv.className = "bubble-text";
  activeAssistantBubble.appendChild(contentDiv);

  el.chatStream.appendChild(activeAssistantBubble);
  scrollChatToBottom(true);
}

function appendThinkingContent(text) {
  if (!activeAssistantBubble) ensureAssistantBubble();

  if (!activeThinkingElement) {
    activeThinkingElement = document.createElement("details");
    activeThinkingElement.className = "thinking-block";
    activeThinkingElement.open = true;
    activeThinkingElement.innerHTML = `
      <summary class="thinking-summary">
        <div class="thinking-summary-left">
          <span class="thinking-dot"></span>
          <span class="thinking-title">Hermes is thinking...</span>
        </div>
        <span class="thinking-timer">0.0s</span>
      </summary>
      <div class="thinking-content"></div>
    `;
    activeAssistantBubble.insertBefore(activeThinkingElement, activeAssistantBubble.firstChild);
    const timerSpan = activeThinkingElement.querySelector(".thinking-timer");
    startThinkingTimer(timerSpan);
  }

  const content = activeThinkingElement.querySelector(".thinking-content");
  if (content) {
    content.textContent += text;
  }
  scrollChatToBottom();
}

function appendAssistantContent(text) {
  if (!activeAssistantBubble) ensureAssistantBubble();
  const textDiv = activeAssistantBubble.querySelector(".bubble-text");
  if (textDiv) {
    textDiv.textContent += text;
  }
  scrollChatToBottom();
}

function finalizeAssistantBubble(modelName = null) {
  const elapsedSeconds = stopThinkingTimer();
  if (activeThinkingElement) {
    activeThinkingElement.open = false; // collapse thinking when done
    const dot = activeThinkingElement.querySelector(".thinking-dot");
    if (dot) dot.classList.add("done");
    const title = activeThinkingElement.querySelector(".thinking-title");
    if (title) title.textContent = `Thought for ${elapsedSeconds}s`;
    const timer = activeThinkingElement.querySelector(".thinking-timer");
    if (timer) timer.remove();
  }

  if (activeAssistantBubble) {
    const textDiv = activeAssistantBubble.querySelector(".bubble-text");
    if (textDiv && !textDiv.querySelector(".code-block-wrapper")) {
      const raw = textDiv.textContent || "";
      if (raw.trim()) {
        textDiv.innerHTML = renderFormattedMarkdown(raw);
      }
    }

    if (!activeAssistantBubble.querySelector(".bubble-action-bar")) {
      const actionBar = document.createElement("div");
      actionBar.className = "bubble-action-bar";
      const modelDisplay = modelName || activeModel || "hermes";
      actionBar.innerHTML = `
        <div class="bubble-action-left">
          <button type="button" class="bubble-action-btn btn-copy-msg" title="Copy response markdown">
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>
            <span>Copy</span>
          </button>
        </div>
        <div class="bubble-action-right">
          <span class="bubble-meta-tag">${elapsedSeconds}s · ${escapeHtml(modelDisplay)}</span>
        </div>
      `;
      activeAssistantBubble.appendChild(actionBar);
    }
  }

  activeAssistantBubble = null;
  activeThinkingElement = null;
  scrollChatToBottom();
}

function handleToolActivityEvent(event, data) {
  const toolCard = document.createElement("div");
  toolCard.className = "tool-activity-card";
  const toolName = data?.tool || data?.name || "tool";
  toolCard.innerHTML = `
    <div class="tool-activity-header">
      <span>⚡ Tool: ${escapeHtml(toolName)}</span>
      <span>${escapeHtml(event)}</span>
    </div>
  `;
  el.chatStream.appendChild(toolCard);
  el.chatStream.scrollTop = el.chatStream.scrollHeight;
}

function appendUserBubble(text) {
  if (el.chatEmpty) el.chatEmpty.style.display = "none";
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble user";
  bubble.textContent = text;
  el.chatStream.appendChild(bubble);
  el.chatStream.scrollTop = el.chatStream.scrollHeight;
}

// --------------------------------------------------------------------------
// --------------------------------------------------------------------------
// Progressive Typing Streaming Engine (RAF-driven VSync Cadence)
// --------------------------------------------------------------------------
let activeStreamAbortFn = null;

async function streamTextToBubble(textDiv, fullMarkdown) {
  if (!textDiv || !fullMarkdown) {
    if (textDiv) textDiv.innerHTML = renderFormattedMarkdown(fullMarkdown || "");
    return;
  }

  if (activeStreamAbortFn) {
    activeStreamAbortFn();
    activeStreamAbortFn = null;
  }

  let isSkipped = false;
  let rafId = null;

  const bubble = textDiv.closest(".chat-bubble");
  const skipHandler = (e) => {
    if (e.target.closest("button") || e.target.closest("a")) return;
    isSkipped = true;
  };

  if (bubble) {
    bubble.addEventListener("click", skipHandler);
    bubble.style.cursor = "pointer";
    bubble.title = "Click to instantly show full response";
  }

  const cleanup = () => {
    if (rafId) cancelAnimationFrame(rafId);
    if (bubble) {
      bubble.removeEventListener("click", skipHandler);
      bubble.style.cursor = "";
      bubble.removeAttribute("title");
    }
    activeStreamAbortFn = null;
  };

  return new Promise((resolve) => {
    activeStreamAbortFn = () => {
      isSkipped = true;
      cleanup();
      textDiv.innerHTML = renderFormattedMarkdown(fullMarkdown);
      scrollChatToBottom();
      resolve();
    };

    const len = fullMarkdown.length;
    // Target duration: strictly between 300ms and 1200ms for silky responsiveness
    const targetDurationMs = Math.min(1200, Math.max(300, Math.round(len * 1.1)));
    const startTime = performance.now();
    let lastRenderedIdx = 0;
    let lastScrollTime = 0;

    const frame = (now) => {
      if (isSkipped) {
        cleanup();
        textDiv.innerHTML = renderFormattedMarkdown(fullMarkdown);
        scrollChatToBottom();
        resolve();
        return;
      }

      const elapsed = now - startTime;
      const progress = Math.min(1, elapsed / targetDurationMs);
      // Smooth cubic ease-out curve matching human reading deceleration
      const easeProgress = 1 - Math.pow(1 - progress, 1.5);
      const targetIdx = Math.min(len, Math.max(lastRenderedIdx + 1, Math.round(easeProgress * len)));

      if (targetIdx > lastRenderedIdx) {
        lastRenderedIdx = targetIdx;
        const accumulated = fullMarkdown.slice(0, targetIdx);
        textDiv.innerHTML = renderFormattedMarkdown(accumulated) + '<span class="streaming-cursor">▌</span>';

        if (now - lastScrollTime > 50) {
          scrollChatToBottom();
          lastScrollTime = now;
        }
      }

      if (lastRenderedIdx >= len || progress >= 1) {
        cleanup();
        textDiv.innerHTML = renderFormattedMarkdown(fullMarkdown);
        scrollChatToBottom();
        resolve();
      } else {
        rafId = requestAnimationFrame(frame);
      }
    };

    rafId = requestAnimationFrame(frame);
  });
}

// Direct Prompt via Companion Bridge (Hermes CLI fallback)
async function sendPromptViaBridge(promptPayload) {
  if (isPromptRunning) return;
  isPromptRunning = true;
  if (el.btnSendChat) el.btnSendChat.disabled = true;
  if (el.chatInput) el.chatInput.disabled = true;

  ensureAssistantBubble();
  const textDiv = activeAssistantBubble?.querySelector(".bubble-text");

  // Create thinking block with live timer
  const thinkingDetails = document.createElement("details");
  thinkingDetails.className = "thinking-block";
  thinkingDetails.open = true;
  const modelLabel = promptPayload.model || activeModel || "Hermes";
  const effortLabel = EFFORT_LABELS[promptPayload.reasoning_effort || activeEffort] || "High";
  thinkingDetails.innerHTML = `
    <summary class="thinking-summary">
      <div class="thinking-summary-left">
        <span class="thinking-dot"></span>
        <span class="thinking-title">Hermes is thinking...</span>
      </div>
      <span class="thinking-timer">0.0s</span>
    </summary>
    <div class="thinking-content">Dispatching to ${escapeHtml(modelLabel)} (effort: ${escapeHtml(effortLabel)})...</div>
  `;
  activeAssistantBubble.insertBefore(thinkingDetails, activeAssistantBubble.firstChild);
  activeThinkingElement = thinkingDetails;

  const timerSpan = thinkingDetails.querySelector(".thinking-timer");
  startThinkingTimer(timerSpan);
  scrollChatToBottom(true);

  try {
    const { headers, bridgeUrl } = await getBridgeAuthHeaders();
    const res = await fetch(`${bridgeUrl}/v1/hermes/prompt`, {
      method: "POST",
      headers,
      body: JSON.stringify(promptPayload),
    });

    const elapsedSeconds = stopThinkingTimer();

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const errMsg = errData.error || `Companion Bridge returned HTTP ${res.status}`;
      if (textDiv) {
        textDiv.innerHTML = `
          <div class="chat-error-card">
            <div class="chat-error-header">Execution Failed (${elapsedSeconds}s)</div>
            <div class="chat-error-body">${escapeHtml(errMsg)}</div>
            <button type="button" class="btn-retry-prompt" data-prompt="${escapeHtml(promptPayload.prompt)}">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="1 4 1 10 7 10"></polyline>
                <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
              </svg>
              <span>Retry</span>
            </button>
          </div>
        `;
      }
      finalizeAssistantBubble(promptPayload.model);
      return;
    }

    const data = await res.json();
    if (!data.ok) {
      const errMsg = data.error || "Failed to execute Hermes command.";
      if (textDiv) {
        textDiv.innerHTML = `
          <div class="chat-error-card">
            <div class="chat-error-header">Hermes Error (${elapsedSeconds}s)</div>
            <div class="chat-error-body">${escapeHtml(errMsg)}</div>
            <button type="button" class="btn-retry-prompt" data-prompt="${escapeHtml(promptPayload.prompt)}">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="1 4 1 10 7 10"></polyline>
                <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
              </svg>
              <span>Retry</span>
            </button>
          </div>
        `;
      }
      finalizeAssistantBubble(promptPayload.model);
      return;
    }

    // Collapse thinking details before streaming answer
    if (activeThinkingElement) {
      activeThinkingElement.open = false;
      const dot = activeThinkingElement.querySelector(".thinking-dot");
      if (dot) dot.classList.add("done");
      const title = activeThinkingElement.querySelector(".thinking-title");
      if (title) title.textContent = `Thought for ${elapsedSeconds}s`;
      const timer = activeThinkingElement.querySelector(".thinking-timer");
      if (timer) timer.remove();
    }

    // Smooth Progressive Streaming animation to bubble
    await streamTextToBubble(textDiv, data.response || "(No output returned)");
    finalizeAssistantBubble(promptPayload.model);

    // Resync SQLite database to update session list without wiping live chat DOM
    setTimeout(async () => {
      try {
        const sessions = await fetchHermesSessions(true);
        if (sessions && sessions.length > 0) {
          const latest = sessions[0];
          if (latest && latest.id) {
            currentSessionId = latest.id;
            currentFollowedSessionId = latest.id;
            if (el.chatSessionSelect && el.chatSessionSelect.value !== "__auto__") {
              el.chatSessionSelect.value = latest.id;
            }
          }
        }
      } catch {
        /* ignore background sync errors */
      }
    }, 1000);
  } catch (err) {
    const elapsedSeconds = stopThinkingTimer();
    if (textDiv) {
      textDiv.innerHTML = `
        <div class="chat-error-card">
          <div class="chat-error-header">Bridge Error (${elapsedSeconds}s)</div>
          <div class="chat-error-body">Could not reach Companion Bridge at 127.0.0.1:19876. (${escapeHtml(err.message || String(err))})</div>
          <button type="button" class="btn-retry-prompt" data-prompt="${escapeHtml(promptPayload.prompt)}">
            <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="1 4 1 10 7 10"></polyline>
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
            </svg>
            <span>Retry</span>
          </button>
        </div>
      `;
    }
    finalizeAssistantBubble(promptPayload.model);
  } finally {
    isPromptRunning = false;
    if (el.btnSendChat) el.btnSendChat.disabled = false;
    if (el.chatInput) {
      el.chatInput.disabled = false;
      el.chatInput.focus();
    }
  }
}

// Send Chat Message
el.chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  if (isPromptRunning) return;
  const text = el.chatInput.value.trim();
  if (!text) return;

  appendUserBubble(text);
  el.chatInput.value = "";
  el.chatInput.style.height = "auto";

  let targetSessionId = currentSessionId;
  if (
    el.chatSessionSelect?.value &&
    el.chatSessionSelect.value !== "__auto__" &&
    el.chatSessionSelect.value !== "__new__"
  ) {
    targetSessionId = el.chatSessionSelect.value;
  }

  const promptPayload = {
    session_id: targetSessionId,
    prompt: text,
  };
  if (activeModel) {
    promptPayload.model = activeModel;
  }
  if (activeProvider) {
    promptPayload.provider = activeProvider;
  }
  if (activeEffort) {
    promptPayload.reasoning_effort = activeEffort;
  }

  // If live WebSocket gateway is connected, stream via JSON-RPC; otherwise execute directly via Bridge
  const sent = sendJsonRpc("prompt.submit", promptPayload);
  if (!sent) {
    sendPromptViaBridge(promptPayload);
  }
});

// Auto-expand textarea
el.chatInput.addEventListener("input", () => {
  el.chatInput.style.height = "auto";
  el.chatInput.style.height = `${Math.min(el.chatInput.scrollHeight, 120)}px`;
});
el.chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el.chatForm.dispatchEvent(new Event("submit"));
  }
});

// --------------------------------------------------------------------------
// Safe Markdown Formatter with Code Block Headers & Copy
// --------------------------------------------------------------------------
function renderFormattedMarkdown(text) {
  if (!text) return "";
  let escaped = escapeHtml(text);

  // Auto-close unclosed code blocks during streaming to render syntax box immediately
  const backtickFences = (escaped.match(/```/g) || []).length;
  if (backtickFences % 2 === 1) {
    escaped += "\n```";
  }

  // Auto-close unclosed bold during streaming
  const doubleStars = (escaped.match(/\*\*/g) || []).length;
  if (doubleStars % 2 === 1) {
    escaped += "**";
  }

  // Fenced code blocks ```lang\n...``` (isolated via placeholders so double newlines don't break paragraphs)
  const codeBlocks = [];
  escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (match, lang, code) => {
    const cleanLang = (lang || "").trim();
    const displayLang = cleanLang ? escapeHtml(cleanLang) : "code";
    const idx = codeBlocks.length;
    codeBlocks.push(
      `<div class="code-block-wrapper"><div class="code-block-header"><span class="code-block-lang">${displayLang}</span><button type="button" class="btn-code-copy" title="Copy code"><svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg><span>Copy</span></button></div><pre><code class="code-content">${code.trim()}</code></pre></div>`
    );
    return `\n\n__CODE_BLOCK_${idx}__\n\n`;
  });

  // Inline code `...`
  escaped = escaped.replace(/`([^`\n]+)`/g, "<code>$1</code>");

  // Bold **...**
  escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // Links [text](https://...) — strictly http: or https:
  escaped = escaped.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

  // Paragraphs & line breaks
  const paragraphs = escaped.split(/\n{2,}/);
  let html = paragraphs
    .map((p) => {
      const trimmed = p.trim();
      if (!trimmed) return "";
      if (trimmed.startsWith("__CODE_BLOCK_")) return trimmed;
      if (trimmed.startsWith("<pre>")) return trimmed;
      return `<p>${trimmed.replace(/\n/g, "<br/>")}</p>`;
    })
    .join("");

  // Restore protected code blocks
  html = html.replace(/__CODE_BLOCK_(\d+)__/g, (match, idx) => {
    return codeBlocks[Number(idx)] || "";
  });

  return html;
}

// --------------------------------------------------------------------------
// Hermes Sessions Sync via Companion Bridge (SQLite state.db)
// --------------------------------------------------------------------------
let hermesSessionsCache = [];
let autoFollowTimer = null;
let currentFollowedSessionId = null;
let lastKnownMessageCount = -1;

async function getBridgeAuthHeaders() {
  try {
    const settings = await chrome.runtime.sendMessage({ type: "getSettings" });
    const headers = { "Content-Type": "application/json" };
    if (settings?.bridgeToken) {
      headers["X-Hermes-Chrome-Token"] = settings.bridgeToken;
    }
    const bridgeUrl = (settings?.bridgeUrl || "http://127.0.0.1:19876").replace(/\/$/, "");
    return { headers, bridgeUrl };
  } catch {
    return {
      headers: { "Content-Type": "application/json" },
      bridgeUrl: "http://127.0.0.1:19876",
    };
  }
}

async function fetchHermesSessions(forceReload = false) {
  const { headers, bridgeUrl } = await getBridgeAuthHeaders();
  try {
    const res = await fetch(`${bridgeUrl}/v1/hermes/sessions?limit=50`, {
      method: "GET",
      headers,
    });
    if (!res.ok) {
      return [];
    }
    const data = await res.json();
    if (data.ok && Array.isArray(data.sessions)) {
      hermesSessionsCache = data.sessions;
      updateSessionDropdownUI(data.sessions);
      return data.sessions;
    }
    return [];
  } catch {
    return [];
  }
}

function updateSessionDropdownUI(sessions) {
  if (!el.chatSessionSelect) return;
  const currentVal = el.chatSessionSelect.value;

  el.chatSessionSelect.innerHTML = "";

  // 1. Auto-follow Active
  const autoOpt = document.createElement("option");
  autoOpt.value = "__auto__";
  const activeTitle = sessions[0]?.title || "Auto";
  const displayTitle = activeTitle.length > 20 ? activeTitle.slice(0, 20) + "…" : activeTitle;
  autoOpt.textContent = `⚡ Auto-follow (${displayTitle})`;
  el.chatSessionSelect.appendChild(autoOpt);

  // 2. Individual sessions
  sessions.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    const title = (s.title || "Untitled Session").trim();
    const count = s.message_count || 0;
    const meta = count ? ` (${count} msgs)` : "";
    opt.textContent = `${title}${meta}`;
    el.chatSessionSelect.appendChild(opt);
  });

  // 3. New Chat option
  const newOpt = document.createElement("option");
  newOpt.value = "__new__";
  newOpt.textContent = "+ New Chat";
  el.chatSessionSelect.appendChild(newOpt);

  if (currentVal && Array.from(el.chatSessionSelect.options).some((o) => o.value === currentVal)) {
    el.chatSessionSelect.value = currentVal;
  } else {
    el.chatSessionSelect.value = "__auto__";
  }
}

async function loadHermesSession(sessionId) {
  if (sessionId === "__new__") {
    currentSessionId = null;
    currentFollowedSessionId = null;
    lastKnownMessageCount = 0;
    updateModelPillUI();
    el.chatStream.innerHTML = "";
    if (el.chatEmpty) {
      el.chatEmpty.style.display = "flex";
      el.chatStream.appendChild(el.chatEmpty);
    }
    return;
  }

  let targetId = sessionId;
  if (sessionId === "__auto__") {
    if (hermesSessionsCache.length > 0) {
      targetId = hermesSessionsCache[0].id;
    } else {
      const fresh = await fetchHermesSessions();
      if (fresh.length > 0) {
        targetId = fresh[0].id;
      } else {
        el.chatStream.innerHTML = "";
        if (el.chatEmpty) {
          el.chatEmpty.style.display = "flex";
          el.chatStream.appendChild(el.chatEmpty);
        }
        return;
      }
    }
  }

  if (!targetId) return;
  currentFollowedSessionId = targetId;
  currentSessionId = targetId;

  const { headers, bridgeUrl } = await getBridgeAuthHeaders();
  try {
    const res = await fetch(
      `${bridgeUrl}/v1/hermes/sessions/${encodeURIComponent(targetId)}/messages?limit=100`,
      {
        method: "GET",
        headers,
      }
    );
    if (!res.ok) return;
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.messages)) return;

    lastKnownMessageCount = data.messages.length;
    renderSessionMessages(data.session, data.messages);
  } catch {
    /* fetch error */
  }
}

function renderSessionMessages(sessionInfo, messages) {
  if (el.chatEmpty) el.chatEmpty.style.display = "none";
  el.chatStream.innerHTML = "";

  if (sessionInfo) {
    const badge = document.createElement("div");
    badge.className = "session-info-badge";
    const startTime = sessionInfo.started_at ? formatTime(sessionInfo.started_at) : "";
    const model = sessionInfo.model ? ` • ${sessionInfo.model}` : "";
    badge.textContent = `Session: ${sessionInfo.title || "Untitled"}${startTime ? ` (${startTime})` : ""}${model}`;
    el.chatStream.appendChild(badge);

    if (sessionInfo.model) {
      activeModel = sessionInfo.model;
      updateModelPillUI();
    }
  }

  if (messages.length === 0) {
    const emptyNotice = document.createElement("div");
    emptyNotice.className = "empty-state";
    emptyNotice.innerHTML = `
      <div class="empty-title">Empty Conversation</div>
      <div class="empty-desc">No messages recorded in this session yet.</div>
    `;
    el.chatStream.appendChild(emptyNotice);
    return;
  }

  for (const msg of messages) {
    const role = (msg.role || "").toLowerCase();

    if (role === "user") {
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble user";
      bubble.innerHTML = renderFormattedMarkdown(msg.content || "");
      el.chatStream.appendChild(bubble);
    } else if (role === "assistant") {
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble assistant";

      // 1. Thinking / Reasoning block
      if (msg.reasoning && msg.reasoning.trim()) {
        const thinkingDetails = document.createElement("details");
        thinkingDetails.className = "thinking-block";
        thinkingDetails.innerHTML = `
          <summary class="thinking-summary">
            <div class="thinking-summary-left">
              <span class="thinking-dot done"></span>
              <span>Thinking Process</span>
            </div>
          </summary>
          <div class="thinking-content">${escapeHtml(msg.reasoning)}</div>
        `;
        bubble.appendChild(thinkingDetails);
      }

      // 2. Tool calls
      if (msg.tool_calls) {
        let calls = [];
        try {
          calls = typeof msg.tool_calls === "string" ? JSON.parse(msg.tool_calls) : msg.tool_calls;
        } catch {
          calls = [];
        }
        if (Array.isArray(calls)) {
          calls.forEach((call) => {
            const toolCard = document.createElement("div");
            toolCard.className = "tool-activity-card";
            const toolName = call.function?.name || call.name || "tool";
            const args = call.function?.arguments || call.args || "";
            const argsStr = typeof args === "object" ? JSON.stringify(args, null, 2) : String(args);
            toolCard.innerHTML = `
              <div class="tool-activity-header">
                <span>⚡ Tool: ${escapeHtml(toolName)}</span>
              </div>
              ${argsStr ? `<pre class="tool-output-pre">${escapeHtml(argsStr)}</pre>` : ""}
            `;
            bubble.appendChild(toolCard);
          });
        }
      }

      // 3. Assistant content
      if (msg.content && msg.content.trim()) {
        const textDiv = document.createElement("div");
        textDiv.className = "bubble-text";
        textDiv.innerHTML = renderFormattedMarkdown(msg.content);
        bubble.appendChild(textDiv);
      }

      // Only append if it has at least reasoning, tool calls, or content
      if (bubble.hasChildNodes()) {
        const actionBar = document.createElement("div");
        actionBar.className = "bubble-action-bar";
        const timeLabel = msg.timestamp ? formatTime(msg.timestamp * 1000) : "";
        const metaText = timeLabel
          ? `${timeLabel} · ${sessionInfo?.model || activeModel || "hermes"}`
          : (sessionInfo?.model || activeModel || "hermes");
        actionBar.innerHTML = `
          <div class="bubble-action-left">
            <button type="button" class="bubble-action-btn btn-copy-msg" title="Copy response markdown">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
              <span>Copy</span>
            </button>
          </div>
          <div class="bubble-action-right">
            <span class="bubble-meta-tag">${escapeHtml(metaText)}</span>
          </div>
        `;
        bubble.appendChild(actionBar);
        el.chatStream.appendChild(bubble);
      }
    } else if (role === "tool") {
      const toolCard = document.createElement("div");
      toolCard.className = "tool-result-card";
      const name = msg.tool_name || "Tool Output";
      toolCard.innerHTML = `
        <div class="tool-result-header">
          <span>⚡ Result: ${escapeHtml(name)}</span>
        </div>
        <pre class="tool-output-pre">${escapeHtml(msg.content || "")}</pre>
      `;
      el.chatStream.appendChild(toolCard);
    }
  }

  el.chatStream.scrollTop = el.chatStream.scrollHeight;
}

function startSessionAutoSync() {
  if (autoFollowTimer) clearInterval(autoFollowTimer);

  autoFollowTimer = setInterval(async () => {
    if (document.hidden || isPromptRunning) {
      return;
    }

    const chatPane = document.getElementById("pane-chat");
    if (!chatPane || !chatPane.classList.contains("active")) {
      return;
    }

    if (!el.chatSessionSelect || el.chatSessionSelect.value !== "__auto__") {
      return;
    }

    const { headers, bridgeUrl } = await getBridgeAuthHeaders();
    try {
      const res = await fetch(`${bridgeUrl}/v1/hermes/sessions?limit=3`, {
        method: "GET",
        headers,
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok || !Array.isArray(data.sessions) || data.sessions.length === 0) return;

      const latest = data.sessions[0];
      const hasChanged =
        latest.id !== currentFollowedSessionId ||
        latest.message_count !== lastKnownMessageCount;

      if (hasChanged) {
        hermesSessionsCache = data.sessions;
        updateSessionDropdownUI(data.sessions);
        loadHermesSession("__auto__");
      }
    } catch {
      /* poll error */
    }
  }, 3500);
}

function initSessionSync() {
  if (el.chatSessionSelect) {
    el.chatSessionSelect.addEventListener("change", (e) => {
      const selected = e.target.value;
      if (selected === "__new__") {
        loadHermesSession("__new__");
      } else if (selected === "__auto__") {
        loadHermesSession("__auto__");
      } else {
        loadHermesSession(selected);
      }
    });
  }

  if (el.btnRefreshSessions) {
    el.btnRefreshSessions.addEventListener("click", async () => {
      await fetchHermesSessions(true);
      loadHermesSession(el.chatSessionSelect ? el.chatSessionSelect.value : "__auto__");
    });
  }

  if (el.btnNewSession) {
    el.btnNewSession.addEventListener("click", () => {
      if (el.chatSessionSelect) {
        el.chatSessionSelect.value = "__new__";
      }
      loadHermesSession("__new__");
    });
  }

  document.querySelectorAll(".intro-action-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const prompt = btn.getAttribute("data-prompt");
      if (prompt && el.chatInput) {
        el.chatInput.value = prompt;
        el.chatForm.dispatchEvent(new Event("submit"));
      }
    });
  });

  fetchHermesSessions().then(() => {
    loadHermesSession(el.chatSessionSelect ? el.chatSessionSelect.value : "__auto__");
  });
  startSessionAutoSync();
}

// --------------------------------------------------------------------------
// Page Context Extraction
// --------------------------------------------------------------------------
async function getActiveTabContext() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || tabs.length === 0) return null;
    const tab = tabs[0];

    // Try extracting page selection or title/url
    let selectedText = "";
    if (tab.id && tab.url && !tab.url.startsWith("chrome://")) {
      try {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: () => window.getSelection()?.toString() || "",
        });
        selectedText = result?.result || "";
      } catch {
        /* restricted page */
      }
    }

    return {
      title: tab.title || "",
      url: tab.url || "",
      selectedText,
    };
  } catch {
    return null;
  }
}

el.btnInjectContext.addEventListener("click", async () => {
  const ctx = await getActiveTabContext();
  if (!ctx) return;
  const snippet = `Context from tab [${ctx.title}](${ctx.url}):\n${ctx.selectedText ? `Selection: "${ctx.selectedText}"\n` : ""}`;
  el.chatInput.value = `${snippet}\n${el.chatInput.value}`;
  el.chatInput.focus();
});

el.btnQuickSummarize.addEventListener("click", async () => {
  const ctx = await getActiveTabContext();
  if (!ctx) return;
  el.chatInput.value = `Please summarize the key contents of this page: ${ctx.title} (${ctx.url})`;
  el.chatForm.dispatchEvent(new Event("submit"));
});

el.btnQuickCapture.addEventListener("click", async () => {
  const ctx = await getActiveTabContext();
  if (!ctx) return;
  el.chatInput.value = `Inspect the active page "${ctx.title}" at ${ctx.url} and describe its layout and actions.`;
  el.chatForm.dispatchEvent(new Event("submit"));
});

// --------------------------------------------------------------------------
// Chat Stream Interactions: Copy Code, Copy Message, Retry Prompt
// --------------------------------------------------------------------------
if (el.chatStream) {
  el.chatStream.addEventListener("click", async (e) => {
    // 1. Copy Code Block
    const btnCopyCode = e.target.closest(".btn-code-copy");
    if (btnCopyCode) {
      const wrapper = btnCopyCode.closest(".code-block-wrapper");
      const codeEl = wrapper?.querySelector(".code-content");
      if (codeEl) {
        await navigator.clipboard.writeText(codeEl.textContent || "");
        const label = btnCopyCode.querySelector("span");
        if (label) label.textContent = "Copied!";
        btnCopyCode.classList.add("copied");
        setTimeout(() => {
          if (label) label.textContent = "Copy";
          btnCopyCode.classList.remove("copied");
        }, 1500);
      }
      return;
    }

    // 2. Copy Whole Message
    const btnCopyMsg = e.target.closest(".btn-copy-msg");
    if (btnCopyMsg) {
      const bubble = btnCopyMsg.closest(".chat-bubble.assistant");
      const textEl = bubble?.querySelector(".bubble-text");
      if (textEl) {
        await navigator.clipboard.writeText(textEl.innerText || textEl.textContent || "");
        const label = btnCopyMsg.querySelector("span");
        if (label) label.textContent = "Copied!";
        btnCopyMsg.classList.add("copied");
        setTimeout(() => {
          if (label) label.textContent = "Copy";
          btnCopyMsg.classList.remove("copied");
        }, 1500);
      }
      return;
    }

    // 3. Retry Prompt
    const btnRetry = e.target.closest(".btn-retry-prompt");
    if (btnRetry) {
      const prompt = btnRetry.getAttribute("data-prompt");
      if (prompt && el.chatInput) {
        el.chatInput.value = prompt;
        el.chatForm.dispatchEvent(new Event("submit"));
      }
      return;
    }
  });

  // Smart Auto-Scroll detection (RAF-throttled & passive for 60/120fps scrolling)
  let scrollCheckRaf = null;
  el.chatStream.addEventListener(
    "scroll",
    () => {
      if (scrollCheckRaf) return;
      scrollCheckRaf = requestAnimationFrame(() => {
        scrollCheckRaf = null;
        const distanceFromBottom =
          el.chatStream.scrollHeight - el.chatStream.scrollTop - el.chatStream.clientHeight;
        if (distanceFromBottom > 70) {
          userIsScrollingUp = true;
          if (el.btnScrollBottom) el.btnScrollBottom.classList.remove("hidden");
        } else {
          userIsScrollingUp = false;
          if (el.btnScrollBottom) el.btnScrollBottom.classList.add("hidden");
        }
      });
    },
    { passive: true }
  );
}

if (el.btnScrollBottom) {
  el.btnScrollBottom.addEventListener("click", () => {
    userIsScrollingUp = false;
    el.btnScrollBottom.classList.add("hidden");
    el.chatStream.scrollTo({ top: el.chatStream.scrollHeight, behavior: "smooth" });
  });
}

// --------------------------------------------------------------------------
// Workspace Tab Group Management
// --------------------------------------------------------------------------
async function refreshWorkspaceTabs() {
  try {
    const response = await chrome.runtime.sendMessage({ type: "status" });
    el.workspaceTabsList.innerHTML = "";

    const tabs = response?.tabs || [];
    el.workspaceBadge.textContent = tabs.length;

    if (tabs.length === 0) {
      el.workspaceTabsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-title">No workspace tabs open</div>
          <div class="empty-desc">Open a tab below or via CLI:</div>
          <code class="code-snippet">hermes-chrome open &lt;url&gt;</code>
        </div>
      `;
      return;
    }

    tabs.forEach((tab) => {
      const card = document.createElement("div");
      card.className = "tab-card";
      card.innerHTML = `
        <div class="tab-card-info">
          <div class="tab-card-title">${escapeHtml(tab.title || "Untitled")}</div>
          <div class="tab-card-url">${escapeHtml(tab.url || "")}</div>
        </div>
        <div class="tab-card-actions">
          <button class="icon-btn btn-tab-focus" title="Focus Tab">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
              <circle cx="12" cy="12" r="3"></circle>
            </svg>
          </button>
          <button class="icon-btn btn-tab-close" title="Close Tab">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      `;

      card.querySelector(".btn-tab-focus").addEventListener("click", () => {
        chrome.tabs.update(tab.id, { active: true });
      });
      card.querySelector(".btn-tab-close").addEventListener("click", () => {
        chrome.tabs.remove(tab.id);
        setTimeout(refreshWorkspaceTabs, 300);
      });

      el.workspaceTabsList.appendChild(card);
    });
  } catch {
    /* ignore */
  }
}

el.btnRefreshWorkspace.addEventListener("click", refreshWorkspaceTabs);

el.btnWsOpen.addEventListener("click", async () => {
  const rawUrl = el.wsNewUrl.value.trim();
  if (!rawUrl) return;
  try {
    const u = new URL(rawUrl);
    // SECURITY: Enforce strictly http: or https: to prevent javascript: / file: navigation
    if (u.protocol !== "http:" && u.protocol !== "https:") {
      return;
    }
    el.wsNewUrl.value = "";
    await chrome.tabs.create({ url: u.href, active: false });
    setTimeout(refreshWorkspaceTabs, 500);
  } catch {
    /* invalid url */
  }
});

// --------------------------------------------------------------------------
// Bridge Status Monitor
// --------------------------------------------------------------------------
async function checkBridgeStatus() {
  try {
    const res = await chrome.runtime.sendMessage({ type: "status" });
    const isOnline = !!res?.bridgeOk && (!res?.bridgeAuth || (!!res?.tokenSet && !!res?.authReady));
    if (isOnline) {
      el.statusBridge.className = "status-pill online";
      el.statusBridge.querySelector(".status-label").textContent = "Bridge Online";
      if (el.bridgeWarningBanner) {
        el.bridgeWarningBanner.classList.add("hidden");
      }
      updateAgentStatusPill(true);
    } else {
      el.statusBridge.className = "status-pill offline";
      el.statusBridge.querySelector(".status-label").textContent = "Bridge Disconnected";
      if (el.bridgeWarningBanner) {
        el.bridgeWarningBanner.classList.remove("hidden");
        if (el.bannerText) {
          el.bannerText.textContent = res?.bridgeOk && !res?.tokenSet
            ? "Token required — Pair with Companion"
            : "Companion bridge disconnected (127.0.0.1:19876)";
        }
      }
      updateAgentStatusPill(false);
    }
  } catch {
    el.statusBridge.className = "status-pill offline";
    if (el.bridgeWarningBanner) el.bridgeWarningBanner.classList.remove("hidden");
    updateAgentStatusPill(false);
  }
}

// --------------------------------------------------------------------------
// Message Dispatcher
// --------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "hermes_action_event") {
    handleLiveActionEvent(msg);
  } else if (msg?.type === "hermes_action_history_cleared") {
    el.actionsFeed.innerHTML = "";
    actionCardsMap.clear();
    actionCount = 0;
    el.actionsBadge.textContent = "0";
    if (el.actionsEmpty) el.actionsFeed.appendChild(el.actionsEmpty);
  }
});

// --------------------------------------------------------------------------
// Model & Reasoning Effort State & Picker
// --------------------------------------------------------------------------
function updateModelPillUI() {
  if (el.modelPillName) {
    el.modelPillName.textContent = activeModel || "gemini-3.8-flash";
  }
  const effortLabel = EFFORT_LABELS[activeEffort] || activeEffort || "High";
  if (el.modelPillEffort) {
    el.modelPillEffort.textContent = effortLabel;
  }
  if (el.popoverCurrentEffortDesc) {
    el.popoverCurrentEffortDesc.textContent = EFFORT_DESCRIPTIONS[activeEffort] || activeEffort;
  }
  if (el.popoverActiveProvider) {
    const provObj = hermesModelsData?.providers?.find((p) => p.id === activeProvider);
    el.popoverActiveProvider.textContent = provObj ? provObj.name : (activeProvider || "");
  }
  if (el.composerModelPill) {
    el.composerModelPill.title = `Active Model: ${activeModel || "gemini-3.8-flash"} · Effort: ${effortLabel} (Click to change)`;
  }
  document.querySelectorAll(".effort-btn").forEach((btn) => {
    if (btn.getAttribute("data-effort") === activeEffort) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });
}

function openModelPopover() {
  if (!el.modelEffortPopover) return;
  el.modelEffortPopover.classList.remove("hidden");
  el.composerModelPill?.classList.add("open");
  if (el.modelSearchInput) {
    el.modelSearchInput.value = "";
    el.modelSearchInput.focus();
  }
  if (hermesModelsData?.providers) {
    renderModelList(hermesModelsData.providers, "");
  } else {
    fetchHermesModels();
  }
}

function closeModelPopover() {
  if (!el.modelEffortPopover) return;
  el.modelEffortPopover.classList.add("hidden");
  el.composerModelPill?.classList.remove("open");
}

function toggleModelPopover() {
  if (!el.modelEffortPopover) return;
  if (el.modelEffortPopover.classList.contains("hidden")) {
    openModelPopover();
  } else {
    closeModelPopover();
  }
}

async function fetchHermesModels() {
  const { headers, bridgeUrl } = await getBridgeAuthHeaders();
  try {
    const res = await fetch(`${bridgeUrl}/v1/hermes/models`, {
      method: "GET",
      headers,
    });
    if (!res.ok) return;
    const data = await res.json();
    if (data.ok) {
      hermesModelsData = data;
      const stored = await chrome.storage.local.get([
        "hermes_active_model",
        "hermes_active_effort",
        "hermes_active_provider",
      ]);
      if (!stored.hermes_active_model && data.current_model) {
        activeModel = data.current_model;
      }
      if (!stored.hermes_active_effort && data.current_effort) {
        activeEffort = data.current_effort;
      }
      if (!stored.hermes_active_provider && data.current_provider) {
        activeProvider = data.current_provider;
      }
      updateModelPillUI();
      renderModelList(data.providers, el.modelSearchInput?.value || "");
    }
  } catch {
    /* bridge may be offline */
  }
}

function renderModelList(providers, searchQuery = "") {
  if (!el.modelPickerList) return;
  el.modelPickerList.innerHTML = "";
  if (!Array.isArray(providers) || providers.length === 0) {
    el.modelPickerList.innerHTML = `<div style="padding: 12px; font-size: 11px; color: var(--text-muted); text-align: center;">No models catalog available</div>`;
    return;
  }

  const query = (searchQuery || "").trim().toLowerCase();
  let matchCount = 0;

  providers.forEach((prov) => {
    const filteredModels = prov.models.filter((m) => {
      if (!query) return true;
      return (
        m.toLowerCase().includes(query) ||
        prov.name.toLowerCase().includes(query) ||
        prov.id.toLowerCase().includes(query)
      );
    });

    if (filteredModels.length === 0) return;

    const groupTitle = document.createElement("div");
    groupTitle.className = "model-group-title";
    groupTitle.textContent = `${prov.name} (${filteredModels.length})`;
    el.modelPickerList.appendChild(groupTitle);

    filteredModels.forEach((modelId) => {
      matchCount++;
      const item = document.createElement("div");
      const isSel = modelId === activeModel;
      item.className = `model-picker-item ${isSel ? "selected" : ""}`;
      item.innerHTML = `
        <span class="model-item-name">${escapeHtml(modelId)}</span>
        <span class="model-item-check">✓</span>
      `;
      item.addEventListener("click", () => {
        selectModel(modelId, prov.id);
      });
      el.modelPickerList.appendChild(item);
    });
  });

  if (matchCount === 0) {
    el.modelPickerList.innerHTML = `<div style="padding: 12px; font-size: 11px; color: var(--text-muted); text-align: center;">No models match "${escapeHtml(query)}"</div>`;
  }
}

async function selectModel(modelId, providerId) {
  activeModel = modelId;
  if (providerId) activeProvider = providerId;
  updateModelPillUI();
  closeModelPopover();
  await chrome.storage.local.set({
    hermes_active_model: activeModel,
    hermes_active_provider: activeProvider,
  });
  syncConfigToBridge({ model: activeModel, provider: activeProvider });
  if (hermesModelsData?.providers) {
    renderModelList(hermesModelsData.providers, el.modelSearchInput?.value || "");
  }
}

async function selectEffort(effort) {
  activeEffort = effort;
  updateModelPillUI();
  await chrome.storage.local.set({
    hermes_active_effort: activeEffort,
  });
  syncConfigToBridge({ reasoning_effort: activeEffort });
}

async function syncConfigToBridge(configPayload) {
  const { headers, bridgeUrl } = await getBridgeAuthHeaders();
  try {
    await fetch(`${bridgeUrl}/v1/hermes/config`, {
      method: "POST",
      headers,
      body: JSON.stringify(configPayload),
    });
  } catch {
    /* bridge offline */
  }
}

async function initModelAndEffort() {
  try {
    const stored = await chrome.storage.local.get([
      "hermes_active_model",
      "hermes_active_provider",
      "hermes_active_effort",
    ]);
    if (stored.hermes_active_model) activeModel = stored.hermes_active_model;
    if (stored.hermes_active_provider) activeProvider = stored.hermes_active_provider;
    if (stored.hermes_active_effort) activeEffort = stored.hermes_active_effort;
  } catch {
    /* storage error */
  }

  updateModelPillUI();
  await fetchHermesModels();

  if (el.composerModelPill) {
    el.composerModelPill.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleModelPopover();
    });
  }

  if (el.btnCloseModelPopover) {
    el.btnCloseModelPopover.addEventListener("click", (e) => {
      e.stopPropagation();
      closeModelPopover();
    });
  }

  document.addEventListener("click", (e) => {
    if (
      el.modelEffortPopover &&
      !el.modelEffortPopover.classList.contains("hidden") &&
      !el.modelEffortPopover.contains(e.target) &&
      !el.composerModelPill?.contains(e.target)
    ) {
      closeModelPopover();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (
      e.key === "Escape" &&
      el.modelEffortPopover &&
      !el.modelEffortPopover.classList.contains("hidden")
    ) {
      closeModelPopover();
    }
  });

  document.querySelectorAll(".effort-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const effort = btn.getAttribute("data-effort");
      if (effort) {
        selectEffort(effort);
      }
    });
  });

  if (el.modelSearchInput) {
    el.modelSearchInput.addEventListener("input", (e) => {
      if (hermesModelsData?.providers) {
        renderModelList(hermesModelsData.providers, e.target.value);
      }
    });
  }

  if (el.btnApplyCustomModel) {
    el.btnApplyCustomModel.addEventListener("click", () => {
      const val = el.customModelInput?.value?.trim();
      if (val) {
        selectModel(val, "custom");
        el.customModelInput.value = "";
      }
    });
  }

  if (el.customModelInput) {
    el.customModelInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        el.btnApplyCustomModel?.click();
      }
    });
  }
}

// --------------------------------------------------------------------------
// Initialization
// --------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  loadActionHistory();
  connectGateway();
  initSessionSync();
  initModelAndEffort();
  checkBridgeStatus();
  setInterval(checkBridgeStatus, 6000);

  if (el.btnSidepanelOptions) {
    el.btnSidepanelOptions.addEventListener("click", () => {
      chrome.runtime.openOptionsPage();
    });
  }
  if (el.btnBannerPair) {
    el.btnBannerPair.addEventListener("click", async () => {
      const res = await chrome.runtime.sendMessage({ type: "pair" });
      if (res?.ok) {
        checkBridgeStatus();
      } else {
        alert((res && res.error) || "Pair failed — please ensure companion bridge is running");
      }
    });
  }
  if (el.btnBannerGuide) {
    el.btnBannerGuide.addEventListener("click", () => {
      chrome.tabs.create({ url: "help.html" });
    });
  }
});
