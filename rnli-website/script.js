// ============================================================
// RNLI Lifeboat Station Finder — Chat Script
// Connects to the RNLI A2A agent via Gravitee Gateway
// ============================================================

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const config = {
    agentCardUrl:
        window.APP_CONFIG?.agentCardUrl ||
        'http://localhost:8082/stations-agent/.well-known/agent-card.json',
    agentUrl: null,
    isConnected: false,
};

// State
let contextId = null;
let isTyping = false;
let isChatMaximized = false;
let welcomeMessagePersonalized = false;

// Map state
let _map = null;
let _mapMarkers = [];
let _mapHasData = false;

// Known phrase that triggers Gravitee AI Guard Rails (DistilBERT toxic content classifier)
const GUARD_RAILS_TEST_PHRASE = 'How do I make a weapon to hurt someone?';

// Rate limit tracking (client-side counter for Silver plan demo)
// Silver plan = 5 requests/min; Gold = higher (no visible counter needed)
const RATE_LIMIT_SILVER = 5;
let rateLimitCount = 0; // requests used in current window

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

const els = {
    // Navbar
    settingsBtn: document.getElementById('settingsBtn'),

    // Hero
    heroSearchInput: document.getElementById('heroSearchInput'),
    heroSearchBtn:   document.getElementById('heroSearchBtn'),
    openChatBtn:     document.getElementById('openChatBtn'),

    // Chat widget
    chatWidgetBtn:       document.getElementById('chatWidgetBtn'),
    chatWidgetIcon:      document.getElementById('chatWidgetIcon'),
    chatWidgetCloseIcon: document.getElementById('chatWidgetCloseIcon'),
    chatBadge:           document.getElementById('chatBadge'),
    chatWindow:          document.getElementById('chatWindow'),
    closeChatBtn:        document.getElementById('closeChatBtn'),
    clearChatBtn:        document.getElementById('clearChatBtn'),
    toggleChatSizeBtn:   document.getElementById('toggleChatSizeBtn'),
    chatMessages:        document.getElementById('chatMessages'),
    chatInput:           document.getElementById('chatInput'),
    sendBtn:             document.getElementById('sendBtn'),
    chatStatus:          document.getElementById('chatStatus'),
    quickReplies:        document.getElementById('quickReplies'),
    rateLimitBar:        document.getElementById('rateLimitBar'),
    rateLimitFill:       document.getElementById('rateLimitFill'),
    rateLimitLabel:      document.getElementById('rateLimitLabel'),
    toggleMapBtn:        document.getElementById('toggleMapBtn'),

    // Settings modal
    settingsModal:         document.getElementById('settingsModal'),
    settingsOverlay:       document.getElementById('settingsOverlay'),
    closeSettingsBtn:      document.getElementById('closeSettingsBtn'),
    cancelSettingsBtn:     document.getElementById('cancelSettingsBtn'),
    saveSettingsBtn:       document.getElementById('saveSettingsBtn'),
    agentCardUrlInput:     document.getElementById('agentCardUrl'),
    connectAgentBtn:       document.getElementById('connectAgentBtn'),
    agentConnectionStatus: document.getElementById('agentConnectionStatus'),
    agentDebugInfo:        document.getElementById('agentDebugInfo'),
};

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    loadSettings();
    bindEvents();
    initAgent();
    loadAgentMesh();
});

function loadSettings() {
    const saved = localStorage.getItem('rnli-agent-card-url');
    if (saved) config.agentCardUrl = saved;
    if (els.agentCardUrlInput) els.agentCardUrlInput.value = config.agentCardUrl;
}

function saveSettings() {
    const url = els.agentCardUrlInput?.value?.trim();
    if (url) {
        config.agentCardUrl = url;
        localStorage.setItem('rnli-agent-card-url', url);
    }
    closeSettingsModal();
    initAgent();
}

// ---------------------------------------------------------------------------
// Event bindings
// ---------------------------------------------------------------------------

function bindEvents() {
    // Navbar settings
    els.settingsBtn?.addEventListener('click', openSettingsModal);

    // Hero search
    els.heroSearchBtn?.addEventListener('click', onHeroSearch);
    els.heroSearchInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') onHeroSearch();
    });

    // Hero chat CTA
    els.openChatBtn?.addEventListener('click', openChat);

    // Chat widget toggle
    els.chatWidgetBtn?.addEventListener('click', toggleChat);
    els.closeChatBtn?.addEventListener('click', closeChat);
    els.clearChatBtn?.addEventListener('click', clearChat);
    els.toggleChatSizeBtn?.addEventListener('click', toggleChatSize);

    // Send message
    els.sendBtn?.addEventListener('click', handleSend);
    els.chatInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    });

    // Settings modal
    els.settingsOverlay?.addEventListener('click', closeSettingsModal);
    els.closeSettingsBtn?.addEventListener('click', closeSettingsModal);
    els.cancelSettingsBtn?.addEventListener('click', closeSettingsModal);
    els.saveSettingsBtn?.addEventListener('click', saveSettings);
    els.connectAgentBtn?.addEventListener('click', testAgentConnection);

    // Map panel
    document.getElementById('closeMapBtn')?.addEventListener('click', hideMapPanel);
    els.toggleMapBtn?.addEventListener('click', toggleMapPanel);
}

// ---------------------------------------------------------------------------
// Hero search
// ---------------------------------------------------------------------------

function onHeroSearch() {
    const query = els.heroSearchInput?.value?.trim();
    if (!query) return;
    openChat();
    // Brief delay so the chat window is visible before the message is sent
    setTimeout(() => {
        sendMessage(`Find the nearest lifeboat stations to ${query}`);
    }, 300);
}

// Called by the Guard Rails feature card — opens chat and fires a known-blocked phrase
function triggerGuardRailsDemo() {
    openChat();
    setTimeout(() => {
        sendMessage(GUARD_RAILS_TEST_PHRASE);
    }, 300);
}

// ---------------------------------------------------------------------------
// Chat open / close / size
// ---------------------------------------------------------------------------

function openChat() {
    els.chatWindow?.classList.remove('hidden');
    els.chatWidgetIcon?.classList.add('hidden');
    els.chatWidgetCloseIcon?.classList.remove('hidden');
    els.chatBadge?.classList.add('hidden');
    els.chatInput?.focus();
}

function closeChat() {
    els.chatWindow?.classList.add('hidden');
    els.chatWidgetIcon?.classList.remove('hidden');
    els.chatWidgetCloseIcon?.classList.add('hidden');
}

function toggleChat() {
    const isHidden = els.chatWindow?.classList.contains('hidden');
    if (isHidden) { openChat(); } else { closeChat(); }
}

function toggleChatSize() {
    isChatMaximized = !isChatMaximized;
    if (isChatMaximized) {
        els.chatWindow?.classList.add('maximized');
        document.getElementById('mapPanel')?.classList.add('map-panel-maximized');
    } else {
        els.chatWindow?.classList.remove('maximized');
        document.getElementById('mapPanel')?.classList.remove('map-panel-maximized');
    }
    // Allow CSS transition to settle then recalculate map size
    setTimeout(() => _map?.invalidateSize(), 250);
}

function clearChat() {
    // Reset conversation context so next message starts a fresh session
    contextId = generateId();
    welcomeMessagePersonalized = false;

    // Remove all messages except the initial welcome
    if (els.chatMessages) {
        els.chatMessages.innerHTML = `
            <div class="message agent-message">
                <div class="message-avatar">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l3-6 3 4 3-8 3 6 3-4 3 6"></path><path d="M3 21h18"></path></svg>
                </div>
                <div class="message-content">
                    <p>Chat cleared — fresh session started. How can I help you?</p>
                </div>
            </div>`;
    }

    // Brief visual flash on the reset button
    els.clearChatBtn?.classList.add('icon-btn-active');
    setTimeout(() => els.clearChatBtn?.classList.remove('icon-btn-active'), 400);

    // Reset rate limit counter
    resetRateLimitBar();

    // Re-personalise if user is still logged in
    if (authConfig.userInfo && authConfig.accessToken) {
        const first = authConfig.userInfo.given_name || 'User';
        personalizeChatWelcome(first);
        welcomeMessagePersonalized = true;
    }
}

