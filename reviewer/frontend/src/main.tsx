import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { VoxelRenderer, stateText, type SceneData, type Marker, type Selection, type Appearance } from './viewer';
import './style.css';

type Build = {
  id: string; filename: string; source_hash: string; type: string; styles: string[]; functions: string[];
  features: string[]; redstone: string; notes: string; family: string; family_confirmed: boolean;
  archived: boolean; provenance: string; evidence: string; source_url: string; revision: string;
  license: string; attribution: string; review_status: string; eligible: boolean; stale: boolean;
  eligibility_reasons: string[]; summary: { enclosing_dimensions_xyz: number[]; total_non_air_blocks_across_regions: number; region_count: number } | null;
  conversion: { id: string; report: { passed: boolean; counts: Record<string, number>; geometry_errors: string[]; metadata_changes: string[] } } | null;
  source_references: { filename: string; reference: string | null }[];
  reviews: { decision: string; scope: string; created: string; notes: string }[];
};
type Dataset = { id: string; name: string; description: string; exclude_redstone: boolean; archived: boolean; build_ids: string[]; splits: Record<string, string>; versions: { id: string; created: string }[] };
type Job = { id: string; filename: string; status: string; error: string | null };
type Comparison = { original: SceneData; converted: SceneData; passed: boolean; markers: Record<string, Marker[]>; counts: Record<string, number>; geometry_errors: string[]; metadata_changes: string[]; differences: { region: string; index: number; position: number[]; kind: string }[] };
type Preview = { ready: boolean; warnings: string[]; entries: { build: Build; split: string; blocking_reasons: string[] }[] };
type Coverage = { version: string; total: number; scope: string; counts: Record<string, number>; entries: { name: string; category: string; warnings: string[]; has_texture: boolean; properties: Record<string, string> }[] };
const types = ['unclassified', 'house', 'castle', 'tower', 'farm', 'redstone build', 'other'];
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function json(method: string, payload: unknown): RequestInit { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }; }
function Badge({ children, good = false }: { children: React.ReactNode; good?: boolean }) { return <span className={`badge ${good ? 'good' : ''}`}>{children}</span>; }
function Labels({ category, value, options, onChange, onAdd }: { category: 'styles' | 'functions' | 'features'; value: string[]; options: string[]; onChange: (values: string[]) => void; onAdd: (category: string, label: string) => Promise<string> }) {
  const [newLabel, setNewLabel] = useState('');
  const [error, setError] = useState('');
  async function add() {
    try { const label = await onAdd(category, newLabel); onChange([...new Set([...value, label])]); setNewLabel(''); setError(''); }
    catch (reason) { setError(String(reason)); }
  }
  return <><div className="label-options">{options.length ? options.map(label => <label className="check" key={label}><input type="checkbox" checked={value.includes(label)} onChange={e => onChange(e.target.checked ? [...value, label] : value.filter(item => item !== label))}/>{label}</label>) : <small>No labels yet.</small>}</div><div className="label-add"><input aria-label={`New ${category} label`} value={newLabel} placeholder="Add a new label" onChange={e => setNewLabel(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && newLabel.trim()) { e.preventDefault(); void add(); } }}/><button type="button" disabled={!newLabel.trim()} onClick={() => void add()}>Add</button></div>{error && <small role="alert">{error}</small>}</>;
}

