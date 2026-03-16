"""Patent importance scoring via Gemini AI analysis of SEC 10-K filings."""

import json
import logging
import vertexai
from vertexai.generative_models import GenerativeModel

log = logging.getLogger(__name__)

_MODEL_NAME = "gemini-2.5-flash"
_model = None


def _get_model() -> GenerativeModel:
    """Lazy-init Vertex AI and return the model."""
    global _model
    if _model is None:
        vertexai.init(project="uspto-data-app", location="us-central1")
        _model = GenerativeModel(_MODEL_NAME)
    return _model


SCORING_PROMPT = """You are analyzing a SEC 10-K annual report for {company_name} to determine how importantly \
the company perceives its own patent portfolio.

Score the company's patent-importance perception on a 1-10 integer scale:
1-2 = Patents barely mentioned; company does not rely on them
3-4 = Patents mentioned as routine legal protection only
5-6 = Patents acknowledged as meaningful competitive assets
7-8 = Patents described as significant strategic drivers / competitive advantage
9-10 = Patents are central to the company's identity; business described as inseparable from IP

IMPORTANT DISTINCTIONS:
- assertion_signals = company actively licensing/enforcing its OWN patents (plaintiff/licensor). This IS a strong positive signal.
- defendant_signals = company being sued by others for patent infringement. This is NOT a positive signal — do not inflate the score for this.

Return ONLY valid JSON with this structure:
{{
  "score": <integer 1-10>,
  "rationale": "<2-3 sentences citing specific language from the filing>",
  "key_excerpts": [
    {{"type": "<high_importance|patent_assertion|revenue_connection|risk_factor|quantitative|general_mention>",
     "text": "<verbatim excerpt up to 300 chars>"}}
  ],
  "stats": {{
    "total_patent_mentions": <int>,
    "has_dedicated_ip_section": <bool>,
    "assertion_signals": <int>,
    "defendant_signals": <int>,
    "revenue_connections": <int>,
    "risk_factor_mentions": <int>,
    "quantitative_references": <int>
  }}
}}

Here is the filing text to analyze:

{filing_text}
"""


def score_company(company_name: str, sections: dict) -> dict:
    """Score a company's patent importance from 10-K sections.

    Args:
        company_name: Normalized company name
        sections: Dict from extract_sections() with item1/item1a/item7 or full_text

    Returns dict with: score, rationale, gist, key_excerpts, stats,
                       key_excerpts_json, stats_json
    """
    # Build filing text from available sections
    parts = []
    if "item1" in sections:
        parts.append("=== ITEM 1 — BUSINESS ===\n" + sections["item1"])
    if "item1a" in sections:
        parts.append("=== ITEM 1A — RISK FACTORS ===\n" + sections["item1a"])
    if "item7" in sections:
        parts.append("=== ITEM 7 — MD&A ===\n" + sections["item7"])
    if "full_text" in sections:
        parts.append(sections["full_text"])
    filing_text = "\n\n".join(parts)

    prompt = SCORING_PROMPT.format(
        company_name=company_name,
        filing_text=filing_text,
    )

    try:
        model = _get_model()
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.1, "max_output_tokens": 4096},
        )
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        parsed = json.loads(raw)
        score = max(1, min(10, int(parsed.get("score", 1))))
        rationale = parsed.get("rationale", "")
        key_excerpts = parsed.get("key_excerpts", [])
        stats = parsed.get("stats", {})

        # Generate gist: 1-2 sentence summary for table display
        gist = rationale
        if len(gist) > 300:
            gist = gist[:297] + "..."

        return {
            "score": score,
            "rationale": rationale,
            "gist": gist,
            "key_excerpts": key_excerpts,
            "stats": stats,
            "key_excerpts_json": json.dumps(key_excerpts),
            "stats_json": json.dumps(stats),
        }

    except Exception as e:
        log.error("Scoring failed for %s: %s", company_name, e)
        return {
            "score": 1,
            "rationale": f"Scoring error: {e}",
            "gist": f"Scoring error: {e}",
            "key_excerpts": [],
            "stats": {},
            "key_excerpts_json": "[]",
            "stats_json": "{}",
        }
