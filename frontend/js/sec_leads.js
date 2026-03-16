/**
 * sec_leads.js — SEC 10-K Patent Importance Analysis tab.
 *
 * Loads reports from /api/sec-leads and renders the analysis table.
 * Memo and Letter buttons open a modal popup with generated documents.
 */

import {
  apiGet, showStatus, escHtml,
  enableTableSorting, stampOriginalOrder,
} from './app.js';

// ── DOM references ─────────────────────────────────────────────────

const datePicker    = document.getElementById('sec-date-picker');
const loadBtn       = document.getElementById('sec-load-btn');
const statsGrid     = document.getElementById('sec-stats');
const filingsCount  = document.getElementById('sec-filings-count');
const score5Count   = document.getElementById('sec-score5-count');
const score7Count   = document.getElementById('sec-score7-count');
const reportDate    = document.getElementById('sec-report-date');
const resultsArea   = document.getElementById('sec-results');
const resultsCount  = document.getElementById('sec-results-count');
const tableBody     = document.getElementById('sec-table-body');
const tableEl       = document.getElementById('sec-table');
const statusEl      = document.getElementById('sec-status');

// Modal
const modal         = document.getElementById('sec-doc-modal');
const modalTitle    = document.getElementById('sec-modal-title');
const modalBody     = document.getElementById('sec-modal-body');
const modalCopyBtn  = document.getElementById('sec-modal-copy-btn');
const modalCloseBtn = document.getElementById('sec-modal-close-btn');
const modalBackdrop = modal?.querySelector('.modal-backdrop');

// ── Score badge colors ─────────────────────────────────────────────

const SCORE_COLORS = {
  10: '#c0392b', 9: '#c0392b',
  8: '#e74c3c',
  7: '#e67e22',
  6: '#f39c12',
  5: '#95a5a6',
};

function scoreColor(score) {
  return SCORE_COLORS[score] || '#bdc3c7';
}

// ── State ──────────────────────────────────────────────────────────

let loaded = false;
let currentDate = '';

// ── Load report dates into picker ──────────────────────────────────

async function loadReportDates() {
  try {
    const data = await apiGet('/api/sec-leads/reports');
    const reports = data.reports || [];
    datePicker.innerHTML = '';

    if (reports.length === 0) {
      datePicker.innerHTML = '<option value="">No reports available</option>';
      return;
    }

    reports.forEach((r, i) => {
      const opt = document.createElement('option');
      opt.value = r.analysis_date;
      opt.textContent = `${r.analysis_date} (${r.total_companies} companies, ${r.score_5_plus} scoring 5+)`;
      if (i === 0) opt.selected = true;
      datePicker.appendChild(opt);
    });

    // Auto-load the latest report
    await loadReport(reports[0].analysis_date);
  } catch (err) {
    datePicker.innerHTML = '<option value="">Error loading reports</option>';
    showStatus(statusEl, `Error loading report list: ${err.message}`, 'error');
  }
}

// ── Load and render a report ───────────────────────────────────────

async function loadReport(dateStr) {
  if (!dateStr) return;
  currentDate = dateStr;

  try {
    const data = dateStr === 'latest'
      ? await apiGet('/api/sec-leads/reports/latest')
      : await apiGet(`/api/sec-leads/reports/${dateStr}`);

    const stats = data.stats || {};
    const results = data.results || [];

    // Update stats bar
    filingsCount.textContent = stats.total_companies || 0;
    score5Count.textContent = stats.score_5_plus || 0;
    score7Count.textContent = stats.score_7_plus || 0;
    reportDate.textContent = stats.analysis_date || dateStr;
    statsGrid.classList.remove('hidden');

    // Filter to score >= 5 and render table
    const qualified = results.filter(r => r.score >= 5);
    qualified.sort((a, b) => b.score - a.score || a.company_name.localeCompare(b.company_name));

    renderTable(qualified);
    resultsCount.textContent = `${qualified.length} companies scoring 5+`;
    resultsArea.classList.remove('hidden');

  } catch (err) {
    showStatus(statusEl, `Error loading report: ${err.message}`, 'error');
  }
}

// ── Render table rows ──────────────────────────────────────────────

