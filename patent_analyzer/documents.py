"""Generate executive summary memos and Caremark fiduciary duty letters via Gemini."""

import json
import logging
import vertexai
from vertexai.generative_models import GenerativeModel

log = logging.getLogger(__name__)

_MODEL_NAME = "gemini-2.5-flash"
_model = None


def _get_model() -> GenerativeModel:
    global _model
    if _model is None:
        vertexai.init(project="uspto-data-app", location="us-central1")
        _model = GenerativeModel(_MODEL_NAME)
    return _model


# ── Score descriptor (qualitative, never numeric in output) ─────────

def _score_descriptor(score: int) -> str:
    """Convert numeric score to qualitative descriptor for letter use."""
    if score >= 9:
        return "exceptionally high patent importance — patents are central to the company's identity"
    if score >= 7:
        return "high patent importance — patents described as significant strategic drivers"
    if score >= 5:
        return "meaningful patent importance — patents acknowledged as competitive assets"
    if score >= 3:
        return "moderate patent importance — patents mentioned as routine legal protection"
    return "low patent importance — patents barely mentioned"


# ── Memo generation ─────────────────────────────────────────────────

MEMO_PROMPT = """\
Write a professional patent importance assessment memo for {company_name} based on their \
{form_type} filing (filed {filing_date}).

Score: {score}/10
Rationale: {rationale}
Stats: {stats_summary}

Instructions:
- Write in polished business prose — no bullet points, no markdown
- Use these all-caps section headers on their own lines:
  PATENT IMPORTANCE ASSESSMENT, CONCLUSION, SUPPORTING EXCERPTS FROM THE FILING
- Start with a header block: Company / Filing / Analyzed date
- Write 4-6 substantive paragraphs covering what the score means, key signals found \
(only mention signals that actually apply: assertion activity, revenue links, \
risk disclosures, dedicated IP section), and a strong conclusion
- Do NOT mention the numerical score in the body text — describe it qualitatively
- End with the supporting excerpts verbatim (include them exactly as provided below)
- Tone: authoritative, analytical, like a senior IP analyst wrote it

Supporting excerpts to include verbatim at the end:
{formatted_excerpts}
"""


def generate_memo(
    company_name: str,
    form_type: str,
    filing_date: str,
    score: int,
    rationale: str,
    stats: dict,
    key_excerpts: list,
) -> str:
    """Generate an internal executive summary memo.

    Returns memo text (plain text, no markdown).
    """
    # Build stats summary
    stats_parts = []
    if stats.get("total_patent_mentions"):
        stats_parts.append(f"Total patent mentions: {stats['total_patent_mentions']}")
    if stats.get("has_dedicated_ip_section"):
        stats_parts.append("Has dedicated IP section")
    if stats.get("assertion_signals"):
        stats_parts.append(f"Assertion signals: {stats['assertion_signals']}")
    if stats.get("revenue_connections"):
        stats_parts.append(f"Revenue connections: {stats['revenue_connections']}")
    if stats.get("risk_factor_mentions"):
        stats_parts.append(f"Risk factor mentions: {stats['risk_factor_mentions']}")
    if stats.get("quantitative_references"):
        stats_parts.append(f"Quantitative references: {stats['quantitative_references']}")
    stats_summary = "; ".join(stats_parts) if stats_parts else "No detailed stats available"

    # Format excerpts
    formatted = []
    for i, exc in enumerate(key_excerpts[:6], 1):
        exc_type = exc.get("type", "general_mention")
        exc_text = exc.get("text", "")
        formatted.append(f'{i}. [{exc_type}] "{exc_text}"')
    formatted_excerpts = "\n".join(formatted) if formatted else "No excerpts available."

    prompt = MEMO_PROMPT.format(
        company_name=company_name,
        form_type=form_type,
        filing_date=filing_date,
        score=score,
        rationale=rationale,
        stats_summary=stats_summary,
        formatted_excerpts=formatted_excerpts,
    )

    try:
        model = _get_model()
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 4096},
        )
        return response.text.strip()
    except Exception as e:
        log.error("Memo generation failed for %s: %s", company_name, e)
        return f"[Memo generation error: {e}]"


# ── Caremark fiduciary duty letter generation ───────────────────────

