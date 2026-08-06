"""
pdf_engine.sections.payment_history
======================================

Renders the "Payment History/Asset Classification:" grid for every loan
account in the report, sourced from ``LoanAccount.payment_history``.

Each account's monthly payment-history entries are pivoted into a table
with one row per calendar year and one column per month (Jan-Dec),
mirroring ``docs/sample_report.pdf``'s "Amount Paid History" grid. This
shape scales to an unbounded amount of history automatically: more months
only ever add more year-rows, never more columns, so a decade of history
renders exactly as reliably as two months of it. Each cell shows both
halves of the underlying data -- days-past-due and asset classification,
e.g. ``"000/STD"`` -- matching the two-part format CRIF itself uses in
``combined_payment_history`` and explains in the report's own Appendix.

Unlike every other table in this report, the grid built here does **not**
wrap its cells in ``Paragraph`` objects. A code like ``"000/STD"`` has no
spaces, so if it doesn't fit a Paragraph cell, ReportLab's word-wrapper
falls back to breaking it mid-string -- exactly the "code splits onto two
lines" defect this module exists to prevent. Plain-string cells styled
via ``TableStyle`` ``FONT``/``FONTSIZE`` commands are never wrapped by
ReportLab; combined with a font size chosen (once, at import time, via
:func:`helpers.fit_font_size`) to guarantee the widest realistic code
fits the column at all, every code is guaranteed single-line.

Each account is rendered independently (``_render_single_account``): an
account with no payment history is skipped without affecting any other
account, and one account's table is free to split across as many pages
as it needs -- ``repeatRows=1`` keeps the Jan-Dec header attached to
every continuation page of a long table.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from .. import theme
from ..parser import CreditReport, LoanAccount, PaymentHistoryEntry

__all__ = ["render"]

_MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

#: Fraction of the content width reserved for the leading "Year" column.
#: Kept small -- a 4-digit year needs far less room than a month column.
_YEAR_COLUMN_FRACTION = 0.06

#: Horizontal/vertical padding for code cells. Deliberately tighter than
#: the report-wide table padding (constants.TABLE_CELL_PADDING_*): with
#: 12 month columns competing for a fixed page width, every point spent
#: on padding is a point not available for the code text itself.
_CODE_CELL_PADDING_X = 2
_CODE_CELL_PADDING_Y = 4

#: The widest realistically-occurring code: a 3-digit days-past-due value
#: plus a 4-letter asset-classification code (CRIF's Appendix documents
#: STD/XXX; other bureaus' equivalents such as SUB/DBT/LSS are the same
#: length or shorter). Sized against this, not the actual data, so the
#: chosen font size is identical -- and therefore visually consistent --
#: across every account's table in the report, regardless of which
#: accounts happen to have longer or shorter codes.
_WORST_CASE_CODE_SAMPLE = "000/XXXX"

#: Font sizes tried, largest to smallest, when fitting code cells.
_CODE_FONT_CANDIDATE_SIZES = (8.0, 7.5, 7.0, 6.5, 6.0, 5.5)

_YEAR_COLUMN_WIDTH = c.CONTENT_WIDTH * _YEAR_COLUMN_FRACTION
_MONTH_COLUMN_WIDTH = (c.CONTENT_WIDTH - _YEAR_COLUMN_WIDTH) / len(_MONTH_LABELS)
_COL_WIDTHS = [_YEAR_COLUMN_WIDTH] + [_MONTH_COLUMN_WIDTH] * len(_MONTH_LABELS)

#: Computed once at import time (the column widths above are fixed
#: constants, not report data) so every payment-history table in the
#: document renders its codes at the exact same size -- both a
#: correctness guarantee (fits the narrowest column) and a typography
#: guarantee (nothing to compute or vary per account).
_CODE_FONT_SIZE = h.fit_font_size(
    _WORST_CASE_CODE_SAMPLE,
    _MONTH_COLUMN_WIDTH - 2 * _CODE_CELL_PADDING_X,
    font_name=theme.FONTS["Roboto-Regular"],
    candidate_sizes=_CODE_FONT_CANDIDATE_SIZES,
)

_TABLE_STYLE = TableStyle(
    [
        # Header row (Year, Jan..Dec).
        ("FONT", (0, 0), (-1, 0), theme.FONTS["Roboto-SemiBold"], c.FONT_SIZE_TABLE_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), theme.PRIMARY_BLUE),
        ("BACKGROUND", (0, 0), (-1, 0), theme.SECONDARY_BLUE_LIGHT),
        # Code cells: the computed, guaranteed-to-fit, never-wrapped size.
        ("FONT", (0, 1), (-1, -1), theme.FONTS["Roboto-Regular"], _CODE_FONT_SIZE),
        ("TEXTCOLOR", (0, 1), (-1, -1), theme.GRAY_900),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [theme.WHITE, theme.ZEBRA_STRIPE]),
        # Every column here is a year or a code -- centered reads far
        # better than left-aligned for a dense grid of short tokens.
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), _CODE_CELL_PADDING_X),
        ("RIGHTPADDING", (0, 0), (-1, -1), _CODE_CELL_PADDING_X),
        ("TOPPADDING", (0, 0), (-1, -1), _CODE_CELL_PADDING_Y),
        ("BOTTOMPADDING", (0, 0), (-1, -1), _CODE_CELL_PADDING_Y),
        ("GRID", (0, 0), (-1, -1), c.BORDER_WIDTH_HAIRLINE, theme.BORDER_LIGHT),
    ]
)


def _pivot_payment_history(entries: list[PaymentHistoryEntry]) -> list[list[str]]:
    """
    Pivots a flat list of monthly payment-history entries into one row
    per calendar year, ascending, with one cell per month (blank where no
    entry was reported for that month).

    Unbounded by construction: however many years ``entries`` spans, the
    result simply has that many rows -- the column count (13: Year +
    12 months) never changes.

    Cell values are plain strings, not XML-escaped: the payment-history
    table renders them as unwrapped plain-string Table cells (see the
    module docstring), which are drawn as literal text and never
    interpreted as markup, so ``safe_text`` escaping does not apply here.
    """
    by_year: dict[int, dict[int, str]] = {}
    for entry in entries:
        by_year.setdefault(entry.year, {})[entry.month] = (
            f"{entry.days_past_due}/{entry.asset_classification}"
        )

    rows: list[list[str]] = []
    for year in sorted(by_year):
        months = by_year[year]
        rows.append([str(year)] + [months.get(month, "") for month in range(1, 13)])
    return rows


def _build_payment_history_table(entries: list[PaymentHistoryEntry]) -> Table:
    """Builds the Year x Jan-Dec payment history grid for one account."""
    table_data = [["Year", *_MONTH_LABELS], *_pivot_payment_history(entries)]
    table = Table(table_data, colWidths=_COL_WIDTHS, repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    return table


def _account_identifier_line(account: LoanAccount) -> Paragraph:
    """
    Builds a small caption identifying which account the following table
    belongs to.

    Necessary because this module renders independently of
    ``sections.account``: its output is not guaranteed to sit directly
    beneath that account's "Account Information" block in the final
    document, so each payment history table must be self-describing on
    its own.
    """
    parts = [
        part
        for part in (
            h.safe_text(account.acct_number),
            h.safe_text(account.acct_type),
            h.safe_text(account.credit_guarantor),
        )
        if part
    ]
    return Paragraph(" | ".join(parts) if parts else "-", s.STYLES["Small"])


def _render_single_account(story: list, account: LoanAccount) -> None:
    """
    Appends one account's Payment History block to ``story``.

    Self-contained: depends only on ``account``, and does nothing when
    that account has no payment history -- skipping it never affects any
    other account's block.
    """
    if not account.payment_history:
        return

    story.append(
        KeepTogether(
            [
                h.create_heading("Payment History/Asset Classification:"),
                _account_identifier_line(account),
                Spacer(1, c.SPACE_XXS),
            ]
        )
    )
    story.append(_build_payment_history_table(account.payment_history))
    story.append(Spacer(1, c.SPACE_LG))


def render(story: list, report: CreditReport) -> None:
    """
    Appends the Payment History/Asset Classification table for every
    account in the report to ``story``. Supports an unbounded number of
    accounts, each with an unbounded amount of payment history.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    for account in report.accounts:
        _render_single_account(story, account)
