<script>
  import { humanize } from './format.js';

  // Reads /llm-metrics/summary and surfaces the truncation-risk
  // signal the qwen instrumentation was put in place to answer:
  // is rubric (or any other call kind) silently hitting its budget?
  //
  // Per call_kind we render: configured max_tokens, avg + max
  // completion tokens, avg reasoning tokens (when reported), and
  // the n_length count — finish_reason='length' is the smoking-gun
  // shape for thinking-mode budget exhaustion. A horizontal bar
  // showing avg_completion / configured_max_tokens reads "how close
  // are we to the cap" at a glance; values past ~75% are where the
  // truncation pattern starts surfacing.

  let summary = $state([]);
  let timer;
  let loading = $state(true);

  async function refresh() {
    try {
      const r = await fetch('/llm-metrics/summary');
      if (!r.ok) return;
      const data = await r.json();
      summary = data.by_kind || [];
    } catch (e) {
      console.warn('llm-metrics fetch failed', e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    refresh();
    timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  });

  function pctOfMax(row) {
    if (!row.configured_max_tokens || !row.avg_completion) return 0;
    return Math.min(100, (row.avg_completion / row.configured_max_tokens) * 100);
  }

  function risk(row) {
    if (!row.n) return 'none';
    if ((row.n_length ?? 0) / row.n > 0.10) return 'high';
    const ratio = pctOfMax(row);
    if (ratio >= 75) return 'high';
    if (ratio >= 50) return 'med';
    return 'low';
  }
</script>

<section class="panel">
  <header>
    <div>
      <h3>llm budget · per call kind</h3>
      <p class="muted">
        configured max_tokens vs observed completion / reasoning. fills past
        ~75% of cap or any ``finish_reason=length`` events flag truncation
        risk for that kind.
      </p>
    </div>
  </header>

  {#if loading}
    <p class="empty">loading…</p>
  {:else if summary.length === 0}
    <p class="empty">
      no llm calls recorded yet. instrumentation lands a row on every
      synth / drift / query / rubric / consolidator call.
    </p>
  {:else}
    <div class="grid">
      {#each summary as row}
        {@const riskLevel = risk(row)}
        <article class="kind risk-{riskLevel}">
          <header class="kh">
            <span class="kind-label">{row.call_kind}</span>
            <span class="risk-pill risk-{riskLevel}">{riskLevel} risk</span>
          </header>
          <div class="rule">
            <div class="rule-label">avg completion vs configured max</div>
            <div class="rule-bar">
              <div class="rule-fill risk-{riskLevel}"
                   style="width: {pctOfMax(row).toFixed(1)}%"></div>
            </div>
            <div class="rule-num">
              <code>{Math.round(row.avg_completion ?? 0)}</code>
              <span class="muted">/</span>
              <code>{row.configured_max_tokens ?? '—'}</code>
              <span class="muted">tokens</span>
            </div>
          </div>
          <dl class="stats">
            <dt>n</dt><dd>{row.n}</dd>
            <dt>finish=length</dt>
            <dd class:warn={(row.n_length ?? 0) > 0}>{row.n_length ?? 0}</dd>
            <dt>finish=stop</dt><dd>{row.n_stop ?? 0}</dd>
            <dt>errors</dt>
            <dd class:err={(row.n_error ?? 0) > 0}>{row.n_error ?? 0}</dd>
            <dt>avg prompt</dt><dd>{humanize(Math.round(row.avg_prompt ?? 0))}</dd>
            <dt>max completion</dt><dd>{humanize(row.max_completion ?? 0)}</dd>
            {#if row.avg_reasoning != null}
              <dt>avg reasoning</dt><dd>{humanize(Math.round(row.avg_reasoning))}</dd>
              <dt>max reasoning</dt><dd>{humanize(row.max_reasoning ?? 0)}</dd>
            {:else}
              <dt>reasoning</dt><dd class="muted">—</dd>
            {/if}
            <dt>avg duration</dt>
            <dd>{Math.round(row.avg_duration_ms ?? 0)} ms</dd>
          </dl>
        </article>
      {/each}
    </div>

    <p class="footer">
      decision rule: bump <code>qwen_max_tokens_&lt;kind&gt;</code> if
      <code>finish=length</code> &gt; 0 or if avg completion sits past 75% of
      cap with thinking enabled. the table is the inspection surface for the
      open token-budget calibration TODO.
    </p>
  {/if}
</section>

<style>
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px 22px;
  }
  header {
    margin-bottom: 14px;
  }
  h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
  }
  .muted {
    color: var(--muted);
    margin: 4px 0 0;
    font-size: 11px;
    line-height: 1.55;
    max-width: 720px;
  }
  .empty {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px;
  }
  .kind {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }
  .kh {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .kind-label {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text);
    letter-spacing: 0.02em;
  }
  .risk-pill {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid var(--border);
  }
  .risk-pill.risk-low  { color: var(--ok);   border-color: rgba(95,195,167,0.30); background: rgba(95,195,167,0.06); }
  .risk-pill.risk-med  { color: var(--warn); border-color: rgba(230,192,84,0.30); background: rgba(230,192,84,0.06); }
  .risk-pill.risk-high { color: var(--err);  border-color: rgba(232,122,122,0.30); background: rgba(232,122,122,0.08); }
  .risk-pill.risk-none { color: var(--muted); }

  .rule { margin-bottom: 12px; }
  .rule-label {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  .rule-bar {
    height: 8px;
    background: var(--bg);
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 4px;
  }
  .rule-fill { height: 100%; transition: width 600ms ease; }
  .rule-fill.risk-low  { background: linear-gradient(90deg, rgba(95,195,167,0.5), var(--ok)); }
  .rule-fill.risk-med  { background: linear-gradient(90deg, rgba(230,192,84,0.5), var(--warn)); }
  .rule-fill.risk-high { background: linear-gradient(90deg, rgba(232,122,122,0.5), var(--err)); }
  .rule-fill.risk-none { background: var(--muted-deep); }
  .rule-num {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-soft);
  }
  .rule-num code { font: inherit; }

  .stats {
    margin: 0;
    display: grid;
    grid-template-columns: 1fr auto;
    column-gap: 12px;
    row-gap: 4px;
    font-size: 11px;
  }
  dt { color: var(--muted); }
  dd {
    margin: 0;
    text-align: right;
    font-family: var(--mono);
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }
  dd.warn { color: var(--warn); }
  dd.err { color: var(--err); }
  dd.muted { color: var(--muted); }

  .footer {
    margin: 14px 0 0;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.55;
  }
  .footer code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 10.5px;
    border: 1px solid var(--border);
    color: var(--text-soft);
  }
</style>
