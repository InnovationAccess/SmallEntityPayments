/**
 * query_builder.js – Boolean Query Builder panel logic (Tab 2).
 */

import { apiPost, apiGet, setLoading, buildGenericTable, escHtml } from './app.js';

const conditionsContainer = document.getElementById('qb-conditions');
const addBtn              = document.getElementById('qb-add-btn');
const executeBtn          = document.getElementById('qb-execute-btn');
const resultsDiv          = document.getElementById('qb-results');
const logicBtns           = document.querySelectorAll('.toggle-btn');
const tablesSelect        = document.getElementById('qb-tables');

let fieldsByTable      = {};
let availableOperators = ['CONTAINS', 'EQUALS', 'STARTS_WITH', 'ENDS_WITH'];
let activeLogic        = 'AND';

// ---- Bootstrap -----------------------------------------------------------

(async function init() {
  try {
    const meta = await apiGet('/query/fields');
    fieldsByTable      = meta.fields ?? {};
    availableOperators = meta.operators ?? availableOperators;
  } catch (_) {
    fieldsByTable = {
      patent_file_wrapper: [
        'patent_number', 'invention_title', 'grant_date',
        'applicant_name', 'applicant_city', 'applicant_state',
        'applicant_country', 'applicant_entity_type',
      ],
      patent_assignments: [
        'patent_number', 'recorded_date',
        'assignee_name', 'assignee_city', 'assignee_state', 'assignee_country',
      ],
      maintenance_fee_events: [
        'patent_number', 'event_code', 'event_date', 'fee_code', 'entity_status',
      ],
    };
  }
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

// ---- Add condition row ---------------------------------------------------

function addConditionRow(defaultField = '', defaultOp = 'CONTAINS', defaultVal = '') {
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
  availableOperators.forEach(op => {
    const opt = document.createElement('option');
    opt.value = op;
    opt.textContent = op.replace(/_/g, ' ');
    if (op === defaultOp) opt.selected = true;
    opSel.appendChild(opt);
  });

  const valueInput = document.createElement('input');
  valueInput.type = 'text';
  valueInput.placeholder = 'Value\u2026';
  valueInput.className = 'qb-value';
  valueInput.setAttribute('aria-label', 'Value');
  valueInput.value = defaultVal;

  const removeBtn = document.createElement('button');
  removeBtn.className = 'btn btn-danger';
  removeBtn.setAttribute('aria-label', 'Remove condition');
  removeBtn.textContent = '\u2715';
  removeBtn.addEventListener('click', () => {
    if (conditionsContainer.children.length > 1) {
      row.remove();
    }
  });

  row.appendChild(fieldSel);
  row.appendChild(opSel);
  row.appendChild(valueInput);
  row.appendChild(removeBtn);
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
    const value    = row.querySelector('.qb-value').value.trim();
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
    resultsDiv.innerHTML = `
      <div class="results-header">
        <strong>Query Results</strong>
        <span class="results-count">${result.total_rows} record(s) returned</span>
      </div>
      ${buildGenericTable(result.rows)}`;
    resultsDiv.classList.remove('hidden');
  } catch (err) {
    resultsDiv.innerHTML = `<p class="status-msg error">${escHtml(err.message)}</p>`;
    resultsDiv.classList.remove('hidden');
  } finally {
    setLoading(executeBtn, false);
  }
});