function ComparisonView({ value }: { value: Comparison }) {
  const leftHost = useRef<HTMLDivElement>(null), rightHost = useRef<HTMLDivElement>(null);
  const left = useRef<VoxelRenderer | null>(null), right = useRef<VoxelRenderer | null>(null);
  const linked = useRef(true);
  const [sync, setSync] = useState(true), [highlight, setHighlight] = useState(true), [fly, setFly] = useState(false);
  const [regionName, setRegionName] = useState(value.original.regions[0]?.name || '');
  const [layer, setLayer] = useState<number | null>(null), [selection, setSelection] = useState<Selection | null>(null);
  const [viewerError, setViewerError] = useState('');
  const [textures, setTextures] = useState(true);
  const [assets, setAssets] = useState<{ available: boolean; version?: string } | null>(null);
  const [appearance, setAppearance] = useState<{ original: Appearance[]; converted: Appearance[] }>({ original: [], converted: [] });
  const [textureMessage, setTextureMessage] = useState('Loading local vanilla textures…');
  const [missingMaterials, setMissingMaterials] = useState<string[]>([]);
  const [modelWarnings, setModelWarnings] = useState<string[]>([]);
  const names = [...new Set([...value.original.regions, ...value.converted.regions].map(r => r.name))];
  const original = value.original.regions.find(r => r.name === regionName), converted = value.converted.regions.find(r => r.name === regionName);
  const minY = Math.min(...[original, converted].filter(Boolean).map(r => r!.origin[1]));
  const maxY = Math.max(...[original, converted].filter(Boolean).map(r => r!.origin[1] + r!.size[1] - 1));
  useEffect(() => {
    let live = true;
    setAppearance({ original: [], converted: [] });
    setMissingMaterials([]);
    setModelWarnings([]);
    api<{ available: boolean; version?: string }>('/vanilla').then(async status => {
      if (!live) return;
      setAssets(status);
      if (!status.available) { setTextureMessage('Vanilla assets not installed. See reviewer setup instructions; diagnostic colors remain available.'); return; }
      const [a, b] = await Promise.all([api<Appearance[]>('/vanilla/palette', json('POST', original?.palette || [])), api<Appearance[]>('/vanilla/palette', json('POST', converted?.palette || []))]);
      if (!live) return;
      setAppearance({ original: a, converted: b });
      const missing = new Set<string>();
      const warnings = new Set<string>();
      for (const [region, palette] of [[original, a], [converted, b]] as const) {
        if (!region) continue;
        for (const index of new Set(region.indices)) {
          const name = region.palette[index].name;
          if (!['minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'].includes(name)) {
            if (!palette[index]?.invisible && !palette[index]?.parts?.some(p => !!p.texture)) missing.add(name);
            palette[index]?.warnings?.forEach(warning => warnings.add(`${name.replace('minecraft:', '')}: ${warning}`));
          }
        }
      }
      setMissingMaterials([...missing].sort());
      setModelWarnings([...warnings].sort());
      setTextureMessage(`Local vanilla textures · Minecraft ${status.version} · ${missing.size} material(s) using diagnostic colors in this region.`);
    }).catch(error => { if (live) setTextureMessage(`Texture setup failed: ${String(error)}. Diagnostic colors remain available.`); });
    return () => { live = false; };
  }, [value, regionName]);
  useEffect(() => {
    try {
      left.current = new VoxelRenderer(leftHost.current!, 'original', camera => { if (linked.current) right.current?.setCamera(camera); }, setSelection, setFly);
      right.current = new VoxelRenderer(rightHost.current!, 'converted', camera => { if (linked.current) left.current?.setCamera(camera); }, setSelection, setFly);
    } catch (error) { setViewerError(`3D graphics unavailable: ${String(error)}. Exact comparison details remain available.`); }
    return () => { left.current?.dispose(); right.current?.dispose(); };
  }, []);
  useEffect(() => { linked.current = sync; if (sync && left.current) right.current?.setCamera(left.current.getCamera()); }, [sync]);
  useEffect(() => {
    left.current?.setAppearance(appearance.original, textures && !!assets?.available);
    right.current?.setAppearance(appearance.converted, textures && !!assets?.available);
  }, [appearance, textures, assets]);
  useEffect(() => {
    const failed = () => setTextureMessage('One or more vanilla textures failed to load. Reload the viewer or switch to diagnostic colors.');
    leftHost.current?.addEventListener('texture-error', failed); rightHost.current?.addEventListener('texture-error', failed);
    return () => { leftHost.current?.removeEventListener('texture-error', failed); rightHost.current?.removeEventListener('texture-error', failed); };
  }, []);
  useEffect(() => {
    left.current?.loadScene(original, layer ?? Infinity, value.markers[regionName] || [], highlight);
    right.current?.loadScene(converted, layer ?? Infinity, value.markers[regionName] || [], highlight);
    if (sync && left.current) right.current?.setCamera(left.current.getCamera());
  }, [value, regionName, layer, highlight]);
  useEffect(() => { left.current?.setSelection(selection?.index ?? null); right.current?.setSelection(selection?.index ?? null); }, [selection, appearance, textures, layer, highlight]);
  const selectedOriginal = selection && original?.palette[original.indices[selection.index]];
  const selectedConverted = selection && converted?.palette[converted.indices[selection.index]];
  return <section className="comparison">
    <div className="viewer-tools">
      <label>Region <select aria-label="Region" value={regionName} onChange={e => { setRegionName(e.target.value); setLayer(null); setSelection(null); }}>{names.map(name => <option key={name}>{name}</option>)}</select></label>
      <label className="check"><input type="checkbox" checked={sync} onChange={e => setSync(e.target.checked)}/>Link cameras</label>
      <label className="check"><input type="checkbox" checked={highlight} onChange={e => setHighlight(e.target.checked)}/>Show differences</label>
      <label className="check"><input type="checkbox" checked={textures} disabled={!assets?.available} onChange={e => setTextures(e.target.checked)}/>Vanilla textures</label>
      <button onClick={() => { left.current?.resetCamera(); if (!sync) right.current?.resetCamera(); }}>Reset view</button>
    </div>
    <div className="notice">Vanilla model preview · block-state shapes, face UVs, rotations and multipart connections use local game resources. Biome colors, fluids, special block entities, lighting and transparency sorting remain approximate; animated textures use a static frame. This is not Minecraft’s renderer.</div>
    <p role="status" className="muted">{textureMessage}</p>
    {!!missingMaterials.length && <details><summary>Materials without supported vanilla textures ({missingMaterials.length})</summary><p>{missingMaterials.map(name => name.replace('minecraft:', '')).join(', ')}</p></details>}
    {!!modelWarnings.length && <details><summary>Model preview limitations ({modelWarnings.length})</summary>{modelWarnings.map(warning => <p key={warning}>{warning}</p>)}</details>}
    {viewerError && <p role="alert" className="error">{viewerError}</p>}
    <div className="viewers">
      <div className="viewer-panel"><div className="viewer-heading"><strong>Original</strong><button onClick={() => left.current?.startFly()}>Fly here</button></div><div className="canvas-host" ref={leftHost}/>{!original && <div className="no-counterpart">No original region</div>}</div>
      <div className="viewer-panel"><div className="viewer-heading"><strong>Converted</strong><button onClick={() => right.current?.startFly()}>Fly here</button></div><div className="canvas-host" ref={rightHost}/>{!converted && <div className="no-counterpart">No converted counterpart</div>}</div>
    </div>
    <div className="layer-bar"><label>Layer {layer === null ? 'all' : `y = ${layer}`}<input aria-label="Layer slicing" type="range" min={minY} max={maxY} value={layer ?? maxY} onChange={e => setLayer(Number(e.target.value))}/></label><button onClick={() => setLayer(null)}>All layers</button><small>{fly ? 'Flying: WASD · Space/E up · Shift/Q down · Esc exit · drag to look if mouse lock is unavailable' : 'Drag to orbit · scroll to zoom · click a block to inspect'}</small></div>
    <div className="difference-key"><span className="missing">Missing {value.counts.missing}</span><span className="added">Added {value.counts.added}</span><span className="changed">Changed {value.counts.changed}</span><span>{value.passed ? 'Exact data comparison passed' : 'Conversion differences found'}</span></div>
    {selection && <div className="block-inspection"><strong>Block at {selection.position.join(', ')} · {selection.region}</strong><div>Original: {stateText(selectedOriginal || undefined)}</div><div>Converted: {stateText(selectedConverted || undefined)}</div><small>Original model: {appearance.original[original?.indices[selection.index] ?? -1]?.models?.join(', ') || 'Diagnostic fallback'} · Converted model: {appearance.converted[converted?.indices[selection.index] ?? -1]?.models?.join(', ') || 'Diagnostic fallback'}</small></div>}
    {!!value.geometry_errors.length && <div className="error">{value.geometry_errors.join('; ')}. Coordinate comparison is ambiguous; regions remain separate.</div>}
    {!!value.metadata_changes.length && <details><summary>Metadata differences ({value.metadata_changes.length})</summary>{value.metadata_changes.map((text, i) => <p key={i}>{text}</p>)}</details>}
    {!!value.differences.length && <details><summary>Inspect block differences (sample, up to 200)</summary><div className="difference-list">{value.differences.map((difference, i) => <button key={i} onClick={() => { setRegionName(difference.region); setLayer(null); const region = value.original.regions.find(r => r.name === difference.region)!; setSelection({ ...difference, state: region.palette[region.indices[difference.index]], side: 'original' }); }}>{difference.kind} · {difference.region} · {difference.position.join(', ')}</button>)}</div></details>}
    <small className="muted">Difference overlays show up to 10,000 markers per region; counts remain exact. Disable vanilla textures to inspect deterministic material colors. Rendering does not change approval scope or training data.</small>
  </section>;
}

