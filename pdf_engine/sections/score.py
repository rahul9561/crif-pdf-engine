"""
pdf_engine.sections.score
============================

Renders the "CRIF HM Score(S)" section: the score name, score value,
grade, and scoring factors table near the top of ``docs/sample_report.pdf``.

The reference PDF also shows a "Range" column (e.g. "300-900"). CRIF's
raw payload does not report a numeric range for the score -- it is not a
field on ``Score`` (from ``pdf_engine.parser``) -- so the Range column
header is kept for visual fidelity to the reference layout, but its cell
is left blank rather than inventing a value.

Scoring factors are rendered from ``Score.score_factors``, which the
parser populates with each factor's human-readable description when the
bureau supplies one (falling back to its short code, e.g. "SF11", only
when no description is available) -- see ``CrifParser._adapt_score``.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Spacer

from .. import constants as c
from .. import helpers as h
from ..parser import CreditReport

__all__ = ["render"]

_HEADERS = ["Score Name", "Range", "Score", "Grade", "Scoring Factors (Up to 4 only)"]
_COLUMN_WIDTH_FRACTIONS = (0.22, 0.09, 0.09, 0.10, 0.50)

#: The reference report caps displayed scoring factors at four; mirrored
#: here purely as a display-truncation limit, not a scoring rule.
_MAX_DISPLAYED_FACTORS = 4


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "CRIF HM Score(S)" section to ``story``.

    Does nothing if the parsed report carries no score at all (neither a
    score type nor a score value), matching the reference report's
    behavior of only ever showing this section when there is a score to
    show.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    score = report.score
    if not score.score_type and score.score_value is None:
        return

    # Each factor is escaped individually and joined with a Paragraph
    # line break so multiple, potentially long, factor descriptions each
    # get their own line rather than running together -- create_data_table
    # uses cell text as-is (see its docstring), so joining pre-escaped
    # pieces is safe and avoids re-escaping the "<br/>" markup itself.
    factors_html = "<br/>".join(
        h.safe_text(factor) for factor in score.score_factors[:_MAX_DISPLAYED_FACTORS]
    )

    row = [
        h.safe_text(score.score_type),
        "",
        h.safe_text(score.score_value),
        h.safe_text(score.score_description),
        factors_html,
    ]
    col_widths = [fraction * c.CONTENT_WIDTH for fraction in _COLUMN_WIDTH_FRACTIONS]

    table = h.create_data_table(_HEADERS, [row], col_widths=col_widths, zebra=False)
    # Always exactly one row -- safe to keep the whole block together.
    story.append(KeepTogether([h.create_section_header("CRIF HM Score(S)"), table]))
    story.append(Spacer(1, c.SPACE_MD))
