<script>
  import { live } from './lib/live.svelte.js';
  import HeaderBar from './lib/HeaderBar.svelte';
  import HeroStrip from './lib/HeroStrip.svelte';
  import SessionTimeline from './lib/SessionTimeline.svelte';
  import Feed from './lib/Feed.svelte';
  import Rail from './lib/Rail.svelte';

  let { ph = '', sessionId = '' } = $props();

  $effect(() => {
    if (!ph || !sessionId) return;
    live.connect(ph, sessionId);
    return () => live.disconnect();
  });
</script>

<div class="app">
  <HeaderBar {ph} {sessionId} />
  <HeroStrip />
  <SessionTimeline />
  <main class="layout">
    <Feed />
    <Rail />
  </main>
</div>

<style>
  /* ----- design tokens ------------------------------------------- */
  :global(:root) {
    /* Slightly warm dark surfaces — pure #000-leaning blacks read as
       server-room / terminal; this carries a small amount of warmth
       so the page feels lived-in. */
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

    /* Copper / warm-ember as the brand accent. Distinctive against
       the violet-saturated dev-tool palette; reserved for the brand
       mark and a small number of emphasis spots. Per-event chips
       keep their semantic colors. */
    --accent:       #e89968;
    --accent-soft:  #f5b58e;
    --accent-glow:  rgba(232,153,104,0.45);

    /* Secondary brand violet — used for assistant turns / rubric so
       that the copper doesn't get diluted carrying every meaning. */
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

  /* ----- layout -------------------------------------------------- */
  .app {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }
  .layout {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 16px;
    padding: 16px 24px 32px;
    align-items: start;
  }
  @media (max-width: 1080px) {
    .layout { grid-template-columns: 1fr; }
  }
</style>
