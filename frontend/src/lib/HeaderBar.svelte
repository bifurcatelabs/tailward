<script>
  import { live } from './live.svelte.js';
  import SessionPicker from './SessionPicker.svelte';
  import SearchPanel from './SearchPanel.svelte';
  let { ph, sessionId } = $props();

  // Tick once per second so the "last contact" relative time updates
  // without us reaching for an extra dependency. Cheap; `now` reads
  // are batched into Svelte's reactivity graph.
  let now = $state(Math.floor(Date.now() / 1000));
  $effect(() => {
    const id = setInterval(() => {
      now = Math.floor(Date.now() / 1000);
    }, 1000);
    return () => clearInterval(id);
  });

  let contactText = $derived.by(() => {
    if (live.lastContactAt == null) return 'awaiting first event';
    const delta = Math.max(0, Math.floor(now - live.lastContactAt));
    if (delta < 5) return 'just now';
    if (delta < 60) return `${delta}s ago`;
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    return `${Math.floor(delta / 3600)}h ago`;
  });

  // Pulse color tracks staleness, not raw connection state. The user
  // cares whether events are arriving — "polling" is fine if events
  // still land within the polling cadence.
  let staleness = $derived.by(() => {
    if (live.lastContactAt == null) return 'pending';
    const delta = Math.max(0, Math.floor(now - live.lastContactAt));
    if (delta < 30) return 'fresh';
    if (delta < 180) return 'idle';
    return 'stale';
  });
</script>

