<script>
  import { live } from './live.svelte.js';
  import { fmtClock, humanize, humanizeBytes, toEpochSeconds } from './format.js';

  let { event, sessionId } = $props();
  let expanded = $state(false);
  // Brief "copied" feedback for the per-row copy button. Cleared
  // 1.2s after the click. Mirrors the SearchPanel pattern so the
  // copy UX is consistent wherever a turn can be grabbed from.
  let copied = $state(false);

  // Click handler that doesn't fire when the user is mid-selection.
  // Without this, click-and-drag to select evidence text *also*
  // toggles the expand state, which fights careful copy/paste.
  function toggleExpanded() {
    if (typeof window !== 'undefined') {
      const sel = window.getSelection();
      if (sel && sel.toString().length > 0) return;
    }
    expanded = !expanded;
  }

  // Mapping from event type -> friendly chip label.
  const chipLabel = {
    turn: 'assistant',
    user_turn: 'user',
    tool_call: 'tool',
    constraint_violation: 'violation',
    scope_snapshot: 'scope',
    scope_creep: 'scope creep',
    rubric_sample: 'rubric',
    rubric_in_flight: 'rubric…',
    rubric_done: 'rubric done',
    drift: 'drift',
    claim: 'claim',
    report_progress: 'report',
    report_ready: 'report',
    session_closed: 'session closed',
    compact_summary: 'synthesized',
    turn_metric: 'perf',
    permission_mode_change: 'permission',
    away_summary: 'away',
    tool_interrupted: 'declined',
    exfiltration_alert: 'leak',
    memory_edit: 'memory',
  };

  let p = $derived(event.payload || {});
  let kind = $derived(event.eventType);

  // Local-time clock derived from the event's createdAt, falling back
  // to render-time if missing.
  let clock = $derived.by(() => {
    const sec = toEpochSeconds(event.createdAt);
    return sec ? fmtClock(new Date(sec * 1000)) : fmtClock();
  });

  // Full ISO timestamp (local tz) for the copy header — matches the
  // SearchPanel format so a copied feed event reads identically to
  // a copied search result. The compact HH:MM:SS clock above stays
  // for the inline display; ISO only appears in the copied text.
  function fmtIsoTs(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      const pad = (n) => String(n).padStart(2, '0');
      const off = -d.getTimezoneOffset();
      const sign = off >= 0 ? '+' : '-';
      const ah = pad(Math.floor(Math.abs(off) / 60));
      const am = pad(Math.abs(off) % 60);
      return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
        `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
        `${sign}${ah}:${am}`
      );
    } catch {
      return String(ts);
    }
  }

  // Pick the most useful body text for the clipboard, per event type.
  // Mirrors SearchPanel.previewText so format is consistent across
  // both surfaces.
  function previewBody() {
    switch (kind) {
      case 'turn':
      case 'user_turn':
      case 'compact_summary':
        return p.text_preview ?? '';
      case 'tool_call':
        return `${p.tool ?? '?'}\n${p.input_preview ?? ''}`;
      case 'claim':
        return `${p.text ?? ''}${p.evidence ? '\n\n' + p.evidence : ''}`;
      case 'away_summary':
        return p.content ?? '';
      case 'permission_mode_change':
        return `${p.previous_mode ?? '?'} → ${p.mode ?? '?'}`;
      case 'tool_interrupted':
        return `${p.tool ?? '?'} interrupted${p.permission_mode ? ` (${p.permission_mode} mode)` : ''}`;
      case 'memory_edit':
        return `${p.tool ?? '?'} ${p.path ?? ''}`;
      case 'rubric_sample':
        return `${p.dim ?? '?'}: ${p.score ?? '?'}/5${p.evidence ? ' — ' + p.evidence : ''}`;
      case 'constraint_violation':
        return `${p.rule_text ?? p.rule_id ?? '?'}${p.evidence ? '\n\n' + p.evidence : ''}`;
      case 'drift':
        return `${p.detail ?? ''}${p.corrective ? '\n\n' + p.corrective : ''}`;
      default:
        return JSON.stringify(p, null, 2);
    }
  }

  function buildCopyText() {
    const type = chipLabel[kind] ?? kind;
    const sid = sessionId ? sessionId.slice(0, 8) : '?';
    const head = `[${type} · #${event.id} · session ${sid} · ${fmtIsoTs(event.createdAt)}]`;
    return `${head}\n${previewBody()}`;
  }

  async function copyEvent(e) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(buildCopyText());
      copied = true;
      setTimeout(() => { copied = false; }, 1200);
    } catch (err) {
      console.warn(`[feed-item #${event.id}] copy failed`, err);
    }
  }
