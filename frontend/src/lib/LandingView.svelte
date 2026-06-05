<script>
  /**
   * Landing — what greets a user on ``/``. Lists projects tailward has
   * seen + their most-recent session, with click-through to the
   * audit surface. Replaces the legacy Jinja project-index page in
   * v2.1 so the SPA owns every visible surface.
   */
  let loading = $state(true);
  let error = $state(null);
  let projects = $state([]);
  // project_hash -> 'idle' | 'seeding' | 'error'
  let seedState = $state({});

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch('/v2/projects');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      projects = data.projects || [];
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });

  /**
   * Seed a project — opt it into deep-parse on startup. Backend
   * marks the project seeded in the ``seeded_projects`` table and
   * parses its existing JSONL content (with ``is_backlog=True`` so
   * LLM workers skip per the load-respecting design). After
   * completion, refresh the project list so the indicator reflects
   * the new state.
   */
  async function seedProject(event, projectHash) {
    event.preventDefault();
    event.stopPropagation();
    seedState = { ...seedState, [projectHash]: 'seeding' };
    try {
      const r = await fetch(`/api/projects/${projectHash}/seed`, {
        method: 'POST',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      seedState = { ...seedState, [projectHash]: 'idle' };
      await load();
    } catch (e) {
      seedState = { ...seedState, [projectHash]: 'error' };
    }
  }

  function shortName(projectPath) {
    if (!projectPath) return '—';
    const parts = String(projectPath).replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectPath;
  }

  function formatRelative(iso) {
    if (!iso) return 'never';
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return '';
    const diff = Math.max(0, (Date.now() - t) / 1000);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }

  function urlFor(p) {
    if (p.latest_session_id) {
      return `/p/${p.project_hash}/live/${p.latest_session_id}`;
    }
    return `/p/${p.project_hash}`;
  }
</script>

<section class="view">
  <header class="hd">
    <h2>projects</h2>
    <p>local-first audit, reflection, and platform telemetry across every project tailward is watching.</p>
    <button class="reload" onclick={load} disabled={loading} title="refresh">↻</button>
  </header>

  {#if loading && projects.length === 0}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if projects.length === 0}
    <div class="empty">
      No projects yet. tailward watches every project under
      <code>~/.claude/projects/</code>; once Claude Code writes a
      transcript for a project, it'll appear here.
    </div>
  {:else}
    <div class="grid">
      {#each projects as p (p.project_hash)}
        <a class="card" href={urlFor(p)} title={p.project_path}>
          <div class="card-head">
            <span class="proj">{shortName(p.project_path)}</span>
            {#if p.box}
              <span class="box" title="remote box: {p.box}">⇄ {p.box}</span>
            {/if}
            {#if p.session_mode}
              <span class="mode">{p.session_mode}</span>
            {/if}
            {#if p.seeded}
              <span class="seeded" title="opted into deep-parse on daemon startup">seeded</span>
            {:else}
              <button
                type="button"
                class="seed-btn"
                onclick={(e) => seedProject(e, p.project_hash)}
                disabled={seedState[p.project_hash] === 'seeding'}
                title="parse this project's historical session content (LLM workers skip on backlog)"
              >
                {seedState[p.project_hash] === 'seeding' ? 'seeding…' :
                 seedState[p.project_hash] === 'error' ? 'seed failed' :
                 'seed'}
              </button>
            {/if}
          </div>
          <div class="path">{p.project_path}</div>
          <div class="meta">
            <span>{p.session_count} session{p.session_count === 1 ? '' : 's'}</span>
            <span class="dot">·</span>
            <span>last active {formatRelative(p.last_active_at)}</span>
            {#if p.intent_exists}
              <span class="dot">·</span>
              <span class="rules">rules</span>
            {/if}
          </div>
        </a>
      {/each}
    </div>
  {/if}
</section>

<style>
  .view {
    padding: 32px 24px;
    max-width: 1100px;
    margin: 0 auto;
  }
  .hd {
    display: flex;
    align-items: baseline;
    gap: 16px;
    margin-bottom: 28px;
  }
  .hd h2 {
    margin: 0;
    font-size: 22px;
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

  .empty {
    padding: 48px 12px;
    color: var(--muted);
    text-align: center;
    font-size: 13px;
    line-height: 1.6;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }
  .empty code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 5px;
    border-radius: 3px;
    color: var(--text-soft);
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 14px;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 18px 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    text-decoration: none;
    color: inherit;
    transition: border-color 120ms, transform 120ms, box-shadow 120ms;
  }
  .card:hover {
    border-color: rgba(232,153,104,0.40);
    transform: translateY(-1px);
    box-shadow: 0 4px 18px rgba(0,0,0,0.30);
  }
  .card-head {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }
  .proj {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
  }
  .mode {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(232,153,104,0.10);
    color: var(--accent);
    border: 1px solid rgba(232,153,104,0.30);
  }
  .box {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(120,160,210,0.10);
    color: #7ba0d2;
    border: 1px solid rgba(120,160,210,0.30);
  }
  .seeded {
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(123,180,140,0.10);
    color: var(--ok, #7bb48c);
    border: 1px solid rgba(123,180,140,0.30);
    margin-left: auto;
  }
  .seed-btn {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    font-family: var(--mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 3px 9px;
    border-radius: 999px;
    cursor: pointer;
    transition: border-color 120ms, color 120ms;
  }
  .seed-btn:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }
  .seed-btn:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .path {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--muted-deep);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px;
    margin-top: 6px;
    font-size: 11px;
    color: var(--muted);
    font-family: var(--mono);
  }
  .dot { color: var(--muted-deep); }
  .rules {
    color: var(--ok);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 9px;
  }
</style>
