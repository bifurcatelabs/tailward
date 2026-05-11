<script>
  import { live } from './live.svelte.js';
  import { fmtClock, humanizeDuration } from './format.js';
  import { feedFilter } from './feedFilter.svelte.js';
  import SynthTrigger from './SynthTrigger.svelte';

  let { ph = '', sessionId = '' } = $props();

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
    permission_mode_change: '#8a96a8',
    away_summary: '#8a96a8',
    tool_interrupted: 'var(--warn)',
    exfiltration_alert: 'var(--err)',
    memory_edit: 'var(--ok)',
    turn_metric: 'var(--accent-soft)',
  };

  // Friendly label per event type. Mirrors FeedItem.svelte's chipLabel
  // so the timeline tooltip reads with the same vocabulary the rest of
  // the dashboard uses. Duplicated here intentionally — extracting to
  // a shared module is queued (see passes.md follow-ups) but the cost
  // is small enough today.
  const LABELS = {
    turn: 'assistant',
    user_turn: 'user',
    tool_call: 'tool',
    constraint_violation: 'violation',
    scope_snapshot: 'scope',
    scope_creep: 'scope creep',
    rubric_sample: 'rubric',
    rubric_in_flight: 'rubric…',
    rubric_done: 'rubric done',
    drift: 'drift',
    claim: 'claim',
    report_progress: 'report',
    report_ready: 'report',
    session_closed: 'session closed',
    compact_summary: 'synthesized',
    turn_metric: 'perf',
    permission_mode_change: 'permission',
    away_summary: 'away',
    tool_interrupted: 'declined',
    exfiltration_alert: 'secret',
    memory_edit: 'memory',
  };

  // Idle-gap threshold for period banding. Gaps longer than this get
  // a dimmed background band on the track + their own hover info.
  // 120s skips routine tool round-trips and captures genuine
  // "stepped away" gaps without flooding the timeline with bands
  // during normal activity.
  const IDLE_GAP_THRESHOLD_SEC = 120;

  // Time-range lens for the strip. A multi-day session compressed
  // into one viewport-width track turns each cluster into hundreds
  // of events with no useful resolution. The lens trims the view to
  // the last N seconds of activity so individual ticks stay
  // distinguishable; "all" keeps the full arc for cross-session
  // overview at the cost of density.
  const RANGES = [
    { key: '30m', label: '30m', seconds: 1800 },
    { key: '1h',  label: '1h',  seconds: 3600 },
    { key: '8h',  label: '8h',  seconds: 28800 },
    { key: '24h', label: '24h', seconds: 86400 },
    { key: 'all', label: 'all', seconds: null },
  ];

  let activeRange = $state('24h');

  // Cluster threshold — ticks falling within this pct distance of
  // each other get merged into one. 0.4% on a 1000-pixel track is
  // ~4 pixels — wider than the 2px tick so neighbors that visually
  // overlap collapse into a single hoverable cluster instead of
  // hiding their neighbors under the topmost tick.
  const CLUSTER_PCT_THRESHOLD = 0.4;

  // Window bounds drive layout, filtering, AND the header span as one
  // source of truth. ``tMax`` is always the latest stamped event in
  // the full session arc (right edge = "now-ish"); ``tMin`` is either
  // the session's earliest event ("all") or ``tMax - range.seconds``
  // for fixed ranges, clamped so we never claim a window that
  // pre-dates the session itself. The strip then renders the full
  // window even if activity within it is sparse — that's the fix
  // for "8h selected but only 3h shown" (the layout was rescaling to
  // the visible activity instead of the requested window).
  let windowBounds = $derived.by(() => {
    const arc = live.arc;
    if (!arc.length) return null;
    const stamped = arc.filter((e) => e.t != null);
    if (!stamped.length) return null;
    let sessionMin = Infinity, sessionMax = -Infinity;
    for (const e of stamped) {
      if (e.t < sessionMin) sessionMin = e.t;
      if (e.t > sessionMax) sessionMax = e.t;
    }
    const range = RANGES.find((r) => r.key === activeRange);
    let tMin = sessionMin;
    if (range && range.seconds != null) {
      tMin = Math.max(sessionMin, sessionMax - range.seconds);
    }
    return { tMin, tMax: sessionMax };
  });

  // Filtered arc — events inside the window AND passing the shared
  // feedFilter pills. The pill filter is the same one driving the
  // Feed below; toggling pills on either surface narrows both.
  let filteredArc = $derived.by(() => {
    const arc = live.arc;
    if (!arc.length) return arc;
    let out = arc;
    const wb = windowBounds;
    if (wb) {
      out = out.filter((e) => e.t == null || (e.t >= wb.tMin && e.t <= wb.tMax));
    }
    if (!feedFilter.empty()) {
      out = out.filter((e) => feedFilter.matches(e));
    }
    return out;
  });

  // Layout in epoch-seconds space, scaled to the *window* bounds (not
  // to the visible-event bounds). A long idle gap at the start of the
  // window reads as empty space at the left of the track; the right
  // edge always represents the latest event in the session.
  let layout = $derived.by(() => {
    const arc = filteredArc;
    if (!arc.length) return [];
    const wb = windowBounds;
    if (!wb) {
      // Unstamped fallback: even spacing.
      return arc.map((e, i) => ({
        ...e,
        pct: arc.length === 1 ? 0 : (i / (arc.length - 1)) * 100,
      }));
    }
    const span = wb.tMax - wb.tMin || 1;
    return arc.map((e) => ({
      ...e,
      pct: e.t != null ? ((e.t - wb.tMin) / span) * 100 : null,
    }));
  });

  // Header span info — start/end clocks + total duration of the
  // *window* (not the visible-events span). For "all" this matches
  // the session boundaries; for fixed ranges it's "tMax-N → tMax".
  let headerSpan = $derived.by(() => {
    const wb = windowBounds;
    if (!wb) return null;
    return {
      startStr: fmtClock(new Date(wb.tMin * 1000)),
      endStr: fmtClock(new Date(wb.tMax * 1000)),
      durationStr: humanizeDuration(wb.tMax - wb.tMin),
    };
  });

  // Cluster overlapping ticks. Walking the pct-sorted list, any tick
  // whose pct is within the threshold of the previous merges into the
  // running cluster. Single-event clusters render as ordinary ticks;
  // multi-event clusters get a wider tick + a count-on-hover list so
  // dense regions don't hide events under the topmost.
  let clusters = $derived.by(() => {
    const stamped = layout.filter((e) => e.pct != null);
    if (!stamped.length) return [];
    const sorted = [...stamped].sort((a, b) => a.pct - b.pct);
    const out = [];
    let current = null;
    for (const e of sorted) {
      if (current && e.pct - current.maxPct <= CLUSTER_PCT_THRESHOLD) {
        current.events.push(e);
        current.maxPct = e.pct;
      } else {
        current = { minPct: e.pct, maxPct: e.pct, events: [e] };
        out.push(current);
      }
    }
    return out.map((c, i) => ({
      key: `${c.events[0].id}-${i}`,
      pct: (c.minPct + c.maxPct) / 2,
      events: c.events,
      count: c.events.length,
    }));
  });

  // Idle gaps detected anywhere on the rendered window: leading
  // (window start → first event), trailing (last event → window
  // end), and inter-event. Without the leading/trailing variants,
  // selecting "8h" for a session where activity only spans the last
  // 3h would leave the first 5h as plain track without any idle
  // signal — visually empty but unlabeled. Now the strip is honest
  // about every minute of the requested window: events are events,
  // gaps are gaps.
  let periods = $derived.by(() => {
    const wb = windowBounds;
    const stamped = layout.filter((e) => e.t != null && e.pct != null);
    if (!wb) return [];
    if (!stamped.length) {
      // Whole window is empty under the current filter / range.
      const span = wb.tMax - wb.tMin;
      if (span > IDLE_GAP_THRESHOLD_SEC) {
        return [{
          kind: 'idle',
          startPct: 0,
          endPct: 100,
          durationSec: span,
          startSec: wb.tMin,
          endSec: wb.tMax,
        }];
      }
      return [];
    }
    const sorted = [...stamped].sort((a, b) => a.t - b.t);
    const out = [];

    // Leading: window start → first event in view.
    const first = sorted[0];
    const leading = first.t - wb.tMin;
    if (leading > IDLE_GAP_THRESHOLD_SEC) {
      out.push({
        kind: 'idle',
        startPct: 0,
        endPct: first.pct,
        durationSec: leading,
        startSec: wb.tMin,
        endSec: first.t,
      });
    }

    // Inter-event gaps.
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i];
      const b = sorted[i + 1];
      const gap = b.t - a.t;
      if (gap > IDLE_GAP_THRESHOLD_SEC) {
        out.push({
          kind: 'idle',
          startPct: a.pct,
          endPct: b.pct,
          durationSec: gap,
          startSec: a.t,
          endSec: b.t,
        });
      }
    }

    // Trailing: last event in view → window end.
    const last = sorted[sorted.length - 1];
    const trailing = wb.tMax - last.t;
    if (trailing > IDLE_GAP_THRESHOLD_SEC) {
      out.push({
        kind: 'idle',
        startPct: last.pct,
        endPct: 100,
        durationSec: trailing,
        startSec: last.t,
        endSec: wb.tMax,
      });
    }
    return out;
  });

  // Full session count vs visible count under the current lens.
  // Both surfaced in the header so the user knows the lens is
  // *windowing* the data, not the data being thin.
  let totalCount = $derived(live.arc.length);
  let count = $derived(filteredArc.length);

  // Hovered element drives the floating tooltip. One shared tooltip
  // is cheaper than per-element popovers and avoids overlap glitches
  // when adjacent clusters share <2px of horizontal space.
  let hover = $state(null);

  function clusterEnter(ev, c) {
    if (c.count === 1) {
      const e = c.events[0];
      const sortedByTime = layout
        .filter((x) => x.t != null)
        .sort((a, b) => a.t - b.t);
      const idx = sortedByTime.findIndex((x) => x.id === e.id);
      const prev = idx > 0 ? sortedByTime[idx - 1] : null;
      const deltaSec = e.t != null && prev?.t != null ? e.t - prev.t : null;
      hover = {
        kind: 'tick',
        label: LABELS[e.type] ?? e.type,
        time: e.t != null ? fmtClock(new Date(e.t * 1000)) : null,
        delta: deltaSec != null ? `+${humanizeDuration(deltaSec)}` : null,
        x: ev.clientX,
        y: ev.clientY,
      };
    } else {
      // Cluster: surface the first ~5 members with friendly label +
      // local time, plus an "+N more" tail so dense regions are still
      // glanceable. Show start → end of the cluster's time range so
      // the user knows how compressed in time it actually is.
      const sortedMembers = [...c.events]
        .filter((x) => x.t != null)
        .sort((a, b) => a.t - b.t);
      const head = sortedMembers.slice(0, 5);
      const more = sortedMembers.length > 5 ? sortedMembers.length - 5 : 0;
      const tMin = sortedMembers.length ? sortedMembers[0].t : null;
      const tMax = sortedMembers.length ? sortedMembers[sortedMembers.length - 1].t : null;
      hover = {
        kind: 'cluster',
        count: c.count,
        time: tMin != null && tMax != null
          ? (tMin === tMax
              ? fmtClock(new Date(tMin * 1000))
              : `${fmtClock(new Date(tMin * 1000))} → ${fmtClock(new Date(tMax * 1000))}`)
          : null,
        list: head.map((e) => ({
          label: LABELS[e.type] ?? e.type,
          time: e.t != null ? fmtClock(new Date(e.t * 1000)) : null,
        })),
        more,
        x: ev.clientX,
        y: ev.clientY,
      };
    }
  }

  function periodEnter(ev, p) {
    hover = {
      kind: 'period',
      label: 'no activity',
      time: p.startSec != null
        ? `${fmtClock(new Date(p.startSec * 1000))} → ${fmtClock(new Date(p.endSec * 1000))}`
        : null,
      delta: humanizeDuration(p.durationSec),
      x: ev.clientX,
      y: ev.clientY,
    };
  }

  function leave() {
    hover = null;
  }

  // Height encoding: every tick is the same narrow width (2px) so
  // the arc reads as time density across the horizontal axis. Count
  // encodes vertically — a single event is a short tick, a busy
  // cluster grows toward the top of the track. Reads as a sparkline
  // /histogram instead of width-encoded chunks that competed with
  // the no-activity band for visual weight.
  const TICK_WIDTH_PX = 2;
  const TRACK_HEIGHT_PX = 22;
  const TICK_MIN_PX = 6;
  function tickHeightPx(count) {
    if (count <= 1) return TICK_MIN_PX;
    return Math.min(TRACK_HEIGHT_PX, TICK_MIN_PX + Math.log2(count) * 4);
  }
