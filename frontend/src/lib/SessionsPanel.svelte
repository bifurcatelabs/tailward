<script>
  /**
   * Past-sessions table for the Reflection view.
   *
   * Shows recent sessions across all projects with summary stats
   * (turns, token totals, avg rubric score, has-report flag). Click
   * a row to expand a deep view: 8-mode score card + rubric
   * trajectory plot.
   *
   * Pre-calibration sessions (pre-aef88b3 in this repo) tend to have
   * avg_score clustered near 3.0 because the rubric was truncating
   * mid-think; that's noise. We surface the data anyway and rely on
   * the user reading the suggestion column for richer signal.
   */
  import Sparkline from './Sparkline.svelte';

  let loading = $state(true);
  let error = $state(null);
  let sessions = $state([]);
  let expandedId = $state(null);
  let detail = $state(null);
  let detailLoading = $state(false);
  let detailError = $state(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch('/v2/reflection/sessions?limit=50');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      sessions = data.sessions || [];
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  async function loadDetail(sid) {
    detailLoading = true;
    detailError = null;
    detail = null;
    try {
      const r = await fetch(`/v2/reflection/sessions/${sid}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      detail = await r.json();
    } catch (e) {
      detailError = String(e);
    } finally {
      detailLoading = false;
    }
  }

  async function toggleRow(sid) {
    if (expandedId === sid) {
      expandedId = null;
      detail = null;
      return;
    }
    expandedId = sid;
    await loadDetail(sid);
  }

  $effect(() => { load(); });

  function shortName(projectPath) {
    if (!projectPath) return '—';
    const parts = String(projectPath).replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectPath;
  }

  function formatRelative(iso) {
    if (!iso) return '';
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return '';
    const diff = Math.max(0, (Date.now() - t) / 1000);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function fmtTokens(n) {
    if (!n) return '—';
    if (n < 1000) return `${n}`;
    if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
    return `${(n / 1_000_000).toFixed(2)}M`;
  }

  function scoreClass(score) {
    if (score == null) return 'na';
    if (score >= 4) return 'high';
    if (score >= 3) return 'mid';
    return 'low';
  }

  // Pivot the trajectory rows into per-dimension series for the chart.
  function trajectorySeries(rows) {
    if (!rows || rows.length === 0) return [];
    const byDim = new Map();
    for (const r of rows) {
      if (!byDim.has(r.dim_name)) byDim.set(r.dim_name, []);
      byDim.get(r.dim_name).push({ turn: r.turn_idx, score: Number(r.score) });
    }
    return Array.from(byDim.entries()).map(([dim, points]) => ({ dim, points }));
  }

  // Group session rows by project so the table reads as a hierarchy
  // (matches the picker's grouping). Order = project's most recent
  // session.
  let grouped = $derived.by(() => {
    const groups = new Map();
    for (const s of sessions) {
      const key = s.project_path || s.project_hash || '?';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    }
    return Array.from(groups.entries()).map(([projectPath, items]) => ({
      projectPath,
      shortName: shortName(projectPath),
      sessions: items,
    }));
  });

  let detailSeries = $derived.by(() =>
    detail?.trajectory ? trajectorySeries(detail.trajectory) : []
  );
</script>

<section class="panel">
  <header class="hd">
    <h3>past sessions</h3>
    <span class="muted">{sessions.length} session{sessions.length === 1 ? '' : 's'}</span>
    <button class="reload" onclick={load} disabled={loading} title="refresh">↻</button>
  </header>

  {#if loading && sessions.length === 0}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if sessions.length === 0}
    <div class="empty">no sessions yet</div>
  {:else}
    <div class="rows">
      {#each grouped as g (g.projectPath)}
        <div class="group-head">
          <span class="proj">{g.shortName}</span>
          <span class="proj-path" title={g.projectPath}>{g.projectPath}</span>
        </div>
        {#each g.sessions as s (s.session_id)}
          <button
            class="row"
            class:expanded={expandedId === s.session_id}
            onclick={() => toggleRow(s.session_id)}
          >
            <span class="caret">{expandedId === s.session_id ? '▾' : '▸'}</span>
            <code class="sid">{s.session_id.slice(0, 8)}</code>
            <span class="when">{formatRelative(s.last_seen_at)}</span>
            <span class="turns">{s.turns_seen ?? 0} turn{s.turns_seen === 1 ? '' : 's'}</span>
            <span class="tokens">
              {fmtTokens(s.total_input_tokens)} in · {fmtTokens(s.total_output_tokens)} out
            </span>
            <span class="score score-{scoreClass(s.avg_score)}">
              {s.avg_score != null ? `${s.avg_score.toFixed(2)} avg` : '— no rubric'}
              {#if s.sample_count}<span class="sample">·{s.sample_count}</span>{/if}
            </span>
            {#if s.has_report}<span class="badge">report</span>{/if}
          </button>

          {#if expandedId === s.session_id}
            <div class="detail">
              {#if detailLoading}
                <div class="empty inline">loading detail…</div>
              {:else if detailError}
                <div class="empty inline err">{detailError}</div>
              {:else if detail}
                <!-- Report card -->
                <div class="detail-block">
                  <h4>report card (8 modes)</h4>
                  {#if detail.report?.length}
                    <div class="modes">
                      {#each detail.report as m (m.mode_id)}
                        <div class="mode">
                          <div class="mode-head">
                            <span class="mode-name">{m.mode_name}</span>
                            <span class="mode-score score-{scoreClass(m.score)}">
                              {Number(m.score).toFixed(1)}<span class="muted">/5</span>
                            </span>
                          </div>
                          {#if m.suggestion}
                            <div class="mode-suggestion">{m.suggestion}</div>
                          {/if}
                        </div>
                      {/each}
                    </div>
                  {:else}
                    <div class="empty inline">consolidator hasn't run for this session yet (fires after 10-min idle)</div>
                  {/if}
                </div>

                <!-- Rubric trajectory -->
                <div class="detail-block">
                  <h4>rubric trajectory</h4>
                  {#if detailSeries.length}
                    <div class="series">
                      {#each detailSeries as s (s.dim)}
                        <div class="serie">
                          <span class="serie-lbl">{s.dim}</span>
                          <span class="serie-spark">
                            <Sparkline points={s.points.map((p) => p.score)} />
                          </span>
                          <span class="serie-meta">
                            n={s.points.length}
                            · last {s.points[s.points.length - 1]?.score?.toFixed?.(1) ?? '—'}
                          </span>
                        </div>
                      {/each}
                    </div>
                  {:else}
                    <div class="empty inline">no rubric samples for this session</div>
                  {/if}
                </div>
              {/if}
            </div>
          {/if}
        {/each}
      {/each}
    </div>

    <p class="footnote">
      Pre-calibration sessions may show <code>avg ≈ 3.0</code> across the board — that's the rubric
      truncating mid-think on the old budget, not a real signal. Suggestions and trajectory shape
      are richer than the raw average for those rows.
    </p>
  {/if}
</section>

<style>
  .panel {
    margin-top: 24px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
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
  .reload {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    font-size: 14px;
    padding: 0 4px;
    margin-left: auto;
  }
  .reload:hover:not(:disabled) { color: var(--text); }
  .reload:disabled { opacity: 0.4; cursor: default; }

  .empty {
    padding: 20px 4px;
    color: var(--muted);
    font-size: 12px;
  }
  .empty.inline { padding: 8px 0; }
  .empty.err { color: var(--err); font-family: var(--mono); }

  .rows { display: flex; flex-direction: column; gap: 2px; }

  .group-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 10px 4px 4px;
    margin-top: 6px;
    border-bottom: 1px solid var(--border);
  }
  .group-head:first-child { margin-top: 0; }
  .proj {
    font-weight: 600;
    color: var(--text);
    font-size: 12px;
  }
  .proj-path {
    color: var(--muted-deep);
    font-family: var(--mono);
    font-size: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .row {
    display: grid;
    grid-template-columns: 18px 80px 80px 80px 1fr 130px 60px;
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
  .sid {
    font-family: var(--mono);
    color: var(--muted);
    font-size: 11px;
  }
  .when, .turns, .tokens, .score {
    color: var(--text-soft);
    font-family: var(--mono);
    font-size: 11px;
  }
  .when, .turns, .tokens { color: var(--muted); }
  .tokens { color: var(--muted-deep); }
  .score-high { color: var(--ok); }
  .score-mid  { color: var(--accent); }
  .score-low  { color: var(--warn); }
  .score-na   { color: var(--muted-deep); }
  .sample { color: var(--muted-deep); margin-left: 4px; }
  .badge {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(95,195,167,0.10);
    color: var(--ok);
    border: 1px solid rgba(95,195,167,0.25);
    text-align: center;
  }

  .detail {
    background: var(--surface-2);
    border-radius: 6px;
    margin: 4px 18px 8px;
    padding: 14px 18px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 760px) { .detail { grid-template-columns: 1fr; } }
  .detail-block { min-width: 0; }
  .detail-block h4 {
    margin: 0 0 10px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-weight: 600;
  }

  .modes { display: flex; flex-direction: column; gap: 8px; }
  .mode {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px 10px;
  }
  .mode-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }
  .mode-name {
    font-size: 11px;
    color: var(--text-soft);
    font-weight: 500;
  }
  .mode-score {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 600;
  }
  .mode-suggestion {
    margin-top: 4px;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.4;
  }

  .series { display: flex; flex-direction: column; gap: 6px; }
  .serie {
    display: grid;
    grid-template-columns: 140px 1fr 100px;
    align-items: center;
    gap: 8px;
  }
  .serie-lbl {
    font-size: 11px;
    color: var(--text-soft);
    font-family: var(--mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .serie-spark {
    display: block;
    height: 22px;
  }
  .serie-meta {
    font-size: 10px;
    color: var(--muted);
    font-family: var(--mono);
    text-align: right;
  }

  .footnote {
    margin-top: 16px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    color: var(--muted-deep);
    font-size: 11px;
    line-height: 1.55;
  }
  code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 4px;
    border-radius: 3px;
    color: var(--muted);
  }
</style>
