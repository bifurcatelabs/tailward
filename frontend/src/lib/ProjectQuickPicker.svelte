<script>
  /**
   * Inline project picker for the Platform view header. Different
   * shape than HeaderBar's SessionPicker — this one is project-only
   * (Platform metrics aren't session-scoped) and inline rather than
   * a dropdown with rich session detail.
   *
   * Click → navigate to that project's most-recent session at the
   * Platform view (#platform hash). Uses the same /v2/projects
   * endpoint the LandingView reads.
   */
  let { ph } = $props();

  let projects = $state([]);
  let loading = $state(false);
  let open = $state(false);

  async function load() {
    if (projects.length || loading) return;
    loading = true;
    try {
      const r = await fetch('/v2/projects');
      if (r.ok) {
        const data = await r.json();
        projects = data.projects || [];
      }
    } finally {
      loading = false;
    }
  }

  async function toggle() {
    open = !open;
    if (open) await load();
  }

  function shortName(projectPath) {
    if (!projectPath) return '—';
    const parts = String(projectPath).replace(/\\/g, '/').split('/').filter(Boolean);
    return parts[parts.length - 1] || projectPath;
  }

  function urlFor(p) {
    if (!p.latest_session_id) return null;
    return `/p/${p.project_hash}/live/${p.latest_session_id}#platform`;
  }

  let activeName = $derived.by(() => {
    const match = projects.find((p) => p.project_hash === ph);
    return match ? shortName(match.project_path) : ph.slice(0, 8);
  });
</script>

<div class="picker" class:open>
  <button
    type="button"
    class="trigger"
    onclick={toggle}
    title="switch project · platform metrics aren't session-scoped, only the project hash matters"
    aria-expanded={open}
  >
    <span class="muted">project</span>
    <span class="name">{activeName}</span>
    <span class="caret" class:rot={open}>▾</span>
  </button>

  {#if open}
    <div class="panel" role="dialog" aria-label="project picker">
      {#if loading}
        <div class="empty">loading…</div>
      {:else if projects.length === 0}
        <div class="empty">no projects yet</div>
      {:else}
        {#each projects as p (p.project_hash)}
          {@const url = urlFor(p)}
          {#if url}
            <a class="row" class:active={p.project_hash === ph} href={url}>
              <span class="proj">{shortName(p.project_path)}</span>
              <span class="hash">{p.project_hash.slice(0, 8)}</span>
              <span class="meta">{p.session_count} session{p.session_count === 1 ? '' : 's'}</span>
            </a>
          {:else}
            <span class="row disabled" title="no sessions in this project yet">
              <span class="proj">{shortName(p.project_path)}</span>
              <span class="hash">{p.project_hash.slice(0, 8)}</span>
              <span class="meta">no sessions</span>
            </span>
          {/if}
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .picker { position: relative; display: inline-flex; }
  .trigger {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-soft);
    font-family: inherit;
    font-size: 12px;
    cursor: pointer;
    transition: color 120ms, border-color 120ms;
  }
  .trigger:hover { color: var(--text); border-color: var(--text-soft); }
  .picker.open .trigger { border-color: var(--accent); }
  .muted {
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 10px;
  }
  .name { color: var(--text); font-weight: 500; }
  .caret {
    transition: transform 160ms ease;
    font-size: 10px;
    color: var(--muted);
  }
  .caret.rot { transform: rotate(180deg); }

  .panel {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    width: min(360px, 70vw);
    max-height: 50vh;
    overflow: auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    z-index: 1000;
    padding: 6px;
  }
  .empty { padding: 16px; color: var(--muted); font-size: 12px; }
  .row {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 12px;
    align-items: baseline;
    padding: 8px 10px;
    border-radius: 4px;
    text-decoration: none;
    color: var(--text-soft);
    font-size: 12px;
    transition: background 120ms;
  }
  .row:hover:not(.disabled) { background: var(--surface-2); color: var(--text); }
  .row.active {
    background: rgba(232,153,104,0.08);
    border: 1px dashed rgba(232,153,104,0.30);
    padding: 7px 9px;
  }
  .row.disabled { color: var(--muted-deep); cursor: default; }
  .proj { font-weight: 500; }
  .hash, .meta {
    font-family: var(--mono);
    color: var(--muted);
    font-size: 10px;
  }
</style>
