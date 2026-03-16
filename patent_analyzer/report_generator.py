"""Generate self-contained HTML report for SEC Leads analysis results."""

import json
import logging
from datetime import datetime
from google.cloud import storage

log = logging.getLogger(__name__)

GCS_BUCKET = "uspto-bulk-staging"
GCS_PREFIX = "sec-leads-reports"

# Score badge colors
_SCORE_COLORS = {
    10: "#c0392b", 9: "#c0392b",
    8: "#e74c3c",
    7: "#e67e22",
    6: "#f39c12",
    5: "#95a5a6",
}


def _score_color(score: int) -> str:
    return _SCORE_COLORS.get(score, "#bdc3c7")


def _esc(text) -> str:
    """HTML-escape a string."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_report(
    results: list[dict],
    analysis_date: str,
    total_analyzed: int,
) -> str:
    """Generate self-contained HTML report.

    Only includes companies scoring 5+, sorted by score descending.
    """
    # Filter and sort
    qualified = [r for r in results if r.get("score", 0) >= 5]
    qualified.sort(key=lambda r: (-r.get("score", 0), r.get("company_name", "")))
    score7_count = sum(1 for r in qualified if r.get("score", 0) >= 7)

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    # Build table rows
    rows_html = []
    for r in qualified:
        score = r.get("score", 0)
        color = _score_color(score)

        # Determine primary contact
        contact_name = ""
        contact_title = ""
        if r.get("secretary_name"):
            contact_name = r["secretary_name"]
            contact_title = r.get("secretary_title", "Corporate Secretary")
        elif r.get("general_counsel_name"):
            contact_name = r["general_counsel_name"]
            contact_title = r.get("general_counsel_title", "General Counsel")
        elif r.get("board_chair_name"):
            contact_name = r["board_chair_name"]
            contact_title = r.get("board_chair_title", "Board Chair")

        # Board member count
        board_count = 0
        try:
            board = json.loads(r.get("board_members_json", "[]") or "[]")
            board_count = len(board)
        except (json.JSONDecodeError, TypeError):
            pass

        board_info = f"<br><span class='board-count'>{board_count} board members</span>" if board_count else ""

        ticker = _esc(r.get("ticker", ""))
        company = _esc(r.get("company_name", ""))
        filing_url = _esc(r.get("filing_url", ""))
        gist = _esc(r.get("gist", ""))

        memo_id = f"memo-{ticker}"
        letter_id = f"letter-{ticker}"

        rows_html.append(f"""
        <tr style="border-left: 4px solid {color};">
          <td>{_esc(analysis_date)}</td>
          <td>{_esc(r.get('filing_date', ''))}</td>
          <td><strong>{company}</strong><br><span class="ticker">{ticker}</span></td>
          <td><a href="{filing_url}" target="_blank" rel="noopener">View 10-K on SEC</a></td>
          <td><span class="score-badge" style="background:{color};">{score}</span></td>
          <td class="gist-cell">{gist}</td>
          <td><strong>{_esc(contact_name)}</strong><br>{_esc(contact_title)}{board_info}</td>
          <td><button class="btn-memo" onclick="showDoc('{memo_id}')">Memo</button></td>
          <td><button class="btn-letter" onclick="showDoc('{letter_id}')">Letter</button></td>
        </tr>
        """)

    # Build hidden modals for memos and letters
    modals_html = []
    for r in qualified:
        ticker = _esc(r.get("ticker", ""))
        company = _esc(r.get("company_name", ""))
        memo = _esc(r.get("memo_text", ""))
        letter = _esc(r.get("letter_text", ""))

        modals_html.append(f"""
        <div id="memo-{ticker}" class="doc-modal" style="display:none;">
          <div class="doc-modal-overlay" onclick="hideDoc('memo-{ticker}')"></div>
          <div class="doc-modal-content">
            <div class="doc-modal-header">
              <h3>Memo — {company}</h3>
              <button onclick="copyDoc('memo-{ticker}')">Copy to Clipboard</button>
              <button onclick="hideDoc('memo-{ticker}')">Close</button>
            </div>
            <pre class="doc-text">{memo}</pre>
          </div>
        </div>
        <div id="letter-{ticker}" class="doc-modal" style="display:none;">
          <div class="doc-modal-overlay" onclick="hideDoc('letter-{ticker}')"></div>
          <div class="doc-modal-content">
            <div class="doc-modal-header">
              <h3>Letter — {company}</h3>
              <button onclick="copyDoc('letter-{ticker}')">Copy to Clipboard</button>
              <button onclick="hideDoc('letter-{ticker}')">Close</button>
            </div>
            <pre class="doc-text">{letter}</pre>
          </div>
        </div>
        """)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SEC 10-K Patent Importance Analysis — {analysis_date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #f4f6f9; color: #111827; }}
.header {{ background: linear-gradient(135deg, #0c2461, #1e3799); color: #fff; padding: 2rem 3rem; }}
.header h1 {{ font-size: 1.75rem; margin-bottom: .5rem; }}
.header p {{ opacity: .85; font-size: .95rem; }}
.stats-bar {{ display: flex; gap: 2rem; padding: 1rem 3rem; background: #fff; border-bottom: 1px solid #dde2ea; align-items: center; flex-wrap: wrap; }}
.stat {{ font-size: .9rem; color: #6b7280; }}
.stat strong {{ color: #111827; font-size: 1.1rem; }}
.content {{ padding: 1.5rem 3rem; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
thead {{ background: #1e2a45; color: #fff; }}
th {{ padding: .75rem 1rem; text-align: left; font-weight: 600; font-size: .8rem; text-transform: uppercase; letter-spacing: .5px; }}
td {{ padding: .75rem 1rem; border-bottom: 1px solid #eee; vertical-align: top; font-size: .9rem; }}
tr:hover {{ background: #f8f9fa; }}
.ticker {{ color: #6b7280; font-size: .85rem; font-weight: 600; }}
.board-count {{ color: #6b7280; font-size: .8rem; }}
.score-badge {{ display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; font-weight: 700; font-size: .9rem; color: #fff; }}
.gist-cell {{ max-width: 400px; line-height: 1.5; }}
a {{ color: #1a56db; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.btn-memo {{ background: #2563eb; color: #fff; border: none; padding: .4rem .8rem; border-radius: 6px; cursor: pointer; font-size: .85rem; font-weight: 500; }}
.btn-memo:hover {{ background: #1d4ed8; }}
.btn-letter {{ background: #7c3aed; color: #fff; border: none; padding: .4rem .8rem; border-radius: 6px; cursor: pointer; font-size: .85rem; font-weight: 500; }}
.btn-letter:hover {{ background: #6d28d9; }}
.methodology {{ margin-top: 2rem; padding: 1.25rem; background: #f8f9fa; border-radius: 8px; border-left: 4px solid #1a56db; }}
.methodology strong {{ display: block; margin-bottom: .5rem; }}
.methodology p {{ font-size: .85rem; color: #6b7280; line-height: 1.6; }}
.footer {{ text-align: center; padding: 2rem; color: #9ca3af; font-size: .8rem; }}
/* Modal styles */
.doc-modal {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 1000; }}
.doc-modal-overlay {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,.5); }}
.doc-modal-content {{ position: relative; margin: 3vh auto; width: 90%; max-width: 900px; max-height: 90vh; background: #fff; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; }}
.doc-modal-header {{ display: flex; justify-content: space-between; align-items: center; padding: 1rem 1.5rem; background: #f8f9fa; border-bottom: 1px solid #dde2ea; gap: .5rem; }}
.doc-modal-header h3 {{ flex: 1; font-size: 1.1rem; }}
.doc-modal-header button {{ padding: .4rem .8rem; border: 1px solid #dde2ea; border-radius: 6px; background: #fff; cursor: pointer; font-size: .85rem; }}
.doc-modal-header button:hover {{ background: #e5e7eb; }}
.doc-text {{ padding: 1.5rem; overflow-y: auto; white-space: pre-wrap; font-family: 'Segoe UI', system-ui, sans-serif; font-size: .9rem; line-height: 1.6; flex: 1; }}
</style>
</head>
<body>

<div class="header">
  <h1>SEC 10-K Patent Importance Analysis</h1>
  <p>Automated assessment of how critically companies perceive their patent portfolios</p>
</div>

<div class="stats-bar">
  <span class="stat">Filings Analyzed: <strong>{total_analyzed}</strong></span>
  <span class="stat">Companies Scoring 5+: <strong>{len(qualified)}</strong></span>
  <span class="stat">Companies Scoring 7+: <strong>{score7_count}</strong></span>
  <span class="stat">Report Generated: <strong>{now_str}</strong></span>
</div>

<div class="content">
  <table>
    <thead>
      <tr>
        <th>Analyzed</th>
        <th>Filed</th>
        <th>Company</th>
        <th>10-K Filing</th>
        <th>Score</th>
        <th>Gist</th>
        <th>Secretary / Counsel</th>
        <th>Memo</th>
        <th>Letter</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html) if rows_html else '<tr><td colspan="9" style="text-align:center;color:#9ca3af;padding:2rem;">No companies scored 5 or higher on this date.</td></tr>'}
    </tbody>
  </table>

  <div class="methodology">
    <strong>Scoring Methodology</strong>
    <p>Each 10-K filing is scored 1-10 based on: patent term frequency and density, strategic importance language, \
whether the company asserts its own patents (vs. being asserted against), connection between patents and revenue/strategy, \
patent risk disclosures, quantitative portfolio references, and presence of dedicated IP sections. \
Companies scoring 5+ are displayed. A penalty is applied when patent litigation references are primarily about \
the company being a defendant rather than asserting its own patents.</p>
  </div>
</div>

{''.join(modals_html)}

<div class="footer">
  Patent Portfolio Importance Analyzer — Automated SEC 10-K Analysis
</div>

<script>
function showDoc(id) {{
  document.getElementById(id).style.display = 'block';
}}
function hideDoc(id) {{
  document.getElementById(id).style.display = 'none';
}}
function copyDoc(id) {{
  const el = document.getElementById(id);
  const text = el.querySelector('.doc-text').textContent;
  navigator.clipboard.writeText(text).then(() => {{
    const btn = el.querySelector('.doc-modal-header button');
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = orig, 2000);
  }});
}}
</script>

</body>
</html>"""

    return html


def upload_report(html_content: str, date_str: str) -> str:
    """Upload HTML report to GCS.

    Saves as report_{date}.html and overwrites report_latest.html.
    Returns the GCS path of the dated report.
    """
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)

    dated_path = f"{GCS_PREFIX}/report_{date_str}.html"
    latest_path = f"{GCS_PREFIX}/report_latest.html"

    # Upload dated report
    blob = bucket.blob(dated_path)
    blob.upload_from_string(html_content, content_type="text/html")
    log.info("Uploaded report to gs://%s/%s", GCS_BUCKET, dated_path)

    # Overwrite latest
    blob_latest = bucket.blob(latest_path)
    blob_latest.upload_from_string(html_content, content_type="text/html")
    log.info("Updated gs://%s/%s", GCS_BUCKET, latest_path)

    return f"gs://{GCS_BUCKET}/{dated_path}"
