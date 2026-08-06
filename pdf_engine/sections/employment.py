"""
pdf_engine.sections.employment
=================================

Renders the "Employment Details" section of ``docs/sample_report.pdf``:
a single-row table of the applicant's reported occupation.

The reference PDF's row shows the occupation, a reported date repeated
under both "First Reported" and "Last Reported", and the associated
account type under "Type" (e.g. "Loan Against Bank Deposits"). CRIF's raw
payload only reports one date per employment record (see
``EmploymentDetails.date_reported``) and no separate "Source Indicator"
value, so this module fills both date columns from that single value and
leaves "Source Indicator" blank rather than fabricating data the parser
does not carry.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Spacer

from .. import constants as c
from .. import helpers as h
from ..parser import CreditReport

__all__ = ["render"]

_HEADERS = ["Occupation", "First Reported", "Last Reported", "Type", "Source Indicator"]


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "Employment Details" section to ``story``.

    Does nothing if the parsed report carries no employment data at all
    (no occupation, no account type, and no reported date), rather than
    rendering an empty table.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    employment = report.employment_details
    if not employment.occupation and not employment.acct_type and employment.date_reported is None:
        return

    reported = h.safe_date(employment.date_reported)
    row = [
        h.safe_text(employment.occupation),
        reported,
        reported,
        h.safe_text(employment.acct_type),
        "",
    ]

    table = h.create_data_table(_HEADERS, [row], zebra=False)
    # Always exactly one row -- safe to keep the whole block together.
    story.append(
        KeepTogether([h.create_section_header("Employment Details"), Spacer(1, c.SPACE_XS), table])
    )
    story.append(Spacer(1, c.SPACE_MD))