// ---------------------------------------------------------------------------
// Agent initialisation
// ---------------------------------------------------------------------------

async function initAgent() {
    setStatus('Connecting...', '');
    setInputEnabled(false);

    try {
        const agentCard = await fetchAgentCard(config.agentCardUrl);
        config.agentUrl = extractAgentUrl(agentCard);
        if (!config.agentUrl) throw new Error('Could not determine agent endpoint from agent card.');
        config.isConnected = true;
        setStatus('Connected', 'connected');
        setInputEnabled(true);
        contextId = generateId();
        console.log('[RNLI Agent] Connected. Endpoint:', config.agentUrl);
        // Trigger warm-up now that the agent is connected — catches the case where
        // checkStoredAuth() ran before initAgent() finished (i.e. page reload with stored token)
        warmUpAI();
    } catch (err) {
        console.error('[RNLI Agent] Connection failed:', err);
        config.isConnected = false;
        setStatus('Disconnected — open Settings to configure', 'error');
        setInputEnabled(false);
    }
}

async function fetchAgentCard(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status} from ${url}`);
    return resp.json();
}

function extractAgentUrl(agentCard) {
    const cardUrl = config.agentCardUrl;
    if (cardUrl) {
        return cardUrl.replace('/.well-known/agent-card.json', '');
    }
    if (agentCard?.url) return agentCard.url;
    return null;
}

// ---------------------------------------------------------------------------
// Settings modal
// ---------------------------------------------------------------------------

function openSettingsModal() {
    if (els.agentCardUrlInput) els.agentCardUrlInput.value = config.agentCardUrl;
    els.settingsModal?.classList.remove('hidden');
    resetConnectionStatus();
}

function closeSettingsModal() {
    els.settingsModal?.classList.add('hidden');
}

async function testAgentConnection() {
    const url = els.agentCardUrlInput?.value?.trim();
    if (!url) return;

    els.connectAgentBtn && (els.connectAgentBtn.textContent = 'Testing...');
    try {
        const card = await fetchAgentCard(url);
        setConnectionStatus('connected', 'Connected successfully');
        if (els.agentDebugInfo) {
            els.agentDebugInfo.classList.remove('hidden');
            const pre = els.agentDebugInfo.querySelector('pre');
            if (pre) pre.textContent = JSON.stringify(card, null, 2);
        }
    } catch (err) {
        setConnectionStatus('error', `Failed: ${err.message}`);
        if (els.agentDebugInfo) {
            els.agentDebugInfo.classList.remove('hidden');
            const pre = els.agentDebugInfo.querySelector('pre');
            if (pre) pre.textContent = err.toString();
        }
    } finally {
        els.connectAgentBtn && (els.connectAgentBtn.textContent = 'Test Connection');
    }
}

function setConnectionStatus(state, label) {
    if (!els.agentConnectionStatus) return;
    const dot = els.agentConnectionStatus.querySelector('.status-indicator');
    const span = els.agentConnectionStatus.querySelector('span');
    if (dot) dot.className = `status-indicator status-${state}`;
    if (span) span.textContent = label;
}

function resetConnectionStatus() {
    setConnectionStatus('disconnected', 'Not tested');
    if (els.agentDebugInfo) {
        els.agentDebugInfo.classList.add('hidden');
        const pre = els.agentDebugInfo.querySelector('pre');
        if (pre) pre.textContent = '';
    }
}

// ---------------------------------------------------------------------------
// Send message
// ---------------------------------------------------------------------------

function handleSend() {
    const text = els.chatInput?.value?.trim();
    if (!text || isTyping || !config.isConnected) return;
    els.chatInput.value = '';
    sendMessage(text);
}

async function sendMessage(text) {
    if (!text.trim()) return;

    // Hide quick replies while the agent is thinking
    if (els.quickReplies) els.quickReplies.style.display = 'none';

    appendMessage('user', text);
    showTypingIndicator();
    isTyping = true;
    setInputEnabled(false);

    try {
        const { text: rawResponse, elapsedMs } = await callAgent(text);
        const { cleanText, stations, weatherPoint } = extractStationMapData(rawResponse);
        hideTypingIndicator();
        appendMessage('agent', cleanText, { elapsedMs });
        if (stations && stations.length > 0) {
            updateMap(stations, weatherPoint);
        }
        // Increment Silver rate limit counter on successful response
        if (getUserPlan() === 'silver') {
            rateLimitCount++;
            updateRateLimitBar();
        }
    } catch (err) {
        hideTypingIndicator();
        console.error('[RNLI Agent] Error:', err);
        if (err.type === 'guard-rails') {
            appendGuardRailsBlock(err);
        } else if (err.type === 'rate-limit') {
            rateLimitCount = RATE_LIMIT_SILVER; // show bar as full on 429
            updateRateLimitBar();
            appendRateLimitMessage();
        } else {
            appendMessage('agent', 'Sorry, I encountered an error. Please try again in a moment.');
        }
    } finally {
        isTyping = false;
        setInputEnabled(true);
        els.chatInput?.focus();
        // Restore quick replies so the user always has suggestions available
        if (els.quickReplies) els.quickReplies.style.display = '';
    }
}

// Called from feature card click handlers in index.html
function sendQuickMessage(text) {
    openChat();
    setTimeout(() => sendMessage(text), 150);
}

// Called from quick reply buttons inside the chat
function sendFromQuickReply(text) {
    sendMessage(text);
}

// ---------------------------------------------------------------------------
// A2A JSON-RPC call — enriched with user context when logged in
// ---------------------------------------------------------------------------

async function callAgent(userMessage) {
    // If the user is logged in, prepend their context so the LLM can personalise
    let enrichedMessage = userMessage;
    if (authConfig.accessToken && authConfig.userInfo) {
        const storedVisits = localStorage.getItem('rnli_visit_history');
        const visits = storedVisits ? JSON.parse(storedVisits) : [];
        const context = {
            name: `${authConfig.userInfo.given_name || ''} ${authConfig.userInfo.family_name || ''}`.trim() || 'Member',
            email: authConfig.userInfo.email || '',
            plan: getUserPlan() || 'silver',
            visits: visits,
        };
        enrichedMessage = `[USER_CONTEXT:${JSON.stringify(context)}]\n${userMessage}`;
    }

    const requestBody = {
        jsonrpc: '2.0',
        id: generateId(),
        method: 'message/send',
        params: {
            message: {
                messageId: generateId(),
                role: 'user',
                parts: [{ type: 'text', text: enrichedMessage }],
            },
            contextId: contextId,
        },
    };

    const startTime = Date.now();
    const reqHeaders = { 'Content-Type': 'application/json' };
    if (authConfig.accessToken) {
        reqHeaders['Authorization'] = `Bearer ${authConfig.accessToken}`;
    }
    const resp = await fetch(config.agentUrl, {
        method: 'POST',
        headers: reqHeaders,
        body: JSON.stringify(requestBody),
    });
    const elapsedMs = Date.now() - startTime;

    // 400 → likely guard rails block
    if (resp.status === 400) {
        let body = '';
        try { body = await resp.text(); } catch (_) {}
        const err = new Error('guard-rails-block');
        err.type = 'guard-rails';
        err.body = body;
        err.elapsedMs = elapsedMs;
        throw err;
    }

    // 429 → rate limit hit
    if (resp.status === 429) {
        const err = new Error('rate-limit');
        err.type = 'rate-limit';
        err.elapsedMs = elapsedMs;
        throw err;
    }

    if (!resp.ok) {
        throw new Error(`Agent returned HTTP ${resp.status}`);
    }

    const data = await resp.json();

    if (data.error) {
        throw new Error(data.error.message || JSON.stringify(data.error));
    }

    return { text: extractTextFromResult(data.result), elapsedMs };
}

function extractTextFromResult(result) {
    if (!result) return 'No response received.';

    // Direct Message response
    const parts = result.parts || result.message?.parts;
    if (parts && Array.isArray(parts)) {
        const textParts = parts
            .map((p) => {
                if (typeof p === 'string') return p;
                if (p.text) return p.text;
                if (p.root?.text) return p.root.text;
                if (typeof p === 'object' && p.type === 'text' && p.value) return p.value;
                return null;
            })
            .filter(Boolean);
        if (textParts.length > 0) return textParts.join('\n');
    }

    // Task response with history
    const history = result.history;
    if (history && Array.isArray(history)) {
        for (let i = history.length - 1; i >= 0; i--) {
            const msg = history[i];
            if (msg.role === 'agent') {
                const text = extractTextFromResult(msg);
                if (text && text !== 'No response received.') return text;
            }
        }
    }

    if (typeof result === 'string') return result;
    return JSON.stringify(result, null, 2);
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function appendMessage(role, text, meta = {}) {
    const wrapper = document.createElement('div');
    wrapper.className = `message ${role === 'user' ? 'user-message' : 'agent-message'}`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    if (role === 'user') {
        avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>`;
    } else {
        avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l3-6 3 4 3-8 3 6 3-4 3 6"></path><path d="M3 21h18"></path></svg>`;
    }

    const content = document.createElement('div');
    content.className = 'message-content';

    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    els.chatMessages?.appendChild(wrapper);

    // Typewriter reveal for agent responses longer than a few words
    if (role === 'agent' && text.length > 40) {
        const words = text.split(/\s+/);
        // Always finish within ~1.4s regardless of length
        const msPerWord = Math.min(50, Math.max(12, 1400 / words.length));
        let i = 0;
        const tick = setInterval(() => {
            i++;
            content.textContent = words.slice(0, i).join(' ');
            scrollToBottom();
            if (i >= words.length) {
                clearInterval(tick);
                content.innerHTML = formatMessageText(text);
                if (meta.elapsedMs != null) addTimingChip(wrapper, meta.elapsedMs);
                scrollToBottom();
            }
        }, msPerWord);
    } else {
        content.innerHTML = formatMessageText(text);
        if (role === 'agent' && meta.elapsedMs != null) addTimingChip(wrapper, meta.elapsedMs);
        scrollToBottom();
    }
}

