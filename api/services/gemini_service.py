"""Gemini AI service – uses Vertex AI for NL-to-SQL and direct answers."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from google.cloud import aiplatform
from vertexai.generative_models import Content, GenerativeModel, Part

from api.config import settings

# BigQuery schema description injected into every prompt so Gemini understands
# the available tables and columns.
_SCHEMA_CONTEXT = """
You have access to a Google BigQuery dataset called `uspto_data` with the following tables:

Table: patent_file_wrapper
  - patent_number      STRING   (nullable – NULL for pending applications)
  - application_number STRING   (USPTO application number)
  - invention_title    STRING
  - grant_date         DATE
  - applicants         ARRAY<STRUCT<name STRING, street_address STRING, city STRING, state STRING, country STRING, entity_type STRING>>

Table: patent_assignments
  - patent_number      STRING   (nullable – NULL for ungranted applications)
  - application_number STRING   (USPTO application number)
  - recorded_date      DATE
  - assignees          ARRAY<STRUCT<name STRING, street_address STRING, city STRING, state STRING, country STRING>>

Table: maintenance_fee_events
  - patent_number      STRING
  - application_number STRING
  - event_code         STRING
  - event_date         DATE
  - fee_code           STRING
  - entity_status      STRING   (SMALL | MICRO | LARGE)

Table: name_unification
  - representative_name STRING  (the canonical entity name)
  - associated_name     STRING  (a variant/typo name linked to the representative)

Table: entity_names
  - entity_name  STRING   (unique entity name from applicants/assignees)
  - frequency    INT64    (total occurrence count across all patent tables)

BIGQUERY SQL RULES (you MUST follow these — violations cause query failures):
1. NEVER write `SELECT expression WHERE condition` without a FROM clause.
   BigQuery requires FROM before WHERE. Wrong: `SELECT 'x' WHERE y`. Right: use
   a CTE or subquery that has a FROM clause.
2. Do NOT use correlated subqueries that reference other CTEs or tables.
   Use JOINs instead.
3. Do NOT end queries with a semicolon.
4. Use CROSS JOIN UNNEST(...) for nested ARRAY fields (not comma UNNEST).
5. ARRAY_AGG syntax: the OFFSET accessor goes OUTSIDE the function call on the
   resulting array, never inside the function arguments. Correct syntax:
     ARRAY_AGG(expr ORDER BY col DESC LIMIT 1)[OFFSET(0)]
   Wrong: ARRAY_AGG(expr OFFSET 0) — this will fail.
6. When using CASE expressions inside ARRAY_AGG or other aggregate functions,
   make sure the entire expression is valid BigQuery SQL. Test mentally that
   each SELECT has a FROM clause.

COMPREHENSIVE RESULTS RULE (IMPORTANT):
Always produce rich, comprehensive results by JOINing related tables. The tables
are linked via patent_number and application_number. When a query involves any
table, JOIN the other tables to include as many useful columns as possible:
- patent_file_wrapper: invention_title, grant_date, applicant name/address/entity_type
- patent_assignments: assignee name/address, recorded_date
- maintenance_fee_events: event_code, event_date, fee_code, entity_status

For example, if the user asks about maintenance fee payments for an entity, do NOT
select only from maintenance_fee_events. Instead, JOIN patent_file_wrapper and
patent_assignments to also include invention_title, grant_date, applicant_name,
assignee_name, etc. Use LEFT JOINs so rows are not lost when a patent exists in
one table but not another.

UNNEST WITH LEFT JOIN RULE (CRITICAL):
When you LEFT JOIN a table that has ARRAY fields, you MUST NOT use CROSS JOIN UNNEST
on that table's arrays — it converts the LEFT JOIN into an effective INNER JOIN
(drops rows where the LEFT JOIN produced NULL). Instead, flatten arrays into scalar
values using a subquery or CTE with ARRAY_AGG(...LIMIT 1)[OFFSET(0)].

Recommended pattern for getting applicant/assignee names alongside other data:
  WITH applicant_info AS (
    SELECT pfw.patent_number, pfw.invention_title, pfw.grant_date,
      ARRAY_AGG(app.name LIMIT 1)[OFFSET(0)] AS applicant_name
    FROM `uspto_data.patent_file_wrapper` pfw
    CROSS JOIN UNNEST(pfw.applicants) AS app
    WHERE app.name IS NOT NULL
    GROUP BY pfw.patent_number, pfw.invention_title, pfw.grant_date
  ),
  assignee_info AS (
    SELECT pa.patent_number,
      ARRAY_AGG(asgn.name ORDER BY pa.recorded_date DESC LIMIT 1)[OFFSET(0)] AS recent_assignee_name
    FROM `uspto_data.patent_assignments` pa
    CROSS JOIN UNNEST(pa.assignees) AS asgn
    WHERE asgn.name IS NOT NULL
    GROUP BY pa.patent_number
  )
Then LEFT JOIN these CTEs to your main query on patent_number.

SCALAR VALUES ONLY: Never return ARRAY columns in the final SELECT. Always flatten
arrays to scalar values using ARRAY_AGG(...LIMIT 1)[OFFSET(0)] or similar. The
frontend cannot display array columns.

INCLUDE ALL RELEVANT COLUMNS: When querying a table, include ALL its useful columns
in the SELECT, not just the ones mentioned in the filter. For maintenance_fee_events
always include: patent_number, event_code, event_date, fee_code, entity_status.
For patent_file_wrapper always include: patent_number, application_number,
invention_title, grant_date. For patent_assignments always include: recorded_date.

When you need entity name frequency counts or want to list/search entities, prefer using the
entity_names table over UNNESTing the raw applicants/assignees arrays, as it contains
pre-computed aggregates. Use the raw tables only when you need address details or patent-level data.

