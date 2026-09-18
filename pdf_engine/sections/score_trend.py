"""
pdf_engine.sections.score_trend
==================================

Renders the "Score Trend" section: the historical Retro Date / Score
series from ``docs/sample_report.pdf``, sourced from
``CreditReport.score_trend``.

Rendered as a two-row table -- a "Retro Date" row and a "Score" row, one
column per historical sample -- mirroring the reference report's layout,
where each retro date lines up with its score directly beneath it. The
number of columns is entirely driven by ``len(points)`` -- there is no
fixed/assumed sample count, so a payload with 12 quarterly samples, 2, or
40 monthly ones all render correctly, each date lined up with its own
score.

Retro dates are shown in a compact ``Mon-YY`` form (e.g. "Mar-26") rather
than the report-wide ``DD-MM-YYYY`` format: with up to a dozen or more
date columns sharing the page width, a compact label is what keeps every
column readable without wrapping or shrinking the font past legibility.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Paragraph, Spacer

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import CreditReport

__all__ = ["render"]

#: Fraction of the content width reserved for the row-label column.
_LABEL_COLUMN_FRACTION = 0.10

#: Compact retro-date format used for this table's column headers only
#: (see module docstring) -- every other date in the report uses
#: constants.DATE_DISPLAY_FORMAT.
_RETRO_DATE_FORMAT = "%b-%y"


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "Score Trend" section to ``story``.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    header = h.create_section_header("Score Trend")

    points = report.score_trend.points
    if not points:
        story.append(
            KeepTogether(
                [
                    header,
                    Spacer(1, c.SPACE_XS),
                    Paragraph("No score trend data available.", s.STYLES["Small"]),
                ]
            )
        )
        story.append(Spacer(1, c.SPACE_MD))
        return

    headers = ["Retro Date"] + [
        h.safe_date(point.as_of, fmt=_RETRO_DATE_FORMAT) for point in points
    ]
    score_row = ["Score"] + [h.safe_text(point.value) for point in points]

    label_width = c.CONTENT_WIDTH * _LABEL_COLUMN_FRACTION
    remaining_width = c.CONTENT_WIDTH - label_width
    col_widths = [label_width] + [remaining_width / len(points)] * len(points)

    table = h.create_data_table(
        headers, [score_row], col_widths=col_widths, zebra=False, align="CENTER"
    )
    story.append(KeepTogether([header, table]))
    story.append(Spacer(1, c.SPACE_MD))
