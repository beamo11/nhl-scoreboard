// Dashboard info cards: what's on (games for the next few days) and what's around
// (planes, holidays, weather). Everything comes from /api/dashboard, which is the snapshot
// trimmed to these fields; the cards only format it.
//
// The games window is whatever each sport's `show_games_within_days` setting says — the
// sources publish exactly that many days as `<sport>.schedule` — so the dashboard and the
// panel agree on how far ahead "upcoming" looks.

import { html, useState, useEffect } from './htm-preact.js';

const POLL_MS = 5000;

export function useDashboard() {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const tick = () => fetch('/api/dashboard').then(r => r.json()).then(d => { setData(d); setFailed(false); }).catch(() => setFailed(true));
    tick(); const id = setInterval(tick, POLL_MS); return () => clearInterval(id);
  }, []);
  return [data, failed];
}

// -- date / time helpers (dates are YYYY-MM-DD in the scoreboard's timezone) --------------

const parseDay = (s) => { const [y, m, d] = (s || '').split('-').map(Number); return Number.isFinite(d) ? Date.UTC(y, m - 1, d) : null; };
const dayDiff = (day, today) => { const a = parseDay(day), b = parseDay(today); return a == null || b == null ? null : Math.round((a - b) / 86400000); };
const WEEKDAY = new Intl.DateTimeFormat([], { weekday: 'short', timeZone: 'UTC' });
const MONTHDAY = new Intl.DateTimeFormat([], { month: 'short', day: 'numeric', timeZone: 'UTC' });

export function dayLabel(day, today) {
  const diff = dayDiff(day, today);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  const t = parseDay(day);
  return t == null ? day : `${WEEKDAY.format(t)} ${MONTHDAY.format(t)}`;
}

const shortDay = (day) => { const t = parseDay(day); return t == null ? day : `${WEEKDAY.format(t)} ${MONTHDAY.format(t)}`; };
const localTime = (iso) => { const t = iso ? new Date(iso) : null; return t && !isNaN(t) ? t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''; };

// -- games ---------------------------------------------------------------------------------

function gameStatus(g) {
  if (g.phase === 'postgame') return g.outcome || 'Final';
  if (g.phase === 'live' || g.phase === 'intermission') return [g.period, g.phase === 'intermission' ? 'INT' : g.clock].filter(Boolean).join(' ');
  return localTime(g.start_time_utc) || 'TBD';
}

function GameRow({ g }) {
  const started = g.phase !== 'pregame';
  return html`<tr class=${(g.favorite ? 'fav ' : '') + (g.main ? 'main ' : '') + g.phase}>
    <td class="team">${g.away.abbrev}${started ? html` <b>${g.away.score}</b>` : ''}</td>
    <td class="at">@</td>
    <td class="team">${g.home.abbrev}${started ? html` <b>${g.home.score}</b>` : ''}</td>
    <td class="state">${gameStatus(g)}${g.main ? html` <span class="chip" title="The game the panel is following">on panel</span>` : ''}</td>
  </tr>`;
}

export function recordText(r) {
  if (!r || r.wins == null) return '';
  const parts = [r.wins, r.losses];
  if (r.otl != null) parts.push(r.otl);
  if (r.ties) parts.push(r.ties);
  const extras = [];
  if (r.points != null) extras.push(`${r.points} pts`);
  if (r.games_back != null && r.games_back !== 0) extras.push(`${r.games_back} GB`);
  if (r.division_rank && r.division) extras.push(`${ordinal(r.division_rank)} in ${r.division}`);
  return [parts.join('-'), ...extras].join(' · ');
}

const ordinal = (n) => `${n}${['th', 'st', 'nd', 'rd'][(n % 100 > 10 && n % 100 < 20) ? 0 : Math.min(n % 10, 4) % 4] || 'th'}`;

function nextGameText(ng, today) {
  if (!ng) return '';
  const when = dayLabel(ng.date, today);
  const time = localTime(ng.start_time_utc);
  return `${ng.home ? 'vs' : '@'} ${ng.opponent} · ${when}${time ? ' ' + time : ''}`;
}

