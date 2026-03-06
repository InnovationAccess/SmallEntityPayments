/**
 * mdm.js – MDM Entity Name Normalization workspace (Tab 1).
 *
 * Implements the copy/paste normalization workflow with boolean search,
 * frequency sorting, address modal, and batch operations.
 */

import { apiPost, apiDelete, setLoading, showStatus, escHtml } from './app.js';

// ---- DOM references -------------------------------------------------------
const searchInput   = document.getElementById('mdm-search-input');
const searchBtn     = document.getElementById('mdm-search-btn');
const workspace     = document.getElementById('mdm-workspace');
const tableBody     = document.getElementById('mdm-table-body');
const resultsCount  = document.getElementById('mdm-results-count');
const activeRepSpan = document.getElementById('mdm-active-rep');
const statusEl      = document.getElementById('mdm-status');

// Address modal
const addressModal     = document.getElementById('address-modal');
const addressModalName = document.getElementById('address-modal-name');
const addressList      = document.getElementById('address-list');
const addrSearchBtn    = document.getElementById('addr-search-btn');
const addrUnselectBtn  = document.getElementById('addr-unselect-btn');
const addrCloseBtn     = document.getElementById('addr-close-btn');

// Address results modal
const addrResultsModal    = document.getElementById('address-results-modal');
const addrResultsBody     = document.getElementById('addr-results-body');
const addrResultsCloseBtn = document.getElementById('addr-results-close-btn');

// ---- State ----------------------------------------------------------------
let workspaceData      = [];    // [{raw_name, frequency, representative_name}]
let activeRepresentative = null; // currently "copied" representative name
let selectedRows       = new Set(); // indices of checkbox-selected rows
let lastCheckboxIndex  = null;
let sortColumn         = 'frequency';
let sortDirection      = 'desc';

// Address modal state
let addressData           = [];    // [{street_address, city}]
let selectedAddresses     = new Set();
let lastAddrCheckboxIndex = null;
let addressEntityName     = '';

// Address results modal state
let addrResultsData      = [];
let addrSelectedRows     = new Set();
let addrLastCheckboxIndex = null;

// ---- Search ---------------------------------------------------------------

searchBtn.addEventListener('click', handleSearch);
searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') handleSearch();
});

async function handleSearch() {
  const query = searchInput.value.trim();
  if (!query) {
    showStatus(statusEl, 'Please enter a search expression.', 'error');
    return;
  }

  setLoading(searchBtn, true);
  workspace.classList.add('hidden');

  try {
    workspaceData = await apiPost('/mdm/search', { query });
    selectedRows.clear();
    lastCheckboxIndex = null;
    sortData();
    renderTable();
    workspace.classList.remove('hidden');
    resultsCount.textContent = `${workspaceData.length} unique name(s)`;
  } catch (err) {
    showStatus(statusEl, err.message, 'error');
  } finally {
    setLoading(searchBtn, false);
  }
}

// ---- Sorting --------------------------------------------------------------

document.querySelectorAll('#mdm-table .sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (sortColumn === col) {
      sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
      sortColumn = col;
      sortDirection = col === 'frequency' ? 'desc' : 'asc';
    }
    // Update header classes.
    document.querySelectorAll('#mdm-table .sortable').forEach(h => {
      h.classList.remove('sort-asc', 'sort-desc');
    });
    th.classList.add(sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');

    sortData();
    renderTable();
  });
});

function sortData() {
  workspaceData.sort((a, b) => {
    let va = a[sortColumn] ?? '';
    let vb = b[sortColumn] ?? '';
    if (sortColumn === 'frequency') {
      va = Number(va); vb = Number(vb);
    } else {
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
    }
    if (va < vb) return sortDirection === 'asc' ? -1 : 1;
    if (va > vb) return sortDirection === 'asc' ? 1 : -1;
    return 0;
  });
}

// ---- Table rendering ------------------------------------------------------

function renderTable() {
  updateActiveRepDisplay();
  const html = workspaceData.map((row, i) => buildRow(row, i)).join('');
  tableBody.innerHTML = html;
}

