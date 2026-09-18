"""
pdf_engine.generator
======================

Top-level orchestrator: assembles every section module's output into one
ReportLab story and builds the final PDF, saving it to a given output
path.

Section call order
-------------------
``build_story`` renders the report in the order its content appears in
``docs/sample_report.pdf``: masthead, identity, score, score trend,
summaries, personal-info variations, employment, then one interleaved
block per account (that account's "Account Information" immediately
followed by that same account's payment history), then inquiries and the
appendix/footer.

Every section *except* accounts/payment-history is a simple, uniform
``section_module.render(story, report)`` call over the whole report --
see ``_SECTIONS_BEFORE_ACCOUNTS`` / ``_SECTIONS_AFTER_ACCOUNTS`` below.
Accounts and payment history are the one place that call pattern doesn't
fit: both ``sections.account`` and ``sections.payment_history`` expose a
per-account entrypoint (``render_account`` / ``render_payment_history``,
each taking a single already-parsed ``LoanAccount`` rather than the
whole ``report``), and this function is the only place that calls both,
once per account, back to back. Neither section module depends on the
other to do this -- each remains independently reusable on its own (its
existing whole-report ``render(story, report)`` is unchanged and still
renders every account's block in one pass, for callers who want account
information or payment history alone). Every section module already
skips itself automatically when it has nothing to render (including a
single account with no payment history, which contributes nothing and
does not affect the next account), so this function does no filtering of
its own -- it purely sequences content.

Page numbering and page breaks
-------------------------------
Both are automatic and require no manual bookkeeping in this module:
``helpers.create_page_number`` is wired up as the page-drawing callback
for every page (first and later), and ReportLab's own Platypus layout
engine paginates the story as it is built -- flowables that do not fit
the remaining space on a page flow onto the next one, and tables built
via ``helpers.create_data_table`` (``repeatRows=1``) reattach their
header row to every continuation page.
"""

from __future__ import annotations

import logging
from pathlib import Path

from reportlab.platypus import SimpleDocTemplate

from . import constants as c
from . import helpers as h
from .parser import CreditReport, RawMapping, parse_credit_report
from .sections import (
    account,
    customer,
    employment,
    enquiries,
    footer,
    header,
    payment_history,
    personal_variations,
    score,
    score_trend,
    summary,
)

logger = logging.getLogger(__name__)

__all__ = ["build_story", "render_pdf", "generate_report"]

#: Whole-report section modules rendered before the per-account content,
#: in the order their content appears in the final report. Each exposes
#: a ``render(story, report)`` entrypoint and is independently
#: responsible for skipping itself when it has nothing to render.
_SECTIONS_BEFORE_ACCOUNTS = (
    header,
    customer,
    score,
    score_trend,
    summary,
    personal_variations,
    employment,
)

#: Whole-report section modules rendered after the per-account content.
_SECTIONS_AFTER_ACCOUNTS = (
    enquiries,
    footer,
)

_DEFAULT_TITLE = "Credit Information Report"


def build_story(report: CreditReport) -> list:
    """
    Assembles the complete ReportLab story for ``report``.

    Calls every whole-report section module's ``render(story, report)``
    once, in report order, except accounts/payment-history: those are
    rendered by iterating ``report.accounts`` exactly once and, for each
    already-parsed account, calling ``sections.account.render_account``
    immediately followed by
    ``sections.payment_history.render_payment_history`` -- so every
    account's payment history appears directly beneath that same
    account's "Account Information" block (see the module docstring for
    why this is the one section pair that needs special-casing here).
    No parsing happens in this function; it only sequences content
    already produced by ``pdf_engine.parser``.

    Args:
        report: The normalized credit report to render.

    Returns:
        A list of ReportLab flowables, ready to pass to
        :func:`render_pdf` (or directly to a ``SimpleDocTemplate``).
    """
    story: list = []

    for section_module in _SECTIONS_BEFORE_ACCOUNTS:
        section_module.render(story, report)

    for loan_account in report.accounts:
        account.render_account(story, loan_account)
        payment_history.render_payment_history(story, loan_account)

    for section_module in _SECTIONS_AFTER_ACCOUNTS:
        section_module.render(story, report)

    return story


def render_pdf(story: list, output_path: str | Path, *, title: str = _DEFAULT_TITLE) -> Path:
    """
    Builds a final PDF from an assembled story and saves it to
    ``output_path``.

    Args:
        story: A list of ReportLab flowables, typically produced by
            :func:`build_story`. Not mutated: ReportLab's own
            ``document.build()`` drains whatever list it's given as it
            lays out pages, so this function passes it a shallow copy,
            leaving the caller's original ``story`` list intact and
            reusable after this call returns.
        output_path: Filesystem path the PDF should be written to. Its
            parent directory is created first if it does not already
            exist.
        title: PDF document-info title metadata.

    Returns:
        The resolved ``Path`` the PDF was written to.

    Raises:
        Exception: Whatever the underlying failure was -- directory
            creation (``OSError``), document layout (ReportLab can raise
            its own exception types for content that cannot be laid out),
            or the final file write. Every case is logged with the
            resolved output path for diagnosability before re-raising;
            nothing is caught or swallowed.
    """
    resolved_path = Path(output_path).resolve()

    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        document = SimpleDocTemplate(
            str(resolved_path),
            pagesize=c.PAGE_SIZE,
            topMargin=c.MARGIN_TOP,
            bottomMargin=c.MARGIN_BOTTOM,
            leftMargin=c.MARGIN_LEFT,
            rightMargin=c.MARGIN_RIGHT,
            title=title,
        )
        document.build(
            list(story), onFirstPage=h.create_page_number, onLaterPages=h.create_page_number
        )
    except Exception:
        logger.exception("Failed to generate PDF at %s", resolved_path)
        raise

    logger.info("Wrote %d-page PDF to %s", document.page, resolved_path)
    return resolved_path


def generate_report(raw_json: RawMapping, output_path: str | Path) -> Path:
    """
    End-to-end pipeline: parses a raw CRIF Highmark API response, builds
    the report story, and saves the final PDF to ``output_path``.

    This is the single entrypoint intended for external callers (e.g. a
    Django view) that hold a raw bureau response and want a finished PDF
    on disk. Callers that already have a normalized ``CreditReport``, or
    that want to control document assembly themselves, should use
    :func:`build_story` and :func:`render_pdf` directly instead.

    Args:
        raw_json: The full decoded CRIF Highmark API response body (see
            ``pdf_engine.parser.parse_credit_report``).
        output_path: Filesystem path the PDF should be written to.

    Returns:
        The resolved ``Path`` the PDF was written to.
    """
    report = parse_credit_report(raw_json)
    story = build_story(report)

    name = report.customer_identity.name
    title = f"{_DEFAULT_TITLE} - {name}" if name else _DEFAULT_TITLE

    return render_pdf(story, output_path, title=title)
