<script>
  import uPlot from 'uplot';
  import 'uplot/dist/uPlot.min.css';
  import { onDestroy } from 'svelte';

  // Thin wrapper around uPlot. Takes a chronological array of x
  // values and one or more y-series, renders a dark-themed line
  // chart matching the tailward palette, and rebuilds when the data
  // shape changes (number of points or series count). Otherwise it
  // updates in place via uPlot's setData for cheap reactive
  // re-renders.

  let {
    /** Array of x-axis values (numbers). For turn-indexed data this
     *  is just [1, 2, 3, ...]; for time-indexed data, epoch seconds. */
    xValues = [],
    /** [{ label, color, data }, ...]. ``data`` length must match
     *  ``xValues.length`` — null entries draw a gap. */
    series = [],
    /** Display label below the chart (the x-axis is unlabelled by
     *  default since "turn N" or wall time is usually obvious). */
    yLabel = '',
    /** ``(v) => string`` for value tooltips and y-axis ticks. */
    formatY,
    /** Whether the x-axis ticks should render as integers (turn
     *  indices) rather than the default time format. */
    xIsIndex = true,
    height = 200,
  } = $props();

  let container;
  let plot = null;
  let lastShape = '';

  // Floating value tag, top-right of the chart. Stays put rather
  // than chasing the cursor so it doesn't add motion noise. Cleared
  // when the cursor leaves the chart area.
  let hoverIdx = $state(null);
  let hoverValues = $state([]);
  let hoverXLabel = $state('');

  // ----- styling that matches the tailward palette ------------------
  // Resolved at build time off the design tokens declared in App.svelte's
  // ``:global(:root)``. We pass concrete colors to uPlot rather than
  // CSS variables because the chart's canvas paint can't read CSS
  // custom properties directly.
  const PALETTE = {
    grid: 'rgba(58,66,84,0.35)',
    axis: '#7d8693',
    text: '#c0c5d1',
    bg: 'transparent',
  };

  function buildOptions(width) {
    return {
      width,
      height,
      padding: [12, 16, 28, 44],
      legend: { show: false },
      cursor: {
        drag: { x: false, y: false },
        points: {
          size: 6,
          width: 2,
          stroke: (u, sIdx) => series[sIdx - 1]?.color || '#fff',
          fill: '#0b0c10',
        },
      },
      hooks: {
        setCursor: [
          (u) => {
            const idx = u.cursor.idx;
            if (idx == null || idx < 0) {
              hoverIdx = null;
              return;
            }
            hoverIdx = idx;
            hoverValues = series.map((s) =>
              Array.isArray(s.data) ? s.data[idx] : null
            );
            const x = xValues[idx];
            if (xIsIndex) {
              hoverXLabel = `turn ${Math.round(x)}`;
            } else if (typeof x === 'number') {
              const d = new Date(x * 1000);
              hoverXLabel = d.toLocaleTimeString();
            } else {
              hoverXLabel = '';
            }
          },
        ],
      },
      scales: {
        x: { time: !xIsIndex },
        y: {},
      },
      axes: [
        {
          stroke: PALETTE.axis,
          grid: { stroke: PALETTE.grid, width: 1 },
          ticks: { stroke: PALETTE.grid },
          font: '11px ui-monospace, SFMono-Regular, Menlo, monospace',
          values: xIsIndex
            ? (u, splits) => splits.map((v) => Math.round(v).toString())
            : undefined,
        },
        {
          stroke: PALETTE.axis,
          grid: { stroke: PALETTE.grid, width: 1 },
          ticks: { stroke: PALETTE.grid },
          font: '11px ui-monospace, SFMono-Regular, Menlo, monospace',
          values: formatY
            ? (u, splits) => splits.map((v) => formatY(v))
            : undefined,
          size: 44,
          label: yLabel || undefined,
          labelSize: yLabel ? 26 : 0,
          labelFont: '10px ui-sans-serif, system-ui, sans-serif',
          labelGap: 4,
        },
      ],
      series: [
        { label: 'x' }, // x series is implicit
        ...series.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: 1.6,
          fill: s.fill || undefined,
          points: { size: 4 },
          value: formatY ? (u, v) => formatY(v) : undefined,
        })),
      ],
    };
  }

  function dataMatrix() {
    return [
      xValues,
      ...series.map((s) => s.data),
    ];
  }

  function shapeKey() {
    return `${xValues.length}|${series.length}|${series.map((s) => s.label).join(',')}|${height}|${xIsIndex}|${yLabel}`;
  }

  $effect(() => {
    if (!container) return;
    const want = shapeKey();
    if (plot && want === lastShape) {
      // Same shape, just new numbers — cheap path.
      plot.setData(dataMatrix());
      return;
    }
    // Shape changed — rebuild.
    if (plot) {
      plot.destroy();
      plot = null;
    }
    const width = container.clientWidth || 600;
    plot = new uPlot(buildOptions(width), dataMatrix(), container);
    lastShape = want;
  });

  // Resize the plot when the parent's width changes. ResizeObserver
  // covers responsive layout, the 1080px breakpoint, and the user
  // dragging their window.
  $effect(() => {
    if (!container) return;
    const ro = new ResizeObserver((entries) => {
      if (!plot) return;
      const w = entries[0].contentRect.width;
      if (w > 0 && w !== plot.width) {
        plot.setSize({ width: w, height });
      }
    });
    ro.observe(container);
    return () => ro.disconnect();
  });

  onDestroy(() => {
    if (plot) plot.destroy();
  });
</script>

<div class="chart" bind:this={container} style="height: {height}px;">
  {#if hoverIdx != null && hoverValues.some((v) => v != null)}
    <div class="hover-tag">
      {#if hoverXLabel}<span class="hover-x">{hoverXLabel}</span>{/if}
      {#each series as s, i (s.label)}
        {#if hoverValues[i] != null}
          <span class="hover-row">
            <span class="hover-dot" style="background: {s.color}"></span>
            <span class="hover-label">{s.label}</span>
            <span class="hover-val">{formatY ? formatY(hoverValues[i]) : hoverValues[i]}</span>
          </span>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .chart {
    width: 100%;
    position: relative;
  }
  .hover-tag {
    position: absolute;
    top: 6px;
    right: 12px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 6px 10px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-soft);
    pointer-events: none;
    display: flex;
    flex-direction: column;
    gap: 2px;
    z-index: 4;
  }
  .hover-x {
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 2px;
  }
  .hover-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .hover-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .hover-label {
    color: var(--muted);
    font-size: 10px;
  }
  .hover-val {
    color: var(--text);
    font-variant-numeric: tabular-nums;
    margin-left: auto;
  }
  /* uPlot's default styling is light-themed; override what bleeds
     through into our dark surface. The values/axis colors come from
     the per-instance options above; these are the structural bits. */
  :global(.u-wrap) { color: var(--text-soft); }
  :global(.u-legend) { display: none; }
  :global(.u-cursor-pt) { z-index: 5; }
  :global(.u-cursor-x), :global(.u-cursor-y) {
    border-color: var(--muted-deep) !important;
    border-style: dashed !important;
  }
  :global(.u-axis) { color: var(--muted); }
</style>
