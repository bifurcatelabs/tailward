<script>
  /**
   * Session synthesis stream — captured snapshots for the current session.
   *
   * Lists snapshots written by ``synthesis_worker`` to
   * ``~/.tailward/projects/<ph>/snapshots/`` and offers a "synthesize now"
   * button that triggers an on-demand capture. Subscribes to the
   * reactive ``live.events`` so new ``synthesis_captured`` events
   * append without a manual refresh.
   *
   * Graceful empty state when no local LLM is configured: the trigger
   * endpoint returns 503 in that case; we surface the message rather
   * than render a half-broken card.
   */
  import { live } from './live.svelte.js';

  let { ph, sessionId } = $props();

  let snapshots = $state([]);
  let total = $state(0);
  let limit = $state(50);
  let loading = $state(true);
  let error = $state(null);
  let synthesizing = $state(false);
  let triggerError = $state(null);
  let expandedTs = $state(null);
  let bodies = $state({}); // ts -> { body, meta } once fetched

  async function loadList() {
    loading = true;
    error = null;
    try {
      const r = await fetch(
        `/p/${ph}/live/${sessionId}/snapshots?limit=${limit}`
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      snapshots = data.snapshots || [];
      total = data.total ?? snapshots.length;
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  function showAll() {
    limit = 1000;
    loadList();
  }

  async function loadBody(ts) {
    if (bodies[ts]) return;
    try {
      const r = await fetch(`/p/${ph}/live/${sessionId}/snapshots/${ts}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      bodies = { ...bodies, [ts]: data };
    } catch (e) {
      bodies = { ...bodies, [ts]: { body: `(failed to load: ${e})`, meta: {} } };
    }
  }

  async function toggleRow(ts) {
    if (expandedTs === ts) {
      expandedTs = null;
      return;
    }
    expandedTs = ts;
    await loadBody(ts);
  }

  async function synthesizeNow() {
    if (synthesizing) return;
    synthesizing = true;
    triggerError = null;
    try {
      const r = await fetch(`/p/${ph}/live/${sessionId}/synthesize`, { method: 'POST' });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try {
          const data = await r.json();
          if (data?.detail) msg = data.detail;
        } catch {}
        throw new Error(msg);
      }
      // Result lands via livebus too; no need to re-fetch the list here —
      // the $effect watching live.events will pick it up.
    } catch (e) {
      triggerError = String(e).replace(/^Error:\s*/, '');
    } finally {
      synthesizing = false;
    }
  }

  $effect(() => { loadList(); });

  // Reactive: react to new live events in order.
  //  * synthesis_captured → reload list + clear any stale failure
  //    notice (a successful capture supersedes a prior error)
  //  * synthesis_failed → surface the error in the panel
  // Sequential processing keeps the end state consistent with the
  // most recent outcome on a fresh page load — even if a failure
  // event sits before a success in the replay.
  let lastSeenLen = 0;
  $effect(() => {
    const events = live.events;
    if (events.length === lastSeenLen) return;
    const fresh = events.slice(lastSeenLen);
    lastSeenLen = events.length;
    let needRefetch = false;
    for (const e of fresh) {
      const t = e.eventType ?? e.type;
      if (t === 'synthesis_captured') {
        needRefetch = true;
        triggerError = null;
      } else if (t === 'synthesis_failed') {
        const err = e.payload?.error || 'unknown error';
        const trig = e.payload?.trigger || 'synthesis';
        triggerError = `${trig} synthesis failed: ${err}`;
      }
    }
    if (needRefetch) loadList();
  });

  function formatRelative(ts) {
    if (!ts) return '';
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(ts);
    if (!m) return ts;
    const [, y, mo, d, h, mi, s] = m;
    const epoch = Date.UTC(+y, +mo - 1, +d, +h, +mi, +s);
    const diff = Math.max(0, (Date.now() - epoch) / 1000);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function fmtTokens(n) {
    if (n == null) return '—';
    if (n < 1000) return `${n}`;
    if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
    return `${(n / 1_000_000).toFixed(2)}M`;
  }
</script>

<section class="panel">
  <header class="hd">
    <h3>session synthesis</h3>
    <span class="muted">
      {#if total > snapshots.length}
        showing {snapshots.length} of {total}
      {:else}
        {total} snapshot{total === 1 ? '' : 's'}
      {/if}
    </span>
    {#if total > snapshots.length}
      <button class="show-all" onclick={showAll} title="Load all snapshots for this session">
        show all
      </button>
    {/if}
    <button
      class="trigger"
      onclick={synthesizeNow}
      disabled={synthesizing}
      title="Capture an incremental snapshot of recent activity using your configured local LLM"
    >
      {synthesizing ? 'synthesizing…' : 'synthesize now'}
    </button>
  </header>

  {#if triggerError}
    <div class="trigger-err">
      <span class="trigger-err-msg">{triggerError}</span>
      {#if /no local LLM/i.test(triggerError)}
        <span class="hint">configure one in <a href="#settings">settings</a></span>
      {/if}
      <button
        class="dismiss"
        onclick={() => { triggerError = null; }}
        title="dismiss"
        aria-label="dismiss"
      >×</button>
    </div>
  {/if}

  {#if loading && snapshots.length === 0}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if snapshots.length === 0}
    <div class="empty">
      no snapshots captured yet. periodic capture fires when this session's
      input tokens grow past the configured threshold; you can also trigger
      one on demand with the button above.
    </div>
  {:else}
    <div class="rows">
      {#each snapshots as s (s.created_at)}
        <button
          class="row"
          class:expanded={expandedTs === s.created_at}
          onclick={() => toggleRow(s.created_at)}
        >
          <span class="caret">{expandedTs === s.created_at ? '▾' : '▸'}</span>
          <span class="when">{formatRelative(s.created_at)}</span>
          <span class="trigger-chip chip-{s.trigger}">{s.trigger ?? '—'}</span>
          {#if s.model}
            <code class="model" title={s.model}>{s.model}</code>
          {:else}
            <span class="muted">—</span>
          {/if}
          <span
            class="tokens"
            title={
              `claude (api): ${fmtTokens(s.fullness_input_tokens)} input tokens at trigger time\n` +
              `synth (local): ${fmtTokens(s.input_chars)} chars sent to local LLM\n` +
              `event count: ${s.event_count ?? '—'}`
            }
          >{fmtTokens(s.fullness_input_tokens)} claude in</span>
        </button>
        {#if expandedTs === s.created_at}
          <div class="detail">
            {#if !bodies[s.created_at]}
              <div class="empty inline">loading body…</div>
            {:else}
              <pre class="body">{bodies[s.created_at].body}</pre>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
    <p class="footnote">
      <code>claude in</code> = the assistant turn's
      <code>usage.input_tokens</code> when the snapshot fired —
      Claude's context fullness, not your local LLM's. Hover the
      row for the local-side numbers (chars sent to synth, event
      count). Captures land in <code>~/.tailward/projects/&lt;ph&gt;/snapshots/</code>
      with a sidecar JSON (model, sampler kind, trigger,
      <code>input_chars</code>, <code>input_chars_cap</code>) so A/B
      runs across configurations are comparable.
    </p>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
    margin: 16px 24px 0;
  }
  .hd {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 12px;
  }
  .hd h3 {
    margin: 0;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }
  .muted {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 11px;
  }
  .trigger {
    margin-left: auto;
    background: rgba(232,153,104,0.10);
    border: 1px solid rgba(232,153,104,0.30);
    color: var(--accent);
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 11px;
    font-family: inherit;
    cursor: pointer;
    transition: background 120ms;
  }
  .trigger:hover:not(:disabled) {
    background: rgba(232,153,104,0.18);
  }
  .trigger:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .show-all {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 10px;
    font-family: var(--mono);
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .show-all:hover {
    color: var(--text);
    border-color: var(--border-strong);
  }
  .trigger-err {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--err);
    font-size: 11px;
    padding: 6px 10px;
    margin-bottom: 10px;
    background: rgba(232,122,122,0.06);
    border: 1px solid rgba(232,122,122,0.20);
    border-radius: 4px;
  }
  .trigger-err-msg { flex: 1; }
  .trigger-err .hint {
    color: var(--muted);
    font-style: italic;
  }
  .trigger-err a {
    color: var(--accent);
    text-decoration: underline;
  }
  .dismiss {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    padding: 0 4px;
    margin-left: auto;
  }
  .dismiss:hover { color: var(--text); }

  .rows {
    display: flex;
    flex-direction: column;
    gap: 2px;
    /* Cap the panel's vertical real estate so a long snapshot list
       (dozens, eventually) doesn't push the feed off the page. The
       count + "show all" pill in the header tell the user there's
       more in the scroll. */
    max-height: 180px;
    overflow-y: auto;
    /* Slim padding so the scrollbar gutter doesn't crop row content. */
    padding-right: 4px;
  }
  .rows::-webkit-scrollbar { width: 6px; }
  .rows::-webkit-scrollbar-track { background: transparent; }
  .rows::-webkit-scrollbar-thumb {
    background: var(--border-strong);
    border-radius: 3px;
  }
  .rows::-webkit-scrollbar-thumb:hover { background: var(--muted-deep); }
  .row {
    display: grid;
    grid-template-columns: 18px 90px 90px 1fr 90px;
    align-items: center;
    gap: 10px;
    padding: 8px 6px;
    border-radius: 4px;
    background: transparent;
    border: 0;
    color: var(--text);
    font-family: inherit;
    font-size: 12px;
    cursor: pointer;
    text-align: left;
    transition: background 120ms;
  }
  .row:hover { background: var(--surface-2); }
  .row.expanded {
    background: rgba(232,153,104,0.06);
    border: 1px dashed rgba(232,153,104,0.30);
    padding: 7px 5px;
  }
  .caret {
    color: var(--muted-deep);
    font-size: 10px;
    text-align: center;
  }
  .when, .tokens {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 11px;
  }
  .tokens { text-align: right; color: var(--muted-deep); }
  .trigger-chip {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 7px;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--muted);
    border: 1px solid var(--border);
    text-align: center;
  }
  .trigger-chip.chip-on_demand {
    background: rgba(232,153,104,0.10);
    color: var(--accent);
    border-color: rgba(232,153,104,0.30);
  }
  .trigger-chip.chip-periodic {
    background: rgba(150,144,248,0.10);
    color: var(--violet-soft);
    border-color: rgba(150,144,248,0.25);
  }
  .model {
    font-family: var(--mono);
    color: var(--text-soft);
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .detail {
    background: var(--surface-2);
    border-radius: 6px;
    margin: 4px 18px 8px;
    padding: 14px 18px;
  }
  .body {
    margin: 0;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-soft);
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-x: auto;
  }
  .empty {
    padding: 20px 4px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.55;
  }
  .empty.inline { padding: 8px 0; }
  .empty.err { color: var(--err); font-family: var(--mono); }

  .footnote {
    margin: 14px 0 0;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    color: var(--muted-deep);
    font-size: 11px;
    line-height: 1.55;
  }
  .footnote code {
    font-family: var(--mono);
    background: transparent;
    color: var(--muted);
    font-size: 10px;
  }
</style>
