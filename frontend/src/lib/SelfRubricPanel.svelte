<script>
  /**
   * Self-rubric panel — LLM-scored user-side dimensions.
   *
   * Surfaces what the user-side rubric_worker has scored across the
   * 4 user dimensions (intent_clarity, context_coverage,
   * verification_engagement, mode_coherence). Two-row layout:
   *
   *   1. Per-dimension averages with sample counts.
   *   2. Recent samples with evidence + suggestion — the specific
   *      feedback is much higher signal than the dimension average,
   *      especially early on when n is small.
   *
   * No data → empty state with copy explaining when scores will
   * accumulate (every N typed user turns; configured in
   * user_rubric_turn_interval).
   */
  let { ph } = $props();

  let loading = $state(true);
  let error = $state(null);
  let data = $state(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch(`/v2/reflection/${ph}/self-rubric?limit=20`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });

  function scoreClass(score) {
    if (score == null) return 'na';
    if (score >= 4) return 'high';
    if (score >= 3) return 'mid';
    return 'low';
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

  // Canonical dimension list — ensures we always render all 4
  // even when some haven't been scored yet (sample_count = 0).
  const ALL_DIMS = [
    'intent_clarity',
    'context_coverage',
    'verification_engagement',
    'mode_coherence',
  ];

  let dimRows = $derived.by(() => {
    if (!data) return [];
    const byName = new Map();
    for (const d of (data.by_dim || [])) byName.set(d.dim, d);
    return ALL_DIMS.map((name) => {
      const row = byName.get(name);
      return {
        dim: name,
        avg: row?.avg_score ?? null,
        n: row?.n ?? 0,
      };
    });
  });
</script>

<section class="panel">
  <header class="hd">
    <h3>self-rubric (LLM-scored)</h3>
    <span class="muted">user-side dimensions</span>
    <button class="reload" onclick={load} disabled={loading} title="refresh">↻</button>
  </header>

  {#if loading && !data}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if !data || (dimRows.every((r) => r.n === 0))}
    <div class="empty">
      no self-rubric scores yet. The user-rubric worker fires on a cadence of typed user turns
      (synthesized <code>/compact</code> turns excluded). Keep using the session and the panel
      will populate.
    </div>
  {:else}
    <!-- Averages row -->
    <div class="dims">
      {#each dimRows as r (r.dim)}
        <div class="dim">
          <div class="dim-name">{r.dim.replace(/_/g, ' ')}</div>
          <div class="dim-score score-{scoreClass(r.avg)}">
            {r.avg != null ? r.avg.toFixed(2) : '—'}
            <span class="muted">/5</span>
          </div>
          <div class="dim-n">n={r.n}</div>
        </div>
      {/each}
    </div>

    <!-- Recent samples -->
    {#if data.recent?.length}
      <div class="recent">
        <h4>recent feedback</h4>
        <p class="recent-help">
          Each row is one LLM call's verdict on a single dimension. The
          <em>quote</em> is verbatim from your typed turns (the model's
          evidence for the score); the <em>suggestion</em> is LLM-generated
          and may not be actionable mid-session — these are reflection
          prompts, not real-time corrections.
        </p>
        <ul>
          {#each data.recent.slice(0, 10) as s (s.id)}
            <li>
              <div class="srow">
                <span class="sdim">{s.dim_name.replace(/_/g, ' ')}</span>
                <span class="sscore score-{scoreClass(s.score)}">
                  {Number(s.score).toFixed(1)}<span class="muted">/5</span>
                </span>
                <span class="swhen">{formatRelative(s.created_at)}</span>
              </div>
              {#if s.evidence}
                <div class="sev">
                  <span class="kind-label">quote</span>
                  <span class="sev-text">"{s.evidence}"</span>
                </div>
              {/if}
              {#if s.suggestion}
                <div class="sugg">
                  <span class="kind-label">suggestion</span>
                  <span class="sugg-text">{s.suggestion}</span>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    <p class="footnote">
      4 dimensions: intent clarity (goal/scope), context coverage (paths/decisions provided),
      verification engagement (checking claims), mode coherence (behavior matches stated session_mode).
      Specific evidence + suggestions are higher signal than averages — early-stage scores will
      cluster near 3.0 by design.
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
    margin-bottom: 14px;
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
    font-size: 11px;
    font-family: var(--mono);
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
    line-height: 1.55;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }

  .dims {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 18px;
  }
  @media (max-width: 760px) { .dims { grid-template-columns: repeat(2, 1fr); } }

  .dim {
    background: var(--surface-2);
    border-radius: 6px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .dim-name {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted-deep);
  }
  .dim-score {
    font-family: var(--mono);
    font-size: 18px;
    font-weight: 600;
  }
  .dim-n {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--muted-deep);
  }
  .score-high { color: var(--ok); }
  .score-mid  { color: var(--accent); }
  .score-low  { color: var(--warn); }
  .score-na   { color: var(--muted-deep); }

  .recent h4 {
    margin: 0 0 10px;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    font-weight: 600;
  }
  .recent ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; }
  .recent li {
    background: var(--surface-2);
    border-radius: 6px;
    padding: 10px 12px;
  }
  .srow {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 4px;
  }
  .sdim {
    font-size: 11px;
    color: var(--text-soft);
    font-weight: 500;
    text-transform: lowercase;
    flex: 1;
  }
  .sscore {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 600;
  }
  .swhen {
    color: var(--muted-deep);
    font-family: var(--mono);
    font-size: 10px;
  }
  .recent-help {
    font-size: 10px;
    color: var(--muted-deep);
    line-height: 1.55;
    margin: 0 0 12px;
  }
  .recent-help em {
    color: var(--muted);
    font-style: normal;
    font-weight: 600;
  }
  .sev,
  .sugg {
    margin-top: 6px;
    display: grid;
    grid-template-columns: 70px 1fr;
    gap: 8px;
    align-items: baseline;
    font-size: 11px;
    line-height: 1.5;
    user-select: text;
    -webkit-user-select: text;
  }
  .kind-label {
    color: var(--muted-deep);
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    text-align: right;
  }
  .sev-text {
    color: var(--muted);
    font-style: italic;
  }
  .sugg-text {
    color: var(--text-soft);
  }

  .footnote {
    margin-top: 16px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    color: var(--muted-deep);
    font-size: 11px;
    line-height: 1.6;
  }
  code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 4px;
    border-radius: 3px;
    color: var(--muted);
  }
</style>
