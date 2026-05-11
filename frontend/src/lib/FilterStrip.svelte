<script>
  // Filter & Actions card — event-type filter pills shared with both
  // the arc above and the feed below, plus the session-level
  // synthesize trigger in the header. Pills + trigger are session-
  // scoped controls; the live event counter moved to the Feed card.

  import { feedFilter, FILTER_GROUPS } from './feedFilter.svelte.js';
  import SynthTrigger from './SynthTrigger.svelte';

  let { ph = '', sessionId = '' } = $props();
</script>

<section class="strip" role="group" aria-label="event type filter">
  <header>
    <span class="label">filter &amp; actions</span>
    {#if ph && sessionId}
      <SynthTrigger {ph} {sessionId} />
    {/if}
  </header>
  <div class="pills">
    <button
      type="button"
      class="pill"
      class:active={feedFilter.empty()}
      onclick={() => feedFilter.clear()}
      title="show every event type (clear all filter pills)"
    >all</button>
    {#each FILTER_GROUPS as g (g.key)}
      <button
        type="button"
        class="pill pill-{g.key}"
        class:active={feedFilter.isActive(g.key)}
        onclick={() => feedFilter.toggle(g.key)}
        title={g.description}
      >{g.label}</button>
    {/each}
  </div>
</section>

<style>
  .strip {
    margin: 16px 24px 0;
    padding: 14px 18px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 12px;
    flex-wrap: wrap;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 600;
  }
  .pills {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .pill {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 3px 10px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    border-radius: 999px;
    cursor: pointer;
    font-family: inherit;
    transition: color 120ms, border-color 120ms, background 120ms;
  }
  .pill:hover { color: var(--text-soft); border-color: var(--text-soft); }
  .pill.active {
    color: var(--accent);
    border-color: rgba(232,153,104,0.40);
    background: rgba(232,153,104,0.08);
  }
  @media (max-width: 720px) {
    .strip { margin: 12px 16px 0; padding: 12px 14px; }
  }
</style>
