"""Gemini AI service – wraps google-generativeai for NL-to-SQL and direct answers."""

from __future__ import annotations

import re
from typing import Optional, Tuple

import google.generativeai as genai

from api.config import settings

# BigQuery schema description injected into every prompt so Gemini understands
# the available tables and columns.
_SCHEMA_CONTEXT = """
You have access to a Google BigQuery dataset called `uspto_data` with the following tables:

Table: patent_file_wrapper
  - patent_number   STRING   (e.g. US10000001B2)
  - invention_title STRING
  - grant_date      DATE
  - applicants      ARRAY<STRUCT<name STRING, street_address STRING, city STRING, state STRING, country STRING, entity_type STRING>>

Table: patent_assignments
  - patent_number   STRING
  - recorded_date   DATE
  - assignees       ARRAY<STRUCT<name STRING, street_address STRING, city STRING, state STRING, country STRING>>

Table: maintenance_fee_events
  - patent_number   STRING
  - event_code      STRING
  - event_date      DATE
  - fee_code        STRING
  - entity_status   STRING   (SMALL | MICRO | LARGE)

Table: name_unification
  - representative_name STRING  (the canonical entity name)
  - associated_name     STRING  (a variant/typo name linked to the representative)

Always generate standard SQL compatible with Google BigQuery.
When referencing nested ARRAY fields use UNNEST or CROSS JOIN UNNEST syntax.

IMPORTANT: When filtering by entity name (applicant or assignee), use the name_unification
table to expand the search. Join or subquery against name_unification to find ALL variant
names associated with the same representative_name, so that the query covers all known
spellings of the entity. For example:
  WHERE app.name IN (
    SELECT associated_name FROM `uspto_data.name_unification`
    WHERE representative_name = (
      SELECT representative_name FROM `uspto_data.name_unification`
      WHERE associated_name = 'Entity Name' LIMIT 1
    )
  )
If the entity is not found in name_unification, fall back to matching the name directly.
"""

_SYSTEM_PROMPT = (
    "You are an expert data analyst assistant for the USPTO Data Platform. "
    + _SCHEMA_CONTEXT
    + "\nWhen a user asks a question, first output a valid BigQuery SQL query wrapped in "
    "```sql ... ``` fences, then on a new line beginning with 'Answer:' provide a "
    "concise plain-English explanation of the query and what it will return."
)


class GeminiService:
    def __init__(self) -> None:
        self._model: Optional[genai.GenerativeModel] = None

    def _get_model(self) -> genai.GenerativeModel:
        if self._model is None:
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self._model = genai.GenerativeModel("gemini-2.5-flash")
        return self._model

    def generate_sql_and_answer(self, user_prompt: str) -> Tuple[Optional[str], str]:
        """
        Ask Gemini to translate *user_prompt* into a BigQuery SQL query.

        Returns
        -------
        (sql, answer)
            sql    – extracted SQL string, or None if Gemini did not produce one
            answer – the plain-English portion of the response
        """
        model = self._get_model()
        full_prompt = f"{_SYSTEM_PROMPT}\n\nUser question: {user_prompt}"
        response = model.generate_content(full_prompt)
        text = response.text or ""

        sql = _extract_sql(text)
        answer = _extract_answer(text)
        return sql, answer


def _extract_sql(text: str) -> Optional[str]:
    """Pull the first ```sql ... ``` block from Gemini's response."""
    match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_answer(text: str) -> str:
    """Return the 'Answer:' section, or the full text if not present."""
    idx = text.find("Answer:")
    if idx != -1:
        return text[idx + len("Answer:"):].strip()
    # Fall back to text with code fences removed
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()


gemini_service = GeminiService()