LETTER_PROMPT = """\
Write a formal Caremark fiduciary duty letter on behalf of a patent portfolio advisory firm, \
addressed to the board of {company_name}.

Date: {date}
Address block: {addressee_name}, {addressee_title} / Board of Directors / {company_name}
Salutation: Dear {salutation_name},

Patent importance finding: {score_descriptor}
Analysis rationale: {rationale}
Key filing excerpts to reference naturally: {top_excerpts}

Letter structure (use these exact all-caps section headers):
1. Opening paragraph introducing the fiduciary significance
{secretary_note}
3. Section: YOUR PATENTS ARE A CRITICAL CORPORATE ASSET
   — Reference specific language from the company's own 10-K excerpts provided above
   — Explain what their own disclosures reveal about patent importance
4. Section: THE CAREMARK DUTY APPLIES TO PATENT PORTFOLIO OVERSIGHT
   — Cite: In re Caremark International Inc. Derivative Litigation (698 A.2d 959, Del. Ch. 1996)
   — Cite: Stone v. Ritter (911 A.2d 362, Del. 2006)
   — Three numbered failure modes exposing directors to personal liability:
     (1) Portfolio loses enforceability due to missed maintenance fees or prosecution deadlines
     (2) Patents found unenforceable in litigation due to issues a systematic review would have caught
     (3) Board has no reporting mechanism to receive structured information about portfolio health
   — Make this section feel tailored to THIS company's specific patent situation
5. Section: OUR QUARTERLY EXECUTIVE PATENT PORTFOLIO REPORTS
   — Four bullet deliverables: enforceable condition assessment; prioritized issues register \
(flagging claim construction risks, prosecution history problems, inequitable conduct exposure, \
statutory compliance gaps); executive summary for board-level consumption; trend analysis
   — Position as the Caremark-compliant oversight instrument the board needs
   — "The only system purpose-built to enable boards to discharge Caremark duties re: patent portfolio"
6. Closing: invite board presentation, offer sample report
7. Signature block: [Your Name] / [Your Title] / [Company Name] / [Contact Information]
8. cc: line with remaining director names: {cc_names}

Rules:
- No markdown, no score numbers, no ticker codes in the letter body
- Company name in proper title case (not ALL CAPS)
- Authoritative, collegial, sophisticated legal-business prose
- Do NOT use horizontal lines or dividers
- The letter should feel genuinely custom, not templated
"""


def _determine_addressee(officers: dict) -> tuple:
    """Pick addressee and build cc list from extracted officers.

    Returns (addressee_name, addressee_title, salutation, secretary_note, cc_names).
    Priority: secretary > general_counsel > board_chair > fallback
    """
    cc_directors = [d["name"] for d in officers.get("directors", []) if d]

    # Try corporate secretary first
    sec = officers.get("secretary")
    if sec and sec.get("name"):
        name = sec["name"]
        title = sec.get("title", "Corporate Secretary")
        # Remove secretary from cc if present
        cc_directors = [d for d in cc_directors if d != name]
        secretary_note = (
            "2. One sentence noting the Corporate Secretary's specific responsibility "
            "for ensuring the Board receives information needed for oversight"
        )
        return name, title, name, secretary_note, ", ".join(cc_directors)

    # Try general counsel
    gc = officers.get("general_counsel")
    if gc and gc.get("name"):
        name = gc["name"]
        title = gc.get("title", "General Counsel")
        cc_directors = [d for d in cc_directors if d != name]
        return name, title, name, "", ", ".join(cc_directors)

    # Try board chair
    chair = officers.get("board_chair")
    if chair and chair.get("name"):
        name = chair["name"]
        title = chair.get("title", "Chairman of the Board")
        cc_directors = [d for d in cc_directors if d != name]
        return name, title, name, "", ", ".join(cc_directors)

    # Fallback
    return (
        "Members of the Board",
        "Board of Directors",
        "Members of the Board",
        "",
        ", ".join(cc_directors),
    )


def generate_letter(
    company_name: str,
    date: str,
    officers: dict,
    score: int,
    rationale: str,
    key_excerpts: list,
) -> str:
    """Generate a Caremark fiduciary duty letter.

    Returns letter text (plain text, no markdown).
    """
    addressee_name, addressee_title, salutation, secretary_note, cc_names = (
        _determine_addressee(officers)
    )

    # Top 3 excerpts for the letter
    top_excerpts_parts = []
    for exc in key_excerpts[:3]:
        top_excerpts_parts.append(f'"{exc.get("text", "")}"')
    top_excerpts = "\n".join(top_excerpts_parts) if top_excerpts_parts else "N/A"

    prompt = LETTER_PROMPT.format(
        company_name=company_name,
        date=date,
        addressee_name=addressee_name,
        addressee_title=addressee_title,
        salutation_name=salutation,
        score_descriptor=_score_descriptor(score),
        rationale=rationale,
        top_excerpts=top_excerpts,
        secretary_note=secretary_note,
        cc_names=cc_names if cc_names else "N/A",
    )

    try:
        model = _get_model()
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.3, "max_output_tokens": 8192},
        )
        return response.text.strip()
    except Exception as e:
        log.error("Letter generation failed for %s: %s", company_name, e)
        return f"[Letter generation error: {e}]"
