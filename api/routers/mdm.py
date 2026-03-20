"""MDM Name Normalization router – endpoints for the workspace in Tab 1."""

from __future__ import annotations

import re
from typing import List, Tuple

from fastapi import APIRouter, HTTPException, status

from fastapi import Query as QueryParam

from api.models.schemas import (
    Address,
    MDMAddressRequest,
    MDMAddressSearchRequest,
    MDMAssociateRequest,
    MDMDeleteRequest,
    MDMResolveResult,
    MDMSearchRequest,
    MDMSearchResult,
)
from api.services.bigquery_service import bq_service

router = APIRouter(prefix="/mdm", tags=["MDM"])


# ------------------------------------------------------------------
# Boolean query parser
# ------------------------------------------------------------------

def _parse_boolean_query(query: str) -> Tuple[List[str], List[str]]:
    """Parse a boolean search expression into AND terms and NOT terms.

    Syntax:
      +  = AND (terms separated by +)
      -  = NOT (prefix a term with -)
      *  = wildcard (translated to % for SQL LIKE)

    Without *, a term is an exact (case-insensitive) match.
    With *, the * positions become SQL % wildcards.

    Examples:
      "etri"            → and_terms=["etri"], not_terms=[]          (exact match)
      "GOOG*"           → and_terms=["%GOOG%"], not_terms=[]        (contains GOOG…)
      "*etri*"          → and_terms=["%etri%"], not_terms=[]         (contains etri)
      "+KOR*+INS*"      → and_terms=["%KOR%", "%INS%"], not_terms=[]  (contains KOR AND INS)
      "+APPLE+-INC"     → and_terms=["APPLE"], not_terms=["%INC%"]
    """
    and_terms: List[str] = []
    not_terms: List[str] = []

    # Normalize: treat + as a separator (like space), then split on whitespace.
    # This handles both "+term1+term2" and "+term1 +term2 -term3" syntax.
    parts = query.replace("+", " ").split()

    for part in parts:
        if not part:
            continue

        is_not = part.startswith("-")
        if is_not:
            part = part[1:]

        if not part:
            continue

        # Replace * with % for SQL LIKE.
        term = part.replace("*", "%")

        # Auto-wrap with % for true substring matching:
        # "KOR%" (starts-with) → "%KOR%" (contains KOR…)
        # Without this, "+KOR* +INS*" would require the name to
        # START WITH both KOR and INS, which is impossible.
        if "%" in term:
            if not term.startswith("%"):
                term = "%" + term
            if not term.endswith("%"):
                term = term + "%"

        if is_not:
            not_terms.append(term)
        else:
            and_terms.append(term)

    return and_terms, not_terms


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/search", response_model=List[MDMSearchResult])
def search_entity_names(req: MDMSearchRequest) -> List[MDMSearchResult]:
    """Boolean search for entity names across both patent tables."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    and_terms, not_terms = _parse_boolean_query(req.query)
    if not and_terms and not not_terms:
        raise HTTPException(status_code=400, detail="No valid search terms found.")

    rows = bq_service.search_entity_names(and_terms, not_terms)
    return [MDMSearchResult(**row) for row in rows]


@router.post("/associate")
def associate_names(req: MDMAssociateRequest) -> dict:
    """Create associations between names and a representative."""
    if not req.representative_name.strip():
        raise HTTPException(status_code=400, detail="Representative name is required.")
    if not req.associated_names:
        raise HTTPException(status_code=400, detail="At least one name to associate is required.")

    count = bq_service.associate_names(
        req.representative_name.strip(),
        [n.strip() for n in req.associated_names if n.strip()],
    )
    return {"status": "ok", "representative_name": req.representative_name, "count": count}


@router.delete("/associate")
def delete_association(req: MDMDeleteRequest) -> dict:
    """Remove an association. If the name is a representative, un-associate all."""
    if not req.associated_name.strip():
        raise HTTPException(status_code=400, detail="Associated name is required.")

    result = bq_service.delete_association(req.associated_name.strip())
    return {"status": "ok", **result}


@router.post("/addresses", response_model=List[Address])
def get_addresses(req: MDMAddressRequest) -> List[Address]:
    """Return unique addresses for an entity name."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Entity name is required.")

    rows = bq_service.get_addresses(req.name.strip())
    return [Address(**row) for row in rows]


@router.post("/search-by-address", response_model=List[MDMSearchResult])
def search_by_address(req: MDMAddressSearchRequest) -> List[MDMSearchResult]:
    """Find entity names matching the given addresses."""
    if not req.addresses:
        raise HTTPException(status_code=400, detail="At least one address is required.")

    addr_dicts = [a.model_dump() for a in req.addresses]
    rows = bq_service.search_by_address(addr_dicts)
    return [MDMSearchResult(**row) for row in rows]


@router.get("/resolve", response_model=MDMResolveResult)
def resolve_name(name: str = QueryParam(..., description="Entity name to resolve")) -> MDMResolveResult:
    """Resolve an entity name to its representative and all associated variants.

    Checks the name_unification table. If the name has been normalized,
    returns the representative name and all names in that group.
    If not normalized, returns just the original name.
    """
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name parameter is required.")

    all_names = bq_service.expand_name_for_query(name.strip())
    is_unified = len(all_names) > 1

    # Determine the representative name (the canonical name for this group)
    representative = None
    if is_unified:
        # expand_name_for_query returns associated_names; look up the representative
        from google.cloud import bigquery as bq
        from api.config import settings

        rep_sql = f"""
        SELECT representative_name
        FROM `{settings.unification_table}`
        WHERE LOWER(associated_name) = LOWER(@name)
        LIMIT 1
        """
        rep_params = [bq.ScalarQueryParameter("name", "STRING", name.strip())]
        rep_rows = bq_service.run_query(rep_sql, rep_params)
        if rep_rows:
            representative = rep_rows[0]["representative_name"]

    return MDMResolveResult(
        input_name=name.strip(),
        representative_name=representative,
        all_names=all_names,
        is_unified=is_unified,
    )
