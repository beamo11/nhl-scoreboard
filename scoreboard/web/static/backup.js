// Backup & restore: one file with the settings and the data you could not get back (the
// pictures you uploaded, the flight sightings log), the way to put one back, and the
// copies of the settings the scoreboard keeps on every save.
import { html, useState, useEffect } from './htm-preact.js';
import { RestartButton } from './system.js';

// Every state-changing call carries this header; see scoreboard/web/guard.py.
const UI = { 'x-requested-with': 'scoreboard-ui' };

async function reason(r) {
  const text = await r.text().catch(() => '');
  let d = null;
  try { d = JSON.parse(text).detail; } catch (e) { return text.trim() || `${r.status} ${r.statusText}`; }
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).join('.') || 'value'}: ${e.msg}`).join(' · ');
  return `${r.status} ${r.statusText}`;
}
const ok = async (r) => { if (!r.ok) throw new Error(await reason(r)); return r.json(); };
const api = {
  get: (p) => fetch(p).then(ok),
  post: (p) => fetch(p, { method: 'POST', headers: UI }).then(ok),
  upload: (p, file) => fetch(p, { method: 'POST', headers: UI, body: file }).then(ok),
};

const LABELS = { holidays: 'holiday picture', flights: 'flight sightings log' };
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
const when = (epoch) => new Date(epoch * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

function describe(contents) {
  const parts = Object.entries(contents || {}).filter(([, n]) => n > 0)
    .map(([k, n]) => plural(n, LABELS[k] || `${k} file`));
  return parts.length ? `the settings, ${parts.join(' and ')}` : 'the settings';
}

function reportText(rep) {
  const parts = ['settings restored'];
  for (const [k, n] of Object.entries(rep.restored || {})) parts.push(plural(n, LABELS[k] || `${k} file`) + ' restored');
  let text = parts.join(', ') + '.';
  if (rep.skipped && rep.skipped.length) text += ` Skipped: ${rep.skipped.join('; ')}.`;
  return text;
}

export function Backup() {
  const [st, setSt] = useState(null);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [restart, setRestart] = useState(false);
  const picker = { current: null };

  const load = () => api.get('/api/backup').then(setSt).catch(e => setError(e.message));
  useEffect(() => { load(); }, []);

  const done = (text, needsRestart) => {
    setMsg(text + (needsRestart ? ' Display or follower settings changed: restart to apply them.' : ''));
    setRestart(!!needsRestart);
    setError('');
    load();
  };

  const chosen = (ev) => {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = '';
    if (!file) return;
    if (!confirm(`Restore settings and data from ${file.name}? What is on the panel now is replaced (the System › web settings are kept). The current settings go to the "previous settings" list below.`)) return;
    setBusy(true); setMsg(''); setError('');
    api.upload('/api/backup/import', file)
      .then(rep => done(`Restored from ${file.name}: ${reportText(rep)}`, rep.restart_needed))
      .catch(e => setError(e.message))
      .finally(() => setBusy(false));
  };

  const restoreSlot = (v) => {
    if (!confirm(`Go back to the settings as they were at ${when(v.modified)}? (The System › web settings are kept.)`)) return;
    setBusy(true); setMsg(''); setError('');
    api.post(`/api/backup/versions/${v.slot}/restore`)
      .then(rep => done(`Settings from ${when(v.modified)} restored.`, rep.restart_needed))
      .catch(e => setError(e.message))
      .finally(() => setBusy(false));
  };

  return html`<div class="card backup">
    <h2>Backup & restore</h2>
    <p class="muted small">A backup is one file with ${st ? describe(st.contents) : 'the settings and your data'}.
      Keep one somewhere safe before an update or a new SD card; restoring it puts everything back.</p>
    <div class="row">
      <a class="button" href="/api/backup/export" download>Download backup</a>
      <button class="secondary" disabled=${busy} onclick=${() => picker.current && picker.current.click()}>Restore from file…</button>
      <input type="file" accept=".zip,application/zip" ref=${picker} onchange=${chosen} style="display:none" />
    </div>
    ${st && st.versions && st.versions.length > 0 && html`
      <h3 class="group">Previous settings</h3>
      <p class="muted small">The settings as they were before each of the last ${st.versions.length === 1 ? 'save' : `${st.versions.length} saves`} (settings only; the data above is not part of these).</p>
      <ul class="versions">${st.versions.map(v => html`<li key=${v.slot} class="row">
        <span>${when(v.modified)}</span>
        <span class="muted small">${v.slot === 1 ? 'before the last save' : `${v.slot} saves ago`}</span>
        <button class="secondary" disabled=${busy} onclick=${() => restoreSlot(v)}>Restore</button>
      </li>`)}</ul>`}
    ${msg && html`<p class="muted">${msg}</p>`}
    ${restart && html`<${RestartButton} label="Restart now" onDone=${() => location.reload()} />`}
    ${error && html`<p class="error">${error}</p>`}
  </div>`;
}
