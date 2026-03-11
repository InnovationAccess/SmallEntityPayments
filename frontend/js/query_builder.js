/**
 * query_builder.js – Boolean Query Builder panel logic (Tab 2).
 */

import { apiPost, apiGet, setLoading, buildInteractiveTable, escHtml } from './app.js';

const conditionsContainer = document.getElementById('qb-conditions');
const addBtn              = document.getElementById('qb-add-btn');
const executeBtn          = document.getElementById('qb-execute-btn');
const resultsDiv          = document.getElementById('qb-results');
const logicBtns           = document.querySelectorAll('.toggle-btn');
const tablesSelect        = document.getElementById('qb-tables');

let fieldsByTable      = {};
let activeLogic        = 'AND';

const _DATE_FIELDS = new Set(['grant_date', 'filing_date', 'recorded_date', 'event_date']);
const _CODE_FIELDS = new Set(['event_code']);
const _NAME_FIELDS_SET = new Set(['applicant_name', 'inventor_name', 'assignee_name', 'assignor_name']);

const _TEXT_OPS = ['CONTAINS', 'EQUALS', 'STARTS_WITH', 'ENDS_WITH'];
const _DATE_OPS = ['AFTER', 'BEFORE', 'EQUALS'];
const _CODE_OPS = ['EQUALS'];

// Populated at init from API; keys are field names, values are arrays of codes.
let _CODE_FIELD_DATA = {};

function getOperatorsForField(field) {
  if (_DATE_FIELDS.has(field)) return _DATE_OPS;
  if (_CODE_FIELDS.has(field)) return _CODE_OPS;
  return _TEXT_OPS;
}

// ---- Bootstrap -----------------------------------------------------------

(async function init() {
  try {
    const meta = await apiGet('/query/fields');
    fieldsByTable = meta.fields ?? {};
  } catch (_) {
    fieldsByTable = {
      patent_file_wrapper: [
        'patent_number', 'application_number', 'invention_title', 'grant_date',
        'filing_date', 'applicant_name', 'inventor_name', 'entity_status',
        'examiner_name', 'group_art_unit', 'application_type', 'application_status',
      ],
      patent_assignments: [
        'patent_number', 'recorded_date',
        'assignee_name', 'assignee_city', 'assignee_state', 'assignee_country',
        'assignor_name', 'conveyance_text', 'reel_frame',
      ],
      maintenance_fee_events: [
        'patent_number', 'application_number', 'event_code', 'event_date', 'entity_status',
      ],
    };
  }

  // Fetch code dropdown lists (non-critical).
  await apiGet('/query/event-codes')
    .then(r => { _CODE_FIELD_DATA.event_code = r.codes || []; })
    .catch(() => {});

  addConditionRow();
})();

// ---- Logic toggle --------------------------------------------------------

logicBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    activeLogic = btn.dataset.logic;
    logicBtns.forEach(b => b.classList.toggle('active', b === btn));
  });
});

// ---- Get currently available fields based on selected tables --------------

function getAvailableFields() {
  const selected = Array.from(tablesSelect.selectedOptions).map(o => o.value);
  const fields = new Set();
  for (const table of selected) {
    for (const f of (fieldsByTable[table] || [])) {
      fields.add(f);
    }
  }
  return [...fields];
}

// Update condition field dropdowns when table selection changes.
tablesSelect.addEventListener('change', () => {
  const fields = getAvailableFields();
  conditionsContainer.querySelectorAll('.qb-field').forEach(sel => {
    const currentVal = sel.value;
    sel.innerHTML = '';
    fields.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = f.replace(/_/g, ' ');
      if (f === currentVal) opt.selected = true;
      sel.appendChild(opt);
    });
  });
});

// ---- Helpers --------------------------------------------------------------

function _updateOperators(opSel, field) {
  const ops = getOperatorsForField(field);
  const prev = opSel.value;
  opSel.innerHTML = '';
  ops.forEach(op => {
    const opt = document.createElement('option');
    opt.value = op;
    opt.textContent = op.replace(/_/g, ' ');
    if (op === prev) opt.selected = true;
    opSel.appendChild(opt);
  });
}

function _fillCodeSelect(sel, field) {
  sel.innerHTML = '';
  sel.setAttribute('multiple', '');
  const codes = _CODE_FIELD_DATA[field] || [];
  sel.size = Math.min(6, codes.length || 1);
  for (const code of codes) {
    const opt = document.createElement('option');
    opt.value = code;
    opt.textContent = code;
    sel.appendChild(opt);
  }
}

// ---- Add condition row ---------------------------------------------------

