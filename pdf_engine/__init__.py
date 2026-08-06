"""
pdf_engine
==========

A reusable, CRIF Highmark-specific PDF generation engine built on
ReportLab: parses a raw CRIF Highmark API JSON response into a
normalized :class:`~pdf_engine.parser.CreditReport` and renders it as a
paginated PDF.

Public API
----------
:func:`generate_report` is the single entrypoint most callers need --
parse a raw bureau response straight to a saved PDF::

    from pdf_engine import generate_report

    generate_report(raw_json, "report.pdf")

For lower-level control, :func:`build_story` and :func:`render_pdf` (see
``pdf_engine.generator``) split that pipeline into its assembly and
rendering halves, and :func:`parse_credit_report` / :class:`CreditReport`
(see ``pdf_engine.parser``) expose the normalized data model directly for
callers that want to inspect or reuse parsed data without rendering it.

Every name re-exported here remains importable from its original
submodule too (e.g. ``from pdf_engine.generator import generate_report``
continues to work) -- this module only adds the shorter package-level
path, it does not move anything.
"""

from .generator import build_story, generate_report, render_pdf
from .parser import CreditReport, parse_credit_report

__all__ = [
    "generate_report",
    "build_story",
    "render_pdf",
    "parse_credit_report",
    "CreditReport",
]
