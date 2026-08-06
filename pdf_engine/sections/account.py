"""
pdf_engine.sections.account
==============================

Renders one "Account Information" block per loan account in the report
(``CreditReport.accounts``, unbounded), matching ``docs/sample_report.pdf``'s
repeating account layout: a navy title bar, an identifying sub-header line
(type / grantor / account number / as-on date), a colored account-status
line, an 18-field ownership/amount detail grid, and -- only when present
-- a Collateral/Security Details table.

Payment history is rendered separately, per account, by
``pdf_engine.sections.payment_history`` -- it is not part of this module.

Each account is rendered by an independent, self-contained call
(``_render_single_account``): nothing about one account's flowables
depends on any other account, so ten accounts render exactly as reliably
as one, and a page break landing in the middle of one account's content
never affects another's. No account block is force-kept-together as a
whole (the reference report itself lets a long account split across a
page boundary mid-table); only each account's short title bar and
sub-header are kept together, so a section heading never gets orphaned
alone at the bottom of a page.
"""

from __future__ import annotations

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from .. import theme
from ..parser import CreditReport, LoanAccount, SecurityDetail

__all__ = ["render"]

_SECURITY_HEADERS = ["Security Type", "Type of Charge", "Security Value", "Date Of Value"]

#: Account-status -> highlight color, matched case-insensitively. Falls
#: back to a neutral gray for any status not listed here, so an unknown
#: or future status value never fails to render.
_STATUS_COLORS: dict[str, object] = {
    "active": theme.GREEN,
    "closed": theme.GRAY_700,
    "written off": theme.RED,
    "settled": theme.ORANGE,
    "default": theme.RED,
    "overdue": theme.RED,
    "suit filed": theme.RED,
}
_DEFAULT_STATUS_COLOR = theme.GRAY_700

# Built once at import time, not per account: none of these depend on
# report data, so re-constructing them inside a function called once per
# tradeline would be pure repeated work for a report with many accounts.

_SUB_HEADER_PARAGRAPH_STYLE = ParagraphStyle(
    name="AccountSubHeader", parent=s.STYLES["TableCell"], textColor=theme.PRIMARY_BLUE_DARK
)
_SUB_HEADER_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, -1), theme.SECONDARY_BLUE_LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_LEFT),
        ("RIGHTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_RIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_TOP),
        ("BOTTOMPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_BOTTOM),
    ]
)

#: Base style for the account-status line. The per-account text color is
#: applied via ``.clone()`` (cheap: an independent, unregistered copy)
#: rather than constructing a brand new ParagraphStyle from scratch for
#: every account.
_STATUS_BADGE_BASE_STYLE = ParagraphStyle(
    name="AccountStatusBadge", parent=s.STYLES["Heading"], spaceBefore=0, spaceAfter=0
)


def _build_sub_header(account: LoanAccount) -> Table:
    """
    Builds the light-shaded identifying line beneath the title bar
    (Account Type / Credit Grantor / Account # / Lender Type # / As on #).

    Rendered as a single wrapping Paragraph with bold inline labels
    (rather than a multi-column grid) so it degrades gracefully -- long
    grantor names simply wrap instead of overflowing or requiring
    per-field column-width tuning.
    """
    fields = [
        ("Account Type", account.acct_type),
        ("Credit Grantor", account.credit_guarantor),
        ("Account #", account.acct_number),
        # CRIF's own template repeats the credit grantor's name under a
        # separate "Lender Type #" label. LoanAccount has no distinct
        # lender-type field (credit_grantor_type is an internal bureau
        # category code, e.g. "PRB", not a lender name), so the same
        # value is reused here rather than inventing a new one.
        ("Lender Type #", account.credit_guarantor),
        ("As on #", h.safe_date(account.date_reported)),
    ]
    segments = [f"<b>{label}:</b> {h.safe_text(value)}" for label, value in fields]
    markup = "&nbsp;&nbsp;&nbsp;&nbsp;".join(segments)

    row = Table([[Paragraph(markup, _SUB_HEADER_PARAGRAPH_STYLE)]], colWidths=[c.CONTENT_WIDTH])
    row.setStyle(_SUB_HEADER_TABLE_STYLE)
    return row


