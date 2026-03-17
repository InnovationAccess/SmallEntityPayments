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
# Normalized v4 assignment tables: ad=documents, ar=records, ae=assignees, ao=assignors
_TABLE_FIELDS: Dict[str, Dict[str, str]] = {
    "patent_file_wrapper": {
        "patent_number": "p.patent_number",
        "application_number": "p.application_number",
        "invention_title": "p.invention_title",
        "grant_date": "CAST(p.grant_date AS STRING)",
        "filing_date": "CAST(p.filing_date AS STRING)",
        "applicant_name": "p.first_applicant_name",
        "inventor_name": "p.first_inventor_name",
        "entity_status": "p.entity_status",
        "examiner_name": "p.examiner_name",
        "group_art_unit": "p.group_art_unit",
        "application_type": "p.application_type",
        "application_status": "p.application_status",
    },
    "patent_assignments": {
        "patent_number": "ad.patent_number",
        "application_number": "ad.application_number",
        "recorded_date": "CAST(ar.recorded_date AS STRING)",
        "filing_date": "CAST(ad.filing_date AS STRING)",
        "grant_date": "CAST(ad.grant_date AS STRING)",
        "assignee_name": "ae.assignee_name",
        "assignee_city": "ae.assignee_city",
        "assignee_state": "ae.assignee_state",
        "assignee_country": "ae.assignee_country",
        "assignor_name": "ao.assignor_name",
        "conveyance_text": "ar.conveyance_text",
        "conveyance_type": "ar.conveyance_type",
        "reel_frame": "ar.reel_frame",
    },
    "maintenance_fee_events": {
        "patent_number": "m.patent_number",
        "application_number": "m.application_number",
        "event_code": "m.event_code",
        "event_date": "CAST(m.event_date AS STRING)",
        "entity_status": "m.entity_status",
    },
}

# Fields that represent entity names (for name expansion).
_NAME_FIELDS = {"applicant_name", "inventor_name", "assignee_name", "assignor_name"}

# Code fields that support multi-value selection (comma-separated).
_CODE_FIELDS = {"event_code"}

