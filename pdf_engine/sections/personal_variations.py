"""
pdf_engine.sections.personal_variations
==========================================

Renders the "Personal Info Variations" and "Address Variations" sections
of ``docs/sample_report.pdf``: Name, Email-ID, DOB, Phone and ID
variation tables grouped under one "Personal Info Variations" banner, and
Address Variations as its own full-width section -- matching the
reference report's visual hierarchy, where Address Variations gets its
own navy title bar while the other five sit as sub-headings beneath a
single shared one.

Every one of the six variation categories is rendered independently and
skipped automatically when empty (``CreditReport.personal_info_variations``
routinely has several empty categories -- CRIF's raw payload for this
report never populates UID/other-ID/driving-licence/voter-ID/
passport/ration-card variations, which is exactly why the parser merges
those into ``id_variations`` rather than exposing seven near-always-empty
lists). The outer "Personal Info Variations" banner itself is only
rendered when at least one of its five sub-categories has data.

The reference PDF shows separate "First Reported" and "Last Reported"
columns per row. CRIF's raw payload only reports a single
``reported_date`` per variation entry (see ``PersonalVariationEntry``),
so both columns are rendered from that one value rather than a fabricated
second date. Likewise, "Type" and "Source Indicator" are populated by
CRIF only for identity documents (PAN, UID, ...); this module fills
"Type" from ``id_type`` for the ID Variations table and leaves it (and
"Source Indicator", which the parser does not capture for any category)
blank elsewhere.
"""

from __future__ import annotations

from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table

from .. import constants as c
from .. import helpers as h
from .. import styles as s
from ..parser import CreditReport, PersonalVariationEntry

__all__ = ["render"]

_TIP_TEXT = (
    "Tip: These are applicant's personal information variations as "
    "contributed by various financial institutions."
)

_VARIATION_TABLE_HEADERS_SUFFIX = ["First Reported", "Last Reported", "Type", "Source Indicator"]

#: (sub-heading text, row-label column header, entries accessor, whether
#: the "Type" column is populated for this category).
_SUB_CATEGORIES: tuple[tuple[str, str, str, bool], ...] = (
    ("Name Variations", "Name", "name_variations", False),
    ("Email-ID Variations", "Email", "email_variations", False),
    ("DOB Variations", "DOB", "date_of_birth_variations", False),
    ("Phone Variations", "Phone", "phone_variations", False),
    ("ID Variations", "ID", "id_variations", True),
)


def _build_variation_table(
    label: str, entries: list[PersonalVariationEntry], *, include_type: bool
) -> Table:
    """Builds one variation table (Name/Email/DOB/Phone/ID/Address)."""
    headers = [label, *_VARIATION_TABLE_HEADERS_SUFFIX]
    rows: list[list[str]] = []
    for entry in entries:
        reported = h.safe_date(entry.reported_date)
        rows.append(
            [
                h.safe_text(entry.value),
                reported,
                reported,
                h.safe_text(entry.id_type) if include_type else "",
                "",
            ]
        )
    return h.create_data_table(headers, rows)


def _render_sub_category(
    story: list, title: str, label: str, entries: list[PersonalVariationEntry], *, include_type: bool
) -> None:
    """Appends one variation sub-heading + table to ``story``, skipped if ``entries`` is empty."""
    if not entries:
        return
    # No trailing spacer: the "Heading" style's own spaceBefore already
    # separates this from whatever precedes it (the previous
    # sub-category's table, or the outer tip paragraph), and its
    # keepWithNext=True keeps this heading attached to the table that
    # follows without an explicit KeepTogether.
    story.append(h.create_heading(title))
    story.append(_build_variation_table(label, entries, include_type=include_type))


def render(story: list, report: CreditReport) -> None:
    """
    Appends the "Personal Info Variations" and "Address Variations"
    sections to ``story``.

    Each of the six variation categories (Name, Email, DOB, Phone, ID,
    Address) is rendered only when it has at least one entry; the shared
    "Personal Info Variations" banner is itself only rendered when at
    least one of its five sub-categories has data.

    Args:
        story: The in-progress list of ReportLab flowables being built up
            for the final document; flowables are appended in place.
        report: The normalized credit report to render.
    """
    variations = report.personal_info_variations

    sub_category_entries = [
        (title, label, getattr(variations, attr), include_type)
        for title, label, attr, include_type in _SUB_CATEGORIES
    ]

    if any(entries for _, _, entries, _ in sub_category_entries):
        # Only the banner + tip are kept together (both small, fixed
        # size); the sub-category headings/tables that follow rely on
        # the "Heading" style's own keepWithNext instead, since the
        # total content here is not bounded the way a single table is.
        story.append(
            KeepTogether(
                [
                    h.create_section_header("Personal Info Variations"),
                    Spacer(1, c.SPACE_XXS),
                    Paragraph(h.safe_text(_TIP_TEXT), s.STYLES["Small"]),
                ]
            )
        )
        story.append(Spacer(1, c.SPACE_XS))
        for title, label, entries, include_type in sub_category_entries:
            _render_sub_category(story, title, label, entries, include_type=include_type)
        story.append(Spacer(1, c.SPACE_XS))

    if variations.address_variations:
        address_table = _build_variation_table(
            "Address", variations.address_variations, include_type=False
        )
        story.append(
            KeepTogether([h.create_section_header("Address Variations"), address_table])
        )
        story.append(Spacer(1, c.SPACE_MD))
