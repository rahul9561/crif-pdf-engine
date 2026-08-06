"""
pdf_engine.generator
======================

Top-level orchestrator: assembles every section module's output into one
ReportLab story and builds the final PDF, saving it to a given output
path.

Section call order
-------------------
``build_story`` calls each of the eleven section modules' ``render(story,
report)`` exactly once, in the order their content appears in
``docs/sample_report.pdf`` (masthead, identity, score, score trend,
summaries, personal-info variations, employment, accounts, payment
history, inquiries, appendix/footer). ``sections.account`` and
``sections.payment_history`` each loop over *every* account on their own
(see their module docstrings), so this produces every account's "Account
Information" block first, followed by every account's payment-history
table -- not the two interleaved per account the way the reference PDF
does. That is a direct, deliberate consequence of each section module
being independently callable and presence-driven with a single
``render(story, report)`` entrypoint; re-interleaving them here would
mean reaching into another module's private, per-account helpers, which
would break that independence. Every section module already skips itself
automatically when it has nothing to render, so this function does no
filtering of its own -- it purely sequences the modules.

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

#: Section modules in the order their content appears in the final
#: report. Each exposes exactly one public function, ``render(story,
#: report)``, and is independently responsible for skipping itself when
#: it has nothing to render.
_SECTION_MODULES = (
    header,
    customer,
    score,
    score_trend,
    summary,
    personal_variations,
    employment,
    account,
    payment_history,
    enquiries,
    footer,
)

_DEFAULT_TITLE = "Credit Information Report"


def build_story(report: CreditReport) -> list:
    """
    Assembles the complete ReportLab story for ``report``.

    Calls every section module's ``render(story, report)`` once, in
    report order (see the module docstring). No section is special-cased
    here: each module already decides for itself whether it has anything
    to render.

    Args:
        report: The normalized credit report to render.

    Returns:
        A list of ReportLab flowables, ready to pass to
        :func:`render_pdf` (or directly to a ``SimpleDocTemplate``).
    """
    story: list = []
    for section_module in _SECTION_MODULES:
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
