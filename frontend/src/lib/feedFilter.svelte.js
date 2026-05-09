// Shared multi-toggle filter state for the dashboard's event-bearing
// surfaces (Feed + SessionTimeline). Multiple groups can be active
// at once; visible events are the union — any active group's
// type-match or predicate-match passes.
//
// Empty active set = "all" view (no filter). The "all" pill is the
// inverse signal — it lights up when the set is empty and clears
// the set when clicked.

const KNOWN_KEYS = new Set([
  'user', 'assistant', 'tool', 'rubric', 'synthesis', 'audit', 'perf', 'session', 'secrets',
]);

export const FILTER_GROUPS = [
  {
    key: 'user', label: 'user',
    types: ['user_turn', 'compact_summary'],
    description: 'typed prompts you sent + synthesized /compact summaries (Claude Code injects these as user-shaped events; we flag them separately)',
  },
  {
    key: 'assistant', label: 'assistant',
    types: ['turn'],
    description: 'model turns — one entry per logical turn, coalesced from the per-block JSONL stream (thinking + text + tool_use blocks all collapse to one)',
  },
  {
    key: 'tool', label: 'tools',
    types: ['tool_call'],
    description: 'tool_use calls (Read, Edit, Bash, Glob, etc.) and their inputs',
  },
  {
    key: 'rubric', label: 'rubric',
    types: ['rubric_in_flight', 'rubric_sample', 'rubric_done'],
    description: 'LLM-judged scoring runs — in-flight indicator, per-dimension score samples, and the done marker. Includes both assistant-side and self (user-side) rubric',
  },
  {
    key: 'synthesis', label: 'synthesis',
    types: ['synthesis_captured', 'synthesis_failed', 'intent_updated'],
    description: 'session-synthesis stream — periodic + on-demand snapshots, comprehensive intent.md updates, and any failures. Covers all three triggers (periodic / on-demand / threshold) plus the failure surface',
  },
  {
    key: 'audit', label: 'audit',
    types: ['constraint_violation', 'scope_snapshot', 'scope_creep', 'drift', 'claim', 'exfiltration_alert', 'memory_edit'],
    description: 'audit signals — rule violations, scope snapshots and creep, drift detection, claim verification verdicts, secret-leak alerts, and memory-file edits (surfaced for transparency, not violations)',
  },
  {
    key: 'perf', label: 'perf',
    types: ['turn_metric'],
    description: 'per-turn inference-path metrics — TTFT, output TPS, cache hit ratio. Derived from JSONL timestamps + the usage block; no synthetic probes',
  },
  {
    key: 'session', label: 'session',
    types: ['report_progress', 'report_ready', 'session_closed'],
    description: 'session-close consolidator output — runs after 10 min idle, produces the 8-mode report card',
  },
  {
    key: 'secrets', label: 'secrets',
    predicate: (ev) => {
      const t = ev.eventType ?? ev.type;
      if (t === 'exfiltration_alert') return true;
      const sr = ev.payload?.secrets_redacted;
      return Array.isArray(sr) && sr.length > 0;
    },
    description: 'secret-pattern detections — alert chips and the source turns where the redacted secret originated',
  },
];

class FeedFilter {
  active = $state(new Set());

  toggle(key) {
    if (!KNOWN_KEYS.has(key)) return;
    // Replace the Set wholesale so Svelte 5's identity-equality
    // change detection picks up the mutation. Mutating the existing
    // set in place wouldn't trigger reactivity.
    const next = new Set(this.active);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    this.active = next;
  }

  clear() {
    this.active = new Set();
  }

  isActive(key) {
    return this.active.has(key);
  }

  empty() {
    return this.active.size === 0;
  }

  // Match an event against the currently-active groups. Empty set =
  // pass all. Accepts both Feed-shaped events (``.eventType``) and
  // arc-shaped events (``.type``). Predicate groups gracefully
  // handle missing payload — arc events match by event type alone,
  // feed events also match by payload predicate.
  matches(event) {
    if (this.active.size === 0) return true;
    for (const key of this.active) {
      const group = FILTER_GROUPS.find((g) => g.key === key);
      if (!group) continue;
      const t = event.eventType ?? event.type;
      if (group.types && group.types.includes(t)) return true;
      if (group.predicate && group.predicate(event)) return true;
    }
    return false;
  }
}

export const feedFilter = new FeedFilter();
