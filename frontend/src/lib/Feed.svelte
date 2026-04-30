<script>
  import { live } from './live.svelte.js';
  import { feedFilter, FILTER_GROUPS } from './feedFilter.svelte.js';
  import FeedItem from './FeedItem.svelte';

  let { sessionId = null } = $props();

  // Newest-first iteration. Svelte's keyed each block plays nicely
  // with the reactive ``live.events`` array; only newly appended
  // entries trigger inserts at the top, and ``loadOlder`` prepends
  // (in store order) so they land at the bottom of the rendered list.
  //
  // Filter pills are multi-toggle and shared with SessionTimeline
  // via ``feedFilter`` — selecting "user" + "assistant" pills here
  // also narrows the strip above. Empty active set = show
  // everything; the "all" pill is the inverse signal — it lights up
  // when no group is active and clears the set when clicked.
  let reversed = $derived.by(() => {
    const arr = [...live.events].reverse();
    if (feedFilter.empty()) return arr;
    return arr.filter((ev) => feedFilter.matches(ev));
  });

  let totalCount = $derived(live.events.length);
  let visibleCount = $derived(reversed.length);

  // Deep-link target for the search panel — clicking a search result
  // sets the URL hash to ``#event-<id>`` and the matching FeedItem's
  // article element gets scrolled into view + receives the :target
  // highlight. Re-fires whenever the events array changes so a result
  // that was outside the loaded window scrolls into view once it's
  // pulled in via load-older.
  $effect(() => {
    if (typeof window === 'undefined') return;

    function scrollToHashTarget() {
      const m = window.location.hash.match(/^#event-(\d+)$/);
      if (!m) return;
      // Defer one tick so a freshly-rendered element is in the DOM
      // before we measure / scroll.
      setTimeout(() => {
        const el = document.getElementById(m[0].slice(1));
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 50);
    }

    // Initial fire (page load with hash) + every hashchange.
    scrollToHashTarget();
    window.addEventListener('hashchange', scrollToHashTarget);
    return () => window.removeEventListener('hashchange', scrollToHashTarget);
  });

  // Re-scroll when the events list grows / shrinks — handles the case
  // where a search result for an older event lands after load-older
  // pulls more rows in.
  $effect(() => {
    void live.events.length;
    if (typeof window === 'undefined') return;
    if (!window.location.hash.startsWith('#event-')) return;
    setTimeout(() => {
      const el = document.getElementById(window.location.hash.slice(1));
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 50);
  });

  // Clear the active filter when SearchPanel asks ("view in session"
  // click). Without this, a search result for, say, a user_turn would
  // be hidden behind a "tools-only" filter — the FeedItem wouldn't
  // render, so the scroll-to-hash would silently no-op.
  $effect(() => {
    if (typeof window === 'undefined') return;
    function clearFilter() { feedFilter.clear(); }
    window.addEventListener('search:clear-filter', clearFilter);
    return () => window.removeEventListener('search:clear-filter', clearFilter);
  });
</script>

<section class="feed">
  <div class="filter-bar">
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
    <span class="filter-count">
      {#if feedFilter.empty()}
        {totalCount} event{totalCount === 1 ? '' : 's'}
      {:else}
        {visibleCount} / {totalCount}
      {/if}
    </span>
  </div>

  {#if reversed.length === 0}
    <div class="empty">
      {#if feedFilter.empty()}
        <div class="hint">waiting for events…</div>
        <div class="muted">the watcher will surface turns, tool calls, and findings as the session writes to its transcript.</div>
      {:else}
        <div class="hint">no events match the active filter{feedFilter.active.size === 1 ? '' : 's'}</div>
        <div class="muted">try toggling other pills or click "all" to clear.</div>
      {/if}
    </div>
  {:else}
    {#each reversed as ev (ev.id)}
      <FeedItem event={ev} {sessionId} />
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
  .filter-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 10px 14px;
    background: var(--surface-2);
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
  .filter-count {
    margin-left: auto;
    color: var(--muted-deep);
    font-size: 10px;
    font-family: var(--mono);
    text-transform: uppercase;
    letter-spacing: 0.08em;
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
