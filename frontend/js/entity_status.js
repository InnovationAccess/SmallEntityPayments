/**
 * entity_status.js – Entity Status Analytics tab.
 *
 * Provides three analysis modes:
 *   1. Single patent lookup: entity status timeline with conversion detection
 *   2. Conversion search: find patents that changed from small to large
 *   3. Applicant portfolio: entity status breakdown for one company's patents
 *
 * KPI numbers are clickable — clicking filters the Patent Details table to
 * show only patents associated with those events, and renders inline micro
 * chart sparklines showing each patent's full event history.
 */

import {
  apiGet, apiPost, setLoading, showStatus, escHtml,
  enableTableSorting, stampOriginalOrder, enableAssignmentPopup, addColumnPicker,
} from './app.js';

// ── DOM References ───────────────────────────────────────────────

const patentInput     = document.getElementById('es-patent-input');
const patentBtn       = document.getElementById('es-patent-btn');
const convFromStatus  = document.getElementById('es-from-status');
const convToStatus    = document.getElementById('es-to-status');
const convYearStart   = document.getElementById('es-year-start');
const convYearEnd     = document.getElementById('es-year-end');
const convApplicant   = document.getElementById('es-applicant');
const convLimit       = document.getElementById('es-conv-limit');
const convBtn         = document.getElementById('es-conv-btn');
const appInput        = document.getElementById('es-app-input');
const appBtn          = document.getElementById('es-app-btn');
const convApplicantFind = document.getElementById('es-applicant-find');
const convApplicantSugg = document.getElementById('es-applicant-suggestions');
const appFindBtn      = document.getElementById('es-app-find');
const appSuggestions   = document.getElementById('es-app-suggestions');
const patentArea      = document.getElementById('es-patent-results');
const convArea        = document.getElementById('es-conv-results');
const appArea         = document.getElementById('es-app-results');
const statusMsg       = document.getElementById('es-status');

// ── Micro Chart: Event Classification & Icons ───────────────────

const STATUS_COLORS = { large: '#ef4444', small: '#22c55e', micro: '#3b82f6' };
const GRAY = '#6b7280';

// Prosecution status-change codes (beyond the 3 basic ones)
const _PROS_TO_SMALL = new Set(['SES','SMAL','P013','MP013','MSML','NOSE','MRNSME']);
const _PROS_TO_MICRO = new Set(['MICR','MENC','PMRIA','MPMRIA']);
const _PROS_TO_LARGE = new Set(['BIG.','P014','MP014']);

function classifyEvent(code) {
  if (!code) return 'other';
  if (code.startsWith('M1') || code.startsWith('F17')) return 'large_payment';
  if (code.startsWith('M2') || code.startsWith('F27')) return 'small_payment';
  if (code.startsWith('M3')) return 'micro_payment';
  // Prosecution status transitions (extended set)
  if (_PROS_TO_LARGE.has(code)) return 'decl_big';
  if (_PROS_TO_SMALL.has(code)) return 'decl_smal';
  if (_PROS_TO_MICRO.has(code)) return 'decl_micr';
  if (code === 'STOL') return 'trans_to_large';
  if (code === 'LTOS' || code === 'MTOS') return 'trans_to_small';
  if (code === 'STOM') return 'trans_to_micro';
  if (code === 'EXP.') return 'expired';
  if (code === 'GRNT') return 'grant';
  if (code.startsWith('REM')) return 'reminder';
  if (code === 'ASPN') return 'attorney';
  if (code === 'LITG') return 'litigation';
  if (code === 'PROS_PAY') return 'pros_payment';
  return 'other';
}

/** Returns the entity-status color implied by an event, or null. */
function statusColorForEvent(code) {
  const cat = classifyEvent(code);
  if (cat === 'large_payment' || cat === 'decl_big' || cat === 'trans_to_large') return STATUS_COLORS.large;
  if (cat === 'small_payment' || cat === 'decl_smal' || cat === 'trans_to_small') return STATUS_COLORS.small;
  if (cat === 'micro_payment' || cat === 'decl_micr' || cat === 'trans_to_micro') return STATUS_COLORS.micro;
  return null;
}

/** Infer the initial line color from the first status-implying event. */
function inferInitialColor(events) {
  for (const ev of events) {
    const c = statusColorForEvent(ev.c);
    if (c) return c;
  }
  return '#d1d5db'; // gray fallback
}

// ── SVG Icon Factories (return HTML strings, use currentColor) ──

function svgBuilding() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<rect x="2" y="2" width="10" height="12" rx="1"/>'
    + '<rect x="4" y="4" width="2" height="2" rx=".3" fill="white"/>'
    + '<rect x="8" y="4" width="2" height="2" rx=".3" fill="white"/>'
    + '<rect x="4" y="8" width="2" height="2" rx=".3" fill="white"/>'
    + '<rect x="8" y="8" width="2" height="2" rx=".3" fill="white"/>'
    + '</svg>';
}

function svgHouse() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<polygon points="7,1 1,7 3,7 3,13 11,13 11,7 13,7"/>'
    + '<rect x="5.5" y="9" width="3" height="4" rx=".3" fill="white"/>'
    + '</svg>';
}

function svgPerson() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<circle cx="7" cy="4" r="2.5"/>'
    + '<path d="M3,14 L3,10.5 A4,3.5 0 0 1 11,10.5 L11,14"/>'
    + '</svg>';
}

function svgExpired() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">'
    + '<path d="M3,3 L11,11 M11,3 L3,11"/>'
    + '</svg>';
}

function svgAlarm() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">'
    + '<circle cx="7" cy="7.5" r="4.5"/>'
    + '<path d="M7,5 L7,7.5 L9,8.5"/>'
    + '<path d="M4,1.5 L2,3.5"/><path d="M10,1.5 L12,3.5"/>'
    + '</svg>';
}

function svgBriefcase() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<rect x="1" y="5" width="12" height="8" rx="1.5"/>'
    + '<path d="M5,5 L5,3.5 A1.5,1.5 0 0 1 9,3.5 L9,5" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    + '<line x1="1" y1="9" x2="13" y2="9" stroke="white" stroke-width="1"/>'
    + '</svg>';
}

function svgStar() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<polygon points="7,1 8.8,5.3 13.4,5.8 10,9 11,13.5 7,11.2 3,13.5 4,9 0.6,5.8 5.2,5.3"/>'
    + '</svg>';
}

function svgDollar() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<text x="7" y="12" text-anchor="middle" font-size="12" font-weight="bold" font-family="Arial,sans-serif">$</text>'
    + '</svg>';
}

// ── Single Patent Lookup ─────────────────────────────────────────

patentBtn.addEventListener('click', () => lookupPatent());
patentInput.addEventListener('keydown', e => { if (e.key === 'Enter') lookupPatent(); });

