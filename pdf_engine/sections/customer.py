"""
pdf_engine.sections.customer
==============================

Renders the "Inquiry Input Information" section: the applicant identity
grid at the top of ``docs/sample_report.pdf`` (Name, Gender, DOB, Phone
Numbers, ID(s), Email ID(s), Current Address), followed by a generic
"Application Information" grid sourced from ``CreditReport.application_info``
(CRIF's ``APPLICATION-SEGMENT`` -- loan-application metadata such as loan
type/amount/term, when the contributing lender reported any).

Every field in both grids is rendered only when it actually has a value:
CRIF's raw payload commonly leaves several of these blank for a given
report (e.g. Gender/PAN/UID/Address are all absent from the applicant
segment in some payloads, appearing instead only in the Demographic
Variations tables), and showing an empty "Gender:" or "ID(s):" row would
be noise rather than information.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Paragraph, Spacer

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import CreditReport, CustomerIdentity

__all__ = ["render"]


def _format_id_list(identity: CustomerIdentity) -> str:
    """
    Formats the applicant's identity documents using CRIF's own
    ``VALUE[TYPE]`` notation (e.g. ``"CAZPG3241C[PAN]"``), joining
    multiple documents with a comma when more than one is present.
    """
    documents: list[str] = []
    if identity.pan:
        documents.append(f"{identity.pan}[PAN]")
    if identity.uid:
        documents.append(f"{identity.uid}[UID]")
    return ", ".join(documents)


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "Inquiry Input Information" and (when present)
    "Application Information" sections to ``story``.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    identity = report.customer_identity

    pairs = [
        (label, value)
        for label, value in (
            ("Name:", h.safe_text(identity.name)),
            ("Gender:", h.safe_text(identity.gender)),
            ("DOB:", h.safe_date(identity.dob)),
            ("Phone Numbers:", h.safe_text(identity.phone)),
            ("ID(s):", h.safe_text(_format_id_list(identity))),
            ("Email ID(s):", h.safe_text(identity.email)),
        )
        if value
    ]

    if not pairs and not identity.address:
        return

    block: list = [h.create_section_header("Inquiry Input Information")]
    if pairs:
        block.append(Spacer(1, c.SPACE_XS))
        block.append(h.create_key_value_table(pairs, columns=2))
    if identity.address:
        block.append(Paragraph("Current Address:", s.STYLES["TableHeader"]))
        block.append(Paragraph(h.safe_text(identity.address), s.STYLES["TableCell"]))

    # Small and fixed-size -- safe to keep the whole section together so
    # the heading is never orphaned from its content.
    story.append(KeepTogether(block))
    story.append(Spacer(1, c.SPACE_MD))

    if report.application_info:
        app_grid = h.create_key_value_table(
            [(f"{label}:", h.safe_text(value)) for label, value in report.application_info],
            columns=2,
        )
        story.append(
            KeepTogether(
                [
                    h.create_section_header("Application Information"),
                    Spacer(1, c.SPACE_XS),
                    app_grid,
                ]
            )
        )
        story.append(Spacer(1, c.SPACE_MD))