function seasonNote(season) {
  if (!season || season.phase === 'regular' || season.phase === 'playoffs') return '';
  const in_ = (n, what) => (n != null && n >= 0 ? `${what} in ${n} day${n === 1 ? '' : 's'}` : '');
  const note = in_(season.days_to_preseason, 'preseason') || in_(season.days_to_regular, 'regular season') || in_(season.days_to_next, 'next game');
  return `${season.phase === 'preseason' ? 'Preseason' : 'Off-season'}${note ? ' · ' + note : ''}`;
}

function SportGames({ s, today }) {
  const teams = Object.entries(s.teams || {});
  const note = seasonNote(s.season);
  return html`<div class="sport">
    <h3>${s.title}${note ? html` <span class="muted small">${note}</span>` : ''}</h3>
    ${teams.length ? html`<div class="teams">${teams.map(([abbrev, t]) => html`<div class="team-line">
      <b>${abbrev}</b> <span class="muted">${recordText(t.record) || 'no record yet'}</span>
      ${t.next_game ? html`<span class="muted"> · next ${nextGameText(t.next_game, today)}</span>` : ''}
    </div>`)}</div>` : ''}
    ${s.days.length ? s.days.map(d => html`<div class="day">
      <div class="daylabel">${dayLabel(d.date, today)} <span class="muted small">${d.games.length} game${d.games.length === 1 ? '' : 's'}</span></div>
      <table class="games"><tbody>${d.games.map(g => html`<${GameRow} g=${g} />`)}</tbody></table>
    </div>`) : html`<p class="muted small">Nothing scheduled in the look-ahead window (Settings → ${s.title} → show games within days).</p>`}
  </div>`;
}

export function GamesCard() {
  const [data, failed] = useDashboard();
  return html`<div class="card"><h2>Games</h2>
    ${failed ? html`<p class="error">Could not reach the scoreboard.</p>`
      : !data ? html`<p class="muted">Loading…</p>`
      : !data.sports.length ? html`<p class="muted">No sports sources are enabled.</p>`
      : data.sports.map(s => html`<${SportGames} s=${s} today=${data.today} />`)}
  </div>`;
}

// -- around you: weather, planes, holidays -------------------------------------------------

const alt = (ft) => (ft == null ? 'ground' : `${ft.toLocaleString()} ft`);

const seenText = (n) => (n == null ? '' : n === 1 ? 'first visit' : `seen ${n}×`);
const sinceText = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '');
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

function FlightStats({ stats }) {
  if (!stats || !stats.airframes) return html`<p class="muted small">No visits logged yet. Every airframe that comes into range is counted from now on.</p>`;
  const regulars = (stats.regulars || []).filter(r => r.count > 1);
  return html`<div class="flight-stats">
    <div>${plural(stats.airframes, 'airframe')} · ${plural(stats.sightings, 'visit')} · ${stats.today} today
      ${stats.since ? html`<span class="muted"> · since ${sinceText(stats.since)}</span>` : ''}</div>
    ${regulars.length ? html`<div class="muted small">Regulars: ${regulars.map(r => `${r.registration || r.hex}${r.type ? ' ' + r.type : ''} ×${r.count}`).join(', ')}</div>` : ''}
  </div>`;
}

function Flights({ rows }) {
  if (!rows.length) return html`<p class="muted small">No aircraft nearby right now.</p>`;
  return html`<table class="games flights"><tbody>${rows.map(a => html`<tr class=${a.overhead ? 'main' : ''}>
    <td><b>${a.ident || a.callsign || a.hex}</b>${a.overhead ? html` <span class="chip">overhead</span>` : ''}
      ${a.sightings != null ? html`<div class="muted small" title=${a.first_seen ? 'first seen ' + sinceText(a.first_seen) : ''}>${seenText(a.sightings)}</div>` : ''}</td>
    <td class="muted">${a.airline || a.type_name || a.type || ''}</td>
    <td>${a.route || [a.origin, a.destination].filter(Boolean).join('→') || ''}</td>
    <td class="state">${a.on_ground ? 'on ground' : alt(a.altitude_ft)}${a.distance_km != null ? ` · ${a.distance_km} km ${a.bearing_compass || ''}` : ''}</td>
  </tr>`)}</tbody></table>`;
}

const timestampText = (ts) => ts == null ? '—' : new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

