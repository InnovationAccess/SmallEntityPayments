"""Pydantic schemas for request and response models."""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------

class Applicant(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    entity_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Patent file wrapper
# ---------------------------------------------------------------------------

class PatentRecord(BaseModel):
    patent_number: str
    invention_title: Optional[str] = None
    grant_date: Optional[str] = None
    applicants: List[Applicant] = []


# ---------------------------------------------------------------------------
# MDM – Normalized entities
# ---------------------------------------------------------------------------

class NormalizedEntity(BaseModel):
    canonical_name: str
    aliases: List[str] = []
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    entity_type: Optional[str] = None


class EntitySearchRequest(BaseModel):
    name: Optional[str] = Field(None, description="Exact entity name to search for")
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None


class EntityMergeRequest(BaseModel):
    canonical_name: str = Field(..., description="The authoritative name to keep")
    aliases: List[str] = Field(..., description="Raw / variant names to map to the canonical name")
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    entity_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Manual Boolean Query Builder
# ---------------------------------------------------------------------------

class QueryCondition(BaseModel):
    field: str = Field(..., description="Column name (e.g. invention_title, applicants.name)")
    operator: str = Field(..., description="Comparison operator: CONTAINS, EQUALS, STARTS_WITH, ENDS_WITH")
    value: str


class BooleanQuery(BaseModel):
    conditions: List[QueryCondition] = Field(..., min_length=1)
    logic: str = Field("AND", description="Top-level logic joining conditions: AND | OR")
    limit: int = Field(100, ge=1, le=1000)


class QueryResult(BaseModel):
    total_rows: int
    rows: List[PatentRecord]


# ---------------------------------------------------------------------------
# AI / Gemini assistant
# ---------------------------------------------------------------------------

class AIQueryRequest(BaseModel):
    prompt: str = Field(..., description="Natural-language question about the patent data")


class AIQueryResponse(BaseModel):
    generated_sql: Optional[str] = None
    answer: str
    rows: List[PatentRecord] = []
