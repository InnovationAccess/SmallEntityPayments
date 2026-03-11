/**
 * citations.js – Forward Citation Lookup tab.
 *
 * Calls /api/forward-citations/{patent_number} and
 * /api/forward-citations/{patent_number}/summary
 */

import { apiGet, setLoading, showStatus, escHtml, enableTableSorting, stampOriginalOrder } from './app.js';

const searchInput   = document.getElementById('cite-search-input');
const searchBtn     = document.getElementById('cite-search-btn');
const summaryCard   = document.getElementById('cite-summary');
const patentLabel   = document.getElementById('cite-patent-label');
const totalEl       = document.getElementById('cite-total');
const examinerEl    = document.getElementById('cite-examiner');
const applicantEl   = document.getElementById('cite-applicant');
const rangeEl       = document.getElementById('cite-range');
const byYearEl      = document.getElementById('cite-by-year');
const examListEl    = document.getElementById('cite-examiner-list');
const applListEl    = document.getElementById('cite-applicant-list');
const resultsArea   = document.getElementById('cite-results');
const resultsCount  = document.getElementById('cite-results-count');
const citeTable     = document.getElementById('cite-table');
const tableBody     = document.getElementById('cite-table-body');
const statusEl      = document.getElementById('cite-status');

enableTableSorting(citeTable);

function renderBreakdownList(container, items) {
  if (!items || items.length === 0) {
    container.innerHTML = '<div class="cite-breakdown-item text-muted">None</div>';
    return;
  }
  container.innerHTML = items.map(it =>
    `<div class="cite-breakdown-item">
      <span class="cite-breakdown-name" title="${escHtml(it.name)}">${escHtml(it.name)}</span>
      <span class="cite-breakdown-count">${it.count}</span>
    </div>`
  ).join('');
}

async function doSearch() {
  const raw = searchInput.value.trim();
  if (!raw) return;

  setLoading(searchBtn, true);
  summaryCard.classList.add('hidden');
  resultsArea.classList.add('hidden');

  try {
    // Fetch summary and full list in parallel
    const [summary, detail] = await Promise.all([
      apiGet(`/api/forward-citations/${encodeURIComponent(raw)}/summary`),
      apiGet(`/api/forward-citations/${encodeURIComponent(raw)}?limit=2000`),
    ]);

    // Show summary KPIs
    patentLabel.textContent = summary.cited_patent_number;
    totalEl.textContent = summary.total_citations.toLocaleString();
    examinerEl.textContent = (summary.by_category?.examiner ?? 0).toLocaleString();
    applicantEl.textContent = (summary.by_category?.applicant ?? 0).toLocaleString();

    if (summary.earliest_citing_date && summary.latest_citing_date) {
      rangeEl.textContent =
        `${summary.earliest_citing_date.substring(0, 4)} - ${summary.latest_citing_date.substring(0, 4)}`;
    } else {
      rangeEl.textContent = '-';
    }

    // By-year chart (simple bar)
    const years = summary.by_year || {};
    const yearKeys = Object.keys(years).sort();
    if (yearKeys.length > 0) {
      const maxCount = Math.max(...Object.values(years));
      byYearEl.innerHTML =
        '<h4 style="margin:0.5rem 0 0.25rem;font-size:0.85rem;color:var(--text-secondary)">Citations by Year</h4>' +
        '<div class="cite-year-chart">' +
        yearKeys.map(yr => {
          const pct = maxCount > 0 ? (years[yr] / maxCount * 100) : 0;
          return `<div class="cite-year-bar-wrap">
            <div class="cite-year-bar" style="height:${pct}%" title="${yr}: ${years[yr]}"></div>
            <span class="cite-year-label">${yr.substring(2)}</span>
          </div>`;
        }).join('') +
        '</div>';
    } else {
      byYearEl.innerHTML = '';
    }

    // Examiner and applicant breakdown lists
    renderBreakdownList(examListEl, summary.by_examiner || []);
    renderBreakdownList(applListEl, summary.by_applicant || []);

    summaryCard.classList.remove('hidden');

    // Show citation table
    const citations = detail.citations || [];
    resultsCount.textContent = `(${citations.length.toLocaleString()} shown)`;

    if (citations.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="6" class="text-muted">No forward citations found.</td></tr>';
    } else {
      tableBody.innerHTML = citations.map(c => `<tr>
        <td>${escHtml(c.citing_patent_number || '')}</td>
        <td>${escHtml(c.citing_application_number || '')}</td>
        <td>${escHtml(c.citing_filing_date || '')}</td>
        <td><span class="cite-cat cite-cat-${escHtml(c.citation_category || 'unknown')}">${escHtml(c.citation_category || '')}</span></td>
        <td>${escHtml(c.citing_applicant_name || '')}</td>
        <td>${escHtml(c.citing_examiner_name || '')}</td>
      </tr>`).join('');
      stampOriginalOrder(citeTable);
    }

    resultsArea.classList.remove('hidden');

  } catch (err) {
    showStatus(statusEl, `Error: ${err.message}`, 'error');
  } finally {
    setLoading(searchBtn, false);
  }
}

searchBtn.addEventListener('click', doSearch);
searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});