export function AirframesPage() {
  const [rows, setRows] = useState(null);
  const [failed, setFailed] = useState(false);
  const [history, setHistory] = useState(null);
  const [historyFailed, setHistoryFailed] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('recent');
  const [view, setView] = useState('airframes');
  const [filterBy, setFilterBy] = useState(null);
  useEffect(() => {
    const tick = () => fetch('/api/flights/airframes').then(r => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    }).then(data => { setRows(data); setFailed(false); }).catch(() => setFailed(true));
    tick(); const id = setInterval(tick, 30000); return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const tick = () => fetch('/api/flights/history').then(r => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    }).then(data => { setHistory(data); setHistoryFailed(false); }).catch(() => setHistoryFailed(true));
    tick(); const id = setInterval(tick, 60000); return () => clearInterval(id);
  }, []);
  const needle = query.trim().toLowerCase();
  const visible = (rows || []).filter(a => filterBy
    ? (filterBy.field === 'type' ? (a.type || 'Unknown') === filterBy.value : (a.operator || 'Unknown') === filterBy.value)
    : !needle || (needle === 'unknown' ? !a.type
      : [a.hex, a.registration, a.type, a.operator].some(v => (v || '').toLowerCase().includes(needle))));
  visible.sort((a, b) => sort === 'count' ? b.count - a.count || b.last_seen - a.last_seen
    : sort === 'registration' ? (a.registration || a.hex).localeCompare(b.registration || b.hex)
      : b.last_seen - a.last_seen || b.count - a.count);
  const types = Object.values((rows || []).reduce((groups, a) => {
    const type = a.type || 'Unknown';
    const group = groups[type] || (groups[type] = { type, airframes: 0, sightings: 0, last_seen: 0 });
    group.airframes += 1;
    group.sightings += a.count;
    group.last_seen = Math.max(group.last_seen, a.last_seen || 0);
    return groups;
  }, {})).sort((a, b) => b.airframes - a.airframes || b.sightings - a.sightings || a.type.localeCompare(b.type));
  const operators = Object.values((rows || []).reduce((groups, a) => {
    const operator = a.operator || 'Unknown';
    const group = groups[operator] || (groups[operator] = { operator, airframes: 0, sightings: 0, last_seen: 0 });
    group.airframes += 1;
    group.sightings += a.count;
    group.last_seen = Math.max(group.last_seen, a.last_seen || 0);
    return groups;
  }, {})).sort((a, b) => b.airframes - a.airframes || b.sightings - a.sightings || a.operator.localeCompare(b.operator));
  return html`<div class="card airframes">
    <div class="airframes-head"><div><h2>Airframes</h2>
      <p class="muted small">${rows ? `${rows.length.toLocaleString()} recorded` : 'Loading sightings…'}</p></div>
      <div class="airframes-tools"><div class="segmented" role="group" aria-label="Airframe view">
        <button class=${view === 'airframes' ? 'active' : ''} aria-pressed=${view === 'airframes'} onclick=${() => setView('airframes')}>Airframes</button>
        <button class=${view === 'types' ? 'active' : ''} aria-pressed=${view === 'types'} onclick=${() => setView('types')}>Types</button>
        <button class=${view === 'operators' ? 'active' : ''} aria-pressed=${view === 'operators'} onclick=${() => setView('operators')}>Operators</button>
        <button class=${view === 'activity' ? 'active' : ''} aria-pressed=${view === 'activity'} onclick=${() => setView('activity')}>Activity</button>
      </div>${view === 'airframes' ? html`<input type="search" placeholder="Search registration, hex, type or operator" value=${query} oninput=${e => setQuery(e.target.value)} />
        <select aria-label="Sort airframes" value=${sort} onchange=${e => setSort(e.target.value)}>
          <option value="recent">Most recent</option><option value="count">Most sightings</option><option value="registration">Registration</option>
        </select>` : ''}</div></div>
    ${failed ? html`<p class="error">Could not load the airframe log.</p>`
      : rows === null ? html`<p class="muted">Loading…</p>`
      : !rows.length ? html`<p class="muted">No airframes recorded yet. Enable flight sighting counts in Settings to start an inventory.</p>`
      : view === 'activity' ? historyFailed ? html`<p class="error">Could not load flight history.</p>`
        : history === null ? html`<p class="muted">Loading flight history…</p>` : html`<${ActivityView} data=${history} />`
      : view === 'types' ? html`<div class="tablewrap"><table class="airframe-table type-table"><thead><tr>
          <th>Type</th><th>Airframes</th><th>Total sightings</th><th>Most recent</th>
        </tr></thead><tbody>${types.map(t => html`<tr>
          <td><button class="type-link" onclick=${() => { setQuery(''); setFilterBy({ field: 'type', value: t.type }); setView('airframes'); }}>${t.type}</button></td>
          <td>${t.airframes.toLocaleString()}</td><td>${t.sightings.toLocaleString()}</td><td>${timestampText(t.last_seen)}</td>
        </tr>`)}</tbody></table></div>`
      : view === 'operators' ? html`<div class="tablewrap"><table class="airframe-table type-table"><thead><tr>
          <th>Operator / airline</th><th>Airframes</th><th>Total sightings</th><th>Most recent</th>
        </tr></thead><tbody>${operators.map(o => html`<tr>
          <td><button class="type-link" onclick=${() => { setQuery(''); setFilterBy({ field: 'operator', value: o.operator }); setView('airframes'); }}>${o.operator}</button></td>
          <td>${o.airframes.toLocaleString()}</td><td>${o.sightings.toLocaleString()}</td><td>${timestampText(o.last_seen)}</td>
        </tr>`)}</tbody></table></div>`
      : !visible.length ? html`<p class="muted">No airframes match “${query}”.</p>`
      : html`<div>${filterBy ? html`<p class="muted small">Filtered by ${filterBy.field}: ${filterBy.value} <button class="clear-filter" onclick=${() => setFilterBy(null)}>Clear</button></p>` : ''}
        <div class="airframes-tools"><input type="search" placeholder="Search registration, hex, type or operator" value=${query} oninput=${e => { setQuery(e.target.value); setFilterBy(null); }} />
          <select aria-label="Sort airframes" value=${sort} onchange=${e => setSort(e.target.value)}>
            <option value="recent">Most recent</option><option value="count">Most sightings</option><option value="registration">Registration</option>
          </select></div>
        <div class="tablewrap"><table class="airframe-table"><thead><tr>
          <th>Airframe</th><th>Type</th><th>Airline / operator</th><th>Sightings</th><th>First seen</th><th>Most recent</th>
        </tr></thead><tbody>${visible.map(a => html`<tr>
          <td><b>${a.registration || a.hex}</b><div class="muted small">ICAO ${a.hex}</div></td>
          <td>${a.type || '—'}</td><td>${a.operator || '—'}</td><td>${a.count.toLocaleString()}</td>
          <td>${timestampText(a.first_seen)}</td><td>${timestampText(a.last_seen)}</td>
        </tr>`)}</tbody></table></div></div>`}
  </div>`;
}