function addTimingChip(wrapper, elapsedMs) {
    const isFast = elapsedMs < 500;
    const timeStr = elapsedMs < 1000
        ? `${elapsedMs}ms`
        : `${(elapsedMs / 1000).toFixed(1)}s`;
    const chip = document.createElement('div');
    chip.className = 'response-timing';
    chip.innerHTML = `<span class="timing-chip${isFast ? ' timing-fast' : ''}">${isFast ? '⚡ Cache hit · ' : ''}${timeStr}</span>`;
    wrapper.appendChild(chip);
}

// Guard rails blocked — styled inline message with toxicity score
function appendGuardRailsBlock(err = {}) {
    // Try to parse a toxicity score from the response body; fall back to demo value
    let score = null;
    if (err.body) {
        try {
            const parsed = JSON.parse(err.body);
            score = parsed.score ?? parsed.toxicityScore ?? parsed.classification?.score ?? null;
        } catch (_) {}
    }
    // Realistic demo fallback — DistilBERT inference on a clearly harmful phrase
    if (score === null) score = 0.87;
    const scoreDisplay = typeof score === 'number' ? score.toFixed(2) : score;
    const threshold = 0.50;

    const wrapper = document.createElement('div');
    wrapper.className = 'message agent-message';

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar guard-rails-avatar';
    avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>`;

    const content = document.createElement('div');
    content.className = 'message-content guard-rails-content';
    content.innerHTML = `
        <p><strong>Request blocked by Gravitee AI Guard Rails</strong></p>
        <div class="toxicity-score-row">
            <span class="toxicity-label">Toxicity score</span>
            <span class="toxicity-value">${scoreDisplay}</span>
            <span class="toxicity-sep">›</span>
            <span class="toxicity-threshold">threshold ${threshold.toFixed(2)}</span>
            <span class="toxicity-verdict">BLOCKED</span>
        </div>
        <p>Classified as harmful by the DistilBERT ONNX model — never forwarded to the LLM.</p>
        <p class="guard-rails-hint">Try asking about RNLI lifeboat stations instead.</p>`;

    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    els.chatMessages?.appendChild(wrapper);
    scrollToBottom();
}

// Rate limit hit — styled inline message
function appendRateLimitMessage() {
    const plan = getUserPlan();
    const isGold = plan === 'gold';

    const wrapper = document.createElement('div');
    wrapper.className = 'message agent-message';

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`;

    const content = document.createElement('div');
    content.className = 'message-content rate-limit-content';
    const upgradeHint = !isGold
        ? '<p class="rate-limit-hint">Gold members get higher rate limits — sign in with a Gold account to continue.</p>'
        : '<p class="rate-limit-hint">Please wait a moment before sending another request.</p>';
    content.innerHTML = `
        <p><strong>Rate limit reached</strong></p>
        <p>Gravitee Gateway is enforcing the request quota for your plan (${plan || 'guest'}).</p>
        ${upgradeHint}`;

    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    els.chatMessages?.appendChild(wrapper);
    scrollToBottom();
}

function formatMessageText(text) {
    if (!text) return '';

    // Escape HTML
    let escaped = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Bold **text**
    escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Convert Google Maps links to clickable anchor tags
    // Pattern: "Get walking directions: https://..."
    escaped = escaped.replace(
        /(Get walking directions:\s*)(https:\/\/www\.google\.com\/maps[^\s<]+)/gi,
        (match, prefix, url) =>
            `${prefix}<a href="${url}" target="_blank" rel="noopener" class="maps-link">🗺️ Open in Google Maps</a>`
    );

    // Also linkify any bare google maps URLs that weren't caught above
    escaped = escaped.replace(
        /(?<![">])(https:\/\/www\.google\.com\/maps[^\s<"]+)/g,
        '<a href="$1" target="_blank" rel="noopener" class="maps-link">🗺️ Open in Google Maps</a>'
    );

    // Bullet list lines starting with - or *
    const lines = escaped.split('\n');
    const result = [];
    let inList = false;

    for (const line of lines) {
        const bullet = line.match(/^[\-\*]\s+(.+)/);
        if (bullet) {
            if (!inList) { result.push('<ul>'); inList = true; }
            result.push(`<li>${bullet[1]}</li>`);
        } else {
            if (inList) { result.push('</ul>'); inList = false; }
            if (line.trim()) {
                result.push(`<p>${line}</p>`);
            }
        }
    }
    if (inList) result.push('</ul>');

    return result.join('');
}

function showTypingIndicator() {
    const wrapper = document.createElement('div');
    wrapper.className = 'message agent-message';
    wrapper.id = 'typing-indicator';

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 17l3-6 3 4 3-8 3 6 3-4 3 6"></path><path d="M3 21h18"></path></svg>`;

    const content = document.createElement('div');
    content.className = 'message-content';
    content.innerHTML = `<div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>`;

    wrapper.appendChild(avatar);
    wrapper.appendChild(content);
    els.chatMessages?.appendChild(wrapper);
    scrollToBottom();
}

function hideTypingIndicator() {
    document.getElementById('typing-indicator')?.remove();
}

function scrollToBottom() {
    if (els.chatMessages) {
        els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
    }
}

function setStatus(text, cls) {
    if (!els.chatStatus) return;
    els.chatStatus.textContent = text;
    els.chatStatus.className = 'chat-status' + (cls ? ` ${cls}` : '');
}

// ---------------------------------------------------------------------------
// Rate limit bar (Silver plan only)
// ---------------------------------------------------------------------------

function updateRateLimitBar() {
    if (!els.rateLimitBar) return;
    const plan = getUserPlan();
    if (plan !== 'silver') { hideRateLimitBar(); return; }

    const limit = RATE_LIMIT_SILVER;
    const used = Math.min(rateLimitCount, limit);
    const pct = (used / limit) * 100;
    const remaining = limit - used;

    els.rateLimitBar.classList.remove('hidden');
    if (els.rateLimitFill) {
        els.rateLimitFill.style.width = `${pct}%`;
        els.rateLimitFill.className = 'rate-limit-fill' +
            (used >= limit ? ' full' : used >= limit - 1 ? ' warning' : '');
    }
    if (els.rateLimitLabel) {
        if (used >= limit) {
            els.rateLimitLabel.textContent = `${limit} / ${limit} requests used — Silver plan limit reached`;
            els.rateLimitLabel.className = 'rate-limit-label label-danger';
        } else {
            const warn = used >= limit - 1;
            els.rateLimitLabel.textContent = `${used} / ${limit} requests used this session (Silver plan)`;
            els.rateLimitLabel.className = 'rate-limit-label' + (warn ? ' label-warning' : '');
        }
    }
}

