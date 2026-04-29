<script>
  import { live } from './lib/live.svelte.js';
  import HeaderBar from './lib/HeaderBar.svelte';
  import TopStrip from './lib/TopStrip.svelte';
  import TabNav from './lib/TabNav.svelte';
  import SessionView from './lib/SessionView.svelte';
  import ReflectionView from './lib/ReflectionView.svelte';
  import PlatformView from './lib/PlatformView.svelte';
  import LandingView from './lib/LandingView.svelte';
  import ProjectView from './lib/ProjectView.svelte';

  let { page = 'landing', ph = '', sessionId = '' } = $props();

  // Within the session page, three tabs (hash-routed).
  const TABS = ['session', 'reflection', 'platform'];

  function readHash() {
    const h = (typeof window !== 'undefined' ? window.location.hash : '')
      .replace(/^#/, '');
    return TABS.includes(h) ? h : 'session';
  }

  let view = $state(readHash());

  function setView(v) {
    if (!TABS.includes(v)) return;
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
  {#if page === 'session'}
    <HeaderBar {ph} {sessionId} />
    <TabNav {view} {setView} />
    {#if view === 'session'}
      <SessionView {sessionId} />
    {:else if view === 'reflection'}
      <ReflectionView {ph} />
    {:else if view === 'platform'}
      <PlatformView {ph} />
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
