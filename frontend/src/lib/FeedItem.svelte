<script>
  import { live } from './live.svelte.js';
  import { fmtClock, humanize, humanizeBytes, toEpochSeconds } from './format.js';

  let { event } = $props();
  let expanded = $state(false);

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
  };

  let p = $derived(event.payload || {});
  let kind = $derived(event.eventType);

  // Local-time clock derived from the event's createdAt, falling back
  // to render-time if missing.
  let clock = $derived.by(() => {
    const sec = toEpochSeconds(event.createdAt);
    return sec ? fmtClock(new Date(sec * 1000)) : fmtClock();
  });
</script>

<article class="item" data-kind={kind}>
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
          onclick={() => (expanded = !expanded)}
        >{p.text_preview}</button>
      {/if}
    {:else if kind === 'user_turn'}
      {#if p.text_preview}
        <button
          type="button"
          class="evidence"
          class:expanded
          onclick={() => (expanded = !expanded)}
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
          onclick={() => (expanded = !expanded)}
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
      <div class="muted small">turn {p.turn_idx} · {(p.triggers || []).join(', ')}</div>
    {:else if kind === 'rubric_done'}
      {#if p.error}
        <div class="row sev-high">{p.error}</div>
      {:else}
        <div class="muted small">turn {p.turn_idx}</div>
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
  }
  .evidence {
    border: 0;
    border-left: 2px solid var(--border-strong);
    cursor: pointer;
    max-height: 16em;
    overflow: hidden;
    position: relative;
    transition: max-height 200ms ease;
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
