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
 * DELETE JSON to the given API path and return the parsed response body.
 */
export async function apiDelete(path, body) {
  const res = await fetch(path, {
    method: 'DELETE',
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
    btn.innerHTML = '<span class="spinner"></span>Loading\u2026';
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
 * Enable click-to-sort on any static <table>.
 *
 * Each <th> that should be sortable needs `data-sort-key="<colIndex>"` where
 * colIndex is the 0-based column position in the table.  The function reads
 * rows from <tbody>, detaches them, sorts, and reattaches.
 *
 * Adds the `.sortable` class and sort-direction indicators automatically.
 *
 * Usage:
 *   enableTableSorting(document.getElementById('my-table'));
 */
export function enableTableSorting(tableEl, onSort) {
  if (!tableEl) return;
  const thead = tableEl.querySelector('thead');
  const tbody = tableEl.querySelector('tbody');
  if (!thead || !tbody) return;

  let sortCol = null;
  let sortDir = 0; // 0=none, 1=asc, -1=desc

  const ths = thead.querySelectorAll('th[data-sort-key]');
  ths.forEach(th => {
    th.classList.add('sortable');
    th.addEventListener('click', () => {
      const colIdx = parseInt(th.dataset.sortKey, 10);

      // Toggle direction
      if (sortCol === colIdx) {
        sortDir = sortDir === 1 ? -1 : sortDir === -1 ? 0 : 1;
      } else {
        sortCol = colIdx;
        sortDir = 1;
      }

      // Update visual indicators
      ths.forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
      if (sortDir !== 0) {
        th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
      }

      // If external sort callback provided, delegate to it
      if (typeof onSort === 'function') {
        onSort(sortDir === 0 ? null : colIdx, sortDir);
        return;
      }

      // Sort rows
      const rows = Array.from(tbody.querySelectorAll('tr'));
      if (sortDir === 0) {
        // Restore original order via data-orig-idx
        rows.sort((a, b) => (a._origIdx ?? 0) - (b._origIdx ?? 0));
      } else {
        rows.sort((a, b) => {
          const cellA = a.cells[colIdx]?.textContent.trim() ?? '';
          const cellB = b.cells[colIdx]?.textContent.trim() ?? '';
          // Try numeric comparison first (strip commas from locale-formatted numbers)
          const numA = parseFloat(cellA.replace(/,/g, ''));
          const numB = parseFloat(cellB.replace(/,/g, ''));
          if (!isNaN(numA) && !isNaN(numB)) return sortDir * (numA - numB);
          return sortDir * cellA.localeCompare(cellB, undefined, { numeric: true });
        });
      }

      // Reattach sorted rows
      rows.forEach(r => tbody.appendChild(r));
    });
  });
}

/**
 * Stamp original row indices on current tbody rows so we can restore
 * the original order after sorting.  Call this after populating the tbody.
 */
export function stampOriginalOrder(tableEl) {
  if (!tableEl) return;
  const tbody = tableEl.querySelector('tbody');
  if (!tbody) return;
  Array.from(tbody.querySelectorAll('tr')).forEach((tr, i) => {
    tr._origIdx = i;
  });
}

/**
 * Render an array of objects as a dynamic HTML table.
 * Infers columns from the keys of the first row.
 */
export function buildGenericTable(rows) {
  if (!rows || rows.length === 0) {
    return '<p class="text-muted">No records found.</p>';
  }

  const columns = Object.keys(rows[0]).filter(k => {
    // Skip nested arrays/objects for column display (but allow null)
    const sample = rows[0][k];
    return sample === null || (!Array.isArray(sample) && typeof sample !== 'object');
  });

  const headerCells = columns.map(c =>
    `<th>${escHtml(c.replace(/_/g, ' '))}</th>`
  ).join('');

  const bodyRows = rows.map(r => {
    const cells = columns.map(c => `<td>${escHtml(String(r[c] ?? ''))}</td>`).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  return `<div class="table-scroll-wrap"><table class="data-table">
    <thead><tr>${headerCells}</tr></thead>
    <tbody>${bodyRows}</tbody>
  </table></div>`;
}

/** Escape HTML special characters. */
export function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Render an interactive table into *container* with sortable columns and
 * a column-visibility dropdown.  Appends elements to the container (does
 * NOT clear it — caller should set innerHTML='' first if needed).
 */
export function buildInteractiveTable(container, rows) {
  if (!rows || rows.length === 0) {
    const p = document.createElement('p');
    p.className = 'text-muted';
    p.textContent = 'No records found.';
    container.appendChild(p);
    return;
  }

  const columns = Object.keys(rows[0]).filter(k => {
    const sample = rows[0][k];
    return sample === null || (!Array.isArray(sample) && typeof sample !== 'object');
  });

  const visibleCols = new Set(columns);
  let sortCol = null;
  let sortDir = 0; // 0=none, 1=asc, -1=desc

  // ---- Toolbar with column picker ----
  const toolbar = document.createElement('div');
  toolbar.className = 'table-toolbar';

  const pickerWrap = document.createElement('div');
  pickerWrap.className = 'col-picker-wrap';

  const pickerBtn = document.createElement('button');
  pickerBtn.className = 'btn btn-secondary col-picker-btn';
  pickerBtn.textContent = 'Columns \u25BE';

  const pickerMenu = document.createElement('div');
  pickerMenu.className = 'col-picker-menu hidden';

  columns.forEach(col => {
    const lbl = document.createElement('label');
    lbl.className = 'col-picker-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.addEventListener('change', () => {
      if (cb.checked) visibleCols.add(col); else visibleCols.delete(col);
      toggleCol(col, cb.checked);
    });
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(' ' + col.replace(/_/g, ' ')));
    pickerMenu.appendChild(lbl);
  });

  pickerBtn.addEventListener('click', e => {
    e.stopPropagation();
    document.querySelectorAll('.col-picker-menu').forEach(m => m.classList.add('hidden'));
    pickerMenu.classList.toggle('hidden');
  });
  pickerMenu.addEventListener('click', e => e.stopPropagation());

  pickerWrap.appendChild(pickerBtn);
  pickerWrap.appendChild(pickerMenu);
  toolbar.appendChild(pickerWrap);

  // ---- Table ----
  const tableWrap = document.createElement('div');
  tableWrap.className = 'table-scroll-wrap';

  const table = document.createElement('table');
  table.className = 'data-table';

  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  columns.forEach(col => {
    const th = document.createElement('th');
    th.className = 'sortable';
    th.dataset.col = col;
    th.textContent = col.replace(/_/g, ' ');
    th.addEventListener('click', () => doSort(col));
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  table.appendChild(tbody);

  let currentRows = [...rows];

  function renderBody() {
    const html = currentRows.map(r => {
      return '<tr>' + columns.map(c => {
        const hide = visibleCols.has(c) ? '' : ' style="display:none"';
        return `<td data-col="${c}"${hide}>${escHtml(String(r[c] ?? ''))}</td>`;
      }).join('') + '</tr>';
    }).join('');
    tbody.innerHTML = html;
  }

  function doSort(col) {
    if (sortCol === col) {
      sortDir = sortDir === 1 ? -1 : sortDir === -1 ? 0 : 1;
    } else {
      sortCol = col;
      sortDir = 1;
    }
    headerRow.querySelectorAll('th').forEach(th => {
      th.classList.remove('sort-asc', 'sort-desc');
      if (th.dataset.col === sortCol && sortDir !== 0) {
        th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
      }
    });
    if (sortDir === 0) {
      currentRows = [...rows];
      sortCol = null;
    } else {
      currentRows = [...rows].sort((a, b) => {
        const va = a[col] ?? '';
        const vb = b[col] ?? '';
        if (typeof va === 'number' && typeof vb === 'number') return sortDir * (va - vb);
        return sortDir * String(va).localeCompare(String(vb), undefined, { numeric: true });
      });
    }
    renderBody();
  }

  function toggleCol(col, show) {
    const th = headerRow.querySelector(`th[data-col="${col}"]`);
    if (th) th.style.display = show ? '' : 'none';
    tbody.querySelectorAll(`td[data-col="${col}"]`).forEach(td => {
      td.style.display = show ? '' : 'none';
    });
  }

  renderBody();
  tableWrap.appendChild(table);
  container.appendChild(toolbar);
  container.appendChild(tableWrap);

  // Close column picker on outside click (one-time global handler).
  if (!window._colPickerGlobalHandler) {
    document.addEventListener('click', () => {
      document.querySelectorAll('.col-picker-menu').forEach(m => m.classList.add('hidden'));
    });
    window._colPickerGlobalHandler = true;
  }
}

/**
 * Add a column-visibility dropdown to an existing static table.
 * Place it right above the table's scroll wrapper.
 *
 * @param {HTMLTableElement} tableEl  – the <table> element
 * @param {Object} [opts]
 * @param {string[]} [opts.defaultHidden] – column header text to hide by default
 */
export function addColumnPicker(tableEl, opts = {}) {
  if (!tableEl) return;
  const thead = tableEl.querySelector('thead');
  if (!thead) return;

  const ths = Array.from(thead.querySelectorAll('th'));
  if (ths.length === 0) return;

  // Don't add a picker twice
  const parentWrap = tableEl.closest('.table-scroll-wrap') || tableEl.parentElement;
  if (parentWrap.previousElementSibling?.classList.contains('table-toolbar')) return;

  const defaultHidden = new Set((opts.defaultHidden || []).map(s => s.toLowerCase()));

  const toolbar = document.createElement('div');
  toolbar.className = 'table-toolbar';

  const pickerWrap = document.createElement('div');
  pickerWrap.className = 'col-picker-wrap';

  const pickerBtn = document.createElement('button');
  pickerBtn.className = 'btn btn-secondary col-picker-btn';
  pickerBtn.textContent = 'Columns \u25BE';

  const pickerMenu = document.createElement('div');
  pickerMenu.className = 'col-picker-menu hidden';

  ths.forEach((th, idx) => {
    const label = th.textContent.trim();
    if (!label) return; // skip icon-only columns

    const lbl = document.createElement('label');
    lbl.className = 'col-picker-item';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    const isHidden = defaultHidden.has(label.toLowerCase());
    cb.checked = !isHidden;

    cb.addEventListener('change', () => {
      toggleStaticCol(tableEl, idx, cb.checked);
    });

    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(' ' + label));
    pickerMenu.appendChild(lbl);

    // Apply default hidden
    if (isHidden) toggleStaticCol(tableEl, idx, false);
  });

  pickerBtn.addEventListener('click', e => {
    e.stopPropagation();
    document.querySelectorAll('.col-picker-menu').forEach(m => m.classList.add('hidden'));
    pickerMenu.classList.toggle('hidden');
  });
  pickerMenu.addEventListener('click', e => e.stopPropagation());

  pickerWrap.appendChild(pickerBtn);
  pickerWrap.appendChild(pickerMenu);
  toolbar.appendChild(pickerWrap);

  parentWrap.parentElement.insertBefore(toolbar, parentWrap);

  // Close on outside click
  if (!window._colPickerGlobalHandler) {
    document.addEventListener('click', () => {
      document.querySelectorAll('.col-picker-menu').forEach(m => m.classList.add('hidden'));
    });
    window._colPickerGlobalHandler = true;
  }
}

function toggleStaticCol(tableEl, colIdx, show) {
  const displayVal = show ? '' : 'none';
  // Header
  const ths = tableEl.querySelectorAll('thead th');
  if (ths[colIdx]) ths[colIdx].style.display = displayVal;
  // Body rows
  tableEl.querySelectorAll('tbody tr').forEach(tr => {
    if (tr.cells[colIdx]) tr.cells[colIdx].style.display = displayVal;
  });
}

// ---- Assignment chain popup (draggable + resizable) -----------------------

const _chainCache = {};   // patent_number -> { assignments: [...] }
let _chainPopup = null;   // the single popup element
let _chainHideTimer = null;
let _chainShowTimer = null;
let _chainDrag = null;    // drag state

function getOrCreatePopup() {
  if (_chainPopup) return _chainPopup;
  const el = document.createElement('div');
  el.id = 'assignment-chain-popup';
  el.className = 'chain-popup hidden';
  // Keep popup open while mouse is inside it
  el.addEventListener('mouseenter', () => clearTimeout(_chainHideTimer));
  el.addEventListener('mouseleave', () => hideChainPopup());
  document.body.appendChild(el);
  _chainPopup = el;

  // ---- Drag support on the header ----
  el.addEventListener('mousedown', e => {
    const header = e.target.closest('.chain-header');
    if (!header || e.target.closest('.chain-close-btn')) return;
    e.preventDefault();
    _chainDrag = {
      startX: e.clientX,
      startY: e.clientY,
      origLeft: el.offsetLeft,
      origTop: el.offsetTop,
    };
  });

  document.addEventListener('mousemove', e => {
    if (!_chainDrag) return;
    e.preventDefault();
    const dx = e.clientX - _chainDrag.startX;
    const dy = e.clientY - _chainDrag.startY;
    el.style.left = `${_chainDrag.origLeft + dx}px`;
    el.style.top  = `${_chainDrag.origTop  + dy}px`;
  });

  document.addEventListener('mouseup', () => { _chainDrag = null; });

  return el;
}

function hideChainPopup() {
  clearTimeout(_chainShowTimer);
  _chainHideTimer = setTimeout(() => {
    const popup = getOrCreatePopup();
    popup.classList.add('hidden');
  }, 200);
}

function closeChainPopup() {
  clearTimeout(_chainShowTimer);
  clearTimeout(_chainHideTimer);
  const popup = getOrCreatePopup();
  popup.classList.add('hidden');
}

async function showChainPopup(patentNum, anchorEl) {
  clearTimeout(_chainHideTimer);
  clearTimeout(_chainShowTimer);

  // Short delay to avoid flashing on accidental hover
  _chainShowTimer = setTimeout(async () => {
    const popup = getOrCreatePopup();

    // Position to the right of the anchor — fill viewport from anchor to right edge
    const rect = anchorEl.getBoundingClientRect();
    const vpH = window.innerHeight;
    const vpW = window.innerWidth;
    const gap = 8;

    const leftPx = rect.right + window.scrollX + gap;
    const topPx = window.scrollY + gap;
    const widthPx = vpW - rect.right - gap * 2;
    const heightPx = vpH - gap * 2;

    popup.style.top = `${topPx}px`;
    popup.style.left = `${leftPx}px`;
    popup.style.width = `${widthPx}px`;
    popup.style.height = `${heightPx}px`;

    popup.innerHTML = '<div class="chain-header"><span>Loading\u2026</span><button class="chain-close-btn" title="Close">\u00D7</button></div><div class="chain-loading">Loading assignments\u2026</div>';
    popup.classList.remove('hidden');
    popup.querySelector('.chain-close-btn').addEventListener('click', closeChainPopup);

    // Fetch (cached)
    if (!_chainCache[patentNum]) {
      try {
        const data = await apiGet(`/api/assignments/${encodeURIComponent(patentNum)}/chain`);
        _chainCache[patentNum] = data;
      } catch (err) {
        popup.innerHTML = `<div class="chain-header"><span>Error</span><button class="chain-close-btn" title="Close">\u00D7</button></div><div class="chain-loading">Error: ${escHtml(err.message)}</div>`;
        popup.querySelector('.chain-close-btn').addEventListener('click', closeChainPopup);
        return;
      }
    }

    const chain = _chainCache[patentNum].assignments || [];
    if (chain.length === 0) {
      popup.innerHTML = `<div class="chain-header"><span>Patent ${escHtml(patentNum)}</span><button class="chain-close-btn" title="Close">\u00D7</button></div><div class="chain-loading">No assignment records found.</div>`;
      popup.querySelector('.chain-close-btn').addEventListener('click', closeChainPopup);
      return;
    }

    /* Split semicolon-separated names into one-per-line for readability */
    const multiLine = (s) =>
      (s || '').split('; ').map(n => escHtml(n.trim())).filter(Boolean).join('<br>');

    const rows = chain.map(a =>
      `<tr>
        <td class="chain-td-date">${escHtml(a.execution_date || '\u2014')}</td>
        <td>${multiLine(a.assignor)}</td>
        <td>${escHtml(a.normalized_type || '\u2014')}</td>
        <td>${escHtml(a.conveyance)}</td>
        <td>${multiLine(a.assignee)}</td>
      </tr>`
    ).join('');

    popup.innerHTML = `
      <div class="chain-header">
        <span>Assignment Chain \u2014 Patent ${escHtml(patentNum)}</span>
        <button class="chain-close-btn" title="Close">\u00D7</button>
      </div>
      <div class="chain-table-wrap">
        <table class="chain-table">
          <thead><tr>
            <th>Date</th><th>Assignor</th><th>Type</th><th>Conveyance Text</th><th>Assignee</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    popup.querySelector('.chain-close-btn').addEventListener('click', closeChainPopup);
  }, 300);
}

/**
 * Make patent number cells in a table trigger the assignment chain popup.
 *
 * Call after populating the table body.  Pass a CSS selector that identifies
 * the patent-number cells (e.g. '#cite-table-body td:nth-child(1)').
 */
export function enableAssignmentPopup(selector) {
  document.querySelectorAll(selector).forEach(td => {
    const patentNum = td.textContent.trim();
    if (!patentNum) return;

    td.classList.add('patent-link');
    td.addEventListener('mouseenter', () => showChainPopup(patentNum, td));
    td.addEventListener('mouseleave', () => hideChainPopup());
  });
}
