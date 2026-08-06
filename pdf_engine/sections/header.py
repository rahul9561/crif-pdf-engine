"""
pdf_engine.sections.header
============================

Renders the report masthead: the CRIF logo alongside the report title and
the applicant's name, matching the top banner of ``docs/sample_report.pdf``
("Credit Information(TM) Report" / "For ANAND GOYAL").

The reference PDF's masthead also shows a CHM reference number,
application ID, and request/issue dates. None of those are present on
``CreditReport`` -- the parser only normalizes the ten sections it was
built for, and that metadata was never part of the schema it reads -- so
this module renders only what the parsed model actually provides (the
applicant's name) rather than inventing placeholder values.
"""

from __future__ import annotations

from reportlab.platypus import Paragraph, Table, TableStyle

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import CreditReport

__all__ = ["render"]

#: Static report title text. Not applicant data -- the report kind never
#: changes, so it is not something ``pdf_engine.parser`` would extract.
_REPORT_TITLE = "Credit Information™ Report"

#: Target rendered height of the logo within the masthead.
_LOGO_HEIGHT = 40


def _build_title_block(report: CreditReport) -> list[Paragraph]:
    """Builds the report title + "For <name>" paragraph stack."""
    title = Paragraph(h.safe_text(_REPORT_TITLE), s.STYLES["ReportTitle"])
    name = h.safe_text(report.customer_identity.name)
    subtitle = Paragraph(f"For {name}" if name else "For -", s.STYLES["Heading"])
    return [title, subtitle]


def render(story: list, report: CreditReport) -> None:
    """
    Appends the report masthead to ``story``.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    logo = h.load_logo(height=_LOGO_HEIGHT)
    title_block = _build_title_block(report)

    if logo is not None:
        masthead_style = TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
        logo_col_width = logo.drawWidth + c.SPACE_MD
        masthead = Table(
            [[logo, title_block]],
            colWidths=[logo_col_width, c.CONTENT_WIDTH - logo_col_width],
        )
        masthead.setStyle(masthead_style)
        story.append(masthead)
    else:
        story.extend(title_block)

    story.append(
        h.horizontal_rule(
            thickness=c.BORDER_WIDTH_STANDARD,
            space_before=c.SPACE_SM,
            space_after=c.SPACE_MD,
        )
    )
