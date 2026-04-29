<script>
  import HeroStrip from './HeroStrip.svelte';
  import SessionTimeline from './SessionTimeline.svelte';
  import Feed from './Feed.svelte';
  import Rail from './Rail.svelte';

  let { sessionId = null } = $props();
</script>

<HeroStrip />
<SessionTimeline />
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