_ALLOWED_OPERATORS = {"CONTAINS", "EQUALS", "STARTS_WITH", "ENDS_WITH", "AFTER", "BEFORE"}
_VALID_TABLES = {"patent_file_wrapper", "patent_assignments", "maintenance_fee_events"}
# Keep old table names as aliases for backward compatibility
_TABLE_ALIAS = {
    "patent_file_wrapper": "patent_file_wrapper",
    "patent_assignments": "patent_assignments",
    "maintenance_fee_events": "maintenance_fee_events",
}


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

    # Build FROM clause with appropriate JOINs.
    # All cross-table joins use application_number as the universal key.
    from_parts: List[str] = []
    select_parts: List[str] = []

    if "patent_file_wrapper" in tables:
        from_parts.append(f"`{settings.patent_table}` AS p")
        select_parts.extend([
            "p.patent_number", "p.application_number", "p.invention_title",
            "CAST(p.grant_date AS STRING) AS grant_date",
            "p.first_applicant_name AS applicant_name",
            "p.entity_status",
        ])

    if "patent_assignments" in tables:
        # Normalized: 4-table JOIN via reel_frame, cross-table via application_number
        if "patent_file_wrapper" in tables:
            # Join through documents table on application_number
            from_parts.append(
                f"JOIN `{settings.assign_documents_table}` AS ad "
                f"ON ad.application_number = p.application_number AND p.application_number IS NOT NULL"
            )
        else:
            from_parts.append(f"`{settings.assign_documents_table}` AS ad")
            select_parts.extend(["ad.patent_number", "ad.application_number"])
        # Join records, assignees, assignors via reel_frame
        from_parts.append(
            f"JOIN `{settings.assign_records_table}` AS ar ON ar.reel_frame = ad.reel_frame"
        )
        from_parts.append(
            f"LEFT JOIN `{settings.assign_assignees_table}` AS ae ON ae.reel_frame = ad.reel_frame"
        )
        from_parts.append(
            f"LEFT JOIN `{settings.assign_assignors_table}` AS ao ON ao.reel_frame = ad.reel_frame"
        )
        select_parts.extend([
            "CAST(ar.recorded_date AS STRING) AS recorded_date",
            "ae.assignee_name", "ao.assignor_name",
        ])

    if "maintenance_fee_events" in tables:
        if from_parts:
            # Join via application_number — the universal key
            if "patent_file_wrapper" in tables:
                from_parts.append(
                    f"JOIN `{settings.maintenance_table}` AS m "
                    f"ON m.application_number = p.application_number AND p.application_number IS NOT NULL"
                )
            elif "patent_assignments" in tables:
                from_parts.append(
                    f"JOIN `{settings.maintenance_table}` AS m "
                    f"ON m.application_number = ad.application_number AND ad.application_number IS NOT NULL"
                )
            else:
                from_parts.append(f"`{settings.maintenance_table}` AS m")
        else:
            from_parts.append(f"`{settings.maintenance_table}` AS m")
            select_parts.extend(["m.patent_number", "m.application_number"])
        select_parts.extend([
            "m.event_code", "CAST(m.event_date AS STRING) AS event_date",
            "m.entity_status AS maint_entity_status",
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
                    # Ensure CONTAINS semantics: wrap with % if not already present
                    if not term.startswith("%"):
                        term = "%" + term
                    if not term.endswith("%"):
                        term = term + "%"
                    pn = f"p{i}_and{j}"
                    sub_clauses.append(f"LOWER({col_expr}) LIKE LOWER(@{pn})")
                    params.append(bigquery.ScalarQueryParameter(pn, "STRING", term))
                for j, term in enumerate(not_terms):
                    if not term.startswith("%"):
                        term = "%" + term
                    if not term.endswith("%"):
                        term = term + "%"
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
    # Recent assignee: join documents (by application_number) → records → assignees
    sql = f"""
    WITH main_results AS (
      {main_sql}
    ),
    applicant_names AS (
      SELECT pfw.application_number,
        pfw.first_applicant_name AS applicant_name
      FROM `{settings.patent_table}` pfw
      WHERE pfw.application_number IN (SELECT application_number FROM main_results WHERE application_number IS NOT NULL)
        AND pfw.first_applicant_name IS NOT NULL
    ),
    recent_assignees AS (
      SELECT ad.application_number,
        ARRAY_AGG(ae.assignee_name ORDER BY ar.recorded_date DESC LIMIT 1)[OFFSET(0)] AS recent_assignee_name
      FROM `{settings.assign_documents_table}` ad
      JOIN `{settings.assign_records_table}` ar ON ar.reel_frame = ad.reel_frame
      JOIN `{settings.assign_assignees_table}` ae ON ae.reel_frame = ad.reel_frame
      WHERE ad.application_number IN (SELECT application_number FROM main_results WHERE application_number IS NOT NULL)
        AND ae.assignee_name IS NOT NULL
      GROUP BY ad.application_number
    )
    SELECT mr.*, an.applicant_name, ra.recent_assignee_name
    FROM main_results mr
    LEFT JOIN applicant_names an ON an.application_number = mr.application_number
    LEFT JOIN recent_assignees ra ON ra.application_number = mr.application_number
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


@router.get("/entity-statuses")
def list_entity_statuses() -> dict:
    """Return distinct entity_status values from maintenance_fee_events_v2."""
    sql = f"""
    SELECT DISTINCT entity_status
    FROM `{settings.maintenance_table}`
    WHERE entity_status IS NOT NULL
    ORDER BY entity_status
    """
    rows = bq_service.run_query(sql)
    return {"statuses": [row["entity_status"] for row in rows]}