function ActivityView({ data }) {
  const maxDay = Math.max(1, ...(data.days || []).map(d => d.visits));
  const busyHours = (data.hours || []).filter(h => h.visits).sort((a, b) => b.visits - a.visits || a.hour - b.hour).slice(0, 8);
  const maxHour = Math.max(1, ...busyHours.map(h => h.visits));
  return html`<div class="activity-view">
    <div class="activity-summary"><div><b>${data.last_7_days || 0}</b><span>visits · 7 days</span></div>
      <div><b>${data.last_30_days || 0}</b><span>visits · 30 days</span></div>
      <div><b>${data.visits || 0}</b><span>visits · retained history</span></div></div>
    ${data.truncated ? html`<p class="muted small">Hourly and route rankings use the latest 5,000 visits; history is retained for up to 60 days.</p>` : ''}
    <div class="activity-grid">
      <section class="activity-block"><h3>Daily visits</h3>
        ${data.days && data.days.length ? data.days.map(d => html`<div class="activity-bar-row">
          <span>${d.date}</span><div class="activity-track"><i style=${`width:${Math.max(2, 100 * d.visits / maxDay)}%`}></i></div><b>${d.visits}</b>
        </div>`) : html`<p class="muted small">No visit history yet.</p>`}
      </section>
      <section class="activity-block"><h3>Busiest hours</h3>
        ${busyHours.length ? busyHours.map(h => html`<div class="activity-bar-row">
          <span>${String(h.hour).padStart(2, '0')}:00</span><div class="activity-track"><i style=${`width:${Math.max(2, 100 * h.visits / maxHour)}%`}></i></div><b>${h.visits}</b>
        </div>`) : html`<p class="muted small">No hourly visit history yet.</p>`}
      </section>
      <section class="activity-block"><h3>Common routes</h3>
        ${data.routes && data.routes.length ? html`<table class="airframe-table"><thead><tr><th>Route</th><th>Visits</th></tr></thead><tbody>${data.routes.map(r => html`<tr><td>${r.route}</td><td>${r.visits}</td></tr>`)}</tbody></table>`
          : html`<p class="muted small">No routes recorded yet.</p>`}
      </section>
      <section class="activity-block"><h3>Highest recorded altitude</h3>
        ${data.highest_altitude && data.highest_altitude.length ? html`<table class="airframe-table"><thead><tr><th>Airframe</th><th>Type</th><th>Altitude</th><th>Recorded</th></tr></thead><tbody>
          ${data.highest_altitude.map(a => html`<tr><td>${a.registration || a.hex}</td><td>${a.type || '—'}</td><td>${a.max_altitude_ft.toLocaleString()} ft</td><td>${timestampText(a.max_altitude_at)}</td></tr>`)}
        </tbody></table>` : html`<p class="muted small">No altitude records yet.</p>`}
      </section>
    </div>
  </div>`;
}

