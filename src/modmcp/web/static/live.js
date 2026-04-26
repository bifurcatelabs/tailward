(function () {
    'use strict';

    const strip = document.querySelector('.header-strip');
    if (!strip) return;
    const ph = strip.dataset.ph;
    const sessionId = strip.dataset.sessionId;

    const feed = document.getElementById('feed');
    const empty = document.getElementById('feed-empty');
    const stats = {
        turns: document.getElementById('stat-turns'),
        files: document.getElementById('stat-files'),
        diff: document.getElementById('stat-diff'),
        baseline: document.getElementById('stat-baseline'),
        violations: document.getElementById('stat-violations'),
        rubric: document.getElementById('stat-rubric'),
        close: document.getElementById('stat-close'),
        model: document.getElementById('stat-model'),
        tokensIn: document.getElementById('stat-tokens-in'),
        tokensOut: document.getElementById('stat-tokens-out'),
        cacheRead: document.getElementById('stat-cache-read'),
    };
    const connState = document.getElementById('conn-state');

    let lastEventId = 0;
    let renderedIds = new Set();
    let violationCount = 0;
    let rubricSampleCount = 0;
    let rubricBuffers = {};
    // Wall-clock of the most recently processed event (epoch seconds), used
    // to render an inter-event delta on the next item. Captures both human
    // idle time (gap between an assistant turn and the next user prompt)
    // and assistant latency (gap between user prompt and assistant turn)
    // because both show up as deltas between consecutive events.
    let lastEventAt = null;

    // ------------ partial renderers per event type ------------
    const renderers = {
        user_turn: (p) => `<div class="feed-meta"><span class="feed-kind kind-user_turn">user</span><span>${fmtTime()}</span></div>
            <div>${p.chars ? p.chars + ' chars' : 'user prompt'}</div>
            ${p.text_preview ? `<div class="evidence">${escape(p.text_preview)}</div>` : ''}`,

        turn: (p) => {
            const u = p.usage || {};
            const usageBits = [];
            if (u.input_tokens) usageBits.push(`${humanize(u.input_tokens)} in`);
            if (u.output_tokens) usageBits.push(`${humanize(u.output_tokens)} out`);
            if (u.cache_read_input_tokens) usageBits.push(`${humanize(u.cache_read_input_tokens)} cached`);
            const usageLine = usageBits.length
                ? `<div class="muted" style="margin-top:4px; font-size:11px">${usageBits.join(' · ')}${p.model ? ` · ${escape(p.model)}` : ''}${p.stop_reason ? ` · stop: ${escape(p.stop_reason)}` : ''}</div>`
                : '';
            return `<div class="feed-meta"><span class="feed-kind kind-turn">turn</span><span>${fmtTime()}</span></div>
                <div>assistant turn ${p.turn_idx} · ${p.chars} chars</div>
                ${p.text_preview ? `<div class="evidence">${escape(p.text_preview)}</div>` : ''}
                ${usageLine}`;
        },

        tool_call: (p) => `<div class="feed-meta"><span class="feed-kind kind-tool_call">tool</span><span>${fmtTime()}</span></div>
            <div>${escape(p.tool || '')} <span class="muted">${escape(p.input_preview || '')}</span></div>`,

        constraint_violation: (p, id) => `<div class="feed-meta">
                <span class="feed-kind kind-constraint_violation">violation · ${escape(p.severity || '')}</span>
                <span>${fmtTime()}</span>
              </div>
              <div><strong>${escape(p.rule_text || p.rule_id || '')}</strong></div>
              <div class="evidence">${escape(p.evidence || '')}</div>
              <div style="margin-top:6px">
                <button class="btn-inline" onclick="wardenAck(${p.id})">acknowledge</button>
                <button class="btn-inline" onclick="wardenDismiss(${p.id})">dismiss</button>
              </div>`,

        scope_snapshot: (p) => `<div class="feed-meta"><span class="feed-kind kind-scope_snapshot">scope</span><span>${fmtTime()}</span></div>
            <div>turn ${p.turn_idx} · files ${p.files_touched} · ${p.diff_bytes}B
              ${p.baseline ? `· baseline ${p.baseline} · threshold ${p.threshold}` : ''}</div>`,

        scope_creep: (p) => `<div class="feed-meta"><span class="feed-kind kind-scope_creep">scope creep</span><span>${fmtTime()}</span></div>
            <div>files touched <strong>${p.files_touched}</strong> exceeded threshold ${p.threshold} (baseline ${p.baseline})</div>
            <div class="evidence">${escape(JSON.stringify(p.tool_kinds || {}, null, 0))}</div>`,

        rubric_in_flight: (p) => `<div class="feed-meta"><span class="feed-kind kind-rubric_in_flight">rubric running</span><span>${fmtTime()}</span></div>
            <div>turn ${p.turn_idx} · ${(p.triggers || []).join(', ')}</div>`,

        rubric_sample: (p) => `<div class="feed-meta"><span class="feed-kind kind-rubric_sample">rubric</span><span>${fmtTime()}</span></div>
            <div>${escape(p.dim)} · <strong>${Number(p.score).toFixed(1)} / 5</strong> · turn ${p.turn_idx}</div>
            ${p.evidence ? `<div class="evidence">${escape(p.evidence)}</div>` : ''}
            ${p.suggestion ? `<div class="muted" style="margin-top:4px">suggestion: ${escape(p.suggestion)}</div>` : ''}
            <div style="margin-top:4px">
              <button class="btn-inline" onclick="wardenRubricFeedback(${p.id}, 'disagree')">disagree</button>
            </div>`,

        rubric_done: (p) => p.error
            ? `<div class="feed-meta"><span class="feed-kind kind-rubric_done">rubric failed</span><span>${fmtTime()}</span></div>
               <div class="muted">${escape(p.error)}</div>`
            : `<div class="feed-meta"><span class="feed-kind kind-rubric_done">rubric done</span><span>${fmtTime()}</span></div>
               <div class="muted">turn ${p.turn_idx}</div>`,

        drift: (p) => `<div class="feed-meta"><span class="feed-kind kind-drift">drift · ${escape(p.severity || '')}</span><span>${fmtTime()}</span></div>
            <div>${escape(p.detail || '')}</div>
            ${p.corrective ? `<div class="evidence">${escape(p.corrective)}</div>` : ''}`,

        claim: (p) => `<div class="feed-meta"><span class="feed-kind kind-claim">claim · ${escape(p.status || '')}</span><span>${fmtTime()}</span></div>
            <div>${escape(p.text || '')}</div>
            ${p.evidence ? `<div class="evidence">${escape(p.evidence)}</div>` : ''}`,

        report_progress: (p) => `<div class="feed-meta"><span class="feed-kind kind-report_progress">report progress</span><span>${fmtTime()}</span></div>
            <div class="muted">${escape(p.stage || 'updating')}</div>`,

        report_ready: (p) => `<div class="feed-meta"><span class="feed-kind kind-report_ready">report ready</span><span>${fmtTime()}</span></div>
            <div><a href="/p/${ph}/sessions/${sessionId}">view permalink</a></div>`,

        session_closed: (p) => `<div class="feed-meta"><span class="feed-kind kind-session_closed">session closed</span><span>${fmtTime()}</span></div>
            <div class="muted">consolidation complete</div>`,

        // "turn_metric" is consumed by the v0.2 Platform view (Svelte).
        // The legacy live.js intentionally has no renderer for it so
        // the feed isn't polluted with metric events; the SSE handler
        // skips subscribing to types absent from this map.
    };

    // ------------ side-effects on the right rail / stats ------------
    const sideEffects = {
        turn: (p) => {
            if (stats.turns && p.turn_idx != null) stats.turns.textContent = p.turn_idx;
            if (stats.model && p.model) {
                stats.model.textContent = p.model;
                stats.model.title = p.model;
            }
            const t = p.totals || {};
            if (stats.tokensIn && t.input_tokens != null) {
                stats.tokensIn.textContent = humanize(t.input_tokens);
                stats.tokensIn.title = `${t.input_tokens} input tokens`;
            }
            if (stats.tokensOut && t.output_tokens != null) {
                stats.tokensOut.textContent = humanize(t.output_tokens);
                stats.tokensOut.title = `${t.output_tokens} output tokens`;
            }
            if (stats.cacheRead && t.cache_read_input_tokens != null) {
                stats.cacheRead.textContent = humanize(t.cache_read_input_tokens);
                stats.cacheRead.title = `${t.cache_read_input_tokens} cache_read tokens`;
            }
        },
        scope_snapshot: (p) => {
            if (stats.files) stats.files.textContent = p.files_touched ?? '?';
            if (stats.diff) stats.diff.textContent = p.diff_bytes ?? 0;
            if (stats.baseline) stats.baseline.textContent = p.baseline ?? '-';
        },
        constraint_violation: () => {
            violationCount += 1;
            if (stats.violations) stats.violations.textContent = violationCount;
        },
        rubric_sample: (p) => {
            rubricSampleCount += 1;
            if (stats.rubric) stats.rubric.textContent = rubricSampleCount;
            const buf = rubricBuffers[p.dim] = (rubricBuffers[p.dim] || []);
            buf.push(Number(p.score));
            while (buf.length > 3) buf.shift();
            const bar = document.querySelector(`.rubric-bar[data-dim="${p.dim}"]`);
            if (bar) {
                const avg = buf.reduce((a, b) => a + b, 0) / buf.length;
                bar.querySelector('[data-role="fill"]').style.width = `${(avg / 5 * 100).toFixed(0)}%`;
                bar.querySelector('[data-role="val"]').textContent = avg.toFixed(2);
            }
        },
        session_closed: () => { if (stats.close) stats.close.textContent = 'closed'; },
        report_ready: async () => {
            if (stats.close) stats.close.textContent = 'consolidated';
            await refreshReportCard();
        },
    };

    async function refreshReportCard() {
        try {
            const r = await fetch(`/p/${ph}/live/${sessionId}/state`);
            if (!r.ok) return;
            const data = await r.json();
            const rc = document.getElementById('report-card');
            if (!rc || !data.report_rows || !data.report_rows.length) return;
            rc.innerHTML = '';
            for (const row of data.report_rows) {
                const pct = (row.score / 5.0 * 100).toFixed(0);
                const el = document.createElement('div');
                el.className = 'report-mode';
                el.dataset.modeId = row.mode_id;
                el.innerHTML = `
                    <div class="title"><span>${escape(row.mode_name)}</span><span class="muted">${Number(row.score).toFixed(1)} / 5</span></div>
                    <div class="bar"><div class="fill" style="width:${pct}%"></div></div>
                    <div class="suggestion">${escape(row.suggestion || '')}</div>`;
                el.addEventListener('click', () => el.classList.toggle('open'));
                rc.appendChild(el);
            }
        } catch (e) {
            console.warn('refreshReportCard failed', e);
        }
    }

    function escape(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function fmtTime() {
        const d = new Date();
        return d.toLocaleTimeString();
    }

    function humanize(n) {
        if (n == null || isNaN(n)) return '0';
        const abs = Math.abs(n);
        if (abs >= 1e9) return (n / 1e9).toFixed(1) + 'B';
        if (abs >= 1e6) return (n / 1e6).toFixed(1) + 'M';
        if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k';
        return String(n);
    }

    function humanizeDuration(seconds) {
        if (seconds == null || !isFinite(seconds) || seconds < 0) return '';
        if (seconds < 1) return '<1s';
        if (seconds < 60) return Math.round(seconds) + 's';
        const m = Math.floor(seconds / 60);
        const s = Math.round(seconds % 60);
        if (m < 60) return s ? `${m}m${s}s` : `${m}m`;
        const h = Math.floor(m / 60);
        const mm = m % 60;
        return mm ? `${h}h${mm}m` : `${h}h`;
    }

    function prepend(html, deltaText) {
        if (empty && empty.parentNode) empty.remove();
        const div = document.createElement('div');
        div.className = 'feed-item';
        div.innerHTML = html;
        if (deltaText) {
            // Inject the inter-event delta next to the time stamp in
            // .feed-meta. Appending to the last span keeps the kind chip
            // on the left and stamps "+12s" right after the time.
            const meta = div.querySelector('.feed-meta');
            if (meta && meta.lastElementChild) {
                const sp = document.createElement('span');
                sp.style.cssText = 'color: var(--muted); margin-left: 6px;';
                sp.textContent = deltaText;
                meta.lastElementChild.appendChild(sp);
            }
        }
        // Click anywhere on a long ``.evidence`` block toggles its
        // expanded class so the full text is visible. The 18em cap +
        // gradient overlay (set in CSS) keeps the feed compact by
        // default; this gives one click to drill in.
        div.querySelectorAll('.evidence').forEach(el => {
            el.addEventListener('click', () => {
                el.classList.toggle('expanded');
            });
        });
        feed.insertBefore(div, feed.firstChild);
        while (feed.children.length > 200) feed.removeChild(feed.lastChild);
    }

    function _toEpochSeconds(createdAt) {
        if (createdAt == null) return null;
        if (typeof createdAt === 'number') return createdAt;
        // ISO string from /replay, /events, and SSE replay frames.
        const ms = Date.parse(createdAt);
        return isNaN(ms) ? null : ms / 1000;
    }

    function handleEvent(id, eventType, payload, createdAt) {
        if (renderedIds.has(id)) return;
        renderedIds.add(id);
        if (id > lastEventId) lastEventId = id;
        const renderer = renderers[eventType];
        let deltaText = '';
        const tsSeconds = _toEpochSeconds(createdAt);
        if (lastEventAt != null && tsSeconds != null && tsSeconds >= lastEventAt) {
            const dur = humanizeDuration(tsSeconds - lastEventAt);
            if (dur) deltaText = '+' + dur;
        }
        if (tsSeconds != null) lastEventAt = tsSeconds;
        if (renderer) prepend(renderer(payload || {}, id), deltaText);
        const effect = sideEffects[eventType];
        if (effect) {
            try { effect(payload || {}); } catch (e) { console.warn(eventType, e); }
        }
    }

    window.wardenAck = async (vid) => {
        await fetch(`/p/${ph}/violations/${vid}/ack`, { method: 'POST' });
    };
    window.wardenDismiss = async (vid) => {
        await fetch(`/p/${ph}/violations/${vid}/dismiss`, { method: 'POST' });
    };
    window.wardenRubricFeedback = async (sid, verdict) => {
        await fetch(`/p/${ph}/rubric/${sid}/feedback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ verdict }),
        });
    };

    async function bootstrap() {
        try {
            const r = await fetch(`/p/${ph}/live/${sessionId}/replay`);
            if (r.ok) {
                const data = await r.json();
                for (const ev of data.events) {
                    handleEvent(ev.id, ev.event_type, ev.payload, ev.created_at);
                }
                if (data.next_since) lastEventId = data.next_since;
            }
        } catch (e) { console.warn('replay failed', e); }
    }

    let es = null;
    let pollingTimer = null;
    let esFailures = 0;

    function setConn(state) {
        if (!connState) return;
        connState.textContent = state;
        connState.className = 'conn-state ' + state;
    }

    function attachSSE() {
        if (typeof EventSource === 'undefined') {
            startPolling();
            return;
        }
        try {
            es = new EventSource(`/p/${ph}/live/${sessionId}/stream?since=${lastEventId}`);
        } catch (e) {
            startPolling();
            return;
        }
        es.onopen = () => {
            setConn('live');
            esFailures = 0;
        };
        es.onerror = () => {
            setConn('offline');
            esFailures += 1;
            es.close();
            es = null;
            if (esFailures >= 2) {
                startPolling();
            } else {
                setTimeout(attachSSE, 1500);
            }
        };
        for (const type of Object.keys(renderers)) {
            es.addEventListener(type, (evt) => {
                try {
                    const data = JSON.parse(evt.data);
                    handleEvent(data.id, data.type, data.payload, data.created_at);
                } catch (e) { console.warn('SSE parse', e); }
            });
        }
        es.addEventListener('saturated', () => {
            setConn('polling');
            es.close();
            es = null;
            startPolling();
        });
    }

    async function pollOnce() {
        try {
            const r = await fetch(`/p/${ph}/live/${sessionId}/events?since=${lastEventId}`);
            if (!r.ok) {
                setConn('offline');
                return;
            }
            setConn('polling');
            const data = await r.json();
            for (const ev of data.events) {
                handleEvent(ev.id, ev.event_type, ev.payload, ev.created_at);
            }
        } catch (e) {
            setConn('offline');
        }
    }

    function startPolling() {
        if (pollingTimer) return;
        setConn('polling');
        pollingTimer = setInterval(pollOnce, 3000);
    }

    (async () => {
        setConn('init');
        await bootstrap();
        attachSSE();
    })();
})();
