/**
 * mdm.js – MDM Entity Normalisation panel logic.
 */

import { apiPost, setLoading, showStatus, escHtml } from './app.js';

const searchBtn  = document.getElementById('mdm-search-btn');
const mergeBtn   = document.getElementById('mdm-merge-btn');
const resultsDiv = document.getElementById('mdm-results');
const mergeStatus = document.getElementById('mdm-merge-status');

// ---- Entity search --------------------------------------------------------

searchBtn.addEventListener('click', async () => {
  const name    = document.getElementById('mdm-name').value.trim();
  const city    = document.getElementById('mdm-city').value.trim();
  const state   = document.getElementById('mdm-state').value.trim();
  const country = document.getElementById('mdm-country').value.trim();

  if (!name && !city && !state && !country) {
    showStatus(mergeStatus, 'Please enter at least one search filter.', 'error');
    return;
  }

  setLoading(searchBtn, true);
  resultsDiv.classList.add('hidden');

  try {
    const entities = await apiPost('/mdm/search', { name: name || null, city: city || null, state: state || null, country: country || null });
    resultsDiv.innerHTML = buildEntityTable(entities);
    resultsDiv.classList.remove('hidden');
  } catch (err) {
    resultsDiv.innerHTML = `<p class="status-msg error">${escHtml(err.message)}</p>`;
    resultsDiv.classList.remove('hidden');
  } finally {
    setLoading(searchBtn, false);
  }
});

function buildEntityTable(entities) {
  if (!entities || entities.length === 0) {
    return '<p class="text-muted">No entities found matching your criteria.</p>';
  }
  const rows = entities.map(e => {
    const aliases = (e.aliases || []).map(a => `<code>${escHtml(a)}</code>`).join(', ');
    const geo = [e.city, e.state, e.country].filter(Boolean).join(', ');
    const typeClass = ({ SMALL: 'badge-small', MICRO: 'badge-micro', LARGE: 'badge-large' })[e.entity_type] ?? '';
    return `<tr>
      <td><strong>${escHtml(e.canonical_name)}</strong></td>
      <td>${aliases || '<span class="text-muted">—</span>'}</td>
      <td>${escHtml(geo || '—')}</td>
      <td>${e.entity_type ? `<span class="applicant-badge ${typeClass}">${escHtml(e.entity_type)}</span>` : '—'}</td>
    </tr>`;
  }).join('');

  return `<div class="results-header">
      <span class="results-count">${entities.length} entity record(s) found</span>
    </div>
    <table class="data-table">
      <thead><tr>
        <th>Canonical Name</th>
        <th>Aliases</th>
        <th>Location</th>
        <th>Entity Type</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ---- Entity merge / normalise --------------------------------------------

mergeBtn.addEventListener('click', async () => {
  const canonical = document.getElementById('merge-canonical').value.trim();
  if (!canonical) {
    showStatus(mergeStatus, 'Canonical name is required.', 'error');
    return;
  }

  const aliasText  = document.getElementById('merge-aliases').value;
  const aliases    = aliasText.split('\n').map(s => s.trim()).filter(Boolean);
  const city       = document.getElementById('merge-city').value.trim() || null;
  const state      = document.getElementById('merge-state').value.trim() || null;
  const country    = document.getElementById('merge-country').value.trim() || null;
  const entityType = document.getElementById('merge-entity-type').value || null;

  setLoading(mergeBtn, true);
  try {
    await apiPost('/mdm/merge', {
      canonical_name: canonical,
      aliases,
      city,
      state,
      country,
      entity_type: entityType,
    });
    showStatus(mergeStatus, `Canonical mapping saved for "${canonical}".`, 'success');
  } catch (err) {
    showStatus(mergeStatus, err.message, 'error');
  } finally {
    setLoading(mergeBtn, false);
  }
});
