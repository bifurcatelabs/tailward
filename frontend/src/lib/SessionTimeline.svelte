<script>
  import { live } from './live.svelte.js';

  // Color per event type — kept in lockstep with the chip palette in
  // FeedItem so the timeline reads as a compressed view of the feed.
  const COLORS = {
    turn: 'var(--accent)',
    user_turn: 'var(--ok)',
    tool_call: 'var(--muted)',
    constraint_violation: 'var(--err)',
    scope_snapshot: 'rgba(94,197,179,0.45)',
    scope_creep: 'var(--warn)',
    rubric_sample: 'var(--accent-soft)',
    rubric_in_flight: 'var(--muted-deep)',
    rubric_done: 'var(--muted-deep)',
    drift: 'var(--warn)',
    claim: 'var(--err)',
    report_progress: 'var(--accent)',
    report_ready: 'var(--accent)',
    session_closed: 'var(--muted)',
    compact_summary: 'var(--accent)',
  };

  // Compute layout in epoch-seconds space so a long idle gap reads
  // as a long gap, not just a 1px tick like the rest. If we have <2
  // events with timestamps we fall back to evenly-spaced index layout.
  let layout = $derived.by(() => {
    const arc = live.arc;
    if (!arc.length) return [];
    const stamped = arc.filter((e) => e.t != null);
    if (stamped.length < 2) {
      return arc.map((e, i) => ({
        ...e,
        pct: arc.length === 1 ? 0 : (i / (arc.length - 1)) * 100,
      }));
    }
    let tMin = Infinity, tMax = -Infinity;
    for (const e of stamped) {
      if (e.t < tMin) tMin = e.t;
      if (e.t > tMax) tMax = e.t;
    }
    const span = tMax - tMin || 1;
    return arc.map((e) => ({
      ...e,
      pct: e.t != null ? ((e.t - tMin) / span) * 100 : null,
    }));
  });

  let count = $derived(live.arc.length);
</script>

<section class="timeline">
  <header>
    <span class="label">session arc</span>
    <span class="count">{count} event{count === 1 ? '' : 's'}</span>
  </header>
  <div class="track" role="img" aria-label="session event timeline">
    {#if layout.length === 0}
      <div class="placeholder">no activity yet</div>
    {:else}
      {#each layout as e (e.id)}
        {#if e.pct != null}
          <span
            class="tick"
            style="left: {e.pct}%; background: {COLORS[e.type] ?? 'var(--muted)'};"
            title="{e.type}"
          ></span>
        {/if}
      {/each}
    {/if}
  </div>
</section>

<style>
  .timeline {
    margin: 16px 24px 0;
    padding: 14px 18px 18px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 10px;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 600;
  }
  .count {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted-deep);
    font-variant-numeric: tabular-nums;
  }
  .track {
    position: relative;
    height: 22px;
    background:
      linear-gradient(90deg, var(--surface-2) 0%, var(--surface-2) 100%);
    border-radius: 4px;
    overflow: hidden;
  }
  .track::before {
    /* faint baseline line so the row has structure even when sparse */
    content: '';
    position: absolute;
    left: 0; right: 0; top: 50%;
    height: 1px;
    background: var(--border);
  }
  .tick {
    position: absolute;
    top: 4px;
    bottom: 4px;
    width: 2px;
    border-radius: 1px;
    transform: translateX(-1px);
    opacity: 0.85;
    transition: opacity 120ms;
  }
  .tick:hover { opacity: 1; }
  .placeholder {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--muted-deep);
    font-size: 11px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
</style>