function renderTable(results) {
  if (!results.length) {
    tableBody.innerHTML = '<tr><td colspan="10" class="text-muted">No companies scored 5 or higher on this date.</td></tr>';
    return;
  }

  tableBody.innerHTML = results.map(r => {
    const color = scoreColor(r.score);

    // Determine primary contact
    let contactName = '';
    let contactTitle = '';
    let contactEmail = '';
    if (r.secretary_name) {
      contactName = r.secretary_name;
      contactTitle = r.secretary_title || 'Corporate Secretary';
      contactEmail = r.secretary_email || '';
    } else if (r.general_counsel_name) {
      contactName = r.general_counsel_name;
      contactTitle = r.general_counsel_title || 'General Counsel';
      contactEmail = r.general_counsel_email || '';
    } else if (r.board_chair_name) {
      contactName = r.board_chair_name;
      contactTitle = r.board_chair_title || 'Board Chair';
      contactEmail = r.board_chair_email || '';
    }

    // Parse board members for the new column
    let boardMembers = [];
    try {
      boardMembers = JSON.parse(r.board_members_json || '[]');
    } catch (_) { /* ignore */ }

    // Build board members cell with names and emails
    let boardHtml = '';
    if (boardMembers.length > 0) {
      boardHtml = boardMembers.map(m => {
        const name = escHtml(m.name || '');
        const title = m.title ? `<span class="sec-board-title">${escHtml(m.title)}</span>` : '';
        const email = m.email
          ? `<a href="mailto:${escHtml(m.email)}" class="sec-board-email">${escHtml(m.email)}</a>`
          : '';
        return `<div class="sec-board-member">${name}${title ? ' — ' + title : ''}${email ? '<br>' + email : ''}</div>`;
      }).join('');
    } else {
      boardHtml = '<span class="text-muted">—</span>';
    }

    const ticker = escHtml(r.ticker || '');
    const hasMemo = r.memo_text ? '' : ' disabled';
    const hasLetter = r.letter_text ? '' : ' disabled';

    return `<tr style="border-left: 4px solid ${color};">
      <td>${escHtml(r.analysis_date || '')}</td>
      <td>${escHtml(r.filing_date || '')}</td>
      <td><strong>${escHtml(r.company_name || '')}</strong><br><span class="sec-ticker">${ticker}</span></td>
      <td><a href="${escHtml(r.filing_url || '')}" target="_blank" rel="noopener">View 10-K on SEC</a></td>
      <td><span class="sec-score-badge" style="background:${color};">${r.score}</span></td>
      <td class="sec-gist-cell">${escHtml(r.gist || '')}</td>
      <td><strong>${escHtml(contactName)}</strong><br>${escHtml(contactTitle)}${contactEmail ? '<br><a href="mailto:' + escHtml(contactEmail) + '" class="sec-board-email">' + escHtml(contactEmail) + '</a>' : ''}</td>
      <td class="sec-board-cell">${boardHtml}</td>
      <td><button class="btn btn-primary btn-sm sec-memo-btn" data-ticker="${ticker}" data-date="${escHtml(r.analysis_date || '')}" data-company="${escHtml(r.company_name || '')}"${hasMemo}>Memo</button></td>
      <td><button class="btn-purple btn-sm sec-letter-btn" data-ticker="${ticker}" data-date="${escHtml(r.analysis_date || '')}" data-company="${escHtml(r.company_name || '')}"${hasLetter}>Letter</button></td>
    </tr>`;
  }).join('');

  // Enable sorting
  stampOriginalOrder(tableEl);
  enableTableSorting(tableEl);

  // Wire up memo/letter buttons
  tableBody.querySelectorAll('.sec-memo-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      openDocument('memo', btn.dataset.date, btn.dataset.ticker, btn.dataset.company);
    });
  });
  tableBody.querySelectorAll('.sec-letter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      openDocument('letter', btn.dataset.date, btn.dataset.ticker, btn.dataset.company);
    });
  });
}

// ── Document modal ─────────────────────────────────────────────────

async function openDocument(type, date, ticker, companyName) {
  const label = type === 'memo' ? 'Memo' : 'Letter';
  modalTitle.textContent = `${label} — ${companyName}`;
  modalBody.textContent = 'Loading...';
  modal.classList.remove('hidden');

  try {
    const data = await apiGet(`/api/sec-leads/reports/${date}/${ticker}/${type}`);
    const text = type === 'memo' ? data.memo_text : data.letter_text;
    modalBody.textContent = text || `No ${type} available for this company.`;
  } catch (err) {
    modalBody.textContent = `Error loading ${type}: ${err.message}`;
  }
}

function closeModal() {
  modal.classList.add('hidden');
}

function copyToClipboard() {
  const text = modalBody.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const orig = modalCopyBtn.textContent;
    modalCopyBtn.textContent = 'Copied!';
    setTimeout(() => { modalCopyBtn.textContent = orig; }, 2000);
  }).catch(() => {
    showStatus(statusEl, 'Failed to copy to clipboard', 'error');
  });
}

// ── Event listeners ────────────────────────────────────────────────

loadBtn.addEventListener('click', () => {
  const dateStr = datePicker.value;
  if (dateStr) loadReport(dateStr);
});

modalCloseBtn.addEventListener('click', closeModal);
modalBackdrop?.addEventListener('click', closeModal);
modalCopyBtn.addEventListener('click', copyToClipboard);

// Close modal with Escape key
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
    closeModal();
  }
});

// ── Lazy load on first tab visit ───────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.dataset.tab === 'sec-leads' && !loaded) {
      loaded = true;
      loadReportDates();
    }
  });
});
