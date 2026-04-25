<script>
  import { live } from './live.svelte.js';
  let { ph, sessionId } = $props();
</script>

<header class="bar">
  <div class="brand">
    <span class="dot"></span>
    <span class="name">warden</span>
    <span class="tag">v0.2</span>
  </div>

  <div class="session">
    <span class="muted">session</span>
    <code title={sessionId}>{sessionId.slice(0, 8)}</code>
    {#if live.model}
      <span class="sep">·</span>
      <code>{live.model}</code>
    {/if}
  </div>

  <div class="conn conn-{live.conn}">
    <span class="pulse"></span>
    {live.conn}
  </div>
</header>

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 14px 24px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, var(--surface) 0%, var(--bg) 100%);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 12px var(--accent);
  }
  .name {
    font-weight: 600;
    letter-spacing: 0.02em;
    font-size: 14px;
  }
  .tag {
    color: var(--muted);
    font-size: 11px;
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
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
  .muted { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
  .sep { color: var(--muted-deep); }
  .conn {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .pulse {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--muted-deep);
  }
  .conn-live .pulse {
    background: var(--ok);
    animation: pulse 2s ease-in-out infinite;
  }
  .conn-live { color: var(--ok); }
  .conn-polling .pulse { background: var(--warn); }
  .conn-polling { color: var(--warn); }
  .conn-offline .pulse { background: var(--err); }
  .conn-offline { color: var(--err); }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.55; transform: scale(1.4); }
  }
</style>
