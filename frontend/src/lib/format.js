// Pure formatting helpers shared across components. No reactivity here.

export function humanize(n) {
  if (n == null || isNaN(n)) return '0';
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

export function humanizeBytes(n) {
  if (n == null || isNaN(n)) return '0 B';
  const abs = Math.abs(n);
  if (abs >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
  if (abs >= 1024) return (n / 1024).toFixed(1) + ' kB';
  return n + ' B';
}

export function humanizeDuration(seconds) {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '';
  if (seconds < 1) return '<1s';
  if (seconds < 60) return Math.round(seconds) + 's';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return s ? `${m}m${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${h}h${mm}m` : `${h}h`;
}

export function fmtClock(date = new Date()) {
  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function toEpochSeconds(createdAt) {
  if (createdAt == null) return null;
  if (typeof createdAt === 'number') return createdAt;
  const ms = Date.parse(createdAt);
  return isNaN(ms) ? null : ms / 1000;
}
