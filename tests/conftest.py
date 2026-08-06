"""
Shared pytest fixtures for the pdf_engine test suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pdf_engine.parser import CreditReport, parse_credit_report

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_INPUT_PATH = REPO_ROOT / "input" / "crif_response.json"


@pytest.fixture(scope="session")
def raw_crif_response() -> dict[str, Any]:
    """The real CRIF Highmark sample payload shipped with this repo."""
    with open(SAMPLE_INPUT_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def parsed_report(raw_crif_response: dict[str, Any]) -> CreditReport:
    """The normalized ``CreditReport`` parsed from the real sample payload."""
    return parse_credit_report(raw_crif_response)
