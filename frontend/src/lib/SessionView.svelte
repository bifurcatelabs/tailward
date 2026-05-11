<script>
  import SessionTimeline from './SessionTimeline.svelte';
  import FilterStrip from './FilterStrip.svelte';
  import Feed from './Feed.svelte';

  let { sessionId = null } = $props();

  // Project hash from the URL (the session page lives at
  // ``/p/{ph}/live/{sid}``) — threaded into the arc so the synth
  // trigger inside its header has the context it needs.
  let ph = $derived.by(() => {
    if (typeof window === 'undefined') return '';
    const m = /^\/p\/([^/]+)\//.exec(window.location.pathname);
    return m ? m[1] : '';
  });
</script>

<SessionTimeline {ph} {sessionId} />
<FilterStrip />
<main class="layout">
  <Feed {sessionId} />
</main>

<style>
  .layout {
    flex: 1;
    display: block;
    padding: 16px 24px 32px;
  }
  @media (max-width: 720px) {
    .layout { padding: 12px 16px 24px; }
  }
</style>
