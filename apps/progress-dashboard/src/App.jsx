import { useEffect, useRef, useState } from 'react';
import { ArrowClockwise, CaretRight, Circle, CircleNotch, WarningCircle } from '@phosphor-icons/react';
import { createRefreshController, fetchEvidence, formatRecordAge, safeExternalUrl, summarizeSnapshot } from './dashboard.js';

function TaskDetails({ task }) {
  const [sources, setSources] = useState({});
  async function toggle(item) {
    if (sources[item.id]?.visible) {
      setSources(old => ({ ...old, [item.id]: { ...old[item.id], visible: false } }));
      return;
    }
    if (sources[item.id]?.text) {
      setSources(old => ({ ...old, [item.id]: { ...old[item.id], visible: true } }));
      return;
    }
    setSources(old => ({ ...old, [item.id]: { loading: true } }));
    try {
      const text = await fetchEvidence(item.id, { signal: AbortSignal.timeout(10000) });
      setSources(old => ({ ...old, [item.id]: { text, visible: true } }));
    } catch {
      setSources(old => ({ ...old, [item.id]: { error: 'This source is unavailable or is too large to display.' } }));
    }
  }
  return <div className="task-details">
    <dl><div><dt>Recorded state</dt><dd>{task.status_label}</dd></div><div><dt>Record updated</dt><dd>{task.updated_at ? new Date(task.updated_at).toLocaleString('en-US') : 'Unknown'}</dd></div></dl>
    <p className="source-id">Source ID: {task.id}</p>
    {task.original_title !== task.title && <details className="original"><summary>Original title</summary><p>{task.original_title}</p></details>}
    <h3>Evidence</h3>
    {task.evidence?.length ? task.evidence.map(item => <div className="evidence-row" key={item.id}>
      <span>{item.label}</span>
      {item.availability !== 'available' ? <small>Unavailable in this view</small> : safeExternalUrl(item.url)
        ? <a href={safeExternalUrl(item.url)} target="_blank" rel="noopener noreferrer">Open source</a>
        : <button className="text-button" disabled={sources[item.id]?.loading} onClick={() => toggle(item)}>
          {sources[item.id]?.loading ? 'Reading…' : sources[item.id]?.visible ? 'Hide source' : 'Read source'}
        </button>}
      {sources[item.id]?.error && <p className="warning-text evidence-text">{sources[item.id].error}</p>}
      {sources[item.id]?.visible && <pre className="evidence-text">{sources[item.id].text}</pre>}
    </div>) : <p>No evidence linked yet.</p>}
  </div>;
}

function TaskRow({ task }) {
  const [open, setOpen] = useState(false);
  const age = formatRecordAge(task.updated_at);
  return <details className="task-row" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <span className="task-copy"><strong>{task.title}</strong>
        <span className={`status ${task.category === 'attention' ? 'warning-text' : ''}`}>{task.status_label}</span>
        {task.next_action && <span className="next-action">Next: {task.next_action}</span>}
      </span>
      <span className="task-meta"><span className={['old','unknown','future'].includes(task.freshness) ? 'old-record' : ''}>{age}{task.freshness === 'old' ? ' · Old record' : ''}</span>
        <span className="view-details">{open ? 'Hide details' : 'View details'} <CaretRight size={18} aria-hidden="true" /></span>
      </span>
    </summary>
    {open && <TaskDetails task={task} />}
  </details>;
}

function RecordList({ tasks, initial = 5, empty = 'No records yet.' }) {
  const [limit, setLimit] = useState(initial);
  return <>{tasks.length ? tasks.slice(0, limit).map(task => <TaskRow key={task.id} task={task} />) : <p className="empty">{empty}</p>}
    {tasks.length > limit && <button className="text-button show-more" onClick={() => setLimit(limit + initial)}>Show more records <CaretRight size={16} aria-hidden="true" /></button>}</>;
}

