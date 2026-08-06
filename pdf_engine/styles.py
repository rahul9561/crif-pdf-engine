"""
pdf_engine.styles
===================

Reusable ReportLab paragraph and table styles for the CRIF Highmark PDF
engine, built on top of the color palette and registered fonts in
``theme.py`` and the spacing/typography scale in ``constants.py``.

Everything here is presentation only: no section-building or
CRIF-specific content lives in this module. Consumers should treat
:data:`STYLES` and the ``TABLE_STYLE_*`` constants (or
:func:`build_zebra_table_style`, for tables whose exact banding depends
on an actual row count) as the single source of truth for how text and
tables look, rather than constructing ad hoc ``ParagraphStyle`` /
``TableStyle`` objects elsewhere in the codebase.
"""

from __future__ import annotations

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.platypus import TableStyle

from . import constants as c
from . import theme

__all__ = [
    "STYLES",
    "TABLE_STYLE_SECTION_HEADER",
    "TABLE_STYLE_KEY_VALUE",
    "TABLE_STYLE_DATA",
    "TABLE_STYLE_DATA_ZEBRA",
    "TABLE_STYLE_SUMMARY",
    "build_zebra_table_style",
]


def _build_stylesheet() -> StyleSheet1:
    """
    Builds the paragraph stylesheet used throughout the report.

    Starts from ReportLab's own :func:`getSampleStyleSheet` (best
    practice: reuse its base styles rather than constructing every
    ``ParagraphStyle`` from scratch), customizes the existing ``Normal``
    style in place, then adds the report-specific styles required by this
    package: ``ReportTitle``, ``SectionTitle``, ``Heading``, ``Small``,
    ``Caption``, ``TableHeader``, ``TableCell``.
    """
    stylesheet = getSampleStyleSheet()

    normal = stylesheet["Normal"]
    normal.fontName = theme.FONTS["Roboto-Regular"]
    normal.fontSize = c.FONT_SIZE_NORMAL
    normal.leading = c.FONT_SIZE_NORMAL * c.LEADING_RATIO
    normal.textColor = theme.GRAY_900
    normal.spaceAfter = c.SPACE_XS

    stylesheet.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=normal,
            fontName=theme.FONTS["Montserrat-Bold"],
            fontSize=c.FONT_SIZE_REPORT_TITLE,
            leading=c.FONT_SIZE_REPORT_TITLE * c.LEADING_RATIO,
            textColor=theme.PRIMARY_BLUE,
            alignment=TA_LEFT,
            spaceAfter=c.SPACE_SM,
        )
    )

    # Meant to sit inside a single-cell Table styled with
    # TABLE_STYLE_SECTION_HEADER (which supplies the navy background) --
    # this ParagraphStyle only controls the white bold text itself.
    stylesheet.add(
        ParagraphStyle(
            name="SectionTitle",
            parent=normal,
            fontName=theme.FONTS["Montserrat-SemiBold"],
            fontSize=c.FONT_SIZE_SECTION_TITLE,
            leading=c.FONT_SIZE_SECTION_TITLE * c.LEADING_RATIO,
            textColor=theme.WHITE,
            alignment=TA_LEFT,
            spaceBefore=0,
            spaceAfter=0,
        )
    )

    stylesheet.add(
        ParagraphStyle(
            name="Heading",
            parent=normal,
            fontName=theme.FONTS["Montserrat-SemiBold"],
            fontSize=c.FONT_SIZE_HEADING,
            leading=c.FONT_SIZE_HEADING * c.LEADING_RATIO,
            textColor=theme.PRIMARY_BLUE,
            spaceBefore=c.SPACE_SM,
            spaceAfter=c.SPACE_XS,
            # Never leave a subsection heading (e.g. "Payment History/Asset
            # Classification:") alone at the bottom of a page with its
            # content pushed to the next one -- if there isn't room for at
            # least a little of what follows, the heading itself moves down.
            keepWithNext=True,
        )
    )

    stylesheet.add(
        ParagraphStyle(
            name="Small",
            parent=normal,
            fontName=theme.FONTS["Roboto-Regular"],
            fontSize=c.FONT_SIZE_SMALL,
            leading=c.FONT_SIZE_SMALL * c.LEADING_RATIO,
            textColor=theme.GRAY_700,
            spaceAfter=c.SPACE_XXS,
        )
    )

    stylesheet.add(
        ParagraphStyle(
            name="Caption",
            parent=normal,
            fontName=theme.FONTS["Roboto-Regular"],
            fontSize=c.FONT_SIZE_CAPTION,
            leading=c.FONT_SIZE_CAPTION * c.LEADING_RATIO,
            textColor=theme.GRAY_500,
            spaceAfter=c.SPACE_XXS,
        )
    )

    stylesheet.add(
        ParagraphStyle(
            name="TableHeader",
            parent=normal,
            fontName=theme.FONTS["Roboto-SemiBold"],
            fontSize=c.FONT_SIZE_TABLE_HEADER,
            leading=c.FONT_SIZE_TABLE_HEADER * c.LEADING_RATIO,
            textColor=theme.PRIMARY_BLUE,
            alignment=TA_LEFT,
            spaceAfter=0,
        )
    )

    stylesheet.add(
        ParagraphStyle(
            name="TableCell",
            parent=normal,
            fontName=theme.FONTS["Roboto-Regular"],
            fontSize=c.FONT_SIZE_TABLE_CELL,
            leading=c.FONT_SIZE_TABLE_CELL * c.LEADING_RATIO,
            textColor=theme.GRAY_900,
            alignment=TA_LEFT,
            spaceAfter=0,
        )
    )

    return stylesheet