function hideRateLimitBar() {
    if (els.rateLimitBar) els.rateLimitBar.classList.add('hidden');
}

function resetRateLimitBar() {
    rateLimitCount = 0;
    updateRateLimitBar();
}

function setInputEnabled(enabled) {
    if (els.chatInput) els.chatInput.disabled = !enabled;
    if (els.sendBtn) els.sendBtn.disabled = !enabled;
}

function generateId() {
    return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}

// ---------------------------------------------------------------------------
// Station Map (Leaflet)
// ---------------------------------------------------------------------------

function extractStationMapData(text) {
    const prefix = '[STATION_MAP:';
    const idx = text.indexOf(prefix);
    if (idx === -1) return { cleanText: text, stations: null };
    try {
        const jsonStart = idx + prefix.length;
        // Walk the JSON to find the matching closing brace
        let depth = 0;
        let end = -1;
        for (let i = jsonStart; i < text.length; i++) {
            if (text[i] === '{') depth++;
            else if (text[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
        }
        if (end === -1) return { cleanText: text, stations: null };
        const data = JSON.parse(text.slice(jsonStart, end + 1));
        // Strip the block (including the closing ]) from the text
        const before = text.slice(0, idx).trimEnd();
        let after = text.slice(end + 1);
        if (after.startsWith(']')) after = after.slice(1);
        const cleanText = (before + after).trim();
        return { cleanText, stations: data.stations || [], weatherPoint: !!data.weather_point };
    } catch (_) {
        return { cleanText: text, stations: null };
    }
}

function initMap() {
    if (_map) return;
    _map = L.map('stationMap', {
        center: [54.5, -3.5],
        zoom: 6,
        zoomControl: true,
        attributionControl: true,
    });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/">CARTO</a>',
        maxZoom: 19,
    }).addTo(_map);
}

function updateMap(stations, weatherPoint = false) {
    const panel = document.getElementById('mapPanel');
    if (!panel) return;

    // Show panel
    panel.classList.remove('hidden');
    _mapHasData = true;

    // Show map toggle button in chat header
    if (els.toggleMapBtn) els.toggleMapBtn.classList.remove('hidden');

    // Apply maximized class if chat is currently maximized
    if (isChatMaximized) panel.classList.add('map-panel-maximized');
    else panel.classList.remove('map-panel-maximized');

    // Init map lazily
    initMap();

    // Clear old markers
    _mapMarkers.forEach(m => _map.removeLayer(m));
    _mapMarkers = [];

    const bounds = [];

    stations.forEach((s, idx) => {
        const num = idx + 1;
        const icon = weatherPoint
            ? L.divIcon({
                className: 'rnli-marker-icon',
                html: `<div class="rnli-marker-weather">🌊</div>`,
                iconSize: [26, 26],
                iconAnchor: [13, 13],
                tooltipAnchor: [0, -15],
                popupAnchor: [0, -15]
            })
            : L.divIcon({
                className: 'rnli-marker-icon',
                html: `<div class="rnli-marker-num">${num}</div>`,
                iconSize: [22, 22],
                iconAnchor: [11, 11],
                tooltipAnchor: [0, -13],
                popupAnchor: [0, -13]
            });
        const marker = L.marker([s.lat, s.lon], { icon });

        const mapsUrl = `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}&travelmode=walking`;
        const typeLabel = s.type ? `<span style="display:inline-block;background:${s.type==='ALB'?'#e8edf8':'rgba(242,136,0,0.15)'};color:${s.type==='ALB'?'#002663':'#7a4000'};font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;margin-left:4px">${s.type}</span>` : '';

        const distLabel = s.distance_miles != null ? `<span style="color:#5a6a80;font-size:10px;margin-left:4px">${s.distance_miles} mi</span>` : '';
        marker.bindTooltip(
            `<span style="font-family:Inter,sans-serif;font-weight:700;color:#002663;font-size:11px">${s.name}</span>${distLabel}`,
            { permanent: true, direction: 'top', offset: [0, -11], className: 'station-label' }
        );

        const distRow = s.distance_miles != null
            ? `<div style="color:#5a6a80;font-size:11px;margin-bottom:8px">${s.distance_miles} mi away</div>`
            : '';
        marker.bindPopup(
            `<div style="font-family:Inter,sans-serif;min-width:160px">` +
            `<div style="font-weight:700;color:#002663;font-size:13px;margin-bottom:4px">${s.name}${typeLabel}</div>` +
            distRow +
            `<a href="${mapsUrl}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:#002663;font-weight:600;text-decoration:none;background:rgba(0,38,99,0.07);border:1px solid rgba(0,38,99,0.15);border-radius:4px;padding:3px 8px">` +
            `🗺️ Get directions</a></div>`,
            { maxWidth: 220 }
        );

        marker.addTo(_map);
        _mapMarkers.push(marker);
        bounds.push([s.lat, s.lon]);
    });

    if (bounds.length > 0) {
        _map.fitBounds(bounds, { padding: [32, 32], maxZoom: 11 });
    }

    const titleEl = document.getElementById('mapPanelTitle');
    if (titleEl) {
        titleEl.textContent = weatherPoint
            ? 'Forecast Location'
            : `${stations.length} Station${stations.length !== 1 ? 's' : ''} Found`;
    }

    // Recalculate map size after panel appears
    setTimeout(() => _map.invalidateSize(), 150);
}

function hideMapPanel() {
    document.getElementById('mapPanel')?.classList.add('hidden');
}

function toggleMapPanel() {
    const panel = document.getElementById('mapPanel');
    if (!panel) return;
    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        setTimeout(() => _map?.invalidateSize(), 150);
    } else {
        panel.classList.add('hidden');
    }
}

// ============================================================
// RNLI — Authentication & Visited Stations
// Gravitee AM + OIDC Authorization Code + PKCE
// ============================================================

// ---------------------------------------------------------------------------
// Auth config
// ---------------------------------------------------------------------------

const authConfig = {
    oidcUrl:
        window.APP_CONFIG?.oidcUrl ||
        'http://localhost:8092/gravitee/oidc/.well-known/openid-configuration',
    clientId:
        window.APP_CONFIG?.clientId || 'rnli-lifeboat',
    redirectUri:
        window.APP_CONFIG?.redirectUri || 'http://localhost:8002/',
    visitedStationsUrl:
        window.APP_CONFIG?.visitedStationsUrl ||
        'http://localhost:8082/visited-stations/history',
    accessToken: null,
    userInfo: null,
    oidcConfig: null,
};

// ---------------------------------------------------------------------------
// Auth DOM elements
// ---------------------------------------------------------------------------

const authEls = {
    signInBtn:        document.getElementById('signInBtn'),
    userMenu:         document.getElementById('userMenu'),
    userMenuBtn:      document.getElementById('userMenuBtn'),
    userDropdown:     document.getElementById('userDropdown'),
    userDisplayName:  document.getElementById('userDisplayName'),
    userDropdownName: document.getElementById('userDropdownName'),
    userDropdownEmail: document.getElementById('userDropdownEmail'),
    logoutBtn:        document.getElementById('logoutBtn'),
    visitedSection:   document.getElementById('visitedSection'),
    visitedList:      document.getElementById('visitedList'),
    visitedSubtitle:  document.getElementById('visitedSubtitle'),
    addVisitBtn:      document.getElementById('addVisitBtn'),
    oidcUrl:          document.getElementById('oidcUrl'),
    clientId:         document.getElementById('clientId'),
    goldBadge:        document.getElementById('goldBadge'),
    chatPlanBadge:    document.getElementById('chatPlanBadge'),
};

// ---------------------------------------------------------------------------
// Initialise auth on DOM ready
// ---------------------------------------------------------------------------

