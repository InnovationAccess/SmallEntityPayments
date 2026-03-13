/**
 * prosecution.js – Prosecution Fee Investigation tab.
 *
 * Phase 1: Entity discovery — find entities with N+ SMAL declarations
 * Phase 2: Application drill-down — list apps for selected entity
 * Phase 3: Invoice retrieval + extraction (future)
 */

import {
  apiPost, setLoading, showStatus, escHtml,
  enableTableSorting, stampOriginalOrder, enableAssignmentPopup,
} from './app.js';

// ── DOM References ───────────────────────────────────────────────

const minDeclInput    = document.getElementById('px-min-decl');
const entityLimitInput = document.getElementById('px-entity-limit');
const discoverBtn     = document.getElementById('px-discover-btn');
const entityResultsEl = document.getElementById('px-entity-results');

const phase2Card      = document.getElementById('px-phase2');
const selectedEntityEl = document.getElementById('px-selected-entity');
const dateFromInput   = document.getElementById('px-date-from');
const dateToInput     = document.getElementById('px-date-to');
const appLimitInput   = document.getElementById('px-app-limit');
const drilldownBtn    = document.getElementById('px-drilldown-btn');
const appResultsEl    = document.getElementById('px-app-results');

const statusMsg       = document.getElementById('px-status');

let selectedEntity = null;

// ── Phase 1: Entity Discovery ───────────────────────────────────

discoverBtn.addEventListener('click', () => discoverEntities());

