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
    } else {
        els.chatWindow?.classList.remove('maximized');
    }
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

    // Hide quick replies after first send
    if (els.quickReplies) els.quickReplies.style.display = 'none';

    appendMessage('user', text);
    showTypingIndicator();
    isTyping = true;
    setInputEnabled(false);

    try {
        const { text: response, elapsedMs } = await callAgent(text);
        hideTypingIndicator();
        appendMessage('agent', response, { elapsedMs });
        // Increment Silver rate limit counter on successful response
        if (getUserPlan() === 'silver') {
            rateLimitCount++;
            updateRateLimitBar();
        }
    } catch (err) {
        hideTypingIndicator();
        console.error('[RNLI Agent] Error:', err);
        if (err.type === 'guard-rails') {
            appendGuardRailsBlock();
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
    content.innerHTML = formatMessageText(text);

    wrapper.appendChild(avatar);
    wrapper.appendChild(content);

    // Timing chip — shown on agent responses when elapsed time is available
    if (role === 'agent' && meta.elapsedMs != null) {
        const isFast = meta.elapsedMs < 500;
        const timeStr = meta.elapsedMs < 1000
            ? `${meta.elapsedMs}ms`
            : `${(meta.elapsedMs / 1000).toFixed(1)}s`;
        const chip = document.createElement('div');
        chip.className = 'response-timing';
        chip.innerHTML = `<span class="timing-chip${isFast ? ' timing-fast' : ''}">${isFast ? '⚡ Cache hit · ' : ''}${timeStr}</span>`;
        wrapper.appendChild(chip);
    }

    els.chatMessages?.appendChild(wrapper);
    scrollToBottom();
}

// Guard rails blocked — styled inline message
function appendGuardRailsBlock() {
    const wrapper = document.createElement('div');
    wrapper.className = 'message agent-message';

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar guard-rails-avatar';
    avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>`;

    const content = document.createElement('div');
    content.className = 'message-content guard-rails-content';
    content.innerHTML = `
        <p><strong>Request blocked by Gravitee AI Guard Rails</strong></p>
        <p>Your message was flagged by the content safety policy and was not forwarded to the AI. Gravitee Gateway is protecting the API in real time.</p>
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
            els.rateLimitLabel.textContent = 'Rate limit reached — wait a moment';
            els.rateLimitLabel.className = 'rate-limit-label label-danger';
        } else {
            const warn = used >= limit - 1;
            els.rateLimitLabel.textContent = `${remaining} request${remaining !== 1 ? 's' : ''} remaining (Silver plan)`;
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
                plan: ui.plan || null,
            };
        }

        localStorage.setItem('rnli_access_token', authConfig.accessToken);
        if (idToken) localStorage.setItem('rnli_id_token', idToken);
        if (authConfig.userInfo) localStorage.setItem('rnli_user_info', JSON.stringify(authConfig.userInfo));

        sessionStorage.removeItem('rnli_code_verifier');
        sessionStorage.removeItem('rnli_auth_state');
        window.history.replaceState({}, document.title, window.location.pathname);

        updateUserDisplay();
        warmUpAI();
        // Visit history is a Gold-exclusive feature
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
        updateUserDisplay();
        warmUpAI();

        if (!authConfig.oidcConfig) {
            try {
                const r = await fetch(authConfig.oidcUrl);
                if (r.ok) authConfig.oidcConfig = await r.json();
            } catch (_) {}
        }

        // Visit history is a Gold-exclusive feature
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
 * Determine the current user's tier.
 * Reads `plan` from the userInfo response (set as an AM additionalInformation claim).
 * Falls back to email lookup for demo robustness.
 */
function getUserPlan() {
    if (!authConfig.userInfo) return null;
    if (authConfig.userInfo.plan) return authConfig.userInfo.plan;
    // Fallback: derive from known demo accounts
    const email = authConfig.userInfo.email || '';
    if (email === 'joe.doe@gravitee.io') return 'gold';
    if (email === 'silver.user@rnli.org') return 'silver';
    return 'silver'; // default for authenticated users
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

        // Show visited section for Gold members only (personal visit history is a Gold-exclusive feature)
        if (isGold) {
            authEls.visitedSection?.classList.remove('hidden');
        } else {
            authEls.visitedSection?.classList.add('hidden');
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
            <button class="quick-reply-btn" onclick="sendFromQuickReply('Stations in Scotland')">Scotland</button>
            <button class="quick-reply-btn" onclick="sendFromQuickReply('ALB stations in Wales')">Wales ALBs</button>
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
