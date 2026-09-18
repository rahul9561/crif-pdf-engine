"""
pdf_engine.sections.employment
=================================

Renders the "Employment Details" section of ``docs/sample_report.pdf``:
one row per reported employment record, sourced from
``CreditReport.employment_details``.

CRIF reports one employment record per contributing institution/period,
so an applicant commonly has more than one (each with its own First/Last
Reported date and Source Indicator) -- this module renders every record
the parser found, not just the first, and the row count is unbounded by
construction (however many records there are, the table simply has that
many rows).
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Spacer

from .. import constants as c
from .. import helpers as h
from ..parser import CreditReport, EmploymentDetail

__all__ = ["render"]

_HEADERS = ["Occupation", "First Reported", "Last Reported", "Type", "Source Indicator"]


def _format_row(employment: EmploymentDetail) -> list[str]:
    """Formats one ``EmploymentDetail`` as a display row matching ``_HEADERS``."""
    return [
        h.safe_text(employment.occupation),
        h.safe_date(employment.first_reported),
        h.safe_date(employment.last_reported),
        h.safe_text(employment.acct_type),
        h.safe_text(employment.source_indicator),
    ]


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "Employment Details" section to ``story``.

    Does nothing if the parsed report carries no employment records at
    all, rather than rendering an empty table.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    records = report.employment_details
    if not records:
        return

    rows = [_format_row(record) for record in records]
    table = h.create_data_table(_HEADERS, rows, zebra=len(rows) > 1)
    story.append(
        KeepTogether([h.create_section_header("Employment Details"), Spacer(1, c.SPACE_XS), table])
    )
    story.append(Spacer(1, c.SPACE_MD))
