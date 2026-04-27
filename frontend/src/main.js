import { mount } from 'svelte';
import App from './App.svelte';

// Parse the URL pathname into a page kind + props. The same SPA
// bundle serves three pages — ``/``, ``/p/<ph>``, and
// ``/p/<ph>/live/<sid>`` — distinguished by the path the daemon
// served the shell from. The data-* attributes on the mount node
// are kept as a fallback for the session page so existing routing
// (which sets data-ph / data-session-id) continues to work without
// requiring the route handlers to re-encode the URL.
const target = document.getElementById('app');

function parseRoute() {
  const path = (typeof window !== 'undefined' ? window.location.pathname : '') || '/';
  const session = path.match(/^\/p\/([^/]+)\/live\/([^/]+)\/?$/);
  if (session) {
    return { page: 'session', ph: session[1], sessionId: session[2] };
  }
  const project = path.match(/^\/p\/([^/]+)\/?$/);
  if (project) {
    return { page: 'project', ph: project[1], sessionId: '' };
  }
  return { page: 'landing', ph: '', sessionId: '' };
}

const route = parseRoute();

const app = mount(App, {
  target,
  props: {
    page: route.page,
    ph: target.dataset.ph || route.ph,
    sessionId: target.dataset.sessionId || route.sessionId,
  },
});

export default app;
