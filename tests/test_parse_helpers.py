"""Tests for ETL parsing helper functions shared across v3/v4 parsers."""

import pytest
from etl.parse_assignments_xml_v3 import parse_date, extract_text, _classify_doc_id
from xml.etree.ElementTree import Element, SubElement


class TestParseDate:
    """Test date parsing from YYYYMMDD to YYYY-MM-DD."""

    @pytest.mark.parametrize("raw, expected", [
        ("20250115", "2025-01-15"),
        ("20060301", "2006-03-01"),
        ("17000101", "1700-01-01"),
        ("21001231", "2100-12-31"),
    ])
    def test_valid_dates(self, raw, expected):
        assert parse_date(raw) == expected

    @pytest.mark.parametrize("raw", [
        "",
        None,
        "2025",
        "not-a-date",
        "2025011",    # too short
        "202501155",  # too long
    ])
    def test_invalid_dates_return_none(self, raw):
        assert parse_date(raw) is None

    def test_year_too_low_rejected(self):
        assert parse_date("00000101") is None
        assert parse_date("16990101") is None

    def test_year_too_high_rejected(self):
        assert parse_date("21010101") is None
        assert parse_date("99991231") is None

    def test_whitespace_stripped(self):
        assert parse_date("  20250115  ") == "2025-01-15"


class TestExtractText:
    """Test XML text extraction helper."""

    def test_basic_extraction(self):
        root = Element("root")
        child = SubElement(root, "name")
        child.text = "ACME CORP"
        assert extract_text(root, "name") == "ACME CORP"

    def test_missing_element_returns_empty(self):
        root = Element("root")
        assert extract_text(root, "missing") == ""

    def test_none_text_returns_empty(self):
        root = Element("root")
        SubElement(root, "empty")  # no text
        assert extract_text(root, "empty") == ""

    def test_whitespace_stripped(self):
        root = Element("root")
        child = SubElement(root, "name")
        child.text = "  ACME CORP  "
        assert extract_text(root, "name") == "ACME CORP"

    def test_nested_path(self):
        root = Element("root")
        parent = SubElement(root, "recorded-date")
        child = SubElement(parent, "date")
        child.text = "20250115"
        assert extract_text(root, "recorded-date/date") == "20250115"


class TestClassifyDocId:
    """Test document ID type classification."""

    def test_application_kind_x0(self):
        assert _classify_doc_id("X0", "12345678") == "application"

    def test_grant_kind_b1(self):
        assert _classify_doc_id("B1", "7654321") == "grant"

    def test_grant_kind_b2(self):
        assert _classify_doc_id("B2", "7654321") == "grant"

    def test_grant_kind_s1(self):
        assert _classify_doc_id("S1", "D987654") == "grant"

    def test_publication_kind_a2(self):
        assert _classify_doc_id("A2", "20050123456") == "publication"

    def test_a1_ambiguity_short_is_grant(self):
        """7-digit number with A1 = pre-2001 utility grant."""
        assert _classify_doc_id("A1", "7654321") == "grant"

    def test_a1_ambiguity_long_is_publication(self):
        """10+ digit number with A1 = post-2001 publication."""
        assert _classify_doc_id("A1", "20050123456") == "publication"

    def test_empty_kind_defaults(self):
        # Varies by implementation — just ensure no crash
        result = _classify_doc_id("", "7654321")
        assert result in ("application", "publication", "grant")
