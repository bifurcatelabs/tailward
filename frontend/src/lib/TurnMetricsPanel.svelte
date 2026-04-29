<script>
  import MetricCell from './MetricCell.svelte';

  let { ph } = $props();

  let metrics = $state([]);
  let timer;

  async function refresh() {
    try {
      const r = await fetch(`/p/${ph}/turn-metrics?limit=120`);
      if (!r.ok) return;
      const data = await r.json();
      metrics = data.metrics || [];
    } catch (e) {
      console.warn('turn-metrics fetch failed', e);
    }
  }

  $effect(() => {
    refresh();
    timer = setInterval(refresh, 8000);
    return () => clearInterval(timer);
  });

  // Per-metric series, oldest-to-newest.
  let ttftSeries = $derived(
    metrics.map((m) => m.prompt_to_response_ms).filter((v) => typeof v === 'number'),
  );
  let tpsSeries = $derived(
    metrics.map((m) => m.output_tps).filter((v) => typeof v === 'number'),
  );
  let cacheSeries = $derived(
    metrics.map((m) => m.cache_hit_ratio).filter((v) => typeof v === 'number'),
  );
  let stopReasons = $derived.by(() => {
    const out = {};
    for (const m of metrics) {
      const k = m.stop_reason || 'unknown';
      out[k] = (out[k] || 0) + 1;
    }
    return out;
  });
  let stopReasonsTotal = $derived(metrics.length);

  function pct(v) {
    if (v == null) return '—';
    // 1-decimal precision matters for cache_hit_ratio: long sessions
    // routinely sit at 0.99-0.998 due to heavy prompt caching, and
    // .toFixed(0) collapses that whole band to "100%" — both in the
    // big number and in the uplot hover tooltip — making variance
    // invisible and the hover read as inaccurate vs the chart line
    // position. One decimal preserves the meaningful spread.
    return (v * 100).toFixed(1) + '%';
  }
</script>

<section class="panel">
  <header>
    <div>
      <h3>inference path · per-turn</h3>
      <p class="muted">
        derived in-band from this project's transcripts. {metrics.length}
        turn{metrics.length === 1 ? '' : 's'} sampled.
      </p>
    </div>
  </header>

  {#if metrics.length === 0}
    <p class="empty">
      no turns recorded yet — open a session in this project and the
      first assistant turn that closes will populate the panels below.
    </p>
  {:else}
    <div class="grid">
      <MetricCell
        label="prompt → response"
        unit="ms"
        series={ttftSeries}
        color="var(--accent)"
        fill="rgba(232,153,104,0.10)"
        hint="first-block latency"
        tooltip={"Time from your prompt to the first assistant content block written to JSONL.\n\nNot pure TTFT — Claude Code writes a block after it completes, not on the first streamed token. For thinking-enabled responses, this includes the full thinking duration since <thinking> is the first block."}
      />
      <MetricCell
        label="output throughput"
        unit="tps"
        series={tpsSeries}
        color="var(--violet)"
        fill="rgba(150,144,248,0.10)"
        hint="output_tokens / message_duration"
        tooltip={"Output tokens divided by wall-clock duration from first to last block of one assistant message.\n\nMeasures \"tokens per second the model produced for this message,\" not raw inference rate. If a message contains text + tool_use blocks, the duration spans both. Suppressed (—) when block-emit duration is under 100ms (single-block turns produce nonsense divisions)."}
      />
      <MetricCell
        label="cache hit ratio"
        series={cacheSeries}
        color="var(--ok)"
        fill="rgba(95,195,167,0.10)"
        formatValue={pct}
        hint="cache_read / input-side total"
        tooltip={"Fraction of input-side tokens that came from prompt cache.\n\nDenominator is cache_read + input_tokens + cache_creation — every token Anthropic billed on the input side. Higher is cheaper and faster; sustained low values mean cache is being invalidated frequently."}
      />
      <article class="cell stop-cell">
        <div class="head">
          <span class="label">stop-reason mix</span>
          <span class="hint">distribution across {stopReasonsTotal} turns</span>
        </div>
        <div class="stop-bars">
          {#each Object.entries(stopReasons) as [reason, count]}
            <div class="stop-row">
              <span class="stop-label">{reason}</span>
              <div class="stop-bar">
                <div class="stop-fill"
                     style="width: {(count / stopReasonsTotal * 100).toFixed(1)}%"></div>
              </div>
              <span class="stop-count">{count}</span>
            </div>
          {/each}
        </div>
      </article>
    </div>
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
    margin: 4px 0 0;
    color: var(--muted);
    font-size: 11px;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
  }
  @media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
  .cell.stop-cell {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }
  .hint {
    font-size: 10px;
    color: var(--muted-deep);
    text-align: right;
  }

  .stop-bars { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .stop-row {
    display: grid;
    grid-template-columns: 90px 1fr 30px;
    gap: 10px;
    align-items: center;
    font-size: 11px;
  }
  .stop-label {
    font-family: var(--mono);
    color: var(--text-soft);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .stop-bar {
    background: var(--bg);
    height: 6px;
    border-radius: 3px;
    overflow: hidden;
  }
  .stop-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--violet), var(--violet-soft));
  }
  .stop-count {
    font-family: var(--mono);
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .empty {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
</style>
