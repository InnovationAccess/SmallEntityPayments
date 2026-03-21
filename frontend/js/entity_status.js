/**
 * entity_status.js – Entity Status Analytics tab.
 *
 * Provides three analysis modes:
 *   1. Single patent lookup: entity status timeline with conversion detection
 *   2. Conversion search: find patents that changed from small to large
 *   3. Applicant portfolio: entity status breakdown for one company's patents
 *
 * KPI numbers are clickable — clicking filters the Patent Details table to
 * show only patents associated with those events, and renders inline micro
 * chart sparklines showing each patent's full event history.
 */

import {
  apiGet, apiPost, setLoading, showStatus, escHtml,
  enableTableSorting, stampOriginalOrder, enableAssignmentPopup, addColumnPicker,
} from './app.js';

// ── DOM References ───────────────────────────────────────────────

const patentInput     = document.getElementById('es-patent-input');
const patentBtn       = document.getElementById('es-patent-btn');
const convFromStatus  = document.getElementById('es-from-status');
const convToStatus    = document.getElementById('es-to-status');
const convYearStart   = document.getElementById('es-year-start');
const convYearEnd     = document.getElementById('es-year-end');
const convApplicant   = document.getElementById('es-applicant');
const convLimit       = document.getElementById('es-conv-limit');
const convBtn         = document.getElementById('es-conv-btn');
const appInput        = document.getElementById('es-app-input');
const appBtn          = document.getElementById('es-app-btn');
const convApplicantFind = document.getElementById('es-applicant-find');
const convApplicantSugg = document.getElementById('es-applicant-suggestions');
const appFindBtn      = document.getElementById('es-app-find');
const appSuggestions   = document.getElementById('es-app-suggestions');
const patentArea      = document.getElementById('es-patent-results');
const convArea        = document.getElementById('es-conv-results');
const appArea         = document.getElementById('es-app-results');
const statusMsg       = document.getElementById('es-status');

// ── Queue Stats (load on tab init) ──────────────────────────────
(async function loadQueueStats() {
  const el = document.getElementById('es-queue-stats');
  if (!el) return;
  try {
    const s = await apiGet('/api/entity-status/queue-stats');
    const parts = [];
    if (s.queue_count) parts.push(`<span class="qs-num">${s.queue_count.toLocaleString()}</span> apps in queue`);
    if (s.pending_ocr) parts.push(`<span class="qs-num">${s.pending_ocr.toLocaleString()}</span> PDFs awaiting extraction`);
    if (s.extracted)   parts.push(`<span class="qs-num qs-done">${s.extracted.toLocaleString()}</span> PDFs extracted`);
    el.innerHTML = parts.join(' &middot; ');
  } catch (e) {
    el.textContent = '';
  }
})();

// ── Portfolio State (pagination, sorting, filtering) ─────────────
const PAGE_SIZE = 50;
let _portfolioState = {
  allResults: [],        // full result set from backend
  filteredResults: [],   // after applying filter
  currentOffset: 0,
  sortColumn: null,      // field name
  sortDirection: null,   // 'asc' or 'desc'
  filterSpec: null,
  filterLabel: null,
  loading: false,
  entityName: '',
};

// Map column indices (from data-sort-key) to result object field names
const SORT_COL_MAP = {
  0: 'patent_number',
  1: 'application_number',
  2: 'grant_date',
  // 3: Events (not sortable)
  4: 'prosecution_status',
  5: 'post_grant_first',
  6: 'post_grant_current',
  7: 'status_changed',
  8: '_ownership_sort',  // computed sort key
};

// ── Micro Chart: Event Classification & Icons ───────────────────

const STATUS_COLORS = { large: '#ef4444', small: '#22c55e', micro: '#3b82f6' };
const GRAY = '#6b7280';

// Prosecution payment event code descriptions (for tooltips)
const PROS_PAY_DESCRIPTIONS = {
  'A.I.': 'Processing fee (1.17(i)(1))',
  'A.LA': 'Processing fee (1.17(i)(1))',
  'A.NQ': 'Processing fee (1.17(i)(1))',
  'A.NR': 'Processing fee (1.17(i)(1))',
  'A.PE': 'Processing fee (1.17(i)(1))',
  'A371': 'Basic filing fee - Utility (1.16(a))',
  'AABR': 'Filing a brief in support of an appeal (1.17(b)(2))',
  'ABN/': 'Basic filing fee - Utility (1.16(a))',
  'ABN6': 'Basic filing fee - Utility (1.16(a))',
  'ABN9': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'ABNF': 'Basic filing fee - Utility (1.16(a))',
  'ACKNAHA': 'Processing fee (1.17(i)(1))',
  'ADDDWRG': 'Processing fee (1.17(i)(1))',
  'ADDFLFEE': 'Basic filing fee - Utility (1.16(a))',
  'ADDSPEC': 'Processing fee (1.17(i)(1))',
  'AFNE': 'Processing fee (1.17(i)(1))',
  'AP.B': 'Filing a brief in support of an appeal (1.17(b)(2))',
  'AP.C': 'Notice of appeal (1.17(b)(1))',
  'AP.C3': 'Notice of appeal (1.17(b)(1))',
  'AP/A': 'Notice of appeal (1.17(b)(1))',
  'APBD': 'Notice of appeal (1.17(b)(1))',
  'APBI': 'Filing a brief in support of an appeal (1.17(b)(2))',
  'APBR': 'Filing a brief in support of an appeal (1.17(b)(2))',
  'APCA': 'Notice of appeal (1.17(b)(1))',
  'APCD': 'Notice of appeal (1.17(b)(1))',
  'APCP': 'Notice of appeal (1.17(b)(1))',
  'APCR': 'Notice of appeal (1.17(b)(1))',
  'APE2': 'Notice of appeal (1.17(b)(1))',
  'APEA': 'Notice of appeal (1.17(b)(1))',
  'APFC': 'Notice of appeal (1.17(b)(1))',
  'APHT': 'Request for an oral hearing before the PTAB (1.17(d))',
  'APND': 'Notice of appeal (1.17(b)(1))',
  'APNH': 'Notice of appeal (1.17(b)(1))',
  'APNH.CA': 'Notice of appeal (1.17(b)(1))',
  'APNH.CO': 'Notice of appeal (1.17(b)(1))',
  'APNH.MI': 'Notice of appeal (1.17(b)(1))',
  'APNH.TX': 'Notice of appeal (1.17(b)(1))',
  'APNH.VA': 'Notice of appeal (1.17(b)(1))',
  'APOH': 'Request for an oral hearing before the PTAB (1.17(d))',
  'APRD': 'Notice of appeal (1.17(b)(1))',
  'APRR': 'Notice of appeal (1.17(b)(1))',
  'ARBP': 'Processing fee (1.17(i)(1))',
  'BRCE': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'C610': 'Processing fee (1.17(i)(1))',
  'C9DE': 'Processing fee (1.17(i)(1))',
  'C9GR': 'Basic filing fee - Utility (1.16(a))',
  'CPA-AMD': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'DIST': 'Terminal disclaimer (1.20(d))',
  'FEE.': 'Processing fee (1.17(i)(1))',
  'FLFEE': 'Basic filing fee - Utility (1.16(a))',
  'FRCE': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'IDS.': 'Submission of Information Disclosure Statement (1.17(p))',
  'IDSPTA': 'Submission of Information Disclosure Statement (1.17(p))',
  'IFEE': 'Utility issue fee (1.18(a))',
  'IFEEHA': 'Utility issue fee (1.18(a))',
  'IRCE': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'J521': 'Processing fee (1.17(i)(1))',
  'JA94': 'Extension for response within first month (1.17(a)(1))',
  'JA95': 'Extension for response within first month (1.17(a)(1))',
  'JS13': 'Processing fee (1.17(i)(1))',
  'MABN6': 'Basic filing fee - Utility (1.16(a))',
  'MAPHT': 'Request for an oral hearing before the PTAB (1.17(d))',
  'MCPA-AMD': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'MODPD28': 'Utility issue fee (1.18(a))',
  'MODPD33': 'Processing fee (1.17(i)(1))',
  'MP005': 'Utility issue fee (1.18(a))',
  'MP020': 'Extension for response within first month (1.17(a)(1))',
  'MQRCE': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'MRAPD': 'Notice of appeal (1.17(b)(1))',
  'MRAPS': 'Processing fee (1.17(i)(1))',
  'MRXEAS': 'Processing fee (1.17(i)(1))',
  'MRXG.': 'Extension for response within first month (1.17(a)(1))',
  'MRXTG': 'Extension for response within first month (1.17(a)(1))',
  'MSML': 'Processing fee (1.17(i)(1))',
  'N/AP': 'Notice of appeal (1.17(b)(1))',
  'N/AP-NOA': 'Notice of appeal (1.17(b)(1))',
  'N084': 'Utility issue fee (1.18(a))',
  'NOIFIBHA': 'Processing fee (1.17(i)(1))',
  'ODPD28': 'Utility issue fee (1.18(a))',
  'ODPD33': 'Processing fee (1.17(i)(1))',
  'ODPET4': 'Petition for revival of abandoned application (1.17(m))',
  'P003': 'Processing fee (1.17(i)(1))',
  'P005': 'Utility issue fee (1.18(a)) + Petition for revival (1.17(m))',
  'P007': 'Processing fee (1.17(i)(1)) — proxy',
  'P010': 'Processing fee (1.17(i)(1))',
  'P012': 'Processing fee (1.17(i)(1))',
  'P020': 'Extension for response within first month (1.17(a)(1))',
  'P131': 'Processing fee (1.17(i)(1))',
  'P138': 'Extension for response within first month (1.17(a)(1))',
  'PFP': 'Processing fee (1.17(i)(1))',
  'PMFP': 'Processing fee (1.17(i)(1))',
  'QRCE': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'RCEX': 'Request for continued examination (RCE) - 1st request (1.17(e)(1))',
  'RETF': 'Processing fee (1.17(i)(1))',
  'RVIFEEHA': 'Utility issue fee (1.18(a)) — reversal',
  'RXIDS.R': 'Submission of Information Disclosure Statement (1.17(p))',
  'RXRQ/T': 'Extension for response within first month (1.17(a)(1))',
  'RXSAPB': 'Filing a brief in support of an appeal (1.17(b)(2))',
  'RXXT/G': 'Extension for response within first month (1.17(a)(1))',
  'TDP': 'Terminal disclaimer (1.20(d))',
  'VFEE': 'Utility issue fee (1.18(a)) — reversal',
  'XT/G': 'Extension for response within first month (1.17(a)(1))',
};

// Prosecution status-change codes (beyond the 3 basic ones)
const _PROS_TO_SMALL = new Set(['SES','SMAL','P013','MP013','MSML','NOSE','MRNSME']);
const _PROS_TO_MICRO = new Set(['MICR','MENC','PMRIA','MPMRIA']);
const _PROS_TO_LARGE = new Set(['BIG.','P014','MP014']);

// Human-readable descriptions for status-change events (used in yellow dot tooltips)
const _STATUS_CHANGE_DESC = {
  'SES': 'Small entity status established',
  'SMAL': 'Small entity declaration',
  'P013': 'Small entity status established',
  'MP013': 'Small entity status established',
  'MSML': 'Small entity status \u2014 processing fee',
  'NOSE': 'Notice of small entity status',
  'MRNSME': 'Notice of small entity status',
  'MICR': 'Micro entity status declared',
  'MENC': 'Micro entity certification',
  'PMRIA': 'Micro entity status established',
  'MPMRIA': 'Micro entity status established',
  'BIG.': 'Large entity status declared',
  'P014': 'Large entity status established',
  'MP014': 'Large entity status established',
  'STOL': 'Status change: Small \u2192 Large',
  'LTOS': 'Status change: Large \u2192 Small',
  'MTOS': 'Status change: Micro \u2192 Small',
  'STOM': 'Status change: Small \u2192 Micro',
};

function classifyEvent(code) {
  if (!code) return 'other';
  if (code.startsWith('M1') || code.startsWith('F17')) return 'large_payment';
  if (code.startsWith('M2') || code.startsWith('F27')) return 'small_payment';
  if (code.startsWith('M3')) return 'micro_payment';
  // Prosecution status transitions (extended set)
  if (_PROS_TO_LARGE.has(code)) return 'decl_big';
  if (_PROS_TO_SMALL.has(code)) return 'decl_smal';
  if (_PROS_TO_MICRO.has(code)) return 'decl_micr';
  if (code === 'STOL') return 'trans_to_large';
  if (code === 'LTOS' || code === 'MTOS') return 'trans_to_small';
  if (code === 'STOM') return 'trans_to_micro';
  if (code === 'EXP.') return 'expired';
  if (code === 'GRNT') return 'grant';
  if (code.startsWith('REM')) return 'reminder';
  if (code === 'ASPN') return 'attorney';
  if (code === 'LITG') return 'litigation';
  if (code === 'PROS_PAY') return 'pros_payment';
  return 'other';
}

/** Returns the entity-status color implied by an event, or null. */
function statusColorForEvent(code) {
  const cat = classifyEvent(code);
  if (cat === 'large_payment' || cat === 'decl_big' || cat === 'trans_to_large') return STATUS_COLORS.large;
  if (cat === 'small_payment' || cat === 'decl_smal' || cat === 'trans_to_small') return STATUS_COLORS.small;
  if (cat === 'micro_payment' || cat === 'decl_micr' || cat === 'trans_to_micro') return STATUS_COLORS.micro;
  return null;
}

/** Infer the initial line color from the first status-implying event. */
function inferInitialColor(events) {
  for (const ev of events) {
    const c = statusColorForEvent(ev.c);
    if (c) return c;
  }
  return '#d1d5db'; // gray fallback
}

// ── SVG Icon Factories (return HTML strings, use currentColor) ──

function svgBuilding() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<rect x="2" y="2" width="10" height="12" rx="1"/>'
    + '<rect x="4" y="4" width="2" height="2" rx=".3" fill="white"/>'
    + '<rect x="8" y="4" width="2" height="2" rx=".3" fill="white"/>'
    + '<rect x="4" y="8" width="2" height="2" rx=".3" fill="white"/>'
    + '<rect x="8" y="8" width="2" height="2" rx=".3" fill="white"/>'
    + '</svg>';
}

function svgHouse() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<polygon points="7,1 1,7 3,7 3,13 11,13 11,7 13,7"/>'
    + '<rect x="5.5" y="9" width="3" height="4" rx=".3" fill="white"/>'
    + '</svg>';
}