def _build_status_badge(account: LoanAccount) -> Paragraph:
    """Builds the account-status line, colored per ``_STATUS_COLORS``."""
    raw_status = account.account_status or ""
    color = _STATUS_COLORS.get(raw_status.strip().lower(), _DEFAULT_STATUS_COLOR)
    style = _STATUS_BADGE_BASE_STYLE.clone("AccountStatusBadgeColored", textColor=color)
    status_display = h.safe_text(raw_status, default="Unknown")
    return Paragraph(f"Account Status: <b>{status_display}</b>", style)


def _build_detail_grid(account: LoanAccount) -> Table:
    """Builds the 3-column, 18-field ownership/amount detail grid."""
    pairs = [
        ("Ownership:", h.safe_text(account.ownership_ind)),
        ("Disbursed Date:", h.safe_date(account.disbursed_dt)),
        ("Disbd Amt/High Credit:", h.safe_decimal(account.disbursed_amt)),
        ("Credit Limit:", h.safe_decimal(account.credit_limit)),
        ("Last Payment Date:", h.safe_date(account.last_payment_date)),
        ("Current Balance:", h.safe_decimal(account.current_bal)),
        ("Cash Limit:", h.safe_decimal(account.cash_limit)),
        ("Closed Date:", h.safe_date(account.closed_date)),
        ("Last Paid Amt:", h.safe_decimal(account.last_paid_amount)),
        ("InstlAmt/Freq:", h.safe_text(account.installment_amt)),
        ("Tenure(month):", h.safe_text(account.repayment_tenure, default="0")),
        ("Overdue Amt:", h.safe_decimal(account.overdue_amt, default="0")),
        ("Write off Date:", h.safe_date(account.write_off_dt)),
        ("Account in Dispute:", h.safe_text(account.acct_in_dispute)),
        ("Account Remarks:", h.safe_text(account.account_remarks)),
        ("Principal Writeoff Amt:", h.safe_decimal(account.principal_write_off_amt)),
        ("Total Writeoff Amt:", h.safe_decimal(account.write_off_amt, default="0")),
        ("Settlement Amt:", h.safe_decimal(account.settlement_amt)),
    ]
    return h.create_key_value_table(pairs, columns=3)


def _build_security_table(details: list[SecurityDetail]) -> Table:
    """Builds the Collateral/Security Details table for one account."""
    rows = [
        [
            h.safe_text(detail.security_type),
            h.safe_text(detail.security_charge),
            h.safe_decimal(detail.security_value),
            h.safe_date(detail.date_of_value),
        ]
        for detail in details
    ]
    return h.create_data_table(_SECURITY_HEADERS, rows, zebra=len(rows) > 1)


def _render_single_account(story: list, account: LoanAccount) -> None:
    """
    Appends one account's "Account Information" block to ``story``.

    Self-contained: depends only on ``account``, so accounts render one
    at a time with no shared state and no dependency on how many other
    accounts came before or after.
    """
    story.append(
        KeepTogether([h.create_section_header("Account Information"), _build_sub_header(account)])
    )
    story.append(Spacer(1, c.SPACE_XS))
    story.append(_build_status_badge(account))
    story.append(Spacer(1, c.SPACE_XS))
    story.append(_build_detail_grid(account))

    if account.security_details:
        # No explicit spacer before the heading: the "Heading" style
        # already carries its own spaceBefore, and doubling up on top of
        # that would be exactly the kind of unnecessary extra gap this
        # pass is trying to remove. Security detail lists are always
        # short, so it's safe to keep the heading and its table together.
        security_table = _build_security_table(account.security_details)
        story.append(KeepTogether([h.create_heading("Collateral/Security Details:"), security_table]))

    # A thin rule reads as a deliberate boundary between two accounts;
    # a bare blank gap of the same height reads as accidental leftover
    # whitespace. Total vertical space spent is about the same as before.
    story.append(Spacer(1, c.SPACE_SM))
    story.append(h.horizontal_rule(thickness=c.BORDER_WIDTH_HAIRLINE, space_before=0, space_after=0))
    story.append(Spacer(1, c.SPACE_SM))


def render(story: list, report: CreditReport) -> None:
    """
    Appends one "Account Information" block per account in the report to
    ``story``. Supports an unbounded number of accounts -- there is no
    fixed limit or pre-computed layout, each account simply extends the
    story and ReportLab paginates automatically.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    for account in report.accounts:
        _render_single_account(story, account)