function buildRow(row, index) {
  const isActiveRep  = activeRepresentative && row.raw_name === activeRepresentative;
  const isRep        = !isActiveRep && row.representative_name && row.representative_name === row.raw_name;
  const isNormalized = !isActiveRep && !isRep && !!row.representative_name;
  const isSelected   = selectedRows.has(index);

  let nameClass = '';
  if (isActiveRep)       nameClass = 'name-active-rep';
  else if (isRep)        nameClass = 'name-is-rep';
  else if (isNormalized) nameClass = 'name-normalized';

  const rowClass = isSelected ? 'mdm-row-selected' : '';

  const hasRep = !!row.representative_name;

  return `<tr class="${rowClass}" data-index="${index}">
    <td><input type="checkbox" class="mdm-checkbox" data-action="checkbox"
        ${isSelected ? 'checked' : ''} /></td>
    <td><button class="mdm-icon" data-action="address" title="View addresses">&#127968;</button></td>
    <td class="${nameClass}">${escHtml(row.raw_name)}</td>
    <td><button class="mdm-icon" data-action="copy" title="Select as Representative">&#128203;</button></td>
    <td><button class="mdm-icon" data-action="paste" title="Associate with Representative"
        ${!activeRepresentative ? 'disabled' : ''}>&#128204;</button></td>
    <td>${row.frequency}</td>
    <td class="${hasRep ? 'name-is-rep' : ''}">${escHtml(row.representative_name ?? '')}</td>
    <td>${hasRep
      ? `<button class="mdm-icon" data-action="copy-rep" title="Select this Representative">&#128203;</button>`
      : ''}</td>
    <td>${hasRep
      ? `<button class="mdm-icon" data-action="trash" title="Delete association">&#128465;</button>`
      : ''}</td>
  </tr>`;
}

function updateActiveRepDisplay() {
  if (activeRepresentative) {
    activeRepSpan.innerHTML = `Active Representative: <strong style="color:#dc2626">${escHtml(activeRepresentative)}</strong>`;
  } else {
    activeRepSpan.textContent = '';
  }
}

// ---- Table event delegation -----------------------------------------------

tableBody.addEventListener('click', e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;

  const tr    = btn.closest('tr');
  const index = Number(tr.dataset.index);
  const row   = workspaceData[index];
  const action = btn.dataset.action;

  switch (action) {
    case 'checkbox':  handleCheckbox(index, e); return;
    case 'address':   openAddressModal(row.raw_name); return;
    case 'copy':      setActiveRepresentative(row.raw_name); return;
    case 'paste':     associateNames(index); return;
    case 'copy-rep':  setActiveRepresentative(row.representative_name); return;
    case 'trash':     deleteAssociation(row.raw_name, index); return;
  }
});

// ---- Checkbox multi-select with Ctrl+Click range --------------------------

function handleCheckbox(index, event) {
  const checkbox = event.target;

  if (event.ctrlKey && lastCheckboxIndex !== null) {
    // Range selection.
    const start = Math.min(lastCheckboxIndex, index);
    const end   = Math.max(lastCheckboxIndex, index);
    for (let i = start; i <= end; i++) {
      selectedRows.add(i);
    }
    renderTable();
  } else {
    // Single toggle.
    if (checkbox.checked) {
      selectedRows.add(index);
    } else {
      selectedRows.delete(index);
    }
    // Update row highlight without full re-render.
    const tr = checkbox.closest('tr');
    tr.classList.toggle('mdm-row-selected', selectedRows.has(index));
  }

  lastCheckboxIndex = index;
}

// ---- Copy (select representative) ----------------------------------------

function setActiveRepresentative(name) {
  activeRepresentative = name;
  renderTable();
}

// ---- Paste (associate names) ----------------------------------------------

async function associateNames(clickedIndex) {
  if (!activeRepresentative) return;

  // Determine which names to associate.
  let indices;
  if (selectedRows.size > 0 && selectedRows.has(clickedIndex)) {
    indices = [...selectedRows];
  } else {
    indices = [clickedIndex];
  }

  const names = indices.map(i => workspaceData[i].raw_name);

  try {
    await apiPost('/mdm/associate', {
      representative_name: activeRepresentative,
      associated_names: names,
    });

    // Update local state.
    for (const i of indices) {
      workspaceData[i].representative_name = activeRepresentative;
    }
    // Ensure the representative itself is marked.
    const repRow = workspaceData.find(r => r.raw_name === activeRepresentative);
    if (repRow) repRow.representative_name = activeRepresentative;

    selectedRows.clear();
    renderTable();
    showStatus(statusEl, `${names.length} name(s) associated with "${activeRepresentative}".`, 'success');
  } catch (err) {
    showStatus(statusEl, err.message, 'error');
  }
}

// ---- Trash (delete association) -------------------------------------------

async function deleteAssociation(name, index) {
  try {
    const result = await apiDelete('/mdm/associate', { associated_name: name });

    if (result.was_representative) {
      // Un-associate all names that had this as representative.
      for (const row of workspaceData) {
        if (row.representative_name === name) {
          row.representative_name = null;
        }
      }
    } else {
      workspaceData[index].representative_name = null;
    }

    // If the deleted name was the active representative, clear it.
    if (activeRepresentative === name) {
      activeRepresentative = null;
    }

    renderTable();
    showStatus(statusEl, `Association removed for "${name}".`, 'success');
  } catch (err) {
    showStatus(statusEl, err.message, 'error');
  }
}

// ---- Address Modal --------------------------------------------------------

async function openAddressModal(name) {
  addressEntityName = name;
  addressModalName.textContent = name;
  selectedAddresses.clear();
  lastAddrCheckboxIndex = null;

  addressList.innerHTML = '<p class="text-muted" style="padding:.5rem">Loading\u2026</p>';
  addressModal.classList.remove('hidden');

  try {
    addressData = await apiPost('/mdm/addresses', { name });
    renderAddressList();
  } catch (err) {
    addressList.innerHTML = `<p class="status-msg error">${escHtml(err.message)}</p>`;
  }
}

function renderAddressList() {
  if (!addressData.length) {
    addressList.innerHTML = '<p class="text-muted" style="padding:.5rem">No addresses found.</p>';
    return;
  }

  addressList.innerHTML = addressData.map((addr, i) => {
    const label = [addr.street_address, addr.city].filter(Boolean).join(', ') || '(no address)';
    const checked = selectedAddresses.has(i) ? 'checked' : '';
    return `<div class="address-item" data-index="${i}">
      <input type="checkbox" class="address-checkbox" ${checked} />
      <span>${escHtml(label)}</span>
    </div>`;
  }).join('');
}

// Address checkbox event delegation.
addressList.addEventListener('click', e => {
  const checkbox = e.target.closest('.address-checkbox');
  if (!checkbox) return;

  const item  = checkbox.closest('.address-item');
  const index = Number(item.dataset.index);

  if (e.ctrlKey && lastAddrCheckboxIndex !== null) {
    const start = Math.min(lastAddrCheckboxIndex, index);
    const end   = Math.max(lastAddrCheckboxIndex, index);
    for (let i = start; i <= end; i++) {
      selectedAddresses.add(i);
    }
    renderAddressList();
  } else {
    if (checkbox.checked) {
      selectedAddresses.add(index);
    } else {
      selectedAddresses.delete(index);
    }
  }

  lastAddrCheckboxIndex = index;
});

addrUnselectBtn.addEventListener('click', () => {
  selectedAddresses.clear();
  renderAddressList();
});

addrCloseBtn.addEventListener('click', () => {
  addressModal.classList.add('hidden');
});

// Close modal on backdrop click.
addressModal.querySelector('.modal-backdrop').addEventListener('click', () => {
  addressModal.classList.add('hidden');
});

// ---- Address Search → Second Modal ----------------------------------------

addrSearchBtn.addEventListener('click', async () => {
  if (selectedAddresses.size === 0) {
    showStatus(statusEl, 'Please select at least one address.', 'error');
    return;
  }

  const addresses = [...selectedAddresses].map(i => addressData[i]);

  setLoading(addrSearchBtn, true);
  try {
    addrResultsData = await apiPost('/mdm/search-by-address', { addresses });
    addrSelectedRows.clear();
    addrLastCheckboxIndex = null;
    renderAddrResultsTable();
    addrResultsModal.classList.remove('hidden');
  } catch (err) {
    showStatus(statusEl, err.message, 'error');
  } finally {
    setLoading(addrSearchBtn, false);
  }
});

function renderAddrResultsTable() {
  addrResultsBody.innerHTML = addrResultsData.map((row, i) => {
    return buildRowForModal(row, i);
  }).join('');
}

function buildRowForModal(row, index) {
  const isActiveRep  = activeRepresentative && row.raw_name === activeRepresentative;
  const isRep        = !isActiveRep && row.representative_name && row.representative_name === row.raw_name;
  const isNormalized = !isActiveRep && !isRep && !!row.representative_name;
  const isSelected   = addrSelectedRows.has(index);

  let nameClass = '';
  if (isActiveRep)       nameClass = 'name-active-rep';
  else if (isRep)        nameClass = 'name-is-rep';
  else if (isNormalized) nameClass = 'name-normalized';

  const rowClass = isSelected ? 'mdm-row-selected' : '';
  const hasRep = !!row.representative_name;

  return `<tr class="${rowClass}" data-index="${index}">
    <td><input type="checkbox" class="mdm-checkbox" data-action="modal-checkbox"
        ${isSelected ? 'checked' : ''} /></td>
    <td><button class="mdm-icon" data-action="modal-address" title="View addresses">&#127968;</button></td>
    <td class="${nameClass}">${escHtml(row.raw_name)}</td>
    <td><button class="mdm-icon" data-action="modal-copy" title="Select as Representative">&#128203;</button></td>
    <td><button class="mdm-icon" data-action="modal-paste" title="Associate"
        ${!activeRepresentative ? 'disabled' : ''}>&#128204;</button></td>
    <td>${row.frequency}</td>
    <td class="${hasRep ? 'name-is-rep' : ''}">${escHtml(row.representative_name ?? '')}</td>
    <td>${hasRep
      ? `<button class="mdm-icon" data-action="modal-copy-rep" title="Select Representative">&#128203;</button>`
      : ''}</td>
    <td>${hasRep
      ? `<button class="mdm-icon" data-action="modal-trash" title="Delete association">&#128465;</button>`
      : ''}</td>
  </tr>`;
}

// Address results modal event delegation.
addrResultsBody.addEventListener('click', async e => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;

  const tr    = btn.closest('tr');
  const index = Number(tr.dataset.index);
  const row   = addrResultsData[index];
  const action = btn.dataset.action;

  switch (action) {
    case 'modal-checkbox':
      handleModalCheckbox(index, e);
      return;
    case 'modal-copy':
      setActiveRepresentative(row.raw_name);
      renderAddrResultsTable();
      renderTable(); // also update main table
      return;
    case 'modal-paste':
      await associateInModal(index);
      return;
    case 'modal-copy-rep':
      setActiveRepresentative(row.representative_name);
      renderAddrResultsTable();
      renderTable();
      return;
    case 'modal-trash':
      await deleteInModal(row.raw_name, index);
      return;
  }
});

function handleModalCheckbox(index, event) {
  const checkbox = event.target;
  if (event.ctrlKey && addrLastCheckboxIndex !== null) {
    const start = Math.min(addrLastCheckboxIndex, index);
    const end   = Math.max(addrLastCheckboxIndex, index);
    for (let i = start; i <= end; i++) addrSelectedRows.add(i);
    renderAddrResultsTable();
  } else {
    if (checkbox.checked) addrSelectedRows.add(index);
    else addrSelectedRows.delete(index);
    checkbox.closest('tr').classList.toggle('mdm-row-selected', addrSelectedRows.has(index));
  }
  addrLastCheckboxIndex = index;
}

async function associateInModal(clickedIndex) {
  if (!activeRepresentative) return;

  let indices;
  if (addrSelectedRows.size > 0 && addrSelectedRows.has(clickedIndex)) {
    indices = [...addrSelectedRows];
  } else {
    indices = [clickedIndex];
  }

  const names = indices.map(i => addrResultsData[i].raw_name);

  try {
    await apiPost('/mdm/associate', {
      representative_name: activeRepresentative,
      associated_names: names,
    });

    for (const i of indices) {
      addrResultsData[i].representative_name = activeRepresentative;
    }
    // Also update in main workspace if present.
    for (const name of names) {
      const main = workspaceData.find(r => r.raw_name === name);
      if (main) main.representative_name = activeRepresentative;
    }

    addrSelectedRows.clear();
    renderAddrResultsTable();
    renderTable();
    showStatus(statusEl, `${names.length} name(s) associated.`, 'success');
  } catch (err) {
    showStatus(statusEl, err.message, 'error');
  }
}

async function deleteInModal(name, index) {
  try {
    const result = await apiDelete('/mdm/associate', { associated_name: name });

    if (result.was_representative) {
      for (const row of addrResultsData) {
        if (row.representative_name === name) row.representative_name = null;
      }
      for (const row of workspaceData) {
        if (row.representative_name === name) row.representative_name = null;
      }
    } else {
      addrResultsData[index].representative_name = null;
      const main = workspaceData.find(r => r.raw_name === name);
      if (main) main.representative_name = null;
    }

    if (activeRepresentative === name) activeRepresentative = null;

    renderAddrResultsTable();
    renderTable();
    showStatus(statusEl, `Association removed for "${name}".`, 'success');
  } catch (err) {
    showStatus(statusEl, err.message, 'error');
  }
}

addrResultsCloseBtn.addEventListener('click', () => {
  addrResultsModal.classList.add('hidden');
});

addrResultsModal.querySelector('.modal-backdrop').addEventListener('click', () => {
  addrResultsModal.classList.add('hidden');
});
