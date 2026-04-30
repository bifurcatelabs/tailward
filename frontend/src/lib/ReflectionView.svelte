<script>
  /**
   * Reflection view — am I behaving?
   *
   * v1 surfaces signals derivable from existing data with no LLM
   * calls: typed-turn pacing, prompt-length distribution, the user's
   * response cadence on constraint-violation surfacings, and how
   * often assistant claims held up under verification.
   *
   * The self-rubric (LLM-scored user-side dimensions) lands in v2 as
   * a separate panel; this view leaves room for it without faking
   * placeholder content here.
   */
  import SessionsPanel from './SessionsPanel.svelte';
  import SelfRubricPanel from './SelfRubricPanel.svelte';

  let { ph } = $props();

  let loading = $state(true);
  let error = $state(null);
  let data = $state(null);

  async function load() {
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

  // Re-fetch when LiveStore signals a violation-status change
  // (ack / dismiss in the Session feed). Without this the response
  // cadence card drifts behind the ledger until the user reloads.
  $effect(() => {
    if (typeof window === 'undefined') return;
    const handler = () => { load(); };
    window.addEventListener('reflection:refresh', handler);
    return () => window.removeEventListener('reflection:refresh', handler);
  });

  // ---------- helpers ----------
  function quantiles(arr, qs) {
    if (!arr || arr.length === 0) return qs.map(() => null);
    const sorted = [...arr].sort((a, b) => a - b);
    return qs.map((q) => {
      const i = (sorted.length - 1) * q;
      const lo = Math.floor(i);
      const hi = Math.ceil(i);
      if (lo === hi) return sorted[lo];
      return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
    });
  }

  function fmtSeconds(s) {
    if (s == null) return '—';
    if (s < 60) return `${s.toFixed(1)}s`;
    if (s < 3600) return `${(s / 60).toFixed(1)}m`;
    return `${(s / 3600).toFixed(2)}h`;
  }

  function fmtChars(n) {
    if (n == null) return '—';
    if (n < 1000) return `${n}`;
    return `${(n / 1000).toFixed(1)}k`;
  }

  // ---------- derived stats ----------
  let intervalStats = $derived.by(() => {
    if (!data || !data.intervals_seconds?.length) return null;
    const a = data.intervals_seconds;
    const [p25, p50, p75, p95] = quantiles(a, [0.25, 0.5, 0.75, 0.95]);
    return {
      n: a.length,
      p25, p50, p75, p95,
      max: Math.max(...a),
      min: Math.min(...a),
    };
  });

  let lengthStats = $derived.by(() => {
    if (!data || !data.prompt_lengths_chars?.length) return null;
    const a = data.prompt_lengths_chars;
    const [p25, p50, p75, p95] = quantiles(a, [0.25, 0.5, 0.75, 0.95]);
    return {
      n: a.length,
      p25, p50, p75, p95,
      max: Math.max(...a),
      min: Math.min(...a),
    };
  });

  // Histogram bins for prompt-length distribution. Log-ish bins so a
  // few long pastes don't flatten the typical distribution.
  let lengthHistogram = $derived.by(() => {
    const a = data?.prompt_lengths_chars;
    if (!a || a.length === 0) return [];
    const bins = [0, 50, 150, 400, 1000, 3000, 10000, Infinity];
    const labels = ['<50', '50-150', '150-400', '400-1k', '1k-3k', '3k-10k', '>10k'];
    const counts = labels.map(() => 0);
    for (const v of a) {
      for (let i = 0; i < counts.length; i++) {
        if (v < bins[i + 1]) { counts[i]++; break; }
      }
    }
    const max = Math.max(...counts);
    return labels.map((label, i) => ({
      label,
      count: counts[i],
      pct: max > 0 ? (counts[i] / max) * 100 : 0,
    }));
  });

  // Idle-gap histogram on a fixed ladder of seconds. Captures
  // "fast follow-up" vs "stepped away" patterns.
  let intervalHistogram = $derived.by(() => {
    const a = data?.intervals_seconds;
    if (!a || a.length === 0) return [];
    const bins = [0, 5, 30, 120, 600, 1800, 7200, Infinity];
    const labels = ['<5s', '5-30s', '30s-2m', '2-10m', '10-30m', '30m-2h', '>2h'];
    const counts = labels.map(() => 0);
    for (const v of a) {
      for (let i = 0; i < counts.length; i++) {
        if (v < bins[i + 1]) { counts[i]++; break; }
      }
    }
    const max = Math.max(...counts);
    return labels.map((label, i) => ({
      label,
      count: counts[i],
      pct: max > 0 ? (counts[i] / max) * 100 : 0,
    }));
  });

  let approvalRows = $derived.by(() => {
    const a = data?.approvals;
    if (!a) return [];
    const total = (a.new || 0) + (a.acknowledged || 0) + (a.dismissed || 0);
    return [
      { label: 'acknowledged', n: a.acknowledged || 0, total, kind: 'ok' },
      { label: 'dismissed',    n: a.dismissed || 0,    total, kind: 'warn' },
      { label: 'new (no response)', n: a.new || 0,     total, kind: 'muted' },
    ];
  });

  // Tool-calls-by-permission-mode matrix. Rows = tool kinds, columns
  // = modes (default / acceptEdits / bypassPermissions / plan) +
  // an "interrupted" column. Computed by pivoting the flat
  // {tool, permission_mode, count} entries the backend returns.
  let toolModeMatrix = $derived.by(() => {
    const t = data?.tool_calls_by_mode;
    if (!t) return null;
    const toolCalls = t.tool_calls || [];
    const interruptions = t.interruptions || [];
    if (toolCalls.length === 0 && interruptions.length === 0) return null;

    // Mode order (deterministic): canonical Claude Code modes first,
    // then any unknown modes alphabetically. Unknown/null modes
    // collapse into "untagged" (older events without permission_mode
    // tagged — events emitted before the permission_mode field was
    // added to tool_call payloads).
    const UNTAGGED = 'untagged';
    const canonical = ['default', 'acceptEdits', 'bypassPermissions', 'plan'];
    const seenModes = new Set();
    for (const e of toolCalls) seenModes.add(e.permission_mode || UNTAGGED);
    const modes = [
      ...canonical.filter((m) => seenModes.has(m)),
      ...[...seenModes].filter((m) => !canonical.includes(m) && m !== UNTAGGED).sort(),
      ...(seenModes.has(UNTAGGED) ? [UNTAGGED] : []),
    ];

    // Tool list: union of tools in calls + interruptions.
    const toolSet = new Set();
    for (const e of toolCalls) if (e.tool) toolSet.add(e.tool);
    for (const e of interruptions) if (e.tool) toolSet.add(e.tool);
    const tools = [...toolSet].sort();

    // Matrix lookup
    const cellCount = (tool, mode) => {
      const entries = toolCalls.filter(
        (e) => e.tool === tool && (e.permission_mode || UNTAGGED) === mode,
      );
      return entries.reduce((s, e) => s + (e.count || 0), 0);
    };
    const interruptCount = (tool) =>
      interruptions
        .filter((e) => e.tool === tool)
        .reduce((s, e) => s + (e.count || 0), 0);

    const rows = tools.map((tool) => ({
      tool,
      cells: modes.map((m) => ({ mode: m, count: cellCount(tool, m) })),
      interrupted: interruptCount(tool),
      total: modes.reduce((s, m) => s + cellCount(tool, m), 0) + interruptCount(tool),
    }));

    return {
      modes,
      rows,
      totals: t.totals || { tool_calls: 0, interruptions: 0 },
    };
  });

</script>

<section class="view">
  <header class="hd">
    <h2>reflection</h2>
    <p>your patterns — pacing, prompt shape, response cadence, mode posture, and self-rubric.</p>
    <button class="reload" onclick={load} disabled={loading} title="refresh">↻</button>
  </header>

  {#if loading && !data}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if !data}
    <div class="empty">no data</div>
  {:else}
    <div class="grid">
      <!-- IDLE GAPS -->
      <div class="card">
        <h3>idle gaps between user prompts</h3>
        {#if intervalStats}
          <div class="quartiles">
            <div class="q"><span class="lbl">p25</span><span class="val">{fmtSeconds(intervalStats.p25)}</span></div>
            <div class="q"><span class="lbl">median</span><span class="val emph">{fmtSeconds(intervalStats.p50)}</span></div>
            <div class="q"><span class="lbl">p75</span><span class="val">{fmtSeconds(intervalStats.p75)}</span></div>
            <div class="q"><span class="lbl">p95</span><span class="val">{fmtSeconds(intervalStats.p95)}</span></div>
          </div>
          <div class="hist">
            {#each intervalHistogram as b (b.label)}
              <div class="hist-row">
                <span class="hist-lbl">{b.label}</span>
                <span class="hist-bar"><span class="hist-fill" style="width: {b.pct}%"></span></span>
                <span class="hist-n">{b.count}</span>
              </div>
            {/each}
          </div>
          <div class="footnote">
            n={intervalStats.n} intra-session intervals across this project. Synthesized turns excluded
            ({data.synthesized_user_turns} found). Long tails read as "stepped away," short ones as fast follow-ups.
          </div>
        {:else}
          <div class="empty inline">not enough typed turns yet</div>
        {/if}
      </div>

      <!-- PROMPT LENGTHS -->
      <div class="card">
        <h3>prompt length distribution</h3>
        {#if lengthStats}
          <div class="quartiles">
            <div class="q"><span class="lbl">p25</span><span class="val">{fmtChars(lengthStats.p25)}</span></div>
            <div class="q"><span class="lbl">median</span><span class="val emph">{fmtChars(lengthStats.p50)}</span></div>
            <div class="q"><span class="lbl">p75</span><span class="val">{fmtChars(lengthStats.p75)}</span></div>
            <div class="q"><span class="lbl">max</span><span class="val">{fmtChars(lengthStats.max)}</span></div>
          </div>
          <div class="hist">
            {#each lengthHistogram as b (b.label)}
              <div class="hist-row">
                <span class="hist-lbl">{b.label}</span>
                <span class="hist-bar"><span class="hist-fill" style="width: {b.pct}%"></span></span>
                <span class="hist-n">{b.count}</span>
              </div>
            {/each}
          </div>
          <div class="footnote">
            n={lengthStats.n} typed prompts. Many short prompts → quick iteration. Many long ones → big specs or pasted context.
          </div>
        {:else}
          <div class="empty inline">no prompt-length data yet</div>
        {/if}
      </div>

      <!-- APPROVALS -->
      <div class="card">
        <h3>destructive-action response cadence</h3>
        <div class="bars">
          {#each approvalRows as r (r.label)}
            <div class="bar-row">
              <span class="bar-lbl">{r.label}</span>
              <span class="bar-track">
                <span class="bar-fill kind-{r.kind}"
                  style="width: {r.total > 0 ? (r.n / r.total) * 100 : 0}%"></span>
              </span>
              <span class="bar-n">{r.n}</span>
            </div>
          {/each}
          {#if data.memory_edits != null}
            <div
              class="bar-row bar-row-meta"
              title="edits to ~/.claude/projects/<ph>/memory/**"
            >
              <span class="bar-lbl">memory edits</span>
              <span class="meta-text">surfaced separately, not counted as violations</span>
              <span class="bar-n">{data.memory_edits}</span>
            </div>
          {/if}
        </div>
        <div class="footnote">
          How you responded when Warden surfaced a constraint violation. Many "new" → events going unread; many "dismissed" → noise mismatch worth investigating.
        </div>
      </div>

      <!-- TOOL CALLS BY PERMISSION MODE -->
      <div class="card card-wide">
        <h3>tool calls by user-selected permission mode</h3>
        {#if toolModeMatrix}
          <table class="tool-mode">
            <thead>
              <tr>
                <th class="tool-col">tool</th>
                {#each toolModeMatrix.modes as m (m)}
                  <th><code>{m}</code></th>
                {/each}
                <th class="interrupted-col">declined</th>
                <th class="total-col">total</th>
              </tr>
            </thead>
            <tbody>
              {#each toolModeMatrix.rows as r (r.tool)}
                <tr>
                  <td class="tool-col"><strong>{r.tool}</strong></td>
                  {#each r.cells as c (c.mode)}
                    <td class:zero={c.count === 0}>{c.count}</td>
                  {/each}
                  <td class="interrupted-col" class:zero={r.interrupted === 0}>{r.interrupted}</td>
                  <td class="total-col">{r.total}</td>
                </tr>
              {/each}
            </tbody>
          </table>
          <div class="footnote">
            {toolModeMatrix.totals.tool_calls} tool calls, {toolModeMatrix.totals.interruptions} interrupted across this project.
            <code>untagged</code> = events without a recorded permission_mode value.
          </div>
        {:else}
          <div class="empty inline">no tool calls captured yet for this project</div>
        {/if}
      </div>

    </div>

    <SelfRubricPanel {ph} />

    <SessionsPanel />

    <p class="cutout">
      Two layers: derived signals above (no LLM, free) and the self-rubric below (LLM-scored,
      every <code>user_rubric_turn_interval</code> typed user turns). The bottom layer accumulates
      slowly by design — early scores cluster near 3.0 until the LLM has enough context to
      discriminate.
    </p>
  {/if}
</section>

<style>
  .view { padding: 24px; max-width: 1100px; margin: 0 auto; }
  .hd {
    display: flex;
    align-items: baseline;
    gap: 16px;
  }
  .hd h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .hd p {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    flex: 1;
  }
  .reload {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    font-size: 16px;
    padding: 0 6px;
  }
  .reload:hover:not(:disabled) { color: var(--text); }
  .reload:disabled { opacity: 0.4; cursor: default; }

  .grid {
    margin-top: 24px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 880px) { .grid { grid-template-columns: 1fr; } }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  /* Wide card spans both columns of the grid — used by the
     tool-mode matrix which is too wide to fit in a single column
     comfortably. */
  .card-wide { grid-column: 1 / -1; }
  .tool-mode {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .tool-mode th, .tool-mode td {
    padding: 6px 10px;
    text-align: right;
    border-bottom: 1px solid var(--border);
  }
  .tool-mode th {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-weight: 600;
  }
  .tool-mode td {
    font-family: var(--mono);
    color: var(--text-soft);
    font-variant-numeric: tabular-nums;
  }
  .tool-mode .tool-col {
    text-align: left;
    font-family: inherit;
  }
  .tool-mode tbody td.zero { color: var(--muted-deep); }
  .tool-mode .interrupted-col {
    color: var(--warn);
  }
  .tool-mode tbody td.interrupted-col.zero { color: var(--muted-deep); }
  .tool-mode .total-col {
    color: var(--text);
    font-weight: 600;
  }
  .tool-mode tbody tr:last-child td { border-bottom: 0; }
  .tool-mode th code {
    font-family: var(--mono);
    background: transparent;
    padding: 0;
    color: var(--muted);
    font-size: 10px;
  }
  .card h3 {
    margin: 0 0 14px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--muted);
    font-weight: 600;
  }

  .quartiles {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    margin-bottom: 16px;
  }
  .q {
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: var(--surface-2);
    border-radius: 6px;
    padding: 8px 10px;
  }
  .lbl {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted-deep);
  }
  .val {
    font-family: var(--mono);
    font-size: 13px;
    color: var(--text-soft);
  }
  .val.emph {
    color: var(--accent);
    font-weight: 600;
  }

  .hist { display: flex; flex-direction: column; gap: 4px; }
  .hist-row {
    display: grid;
    grid-template-columns: 80px 1fr 30px;
    align-items: center;
    gap: 8px;
    font-size: 11px;
  }
  .hist-lbl {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 10px;
    text-align: right;
  }
  .hist-bar {
    background: var(--surface-2);
    height: 10px;
    border-radius: 2px;
    overflow: hidden;
    position: relative;
  }
  .hist-fill {
    display: block;
    height: 100%;
    background: linear-gradient(90deg, var(--accent-soft), var(--accent));
    transition: width 240ms ease;
  }
  .hist-n {
    color: var(--muted);
    font-family: var(--mono);
    font-size: 11px;
    text-align: right;
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

  /* Memory-edits row shares the bar-row column rhythm so it slots
     into the .bars list without reading as a separate block. The
     middle column is descriptive text instead of a fill bar (memory
     edits don't have a status partition to chart), and a soft
     dashed divider above signals "still part of this card, but a
     different kind of count." */
  .bar-row-meta {
    margin-top: 6px;
    padding-top: 8px;
    border-top: 1px dashed var(--border);
  }
  .bar-row-meta .bar-lbl { color: var(--muted); }
  .bar-row-meta .bar-n { color: var(--ok); font-weight: 600; }
  .meta-text {
    color: var(--muted-deep);
    font-size: 11px;
    font-style: italic;
  }

  .empty {
    padding: 32px 12px;
    color: var(--muted);
    text-align: center;
    font-size: 13px;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }
  .empty.inline {
    padding: 12px 0;
    text-align: left;
    font-size: 12px;
  }

  .cutout {
    margin: 28px 0 0;
    color: var(--muted-deep);
    font-size: 12px;
    line-height: 1.6;
    max-width: 720px;
    border-left: 2px solid var(--border-strong);
    padding-left: 12px;
  }
</style>