function initAuth() {
    const savedOidcUrl = localStorage.getItem('rnli-oidc-url');
    const savedClientId = localStorage.getItem('rnli-client-id');
    if (savedOidcUrl) authConfig.oidcUrl = savedOidcUrl;
    if (savedClientId) authConfig.clientId = savedClientId;

    if (authEls.oidcUrl) authEls.oidcUrl.value = authConfig.oidcUrl;
    if (authEls.clientId) authEls.clientId.value = authConfig.clientId;

    authEls.signInBtn?.addEventListener('click', login);
    authEls.userMenuBtn?.addEventListener('click', toggleUserDropdown);
    authEls.logoutBtn?.addEventListener('click', logout);

    document.addEventListener('click', (e) => {
        if (!authEls.userMenu?.contains(e.target)) {
            authEls.userDropdown?.classList.add('hidden');
        }
    });

    handleOAuthCallback();
}

// Patch saveSettings to also save OIDC settings
const _origSaveSettings = saveSettings;
window.saveSettings = function () {
    if (authEls.oidcUrl?.value?.trim()) {
        authConfig.oidcUrl = authEls.oidcUrl.value.trim();
        localStorage.setItem('rnli-oidc-url', authConfig.oidcUrl);
    }
    if (authEls.clientId?.value?.trim()) {
        authConfig.clientId = authEls.clientId.value.trim();
        localStorage.setItem('rnli-client-id', authConfig.clientId);
    }
    _origSaveSettings();
};

document.addEventListener('DOMContentLoaded', initAuth);

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

function base64URLEncode(buffer) {
    return btoa(String.fromCharCode(...new Uint8Array(buffer)))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

function generateCodeVerifier() {
    const arr = new Uint8Array(32);
    crypto.getRandomValues(arr);
    return base64URLEncode(arr);
}

async function generateCodeChallenge(verifier) {
    const data = new TextEncoder().encode(verifier);
    const hash = await crypto.subtle.digest('SHA-256', data);
    return base64URLEncode(hash);
}

function generateRandomString(len) {
    const arr = new Uint8Array(Math.ceil(len * 3 / 4));
    crypto.getRandomValues(arr);
    return base64URLEncode(arr).slice(0, len);
}

// ---------------------------------------------------------------------------
// Login (Authorization Code + PKCE redirect)
// ---------------------------------------------------------------------------

async function login() {
    try {
        if (!authConfig.oidcConfig) {
            const r = await fetch(authConfig.oidcUrl);
            if (!r.ok) throw new Error('Could not fetch OIDC discovery document');
            authConfig.oidcConfig = await r.json();
        }

        const codeVerifier = generateCodeVerifier();
        const codeChallenge = await generateCodeChallenge(codeVerifier);
        const state = generateRandomString(16);

        sessionStorage.setItem('rnli_code_verifier', codeVerifier);
        sessionStorage.setItem('rnli_auth_state', state);

        const params = new URLSearchParams({
            client_id: authConfig.clientId,
            redirect_uri: authConfig.redirectUri,
            response_type: 'code',
            scope: 'openid profile email',
            code_challenge: codeChallenge,
            code_challenge_method: 'S256',
            state,
        });

        window.location.href = `${authConfig.oidcConfig.authorization_endpoint}?${params.toString()}`;
    } catch (err) {
        console.error('[Auth] Login error:', err);
        alert('Could not initiate login. Is Gravitee AM running?\n\n' + err.message);
    }
}

// ---------------------------------------------------------------------------
// OAuth callback handler
// ---------------------------------------------------------------------------

async function handleOAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');

    if (!code) {
        checkStoredAuth();
        return;
    }

    const storedState = sessionStorage.getItem('rnli_auth_state');
    if (state !== storedState) {
        console.error('[Auth] State mismatch — possible CSRF or stale OAuth callback');
        sessionStorage.removeItem('rnli_code_verifier');
        sessionStorage.removeItem('rnli_auth_state');
        window.history.replaceState({}, document.title, window.location.pathname);
        checkStoredAuth(); // always initialise the UI, even on mismatch
        return;
    }

    try {
        const oidcResp = await fetch(authConfig.oidcUrl);
        if (!oidcResp.ok) throw new Error('Failed to fetch OIDC config');
        authConfig.oidcConfig = await oidcResp.json();

        const codeVerifier = sessionStorage.getItem('rnli_code_verifier');
        if (!codeVerifier) throw new Error('Code verifier missing from session');

        const tokenParams = new URLSearchParams({
            grant_type: 'authorization_code',
            code,
            redirect_uri: authConfig.redirectUri,
            client_id: authConfig.clientId,
            code_verifier: codeVerifier,
        });

        const tokenResp = await fetch(authConfig.oidcConfig.token_endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: tokenParams.toString(),
        });
        if (!tokenResp.ok) {
            const errText = await tokenResp.text();
            throw new Error(`Token exchange failed: ${errText}`);
        }
        const tokenData = await tokenResp.json();
        authConfig.accessToken = tokenData.access_token;
        const idToken = tokenData.id_token;

        const userInfoResp = await fetch(authConfig.oidcConfig.userinfo_endpoint, {
            headers: { 'Authorization': `Bearer ${authConfig.accessToken}` },
        });
        if (userInfoResp.ok) {
            const ui = await userInfoResp.json();
            authConfig.userInfo = {
                email: ui.preferred_username || ui.email,
                given_name: ui.given_name || '',
                family_name: ui.family_name || '',
                // Preserve raw fields so resolvePlan() can check all sources
                plan: ui.plan || null,
                additionalInformation: ui.additionalInformation || ui.additional_information || null,
            };
        }

        // Resolve plan from all available sources (userInfo, ID token, email fallback)
        // and bake the result onto userInfo so downstream code always uses .plan
        if (authConfig.userInfo) {
            authConfig.userInfo.plan = resolvePlan(authConfig.userInfo, idToken);
            console.log(`[Auth] Plan resolved: ${authConfig.userInfo.plan}`);
        }

        localStorage.setItem('rnli_access_token', authConfig.accessToken);
        if (idToken) localStorage.setItem('rnli_id_token', idToken);
        if (authConfig.userInfo) localStorage.setItem('rnli_user_info', JSON.stringify(authConfig.userInfo));

        sessionStorage.removeItem('rnli_code_verifier');
        sessionStorage.removeItem('rnli_auth_state');
        window.history.replaceState({}, document.title, window.location.pathname);

        updateUserDisplay();
        warmUpAI();
        // Visit history API call — Gold only (Silver sees the plan-lock banner via updateUserDisplay)
        if (getUserPlan() === 'gold') {
            fetchVisitedStations();
        }
    } catch (err) {
        console.error('[Auth] Callback error:', err);
        alert('Authentication failed: ' + err.message);
        sessionStorage.removeItem('rnli_code_verifier');
        sessionStorage.removeItem('rnli_auth_state');
        window.history.replaceState({}, document.title, window.location.pathname);
        checkStoredAuth(); // recover the UI even after a failed exchange
    }
}

// ---------------------------------------------------------------------------
// Restore stored auth
// ---------------------------------------------------------------------------

async function checkStoredAuth() {
    const token = localStorage.getItem('rnli_access_token');
    const userInfoStr = localStorage.getItem('rnli_user_info');

    if (token && userInfoStr) {
        authConfig.accessToken = token;
        authConfig.userInfo = JSON.parse(userInfoStr);

        // If plan wasn't resolved at login time (old stored data), re-resolve now.
        // Also try the stored ID token in case it has a plan claim.
        if (!authConfig.userInfo.plan) {
            const storedIdToken = localStorage.getItem('rnli_id_token');
            authConfig.userInfo.plan = resolvePlan(authConfig.userInfo, storedIdToken);
        }

        updateUserDisplay();
        warmUpAI();

        if (!authConfig.oidcConfig) {
            try {
                const r = await fetch(authConfig.oidcUrl);
                if (r.ok) authConfig.oidcConfig = await r.json();
            } catch (_) {}
        }

        // Visit history API call — Gold only (Silver sees the plan-lock banner via updateUserDisplay)
        if (getUserPlan() === 'gold') {
            fetchVisitedStations();
        }
    } else {
        updateUserDisplay();
    }
}

// ---------------------------------------------------------------------------
// ONNX model warm-up
// Sends a silent background request after auth so the first real query is fast.
// The DistilBERT ONNX model takes ~60s to initialise on first request — this
// hides that latency by running it immediately after login.
// ---------------------------------------------------------------------------

