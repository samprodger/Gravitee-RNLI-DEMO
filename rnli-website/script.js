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
    toggleChatSizeBtn:   document.getElementById('toggleChatSizeBtn'),
    chatMessages:        document.getElementById('chatMessages'),
    chatInput:           document.getElementById('chatInput'),
    sendBtn:             document.getElementById('sendBtn'),
    chatStatus:          document.getElementById('chatStatus'),
    quickReplies:        document.getElementById('quickReplies'),

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
    // We derive the JSON-RPC endpoint from the agent card URL rather than
    // agentCard.url, because agentCard.url contains the direct service address
    // (e.g. http://localhost:8003) which is cross-origin without CORS headers.
    // Instead we use the gateway base URL (the agentCardUrl minus the
    // /.well-known/agent-card.json suffix) so all requests go through
    // the Gravitee gateway which has CORS enabled.
    const cardUrl = config.agentCardUrl;
    if (cardUrl) {
        return cardUrl.replace('/.well-known/agent-card.json', '');
    }
    // Fallback to agentCard.url if no agentCardUrl configured
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
        const response = await callAgent(text);
        hideTypingIndicator();
        appendMessage('agent', response);
    } catch (err) {
        hideTypingIndicator();
        console.error('[RNLI Agent] Error:', err);
        appendMessage('agent', 'Sorry, I encountered an error. Please try again in a moment.');
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
// A2A JSON-RPC call
// ---------------------------------------------------------------------------

async function callAgent(userMessage) {
    const requestBody = {
        jsonrpc: '2.0',
        id: generateId(),
        method: 'message/send',
        params: {
            message: {
                messageId: generateId(),
                role: 'user',
                parts: [{ type: 'text', text: userMessage }],
            },
            contextId: contextId,
        },
    };

    const resp = await fetch(config.agentUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
    });

    if (!resp.ok) {
        throw new Error(`Agent returned HTTP ${resp.status}`);
    }

    const data = await resp.json();

    if (data.error) {
        throw new Error(data.error.message || JSON.stringify(data.error));
    }

    // Parse the A2A response — result can be a Message or Task
    return extractTextFromResult(data.result);
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

    // Fallback: stringify
    if (typeof result === 'string') return result;
    return JSON.stringify(result, null, 2);
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function appendMessage(role, text) {
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

    // Convert plain newlines to paragraphs / preserve markdown-ish formatting
    const formatted = formatMessageText(text);
    content.innerHTML = formatted;

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

function setInputEnabled(enabled) {
    if (els.chatInput) els.chatInput.disabled = !enabled;
    if (els.sendBtn) els.sendBtn.disabled = !enabled;
}

function generateId() {
    return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
}