function svgPerson() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<circle cx="7" cy="4" r="2.5"/>'
    + '<path d="M3,14 L3,10.5 A4,3.5 0 0 1 11,10.5 L11,14"/>'
    + '</svg>';
}

function svgExpired() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">'
    + '<path d="M3,3 L11,11 M11,3 L3,11"/>'
    + '</svg>';
}

function svgAlarm() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">'
    + '<circle cx="7" cy="7.5" r="4.5"/>'
    + '<path d="M7,5 L7,7.5 L9,8.5"/>'
    + '<path d="M4,1.5 L2,3.5"/><path d="M10,1.5 L12,3.5"/>'
    + '</svg>';
}

function svgBriefcase() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<rect x="1" y="5" width="12" height="8" rx="1.5"/>'
    + '<path d="M5,5 L5,3.5 A1.5,1.5 0 0 1 9,3.5 L9,5" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    + '<line x1="1" y1="9" x2="13" y2="9" stroke="white" stroke-width="1"/>'
    + '</svg>';
}

function svgStar() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<polygon points="7,1 8.8,5.3 13.4,5.8 10,9 11,13.5 7,11.2 3,13.5 4,9 0.6,5.8 5.2,5.3"/>'
    + '</svg>';
}

function svgDollar() {
  return '<svg viewBox="0 0 14 14" width="100%" height="100%" fill="currentColor">'
    + '<text x="7" y="12" text-anchor="middle" font-size="12" font-weight="bold" font-family="Arial,sans-serif">$</text>'
    + '</svg>';
}

// ── Single Patent Lookup ─────────────────────────────────────────

patentBtn.addEventListener('click', () => lookupPatent());
patentInput.addEventListener('keydown', e => { if (e.key === 'Enter') lookupPatent(); });

async function lookupPatent() {
  const pn = patentInput.value.trim();
  if (!pn) return;

  setLoading(patentBtn, true);
  patentArea.classList.remove('hidden');
  patentArea.innerHTML = '<p class="text-muted">Loading...</p>';

  try {
    const data = await apiGet(`/api/entity-status/${encodeURIComponent(pn)}`);
    renderPatentTimeline(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    patentArea.innerHTML = '';
    patentArea.classList.add('hidden');
  } finally {
    setLoading(patentBtn, false);
  }
}

function renderPatentTimeline(data) {
  const changedBadge = data.status_changed
    ? `<span class="es-badge es-badge--changed">CONVERTED</span>`
    : `<span class="es-badge es-badge--same">No Change</span>`;

  let html = `
    <div class="card">
      <h3 class="card-title">Entity Status for Patent ${escHtml(data.patent_number)} ${changedBadge}</h3>
      <div class="es-patent-info">
        <div><strong>Title:</strong> ${escHtml(data.invention_title || 'N/A')}</div>
        <div><strong>Applicant:</strong> ${escHtml(data.applicant_name || 'N/A')}</div>
        <div><strong>Filed:</strong> ${escHtml(data.filing_date || 'N/A')} &nbsp; <strong>Granted:</strong> ${escHtml(data.grant_date || 'N/A')}</div>
        <div><strong>First Status:</strong> ${statusBadge(data.filing_entity_status)} &nbsp; <strong>Current:</strong> ${statusBadge(data.current_entity_status)}</div>
        ${data.conversion_date ? `<div><strong>Conversion Date:</strong> ${escHtml(data.conversion_date)}</div>` : ''}
      </div>
  `;

  if (data.timeline.length > 0) {
    html += '<div id="es-single-timeline-wrap" style="margin-top:1rem"></div>';
  } else {
    html += '<p class="text-muted">No maintenance fee events found.</p>';
  }

  html += '</div>';
  patentArea.innerHTML = html;

  // Render the full-width micro chart timeline
  if (data.timeline.length > 0) {
    const wrap = document.getElementById('es-single-timeline-wrap');
    const events = data.timeline.map(ev => ({ d: ev.event_date, c: ev.event_code }));

    // Add grant date as synthetic GRNT event if available
    if (data.grant_date) {
      events.push({ d: data.grant_date, c: 'GRNT' });
      events.sort((a, b) => a.d.localeCompare(b.d));
    }

    const dates = events.map(e => new Date(e.d).getTime());
    const minDate = new Date(Math.min(...dates));
    const maxDate = new Date(Math.max(...dates));
    const totalMs = maxDate.getTime() - minDate.getTime();
    if (totalMs <= 0) return;

    const track = document.createElement('div');
    track.className = 'es-microchart-track es-single-track';

    // ── Build colored status line ──
    const initColor = inferInitialColor(events);
    let currentColor = initColor;
    const changePoints = [];

    let endPct = 100;
    for (const ev of events) {
      if (ev.c === 'EXP.') {
        const expDate = new Date(ev.d);
        endPct = clampPct(((expDate.getTime() - minDate.getTime()) / totalMs) * 100);
        break;
      }
    }

    for (const ev of events) {
      const newColor = statusColorForEvent(ev.c);
      if (newColor && newColor !== currentColor) {
        const evDate = new Date(ev.d);
        const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
        if (pct >= endPct) break;
        changePoints.push({ pct, color: newColor });
        currentColor = newColor;
      }
    }

    let prevPct = 0;
    let lineColor = initColor;
    for (const cp of changePoints) {
      appendLine(track, prevPct, cp.pct - prevPct, lineColor);
      lineColor = cp.color;
      prevPct = cp.pct;
    }
    appendLine(track, prevPct, endPct - prevPct, lineColor);

    // ── Place icons with labels ──
    for (const ev of events) {
      const evDate = new Date(ev.d);
      const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
      const cat = classifyEvent(ev.c);

      let marker = null;
      if (cat === 'large_payment') {
        marker = createIconEl(svgBuilding, STATUS_COLORS.large, pct, ev);
      } else if (cat === 'small_payment') {
        marker = createIconEl(svgHouse, STATUS_COLORS.small, pct, ev);
      } else if (cat === 'micro_payment') {
        marker = createIconEl(svgPerson, STATUS_COLORS.micro, pct, ev);
      } else if (cat === 'expired') {
        marker = createIconEl(svgExpired, GRAY, pct, ev);
      } else if (cat === 'grant') {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#8b5cf6';
        marker.title = `Grant \u2014 ${ev.d}`;
      } else if (cat === 'reminder') {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#eab308';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      } else if (cat === 'attorney') {
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#92400e';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      } else if (cat === 'litigation') {
        marker = createIconEl(svgStar, '#d4a017', pct, ev);
        marker.title = `Litigation filed \u2014 ${ev.d}${ev._case ? ' \u2014 ' + ev._case.case_no : ''}`;
      } else if (cat.startsWith('trans_to_') || cat.startsWith('decl_')) {
        // Status-change events: yellow dot with description
        marker = document.createElement('div');
        marker.className = 'es-microchart-dot-sm';
        marker.style.left = pct + '%';
        marker.style.backgroundColor = '#eab308'; // yellow
        const desc = _STATUS_CHANGE_DESC[ev.c] || ev.c;
        marker.title = `${desc} (${ev.c}) \u2014 ${ev.d}`;
      } else {
        marker = document.createElement('div');
        marker.className = 'es-microchart-other';
        marker.style.left = pct + '%';
        marker.title = `${ev.c} \u2014 ${ev.d}`;
      }

      if (marker) {
        // Add date label below the track for this larger view
        const label = document.createElement('div');
        label.className = 'es-single-label';
        label.style.left = pct + '%';
        label.textContent = ev.c;
        track.appendChild(label);

        track.appendChild(marker);
      }
    }

    // ── Date axis: show min and max dates ──
    const axisDiv = document.createElement('div');
    axisDiv.className = 'es-single-axis';
    axisDiv.innerHTML = `<span>${escHtml(minDate.toISOString().slice(0, 10))}</span><span>${escHtml(maxDate.toISOString().slice(0, 10))}</span>`;

    wrap.appendChild(track);
    wrap.appendChild(axisDiv);
  }
}

// ── Boolean Name Search ──────────────────────────────────────────

/** Returns true if the text contains boolean operators (+, -, *). */
function isBooleanQuery(text) {
  return /[+\-*]/.test(text);
}

/**
 * Search for entity names via the MDM boolean search endpoint.
 * Renders matching names as a clickable list below the input.
 */
async function findNames(inputEl, suggestEl, findBtn) {
  const query = inputEl.value.trim();
  if (!query) {
    showStatus(statusMsg, 'Enter a search expression (e.g. +elect* +telecom*)', 'error');
    return;
  }

  setLoading(findBtn, true);
  suggestEl.innerHTML = '';
  suggestEl.classList.add('hidden');

  try {
    const results = await apiPost('/mdm/search', { query });

    if (!results || results.length === 0) {
      suggestEl.innerHTML = '<div class="es-suggestion-header">No matching names found</div>';
      suggestEl.classList.remove('hidden');
      return;
    }

    let html = `<div class="es-suggestion-header">
      <span>${results.length} matching name(s)</span>
      <span>Click to select</span>
    </div>`;

    for (const r of results) {
      const name = r.raw_name || r.name || '';
      const freq = r.frequency || 0;
      const rep = r.representative_name || '';
      const badge = rep
        ? `<span class="es-unified-badge" title="Normalized to: ${escHtml(rep)}">unified</span>`
        : '';
      html += `<div class="es-suggestion-item" data-name="${escHtml(name)}" data-representative="${escHtml(rep)}">
        <span class="es-suggestion-name">${escHtml(name)}${badge}</span>
        <span class="es-suggestion-freq">${freq.toLocaleString()}</span>
      </div>`;
    }

    suggestEl.innerHTML = html;
    suggestEl.classList.remove('hidden');

    suggestEl.querySelectorAll('.es-suggestion-item').forEach(item => {
      item.addEventListener('click', () => {
        const rep = item.dataset.representative;
        inputEl.value = rep || item.dataset.name;
        suggestEl.classList.add('hidden');
        suggestEl.innerHTML = '';
      });
    });
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
  } finally {
    setLoading(findBtn, false);
  }
}

// Wire up Find buttons
convApplicantFind.addEventListener('click', () => {
  findNames(convApplicant, convApplicantSugg, convApplicantFind);
});
convApplicant.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    findNames(convApplicant, convApplicantSugg, convApplicantFind);
  }
});

appFindBtn.addEventListener('click', () => {
  findNames(appInput, appSuggestions, appFindBtn);
});

// ── Conversion Search ────────────────────────────────────────────

convBtn.addEventListener('click', () => searchConversions());