async function discoverEntities() {
  const minDecl = parseInt(minDeclInput.value) || 1000;
  const limit = parseInt(entityLimitInput.value) || 200;

  setLoading(discoverBtn, true);
  entityResultsEl.classList.remove('hidden');
  entityResultsEl.innerHTML = '<p class="text-muted">Searching for entities with SMAL declarations...</p>';

  // Hide phase 2 when running a new discovery
  phase2Card.classList.add('hidden');
  appResultsEl.classList.add('hidden');
  selectedEntity = null;

  try {
    const data = await apiPost('/api/prosecution/entities', {
      min_declarations: minDecl,
      limit: limit,
    });
    renderEntityResults(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    entityResultsEl.innerHTML = '';
    entityResultsEl.classList.add('hidden');
  } finally {
    setLoading(discoverBtn, false);
  }
}

function renderEntityResults(data) {
  if (!data.results || data.results.length === 0) {
    entityResultsEl.innerHTML = '<p class="text-muted">No entities found with that many SMAL declarations.</p>';
    return;
  }

  let html = `
    <div class="results-header">
      <strong>Entities with ≥${data.min_declarations.toLocaleString()} SMAL Declarations</strong>
      <span class="results-count">${data.total} entities found</span>
    </div>
    <p class="px-hint">Click an entity row to select it for Phase 2 drill-down.</p>
    <div class="table-scroll-wrap">
      <table class="data-table" id="px-entity-table">
        <thead><tr>
          <th data-sort-key="0">Applicant Name</th>
          <th data-sort-key="1">SMAL Count</th>
          <th data-sort-key="2">Applications</th>
          <th data-sort-key="3">Earliest</th>
          <th data-sort-key="4">Latest</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    html += `<tr class="px-entity-row" data-entity="${escHtml(r.applicant_name)}">
      <td>${escHtml(r.applicant_name)}</td>
      <td>${r.smal_count.toLocaleString()}</td>
      <td>${r.app_count.toLocaleString()}</td>
      <td>${escHtml(r.earliest_date || '')}</td>
      <td>${escHtml(r.latest_date || '')}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  entityResultsEl.innerHTML = html;

  // Enable sorting
  const tbl = document.getElementById('px-entity-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
  }

  // Click-to-select entity
  entityResultsEl.querySelectorAll('.px-entity-row').forEach(row => {
    row.addEventListener('click', () => {
      // Deselect all
      entityResultsEl.querySelectorAll('.px-entity-row').forEach(r =>
        r.classList.remove('px-selected'));
      // Select this one
      row.classList.add('px-selected');
      selectEntity(row.dataset.entity);
    });
  });
}

function selectEntity(entityName) {
  selectedEntity = entityName;
  selectedEntityEl.textContent = entityName;
  phase2Card.classList.remove('hidden');
  appResultsEl.classList.add('hidden');
  // Scroll to phase 2
  phase2Card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Phase 2: Application Drill-down ─────────────────────────────

drilldownBtn.addEventListener('click', () => loadApplications());

async function loadApplications() {
  if (!selectedEntity) return;

  setLoading(drilldownBtn, true);
  appResultsEl.classList.remove('hidden');
  appResultsEl.innerHTML = '<p class="text-muted">Loading applications...</p>';

  try {
    const data = await apiPost('/api/prosecution/applications', {
      applicant_name: selectedEntity,
      date_from: dateFromInput.value || '2016-01-01',
      date_to: dateToInput.value || '2026-12-31',
      limit: parseInt(appLimitInput.value) || 5000,
    });
    renderApplicationResults(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    appResultsEl.innerHTML = '';
    appResultsEl.classList.add('hidden');
  } finally {
    setLoading(drilldownBtn, false);
  }
}

function renderApplicationResults(data) {
  if (!data.results || data.results.length === 0) {
    appResultsEl.innerHTML = '<p class="text-muted">No applications found for this entity in the date range.</p>';
    return;
  }

  const selectedRows = new Set();

  let html = `
    <div class="results-header">
      <strong>Applications for ${escHtml(data.applicant_name)}</strong>
      <span class="results-count">${data.total} applications</span>
      <span id="px-selected-count" class="results-count px-sel-count">0 selected</span>
    </div>
    <p class="px-hint">Ctrl+Click to select multiple rows. Selected rows will be used for invoice retrieval (Phase 3).</p>
    <div class="table-scroll-wrap">
      <table class="data-table" id="px-app-table">
        <thead><tr>
          <th data-sort-key="0">App #</th>
          <th data-sort-key="1">Patent #</th>
          <th data-sort-key="2">Title</th>
          <th data-sort-key="3">Filing Date</th>
          <th data-sort-key="4">Grant Date</th>
          <th data-sort-key="5">Status</th>
          <th data-sort-key="6">SMAL Count</th>
          <th data-sort-key="7">First SMAL</th>
          <th data-sort-key="8">Last SMAL</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    html += `<tr class="px-app-row" data-app="${escHtml(r.application_number)}">
      <td>${escHtml(r.application_number || '')}</td>
      <td class="patent-number">${escHtml(r.patent_number || '—')}</td>
      <td>${escHtml(r.invention_title || '')}</td>
      <td>${escHtml(r.filing_date || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td>${escHtml(r.application_status || '')}</td>
      <td>${r.smal_count}</td>
      <td>${escHtml(r.first_smal_date || '')}</td>
      <td>${escHtml(r.last_smal_date || '')}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  appResultsEl.innerHTML = html;

  // Enable sorting + assignment popup
  const tbl = document.getElementById('px-app-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    enableAssignmentPopup('#px-app-table .patent-number');
  }

  // Ctrl+Click multi-select
  const countEl = document.getElementById('px-selected-count');
  let lastClickedIdx = null;

  appResultsEl.querySelectorAll('.px-app-row').forEach((row, idx) => {
    row.addEventListener('click', (e) => {
      const appNum = row.dataset.app;

      if (e.ctrlKey || e.metaKey) {
        // Toggle this row
        if (selectedRows.has(appNum)) {
          selectedRows.delete(appNum);
          row.classList.remove('px-selected');
        } else {
          selectedRows.add(appNum);
          row.classList.add('px-selected');
        }
      } else if (e.shiftKey && lastClickedIdx !== null) {
        // Range select
        const allRows = Array.from(appResultsEl.querySelectorAll('.px-app-row'));
        const start = Math.min(lastClickedIdx, idx);
        const end = Math.max(lastClickedIdx, idx);
        for (let i = start; i <= end; i++) {
          const r = allRows[i];
          const an = r.dataset.app;
          selectedRows.add(an);
          r.classList.add('px-selected');
        }
      } else {
        // Single click — clear all and select this one
        selectedRows.clear();
        appResultsEl.querySelectorAll('.px-app-row').forEach(r =>
          r.classList.remove('px-selected'));
        selectedRows.add(appNum);
        row.classList.add('px-selected');
      }

      lastClickedIdx = idx;
      countEl.textContent = `${selectedRows.size} selected`;
    });
  });
}
