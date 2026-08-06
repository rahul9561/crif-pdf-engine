"""
pdf_engine.sections.footer
=============================

Renders the closing content of ``docs/sample_report.pdf``: the
"-END OF REPORT-" marker, the Appendix (a static legend of section codes
used elsewhere in the report), and CRIF's disclaimer/copyright text.

Every string in this module is static boilerplate reproduced verbatim
from the reference report -- none of it is derived from ``CreditReport``.
The ``report`` parameter is still accepted (and unused) so this module's
public ``render(story, report)`` matches the calling convention shared by
every other section.
"""

from __future__ import annotations

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import CreditReport

__all__ = ["render"]

_APPENDIX_HEADERS = ["Section", "Code", "Description"]
_APPENDIX_COLUMN_FRACTIONS = (0.22, 0.16, 0.62)
_APPENDIX_ROWS = [
    [
        "Account Summary",
        "Number of Delinquent Accounts",
        "Indicates number of accounts that the applicant has defaulted on within the last 6 months",
    ],
    [
        "Account Information - Credit Grantor",
        "XXXX",
        "Name of grantor undisclosed as credit grantor is different from inquiring institution",
    ],
    [
        "Payment History / Asset Classification",
        "STD",
        "Account Reported as STANDARD Asset",
    ],
    [
        "Payment History / Asset Classification",
        "XXX",
        "Data not reported by institution",
    ],
]

_DISCLAIMER = (
    "Disclaimer: This document is prepared based on the data submitted by member "
    "institutions of CRIF High Mark Credit Information Services Private Limited "
    "(CRIF High Mark). No alterations are made to the data submitted by member "
    "institutions and the same is up to date as well as accurate to the best of "
    "its knowledge. By using data contained in this document, the user "
    "acknowledges that CRIF High Mark is not responsible for errors/omissions "
    "resulting from submission of erroneous data from Members to CRIF High Mark. "
    "This document may not be used or disclosed to others, except with the "
    "written permission of CRIF High Mark. Any paper copy of this document will "
    "be considered uncontrolled. If you are not the intended recipient, you are "
    "not authorized to read, print, retain, copy, disseminate, distribute or use "
    "this information or any part thereof."
)
_PERFORM_NOTICE = (
    "PERFORM score provided in this document is joint work of CRIF SPA (Italy) "
    "and CRIF High Mark (India). For any assistance on this report, reach out to "
    "us at: customerservice@crifhighmark.com"
)
_COPYRIGHT = "Copyrights reserved (c) 2021 CRIF High Mark Credit Information Services Pvt Ltd"


def render(story: list, report: CreditReport) -> None:
    """
    Appends the end-of-report marker, Appendix, and disclaimer/copyright
    text to ``story``.

    A ``PageBreak`` always precedes this module's content, so the
    "-END OF REPORT-" marker and the Appendix that follows it always
    begin together at the top of a fresh page -- the marker never ends up
    stranded as the last line of a page that also holds other content,
    and the Appendix never starts low on a page with too little room left
    to be useful.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report. Unused -- every element
            rendered by this module is static boilerplate -- but accepted
            to match the ``render(story, report)`` calling convention
            shared by every section.
    """
    end_of_report_style = ParagraphStyle(
        name="EndOfReport", parent=s.STYLES["Heading"], alignment=TA_CENTER
    )
    copyright_style = ParagraphStyle(
        name="Copyright", parent=s.STYLES["Caption"], alignment=TA_CENTER
    )

    story.append(PageBreak())

    story.append(h.horizontal_rule(thickness=c.BORDER_WIDTH_THIN))
    story.append(Paragraph("-END OF REPORT-", end_of_report_style))
    story.append(h.horizontal_rule(thickness=c.BORDER_WIDTH_THIN))
    story.append(Spacer(1, c.SPACE_MD))

    col_widths = [fraction * c.CONTENT_WIDTH for fraction in _APPENDIX_COLUMN_FRACTIONS]
    # create_data_table uses cell values as-is (see its docstring), so the
    # static appendix text is escaped here rather than relying on the
    # table builder to do it -- these rows are plain literals today, but
    # escaping them at the boundary keeps this consistent with every
    # other section and safe if the text ever changes.
    escaped_rows = [[h.safe_text(cell) for cell in row] for row in _APPENDIX_ROWS]
    appendix_table = h.create_data_table(
        _APPENDIX_HEADERS, escaped_rows, col_widths=col_widths, zebra=False
    )
    # Fixed-size (always exactly four rows) and already guaranteed to
    # start with a full fresh page beneath it (see PageBreak above), so
    # this KeepTogether is a belt-and-suspenders guarantee, not a
    # necessity -- the Appendix title can never be split from its table.
    story.append(KeepTogether([h.create_section_header("Appendix"), appendix_table]))
    story.append(Spacer(1, c.SPACE_MD))

    story.append(Paragraph(h.safe_text(_DISCLAIMER), s.STYLES["Caption"]))
    story.append(Paragraph(h.safe_text(_PERFORM_NOTICE), s.STYLES["Caption"]))
    story.append(Spacer(1, c.SPACE_XS))
    story.append(Paragraph(h.safe_text(_COPYRIGHT), copyright_style))
