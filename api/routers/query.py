"""Manual Boolean Query Builder router – multi-table support."""

from __future__ import annotations

from typing import Any, Dict, List, Set

from fastapi import APIRouter, HTTPException, status
from google.cloud import bigquery

from api.config import settings
from api.models.schemas import BooleanQuery, QueryResult
from api.services.bigquery_service import bq_service

# Re-use the same boolean parser as MDM for name field boolean expressions.
from api.routers.mdm import _parse_boolean_query

router = APIRouter(prefix="/query", tags=["Query"])

# Allowed fields grouped by table, with the SQL expression to reference them.
_TABLE_FIELDS: Dict[str, Dict[str, str]] = {
    "patent_file_wrapper": {
        "patent_number": "p.patent_number",
        "application_number": "p.application_number",
        "invention_title": "p.invention_title",
        "grant_date": "CAST(p.grant_date AS STRING)",
        "applicant_name": "app.name",
        "applicant_street_address": "app.street_address",
        "applicant_city": "app.city",
        "applicant_state": "app.state",
        "applicant_country": "app.country",
        "applicant_entity_type": "app.entity_type",
    },
    "patent_assignments": {
        "patent_number": "a.patent_number",
        "application_number": "a.application_number",
        "recorded_date": "CAST(a.recorded_date AS STRING)",
        "assignee_name": "asgn.name",
        "assignee_street_address": "asgn.street_address",
        "assignee_city": "asgn.city",
        "assignee_state": "asgn.state",
        "assignee_country": "asgn.country",
    },
    "maintenance_fee_events": {
        "patent_number": "m.patent_number",
        "application_number": "m.application_number",
        "event_code": "m.event_code",
        "event_date": "CAST(m.event_date AS STRING)",
        "fee_code": "m.fee_code",
        "entity_status": "m.entity_status",
    },
}

_UNNEST_FIELDS_PATENT = {
    "applicant_name", "applicant_street_address", "applicant_city",
    "applicant_state", "applicant_country", "applicant_entity_type",
}
_UNNEST_FIELDS_ASSIGNMENT = {
    "assignee_name", "assignee_street_address", "assignee_city",
    "assignee_state", "assignee_country",
}

# Fields that represent entity names (for name expansion).
_NAME_FIELDS = {"applicant_name", "assignee_name"}

# Code fields that support multi-value selection (comma-separated).
_CODE_FIELDS = {"event_code", "fee_code"}

_ALLOWED_OPERATORS = {"CONTAINS", "EQUALS", "STARTS_WITH", "ENDS_WITH", "AFTER", "BEFORE"}
_VALID_TABLES = {"patent_file_wrapper", "patent_assignments", "maintenance_fee_events"}


def _build_condition(col_expr: str, operator: str, param_name: str) -> str:
    if operator == "EQUALS":
        return f"LOWER({col_expr}) = LOWER(@{param_name})"
    if operator == "CONTAINS":
        return f"LOWER({col_expr}) LIKE LOWER(CONCAT('%', @{param_name}, '%'))"
    if operator == "STARTS_WITH":
        return f"LOWER({col_expr}) LIKE LOWER(CONCAT(@{param_name}, '%'))"
    if operator == "ENDS_WITH":
        return f"LOWER({col_expr}) LIKE LOWER(CONCAT('%', @{param_name}))"
    if operator == "AFTER":
        return f"{col_expr} >= @{param_name}"
    if operator == "BEFORE":
        return f"{col_expr} <= @{param_name}"
    raise ValueError(f"Unsupported operator: {operator}")


def _resolve_field(field: str, tables: List[str]) -> str:
    """Find the SQL expression for a field across selected tables."""
    for table in tables:
        table_fields = _TABLE_FIELDS.get(table, {})
        if field in table_fields:
            return table_fields[field]
    raise ValueError(f"Field '{field}' is not available in the selected tables.")