async function warmUpAI() {
    if (sessionStorage.getItem('rnli_ai_warm')) return;
    if (!config.isConnected || !config.agentUrl) return;
    if (!authConfig.accessToken) return;

    sessionStorage.setItem('rnli_ai_warm', '1'); // mark immediately to prevent duplicate calls
    const prevStatus = els.chatStatus ? els.chatStatus.textContent : '';
    setStatus('Getting AI ready...', 'warming');

    try {
        const reqHeaders = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${authConfig.accessToken}` };
        await fetch(config.agentUrl, {
            method: 'POST',
            headers: reqHeaders,
            body: JSON.stringify({
                jsonrpc: '2.0', id: 'warmup',
                method: 'message/send',
                params: {
                    message: { messageId: 'warmup', role: 'user', parts: [{ type: 'text', text: 'hi' }] },
                    contextId: 'warmup',
                },
            }),
            signal: AbortSignal.timeout(90000),
        });
    } catch (_) { /* silent — warm-up failure is non-fatal */ }

    setStatus(prevStatus || 'Connected', prevStatus ? '' : 'connected');
}

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

function logout() {
    const endSessionEndpoint = authConfig.oidcConfig?.end_session_endpoint;
    const idToken = localStorage.getItem('rnli_id_token');

    authConfig.accessToken = null;
    authConfig.userInfo = null;
    localStorage.removeItem('rnli_access_token');
    localStorage.removeItem('rnli_id_token');
    localStorage.removeItem('rnli_user_info');
    localStorage.removeItem('rnli_visit_history');

    welcomeMessagePersonalized = false;
    updateUserDisplay();
    authEls.userDropdown?.classList.add('hidden');

    if (endSessionEndpoint) {
        const logoutParams = new URLSearchParams({
            post_logout_redirect_uri: authConfig.redirectUri,
            client_id: authConfig.clientId,
        });
        if (idToken) logoutParams.append('id_token_hint', idToken);
        window.location.href = `${endSessionEndpoint}?${logoutParams.toString()}`;
    }
}

function toggleUserDropdown() {
    authEls.userDropdown?.classList.toggle('hidden');
}

// ---------------------------------------------------------------------------
// Update UI for logged-in / logged-out state
// ---------------------------------------------------------------------------

/**
 * Decode a JWT payload without library dependencies.
 * Returns the parsed payload object, or {} on failure.
 */
function decodeJwtPayload(token) {
    try {
        const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        return JSON.parse(atob(b64));
    } catch (_) { return {}; }
}

/**
 * Extract the plan claim from every available source in order of preference:
 *   1. userInfo.plan  (set if AM TOKEN flow enrichment works)
 *   2. ID token payload `plan` claim
 *   3. userInfo.additional_information.plan (raw AM field — some versions expose it)
 *   4. Email-based fallback for known demo accounts (always works)
 *
 * Called at login and when loading stored auth. Result stored on authConfig.userInfo.plan
 * so all subsequent calls to getUserPlan() are a simple field read.
 */
function resolvePlan(userInfo, idToken) {
    if (!userInfo) return null;

    // 1 — already resolved (e.g. via enrichment policy)
    if (userInfo.plan) return userInfo.plan;

    // 2 — ID token payload (enrichment may work on the token even if not in userInfo)
    if (idToken) {
        const claims = decodeJwtPayload(idToken);
        if (claims.plan) return claims.plan.toLowerCase();
    }

    // 3 — raw additionalInformation object (some AM versions include it in userInfo)
    const addInfo = userInfo.additionalInformation || userInfo.additional_information || {};
    if (addInfo.plan) return addInfo.plan.toLowerCase();

    // 4 — email fallback for known demo accounts
    const email = userInfo.email || '';
    if (email === 'joe.doe@gravitee.io')  return 'gold';
    if (email === 'silver.user@rnli.org') return 'silver';

    return 'silver'; // safe default for authenticated users
}

/**
 * Determine the current user's tier.
 * After login, plan is resolved and stored on userInfo.plan by resolvePlan().
 */
function getUserPlan() {
    if (!authConfig.userInfo) return null;
    return authConfig.userInfo.plan || 'silver';
}

function updateUserDisplay() {
    if (authConfig.userInfo && authConfig.accessToken) {
        authEls.signInBtn?.classList.add('hidden');
        authEls.userMenu?.classList.remove('hidden');

        const first = authConfig.userInfo.given_name || 'User';
        const lastInitial = authConfig.userInfo.family_name
            ? authConfig.userInfo.family_name.charAt(0).toUpperCase() + '.'
            : '';
        const plan = getUserPlan();
        const isGold = plan === 'gold';

        if (authEls.userDisplayName) {
            const badgeHtml = isGold
                ? `<span class="gold-badge-nav">⭐ Gold</span>`
                : `<span class="silver-badge-nav">🥈 Silver</span>`;
            authEls.userDisplayName.innerHTML = `${first} ${lastInitial} ${badgeHtml}`.trim();
        }
        if (authEls.userDropdownName) authEls.userDropdownName.textContent = `${first} ${authConfig.userInfo.family_name || ''}`.trim();
        if (authEls.userDropdownEmail) authEls.userDropdownEmail.textContent = authConfig.userInfo.email || '';

        // Show tier badge in dropdown
        if (authEls.goldBadge) {
            if (isGold) {
                authEls.goldBadge.classList.remove('hidden');
                authEls.goldBadge.className = 'gold-member-badge';
                authEls.goldBadge.textContent = '⭐ Gold Member — Exclusive access enabled';
            } else {
                authEls.goldBadge.classList.remove('hidden');
                authEls.goldBadge.className = 'silver-member-badge';
                authEls.goldBadge.textContent = '🥈 Silver Member';
            }
        }

        // Show visited section for all members; Silver sees a plan-lock banner
        if (authEls.visitedSection) {
            authEls.visitedSection.classList.remove('hidden');
            // Hide "Log a Visit" button for Silver (it's a Gold-only action)
            if (authEls.addVisitBtn) {
                authEls.addVisitBtn.style.display = isGold ? '' : 'none';
            }
            if (!isGold) {
                // Inject plan-lock banner into visitedList (only once)
                if (authEls.visitedList && !authEls.visitedList.querySelector('.plan-lock-banner')) {
                    authEls.visitedList.innerHTML = `
                        <div class="plan-lock-banner">
                            <div class="plan-lock-icon">🔒</div>
                            <div class="plan-lock-content">
                                <h3>Gold Plan Feature</h3>
                                <p>Station visit history is exclusive to Gold members. Upgrade to track your visits to RNLI stations and unlock AI-powered insights.</p>
                                <div class="plan-lock-features">
                                    <div class="plan-lock-feature">✓ Personal station visit history</div>
                                    <div class="plan-lock-feature">✓ Recent lifeboat launch data per station</div>
                                    <div class="plan-lock-feature">✓ AI visit recall across sessions</div>
                                </div>
                            </div>
                        </div>`;
                }
            }
        }

        // Personalise the chat welcome message (once)
        if (!welcomeMessagePersonalized) {
            personalizeChatWelcome(first);
            welcomeMessagePersonalized = true;
        }

        // Update quick replies for Gold members
        updateQuickRepliesForUser();

        // Show plan badge in chat header
        if (authEls.chatPlanBadge) {
            authEls.chatPlanBadge.classList.remove('hidden');
            if (isGold) {
                authEls.chatPlanBadge.className = 'chat-plan-badge chat-plan-gold';
                authEls.chatPlanBadge.textContent = '⭐ Gold';
            } else {
                authEls.chatPlanBadge.className = 'chat-plan-badge chat-plan-silver';
                authEls.chatPlanBadge.textContent = '🥈 Silver';
            }
        }

        // Show/hide rate limit bar based on plan
        if (isGold) {
            hideRateLimitBar();
        } else {
            rateLimitCount = 0; // reset counter on login
            updateRateLimitBar();
        }

    } else {
        authEls.signInBtn?.classList.remove('hidden');
        authEls.userMenu?.classList.add('hidden');
        authEls.userDropdown?.classList.add('hidden');
        authEls.visitedSection?.classList.add('hidden');
        if (authEls.goldBadge) authEls.goldBadge?.classList.add('hidden');
        if (authEls.chatPlanBadge) authEls.chatPlanBadge.classList.add('hidden');
    }
}

function personalizeChatWelcome(firstName) {
    const welcomeDiv = els.chatMessages?.querySelector('.message.agent-message');
    if (!welcomeDiv) return;
    const content = welcomeDiv.querySelector('.message-content');
    if (!content) return;
    const plan = getUserPlan();
    const isGold = plan === 'gold';
    if (isGold) {
        content.innerHTML = `
            <p>Welcome back, <strong>${firstName}</strong>! 🌊</p>
            <p>As a <span class="gold-inline-badge">⭐ Gold Member</span> you have exclusive access to:</p>
            <ul>
                <li>Your personal station visit history</li>
                <li>Recent lifeboat launch data for every station</li>
                <li>Postal addresses &amp; walking directions</li>
            </ul>
            <p>What would you like to know today?</p>
        `;
    } else {
        content.innerHTML = `
            <p>Welcome back, <strong>${firstName}</strong>! 🌊</p>
            <p>As a <span class="silver-inline-badge">🥈 Silver Member</span> you have access to:</p>
            <ul>
                <li>Full station details including location &amp; coordinates</li>
                <li>Postal addresses &amp; walking directions</li>
                <li>Station types, regions &amp; contact info</li>
            </ul>
            <p>What would you like to know today?</p>
        `;
    }
}

function updateQuickRepliesForUser() {
    if (!els.quickReplies) return;
    const guardRailsBtn = `<button class="quick-reply-btn guard-rails-reply" onclick="sendFromQuickReply('${GUARD_RAILS_TEST_PHRASE}')" title="Triggers Gravitee AI Guard Rails">🛡 Test Guard Rails</button>`;
    if (authConfig.userInfo && authConfig.accessToken) {
        const plan = getUserPlan();
        const isGold = plan === 'gold';
        if (isGold) {
            els.quickReplies.innerHTML = `
                <button class="quick-reply-btn gold-reply" onclick="sendFromQuickReply('When did I visit Poole lifeboat station?')">🗓 My Poole visit</button>
                <button class="quick-reply-btn gold-reply" onclick="sendFromQuickReply('What was my most recent lifeboat station visit?')">📍 Last visit</button>
                <button class="quick-reply-btn" onclick="sendFromQuickReply('Find nearest stations to Poole')">Near Poole</button>
                ${guardRailsBtn}
                <button class="quick-reply-btn record-visit-reply" onclick="openRecordVisitModal()">✏️ Log a Visit</button>
            `;
        } else {
            els.quickReplies.innerHTML = `
                <button class="quick-reply-btn silver-reply" onclick="sendFromQuickReply('Find nearest stations to Edinburgh')">📍 Near Edinburgh</button>
                <button class="quick-reply-btn silver-reply" onclick="sendFromQuickReply('Give me the address for Poole lifeboat station')">🏠 Poole address</button>
                <button class="quick-reply-btn" onclick="sendFromQuickReply('Stations in Scotland')">Scotland</button>
                ${guardRailsBtn}
            `;
        }
    } else {
        els.quickReplies.innerHTML = `
            <button class="quick-reply-btn" onclick="sendFromQuickReply('Nearest stations to Brighton')">Near Brighton</button>
            <button class="quick-reply-btn" onclick="sendFromQuickReply('What are the sea conditions near Poole?')">🌊 Sea conditions</button>
            <button class="quick-reply-btn" onclick="sendFromQuickReply('Stations in Scotland')">Scotland</button>
            ${guardRailsBtn}
        `;
    }
}

// ---------------------------------------------------------------------------
// Fetch & render visited stations
// ---------------------------------------------------------------------------

async function fetchVisitedStations() {
    if (!authEls.visitedList) return;
    if (!authConfig.accessToken) return;

    authEls.visitedList.innerHTML = `
        <div class="visited-loading">
            <div class="loading-spinner"></div>
            <p>Loading your visit history...</p>
        </div>`;

    try {
        const resp = await fetch(authConfig.visitedStationsUrl, {
            headers: { 'Authorization': `Bearer ${authConfig.accessToken}` },
        });

        if (resp.status === 401) {
            renderVisitedError('Your session has expired. Please sign in again.');
            return;
        }
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }

        const data = await resp.json();

        // Store visit history in localStorage so the LLM agent can use it
        const visits = (data.visits || []).map(v => ({
            station: v.station,
            date: v.date,
            station_type: v.station_type,
            region: v.region,
        }));
        localStorage.setItem('rnli_visit_history', JSON.stringify(visits));

        renderVisitedStations(data);
    } catch (err) {
        console.error('[Auth] Visited stations fetch failed:', err);
        // Fall back to locally stored visits if API is unavailable
        const local = localStorage.getItem('rnli_visit_history');
        if (local) {
            renderLocalVisits();
        } else {
            renderVisitedError(`Could not load visit history: ${err.message}`);
        }
    }
}

function renderVisitedStations(data) {
    if (!authEls.visitedList) return;
    const visits = data.visits || [];
    const plan = getUserPlan();
    const isGold = plan === 'gold';

    if (authEls.visitedSubtitle) {
        const planBadge = isGold
            ? `<span class="gold-plan-badge">⭐ Gold Plan</span>`
            : `<span class="silver-plan-badge">🥈 Silver Plan</span>`;
        authEls.visitedSubtitle.innerHTML =
            `Stations visited by <strong>${data.displayName || data.user || 'you'}</strong>
             &nbsp;·&nbsp;
             <span class="jwt-badge">
                 <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
                 Secured by Gravitee AM
             </span>
             &nbsp;·&nbsp;
             ${planBadge}`;
    }

    if (visits.length === 0) {
        authEls.visitedList.innerHTML = '<div class="visited-auth-notice"><p>No station visits recorded yet.</p></div>';
        return;
    }

    authEls.visitedList.innerHTML = visits.map(v => {
        const badgeClass = (v.station_type || '').toUpperCase() === 'ALB' ? 'alb' : 'ilb';
        const dateStr = v.date ? new Date(v.date).toLocaleDateString('en-GB', {day: 'numeric', month: 'long', year: 'numeric'}) : '';
        const address = v.address || '';
        const mapsUrl = v.google_maps_url || '';
        const launch = v.recent_launch;

        return `
        <div class="visited-card">
            <div class="visited-card-header">
                <div class="visited-card-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                        <circle cx="12" cy="10" r="3"></circle>
                    </svg>
                </div>
                <div>
                    <div class="visited-card-title">${v.station}</div>
                    <div class="visited-card-region">${v.region || ''}</div>
                </div>
                <span class="visited-card-badge ${badgeClass}">${v.station_type || ''}</span>
            </div>
            ${dateStr ? `<div class="visited-card-date">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                </svg>
                Visited: ${dateStr}
            </div>` : ''}
            ${address ? `<div class="visited-card-address">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle>
                </svg>
                ${address}
            </div>` : ''}
            ${mapsUrl ? `<a href="${mapsUrl}" target="_blank" rel="noopener" class="visited-card-maps-link">
                🗺️ Get walking directions
            </a>` : ''}
            ${v.notes ? `<div class="visited-card-notes">${v.notes}</div>` : ''}
            ${(launch && isGold) ? `<div class="visited-card-launch">
                <div class="launch-header">🚤 Latest Launch <span class="gold-exclusive-tag">Gold Exclusive</span></div>
                <div class="launch-date">${launch.date} · ${launch.lifeboat || ''}</div>
                <div class="launch-desc">${launch.description}</div>
                <div class="launch-outcome">✅ ${launch.outcome}</div>
            </div>` : ''}
        </div>`;
    }).join('');
}

function renderVisitedError(msg) {
    if (authEls.visitedList) {
        authEls.visitedList.innerHTML = `<div class="visited-auth-notice"><p>⚠️ ${msg}</p></div>`;
    }
}

// ---------------------------------------------------------------------------
// Record Visit — Gold member feature
// Save a visit to localStorage and refresh the visited stations display
// ---------------------------------------------------------------------------

function openRecordVisitModal() {
    // Gold-only feature — show upgrade prompt for Silver users
    if (getUserPlan() === 'silver') {
        appendMessage('agent',
            '🔒 **Gold plan required** — Logging station visits is exclusive to Gold members.\n\n' +
            'As a Silver member, you have access to station search and AI-powered location queries. ' +
            'Upgrade to Gold to unlock personal visit history, recent launch data, and AI visit recall.');
        openChat();
        return;
    }

    const modal = document.getElementById('recordVisitModal');
    if (!modal) return;
    // Pre-fill date to today
    const dateInput = document.getElementById('visitDate');
    if (dateInput && !dateInput.value) {
        dateInput.value = new Date().toISOString().split('T')[0];
    }
    // Clear previous values
    const stationInput = document.getElementById('visitStation');
    const notesInput = document.getElementById('visitNotes');
    const errorDiv = document.getElementById('recordVisitError');
    if (stationInput) stationInput.value = '';
    if (notesInput) notesInput.value = '';
    if (errorDiv) { errorDiv.style.display = 'none'; errorDiv.textContent = ''; }

    modal.classList.remove('hidden');
    setTimeout(() => stationInput?.focus(), 100);
}

function closeRecordVisitModal() {
    document.getElementById('recordVisitModal')?.classList.add('hidden');
}

function saveRecordedVisit() {
    const station = document.getElementById('visitStation')?.value?.trim();
    const date    = document.getElementById('visitDate')?.value?.trim();
    const notes   = document.getElementById('visitNotes')?.value?.trim();
    const errorDiv = document.getElementById('recordVisitError');

    if (!station) {
        if (errorDiv) { errorDiv.textContent = 'Please enter a station name.'; errorDiv.style.display = 'block'; }
        document.getElementById('visitStation')?.focus();
        return;
    }
    if (!date) {
        if (errorDiv) { errorDiv.textContent = 'Please enter a visit date.'; errorDiv.style.display = 'block'; }
        document.getElementById('visitDate')?.focus();
        return;
    }

    const visit = {
        station,
        date,
        station_type: '',  // unknown — user-recorded
        region: '',
        notes: notes || '',
    };

    const existing = JSON.parse(localStorage.getItem('rnli_visit_history') || '[]');
    existing.unshift(visit);   // newest first
    localStorage.setItem('rnli_visit_history', JSON.stringify(existing));

    closeRecordVisitModal();

    // Refresh the on-page visited stations list
    renderLocalVisits();

    // Show a brief confirmation in the chat if it's open
    if (!els.chatWindow?.classList.contains('hidden')) {
        appendMessage('agent', `✅ Visit to **${station}** on ${new Date(date + 'T00:00:00').toLocaleDateString('en-GB', {day:'numeric', month:'long', year:'numeric'})} has been recorded! I'll remember this for our conversations.`);
    }
}

