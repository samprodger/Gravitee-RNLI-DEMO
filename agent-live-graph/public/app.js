/**
 * Gravitee AI Agent Inspector — App
 *
 * Dual-mode sequence diagram:
 *   LIVE  — real events from the Gravitee Gateway TCP reporter via WebSocket
 *   DEMO  — scripted educational scenario (hardcoded steps)
 *
 * Both modes share the same grid-based rendering engine.
 * Light theme · No emojis · Click-to-popup details · Policy blocks on gateway lane
 */
(() => {
  'use strict';

  /* ── Lane geometry ──────────────────────────────────────── */
  const LANES = { client: 0, agent: 1, gateway: 2, llm: 3, api: 4 };
  const centerPct = (idx) => (idx * 20) + 10;
  const LANE_COLORS = {
    client:  '#6B7280',
    agent:   '#7C3AED',
    gateway: '#0284C7',
    llm:     '#EA580C',
    api:     '#059669',
  };
  const laneColor = (l) => LANE_COLORS[l] || '#666';

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function formatJson(s) {
    if (!s) return '';
    try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
  }

  /* ── DOM refs ───────────────────────────────────────────── */
  const stepsEl      = document.getElementById('stepsContainer');
  const graphArea    = document.getElementById('graphArea');
  const laneHeaders  = document.getElementById('laneHeaders');
  const modeToggle   = document.getElementById('modeToggle');
  const wsIndicator  = document.getElementById('wsIndicator');
  const liveStats    = document.getElementById('liveStats');
  const eventCountEl = document.getElementById('eventCount');
  const stepCounter  = document.getElementById('stepCounter');
  const stepNumEl    = document.getElementById('stepNum');
  const stepTotalEl  = document.getElementById('stepTotal');
  const analyticsPanel   = document.getElementById('analyticsPanel');
  const progressEl   = document.getElementById('progressFill');
  const livePulse    = document.getElementById('livePulse');
  const liveLabel    = document.querySelector('.live-label');

  const liveControls = document.getElementById('liveControls');
  const demoControls = document.getElementById('demoControls');
  const btnClear     = document.getElementById('btnClear');
  const btnPlay      = document.getElementById('btnPlay');
  const btnStep      = document.getElementById('btnStep');
  const btnReset     = document.getElementById('btnReset');
  const speedRange   = document.getElementById('speedRange');
  const speedLabelEl = document.getElementById('speedLabel');
  const playIcon     = document.getElementById('playIcon');
  const pauseIcon    = document.getElementById('pauseIcon');
  const detailModal  = document.getElementById('detailModal');
  const modalTitle   = document.getElementById('modalTitle');
  const modalBody    = document.getElementById('modalBody');
  const modalClose   = document.getElementById('modalClose');
  const scenarioPicker = document.getElementById('scenarioPicker');
  const scenarioSelect = document.getElementById('scenarioSelect');

  /* ── State ──────────────────────────────────────────────── */
  let mode         = 'live';
  let ws           = null;
  let liveCount    = 0;
  let demoPlaying  = false;
  let demoSpeed    = 1;
  let demoCursor   = 0;
  let demoTimer    = null;
  let currentGroup    = null;  // tracks current <details> group body for arrows
  let currentFlowBody = null;  // tracks current flow wrapper body

  /* ── Analytics state (accumulated from live WS events) ─── */
  const analyticsStats = {
    total: 0, gold: 0, silver: 0, unknown: 0,
    blocked: 0, cached: 0, rateLimited: 0,
    responseTimes: [],   // rolling last-100 response times (ms)
  };
  const analyticsTimeline = [];  // max 20 recent requests

  /* ════════════════════════════════════════════════════════════
   * SHARED RENDERING ENGINE
   * ════════════════════════════════════════════════════════════ */

  function renderStep(step) {
    if (step.type === 'divider') return renderDivider(step);
    if (step.type === 'arrow')   return renderArrow(step);
    return document.createElement('div');
  }

  /* ── Divider ────────────────────────────────────────────── */
  function renderDivider(step) {
    const el = document.createElement('div');
    el.className = 'step-divider';
    el.innerHTML = `
      <div class="divider-inner">
        <div class="divider-line"></div>
        <span class="divider-label">
          <svg class="divider-chevron" width="10" height="10" viewBox="0 0 10 10">
            <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          ${escapeHtml(step.label)}
        </span>
        <div class="divider-line"></div>
      </div>`;

    return el;
  }

  /* ── Arrow (grid-based, no overlapping) ─────────────────── */
  function renderArrow(step) {
    const row = document.createElement('div');
    row.className = 'step-row';

    const fi = LANES[step.from];
    const ti = LANES[step.to];

    /* ── Arrow zone (horizontal arrow with arrowhead + label + particle) ── */
    const arrowZone = document.createElement('div');
    arrowZone.className = 'arrow-zone';

    if (fi !== ti) {
      const fromC  = centerPct(fi);
      const toC    = centerPct(ti);
      const minC   = Math.min(fromC, toC);
      const maxC   = Math.max(fromC, toC);
      const goRight = ti > fi;
      const fColor = laneColor(step.from);
      const tColor = laneColor(step.to);

      const arrow = document.createElement('div');
      arrow.className = `step-arrow ${goRight ? 'dir-right' : 'dir-left'}`;
      arrow.style.cssText = `left:${minC}%;width:${maxC - minC}%;--arrow-from:${fColor};--arrow-to:${tColor}`;

      // Label above the arrow
      if (step.label) {
        const lbl = document.createElement('span');
        lbl.className = 'arrow-label';
        lbl.textContent = step.label;
        arrow.appendChild(lbl);
      }

      // Latency chip below the label (optional — shows ms for demo scenarios)
      if (step.latency) {
        const lat = document.createElement('span');
        lat.className = `latency-chip latency-${step.latency.type || 'normal'}`;
        lat.textContent = step.latency.text;
        arrow.appendChild(lat);
      }

      // Animated particle
      const particle = document.createElement('div');
      particle.className = 'arrow-particle';
      arrow.appendChild(particle);

      arrowZone.appendChild(arrow);
    }

    row.appendChild(arrowZone);

    /* ── Content zone (5-column grid — message cards, policies, badges) ── */
    const contentZone = document.createElement('div');
    contentZone.className = 'content-zone';

    for (let i = 0; i < 5; i++) {
      const col = document.createElement('div');
      col.className = 'lane-col';

      // Message card — placed in the target lane column
      if (step.message && LANES[step.message.lane] === i) {
        col.appendChild(createCard(step.message));
      }

      // Gateway column — policies, plan, badge
      if (i === LANES.gateway) {
        // Policy blocks (request arrows through gateway)
        if (step.policies && step.policies.length) {
          const pg = document.createElement('div');
          pg.className = 'policy-group';
          for (const p of step.policies) {
            const pb = document.createElement('div');
            // Support both old string format and new {name, passed} format
            const pName = typeof p === 'string' ? p : p.name;
            const pPassed = typeof p === 'string' ? true : p.passed;
            pb.className = `policy-block ${pPassed ? 'policy-pass' : 'policy-fail'}`;
            pb.innerHTML = `<i class="ph${pPassed ? '' : '-fill'} ${pPassed ? 'ph-check-circle' : 'ph-x-circle'}"></i><span>${escapeHtml(pName)}</span>`;
            pg.appendChild(pb);
          }
          col.appendChild(pg);
        }

        // Plan tag
        if (step.plan) {
          const pt = document.createElement('div');
          pt.className = 'plan-tag';
          pt.textContent = step.plan + ' Plan';
          col.appendChild(pt);
        }

        // Badge (response arrows — latency/status info)
        if (step.badge) {
          const bg = document.createElement('div');
          bg.className = `badge badge-${step.badge.type}`;
          bg.textContent = step.badge.text;
          col.appendChild(bg);
        }
      }

      contentZone.appendChild(col);
    }

    row.appendChild(contentZone);
    return row;
  }

  /* ── Message card ───────────────────────────────────────── */
  function createCard(msg) {
    const card = document.createElement('div');
    card.className = `msg-card msg-${msg.lane}`;

    const text = document.createElement('div');
    text.className = 'msg-text';
    text.textContent = msg.text;
    card.appendChild(text);

    // Tool list — render as a proper bullet list
    if (msg.toolList && msg.toolList.length) {
      const ul = document.createElement('ul');
      ul.className = 'tool-list';
      for (const t of msg.toolList) {
        const li = document.createElement('li');
        li.innerHTML = `<i class="ph ph-wrench"></i><span>${escapeHtml(t)}</span>`;
        ul.appendChild(li);
      }
      card.appendChild(ul);
    }

    // Tool call — render as function name + formatted args
    if (msg.toolCall) {
      const tc = document.createElement('div');
      tc.className = 'tool-call';
      tc.innerHTML = `<span class="tc-name"><i class="ph ph-function"></i>${escapeHtml(msg.toolCall.name)}</span>`;
      if (msg.toolCall.args && Object.keys(msg.toolCall.args).length) {
        const argsEl = document.createElement('div');
        argsEl.className = 'tc-args';
        for (const [k, v] of Object.entries(msg.toolCall.args)) {
          const row = document.createElement('div');
          row.className = 'tc-arg';
          row.innerHTML = `<span class="tc-arg-key">${escapeHtml(k)}</span><span class="tc-arg-val">${escapeHtml(String(v))}</span>`;
          argsEl.appendChild(row);
        }
        tc.appendChild(argsEl);
      }
      card.appendChild(tc);
    }

    if (msg.rawDetail) {
      const hint = document.createElement('div');
      hint.className = 'msg-hint';
      hint.innerHTML = `<i class="ph ph-magnifying-glass"></i> Click for details`;
      card.appendChild(hint);

      card.setAttribute('data-clickable', 'true');
      card.addEventListener('click', (e) => {
        e.stopPropagation();
        openModal(msg.text, msg.rawDetail);
      });
    }

    return card;
  }

  /* ── Detail Modal (tabbed: Pretty + Raw) ─────────────────── */
  function openModal(title, raw) {
    modalTitle.textContent = title;

    const parsed = tryParseJSON(raw);
    const prettyHtml = parsed ? renderPrettyView(parsed) : `<p class="pretty-fallback">Unable to parse as JSON</p>`;
    const rawHtml = `<pre>${escapeHtml(formatJson(raw))}</pre>`;

    modalBody.innerHTML = `
      <div class="modal-tabs">
        <button class="modal-tab active" data-tab="pretty">Pretty</button>
        <button class="modal-tab" data-tab="raw">Raw</button>
      </div>
      <div class="modal-tab-content active" data-panel="pretty">${prettyHtml}</div>
      <div class="modal-tab-content" data-panel="raw">${rawHtml}</div>`;

    modalBody.querySelectorAll('.modal-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        modalBody.querySelectorAll('.modal-tab').forEach(b => b.classList.remove('active'));
        modalBody.querySelectorAll('.modal-tab-content').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        modalBody.querySelector(`[data-panel="${btn.dataset.tab}"]`).classList.add('active');
      });
    });

    detailModal.classList.add('open');
  }

  function tryParseJSON(s) {
    try { return JSON.parse(s); } catch { return null; }
  }

  /* ── Pretty view — type-aware rendering ──────────────────── */
  function renderPrettyView(obj) {
    // LLM Request: model + messages
    if (obj.model && obj.messages) {
      const tools = obj.tools || [];
      let html = `<div class="pv-section"><span class="pv-label">Model</span><span class="pv-value">${escapeHtml(obj.model)}</span></div>`;
      html += `<div class="pv-section"><span class="pv-label">Messages</span><span class="pv-value">${obj.messages.length}</span></div>`;
      if (tools.length) {
        html += `<div class="pv-section"><span class="pv-label">Tool definitions</span><span class="pv-value">${tools.length}</span></div>`;
        html += `<ul class="pv-list">${tools.map(t => `<li>${escapeHtml(t.function ? t.function.name : t.name || '?')}</li>`).join('')}</ul>`;
      }
      if (obj.messages.length) {
        html += `<div class="pv-subsection"><span class="pv-label">Messages</span></div>`;
        html += obj.messages.map(m => {
          const role = m.role || '?';
          const content = typeof m.content === 'string' ? m.content.slice(0, 200) : (m.content ? JSON.stringify(m.content).slice(0, 200) : '');
          return `<div class="pv-msg"><span class="pv-msg-role pv-role-${escapeHtml(role)}">${escapeHtml(role)}</span><span class="pv-msg-content">${escapeHtml(content)}${content.length >= 200 ? '...' : ''}</span></div>`;
        }).join('');
      }
      return html;
    }

    // LLM Response: choices array
    if (obj.choices && Array.isArray(obj.choices)) {
      const msg = (obj.choices[0] || {}).message || {};
      const toolCalls = msg.tool_calls || [];
      let html = '';
      if (toolCalls.length) {
        html += `<div class="pv-section"><span class="pv-label">Tool calls</span><span class="pv-value">${toolCalls.length}</span></div>`;
        html += `<ul class="pv-list">${toolCalls.map(tc => {
          const fn = tc.function || {};
          return `<li><strong>${escapeHtml(fn.name || '?')}</strong>${fn.arguments ? `<pre class="pv-inline-pre">${escapeHtml(typeof fn.arguments === 'string' ? fn.arguments : JSON.stringify(fn.arguments, null, 2))}</pre>` : ''}</li>`;
        }).join('')}</ul>`;
      } else if (msg.content) {
        html += `<div class="pv-section"><span class="pv-label">Content</span></div>`;
        html += `<div class="pv-markdown">${renderMarkdown(msg.content)}</div>`;
      }
      if (obj.usage) {
        html += `<div class="pv-section"><span class="pv-label">Tokens</span><span class="pv-value">${obj.usage.total_tokens || '?'} total (${obj.usage.prompt_tokens || '?'} prompt + ${obj.usage.completion_tokens || '?'} completion)</span></div>`;
      }
      return html || renderGenericView(obj);
    }

    // MCP Request: method = tools/*
    if (obj.method && typeof obj.method === 'string' && obj.method.startsWith('tools/')) {
      let html = `<div class="pv-section"><span class="pv-label">Method</span><span class="pv-value">${escapeHtml(obj.method)}</span></div>`;
      if (obj.params && obj.params.name) {
        html += `<div class="pv-section"><span class="pv-label">Tool</span><span class="pv-value">${escapeHtml(obj.params.name)}</span></div>`;
      }
      if (obj.params && obj.params.arguments && Object.keys(obj.params.arguments).length) {
        html += `<div class="pv-section"><span class="pv-label">Arguments</span></div>`;
        html += `<pre class="pv-pre">${escapeHtml(JSON.stringify(obj.params.arguments, null, 2))}</pre>`;
      }
      return html;
    }

    // MCP Response: result.tools or result.content
    if (obj.result && (obj.result.tools || obj.result.content)) {
      let html = '';
      if (obj.result.tools) {
        html += `<div class="pv-section"><span class="pv-label">Discovered tools</span><span class="pv-value">${obj.result.tools.length}</span></div>`;
        html += `<ul class="pv-list">${obj.result.tools.map(t => `<li><strong>${escapeHtml(t.name)}</strong>${t.description ? ` — ${escapeHtml(t.description)}` : ''}</li>`).join('')}</ul>`;
      }
      if (obj.result.content) {
        html += `<div class="pv-section"><span class="pv-label">Result content</span></div>`;
        const texts = (obj.result.content || []).filter(c => c.type === 'text');
        for (const t of texts) {
          html += `<pre class="pv-pre">${escapeHtml(t.text.slice(0, 2000))}${t.text.length > 2000 ? '...' : ''}</pre>`;
        }
      }
      return html || renderGenericView(obj);
    }

    // A2A Request: params.message
    if (obj.params && obj.params.message) {
      let html = '';
      if (obj.method) html += `<div class="pv-section"><span class="pv-label">Method</span><span class="pv-value">${escapeHtml(obj.method)}</span></div>`;
      const parts = (obj.params.message.parts || []).filter(p => p.text);
      if (parts.length) {
        html += `<div class="pv-section"><span class="pv-label">User message</span></div>`;
        html += `<div class="pv-text">${escapeHtml(parts.map(p => p.text).join('\n'))}</div>`;
      }
      return html || renderGenericView(obj);
    }

    // A2A Response: result.parts / result.artifacts / result.status
    if (obj.result && (obj.result.parts || obj.result.artifacts || obj.result.status)) {
      let html = '';
      if (obj.result.parts) {
        const texts = obj.result.parts.filter(p => p.text);
        if (texts.length) {
          html += `<div class="pv-section"><span class="pv-label">Agent response</span></div>`;
          html += `<div class="pv-markdown">${renderMarkdown(texts.map(p => p.text).join('\n'))}</div>`;
        }
      }
      if (obj.result.artifacts) {
        for (const art of obj.result.artifacts) {
          const texts = (art.parts || []).filter(p => p.text);
          if (texts.length) {
            html += `<div class="pv-section"><span class="pv-label">Artifact</span></div>`;
            html += `<div class="pv-markdown">${renderMarkdown(texts.map(p => p.text).join('\n'))}</div>`;
          }
        }
      }
      if (obj.result.status && obj.result.status.message && obj.result.status.message.parts) {
        const texts = obj.result.status.message.parts.filter(p => p.text);
        if (texts.length) {
          html += `<div class="pv-section"><span class="pv-label">Status message</span></div>`;
          html += `<div class="pv-markdown">${renderMarkdown(texts.map(p => p.text).join('\n'))}</div>`;
        }
      }
      return html || renderGenericView(obj);
    }

    return renderGenericView(obj);
  }

  /* ── Lightweight Markdown → HTML ───────────────────────── */
  function renderMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    // Code blocks (```)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="pv-pre">$2</pre>');
    // Headers
    html = html.replace(/^### (.+)$/gm, '<strong class="md-h3">$1</strong>');
    html = html.replace(/^## (.+)$/gm, '<strong class="md-h2">$1</strong>');
    html = html.replace(/^# (.+)$/gm, '<strong class="md-h1">$1</strong>');
    // Bold + italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
    // List items (- or *)
    html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul class="md-list">$1</ul>');
    // Numbered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    // Paragraphs (double newline)
    html = html.replace(/\n\n/g, '</p><p>');
    // Single newlines → <br>
    html = html.replace(/\n/g, '<br>');
    return '<p>' + html + '</p>';
  }

  function renderGenericView(obj) {
    let html = '';
    for (const [key, val] of Object.entries(obj)) {
      const display = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
      html += `<div class="pv-section"><span class="pv-label">${escapeHtml(key)}</span></div>`;
      if (typeof val === 'object' && val !== null) {
        html += `<pre class="pv-pre">${escapeHtml(display)}</pre>`;
      } else {
        html += `<span class="pv-value">${escapeHtml(display)}</span>`;
      }
    }
    return html;
  }

  function closeModal() { detailModal.classList.remove('open'); }

  modalClose.addEventListener('click', closeModal);
  detailModal.addEventListener('click', (e) => {
    if (e.target === detailModal) closeModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
  });

  /* ── Scroll helper ──────────────────────────────────────── */
  function scrollToBottom() {
    graphArea.scrollTo({ top: graphArea.scrollHeight, behavior: 'smooth' });
  }

  /* ── Foldable group helper ─────────────────────────────── */
  function resetGroupState() { currentGroup = null; currentFlowBody = null; }

  /* ── Flow-level wrapper (collapsible per request) ─────── */
  function extractFlowSummary(steps) {
    let userText = '', totalTime = '', status = 'ok';
    const phases = [];
    for (const step of steps) {
      if (step.type === 'divider' && step.label !== 'User Request' && step.label !== 'Agent Response') {
        phases.push(step.label);
      }
      if (step.type === 'arrow') {
        if (step.from === 'client' && step.to === 'gateway' && step.message && !userText) {
          userText = step.message.text || '';
        }
        if (step.from === 'gateway' && step.to === 'client' && step.badge) {
          totalTime = step.badge.text || '';
          status = step.badge.type || 'ok';
        }
      }
    }
    return { userText, totalTime, status, phases };
  }

  function createFlowWrapper(summary) {
    const details = document.createElement('details');
    details.className = 'flow-wrapper';
    details.open = true;

    const s = document.createElement('summary');
    s.className = 'flow-summary';

    const statusClass = `flow-status-${summary.status || 'ok'}`;
    const phasesStr = summary.phases.join(' \u00b7 '); // middle dot

    s.innerHTML = `
      <svg class="flow-chevron" width="12" height="12" viewBox="0 0 10 10">
        <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="flow-status ${statusClass}"></span>
      <span class="flow-user-text">${escapeHtml(summary.userText || 'Request')}</span>
      ${phasesStr ? `<span class="flow-phases">${escapeHtml(phasesStr)}</span>` : ''}
      ${summary.totalTime ? `<span class="flow-time">${escapeHtml(summary.totalTime)}</span>` : ''}`;

    details.appendChild(s);

    const body = document.createElement('div');
    body.className = 'flow-body';
    details.appendChild(body);

    return details;
  }

  function appendStepToDOM(step, container) {
    const el = renderStep(step);

    if (step.type === 'divider') {
      // Create a new collapsible group
      const details = document.createElement('details');
      details.className = 'step-group';
      details.open = true;

      const summary = document.createElement('summary');
      summary.className = 'step-group-summary';
      summary.appendChild(el);
      details.appendChild(summary);

      const body = document.createElement('div');
      body.className = 'step-group-body';
      details.appendChild(body);

      container.appendChild(details);
      currentGroup = body;

      requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('visible')));
      return el;
    }

    // arrow → append to current group body (or container if no group)
    const target = currentGroup || container;
    target.appendChild(el);
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('visible')));
    return el;
  }

  /* ════════════════════════════════════════════════════════════
   * LIVE MODE — WebSocket consumer
   * ════════════════════════════════════════════════════════════ */

  function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}`);

    ws.onopen = () => {
      wsIndicator.classList.add('connected');
      wsIndicator.title = 'Connected to gateway stream';
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'live-steps' && mode === 'live') {
          onLiveSteps(msg);
        } else if (msg.type === 'tx-progress' && mode === 'live') {
          onTxProgress(msg);
        }
      } catch (_) { /* ignore */ }
    };

    ws.onclose = () => {
      wsIndicator.classList.remove('connected');
      setTimeout(connectWS, 2000);
    };

    ws.onerror = () => ws.close();
  }

  /* ── Single progress indicator ──────────────────────────── */
  let progressEl_live = null;        // reference to the single progress DOM node

  function onTxProgress(msg) {
    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();

    if (!progressEl_live) {
      progressEl_live = document.createElement('div');
      progressEl_live.className = 'tx-progress';
      progressEl_live.innerHTML = `
        <div class="txp-spinner"></div>
        <div class="txp-body">
          <span class="txp-label">Collecting gateway events</span>
          <span class="txp-count">${msg.buffered}</span>
        </div>`;
      stepsEl.appendChild(progressEl_live);
      requestAnimationFrame(() =>
        requestAnimationFrame(() => progressEl_live && progressEl_live.classList.add('visible')));
    } else {
      progressEl_live.querySelector('.txp-count').textContent = msg.buffered;
    }

    livePulse.classList.add('active');
    liveLabel.textContent = 'Buffering events...';
    liveLabel.classList.add('has-events');
    scrollToBottom();
  }

  function removeProgressIndicator() {
    if (progressEl_live) {
      progressEl_live.remove();
      progressEl_live = null;
    }
  }

  /* ── Staggered rendering queue for transaction batches ───── */
  let liveQueue      = [];
  let liveQueueTimer = null;
  const LIVE_DELAY_DIVIDER = 200;
  const LIVE_DELAY_ARROW   = 350;

  function drainLiveQueue() {
    if (!liveQueue.length) { liveQueueTimer = null; return; }

    const step = liveQueue.shift();

    // Flow markers — create/close wrapper, then process next immediately
    if (step.type === 'flow-start') {
      const wrapper = createFlowWrapper(step.summary);
      stepsEl.appendChild(wrapper);
      currentFlowBody = wrapper.querySelector('.flow-body');
      currentGroup = null;
      requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add('visible')));
      drainLiveQueue();
      return;
    }
    if (step.type === 'flow-end') {
      currentFlowBody = null;
      currentGroup = null;
      drainLiveQueue();
      return;
    }

    const container = currentFlowBody || stepsEl;
    const el = appendStepToDOM(step, container);
    el.classList.add('flash');

    liveCount++;
    eventCountEl.textContent = liveCount;
    scrollToBottom();

    if (liveQueue.length) {
      const next  = liveQueue[0];
      const delay = (next.type === 'flow-start' || next.type === 'flow-end') ? 0
                  : next.type === 'divider' ? LIVE_DELAY_DIVIDER : LIVE_DELAY_ARROW;
      liveQueueTimer = setTimeout(drainLiveQueue, delay);
    } else {
      liveQueueTimer = null;
    }
  }

  function onLiveSteps(msg) {
    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();

    removeProgressIndicator();

    const steps = msg.steps || [];
    const summary = extractFlowSummary(steps);

    // Always accumulate analytics regardless of current mode
    updateAnalyticsFromFlow(msg, steps, summary);

    liveQueue.push({ type: 'flow-start', summary });
    liveQueue.push(...steps);
    liveQueue.push({ type: 'flow-end' });

    livePulse.classList.add('active');
    liveLabel.textContent = `${msg.apiName || 'Event'} — ${new Date(msg.timestamp).toLocaleTimeString()}`;
    liveLabel.classList.add('has-events');

    if (!liveQueueTimer) drainLiveQueue();
  }

  function clearLive() {
    if (liveQueueTimer) { clearTimeout(liveQueueTimer); liveQueueTimer = null; }
    liveQueue = [];
    removeProgressIndicator();
    stepsEl.innerHTML = '';
    resetGroupState();
    liveCount = 0;
    eventCountEl.textContent = '0';
    livePulse.classList.remove('active');
    liveLabel.textContent = 'Waiting for gateway events...';
    liveLabel.classList.remove('has-events');
    showLiveWaiting();
  }

  function showLiveWaiting() {
    const w = document.createElement('div');
    w.className = 'waiting-state';
    w.innerHTML = `
      <img src="assets/gravitee-mark.svg" class="waiting-logo" alt="Gravitee" />
      <p>Waiting for the Gravitee Gateway logs</p>
      <small>Send a request through the Gravitee Gateway and watch the full AI Agent flow appear here in real time.</small>
      <div class="waiting-options">
        <a class="waiting-card" href="http://localhost:8002" target="_blank" rel="noopener">
          <div class="wc-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke-linejoin="round"/>
            </svg>
          </div>
          <div class="wc-body">
            <span class="wc-title">Chat on the RNLI AI Find My demo site</span>
            <span class="wc-desc">Open the Mock RNLI website and use the AI chatbot. Try simple queries, ask for private data, or trigger guardrails and rate limits.</span>
          </div>
          <svg class="wc-arrow" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>
        <div class="waiting-card wc-curl">
          <div class="wc-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <polyline points="4 17 10 11 4 5" stroke-linecap="round" stroke-linejoin="round"/>
              <line x1="12" y1="19" x2="20" y2="19" stroke-linecap="round"/>
            </svg>
          </div>
          <div class="wc-body">
            <span class="wc-title">Send a curl request</span>
            <span class="wc-desc">Call the agent directly from your terminal:</span>
            <code class="wc-code">curl -X POST http://localhost:8082/stations-agent/ \\
  -H "Content-Type: application/json" \\
  -d '{"jsonrpc":"2.0","method":"message/send","params":{"message":{"role":"user","parts":[{"text":"Which RNLI stations are in Scotland?"}]}}}'</code>
          </div>
        </div>
      </div>`;
    stepsEl.appendChild(w);
  }

  /* ════════════════════════════════════════════════════════════
   * ANALYTICS — accumulate stats from live WS events
   * ════════════════════════════════════════════════════════════ */

  function updateAnalyticsFromFlow(msg, steps, summary) {
    analyticsStats.total++;

    // Plan — first arrow with a plan field wins
    let plan = 'unknown';
    for (const step of steps) {
      if (step.plan) { plan = step.plan.toLowerCase(); break; }
    }
    if (plan === 'gold')        analyticsStats.gold++;
    else if (plan === 'silver') analyticsStats.silver++;
    else                        analyticsStats.unknown++;

    // Status from summary
    if (summary.status === 'blocked')     analyticsStats.blocked++;
    else if (summary.status === 'cached') analyticsStats.cached++;
    else if (summary.status === 'err')    analyticsStats.rateLimited++;

    // Response time — parse "1.9s" or "310ms" from totalTime
    const t = summary.totalTime || '';
    const mMatch = t.match(/([\d.]+)ms/);
    const sMatch = t.match(/([\d.]+)s\b/);
    let ms = null;
    if (mMatch)      ms = parseFloat(mMatch[1]);
    else if (sMatch) ms = parseFloat(sMatch[1]) * 1000;
    if (ms !== null) {
      analyticsStats.responseTimes.push(ms);
      if (analyticsStats.responseTimes.length > 100) analyticsStats.responseTimes.shift();
    }

    // Timeline entry (most recent first, max 20)
    analyticsTimeline.unshift({
      time: msg.timestamp ? new Date(msg.timestamp) : new Date(),
      query: summary.userText || (msg.apiName || ''),
      plan,
      status: summary.status || 'ok',
      totalTime: summary.totalTime || '',
    });
    if (analyticsTimeline.length > 20) analyticsTimeline.pop();

    if (mode === 'analytics') renderAnalytics();
  }

  function resetAnalytics() {
    Object.assign(analyticsStats, { total: 0, gold: 0, silver: 0, unknown: 0, blocked: 0, cached: 0, rateLimited: 0, responseTimes: [] });
    analyticsTimeline.length = 0;
    renderAnalytics();
  }

  function renderAnalytics() {
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };

    const total = analyticsStats.total;
    const times = analyticsStats.responseTimes;
    const avgMs = times.length
      ? Math.round(times.reduce((a, b) => a + b, 0) / times.length)
      : null;

    set('analyticsTotal',    total);
    set('analyticsGold',     analyticsStats.gold);
    set('analyticsSilver',   analyticsStats.silver);
    set('analyticsBlocked',  analyticsStats.blocked);
    set('analyticsCached',   analyticsStats.cached);
    set('analyticsRateLim',  analyticsStats.rateLimited);
    set('analyticsAvgTime',  avgMs !== null ? `${avgMs}ms` : '—');

    // Cache / block percentages (shown if total > 0)
    const pct = (n) => total > 0 ? `${Math.round((n / total) * 100)}%` : '—';
    set('analyticsBlockPct',  pct(analyticsStats.blocked));
    set('analyticsCachePct',  pct(analyticsStats.cached));

    renderAnalyticsTimeline();
  }

  function renderAnalyticsTimeline() {
    const tl = document.getElementById('analyticsTimeline');
    if (!tl) return;

    if (!analyticsTimeline.length) {
      tl.innerHTML = '<div class="atl-empty">No requests yet — send a query through the gateway.</div>';
      return;
    }

    tl.innerHTML = analyticsTimeline.map(entry => {
      const timeStr = entry.time instanceof Date
        ? entry.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        : '';
      const planCls   = entry.plan === 'gold' ? 'atl-gold' : entry.plan === 'silver' ? 'atl-silver' : 'atl-unknown';
      const statusCls = entry.status === 'blocked' ? 'atl-blocked'
                      : entry.status === 'cached'  ? 'atl-cached'
                      : entry.status === 'err'     ? 'atl-err'
                      : 'atl-ok';
      const statusLabel = entry.status === 'blocked' ? 'Blocked'
                        : entry.status === 'cached'  ? 'Cached'
                        : entry.status === 'err'     ? 'Error'
                        : 'OK';
      const queryText = entry.query
        ? escapeHtml(entry.query.length > 55 ? entry.query.slice(0, 52) + '…' : entry.query)
        : '<em>unknown query</em>';
      return `<div class="atl-row">
        <span class="atl-time">${timeStr}</span>
        <span class="atl-query">${queryText}</span>
        <span class="atl-badge ${planCls}">${entry.plan.charAt(0).toUpperCase() + entry.plan.slice(1)}</span>
        <span class="atl-badge ${statusCls}">${statusLabel}</span>
        ${entry.totalTime ? `<span class="atl-latency">${escapeHtml(entry.totalTime)}</span>` : ''}
      </div>`;
    }).join('');
  }

  /* ════════════════════════════════════════════════════════════
   * DEMO MODE — scripted scenario (no emojis, clean labels)
   * ════════════════════════════════════════════════════════════ */

  const SCENARIO = [
    /* ── Phase 1 — User Request (Gold plan, authenticated) ── */
    { type: 'divider', label: 'Phase 1 — User Request' },
    {
      type: 'arrow', from: 'client', to: 'gateway',
      label: 'POST /stations-agent/',
      message: { lane: 'gateway', text: 'Find me the nearest RNLI stations to Poole in Dorset' },
      policies: [], plan: 'Gold',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: 'Authenticated & forwarded',
      message: { lane: 'agent', text: 'Processing request (Gold plan)' },
    },

    /* ── Phase 2 — Tool Discovery ── */
    { type: 'divider', label: 'Phase 2 — Tool Discovery' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /lifeboat-mcp',
      message: { lane: 'gateway', text: 'MCP tools/list request' },
      policies: [], plan: 'Gold',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 11ms',
      message: { lane: 'agent', text: '2 tools discovered', toolList: ['listStationsByRegion', 'findNearestStations'] },
      badge: { type: 'ok', text: '11ms / 3ms gw' },
    },

    /* ── Phase 3 — LLM Decision ── */
    { type: 'divider', label: 'Phase 3 — LLM Decision' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /llm-proxy/chat/completions',
      message: { lane: 'gateway', text: 'Forwarding to LLM' },
      policies: [
        { name: 'AI Guardrails', passed: true },
        { name: 'Token Rate Limit', passed: true },
      ], plan: 'Gold',
      latency: { type: 'normal', text: 'guard rails: ~2ms' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: 'qwen3:0.6b',
      message: { lane: 'llm', text: '4 messages + 2 tool definitions' },
    },
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: '200',
      message: { lane: 'gateway', text: 'Call findNearestStations' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 310ms',
      message: { lane: 'agent', text: 'Call findNearestStations' },
      badge: { type: 'ok', text: '310ms / 820 tokens / 7ms gw' },
      latency: { type: 'slow', text: '310ms LLM inference' },
    },

    /* ── Phase 4 — Tool Execution ── */
    { type: 'divider', label: 'Phase 4 — Tool Execution' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /lifeboat-mcp',
      message: { lane: 'gateway', text: 'MCP tools/call — findNearestStations', toolCall: { name: 'findNearestStations', args: { location: 'Poole, Dorset', limit: 5 } } },
      policies: [], plan: 'Gold',
      latency: { type: 'normal', text: '~3ms policy overhead' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'api',
      label: 'GET /stations/nearest?location=Poole',
      message: { lane: 'api', text: 'Find nearest stations' },
    },
    {
      type: 'arrow', from: 'api', to: 'gateway',
      label: '200',
      message: { lane: 'gateway', text: '5 stations returned' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 38ms',
      message: { lane: 'agent', text: '5 nearest stations returned' },
      badge: { type: 'ok', text: '38ms / 5ms gw' },
      latency: { type: 'normal', text: '38ms API + 5ms gw' },
    },

    /* ── Phase 5 — Format Response ── */
    { type: 'divider', label: 'Phase 5 — Format Response' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /llm-proxy/chat/completions',
      message: { lane: 'gateway', text: 'Forwarding to LLM' },
      policies: [
        { name: 'AI Guardrails', passed: true },
        { name: 'Token Rate Limit', passed: true },
        { name: 'Cache', passed: true },
      ], plan: 'Gold',
      latency: { type: 'normal', text: 'cache miss + guard rails: ~2ms' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'llm',
      label: 'qwen3:0.6b',
      message: { lane: 'llm', text: '6 messages (with tool results)' },
    },
    {
      type: 'arrow', from: 'llm', to: 'gateway',
      label: '200',
      message: { lane: 'gateway', text: 'Text response — 158 tokens' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — 295ms',
      message: { lane: 'agent', text: 'Text response — 158 tokens' },
      badge: { type: 'ok', text: '295ms / 158 tokens / 6ms gw' },
      latency: { type: 'slow', text: '295ms LLM inference' },
    },

    /* ── Phase 6 — Response Delivered ── */
    { type: 'divider', label: 'Phase 6 — Response Delivered' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'A2A response',
      message: { lane: 'gateway', text: 'Agent response ready' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'client',
      label: '200 — 1.9s total',
      message: { lane: 'client', text: 'Response delivered to user' },
      badge: { type: 'ok', text: '1.9s total / Gold plan' },
    },
  ];

  /* ── Guard Rails scenario — Silver plan, harmful query blocked ── */
  const SCENARIO_GUARD_RAILS = [
    /* ── Phase 1 — User Request (Silver plan, harmful query) ── */
    { type: 'divider', label: 'Phase 1 — User Request' },
    {
      type: 'arrow', from: 'client', to: 'gateway',
      label: 'POST /stations-agent/',
      message: { lane: 'gateway', text: 'How do I make a weapon to hurt someone?' },
      policies: [], plan: 'Silver',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: 'Authenticated & forwarded',
      message: { lane: 'agent', text: 'Processing request (Silver plan)' },
    },

    /* ── Phase 2 — Guard Rails Check ── */
    { type: 'divider', label: 'Phase 2 — Guard Rails Check' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /llm-proxy/chat/completions',
      message: { lane: 'gateway', text: 'LLM request intercepted by AI Guardrails' },
      policies: [
        { name: 'AI Guardrails', passed: false },
      ], plan: 'Silver',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '400 — Content blocked',
      message: { lane: 'agent', text: 'Guard Rails: toxicity score 0.87 > threshold 0.50 — denied' },
      badge: { type: 'blocked', text: '0.87 toxicity · BLOCKED' },
    },

    /* ── Phase 3 — Error Returned to Client ── */
    { type: 'divider', label: 'Phase 3 — Request Rejected' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'Error forwarded',
      message: { lane: 'gateway', text: 'Propagating 400 to client' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'client',
      label: '400 — Guard Rails',
      message: { lane: 'client', text: 'Request blocked — content policy violation' },
      badge: { type: 'blocked', text: '400 blocked / Silver plan' },
    },
  ];

  /* ── Cache Hit scenario — same query returns instantly from cache ── */
  const SCENARIO_CACHE_HIT = [
    { type: 'divider', label: 'Phase 1 — User Request (same query as before)' },
    {
      type: 'arrow', from: 'client', to: 'gateway',
      label: 'POST /stations-agent/',
      message: { lane: 'gateway', text: 'Find me the nearest RNLI stations to Poole in Dorset' },
      policies: [], plan: 'Gold',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: 'Authenticated & forwarded',
      message: { lane: 'agent', text: 'Processing request (Gold plan)' },
    },

    { type: 'divider', label: 'Phase 2 — LLM Cache Hit' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'POST /llm-proxy/chat/completions',
      message: { lane: 'gateway', text: 'LLM request — checking cache first' },
      policies: [
        { name: 'AI Guardrails', passed: true },
        { name: 'Cache', passed: true },
      ], plan: 'Gold',
    },
    {
      type: 'arrow', from: 'gateway', to: 'agent',
      label: '200 — Cache hit',
      message: { lane: 'agent', text: 'Identical prompt found in cache — LLM not called' },
      badge: { type: 'cached', text: '180ms (was 1,200ms)' },
      latency: { type: 'fast', text: '6x faster — cached response' },
    },

    { type: 'divider', label: 'Phase 3 — Response Delivered' },
    {
      type: 'arrow', from: 'agent', to: 'gateway',
      label: 'A2A response',
      message: { lane: 'gateway', text: 'Agent response ready' },
    },
    {
      type: 'arrow', from: 'gateway', to: 'client',
      label: '200 — 190ms total',
      message: { lane: 'client', text: 'Response delivered — served from cache' },
      badge: { type: 'cached', text: '190ms total / Gold plan' },
    },
  ];

  /* ── Rate Limit scenario — Silver plan quota exceeded ── */
  const SCENARIO_RATE_LIMIT = [
    { type: 'divider', label: 'Phase 1 — User Request (quota exhausted)' },
    {
      type: 'arrow', from: 'client', to: 'gateway',
      label: 'POST /stations-agent/',
      message: { lane: 'gateway', text: 'Find me the nearest RNLI stations to Edinburgh' },
      policies: [], plan: 'Silver',
    },

    { type: 'divider', label: 'Phase 2 — Rate Limit Enforced' },
    {
      type: 'arrow', from: 'gateway', to: 'client',
      label: '429 — Too Many Requests',
      message: { lane: 'gateway', text: 'Silver plan quota exceeded — request blocked at gateway' },
      policies: [
        { name: 'Rate Limit', passed: false },
      ], plan: 'Silver',
      badge: { type: 'blocked', text: '429 / 5 req/min limit' },
    },
  ];

  function getActiveScenario() {
    const val = scenarioSelect ? scenarioSelect.value : 'happy';
    if (val === 'guardrails') return SCENARIO_GUARD_RAILS;
    if (val === 'cache')      return SCENARIO_CACHE_HIT;
    if (val === 'ratelimit')  return SCENARIO_RATE_LIMIT;
    return SCENARIO;
  }

  function getTotalDemoSteps() { return getActiveScenario().length; }

  function showCompletionBanner() {
    const val = scenarioSelect ? scenarioSelect.value : 'happy';
    const configs = {
      happy: {
        icon: '✅', title: 'Request complete',
        rows: [
          { icon: '🛡️', text: 'Guard Rails checked ×2 — no harmful content detected' },
          { icon: '⚡', text: 'Gateway overhead: ~7ms per hop' },
          { icon: '📦', text: 'Cache miss on first query — response now stored (5 min TTL)' },
          { icon: '🔑', text: 'JWT validated · Gold plan · rate limit not reached' },
        ],
        footer: '1.9s total  ·  Gold plan  ·  2 LLM calls  ·  1 MCP tool call',
        cls: 'cb-ok',
      },
      guardrails: {
        icon: '🛡️', title: 'Request blocked by Guard Rails',
        rows: [
          { icon: '🤖', text: 'DistilBERT ONNX model scored content 0.87 (threshold: 0.50)' },
          { icon: '🚫', text: 'LLM never reached — blocked at gateway in < 5ms' },
          { icon: '📋', text: 'Policy decision logged in Gravitee Analytics' },
        ],
        footer: '< 50ms total  ·  Silver plan  ·  0 LLM calls  ·  0 tokens consumed',
        cls: 'cb-blocked',
      },
      cache: {
        icon: '⚡', title: 'Cache hit — LLM skipped entirely',
        rows: [
          { icon: '📦', text: 'Identical prompt found in response cache (TTL: 5 min)' },
          { icon: '🚀', text: '180ms vs 1,200ms — 6× faster, zero LLM cost' },
          { icon: '🔑', text: 'Guard Rails still evaluated — cache never bypasses security' },
        ],
        footer: '190ms total  ·  Gold plan  ·  0 LLM calls  ·  0 tokens consumed',
        cls: 'cb-cached',
      },
      ratelimit: {
        icon: '🚫', title: 'Rate limit exceeded',
        rows: [
          { icon: '📊', text: 'Silver plan quota: 5 requests / minute reached' },
          { icon: '⚡', text: 'Blocked at gateway in < 10ms — agent never contacted' },
          { icon: '💡', text: 'Gold plan offers higher rate limits and response cache access' },
        ],
        footer: '< 10ms total  ·  Silver plan  ·  429 Too Many Requests',
        cls: 'cb-blocked',
      },
    };
    const cfg = configs[val] || configs.happy;

    const banner = document.createElement('div');
    banner.className = `completion-banner ${cfg.cls}`;
    banner.innerHTML = `
      <div class="cb-header">
        <span class="cb-icon">${cfg.icon}</span>
        <span class="cb-title">${cfg.title}</span>
        <span class="cb-subtitle">What Gravitee did</span>
      </div>
      <div class="cb-rows">
        ${cfg.rows.map(r => `<div class="cb-row"><span class="cb-row-icon">${r.icon}</span><span>${r.text}</span></div>`).join('')}
      </div>
      <div class="cb-footer">${cfg.footer}</div>`;

    stepsEl.appendChild(banner);
    requestAnimationFrame(() => requestAnimationFrame(() => banner.classList.add('visible')));
    scrollToBottom();
  }

  function demoShowNext() {
    const scenario = getActiveScenario();
    const total    = getTotalDemoSteps();
    if (demoCursor >= total) { demoStop(); showCompletionBanner(); return; }

    // Create flow wrapper before the first step
    if (demoCursor === 0 && !currentFlowBody) {
      const summary = extractFlowSummary(scenario);
      const wrapper = createFlowWrapper(summary);
      stepsEl.appendChild(wrapper);
      currentFlowBody = wrapper.querySelector('.flow-body');
      currentGroup = null;
      requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.add('visible')));
    }

    const step = scenario[demoCursor];
    const container = currentFlowBody || stepsEl;
    appendStepToDOM(step, container);

    demoCursor++;
    stepNumEl.textContent  = demoCursor;
    progressEl.style.width = `${(demoCursor / total) * 100}%`;
    scrollToBottom();

    if (demoPlaying) {
      const delay = step.type === 'divider' ? 800 : 1200;
      demoTimer = setTimeout(demoShowNext, delay / demoSpeed);
    }
  }

  function demoPlay() {
    if (demoCursor >= getTotalDemoSteps()) demoReset();
    demoPlaying = true;
    playIcon.style.display  = 'none';
    pauseIcon.style.display = 'block';
    demoShowNext();
  }

  function demoStop() {
    demoPlaying = false;
    playIcon.style.display  = 'block';
    pauseIcon.style.display = 'none';
    if (demoTimer) { clearTimeout(demoTimer); demoTimer = null; }
  }

  function demoReset() {
    demoStop();
    demoCursor = 0;
    stepsEl.innerHTML = '';
    resetGroupState();
    stepNumEl.textContent  = '0';
    stepTotalEl.textContent = getTotalDemoSteps();
    progressEl.style.width = '0%';
    showDemoWaiting();
  }

  function showDemoWaiting() {
    const val = scenarioSelect ? scenarioSelect.value : 'happy';
    const w = document.createElement('div');
    w.className = 'waiting-state';

    const configs = {
      happy: {
        desc: 'Watch a Gold-plan user ask "Find me the nearest RNLI stations to Poole" — and see every hop through the Gravitee Gateway.',
        pills: [
          { label: 'AI Guard Rails', cls: '' },
          { label: 'Token Rate Limit', cls: '' },
          { label: 'Response Cache', cls: '' },
          { label: 'MCP Tool Calls', cls: '' },
        ],
      },
      guardrails: {
        desc: 'Watch a Silver-plan user send a harmful query — and see Gravitee\'s AI Guardrails block it before it ever reaches the LLM.',
        pills: [
          { label: 'AI Guard Rails', cls: 'wh-pill-blocked' },
          { label: 'Silver Plan', cls: '' },
          { label: '400 Blocked', cls: 'wh-pill-blocked' },
        ],
      },
      cache: {
        desc: 'Watch the same Gold-plan query return instantly from cache — ~6x faster, LLM never called.',
        pills: [
          { label: 'Response Cache', cls: 'wh-pill-cached' },
          { label: 'Gold Plan', cls: '' },
          { label: '~180ms vs 1,200ms', cls: 'wh-pill-cached' },
        ],
      },
      ratelimit: {
        desc: 'Watch a Silver-plan user hit their request quota — blocked at the gateway before the agent is even contacted.',
        pills: [
          { label: 'Rate Limit', cls: 'wh-pill-blocked' },
          { label: 'Silver Plan', cls: '' },
          { label: '429 Blocked', cls: 'wh-pill-blocked' },
        ],
      },
    };

    const cfg = configs[val] || configs.happy;
    const pillsHtml = cfg.pills.map(p =>
      `<span class="wh-pill ${p.cls}">${p.label}</span>`).join('');

    w.innerHTML = `
      <img src="assets/gravitee-mark.svg" class="waiting-logo" alt="Gravitee" />
      <p>Press Play to start the demo</p>
      <small>${cfg.desc}</small>
      <div class="waiting-hints">${pillsHtml}</div>`;

    stepsEl.appendChild(w);
  }

  /* ════════════════════════════════════════════════════════════
   * MODE SWITCHING
   * ════════════════════════════════════════════════════════════ */

  function switchMode(newMode) {
    mode = newMode;

    modeToggle.querySelectorAll('.mode-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === newMode));

    // Show/hide the sequence diagram area vs analytics panel
    const showDiagram   = newMode !== 'analytics';
    if (laneHeaders)    laneHeaders.style.display    = showDiagram ? '' : 'none';
    if (graphArea)      graphArea.style.display      = showDiagram ? '' : 'none';
    if (analyticsPanel) analyticsPanel.style.display = showDiagram ? 'none' : 'flex';

    if (newMode === 'analytics') {
      liveControls.style.display   = 'none';
      demoControls.style.display   = 'none';
      liveStats.style.display      = 'none';
      stepCounter.style.display    = 'none';
      scenarioPicker.style.display = 'none';
      demoStop();
      renderAnalytics();
      return;
    }

    stepsEl.innerHTML = '';
    resetGroupState();

    if (newMode === 'live') {
      liveControls.style.display   = 'flex';
      demoControls.style.display   = 'none';
      liveStats.style.display      = 'flex';
      stepCounter.style.display    = 'none';
      scenarioPicker.style.display = 'none';
      demoStop();
      if (liveQueueTimer) { clearTimeout(liveQueueTimer); liveQueueTimer = null; }
      liveQueue = [];
      removeProgressIndicator();
      liveCount = 0;
      eventCountEl.textContent = '0';
      livePulse.classList.remove('active');
      liveLabel.textContent = 'Waiting for gateway events...';
      liveLabel.classList.remove('has-events');
      showLiveWaiting();
    } else {
      liveControls.style.display   = 'none';
      demoControls.style.display   = 'flex';
      liveStats.style.display      = 'none';
      stepCounter.style.display    = 'block';
      scenarioPicker.style.display = 'flex';
      stepTotalEl.textContent      = getTotalDemoSteps();
      demoReset();
    }
  }

  /* ════════════════════════════════════════════════════════════
   * EVENT LISTENERS
   * ════════════════════════════════════════════════════════════ */

  modeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-mode]');
    if (btn && btn.dataset.mode !== mode) switchMode(btn.dataset.mode);
  });

  btnClear.addEventListener('click', clearLive);

  btnPlay.addEventListener('click', () => {
    if (demoPlaying) demoStop(); else {
      const w = stepsEl.querySelector('.waiting-state');
      if (w) w.remove();
      demoPlay();
    }
  });

  btnStep.addEventListener('click', () => {
    demoStop();
    const w = stepsEl.querySelector('.waiting-state');
    if (w) w.remove();
    demoShowNext();
  });

  btnReset.addEventListener('click', demoReset);

  speedRange.addEventListener('input', () => {
    demoSpeed = parseFloat(speedRange.value);
    speedLabelEl.textContent = demoSpeed + 'x';
    document.documentElement.style.setProperty('--speed', demoSpeed);
  });

  scenarioSelect.addEventListener('change', () => {
    if (mode === 'demo') demoReset();
  });

  const btnResetAnalytics = document.getElementById('btnResetAnalytics');
  if (btnResetAnalytics) btnResetAnalytics.addEventListener('click', resetAnalytics);

  /* ════════════════════════════════════════════════════════════
   * INIT
   * ════════════════════════════════════════════════════════════ */
  connectWS();
  switchMode('live');

})();
