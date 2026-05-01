// Reactive live-session store. Owns events, derived header stats,
// SSE / polling lifecycle, and the bootstrap replay. Components read
// from the singleton ``live`` and call ``live.connect(ph, sessionId)``
// once on mount.
//
// Svelte 5 runes (``$state``) provide per-field reactivity; Svelte
// rerenders any component touching a property of ``live`` when that
// property is reassigned.

import { humanizeDuration, toEpochSeconds } from './format.js';

const KNOWN_EVENT_TYPES = new Set([
  'turn',
  'user_turn',
  'tool_call',
  'constraint_violation',
  'scope_snapshot',
  'scope_creep',
  'rubric_in_flight',
  'rubric_sample',
  'rubric_done',
  'report_progress',
  'report_ready',
  'drift',
  'claim',
  'session_closed',
  // Tool-emitted synthesis (Claude Code /compact). Surfaced as a
  // distinct kind so the feed can render it explicitly instead of as
  // a regular user turn. See memory: project_synthesized_turns.md.
  'compact_summary',
  // Per-turn inference-path metrics (TTFT, TPS, cache hit ratio).
  // The Platform view aggregates these via /p/<ph>/turn-metrics; the
  // Session feed renders a small "perf" chip for moment-of-arrival
  // visibility. Without this entry the SSE listener filters them out.
  'turn_metric',
  // Permission-mode transitions (default / acceptEdits / bypassPermissions
  // / plan). Claude Code emits a dedicated event when the user changes
  // mode via Shift+Tab; we render a chip showing "previous → current"
  // so the session timeline reads how trust posture shifted across the
  // session. See memory: project_notes_04282026_passes.md.
  'permission_mode_change',
  // Away-summary captures. Claude Code emits a `type: "system"` event
  // with `subtype: "away_summary"` carrying a structured recap (goal /
  // current task / next action) when it observes the user has stepped
  // away. Surfacing as a feed chip so the session timeline shows when
  // the user was driving vs idle. The full text is available on
  // expand. See memory: project_notes_04282026_passes.md.
  'away_summary',
  // User declined / interrupted a tool call. Detected via
  // `toolUseResult.interrupted: true` on the JSONL line — the closest
  // signal Claude Code exposes to an explicit "user denied" decision.
  // Rare in practice but real audit signal when it fires. Tool name
  // resolved via the dispatcher's tool_use_id → name cache.
  'tool_interrupted',
  // Exfiltration alert — a known secret pattern (API key, PAT,
  // private key block, etc.) was detected in a tool_call payload
  // before it landed in live_events. The original event's payload is
  // sanitized; this alert surfaces the redacted match so the user
  // can see + verify + rotate. See `modmcp.schema.exfiltration`.
  'exfiltration_alert',
  // Edit landed in a Claude Code memory file (~/.claude/projects/
  // <ph>/memory/**). Path-policy would otherwise count this as a
  // constraint violation since the path falls outside the watched
  // project root. The constraints worker emits this event instead so
  // the signal stays visible without polluting the violation count.
  'memory_edit',
  // Session-synthesis snapshot captured to disk by the synthesis_worker
  // (~/.modmcp/projects/<ph>/snapshots/). Payload: trigger kind, path,
  // model, timestamp. UI consumer is the snapshot-timeline panel on
  // the Session view (lands in a follow-up commit).
  'synthesis_captured',
  // Synthesis attempt failed loudly (LLM unreachable, timeout, malformed
  // response, etc). Payload carries trigger + error message; the panel
  // surfaces this so silent misses don't accumulate.
  'synthesis_failed',
]);

const FEED_CAP = 250;

class LiveStore {
  // Connection state -------------------------------------------------
  conn = $state('init'); // 'init' | 'live' | 'polling' | 'offline'

  // Events feed ------------------------------------------------------
  events = $state([]);

  // Header stats -----------------------------------------------------
  turns = $state(0);
  files = $state(0);
  diffBytes = $state(0);
  baseline = $state(null);
  threshold = $state(null);
  violations = $state(0);
  rubricSamples = $state(0);
  closeStatus = $state(null);
  model = $state(null);
  tokensIn = $state(0);
  tokensOut = $state(0);
  cacheRead = $state(0);
  cacheCreate = $state(0);

  // Rubric per-dimension rolling avg (keyed by dim name) ------------
  rubricAvgs = $state({});

  // End-of-session report card --------------------------------------
  reportRows = $state([]);

