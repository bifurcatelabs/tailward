<script>
  /**
   * Landing — what greets a user on ``/``. Lists projects warden has
   * seen + their most-recent session, with click-through to the
   * audit surface. Replaces the legacy Jinja project-index page in
   * v2.1 so the SPA owns every visible surface.
   */
  let loading = $state(true);
  let error = $state(null);
  let projects = $state([]);

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
    <p>local-first audit, reflection, and platform telemetry across every project warden is watching.</p>
    <button class="reload" onclick={load} disabled={loading} title="refresh">↻</button>
  </header>

  {#if loading && projects.length === 0}
    <div class="empty">loading…</div>
  {:else if error}
    <div class="empty err">{error}</div>
  {:else if projects.length === 0}
    <div class="empty">
      No projects yet. Warden watches every project under
      <code>~/.claude/projects/</code>; once Claude Code writes a
      transcript for a project, it'll appear here.
    </div>
  {:else}
    <div class="grid">
      {#each projects as p (p.project_hash)}
        <a class="card" href={urlFor(p)} title={p.project_path}>
          <div class="card-head">
            <span class="proj">{shortName(p.project_path)}</span>
            {#if p.session_mode}
              <span class="mode">{p.session_mode}</span>
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
