"""Tests for etl/parse_assignments_xml_v4.py — normalized assignment parser."""

import gzip
import json
import os
import tempfile

import pytest
from xml.etree.ElementTree import Element, SubElement, tostring

from etl.parse_assignments_xml_v4 import parse_assignment, parse_input


def _build_assignment_xml(
    reel_no="12345",
    frame_no="678",
    recorded_date="20200115",
    conveyance_text="ASSIGNMENT OF ASSIGNORS INTEREST",
    assignors=None,
    assignees=None,
    documents=None,
):
    """Build a <patent-assignment> XML element for testing."""
    pa = Element("patent-assignment")

    # Assignment record
    rec = SubElement(pa, "assignment-record")
    _add_text(rec, "reel-no", reel_no)
    _add_text(rec, "frame-no", frame_no)
    rd = SubElement(rec, "recorded-date")
    _add_text(rd, "date", recorded_date)
    _add_text(rec, "conveyance-text", conveyance_text)

    # Assignors
    if assignors is None:
        assignors = [("SMITH, JOHN", "20200110")]
    assignors_elem = SubElement(pa, "patent-assignors")
    for name, exec_date in assignors:
        ao = SubElement(assignors_elem, "patent-assignor")
        _add_text(ao, "name", name)
        ed = SubElement(ao, "execution-date")
        _add_text(ed, "date", exec_date)

    # Assignees
    if assignees is None:
        assignees = [("ACME CORP", "New York", "NY", "US")]
    assignees_elem = SubElement(pa, "patent-assignees")
    for name, city, state, country in assignees:
        ae = SubElement(assignees_elem, "patent-assignee")
        _add_text(ae, "name", name)
        _add_text(ae, "city", city)
        _add_text(ae, "state", state)
        _add_text(ae, "country-name", country)

    # Documents
    if documents is None:
        documents = [
            {"kind": "X0", "number": "16123456", "date": "20190301",
             "title": "Test Invention"},
        ]
    props_elem = SubElement(pa, "patent-properties")
    for doc in documents:
        prop = SubElement(props_elem, "patent-property")
        _add_text(prop, "invention-title", doc.get("title", ""))
        doc_id = SubElement(prop, "document-id")
        _add_text(doc_id, "country", "US")
        _add_text(doc_id, "doc-number", doc["number"])
        _add_text(doc_id, "kind", doc["kind"])
        _add_text(doc_id, "date", doc.get("date", ""))

    return pa


def _add_text(parent, tag, text):
    """Add a child element with text."""
    child = SubElement(parent, tag)
    child.text = str(text) if text else ""
    return child


class TestParseAssignment:
    """Test the parse_assignment function with mock XML elements."""

    def test_basic_assignment(self):
        elem = _build_assignment_xml()
        result = parse_assignment(elem, "test.xml", min_year=2006)

        assert result is not None
        assert result["record"]["reel_frame"] == "12345/678"
        assert result["record"]["recorded_date"] == "2020-01-15"
        assert result["record"]["conveyance_type"] == "ASSIGNMENT"
        assert len(result["assignors"]) == 1
        assert result["assignors"][0]["assignor_name"] == "SMITH, JOHN"
        assert len(result["assignees"]) == 1
        assert result["assignees"][0]["assignee_name"] == "ACME CORP"
        assert result["assignees"][0]["assignee_city"] == "New York"
        assert len(result["documents"]) == 1
        assert result["documents"][0]["application_number"] == "16123456"

    def test_multiple_assignors(self):
        elem = _build_assignment_xml(
            assignors=[("SMITH, JOHN", "20200110"), ("DOE, JANE", "20200112")]
        )
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert len(result["assignors"]) == 2
        assert result["assignors"][1]["assignor_name"] == "DOE, JANE"

    def test_multiple_assignees(self):
        elem = _build_assignment_xml(
            assignees=[
                ("ACME CORP", "New York", "NY", "US"),
                ("BETA INC", "Boston", "MA", "US"),
            ]
        )
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert len(result["assignees"]) == 2
        assert result["assignees"][1]["assignee_name"] == "BETA INC"

    def test_multiple_documents(self):
        elem = _build_assignment_xml(
            documents=[
                {"kind": "X0", "number": "16123456", "date": "20190301",
                 "title": "Invention A"},
                {"kind": "B2", "number": "10555555", "date": "20200101",
                 "title": "Invention A"},
            ]
        )
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert len(result["documents"]) == 2
        # First doc is application
        assert result["documents"][0]["application_number"] == "16123456"
        # Second doc is grant
        assert result["documents"][1]["patent_number"] == "10555555"

    def test_reel_frame_consistency(self):
        """All output tables should have the same reel_frame."""
        elem = _build_assignment_xml()
        result = parse_assignment(elem, "test.xml", min_year=2006)
        rf = result["record"]["reel_frame"]
        assert rf == "12345/678"
        for ao in result["assignors"]:
            assert ao["reel_frame"] == rf
        for ae in result["assignees"]:
            assert ae["reel_frame"] == rf
        for doc in result["documents"]:
            assert doc["reel_frame"] == rf

    def test_min_year_filter(self):
        elem = _build_assignment_xml(recorded_date="20050601")
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert result is None

    def test_missing_reel_frame_returns_none(self):
        elem = _build_assignment_xml(reel_no="", frame_no="")
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert result is None

    def test_missing_recorded_date_returns_none(self):
        elem = _build_assignment_xml(recorded_date="")
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert result is None

    def test_security_interest_classification(self):
        elem = _build_assignment_xml(
            conveyance_text="SECURITY AGREEMENT"
        )
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert result["record"]["conveyance_type"] == "SECURITY_INTEREST"

    def test_no_assignors_emits_null_row(self):
        """Even with no assignors, a row with None values is emitted."""
        elem = _build_assignment_xml(assignors=[])
        # Remove the assignors element
        for child in list(elem):
            if child.tag == "patent-assignors":
                elem.remove(child)
        result = parse_assignment(elem, "test.xml", min_year=2006)
        assert len(result["assignors"]) == 1
        assert result["assignors"][0]["assignor_name"] is None