async function lookupPatent() {
  const pn = patentInput.value.trim();
  if (!pn) return;

  setLoading(patentBtn, true);
  patentArea.classList.remove('hidden');
  patentArea.innerHTML = '<p class="text-muted">Loading...</p>';

  try {
    const data = await apiGet(`/api/entity-status/${encodeURIComponent(pn)}`);
    renderPatentTimeline(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    patentArea.innerHTML = '';
    patentArea.classList.add('hidden');
  } finally {
    setLoading(patentBtn, false);
  }
}

function renderPatentTimeline(data) {
  const changedBadge = data.status_changed
    ? `<span class="es-badge es-badge--changed">CONVERTED</span>`
    : `<span class="es-badge es-badge--same">No Change</span>`;

  let html = `
    <div class="card">
      <h3 class="card-title">Entity Status for Patent ${escHtml(data.patent_number)} ${changedBadge}</h3>
      <div class="es-patent-info">
        <div><strong>Title:</strong> ${escHtml(data.invention_title || 'N/A')}</div>
        <div><strong>Applicant:</strong> ${escHtml(data.applicant_name || 'N/A')}</div>
        <div><strong>Filed:</strong> ${escHtml(data.filing_date || 'N/A')} &nbsp; <strong>Granted:</strong> ${escHtml(data.grant_date || 'N/A')}</div>
        <div><strong>First Status:</strong> ${statusBadge(data.filing_entity_status)} &nbsp; <strong>Current:</strong> ${statusBadge(data.current_entity_status)}</div>
        ${data.conversion_date ? `<div><strong>Conversion Date:</strong> ${escHtml(data.conversion_date)}</div>` : ''}
      </div>
  `;

  if (data.timeline.length > 0) {
    html += '<div id="es-single-timeline-wrap" style="margin-top:1rem"></div>';
  } else {
    html += '<p class="text-muted">No maintenance fee events found.</p>';
  }

  html += '</div>';
  patentArea.innerHTML = html;

  // Render the full-width micro chart timeline
  if (data.timeline.length > 0) {
    const wrap = document.getElementById('es-single-timeline-wrap');
    const events = data.timeline.map(ev => ({ d: ev.event_date, c: ev.event_code }));

    // Add grant date as synthetic GRNT event if available
    if (data.grant_date) {
      events.push({ d: data.grant_date, c: 'GRNT' });
      events.sort((a, b) => a.d.localeCompare(b.d));
    }

    const dates = events.map(e => new Date(e.d).getTime());
    const minDate = new Date(Math.min(...dates));
    const maxDate = new Date(Math.max(...dates));
    const totalMs = maxDate.getTime() - minDate.getTime();
    if (totalMs <= 0) return;

    const track = document.createElement('div');
    track.className = 'es-microchart-track es-single-track';

    // ── Build colored status line ──
    const initColor = inferInitialColor(events);
    let currentColor = initColor;
    const changePoints = [];

    let endPct = 100;
    for (const ev of events) {
      if (ev.c === 'EXP.') {
        const expDate = new Date(ev.d);
        endPct = clampPct(((expDate.getTime() - minDate.getTime()) / totalMs) * 100);
        break;
      }
    }

    for (const ev of events) {
      const newColor = statusColorForEvent(ev.c);
      if (newColor && newColor !== currentColor) {
        const evDate = new Date(ev.d);
        const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
        if (pct >= endPct) break;
        changePoints.push({ pct, color: newColor });
        currentColor = newColor;
      }
    }

    let prevPct = 0;
    let lineColor = initColor;
    for (const cp of changePoints) {
      appendLine(track, prevPct, cp.pct - prevPct, lineColor);
      lineColor = cp.color;
      prevPct = cp.pct;
    }
    appendLine(track, prevPct, endPct - prevPct, lineColor);

    // ── Place icons with labels ──
    for (const ev of events) {
      const evDate = new Date(ev.d);
      const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
      const cat = classifyEvent(ev.c);

      let marker = null;
      if (cat === 'large_payment') {
        marker = createIconEl(svgBuilding, STATUS_COLORS.large, pct, ev);
      } else if (cat === 'small_payment') {
        marker = createIconEl(svgHouse, STATUS_COLORS.small, pct, ev);
      } else if (cat === 'micro_payment') {
        marker = createIconEl(svgPerson, STATUS_COLORS.micro, pct, ev);
      } else if (cat === 'expired') {
        marker = createIconEl(svgExpired, GRAY, pct, ev);
      } else if (cat === 'grant') {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#8b5cf6';
        marker.title = `Grant \u2014 ${ev.d}`;
      } else if (cat === 'reminder') {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#eab308';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      } else if (cat === 'attorney') {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#92400e';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      } else if (cat === 'litigation') {
        marker = createIconEl(svgStar, '#d4a017', pct, ev);
        marker.title = `Litigation filed \u2014 ${ev.d}${ev._case ? ' \u2014 ' + ev._case.case_no : ''}`;
      } else if (cat.startsWith('trans_to_')) {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-trans';
        marker.style.left = pct + '%';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      } else if (cat.startsWith('decl_')) {
        continue;
      } else {
        marker = document.createElement('div');
        marker.className = 'es-microchart-other';
        marker.style.left = pct + '%';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      }

      if (marker) {
        // Add date label below the track for this larger view
        const label = document.createElement('div');
        label.className = 'es-single-label';
        label.style.left = pct + '%';
        label.textContent = ev.c;
        track.appendChild(label);

        track.appendChild(marker);
      }
    }

    // ── Date axis: show min and max dates ──
    const axisDiv = document.createElement('div');
    axisDiv.className = 'es-single-axis';
    axisDiv.innerHTML = `<span>${escHtml(minDate.toISOString().slice(0, 10))}</span><span>${escHtml(maxDate.toISOString().slice(0, 10))}</span>`;

    wrap.appendChild(track);
    wrap.appendChild(axisDiv);
  }
}

// ── Boolean Name Search ──────────────────────────────────────────

/** Returns true if the text contains boolean operators (+, -, *). */
function isBooleanQuery(text) {
  return /[+\-*]/.test(text);
}

/**
 * Search for entity names via the MDM boolean search endpoint.
 * Renders matching names as a clickable list below the input.
 */
async function findNames(inputEl, suggestEl, findBtn) {
  const query = inputEl.value.trim();
  if (!query) {
    showStatus(statusMsg, 'Enter a search expression (e.g. +elect* +telecom*)', 'error');
    return;
  }

  setLoading(findBtn, true);
  suggestEl.innerHTML = '';
  suggestEl.classList.add('hidden');

  try {
    const results = await apiPost('/mdm/search', { query });

    if (!results || results.length === 0) {
      suggestEl.innerHTML = '<div class="es-suggestion-header">No matching names found</div>';
      suggestEl.classList.remove('hidden');
      return;
    }

    let html = `<div class="es-suggestion-header">
      <span>${results.length} matching name(s)</span>
      <span>Click to select</span>
    </div>`;

    for (const r of results) {
      const name = r.raw_name || r.name || '';
      const freq = r.frequency || 0;
      const rep = r.representative_name || '';
      const badge = rep
        ? `<span class="es-unified-badge" title="Normalized to: ${escHtml(rep)}">unified</span>`
        : '';
      html += `<div class="es-suggestion-item" data-name="${escHtml(name)}" data-representative="${escHtml(rep)}">
        <span class="es-suggestion-name">${escHtml(name)}${badge}</span>
        <span class="es-suggestion-freq">${freq.toLocaleString()}</span>
      </div>`;
    }

    suggestEl.innerHTML = html;
    suggestEl.classList.remove('hidden');

    suggestEl.querySelectorAll('.es-suggestion-item').forEach(item => {
      item.addEventListener('click', () => {
        const rep = item.dataset.representative;
        inputEl.value = rep || item.dataset.name;
        suggestEl.classList.add('hidden');
        suggestEl.innerHTML = '';
      });
    });
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
  } finally {
    setLoading(findBtn, false);
  }
}

// Wire up Find buttons
convApplicantFind.addEventListener('click', () => {
  findNames(convApplicant, convApplicantSugg, convApplicantFind);
});
convApplicant.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    findNames(convApplicant, convApplicantSugg, convApplicantFind);
  }
});

appFindBtn.addEventListener('click', () => {
  findNames(appInput, appSuggestions, appFindBtn);
});

// ── Conversion Search ────────────────────────────────────────────

convBtn.addEventListener('click', () => searchConversions());

async function searchConversions() {
  convApplicantSugg.classList.add('hidden');
  setLoading(convBtn, true);
  convArea.classList.remove('hidden');
  convArea.innerHTML = '<p class="text-muted">Searching...</p>';

  try {
    const data = await apiPost('/api/entity-status/conversions', {
      from_status: convFromStatus.value,
      to_status: convToStatus.value,
      grant_year_start: parseInt(convYearStart.value) || 2010,
      grant_year_end: parseInt(convYearEnd.value) || 2025,
      applicant_name: convApplicant.value.trim() || null,
      limit: parseInt(convLimit.value) || 200,
    });
    renderConversionResults(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    convArea.innerHTML = '';
    convArea.classList.add('hidden');
  } finally {
    setLoading(convBtn, false);
  }
}

function renderConversionResults(data) {
  const expandedInfo = data.expanded_names && data.expanded_names.length > 1
    ? `<p class="text-muted">Searching ${data.expanded_names.length} name variants</p>` : '';
  let html = `
    <div class="results-header">
      <strong>Conversion Results</strong>
      <span class="results-count">${data.total} patents</span>
    </div>
    ${expandedInfo}
    <div class="table-scroll-wrap">
      <table class="data-table" id="es-conv-table">
        <thead><tr>
          <th data-sort-key="0">Patent #</th>
          <th data-sort-key="1">App #</th>
          <th data-sort-key="2">Grant Date</th>
          <th data-sort-key="3">Applicant</th>
          <th data-sort-key="4">Title</th>
          <th data-sort-key="5">From</th>
          <th data-sort-key="6">To</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    html += `<tr>
      <td class="patent-number">${escHtml(r.patent_number || '')}</td>
      <td>${escHtml(r.application_number || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td>${escHtml(r.applicant_name || '')}</td>
      <td>${escHtml(r.invention_title || '')}</td>
      <td>${statusBadge(r.first_status)}</td>
      <td>${statusBadge(r.last_status)}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  convArea.innerHTML = html;

  const tbl = document.getElementById('es-conv-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    enableAssignmentPopup('#es-conv-table .patent-number');
    addColumnPicker(tbl);
  }
}

// ── Applicant Portfolio ──────────────────────────────────────────

appBtn.addEventListener('click', () => loadApplicantPortfolio());
appInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    if (isBooleanQuery(appInput.value)) {
      e.preventDefault();
      findNames(appInput, appSuggestions, appFindBtn);
    } else {
      loadApplicantPortfolio();
    }
  }
});

