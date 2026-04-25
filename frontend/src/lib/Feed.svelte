<script>
  import { live } from './live.svelte.js';
  import FeedItem from './FeedItem.svelte';

  // Newest-first iteration. Svelte's keyed each block plays nicely
  // with the reactive ``live.events`` array; only newly appended
  // entries trigger inserts at the top.
  let reversed = $derived([...live.events].reverse());
</script>

<section class="feed">
  {#if reversed.length === 0}
    <div class="empty">
      <div class="hint">waiting for events…</div>
      <div class="muted">the watcher will surface turns, tool calls, and findings as the session writes to its transcript.</div>
    </div>
  {:else}
    {#each reversed as ev (ev.id)}
      <FeedItem event={ev} />
    {/each}
  {/if}
</section>

<style>
  .feed {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .empty {
    padding: 32px;
    text-align: center;
  }
  .hint {
    font-size: 13px;
    color: var(--text-soft);
    margin-bottom: 6px;
  }
  .muted {
    font-size: 12px;
    color: var(--muted);
    max-width: 360px;
    margin: 0 auto;
    line-height: 1.5;
  }
</style>