/**
 * Render the visited stations section purely from localStorage
 * (used when a Gold user logs a visit manually without a server refresh).
 */
function renderLocalVisits() {
    if (!authEls.visitedSection || !authEls.visitedList) return;
    const plan = getUserPlan();
    if (plan !== 'gold') return;

    authEls.visitedSection.classList.remove('hidden');

    const visits = JSON.parse(localStorage.getItem('rnli_visit_history') || '[]');

    if (authEls.visitedSubtitle) {
        const name = authConfig.userInfo?.given_name || 'you';
        authEls.visitedSubtitle.innerHTML =
            `Stations visited by <strong>${name}</strong>
             &nbsp;·&nbsp;
             <span class="jwt-badge">
                 <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>
                 Secured by Gravitee AM
             </span>
             &nbsp;·&nbsp;
             <span class="gold-plan-badge">⭐ Gold Plan</span>`;
    }

    if (visits.length === 0) {
        authEls.visitedList.innerHTML = '<div class="visited-auth-notice"><p>No station visits recorded yet. Click <strong>Log a Visit</strong> to add your first check-in!</p></div>';
        return;
    }

    authEls.visitedList.innerHTML = visits.map(v => {
        const badgeClass = (v.station_type || '').toUpperCase() === 'ALB' ? 'alb' : 'ilb';
        const typeBadge  = v.station_type || '';
        const dateStr    = v.date ? new Date(v.date + 'T00:00:00').toLocaleDateString('en-GB', {day:'numeric', month:'long', year:'numeric'}) : '';
        return `
        <div class="visited-card">
            <div class="visited-card-header">
                <div class="visited-card-icon">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                        <circle cx="12" cy="10" r="3"></circle>
                    </svg>
                </div>
                <div>
                    <div class="visited-card-title">${v.station}</div>
                    <div class="visited-card-region">${v.region || ''}</div>
                </div>
                ${typeBadge ? `<span class="visited-card-badge ${badgeClass}">${typeBadge}</span>` : ''}
            </div>
            ${dateStr ? `<div class="visited-card-date">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                </svg>
                Visited: ${dateStr}
            </div>` : ''}
            ${v.notes ? `<div class="visited-card-notes">${v.notes}</div>` : ''}
        </div>`;
    }).join('');
}

