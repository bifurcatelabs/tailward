<script>
  // Minimal inline affordance to fire a comprehensive synth from the
  // live page without opening the full Synthesis surface. The full
  // panel (snapshot list, regenerate, intent.md viewer) lives on the
  // Synthesis tab and pops out in its own window via #30.

  let { ph, sessionId } = $props();

  let synthesizing = $state(false);
  let lastResult = $state(null);

  async function fire() {
    if (synthesizing || !ph || !sessionId) return;
    synthesizing = true;
    lastResult = null;
    try {
      const r = await fetch(`/p/${ph}/live/${sessionId}/synthesize`, {
        method: 'POST',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      lastResult = data?.trigger ? `${data.trigger} captured` : 'captured';
    } catch (e) {
      lastResult = `failed: ${String(e).slice(0, 80)}`;
    } finally {
      synthesizing = false;
    }
  }
</script>

<div class="trigger">
  <button onclick={fire} disabled={synthesizing}>
    {synthesizing ? 'synthesizing…' : 'synthesize now'}
  </button>
  {#if lastResult}
    <span class="result">{lastResult}</span>
  {/if}
  <span class="muted">full snapshot history on the synthesis tab</span>
</div>

<style>
  .trigger {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 24px 0;
    font-size: 11px;
  }
  button {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text-soft);
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
  }
  button:hover:not(:disabled) {
    background: var(--surface-3);
    color: var(--text);
  }
  button:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .result {
    color: var(--muted);
    font-family: var(--mono);
  }
  .muted {
    color: var(--muted-deep);
    margin-left: auto;
  }
  @media (max-width: 720px) {
    .trigger { padding: 8px 16px 0; font-size: 10px; }
    .muted { display: none; }
  }
</style>
