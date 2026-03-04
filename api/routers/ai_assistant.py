"""Gemini AI Assistant router – natural-language querying of patent data."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.models.schemas import AIQueryRequest, AIQueryResponse, PatentRecord
from api.services.bigquery_service import bq_service
from api.services.gemini_service import gemini_service

router = APIRouter(prefix="/ai", tags=["AI Assistant"])


def _row_to_patent(row: dict) -> PatentRecord:
    applicants = []
    for a in row.get("applicants") or []:
        applicants.append(
            {
                "name": a.get("name"),
                "city": a.get("city"),
                "state": a.get("state"),
                "country": a.get("country"),
                "entity_type": a.get("entity_type"),
            }
        )
    return PatentRecord(
        patent_number=row.get("patent_number", ""),
        invention_title=row.get("invention_title"),
        grant_date=str(row["grant_date"]) if row.get("grant_date") else None,
        applicants=applicants,
    )


@router.post("/ask", response_model=AIQueryResponse, summary="Ask the AI assistant a natural-language question")
def ask(request: AIQueryRequest) -> AIQueryResponse:
    """
    Translates a natural-language question into a BigQuery SQL query using
    Gemini, executes the query, and returns both the generated SQL and the
    plain-English answer together with matching patent records.
    """
    if not request.prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Prompt must not be empty.",
        )

    sql, answer = gemini_service.generate_sql_and_answer(request.prompt)

    rows: list[PatentRecord] = []
    if sql:
        try:
            raw_rows = bq_service.run_query(sql)
            rows = [_row_to_patent(r) for r in raw_rows]
        except Exception as exc:  # noqa: BLE001
            # Provide a user-friendly error without leaking internal details
            error_type = type(exc).__name__
            answer = f"{answer}\n\n[Query could not be executed ({error_type}). Please refine your question or check that the data exists.]"

    return AIQueryResponse(generated_sql=sql, answer=answer, rows=rows)