#: The shared stylesheet. Access styles as ``STYLES["ReportTitle"]``,
#: ``STYLES["TableCell"]``, etc.
STYLES: StyleSheet1 = _build_stylesheet()


# ---------------------------------------------------------------------------
# Table styles
# ---------------------------------------------------------------------------

#: Single-cell "section title bar" style: solid navy background, no
#: borders. Pair with a one-row, one-column Table wrapping a Paragraph in
#: the ``SectionTitle`` style.
TABLE_STYLE_SECTION_HEADER = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, -1), theme.PRIMARY_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_LEFT),
        ("RIGHTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_RIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), c.SECTION_HEADER_PADDING_TOP),
        ("BOTTOMPADDING", (0, 0), (-1, -1), c.SECTION_HEADER_PADDING_BOTTOM),
    ]
)

#: Borderless label/value grid style, for key-value blocks such as
#: "Inquiry Input Information" or an account's detail grid. A faint
#: bottom rule separates rows; there are no column borders.
TABLE_STYLE_KEY_VALUE = TableStyle(
    [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_LEFT),
        ("RIGHTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_RIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_TOP),
        ("BOTTOMPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_BOTTOM),
        ("LINEBELOW", (0, 0), (-1, -1), c.BORDER_WIDTH_HAIRLINE, theme.BORDER_LIGHT),
    ]
)

#: General bordered data table: shaded header row, light grid lines.
#: Suitable for variation tables, inquiry tables, and any simple
#: header+rows table with no zebra banding.
TABLE_STYLE_DATA = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), theme.SECONDARY_BLUE_LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_LEFT),
        ("RIGHTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_RIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_TOP),
        ("BOTTOMPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_BOTTOM),
        ("GRID", (0, 0), (-1, -1), c.BORDER_WIDTH_HAIRLINE, theme.BORDER_LIGHT),
    ]
)

#: Variant of ``TABLE_STYLE_DATA`` with alternating row backgrounds, for
#: long/dense tables (e.g. a multi-year payment history grid) of unknown
#: size at style-definition time.
TABLE_STYLE_DATA_ZEBRA = TableStyle(
    TABLE_STYLE_DATA.getCommands()
    + [("ROWBACKGROUNDS", (0, 1), (-1, -1), [theme.WHITE, theme.ZEBRA_STRIPE])]
)

#: Numeric summary table style (Primary/Secondary/Group Account Summary):
#: shaded header row, centered body cells, medium-weight grid.
TABLE_STYLE_SUMMARY = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), theme.SECONDARY_BLUE_LIGHT),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_LEFT),
        ("RIGHTPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_RIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_TOP),
        ("BOTTOMPADDING", (0, 0), (-1, -1), c.TABLE_CELL_PADDING_BOTTOM),
        ("GRID", (0, 0), (-1, -1), c.BORDER_WIDTH_HAIRLINE, theme.BORDER_MEDIUM),
    ]
)


def build_zebra_table_style(row_count: int, *, header_rows: int = 1) -> TableStyle:
    """
    Builds a :class:`TableStyle` with alternating row backgrounds sized to
    an actual table's row count.

    ``TABLE_STYLE_DATA_ZEBRA`` uses an open-ended ``ROWBACKGROUNDS`` range
    that works for any table size, which is sufficient for most callers.
    This factory exists for the cases where banding must stop short of
    the last row (e.g. to leave a trailing totals row unshaded).

    Args:
        row_count: Total number of rows in the table, including header
            row(s).
        header_rows: Number of leading header rows to exclude from
            banding.

    Returns:
        A new ``TableStyle`` combining ``TABLE_STYLE_DATA`` with row
        banding confined to the data rows. If ``row_count`` leaves no
        data rows at all (a header-only table), the ``ROWBACKGROUNDS``
        command is omitted entirely rather than referencing a row index
        that doesn't exist -- ReportLab does not validate that at
        construction time, it raises ``IndexError`` deep inside layout
        when the table is actually drawn.
    """
    first_data_row = header_rows
    if row_count <= first_data_row:
        return TableStyle(TABLE_STYLE_DATA.getCommands())

    last_data_row = row_count - 1
    return TableStyle(
        TABLE_STYLE_DATA.getCommands()
        + [
            (
                "ROWBACKGROUNDS",
                (0, first_data_row),
                (-1, last_data_row),
                [theme.WHITE, theme.ZEBRA_STRIPE],
            )
        ]
    )
