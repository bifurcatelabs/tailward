<script>
  // Inline session-level synth trigger. Pill-shaped with a faint
  // accent glow so it reads as "action" against the muted-border
  // filter pills around it but doesn't shout. Lives in the Filter &
  // Actions card header (next to the card label).

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

<button
  class="synth"
  onclick={fire}
  disabled={synthesizing}
  title={lastResult || 'fire a comprehensive synthesis now'}
>
  {synthesizing ? 'synthesizing…' : 'synthesize now'}
</button>

<style>
  .synth {
    background: rgba(232,153,104,0.06);
    border: 1px solid rgba(232,153,104,0.40);
    color: var(--accent);
    padding: 3px 10px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    border-radius: 999px;
    box-shadow: 0 0 12px rgba(232,153,104,0.10);
    cursor: pointer;
    font-family: inherit;
    transition:
      background 140ms ease,
      box-shadow 140ms ease,
      color 140ms ease;
  }
  .synth:hover:not(:disabled) {
    background: rgba(232,153,104,0.14);
    box-shadow: 0 0 18px rgba(232,153,104,0.22);
    color: var(--accent-soft);
  }
  .synth:active:not(:disabled) {
    background: rgba(232,153,104,0.20);
  }
  .synth:disabled {
    opacity: 0.6;
    cursor: default;
    box-shadow: none;
  }
</style>
