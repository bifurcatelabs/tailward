<script>
  import HeroStrip from './HeroStrip.svelte';
  import SessionTimeline from './SessionTimeline.svelte';
  import SnapshotsPanel from './SnapshotsPanel.svelte';
  import Feed from './Feed.svelte';
  import Rail from './Rail.svelte';

  let { sessionId = null } = $props();

  // The synthesis panel needs the project hash. Pull it off the URL
  // (the session page lives at ``/p/{ph}/live/{sid}``) so the parent
  // doesn't have to thread it down explicitly.
  let ph = $derived.by(() => {
    if (typeof window === 'undefined') return '';
    const m = /^\/p\/([^/]+)\//.exec(window.location.pathname);
    return m ? m[1] : '';
  });
</script>

<HeroStrip />
<SessionTimeline />
{#if ph && sessionId}
  <SnapshotsPanel {ph} {sessionId} />
{/if}
<main class="layout">
  <Feed {sessionId} />
  <Rail />
</main>

<style>
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
