"""
pdf_engine.sections.customer
==============================

Renders the "Inquiry Input Information" section: the applicant identity
grid at the top of ``docs/sample_report.pdf`` (Name, DOB, Phone Numbers,
ID(s), Email ID(s), Current Address).

The reference PDF's grid also includes Father, Mother, Spouse, Gender and
an "Other Address" field, and labels the date-of-birth row "DOB/Age".
``CustomerIdentity`` (from ``pdf_engine.parser``) carries none of those --
CRIF's raw payload for this report does not report them, and computing an
age from a date of birth is a derived business rule the parser does not
perform -- so this module renders only the fields the parsed model
actually has, labeling the date-of-birth row plainly as "DOB".
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
    Appends the "Inquiry Input Information" section to ``story``.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    identity = report.customer_identity

    grid = h.create_key_value_table(
        [
            ("Name:", h.safe_text(identity.name)),
            ("DOB:", h.safe_date(identity.dob)),
            ("Phone Numbers:", h.safe_text(identity.phone)),
            ("ID(s):", h.safe_text(_format_id_list(identity))),
            ("Email ID(s):", h.safe_text(identity.email)),
        ],
        columns=2,
    )

    # Small and fixed-size -- safe to keep the whole section together so
    # the heading is never orphaned from its content.
    story.append(
        KeepTogether(
            [
                h.create_section_header("Inquiry Input Information"),
                Spacer(1, c.SPACE_XS),
                grid,
                Paragraph("Current Address:", s.STYLES["TableHeader"]),
                Paragraph(h.safe_text(identity.address), s.STYLES["TableCell"]),
            ]
        )
    )
    story.append(Spacer(1, c.SPACE_MD))
