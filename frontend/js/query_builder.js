/**
 * query_builder.js – Boolean Query Builder panel logic.
 */

import { apiPost, apiGet, setLoading, buildPatentTable, escHtml } from './app.js';

const conditionsContainer = document.getElementById('qb-conditions');
const addBtn              = document.getElementById('qb-add-btn');
const executeBtn          = document.getElementById('qb-execute-btn');
const resultsDiv          = document.getElementById('qb-results');
const logicBtns           = document.querySelectorAll('.toggle-btn');

let availableFields    = [];
let availableOperators = [];
let activeLogic        = 'AND';

// ---- Bootstrap -----------------------------------------------------------

(async function init() {
  try {
    const meta = await apiGet('/query/fields');
    availableFields    = meta.fields    ?? [];
    availableOperators = meta.operators ?? ['CONTAINS', 'EQUALS', 'STARTS_WITH', 'ENDS_WITH'];
  } catch (_) {
    availableFields    = ['patent_number', 'invention_title', 'grant_date', 'applicant_name', 'applicant_city', 'applicant_state', 'applicant_country', 'applicant_entity_type'];
    availableOperators = ['CONTAINS', 'EQUALS', 'STARTS_WITH', 'ENDS_WITH'];
  }
  addConditionRow();   // start with one row
})();

// ---- Logic toggle --------------------------------------------------------

logicBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    activeLogic = btn.dataset.logic;
    logicBtns.forEach(b => b.classList.toggle('active', b === btn));
  });
});

// ---- Add condition row ---------------------------------------------------

function addConditionRow(defaultField = '', defaultOp = 'CONTAINS', defaultVal = '') {
  const row = document.createElement('div');
  row.className = 'qb-row';

  const fieldSel = document.createElement('select');
  fieldSel.className = 'qb-field';
  fieldSel.setAttribute('aria-label', 'Field');
  availableFields.forEach(f => {
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
  valueInput.placeholder = 'Value…';
  valueInput.className = 'qb-value';
  valueInput.setAttribute('aria-label', 'Value');
  valueInput.value = defaultVal;

  const removeBtn = document.createElement('button');
  removeBtn.className = 'btn btn-danger';
  removeBtn.setAttribute('aria-label', 'Remove condition');
  removeBtn.textContent = '✕';
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

  setLoading(executeBtn, true);
  resultsDiv.classList.add('hidden');

  try {
    const result = await apiPost('/query/execute', { conditions, logic: activeLogic, limit });
    resultsDiv.innerHTML = `
      <div class="results-header">
        <strong>Query Results</strong>
        <span class="results-count">${result.total_rows} record(s) returned</span>
      </div>
      ${buildPatentTable(result.rows)}`;
    resultsDiv.classList.remove('hidden');
  } catch (err) {
    resultsDiv.innerHTML = `<p class="status-msg error">${escHtml(err.message)}</p>`;
    resultsDiv.classList.remove('hidden');
  } finally {
    setLoading(executeBtn, false);
  }
});
