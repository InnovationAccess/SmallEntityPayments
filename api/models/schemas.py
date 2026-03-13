"""Pydantic schemas for request and response models."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Patent records (v2 flat schema — no nested arrays)
# ---------------------------------------------------------------------------

class PatentRecord(BaseModel):
    application_number: Optional[str] = None
    patent_number: Optional[str] = None
    invention_title: Optional[str] = None
    filing_date: Optional[str] = None
    grant_date: Optional[str] = None
    entity_status: Optional[str] = None
    small_entity_indicator: Optional[bool] = None
    application_type: Optional[str] = None
    application_status: Optional[str] = None
    first_inventor_name: Optional[str] = None
    first_applicant_name: Optional[str] = None
    examiner_name: Optional[str] = None
    group_art_unit: Optional[str] = None


class AssignmentRecord(BaseModel):
    reel_frame: Optional[str] = None
    doc_number: Optional[str] = None
    recorded_date: Optional[str] = None
    conveyance_text: Optional[str] = None
    assignor_name: Optional[str] = None
    assignee_name: Optional[str] = None
    assignee_city: Optional[str] = None
    assignee_state: Optional[str] = None
    assignee_country: Optional[str] = None


class MaintenanceFeeRecord(BaseModel):
    patent_number: Optional[str] = None
    application_number: Optional[str] = None
    event_code: Optional[str] = None
    event_date: Optional[str] = None
    entity_status: Optional[str] = None


# ---------------------------------------------------------------------------
# MDM – Name Normalization
# ---------------------------------------------------------------------------

class MDMSearchRequest(BaseModel):
    query: str = Field(..., description="Boolean search expression: + (AND), - (NOT), * (wildcard)")


class MDMSearchResult(BaseModel):
    raw_name: str
    frequency: int
    representative_name: Optional[str] = None


class MDMAssociateRequest(BaseModel):
    representative_name: str = Field(..., description="The name to serve as representative")
    associated_names: List[str] = Field(..., description="Names to associate with the representative")


class MDMResolveResult(BaseModel):
    input_name: str
    representative_name: Optional[str] = None
    all_names: List[str]
    is_unified: bool = False


class MDMDeleteRequest(BaseModel):
    associated_name: str = Field(..., description="The name to un-associate")


class Address(BaseModel):
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None


class MDMAddressRequest(BaseModel):
    name: str = Field(..., description="Entity name to look up addresses for")


class MDMAddressSearchRequest(BaseModel):
    addresses: List[Address] = Field(..., description="Addresses to search entities by")


# ---------------------------------------------------------------------------
# Manual Boolean Query Builder
# ---------------------------------------------------------------------------

class QueryCondition(BaseModel):
    field: str = Field(..., description="Column name (e.g. invention_title, applicant_name)")
    operator: str = Field(..., description="Comparison operator: CONTAINS, EQUALS, STARTS_WITH, ENDS_WITH")
    value: str


class BooleanQuery(BaseModel):
    conditions: List[QueryCondition] = Field(..., min_length=1)
    logic: str = Field("AND", description="Top-level logic joining conditions: AND | OR")
    limit: int = Field(100, ge=1)
    tables: List[str] = Field(
        default=["patent_file_wrapper"],
        description="Tables to query against",
    )


class QueryResult(BaseModel):
    total_rows: int
    rows: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# AI / Gemini assistant
# ---------------------------------------------------------------------------

class AIChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'ai'")
    content: str


class AIQueryRequest(BaseModel):
    prompt: str = Field(..., description="Natural-language question about the patent data")
    history: List[AIChatMessage] = Field(
        default=[],
        description="Previous conversation messages for context",
    )


class AIQueryResponse(BaseModel):
    generated_sql: Optional[str] = None
    answer: str
    rows: List[Dict[str, Any]] = []
