<script>
  import { live } from './lib/live.svelte.js';
  import HeaderBar from './lib/HeaderBar.svelte';
  import StatStrip from './lib/StatStrip.svelte';
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
  <StatStrip />
  <main class="layout">
    <Feed />
    <Rail />
  </main>
</div>

<style>
  /* ----- design tokens ------------------------------------------- */
  :global(:root) {
    --bg:           #0a0c10;
    --surface:      #11141a;
    --surface-2:    #161a23;
    --surface-3:    #1c2230;
    --border:       #1f2531;
    --border-strong:#2c3342;
    --text:         #e6e9ef;
    --text-soft:    #b9c0cc;
    --muted:        #7d8693;
    --muted-deep:   #4d5562;
    --accent:       #8b7ff5;
    --accent-soft:  #a89dff;
    --ok:           #5ec5b3;
    --warn:         #e6a554;
    --err:          #e87a7a;

    --sans: "Inter", ui-sans-serif, system-ui, -apple-system,
            "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --mono: "JetBrains Mono", ui-monospace, SFMono-Regular,
            Menlo, Consolas, "Liberation Mono", monospace;
  }

  :global(*) { box-sizing: border-box; }

  :global(body) {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }

  :global(button) {
    font-family: inherit;
  }

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
    .layout {
      grid-template-columns: 1fr;
    }
  }
</style>
