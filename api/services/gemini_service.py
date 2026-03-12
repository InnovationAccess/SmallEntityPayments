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
You have access to a Google BigQuery dataset called `uspto_data` with the following tables.
All tables use a FLAT (denormalized) schema — there are NO nested ARRAY or STRUCT columns.

Table: patent_file_wrapper_v2
  - application_number       STRING NOT NULL  (USPTO application number, primary key)
  - patent_number            STRING           (nullable – NULL for pending applications)
  - invention_title          STRING
  - filing_date              DATE
  - effective_filing_date    DATE
  - grant_date               DATE
  - entity_status            STRING           (SMALL | MICRO | LARGE | UNDISCOUNTED)
  - small_entity_indicator   BOOLEAN
  - application_type         STRING
  - application_type_category STRING
  - application_status_code  INT64
  - application_status       STRING
  - first_inventor_name      STRING           (first listed inventor)
  - first_applicant_name     STRING           (first listed applicant/owner)
  - examiner_name            STRING
  - group_art_unit           STRING
  - customer_number          INT64
  - earliest_publication_number STRING
  - earliest_publication_date DATE
  - national_stage_indicator BOOLEAN
  - first_inventor_to_file   BOOLEAN

Table: patent_assignments_v3
  - reel_frame               STRING NOT NULL  (reel/frame number, e.g. "12345/0001")
  - recorded_date            DATE NOT NULL     (partitioned by this column)
  - last_update_date         DATE
  - conveyance_text          STRING           (raw text, e.g. "ASSIGNMENT OF ASSIGNORS INTEREST")
  - conveyance_type          STRING           (classified: ASSIGNMENT, SECURITY_INTEREST, MERGER, RELEASE, LICENSE, GOVERNMENT_INTEREST, CORRECTION, OTHER)
  - assignor_name            STRING           (entity transferring rights)
  - assignor_execution_date  DATE
  - assignee_name            STRING           (entity receiving rights)
  - assignee_address_1       STRING
  - assignee_address_2       STRING
  - assignee_city            STRING
  - assignee_state           STRING
  - assignee_postcode        STRING
  - assignee_country         STRING
  - correspondent_name       STRING
  - application_number       STRING           (USPTO application number, e.g. "15123456")
  - filing_date              DATE
  - publication_number       STRING           (pre-grant publication number)
  - publication_date         DATE
  - patent_number            STRING           (granted patent number, e.g. "10123456")
  - grant_date               DATE
  - invention_title          STRING
  - page_count               INT64
  - employer_assignment      BOOLEAN          (NULL for now — future use)

Table: maintenance_fee_events_v2
  - patent_number            STRING NOT NULL
  - application_number       STRING
  - entity_status            STRING           (SMALL | MICRO | LARGE | UNDISCOUNTED)
  - filing_date              DATE
  - grant_date               DATE
  - event_date               DATE
  - event_code               STRING

Table: pfw_transactions
  - application_number       STRING NOT NULL
  - event_date               DATE
  - event_code               STRING
  - event_description        STRING

Table: pfw_continuity
  - application_number       STRING NOT NULL
  - claim_parentage_type_code STRING
  - claim_parentage_description STRING
  - parent_application_number STRING
  - parent_filing_date       DATE
  - child_application_number STRING
  - parent_patent_number     STRING
  - parent_status_code       INT64
  - parent_status_description STRING

Table: forward_citations
  - citing_patent_number     STRING NOT NULL   (the patent that cites another)
  - citing_grant_date        DATE
  - cited_patent_number      STRING NOT NULL   (the patent being cited)
  - citation_category        STRING

Table: name_unification
  - representative_name      STRING  (the canonical entity name)
  - associated_name          STRING  (a variant/typo name linked to the representative)

Table: entity_names
  - entity_name              STRING  (unique entity name from applicants/assignees)
  - frequency                INT64   (total occurrence count across all patent tables)

BIGQUERY SQL RULES (you MUST follow these — violations cause query failures):
1. NEVER write `SELECT expression WHERE condition` without a FROM clause.
   BigQuery requires FROM before WHERE.
2. Do NOT use correlated subqueries that reference other CTEs or tables.
   Use JOINs instead.
3. Do NOT end queries with a semicolon.
4. There are NO ARRAY columns in any table — do NOT use UNNEST anywhere.
   All columns are scalar (flat). Just use direct column references.
