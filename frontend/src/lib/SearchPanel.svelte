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
  // Which result row is expanded (showing full payload). null = none.
  // Keyed by event_id so it survives a results refresh that keeps
  // the same matches in the same order.
  let expandedId = $state(null);
  // Brief "copied" feedback state for the copy buttons. Holds the
  // event_id of the most recently copied result; cleared after 1.2s.
  let copiedId = $state(null);
  // Failure message for "view in session" when the matched FeedItem
  // isn't in the loaded feed window. Shown inline; carries the event
  // ID so the user has a referenceable token if reporting an issue.
  let viewError = $state(null);

  let debounceTimer = null;

  function shortSession(sid) {
    return sid ? sid.slice(0, 8) : '?';
  }

  function fmtTs(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      // ISO-shaped local time with timezone offset.
      // Sortable + unambiguous + matches the user's lived experience
      // (UTC-only would shift events by their local tz offset).
      const pad = (n) => String(n).padStart(2, '0');
      const off = -d.getTimezoneOffset();
      const sign = off >= 0 ? '+' : '-';
      const ah = pad(Math.floor(Math.abs(off) / 60));
      const am = pad(Math.abs(off) % 60);
      return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
        `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
        `${sign}${ah}:${am}`
      );
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
    // "View in session" sub-action — opens the session view at the
    // matching event via the deep-link hash. The Feed component's
    // hashchange listener scrolls the matching FeedItem into view
    // (when it's loaded; older events outside the loaded window
    // simply won't scroll, which is fine because the inline preview
    // already shows the content).
    return `/p/${ph}/live/${r.session_id}#event-${r.event_id}`;
  }

  function toggleExpand(r) {
    expandedId = expandedId === r.event_id ? null : r.event_id;
  }

  function dismissViewError() { viewError = null; }

  function onViewInSession(r, e) {
    viewError = null;
    if (r.session_id === currentSessionId) {
      // Same-session click: don't reload. Two things have to happen
      // for the scroll-and-flash to actually land:
      //   1. The Feed must not be filtering out the matched event
      //      type (otherwise the FeedItem isn't rendered).
      //   2. The matched event must be in the loaded window.
      // (1) is fixable by clearing the filter; we dispatch a custom
      // event the Feed listens for. (2) isn't fixable here — older
      // events outside the loaded window need a load-older click —
      // but the inline preview already shows the content, so the
      // feed jump is best-effort, not load-bearing.
      e.preventDefault();
      window.dispatchEvent(new CustomEvent('search:clear-filter'));
      const newHash = `#event-${r.event_id}`;
      if (window.location.hash === newHash) {
        // Force a hashchange when the hash already matches so the
        // listener re-fires (clicking the same result twice still
        // re-triggers scroll + flash).
        window.location.hash = '';
      }
      window.location.hash = newHash;
      // Defer a check so we can surface a visible status if the
      // element didn't come into the DOM (older than the loaded
      // window). The inline preview already shows the content; this
      // status just makes the limitation explicit and provides the
      // event_id as a referenceable token.
      setTimeout(() => {
        if (!document.getElementById(`event-${r.event_id}`)) {
          viewError = {
            event_id: r.event_id,
            message: `Event #${r.event_id} not in the loaded feed window. The inline preview above shows the content; to audit events around it, click "load older" in the feed until this event scrolls into view.`,
          };
        } else {
          // Element rendered — close the popover so the feed scroll
          // is unobstructed.
          open = false;
        }
      }, 200);
    }
    // Different-session: let the browser navigate normally.
  }

  function parsePayload(payloadStr) {
    if (!payloadStr) return null;
    try {
      return JSON.parse(payloadStr);
    } catch {
      return null;
    }
  }

  // Pull the most useful text content out of a parsed payload by
  // event type. The endpoint returns the raw JSON string; we parse
  // and shape per-type so the preview reads cleanly instead of as
  // raw JSON.
  function previewText(r) {
    const p = parsePayload(r.payload);
    if (!p) return r.snippet;
    switch (r.event_type) {
      case 'user_turn':
      case 'turn':
      case 'compact_summary':
        return p.text_preview || r.snippet;
      case 'tool_call':
        return `${p.tool ?? '?'}\n${p.input_preview ?? ''}`;
      case 'claim':
        return `${p.text ?? ''}${p.evidence ? '\n\n' + p.evidence : ''}`;
      case 'away_summary':
        return p.content || r.snippet;
      default:
        return r.snippet;
    }
  }

  // Format a result for clipboard copy. Includes a header line with
  // type, event_id, session prefix, timestamp — enough to paste into
  // chat / notes as referenceable context. Body is the formatted
  // preview text (matches what the inline expand shows).
  function buildCopyText(r) {
    const type = typeLabel[r.event_type] ?? r.event_type;
    const head = `[${type} · #${r.event_id} · session ${shortSession(r.session_id)} · ${fmtTs(r.created_at)}]`;
    return `${head}\n${previewText(r)}`;
  }

  async function copyResult(r, e) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(buildCopyText(r));
      copiedId = r.event_id;
      setTimeout(() => {
        if (copiedId === r.event_id) copiedId = null;
      }, 1200);
    } catch (err) {
      console.warn('[search] copy failed', err);
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
        {#if viewError}
          <div class="view-error">
            <span class="view-error-msg">{viewError.message}</span>
            <button type="button" class="view-error-close" onclick={dismissViewError} title="dismiss">×</button>
          </div>
        {/if}
        {#each results as r (r.event_id)}
          {@const isExpanded = expandedId === r.event_id}
          {@const isCopied = copiedId === r.event_id}
          <div class="result" class:active={r.session_id === currentSessionId} class:expanded={isExpanded}>
            <div class="result-row">
              <button type="button" class="row-main" onclick={() => toggleExpand(r)}>
                <div class="result-head">
                  <span class="type-tag type-{r.event_type}">{typeLabel[r.event_type] ?? r.event_type}</span>
                  <code class="eid" title="event id (copy via the icon to include with content)">#{r.event_id}</code>
                  <code class="sid" title={r.session_id}>{shortSession(r.session_id)}</code>
                  <span class="ts">{fmtTs(r.created_at)}</span>
                </div>
                <div class="snippet">{r.snippet}</div>
              </button>
              <button
                type="button"
                class="copy-btn"
                class:copied={isCopied}
                onclick={(e) => copyResult(r, e)}
                title="copy event header + content to clipboard"
                aria-label="copy result"
              >{isCopied ? '✓' : '⧉'}</button>
            </div>
            {#if isExpanded}
              <div class="expand-body">
                <pre class="payload-text">{previewText(r)}</pre>
                <a
                  class="view-link"
                  href={urlFor(r)}
                  onclick={(e) => onViewInSession(r, e)}
                  title={r.session_id === currentSessionId
                    ? 'jump to this event in the current feed (best-effort; only works if event is loaded)'
                    : 'open the session containing this event'}
                >view in session →</a>
              </div>
            {/if}
          </div>
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
    border-bottom: 1px solid var(--border);
    color: var(--text-soft);
  }
  .result:last-child { border-bottom: 0; }
  .result.active .result-row { background: rgba(232, 153, 104, 0.06); }
  .result.expanded .result-row { background: var(--surface-2); }
  .result-row {
    display: flex;
    align-items: stretch;
    gap: 0;
    transition: background 120ms;
  }
  .result-row:hover { background: var(--surface-2); color: var(--text); }
  .row-main {
    flex: 1;
    text-align: left;
    background: transparent;
    border: 0;
    padding: 10px 12px;
    color: inherit;
    font-family: inherit;
    cursor: pointer;
    min-width: 0;
  }
  .copy-btn {
    background: transparent;
    border: 0;
    border-left: 1px solid var(--border);
    padding: 0 12px;
    color: var(--muted);
    cursor: pointer;
    font-size: 14px;
    transition: color 120ms, background 120ms;
    flex-shrink: 0;
  }
  .copy-btn:hover { color: var(--text); background: var(--surface-2); }
  .copy-btn.copied { color: var(--ok); }
  .eid {
    font-family: var(--mono);
    color: var(--muted);
    font-size: 10px;
    user-select: all;
  }
  .view-error {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 12px;
    background: rgba(230,192,84,0.08);
    border-bottom: 1px solid rgba(230,192,84,0.25);
    color: var(--text-soft);
    font-size: 11px;
    line-height: 1.5;
  }
  .view-error-msg { flex: 1; }
  .view-error-close {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    font-size: 14px;
    padding: 0;
    flex-shrink: 0;
  }
  .view-error-close:hover { color: var(--text); }
  .expand-body {
    padding: 8px 12px 12px;
    background: var(--surface-2);
    border-top: 1px solid var(--border);
  }
  .payload-text {
    margin: 0 0 8px;
    font-size: 11px;
    color: var(--text);
    font-family: var(--mono);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 240px;
    overflow-y: auto;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px 10px;
    line-height: 1.5;
  }
  .view-link {
    display: inline-block;
    color: var(--accent);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    text-decoration: none;
    font-weight: 600;
  }
  .view-link:hover { color: var(--text); text-decoration: underline; }
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