function Weather({ w }) {
  const c = w.current || {};
  const unit = (c.units && c.units.temp) ? `°${c.units.temp}` : '°';
  return html`<div class="weather">
    <div class="now"><b>${c.temp != null ? `${c.temp}${unit}` : '—'}</b> <span>${c.desc || c.short || ''}</span>
      ${c.feels != null ? html`<span class="muted small"> · feels ${c.feels}${unit}</span>` : ''}
      ${c.label && c.label !== 'WEATHER' ? html`<span class="muted small"> · ${c.label}</span>` : ''}</div>
    ${w.daily && w.daily.length ? html`<div class="forecast">${w.daily.map(d => html`<div>
      <span class="muted small">${shortDay(d.date).split(' ')[0]}</span><div>${d.hi != null ? d.hi : '—'}/${d.lo != null ? d.lo : '—'}</div><span class="muted small">${d.short || ''}</span>
    </div>`)}</div>` : ''}
  </div>`;
}

const untilText = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }); };

function Alerts({ rows }) {
  if (!rows.length) return html`<p class="muted small">No watches or warnings in effect.</p>`;
  return html`<ul class="alerts">${rows.map(a => html`<li><span class=${`chip level-${a.level}`}>${a.level}</span>
    <div><b>${a.event}</b> <span class="muted">${a.area || ''}${a.expires ? ` · until ${untilText(a.expires)}` : ''}</span>
      ${a.headline ? html`<div class="muted small">${a.headline}</div>` : ''}</div></li>`)}</ul>`;
}

function Holidays({ rows, today }) {
  if (!rows.length) return html`<p class="muted small">No holidays coming up.</p>`;
  return html`<ul class="holidays">${rows.map(h => html`<li><b>${h.display || h.name}</b>
    <span class="muted">${shortDay(h.date)} · ${h.days === 0 ? 'today' : h.days === 1 ? 'tomorrow' : `in ${h.days} days`}</span></li>`)}</ul>`;
}

export function AroundCard() {
  const [data] = useDashboard();
  if (!data) return html`<div class="card"><h2>Around you</h2><p class="muted">Loading…</p></div>`;
  const sections = [];
  if (data.alerts) sections.push(html`<div class="section"><h3>Weather alerts</h3><${Alerts} rows=${data.alerts} /></div>`);
  if (data.weather) sections.push(html`<div class="section"><h3>Weather</h3><${Weather} w=${data.weather} /></div>`);
  if (data.flights) sections.push(html`<div class="section"><h3>Planes nearby <a class="editor-link" href="#airframes">All airframes →</a></h3><${FlightStats} stats=${data.flight_stats} /><${Flights} rows=${data.flights} /></div>`);
  if (data.holidays) sections.push(html`<div class="section"><h3>Holidays</h3><${Holidays} rows=${data.holidays} today=${data.today} /></div>`);
  return html`<div class="card"><h2>Around you</h2>
    ${sections.length ? sections : html`<p class="muted">Turn on weather, weather alerts, flights or holidays in <a href="#settings">Settings</a> to see them here.</p>`}
  </div>`;
}
