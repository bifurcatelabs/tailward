<script>
  import { live } from './lib/live.svelte.js';
  import HeaderBar from './lib/HeaderBar.svelte';
  import TopStrip from './lib/TopStrip.svelte';
  import TabNav from './lib/TabNav.svelte';
  import TitleBar from './lib/TitleBar.svelte';
  import SessionView from './lib/SessionView.svelte';
  import ReflectionView from './lib/ReflectionView.svelte';
  import PlatformView from './lib/PlatformView.svelte';
  import SynthesisView from './lib/SynthesisView.svelte';
  import SettingsView from './lib/SettingsView.svelte';
  import LandingView from './lib/LandingView.svelte';
  import ProjectView from './lib/ProjectView.svelte';

  let { page = 'landing', ph = '', sessionId = '' } = $props();

  // Within the session page, five tabs (hash-routed).
  const TABS = ['session', 'reflection', 'platform', 'synthesis', 'settings'];

  // Spawned-window detection. Review surfaces (reflection / platform /
  // settings) can open in their own Tauri window with ?spawned=1 in the
  // URL. In that mode the SPA hides TabNav so the window is dedicated
  // to one view — the sidebar window owns navigation.
  const params = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search)
    : new URLSearchParams();
  const spawned = params.get('spawned') === '1';

  // Tauri context detection. In Tauri (and not already in a spawned
  // window), review-tab clicks call the open_review_window command to
  // spawn a separate window instead of switching the current view.
  // In a regular browser, the existing hash-route behavior is preserved.
  const isTauri = typeof window !== 'undefined'
    && window.__TAURI_INTERNALS__ !== undefined;

  function readHash() {
    const h = (typeof window !== 'undefined' ? window.location.hash : '')
      .replace(/^#/, '');
    return TABS.includes(h) ? h : 'session';
  }

  let view = $state(readHash());

  async function setView(v) {
    if (!TABS.includes(v)) return;

    // Main window in Tauri: clicking a review tab spawns a separate
    // window instead of switching view in-place. Session tab stays in
    // the main window. Spawned review windows fall through to the
    // in-place switch (they don't re-spawn from themselves; not that
    // they show TabNav anyway).
    if (isTauri && !spawned && page === 'session' && v !== 'session') {
      try {
        await window.__TAURI_INTERNALS__.invoke('open_review_window', {
          view: v,
          ph,
          sessionId,
        });
        return;
      } catch (e) {
        // Graceful degradation: log + fall through to in-place switch.
        console.error('[tailward] open_review_window failed:', e);
      }
    }

    view = v;
    if (typeof window !== 'undefined' && window.location.hash !== '#' + v) {
      history.replaceState(null, '', '#' + v);
    }
  }

  $effect(() => {
    if (page !== 'session') return;
    const onHash = () => { view = readHash(); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  });

  // Live store only connects on the session page. Landing + project
  // pages don't talk to the per-session bus.
  $effect(() => {
    if (page !== 'session' || !ph || !sessionId) return;
    live.connect(ph, sessionId);
    return () => live.disconnect();
  });
</script>

<div class="app">
  {#if isTauri}
    <TitleBar />
  {/if}
  {#if page === 'session'}
    <HeaderBar {ph} {sessionId} />
    {#if !spawned}
      <TabNav {view} {setView} />
    {/if}
    {#if view === 'session'}
      <SessionView {sessionId} />
    {:else if view === 'reflection'}
      <ReflectionView {ph} />
    {:else if view === 'platform'}
      <PlatformView {ph} />
    {:else if view === 'synthesis'}
      <SynthesisView {ph} {sessionId} />
    {:else if view === 'settings'}
      <SettingsView />
    {/if}
  {:else if page === 'project'}
    <TopStrip />
    <ProjectView {ph} />
  {:else}
    <TopStrip />
    <LandingView />
  {/if}
</div>

<style>
  /* ----- design tokens ------------------------------------------- */
  :global(:root) {
    --bg:           #0b0c10;
    --surface:      #11141a;
    --surface-2:    #161a23;
    --surface-3:    #1c2230;
    --border:       #1d2330;
    --border-strong:#2a3041;

    --text:         #ecedf2;
    --text-soft:    #c0c5d1;
    --muted:        #7a8290;
    --muted-deep:   #4d5462;

    --accent:       #e89968;
    --accent-soft:  #f5b58e;
    --accent-glow:  rgba(232,153,104,0.45);

    --violet:       #9690f8;
    --violet-soft:  #b3aeff;

    --ok:           #5fc3a7;
    --warn:         #e6c054;
    --err:          #e87a7a;

    --sans: "Inter", "InterVariable", ui-sans-serif, system-ui,
            -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
            Arial, sans-serif;
    --mono: "JetBrains Mono", "JetBrainsMonoVariable", ui-monospace,
            SFMono-Regular, Menlo, Consolas, "Liberation Mono",
            monospace;
  }

  :global(*) { box-sizing: border-box; }

  :global(body) {
    margin: 0;
    background:
      radial-gradient(1200px 600px at 15% -10%,
        rgba(232,153,104,0.06) 0%,
        transparent 60%),
      radial-gradient(1000px 500px at 85% 110%,
        rgba(150,144,248,0.05) 0%,
        transparent 60%),
      var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
    min-height: 100vh;
  }

  :global(button) { font-family: inherit; }

  :global(::-webkit-scrollbar) { width: 8px; height: 8px; }
  :global(::-webkit-scrollbar-track) { background: transparent; }
  :global(::-webkit-scrollbar-thumb) {
    background: var(--border-strong);
    border-radius: 4px;
  }
  :global(::-webkit-scrollbar-thumb:hover) { background: var(--muted-deep); }

  .app {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }
</style>
