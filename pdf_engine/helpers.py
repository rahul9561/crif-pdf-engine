"""
pdf_engine.helpers
====================

Generic, reusable ReportLab flowable-building utilities for the CRIF
Highmark PDF engine: safe value-to-text formatting for use inside
Paragraphs/Tables, and small factory functions that turn already-prepared
generic data (strings, (label, value) pairs, header/row grids) into
ReportLab flowables using the styles defined in ``styles.py``.

Nothing in this module knows about CRIF-specific concepts (accounts,
payment history, credit scores, ...). Callers are expected to have
already extracted and formatted their data (typically via
``pdf_engine.parser``) before handing it to these functions; this module
only concerns itself with turning that data into PDF content safely and
consistently.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable, HRFlowable, Image, Paragraph, Table

from . import constants as c
from . import styles as s
from . import theme

logger = logging.getLogger(__name__)

__all__ = [
    "safe_text",
    "safe_decimal",
    "safe_date",
    "fit_font_size",
    "create_heading",
    "create_section_header",
    "create_key_value_table",
    "create_data_table",
    "horizontal_rule",
    "load_logo",
    "create_page_number",
]


# ---------------------------------------------------------------------------
# Safe formatting
# ---------------------------------------------------------------------------


def safe_text(value: object, *, default: str = "") -> str:
    """
    Safely coerces a value to display text suitable for a ReportLab
    ``Paragraph``.

    ``Paragraph`` interprets its input as a small XML-like markup
    language, so raw ``&``, ``<`` and ``>`` characters in report data
    (e.g. free-text account remarks) would otherwise raise a parse error
    or silently corrupt the rendered layout. This function escapes those
    characters via :func:`xml.sax.saxutils.escape`.

    Args:
        value: Any value, typically already a ``str`` from the parsed
            model, or ``None``.
        default: Text to return when ``value`` is ``None`` or an
            empty/whitespace-only string.

    Returns:
        XML-safe display text, never ``None``.
    """
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return _xml_escape(text)


def _format_indian_grouping(digits: str) -> str:
    """
    Applies Indian digit grouping (last 3 digits, then groups of 2) to a
    string of digits, e.g. ``"4000000"`` -> ``"40,00,000"``.

    Assumes ``digits`` contains only ASCII digits (no sign, no decimal
    point); :func:`safe_decimal` handles those separately.
    """
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def safe_decimal(
    value: Decimal | int | float | None,
    *,
    default: str = "",
    decimal_places: int = 0,
    grouping: bool = True,
) -> str:
    """
    Safely formats a numeric value for display using Indian digit
    grouping (e.g. ``1,11,600`` / ``40,00,000``), matching CRIF's own
    printed report convention.

    Args:
        value: A ``Decimal``/``int``/``float`` (typically already parsed
            by ``pdf_engine.parser``), or ``None``.
        default: Text to return when ``value`` is ``None`` or cannot be
            formatted as a number.
        decimal_places: Number of fractional digits to display (0 for
            whole-rupee amounts, e.g. 2 for an interest rate).
        grouping: When ``True`` (default), apply Indian digit grouping to
            the integer part. When ``False``, return the plain number.

    Returns:
        Display text, never ``None``.
    """
    if value is None:
        return default

    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
        quantum = Decimal(1).scaleb(-decimal_places)
        quantized = numeric.quantize(quantum)
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("Could not format %r as a decimal for display", value)
        return default

    negative = quantized < 0
    quantized = abs(quantized)
    sign = "-" if negative else ""

    if decimal_places > 0:
        text = f"{quantized:.{decimal_places}f}"
        integer_part, _, fractional_part = text.partition(".")
        if grouping:
            integer_part = _format_indian_grouping(integer_part)
        return f"{sign}{integer_part}.{fractional_part}"

    integer_part = str(int(quantized))
    if grouping:
        integer_part = _format_indian_grouping(integer_part)
    return f"{sign}{integer_part}"


def safe_date(
    value: date | None,
    *,
    fmt: str = c.DATE_DISPLAY_FORMAT,
    default: str = "",
) -> str:
    """
    Safely formats a ``date`` for display.

    Args:
        value: A ``date`` (typically already parsed by
            ``pdf_engine.parser``), or ``None``.
        fmt: ``strftime`` format string; defaults to CRIF's own
            DD-MM-YYYY convention.
        default: Text to return when ``value`` is ``None`` or cannot be
            formatted.

    Returns:
        Display text, never ``None``.
    """
    if value is None:
        return default
    try:
        return value.strftime(fmt)
    except (ValueError, AttributeError):
        logger.warning("Could not format %r as a date using %r", value, fmt)
        return default


def fit_font_size(
    text: str,
    max_width: float,
    *,
    font_name: str,
    candidate_sizes: tuple[float, ...],
) -> float:
    """
    Picks the largest font size from ``candidate_sizes`` at which
    ``text`` fits on a single line within ``max_width``.

    Used to guarantee short, unbreakable codes (e.g. a payment-history
    cell like ``"000/STD"``) never wrap, by measuring against real font
    metrics instead of guessing a size that is "probably small enough".

    Args:
        text: The (typically worst-case / longest realistic) sample text
            to measure.
        max_width: Available width in points.
        font_name: A font name already registered with ReportLab (see
            ``theme.FONTS``).
        candidate_sizes: Font sizes to try, largest to smallest.

    Returns:
        The largest candidate size that fits. If none fit, returns the
        smallest candidate rather than raising -- a pathologically narrow
        column degrades to "as small as we're willing to go" instead of
        crashing.

    Raises:
        ValueError: If ``candidate_sizes`` is empty -- there is no size
            to fall back to.
    """
    if not candidate_sizes:
        raise ValueError("candidate_sizes must not be empty")
    for size in sorted(candidate_sizes, reverse=True):
        if stringWidth(text, font_name, size) <= max_width:
            return size
    return min(candidate_sizes)


# ---------------------------------------------------------------------------
# Flowable factories
# ---------------------------------------------------------------------------


def create_section_header(text: str) -> Table:
    """
    Builds a full-width navy section title bar (e.g. "Inquiry Input
    Information", "Account Information", "Appendix") -- the recurring
    section-divider used throughout the report.

    Args:
        text: Section title text (XML-escaped via :func:`safe_text`).

    Returns:
        A single-row, single-column ``Table`` flowable styled with
        ``styles.TABLE_STYLE_SECTION_HEADER``.
    """
    bar = Table(
        [[Paragraph(safe_text(text), s.STYLES["SectionTitle"])]],
        colWidths=[c.CONTENT_WIDTH],
    )
    bar.setStyle(s.TABLE_STYLE_SECTION_HEADER)
    return bar


def create_heading(text: str, *, style_name: str = "Heading") -> Paragraph:
    """
    Builds a subsection heading Paragraph (e.g. "Payment History/Asset
    Classification:").

    Args:
        text: Heading text (XML-escaped via :func:`safe_text`).
        style_name: Name of a style registered in ``styles.STYLES``.
            Defaults to ``"Heading"``.

    Returns:
        A ``Paragraph`` flowable.
    """
    return Paragraph(safe_text(text), s.STYLES[style_name])


def create_key_value_table(
    pairs: list[tuple[str, str]],
    *,
    columns: int = 2,
    col_widths: list[float] | None = None,
) -> Table:
    """
    Builds a borderless label/value grid (e.g. "Inquiry Input
    Information", an account's detail grid) from a flat list of
    ``(label, value)`` pairs.

    Pairs are laid out left-to-right, top-to-bottom into ``columns``
    label/value groups per row (so ``columns=3`` produces 6 physical
    table columns: label, value, label, value, label, value), matching
    the multi-column key/value layout used throughout the reference
    report. The final row is padded with empty cells if ``pairs`` does
    not divide evenly by ``columns``.

    Args:
        pairs: Ordered ``(label, value)`` display text pairs. Values must
            already be display-formatted and XML-safe (see
            :func:`safe_text`, :func:`safe_decimal`, :func:`safe_date`) --
            this function uses them as-is and does **not** re-escape them,
            so passing already-escaped text here again would corrupt it
            (e.g. turning ``"L&amp;T"`` into ``"L&amp;amp;T"``).
        columns: Number of label/value groups per row.
        col_widths: Explicit widths for the resulting physical columns
            (length ``columns * 2``). When omitted, columns are sized
            evenly across ``constants.CONTENT_WIDTH``.

    Returns:
        A ``Table`` flowable styled with ``styles.TABLE_STYLE_KEY_VALUE``.
    """
    if columns < 1:
        raise ValueError("columns must be at least 1")

    label_style = s.STYLES["TableHeader"]
    value_style = s.STYLES["TableCell"]

    cells: list[Paragraph] = []
    for label, value in pairs:
        cells.append(Paragraph(str(label), label_style))
        cells.append(Paragraph(str(value), value_style))

    # Pad to a full multiple of (columns * 2) physical cells so the last
    # row has the same shape as the others.
    physical_columns = columns * 2
    remainder = len(cells) % physical_columns
    if remainder:
        cells.extend(Paragraph("", value_style) for _ in range(physical_columns - remainder))

    rows = [cells[i : i + physical_columns] for i in range(0, len(cells), physical_columns)]

    if col_widths is not None:
        widths = col_widths
    else:
        widths = [c.CONTENT_WIDTH / physical_columns] * physical_columns

    table = Table(rows, colWidths=widths)
    table.setStyle(s.TABLE_STYLE_KEY_VALUE)
    return table


#: Maps the public ``align`` argument of :func:`create_data_table` to a
#: ReportLab paragraph alignment constant.
_PARAGRAPH_ALIGNMENTS = {"LEFT": TA_LEFT, "CENTER": TA_CENTER, "RIGHT": TA_RIGHT}


def create_data_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    col_widths: list[float] | None = None,
    zebra: bool = True,
    align: str = "LEFT",
) -> Table:
    """
    Builds a generic header + rows data table (variation tables, summary
    tables, the payment history grid, inquiry tables, ...).

    Args:
        headers: Column header labels. Must not be empty.
        rows: Row data; each inner list should have the same length as
            ``headers``. Cell values must already be display-formatted
            and XML-safe (see :func:`safe_text`, :func:`safe_decimal`,
            :func:`safe_date`) -- this function uses them as-is and does
            **not** re-escape them, so passing already-escaped text here
            again would corrupt it (e.g. turning ``"L&amp;T"`` into
            ``"L&amp;amp;T"``).
        col_widths: Explicit column widths. When omitted, columns are
            sized evenly across ``constants.CONTENT_WIDTH``.
        zebra: When ``True`` (default), alternate data row backgrounds
            for readability on long tables.
        align: Horizontal alignment for every header and body cell --
            ``"LEFT"`` (default), ``"CENTER"`` or ``"RIGHT"``. Use
            ``"CENTER"`` for predominantly numeric tables (counts,
            amounts, dates), which reads more clearly than left-aligned
            digits.

    Returns:
        A ``Table`` flowable with a shaded, repeating header row.

    Raises:
        ValueError: If ``headers`` is empty.
    """
    if not headers:
        raise ValueError("headers must not be empty")

    alignment = _PARAGRAPH_ALIGNMENTS.get(align.upper(), TA_LEFT)
    header_style = s.STYLES["TableHeader"]
    cell_style = s.STYLES["TableCell"]
    if alignment != TA_LEFT:
        # ParagraphStyle.alignment, not a TableStyle ALIGN command, is
        # what actually controls text alignment for Paragraph-wrapped
        # cells -- .clone() gives an independent, unregistered style
        # object without touching the shared stylesheet.
        header_style = header_style.clone("TableHeaderAligned", alignment=alignment)
        cell_style = cell_style.clone("TableCellAligned", alignment=alignment)

    header_row = [Paragraph(str(header), header_style) for header in headers]
    body_rows = [[Paragraph(str(cell), cell_style) for cell in row] for row in rows]
    table_data = [header_row, *body_rows]

    if col_widths is not None:
        widths = col_widths
    else:
        widths = [c.CONTENT_WIDTH / len(headers)] * len(headers)

    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(s.build_zebra_table_style(len(table_data)) if zebra else s.TABLE_STYLE_DATA)
    return table


def horizontal_rule(
    *,
    width: float | str = "100%",
    thickness: float = c.BORDER_WIDTH_THIN,
    color=theme.BORDER_LIGHT,
    space_before: float = c.SPACE_XS,
    space_after: float = c.SPACE_XS,
) -> Flowable:
    """
    Builds a thin horizontal divider line.

    Args:
        width: Rule width; either points or a percentage string (e.g.
            ``"100%"`` of the available frame width).
        thickness: Line thickness in points.
        color: Line color.
        space_before: Vertical space above the rule.
        space_after: Vertical space below the rule.

    Returns:
        An ``HRFlowable``.
    """
    return HRFlowable(
        width=width,
        thickness=thickness,
        color=color,
        spaceBefore=space_before,
        spaceAfter=space_after,
        lineCap="butt",
    )


def load_logo(*, width: float | None = None, height: float | None = None) -> Image | None:
    """
    Loads the CRIF logo (from the existing ``assets/`` directory, see
    ``constants.LOGO_PATH``) as a ReportLab ``Image`` flowable.

    Args:
        width: Target width in points. When only one of ``width``/
            ``height`` is given, the other is derived to preserve the
            source image's aspect ratio.
        height: Target height in points.

    Returns:
        An ``Image`` flowable, or ``None`` (logged as a warning) if the
        logo file is missing or cannot be read -- callers should treat a
        missing logo as non-fatal and simply omit it.
    """
    logo_path: Path = c.LOGO_PATH
    if not logo_path.is_file():
        logger.warning("Logo file not found: %s", logo_path)
        return None

    try:
        image = Image(str(logo_path))
    except Exception:
        logger.exception("Failed to load logo image: %s", logo_path)
        return None

    original_width, original_height = image.imageWidth, image.imageHeight
    if width and not height:
        height = original_height * (width / original_width)
    elif height and not width:
        width = original_width * (height / original_height)
    elif not width and not height:
        width, height = original_width, original_height

    image.drawWidth = width
    image.drawHeight = height
    return image


def create_page_number(canvas, doc) -> None:
    """
    Draws the current page number in the footer margin.

    Intended for use as a page-drawing callback wired up in
    ``generator.render_pdf`` (e.g. ``onFirstPage`` / ``onLaterPages`` of a
    ``BaseDocTemplate``/``SimpleDocTemplate``). Follows the standard
    ReportLab canvas-callback pattern: draw directly on the low-level
    canvas, since Platypus flowables are not aware of page numbers at
    layout time, bracketed by ``saveState``/``restoreState`` so the
    callback cannot leak canvas state into whatever renders next.

    Note:
        This draws the page number alone (``"Page N"``), not a total
        page count -- producing "Page N of M" requires a two-pass canvas
        (e.g. a ``NumberedCanvas`` subclass), which is out of scope for
        this foundation layer.

    Args:
        canvas: The ReportLab canvas being drawn to (injected by
            Platypus).
        doc: The document template being built (injected by Platypus);
            ``doc.page`` gives the current 1-based page number.
    """
    canvas.saveState()
    canvas.setFont(theme.FONTS["Roboto-Regular"], c.FONT_SIZE_CAPTION)
    canvas.setFillColor(theme.GRAY_500)
    text = c.PAGE_NUMBER_TEXT.format(page=doc.page)
    canvas.drawRightString(c.PAGE_WIDTH - c.MARGIN_RIGHT, c.MARGIN_BOTTOM / 2, text)
    canvas.restoreState()