</script>

<article class="item" data-kind={kind} id="event-{event.id}">
  <header>
    <span class="chip chip-{kind}">{chipLabel[kind] ?? kind}</span>
    {#if kind === 'constraint_violation' && p.severity}
      <span class="severity sev-{p.severity}">{p.severity}</span>
    {/if}
    {#if kind === 'drift' && p.severity}
      <span class="severity sev-{p.severity}">{p.severity}</span>
    {/if}
    {#if kind === 'claim' && p.status}
      <span class="severity sev-claim-{p.status}">{p.status}</span>
    {/if}
    <span class="grow"></span>
    <code class="eid" title="event id">#{event.id}</code>
    <button
      type="button"
      class="copy-btn"
      class:copied
      onclick={copyEvent}
      title="copy event header + content to clipboard"
      aria-label="copy event"
    >{copied ? '✓' : '⧉'}</button>
    <time>{clock}</time>
    {#if event.deltaText}
      <span class="delta">{event.deltaText}</span>
    {/if}
  </header>

  <div class="body">
    {#if kind === 'turn'}
      <div class="row">
        <span class="muted">turn {p.turn_idx}</span>
        {#if p.usage}
          <span class="usage">
            {#if p.usage.input_tokens}{humanize(p.usage.input_tokens)} in{/if}
            {#if p.usage.output_tokens}<span class="dot">·</span>{humanize(p.usage.output_tokens)} out{/if}
            {#if p.usage.cache_read_input_tokens}<span class="dot">·</span>{humanize(p.usage.cache_read_input_tokens)} cached{/if}
          </span>
        {/if}
        {#if p.stop_reason}
          <span class="muted small">stop: {p.stop_reason}</span>
        {/if}
      </div>
      {#if p.text_preview}
        <button
          type="button"
          class="evidence"
          class:expanded
          onclick={toggleExpanded}
        >{p.text_preview}</button>
      {/if}
    {:else if kind === 'user_turn'}
      {#if p.text_preview}
        <button
          type="button"
          class="evidence"
          class:expanded
          onclick={toggleExpanded}
        >{p.text_preview}</button>
      {/if}
    {:else if kind === 'compact_summary'}
      <div class="row">
        <span class="muted small">
          synthesized handoff (Claude Code <code>/compact</code>) — persisted as a user turn in the
          transcript via <code>isCompactSummary: true</code>. Not typed by the user.
        </span>
      </div>
      {#if p.text_preview}
        <button
          type="button"
          class="evidence"
          class:expanded
          onclick={toggleExpanded}
        >{p.text_preview}</button>
      {/if}
    {:else if kind === 'tool_call'}
      <div class="row">
        <strong>{p.tool}</strong>
        {#if p.input_preview}
          <span class="muted mono">{p.input_preview}</span>
        {/if}
      </div>
    {:else if kind === 'constraint_violation'}
      <div class="row">
        <strong>{p.rule_text || p.rule_id}</strong>
      </div>
      {#if p.evidence}
        <div class="evidence-static">{p.evidence}</div>
      {/if}
      <div class="actions">
        <button class="btn" onclick={() => live.ackViolation(p.id)}>acknowledge</button>
        <button class="btn" onclick={() => live.dismissViolation(p.id)}>dismiss</button>
      </div>
    {:else if kind === 'scope_snapshot'}
      <div class="row">
        <span class="muted">turn {p.turn_idx}</span>
        <span><strong>{p.files_touched ?? 0}</strong> files</span>
        <span class="dot">·</span>
        <span>{humanizeBytes(p.diff_bytes ?? 0)}</span>
        {#if p.baseline != null && p.baseline > 0}
          <span class="dot">·</span>
          <span class="muted">baseline {p.baseline} · threshold {p.threshold}</span>
        {/if}
      </div>
    {:else if kind === 'scope_creep'}
      <div class="row">
        <strong>{p.files_touched}</strong>
        <span class="muted">files exceeded threshold {p.threshold} (baseline {p.baseline})</span>
      </div>
    {:else if kind === 'rubric_sample'}
      <div class="row">
        {#if p.subject === 'user'}
          <span class="subject-tag">self</span>
        {/if}
        <strong>{p.dim}</strong>
        <span class="score">{Number(p.score).toFixed(1)}<span class="muted">/5</span></span>
        <span class="muted">turn {p.turn_idx}</span>
      </div>
      {#if p.evidence}
        <div class="evidence-static">{p.evidence}</div>
      {/if}
      {#if p.suggestion}
        <div class="muted small suggestion">suggestion: {p.suggestion}</div>
      {/if}
      <div class="actions">
        <button class="btn" onclick={() => live.rubricFeedback(p.id, 'disagree')}>disagree</button>
      </div>
    {:else if kind === 'rubric_in_flight'}
      <div class="row">
        <span class="inflight-dot"></span>
        <strong>calling local LLM</strong>
        {#if p.subject === 'user'}
          <span class="subject-tag">self</span>
        {/if}
      </div>
      <div class="muted small">
        turn {p.turn_idx} · {(p.triggers || []).join(', ') || 'cadence'}
        · expect 3-10s for the score to land
      </div>
    {:else if kind === 'rubric_done'}
      {#if p.error}
        <div class="row sev-high"><strong>rubric failed</strong></div>
        <div class="muted small">{p.error}</div>
      {:else}
        <div class="row">
          {#if p.subject === 'user'}
            <span class="subject-tag">self</span>
          {/if}
          <strong>rubric complete</strong>
          <span class="muted small">turn {p.turn_idx}</span>
        </div>
        <div class="muted small">
          per-dimension scores landed in the rubric_sample entries above this one
          {#if p.subject === 'user'}
            · also visible in the Reflection view's self-rubric panel
          {:else}
            · also rolled up in the right-rail rubric averages
          {/if}
        </div>
      {/if}
    {:else if kind === 'drift'}
      <div class="row"><strong>{p.detail}</strong></div>
      {#if p.corrective}
        <div class="evidence-static">{p.corrective}</div>
      {/if}
    {:else if kind === 'claim'}
      <div class="row"><strong>{p.text}</strong></div>
      {#if p.evidence}
        <div class="evidence-static">{p.evidence}</div>
      {/if}
    {:else if kind === 'report_ready'}
      <div class="row">consolidation complete</div>
    {:else if kind === 'session_closed'}
      <div class="muted small">session marked closed</div>
    {:else if kind === 'permission_mode_change'}
      <div class="row">
        <code class="mode-prev">{p.previous_mode}</code>
        <span class="mode-arrow">→</span>
        <code class="mode-next">{p.mode}</code>
      </div>
      <div class="muted small">
        permission mode changed (Shift+Tab in Claude Code cycles through these)
      </div>
    {:else if kind === 'away_summary'}
      <div class="row">
        <span class="muted small">
          Claude Code captured a recap during a quiet period — goal,
          current task, next action. The agent emits these so context
          survives if you step away.
        </span>
      </div>
      {#if p.content}
        <button
          type="button"
          class="evidence"
          class:expanded
          onclick={toggleExpanded}
        >{p.content}</button>
      {/if}
    {:else if kind === 'tool_interrupted'}
      <div class="row">
        {#if p.tool}
          <strong>{p.tool}</strong>
        {/if}
        <span class="muted small">
          tool call interrupted{p.permission_mode ? ` (${p.permission_mode} mode)` : ''}
        </span>
      </div>
    {:else if kind === 'exfiltration_alert'}
      <div class="row">
        <strong>{p.pattern}</strong>
        {#if p.tool}
          <span class="muted small">in {p.tool}</span>
        {/if}
      </div>
      {#if p.redacted}
        <div class="evidence-static mono">{p.redacted}</div>
      {/if}
      <div class="muted small">
        a known secret pattern was detected in tool input — the source
        event's payload was sanitized before storage. verify the
        secret didn't leak elsewhere and rotate if needed.
      </div>
    {:else if kind === 'memory_edit'}
      <div class="row">
        {#if p.tool}
          <strong>{p.tool}</strong>
        {/if}
        {#if p.path}
          <span class="muted mono">{p.path}</span>
        {/if}
      </div>
      <div class="muted small">
        edit landed in a Claude Code memory file. Surfaced for transparency —
        not counted as a constraint violation.
      </div>
    {:else if kind === 'turn_metric'}
      <div class="row">
        <span class="muted">turn {p.turn_idx}</span>
        {#if p.prompt_to_response_ms != null}
          <span class="usage">{Math.round(p.prompt_to_response_ms)} ms ttft</span>
        {/if}
        {#if p.output_tps != null}
          <span class="usage">{p.output_tps.toFixed(1)} tps</span>
        {/if}
        {#if p.cache_hit_ratio != null}
          <span class="muted small">cache {(p.cache_hit_ratio * 100).toFixed(0)}%</span>
        {/if}
      </div>
    {/if}
  </div>
</article>

<style>
  .item {
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    transition: background 120ms ease;
    animation: enter 200ms ease;
  }
  .item:hover { background: var(--surface-2); }
  @keyframes enter {
    from { opacity: 0; transform: translateY(-2px); }
    to { opacity: 1; transform: none; }
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    margin-bottom: 8px;
  }
  .grow { flex: 1; }
  time { color: var(--muted); font-family: var(--mono); }
  .delta { color: var(--muted-deep); font-family: var(--mono); }
  /* Event ID — referenceable token. Small, muted, ``user-select: all``
     so a single click selects the whole ``#<id>`` for copy. Lets the
     user paste an event reference back without going through the
     copy button. */
  .eid {
    font-family: var(--mono);
    color: var(--muted-deep);
    font-size: 10px;
    user-select: all;
  }
  /* Per-row copy button — same icon language as the search panel for
     consistency. Faded by default; on header hover it lights up so
     it's discoverable without dominating the row. */
  .copy-btn {
    background: transparent;
    border: 0;
    color: var(--muted-deep);
    cursor: pointer;
    font-size: 12px;
    padding: 2px 4px;
    line-height: 1;
    border-radius: 3px;
    transition: color 120ms, background 120ms, opacity 120ms;
    opacity: 0.5;
  }
  header:hover .copy-btn { opacity: 1; }
  .copy-btn:hover { color: var(--text); background: var(--surface-2); }
  .copy-btn.copied { color: var(--ok); opacity: 1; }

  .chip {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid transparent;
  }
  .chip-turn         { background: rgba(150,144,248,0.10); color: var(--violet); border-color: rgba(150,144,248,0.25); }
  .chip-user_turn    { background: rgba(95,195,167,0.10); color: var(--ok); border-color: rgba(95,195,167,0.25); }
  .chip-tool_call    { background: var(--surface-2); color: var(--muted); border-color: var(--border); }
  .chip-constraint_violation { background: rgba(232,122,122,0.12); color: var(--err); border-color: rgba(232,122,122,0.30); }
  .chip-scope_snapshot { background: rgba(95,195,167,0.06); color: var(--ok); border-color: rgba(95,195,167,0.18); }
  .chip-scope_creep  { background: rgba(230,192,84,0.12); color: var(--warn); border-color: rgba(230,192,84,0.30); }
  .chip-rubric_sample, .chip-rubric_in_flight, .chip-rubric_done {
    background: rgba(150,144,248,0.06); color: var(--violet); border-color: rgba(150,144,248,0.18);
  }
  .chip-drift        { background: rgba(230,192,84,0.10); color: var(--warn); border-color: rgba(230,192,84,0.25); }
  .chip-claim        { background: rgba(232,122,122,0.10); color: var(--err); border-color: rgba(232,122,122,0.25); }
  .chip-report_progress, .chip-report_ready { background: rgba(232,153,104,0.10); color: var(--accent); border-color: rgba(232,153,104,0.25); }
  .chip-session_closed { background: var(--surface-2); color: var(--muted); border-color: var(--border); }
  /* Synthesized turns sit visually between user_turn and a system marker —
     warm copper-tinted so the user notices it isn't them, with a dashed
     border so it reads as "different shape" not "alarming". */
  .chip-compact_summary {
    background: rgba(232,153,104,0.08);
    color: var(--accent);
    border-color: rgba(232,153,104,0.35);
    border-style: dashed;
  }
  .chip-turn_metric {
    background: rgba(150,144,248,0.06);
    color: var(--violet);
    border-color: rgba(150,144,248,0.18);
  }
  /* Search-result deep-link highlight. The :target pseudo-class
     fires when the URL hash matches the element id. One-shot flash
     animation only — no persistent border — so once the user has
     spotted the event the visual returns to normal. The URL hash
     itself persists for bookmarking / sharing. */
  .item:target {
    animation: search-target-flash 1.6s ease-out;
  }
  @keyframes search-target-flash {
    0%   { background: rgba(232,153,104,0.18); }
    100% { background: transparent; }
  }
  /* Permission-mode change reads as a state indicator (neither alarming
     nor decisive). Slate palette mirrors the closed-session badge —
     state-of-the-session signal, not a behavioral verdict. */
  .chip-permission_mode_change {
    background: rgba(102,117,140,0.10);
    color: #8a96a8;
    border-color: rgba(102,117,140,0.30);
  }
  /* Away-summary is a system-generated state recap. Same slate family
     as permission_mode_change (both are "state indicators") but with
     a softer dotted border so it reads as "system observation" rather
     than "user action / mode shift." */
  .chip-away_summary {
    background: rgba(102,117,140,0.06);
    color: #8a96a8;
    border-color: rgba(102,117,140,0.25);
    border-style: dotted;
  }
  /* tool_interrupted is the rare explicit "user denied" decision —
     warm warn palette (matches scope_creep) so it reads as a notable
     audit event without being alarming. Distinct from constraint_violation
     (red/error) since interruptions aren't violations, they're
     decisions. */
  .chip-tool_interrupted {
    background: rgba(230,192,84,0.08);
    color: var(--warn);
    border-color: rgba(230,192,84,0.25);
  }
  /* exfiltration_alert is genuinely alarming — a real secret was
     about to land in the audit log. Red/error palette to signal
     "you should look at this and probably rotate." */
  .chip-exfiltration_alert {
    background: rgba(232,122,122,0.12);
    color: var(--err);
    border-color: rgba(232,122,122,0.30);
  }
  /* memory_edit is a calibration moment, not a violation. Teal-ish
     palette aligned with user_turn / scope_snapshot (positive-but-
     muted) so it reads as "noted, not flagged." Distinct from the
     slate state-indicator family (permission_mode_change /
     away_summary) since this is *the user acting on the agent*, not
     a state observation. */
  .chip-memory_edit {
    background: rgba(95,195,167,0.06);
    color: var(--ok);
    border-color: rgba(95,195,167,0.22);
  }
  .mode-prev, .mode-next {
    font-family: var(--mono);
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--surface-2);
    color: var(--text-soft);
    border: 1px solid var(--border);
  }
  .mode-arrow {
    color: var(--muted);
    font-size: 12px;
  }
  /* User-side rubric tag — distinguishes self-rubric samples from
     the assistant-side rubric they share an event type with. */
  .subject-tag {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 3px;
    background: rgba(95,195,167,0.10);
    color: var(--ok);
    border: 1px solid rgba(95,195,167,0.25);
  }
  /* Visible "still working" dot for rubric_in_flight events. The
     point is fail-loudly: a Qwen call is up, this might take 3-10s,
     don't think the daemon's wedged. */
  .inflight-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--violet);
    box-shadow: 0 0 6px rgba(150,144,248,0.5);
    animation: pulse 1.4s ease-in-out infinite;
    align-self: center;
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.5; transform: scale(0.85); }
    50%      { opacity: 1; transform: scale(1.15); }
  }

  .severity {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 1px 5px;
    border-radius: 3px;
    background: var(--surface-2);
  }
  .sev-high { color: var(--err); background: rgba(232,122,122,0.10); }
  .sev-med  { color: var(--warn); background: rgba(230,165,84,0.10); }
  .sev-low  { color: var(--muted); }
  .sev-claim-contradicted { color: var(--err); background: rgba(232,122,122,0.10); }
  .sev-claim-verified     { color: var(--ok); background: rgba(94,197,179,0.10); }

  .body { font-size: 13px; color: var(--text); }
  .row { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .muted { color: var(--muted); }
  .muted-deep { color: var(--muted-deep); }
  .small { font-size: 11px; }
  .mono { font-family: var(--mono); font-size: 12px; }
  .dot { color: var(--muted-deep); }
  .usage { font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .score { font-family: var(--mono); font-size: 13px; color: var(--violet); font-weight: 600; }
  .suggestion { margin-top: 4px; }

  /* Evidence: prose / quoted body. Cap height; click to expand. */
  .evidence,
  .evidence-static {
    margin-top: 8px;
    padding: 10px 14px;
    background: var(--surface-2);
    border-left: 2px solid var(--border-strong);
    border-radius: 4px;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--text-soft);
    text-align: left;
    width: 100%;
    display: block;
    user-select: text;
    -webkit-user-select: text;
  }
  .evidence {
    border: 0;
    border-left: 2px solid var(--border-strong);
    cursor: pointer;
    max-height: 16em;
    overflow: hidden;
    position: relative;
    transition: max-height 200ms ease;
    /* Make button text selectable for copy/paste. Default user-agent
       styles on <button> can disable text selection (notably Safari).
       The click-guard in toggleExpanded prevents click-to-toggle from
       firing when text is selected. */
    user-select: text;
    -webkit-user-select: text;
  }
  .evidence:not(.expanded)::after {
    content: '';
    position: absolute;
    inset: auto 0 0 0;
    height: 2.5em;
    background: linear-gradient(transparent, var(--surface-2));
    pointer-events: none;
  }
  .evidence.expanded { max-height: none; overflow: visible; }
  .evidence:hover { background: var(--surface-3); }

  .actions { margin-top: 8px; display: flex; gap: 6px; }
  .btn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 3px 9px;
    font-size: 11px;
    border-radius: 4px;
    cursor: pointer;
    font-family: inherit;
    transition: color 120ms, border-color 120ms;
  }
  .btn:hover { color: var(--text); border-color: var(--text-soft); }
</style>