5. ARRAY_AGG syntax: the OFFSET accessor goes OUTSIDE the function call:
     ARRAY_AGG(expr ORDER BY col DESC LIMIT 1)[OFFSET(0)]
6. When using CASE expressions inside aggregate functions, make sure each
   SELECT has a FROM clause.

COMPREHENSIVE RESULTS RULE (IMPORTANT):
Always produce rich, comprehensive results by JOINing related tables:
- patent_file_wrapper_v2 joins to patent_assignments_v3 ON pfw.patent_number = a.patent_number
- patent_file_wrapper_v2 joins to maintenance_fee_events_v2 ON patent_number
- patent_file_wrapper_v2 joins to pfw_transactions ON application_number
- patent_file_wrapper_v2 joins to pfw_continuity ON application_number
- forward_citations joins ON cited_patent_number or citing_patent_number

When a query involves any table, JOIN related tables to include useful columns:
- patent_file_wrapper_v2: invention_title, grant_date, first_applicant_name, entity_status
- patent_assignments_v3: assignee_name, assignee_city, assignee_state, recorded_date, conveyance_type
- maintenance_fee_events_v2: event_code, event_date, entity_status
- forward_citations: citing_patent_number, citing_grant_date

Use LEFT JOINs so rows are not lost when a patent exists in one table but not another.

Recommended pattern for getting applicant/assignee names alongside other data:
  WITH applicant_info AS (
    SELECT pfw.patent_number, pfw.invention_title, pfw.grant_date,
      pfw.first_applicant_name AS applicant_name
    FROM `uspto_data.patent_file_wrapper_v2` pfw
    WHERE pfw.first_applicant_name IS NOT NULL
  ),
  recent_assignees AS (
    SELECT pa.patent_number,
      ARRAY_AGG(pa.assignee_name ORDER BY pa.recorded_date DESC LIMIT 1)[OFFSET(0)]
        AS recent_assignee_name
    FROM `uspto_data.patent_assignments_v3` pa
    WHERE pa.assignee_name IS NOT NULL AND pa.patent_number IS NOT NULL
    GROUP BY pa.patent_number
  )
Then LEFT JOIN these CTEs to your main query on patent_number.

INCLUDE ALL RELEVANT COLUMNS: When querying a table, include ALL its useful columns
in the SELECT, not just the ones mentioned in the filter.

When you need entity name frequency counts or want to list/search entities, prefer
the entity_names table. Use the raw tables only when you need address details or
patent-level data.

IMPORTANT: Entity names in the data are stored in UPPERCASE. Always use case-insensitive
matching (UPPER or LOWER) when filtering by entity name.

When filtering by entity name (applicant or assignee), use the name_unification
table to expand the search:

  WITH name_variants AS (
    SELECT nu2.associated_name
    FROM `uspto_data.name_unification` nu1
    JOIN `uspto_data.name_unification` nu2
      ON nu2.representative_name = nu1.representative_name
    WHERE LOWER(nu1.associated_name) = LOWER('Entity Name')
  )

Then use: WHERE pfw.first_applicant_name IN (SELECT associated_name FROM name_variants)
Or for assignments: WHERE pa.assignee_name IN (SELECT associated_name FROM name_variants)

IMPORTANT — patent_assignments_v3 has SEPARATE document ID fields:
- application_number: the USPTO application number (e.g. "15123456")
- publication_number: the pre-grant publication number (can be NULL)
- patent_number: the granted patent number (can be NULL for pending apps)
When joining to patent_file_wrapper_v2, ALWAYS use patent_number (not application_number)
unless the query specifically asks about applications.
When a row has patent_number IS NULL, it means the application has not yet been granted.

If no unification exists, fall back to case-insensitive matching:
  WHERE LOWER(pfw.first_applicant_name) = LOWER('Entity Name')

Use a LEFT JOIN pattern to handle both cases:

  WITH name_variants AS (
    SELECT nu2.associated_name
    FROM `uspto_data.name_unification` nu1
    JOIN `uspto_data.name_unification` nu2
      ON nu2.representative_name = nu1.representative_name
    WHERE LOWER(nu1.associated_name) = LOWER('Entity Name')
  )
  ...
  WHERE pfw.first_applicant_name IN (SELECT associated_name FROM name_variants)
     OR (NOT EXISTS (SELECT 1 FROM name_variants)
         AND LOWER(pfw.first_applicant_name) = LOWER('Entity Name'))
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
