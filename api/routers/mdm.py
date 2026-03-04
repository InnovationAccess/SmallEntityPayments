"""MDM router – UI-driven entity name normalisation endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, status

from api.models.schemas import EntityMergeRequest, EntitySearchRequest, NormalizedEntity
from api.services.bigquery_service import bq_service

router = APIRouter(prefix="/mdm", tags=["MDM"])


@router.post("/search", response_model=List[NormalizedEntity], summary="Search normalized entities")
def search_entities(request: EntitySearchRequest) -> List[NormalizedEntity]:
    """
    Search the MDM canonical entity table using exact name matching and/or
    geographic cross-referencing (city / state / country).

    At least one filter must be provided.
    """
    if not any([request.name, request.city, request.state, request.country]):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of name, city, state, or country must be provided.",
        )
    rows = bq_service.search_entities(
        name=request.name,
        city=request.city,
        state=request.state,
        country=request.country,
    )
    return [NormalizedEntity(**row) for row in rows]


@router.post(
    "/merge",
    status_code=status.HTTP_200_OK,
    summary="Create or update a canonical entity mapping",
)
def merge_entity(request: EntityMergeRequest) -> dict:
    """
    Insert or update a canonical entity record.  Aliases (raw / variant names)
    are stored alongside geographic attributes so downstream queries can map
    any raw applicant name to the canonical form.
    """
    bq_service.upsert_entity(
        canonical_name=request.canonical_name,
        aliases=request.aliases,
        city=request.city,
        state=request.state,
        country=request.country,
        entity_type=request.entity_type,
    )
    return {"status": "ok", "canonical_name": request.canonical_name}
