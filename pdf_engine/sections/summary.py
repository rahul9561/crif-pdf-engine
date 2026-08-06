"""
pdf_engine.sections.summary
==============================

Renders the account summary blocks from ``docs/sample_report.pdf``:
Primary Account Summary, Secondary Account Summary, and Perform
Attributes -- all sourced from ``CreditReport.account_summary``.

The reference PDF's Primary/Secondary Account Summary tables also include
"Current Balance Secured", "Current Balance Unsecured" and "Total Amount
Overdue" columns, and separate "Group Account Summary" / "Additional
Summary" boxes. None of those are present on ``AccountsSummary`` -- CRIF's
raw payload for this report does not report them -- so this module renders
exactly the nine fields ``AccountsSummary`` actually carries, and omits
the Group/Additional Summary boxes entirely rather than rendering an
empty shell.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Paragraph, Spacer

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import AccountsSummary, CreditReport, DerivedAttributes

__all__ = ["render"]

# Pre-escaped static caption text (contains a literal "&"), reproduced
# verbatim from the reference report. Passed straight to Paragraph rather
# than through helpers.safe_text, which would re-escape the entity.
_TIP_ACTIVE_ONLY = (
    "Tip: Current Balance &amp; Disbursed Amount is considered ONLY for ACTIVE accounts."
)
_TIP_INR = "Tip: All amounts are in INR."

_SUMMARY_HEADERS = [
    "Number of Accounts",
    "Active Accounts",
    "Overdue Accounts",
    "Secured Accounts",
    "UnSecured Accounts",
    "Untagged Accounts",
    "Total Current Balance",
    "Total Sanctioned Amount",
    "Total Disbursed Amount",
]


def _summary_row(summary: AccountsSummary) -> list[str]:
    """Formats one ``AccountsSummary`` as a display row matching ``_SUMMARY_HEADERS``."""
    return [
        h.safe_text(summary.number_of_accounts, default="0"),
        h.safe_text(summary.active_number_of_accounts, default="0"),
        h.safe_text(summary.overdue_number_of_accounts, default="0"),
        h.safe_decimal(summary.secured_number_of_accounts, default="0", grouping=False),
        h.safe_text(summary.unsecured_number_of_accounts, default="0"),
        h.safe_text(summary.untagged_number_of_accounts, default="0"),
        h.safe_decimal(summary.current_balance, default="0"),
        h.safe_decimal(summary.sanctioned_amount, default="0"),
        h.safe_decimal(summary.disbursed_amount, default="0"),
    ]


def _render_accounts_summary(story: list, title: str, summary: AccountsSummary) -> None:
    """Appends one Primary/Secondary Account Summary block to ``story``."""
    table = h.create_data_table(
        _SUMMARY_HEADERS, [_summary_row(summary)], zebra=False, align="CENTER"
    )
    # Small and fixed-size (always exactly one data row) -- safe to keep
    # the whole block together so the heading is never orphaned from it.
    story.append(
        KeepTogether(
            [
                h.create_section_header(title),
                Spacer(1, c.SPACE_XXS),
                Paragraph(_TIP_ACTIVE_ONLY, s.STYLES["Small"]),
                Paragraph(_TIP_INR, s.STYLES["Small"]),
                Spacer(1, c.SPACE_XS),
                table,
            ]
        )
    )
    story.append(Spacer(1, c.SPACE_MD))


def _render_perform_attributes(story: list, attributes: DerivedAttributes) -> None:
    """Appends the "Perform Attributes" key-value block to ``story``."""
    header = h.create_section_header("Perform Attributes")
    grid = h.create_key_value_table(
        [
            (
                "Inquiries In Last Six Months:",
                h.safe_text(attributes.inquiries_in_last_six_months, default="0"),
            ),
            (
                "Length Of Credit History (Years):",
                h.safe_text(attributes.length_of_credit_history_year, default="0"),
            ),
            (
                "Length Of Credit History (Months):",
                h.safe_text(attributes.length_of_credit_history_month, default="0"),
            ),
            (
                "Average Account Age (Years):",
                h.safe_text(attributes.average_account_age_year, default="0"),
            ),
            (
                "Average Account Age (Months):",
                h.safe_text(attributes.average_account_age_month, default="0"),
            ),
            (
                "New Accounts In Last Six Months:",
                h.safe_text(attributes.new_accounts_in_last_six_months, default="0"),
            ),
            (
                "New Delinquent Accounts In Last Six Months:",
                h.safe_text(attributes.new_delinq_account_in_last_six_months, default="0"),
            ),
        ],
        columns=2,
    )
    # Small and fixed-size (always exactly seven pairs) -- safe to keep
    # the whole block together.
    story.append(KeepTogether([header, Spacer(1, c.SPACE_XS), grid]))
    story.append(Spacer(1, c.SPACE_MD))


def render(story: list, report: CreditReport) -> None:
    """
    Appends the Primary Account Summary, Secondary Account Summary, and
    Perform Attributes blocks to ``story``.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    account_summary = report.account_summary
    _render_accounts_summary(story, "Primary Account Summary", account_summary.primary)
    _render_accounts_summary(story, "Secondary Account Summary", account_summary.secondary)
    _render_perform_attributes(story, account_summary.derived_attributes)
