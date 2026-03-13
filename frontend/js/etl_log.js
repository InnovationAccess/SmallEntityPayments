/**
 * etl_log.js – Data Update Log tab.
 *
 * Calls /api/etl-log and /api/etl-log/summary to show
 * pipeline run history and per-source status.
 */

import { apiGet, showStatus, escHtml, enableTableSorting, stampOriginalOrder, addColumnPicker } from './app.js';

const summaryGrid = document.getElementById('etl-summary-grid');
const etlTable    = document.getElementById('etl-log-table');
const logBody     = document.getElementById('etl-log-body');
const logCount    = document.getElementById('etl-log-count');
const refreshBtn  = document.getElementById('etl-refresh-btn');
const statusEl    = document.getElementById('etl-status');

enableTableSorting(etlTable);

const SOURCE_LABELS = {
  ptblxml:  'Citations',
  pasdl:    'Assignments',
  ptmnfee2: 'Maint. Fees',
  ptfwpre:  'File Wrapper',
  entity:   'Entity Names',
};

function statusBadge(status) {
  const cls = status === 'success' ? 'etl-badge-ok'
            : status === 'failed'  ? 'etl-badge-fail'
            : 'etl-badge-skip';
  const label = status === 'no_updates' ? 'no updates' : status;
  return `<span class="etl-badge ${cls}">${escHtml(label)}</span>`;
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return '-';
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

async function loadLog() {
  try {
    const [logData, summaryData] = await Promise.all([
      apiGet('/api/etl-log?limit=50'),
      apiGet('/api/etl-log/summary'),
    ]);

    // Update summary cards
    const sources = summaryData.sources || {};
    for (const src of ['ptblxml', 'pasdl', 'ptmnfee2', 'ptfwpre']) {
      const el = document.getElementById(`etl-last-${src}`);
      if (!el) continue;
      const info = sources[src];
      if (info && info.last_success) {
        el.textContent = info.last_success.replace(' UTC', '');
        el.closest('.etl-source-card').classList.add('etl-source-active');
      } else {
        el.textContent = 'No runs yet';
        el.closest('.etl-source-card').classList.remove('etl-source-active');
      }
    }

    // Render log table
    const entries = logData.entries || [];
    logCount.textContent = `(${entries.length} shown)`;

    if (entries.length === 0) {
      logBody.innerHTML = '<tr><td colspan="7" class="text-muted">No pipeline runs recorded yet. Runs will appear here after the first scheduled update.</td></tr>';
      return;
    }

    logBody.innerHTML = entries.map(e => {
      const errorTip = e.error_message
        ? ` title="${escHtml(e.error_message)}"`
        : '';
      const detailStr = e.details || '';
      return `<tr${errorTip}>
        <td>${statusBadge(e.status)}</td>
        <td>${escHtml(SOURCE_LABELS[e.source] || e.source)}</td>
        <td class="etl-ts">${escHtml(e.started_at || '')}</td>
        <td>${formatDuration(e.duration_seconds)}</td>
        <td>${e.files_processed ?? 0}${e.files_failed > 0 ? ` <span class="etl-badge etl-badge-fail">${e.files_failed} failed</span>` : ''}${e.files_skipped > 0 ? ` <span class="text-muted">(${e.files_skipped} skipped)</span>` : ''}</td>
        <td>${(e.rows_loaded ?? 0).toLocaleString()}</td>
        <td class="etl-detail">${escHtml(detailStr)}</td>
      </tr>`;
    }).join('');
    stampOriginalOrder(etlTable);
    addColumnPicker(etlTable);

  } catch (err) {
    showStatus(statusEl, `Error loading update log: ${err.message}`, 'error');
  }
}

// Load on tab activation (lazy load)
let loaded = false;
const observer = new MutationObserver(() => {
  const panel = document.getElementById('tab-etl-log');
  if (panel && panel.classList.contains('active') && !loaded) {
    loaded = true;
    loadLog();
  }
});
observer.observe(document.getElementById('tab-etl-log'), { attributes: true, attributeFilter: ['class'] });

refreshBtn.addEventListener('click', () => {
  loaded = false;
  loadLog().then(() => { loaded = true; });
});
