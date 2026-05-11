<script>
  // Event-type filter pills, extracted from SessionTimeline and Feed
  // so the live page renders them ONCE between the arc and the feed.
  // Both surfaces read from the same ``feedFilter`` store, so a pill
  // toggle here narrows both above (arc ticks dim) and below (feed
  // rows hide). One control, two reactive surfaces.

  import { feedFilter, FILTER_GROUPS } from './feedFilter.svelte.js';
  import { live } from './live.svelte.js';

  // Total / visible counts so the user has a sense of "how aggressive
  // is my filter." Cheap derivations off the live store; matches
  // Feed's prior counter behavior.
  let totalCount = $derived(live.events?.length ?? 0);
  let visibleCount = $derived(
    feedFilter.empty()
      ? totalCount
      : live.events.filter((ev) => feedFilter.matches(ev)).length
  );
</script>

<section class="strip" role="group" aria-label="event type filter">
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
  <span class="count">
    {#if feedFilter.empty()}
      {totalCount} event{totalCount === 1 ? '' : 's'}
    {:else}
      {visibleCount} / {totalCount}
    {/if}
  </span>
</section>

<style>
  .strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 10px 24px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
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
  .count {
    margin-left: auto;
    color: var(--muted-deep);
    font-size: 10px;
    font-family: var(--mono);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  @media (max-width: 720px) {
    .strip { padding: 8px 16px; gap: 4px; }
  }
</style>
