export function recordFreshness(value, now = Date.now()) {
  const timestamp = Date.parse(value || '');
  if (!Number.isFinite(timestamp)) return 'unknown';
  if (timestamp > now) return 'future';
  return now - timestamp <= 86400000 ? 'fresh' : 'old';
}

export function filterActiveTasks(tasks = [], limit = 3, now = Date.now()) {
  return tasks
    .filter((task) => task.category === 'active')
    .filter((task) => task.freshness === 'fresh')
    .filter((task) => recordFreshness(task.updated_at, now) === 'fresh')
    .sort((a, b) => Date.parse(b.updated_at || '') - Date.parse(a.updated_at || ''))
    .slice(0, limit);
}

export function formatRecordAge(value, now = Date.now()) {
  const timestamp = Date.parse(value || '');
  if (!Number.isFinite(timestamp)) return 'Age unknown';
  const minutes = Math.floor((now - timestamp) / 60000);
  if (minutes < 0) return 'Recorded in the future';
  if (minutes < 1) return 'Updated just now';
  if (minutes < 60) return `Updated ${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Updated ${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.floor(hours / 24);
  return `Updated ${days} day${days === 1 ? '' : 's'} ago`;
}

export function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export async function fetchStatus({ signal } = {}) {
  const token = document.querySelector('meta[name="dashboard-token"]')?.content;
  const response = await fetch('/api/status', { signal, headers: token ? { 'X-Dashboard-Token': token } : {} });
  if (!response.ok) throw new Error(`Status request failed (${response.status})`);
  return response.json();
}

export async function fetchEvidence(id, { signal } = {}) {
  const token = document.querySelector('meta[name="dashboard-token"]')?.content;
  const response = await fetch(`/api/evidence/${encodeURIComponent(id)}`, { signal, headers: token ? { 'X-Dashboard-Token': token } : {} });
  if (!response.ok) throw new Error(`Evidence request failed (${response.status})`);
  return response.text();
}

export function summarizeSnapshot(snapshot, now = Date.now()) {
  const tasks = Array.isArray(snapshot?.tasks) ? snapshot.tasks.map(task => ({
    ...task, freshness: recordFreshness(task.updated_at, now),
  })) : [];
  return {
    attention: tasks.filter(t => t.category === 'attention'),
    active: filterActiveTasks(tasks, Infinity, now),
    recent: tasks.filter(t => t.category === 'recent'),
    all: tasks,
    decisionsKnown: !snapshot?.stale && snapshot?.sources?.some(s => s.name === 'tasks' && s.state === 'available'),
  };
}

export function createRefreshController({ fetcher = fetchStatus, timeoutMs = 10000, onChange = () => {} } = {}) {
  let state = { snapshot: null, loading: false, error: '' };
  let pending = null;
  let disposed = false;
  let controller;
  function emit() { if (!disposed) onChange({ ...state }); }
  return {
    refresh() {
      if (disposed) return Promise.resolve();
      if (pending) return pending;
      state = { ...state, loading: true };
      emit();
      controller = new AbortController();
      let timer;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => { controller.abort(); reject(new Error('Refresh timed out')); }, timeoutMs);
      });
      pending = (async () => {
        try {
          const data = await Promise.race([fetcher({ signal: controller.signal }), timeout]);
          if (!data || !Array.isArray(data.tasks) || !Array.isArray(data.sources)) throw new Error('Invalid snapshot');
          state = { snapshot: data, loading: false, error: '' };
        } catch {
          state = { ...state, loading: false, error: state.snapshot
            ? 'Refresh failed. The last saved view is shown.' : 'Could not read the task records. Try Refresh.' };
        } finally {
          clearTimeout(timer);
          pending = null;
          emit();
        }
      })();
      return pending;
    },
    dispose() { disposed = true; controller?.abort(); },
  };
}
