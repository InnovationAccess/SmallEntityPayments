/**
 * entity_status.js – Entity Status Analytics tab.
 *
 * Provides three analysis modes:
 *   1. Single patent lookup: entity status timeline with conversion detection
 *   2. Conversion search: find patents that changed from small to large
 *   3. Applicant portfolio: entity status breakdown for one company's patents
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
        // Use the representative name if this name is normalized
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
  const pgConvRate = pg.total > 0
    ? (pg.converted / pg.total * 100).toFixed(1) : 0;

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
          <span class="cite-stat-value">${pros.small.toLocaleString()}</span>
          <span class="cite-stat-label">${statusBadge('SMALL')} Small</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pros.micro.toLocaleString()}</span>
          <span class="cite-stat-label">${statusBadge('MICRO')} Micro</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pros.large.toLocaleString()}</span>
          <span class="cite-stat-label">${statusBadge('LARGE')} Large</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pros.total.toLocaleString()}</span>
          <span class="cite-stat-label">Total Declarations</span>
        </div>
      </div>
    </div>

    <!-- Post-Grant Phase -->
    <div class="card" style="margin-top:1rem">
      <h4 class="card-title" style="font-size:1rem">Post-Grant Phase — Maintenance Fees</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">From maintenance fee payments, declarations, and transitions</p>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.small.toLocaleString()}</span>
          <span class="cite-stat-label">${statusBadge('SMALL')} First Small</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.micro.toLocaleString()}</span>
          <span class="cite-stat-label">${statusBadge('MICRO')} First Micro</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.large.toLocaleString()}</span>
          <span class="cite-stat-label">${statusBadge('LARGE')} First Large</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.converted.toLocaleString()}</span>
          <span class="cite-stat-label">Status Changed</span>
        </div>
      </div>
      ${(pg.stol || pg.ltos || pg.stom) ? `
      <div class="cite-summary-grid" style="margin-top:0.5rem">
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.stol.toLocaleString()}</span>
          <span class="cite-stat-label">Small→Large</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.ltos.toLocaleString()}</span>
          <span class="cite-stat-label">Large→Small</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pg.stom.toLocaleString()}</span>
          <span class="cite-stat-label">Small→Micro</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${pgConvRate}%</span>
          <span class="cite-stat-label">Conversion Rate</span>
        </div>
      </div>` : ''}
    </div>

    <div class="results-header" style="margin-top:1rem">
      <strong>Patent Details</strong>
      <span class="results-count">${data.results.length.toLocaleString()} shown</span>
    </div>
    <div class="table-scroll-wrap">
      <table class="data-table" id="es-app-table">
        <thead><tr>
          <th data-sort-key="0">Patent #</th>
          <th data-sort-key="1">App #</th>
          <th data-sort-key="2">Grant Date</th>
          <th data-sort-key="3">Title</th>
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
    html += `<tr>
      <td class="patent-number">${escHtml(r.patent_number || '')}</td>
      <td>${escHtml(r.application_number || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td>${escHtml(r.invention_title || '')}</td>
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
