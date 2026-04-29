<script>
  /**
   * Project-scoped substring search.
   *
   * Mounted in the session HeaderBar. Searches `/p/<ph>/search?q=...`
   * across content-bearing live_events (user / assistant turns, tool
   * calls, synthesized turns, claims, away-summaries). Surfaces a turn
   * the user remembers but can't pinpoint — example queries are short
   * literal strings ("memory", "rebrand", "permissionMode").
   *
   * UX: typing debounces (250ms), results render in a popover below
   * the input. Clicking a result navigates to the session that
   * contained the match. Cross-session results are common — recall
   * isn't usually session-bound.
   */
  let { ph, currentSessionId } = $props();

  let query = $state('');
  let results = $state([]);
  let loading = $state(false);
  let open = $state(false);
  let error = $state(null);

  let debounceTimer = null;

  function shortSession(sid) {
    return sid ? sid.slice(0, 8) : '?';
  }

  function fmtTs(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      return d.toLocaleString();
    } catch {
      return ts;
    }
  }

  // Map event type to a short, friendly label. Mirrors FeedItem's
  // chipLabel but with content-search emphasis.
  const typeLabel = {
    user_turn: 'user',
    turn: 'assistant',
    tool_call: 'tool',
    compact_summary: 'synthesized',
    claim: 'claim',
    away_summary: 'away',
  };

  async function runSearch(q) {
    if (!q || q.trim().length < 2) {
      results = [];
      open = false;
      return;
    }
    loading = true;
    error = null;
    try {
      const r = await fetch(`/p/${ph}/search?q=${encodeURIComponent(q)}&limit=30`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      results = data.results || [];
      open = true;
    } catch (e) {
      error = String(e);
      results = [];
    } finally {
      loading = false;
    }
  }

  function onInput(e) {
    query = e.target.value;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => runSearch(query), 250);
  }

  function onSubmit(e) {
    e.preventDefault();
    clearTimeout(debounceTimer);
    runSearch(query);
  }

  function urlFor(r) {
    return `/p/${ph}/live/${r.session_id}`;
  }

  function onResultClick(r, e) {
    // If clicking a result for the current session, just close the
    // popover (the user's already on that page; full navigation
    // would lose any panel state). Otherwise, follow the link.
    if (r.session_id === currentSessionId) {
      e.preventDefault();
      open = false;
    }
  }

  function clearSearch() {
    query = '';
    results = [];
    open = false;
  }

  // Close popover on outside click. Keep the input itself responsive.
  let panelEl;
  function onDocClick(e) {
    if (!panelEl || !panelEl.contains(e.target)) {
      open = false;
    }
  }
  $effect(() => {
    if (typeof document === 'undefined') return;
    document.addEventListener('click', onDocClick);
    return () => document.removeEventListener('click', onDocClick);
  });
</script>

<div class="search" bind:this={panelEl}>
  <form onsubmit={onSubmit} class="form">
    <input
      type="text"
      placeholder="search this project…"
      value={query}
      oninput={onInput}
      onfocus={() => { if (results.length) open = true; }}
      class="input"
      autocomplete="off"
      spellcheck="false"
    />
    {#if query}
      <button type="button" class="clear" onclick={clearSearch} title="clear">×</button>
    {/if}
  </form>

  {#if open && (loading || results.length > 0 || error)}
    <div class="popover">
      {#if loading}
        <div class="status">searching…</div>
      {:else if error}
        <div class="status err">{error}</div>
      {:else if results.length === 0}
        <div class="status">no matches</div>
      {:else}
        <div class="result-count">{results.length} match{results.length === 1 ? '' : 'es'}</div>
        {#each results as r (r.event_id)}
          <a
            class="result"
            class:active={r.session_id === currentSessionId}
            href={urlFor(r)}
            onclick={(e) => onResultClick(r, e)}
          >
            <div class="result-head">
              <span class="type-tag type-{r.event_type}">{typeLabel[r.event_type] ?? r.event_type}</span>
              <code class="sid" title={r.session_id}>{shortSession(r.session_id)}</code>
              <span class="ts">{fmtTs(r.created_at)}</span>
            </div>
            <div class="snippet">{r.snippet}</div>
          </a>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .search {
    position: relative;
    display: inline-flex;
  }
  .form {
    display: flex;
    align-items: center;
  }
  .input {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 10px;
    color: var(--text);
    font-family: inherit;
    font-size: 12px;
    width: 200px;
    outline: none;
    transition: border-color 120ms;
  }
  .input:focus { border-color: var(--accent); }
  .input::placeholder { color: var(--muted-deep); }
  .clear {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    margin-left: -22px;
    width: 20px;
    font-size: 14px;
    padding: 0;
  }
  .clear:hover { color: var(--text); }

  .popover {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    width: min(480px, 90vw);
    max-height: 60vh;
    overflow-y: auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
    z-index: 1000;
  }
  .status {
    padding: 16px;
    color: var(--muted);
    font-size: 12px;
    text-align: center;
  }
  .status.err { color: var(--err); }
  .result-count {
    padding: 8px 12px;
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border-bottom: 1px solid var(--border);
  }
  .result {
    display: block;
    padding: 10px 12px;
    text-decoration: none;
    color: var(--text-soft);
    border-bottom: 1px solid var(--border);
    transition: background 120ms;
  }
  .result:last-child { border-bottom: 0; }
  .result:hover { background: var(--surface-2); color: var(--text); }
  .result.active {
    background: rgba(232, 153, 104, 0.06);
  }
  .result-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .type-tag {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--surface-2);
    color: var(--muted);
    border: 1px solid var(--border);
    font-weight: 600;
  }
  .type-user_turn { background: rgba(95,195,167,0.10); color: var(--ok); border-color: rgba(95,195,167,0.25); }
  .type-turn { background: rgba(150,144,248,0.10); color: var(--violet); border-color: rgba(150,144,248,0.25); }
  .type-compact_summary { background: rgba(232,153,104,0.08); color: var(--accent); border-color: rgba(232,153,104,0.25); }
  .sid {
    font-family: var(--mono);
    color: var(--muted);
    font-size: 10px;
  }
  .ts {
    color: var(--muted-deep);
    font-size: 10px;
    margin-left: auto;
  }
  .snippet {
    font-size: 11px;
    color: var(--text-soft);
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
  }
</style>
