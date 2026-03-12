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
  enableTableSorting, stampOriginalOrder, enableAssignmentPopup,
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
  }
}

// ── Conversion Search ────────────────────────────────────────────

convBtn.addEventListener('click', () => searchConversions());

async function searchConversions() {
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
  let html = `
    <div class="results-header">
      <strong>Conversion Results</strong>
      <span class="results-count">${data.total} patents</span>
    </div>
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
  }
}

// ── Applicant Portfolio ──────────────────────────────────────────

appBtn.addEventListener('click', () => loadApplicantPortfolio());
appInput.addEventListener('keydown', e => { if (e.key === 'Enter') loadApplicantPortfolio(); });

async function loadApplicantPortfolio() {
  const name = appInput.value.trim();
  if (!name) return;

  setLoading(appBtn, true);
  appArea.classList.remove('hidden');
  appArea.innerHTML = '<p class="text-muted">Loading...</p>';

  try {
    const data = await apiPost('/api/entity-status/by-applicant', {
      applicant_name: name,
      limit: 500,
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
  const convRate = data.total_patents > 0
    ? (data.converted / data.total_patents * 100).toFixed(1) : 0;

  let html = `
    <div class="card">
      <h3 class="card-title">Portfolio for ${escHtml(data.applicant_name)}</h3>
      ${data.expanded_names.length > 1 ? `<p class="text-muted">Including ${data.expanded_names.length} name variants</p>` : ''}
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${data.total_patents.toLocaleString()}</span>
          <span class="cite-stat-label">Total Patents</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${data.small_filed.toLocaleString()}</span>
          <span class="cite-stat-label">Filed as Small/Micro</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${data.converted.toLocaleString()}</span>
          <span class="cite-stat-label">Status Changed</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${convRate}%</span>
          <span class="cite-stat-label">Conversion Rate</span>
        </div>
      </div>
    </div>

    <div class="results-header">
      <strong>Patents</strong>
      <span class="results-count">${data.results.length} shown</span>
    </div>
    <div class="table-scroll-wrap">
      <table class="data-table" id="es-app-table">
        <thead><tr>
          <th data-sort-key="0">Patent #</th>
          <th data-sort-key="1">Grant Date</th>
          <th data-sort-key="2">Title</th>
          <th data-sort-key="3">Filing Status</th>
          <th data-sort-key="4">Current Status</th>
          <th data-sort-key="5">Changed?</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    const changedMark = r.status_changed ? '<span class="es-badge es-badge--changed">Yes</span>' : '';
    html += `<tr>
      <td class="patent-number">${escHtml(r.patent_number || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td>${escHtml(r.invention_title || '')}</td>
      <td>${statusBadge(r.filing_status)}</td>
      <td>${statusBadge(r.current_status)}</td>
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