export function App() {
  const [state, setState] = useState({ snapshot: null, loading: true, error: '' });
  const [now, setNow] = useState(Date.now());
  const [query, setQuery] = useState('');
  const loader = useRef(null);
  useEffect(() => {
    const current = createRefreshController({ onChange: next => { setState(next); setNow(Date.now()); } });
    loader.current = current;
    current.refresh();
    const interval = setInterval(() => { if (document.visibilityState === 'visible') current.refresh(); }, 60000);
    const clock = setInterval(() => setNow(Date.now()), 30000);
    const wake = () => { setNow(Date.now()); if (document.visibilityState === 'visible') current.refresh(); };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('focus', wake);
    return () => { clearInterval(interval); clearInterval(clock); current.dispose(); document.removeEventListener('visibilitychange', wake); window.removeEventListener('focus', wake); };
  }, []);
  const { snapshot, loading, error } = state;
  const view = summarizeSnapshot(snapshot, now);
  const stale = Boolean(error || snapshot?.stale);
  const known = view.decisionsKnown && !error;
  const checked = snapshot?.generated_at ? formatRecordAge(snapshot.generated_at, now).replace('Updated ', 'Checked ') : 'Not checked yet';
  const warnings = [...new Set([error, ...(snapshot?.warnings || [])].filter(Boolean))];
  const filtered = view.all.filter(t => `${t.title} ${t.status_label}`.toLowerCase().includes(query.toLowerCase()));
  return <main className="shell">
    <header><div><h1>Work signals</h1><p className="subtitle">What needs your attention, right now.</p></div>
      <div className="actions"><button className="refresh" disabled={loading} onClick={() => loader.current?.refresh()}>
        <ArrowClockwise size={22} aria-hidden="true" /> {loading ? 'Refreshing…' : 'Refresh'}</button>
        <p className={`checked ${stale ? 'warning-text' : ''}`}>
          {stale ? <WarningCircle size={16} aria-hidden="true" /> : <Circle size={12} weight="fill" aria-hidden="true" />}
          {checked}{stale && ' · Saved view'}
        </p>
      </div>
    </header>
    {warnings.length > 0 && <div className="source-warning" role="status">{warnings.map(w => <p key={w}>{w}</p>)}</div>}
    {loading && !snapshot ? <p className="loading-state">Reading task records… <CircleNotch size={18} className="spin" aria-hidden="true" /></p>
      : !snapshot ? <p className="loading-state">Current work and decisions are unknown until records can be read.</p> : <>
        {view.attention.length > 0 ? <section className="attention" aria-label="Needs your attention"><h2>Needs your attention</h2><RecordList tasks={view.attention} initial={3} /></section>
          : <p className="decisions">{known ? 'No decisions waiting.' : 'Current decisions are unknown; the records could not be refreshed.'}</p>}
        <section aria-label="Active work"><h2>Active work</h2><RecordList tasks={view.active} initial={3}
          empty={known ? 'No recently updated active work. Older records are below.' : 'Current activity is unknown. Any saved records remain below.'} /></section>
        <details className="disclosure"><summary><CaretRight size={18} aria-hidden="true" /> Recent results</summary>
          <div className="disclosure-body"><RecordList tasks={view.recent} empty="No verified results recorded yet." /></div></details>
        <details className="disclosure"><summary><CaretRight size={18} aria-hidden="true" /> All records &amp; sources</summary>
          <div className="disclosure-body"><div className="sources"><h3>Sources</h3>
            <p>These are saved records, not a live check of running agents.</p>
            {snapshot.sources.map(source => <p key={source.name}><strong>{({tasks:'Task records',service:'Background records',vault:'Vault evidence'})[source.name] || source.name}</strong>: {source.state}. Last record: {formatRecordAge(source.last_record_at, now).replace('Updated ', '')}.</p>)}
          </div><label className="search">Find a record<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search titles or states" /></label>
            <RecordList key={query} tasks={filtered} initial={20} empty="No matching records." />
          </div></details>
      </>}
    <footer>Task records and Vault evidence. Older records stay marked.</footer>
  </main>;
}