def _build_sql(query: BooleanQuery) -> tuple[str, List[bigquery.ScalarQueryParameter]]:
    """Translate a BooleanQuery into parameterised BigQuery SQL."""
    tables = [t for t in query.tables if t in _VALID_TABLES]
    if not tables:
        raise ValueError("At least one valid table must be selected.")

    # Determine which fields are used and validate them.
    used_fields: Set[str] = set()
    for cond in query.conditions:
        used_fields.add(cond.field)

    # Build FROM clause with appropriate JOINs.
    needs_patent_unnest = bool(used_fields & _UNNEST_FIELDS_PATENT) and "patent_file_wrapper" in tables
    needs_assignment_unnest = bool(used_fields & _UNNEST_FIELDS_ASSIGNMENT) and "patent_assignments" in tables

    from_parts: List[str] = []
    select_parts: List[str] = []

    if "patent_file_wrapper" in tables:
        from_parts.append(f"`{settings.patent_table}` AS p")
        if needs_patent_unnest:
            from_parts.append("CROSS JOIN UNNEST(p.applicants) AS app")
        select_parts.extend([
            "p.patent_number", "p.application_number", "p.invention_title",
            "CAST(p.grant_date AS STRING) AS grant_date",
        ])

    if "patent_assignments" in tables:
        join_on = ""
        if "patent_file_wrapper" in tables:
            join_on = (
                f"JOIN `{settings.assignments_table}` AS a "
                f"ON (a.patent_number = p.patent_number AND a.patent_number IS NOT NULL) "
                f"OR (a.application_number = p.application_number AND a.application_number IS NOT NULL)"
            )
        else:
            from_parts.append(f"`{settings.assignments_table}` AS a")
        if join_on:
            from_parts.append(join_on)
        if needs_assignment_unnest:
            from_parts.append("CROSS JOIN UNNEST(a.assignees) AS asgn")
        if "patent_file_wrapper" not in tables:
            select_parts.extend(["a.patent_number", "a.application_number"])
        select_parts.append("CAST(a.recorded_date AS STRING) AS recorded_date")

    if "maintenance_fee_events" in tables:
        if from_parts:
            base_alias = "p" if "patent_file_wrapper" in tables else "a"
            from_parts.append(
                f"JOIN `{settings.maintenance_table}` AS m "
                f"ON (m.patent_number = {base_alias}.patent_number AND m.patent_number IS NOT NULL) "
                f"OR (m.application_number = {base_alias}.application_number AND m.application_number IS NOT NULL)"
            )
        else:
            from_parts.append(f"`{settings.maintenance_table}` AS m")
            select_parts.extend(["m.patent_number", "m.application_number"])
        select_parts.extend([
            "m.event_code", "CAST(m.event_date AS STRING) AS event_date",
            "m.fee_code", "m.entity_status",
        ])

    # Build WHERE clause.
    params: List[bigquery.ScalarQueryParameter] = []
    clauses: List[str] = []

    for i, cond in enumerate(query.conditions):
        if cond.operator not in _ALLOWED_OPERATORS:
            raise ValueError(f"Operator '{cond.operator}' is not allowed.")

        col_expr = _resolve_field(cond.field, tables)
        param_name = f"p{i}"

        # Name expansion: if this is an entity name field with EQUALS,
        # expand via name_unification.
        if cond.field in _NAME_FIELDS and cond.operator == "EQUALS":
            expanded = bq_service.expand_name_for_query(cond.value)
            if len(expanded) > 1:
                # Use IN clause with all expanded names.
                in_placeholders: List[str] = []
                for j, name in enumerate(expanded):
                    pn = f"p{i}_exp{j}"
                    in_placeholders.append(f"@{pn}")
                    params.append(bigquery.ScalarQueryParameter(pn, "STRING", name))
                clauses.append(f"LOWER({col_expr}) IN ({', '.join(f'LOWER({p})' for p in in_placeholders)})")
                continue

        # Code fields with EQUALS: support multi-value (comma-separated).
        if cond.field in _CODE_FIELDS and cond.operator == "EQUALS" and "," in cond.value:
            values = [v.strip() for v in cond.value.split(",") if v.strip()]
            if values:
                in_placeholders: List[str] = []
                for j, val in enumerate(values):
                    pn = f"p{i}_code{j}"
                    in_placeholders.append(f"@{pn}")
                    params.append(bigquery.ScalarQueryParameter(pn, "STRING", val))
                clauses.append(f"{col_expr} IN ({', '.join(in_placeholders)})")
                continue

        # Name fields with CONTAINS: support boolean expressions (+, -, *)
        if cond.field in _NAME_FIELDS and cond.operator == "CONTAINS":
            and_terms, not_terms = _parse_boolean_query(cond.value)
            if and_terms or not_terms:
                sub_clauses: List[str] = []
                for j, term in enumerate(and_terms):
                    pn = f"p{i}_and{j}"
                    sub_clauses.append(f"LOWER({col_expr}) LIKE LOWER(@{pn})")
                    params.append(bigquery.ScalarQueryParameter(pn, "STRING", term))
                for j, term in enumerate(not_terms):
                    pn = f"p{i}_not{j}"
                    sub_clauses.append(f"LOWER({col_expr}) NOT LIKE LOWER(@{pn})")
                    params.append(bigquery.ScalarQueryParameter(pn, "STRING", term))
                clauses.append(f"({' AND '.join(sub_clauses)})")
                continue

        clauses.append(_build_condition(col_expr, cond.operator, param_name))
        params.append(bigquery.ScalarQueryParameter(param_name, "STRING", cond.value))

    logic = query.logic.upper()
    if logic not in ("AND", "OR"):
        raise ValueError("Logic must be AND or OR.")

    where = f"WHERE {f' {logic} '.join(clauses)}" if clauses else ""
    from_sql = ", ".join(from_parts[:1])
    if len(from_parts) > 1:
        from_sql += " " + " ".join(from_parts[1:])

    main_sql = f"SELECT DISTINCT {', '.join(select_parts)} FROM {from_sql} {where} LIMIT {query.limit}"

    # Wrap in CTEs to add applicant and recent assignee name columns.
    sql = f"""
    WITH main_results AS (
      {main_sql}
    ),
    applicant_names AS (
      SELECT pfw.patent_number,
        ARRAY_AGG(app_sub.name LIMIT 1)[OFFSET(0)] AS applicant_name
      FROM `{settings.patent_table}` pfw
      CROSS JOIN UNNEST(pfw.applicants) AS app_sub
      WHERE pfw.patent_number IN (SELECT patent_number FROM main_results WHERE patent_number IS NOT NULL)
        AND app_sub.name IS NOT NULL
      GROUP BY pfw.patent_number
    ),
    recent_assignees AS (
      SELECT pa.patent_number,
        ARRAY_AGG(asgn_sub.name ORDER BY pa.recorded_date DESC LIMIT 1)[OFFSET(0)] AS recent_assignee_name
      FROM `{settings.assignments_table}` pa
      CROSS JOIN UNNEST(pa.assignees) AS asgn_sub
      WHERE pa.patent_number IN (SELECT patent_number FROM main_results WHERE patent_number IS NOT NULL)
        AND asgn_sub.name IS NOT NULL
      GROUP BY pa.patent_number
    )
    SELECT mr.*, an.applicant_name, ra.recent_assignee_name
    FROM main_results mr
    LEFT JOIN applicant_names an ON an.patent_number = mr.patent_number
    LEFT JOIN recent_assignees ra ON ra.patent_number = mr.patent_number
    """
    return sql, params


