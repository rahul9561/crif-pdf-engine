"""
Tests for pdf_engine.generator.

Covers story assembly, PDF rendering, and the full generate_report()
pipeline against the real sample payload (input/crif_response.json),
without modifying any generator logic or the resulting PDF layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader
from reportlab.platypus import Flowable

from pdf_engine.generator import build_story, generate_report, render_pdf
from pdf_engine.parser import CreditReport, parse_credit_report

# ---------------------------------------------------------------------------
# build_story
# ---------------------------------------------------------------------------


class TestBuildStory:
    def test_returns_list_of_flowables_for_real_sample(self, parsed_report: CreditReport):
        story = build_story(parsed_report)
        assert isinstance(story, list)
        assert len(story) > 0
        assert all(isinstance(item, Flowable) or hasattr(item, "wrap") for item in story)

    def test_does_not_raise_for_empty_report(self):
        story = build_story(CreditReport())
        assert isinstance(story, list)
        assert len(story) > 0

    def test_does_not_mutate_report(self, raw_crif_response: dict[str, Any]):
        report = parse_credit_report(raw_crif_response)
        account_count_before = len(report.accounts)
        build_story(report)
        assert len(report.accounts) == account_count_before


# ---------------------------------------------------------------------------
# render_pdf
# ---------------------------------------------------------------------------


class TestRenderPdf:
    def test_writes_valid_pdf_file(self, parsed_report: CreditReport, tmp_path: Path):
        story = build_story(parsed_report)
        output_path = tmp_path / "report.pdf"

        result_path = render_pdf(story, output_path)

        assert result_path == output_path.resolve()
        assert result_path.is_file()
        assert result_path.stat().st_size > 0
        assert result_path.read_bytes()[:5] == b"%PDF-"

    def test_creates_missing_parent_directories(self, parsed_report: CreditReport, tmp_path: Path):
        story = build_story(parsed_report)
        output_path = tmp_path / "nested" / "dirs" / "report.pdf"

        result_path = render_pdf(story, output_path)

        assert result_path.is_file()

    def test_produces_multiple_pages_for_real_sample(
        self, parsed_report: CreditReport, tmp_path: Path
    ):
        story = build_story(parsed_report)
        output_path = tmp_path / "report.pdf"
        render_pdf(story, output_path)

        reader = PdfReader(str(output_path))
        # 10 accounts each contributing an Account Information block plus a
        # payment history table, plus the end-of-report appendix on its own
        # page, guarantees more than one page for the real sample.
        assert len(reader.pages) > 1

    def test_does_not_mutate_caller_story_list(self, parsed_report: CreditReport, tmp_path: Path):
        story = build_story(parsed_report)
        original_length = len(story)
        render_pdf(story, tmp_path / "report.pdf")
        assert len(story) == original_length

    def test_sets_document_title(self, parsed_report: CreditReport, tmp_path: Path):
        story = build_story(parsed_report)
        output_path = tmp_path / "report.pdf"
        render_pdf(story, output_path, title="Custom Title")

        reader = PdfReader(str(output_path))
        assert reader.metadata.title == "Custom Title"


# ---------------------------------------------------------------------------
# generate_report -- full pipeline
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_end_to_end_with_real_sample(
        self, raw_crif_response: dict[str, Any], tmp_path: Path
    ):
        output_path = tmp_path / "sample.pdf"

        result_path = generate_report(raw_crif_response, output_path)

        assert result_path.is_file()
        assert result_path.stat().st_size > 0

        reader = PdfReader(str(result_path))
        assert len(reader.pages) > 1
        # Title includes the applicant's name when present in the payload.
        assert "ANAND GOYAL" in (reader.metadata.title or "")

    def test_handles_empty_payload_without_raising(self, tmp_path: Path):
        output_path = tmp_path / "empty.pdf"
        result_path = generate_report({}, output_path)

        assert result_path.is_file()
        assert result_path.stat().st_size > 0

    def test_accepts_str_output_path(self, raw_crif_response: dict[str, Any], tmp_path: Path):
        output_path = str(tmp_path / "str_path.pdf")
        result_path = generate_report(raw_crif_response, output_path)
        assert result_path.is_file()


@pytest.mark.parametrize("bad_payload", [None, "not a dict", [], 42])
def test_generate_report_never_raises_for_malformed_payload(bad_payload, tmp_path: Path):
    output_path = tmp_path / "malformed.pdf"
    result_path = generate_report(bad_payload, output_path)
    assert result_path.is_file()
