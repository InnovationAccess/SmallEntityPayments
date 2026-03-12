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
All tables use a FLAT schema — there are NO nested ARRAY or STRUCT columns.

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

--- PATENT ASSIGNMENTS (4 normalized tables linked by reel_frame) ---

Table: pat_assign_records  (one row per assignment transaction)
  - reel_frame               STRING NOT NULL  (e.g. "12345/1")
  - recorded_date            DATE NOT NULL     (partitioned by MONTH)
  - last_update_date         DATE
  - conveyance_text          STRING           (raw text)
  - conveyance_type          STRING           (ASSIGNMENT, SECURITY_INTEREST, MERGER, RELEASE, LICENSE, GOVERNMENT_INTEREST, CORRECTION, OTHER)
  - page_count               INT64
  - correspondent_name       STRING
  - employer_assignment      BOOLEAN          (NULL for now)

Table: pat_assign_assignors  (one row per assignor per assignment)
  - reel_frame               STRING NOT NULL
  - assignor_name            STRING
  - assignor_execution_date  DATE

Table: pat_assign_assignees  (one row per assignee per assignment)
  - reel_frame               STRING NOT NULL
  - assignee_name            STRING
  - assignee_address_1       STRING
  - assignee_address_2       STRING
  - assignee_city            STRING
  - assignee_state           STRING
  - assignee_postcode        STRING
  - assignee_country         STRING

Table: pat_assign_documents  (one row per patent property per assignment)
  - reel_frame               STRING NOT NULL
  - application_number       STRING           (USPTO application number)
  - filing_date              DATE
  - publication_number       STRING           (pre-grant publication number, can be NULL)
  - publication_date         DATE
  - patent_number            STRING           (granted patent number, can be NULL for pending)
  - grant_date               DATE
  - invention_title          STRING

--- OTHER TABLES ---

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
  - citing_patent_number     STRING NOT NULL
  - citing_grant_date        DATE
  - cited_patent_number      STRING NOT NULL
  - citation_category        STRING

Table: name_unification
  - representative_name      STRING  (the canonical entity name)
  - associated_name          STRING  (a variant/typo name linked to the representative)

Table: entity_names
  - entity_name              STRING  (unique entity name from applicants/assignees)
  - frequency                INT64   (total occurrence count across all patent tables)

BIGQUERY SQL RULES (you MUST follow these — violations cause query failures):
1. NEVER write `SELECT expression WHERE condition` without a FROM clause.
2. Do NOT use correlated subqueries. Use JOINs instead.
3. Do NOT end queries with a semicolon.
4. There are NO ARRAY columns — do NOT use UNNEST anywhere.
5. ARRAY_AGG syntax: ARRAY_AGG(expr ORDER BY col DESC LIMIT 1)[OFFSET(0)]
6. Make sure each SELECT has a FROM clause.

CROSS-TABLE JOIN STRATEGY — application_number IS THE UNIVERSAL KEY:
Every patent asset has an application_number, but only granted patents have a
patent_number and only published apps have a publication_number. Therefore:
- ALL cross-table joins MUST use application_number (not patent_number)
- This ensures pending applications are never silently dropped from results

Join patterns:
- patent_file_wrapper_v2 ↔ pat_assign_documents ON application_number
- patent_file_wrapper_v2 ↔ maintenance_fee_events_v2 ON application_number
- patent_file_wrapper_v2 ↔ pfw_transactions ON application_number
- patent_file_wrapper_v2 ↔ pfw_continuity ON application_number
- pat_assign_documents ↔ pat_assign_records ON reel_frame
- pat_assign_documents ↔ pat_assign_assignees ON reel_frame
- pat_assign_documents ↔ pat_assign_assignors ON reel_frame
- forward_citations joins ON cited_patent_number or citing_patent_number

PATENT NUMBER / PUBLICATION NUMBER RESOLUTION:
When a user asks about a specific patent_number or publication_number:
1. First resolve to application_number:
   SELECT application_number FROM `uspto_data.pat_assign_documents`
   WHERE patent_number = '10123456'
   -- or use patent_file_wrapper_v2 for patent_number → application_number
2. Do the analysis using application_number as the join key
3. Include patent_number and/or publication_number in the output for display

COMPREHENSIVE RESULTS — always JOIN related tables:
- patent_file_wrapper_v2: invention_title, grant_date, first_applicant_name, entity_status
- Assignment tables: assignee_name, assignee_city, recorded_date, conveyance_type
- maintenance_fee_events_v2: event_code, event_date, entity_status
- forward_citations: citing_patent_number, citing_grant_date

Use LEFT JOINs so rows are not lost.

Recommended pattern for getting recent assignee alongside other data:
  WITH recent_assignees AS (
    SELECT ad.application_number,
      ARRAY_AGG(ae.assignee_name ORDER BY ar.recorded_date DESC LIMIT 1)[OFFSET(0)]
        AS recent_assignee_name
    FROM `uspto_data.pat_assign_documents` ad
    JOIN `uspto_data.pat_assign_records` ar ON ar.reel_frame = ad.reel_frame
    JOIN `uspto_data.pat_assign_assignees` ae ON ae.reel_frame = ad.reel_frame
    WHERE ae.assignee_name IS NOT NULL AND ad.application_number IS NOT NULL
    GROUP BY ad.application_number
  )
Then LEFT JOIN on application_number.

INCLUDE ALL RELEVANT COLUMNS in the SELECT, not just filtered ones.

Entity names are stored in UPPERCASE. Use case-insensitive matching.

When filtering by entity name, use name_unification to expand:
  WITH name_variants AS (
    SELECT nu2.associated_name
    FROM `uspto_data.name_unification` nu1
    JOIN `uspto_data.name_unification` nu2
      ON nu2.representative_name = nu1.representative_name
    WHERE LOWER(nu1.associated_name) = LOWER('Entity Name')
  )
Then: WHERE column IN (SELECT associated_name FROM name_variants)

If no unification exists, fall back to case-insensitive matching:
  WHERE column IN (SELECT associated_name FROM name_variants)
     OR (NOT EXISTS (SELECT 1 FROM name_variants)
         AND LOWER(column) = LOWER('Entity Name'))
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
