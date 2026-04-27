<script>
  import Chart from './Chart.svelte';
  import { humanize } from './format.js';

  let {
    label,
    unit = '',
    series = [],
    color = 'var(--accent)',
    fill = 'transparent',
    formatValue,
    hint,
    height = 140,
  } = $props();

  let latest = $derived(series.length ? series[series.length - 1] : null);
  let med = $derived.by(() => {
    if (!series.length) return null;
    const s = [...series].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  });

  let xValues = $derived(series.map((_, i) => i + 1));
  let chartSeries = $derived([
    {
      label,
      color: _resolveColor(color),
      fill: fill !== 'transparent' ? fill : undefined,
      data: series,
    },
  ]);

  function _resolveColor(v) {
    // uPlot paints to canvas and can't resolve CSS custom properties
    // on its own. Read them off the document's computed style so the
    // chart line picks up the same token the rest of the UI uses.
    if (typeof v === 'string' && v.startsWith('var(')) {
      const name = v.slice(4, -1).trim();
      const root = getComputedStyle(document.documentElement);
      const resolved = root.getPropertyValue(name).trim();
      return resolved || '#fff';
    }
    return v;
  }
</script>

<article class="cell">
  <div class="head">
    <span class="label">{label}</span>
    {#if hint}<span class="hint">{hint}</span>{/if}
  </div>
  <div class="row">
    <span class="value">
      {formatValue
        ? formatValue(latest)
        : (latest != null ? humanize(latest) : '—')}
      {#if unit && latest != null && !formatValue}
        <span class="unit">{unit}</span>
      {/if}
    </span>
    <span class="median">
      {#if med != null}
        med {formatValue ? formatValue(med) : humanize(med)}{unit && !formatValue ? ' ' + unit : ''}
      {/if}
    </span>
  </div>
  <div class="chart">
    {#if series.length >= 2}
      <Chart
        {xValues}
        series={chartSeries}
        formatY={formatValue}
        xIsIndex={true}
        {height}
      />
    {:else}
      <div class="empty" style="height: {height}px;">awaiting samples</div>
    {/if}
  </div>
</article>

<style>
  .cell {
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
    max-width: 60%;
  }
  .row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }
  .value {
    font-family: var(--mono);
    font-size: 22px;
    color: var(--text);
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  .unit {
    font-size: 12px;
    color: var(--muted);
    margin-left: 4px;
    font-weight: 400;
  }
  .median {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted);
  }
  .chart {
    margin-top: 4px;
    /* uPlot canvas reads parent width; nothing to do here. */
  }
  .empty {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--muted-deep);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border: 1px dashed var(--border);
    border-radius: 6px;
  }
</style>
