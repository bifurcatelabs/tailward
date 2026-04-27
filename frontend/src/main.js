import { mount } from 'svelte';
import App from './App.svelte';

// The session and project hash arrive as data attributes on the
// mount node so the FastAPI template owns "which session is this
// for" and the bundle stays a single artifact across all sessions.
const target = document.getElementById('app');

const app = mount(App, {
  target,
  props: {
    ph: target.dataset.ph || '',
    sessionId: target.dataset.sessionId || '',
  },
});

export default app;
