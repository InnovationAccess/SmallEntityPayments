/**
 * prosecution.js – Prosecution Fee Investigation tab.
 *
 * Phase 1: Entity discovery — find entities with N+ SMAL declarations
 * Phase 2: Application drill-down — list apps for selected entity
 * Phase 3: Invoice retrieval + fee code extraction via AI vision
 */

import {
  apiPost, setLoading, showStatus, escHtml,
  enableTableSorting, stampOriginalOrder, enableAssignmentPopup, addColumnPicker,
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

const phase3Card      = document.getElementById('px-phase3');
const retrieveBtn     = document.getElementById('px-retrieve-btn');
const retrieveCountEl = document.getElementById('px-retrieve-count');
const docResultsEl    = document.getElementById('px-doc-results');

const extractControlsEl = document.getElementById('px-extract-controls');
const downloadExtractBtn = document.getElementById('px-download-extract-btn');
const extractProgressEl = document.getElementById('px-extract-progress');
const extractResultsEl = document.getElementById('px-extract-results');

const statusMsg       = document.getElementById('px-status');

let selectedEntity = null;

// Module-level sets so Phase 3 can access Phase 2 selections
let selectedAppRows = new Set();
let selectedDocRows = new Set();
let docResultsList = [];  // full list from Phase 3a

// ── Phase 1: Entity Discovery ───────────────────────────────────

discoverBtn.addEventListener('click', () => discoverEntities());

async function discoverEntities() {
  const minDecl = parseInt(minDeclInput.value) || 1000;
  const limit = parseInt(entityLimitInput.value) || 200;

  setLoading(discoverBtn, true);
  entityResultsEl.classList.remove('hidden');
  entityResultsEl.innerHTML = '<p class="text-muted">Searching for entities with SMAL declarations...</p>';

  // Hide phases 2+3 when running a new discovery
  phase2Card.classList.add('hidden');
  appResultsEl.classList.add('hidden');
  hidePhase3();
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

  // Enable sorting + column picker
  const tbl = document.getElementById('px-entity-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
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
  hidePhase3();
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
  hidePhase3();

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

  selectedAppRows = new Set();

  let html = `
    <div class="results-header">
      <strong>Applications for ${escHtml(data.applicant_name)}</strong>
      <span class="results-count">${data.total} applications</span>
      <span id="px-selected-count" class="results-count px-sel-count">0 selected</span>
    </div>
    <p class="px-hint">Ctrl+Click to select multiple rows, Shift+Click for range. Selected rows will be used for invoice retrieval (Phase 3).</p>
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

  // Enable sorting + assignment popup + column picker
  const tbl = document.getElementById('px-app-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    enableAssignmentPopup('#px-app-table .patent-number');
    addColumnPicker(tbl);
  }

  // Ctrl+Click multi-select
  const countEl = document.getElementById('px-selected-count');
  let lastClickedIdx = null;

  appResultsEl.querySelectorAll('.px-app-row').forEach((row, idx) => {
    row.addEventListener('click', (e) => {
      const appNum = row.dataset.app;

      if (e.ctrlKey || e.metaKey) {
        // Toggle this row
        if (selectedAppRows.has(appNum)) {
          selectedAppRows.delete(appNum);
          row.classList.remove('px-selected');
        } else {
          selectedAppRows.add(appNum);
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
          selectedAppRows.add(an);
          r.classList.add('px-selected');
        }
      } else {
        // Single click — clear all and select this one
        selectedAppRows.clear();
        appResultsEl.querySelectorAll('.px-app-row').forEach(r =>
          r.classList.remove('px-selected'));
        selectedAppRows.add(appNum);
        row.classList.add('px-selected');
      }

      lastClickedIdx = idx;
      countEl.textContent = `${selectedAppRows.size} selected`;

      // Show/hide Phase 3 based on selection
      if (selectedAppRows.size > 0) {
        phase3Card.classList.remove('hidden');
        retrieveCountEl.textContent = `${selectedAppRows.size} applications selected`;
      } else {
        phase3Card.classList.add('hidden');
      }
    });
  });
}

// ── Phase 3: Invoice Retrieval + Extraction ─────────────────────

function hidePhase3() {
  phase3Card.classList.add('hidden');
  docResultsEl.classList.add('hidden');
  extractControlsEl.classList.add('hidden');
  extractResultsEl.classList.add('hidden');
  selectedDocRows = new Set();
  docResultsList = [];
}

retrieveBtn.addEventListener('click', () => retrieveInvoices());

async function retrieveInvoices() {
  if (selectedAppRows.size === 0) {
    showStatus(statusMsg, 'Select applications in Phase 2 first.', 'error');
    return;
  }

  const appNumbers = Array.from(selectedAppRows);

  setLoading(retrieveBtn, true);
  docResultsEl.classList.remove('hidden');
  docResultsEl.innerHTML = `<p class="text-muted">Querying USPTO for payment documents across ${appNumbers.length} applications...</p>`;
  extractControlsEl.classList.add('hidden');
  extractResultsEl.classList.add('hidden');

  try {
    const data = await apiPost('/api/prosecution/documents', {
      application_numbers: appNumbers,
    });
    renderDocumentResults(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    docResultsEl.innerHTML = '';
    docResultsEl.classList.add('hidden');
  } finally {
    setLoading(retrieveBtn, false);
  }
}

function renderDocumentResults(data) {
  if (!data.results || data.results.length === 0) {
    let msg = '<p class="text-muted">No payment-related documents found for the selected applications.</p>';
    if (data.errors && data.errors.length > 0) {
      msg += `<p class="text-muted">${data.apps_with_errors} application(s) had errors.</p>`;
    }
    docResultsEl.innerHTML = msg;
    return;
  }

  docResultsList = data.results;
  selectedDocRows = new Set();

  // Select all by default
  for (let i = 0; i < docResultsList.length; i++) {
    selectedDocRows.add(i);
  }

  let html = `
    <div class="results-header">
      <strong>Payment Documents Found</strong>
      <span class="results-count">${data.total} documents across ${data.apps_queried} applications</span>
      <span id="px-doc-selected-count" class="results-count px-sel-count">${data.total} selected</span>
    </div>
  `;

  if (data.apps_with_errors > 0) {
    html += `<p class="px-hint" style="color:#c62828">${data.apps_with_errors} application(s) had errors querying the USPTO API.</p>`;
  }

  html += `
    <p class="px-hint">All documents pre-selected. Ctrl+Click to deselect individual rows. Click "Download &amp; Extract" to process.</p>
    <div class="table-scroll-wrap">
      <table class="data-table" id="px-doc-table">
        <thead><tr>
          <th data-sort-key="0">App #</th>
          <th data-sort-key="1">Doc Code</th>
          <th data-sort-key="2">Description</th>
          <th data-sort-key="3">Mail Date</th>
          <th data-sort-key="4">Pages</th>
        </tr></thead>
        <tbody>
  `;

  for (let i = 0; i < docResultsList.length; i++) {
    const d = docResultsList[i];
    html += `<tr class="px-doc-row px-selected" data-doc-idx="${i}">
      <td>${escHtml(d.app_number || '')}</td>
      <td>${escHtml(d.doc_code || '')}</td>
      <td>${escHtml(d.description || '')}</td>
      <td>${escHtml(d.mail_date || '')}</td>
      <td>${d.page_count || '—'}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  docResultsEl.innerHTML = html;

  // Enable sorting + column picker
  const tbl = document.getElementById('px-doc-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
  }

  // Show extract controls
  extractControlsEl.classList.remove('hidden');
  extractProgressEl.textContent = '';

  // Ctrl+Click toggle for documents
  const docCountEl = document.getElementById('px-doc-selected-count');

  docResultsEl.querySelectorAll('.px-doc-row').forEach(row => {
    row.addEventListener('click', (e) => {
      const idx = parseInt(row.dataset.docIdx);

      if (e.ctrlKey || e.metaKey) {
        if (selectedDocRows.has(idx)) {
          selectedDocRows.delete(idx);
          row.classList.remove('px-selected');
        } else {
          selectedDocRows.add(idx);
          row.classList.add('px-selected');
        }
      } else {
        // Single click — toggle only this one without clearing others
        if (selectedDocRows.has(idx)) {
          selectedDocRows.delete(idx);
          row.classList.remove('px-selected');
        } else {
          selectedDocRows.add(idx);
          row.classList.add('px-selected');
        }
      }

      docCountEl.textContent = `${selectedDocRows.size} selected`;
    });
  });
}

// ── Phase 3b+c: Download & Extract ──────────────────────────────

downloadExtractBtn.addEventListener('click', () => downloadAndExtract());

async function downloadAndExtract() {
  if (selectedDocRows.size === 0) {
    showStatus(statusMsg, 'Select documents to download and extract.', 'error');
    return;
  }

  const docsToProcess = Array.from(selectedDocRows).map(i => docResultsList[i]);

  setLoading(downloadExtractBtn, true);
  extractResultsEl.classList.remove('hidden');
  extractResultsEl.innerHTML = '';

  const total = docsToProcess.length;
  let completed = 0;
  const extractionResults = [];

  // Progress bar
  extractProgressEl.innerHTML = `
    <span id="px-progress-text">Downloading... 0/${total}</span>
    <div class="px-progress-bar"><div class="px-progress-fill" id="px-progress-fill" style="width:0%"></div></div>
  `;
  const progressText = document.getElementById('px-progress-text');
  const progressFill = document.getElementById('px-progress-fill');

  function updateProgress(phase, count) {
    const pct = Math.round((count / total) * 100);
    progressFill.style.width = `${pct}%`;
    progressText.textContent = `${phase}... ${count}/${total}`;
  }

  try {
    // Step 1: Download all documents to GCS
    updateProgress('Downloading', 0);

    const downloadPayload = docsToProcess.map(d => ({
      app_number: d.app_number,
      download_url: d.download_url,
      filename: d.filename,
    }));

    const downloadResult = await apiPost('/api/prosecution/download', {
      documents: downloadPayload,
    });

    if (!downloadResult.downloaded || downloadResult.downloaded.length === 0) {
      extractResultsEl.innerHTML = '<p class="text-muted">No documents were downloaded successfully.</p>';
      if (downloadResult.errors && downloadResult.errors.length > 0) {
        extractResultsEl.innerHTML += `<p style="color:#c62828">${downloadResult.total_errors} download error(s).</p>`;
      }
      return;
    }

    completed = downloadResult.total_downloaded;
    updateProgress('Downloaded', completed);

    // Step 2: Extract from each downloaded PDF one at a time
    const downloaded = downloadResult.downloaded;
    completed = 0;

    for (const doc of downloaded) {
      completed++;
      updateProgress('Extracting', completed);

      try {
        const result = await apiPost('/api/prosecution/extract', {
          gcs_path: doc.gcs_path,
        });
        result._app_number = doc.app_number;
        result._filename = doc.filename;
        extractionResults.push(result);

        // Render incrementally
        renderExtractionResults(extractionResults);
      } catch (err) {
        extractionResults.push({
          _app_number: doc.app_number,
          _filename: doc.filename,
          error: err.message,
          doc_type: 'UNKNOWN',
          entity_status: null,
          fees: [],
        });
        renderExtractionResults(extractionResults);
      }
    }

    // Show download errors if any
    if (downloadResult.total_errors > 0) {
      const errHtml = downloadResult.errors.map(e =>
        `<li>${escHtml(e.filename)}: ${escHtml(e.error)}</li>`
      ).join('');
      extractResultsEl.innerHTML += `
        <div class="px-extract-card" style="border-color:#c62828">
          <h4 style="color:#c62828">Download Errors (${downloadResult.total_errors})</h4>
          <ul style="font-size:.85rem">${errHtml}</ul>
        </div>
      `;
    }

    extractProgressEl.innerHTML = `<span style="color:#0d6e0d;font-weight:600">Complete — ${extractionResults.length} documents processed</span>`;

  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    extractProgressEl.innerHTML = `<span style="color:#c62828">Error: ${escHtml(err.message)}</span>`;
  } finally {
    setLoading(downloadExtractBtn, false);
  }
}

function renderExtractionResults(results) {
  let html = `
    <div class="results-header">
      <strong>Extraction Results</strong>
      <span class="results-count">${results.length} documents processed</span>
    </div>
  `;

  for (const r of results) {
    const entityClass = r.entity_status === 'SMALL' ? 'px-entity-small'
      : r.entity_status === 'LARGE' ? 'px-entity-large'
      : r.entity_status === 'MICRO' ? 'px-entity-micro' : '';

    html += `<div class="px-extract-card">`;
    html += `<h4>${escHtml(r._filename || r.gcs_path || 'Document')}</h4>`;

    if (r.error) {
      html += `<div class="px-field"><span class="px-field-label">Error:</span><span class="px-field-value" style="color:#c62828">${escHtml(r.error)}</span></div>`;
      html += `</div>`;
      continue;
    }

    html += `
      <div class="px-field"><span class="px-field-label">App Number:</span><span class="px-field-value">${escHtml(r._app_number || r.application_number || '—')}</span></div>
      <div class="px-field"><span class="px-field-label">Doc Type:</span><span class="px-field-value">${escHtml(r.doc_type || '—')}</span></div>
      <div class="px-field"><span class="px-field-label">Entity Status:</span><span class="px-field-value ${entityClass}">${escHtml(r.entity_status || 'Not determined')}</span></div>
    `;

    if (r.entity_status_evidence) {
      html += `<div class="px-field"><span class="px-field-label">Evidence:</span><span class="px-field-value">${escHtml(r.entity_status_evidence)}</span></div>`;
    }
    if (r.title) {
      html += `<div class="px-field"><span class="px-field-label">Title:</span><span class="px-field-value">${escHtml(r.title)}</span></div>`;
    }
    if (r.total_amount != null) {
      html += `<div class="px-field"><span class="px-field-label">Total Amount:</span><span class="px-field-value">$${Number(r.total_amount).toLocaleString(undefined, {minimumFractionDigits: 2})}</span></div>`;
    }
    if (r.assignee_name) {
      html += `<div class="px-field"><span class="px-field-label">Assignee:</span><span class="px-field-value">${escHtml(r.assignee_name)}</span></div>`;
    }

    // Fee table
    if (r.fees && r.fees.length > 0) {
      html += `
        <table class="px-fee-table">
          <thead><tr>
            <th>Fee Code</th>
            <th>Description</th>
            <th>Qty</th>
            <th>Amount</th>
          </tr></thead>
          <tbody>
      `;
      for (const f of r.fees) {
        const amt = f.amount != null ? `$${Number(f.amount).toLocaleString(undefined, {minimumFractionDigits: 2})}` : '—';
        html += `<tr>
          <td>${escHtml(f.fee_code || '—')}</td>
          <td>${escHtml(f.description || '')}</td>
          <td>${f.quantity || 1}</td>
          <td>${amt}</td>
        </tr>`;
      }
      html += '</tbody></table>';
    }

    html += `</div>`;
  }

  extractResultsEl.innerHTML = html;
}
