"""
pdf_engine.sections.enquiries
================================

Renders the "Inquiries ( past 24 months)" section of
``docs/sample_report.pdf``: a table of credit inquiries, sourced from
``CreditReport.inquiries``.

CRIF's raw payload for this report reports zero inquiries
(``inquiry_history.history`` is an empty list), so this module is written
to be entirely data-driven and dynamic: it renders nothing at all -- not
even the section title bar -- when ``CreditReport.inquiries`` is empty,
matching the presence-driven rendering used throughout this report rather
than showing an empty table shell.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Spacer

from .. import constants as c
from .. import helpers as h
from ..parser import CreditReport, InquiryRecord

__all__ = ["render"]

_HEADERS = ["Credit Grantor", "Type", "Date of Inquiry", "Account Type", "Amount", "Remark"]
_COLUMN_FRACTIONS = (0.22, 0.12, 0.14, 0.20, 0.12, 0.20)


def _format_row(inquiry: InquiryRecord) -> list[str]:
    """Formats one ``InquiryRecord`` as a display row matching ``_HEADERS``."""
    return [
        h.safe_text(inquiry.credit_grantor),
        h.safe_text(inquiry.inquiry_type),
        h.safe_date(inquiry.date_of_inquiry),
        h.safe_text(inquiry.account_type),
        h.safe_decimal(inquiry.amount),
        h.safe_text(inquiry.remark),
    ]


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "Inquiries ( past 24 months)" section to ``story``.

    Does nothing if the report carries no inquiries at all, so an empty
    inquiry history never leaves a dangling, dataless table in the
    output.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    inquiries = report.inquiries
    if not inquiries:
        return

    rows = [_format_row(inquiry) for inquiry in inquiries]
    col_widths = [fraction * c.CONTENT_WIDTH for fraction in _COLUMN_FRACTIONS]
    table = h.create_data_table(_HEADERS, rows, col_widths=col_widths)

    # In practice inquiry history is short (a handful of rows at most),
    # so it's safe to keep the heading and the whole table together --
    # this guarantees the heading is never orphaned from its data.
    story.append(KeepTogether([h.create_section_header("Inquiries ( past 24 months)"), table]))
    story.append(Spacer(1, c.SPACE_MD))