class TestParseInput:
    """Test the full parse_input pipeline with a minimal XML file."""

    def test_parse_bare_xml(self):
        """Parse a bare XML file and verify 4 output files are created."""
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<patent-assignments>
  <patent-assignment>
    <assignment-record>
      <reel-no>99999</reel-no>
      <frame-no>1</frame-no>
      <recorded-date><date>20200601</date></recorded-date>
      <conveyance-text>ASSIGNMENT OF ASSIGNORS INTEREST</conveyance-text>
    </assignment-record>
    <patent-assignors>
      <patent-assignor>
        <name>TEST INVENTOR</name>
        <execution-date><date>20200530</date></execution-date>
      </patent-assignor>
    </patent-assignors>
    <patent-assignees>
      <patent-assignee>
        <name>TEST COMPANY</name>
        <city>Testville</city>
        <state>CA</state>
        <country-name>US</country-name>
      </patent-assignee>
    </patent-assignees>
    <patent-properties>
      <patent-property>
        <invention-title>Test Widget</invention-title>
        <document-id>
          <country>US</country>
          <doc-number>16999999</doc-number>
          <kind>X0</kind>
          <date>20200101</date>
        </document-id>
      </patent-property>
    </patent-properties>
  </patent-assignment>
</patent-assignments>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            xml_path = os.path.join(tmpdir, "test.xml")
            with open(xml_path, "wb") as f:
                f.write(xml_content)

            output_dir = os.path.join(tmpdir, "output")
            counts = parse_input(xml_path, output_dir, min_year=2006)

            assert counts["records"] == 1
            assert counts["assignors"] == 1
            assert counts["assignees"] == 1
            assert counts["documents"] == 1

            # Verify output files exist and contain valid JSONL
            for prefix in ("records", "assignors", "assignees", "documents"):
                pattern_file = os.path.join(output_dir, f"{prefix}_test.jsonl.gz")
                assert os.path.exists(pattern_file), f"Missing {prefix} output file"

                with gzip.open(pattern_file, "rt") as gf:
                    lines = gf.readlines()
                    assert len(lines) == 1
                    data = json.loads(lines[0])
                    assert data["reel_frame"] == "99999/1"

    def test_parse_zip_file(self):
        """Parse a ZIP containing XML and verify output."""
        import zipfile

        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<patent-assignments>
  <patent-assignment>
    <assignment-record>
      <reel-no>88888</reel-no>
      <frame-no>2</frame-no>
      <recorded-date><date>20210701</date></recorded-date>
      <conveyance-text>MERGER</conveyance-text>
    </assignment-record>
    <patent-assignors>
      <patent-assignor>
        <name>OLD COMPANY</name>
        <execution-date><date>20210615</date></execution-date>
      </patent-assignor>
    </patent-assignors>
    <patent-assignees>
      <patent-assignee>
        <name>NEW COMPANY</name>
        <city>Austin</city>
        <state>TX</state>
        <country-name>US</country-name>
      </patent-assignee>
    </patent-assignees>
    <patent-properties>
      <patent-property>
        <invention-title>Merged Widget</invention-title>
        <document-id>
          <country>US</country>
          <doc-number>15888888</doc-number>
          <kind>X0</kind>
          <date>20190301</date>
        </document-id>
        <document-id>
          <country>US</country>
          <doc-number>10888888</doc-number>
          <kind>B2</kind>
          <date>20210101</date>
        </document-id>
      </patent-property>
    </patent-properties>
  </patent-assignment>
</patent-assignments>"""

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "test.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("test_data.xml", xml_content)

            output_dir = os.path.join(tmpdir, "output")
            counts = parse_input(zip_path, output_dir, min_year=2006)

            assert counts["records"] == 1
            assert counts["documents"] == 1

            # Verify the document has both application_number and patent_number
            doc_file = os.path.join(output_dir, "documents_test.jsonl.gz")
            with gzip.open(doc_file, "rt") as gf:
                doc = json.loads(gf.readline())
                assert doc["application_number"] == "15888888"
                assert doc["patent_number"] == "10888888"
                assert doc["reel_frame"] == "88888/2"