</script>

<section class="timeline">
  <header>
    <span class="label">session arc</span>
    <div class="range-group" role="group" aria-label="time range">
      {#each RANGES as r (r.key)}
        <button
          type="button"
          class="range-pill"
          class:active={activeRange === r.key}
          onclick={() => (activeRange = r.key)}
          title="show events from the last {r.label === 'all' ? 'session entirely' : r.label}"
        >{r.label}</button>
      {/each}
    </div>
    {#if ph && sessionId}
      <SynthTrigger {ph} {sessionId} />
    {/if}
    <span
      class="count"
      title="shown / total in session arc — narrowed by the range and filter pills"
    >
      {count}{#if count !== totalCount} / {totalCount}{/if} event{totalCount === 1 ? '' : 's'}
      {#if headerSpan}
        <span class="span-sep">·</span>
        <span class="time-range">{headerSpan.startStr} → {headerSpan.endStr}</span>
        <span class="span-sep">·</span>
        <span class="duration">{headerSpan.durationStr}</span>
      {/if}
    </span>
  </header>
  <div class="track" role="img" aria-label="session event timeline">
    {#if layout.length === 0}
      <div class="placeholder">no activity yet</div>
    {:else}
      <!-- Period bands render behind the ticks. Idle-gap is the only
           band kind today; the classifier-driven palette is queued. -->
      {#each periods as p, i (i)}
        <span
          class="band band-{p.kind}"
          style="left: {p.startPct}%; width: {p.endPct - p.startPct}%;"
          onmouseenter={(ev) => periodEnter(ev, p)}
          onmouseleave={leave}
          role="img"
          aria-label="no activity, {humanizeDuration(p.durationSec)}"
        ></span>
      {/each}
      {#each clusters as c (c.key)}
        <span
          class="tick"
          class:cluster={c.count > 1}
          style="
            left: {c.pct}%;
            background: {COLORS[c.events[0].type] ?? 'var(--muted)'};
            width: {TICK_WIDTH_PX}px;
            height: {tickHeightPx(c.count)}px;
            transform: translateX(-{TICK_WIDTH_PX / 2}px);
          "
          onmouseenter={(ev) => clusterEnter(ev, c)}
          onmouseleave={leave}
          aria-label={c.count > 1 ? `${c.count} events` : (LABELS[c.events[0].type] ?? c.events[0].type)}
        ></span>
      {/each}
    {/if}
  </div>
  {#if hover}
    <!-- Single floating tooltip pinned to viewport coordinates of the
         hovered element. Translates upward + leftward so the tip
         hovers above the cursor without occluding the underlying
         element, and the centered transform keeps it stable as the
         pointer crosses adjacent ticks. -->
    <div
      class="tooltip"
      class:tooltip-cluster={hover.kind === 'cluster'}
      style="left: {hover.x}px; top: {hover.y}px;"
    >
      {#if hover.kind === 'cluster'}
        <div class="tt-row tt-label">{hover.count} events</div>
        {#if hover.time}
          <div class="tt-row tt-time">{hover.time}</div>
        {/if}
        <ul class="tt-list">
          {#each hover.list as item, i (i)}
            <li>
              <span class="tt-list-label">{item.label}</span>
              <span class="tt-list-time">{item.time}</span>
            </li>
          {/each}
          {#if hover.more}
            <li class="tt-more">+{hover.more} more</li>
          {/if}
        </ul>
      {:else}
        <div class="tt-row tt-label">{hover.label}</div>
        {#if hover.time}
          <div class="tt-row tt-time">{hover.time}</div>
        {/if}
        {#if hover.delta}
          <div class="tt-row tt-delta">
            {hover.kind === 'period' ? hover.delta : hover.delta}
          </div>
        {/if}
      {/if}
    </div>
  {/if}
</section>

<style>
  .timeline {
    margin: 16px 24px 0;
    padding: 14px 18px 18px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    position: relative;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 12px;
    flex-wrap: wrap;
  }
  .label {
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 600;
  }
  /* Range-lens pill group. Mirrors Feed.svelte's filter pill styling
     so the dashboard has one vocabulary for "narrow what you see"
     controls. Active pill picks up the accent treatment. */
  .range-group {
    display: flex;
    gap: 4px;
  }
  .range-pill {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 2px 8px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    border-radius: 999px;
    cursor: pointer;
    font-family: inherit;
    transition: color 120ms, border-color 120ms, background 120ms;
  }
  .range-pill:hover { color: var(--text-soft); border-color: var(--text-soft); }
  .range-pill.active {
    color: var(--accent);
    border-color: rgba(232,153,104,0.40);
    background: rgba(232,153,104,0.08);
  }
  /* Type-pill filter row. Same visual language as the Feed's filter
     bar so the user reads them as the same control. The shared
     feedFilter state means clicks here update the Feed below and
     vice versa. */
  .filter-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-bottom: 10px;
  }
  .filter-pill {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 3px 10px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    border-radius: 999px;
    cursor: pointer;
    font-family: inherit;
    transition: color 120ms, border-color 120ms, background 120ms;
  }
  .filter-pill:hover { color: var(--text-soft); border-color: var(--text-soft); }
  .filter-pill.active {
    color: var(--accent);
    border-color: rgba(232,153,104,0.40);
    background: rgba(232,153,104,0.08);
  }
  .count {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted-deep);
    font-variant-numeric: tabular-nums;
    display: flex;
    align-items: baseline;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .span-sep { color: var(--muted-deep); opacity: 0.5; }
  .time-range { color: var(--muted); }
  .duration { color: var(--muted); }
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
  .band {
    position: absolute;
    top: 0;
    bottom: 0;
    pointer-events: auto;
    z-index: 0;
  }
  /* No-activity band — a subtle dim wash with dotted endpoint markers
     so the eye registers "gap" without competing with the event ticks
     for visual weight. Previously a loud diagonal stripe pattern. */
  .band-idle {
    background: rgba(255, 255, 255, 0.015);
    border-left: 1px dotted rgba(255, 255, 255, 0.10);
    border-right: 1px dotted rgba(255, 255, 255, 0.10);
    cursor: help;
  }
  .band-idle:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  .tick {
    position: absolute;
    bottom: 2px;
    border-radius: 1px;
    opacity: 0.85;
    transition: opacity 120ms, height 180ms ease-out;
    cursor: help;
    z-index: 1;
  }
  .tick:hover {
    opacity: 1;
  }
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

  /* Floating tooltip pinned to viewport coordinates. position:fixed
     keeps it stable regardless of ancestor transforms. The negative
     translate offsets pull it above the cursor. */
  .tooltip {
    position: fixed;
    transform: translate(-50%, calc(-100% - 12px));
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    padding: 6px 10px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
    pointer-events: none;
    z-index: 100;
    min-width: 80px;
    max-width: 280px;
  }
  .tooltip-cluster {
    min-width: 160px;
  }
  .tt-row {
    font-size: 11px;
    line-height: 1.45;
    white-space: nowrap;
  }
  .tt-label {
    color: var(--text);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 10px;
  }
  .tt-time {
    color: var(--text-soft);
    font-family: var(--mono);
    font-size: 11px;
  }
  .tt-delta {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 10px;
  }
  .tt-list {
    list-style: none;
    margin: 6px 0 0;
    padding: 6px 0 0;
    border-top: 1px solid var(--border);
  }
  .tt-list li {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    font-size: 10px;
    line-height: 1.5;
  }
  .tt-list-label {
    color: var(--text-soft);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .tt-list-time {
    color: var(--muted);
    font-family: var(--mono);
  }
  .tt-more {
    color: var(--muted-deep);
    font-style: italic;
    margin-top: 2px;
  }
</style>
