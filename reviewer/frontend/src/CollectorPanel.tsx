import React, { useEffect, useState } from 'react';

type Run = { id: string; kind: string; observed_status: string; started: string; requests?: number; processed?: number; delivered?: number; new_candidates?: number; error?: string; warnings?: string[] };
type Status = { paused: boolean; active: boolean; repository_count: number; pending_work: number; github_retry_at: number; candidate_counts: Record<string, number>; validation_counts: Record<string, number>; handoff_counts: Record<string, number>; runs: Run[]; storage: { used_bytes: number; limit_bytes: number; objects_bytes: number; validation_bytes: number; other_bytes: number; incomplete: boolean; sampled_at: string } };
type Candidate = { id: string; repository: string; path: string; revision: string; blob_sha: string; source_sha256?: string; status: string; size?: number; evidence_status?: string; validation_status?: string; handoff_status: string; reviewer_build_id?: string; retry_at: number; errors: Record<string, string>; evidence: { path: string; sha256: string; size: number }[]; suggestions: { field: string; value: string; origin: string }[]; actions: string[] };
type Page = { items: Candidate[]; total: number; limit: number; offset: number };
type Attempt = { id?: number | string; status: string; started?: string; finished?: string; error?: string | { message: string } };
type History = { collection: Attempt[]; validation: Attempt[]; handoff: { status?: string; attempts?: number; error?: string } };
const endpoint = '/api/discovery/collector';
const labels: Record<string, string> = { dismiss: 'Dismiss candidate', restore: 'Restore candidate', retry: 'Retry collection', 'retry-validation': 'Retry validation', 'retry-handoff': 'Retry delivery' };
const text = (value: string) => value.replaceAll('_', ' ').replaceAll('-', ' ');
function bytes(value: number) { return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GiB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MiB` : value >= 1024 ? `${(value / 1024).toFixed(1)} KiB` : `${value} bytes`; }
async function request<T>(path: string, payload?: unknown): Promise<T> {
  const response = await fetch(endpoint + path, payload === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Collector request failed');
  return result;
}
function Counts({ title, counts }: { title: string; counts: Record<string, number> }) {
  return <section><h3>{title}</h3>{Object.entries(counts).length ? <ul>{Object.entries(counts).map(([state, count]) => <li key={state}><span>{text(state)}</span><strong>{count}</strong></li>)}</ul> : <p>No candidates yet.</p>}</section>;
}
function AttemptList({ title, attempts }: { title: string; attempts: Attempt[] }) {
  return <><h4>{title}</h4>{attempts.length ? <ul>{attempts.map((attempt, i) => <li key={i}>{text(attempt.status)}{attempt.started && ` · ${new Date(attempt.started).toLocaleString()}`}{attempt.error && <p>{typeof attempt.error === 'string' ? attempt.error : attempt.error.message}</p>}</li>)}</ul> : <p>No attempts recorded.</p>}</>;
}

export function CollectorPanel({ reviewerBuildIds, onOpenReview }: { reviewerBuildIds: string[]; onOpenReview: (id: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null), [page, setPage] = useState<Page | null>(null);
  const [query, setQuery] = useState(''), [filter, setFilter] = useState(''), [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [histories, setHistories] = useState<Record<string, History>>({});
  const [historyId, setHistoryId] = useState<string | null>(null);
  const parameters = new URLSearchParams({ query, status: filter, limit: '25', offset: String(offset) });
  async function load() {
    const [state, candidates] = await Promise.all([request<Status>(''), request<Page>('/candidates?' + parameters.toString())]);
    setStatus(state); setPage(candidates); setError('');
    if (offset > 0 && offset >= candidates.total) setOffset(Math.floor(Math.max(0, candidates.total - 1) / 25) * 25);
  }
  useEffect(() => {
    let live = true;
    async function update() {
      try {
        const [state, candidates] = await Promise.all([request<Status>(''), request<Page>('/candidates?' + parameters.toString())]);
        if (!live) return;
        setStatus(state); setPage(candidates); setError('');
        if (offset > 0 && offset >= candidates.total) setOffset(Math.floor(Math.max(0, candidates.total - 1) / 25) * 25);
      } catch (reason) { if (live) setError(reason instanceof Error ? reason.message : String(reason)); }
    }
    const initial = setTimeout(() => void update(), 200);
    const timer = setInterval(() => void update(), 4000);
    return () => { live = false; clearTimeout(initial); clearInterval(timer); };
  }, [query, filter, offset]);
  async function act(path: string, payload: unknown, confirmation: string) {
    setBusy(true); setNotice('');
    try { await request(path, payload); await load(); setHistoryId(null); setNotice(confirmation); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  async function history(id: string) {
    if (historyId === id) { setHistoryId(null); return; }
    try { const result = await request<History>(`/candidates/${id}/history`); setHistories(current => ({ ...current, [id]: result })); setHistoryId(id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }
  return <section className="collector-panel" aria-label="Collector controls">
    <div className="title-row"><h2>Local collector</h2><span role="status">{status ? status.paused ? status.active ? 'Pause requested · worker stopping' : 'Paused' : status.active ? 'Worker active' : 'Ready for the next run' : 'Loading collector state…'}</span><div className="actions"><button disabled={busy} onClick={() => load().catch(reason => setError(String(reason)))}>Refresh collector</button><button disabled={busy || !status} onClick={() => void act('/pause', { paused: !status!.paused }, status!.paused ? 'Resumed for the next run' : 'Pause requested; current work will stop safely')}>{status?.paused ? 'Resume collector' : 'Pause collector'}</button></div></div>
    <p className="muted">Collector controls affect staged candidates. Human review decisions and dataset membership stay independent. Resume enables the next collector run.</p>
    <div aria-live="polite">{error && <p role="alert" className="error">{error}</p>}{notice && <p className="success">{notice}</p>}</div>
    {status && <><div className="collector-summary"><p><strong>{status.repository_count}</strong> repositories · <strong>{status.pending_work}</strong> discovery tasks waiting</p>{status.active && <p>Candidate changes become available when the worker releases its run lock.</p>}{status.github_retry_at > Date.now() / 1000 && <p>GitHub access deferred until {new Date(status.github_retry_at * 1000).toLocaleString()}. Candidate retries retain this server limit.</p>}</div>
      <div className="collector-counts"><Counts title="Collection" counts={status.candidate_counts}/><Counts title="Validation" counts={status.validation_counts}/><Counts title="Delivery" counts={status.handoff_counts}/></div>
      <section aria-label="Staging storage" className="collector-storage"><h3>Staging storage</h3><label>{bytes(status.storage.used_bytes)} used of {bytes(status.storage.limit_bytes)}<progress aria-label="Staging storage usage" value={Math.min(status.storage.used_bytes, status.storage.limit_bytes)} max={status.storage.limit_bytes}/></label><p>Originals & evidence: {bytes(status.storage.objects_bytes)} · Validation: {bytes(status.storage.validation_bytes)} · Other staging: {bytes(status.storage.other_bytes)}</p>{status.storage.used_bytes >= status.storage.limit_bytes && <p className="error">Storage budget reached. Existing files are retained.</p>}{status.storage.incomplete && <p>Some files changed during sampling; usage is approximate.</p>}<small>Sampled {new Date(status.storage.sampled_at).toLocaleString()}</small></section>
      <details className="collector-runs"><summary>Recent runs ({status.runs.length})</summary>{status.runs.length ? status.runs.map(run => <section key={run.id}><strong>{text(run.kind)} · {text(run.observed_status)}</strong><p>{new Date(run.started).toLocaleString()} · Requests: {run.requests ?? '—'} · Processed / discovered: {run.processed ?? run.delivered ?? run.new_candidates ?? '—'}</p>{run.error && <p>{run.error}</p>}{run.warnings?.map((warning, i) => <p key={i}>{warning}</p>)}</section>) : <p>No runs recorded yet.</p>}</details>
    </>}
    <h3>Staged candidates</h3><div className="collector-filters"><label>Search candidates<input value={query} maxLength={200} onChange={event => { setQuery(event.target.value); setOffset(0); }} placeholder="Repository or source path"/></label><label>Collection state<select value={filter} onChange={event => { setFilter(event.target.value); setOffset(0); }}><option value="">All states</option>{['discovered', 'collecting', 'downloaded', 'held', 'failed', 'deferred', 'unsupported', 'lfs_pointer', 'dismissed'].map(state => <option value={state} key={state}>{text(state)}</option>)}</select></label></div>
    {page && <><p>{page.total ? `${page.offset + 1}–${page.offset + page.items.length} of ${page.total} matching candidates` : 'No candidates match. Discovery will add candidates here.'}</p>{page.items.map(candidate => <article className="collector-candidate" key={candidate.id}>
      <div className="title-row"><h4>{candidate.path}</h4><span>{text(candidate.status)} · validation {text(candidate.validation_status || 'not_validated')} · delivery {text(candidate.handoff_status)}</span></div><p>{candidate.repository}{candidate.size != null && ` · ${bytes(candidate.size)}`}</p>
      {Object.entries(candidate.errors).map(([stage, failure]) => <p className="error" key={stage}>{text(stage)}: {failure}</p>)}
      {candidate.retry_at > Date.now() / 1000 && <p>Retry deferred until {new Date(candidate.retry_at * 1000).toLocaleString()}</p>}
      <div className="actions">{candidate.actions.map(action => <button key={action} aria-label={`${labels[action]} ${candidate.path}`} disabled={busy || !!error || !!status?.active} onClick={() => void act(`/candidates/${candidate.id}/actions`, { action }, action.startsWith('retry') ? 'Retry queued for the next run' : action === 'dismiss' ? 'Candidate dismissed; staged files and reviews retained' : 'Candidate restored')}>{labels[action]}</button>)}<button onClick={() => void history(candidate.id)} aria-expanded={historyId === candidate.id}>Attempt history</button>{candidate.reviewer_build_id && reviewerBuildIds.includes(candidate.reviewer_build_id) && <button onClick={() => onOpenReview(candidate.reviewer_build_id!)}>Open for review</button>}</div>
      <details><summary>Source & evidence ({candidate.evidence.length})</summary><p>Revision: {candidate.revision}</p><p>Git blob: {candidate.blob_sha}</p>{candidate.source_sha256 && <p>Original SHA-256: {candidate.source_sha256}</p>}<p>Evidence: {text(candidate.evidence_status || 'not_collected_yet')} · Asset authorship and usage require human review.</p>{candidate.evidence.map((ref, i) => <p key={i}><a href={`${endpoint}/candidates/${candidate.id}/evidence/${ref.sha256}`}>Download {ref.path}</a> · {bytes(ref.size)}</p>)}{candidate.suggestions.map((suggestion, i) => <p key={i}>Unconfirmed suggestion: {suggestion.field} = {suggestion.value} · {suggestion.origin}</p>)}</details>
      {historyId === candidate.id && histories[candidate.id] && <section className="collector-history" aria-label={`Attempt history for ${candidate.path}`}><AttemptList title="Collection attempts" attempts={histories[candidate.id].collection}/><AttemptList title="Validation attempts" attempts={histories[candidate.id].validation}/><h4>Latest delivery</h4><p>{text(histories[candidate.id].handoff.status || 'not_sent')} · {histories[candidate.id].handoff.attempts || 0} attempts</p>{histories[candidate.id].handoff.error && <p>{histories[candidate.id].handoff.error}</p>}</section>}
    </article>)}<div className="collector-pagination"><button disabled={!page.offset || busy} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous candidates</button><button disabled={page.offset + page.limit >= page.total || busy} onClick={() => setOffset(offset + 25)}>Next candidates</button></div></>}
  </section>;
}
