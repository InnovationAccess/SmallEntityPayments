/**
 * app.js – Tab navigation and shared utilities for the USPTO Data Platform.
 */

// ---- Tab switching --------------------------------------------------------
const tabBtns   = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');

tabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.tab;

    tabBtns.forEach(b => {
      b.classList.toggle('active', b === btn);
      b.setAttribute('aria-selected', b === btn ? 'true' : 'false');
    });

    tabPanels.forEach(panel => {
      panel.classList.toggle('active', panel.id === `tab-${target}`);
    });
  });
});

// ---- Shared helpers -------------------------------------------------------

/**
 * POST JSON to the given API path and return the parsed response body.
 * Throws an Error with the server's detail message on non-2xx responses.
 */
export async function apiPost(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.detail ?? `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

/**
 * GET the given API path and return the parsed response body.
 */
export async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Show a spinner inside a button and disable it; returns a restore function. */
export function setLoading(btn, loading) {
  if (loading) {
    btn.dataset.origText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span>Loading…';
    btn.disabled = true;
  } else {
    btn.innerHTML = btn.dataset.origText ?? btn.innerHTML;
    btn.disabled = false;
  }
}

/** Show a status message element with success or error styling. */
export function showStatus(el, message, type = 'success') {
  el.textContent = message;
  el.className   = `status-msg ${type}`;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 6000);
}

/**
 * Render an array of PatentRecord objects as an HTML table.
 * Returns the HTML string.
 */
export function buildPatentTable(records) {
  if (!records || records.length === 0) {
    return '<p class="text-muted">No records found.</p>';
  }
  const rows = records.map(r => {
    const applicantHtml = (r.applicants || []).map(a => {
      const typeClass = ({SMALL: 'badge-small', MICRO: 'badge-micro', LARGE: 'badge-large'})[a.entity_type] ?? '';
      const geo = [a.city, a.state, a.country].filter(Boolean).join(', ');
      return `<span class="applicant-badge ${typeClass}" title="${geo || ''}">${escHtml(a.name ?? '')}${a.entity_type ? ` (${a.entity_type})` : ''}</span>`;
    }).join('');
    return `<tr>
      <td>${escHtml(r.patent_number)}</td>
      <td>${escHtml(r.invention_title ?? '')}</td>
      <td>${escHtml(r.grant_date ?? '')}</td>
      <td>${applicantHtml || '<span class="text-muted">—</span>'}</td>
    </tr>`;
  }).join('');

  return `<table class="data-table">
    <thead><tr>
      <th>Patent #</th>
      <th>Invention Title</th>
      <th>Grant Date</th>
      <th>Applicants</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

/** Escape HTML special characters. */
export function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