async function searchConversions() {
  convApplicantSugg.classList.add('hidden');
  setLoading(convBtn, true);
  convArea.classList.remove('hidden');
  convArea.innerHTML = '<p class="text-muted">Searching...</p>';

  try {
    const data = await apiPost('/api/entity-status/conversions', {
      from_status: convFromStatus.value,
      to_status: convToStatus.value,
      grant_year_start: parseInt(convYearStart.value) || 2010,
      grant_year_end: parseInt(convYearEnd.value) || 2025,
      applicant_name: convApplicant.value.trim() || null,
      limit: parseInt(convLimit.value) || 200,
    });
    renderConversionResults(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    convArea.innerHTML = '';
    convArea.classList.add('hidden');
  } finally {
    setLoading(convBtn, false);
  }
}

function renderConversionResults(data) {
  const expandedInfo = data.expanded_names && data.expanded_names.length > 1
    ? `<p class="text-muted">Searching ${data.expanded_names.length} name variants</p>` : '';
  let html = `
    <div class="results-header">
      <strong>Conversion Results</strong>
      <span class="results-count">${data.total} patents</span>
    </div>
    ${expandedInfo}
    <div class="table-scroll-wrap">
      <table class="data-table" id="es-conv-table">
        <thead><tr>
          <th data-sort-key="0">Patent #</th>
          <th data-sort-key="1">App #</th>
          <th data-sort-key="2">Grant Date</th>
          <th data-sort-key="3">Applicant</th>
          <th data-sort-key="4">Title</th>
          <th data-sort-key="5">From</th>
          <th data-sort-key="6">To</th>
        </tr></thead>
        <tbody>
  `;

  for (const r of data.results) {
    html += `<tr>
      <td class="patent-number">${escHtml(r.patent_number || '')}</td>
      <td>${escHtml(r.application_number || '')}</td>
      <td>${escHtml(r.grant_date || '')}</td>
      <td>${escHtml(r.applicant_name || '')}</td>
      <td>${escHtml(r.invention_title || '')}</td>
      <td>${statusBadge(r.first_status)}</td>
      <td>${statusBadge(r.last_status)}</td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  convArea.innerHTML = html;

  const tbl = document.getElementById('es-conv-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    enableAssignmentPopup('#es-conv-table .patent-number');
    addColumnPicker(tbl);
  }
}

// ── Applicant Portfolio ──────────────────────────────────────────

appBtn.addEventListener('click', () => loadApplicantPortfolio());
appInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    if (isBooleanQuery(appInput.value)) {
      e.preventDefault();
      findNames(appInput, appSuggestions, appFindBtn);
    } else {
      loadApplicantPortfolio();
    }
  }
});

async function loadApplicantPortfolio() {
  appSuggestions.classList.add('hidden');
  const name = appInput.value.trim();
  if (!name) return;

  // Clear any previous extraction progress polling
  if (typeof _extractionPollTimer !== 'undefined' && _extractionPollTimer) {
    clearInterval(_extractionPollTimer);
    _extractionPollTimer = null;
  }

  setLoading(appBtn, true);
  appArea.classList.remove('hidden');
  appArea.innerHTML = '<p class="text-muted">Checking MDM normalization...</p>';

  try {
    // Step 1: Resolve through MDM — Entity Status only works with normalized names
    const resolved = await apiGet(`/mdm/resolve?name=${encodeURIComponent(name)}`);
    if (!resolved.is_unified) {
      appArea.innerHTML = `
        <div class="es-event-code-warning" style="display:block">
          <strong>⚠ Name Not Normalized:</strong> "${escHtml(name)}" is not in the MDM system.
          Please go to the <strong>MDM</strong> tab and associate this name with a representative
          name before analyzing the portfolio.
        </div>`;
      return;
    }

    // Use the canonical representative name for all downstream calls
    const representativeName = resolved.representative_name;
    appArea.innerHTML = '<p class="text-muted">Loading portfolio — searching all applicants, inventors, and assignees...</p>';

    // Step 2: Load portfolio using the representative name
    const data = await apiPost('/api/entity-status/by-applicant', {
      applicant_name: representativeName,
      limit: 50000,
    });
    renderApplicantPortfolio(data);
  } catch (err) {
    showStatus(statusMsg, err.message, 'error');
    appArea.innerHTML = '';
    appArea.classList.add('hidden');
  } finally {
    setLoading(appBtn, false);
  }
}

// ── Portfolio Helpers: row building, filtering, sorting ──────────

/** Build HTML for a single table row from a result object. */
function buildResultRowHtml(r) {
  const changedMark = r.status_changed
    ? `<span class="es-badge es-badge--changed">${r.change_phase === 'prosecution' ? 'Pros' : 'PG'}</span>`
    : '';
  let ownershipBadge;
  if (r.divested) {
    ownershipBadge = `<span class="es-badge es-badge--divested" title="Divested ${r.divested_date || ''}">Divested${r.divested_date ? ' ' + r.divested_date.slice(0, 10) : ''}</span>`;
  } else if (r.acquired_via_assignment) {
    ownershipBadge = `<span class="es-badge es-badge--acquired" title="Acquired via assignment ${r.acquired_date || ''}">Acquired${r.acquired_date ? ' ' + r.acquired_date.slice(0, 10) : ''}</span>`;
  } else {
    ownershipBadge = `<span class="es-badge es-badge--owned">Owned</span>`;
  }
  const rowStyle = r.divested ? ' style="opacity:0.6"' : '';
  return `<tr${rowStyle} data-pn="${escHtml(r.patent_number || '')}" data-app="${escHtml(r.application_number || '')}" data-pros="${r.prosecution_status || ''}" data-pros10y="${r.prosecution_status_10y || ''}" data-pgfirst="${r.post_grant_first || ''}" data-pgcurrent="${r.post_grant_current || ''}" data-mf="${escHtml(r.mf_events || '')}" data-changed="${r.status_changed ? '1' : ''}" data-divested="${r.divested ? '1' : ''}" data-acquired="${r.acquired_via_assignment ? '1' : ''}" data-expired="${r.expired ? '1' : ''}">
    <td class="patent-number">${escHtml(r.patent_number || '')}</td>
    <td>${escHtml(r.application_number || '')}</td>
    <td>${escHtml(r.grant_date || '')}</td>
    <td class="es-events-cell"></td>
    <td>${statusBadge(r.prosecution_status)}</td>
    <td>${statusBadge(r.post_grant_first)}</td>
    <td>${statusBadge(r.post_grant_current)}</td>
    <td>${changedMark}</td>
    <td>${ownershipBadge}</td>
  </tr>`;
}

/** Apply filter to an array of result objects. Returns filtered array. */
function applyResultFilter(results, filterSpec) {
  if (!filterSpec) return results;
  const colonIdx = filterSpec.indexOf(':');
  if (colonIdx < 0) return results;
  const field = filterSpec.slice(0, colonIdx);
  const codes = filterSpec.slice(colonIdx + 1).split(',');

  return results.filter(r => {
    if (field === 'mf') {
      const mfTokens = (r.mf_events || '').split(' ');
      return codes.some(c => mfTokens.includes(c));
    } else if (field === 'pros') {
      return codes.includes(r.prosecution_status);
    } else if (field === 'pros10y') {
      return codes.includes(r.prosecution_status_10y);
    } else if (field === 'pgfirst') {
      return codes.includes(r.post_grant_first);
    } else if (field === 'pgcurrent') {
      return codes.includes(r.post_grant_current);
    } else if (field === 'ownership') {
      if (codes.includes('divested')) return !!r.divested;
      if (codes.includes('acquired')) return !!r.acquired_via_assignment;
      return true;
    } else if (field === 'portfolio') {
      const code = codes[0];
      const isGranted = !!r.patent_number;
      const isPending = !isGranted;
      const isDivested = !!r.divested;
      const isAcquired = !!r.acquired_via_assignment;
      const isExpired = !!r.expired;
      if (code === 'filed_granted') return isGranted && !isAcquired;
      if (code === 'acquired_granted') return isGranted && isAcquired;
      if (code === 'divested_granted') return isGranted && isDivested;
      if (code === 'expired_granted') return isGranted && !isDivested && isExpired;
      if (code === 'filed_pending') return isPending && !isAcquired;
      if (code === 'acquired_pending') return isPending && isAcquired;
      if (code === 'divested_pending') return isPending && isDivested;
      return true;
    } else if (field === 'litigation') {
      if (codes.includes('litigated')) {
        const litData = window._litigationData;
        return litData && !!litData[r.patent_number];
      }
      return true;
    } else if (field === 'prospay') {
      const pd = window._prosecutionData;
      if (!pd || !pd.timelines || !pd.timelines[r.application_number]) return false;
      const statuses = new Set();
      for (const p of (pd.timelines[r.application_number].payments || [])) {
        statuses.add(p.status);
      }
      return codes.some(c => statuses.has(c));
    }
    return true;
  });
}

/** Sort an array of result objects by field name. Returns new sorted array. */
function sortResults(results, fieldName, direction) {
  if (!fieldName || !direction) return results;
  const dir = direction === 'asc' ? 1 : -1;
  const sorted = [...results];
  sorted.sort((a, b) => {
    let va = a[fieldName] ?? '';
    let vb = b[fieldName] ?? '';
    // Compute ownership sort key on the fly
    if (fieldName === '_ownership_sort') {
      va = a.divested ? '2_divested' : a.acquired_via_assignment ? '1_acquired' : '0_owned';
      vb = b.divested ? '2_divested' : b.acquired_via_assignment ? '1_acquired' : '0_owned';
    }
    // Booleans
    if (typeof va === 'boolean') va = va ? 1 : 0;
    if (typeof vb === 'boolean') vb = vb ? 1 : 0;
    // Numeric
    const na = parseFloat(String(va).replace(/,/g, ''));
    const nb = parseFloat(String(vb).replace(/,/g, ''));
    if (!isNaN(na) && !isNaN(nb)) return (na - nb) * dir;
    return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir;
  });
  return sorted;
}

/**
 * Render a page of result rows into the table body.
 * Appends to existing tbody (for infinite scroll).
 * Also sets data-prospay and data-litigated from async data if available.
 */
function appendResultRows(rows) {
  const tbl = document.getElementById('es-app-table');
  if (!tbl) return;
  const tbody = tbl.querySelector('tbody');
  if (!tbody) return;

  let html = '';
  for (const r of rows) {
    html += buildResultRowHtml(r);
  }
  tbody.insertAdjacentHTML('beforeend', html);

  // Set async data attributes on new rows
  const newTrs = Array.from(tbody.querySelectorAll('tr')).slice(-rows.length);
  for (const tr of newTrs) {
    const app = tr.dataset.app;
    const pn = tr.dataset.pn;

    // Prosecution payment status
    if (window._prosecutionData && window._prosecutionData.timelines && app) {
      const tl = window._prosecutionData.timelines[app];
      if (tl) {
        const statuses = new Set();
        for (const p of (tl.payments || [])) statuses.add(p.status);
        tr.dataset.prospay = [...statuses].join(',');
      }
    }
    // Litigation
    if (window._litigationData && pn && window._litigationData[pn]) {
      tr.dataset.litigated = '1';
    }
  }

  // Sync column visibility — hide td cells for columns hidden via column picker
  const ths = tbl.querySelectorAll('thead th');
  for (const tr of newTrs) {
    for (let i = 0; i < ths.length; i++) {
      if (ths[i].style.display === 'none' && tr.cells[i]) {
        tr.cells[i].style.display = 'none';
      }
    }
  }

  // Enable patent number links on new rows
  enableAssignmentPopup('#es-app-table .patent-number');

  // Fetch sparklines for visible patents in new rows
  const newPatents = rows.filter(r => r.patent_number).map(r => r.patent_number);
  if (newPatents.length > 0) {
    fetchAndRenderMicroCharts(newPatents, _portfolioState.filterSpec);
  }
}

/**
 * Reload the portfolio table with current filter/sort state.
 * Clears table, renders first page, resets offset.
 */
function reloadPortfolioTable() {
  const tbl = document.getElementById('es-app-table');
  if (!tbl) return;
  const tbody = tbl.querySelector('tbody');
  if (!tbody) return;

  // Apply filter
  let results = applyResultFilter(_portfolioState.allResults, _portfolioState.filterSpec);
  // Apply sort
  results = sortResults(results, _portfolioState.sortColumn, _portfolioState.sortDirection);
  _portfolioState.filteredResults = results;
  _portfolioState.currentOffset = 0;

  // Clear table body
  tbody.innerHTML = '';

  // Render first page
  const page = results.slice(0, PAGE_SIZE);
  appendResultRows(page);
  _portfolioState.currentOffset = page.length;

  // Update shown count
  const shownCount = document.getElementById('es-shown-count');
  const totalAll = _portfolioState.allResults.length;
  if (shownCount) {
    if (_portfolioState.filterSpec) {
      shownCount.textContent = `${results.length.toLocaleString()} of ${totalAll.toLocaleString()} shown`;
    } else {
      shownCount.textContent = `${totalAll.toLocaleString()} shown`;
    }
  }

  // Update scroll sentinel visibility
  updateScrollSentinel();
}

/** Load the next page of results (infinite scroll). */
function loadMoreRows() {
  if (_portfolioState.loading) return;
  const results = _portfolioState.filteredResults;
  if (_portfolioState.currentOffset >= results.length) return;

  _portfolioState.loading = true;
  const page = results.slice(_portfolioState.currentOffset, _portfolioState.currentOffset + PAGE_SIZE);
  appendResultRows(page);
  _portfolioState.currentOffset += page.length;
  _portfolioState.loading = false;

  updateScrollSentinel();
}

/** Show/hide the scroll sentinel based on whether there are more rows. */
function updateScrollSentinel() {
  const sentinel = document.getElementById('es-scroll-sentinel');
  if (!sentinel) return;
  const hasMore = _portfolioState.currentOffset < _portfolioState.filteredResults.length;
  sentinel.style.display = hasMore ? '' : 'none';
  const remaining = _portfolioState.filteredResults.length - _portfolioState.currentOffset;
  sentinel.textContent = hasMore ? `Scroll for more — ${remaining.toLocaleString()} remaining` : '';
}

/** Set up IntersectionObserver on the scroll sentinel. */
let _scrollObserver = null;
function setupInfiniteScroll() {
  const sentinel = document.getElementById('es-scroll-sentinel');
  if (!sentinel) return;
  if (_scrollObserver) _scrollObserver.disconnect();
  _scrollObserver = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting) loadMoreRows();
  }, { rootMargin: '200px' });
  _scrollObserver.observe(sentinel);
}

function renderApplicantPortfolio(data) {
  const pros = data.prosecution || {};
  const pg = data.post_grant || {};
  const pay = pg.payments || {m1551:0,m1552:0,m1553:0,m2551:0,m2552:0,m2553:0,m3551:0,m3552:0,m3553:0};
  const pf = data.portfolio || {
    granted: {filed:0, acquired:0, divested:0, expired:0, owned:0},
    pending: {filed:0, acquired:0, divested:0, owned:0},
  };

  // Row & column totals for payment table
  const row35 = pay.m3551 + pay.m2551 + pay.m1551;
  const row75 = pay.m3552 + pay.m2552 + pay.m1552;
  const row115 = pay.m3553 + pay.m2553 + pay.m1553;
  const colMicro = pay.m3551 + pay.m3552 + pay.m3553;
  const colSmall = pay.m2551 + pay.m2552 + pay.m2553;
  const colLarge = pay.m1551 + pay.m1552 + pay.m1553;
  const grandTotal = colMicro + colSmall + colLarge;

  /** Wrap a non-zero value in a clickable span with filter metadata. */
  function kpi(val, filterSpec, label) {
    if (!val) return '0';
    return `<span class="kpi-clickable" data-filter="${escHtml(filterSpec)}" data-label="${escHtml(label)}">${val.toLocaleString()}</span>`;
  }

  let html = `
    <div class="card">
      <h3 class="card-title">Portfolio for ${escHtml(data.applicant_name)}</h3>
      ${data.expanded_names.length > 1
        ? `<p class="text-muted">Including ${data.expanded_names.length} name variants (applicants, inventors, and assignees)</p>`
        : ''}

      <!-- Granted Patents KPIs -->
      <h4 style="margin:0.25rem 0 0.5rem;font-size:0.95rem;color:#374151">Granted Patents</h4>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.filed, 'portfolio:filed_granted', 'Filed Patents')}</span>
          <span class="cite-stat-label">Filed</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.acquired, 'portfolio:acquired_granted', 'Acquired Patents')}</span>
          <span class="cite-stat-label">Acquired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.divested, 'portfolio:divested_granted', 'Divested Patents')}</span>
          <span class="cite-stat-label">Divested</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.granted.expired, 'portfolio:expired_granted', 'Expired Patents')}</span>
          <span class="cite-stat-label">Expired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value" style="font-weight:700">${pf.granted.owned.toLocaleString()}</span>
          <span class="cite-stat-label" style="font-weight:600">Owned</span>
        </div>
      </div>

      <!-- Pending Applications KPIs -->
      <h4 style="margin:1rem 0 0.5rem;font-size:0.95rem;color:#374151">Pending Applications</h4>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.pending.filed, 'portfolio:filed_pending', 'Filed Applications')}</span>
          <span class="cite-stat-label">Filed</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.pending.acquired, 'portfolio:acquired_pending', 'Acquired Applications')}</span>
          <span class="cite-stat-label">Acquired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pf.pending.divested, 'portfolio:divested_pending', 'Divested Applications')}</span>
          <span class="cite-stat-label">Divested</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">\u2014</span>
          <span class="cite-stat-label">Expired</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value" style="font-weight:700">${pf.pending.owned.toLocaleString()}</span>
          <span class="cite-stat-label" style="font-weight:600">Owned</span>
        </div>
      </div>
      <p class="text-muted" style="margin:0.5rem 0 0;font-size:0.8rem">Owned = Filed + Acquired \u2212 Divested \u2212 Expired &nbsp;|&nbsp; KPIs reflect only events during the entity's ownership period</p>

      <!-- Litigation KPI (populated asynchronously) -->
      <div id="es-litigation-kpis" style="display:none;margin-top:1rem">
        <h4 class="card-title" style="font-size:1rem">Litigation History</h4>
        <div class="cite-summary-grid">
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-litigated-count">\u2026</span>
            <span class="cite-stat-label">Litigated Patents</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-total-cases">\u2026</span>
            <span class="cite-stat-label">Total Cases</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-active-cases">\u2026</span>
            <span class="cite-stat-label">Active</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-resolved-cases">\u2026</span>
            <span class="cite-stat-label">Resolved</span>
          </div>
          <div class="cite-stat">
            <span class="cite-stat-value" id="es-courts-count">\u2026</span>
            <span class="cite-stat-label">Courts</span>
          </div>
        </div>
        <p class="text-muted" id="es-litigation-status" style="margin:0.25rem 0 0;font-size:0.8rem">Checking litigation history\u2026</p>
      </div>
      <div id="es-litigation-table-wrap" style="display:none;margin-top:1rem"></div>
    </div>

    <!-- Prosecution Payment Analysis -->
    <div id="es-pros-payments" style="margin-top:1rem">
      <div class="card">
        <h4 class="card-title" style="font-size:1rem">Prosecution Payment Analysis</h4>
        <p class="text-muted" style="margin:0 0 0.5rem">Identifies all fee payments made during prosecution and classifies them by the entity status at the time of payment</p>
        <div id="es-event-code-warning" class="es-event-code-warning" style="display:none">
          <strong>\u26A0 Temporary Data Source:</strong> These numbers are derived from prosecution event codes,
          which capture only ~20% of actual fee payments. Invoice-based extraction is in progress \u2014
          numbers will update automatically when complete.
        </div>
        <div id="es-extraction-progress-section" style="display:none;margin:0.5rem 0">
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.15rem">
            <span style="font-size:0.8rem;font-weight:600;min-width:110px">PDF Retrieval:</span>
            <span id="es-retrieval-pct" style="font-size:0.8rem;color:#6b7280">0%</span>
          </div>
          <div class="px-progress-bar">
            <div class="px-progress-fill" id="es-retrieval-fill" style="width:0%"></div>
          </div>
          <div id="es-retrieval-detail" style="font-size:0.75rem;color:#6b7280;margin-top:0.1rem;margin-bottom:0.4rem"></div>
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.15rem">
            <span style="font-size:0.8rem;font-weight:600;min-width:110px">Data Extraction:</span>
            <span id="es-extract-pct" style="font-size:0.8rem;color:#6b7280">0%</span>
          </div>
          <div class="px-progress-bar">
            <div class="px-progress-fill" id="es-extract-fill" style="width:0%"></div>
          </div>
          <div id="es-extract-detail" style="font-size:0.75rem;color:#6b7280;margin-top:0.1rem"></div>
        </div>
        <div id="es-pros-pay-progress" style="margin-bottom:0.5rem;display:none">
          <span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #ccc;border-top-color:#3b82f6;border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle;margin-right:6px"></span>
          <span id="es-pros-pay-status" class="text-muted" style="font-size:0.85rem">Analyzing prosecution payments...</span>
        </div>
        <div id="es-pros-pay-kpis" style="display:none">
          <div class="cite-summary-grid">
            <div class="cite-stat kpi-clickable" data-filter="prospay:SMALL" data-label="Prosecution: Small Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-small">0</span>
              <span class="cite-stat-label">${statusBadge('SMALL')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:MICRO" data-label="Prosecution: Micro Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-micro">0</span>
              <span class="cite-stat-label">${statusBadge('MICRO')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:LARGE" data-label="Prosecution: Large Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-large">0</span>
              <span class="cite-stat-label">${statusBadge('LARGE')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:SMALL,MICRO,LARGE" data-label="Prosecution: All Payments">
              <span class="cite-stat-value" id="es-pros-pay-total">0</span>
              <span class="cite-stat-label">Total</span>
            </div>
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-apps">0</span>
              <span class="cite-stat-label">Apps w/ Findings</span>
            </div>
          </div>
          <p class="text-muted" style="margin:0.5rem 0 0.25rem;font-size:0.8rem">Past 10 years only</p>
          <div class="cite-summary-grid">
            <div class="cite-stat kpi-clickable" data-filter="prospay:SMALL" data-label="Prosecution 10y: Small Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-small-10y">0</span>
              <span class="cite-stat-label">${statusBadge('SMALL')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:MICRO" data-label="Prosecution 10y: Micro Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-micro-10y">0</span>
              <span class="cite-stat-label">${statusBadge('MICRO')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:LARGE" data-label="Prosecution 10y: Large Rate Payments">
              <span class="cite-stat-value" id="es-pros-pay-large-10y">0</span>
              <span class="cite-stat-label">${statusBadge('LARGE')} Rate</span>
            </div>
            <div class="cite-stat kpi-clickable" data-filter="prospay:SMALL,MICRO,LARGE" data-label="Prosecution 10y: All Payments">
              <span class="cite-stat-value" id="es-pros-pay-total-10y">0</span>
              <span class="cite-stat-label">Total</span>
            </div>
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-apps-10y">0</span>
              <span class="cite-stat-label">Apps w/ Findings</span>
            </div>
          </div>
          <p class="text-muted" style="margin:0.75rem 0 0.25rem;font-size:0.8rem;font-weight:600">Dollar Impact — Reduced-Rate Payments (Small + Micro) <span id="es-dollar-source-badge" class="es-data-source-badge event-code">Event Code Data</span></p>
          <div class="cite-summary-grid">
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-dollars-paid">$0</span>
              <span class="cite-stat-label">Amount Paid</span>
            </div>
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-dollars-large">$0</span>
              <span class="cite-stat-label">Large Rate</span>
            </div>
            <div class="cite-stat" style="background:#fef2f2">
              <span class="cite-stat-value" style="color:#dc2626" id="es-pros-pay-dollars-delta">$0</span>
              <span class="cite-stat-label">Underpayment</span>
            </div>
          </div>
          <p class="text-muted" style="margin:0.5rem 0 0.25rem;font-size:0.8rem">Past 10 years only</p>
          <div class="cite-summary-grid">
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-dollars-paid-10y">$0</span>
              <span class="cite-stat-label">Amount Paid</span>
            </div>
            <div class="cite-stat">
              <span class="cite-stat-value" id="es-pros-pay-dollars-large-10y">$0</span>
              <span class="cite-stat-label">Large Rate</span>
            </div>
            <div class="cite-stat" style="background:#fef2f2">
              <span class="cite-stat-value" style="color:#dc2626" id="es-pros-pay-dollars-delta-10y">$0</span>
              <span class="cite-stat-label">Underpayment</span>
            </div>
          </div>
        </div>
        <p class="text-muted" id="es-pros-pay-status" style="margin:0.25rem 0 0;font-size:0.8rem"></p>
        <p id="es-extraction-status" style="display:none;margin:0.15rem 0 0"></p>
      </div>
      <div id="es-pros-summary-wrap" style="display:none;margin-top:1rem"></div>
      <div id="es-pros-detail-wrap" style="display:none;margin-top:1rem"></div>
    </div>

    <!-- Prosecution Phase -->
    <div class="card" style="margin-top:1rem">
      <h4 class="card-title" style="font-size:1rem">Prosecution Phase — Entity Declarations</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">From prosecution transaction codes (SMAL, BIG., MICR)</p>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.small, 'pros:SMALL', 'Prosecution: Small')}</span>
          <span class="cite-stat-label">${statusBadge('SMALL')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.micro, 'pros:MICRO', 'Prosecution: Micro')}</span>
          <span class="cite-stat-label">${statusBadge('MICRO')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.large, 'pros:LARGE', 'Prosecution: Large')}</span>
          <span class="cite-stat-label">${statusBadge('LARGE')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.total, 'pros:SMALL,MICRO,LARGE', 'Prosecution: All')}</span>
          <span class="cite-stat-label">Total</span>
        </div>
      </div>
      <p class="text-muted" style="margin:0.5rem 0 0.25rem;font-size:0.8rem">Past 10 years only</p>
      <div class="cite-summary-grid">
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.small_10y || 0, 'pros10y:SMALL', 'Prosecution 10y: Small')}</span>
          <span class="cite-stat-label">${statusBadge('SMALL')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.micro_10y || 0, 'pros10y:MICRO', 'Prosecution 10y: Micro')}</span>
          <span class="cite-stat-label">${statusBadge('MICRO')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.large_10y || 0, 'pros10y:LARGE', 'Prosecution 10y: Large')}</span>
          <span class="cite-stat-label">${statusBadge('LARGE')}</span>
        </div>
        <div class="cite-stat">
          <span class="cite-stat-value">${kpi(pros.total_10y || 0, 'pros10y:SMALL,MICRO,LARGE', 'Prosecution 10y: All')}</span>
          <span class="cite-stat-label">Total</span>
        </div>
      </div>
    </div>

    <!-- Post-Grant Phase -->
    <div class="card" style="margin-top:1rem">
      <h4 class="card-title" style="font-size:1rem">Post-Grant Phase — Maintenance Fees</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">From maintenance fee payments, declarations, and transitions</p>
      <div style="display:flex;gap:2rem;flex-wrap:wrap">
        <!-- Payments -->
        <div style="flex:1;min-width:340px">
          <div style="color:#c0392b;font-weight:600;margin-bottom:0.5rem">Payments:</div>
          <table class="data-table" style="font-size:0.85rem">
            <thead><tr>
              <th></th>
              <th style="text-align:center">${statusBadge('MICRO')}</th>
              <th style="text-align:center">${statusBadge('SMALL')}</th>
              <th style="text-align:center">${statusBadge('LARGE')}</th>
              <th style="text-align:center"><strong>Total</strong></th>
              <th style="text-align:center;font-size:0.75rem;line-height:1.1" title="37 CFR 1.28(c) — good-faith error corrections (M1559)">37 CFR<br>1.28(c)</th>
            </tr></thead>
            <tbody>
              <tr>
                <td style="font-weight:600">3.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3551, 'mf:M3551', 'Micro 3.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2551, 'mf:M2551', 'Small 3.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1551, 'mf:M1551', 'Large 3.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row35, 'mf:M3551,M2551,M1551', '3.5-yr Total')}</td>
                <td></td>
              </tr>
              <tr>
                <td style="font-weight:600">7.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3552, 'mf:M3552', 'Micro 7.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2552, 'mf:M2552', 'Small 7.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1552, 'mf:M1552', 'Large 7.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row75, 'mf:M3552,M2552,M1552', '7.5-yr Total')}</td>
                <td></td>
              </tr>
              <tr>
                <td style="font-weight:600">11.5-yr</td>
                <td style="text-align:right">${kpi(pay.m3553, 'mf:M3553', 'Micro 11.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m2553, 'mf:M2553', 'Small 11.5-yr')}</td>
                <td style="text-align:right">${kpi(pay.m1553, 'mf:M1553', 'Large 11.5-yr')}</td>
                <td style="text-align:right;font-weight:600">${kpi(row115, 'mf:M3553,M2553,M1553', '11.5-yr Total')}</td>
                <td></td>
              </tr>
            </tbody>
            <tfoot>
              <tr style="border-top:2px solid var(--color-border)">
                <td style="font-weight:600">Total</td>
                <td style="text-align:right;font-weight:600">${kpi(colMicro, 'mf:M3551,M3552,M3553', 'Micro Total')}</td>
                <td style="text-align:right;font-weight:600">${kpi(colSmall, 'mf:M2551,M2552,M2553', 'Small Total')}</td>
                <td style="text-align:right;font-weight:600">${kpi(colLarge, 'mf:M1551,M1552,M1553', 'Large Total')}</td>
                <td style="text-align:right;font-weight:700">${kpi(grandTotal, 'mf:M3551,M3552,M3553,M2551,M2552,M2553,M1551,M1552,M1553', 'All Payments')}</td>
                <td style="text-align:right;font-weight:600">${kpi(pay.m1559 || 0, 'mf:M1559', '37 CFR 1.28(c)')}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <!-- Declarations -->
        <div style="flex:1;min-width:250px">
          <div style="color:#c0392b;font-weight:600;margin-bottom:0.5rem">Declarations:</div>
          <div style="display:flex;gap:2rem;flex-wrap:wrap">
            <div>
              <div style="font-weight:600;margin-bottom:0.25rem">Transitions</div>
              <div style="font-size:0.85rem;line-height:1.8">
                Micro &gt; Small: ${kpi(pg.mtos || 0, 'mf:MTOS', 'Micro \u2192 Small')}<br>
                Small &gt; Micro: ${kpi(pg.stom, 'mf:STOM', 'Small \u2192 Micro')}<br>
                Small &gt; Large: ${kpi(pg.stol, 'mf:STOL', 'Small \u2192 Large')}<br>
                Large &gt; Small: ${kpi(pg.ltos, 'mf:LTOS', 'Large \u2192 Small')}
              </div>
            </div>
            <div>
              <div style="font-weight:600;margin-bottom:0.25rem">Status</div>
              <div style="font-size:0.85rem;line-height:1.8">
                ${statusBadge('MICRO')}: ${kpi(pg.decl_micr || 0, 'mf:MICR', 'Declaration: Micro')}<br>
                ${statusBadge('SMALL')}: ${kpi(pg.decl_smal || 0, 'mf:SMAL', 'Declaration: Small')}<br>
                ${statusBadge('LARGE')}: ${kpi(pg.decl_big || 0, 'mf:BIG.', 'Declaration: Large')}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Filter label (shown when a KPI is clicked) -->
    <div id="es-filter-label" class="es-filter-label hidden"></div>

    <!-- Micro chart legend (shown when timelines are loaded) -->
    <div id="es-microchart-legend" class="es-microchart-legend hidden"></div>

    <div class="results-header" style="margin-top:1rem">
      <strong>Patent Details</strong>
      <span id="es-shown-count" class="results-count">${data.results.length.toLocaleString()} shown</span>
    </div>
    <div class="table-scroll-wrap">
      <table class="data-table" id="es-app-table">
        <thead><tr>
          <th data-sort-key="0">Patent #</th>
          <th data-sort-key="1">App #</th>
          <th data-sort-key="2">Grant Date</th>
          <th class="es-events-hdr">Events</th>
          <th data-sort-key="4">Prosecution</th>
          <th data-sort-key="5">Post-Grant First</th>
          <th data-sort-key="6">Post-Grant Current</th>
          <th data-sort-key="7">Changed?</th>
          <th data-sort-key="8">Ownership</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div id="es-scroll-sentinel" class="es-scroll-sentinel" style="text-align:center;padding:1rem;color:#6b7280;font-size:0.85rem"></div>
  `;

  appArea.innerHTML = html;

  // Store all results in portfolio state for client-side pagination
  _portfolioState.allResults = data.results;
  _portfolioState.filteredResults = data.results;
  _portfolioState.currentOffset = 0;
  _portfolioState.sortColumn = null;
  _portfolioState.sortDirection = null;
  _portfolioState.filterSpec = null;
  _portfolioState.filterLabel = null;
  _portfolioState.entityName = data.applicant_name || '';

  const tbl = document.getElementById('es-app-table');
  if (tbl) {
    // Server-side sort callback: sort from in-memory array, re-render first page
    enableTableSorting(tbl, (colIdx, dir) => {
      if (colIdx === null || dir === 0) {
        _portfolioState.sortColumn = null;
        _portfolioState.sortDirection = null;
      } else {
        _portfolioState.sortColumn = SORT_COL_MAP[colIdx] || null;
        _portfolioState.sortDirection = dir === 1 ? 'asc' : 'desc';
      }
      reloadPortfolioTable();
    });
    addColumnPicker(tbl, {
      defaultHidden: ['Grant Date', 'Prosecution', 'Post-Grant First', 'Post-Grant Current', 'Changed?', 'Ownership'],
    });
  }

  // Render first page of results
  const firstPage = data.results.slice(0, PAGE_SIZE);
  appendResultRows(firstPage);
  _portfolioState.currentOffset = firstPage.length;
  updateScrollSentinel();
  setupInfiniteScroll();

  // Wire clickable KPIs
  appArea.querySelectorAll('.kpi-clickable').forEach(el => {
    el.addEventListener('click', () => {
      filterPatentTable(el.dataset.filter, el.dataset.label, el);
    });
  });

  // Fire async litigation lookup for all granted patents
  const grantedPatents = data.results
    .filter(r => r.patent_number)
    .map(r => r.patent_number);
  if (grantedPatents.length > 0) {
    fetchLitigationData(grantedPatents);
  }

  // Auto-trigger prosecution payment analysis (no button needed)
  const allApps = data.results.map(r => r.application_number).filter(Boolean);
  if (allApps.length > 0) {
    fetchProsecutionPaymentsAuto(data.applicant_name || '', allApps);
  }
}

// ── Litigation Data (async, non-blocking) ──────────────────────

async function fetchLitigationData(patentNumbers) {
  const kpiSection = document.getElementById('es-litigation-kpis');
  const statusEl = document.getElementById('es-litigation-status');
  if (!kpiSection) return;
  kpiSection.style.display = '';

  try {
    const data = await apiPost('/api/litigation/lookup', {
      patent_numbers: patentNumbers,
    });

    // Store for micro chart injection + litigation table
    window._litigationData = data.litigated_patents || {};
    window._litigationCases = data.cases || [];

    const cases = window._litigationCases;
    const litigatedCount = data.litigated_count || 0;
    const totalCases = data.total_cases || 0;
    const activeCases = cases.filter(c => _isActiveCase(c)).length;
    const resolvedCases = totalCases - activeCases;
    const uniqueCourts = new Set(cases.map(c => c.court).filter(Boolean)).size;

    // Populate KPI values with clickable spans
    const litKpi = (id, val, filter, label) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = val
        ? `<span class="kpi-clickable lit-kpi" data-litfilter="${filter}" data-label="${label}">${val.toLocaleString()}</span>`
        : '0';
    };
    litKpi('es-litigated-count', litigatedCount, 'all', 'Litigated Patents');
    litKpi('es-total-cases', totalCases, 'all', 'All Litigation Cases');
    litKpi('es-active-cases', activeCases, 'active', 'Active Cases');
    litKpi('es-resolved-cases', resolvedCases, 'resolved', 'Resolved Cases');
    litKpi('es-courts-count', uniqueCourts, 'all', 'All Litigation Cases');

    statusEl.textContent = `${data.from_cache || 0} from cache, ${data.freshly_queried || 0} freshly queried`;

    // Set data-litigated on table rows
    const tbl = document.getElementById('es-app-table');
    if (tbl) {
      tbl.querySelectorAll('tbody tr').forEach(row => {
        const pn = row.dataset.pn;
        if (pn && window._litigationData[pn]) {
          row.dataset.litigated = '1';
        }
      });
    }

    // Wire litigation KPI clicks
    kpiSection.querySelectorAll('.lit-kpi').forEach(el => {
      el.addEventListener('click', () => {
        const filter = el.dataset.litfilter;
        const label = el.dataset.label;

        // Toggle off if same KPI clicked again
        if (el.classList.contains('kpi-active')) {
          el.classList.remove('kpi-active');
          hideLitigationTable();
          // Reset patent table if "Litigated Patents" was active
          const tbl = document.getElementById('es-app-table');
          if (tbl) {
            const rows = tbl.querySelectorAll('tbody tr');
            rows.forEach(r => { r.style.display = ''; });
            const filterLabel = document.getElementById('es-filter-label');
            const shownCount = document.getElementById('es-shown-count');
            if (filterLabel) filterLabel.classList.add('hidden');
            if (shownCount) shownCount.textContent = `${rows.length.toLocaleString()} shown`;
          }
          clearMicroCharts();
          return;
        }

        // Clear previous active highlights (both patent KPIs and lit KPIs)
        appArea.querySelectorAll('.kpi-active').forEach(a => a.classList.remove('kpi-active'));
        el.classList.add('kpi-active');

        // Show litigation table with appropriate filter
        if (filter === 'active') {
          renderLitigationTable(c => _isActiveCase(c), label);
        } else if (filter === 'resolved') {
          renderLitigationTable(c => !_isActiveCase(c), label);
        } else {
          renderLitigationTable(null, label);
        }

        // If "Litigated Patents" KPI, also filter the patent table
        if (el.closest('#es-litigated-count')) {
          _filterPatentTableForLitigation();
        }
      });
    });
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
}

/** Check if a case is active (not closed/terminated). */
function _isActiveCase(c) {
  const s = (c.status || '').toLowerCase();
  return s !== 'closed' && s !== 'terminated' && s !== 'resolved' && s !== 'settled';
}

/** Filter patent table to show only litigated patents (state-based). */
function _filterPatentTableForLitigation() {
  _portfolioState.filterSpec = 'litigation:litigated';
  _portfolioState.filterLabel = 'Litigated Patents';
  reloadPortfolioTable();

  const filterLabel = document.getElementById('es-filter-label');
  const filtered = _portfolioState.filteredResults.length;
  const total = _portfolioState.allResults.length;
  if (filterLabel) {
    filterLabel.innerHTML = `Filtered: <strong>Litigated Patents</strong> &mdash; ${filtered.toLocaleString()} of ${total.toLocaleString()} patents <button class="es-filter-clear" title="Clear filter">&times;</button>`;
    filterLabel.classList.remove('hidden');
    filterLabel.querySelector('.es-filter-clear')?.addEventListener('click', () => {
      appArea.querySelectorAll('.kpi-active').forEach(a => a.classList.remove('kpi-active'));
      filterLabel.classList.add('hidden');
      _portfolioState.filterSpec = null;
      _portfolioState.filterLabel = null;
      reloadPortfolioTable();
      hideLitigationTable();
    });
  }
}

/** Render the litigation cases table. */
function renderLitigationTable(filterFn, title) {
  const wrap = document.getElementById('es-litigation-table-wrap');
  if (!wrap) return;

  const cases = window._litigationCases || [];
  const filtered = filterFn ? cases.filter(filterFn) : cases;

  let html = `<h4 style="font-size:1rem;margin-bottom:0.5rem">${escHtml(title || 'Litigation Cases')} (${filtered.length})</h4>`;
  html += '<div class="table-scroll-wrap"><table id="es-litigation-table" class="data-table"><thead><tr>';

  const cols = [
    { key: 'case_no', label: 'Case' },
    { key: 'filed_date', label: 'Filing Date' },
    { key: 'status', label: 'Status' },
    { key: 'court', label: 'Court' },
    { key: 'plaintiff', label: 'Plaintiff' },
    { key: 'defendant', label: 'Defendant' },
    { key: 'cause_of_action', label: 'Cause of Action' },
    { key: 'entity_type', label: 'Plaintiff Entity Type' },
    { key: 'industry', label: 'Industry' },
    { key: 'flag', label: 'Source' },
    { key: 'portfolio_patents', label: 'Patents in Case' },
    { key: 'judge', label: 'Judge' },
    { key: 'closed_date', label: 'Termination Date' },
    { key: 'product', label: 'Infringed Product' },
  ];
  const defaultHidden = ['Cause of Action', 'Plaintiff Entity Type', 'Industry', 'Source', 'Patents in Case', 'Judge', 'Termination Date', 'Infringed Product'];

  for (const col of cols) {
    html += `<th>${escHtml(col.label)}</th>`;
  }
  html += '</tr></thead><tbody>';

  for (const c of filtered) {
    html += '<tr>';
    for (const col of cols) {
      let val = c[col.key] || '';
      if (col.key === 'portfolio_patents' && Array.isArray(val)) {
        val = val.join(', ');
      }
      html += `<td>${escHtml(String(val))}</td>`;
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  wrap.innerHTML = html;
  wrap.style.display = '';

  const litTbl = document.getElementById('es-litigation-table');
  if (litTbl) {
    stampOriginalOrder(litTbl);
    enableTableSorting(litTbl);
    addColumnPicker(litTbl, { defaultHidden });
  }
}

/** Hide the litigation table. */
function hideLitigationTable() {
  const wrap = document.getElementById('es-litigation-table-wrap');
  if (wrap) {
    wrap.innerHTML = '';
    wrap.style.display = 'none';
  }
}

// ── Prosecution Payment Analysis ─────────────────────────────────

/**
 * Auto-trigger prosecution payment analysis — single API call to entity-level endpoint.
 * Server does all batching and caching internally.
 */
async function fetchProsecutionPaymentsAuto(entityName, allApps) {
  const tbl = document.getElementById('es-app-table');
  const statusEl = document.getElementById('es-pros-pay-status');
  const progressEl = document.getElementById('es-pros-pay-progress');
  const kpisEl = document.getElementById('es-pros-pay-kpis');
  if (!tbl || allApps.length === 0) return;

  // Show progress
  if (progressEl) progressEl.style.display = '';
  if (statusEl) statusEl.textContent = `Analyzing ${allApps.length.toLocaleString()} applications...`;

  try {
    const merged = await apiPost('/api/entity-status/entity-prosecution-kpis', {
      applicant_name: entityName,
      application_numbers: allApps,
    });

    // Store globally for sparkline injection
    window._prosecutionData = merged;

    // Fetch extraction data in parallel (non-blocking, enriches tooltips)
    fetchExtractionData(allApps);

    // Fetch extraction progress (non-blocking, shows warning + gauges)
    fetchExtractionProgress(entityName, allApps);

    // Queue app numbers for extraction and trigger worker if not running
    queueExtraction(entityName, allApps);

    // Populate KPIs
    const k = merged.kpis || {};
    const setKpi = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = (val || 0).toLocaleString();
    };
    setKpi('es-pros-pay-small', k.small);
    setKpi('es-pros-pay-micro', k.micro);
    setKpi('es-pros-pay-large', k.large);
    setKpi('es-pros-pay-total', k.total);
    setKpi('es-pros-pay-apps', k.apps_with_findings);
    setKpi('es-pros-pay-small-10y', k.small_10y);
    setKpi('es-pros-pay-micro-10y', k.micro_10y);
    setKpi('es-pros-pay-large-10y', k.large_10y);
    setKpi('es-pros-pay-total-10y', k.total_10y);
    setKpi('es-pros-pay-apps-10y', k.apps_with_findings_10y);

    // Dollar KPIs
    const setDollar = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = '$' + Math.round(val || 0).toLocaleString();
    };
    setDollar('es-pros-pay-dollars-paid', k.reduced_paid);
    setDollar('es-pros-pay-dollars-large', k.reduced_large_rate);
    setDollar('es-pros-pay-dollars-delta', k.reduced_underpayment);
    setDollar('es-pros-pay-dollars-paid-10y', k.reduced_paid_10y);
    setDollar('es-pros-pay-dollars-large-10y', k.reduced_large_rate_10y);
    setDollar('es-pros-pay-dollars-delta-10y', k.reduced_underpayment_10y);

    if (kpisEl) kpisEl.style.display = '';

    // Set data-prospay on table rows (comma-separated statuses)
    tbl.querySelectorAll('tbody tr').forEach(row => {
      const app = row.dataset.app;
      if (!app || !merged.timelines || !merged.timelines[app]) return;
      const statuses = new Set();
      for (const p of (merged.timelines[app].payments || [])) {
        statuses.add(p.status);
      }
      row.dataset.prospay = [...statuses].join(',');
    });

    // KPI click handlers already wired by renderApplicantPortfolio() at line 1045
    // (all .kpi-clickable in appArea, including prosecution KPIs)

    // Render summary and detail tables
    renderProsecutionSummaryTable(merged);
    renderProsecutionDetailTable(merged);

    // Re-render sparklines with prosecution data injected
    const visiblePatents = [];
    tbl.querySelectorAll('tbody tr').forEach(row => {
      if (row.style.display !== 'none' && row.dataset.pn) {
        visiblePatents.push(row.dataset.pn);
      }
    });
    if (visiblePatents.length > 0) {
      fetchAndRenderMicroCharts(visiblePatents);
    }

    const cs = merged.cache_stats || {};
    const cacheMsg = cs.from_cache > 0
      ? ` (${cs.from_cache.toLocaleString()} cached, ${cs.freshly_analyzed.toLocaleString()} fresh)`
      : '';
    if (statusEl) statusEl.textContent = `${(k.total || 0).toLocaleString()} payment events across ${allApps.length.toLocaleString()} applications${cacheMsg}`;
    if (progressEl) {
      // Hide spinner but keep status text
      const spinner = progressEl.querySelector('.spinner');
      if (spinner) spinner.style.display = 'none';
    }

  } catch (err) {
    if (statusEl) statusEl.textContent = `Analysis failed: ${err.message || err}`;
    if (progressEl) {
      const spinner = progressEl.querySelector('.spinner');
      if (spinner) spinner.style.display = 'none';
    }
  }
}

/** Render prosecution payment summary pivot table (Year × Event Code). */
function renderProsecutionSummaryTable(data) {
  const wrap = document.getElementById('es-pros-summary-wrap');
  if (!wrap) return;

  const summary = data.summary || {};
  const years = Object.keys(summary).sort();
  if (years.length === 0) {
    wrap.innerHTML = '<p class="text-muted">No prosecution payments found.</p>';
    wrap.style.display = '';
    return;
  }

  // Collect all event codes across all years
  const allCodes = new Set();
  for (const yr of years) {
    for (const code of Object.keys(summary[yr])) {
      allCodes.add(code);
    }
  }
  const codes = [...allCodes].sort();

  let html = `
    <div class="card">
      <h4 class="card-title" style="font-size:1rem">Payment Summary by Year</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">All prosecution payments (Small + Micro + Large rate)</p>
      <div class="table-scroll-wrap">
        <table class="data-table" id="es-pros-summary-table">
          <thead><tr>
            <th data-sort-key="0">Year</th>`;
  codes.forEach((c, i) => {
    const desc = PROS_PAY_DESCRIPTIONS[c] || c;
    html += `<th data-sort-key="${i + 1}" style="text-align:right" title="${escHtml(desc)}">${escHtml(c)}</th>`;
  });
  html += `<th style="text-align:right;font-weight:700">Total</th></tr></thead><tbody>`;

  const colTotals = {};
  let grandTotal = 0;

  for (const yr of years) {
    let rowTotal = 0;
    html += `<tr><td style="font-weight:600">${escHtml(yr)}</td>`;
    for (const code of codes) {
      const cnt = summary[yr][code] || 0;
      rowTotal += cnt;
      colTotals[code] = (colTotals[code] || 0) + cnt;
      html += `<td style="text-align:right">${cnt || ''}</td>`;
    }
    grandTotal += rowTotal;
    html += `<td style="text-align:right;font-weight:600">${rowTotal}</td></tr>`;
  }

  // Footer row
  html += `</tbody><tfoot><tr style="border-top:2px solid var(--color-border)"><td style="font-weight:700">Total</td>`;
  for (const code of codes) {
    html += `<td style="text-align:right;font-weight:600">${colTotals[code] || 0}</td>`;
  }
  html += `<td style="text-align:right;font-weight:700">${grandTotal}</td></tr></tfoot></table></div></div>`;

  wrap.innerHTML = html;
  wrap.style.display = '';

  const tbl = document.getElementById('es-pros-summary-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
  }
}

/** Render detailed prosecution payment table (Small + Micro findings only). */
function renderProsecutionDetailTable(data) {
  const wrap = document.getElementById('es-pros-detail-wrap');
  if (!wrap) return;

  const details = data.payments_detail || [];
  if (details.length === 0) {
    wrap.innerHTML = '<div class="card"><p class="text-muted">No Small or Micro rate payments found.</p></div>';
    wrap.style.display = '';
    return;
  }

  let html = `
    <div class="card">
      <h4 class="card-title" style="font-size:1rem">Flagged Payments — Small &amp; Micro Rate</h4>
      <p class="text-muted" style="margin:0 0 0.5rem">${details.length.toLocaleString()} payment events made at reduced entity rates</p>
      <div class="table-scroll-wrap">
        <table class="data-table" id="es-pros-detail-table">
          <thead><tr>
            <th data-sort-key="0">App #</th>
            <th data-sort-key="1">Date</th>
            <th data-sort-key="2">Code</th>
            <th data-sort-key="3">Description</th>
            <th data-sort-key="4">Status</th>
            <th data-sort-key="5" style="text-align:right">Paid ($)</th>
            <th data-sort-key="6" style="text-align:right">Large ($)</th>
            <th data-sort-key="7" style="text-align:right">Underpay ($)</th>
            <th data-sort-key="8">Fee Category</th>
            <th data-sort-key="9">Origin Code</th>
            <th data-sort-key="10">Origin Date</th>
            <th data-sort-key="11" style="text-align:center">Invoice</th>
          </tr></thead><tbody>`;

  for (const d of details) {
    const paid = d.amount_paid || 0;
    const large = d.large_rate || 0;
    const underpay = d.underpayment || 0;
    const catLabel = (d.fee_category || '').replace(/_/g, ' ');
    html += `<tr>
      <td>${escHtml(d.application_number || '')}</td>
      <td>${escHtml(d.event_date || '')}</td>
      <td title="${escHtml(PROS_PAY_DESCRIPTIONS[d.event_code] || d.event_code || '')}">${escHtml(d.event_code || '')}</td>
      <td>${escHtml(d.event_description || '')}</td>
      <td>${statusBadge(d.claimed_status)}</td>
      <td style="text-align:right">$${paid.toLocaleString()}</td>
      <td style="text-align:right">$${large.toLocaleString()}</td>
      <td style="text-align:right${underpay > 0 ? ';color:#dc2626;font-weight:600' : ''}">$${underpay.toLocaleString()}</td>
      <td>${escHtml(catLabel)}</td>
      <td>${escHtml(d.origin_code || '')}</td>
      <td>${escHtml(d.origin_date || '')}</td>
      <td style="text-align:center"><button class="es-invoice-btn" data-app="${escHtml(d.application_number || '')}" title="View payment invoices">&#x1F4C4;</button></td>
    </tr>`;
  }

  html += '</tbody></table></div></div>';
  wrap.innerHTML = html;
  wrap.style.display = '';

  const tbl = document.getElementById('es-pros-detail-table');
  if (tbl) {
    stampOriginalOrder(tbl);
    enableTableSorting(tbl);
    addColumnPicker(tbl);
  }

  // Wire up invoice buttons
  wrap.querySelectorAll('.es-invoice-btn').forEach(btn => {
    btn.addEventListener('click', () => showInvoicePopup(btn.dataset.app));
  });
}

// ── Patent Table Filtering (for clickable KPIs) ─────────────────

/**
 * Filter the Patent Details table based on a KPI click.
 * Uses state-based filtering: filters the in-memory result set,
 * then re-renders the first page via reloadPortfolioTable().
 */
function filterPatentTable(filterSpec, label, clickedEl) {
  const filterLabel = document.getElementById('es-filter-label');

  // Toggle off if same KPI is clicked again
  const prevActive = appArea.querySelector('.kpi-active');
  if (prevActive === clickedEl) {
    prevActive.classList.remove('kpi-active');
    if (filterLabel) filterLabel.classList.add('hidden');
    _portfolioState.filterSpec = null;
    _portfolioState.filterLabel = null;
    hideLitigationTable();
    reloadPortfolioTable();
    return;
  }

  // Clear previous active highlight
  if (prevActive) prevActive.classList.remove('kpi-active');
  clickedEl.classList.add('kpi-active');

  // Hide litigation table when a non-litigation KPI is clicked
  hideLitigationTable();

  // Set filter in state and reload table
  _portfolioState.filterSpec = filterSpec;
  _portfolioState.filterLabel = label;
  reloadPortfolioTable();

  // Show filter label pill with count
  const filtered = _portfolioState.filteredResults.length;
  const total = _portfolioState.allResults.length;
  if (filterLabel) {
    filterLabel.innerHTML = `Filtered: <strong>${escHtml(label)}</strong> &mdash; ${filtered.toLocaleString()} of ${total.toLocaleString()} patents <button class="es-filter-clear" title="Clear filter">&times;</button>`;
    filterLabel.classList.remove('hidden');
    filterLabel.querySelector('.es-filter-clear').addEventListener('click', () => {
      clickedEl.classList.remove('kpi-active');
      filterLabel.classList.add('hidden');
      _portfolioState.filterSpec = null;
      _portfolioState.filterLabel = null;
      reloadPortfolioTable();
    });
  }
}

// ── Micro Chart Rendering ────────────────────────────────────────

function clampPct(pct) { return Math.max(0, Math.min(100, pct)); }

/** Create a colored line segment div in the track. */
function appendLine(track, leftPct, widthPct, color) {
  if (widthPct <= 0) return;
  const seg = document.createElement('div');
  seg.className = 'es-microchart-line';
  seg.style.left = leftPct + '%';
  seg.style.width = widthPct + '%';
  seg.style.backgroundColor = color;
  track.appendChild(seg);
}

/** Create an absolutely-positioned icon wrapper with an SVG inside. */
function createIconEl(svgFn, color, leftPct, ev) {
  const wrap = document.createElement('div');
  wrap.className = 'es-microchart-icon';
  wrap.style.left = leftPct + '%';
  wrap.style.color = color;
  wrap.title = `${ev.c} \u2014 ${ev.d}`;
  wrap.innerHTML = svgFn();
  return wrap;
}

/**
 * Fetch bulk timelines and render sparklines in the Events column.
 * Each sparkline has a colored status line (red/green/blue = large/small/micro)
 * that changes color at declaration/transition events, with icons for payments
 * and status events positioned on top.
 */
async function fetchAndRenderMicroCharts(patentNumbers, filterSpec) {
  const tbl = document.getElementById('es-app-table');
  if (!tbl) return;

  tbl.querySelectorAll('tbody tr').forEach(row => {
    if (row.style.display !== 'none') {
      const cell = row.querySelector('.es-events-cell');
      if (cell) cell.innerHTML = '<span class="text-muted" style="font-size:0.7rem">Loading...</span>';
    }
  });

  try {
    // Split into batches of 200 and fetch in parallel
    const BATCH = 200;
    const batches = [];
    for (let i = 0; i < patentNumbers.length; i += BATCH) {
      batches.push(patentNumbers.slice(i, i + BATCH));
    }
    const results = await Promise.all(
      batches.map(batch => apiPost('/api/entity-status/bulk-timelines', { patent_numbers: batch }))
    );

    // Merge all batch responses into one unified result
    const data = { timelines: {}, date_range: null };
    for (const r of results) {
      if (!r.date_range) continue;
      Object.assign(data.timelines, r.timelines);
      if (!data.date_range) {
        data.date_range = { ...r.date_range };
      } else {
        if (r.date_range.min < data.date_range.min) data.date_range.min = r.date_range.min;
        if (r.date_range.max > data.date_range.max) data.date_range.max = r.date_range.max;
      }
    }

    // Inject litigation events into timelines (if loaded)
    if (window._litigationData) {
      for (const [pn, cases] of Object.entries(window._litigationData)) {
        if (!data.timelines[pn]) continue;
        for (const c of cases) {
          if (c.filed_date) {
            data.timelines[pn].push({ d: c.filed_date, c: 'LITG', _case: c });
          }
        }
        data.timelines[pn].sort((a, b) => a.d.localeCompare(b.d));
      }
    }

    // Inject prosecution payment events into timelines (if loaded)
    // Only inject for patents in our current batch to avoid overwriting prior pages
    const requestedPnSet = new Set(patentNumbers);
    if (window._prosecutionData) {
      // Build pn→app map from table rows (only for requested patents)
      const pnToApp = {};
      tbl.querySelectorAll('tbody tr').forEach(row => {
        const pn = row.dataset.pn;
        const app = row.dataset.app;
        if (pn && app && requestedPnSet.has(pn)) pnToApp[pn] = app;
      });

      // Store prosecution segments per patent for phase-aware line coloring
      if (!data._prosSegmentsByPn) data._prosSegmentsByPn = {};

      for (const [pn, app] of Object.entries(pnToApp)) {
        const prosTimeline = window._prosecutionData.timelines[app];
        if (!prosTimeline || !prosTimeline.payments) continue;
        if (!data.timelines[pn]) data.timelines[pn] = [];

        // Save prosecution segments for this patent
        data._prosSegmentsByPn[pn] = prosTimeline.segments || [];

        for (const p of prosTimeline.payments) {
          data.timelines[pn].push({
            d: p.d,
            c: 'PROS_PAY',
            _prosCode: p.c,
            _prosDesc: p.desc,
            _prosStatus: p.status,
            _prosApp: app,
            _prosPaid: p.paid,
            _prosLarge: p.large,
            _prosDelta: p.delta,
            _prosCat: p.cat,
          });
        }

        // Also inject prosecution status-change events for yellow dot markers
        for (const seg of prosTimeline.segments) {
          if (seg.trigger) {
            data.timelines[pn].push({
              d: seg.start,
              c: seg.trigger,
            });
          }
        }

        data.timelines[pn].sort((a, b) => a.d.localeCompare(b.d));

        // Expand date range if prosecution data extends earlier
        if (prosTimeline.segments.length > 0) {
          const earliest = prosTimeline.segments[0].start;
          if (earliest && (!data.date_range || earliest < data.date_range.min)) {
            if (!data.date_range) data.date_range = { min: earliest, max: earliest };
            else data.date_range.min = earliest;
          }
        }
      }
    }

    if (!data.date_range) { clearMicroCharts(); return; }

    const minDate = new Date(data.date_range.min);
    const maxDate = new Date(data.date_range.max);
    const totalMs = maxDate.getTime() - minDate.getTime();
    if (totalMs <= 0) { clearMicroCharts(); return; }

    // Parse highlight codes from filter spec
    const highlightCodes = new Set();
    if (filterSpec) {
      const ci = filterSpec.indexOf(':');
      if (ci >= 0) filterSpec.slice(ci + 1).split(',').forEach(c => highlightCodes.add(c));
    }

    // Render sparkline into each visible row's Events cell (only for requested patents)
    tbl.querySelectorAll('tbody tr').forEach(row => {
      if (row.style.display === 'none') return;
      const pn = row.dataset.pn;
      const cell = row.querySelector('.es-events-cell');
      if (!cell || !pn) return;
      if (!requestedPnSet.has(pn)) return; // skip rows from other pages

      const events = data.timelines[pn] || [];
      if (events.length === 0) {
        cell.innerHTML = '<span class="text-muted" style="font-size:0.7rem">&mdash;</span>';
        return;
      }

      cell.innerHTML = '';
      const track = document.createElement('div');
      track.className = 'es-microchart-track';

      // ── Build colored status line (phase-aware: prosecution vs post-grant) ──
      const prosSegs = data._prosSegmentsByPn && data._prosSegmentsByPn[pn];
      const prosStatusToColor = s => s === 'SMALL' ? STATUS_COLORS.small
        : s === 'MICRO' ? STATUS_COLORS.micro : STATUS_COLORS.large;
      const dateToPct = d => clampPct(((new Date(d).getTime() - minDate.getTime()) / totalMs) * 100);

      // Find grant and expiration dates
      let grantDate = null, grantPct = null;
      let endPct = 100;
      for (const ev of events) {
        if (ev.c === 'GRNT' && !grantDate) {
          grantDate = new Date(ev.d);
          grantPct = dateToPct(ev.d);
        }
        if (ev.c === 'EXP.') {
          endPct = dateToPct(ev.d);
        }
      }

      const changePoints = [];
      let initColor;

      if (prosSegs && prosSegs.length > 0) {
        // Phase-aware: prosecution segments drive pre-grant, post-grant events drive post-grant
        initColor = prosStatusToColor(prosSegs[0].status);

        // Pre-grant change points from prosecution segments
        for (let i = 1; i < prosSegs.length; i++) {
          const seg = prosSegs[i];
          const pct = dateToPct(seg.start);
          if (grantPct !== null && pct >= grantPct) break;
          if (pct >= endPct) break;
          changePoints.push({ pct, color: prosStatusToColor(seg.status), code: seg.trigger });
        }

        // Post-grant: only use post-grant status events (M1/M2/M3, STOL/LTOS/MTOS/STOM)
        const lastProsColor = changePoints.length > 0
          ? changePoints[changePoints.length - 1].color : initColor;
        let currentPostColor = lastProsColor;
        for (const ev of events) {
          if (grantDate && new Date(ev.d) < grantDate) continue;
          const cat = classifyEvent(ev.c);
          let newColor = null;
          if (cat === 'large_payment' || cat === 'trans_to_large') newColor = STATUS_COLORS.large;
          else if (cat === 'small_payment' || cat === 'trans_to_small') newColor = STATUS_COLORS.small;
          else if (cat === 'micro_payment' || cat === 'trans_to_micro') newColor = STATUS_COLORS.micro;
          if (newColor && newColor !== currentPostColor) {
            const pct = dateToPct(ev.d);
            if (pct >= endPct) break;
            changePoints.push({ pct, color: newColor, code: ev.c });
            currentPostColor = newColor;
          }
        }
      } else {
        // No prosecution data: use only post-grant events (original behavior)
        initColor = inferInitialColor(events);
        let currentColor = initColor;
        for (const ev of events) {
          const newColor = statusColorForEvent(ev.c);
          if (newColor && newColor !== currentColor) {
            const pct = dateToPct(ev.d);
            if (pct >= endPct) break;
            changePoints.push({ pct, color: newColor, code: ev.c });
            currentColor = newColor;
          }
        }
      }

      // Draw line segments up to expiration point (or full width if no EXP.)
      let prevPct = 0;
      let lineColor = initColor;
      for (const cp of changePoints) {
        appendLine(track, prevPct, cp.pct - prevPct, lineColor);
        lineColor = cp.color;
        prevPct = cp.pct;
      }
      appendLine(track, prevPct, endPct - prevPct, lineColor);

      // ── Place icons on top of the line ──
      for (const ev of events) {
        const evDate = new Date(ev.d);
        const pct = clampPct(((evDate.getTime() - minDate.getTime()) / totalMs) * 100);
        const cat = classifyEvent(ev.c);

        let marker = null;
        if (cat === 'large_payment') {
          marker = createIconEl(svgBuilding, STATUS_COLORS.large, pct, ev);
        } else if (cat === 'small_payment') {
          marker = createIconEl(svgHouse, STATUS_COLORS.small, pct, ev);
        } else if (cat === 'micro_payment') {
          marker = createIconEl(svgPerson, STATUS_COLORS.micro, pct, ev);
        } else if (cat === 'expired') {
          marker = createIconEl(svgExpired, GRAY, pct, ev);
        } else if (cat === 'grant') {
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#8b5cf6'; // purple
          marker.title = `Grant \u2014 ${ev.d}`;
        } else if (cat === 'reminder') {
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#eab308';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        } else if (cat === 'attorney') {
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#92400e';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        } else if (cat === 'pros_payment') {
          const prosColor = ev._prosStatus === 'SMALL' ? STATUS_COLORS.small
            : ev._prosStatus === 'MICRO' ? STATUS_COLORS.micro
            : STATUS_COLORS.large;
          marker = createIconEl(svgDollar, prosColor, pct, ev);
          marker.title = '';  // disable native tooltip — we use custom rich tooltip
          marker.style.cursor = 'pointer';
          _attachProsPayTooltip(marker, ev);
          _attachProsPayClick(marker, ev);
        } else if (cat === 'litigation') {
          marker = createIconEl(svgStar, '#d4a017', pct, ev);
          marker.title = `Litigation filed \u2014 ${ev.d}${ev._case ? ' \u2014 ' + ev._case.case_no : ''}`;
        } else if (cat.startsWith('trans_to_') || cat.startsWith('decl_')) {
          // Status-change events: yellow dot with description
          marker = document.createElement('div');
          marker.className = 'es-microchart-dot-sm';
          marker.style.left = pct + '%';
          marker.style.backgroundColor = '#eab308'; // yellow
          const desc = _STATUS_CHANGE_DESC[ev.c] || ev.c;
          marker.title = `${desc} (${ev.c}) \u2014 ${ev.d}`;
        } else {
          // Catch-all: small gray dot
          marker = document.createElement('div');
          marker.className = 'es-microchart-other';
          marker.style.left = pct + '%';
          marker.title = `${ev.c} \u2014 ${ev.d}`;
        }

        if (marker && highlightCodes.has(ev.c)) {
          marker.classList.add('es-microchart-icon--hl');
        }
        if (marker) track.appendChild(marker);
      }

      cell.appendChild(track);
    });

    showMicroChartLegend();

  } catch (err) {
    clearMicroCharts();
  }
}

/** Clear all micro chart sparklines from the Events column. */
function clearMicroCharts() {
  const tbl = document.getElementById('es-app-table');
  if (tbl) {
    tbl.querySelectorAll('.es-events-cell').forEach(cell => { cell.innerHTML = ''; });
  }
  const legend = document.getElementById('es-microchart-legend');
  if (legend) legend.classList.add('hidden');
}

// ── Rich Tooltip + Click for Prosecution Payment Icons ──────────

/** Active tooltip element (shared singleton). */
let _activeTooltip = null;

/** Attach rich HTML tooltip to a PROS_PAY marker. */
function _attachProsPayTooltip(marker, ev) {
  marker.addEventListener('mouseenter', (e) => {
    _removeActiveTooltip();

    const tip = document.createElement('div');
    tip.className = 'es-microchart-tooltip';

    // Header: event code + status + date
    const statusBadge = ev._prosStatus
      ? `<span class="es-status-badge es-status-${(ev._prosStatus || '').toLowerCase()}">${ev._prosStatus}</span>`
      : '';
    let html = `<div class="tt-header">${escHtml(ev._prosCode || '')} ${statusBadge} — ${ev.d}</div>`;

    // Algorithm estimate
    if (ev._prosDesc) {
      html += `<div class="tt-desc">${escHtml(ev._prosDesc)}</div>`;
    }
    if (ev._prosPaid != null) {
      html += `<div class="tt-algo">`;
      html += `Paid: <strong>$${Math.round(ev._prosPaid).toLocaleString()}</strong>`;
      if (ev._prosLarge != null) html += ` · Large rate: $${Math.round(ev._prosLarge).toLocaleString()}`;
      if (ev._prosDelta != null && ev._prosDelta > 0) html += ` · <span class="tt-delta">Δ $${Math.round(ev._prosDelta).toLocaleString()}</span>`;
      html += `</div>`;
    }

    // Extraction data (if available for this application)
    const appExtractions = window._extractionData && ev._prosApp
      ? window._extractionData[ev._prosApp]
      : null;

    if (appExtractions && appExtractions.length > 0) {
      html += `<div class="tt-divider"></div>`;

      // Derive entity status from fee codes across all invoices
      const invoiceStatuses = new Set();
      for (const ext of appExtractions) {
        for (const fee of (ext.fees || [])) {
          const fc = (fee.fee_code || '').toString();
          if (fc.length > 0) {
            const d = fc[0];
            if (d === '1') invoiceStatuses.add('LARGE');
            else if (d === '2' || d === '4') invoiceStatuses.add('SMALL');
            else if (d === '3') invoiceStatuses.add('MICRO');
          }
        }
      }

      // Show invoice-derived entity status prominently
      if (invoiceStatuses.size > 0) {
        const badges = [...invoiceStatuses].map(s =>
          `<span class="es-status-badge es-status-${s.toLowerCase()}">${s}</span>`
        ).join(' ');
        let discrepancy = '';
        if (ev._prosStatus && !invoiceStatuses.has(ev._prosStatus)) {
          discrepancy = ' <span class="tt-discrepancy">differs from event code</span>';
        }
        html += `<div class="tt-invoice-status">Invoice: ${badges}${discrepancy}</div>`;
      }

      html += `<div class="tt-ext-header">\uD83D\uDCC4 ${appExtractions.length} invoice${appExtractions.length > 1 ? 's' : ''} extracted</div>`;

      // Show up to 3 most relevant invoices (closest to event date first)
      const sorted = [...appExtractions].sort((a, b) => {
        const da = Math.abs(new Date(a.mail_date) - new Date(ev.d));
        const db = Math.abs(new Date(b.mail_date) - new Date(ev.d));
        return da - db;
      });
      const shown = sorted.slice(0, 3);

      for (const ext of shown) {
        const amt = ext.total_amount != null ? `$${Number(ext.total_amount).toLocaleString(undefined, {minimumFractionDigits: 2})}` : '\u2014';
        const feeCount = (ext.fees || []).length;
        const extStatus = ext.entity_status
          ? `<span class="es-status-badge es-status-${ext.entity_status.toLowerCase()}">${ext.entity_status}</span>`
          : '';
        html += `<div class="tt-ext-row">`;
        html += `<span class="tt-ext-date">${ext.mail_date || '?'}</span> `;
        html += `<span class="tt-ext-code">${escHtml(ext.doc_code)}</span> `;
        html += `${extStatus} `;
        html += `<strong>${amt}</strong>`;
        if (feeCount > 0) html += ` <span class="tt-ext-fees">(${feeCount} fees)</span>`;
        // View PDF link using existing endpoint
        if (ext.gcs_path) {
          const pdfParams = new URLSearchParams({
            application_number: ev._prosApp,
            download_url: '',
            filename: ext.gcs_path.split('/').pop() || 'invoice.pdf',
            cached_gcs_path: ext.gcs_path,
          });
          html += ` <a class="tt-view-pdf" href="/api/prosecution/invoice-pdf?${pdfParams}" target="_blank" onclick="event.stopPropagation()">View PDF</a>`;
        }
        html += `</div>`;

        // Show individual fee items for the closest invoice
        if (ext === shown[0] && ext.fees && ext.fees.length > 0) {
          html += `<div class="tt-fee-items">`;
          for (const fee of ext.fees.slice(0, 6)) {
            const feeAmt = fee.amount != null ? `$${Number(fee.amount).toLocaleString(undefined, {minimumFractionDigits: 2})}` : '';
            html += `<div class="tt-fee-item">${escHtml(fee.description || fee.fee_code || '?')} \u2014 ${feeAmt}</div>`;
          }
          if (ext.fees.length > 6) {
            html += `<div class="tt-fee-item tt-more">+ ${ext.fees.length - 6} more</div>`;
          }
          html += `</div>`;
        }
      }
      if (appExtractions.length > 3) {
        html += `<div class="tt-more">+ ${appExtractions.length - 3} more invoices</div>`;
      }
    } else if (window._extractionData !== undefined) {
      // Extraction data loaded but nothing for this app
      html += `<div class="tt-no-ext">No invoice PDFs extracted yet</div>`;
    }

    html += `<div class="tt-click-hint">Click to view invoices</div>`;
    tip.innerHTML = html;

    // Position tooltip near the marker
    document.body.appendChild(tip);
    const rect = marker.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    let left = rect.left + window.scrollX - tipRect.width / 2 + rect.width / 2;
    let top = rect.top + window.scrollY - tipRect.height - 8;
    // Keep within viewport
    if (left < 4) left = 4;
    if (left + tipRect.width > window.innerWidth - 4) left = window.innerWidth - tipRect.width - 4;
    if (top < 4) top = rect.bottom + window.scrollY + 8; // flip below if no room above
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';

    _activeTooltip = tip;
  });

  marker.addEventListener('mouseleave', () => {
    // Small delay so user can move to tooltip
    setTimeout(() => {
      if (_activeTooltip && !_activeTooltip.matches(':hover')) {
        _removeActiveTooltip();
      }
    }, 200);
  });
}

function _removeActiveTooltip() {
  if (_activeTooltip) {
    _activeTooltip.remove();
    _activeTooltip = null;
  }
}

// Remove tooltip when mouse leaves it
document.addEventListener('mouseover', (e) => {
  if (_activeTooltip && !_activeTooltip.contains(e.target) &&
      !e.target.closest('.es-microchart-icon')) {
    _removeActiveTooltip();
  }
});

/** Attach click handler to open invoice popup for a PROS_PAY marker. */
function _attachProsPayClick(marker, ev) {
  marker.addEventListener('click', (e) => {
    e.stopPropagation();
    _removeActiveTooltip();
    if (ev._prosApp) {
      showInvoicePopup(ev._prosApp);
    }
  });
}

/** Fetch extraction data for all applications and store globally. */
async function fetchExtractionData(allApps) {
  if (!allApps || allApps.length === 0) return;
  try {
    const resp = await apiPost('/api/entity-status/extraction-data', {
      application_numbers: allApps,
    });
    window._extractionData = resp.extractions || {};
    const s = resp.stats || {};
    if (s.total_extractions > 0) {
      const statusEl = document.getElementById('es-extraction-status');
      if (statusEl) {
        statusEl.textContent = `📄 ${s.total_extractions.toLocaleString()} invoices extracted across ${s.apps_with_extractions.toLocaleString()} applications`;
        statusEl.style.display = '';
      }
    }
  } catch (err) {
    // Non-critical — tooltips just won't show extraction data
    window._extractionData = {};
  }
}

/**
 * Fetch extraction pipeline progress and render warning banner + gauges.
 * Polls every 30 seconds while extraction is in progress.
 */
let _extractionPollTimer = null;

async function fetchExtractionProgress(entityName, allApps) {
  if (!allApps || allApps.length === 0) return;

  const warningEl = document.getElementById('es-event-code-warning');
  const progressSection = document.getElementById('es-extraction-progress-section');
  const retrievalPctEl = document.getElementById('es-retrieval-pct');
  const retrievalFillEl = document.getElementById('es-retrieval-fill');
  const retrievalDetailEl = document.getElementById('es-retrieval-detail');
  const extractPctEl = document.getElementById('es-extract-pct');
  const extractFillEl = document.getElementById('es-extract-fill');
  const extractDetailEl = document.getElementById('es-extract-detail');

  if (!warningEl || !progressSection) return;

  try {
    const resp = await apiPost('/api/entity-status/extraction-progress', {
      representative_name: entityName,
      application_numbers: allApps,
    });

    const phase = resp.phase || 'not_started';
    const totalApps = resp.total_apps_in_portfolio || allApps.length;
    const appsChecked = resp.apps_checked || 0;
    const totalRetrieved = resp.total_docs_retrieved || 0;
    const extracted = resp.extracted_docs || 0;
    const pending = resp.pending_extraction || 0;
    const failed = resp.failed_docs || 0;
    const noDocs = resp.no_docs_apps || 0;
    const retrievalPct = resp.retrieval_pct || 0;
    const extractionPct = resp.extraction_pct || 0;

    if (phase === 'complete' && extracted > 0) {
      // Extraction complete — green success message, switch to invoice KPIs
      warningEl.className = 'es-event-code-warning ready';
      warningEl.innerHTML = '<strong>\u2713 Invoice KPIs Active:</strong> ' +
        'Dollar amounts computed from ' +
        extracted.toLocaleString() + ' extracted payment invoices across ' +
        appsChecked.toLocaleString() + ' applications.' +
        (noDocs > 0 ? ' ' + noDocs.toLocaleString() + ' apps had no payment receipts.' : '') +
        (failed > 0 ? ' ' + failed.toLocaleString() + ' extractions failed.' : '');
      warningEl.style.display = '';
      progressSection.style.display = 'none';
      if (_extractionPollTimer) { clearInterval(_extractionPollTimer); _extractionPollTimer = null; }

      // Fetch invoice-based KPIs to replace event-code dollar amounts
      fetchInvoiceKpis(entityName, allApps);

    } else if (phase === 'not_started') {
      // No extraction data — red warning, no gauges
      warningEl.className = 'es-event-code-warning';
      warningEl.style.display = '';
      progressSection.style.display = 'none';
      if (_extractionPollTimer) { clearInterval(_extractionPollTimer); _extractionPollTimer = null; }

    } else {
      // In progress — red warning + both gauges
      warningEl.className = 'es-event-code-warning';
      warningEl.style.display = '';
      progressSection.style.display = '';

      // Gauge 1: PDF Retrieval
      retrievalPctEl.textContent = Math.round(retrievalPct) + '%';
      retrievalFillEl.style.width = Math.round(retrievalPct) + '%';
      const retParts = [
        appsChecked.toLocaleString() + ' of ' + totalApps.toLocaleString() + ' apps checked',
        totalRetrieved.toLocaleString() + ' invoices found',
      ];
      if (noDocs > 0) retParts.push(noDocs.toLocaleString() + ' apps with no payment receipts');
      retrievalDetailEl.textContent = retParts.join(' \u00B7 ');

      // Gauge 2: Data Extraction
      if (totalRetrieved > 0) {
        extractPctEl.textContent = Math.round(extractionPct) + '%';
        extractFillEl.style.width = Math.round(extractionPct) + '%';
        const extParts = [
          extracted.toLocaleString() + ' of ' + totalRetrieved.toLocaleString() + ' invoices extracted',
        ];
        if (failed > 0) extParts.push(failed.toLocaleString() + ' failed');
        extractDetailEl.textContent = extParts.join(' \u00B7 ');
      }

      // Start polling if not already
      if (!_extractionPollTimer) {
        _extractionPollTimer = setInterval(() => fetchExtractionProgress(entityName, allApps), 30000);
      }
    }
  } catch (err) {
    // Non-critical — show red warning without gauges
    warningEl.className = 'es-event-code-warning';
    warningEl.style.display = '';
    progressSection.style.display = 'none';
  }
}

/**
 * Queue application numbers for invoice extraction and trigger the worker.
 * Fire-and-forget — the extraction progress gauges (already polling every 30s)
 * will show progress as the worker processes apps.
 */
async function queueExtraction(entityName, allApps) {
  if (!allApps || allApps.length === 0) return;
  try {
    const resp = await apiPost('/api/entity-status/queue-extraction', {
      application_numbers: allApps,
      representative_name: entityName,
    });
    console.log('Extraction queue response:', resp);
  } catch (err) {
    console.warn('Failed to queue extraction:', err);
  }
}

/**
 * Fetch invoice-based KPIs and overlay them on the Dollar Impact section.
 * Called when extraction is complete (phase === 'complete').
 */
async function fetchInvoiceKpis(entityName, allApps) {
  if (!allApps || allApps.length === 0) return;
  try {
    const resp = await apiPost('/api/entity-status/invoice-kpis', {
      applicant_name: entityName,
      application_numbers: allApps,
    });

    window._invoiceKpis = resp;

    const k = resp.kpis || {};

    // Overlay dollar KPIs with invoice-based numbers
    const setDollar = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = '$' + Math.round(val || 0).toLocaleString();
    };
    setDollar('es-pros-pay-dollars-paid', k.reduced_paid);
    setDollar('es-pros-pay-dollars-large', k.reduced_large_rate);
    setDollar('es-pros-pay-dollars-delta', k.reduced_underpayment);
    setDollar('es-pros-pay-dollars-paid-10y', k.reduced_paid_10y);
    setDollar('es-pros-pay-dollars-large-10y', k.reduced_large_rate_10y);
    setDollar('es-pros-pay-dollars-delta-10y', k.reduced_underpayment_10y);

    // Switch data source badge to green "Invoice Data"
    const badge = document.getElementById('es-dollar-source-badge');
    if (badge) {
      badge.className = 'es-data-source-badge invoice';
      badge.textContent = 'Invoice Data';
    }
  } catch (err) {
    // Non-critical — event-code KPIs remain in place
    console.warn('Invoice KPI fetch failed:', err);
  }
}

/** Show the icon + line color legend above the table. */
function showMicroChartLegend() {
  const legend = document.getElementById('es-microchart-legend');
  if (!legend) return;
  legend.innerHTML = '';

  // Payment icons
  const iconEntries = [
    [svgBuilding, STATUS_COLORS.large, 'Large Pay'],
    [svgHouse,    STATUS_COLORS.small, 'Small Pay'],
    [svgPerson,   STATUS_COLORS.micro, 'Micro Pay'],
  ];
  for (const [fn, color, label] of iconEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-icon" style="color:${color}">${fn()}</span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  // Status line swatches
  const lineEntries = [
    [STATUS_COLORS.large, 'Large Status'],
    [STATUS_COLORS.small, 'Small Status'],
    [STATUS_COLORS.micro, 'Micro Status'],
  ];
  for (const [color, label] of lineEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-line" style="background:${color}"></span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  // Expired icon (gray)
  const expItem = document.createElement('span');
  expItem.className = 'es-microchart-legend-item';
  expItem.innerHTML = `<span class="es-microchart-legend-icon" style="color:${GRAY}">${svgExpired()}</span>Expired`;
  legend.appendChild(expItem);

  // Status change dot (yellow)
  const transItem = document.createElement('span');
  transItem.className = 'es-microchart-legend-item';
  transItem.innerHTML = `<span class="es-microchart-legend-dot" style="background:#eab308;width:9px;height:9px"></span>Status Change`;
  legend.appendChild(transItem);

  // Small dot entries (grant + reminder + attorney)
  const dotEntries = [
    ['#8b5cf6', 'Grant Date'],
    ['#eab308', 'Reminder'],
    ['#92400e', 'Attorney'],
  ];
  for (const [color, label] of dotEntries) {
    const item = document.createElement('span');
    item.className = 'es-microchart-legend-item';
    item.innerHTML = `<span class="es-microchart-legend-dot" style="background:${color}"></span>${escHtml(label)}`;
    legend.appendChild(item);
  }

  // Litigation star
  const litItem = document.createElement('span');
  litItem.className = 'es-microchart-legend-item';
  litItem.innerHTML = `<span class="es-microchart-legend-icon" style="color:#d4a017">${svgStar()}</span>Litigation`;
  legend.appendChild(litItem);

  // Prosecution payment $ icons (shown when prosecution data is loaded)
  if (window._prosecutionData) {
    const prosEntries = [
      [STATUS_COLORS.small, 'Small $ Pay'],
      [STATUS_COLORS.micro, 'Micro $ Pay'],
      [STATUS_COLORS.large, 'Large $ Pay'],
    ];
    for (const [color, label] of prosEntries) {
      const item = document.createElement('span');
      item.className = 'es-microchart-legend-item';
      item.innerHTML = `<span class="es-microchart-legend-icon" style="color:${color}">${svgDollar()}</span>${escHtml(label)}`;
      legend.appendChild(item);
    }
  }

  legend.classList.remove('hidden');
}

// ── Invoice Popup ────────────────────────────────────────────────

let _invoicePopup = null;
let _invoiceDrag = null;

function getOrCreateInvoicePopup() {
  if (_invoicePopup) return _invoicePopup;
  const el = document.createElement('div');
  el.id = 'es-invoice-popup';
  el.className = 'es-invoice-popup hidden';
  document.body.appendChild(el);
  _invoicePopup = el;

  // Drag support on header
  el.addEventListener('mousedown', e => {
    const hdr = e.target.closest('.es-invoice-header');
    if (!hdr || e.target.closest('.es-invoice-close')) return;
    e.preventDefault();
    _invoiceDrag = {
      startX: e.clientX, startY: e.clientY,
      origLeft: el.offsetLeft, origTop: el.offsetTop,
    };
  });
  document.addEventListener('mousemove', e => {
    if (!_invoiceDrag) return;
    e.preventDefault();
    el.style.left = `${_invoiceDrag.origLeft + (e.clientX - _invoiceDrag.startX)}px`;
    el.style.top  = `${_invoiceDrag.origTop  + (e.clientY - _invoiceDrag.startY)}px`;
  });
  document.addEventListener('mouseup', () => { _invoiceDrag = null; });

  return el;
}

function closeInvoicePopup() {
  const popup = getOrCreateInvoicePopup();
  popup.classList.add('hidden');
}

async function showInvoicePopup(applicationNumber) {
  if (!applicationNumber) return;
  const popup = getOrCreateInvoicePopup();

  // Position popup in center of viewport
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const w = Math.min(720, vpW - 40);
  const h = Math.min(520, vpH - 40);
  popup.style.width  = `${w}px`;
  popup.style.height = `${h}px`;
  popup.style.left   = `${(vpW - w) / 2 + window.scrollX}px`;
  popup.style.top    = `${(vpH - h) / 2 + window.scrollY}px`;

  popup.innerHTML = `
    <div class="es-invoice-header">
      <span>Payment Invoices \u2014 ${escHtml(applicationNumber)}</span>
      <button class="es-invoice-close" title="Close">\u00D7</button>
    </div>
    <div class="es-invoice-body">
      <p class="text-muted">Loading documents\u2026</p>
    </div>`;
  popup.classList.remove('hidden');
  popup.querySelector('.es-invoice-close').addEventListener('click', closeInvoicePopup);

  try {
    const data = await apiGet(`/api/prosecution/invoice-docs?application_number=${encodeURIComponent(applicationNumber)}`);
    const docs = data.docs || [];

    const body = popup.querySelector('.es-invoice-body');
    if (docs.length === 0) {
      body.innerHTML = '<p class="text-muted">No payment documents found for this application.</p>';
      return;
    }

    let rows = '';
    for (const doc of docs) {
      const cached = doc.cached ? '<span class="es-invoice-cached" title="Already downloaded">&#x2713;</span>' : '';
      rows += `<tr>
        <td>${escHtml(doc.mail_date || '')}</td>
        <td>${escHtml(doc.doc_code || '')}</td>
        <td>${escHtml(doc.description || '')}</td>
        <td style="text-align:center">${doc.page_count || ''}</td>
        <td style="text-align:center">${cached}</td>
        <td style="text-align:center">
          <button class="es-invoice-view-btn"
                  data-app="${escHtml(applicationNumber)}"
                  data-url="${escHtml(doc.download_url || '')}"
                  data-filename="${escHtml(doc.filename || '')}"
                  data-cached-path="${escHtml(doc.cached_gcs_path || '')}"
                  title="View PDF">View</button>
        </td>
      </tr>`;
    }

    body.innerHTML = `
      <p class="text-muted" style="margin:0 0 .5rem">${docs.length} payment document(s) found</p>
      <div class="es-invoice-table-wrap">
        <table class="data-table es-invoice-doc-table">
          <thead><tr>
            <th>Date</th><th>Code</th><th>Description</th>
            <th style="text-align:center">Pages</th>
            <th style="text-align:center">Cached</th>
            <th style="text-align:center">Action</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    // Wire up View PDF buttons — endpoint streams PDF directly
    body.querySelectorAll('.es-invoice-view-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const params = new URLSearchParams({
          application_number: btn.dataset.app,
          download_url: btn.dataset.url,
          filename: btn.dataset.filename,
        });
        if (btn.dataset.cachedPath) {
          params.set('cached_gcs_path', btn.dataset.cachedPath);
        }
        window.open(`/api/prosecution/invoice-pdf?${params}`, '_blank');
        // Mark as cached after viewing
        const cachedCell = btn.closest('tr').querySelector('td:nth-child(5)');
        if (cachedCell && !cachedCell.querySelector('.es-invoice-cached')) {
          cachedCell.innerHTML = '<span class="es-invoice-cached" title="Already downloaded">&#x2713;</span>';
        }
      });
    });
  } catch (err) {
    const body = popup.querySelector('.es-invoice-body');
    body.innerHTML = `<p style="color:var(--color-danger)">Error loading documents: ${escHtml(err.message)}</p>`;
  }
}

// ── Helpers ──────────────────────────────────────────────────────

function statusBadge(status) {
  if (!status) return '<span class="text-muted">N/A</span>';
  const s = status.toUpperCase();
  const cls = s === 'SMALL' ? 'es-status-small'
    : s === 'MICRO' ? 'es-status-micro'
    : s === 'LARGE' ? 'es-status-large'
    : 'es-status-other';
  return `<span class="es-status-badge ${cls}">${escHtml(status)}</span>`;
}