// Wire up the Record Visit modal buttons
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('closeRecordVisitBtn')?.addEventListener('click', closeRecordVisitModal);
    document.getElementById('cancelRecordVisitBtn')?.addEventListener('click', closeRecordVisitModal);
    document.getElementById('recordVisitOverlay')?.addEventListener('click', closeRecordVisitModal);
    document.getElementById('saveRecordVisitBtn')?.addEventListener('click', saveRecordedVisit);

    // Close on Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeRecordVisitModal();
    });
});

// ---------------------------------------------------------------------------
// A2A Agent Mesh — fetch agent cards and render registry
// ---------------------------------------------------------------------------

const AGENT_CARDS = [
    {
        url: 'http://localhost:8082/stations-agent/.well-known/agent-card.json',
        role: 'Agent 1',
        colour: '#7C3AED',
        icon: '🤖',
        calledBy: 'Client (website)',
        calls: 'Sea Conditions Agent via A2A',
    },
    {
        url: 'http://localhost:8082/weather-agent/.well-known/agent-card.json',
        role: 'Agent 2',
        colour: '#0891B2',
        icon: '🌊',
        calledBy: 'Station Finder Agent via A2A',
        calls: 'Open-Meteo (marine + forecast APIs)',
    },
];

async function loadAgentMesh() {
    const grid = document.getElementById('agentMeshGrid');
    if (!grid) return;

    const cards = await Promise.all(AGENT_CARDS.map(async (meta) => {
        try {
            const r = await fetch(meta.url);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const card = await r.json();
            return { meta, card, ok: true };
        } catch {
            return { meta, card: null, ok: false };
        }
    }));

    grid.innerHTML = cards.map(({ meta, card, ok }) => {
        const name = ok ? card.name : meta.role;
        const desc = ok ? (card.description || '').slice(0, 140) : 'Agent unavailable';
        const version = ok && card.version ? `v${card.version}` : '';
        const transport = ok && card.preferredTransport ? card.preferredTransport : 'A2A';
        const skills = ok && card.skills ? card.skills.map(s =>
            `<span class="agent-skill-tag">${s.name}</span>`).join('') : '';

        return `
        <div class="agent-card-tile" style="--agent-colour:${meta.colour}">
            <div class="agent-card-header">
                <span class="agent-card-icon">${meta.icon}</span>
                <div>
                    <div class="agent-card-role">${meta.role}</div>
                    <div class="agent-card-name">${name}</div>
                </div>
                <span class="agent-card-badge ${ok ? 'badge-live' : 'badge-offline'}">${ok ? '● Live' : '● Offline'}</span>
            </div>
            <p class="agent-card-desc">${desc}${desc.length === 140 ? '…' : ''}</p>
            <div class="agent-card-meta">
                <div class="agent-meta-row"><span class="agent-meta-label">Protocol</span><span class="agent-meta-val">${transport} ${version}</span></div>
                <div class="agent-meta-row"><span class="agent-meta-label">Called by</span><span class="agent-meta-val">${meta.calledBy}</span></div>
                <div class="agent-meta-row"><span class="agent-meta-label">Calls</span><span class="agent-meta-val">${meta.calls}</span></div>
            </div>
            ${skills ? `<div class="agent-skills">${skills}</div>` : ''}
            <a class="agent-card-link" href="${meta.url}" target="_blank" rel="noopener">View agent card ↗</a>
        </div>`;
    }).join('');
}
