<script>
  // Distribution of stop_reason across the project's assistant
  // messages. Anthropic-emitted signal — answers "how did the
  // third-party model end its turns?" Belongs on Platform (the
  // third-party-provider-facing surface), not Reflection (user-side).
  //
  // Reads the /v2/reflection/{ph} endpoint's stop_reasons field.
  // The endpoint URL is mis-named for this panel — it pre-dates the
  // source-of-data split — but the data is what we need. Endpoint
  // rename is a separate cleanup (see project_notes_04282026_passes
  // memo for follow-ups).

  let { ph } = $props();

  let loading = $state(true);
  let error = $state(null);
  let data = $state(null);

  async function load() {
    if (!ph) return;
    loading = true;
    error = null;
    try {
      const r = await fetch(`/v2/reflection/${ph}?limit=1000`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });

  const STOP_REASON_DESCRIPTIONS = {
    end_turn: 'natural completion — the model decided it was done',
    tool_use: 'paused for tool result — model invoked a tool, waiting for response',
    max_tokens: 'hit the token cap — context bloat or response too long',
    stop_sequence: 'hit a stop sequence — Claude Code rarely sets these',
    pause_turn: 'paused — needs user / additional context',
    refusal: 'model refused to continue — safety / policy trigger',
    untagged: 'message landed without a stop_reason field — rare; streaming partial or user interrupt before the message closed',
  };

  let rows = $derived.by(() => {
    const sr = data?.stop_reasons;
    if (!sr || !sr.counts || sr.counts.length === 0) return null;
    const max = Math.max(...sr.counts.map((c) => c.count));
    const total = sr.total || sr.counts.reduce((s, c) => s + c.count, 0);
    return {
      total,
      rows: sr.counts.map((c) => ({
        label: c.stop_reason,
        n: c.count,
        barPct: max > 0 ? (c.count / max) * 100 : 0,
        kind: c.stop_reason === 'untagged' ? 'muted'
            : c.stop_reason === 'refusal' ? 'err'
            : c.stop_reason === 'max_tokens' ? 'warn'
            : 'ok',
        description:
          STOP_REASON_DESCRIPTIONS[c.stop_reason]
          ?? 'unfamiliar stop_reason — Claude Code emitted a value the dashboard doesn\'t have a description for',
      })),
    };
  });
</script>

<section class="panel">
  <header>
    <h3>assistant stop reasons</h3>
    <p class="muted">
      how the third-party model ended each message. Anthropic-emitted
      <code>stop_reason</code> values, deduped per <code>message_id</code>.
    </p>
  </header>

  {#if loading && !data}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if !rows}
    <div class="empty">no assistant messages captured yet</div>
  {:else}
    <div class="bars">
      {#each rows.rows as r (r.label)}
        <div class="bar-row" title={r.description}>
          <span class="bar-lbl"><code>{r.label}</code></span>
          <span class="bar-track">
            <span class="bar-fill kind-{r.kind}" style="width: {r.barPct}%"></span>
          </span>
          <span class="bar-n">{r.n}</span>
        </div>
      {/each}
    </div>
    <div class="footnote">
      How {rows.total} assistant messages ended. Hover a row for what each <code>stop_reason</code> represents.
    </div>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  header { margin-bottom: 14px; }
  header h3 {
    margin: 0;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }
  header p {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--muted-deep);
    line-height: 1.55;
  }
  header code {
    font-family: var(--mono);
    background: transparent;
    color: var(--muted);
    font-size: 11px;
  }

  .bars { display: flex; flex-direction: column; gap: 8px; }
  .bar-row {
    display: grid;
    grid-template-columns: 140px 1fr 36px;
    align-items: center;
    gap: 10px;
    font-size: 12px;
  }
  .bar-lbl { color: var(--text-soft); }
  .bar-lbl code {
    font-family: var(--mono);
    background: transparent;
    padding: 0;
    color: var(--text-soft);
    font-size: 12px;
  }
  .bar-track {
    background: var(--surface-2);
    height: 12px;
    border-radius: 3px;
    overflow: hidden;
  }
  .bar-fill {
    display: block;
    height: 100%;
    transition: width 240ms ease;
  }
  .bar-fill.kind-ok    { background: linear-gradient(90deg, rgba(95,195,167,0.4), var(--ok)); }
  .bar-fill.kind-err   { background: linear-gradient(90deg, rgba(232,122,122,0.4), var(--err)); }
  .bar-fill.kind-warn  { background: linear-gradient(90deg, rgba(230,165,84,0.4), var(--warn)); }
  .bar-fill.kind-muted { background: var(--surface-3, rgba(255,255,255,0.08)); }
  .bar-n {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 12px;
    text-align: right;
  }

  .footnote {
    margin-top: 12px;
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

  .empty {
    padding: 24px 12px;
    color: var(--muted);
    text-align: center;
    font-size: 13px;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }
</style>
