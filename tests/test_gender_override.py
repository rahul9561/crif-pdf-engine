"""
Tests for the caller-supplied gender override (``manual_gender``).

The CRIF payload's gender can be wrong, so a non-blank ``manual_gender``
must always win -- through parsing and all the way into the rendered
PDF -- while ``None`` / blank values fall back to the payload's gender.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from pdf_engine import generate_report, parse_credit_report

_MISSING = object()


def _with_json_gender(raw: dict[str, Any], gender: Any) -> dict[str, Any]:
    """Deep-copies the B2C sample with APPLICANT-SEGMENT.GENDER set (or removed)."""
    payload = copy.deepcopy(raw)
    applicant = payload["data"]["result_json"]["parsed_data"]["B2C-REPORT"]["REQUEST-DATA"][
        "APPLICANT-SEGMENT"
    ]
    if gender is _MISSING:
        applicant.pop("GENDER", None)
    else:
        applicant["GENDER"] = gender
    return payload


def _pdf_gender(pdf_path: Path) -> str | None:
    """
    Returns the value printed after "Gender:" in the rendered PDF, or
    ``None`` when no "Gender:" row was rendered (the customer section
    omits empty rows).
    """
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)
    match = re.search(r"Gender:\s*([A-Za-z]+)", text)
    return match.group(1) if match else None


@pytest.mark.parametrize(
    ("json_gender", "manual_gender", "expected"),
    [
        ("Male", "Female", "Female"),
        ("Female", "Male", "Male"),
        ("Male", None, "Male"),
        ("Female", "", "Female"),
        ("Male", "   ", "Male"),
        (_MISSING, None, ""),
        ("", "  ", ""),
    ],
    ids=[
        "json_male_caller_female",
        "json_female_caller_male",
        "caller_omits",
        "caller_empty",
        "caller_whitespace",
        "both_missing",
        "both_blank",
    ],
)
class TestGenderOverride:
    def test_parsed_identity(self, raw_b2c_response, json_gender, manual_gender, expected):
        payload = _with_json_gender(raw_b2c_response, json_gender)
        report = parse_credit_report(payload, manual_gender=manual_gender)
        assert report.customer_identity.gender == expected

    def test_rendered_pdf(
        self, raw_b2c_response, tmp_path: Path, json_gender, manual_gender, expected
    ):
        payload = _with_json_gender(raw_b2c_response, json_gender)
        pdf_path = generate_report(payload, tmp_path / "report.pdf", manual_gender=manual_gender)
        assert _pdf_gender(pdf_path) == (expected or None)


def test_omitting_argument_uses_json_gender(raw_b2c_response, tmp_path: Path):
    # Existing callers that never pass manual_gender keep working unchanged.
    payload = _with_json_gender(raw_b2c_response, "Female")
    assert parse_credit_report(payload).customer_identity.gender == "Female"
    assert _pdf_gender(generate_report(payload, tmp_path / "report.pdf")) == "Female"


def test_override_is_used_verbatim_not_normalized(raw_b2c_response):
    payload = _with_json_gender(raw_b2c_response, "Male")
    report = parse_credit_report(payload, manual_gender="  F  ")
    assert report.customer_identity.gender == "F"


def test_override_survives_manual_dob(raw_b2c_response):
    payload = _with_json_gender(raw_b2c_response, "Male")
    report = parse_credit_report(payload, manual_dob="01-01-1990", manual_gender="Female")
    assert report.customer_identity.gender == "Female"
