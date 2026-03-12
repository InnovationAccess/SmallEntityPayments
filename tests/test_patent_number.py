"""Tests for utils/patent_number.py — patent number normalization."""

import pytest
from utils.patent_number import normalize_patent_number, normalize_doc_number


class TestNormalizePatentNumber:
    """Test normalize_patent_number with various input formats."""

    @pytest.mark.parametrize("raw, expected", [
        # Standard utility patents
        ("7654321", "7654321"),
        ("10123456", "10123456"),
        ("12564117", "12564117"),
        # US prefix stripping
        ("US7654321", "7654321"),
        ("US10123456", "10123456"),
        # Commas
        ("US7,654,321", "7654321"),
        ("7,654,321", "7654321"),
        # Kind code stripping
        ("US7654321B2", "7654321"),
        ("US12564117B2", "12564117"),
        ("7654321B1", "7654321"),
        ("7654321A", "7654321"),
        # Design patents
        ("D987654", "D987654"),
        ("D192388S", "D192388"),
        ("USD987654", "D987654"),
        # Reissue patents
        ("RE49123", "RE49123"),
        ("RE49123E1", "RE49123"),
        # Plant patents
        ("PP33456", "PP33456"),
        # Statutory invention registrations
        ("H001234", "H1234"),
        # Leading zeros
        ("0007654321", "7654321"),
        # Edge cases
        ("", None),
        (None, None),
    ])
    def test_normalization(self, raw, expected):
        assert normalize_patent_number(raw) == expected

    def test_whitespace_handling(self):
        assert normalize_patent_number("  7654321  ") == "7654321"
        assert normalize_patent_number("  US7654321  ") == "7654321"

    def test_case_insensitivity(self):
        assert normalize_patent_number("us7654321") == "7654321"
        assert normalize_patent_number("re49123") == "RE49123"


class TestNormalizeDocNumber:
    """Test normalize_doc_number for citation data."""

    def test_us_document(self):
        assert normalize_doc_number("7654321", "US") == "7654321"

    def test_non_us_returns_none(self):
        assert normalize_doc_number("EP1234567", "EP") is None
        assert normalize_doc_number("JP2020123456", "JP") is None

    def test_default_country_is_us(self):
        assert normalize_doc_number("7654321") == "7654321"
