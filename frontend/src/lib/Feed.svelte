<script>
  import { live } from './live.svelte.js';
  import FeedItem from './FeedItem.svelte';

  // Newest-first iteration. Svelte's keyed each block plays nicely
  // with the reactive ``live.events`` array; only newly appended
  // entries trigger inserts at the top, and ``loadOlder`` prepends
  // (in store order) so they land at the bottom of the rendered list.
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

    {#if live.hasMoreOlder}
      <div class="load-older">
        <button
          type="button"
          onclick={() => live.loadOlder()}
          disabled={live.loadingOlder}
        >
          {live.loadingOlder ? 'loading…' : 'load older'}
        </button>
        <span class="muted">earliest shown · id {live.oldestEventId}</span>
      </div>
    {:else if reversed.length > 100}
      <div class="load-older">
        <span class="muted">start of session</span>
      </div>
    {/if}
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
    line-height: 1.5;
  }
  .empty .muted {
    max-width: 360px;
    margin: 0 auto;
  }
  .load-older {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 14px;
    border-top: 1px solid var(--border);
    background: var(--bg);
  }
  .load-older button {
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--text-soft);
    padding: 6px 14px;
    font-size: 11px;
    border-radius: 999px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    transition: color 120ms, border-color 120ms;
  }
  .load-older button:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--accent);
  }
  .load-older button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .load-older .muted {
    font-size: 11px;
    font-family: var(--mono);
  }
</style>
