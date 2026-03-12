"""Tests for utils/conveyance_classifier.py — assignment type classification."""

import pytest
from utils.conveyance_classifier import classify_conveyance


class TestClassifyConveyance:
    """Test classification of conveyance text into standard categories."""

    @pytest.mark.parametrize("text, expected", [
        # Standard assignments
        ("ASSIGNMENT OF ASSIGNORS INTEREST", "ASSIGNMENT"),
        ("ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS).", "ASSIGNMENT"),
        ("NUNC PRO TUNC ASSIGNMENT", "CORRECTION"),  # correction takes priority
        # Security interests
        ("SECURITY AGREEMENT", "SECURITY_INTEREST"),
        ("SECURITY INTEREST", "SECURITY_INTEREST"),
        ("COLLATERAL ASSIGNMENT", "SECURITY_INTEREST"),
        ("PATENT PLEDGE AGREEMENT", "SECURITY_INTEREST"),
        # Releases
        ("RELEASE BY SECURED PARTY", "RELEASE"),
        ("TERMINATION OF SECURITY INTEREST", "RELEASE"),
        ("LIEN RELEASE", "RELEASE"),
        ("RELEASE OF SECURITY INTEREST", "RELEASE"),
        # Mergers
        ("MERGER AND CHANGE OF NAME", "MERGER"),
        ("CHANGE OF NAME", "MERGER"),
        ("CERTIFICATE OF MERGER", "MERGER"),
        # Licenses
        ("LICENSE AGREEMENT", "LICENSE"),
        ("EXCLUSIVE LICENSE", "LICENSE"),
        # Government interest
        ("CONFIRMATORY LICENSE", "LICENSE"),  # LICENSE pattern matches before GOVERNMENT_INTEREST
        ("GOVERNMENT INTEREST ASSIGNMENT", "GOVERNMENT_INTEREST"),
        # Corrections
        ("CORRECTIVE ASSIGNMENT", "CORRECTION"),
        ("CORRECTION OF ERROR", "CORRECTION"),
        # Other / empty
        ("", "OTHER"),
        (None, "OTHER"),
        ("SOME UNUSUAL TEXT", "OTHER"),
    ])
    def test_classification(self, text, expected):
        assert classify_conveyance(text) == expected

    def test_case_insensitivity(self):
        assert classify_conveyance("assignment of assignors interest") == "ASSIGNMENT"
        assert classify_conveyance("Security Agreement") == "SECURITY_INTEREST"

    def test_priority_correction_over_assignment(self):
        """Corrective assignments should be CORRECTION, not ASSIGNMENT."""
        assert classify_conveyance("CORRECTIVE ASSIGNMENT TO CORRECT THE ASSIGNEE") == "CORRECTION"

    def test_priority_release_over_security(self):
        """Release of security interest should be RELEASE, not SECURITY_INTEREST."""
        assert classify_conveyance("RELEASE OF SECURITY INTEREST") == "RELEASE"