async function loadApplicantPortfolio() {
  appSuggestions.classList.add('hidden');
  const name = appInput.value.trim();
  if (!name) return;

  setLoading(appBtn, true);
  appArea.classList.remove('hidden');
  appArea.innerHTML = '<p class="text-muted">Loading portfolio — searching all applicants, inventors, and assignees...</p>';

  try {
    const data = await apiPost('/api/entity-status/by-applicant', {
      applicant_name: name,
      limit: 50000,
    });
    renderApplicantPortfolio(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    appArea.innerHTML = '';
    appArea.classList.add('hidden');
  } finally {
    setLoading(appBtn, false);
  }
}

function renderApplicantPortfolio(data) {
  const pros = data.prosecution || {};
  const pg = data.post_grant || {};
  const pay = pg.payments || {m1551:0,m1552:0,m1553:0,m2551:0,m2552:0,m2553:0,m3551:0,m3552:0,m3553:0};
  const pf = data.portfolio || {
    granted: {filed:0, acquired:0, divested:0, expired:0, owned:0},
    pending: {filed:0, acquired:0, divested:0, owned:0},
  };

  // Row & column totals for payment table
  const row35 = pay.m3551 + pay.m2551 + pay.m1551;
  const row75 = pay.m3552 + pay.m2552 + pay.m1552;
  const row115 = pay.m3553 + pay.m2553 + pay.m1553;
  const colMicro = pay.m3551 + pay.m3552 + pay.m3553;
  const colSmall = pay.m2551 + pay.m2552 + pay.m2553;
  const colLarge = pay.m1551 + pay.m1552 + pay.m1553;
  const grandTotal = colMicro + colSmall + colLarge;

  /** Wrap a non-zero value in a clickable span with filter metadata. */
  function kpi(val, filterSpec, label) {
    if (!val) return '0';
    return `<span class="kpi-clickable" data-filter="${escHtml(filterSpec)}" data-label="${escHtml(label)}">${val.toLocaleString()}</span>`;
  }

  let html = `
    <div class="card">
      <h3 class="card-title">Portfolio for ${escHtml(data.applicant_name)}</h3>
      ${data.expanded_names.length > 1
        ? `<p class="text-muted">Including ${data.expanded_names.length} name variants (applicants, inventors, and assignees)</p>`
        : ''}

      <!-- Granted Patents KPIs -->
      <h4 style="margin:0.25rem 0 0.5rem;font-size:0.95rem;color:#374151">Granted Patents</h4>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.filed, 'portfolio:filed_granted', 'Filed Patents')}</span>
          <span class="cite-stat-label">Filed</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.acquired, 'portfolio:acquired_granted', 'Acquired Patents')}</span>
          <span class="cite-stat-label">Acquired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.divested, 'portfolio:divested_granted', 'Divested Patents')}</span>
          <span class="cite-stat-label">Divested</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.expired, 'portfolio:expired_granted', 'Expired Patents')}</span>
          <span class="cite-stat-label">Expired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value" style="font-weight:700">${pf.granted.owned.toLocaleString()}</span>
          <span class="cite-stat-label" style="font-weight:600">Owned</span>
        </div>
      </div>

      <!-- Pending Applications KPIs -->
      <h4 style="margin:1rem 0 0.5rem;font-size:0.95rem;color:#374151">Pending Applications</h4>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.pending.filed, 'portfolio:filed_pending', 'Filed Applications')}</span>
          <span class="cite-stat-label">Filed</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.pending.acquired, 'portfolio:acquired_pending', 'Acquired Applications')}</span>
          <span class="cite-stat-label">Acquired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.pending.divested, 'portfolio:divested_pending', 'Divested Applications')}</span>
          <span class="cite-stat-label">Divested</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">\u2014</span>
          <span class="cite-stat-label">Expired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value" style="font-weight:700">${pf.pending.owned.toLocaleString()}</span>
          <span class="cite-stat-label" style="font-weight:600">Owned</span>
        </div>
      </div>
      <p class="text-muted" style="margin:0.5rem 0 0;font-size:0.8rem">Owned = Filed + Acquired \u2212 Divested \u2212 Expired &nbsp;|&nbsp; KPIs reflect only events during the entity's ownership period</p>

      <!-- Litigation KPI (populated asynchronously) -->
      <div id="es-litigation-kpis" style="display:none;margin-top:1rem">
        <h4 class="card-title" style="font-size:1rem">Litigation History</h4>
        <div class="cite-summary-grid">
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-litigated-count">\u2026</span>
            <span class="cite-stat-label">Litigated Patents</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-total-cases">\u2026</span>
            <span class="cite-stat-label">Total Cases</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-active-cases">\u2026</span>
            <span class="cite-stat-label">Active</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-resolved-cases">\u2026</span>
            <span class="cite-stat-label">Resolved</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-courts-count">\u2026</span>
            <span class="cite-stat-label">Courts</span>
          </div>
        </div>
        <p class="text-muted" id="es-litigation-status" style="margin:0.25rem 0 0;font-size:0.8rem">Checking litigation history\u2026</p>
      </div>
      <div id="es-litigation-table-wrap" style="display:none;margin-top:1rem"></div>
    </div>

    <!-- Prosecution Payment Analysis -->
    <div id="es-pros-payments" style="margin-top:1rem">
      <div class="card">
        <h4 class="card-title" style="font-size:1rem">Prosecution Payment Analysis</h4>
        <p class="text-muted" style="margin:0 0 0.5rem">Identifies all fee payments made during prosecution and classifies them by the entity status at the time of payment</p>
        <button class="btn btn-primary" id="es-pros-pay-btn" style="margin-bottom:0.5rem">Analyze Prosecution Payments</button>
        <div id="es-pros-pay-kpis" style="display:none">
          <div class="cite-summary-grid">
            <div class="cite-stat kpi-clickable" data-filter="prospay:SMALL" data-label="Prosecution: Small Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-small">0</span>
              <span class="cite-stat-label">${statusBadge('SMALL')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:MICRO" data-label="Prosecution: Micro Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-micro">0</span>
              <span class="cite-stat-label">${statusBadge('MICRO')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:LARGE" data-label="Prosecution: Large Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-large">0</span>
              <span class="cite-stat-label">${statusBadge('LARGE')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:SMALL,MICRO,LARGE" data-label="Prosecution: All Payments">
              <span class="cite-stat-value" id="es-pros-pay-total">0</span>
              <span class="cite-stat-label">Total</span>
            </div>
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-apps">0</span>
              <span class="cite-stat-label">Apps w/ Findings</span>
            </div>
          </div>
        </div>
        <p class="text-muted" id="es-pros-pay-status" style="margin:0.25rem 0 0;font-size:0.8rem"></p>
      </div>
      <div id="es-pros-summary-wrap" style="display:none;margin-top:1rem"></div>
      <div id="es-pros-detail-wrap" style="display:none;margin-top:1rem"></div>
    </div>

    <!-- Prosecution Phase -->
    <div class="card" style="margin-top:1rem">
      <h4 class="card-title" style="font-size:1rem">Prosecution Phase — Entity Declarations</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">From prosecution transaction codes (SMAL, BIG., MICR)</p>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.small, 'pros:SMALL', 'Prosecution: Small')}</span>
          <span class="cite-stat-label">${statusBadge('SMALL')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.micro, 'pros:MICRO', 'Prosecution: Micro')}</span>
          <span class="cite-stat-label">${statusBadge('MICRO')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.large, 'pros:LARGE', 'Prosecution: Large')}</span>
          <span class="cite-stat-label">${statusBadge('LARGE')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.total, 'pros:SMALL,MICRO,LARGE', 'Prosecution: All')}</span>
          <span class="cite-stat-label">Total</span>
        </div>
      </div>
      <p class="text-muted" style="margin:0.5rem 0 0.25rem;font-size:0.8rem">Past 10 years only</p>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.small_10y || 0, 'pros10y:SMALL', 'Prosecution 10y: Small')}</span>
          <span class="cite-stat-label">${statusBadge('SMALL')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.micro_10y || 0, 'pros10y:MICRO', 'Prosecution 10y: Micro')}</span>
          <span class="cite-stat-label">${statusBadge('MICRO')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.large_10y || 0, 'pros10y:LARGE', 'Prosecution 10y: Large')}</span>
          <span class="cite-stat-label">${statusBadge('LARGE')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.total_10y || 0, 'pros10y:SMALL,MICRO,LARGE', 'Prosecution 10y: All')}</span>
          <span class="cite-stat-label">Total</span>
        </div>
      </div>
    </div>

    <!-- Post-Grant Phase -->
    <div class="card" style="margin-top:1rem">
      <h4 class="card-title" style="font-size:1rem">Post-Grant Phase — Maintenance Fees</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">From maintenance fee payments, declarations, and transitions</p>
      <div style="display:flex;gap:2rem;flex-wrap:wrap">
        <!-- Payments -->
        <div style="flex:1;min-width:340px">
          <div style="color:#c0392b;font-weight:600;margin-bottom:0.5rem">Payments:</div>
          <table class="data-table" style="font-size:0.85rem">
            <thead><tr>
              <th></th>
              <th style="text-align:center">${statusBadge('MICRO')}</th>
              <th style="text-align:center">${statusBadge('SMALL')}</th>
              <th style="text-align:center">${statusBadge('LARGE')}</th>
              <th style="text-align:center"><strong>Total</strong></th>
              <th style="text-align:center;font-size:0.75rem;line-height:1.1" title="37 CFR 1.28(c) — good-faith error corrections (M1559)">37 CFR<br>1.28(c)</th>
            </tr></thead>
            <tbody>
              <tr>
                <td style="font-weight:600">3.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3551, 'mf:M3551', 'Micro 3.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2551, 'mf:M2551', 'Small 3.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1551, 'mf:M1551', 'Large 3.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row35, 'mf:M3551,M2551,M1551', '3.5-yr Total')}</td>
                <td></td>
              </tr>
              <tr>
                <td style="font-weight:600">7.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3552, 'mf:M3552', 'Micro 7.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2552, 'mf:M2552', 'Small 7.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1552, 'mf:M1552', 'Large 7.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row75, 'mf:M3552,M2552,M1552', '7.5-yr Total')}</td>
                <td></td>
              </tr>
              <tr>
                <td style="font-weight:600">11.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3553, 'mf:M3553', 'Micro 11.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2553, 'mf:M2553', 'Small 11.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1553, 'mf:M1553', 'Large 11.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row115, 'mf:M3553,M2553,M1553', '11.5-yr Total')}</td>
                <td></td>
              </tr>
            </tbody>
            <tfoot>
              <tr style="border-top:2px solid var(--color-border)">
                <td style="font-weight:600">Total</td>
                <td style="text-align:right;font-weight:600">${kpi(colMicro, 'mf:M3551,M3552,M3553', 'Micro Total')}</td>
                <td style="text-align:right;font-weight:600">${kpi(colSmall, 'mf:M2551,M2552,M2553', 'Small Total')}</td>
                <td style="text-align:right;font-weight:600">${kpi(colLarge, 'mf:M1551,M1552,M1553', 'Large Total')}</td>
                <td style="text-align:right;font-weight:700">${kpi(grandTotal, 'mf:M3551,M3552,M3553,M2551,M2552,M2553,M1551,M1552,M1553', 'All Payments')}</td>
                <td style="text-align:right;font-weight:600">${kpi(pay.m1559 || 0, 'mf:M1559', '37 CFR 1.28(c)')}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <!-- Declarations -->
        <div style="flex:1;min-width:250px">
          <div style="color:#c0392b;font-weight:600;margin-bottom:0.5rem">Declarations:</div>
          <div style="display:flex;gap:2rem;flex-wrap:wrap">
            <div>
              <div style="font-weight:600;margin-bottom:0.25rem">Transitions</div>
              <div style="font-size:0.85rem;line-height:1.8">
                Micro &gt; Small: ${kpi(pg.mtos || 0, 'mf:MTOS', 'Micro \u2192 Small')}<br>
                Small &gt; Micro: ${kpi(pg.stom, 'mf:STOM', 'Small \u2192 Micro')}<br>
                Small &gt; Large: ${kpi(pg.stol, 'mf:STOL', 'Small \u2192 Large')}<br>
                Large &gt; Small: ${kpi(pg.ltos, 'mf:LTOS', 'Large \u2192 Small')}
              </div>
            </div>
            <div>
              <div style="font-weight:600;margin-bottom:0.25rem">Status</div>
              <div style="font-size:0.85rem;line-height:1.8">
                ${statusBadge('MICRO')}: ${kpi(pg.decl_micr || 0, 'mf:MICR', 'Declaration: Micro')}<br>
                ${statusBadge('SMALL')}: ${kpi(pg.decl_smal || 0, 'mf:SMAL', 'Declaration: Small')}<br>
                ${statusBadge('LARGE')}: ${kpi(pg.decl_big || 0, 'mf:BIG.', 'Declaration: Large')}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Filter label (shown when a KPI is clicked) -->
    <div id="es-filter-label" class="es-filter-label hidden"></div>

    <!-- Micro chart legend (shown when timelines are loaded) -->
    <div id="es-microchart-legend" class="es-microchart-legend hidden"></div>

    <div class="results-header" style="margin-top:1rem">
      <strong>Patent Details</strong>
      <span id="es-shown-count" class="results-count">${data.results.length.toLocaleString()} shown</span>
    </div>
    <div class="table-scroll-wrap">
      <table class="data-table" id="es-app-table">
        <thead><tr>
          <th data-sort-key="0">Patent #</th>
          <th data-sort-key="1">App #</th>
          <th data-sort-key="2">Grant Date</th>
          <th class="es-events-hdr">Events</th>
          <th data-sort-key="4">Prosecution</th>
          <th data-sort-key="5">Post-Grant First</th>
          <th data-sort-key="6">Post-Grant Current</th>
          <th data-sort-key="7">Changed?</th>
          <th data-sort-key="8">Ownership</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    const changedMark = r.status_changed
      ? `<span class="es-badge es-badge--changed">${r.change_phase === 'prosecution' ? 'Pros' : 'PG'}</span>`
      : '';
    // Ownership badge
    let ownershipBadge;
    if (r.divested) {
      ownershipBadge = `<span class="es-badge es-badge--divested" title="Divested ${r.divested_date || ''}">Divested${r.divested_date ? ' ' + r.divested_date.slice(0, 10) : ''}</span>`;
    } else if (r.acquired_via_assignment) {
      ownershipBadge = `<span class="es-badge es-badge--acquired" title="Acquired via assignment ${r.acquired_date || ''}">Acquired${r.acquired_date ? ' ' + r.acquired_date.slice(0, 10) : ''}</span>`;
    } else {
      ownershipBadge = `<span class="es-badge es-badge--owned">Owned</span>`;
    }
    const rowStyle = r.divested ? ' style="opacity:0.6"' : '';
    html += `<tr${rowStyle} data-pn="${escHtml(r.patent_number || '')}" data-app="${escHtml(r.application_number || '')}" data-pros="${r.prosecution_status || ''}" data-pros10y="${r.prosecution_status_10y || ''}" data-pgfirst="${r.post_grant_first || ''}" data-pgcurrent="${r.post_grant_current || ''}" data-mf="${escHtml(r.mf_events || '')}" data-changed="${r.status_changed ? '1' : ''}" data-divested="${r.divested ? '1' : ''}" data-acquired="${r.acquired_via_assignment ? '1' : ''}" data-expired="${r.expired ? '1' : ''}">
      <td class="patent-number">${escHtml(r.patent_number || '')}</td>
      <td>${escHtml(r.application_number || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td class="es-events-cell"></td>
      <td>${statusBadge(r.prosecution_status)}</td>
      <td>${statusBadge(r.post_grant_first)}</td>
      <td>${statusBadge(r.post_grant_current)}</td>
      <td>${changedMark}</td>
      <td>${ownershipBadge}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  appArea.innerHTML = html;

  const tbl = document.getElementById('es-app-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    enableAssignmentPopup('#es-app-table .patent-number');
    addColumnPicker(tbl);
  }

  // Wire clickable KPIs
  appArea.querySelectorAll('.kpi-clickable').forEach(el => {
    el.addEventListener('click', () => {
      filterPatentTable(el.dataset.filter, el.dataset.label, el);
    });
  });

  // Fire async litigation lookup for all granted patents
  const grantedPatents = data.results
    .filter(r => r.patent_number)
    .map(r => r.patent_number);
  if (grantedPatents.length > 0) {
    fetchLitigationData(grantedPatents);
  }

  // Wire prosecution payment analysis button
  const prosPayBtn = document.getElementById('es-pros-pay-btn');
  if (prosPayBtn) {
    prosPayBtn.addEventListener('click', () => fetchProsecutionPayments());
  }
}

// ── Litigation Data (async, non-blocking) ──────────────────────

async function fetchLitigationData(patentNumbers) {
  const kpiSection = document.getElementById('es-litigation-kpis');
  const statusEl = document.getElementById('es-litigation-status');
  if (!kpiSection) return;
  kpiSection.style.display = '';

  try {
    const data = await apiPost('/api/litigation/lookup', {
      patent_numbers: patentNumbers,
    });

    // Store for micro chart injection + litigation table
    window._litigationData = data.litigated_patents || {};
    window._litigationCases = data.cases || [];

    const cases = window._litigationCases;
    const litigatedCount = data.litigated_count || 0;
    const totalCases = data.total_cases || 0;
    const activeCases = cases.filter(c => _isActiveCase(c)).length;
    const resolvedCases = totalCases - activeCases;
    const uniqueCourts = new Set(cases.map(c => c.court).filter(Boolean)).size;

    // Populate KPI values with clickable spans
    const litKpi = (id, val, filter, label) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = val
        ? `<span class="kpi-clickable lit-kpi" data-litfilter="${filter}" data-label="${label}">${val.toLocaleString()}</span>`
        : '0';
    };
    litKpi('es-litigated-count', litigatedCount, 'all', 'Litigated Patents');
    litKpi('es-total-cases', totalCases, 'all', 'All Litigation Cases');
    litKpi('es-active-cases', activeCases, 'active', 'Active Cases');
    litKpi('es-resolved-cases', resolvedCases, 'resolved', 'Resolved Cases');
    litKpi('es-courts-count', uniqueCourts, 'all', 'All Litigation Cases');

    statusEl.textContent = `${data.from_cache || 0} from cache, ${data.freshly_queried || 0} freshly queried`;

    // Set data-litigated on table rows
    const tbl = document.getElementById('es-app-table');
    if (tbl) {
      tbl.querySelectorAll('tbody tr').forEach(row => {
        const pn = row.dataset.pn;
        if (pn && window._litigationData[pn]) {
          row.dataset.litigated = '1';
        }
      });
    }

    // Wire litigation KPI clicks
    kpiSection.querySelectorAll('.lit-kpi').forEach(el => {
      el.addEventListener('click', () => {
        const filter = el.dataset.litfilter;
        const label = el.dataset.label;

        // Toggle off if same KPI clicked again
        if (el.classList.contains('kpi-active')) {
          el.classList.remove('kpi-active');
          hideLitigationTable();
          // Reset patent table if "Litigated Patents" was active
          const tbl = document.getElementById('es-app-table');
          if (tbl) {
            const rows = tbl.querySelectorAll('tbody tr');
            rows.forEach(r => { r.style.display = ''; });
            const filterLabel = document.getElementById('es-filter-label');
            const shownCount = document.getElementById('es-shown-count');
            if (filterLabel) filterLabel.classList.add('hidden');
            if (shownCount) shownCount.textContent = `${rows.length.toLocaleString()} shown`;
          }
          clearMicroCharts();
          return;
        }

        // Clear previous active highlights (both patent KPIs and lit KPIs)
        appArea.querySelectorAll('.kpi-active').forEach(a => a.classList.remove('kpi-active'));
        el.classList.add('kpi-active');

        // Show litigation table with appropriate filter
        if (filter === 'active') {
          renderLitigationTable(c => _isActiveCase(c), label);
        } else if (filter === 'resolved') {
          renderLitigationTable(c => !_isActiveCase(c), label);
        } else {
          renderLitigationTable(null, label);
        }

        // If "Litigated Patents" KPI, also filter the patent table
        if (el.closest('#es-litigated-count')) {
          _filterPatentTableForLitigation();
        }
      });
    });
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
}

/** Check if a case is active (not closed/terminated). */
function _isActiveCase(c) {
  const s = (c.status || '').toLowerCase();
  return s !== 'closed' && s !== 'terminated' && s !== 'resolved' && s !== 'settled';
}

/** Filter patent table to show only litigated patents. */
function _filterPatentTableForLitigation() {
  const tbl = document.getElementById('es-app-table');
  if (!tbl) return;
  const rows = tbl.querySelectorAll('tbody tr');
  let shown = 0;
  const visiblePatents = [];
  rows.forEach(row => {
    const match = row.dataset.litigated === '1';
    row.style.display = match ? '' : 'none';
    if (match) {
      shown++;
      const pn = row.dataset.pn;
      if (pn) visiblePatents.push(pn);
    }
  });
  const filterLabel = document.getElementById('es-filter-label');
  const shownCount = document.getElementById('es-shown-count');
  if (filterLabel) {
    filterLabel.innerHTML = `Filtered: <strong>Litigated Patents</strong> &mdash; ${shown.toLocaleString()} of ${rows.length.toLocaleString()} patents <button class="es-filter-clear" title="Clear filter">&times;</button>`;
    filterLabel.classList.remove('hidden');
    filterLabel.querySelector('.es-filter-clear')?.addEventListener('click', () => {
      appArea.querySelectorAll('.kpi-active').forEach(a => a.classList.remove('kpi-active'));
      rows.forEach(r => { r.style.display = ''; });
      filterLabel.classList.add('hidden');
      if (shownCount) shownCount.textContent = `${rows.length.toLocaleString()} shown`;
      hideLitigationTable();
      clearMicroCharts();
    });
  }
  if (shownCount) shownCount.textContent = `${shown.toLocaleString()} shown`;

  // Fetch and render micro charts for visible litigated patents
  if (visiblePatents.length > 0) {
    fetchAndRenderMicroCharts(visiblePatents, 'litigation:litigated');
  } else {
    clearMicroCharts();
  }
}

/** Render the litigation cases table. */
function renderLitigationTable(filterFn, title) {
  const wrap = document.getElementById('es-litigation-table-wrap');
  if (!wrap) return;

  const cases = window._litigationCases || [];
  const filtered = filterFn ? cases.filter(filterFn) : cases;

  let html = `<h4 style="font-size:1rem;margin-bottom:0.5rem">${escHtml(title || 'Litigation Cases')} (${filtered.length})</h4>`;
  html += '<div class="table-scroll-wrap"><table id="es-litigation-table" class="data-table"><thead><tr>';

  const cols = [
    { key: 'case_no', label: 'Case' },
    { key: 'filed_date', label: 'Filing Date' },
    { key: 'status', label: 'Status' },
    { key: 'court', label: 'Court' },
    { key: 'plaintiff', label: 'Plaintiff' },
    { key: 'defendant', label: 'Defendant' },
    { key: 'cause_of_action', label: 'Cause of Action' },
    { key: 'entity_type', label: 'Plaintiff Entity Type' },
    { key: 'industry', label: 'Industry' },
    { key: 'flag', label: 'Source' },
    { key: 'portfolio_patents', label: 'Patents in Case' },
    { key: 'judge', label: 'Judge' },
    { key: 'closed_date', label: 'Termination Date' },
    { key: 'product', label: 'Infringed Product' },
  ];
  const defaultHidden = ['Cause of Action', 'Plaintiff Entity Type', 'Industry', 'Source', 'Patents in Case', 'Judge', 'Termination Date', 'Infringed Product'];

  for (const col of cols) {
    html += `<th>${escHtml(col.label)}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const c of filtered) {
    html += '<tr>';
    for (const col of cols) {
      let val = c[col.key] || '';
      if (col.key === 'portfolio_patents' && Array.isArray(val)) {
        val = val.join(', ');
      }
      html += `<td>${escHtml(String(val))}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  wrap.innerHTML = html;
  wrap.style.display = '';

  const litTbl = document.getElementById('es-litigation-table');
  if (litTbl) {
    stampOriginalOrder(litTbl);
    enableTableSorting(litTbl);
    addColumnPicker(litTbl, { defaultHidden });
  }
}

/** Hide the litigation table. */
function hideLitigationTable() {
  const wrap = document.getElementById('es-litigation-table-wrap');
  if (wrap) {
    wrap.innerHTML = '';
    wrap.style.display = 'none';
  }
}

// ── Prosecution Payment Analysis ─────────────────────────────────

/**
 * Fetch prosecution payment data for all applications in the table.
 * Builds status segments + payment events, populates KPIs and tables.
 */
async function fetchProsecutionPayments() {
  const tbl = document.getElementById('es-app-table');
  const statusEl = document.getElementById('es-pros-pay-status');
  const kpisEl = document.getElementById('es-pros-pay-kpis');
  const btn = document.getElementById('es-pros-pay-btn');
  if (!tbl) return;

  // Collect all application numbers from table rows
  const allApps = [];
  tbl.querySelectorAll('tbody tr').forEach(row => {
    const app = row.dataset.app;
    if (app) allApps.push(app);
  });

  if (allApps.length === 0) {
    if (statusEl) statusEl.textContent = 'No applications found in table.';
    return;
  }

  // Disable button during fetch
  if (btn) { btn.disabled = true; btn.textContent = 'Analyzing...'; }
  if (statusEl) statusEl.textContent = `Analyzing ${allApps.length.toLocaleString()} applications...`;

  try {
    // Batch into groups of 200
    const BATCH = 200;
    const batches = [];
    for (let i = 0; i < allApps.length; i += BATCH) {
      batches.push(allApps.slice(i, i + BATCH));
    }

    // Fetch all batches (sequential to avoid overloading)
    const merged = {
      timelines: {},
      payments_detail: [],
      summary: {},
      kpis: { small: 0, micro: 0, large: 0, total: 0, apps_with_findings: 0 },
      date_range: null,
      cache_stats: { from_cache: 0, freshly_analyzed: 0 },
    };

    for (let i = 0; i < batches.length; i++) {
      if (statusEl) statusEl.textContent = `Analyzing batch ${i + 1} of ${batches.length}...`;
      const resp = await apiPost('/api/entity-status/prosecution-timelines', {
        application_numbers: batches[i],
      });

      // Merge timelines
      Object.assign(merged.timelines, resp.timelines || {});

      // Merge payments_detail
      if (resp.payments_detail) merged.payments_detail.push(...resp.payments_detail);

      // Merge summary (year → code → count)
      for (const [yr, codes] of Object.entries(resp.summary || {})) {
        if (!merged.summary[yr]) merged.summary[yr] = {};
        for (const [code, cnt] of Object.entries(codes)) {
          merged.summary[yr][code] = (merged.summary[yr][code] || 0) + cnt;
        }
      }

      // Merge KPIs
      const k = resp.kpis || {};
      merged.kpis.small += k.small || 0;
      merged.kpis.micro += k.micro || 0;
      merged.kpis.large += k.large || 0;
      merged.kpis.total += k.total || 0;
      // apps_with_findings must be re-counted from merged timelines
      // (app might appear in multiple batches — unlikely but safe)

      // Merge date_range
      if (resp.date_range) {
        if (!merged.date_range) {
          merged.date_range = { ...resp.date_range };
        } else {
          if (resp.date_range.min < merged.date_range.min) merged.date_range.min = resp.date_range.min;
          if (resp.date_range.max > merged.date_range.max) merged.date_range.max = resp.date_range.max;
        }
      }

      // Merge cache stats
      const cs = resp.cache_stats || {};
      merged.cache_stats.from_cache += cs.from_cache || 0;
      merged.cache_stats.freshly_analyzed += cs.freshly_analyzed || 0;
    }

    // Recount apps_with_findings from merged data
    const appsWithFindings = new Set();
    for (const [an, tl] of Object.entries(merged.timelines)) {
      if (tl.payments && tl.payments.some(p => p.status === 'SMALL' || p.status === 'MICRO')) {
        appsWithFindings.add(an);
      }
    }
    merged.kpis.apps_with_findings = appsWithFindings.size;

    // Store globally for sparkline injection
    window._prosecutionData = merged;

    // Populate KPIs
    const setKpi = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val.toLocaleString();
    };
    setKpi('es-pros-pay-small', merged.kpis.small);
    setKpi('es-pros-pay-micro', merged.kpis.micro);
    setKpi('es-pros-pay-large', merged.kpis.large);
    setKpi('es-pros-pay-total', merged.kpis.total);
    setKpi('es-pros-pay-apps', merged.kpis.apps_with_findings);
    if (kpisEl) kpisEl.style.display = '';

    // Set data-prospay on table rows (comma-separated statuses)
    tbl.querySelectorAll('tbody tr').forEach(row => {
      const app = row.dataset.app;
      if (!app || !merged.timelines[app]) return;
      const statuses = new Set();
      for (const p of (merged.timelines[app].payments || [])) {
        statuses.add(p.status);
      }
      row.dataset.prospay = [...statuses].join(',');
    });

    // Wire KPI click handlers for the prosecution payment section
    const prosPaySection = document.getElementById('es-pros-payments');
    if (prosPaySection) {
      prosPaySection.querySelectorAll('.kpi-clickable').forEach(el => {
        el.addEventListener('click', () => {
          filterPatentTable(el.dataset.filter, el.dataset.label, el);
        });
      });
    }

    // Render summary and detail tables
    renderProsecutionSummaryTable(merged);
    renderProsecutionDetailTable(merged);

    // Re-render sparklines with prosecution data injected
    const visiblePatents = [];
    tbl.querySelectorAll('tbody tr').forEach(row => {
      if (row.style.display !== 'none' && row.dataset.pn) {
        visiblePatents.push(row.dataset.pn);
      }
    });
    if (visiblePatents.length > 0) {
      fetchAndRenderMicroCharts(visiblePatents);
    }

    const cacheMsg = merged.cache_stats.from_cache > 0
      ? ` (${merged.cache_stats.from_cache.toLocaleString()} from cache, ${merged.cache_stats.freshly_analyzed.toLocaleString()} freshly analyzed)`
      : '';
    if (statusEl) statusEl.textContent = `Analysis complete. ${merged.kpis.total.toLocaleString()} payment events found across ${allApps.length.toLocaleString()} applications${cacheMsg}.`;
    if (btn) { btn.textContent = 'Re-Analyze'; btn.disabled = false; }

  } catch (err) {
    if (statusEl) statusEl.textContent = `Error: ${err.message || err}`;
    if (btn) { btn.textContent = 'Retry Analysis'; btn.disabled = false; }
  }
}

/** Render prosecution payment summary pivot table (Year × Event Code). */
function renderProsecutionSummaryTable(data) {
  const wrap = document.getElementById('es-pros-summary-wrap');
  if (!wrap) return;

  const summary = data.summary || {};
  const years = Object.keys(summary).sort();
  if (years.length === 0) {
    wrap.innerHTML = '<p class="text-muted">No prosecution payments found.</p>';
    wrap.style.display = '';
    return;
  }

  // Collect all event codes across all years
  const allCodes = new Set();
  for (const yr of years) {
    for (const code of Object.keys(summary[yr])) {
      allCodes.add(code);
    }
  }
  const codes = [...allCodes].sort();

  let html = `
    <div class="card">
      <h4 class="card-title" style="font-size:1rem">Payment Summary by Year</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">All prosecution payments (Small + Micro + Large rate)</p>
      <div class="table-scroll-wrap">
        <table class="data-table" id="es-pros-summary-table">
          <thead><tr>
            <th data-sort-key="0">Year</th>`;
  codes.forEach((c, i) => {
    html += `<th data-sort-key="${i + 1}" style="text-align:right">${escHtml(c)}</th>`;
  });
  html += `<th style="text-align:right;font-weight:700">Total</th></tr></thead><tbody>`;

  const colTotals = {};
  let grandTotal = 0;

  for (const yr of years) {
    let rowTotal = 0;
    html += `<tr><td style="font-weight:600">${escHtml(yr)}</td>`;
    for (const code of codes) {
      const cnt = summary[yr][code] || 0;
      rowTotal += cnt;
      colTotals[code] = (colTotals[code] || 0) + cnt;
      html += `<td style="text-align:right">${cnt || ''}</td>`;
    }
    grandTotal += rowTotal;
    html += `<td style="text-align:right;font-weight:600">${rowTotal}</td></tr>`;
  }

  // Footer row
  html += `</tbody><tfoot><tr style="border-top:2px solid var(--color-border)"><td style="font-weight:700">Total</td>`;
  for (const code of codes) {
    html += `<td style="text-align:right;font-weight:600">${colTotals[code] || 0}</td>`;
  }
  html += `<td style="text-align:right;font-weight:700">${grandTotal}</td></tr></tfoot></table></div></div>`;

  wrap.innerHTML = html;
  wrap.style.display = '';

  const tbl = document.getElementById('es-pros-summary-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
  }
}

/** Render detailed prosecution payment table (Small + Micro findings only). */
function renderProsecutionDetailTable(data) {
  const wrap = document.getElementById('es-pros-detail-wrap');
  if (!wrap) return;

  const details = data.payments_detail || [];
  if (details.length === 0) {
    wrap.innerHTML = '<div class="card"><p class="text-muted">No Small or Micro rate payments found.</p></div>';
    wrap.style.display = '';
    return;
  }

  let html = `
    <div class="card">
      <h4 class="card-title" style="font-size:1rem">Flagged Payments — Small &amp; Micro Rate</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">${details.length.toLocaleString()} payment events made at reduced entity rates</p>
      <div class="table-scroll-wrap">
        <table class="data-table" id="es-pros-detail-table">
          <thead><tr>
            <th data-sort-key="0">App #</th>
            <th data-sort-key="1">Date</th>
            <th data-sort-key="2">Code</th>
            <th data-sort-key="3">Description</th>
            <th data-sort-key="4">Status</th>
            <th data-sort-key="5">Origin Code</th>
            <th data-sort-key="6">Origin Date</th>
          </tr></thead><tbody>`;

  for (const d of details) {
    html += `<tr>
      <td>${escHtml(d.application_number || '')}</td>
      <td>${escHtml(d.event_date || '')}</td>
      <td>${escHtml(d.event_code || '')}</td>
      <td>${escHtml(d.event_description || '')}</td>
      <td>${statusBadge(d.claimed_status)}</td>
      <td>${escHtml(d.origin_code || '')}</td>
      <td>${escHtml(d.origin_date || '')}</td>
    </tr>`;
  }

  html += '</tbody></table></div></div>';
  wrap.innerHTML = html;
  wrap.style.display = '';

  const tbl = document.getElementById('es-pros-detail-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
  }
}

// ── Patent Table Filtering (for clickable KPIs) ─────────────────

/**
 * Filter the Patent Details table based on a KPI click.
 * Also fetches and renders micro chart sparklines for visible patents.
 */
function filterPatentTable(filterSpec, label, clickedEl) {
  const tbl = document.getElementById('es-app-table');
  if (!tbl) return;

  const rows = tbl.querySelectorAll('tbody tr');
  const filterLabel = document.getElementById('es-filter-label');
  const shownCount = document.getElementById('es-shown-count');

  // Toggle off if same KPI is clicked again
  const prevActive = appArea.querySelector('.kpi-active');
  if (prevActive === clickedEl) {
    prevActive.classList.remove('kpi-active');
    rows.forEach(row => { row.style.display = ''; });
    if (filterLabel) filterLabel.classList.add('hidden');
    if (shownCount) shownCount.textContent = `${rows.length.toLocaleString()} shown`;
    hideLitigationTable();
    clearMicroCharts();
    return;
  }

  // Clear previous active highlight
  if (prevActive) prevActive.classList.remove('kpi-active');
  clickedEl.classList.add('kpi-active');

  // Hide litigation table when a non-litigation KPI is clicked
  hideLitigationTable();

  // Parse filter spec — "field:val1,val2"
  const colonIdx = filterSpec.indexOf(':');
  const field = filterSpec.slice(0, colonIdx);
  const codes = filterSpec.slice(colonIdx + 1).split(',');

  let shown = 0;
  const visiblePatents = [];
  rows.forEach(row => {
    let match = false;
    if (field === 'mf') {
      const mfTokens = (row.dataset.mf || '').split(' ');
      match = codes.some(c => mfTokens.includes(c));
    } else if (field === 'pros') {
      match = codes.includes(row.dataset.pros);
    } else if (field === 'pros10y') {
      match = codes.includes(row.dataset.pros10y);
    } else if (field === 'ownership') {
      if (codes.includes('divested')) match = row.dataset.divested === '1';
      else if (codes.includes('acquired')) match = row.dataset.acquired === '1';
    } else if (field === 'portfolio') {
      const code = codes[0];
      const isGranted = !!row.dataset.pn;
      const isPending = !isGranted;
      const isDivested = row.dataset.divested === '1';
      const isAcquired = row.dataset.acquired === '1';
      const isExpired = row.dataset.expired === '1';
      if (code === 'filed_granted') match = isGranted && !isAcquired;
      else if (code === 'acquired_granted') match = isGranted && isAcquired;
      else if (code === 'divested_granted') match = isGranted && isDivested;
      else if (code === 'expired_granted') match = isGranted && !isDivested && isExpired;
      else if (code === 'filed_pending') match = isPending && !isAcquired;
      else if (code === 'acquired_pending') match = isPending && isAcquired;
      else if (code === 'divested_pending') match = isPending && isDivested;
    } else if (field === 'litigation') {
      if (codes.includes('litigated')) match = row.dataset.litigated === '1';
    } else if (field === 'prospay') {
      // Filter by prosecution payment status (data set after analysis)
      const ppStatuses = (row.dataset.prospay || '').split(',').filter(Boolean);
      match = codes.some(c => ppStatuses.includes(c));
    }
    row.style.display = match ? '' : 'none';
    if (match) {
      shown++;
      const pn = row.dataset.pn;
      if (pn) visiblePatents.push(pn);
    }
  });

  // Update filter label pill
  if (filterLabel) {
    filterLabel.innerHTML = `Filtered: <strong>${escHtml(label)}</strong> &mdash; ${shown.toLocaleString()} of ${rows.length.toLocaleString()} patents <button class="es-filter-clear" title="Clear filter">&times;</button>`;
    filterLabel.classList.remove('hidden');
    filterLabel.querySelector('.es-filter-clear').addEventListener('click', () => {
      clickedEl.classList.remove('kpi-active');
      rows.forEach(row => { row.style.display = ''; });
      filterLabel.classList.add('hidden');
      if (shownCount) shownCount.textContent = `${rows.length.toLocaleString()} shown`;
      clearMicroCharts();
    });
  }

  if (shownCount) shownCount.textContent = `${shown.toLocaleString()} of ${rows.length.toLocaleString()} shown`;

  // Fetch and render micro charts for visible patents
  if (visiblePatents.length > 0) {
    fetchAndRenderMicroCharts(visiblePatents, filterSpec);
  } else {
    clearMicroCharts();
  }
}

// ── Micro Chart Rendering ────────────────────────────────────────

function clampPct(pct) { return Math.max(0, Math.min(100, pct)); }

/** Create a colored line segment div in the track. */
function appendLine(track, leftPct, widthPct, color) {
  if (widthPct <= 0) return;
  const seg = document.createElement('div');
  seg.className = 'es-microchart-line';
  seg.style.left = leftPct + '%';
  seg.style.width = widthPct + '%';
  seg.style.backgroundColor = color;
  track.appendChild(seg);
}

/** Create an absolutely-positioned icon wrapper with an SVG inside. */
function createIconEl(svgFn, color, leftPct, ev) {
  const wrap = document.createElement('div');
  wrap.className = 'es-microchart-icon';
  wrap.style.left = leftPct + '%';
  wrap.style.color = color;
  wrap.title = `${ev.c} \u2014 ${ev.d}`;
  wrap.innerHTML = svgFn();
  return wrap;
}

/**
 * Fetch bulk timelines and render sparklines in the Events column.
 * Each sparkline has a colored status line (red/green/blue = large/small/micro)
 * that changes color at declaration/transition events, with icons for payments
 * and status events positioned on top.
 */
async function fetchAndRenderMicroCharts(patentNumbers, filterSpec) {
  const tbl = document.getElementById('es-app-table');
  if (!tbl) return;

  tbl.querySelectorAll('tbody tr').forEach(row => {
    if (row.style.display !== 'none') {
      const cell = row.querySelector('.es-events-cell');
      if (cell) cell.innerHTML = '<span class="text-muted" style="font-size:0.7rem">Loading...</span>';
    }
  });

  try {
    // Split into batches of 200 and fetch in parallel
    const BATCH = 200;
    const batches = [];
    for (let i = 0; i < patentNumbers.length; i += BATCH) {
      batches.push(patentNumbers.slice(i, i + BATCH));
    }
    const results = await Promise.all(
      batches.map(batch => apiPost('/api/entity-status/bulk-timelines', { patent_numbers: batch }))
    );

    // Merge all batch responses into one unified result
    const data = { timelines: {}, date_range: null };
    for (const r of results) {
      if (!r.date_range) continue;
      Object.assign(data.timelines, r.timelines);
      if (!data.date_range) {
        data.date_range = { ...r.date_range };
      } else {
        if (r.date_range.min < data.date_range.min) data.date_range.min = r.date_range.min;
        if (r.date_range.max > data.date_range.max) data.date_range.max = r.date_range.max;
      }
    }

    // Inject litigation events into timelines (if loaded)
    if (window._litigationData) {
      for (const [pn, cases] of Object.entries(window._litigationData)) {
        if (!data.timelines[pn]) continue;
        for (const c of cases) {
          if (c.filed_date) {
            data.timelines[pn].push({ d: c.filed_date, c: 'LITG', _case: c });
          }
        }
        data.timelines[pn].sort((a, b) => a.d.localeCompare(b.d));
      }
    }

    // Inject prosecution payment events into timelines (if loaded)
    if (window._prosecutionData) {
      // Build pn→app map from table rows
      const pnToApp = {};
      tbl.querySelectorAll('tbody tr').forEach(row => {
        const pn = row.dataset.pn;
        const app = row.dataset.app;
        if (pn && app) pnToApp[pn] = app;
      });

      for (const [pn, app] of Object.entries(pnToApp)) {
        const prosTimeline = window._prosecutionData.timelines[app];
        if (!prosTimeline || !prosTimeline.payments) continue;
        if (!data.timelines[pn]) data.timelines[pn] = [];

        for (const p of prosTimeline.payments) {
          data.timelines[pn].push({
            d: p.d,
            c: 'PROS_PAY',
            _prosCode: p.c,
            _prosDesc: p.desc,
            _prosStatus: p.status,
          });
        }

        // Also inject prosecution status-change events for line coloring
        for (const seg of prosTimeline.segments) {
          if (seg.trigger) {
            data.timelines[pn].push({
              d: seg.start,
              c: seg.trigger,
            });
          }
        }

        data.timelines[pn].sort((a, b) => a.d.localeCompare(b.d));

        // Expand date range if prosecution data extends earlier
        if (prosTimeline.segments.length > 0) {
          const earliest = prosTimeline.segments[0].start;
          if (earliest && (!data.date_range || earliest < data.date_range.min)) {
            if (!data.date_range) data.date_range = { min: earliest, max: earliest };
            else data.date_range.min = earliest;
          }
        }
      }
    }

    if (!data.date_range) { clearMicroCharts(); return; }

    const minDate = new Date(data.date_range.min);
    const maxDate = new Date(data.date_range.max);
    const totalMs = maxDate.getTime() - minDate.getTime();
    if (totalMs <= 0) { clearMicroCharts(); return; }

    // Parse highlight codes from filter spec
    const highlightCodes = new Set();
    if (filterSpec) {
      const ci = filterSpec.indexOf(':');
      if (ci >= 0) filterSpec.slice(ci + 1).split(',').forEach(c => highlightCodes.add(c));
    }

    // Render sparkline into each visible row's Events cell
    tbl.querySelectorAll('tbody tr').forEach(row => {
      if (row.style.display === 'none') return;
      const pn = row.dataset.pn;
      const cell = row.querySelector('.es-events-cell');
      if (!cell || !pn) return;

      const events = data.timelines[pn] || [];
      if (events.length === 0) {
        cell.innerHTML = '<span class="text-muted" style="font-size:0.7rem">&mdash;</span>';
        return;
      }

      cell.innerHTML = '';
      const track = document.createElement('div');
      track.className = 'es-microchart-track';

      // ── Build colored status line ──
      const initColor = inferInitialColor(events);
      let currentColor = initColor;
      const changePoints = [];

      // Find expiration point — line stops there
      let endPct = 100;
      for (const ev of events) {
        if (ev.c === 'EXP.') {
          const expDate = new Date(ev.d);
          endPct = clampPct(((expDate.getTime() - minDate.getTime()) / totalMs) * 100);
          break;
        }
      }

      for (const ev of events) {
        const newColor = statusColorForEvent(ev.c);
        if (newColor && newColor !== currentColor) {
          const evDate = new Date(ev.d);
          const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
          if (pct >= endPct) break; // don't add change points past expiration
          changePoints.push({ pct, color: newColor });
          currentColor = newColor;
        }
      }

      // Draw line segments up to expiration point (or full width if no EXP.)
      let prevPct = 0;
      let lineColor = initColor;
      for (const cp of changePoints) {
        appendLine(track, prevPct, cp.pct - prevPct, lineColor);
        lineColor = cp.color;
        prevPct = cp.pct;
      }
      appendLine(track, prevPct, endPct - prevPct, lineColor);

      // ── Place icons on top of the line ──
      for (const ev of events) {
        const evDate = new Date(ev.d);
        const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
        const cat = classifyEvent(ev.c);

        let marker = null;
        if (cat === 'large_payment') {
          marker = createIconEl(svgBuilding, STATUS_COLORS.large, pct, ev);
        } else if (cat === 'small_payment') {
          marker = createIconEl(svgHouse, STATUS_COLORS.small, pct, ev);
        } else if (cat === 'micro_payment') {
          marker = createIconEl(svgPerson, STATUS_COLORS.micro, pct, ev);
        } else if (cat === 'expired') {
          marker = createIconEl(svgExpired, GRAY, pct, ev);
        } else if (cat === 'grant') {
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#8b5cf6'; // purple
          marker.title = `Grant \u2014 ${ev.d}`;
        } else if (cat === 'reminder') {
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#eab308';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        } else if (cat === 'attorney') {
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#92400e';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        } else if (cat === 'pros_payment') {
          const prosColor = ev._prosStatus === 'SMALL' ? STATUS_COLORS.small
            : ev._prosStatus === 'MICRO' ? STATUS_COLORS.micro
            : STATUS_COLORS.large;
          marker = createIconEl(svgDollar, prosColor, pct, ev);
          marker.title = `${ev._prosCode || ''} (${ev._prosStatus || ''}) \u2014 ${ev.d}${ev._prosDesc ? ' \u2014 ' + ev._prosDesc : ''}`;
        } else if (cat === 'litigation') {
          marker = createIconEl(svgStar, '#d4a017', pct, ev);
          marker.title = `Litigation filed \u2014 ${ev.d}${ev._case ? ' \u2014 ' + ev._case.case_no : ''}`;
        } else if (cat.startsWith('trans_to_')) {
          // Transitions: gray dot on the line to mark the event
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-trans';
          marker.style.left = pct + '%';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        } else if (cat.startsWith('decl_')) {
          // Declarations are shown by the line color change — no marker
          continue;
        } else {
          // Catch-all: small gray dot
          marker = document.createElement('div');
          marker.className = 'es-microchart-other';
          marker.style.left = pct + '%';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        }

        if (marker && highlightCodes.has(ev.c)) {
          marker.classList.add('es-microchart-icon--hl');
        }
        if (marker) track.appendChild(marker);
      }

      cell.appendChild(track);
    });

    showMicroChartLegend();

  } catch (err) {
    clearMicroCharts();
  }
}

/** Clear all micro chart sparklines from the Events column. */
function clearMicroCharts() {
  const tbl = document.getElementById('es-app-table');
  if (tbl) {
    tbl.querySelectorAll('.es-events-cell').forEach(cell => { cell.innerHTML = ''; });
  }
  const legend = document.getElementById('es-microchart-legend');
  if (legend) legend.classList.add('hidden');
}

/** Show the icon + line color legend above the table. */
function showMicroChartLegend() {
  const legend = document.getElementById('es-microchart-legend');
  if (!legend) return;
  legend.innerHTML = '';

  // Payment icons
  const iconEntries = [
    [svgBuilding, STATUS_COLORS.large, 'Large Pay'],
    [svgHouse,    STATUS_COLORS.small, 'Small Pay'],
    [svgPerson,   STATUS_COLORS.micro, 'Micro Pay'],
  ];
  for (const [fn, color, label] of iconEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-icon" style="color:${color}">${fn()}</span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  // Status line swatches
  const lineEntries = [
    [STATUS_COLORS.large, 'Large Status'],
    [STATUS_COLORS.small, 'Small Status'],
    [STATUS_COLORS.micro, 'Micro Status'],
  ];
  for (const [color, label] of lineEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-line" style="background:${color}"></span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  // Expired icon (gray)
  const expItem = document.createElement('span');
  expItem.className = 'es-microchart-legend-item';
  expItem.innerHTML = `<span class="es-microchart-legend-icon" style="color:${GRAY}">${svgExpired()}</span>Expired`;
  legend.appendChild(expItem);

  // Transition dot (slightly larger, gray)
  const transItem = document.createElement('span');
  transItem.className = 'es-microchart-legend-item';
  transItem.innerHTML = `<span class="es-microchart-legend-dot" style="background:#6b7280;width:9px;height:9px"></span>Transition`;
  legend.appendChild(transItem);

  // Small dot entries (grant + reminder + attorney)
  const dotEntries = [
    ['#8b5cf6', 'Grant Date'],
    ['#eab308', 'Reminder'],
    ['#92400e', 'Attorney'],
  ];
  for (const [color, label] of dotEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-dot" style="background:${color}"></span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  // Litigation star
  const litItem = document.createElement('span');
  litItem.className = 'es-microchart-legend-item';
  litItem.innerHTML = `<span class="es-microchart-legend-icon" style="color:#d4a017">${svgStar()}</span>Litigation`;
  legend.appendChild(litItem);

  // Prosecution payment $ icons (shown when prosecution data is loaded)
  if (window._prosecutionData) {
    const prosEntries = [
      [STATUS_COLORS.small, 'Small $ Pay'],
      [STATUS_COLORS.micro, 'Micro $ Pay'],
      [STATUS_COLORS.large, 'Large $ Pay'],
    ];
    for (const [color, label] of prosEntries) {
      const item = document.createElement('span');
      item.className = 'es-microchart-legend-item';
      item.innerHTML = `<span class="es-microchart-legend-icon" style="color:${color}">${svgDollar()}</span>${escHtml(label)}`;
      legend.appendChild(item);
    }
  }

  legend.classList.remove('hidden');
}

// ── Helpers ──────────────────────────────────────────────────────

function statusBadge(status) {
  if (!status) return '<span class="text-muted">N/A</span>';
  const s = status.toUpperCase();
  const cls = s === 'SMALL' ? 'es-status-small'
    : s === 'MICRO' ? 'es-status-micro'
    : s === 'LARGE' ? 'es-status-large'
    : 'es-status-other';
  return `<span class="es-status-badge ${cls}">${escHtml(status)}</span>`;
}
