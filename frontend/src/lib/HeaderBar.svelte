<script>
  import { live } from './live.svelte.js';
  import SessionPicker from './SessionPicker.svelte';
  let { ph, sessionId } = $props();
</script>

<header class="bar">
  <div class="brand">
    <svg class="mark" viewBox="0 0 32 32" aria-hidden="true">
      <!-- Stylized monogram: a "W" rendered as three rising chevrons,
           lit from the left. Matches the copper accent. -->
      <defs>
        <linearGradient id="markFill" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.55"/>
          <stop offset="100%" stop-color="var(--accent-soft)" stop-opacity="1"/>
        </linearGradient>
      </defs>
      <path
        d="M3 9 L8 23 L13 13 L18 23 L23 9 M14 22 L19 12 L24 22"
        fill="none"
        stroke="url(#markFill)"
        stroke-width="2.2"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <span class="name">warden</span>
    <span class="version">0.2</span>
  </div>

  <div class="session">
    <span class="muted">session</span>
    <code title={sessionId}>{sessionId.slice(0, 8)}</code>
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

  <div class="conn conn-{live.conn}">
    <span class="pulse"></span>
    <span class="conn-label">{live.conn}</span>
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
  .muted {
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .conn {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .pulse {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--muted-deep);
  }
  .conn-live .pulse {
    background: var(--ok);
    box-shadow: 0 0 8px rgba(95,195,167,0.6);
    animation: pulse 2s ease-in-out infinite;
  }
  .conn-live { color: var(--ok); }
  .conn-polling .pulse { background: var(--warn); }
  .conn-polling { color: var(--warn); }
  .conn-offline .pulse { background: var(--err); }
  .conn-offline { color: var(--err); }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.55; transform: scale(1.4); }
  }
</style>