  // Session metadata mirrored back from /state ----------------------
  startedAt = $state(null);
  // Free-form session_mode label from intent.md (or null if unset).
  // The active mode profile that drives worker thresholds.
  sessionMode = $state(null);
  modeProfile = $state(null);

  // Time-series samples for in-page sparklines. Each is a bounded
  // ring buffer of {t, v} points; the visual components don't need
  // exact precision — just enough to show shape over the session.
  tokenOutSeries = $state([]);
  filesSeries = $state([]);
  cacheSeries = $state([]);

  // Compact arc of all events in the session, sorted by id, used to
  // render the SessionTimeline strip. Each item is
  // { id, type, t (epoch seconds) }.
  arc = $state([]);

  // Load-older state. ``oldestEventId`` tracks the lowest event id
  // we've rendered; ``hasMoreOlder`` is the optimistic guess used to
  // decide whether to show the affordance — flipped to false when
  // the server returns fewer rows than requested. ``loadingOlder``
  // gates concurrent clicks.
  oldestEventId = $state(0);
  hasMoreOlder = $state(true);
  loadingOlder = $state(false);

  // Wall-clock timestamp (epoch seconds) of the most recent event
  // arriving from the bus. Read by HeaderBar to render a "last
  // contact 3s ago"-style indicator — concrete signal that the
  // stream is producing, instead of trusting the ``conn`` label.
  lastContactAt = $state(null);

  // Internals --------------------------------------------------------
  #renderedIds = new Set();
  // Separate dedupe set for the arc — populated by both the full-arc
  // bootstrap (which runs before the feed replay) and ongoing SSE
  // pushes. Keeping it separate from #renderedIds lets the arc and
  // the feed dedupe independently — the arc may know about events
  // the feed hasn't pulled into its current window yet.
  #arcRenderedIds = new Set();
  #lastEventId = 0;
  #lastEventAt = null;
  #rubricBuffers = {};
  #ph = '';
  #sessionId = '';
  #es = null;
  #pollTimer = null;
  #esFailures = 0;