function addConditionRow(defaultField = '', defaultOp = '', defaultVal = '') {
  const fields = getAvailableFields();
  const row = document.createElement('div');
  row.className = 'qb-row';

  const fieldSel = document.createElement('select');
  fieldSel.className = 'qb-field';
  fieldSel.setAttribute('aria-label', 'Field');
  fields.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f.replace(/_/g, ' ');
    if (f === defaultField) opt.selected = true;
    fieldSel.appendChild(opt);
  });

  const opSel = document.createElement('select');
  opSel.className = 'qb-operator';
  opSel.setAttribute('aria-label', 'Operator');

  // Set operators based on selected field.
  const activeField = defaultField || fieldSel.value;
  const ops = getOperatorsForField(activeField);
  ops.forEach(op => {
    const opt = document.createElement('option');
    opt.value = op;
    opt.textContent = op.replace(/_/g, ' ');
    if (op === defaultOp) opt.selected = true;
    opSel.appendChild(opt);
  });

  // Create default value input element.
  const valueInput = document.createElement('input');
  valueInput.className = 'qb-value';
  valueInput.setAttribute('aria-label', 'Value');

  const removeBtn = document.createElement('button');
  removeBtn.className = 'btn btn-danger';
  removeBtn.setAttribute('aria-label', 'Remove condition');
  removeBtn.textContent = '\u2715';
  removeBtn.addEventListener('click', () => {
    if (conditionsContainer.children.length > 1) {
      row.remove();
    }
  });

  // Assemble row (elements must be in DOM tree for querySelector).
  row.appendChild(fieldSel);
  row.appendChild(opSel);
  row.appendChild(valueInput);
  row.appendChild(removeBtn);

  // Sync the value element type based on the selected field.
  // May swap <input> ↔ <select> for code fields.
  function syncInputType() {
    const field = fieldSel.value;
    const curEl = row.querySelector('.qb-value');

    if (_CODE_FIELDS.has(field) && _CODE_FIELD_DATA[field]?.length > 0) {
      // Code field → dropdown select
      if (curEl.tagName !== 'SELECT') {
        const sel = document.createElement('select');
        sel.className = 'qb-value';
        sel.setAttribute('aria-label', 'Value');
        curEl.replaceWith(sel);
        _fillCodeSelect(sel, field);
      } else {
        _fillCodeSelect(curEl, field);
      }
    } else {
      // Text or date field → input element
      let inp = curEl;
      if (curEl.tagName !== 'INPUT') {
        inp = document.createElement('input');
        inp.className = 'qb-value';
        inp.setAttribute('aria-label', 'Value');
        curEl.replaceWith(inp);
      }
      if (_DATE_FIELDS.has(field)) {
        inp.type = 'date';
        inp.placeholder = '';
      } else {
        inp.type = 'text';
        inp.placeholder = _NAME_FIELDS_SET.has(field)
          ? 'e.g. +elect* +tele*'
          : 'Value\u2026';
      }
    }
  }

  syncInputType();
  fieldSel.addEventListener('change', syncInputType);
  fieldSel.addEventListener('change', () => _updateOperators(opSel, fieldSel.value));

  if (defaultVal) row.querySelector('.qb-value').value = defaultVal;

  conditionsContainer.appendChild(row);
}

addBtn.addEventListener('click', () => addConditionRow());

// ---- Execute query -------------------------------------------------------

executeBtn.addEventListener('click', async () => {
  const rows = conditionsContainer.querySelectorAll('.qb-row');
  const conditions = [];

  for (const row of rows) {
    const field    = row.querySelector('.qb-field').value;
    const operator = row.querySelector('.qb-operator').value;
    const valEl    = row.querySelector('.qb-value');
    let value;
    if (valEl.tagName === 'SELECT' && valEl.multiple) {
      value = Array.from(valEl.selectedOptions).map(o => o.value).filter(v => v).join(',');
    } else {
      value = valEl.value.trim();
    }
    if (!value) continue;
    conditions.push({ field, operator, value });
  }

  if (conditions.length === 0) {
    resultsDiv.innerHTML = '<p class="status-msg error">Please fill in at least one condition value.</p>';
    resultsDiv.classList.remove('hidden');
    return;
  }

  const limit = parseInt(document.getElementById('qb-limit').value, 10) || 50;
  const tables = Array.from(tablesSelect.selectedOptions).map(o => o.value);

  setLoading(executeBtn, true);
  resultsDiv.classList.add('hidden');

  try {
    const result = await apiPost('/query/execute', { conditions, logic: activeLogic, limit, tables });
    resultsDiv.innerHTML = '';
    const hdr = document.createElement('div');
    hdr.className = 'results-header';
    hdr.innerHTML = `<strong>Query Results</strong><span class="results-count">${result.total_rows} record(s) returned</span>`;
    resultsDiv.appendChild(hdr);
    buildInteractiveTable(resultsDiv, result.rows);
    resultsDiv.classList.remove('hidden');
  } catch (err) {
    resultsDiv.innerHTML = `<p class="status-msg error">${escHtml(err.message)}</p>`;
    resultsDiv.classList.remove('hidden');
  } finally {
    setLoading(executeBtn, false);
  }
});