<header class="bar">
  <div class="brand">
    <svg class="mark" viewBox="0 0 32 32" aria-hidden="true">
      <!-- tw monogram: T descender flows into the W's middle peak.
           Crossbar sits slightly off-center to the left of the
           descender (asymmetric, avoids reading as the .tw / TW
           Taiwan abbreviation). W's outer peaks sit higher than the
           middle to make the T-to-W transition visible. -->
      <defs>
        <linearGradient id="markFill" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.55"/>
          <stop offset="100%" stop-color="var(--accent-soft)" stop-opacity="1"/>
        </linearGradient>
      </defs>
      <path
        d="M2 6 L18 6 M11 6 L11 13 M4 8 L7 24 L11 13 L15 24 L18 8"
        fill="none"
        stroke="url(#markFill)"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <a href="/" class="name" style="text-decoration:none;color:inherit">tailward</a>
    <span class="version">2.10.0</span>
  </div>

  <div class="session">
    <span class="muted">session</span>
    <code title={sessionId}>{sessionId.slice(0, 8)}</code>
    {#if live.closeStatus === 'closed' || live.closeStatus === 'consolidated' || live.closeStatus === 'done'}
      <span
        class="closed-badge"
        title="this session has been auto-closed by the consolidator after 10+ minutes idle. JSONL is no longer being written; new events will not arrive."
      >closed</span>
    {/if}
    <span class="muted">·</span>
    <span class="muted">project</span>
    <code class="thin" title={ph}>{ph.slice(0, 8)}</code>
    {#if live.sessionMode || (live.modeProfile && !live.modeProfile.is_default)}
      <span class="muted">·</span>
      <span class="muted">mode</span>
      <code
        class="mode"
        class:mode-default={live.modeProfile?.is_default}
        title={live.modeProfile?.description || 'permissive default profile'}
      >{live.sessionMode || live.modeProfile?.name || '—'}</code>
    {:else if live.modeProfile?.is_default}
      <span class="muted">·</span>
      <span class="muted">mode</span>
      <code class="mode mode-default" title={live.modeProfile.description}>default</code>
    {/if}
  </div>

  <SessionPicker {ph} {sessionId} />

  <SearchPanel {ph} currentSessionId={sessionId} />

  <div
    class="contact stale-{staleness}"
    title={`stream: ${live.conn} · last event ${contactText}`}
  >
    <span class="pulse"></span>
    <span class="contact-label">last event</span>
    <span class="contact-when">{contactText}</span>
  </div>
</header>

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(8px);
    /* Sit above page content (charts, canvases) so the picker
       panel can overlay the body cleanly. */
    position: relative;
    z-index: 100;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .mark {
    width: 22px;
    height: 22px;
    filter: drop-shadow(0 0 6px var(--accent-glow));
  }
  .name {
    font-weight: 600;
    letter-spacing: 0.01em;
    font-size: 15px;
    color: var(--text);
  }
  .version {
    color: var(--muted);
    font-size: 10px;
    font-family: var(--mono);
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    letter-spacing: 0.04em;
  }
  .session {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }
  .session code {
    font-family: var(--mono);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 7px;
    color: var(--text);
    font-size: 11px;
  }
  .session code.thin {
    background: transparent;
    color: var(--muted);
  }
  .session code.mode {
    background: rgba(232,153,104,0.10);
    border-color: rgba(232,153,104,0.30);
    color: var(--accent);
  }
  .session code.mode.mode-default {
    background: var(--surface-2);
    border-color: var(--border);
    color: var(--muted);
  }
  .closed-badge {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 7px;
    border-radius: 999px;
    /* Cool slate — reads as "archived / dormant" rather than warning
       (amber) or error (red). Dashed border reinforces "not active."
       Subtle desaturation distinguishes it from the warm copper /
       violet palette used for active surfaces. */
    background: rgba(102,117,140,0.10);
    color: #8a96a8;
    border: 1px dashed rgba(102,117,140,0.40);
    cursor: help;
  }
  .muted {
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .contact {
    display: flex;
    align-items: baseline;
    gap: 6px;
    font-size: 11px;
    color: var(--muted);
  }
  .contact-label {
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 10px;
    color: var(--muted-deep);
  }
  .contact-when {
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    color: var(--text-soft);
    min-width: 70px;
  }
  .pulse {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    align-self: center;
    background: var(--muted-deep);
  }
  /* Pulse color tracks staleness, not raw connection state. The user
     cares whether events are arriving — pretty pulse for fresh, dim
     for idle, red for genuinely stuck. */
  .stale-fresh .pulse {
    background: var(--ok);
    box-shadow: 0 0 8px rgba(95,195,167,0.6);
    animation: pulse 2s ease-in-out infinite;
  }
  .stale-fresh .contact-when { color: var(--ok); }
  .stale-idle .pulse { background: var(--muted); }
  .stale-stale .pulse { background: var(--err); }
  .stale-stale .contact-when { color: var(--err); }
  .stale-pending .pulse { background: var(--muted-deep); }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.55; transform: scale(1.4); }
  }

  /* Narrow-width pass — main window in assistant-sized mode
     (~480–720px). Two rows: brand-left | picker+search-right on
     row 1, last-event-left | session/project/mode-right on row 2.
     Float-right is via flex order + margin-left:auto on the first
     right-side item per row. Version badge drops as decorative. */
  @media (max-width: 720px) {
    .bar {
      flex-wrap: wrap;
      gap: 8px 10px;
      padding: 12px 16px;
      align-items: center;
    }
    .version { display: none; }
    .brand { order: 1; }
    /* SessionPicker + SearchPanel render their own .picker / .search
       roots; Svelte's scoped CSS won't add this component's hash to
       those (they're rendered inside child components), so target
       via :global. Picker gets margin-left:auto to push itself + the
       trailing search to the right of brand on row 1. */
    .bar > :global(.picker) { order: 2; margin-left: auto; }
    .bar > :global(.search) { order: 3; }
    .contact { order: 4; font-size: 10px; gap: 4px; }
    .session {
      order: 5;
      margin-left: auto;
      font-size: 10px;
      gap: 6px;
    }
    .session code {
      padding: 2px 5px;
      font-size: 10px;
    }
    .contact-when { min-width: 50px; }
  }
</style>