  push(id, eventType, payload, createdAt) {
    if (this.#renderedIds.has(id)) return;
    this.#renderedIds.add(id);
    if (id > this.#lastEventId) this.#lastEventId = id;
    if (!KNOWN_EVENT_TYPES.has(eventType)) return;

    const tsSeconds = toEpochSeconds(createdAt);
    let deltaText = '';
    if (
      this.#lastEventAt != null
      && tsSeconds != null
      && tsSeconds >= this.#lastEventAt
    ) {
      const dur = humanizeDuration(tsSeconds - this.#lastEventAt);
      if (dur) deltaText = '+' + dur;
    }
    if (tsSeconds != null) {
      this.#lastEventAt = tsSeconds;
      // Mirror to public reactive state for HeaderBar / status displays.
      this.lastContactAt = tsSeconds;
    }

    this.events.push({
      id,
      eventType,
      payload: payload || {},
      createdAt,
      deltaText,
    });
    if (this.events.length > FEED_CAP) {
      this.events.splice(0, this.events.length - FEED_CAP);
    }
    if (this.oldestEventId === 0 || id < this.oldestEventId) {
      this.oldestEventId = id;
    }

    // Mirror into the arc strip. Each entry is small (id, type,
    // epoch-seconds) so a generous cap keeps the whole session
    // visible without the feed's 250-event window getting in the
    // way. The cap exists only to bound runaway memory if a wedge
    // emits millions of events; real sessions finish well below it.
    if (!this.#arcRenderedIds.has(id)) {
      this.#arcRenderedIds.add(id);
      this.arc.push({ id, type: eventType, t: tsSeconds });
      if (this.arc.length > 50000) this.arc.splice(0, this.arc.length - 50000);
    }

    this.#applySideEffect(eventType, payload || {});
  }

  #applySideEffect(eventType, p) {
    switch (eventType) {
      case 'turn':
        if (p.turn_idx != null) this.turns = p.turn_idx;
        if (p.model) this.model = p.model;
        if (p.totals) {
          if (p.totals.input_tokens != null) this.tokensIn = p.totals.input_tokens;
          if (p.totals.output_tokens != null) {
            this.tokensOut = p.totals.output_tokens;
            this.#sample('tokenOutSeries', p.totals.output_tokens);
          }
          if (p.totals.cache_read_input_tokens != null) {
            this.cacheRead = p.totals.cache_read_input_tokens;
            this.#sample('cacheSeries', p.totals.cache_read_input_tokens);
          }
          if (p.totals.cache_creation_input_tokens != null)
            this.cacheCreate = p.totals.cache_creation_input_tokens;
        }
        break;
      case 'scope_snapshot':
        if (p.files_touched != null) {
          this.files = p.files_touched;
          this.#sample('filesSeries', p.files_touched);
        }
        if (p.diff_bytes != null) this.diffBytes = p.diff_bytes;
        if (p.baseline != null) this.baseline = p.baseline;
        if (p.threshold != null) this.threshold = p.threshold;
        break;
      case 'constraint_violation':
        this.violations += 1;
        break;
      case 'rubric_sample': {
        this.rubricSamples += 1;
        const dim = p.dim;
        if (dim) {
          const buf = (this.#rubricBuffers[dim] ||= []);
          buf.push(Number(p.score));
          while (buf.length > 3) buf.shift();
          const avg = buf.reduce((a, b) => a + b, 0) / buf.length;
          this.rubricAvgs = { ...this.rubricAvgs, [dim]: avg };
        }
        break;
      }
      case 'session_closed':
        this.closeStatus = 'closed';
        break;
      case 'report_ready':
        this.closeStatus = 'consolidated';
        this.#refreshReportCard();
        break;
    }
  }

  #sample(key, v) {
    const series = this[key];
    series.push(v);
    if (series.length > 60) series.splice(0, series.length - 60);
  }

  async #refreshReportCard() {
    try {
      const r = await fetch(
        `/p/${this.#ph}/live/${this.#sessionId}/state`,
      );
      if (!r.ok) return;
      const data = await r.json();
      if (Array.isArray(data.report_rows)) this.reportRows = data.report_rows;
      if (data.close_status) this.closeStatus = data.close_status;
    } catch (e) {
      console.warn('refreshReportCard failed', e);
    }
  }

  async loadOlder() {
    // Fetch the next-older batch and prepend (chronologically) to
    // the events array. Bookkeeping: track loading flag for UX,
    // flip hasMoreOlder false when the server returns fewer rows
    // than the page size so the affordance disappears at the boundary.
    if (this.loadingOlder || !this.hasMoreOlder) return;
    if (!this.oldestEventId) return;
    this.loadingOlder = true;
    try {
      const r = await fetch(
        `/p/${this.#ph}/live/${this.#sessionId}/replay`
        + `?before=${this.oldestEventId}`,
      );
      if (!r.ok) return;
      const data = await r.json();
      const older = data.events || [];
      if (!older.length) {
        this.hasMoreOlder = false;
        return;
      }
      // Prepend chronologically. ``push`` would put them after the
      // current tail; we want them before. Splice in at index 0 in
      // arrival order so deltaText still reads naturally for the
      // user's eye on a future render — but note: deltaText for the
      // historical batch is *not* recomputed because the wall-clock
      // baseline ``#lastEventAt`` is forward-only. Older events get
      // an empty delta, which is the right answer (you're looking at
      // history, not live activity).
      const olderRendered = [];
      for (const ev of older) {
        if (this.#renderedIds.has(ev.id)) continue;
        this.#renderedIds.add(ev.id);
        olderRendered.push({
          id: ev.id,
          eventType: ev.event_type,
          payload: ev.payload || {},
          createdAt: ev.created_at,
          deltaText: '',
        });
      }
      if (olderRendered.length === 0) {
        this.hasMoreOlder = false;
        return;
      }
      this.events.splice(0, 0, ...olderRendered);
      this.oldestEventId = olderRendered[0].id;
      // If we got fewer than a full page, we've reached the start
      // of the session.
      if (older.length < 100) this.hasMoreOlder = false;
    } catch (e) {
      console.warn('loadOlder failed', e);
    } finally {
      this.loadingOlder = false;
    }
  }

  async ackViolation(id) {
    await fetch(`/p/${this.#ph}/violations/${id}/ack`, { method: 'POST' });
    this.#dispatchReflectionRefresh();
  }

  async dismissViolation(id) {
    await fetch(`/p/${this.#ph}/violations/${id}/dismiss`, { method: 'POST' });
    this.#dispatchReflectionRefresh();
  }

  // Notify ReflectionView (or any listener) that violation-status
  // counts have changed and any cached reflection data should be
  // re-fetched. Fired after ack/dismiss completes — the response
  // cadence card otherwise drifts behind the ledger until the user
  // manually reloads.
  #dispatchReflectionRefresh() {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent('reflection:refresh'));
  }

  async rubricFeedback(scoreId, verdict) {
    await fetch(`/p/${this.#ph}/rubric/${scoreId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict }),
    });
  }

  async connect(ph, sessionId) {
    this.#ph = ph;
    this.#sessionId = sessionId;
    // Arc-only bootstrap fires first so the SessionTimeline reflects
    // the full session immediately, while the feed replay (which
    // carries heavy payloads) runs at its tail-windowed default.
    await this.#bootstrapFullArc();
    await this.#bootstrap();
    this.#attachSSE();
  }

  async #bootstrapFullArc() {
    try {
      const r = await fetch(`/p/${this.#ph}/live/${this.#sessionId}/arc`);
      if (!r.ok) return;
      const data = await r.json();
      const events = data.events || [];
      // Replace existing arc wholesale — this is the authoritative
      // pre-SSE snapshot. Subsequent SSE pushes will append, deduped
      // by ``#arcRenderedIds``.
      this.arc = [];
      this.#arcRenderedIds.clear();
      for (const ev of events) {
        if (this.#arcRenderedIds.has(ev.id)) continue;
        this.#arcRenderedIds.add(ev.id);
        const t = toEpochSeconds(ev.created_at);
        this.arc.push({ id: ev.id, type: ev.event_type, t });
      }
    } catch (e) {
      console.warn('arc bootstrap failed', e);
    }
  }

  disconnect() {
    if (this.#es) { this.#es.close(); this.#es = null; }
    if (this.#pollTimer) { clearInterval(this.#pollTimer); this.#pollTimer = null; }
  }

  async #bootstrap() {
    try {
      const r = await fetch(`/p/${this.#ph}/live/${this.#sessionId}/replay`);
      if (r.ok) {
        const data = await r.json();
        for (const ev of data.events) {
          this.push(ev.id, ev.event_type, ev.payload, ev.created_at);
        }
        if (data.next_since) this.#lastEventId = data.next_since;
      }
    } catch (e) {
      console.warn('replay failed', e);
    }
    // Pick up state that's not derivable from the event stream alone
    // (close_status, initial report rows, dim averages from rubric history).
    try {
      const r = await fetch(`/p/${this.#ph}/live/${this.#sessionId}/state`);
      if (r.ok) {
        const data = await r.json();
        if (data.close_status) this.closeStatus = data.close_status;
        if (Array.isArray(data.report_rows)) this.reportRows = data.report_rows;
        if (data.rubric_dim_avg && typeof data.rubric_dim_avg === 'object') {
          this.rubricAvgs = { ...this.rubricAvgs, ...data.rubric_dim_avg };
        }
        if (data.session?.started_at) this.startedAt = data.session.started_at;
        if (data.session_mode !== undefined) this.sessionMode = data.session_mode;
        if (data.mode_profile !== undefined) this.modeProfile = data.mode_profile;
      }
    } catch {}
  }

  #attachSSE() {
    if (typeof EventSource === 'undefined') {
      this.#startPolling();
      return;
    }
    try {
      this.#es = new EventSource(
        `/p/${this.#ph}/live/${this.#sessionId}/stream?since=${this.#lastEventId}`,
      );
    } catch {
      this.#startPolling();
      return;
    }
    this.#es.onopen = () => {
      this.conn = 'live';
      this.#esFailures = 0;
    };
    this.#es.onerror = () => {
      this.conn = 'offline';
      this.#esFailures += 1;
      this.#es?.close();
      this.#es = null;
      if (this.#esFailures >= 2) this.#startPolling();
      else setTimeout(() => this.#attachSSE(), 1500);
    };
    for (const t of KNOWN_EVENT_TYPES) {
      this.#es.addEventListener(t, (evt) => {
        try {
          const data = JSON.parse(evt.data);
          this.push(data.id, data.type, data.payload, data.created_at);
        } catch (e) {
          console.warn('SSE parse', e);
        }
      });
    }
    this.#es.addEventListener('saturated', () => {
      this.conn = 'polling';
      this.#es?.close();
      this.#es = null;
      this.#startPolling();
    });
  }

  #startPolling() {
    if (this.#pollTimer) return;
    this.conn = 'polling';
    this.#pollTimer = setInterval(() => this.#pollOnce(), 3000);
  }

  async #pollOnce() {
    try {
      const r = await fetch(
        `/p/${this.#ph}/live/${this.#sessionId}/events?since=${this.#lastEventId}`,
      );
      if (!r.ok) {
        this.conn = 'offline';
        return;
      }
      this.conn = 'polling';
      const data = await r.json();
      for (const ev of data.events) {
        this.push(ev.id, ev.event_type, ev.payload, ev.created_at);
      }
    } catch {
      this.conn = 'offline';
    }
  }
}

export const live = new LiveStore();
