<script>
  // Self-contained inline SVG sparkline. No chart lib needed for a
  // line this simple. Takes an array of numeric ``points`` and draws
  // a smooth path scaled to the given dimensions.

  let {
    points = [],
    width = 96,
    height = 24,
    stroke = 'currentColor',
    fill = 'none',
    strokeWidth = 1.4,
  } = $props();

  let pathD = $derived.by(() => {
    if (!points || points.length < 2) return '';
    const n = points.length;
    let min = Infinity, max = -Infinity;
    for (const v of points) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const span = max - min || 1;
    const xStep = width / (n - 1);
    const pad = strokeWidth;
    const innerH = height - pad * 2;
    const segs = points.map((v, i) => {
      const x = i * xStep;
      const y = pad + innerH - ((v - min) / span) * innerH;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
    });
    return segs.join(' ');
  });

  let areaD = $derived.by(() => {
    if (!pathD) return '';
    return `${pathD} L${width} ${height} L0 ${height} Z`;
  });

  let last = $derived(points && points.length ? points[points.length - 1] : null);
  let lastPoint = $derived.by(() => {
    if (!points || points.length < 2 || last == null) return null;
    const n = points.length;
    let min = Infinity, max = -Infinity;
    for (const v of points) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const span = max - min || 1;
    const pad = strokeWidth;
    const innerH = height - pad * 2;
    const x = width;
    const y = pad + innerH - ((last - min) / span) * innerH;
    return { x, y };
  });
</script>

{#if pathD}
  <svg {width} {height} viewBox="0 0 {width} {height}" aria-hidden="true">
    {#if fill && fill !== 'none'}
      <path d={areaD} fill={fill} />
    {/if}
    <path
      d={pathD}
      fill="none"
      stroke={stroke}
      stroke-width={strokeWidth}
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    {#if lastPoint}
      <circle cx={lastPoint.x} cy={lastPoint.y} r="2.2" fill={stroke} />
    {/if}
  </svg>
{:else}
  <svg {width} {height} viewBox="0 0 {width} {height}" aria-hidden="true">
    <line
      x1="0"
      y1={height / 2}
      x2={width}
      y2={height / 2}
      stroke="var(--border-strong)"
      stroke-dasharray="2 3"
    />
  </svg>
{/if}