def _row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a BigQuery row to a plain dict, stringifying special types."""
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


@router.post("/execute", response_model=QueryResult)
def execute_query(query: BooleanQuery) -> QueryResult:
    """Build and execute a Boolean query against selected tables."""
    try:
        sql, params = _build_sql(query)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    rows = bq_service.run_query(sql, params)
    result_rows = [_row_to_dict(r) for r in rows]
    return QueryResult(total_rows=len(result_rows), rows=result_rows)


@router.get("/fields")
def list_fields() -> dict:
    """Return fields grouped by table, operators, and available tables."""
    return {
        "tables": list(_VALID_TABLES),
        "fields": {
            table: list(fields.keys())
            for table, fields in _TABLE_FIELDS.items()
        },
        "operators": list(_ALLOWED_OPERATORS),
        "logic_options": ["AND", "OR"],
    }


@router.get("/event-codes")
def list_event_codes() -> dict:
    """Return distinct event_code values from maintenance_fee_events."""
    sql = f"""
    SELECT DISTINCT event_code
    FROM `{settings.maintenance_table}`
    WHERE event_code IS NOT NULL
    ORDER BY event_code
    """
    rows = bq_service.run_query(sql)
    return {"codes": [row["event_code"] for row in rows]}


@router.get("/fee-codes")
def list_fee_codes() -> dict:
    """Return distinct fee_code values from maintenance_fee_events."""
    sql = f"""
    SELECT DISTINCT fee_code
    FROM `{settings.maintenance_table}`
    WHERE fee_code IS NOT NULL
    ORDER BY fee_code
    """
    rows = bq_service.run_query(sql)
    return {"codes": [row["fee_code"] for row in rows]}