IMPORTANT: Entity names in the data are stored in UPPERCASE. Always use case-insensitive
matching (UPPER or LOWER) when filtering by entity name.

When filtering by entity name (applicant or assignee), use the name_unification
table to expand the search. Use this exact pattern to find all name variants:

  WITH name_variants AS (
    SELECT nu2.associated_name
    FROM `uspto_data.name_unification` nu1
    JOIN `uspto_data.name_unification` nu2
      ON nu2.representative_name = nu1.representative_name
    WHERE LOWER(nu1.associated_name) = LOWER('Entity Name')
  )

Then use: WHERE app.name IN (SELECT associated_name FROM name_variants)

If no unification exists for the entity, fall back to case-insensitive matching directly:
  WHERE LOWER(app.name) = LOWER('Entity Name')

Use COALESCE or a LEFT JOIN pattern to handle both cases in a single query. Example:

  WITH name_variants AS (
    SELECT nu2.associated_name
    FROM `uspto_data.name_unification` nu1
    JOIN `uspto_data.name_unification` nu2
      ON nu2.representative_name = nu1.representative_name
    WHERE LOWER(nu1.associated_name) = LOWER('Entity Name')
  )
  ...
  WHERE app.name IN (SELECT associated_name FROM name_variants)
     OR (NOT EXISTS (SELECT 1 FROM name_variants) AND LOWER(app.name) = LOWER('Entity Name'))
"""

_SYSTEM_PROMPT = (
    "You are an expert data analyst assistant for the USPTO Data Platform. "
    + _SCHEMA_CONTEXT
    + "\n\nCONVERSATION RULES:\n"
    "You are in a conversational chat. The user will describe what data they want. "
    "Your job is to discuss, clarify, and refine the query with them.\n"
    "- When the user describes a query, explain what you understand and ask if they "
    "want to refine it or run it.\n"
    "- Only generate SQL when the user explicitly asks to run/execute the query "
    "(e.g. 'run it', 'execute', 'go ahead', 'yes', 'looks good', 'run the query').\n"
    "- When you DO generate SQL, wrap it in ```sql ... ``` fences, then on a new "
    "line beginning with 'Answer:' provide a concise explanation.\n"
    "- When you are just discussing (not executing), do NOT output SQL fences. "
    "Just respond in plain English.\n"
    "- Keep your responses concise and focused."
)

_VERTEX_REGION = "us-central1"
_MODEL_NAME = "gemini-2.5-flash"


class GeminiService:
    def __init__(self) -> None:
        self._model: Optional[GenerativeModel] = None

    def _get_model(self) -> GenerativeModel:
        if self._model is None:
            aiplatform.init(
                project=settings.GCP_PROJECT_ID,
                location=_VERTEX_REGION,
            )
            self._model = GenerativeModel(_MODEL_NAME)
        return self._model

    def chat(
        self,
        user_prompt: str,
        history: List[Dict[str, str]],
    ) -> Tuple[Optional[str], str]:
        """
        Send a message in a conversational chat with history.

        Parameters
        ----------
        user_prompt : str
            The latest user message.
        history : list of dict
            Previous messages: [{"role": "user"|"ai", "content": "..."}]

        Returns
        -------
        (sql, answer)
            sql    – extracted SQL string, or None if Gemini did not produce one
            answer – the plain-English portion of the response
        """
        model = self._get_model()

        # Build conversation contents: system prompt as first user turn,
        # then alternating user/model turns from history, then current message.
        contents: List[Content] = []

        # System prompt as initial context
        contents.append(Content(role="user", parts=[Part.from_text(_SYSTEM_PROMPT)]))
        contents.append(Content(role="model", parts=[Part.from_text(
            "Understood. I'm ready to help you query the USPTO patent data. "
            "Describe what data you're looking for and I'll help refine the query."
        )]))

        # Add conversation history
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append(Content(role=role, parts=[Part.from_text(msg["content"])]))

        # Add current user message
        contents.append(Content(role="user", parts=[Part.from_text(user_prompt)]))

        response = model.generate_content(contents)
        text = response.text or ""

        sql = _extract_sql(text)
        answer = _extract_answer(text)
        return sql, answer

    def fix_sql(
        self,
        history: List[Dict[str, str]],
        failed_sql: str,
        error: str,
    ) -> Tuple[Optional[str], str]:
        """Ask Gemini to fix a SQL query that failed execution, with chat context."""
        model = self._get_model()

        contents: List[Content] = []
        contents.append(Content(role="user", parts=[Part.from_text(_SYSTEM_PROMPT)]))
        contents.append(Content(role="model", parts=[Part.from_text("Understood.")]))

        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append(Content(role=role, parts=[Part.from_text(msg["content"])]))

        fix_msg = (
            f"The following SQL was generated but failed with this error:\n"
            f"```sql\n{failed_sql}\n```\n"
            f"Error: {error}\n\n"
            f"Please fix the SQL and provide the corrected query."
        )
        contents.append(Content(role="user", parts=[Part.from_text(fix_msg)]))

        response = model.generate_content(contents)
        text = response.text or ""
        return _extract_sql(text), _extract_answer(text)


def _extract_sql(text: str) -> Optional[str]:
    """Pull the first ```sql ... ``` block from Gemini's response."""
    match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        sql = match.group(1).strip().rstrip(";")
        return sql
    return None


def _extract_answer(text: str) -> str:
    """Return the 'Answer:' section, or the full text if not present."""
    idx = text.find("Answer:")
    if idx != -1:
        return text[idx + len("Answer:"):].strip()
    # Fall back to text with code fences removed
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()


gemini_service = GeminiService()
