"""Board member and officer extraction via Gemini with function calling.

Uses an agentic loop: Gemini can call fetch_document() to read proxy
statements when the 10-K incorporates Part III by reference.
"""

import json
import re
import time
import logging
import requests
import vertexai
from vertexai.generative_models import (
    FunctionDeclaration,
    GenerativeModel,
    Tool,
    Content,
    Part,
)

from patent_analyzer.sec_edgar import (
    HEADERS,
    RATE_LIMIT,
    fetch_filing_text,
    get_filings_by_type,
)

log = logging.getLogger(__name__)

_MODEL_NAME = "gemini-2.5-flash"
_model = None


def _get_model() -> GenerativeModel:
    global _model
    if _model is None:
        vertexai.init(project="uspto-data-app", location="us-central1")
        _model = GenerativeModel(
            _MODEL_NAME,
            system_instruction=SYSTEM_PROMPT,
        )
    return _model


# ── Function declarations for Gemini tool use ───────────────────────

_fetch_document_decl = FunctionDeclaration(
    name="fetch_document",
    description=(
        "Fetch and read a document from a URL (SEC EDGAR filing, "
        "proxy statement). Returns the document text content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
        },
        "required": ["url"],
    },
)

_get_sec_filings_decl = FunctionDeclaration(
    name="get_sec_filings",
    description=(
        "Get recent SEC filings of a specific form type for a company CIK. "
        "Returns a JSON list of filing URLs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cik": {"type": "string", "description": "The company CIK number"},
            "form_type": {
                "type": "string",
                "description": "e.g. 'DEF 14A', '10-K'",
            },
        },
        "required": ["cik", "form_type"],
    },
)

_tools = [Tool(function_declarations=[_fetch_document_decl, _get_sec_filings_decl])]


SYSTEM_PROMPT = """\
You are an expert at extracting corporate officer and board member information from SEC filings.

Extract: Corporate Secretary, General Counsel, Board Chair, CEO, CFO, and all directors.

Rules:
1. Extract REAL person names only — not company names, job titles, or placeholder text
2. Valid person names have at least 2 words, start with uppercase, contain no title words
3. Extract /s/ NAME patterns from signature blocks and match with their title lines
4. If the 10-K says Part III is incorporated by reference from the proxy statement, \
use get_sec_filings to find the DEF 14A, then fetch_document to read it
5. Return final answer as JSON only — no prose

JSON format:
{
  "secretary": {"name": "...", "title": "..."} or null,
  "general_counsel": {"name": "...", "title": "..."} or null,
  "board_chair": {"name": "...", "title": "..."} or null,
  "ceo": {"name": "...", "title": "..."} or null,
  "cfo": {"name": "...", "title": "..."} or null,
  "directors": [{"name": "...", "title": "..."}]
}
"""


def _extract_context_sections(filing_text: str, cik: str) -> str:
    """Extract relevant sections from the 10-K for board/officer extraction."""
    parts = []

    # Signature block (last occurrence of "Pursuant to the requirements...")
    sig_matches = list(re.finditer(
        r"(?i)Pursuant to the requirements of the Securities Exchange Act",
        filing_text,
    ))
    if sig_matches:
        start = sig_matches[-1].start()
        parts.append("=== SIGNATURE BLOCK ===\n" + filing_text[start : start + 6000])

    # Executive officers section
    exec_match = re.search(
        r"(?i)(EXECUTIVE\s+OFFICERS|Executive Officers of the Registrant)",
        filing_text,
    )
    if exec_match:
        start = exec_match.start()
        parts.append(
            "=== EXECUTIVE OFFICERS ===\n" + filing_text[start : start + 8000]
        )

    # Directors section
    dir_match = re.search(r"(?i)(?:^|\n)\s*DIRECTORS", filing_text)
    if dir_match:
        start = dir_match.start()
        parts.append("=== DIRECTORS ===\n" + filing_text[start : start + 6000])

    # Check for incorporation by reference
    inc_by_ref = False
    for pattern in [
        r"(?i)incorporat\w+ (?:herein )?by reference.*?(?:proxy|Part\s+III)",
        r"(?i)(?:proxy|Part\s+III).*?incorporat\w+ (?:herein )?by reference",
    ]:
        if re.search(pattern, filing_text[:50000]):
            inc_by_ref = True
            break

    if inc_by_ref:
        parts.append(
            "\n*** NOTE: This 10-K incorporates Part III by reference from "
            f"the company's proxy statement. CIK: {cik}. "
            "Use get_sec_filings to find the DEF 14A and fetch_document to read it. ***"
        )

    if not parts:
        # Fallback: send last 10K chars which often contain signatures
        parts.append("=== END OF FILING ===\n" + filing_text[-10000:])

    return "\n\n".join(parts)


