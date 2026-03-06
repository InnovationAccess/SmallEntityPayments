"""Gemini AI Assistant router – natural-language querying of patent data."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, status

from api.models.schemas import AIQueryRequest, AIQueryResponse
from api.services.bigquery_service import bq_service
from api.services.gemini_service import gemini_service

router = APIRouter(prefix="/ai", tags=["AI Assistant"])


def _row_to_dict(row: dict) -> Dict[str, Any]:
    """Convert a BigQuery row to a JSON-safe dict."""
    result: Dict[str, Any] = {}
    for key, val in row.items():
        if hasattr(val, "isoformat"):
            result[key] = val.isoformat()
        elif isinstance(val, list):
            result[key] = [
                {k: v for k, v in item.items()} if hasattr(item, "items") else item
                for item in val
            ]
        else:
            result[key] = val
    return result


@router.post("/ask", response_model=AIQueryResponse)
def ask(request: AIQueryRequest) -> AIQueryResponse:
    """Translate a natural-language question into BigQuery SQL via Gemini,
    execute it, and return the answer with data rows."""
    if not request.prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Prompt must not be empty.",
        )

    sql, answer = gemini_service.generate_sql_and_answer(request.prompt)

    rows: list[Dict[str, Any]] = []
    if sql:
        try:
            raw_rows = bq_service.run_query(sql)
            rows = [_row_to_dict(r) for r in raw_rows]
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
            answer = (
                f"{answer}\n\n[Query could not be executed ({error_type}). "
                "Please refine your question or check that the data exists.]"
            )

    return AIQueryResponse(generated_sql=sql, answer=answer, rows=rows)
