"""
pdf_engine.sections.header
============================

Renders the report masthead: the CRIF logo alongside the report title and
the applicant's name, matching the top banner of ``docs/sample_report.pdf``
("Credit Information(TM) Report" / "For ANAND GOYAL"), followed by a
compact report-metadata line (Report ID, Status, Date of Issue, Date of
Request, Product Type, Product Version) sourced from
``CreditReport.header`` (CRIF's ``HEADER-SEGMENT``).

``CreditReport.header`` is only ever populated for the current B2C-REPORT
payload shape -- the legacy flat shape never reported this metadata -- so
the metadata line is rendered only when at least one of its fields is
present, and individual blank fields within it are omitted rather than
shown as empty.
"""

from __future__ import annotations

from reportlab.platypus import Paragraph, Table, TableStyle

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import CreditReport, ReportHeader

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


def _build_header_info(header: ReportHeader) -> Table | None:
    """
    Builds the compact report-metadata grid (Report ID, Status, Date of
    Issue, Date of Request, Product Type, Product Version), omitting any
    field that has no value. Internal/administrative fields (Batch ID,
    Prepared For ID) are parsed but deliberately not shown here -- they
    aren't meaningful to a report reader.

    Returns ``None`` when every field is blank, so the caller can skip
    the block entirely rather than render an empty grid.
    """
    pairs = [
        (label, value)
        for label, value in (
            ("Report ID:", h.safe_text(header.report_id)),
            ("Status:", h.safe_text(header.status)),
            ("Date of Issue:", h.safe_date(header.date_of_issue)),
            ("Date of Request:", h.safe_date(header.date_of_request)),
            ("Product Type:", h.safe_text(header.product_type)),
            ("Product Version:", h.safe_text(header.product_version)),
        )
        if value
    ]
    if not pairs:
        return None
    return h.create_key_value_table(pairs, columns=3)


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

    header_info = _build_header_info(report.header)
    if header_info is not None:
        story.append(h.horizontal_rule(thickness=c.BORDER_WIDTH_THIN, space_before=c.SPACE_XS, space_after=c.SPACE_XS))
        story.append(header_info)

    story.append(
        h.horizontal_rule(
            thickness=c.BORDER_WIDTH_STANDARD,
            space_before=c.SPACE_SM,
            space_after=c.SPACE_MD,
        )
    )