def _execute_tool_call(func_name: str, func_args: dict) -> str:
    """Execute a Gemini function call and return the result as text."""
    if func_name == "fetch_document":
        url = func_args.get("url", "")
        log.info("Agent fetching document: %s", url[:100])
        try:
            text = fetch_filing_text(url, max_chars=50_000)
            return text
        except Exception as e:
            return f"Error fetching document: {e}"

    elif func_name == "get_sec_filings":
        cik = func_args.get("cik", "")
        form_type = func_args.get("form_type", "")
        log.info("Agent looking up %s filings for CIK %s", form_type, cik)
        try:
            filings = get_filings_by_type(cik, form_type, limit=3)
            return json.dumps(filings)
        except Exception as e:
            return f"Error getting filings: {e}"

    return f"Unknown function: {func_name}"


def extract_officers_and_board(
    filing_text: str, cik: str, company_name: str
) -> dict:
    """Extract corporate officers and board members using Gemini agent.

    Returns dict with: secretary, general_counsel, board_chair, ceo, cfo, directors[]
    Each person is {name, title} or null.
    """
    context = _extract_context_sections(filing_text, cik)

    model = _get_model()
    user_msg = (
        f"Extract officers and board members for {company_name} (CIK: {cik}).\n\n"
        f"{context}"
    )

    # Build initial content
    history = [Content(role="user", parts=[Part.from_text(user_msg)])]

    empty_result = {
        "secretary": None,
        "general_counsel": None,
        "board_chair": None,
        "ceo": None,
        "cfo": None,
        "directors": [],
    }

    try:
        # Agentic loop — up to 6 iterations
        for iteration in range(6):
            response = model.generate_content(
                history,
                tools=_tools,
                generation_config={"temperature": 0.1, "max_output_tokens": 4096},
            )

            candidate = response.candidates[0]

            # Check for function calls
            function_calls = []
            for part in candidate.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    function_calls.append(part.function_call)

            if not function_calls:
                # No more tool calls — parse final response
                text = candidate.content.parts[0].text if candidate.content.parts else ""
                return _parse_board_json(text, empty_result)

            # Execute each function call and build tool response
            history.append(candidate.content)

            tool_parts = []
            for fc in function_calls:
                result_text = _execute_tool_call(fc.name, dict(fc.args))
                tool_parts.append(
                    Part.from_function_response(
                        name=fc.name,
                        response={"result": result_text},
                    )
                )
            history.append(Content(role="user", parts=tool_parts))

            log.info(
                "Board extraction iteration %d: %d tool calls executed",
                iteration + 1,
                len(function_calls),
            )

        # Max iterations reached — try to parse whatever we have
        log.warning("Board extraction hit max iterations for %s", company_name)
        return empty_result

    except Exception as e:
        log.error("Board extraction failed for %s: %s", company_name, e)
        return empty_result


def _parse_board_json(text: str, default: dict) -> dict:
    """Parse the JSON response from Gemini, handling code fences."""
    if not text:
        return default
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
        # Validate structure
        result = {
            "secretary": _validate_person(parsed.get("secretary")),
            "general_counsel": _validate_person(parsed.get("general_counsel")),
            "board_chair": _validate_person(parsed.get("board_chair")),
            "ceo": _validate_person(parsed.get("ceo")),
            "cfo": _validate_person(parsed.get("cfo")),
            "directors": [],
        }
        for d in parsed.get("directors", []):
            person = _validate_person(d)
            if person:
                result["directors"].append(person)
        return result
    except (json.JSONDecodeError, TypeError) as e:
        log.warning("Failed to parse board JSON: %s", e)
        return default


def _validate_person(p) -> dict | None:
    """Validate a person dict has a real name (at least 2 words)."""
    if not isinstance(p, dict):
        return None
    name = (p.get("name") or "").strip()
    if not name or len(name.split()) < 2:
        return None
    # Reject obvious non-names
    lower = name.lower()
    if any(w in lower for w in ["n/a", "none", "unknown", "not found", "not listed"]):
        return None
    return {"name": name, "title": (p.get("title") or "").strip()}
