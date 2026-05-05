<script>
  /**
   * Cross-project session picker. Opt-in surface — collapsed by default
   * so the HeaderBar stays minimal for the "tailward is an audit overlay"
   * posture. Expanded, it shows recent sessions across all watched
   * projects so the user can jump between them.
   *
   * No live re-connect (yet) — clicking navigates to that session's
   * page URL, which boots a fresh load. Simpler than swapping the
   * `live` store mid-session and good enough for the "review prior
   * sessions" use case the picker is built for.
   */
  let { ph, sessionId } = $props();

  let open = $state(false);
  let loading = $state(false);
  let sessions = $state([]);
  let error = $state(null);

  async function load() {
    loading = true;
    error = null;
    try {
      const r = await fetch('/v2/sessions/recent?limit=30');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      sessions = data.sessions || [];
    } catch (e) {
      error = String(e);
      sessions = [];
    } finally {
      loading = false;
    }
  }

  async function toggle() {
    open = !open;
    if (open && sessions.length === 0 && !loading) {
      await load();
    }
  }

  function shortName(projectPath) {
    if (!projectPath) return '—';
    // Last path segment, regardless of separator. Falls back to the
    // full string for paths without a separator.
    const parts = String(projectPath).replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectPath;
  }

  // Group sessions by project_path, keeping group order = recency of
  // the most-recent session in that group.
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
      projectHash: items[0]?.project_hash,
      sessions: items,
    }));
  });

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

  function urlFor(s) {
    return `/p/${s.project_hash}/live/${s.session_id}`;
  }

  function isActive(s) {
    return s.session_id === sessionId && s.project_hash === ph;
  }
</script>

<div class="picker" class:open>
  <button
    type="button"
    class="trigger"
    onclick={toggle}
    title="switch session"
    aria-expanded={open}
  >
    <span>switch</span>
    <span class="caret" class:rot={open}>▾</span>
  </button>

  {#if open}
    <div class="panel" role="dialog" aria-label="session picker">
      <div class="panel-head">
        <span class="muted">recent sessions</span>
        <button type="button" class="reload" onclick={load} disabled={loading} title="refresh">
          ↻
        </button>
      </div>

      {#if loading}
        <div class="empty">loading…</div>
      {:else if error}
        <div class="empty err">{error}</div>
      {:else if grouped.length === 0}
        <div class="empty">no sessions yet</div>
      {:else}
        <div class="groups">
          {#each grouped as g (g.projectPath)}
            <div class="group">
              <div class="group-head">
                <span class="proj">{g.shortName}</span>
                <span class="proj-path" title={g.projectPath}>{g.projectPath}</span>
              </div>
              {#each g.sessions as s (s.session_id)}
                <a
                  class="row"
                  class:active={isActive(s)}
                  href={urlFor(s)}
                  title={s.session_id}
                >
                  <code class="sid">{s.session_id.slice(0, 8)}</code>
                  <span class="meta">
                    <span class="when">{formatRelative(s.last_seen_at)}</span>
                    {#if s.turns_seen != null}
                      <span class="dot">·</span>
                      <span class="turns">{s.turns_seen} turn{s.turns_seen === 1 ? '' : 's'}</span>
                    {/if}
                    {#if s.last_model}
                      <span class="dot">·</span>
                      <span class="model" title={s.last_model}>{s.last_model}</span>
                    {/if}
                  </span>
                  {#if isActive(s)}<span class="badge">active</span>{/if}
                </a>
              {/each}
            </div>
          {/each}
        </div>
      {/if}

      <div class="footnote">
        Cross-project. tailward watches every project under
        <code>~/.claude/projects/</code> by default; configure
        <code>watch_paths</code> / <code>exclude_paths</code> in
        <code>~/.tailward/config.toml</code> to scope.
      </div>
    </div>
  {/if}
</div>

<style>
  .picker {
    position: relative;
    display: inline-flex;
  }
  .trigger {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--muted);
    font-family: inherit;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    cursor: pointer;
    transition: color 120ms, border-color 120ms;
  }
  .trigger:hover { color: var(--text); border-color: var(--text-soft); }
  .picker.open .trigger { color: var(--text); border-color: var(--accent); }
  .caret {
    transition: transform 160ms ease;
    font-size: 10px;
  }
  .caret.rot { transform: rotate(180deg); }

  .panel {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    width: min(420px, 70vw);
    max-height: 60vh;
    overflow: auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    /* High enough to clear any chart/canvas that may paint with its
       own stacking context further down the page. */
    z-index: 1000;
    padding: 10px 12px;
  }
  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .reload {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    font-size: 13px;
    padding: 0 4px;
  }
  .reload:hover:not(:disabled) { color: var(--text); }
  .reload:disabled { opacity: 0.4; cursor: default; }

  .empty {
    color: var(--muted);
    font-size: 12px;
    padding: 12px 4px;
  }
  .empty.err { color: var(--err); font-family: var(--mono); }

  .groups { display: flex; flex-direction: column; gap: 12px; }
  .group { display: flex; flex-direction: column; }
  .group-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 4px 4px 6px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 4px;
  }
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
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 6px;
    border-radius: 4px;
    text-decoration: none;
    color: var(--text);
    font-size: 12px;
    transition: background 120ms;
  }
  .row:hover { background: var(--surface-2); }
  .row.active {
    background: rgba(232,153,104,0.08);
    border: 1px dashed rgba(232,153,104,0.35);
    padding: 5px 5px;
  }
  .sid {
    font-family: var(--mono);
    color: var(--muted);
    font-size: 11px;
  }
  .meta {
    display: flex;
    gap: 6px;
    align-items: baseline;
    flex: 1;
    color: var(--muted);
    font-size: 11px;
  }
  .when { color: var(--text-soft); }
  .dot { color: var(--muted-deep); }
  .model {
    font-family: var(--mono);
    color: var(--muted-deep);
    font-size: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 140px;
  }
  .badge {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 999px;
    background: rgba(232,153,104,0.10);
    color: var(--accent);
    border: 1px solid rgba(232,153,104,0.30);
  }

  .muted {
    color: var(--muted);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .footnote {
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
    color: var(--muted-deep);
    font-size: 10px;
    line-height: 1.5;
  }
  .footnote code {
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 1px 4px;
    border-radius: 3px;
    color: var(--muted);
  }
</style>
