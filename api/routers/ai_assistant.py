"""Gemini AI Assistant router – conversational querying of patent data."""

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
    """Handle a conversational message — discuss, refine, or execute queries."""
    if not request.prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Prompt must not be empty.",
        )

    history = [{"role": m.role, "content": m.content} for m in request.history]

    try:
        sql, answer = gemini_service.chat(request.prompt, history)
    except Exception as exc:  # noqa: BLE001
        return AIQueryResponse(
            generated_sql=None,
            answer=f"Could not generate response: {exc}",
            rows=[],
        )

    rows: list[Dict[str, Any]] = []
    max_retries = 2
    if sql:
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                raw_rows = bq_service.run_query(sql)
                rows = [_row_to_dict(r) for r in raw_rows]
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < max_retries:
                    try:
                        fixed_sql, fixed_answer = gemini_service.fix_sql(
                            history, sql, last_error
                        )
                        if fixed_sql:
                            sql = fixed_sql
                            answer = fixed_answer
                        else:
                            break
                    except Exception:  # noqa: BLE001
                        break
                else:
                    break
        if last_error:
            answer += f"\n\n[Query failed after {max_retries + 1} attempts: {last_error}]"

    return AIQueryResponse(generated_sql=sql, answer=answer, rows=rows)
