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
const summaryArea     = document.getElementById('es-summary');
const patentArea      = document.getElementById('es-patent-results');
const convArea        = document.getElementById('es-conv-results');
const appArea         = document.getElementById('es-app-results');
const statusMsg       = document.getElementById('es-status');

let summaryLoaded = false;

// ── Micro Chart: Event Classification & Icons ───────────────────

const STATUS_COLORS = { large: '#ef4444', small: '#22c55e', micro: '#3b82f6' };
const GRAY = '#6b7280';

function classifyEvent(code) {
  if (!code) return 'other';
  if (code.startsWith('M1') || code.startsWith('F17')) return 'large_payment';
  if (code.startsWith('M2') || code.startsWith('F27')) return 'small_payment';
  if (code.startsWith('M3')) return 'micro_payment';
  if (code === 'BIG.') return 'decl_big';
  if (code === 'SMAL') return 'decl_smal';
  if (code === 'MICR') return 'decl_micr';
  if (code === 'STOL') return 'trans_to_large';
  if (code === 'LTOS' || code === 'MTOS') return 'trans_to_small';
  if (code === 'STOM') return 'trans_to_micro';
  if (code === 'EXP.') return 'expired';
  if (code.startsWith('REM')) return 'reminder';
  if (code === 'ASPN') return 'attorney';
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

// ── Load summary on first tab view ───────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.dataset.tab === 'entity-status' && !summaryLoaded) {
      summaryLoaded = true;
      loadSummary();
    }
  });
});

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
    html += `
      <div class="es-timeline">
        <h4>Maintenance Fee Events</h4>
        <div class="table-scroll-wrap">
          <table class="data-table" id="es-timeline-table">
            <thead><tr>
              <th data-sort-key="0">Date</th>
              <th data-sort-key="1">Event Code</th>
              <th data-sort-key="2">Entity Status</th>
            </tr></thead>
            <tbody>
    `;
    for (const ev of data.timeline) {
      const highlight = data.conversion_date && ev.event_date === data.conversion_date
        ? ' class="es-highlight"' : '';
      html += `<tr${highlight}>
        <td>${escHtml(ev.event_date || '')}</td>
        <td>${escHtml(ev.event_code || '')}</td>
        <td>${statusBadge(ev.entity_status)}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';
  } else {
    html += '<p class="text-muted">No maintenance fee events found.</p>';
  }

  html += '</div>';
  patentArea.innerHTML = html;

  const tbl = document.getElementById('es-timeline-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
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

      <!-- Top-level summary -->
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${data.total_patents.toLocaleString()}</span>
          <span class="cite-stat-label">Granted Patents</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${(data.total_applications - data.total_patents).toLocaleString()}</span>
          <span class="cite-stat-label">Pending Applications</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${data.sold_count.toLocaleString()}</span>
          <span class="cite-stat-label">Assigned Away</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${data.total_applications.toLocaleString()}</span>
          <span class="cite-stat-label">Total Applications</span>
        </div>
      </div>
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
            </tr></thead>
            <tbody>
              <tr>
                <td style="font-weight:600">3.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3551, 'mf:M3551', 'Micro 3.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2551, 'mf:M2551', 'Small 3.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1551, 'mf:M1551', 'Large 3.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row35, 'mf:M3551,M2551,M1551', '3.5-yr Total')}</td>
              </tr>
              <tr>
                <td style="font-weight:600">7.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3552, 'mf:M3552', 'Micro 7.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2552, 'mf:M2552', 'Small 7.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1552, 'mf:M1552', 'Large 7.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row75, 'mf:M3552,M2552,M1552', '7.5-yr Total')}</td>
              </tr>
              <tr>
                <td style="font-weight:600">11.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3553, 'mf:M3553', 'Micro 11.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2553, 'mf:M2553', 'Small 11.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1553, 'mf:M1553', 'Large 11.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row115, 'mf:M3553,M2553,M1553', '11.5-yr Total')}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr style="border-top:2px solid var(--color-border)">
                <td style="font-weight:600">Total</td>
                <td style="text-align:right;font-weight:600">${kpi(colMicro, 'mf:M3551,M3552,M3553', 'Micro Total')}</td>
                <td style="text-align:right;font-weight:600">${kpi(colSmall, 'mf:M2551,M2552,M2553', 'Small Total')}</td>
                <td style="text-align:right;font-weight:600">${kpi(colLarge, 'mf:M1551,M1552,M1553', 'Large Total')}</td>
                <td style="text-align:right;font-weight:700">${kpi(grandTotal, 'mf:M3551,M3552,M3553,M2551,M2552,M2553,M1551,M1552,M1553', 'All Payments')}</td>
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
          <th>Events</th>
          <th data-sort-key="4">Prosecution</th>
          <th data-sort-key="5">Post-Grant First</th>
          <th data-sort-key="6">Post-Grant Current</th>
          <th data-sort-key="7">Changed?</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    const changedMark = r.status_changed
      ? `<span class="es-badge es-badge--changed">${r.change_phase === 'prosecution' ? 'Pros' : 'PG'}</span>`
      : '';
    html += `<tr data-pn="${escHtml(r.patent_number || '')}" data-pros="${r.prosecution_status || ''}" data-pros10y="${r.prosecution_status_10y || ''}" data-pgfirst="${r.post_grant_first || ''}" data-pgcurrent="${r.post_grant_current || ''}" data-mf="${escHtml(r.mf_events || '')}" data-changed="${r.status_changed ? '1' : ''}">
      <td class="patent-number">${escHtml(r.patent_number || '')}</td>
      <td>${escHtml(r.application_number || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td class="es-events-cell"></td>
      <td>${statusBadge(r.prosecution_status)}</td>
      <td>${statusBadge(r.post_grant_first)}</td>
      <td>${statusBadge(r.post_grant_current)}</td>
      <td>${changedMark}</td>
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
    clearMicroCharts();
    return;
  }

  // Clear previous active highlight
  if (prevActive) prevActive.classList.remove('kpi-active');
  clickedEl.classList.add('kpi-active');

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

  // Fetch and render micro charts for visible patents (max 200)
  if (visiblePatents.length > 0 && visiblePatents.length <= 200) {
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
    const data = await apiPost('/api/entity-status/bulk-timelines', {
      patent_numbers: patentNumbers,
    });

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

      for (const ev of events) {
        const newColor = statusColorForEvent(ev.c);
        if (newColor && newColor !== currentColor) {
          const evDate = new Date(ev.d);
          const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
          changePoints.push({ pct, color: newColor });
          currentColor = newColor;
        }
      }

      // Draw line segments across full track width
      let prevPct = 0;
      let lineColor = initColor;
      for (const cp of changePoints) {
        appendLine(track, prevPct, cp.pct - prevPct, lineColor);
        lineColor = cp.color;
        prevPct = cp.pct;
      }
      appendLine(track, prevPct, 100 - prevPct, lineColor);

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
        } else if (cat.startsWith('decl_') || cat.startsWith('trans_')) {
          // Declarations & transitions are shown by the line color change — no marker
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

  // Small dot entries (reminder + attorney)
  const dotEntries = [
    ['#eab308', 'Reminder'],
    ['#92400e', 'Attorney'],
  ];
  for (const [color, label] of dotEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-dot" style="background:${color}"></span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  legend.classList.remove('hidden');
}

// ── Summary Dashboard ────────────────────────────────────────────

async function loadSummary() {
  summaryArea.classList.remove('hidden');
  summaryArea.innerHTML = '<p class="text-muted">Loading summary statistics...</p>';

  try {
    const data = await apiGet('/api/entity-status/summary');
    renderSummary(data);
  } catch (err) {
    summaryArea.innerHTML = `<p class="text-muted">Could not load summary: ${escHtml(err.message)}</p>`;
  }
}

function renderSummary(data) {
  const totalPatents = Object.values(data.distribution).reduce((a, b) => a + b, 0);

  let html = `
    <div class="cite-summary-grid">
      <div class="cite-stat">
        <span class="cite-stat-value">${totalPatents.toLocaleString()}</span>
        <span class="cite-stat-label">Patents with Entity Status</span>
      </div>
      <div class="cite-stat">
        <span class="cite-stat-value">${data.total_small_filed.toLocaleString()}</span>
        <span class="cite-stat-label">Filed as Small/Micro</span>
      </div>
      <div class="cite-stat">
        <span class="cite-stat-value">${data.total_conversions.toLocaleString()}</span>
        <span class="cite-stat-label">Small-to-Large Conversions</span>
      </div>
      <div class="cite-stat">
        <span class="cite-stat-value">${data.conversion_rate}%</span>
        <span class="cite-stat-label">Conversion Rate</span>
      </div>
    </div>
  `;

  // Entity status distribution
  html += '<div class="es-dist">';
  for (const [status, count] of Object.entries(data.distribution)) {
    const pct = (count / totalPatents * 100).toFixed(1);
    html += `<div class="es-dist-bar">
      <span class="es-dist-label">${escHtml(status)}</span>
      <div class="es-dist-track"><div class="es-dist-fill es-fill-${status.toLowerCase()}" style="width:${pct}%"></div></div>
      <span class="es-dist-value">${count.toLocaleString()} (${pct}%)</span>
    </div>`;
  }
  html += '</div>';

  // Conversion by year chart
  if (data.by_year.length > 0) {
    const maxConv = Math.max(...data.by_year.map(y => y.small_to_large), 1);
    html += '<h4 style="margin-top:1.5rem">Small-to-Large Conversions by Grant Year</h4>';
    html += '<div class="cite-year-chart">';
    for (const y of data.by_year) {
      const pct = (y.small_to_large / maxConv * 100).toFixed(0);
      const rate = y.total_small > 0
        ? (y.small_to_large / y.total_small * 100).toFixed(1) : 0;
      html += `<div class="cite-year-bar" title="${y.year}: ${y.small_to_large.toLocaleString()} conversions (${rate}% of small)">
        <div class="cite-year-fill" style="height:${pct}%"></div>
        <span class="cite-year-label">${String(y.year).slice(2)}</span>
      </div>`;
    }
    html += '</div>';
  }

  summaryArea.innerHTML = html;
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
