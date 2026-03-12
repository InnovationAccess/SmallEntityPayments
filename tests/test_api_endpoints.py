"""API endpoint integration tests using FastAPI test client.

These tests mock the BigQuery service to avoid hitting the real database.
They verify request/response structure, routing, and error handling.
"""

from unittest.mock import MagicMock, patch
import datetime

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


class TestHealthEndpoint:
    """Test the /health endpoint."""

    def test_health_check(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestQueryFields:
    """Test /query/fields metadata endpoint."""

    def test_list_fields(self):
        resp = client.get("/query/fields")
        assert resp.status_code == 200
        data = resp.json()
        assert "tables" in data
        assert "fields" in data
        assert "operators" in data
        assert "patent_file_wrapper" in data["tables"]
        assert "patent_assignments" in data["tables"]
        assert "maintenance_fee_events" in data["tables"]

    def test_assignment_fields_present(self):
        resp = client.get("/query/fields")
        data = resp.json()
        pa_fields = data["fields"]["patent_assignments"]
        assert "assignee_name" in pa_fields
        assert "assignor_name" in pa_fields
        assert "conveyance_type" in pa_fields
        assert "reel_frame" in pa_fields
        assert "application_number" in pa_fields


class TestAssignmentChainEndpoint:
    """Test /api/assignments/{patent_number}/chain endpoint."""

    @patch("api.routers.assignments.bq_service")
    def test_valid_patent_number(self, mock_bq):
        mock_bq.run_query.return_value = [
            {
                "assignor_execution_date": datetime.date(2020, 1, 15),
                "assignor_name": "INVENTOR, JOHN",
                "conveyance_text": "ASSIGNMENT OF ASSIGNORS INTEREST",
                "assignee_name": "ACME CORP",
                "reel_frame": "12345/678",
                "recorded_date": datetime.date(2020, 1, 20),
            }
        ]

        resp = client.get("/api/assignments/10123456/chain")
        assert resp.status_code == 200
        data = resp.json()
        assert data["patent_number"] == "10123456"
        assert len(data["assignments"]) == 1
        assert data["assignments"][0]["assignor"] == "INVENTOR, JOHN"
        assert data["assignments"][0]["assignee"] == "ACME CORP"

    @patch("api.routers.assignments.bq_service")
    def test_no_assignments_returns_empty(self, mock_bq):
        mock_bq.run_query.return_value = []

        resp = client.get("/api/assignments/99999999/chain")
        assert resp.status_code == 200
        data = resp.json()
        assert data["assignments"] == []

    @patch("api.routers.assignments.bq_service")
    def test_application_number_also_works(self, mock_bq):
        """Application numbers (8+ digits) should also be accepted."""
        mock_bq.run_query.return_value = []

        resp = client.get("/api/assignments/16123456/chain")
        assert resp.status_code == 200


class TestQueryExecute:
    """Test /query/execute endpoint."""

    @patch("api.routers.query.bq_service")
    def test_basic_assignment_query(self, mock_bq):
        mock_bq.run_query.return_value = [
            {
                "patent_number": "10555555",
                "application_number": "16555555",
                "recorded_date": "2020-03-15",
                "assignee_name": "TEST CORP",
                "assignor_name": "INVENTOR, A",
                "applicant_name": "TEST CORP",
                "recent_assignee_name": "TEST CORP",
            }
        ]
        mock_bq.expand_name_for_query.return_value = ["TEST CORP"]

        resp = client.post("/query/execute", json={
            "tables": ["patent_assignments"],
            "conditions": [
                {"field": "assignee_name", "operator": "EQUALS", "value": "TEST CORP"}
            ],
            "logic": "AND",
            "limit": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "total_rows" in data
        assert "rows" in data

    def test_invalid_table_rejected(self):
        resp = client.post("/query/execute", json={
            "tables": ["invalid_table"],
            "conditions": [
                {"field": "patent_number", "operator": "EQUALS", "value": "123"}
            ],
            "logic": "AND",
            "limit": 10,
        })
        assert resp.status_code == 422

    def test_invalid_operator_rejected(self):
        resp = client.post("/query/execute", json={
            "tables": ["patent_file_wrapper"],
            "conditions": [
                {"field": "patent_number", "operator": "DROP_TABLE", "value": "123"}
            ],
            "logic": "AND",
            "limit": 10,
        })
        assert resp.status_code == 422


class TestMDMEndpoints:
    """Test /mdm/* endpoints."""

    @patch("api.routers.mdm.bq_service")
    def test_search(self, mock_bq):
        mock_bq.search_entity_names.return_value = [
            {"raw_name": "ACME CORP", "frequency": 100, "representative_name": None}
        ]

        resp = client.post("/mdm/search", json={"query": "ACME"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert data[0]["raw_name"] == "ACME CORP"
