"""Manual Boolean Query Builder router."""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException, status
from google.cloud import bigquery

from api.config import settings
from api.models.schemas import BooleanQuery, PatentRecord, QueryResult
from api.services.bigquery_service import bq_service

router = APIRouter(prefix="/query", tags=["Query"])

# Allowed columns and the BigQuery expression used to reference them.
# Applicant sub-fields require UNNEST and are handled specially.
_ALLOWED_FIELDS: Dict[str, str] = {
    "patent_number": "p.patent_number",
    "invention_title": "p.invention_title",
    "grant_date": "CAST(p.grant_date AS STRING)",
    "applicant_name": "app.name",
    "applicant_city": "app.city",
    "applicant_state": "app.state",
    "applicant_country": "app.country",
    "applicant_entity_type": "app.entity_type",
}

_APPLICANT_FIELDS = {"applicant_name", "applicant_city", "applicant_state", "applicant_country", "applicant_entity_type"}

_ALLOWED_OPERATORS = {"CONTAINS", "EQUALS", "STARTS_WITH", "ENDS_WITH"}


def _build_condition(field: str, operator: str, param_name: str) -> str:
    col = _ALLOWED_FIELDS[field]
    if operator == "EQUALS":
        return f"LOWER({col}) = LOWER(@{param_name})"
    if operator == "CONTAINS":
        return f"LOWER({col}) LIKE LOWER(CONCAT('%', @{param_name}, '%'))"
    if operator == "STARTS_WITH":
        return f"LOWER({col}) LIKE LOWER(CONCAT(@{param_name}, '%'))"
    if operator == "ENDS_WITH":
        return f"LOWER({col}) LIKE LOWER(CONCAT('%', @{param_name}))"
    raise ValueError(f"Unsupported operator: {operator}")


def _build_sql(query: BooleanQuery) -> tuple[str, List[bigquery.ScalarQueryParameter]]:
    """Translate a BooleanQuery into a parameterised BigQuery SQL string."""
    needs_unnest = any(c.field in _APPLICANT_FIELDS for c in query.conditions)

    from_clause = f"FROM `{settings.patent_table}` AS p"
    if needs_unnest:
        from_clause += ", UNNEST(p.applicants) AS app"

    params: List[bigquery.ScalarQueryParameter] = []
    clauses: List[str] = []

    for i, cond in enumerate(query.conditions):
        if cond.field not in _ALLOWED_FIELDS:
            raise ValueError(f"Field '{cond.field}' is not allowed.")
        if cond.operator not in _ALLOWED_OPERATORS:
            raise ValueError(f"Operator '{cond.operator}' is not allowed.")

        param_name = f"p{i}"
        clauses.append(_build_condition(cond.field, cond.operator, param_name))
        params.append(bigquery.ScalarQueryParameter(param_name, "STRING", cond.value))

    logic = query.logic.upper()
    if logic not in ("AND", "OR"):
        raise ValueError("Logic must be AND or OR.")

    where = f"WHERE {f' {logic} '.join(clauses)}"
    sql = (
        f"SELECT p.patent_number, p.invention_title, "
        f"CAST(p.grant_date AS STRING) AS grant_date, p.applicants "
        f"{from_clause} {where} "
        f"LIMIT {query.limit}"
    )
    return sql, params


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
        patent_number=row["patent_number"],
        invention_title=row.get("invention_title"),
        grant_date=row.get("grant_date"),
        applicants=applicants,
    )


@router.post("/execute", response_model=QueryResult, summary="Execute a boolean patent query")
def execute_query(query: BooleanQuery) -> QueryResult:
    """
    Build and execute a strict Boolean query against the patent_file_wrapper
    table.  Returns matching patent records up to the specified limit.
    """
    try:
        sql, params = _build_sql(query)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    rows = bq_service.search_patents(sql, params)
    patents = [_row_to_patent(r) for r in rows]
    return QueryResult(total_rows=len(patents), rows=patents)


@router.get("/fields", summary="List queryable fields and operators")
def list_fields() -> dict:
    """Return the set of fields and operators available in the query builder."""
    return {
        "fields": list(_ALLOWED_FIELDS.keys()),
        "operators": list(_ALLOWED_OPERATORS),
        "logic_options": ["AND", "OR"],
    }