function App() {
  const [coverage, setCoverage] = useState<Coverage | null>(null), [coverageQuery, setCoverageQuery] = useState('');
  const [page, setPage] = useState('Review queue'), [builds, setBuilds] = useState<Build[]>([]), [jobs, setJobs] = useState<Job[]>([]), [datasets, setDatasets] = useState<Dataset[]>([]);
  const [labelOptions, setLabelOptions] = useState<Record<'styles' | 'functions' | 'features', string[]>>({ styles: [], functions: [], features: [] });
  const [selected, setSelected] = useState<string | null>(null), [draft, setDraft] = useState<Build | null>(null), [comparison, setComparison] = useState<Comparison | null>(null);
  const [comparedBuildId, setComparedBuildId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [query, setQuery] = useState(''), [filterType, setFilterType] = useState(''), [filterReview, setFilterReview] = useState(''), [filterRedstone, setFilterRedstone] = useState(''), [filterProvenance, setFilterProvenance] = useState(''), [filterArchive, setFilterArchive] = useState('active');
  const [filterStyle, setFilterStyle] = useState(''), [filterFunction, setFilterFunction] = useState(''), [minDimension, setMinDimension] = useState(''), [maxDimension, setMaxDimension] = useState('');
  const [datasetId, setDatasetId] = useState(''), [datasetName, setDatasetName] = useState(''), [preview, setPreview] = useState<Preview | null>(null), [bulk, setBulk] = useState(false);
  const upload = useRef<HTMLInputElement>(null), replacement = useRef<HTMLInputElement>(null);
  const active = builds.find(b => b.id === selected), dataset = datasets.find(d => d.id === datasetId);
  const filtered = builds.filter(b => {
    const dimensions = b.summary?.enclosing_dimensions_xyz || [], longest = Math.max(...dimensions);
    return (!query || `${b.filename} ${b.notes} ${b.styles.join(' ')} ${b.functions.join(' ')}`.toLowerCase().includes(query.toLowerCase())) &&
      (!filterType || b.type === filterType) && (!filterReview || b.review_status === filterReview) && (!filterRedstone || b.redstone === filterRedstone) &&
      (!filterProvenance || b.provenance === filterProvenance) && (filterArchive === 'all' || b.archived === (filterArchive === 'archived')) &&
      (!filterStyle || b.styles.some(v => v.toLowerCase().includes(filterStyle.toLowerCase()))) && (!filterFunction || b.functions.some(v => v.toLowerCase().includes(filterFunction.toLowerCase()))) &&
      (!minDimension || longest >= Number(minDimension)) && (!maxDimension || longest <= Number(maxDimension));
  });
  const queue = filtered.filter(b => !b.archived && ['pending', 'deferred'].includes(b.review_status)).sort((a, b) => Number(a.review_status === 'deferred') - Number(b.review_status === 'deferred'));
  const displayed = page === 'Review queue' ? queue : filtered;
  async function refresh() {
    const [bs, js, ds, labels] = await Promise.all([api<Build[]>('/builds?archived=all'), api<Job[]>('/jobs'), api<Dataset[]>('/datasets'), api<Record<'styles' | 'functions' | 'features', string[]>>('/labels')]);
    setBuilds(bs); setJobs(js); setDatasets(ds); setLabelOptions(labels);
    setSelected(current => current || bs.find(b => !b.archived)?.id || null);
    setDatasetId(current => current || ds[0]?.id || '');
  }
  useEffect(() => { refresh().catch(e => setError(String(e))); const timer = setInterval(() => refresh().catch(e => setError(String(e))), 4000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    if ((page === 'Review queue' || page === 'Library') && !displayed.some(b => b.id === selected)) {
      setSelected(displayed[0]?.id || null);
    }
  }, [page, selected, displayed.map(b => b.id).join('|')]);
  useEffect(() => { setDraft(active ? { ...active } : null); }, [selected]);
  useEffect(() => {
    let live = true; setComparison(null);
    if (active?.conversion && !active.stale) api<Comparison>(`/builds/${active.id}/comparison`).then(value => { if (live) { setComparison(value); setComparedBuildId(active.id); } }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [selected, active?.conversion?.id, active?.stale]);
  async function act(work: () => Promise<unknown>, success = 'Saved') {
    setBusy(true); setError(''); setMessage('');
    try { await work(); await refresh(); setMessage(success); return true; }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); return false; }
    finally { setBusy(false); }
  }
  const change = (field: keyof Build, value: unknown) => setDraft(current => current ? { ...current, [field]: value } : null);
  function fields() {
    if (!draft) return {};
    const { type, styles, functions, features, redstone, notes, family, family_confirmed, provenance, evidence, source_url, revision, license, attribution } = draft;
    return { type, styles, functions, features, redstone, notes, family, family_confirmed, provenance, evidence, source_url, revision, license, attribution };
  }
  async function save() { if (active) await act(async () => setDraft(await api<Build>(`/builds/${active.id}`, json('PATCH', fields())))); }
  async function decide(decision: string) {
    if (!active || busy) return;
    const previous = active.id;
    const ok = await act(async () => {
      await api(`/builds/${previous}`, json('PATCH', fields()));
      const updated = await api<Build>(`/builds/${previous}/review`, json('POST', { decision, notes: draft?.notes || '' }));
      setDraft(updated);
    }, decision === 'deferred' ? 'Skipped for now; the build remains in your queue' : `Review ${decision}`);
    if (ok && page === 'Review queue') setSelected(queue.find(b => b.id !== previous)?.id || null);
  }
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (document.pointerLockElement || document.querySelector('canvas[data-flying="true"]') || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement || event.ctrlKey || event.metaKey || event.altKey || page !== 'Review queue') return;
      const decisions: Record<string, string> = { a: 'approved', r: 'rejected', s: 'deferred' };
      if (decisions[event.key.toLowerCase()]) { event.preventDefault(); void decide(decisions[event.key.toLowerCase()]); }
    };
    document.addEventListener('keydown', handler); return () => document.removeEventListener('keydown', handler);
  }, [active, draft, busy, queue, page]);
  async function importFiles(files: FileList | File[]) {
    const body = new FormData(); Array.from(files).forEach(file => body.append('files', file));
    await act(async () => { const result = await api<{ error?: string }[]>('/imports', { method: 'POST', body }); const errors = result.filter(r => r.error).map(r => r.error); if (errors.length) throw new Error(errors.join('; ')); }, 'Imports queued; originals are preserved');
  }
  async function fetchPreview() { if (dataset) setPreview(await api<Preview>(`/datasets/${dataset.id}/preview`)); }
  async function addLabel(category: string, label: string) {
    const result = await api<{ category: 'styles' | 'functions' | 'features'; label: string }>('/labels', json('POST', { category, label }));
    setLabelOptions(current => ({ ...current, [result.category]: [...new Set([...current[result.category], result.label])].sort((a, b) => a.localeCompare(b)) }));
    return result.label;
  }
  function formLabel(label: string, field: 'styles' | 'functions' | 'features') {
    return <div className="classification-field"><span>{label}</span><Labels category={field} value={draft?.[field] || []} options={labelOptions[field]} onChange={values => change(field, values)} onAdd={addLabel}/></div>;
  }
  return <div className="app">
    <header className="header"><div className="brand-mark">▦</div><div><h1>Build library</h1><p>Minecraft dataset reviewer</p></div><div className="header-right"><Badge good>Local workspace</Badge><span>{builds.filter(b => !b.archived).length} builds · {datasets.filter(d => !d.archived).length} datasets</span></div></header>
    {coverage && <section className="coverage-report" aria-label="Vanilla block coverage"><h2>Minecraft {coverage.version} · {coverage.total} vanilla block definitions</h2><p>{coverage.scope}</p><p>{Object.entries(coverage.counts).map(([kind,count]) => `${kind.replaceAll('_',' ')}: ${count}`).join(' · ')}</p><label>Search coverage<input value={coverageQuery} onChange={e => setCoverageQuery(e.target.value)} placeholder="Block name or coverage category"/></label><button onClick={() => setCoverage(null)}>Close coverage</button><div className="coverage-entries">{coverage.entries.filter(entry => `${entry.name} ${entry.category}`.includes(coverageQuery.toLowerCase())).map(entry => <details key={entry.name}><summary>{entry.name.replace('minecraft:','')} · {entry.category.replaceAll('_',' ')}</summary><p>{Object.entries(entry.properties).map(([key,value]) => `${key}=${value}`).join(', ') || 'No state properties in this representative sample'}</p>{entry.warnings.map(warning => <p key={warning}>{warning}</p>)}</details>)}</div></section>}
    <nav aria-label="Main navigation">{['Review queue', 'Library', 'Datasets', 'Processing'].map(tab => <button key={tab} className={page === tab ? 'active' : ''} onClick={() => { setPage(tab); setPreview(null); }}>{tab}{tab === 'Review queue' && <span>{builds.filter(b => !b.archived && ['pending', 'deferred'].includes(b.review_status)).length}</span>}</button>)}<button disabled={busy} onClick={() => act(async () => setCoverage(await api<Coverage>('/vanilla/coverage')), 'Vanilla coverage loaded')}>Vanilla coverage</button></nav>
    <div className="feedback" aria-live="polite">{error && <div role="alert" className="error">{error}<button onClick={() => setError('')}>Dismiss</button></div>}{message && <div className="success">{message}</div>}</div>
    {page !== 'Processing' && <section className="filters">
      <input aria-label="Search builds" placeholder="Search builds or labels…" value={query} onChange={e => setQuery(e.target.value)}/>
      <select aria-label="Building type filter" value={filterType} onChange={e => setFilterType(e.target.value)}><option value="">All types</option>{types.map(v => <option key={v}>{v}</option>)}</select>
      <select aria-label="Review status filter" value={filterReview} onChange={e => setFilterReview(e.target.value)}><option value="">All review states</option>{['pending', 'approved', 'rejected', 'deferred'].map(v => <option key={v}>{v}</option>)}</select>
      <select aria-label="Redstone filter" value={filterRedstone} onChange={e => setFilterRedstone(e.target.value)}><option value="">All redstone states</option>{['yes', 'no', 'unknown'].map(v => <option key={v}>{v}</option>)}</select>
      <select aria-label="Provenance filter" value={filterProvenance} onChange={e => setFilterProvenance(e.target.value)}><option value="">All usage states</option>{['unknown', 'allowed', 'denied', 'project_generated'].map(v => <option key={v}>{v}</option>)}</select>
      <select aria-label="Archive filter" value={filterArchive} onChange={e => setFilterArchive(e.target.value)}>{['active', 'archived', 'all'].map(v => <option key={v}>{v}</option>)}</select>
      <details><summary>More filters</summary><div className="extra-filters"><input aria-label="Style filter" placeholder="Style" value={filterStyle} onChange={e => setFilterStyle(e.target.value)}/><input aria-label="Function filter" placeholder="Function" value={filterFunction} onChange={e => setFilterFunction(e.target.value)}/><input aria-label="Minimum largest dimension" type="number" min="0" placeholder="Min largest dimension" value={minDimension} onChange={e => setMinDimension(e.target.value)}/><input aria-label="Maximum largest dimension" type="number" min="0" placeholder="Max largest dimension" value={maxDimension} onChange={e => setMaxDimension(e.target.value)}/></div></details>
    </section>}
    {(page === 'Review queue' || page === 'Library') && <>
      <section className="import-bar" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (!busy) void importFiles(e.dataTransfer.files); }}>
        <div><strong>{page === 'Review queue' ? 'Review before you train' : 'Your approved and pending builds'}</strong><p>Drop .litematic files here, or import your existing collection.</p></div>
        <div className="actions"><button disabled={busy} onClick={() => upload.current?.click()}>Import files</button><button disabled={busy} onClick={() => act(() => api('/imports/existing', { method: 'POST' }), 'Existing project imports queued')}>Import project builds</button><button disabled={busy} onClick={() => act(() => api('/imports/demo', { method: 'POST' }), 'Project-generated demo queued')}>Import generated demo</button></div>
        <input hidden type="file" accept=".litematic" multiple ref={upload} onChange={e => { if (e.target.files) void importFiles(e.target.files); e.target.value = ''; }}/>
      </section>
      <div className="workbench">
        <aside className="build-list"><div className="list-title">{displayed.length} {page === 'Review queue' ? 'to review' : 'matches'}</div>{displayed.map(b => <button className={`build-row ${selected === b.id ? 'selected' : ''}`} key={b.id} onClick={() => setSelected(b.id)}><strong>{b.filename.replace('-from-abfielder', '')}</strong><span>{b.summary?.enclosing_dimensions_xyz.join(' × ') || 'Processing'} · {b.type}</span><Badge good={b.review_status === 'approved'}>{b.review_status}</Badge></button>)}{!displayed.length && <p className="empty-small">No builds match. Import a build or adjust your filters.</p>}</aside>
        {active && draft && draft.id === active.id ? <main className="review-main">
          <div className="title-row"><div><h2>{active.filename}</h2><p>{active.summary ? `${active.summary.total_non_air_blocks_across_regions.toLocaleString()} non-air blocks · ${active.summary.region_count} region(s)` : 'Waiting for inspection'}</p></div><Badge good={active.eligible}>{active.eligible ? 'Training eligible' : 'Not training eligible'}</Badge></div>
          <div className="review-grid"><section className="details-card">
            <h3>Details & classifications</h3><span className="muted">Labels below are human-confirmed when saved.</span>
            <label>Building type<select value={draft.type} onChange={e => change('type', e.target.value)}>{types.map(v => <option key={v}>{v}</option>)}</select></label>
            {formLabel('Style labels', 'styles')}
            {formLabel('Function labels', 'functions')}
            {formLabel('Features', 'features')}
            <label>Contains redstone<select value={draft.redstone} onChange={e => change('redstone', e.target.value)}>{['unknown', 'yes', 'no'].map(v => <option key={v}>{v}</option>)}</select></label>
            <label>Related-build family<input value={draft.family} onChange={e => { change('family', e.target.value); change('family_confirmed', false); }}/></label>
            <label className="check"><input type="checkbox" checked={draft.family_confirmed} onChange={e => change('family_confirmed', e.target.checked)}/>I reviewed this build's family grouping</label>
            <label>Notes<textarea value={draft.notes} onChange={e => change('notes', e.target.value)} rows={3}/></label>
            <details><summary>Provenance & permitted usage</summary>
              <label>Usage status<select value={draft.provenance} onChange={e => change('provenance', e.target.value)}>{['unknown', 'allowed', 'denied', 'project_generated'].map(v => <option key={v}>{v}</option>)}</select></label>
              <p className="muted">Free download is not evidence of permission. Record the applicable terms or project authorship.</p>
              {(['source_url', 'license', 'revision', 'attribution', 'evidence'] as const).map(field => <label key={field}>{({ source_url: 'Source URL', license: 'License', revision: 'Repository revision', attribution: 'Attribution', evidence: 'Permission evidence' })[field]}<input value={draft[field]} onChange={e => change(field, e.target.value)}/></label>)}
              <small>Source hash: {active.source_hash}</small>{active.source_references.map((ref, i) => <p className="muted" key={i}>{ref.filename} · {ref.reference || 'Uploaded file'}</p>)}
            </details>
            <button className="primary" disabled={busy} onClick={save}>Save details</button>
            {!!active.eligibility_reasons.length && <details><summary>Why not ready for training?</summary><ul>{active.eligibility_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></details>}
          </section><section className="comparison-card">
            {comparison && comparedBuildId === active.id ? <ComparisonView key={`${active.id}:${active.conversion?.id}`} value={comparison}/> : <div className="viewer-empty"><h3>{active.stale ? 'Comparison needs reprocessing' : 'Preparing comparison'}</h3><p>{active.stale ? 'The source, converted file, or converter has changed. Previous review is no longer current.' : 'Independent file inspection appears here when processing is complete.'}</p><button disabled={busy} onClick={() => act(() => api(`/builds/${active.id}/reprocess`, { method: 'POST' }), 'Reprocessing queued')}>Reprocess build</button></div>}
            <div className="review-actions"><span>Diagnostic data review · A approve / R reject / S skip</span><button disabled={busy || !active.conversion || active.stale} onClick={() => decide('deferred')}>Skip</button><button className="reject" disabled={busy || !active.conversion || active.stale} onClick={() => decide('rejected')}>Reject</button><button className="primary" disabled={busy || !active.conversion?.report.passed || active.stale} onClick={() => decide('approved')}>Approve</button></div>
          </section></div>
          <div className="build-footer"><div className="actions"><a href={`/api/builds/${active.id}/files/original`}>Download original</a>{active.conversion && <a href={`/api/builds/${active.id}/files/converted`}>Download converted</a>}<button disabled={busy} onClick={() => replacement.current?.click()}>Replace converted file</button><button disabled={busy} onClick={() => act(() => api(`/builds/${active.id}/reprocess`, { method: 'POST' }), 'Reprocessing queued')}>Reprocess</button><button disabled={busy} onClick={() => act(() => api(`/builds/${active.id}`, json('PATCH', { archived: !active.archived })), active.archived ? 'Build restored' : 'Build archived; originals and history retained')}>{active.archived ? 'Restore build' : 'Archive build'}</button></div><input type="file" accept=".litematic" hidden ref={replacement} onChange={e => { const file = e.target.files?.[0]; if (file) { const body = new FormData(); body.append('file', file); void act(() => api(`/builds/${active.id}/conversion`, { method: 'POST', body }), 'New conversion loaded; review required'); } e.target.value = ''; }}/>
          <details><summary>Review history ({active.reviews.length})</summary>{active.reviews.map((r, i) => <p key={i}>{r.decision} · {new Date(r.created).toLocaleString()} · {r.scope} {r.notes && `· ${r.notes}`}</p>)}</details></div>
        </main> : <main className="welcome"><h2>A better dataset starts here.</h2><p>Import a build, inspect the conversion, and curate your training collection.</p><p>Approvals, usage evidence, and dataset membership are recorded separately.</p></main>}
      </div></>}
    {page === 'Datasets' && <main className="dataset-page">
      <section className="dataset-sidebar"><h2>Training datasets</h2><p>Membership is manual. Labels never silently add or remove builds.</p><form onSubmit={e => { e.preventDefault(); void act(async () => { const ds = await api<Dataset>('/datasets', json('POST', { name: datasetName })); setDatasetId(ds.id); setDatasetName(''); }, 'Dataset created'); }}><label>New dataset name<input value={datasetName} onChange={e => setDatasetName(e.target.value)} required maxLength={100}/></label><button className="primary" disabled={busy}>Create dataset</button></form>{datasets.map(d => <button key={d.id} className={`dataset-row ${datasetId === d.id ? 'selected' : ''}`} onClick={() => { setDatasetId(d.id); setPreview(null); }}><strong>{d.name}</strong><span>{d.build_ids.length} builds · {d.archived ? 'archived' : 'active'}</span></button>)}</section>
      <section className="dataset-content">{dataset ? <>
        <div className="title-row"><h2>{dataset.name}</h2><div className="actions"><button disabled={busy} onClick={() => { const name = window.prompt('Dataset name', dataset.name); if (name) void act(() => api(`/datasets/${dataset.id}`, json('PATCH', { name }))); }}>Rename</button><button disabled={busy} onClick={() => act(() => api(`/datasets/${dataset.id}`, json('PATCH', { archived: !dataset.archived })))}>{dataset.archived ? 'Restore' : 'Archive'}</button></div></div>
        <label>Description<input key={dataset.id} defaultValue={dataset.description} onBlur={e => { if (e.target.value !== dataset.description) void act(() => api(`/datasets/${dataset.id}`, json('PATCH', { description: e.target.value }))); }}/></label>
        <label className="check"><input type="checkbox" checked={dataset.exclude_redstone} disabled={busy} onChange={e => act(() => api(`/datasets/${dataset.id}`, json('PATCH', { exclude_redstone: e.target.checked })))}/>Exclude confirmed redstone builds from training export</label>
        <div className="dataset-toolbar"><span>{filtered.length} builds in current filters</span><button disabled={busy || dataset.archived || !filtered.length} onClick={() => setBulk(true)}>Add filtered builds…</button><button disabled={busy} onClick={() => act(fetchPreview, 'Export preview updated')}>Preview export</button><button className="primary" disabled={busy || !preview?.ready} onClick={() => act(async () => { await api(`/datasets/${dataset.id}/versions`, { method: 'POST' }); await fetchPreview(); }, 'Immutable dataset version exported')}>Export version</button></div>
        {bulk && <div className="bulk-confirm" role="dialog" aria-label="Confirm bulk membership"><h3>Add these {filtered.length} builds to {dataset.name}?</h3><ul>{filtered.map(b => <li key={b.id}>{b.filename}</li>)}</ul><p>This changes membership only, not approval or training eligibility.</p><button disabled={busy} className="primary" onClick={() => act(async () => { await api(`/datasets/${dataset.id}/membership`, json('POST', { build_ids: filtered.map(b => b.id), include: true })); setBulk(false); setPreview(null); }, 'Filtered selection added')}>Confirm add</button><button onClick={() => setBulk(false)}>Cancel</button></div>}
        <table><thead><tr><th>Include</th><th>Build</th><th>Review / eligibility</th><th>Family split</th></tr></thead><tbody>{filtered.map(b => <tr key={b.id}><td><input aria-label={`Include ${b.filename}`} type="checkbox" checked={dataset.build_ids.includes(b.id)} disabled={busy || dataset.archived} onChange={e => act(async () => { await api(`/datasets/${dataset.id}/membership`, json('POST', { build_ids: [b.id], include: e.target.checked })); setPreview(null); })}/></td><td><button className="text-button" onClick={() => { setSelected(b.id); setPage('Library'); }}>{b.filename}</button><small>{b.type} · family {b.family.slice(0, 16)}{!b.family_confirmed && ' (unconfirmed)'}</small></td><td><Badge good={b.eligible}>{b.eligible ? 'eligible' : b.review_status}</Badge></td><td>{dataset.build_ids.includes(b.id) ? <select aria-label={`Split for ${b.filename}`} disabled={busy || dataset.archived} value={dataset.splits[b.family] || 'unassigned'} onChange={e => act(async () => { await api(`/datasets/${dataset.id}/splits`, json('POST', { family: b.family, split: e.target.value })); setPreview(null); })}>{['unassigned', 'train', 'validation', 'test'].map(v => <option key={v}>{v}</option>)}</select> : '—'}</td></tr>)}</tbody></table>
        {preview && <section className="export-preview"><h3>{preview.ready ? 'Ready to export' : 'Export is blocked'}</h3>{preview.warnings.map(w => <p key={w}>{w}</p>)}{preview.entries.map(entry => <div key={entry.build.id}><strong>{entry.build.filename} · {entry.split}</strong>{entry.blocking_reasons.length ? <ul>{entry.blocking_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : <p className="success">All checks passed</p>}</div>)}<p className="muted">No members are silently omitted. Each export copies verified artifacts and freezes labels, hashes and splits.</p></section>}
        <h3>Exported versions</h3>{dataset.versions.length ? dataset.versions.map(v => <div className="version-row" key={v.id}><span>{new Date(v.created).toLocaleString()} · {v.id.slice(0, 12)}</span><a href={`/api/datasets/${dataset.id}/versions/${v.id}/download`}>Download ZIP</a><a href={`/api/datasets/${dataset.id}/versions/${v.id}`} target="_blank" rel="noreferrer">Manifest</a></div>) : <p className="muted">No versions exported yet.</p>}
      </> : <div className="welcome"><h2>Create your first dataset.</h2><p>A build can belong to several collections, each with independent membership.</p></div>}</section>
    </main>}
    {page === 'Processing' && <main className="processing-page"><div className="title-row"><h2>Import & conversion jobs</h2><span>One worker · failures are isolated · originals stay unchanged</span></div>{jobs.length ? jobs.map(job => <div className="job" key={job.id}><div><strong>{job.filename}</strong><p>{job.error || 'Independent inspection and full block-state comparison'}</p></div><Badge good={job.status === 'completed'}>{job.status}</Badge>{['failed', 'interrupted'].includes(job.status) && <button disabled={busy} onClick={() => act(() => api(`/jobs/${job.id}/retry`, { method: 'POST' }), 'Retry queued')}>Retry</button>}</div>) : <p>No processing jobs yet. Import a file from the review queue.</p>}</main>}
    <footer>Local dataset reviewer · Originals preserved · Diagnostic rendering only</footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
