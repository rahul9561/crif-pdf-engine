"""
pdf_engine.constants
=====================

Low-level, presentation-agnostic constants shared across the ``pdf_engine``
foundation layer: page geometry, the spacing scale, typographic sizes,
border weights, table padding, and small formatting/configuration values
(date format, page-number text).

This module defines *values only* -- no ReportLab style/color objects are
constructed here (that is ``theme.py``'s and ``styles.py``'s job), so it
has no dependency on any other ``pdf_engine`` module and can be safely
imported from anywhere in the package without risk of a circular import.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

__all__ = [
    "PACKAGE_DIR",
    "ASSETS_DIR",
    "FONT_DIRS",
    "LOGO_PATH",
    "PAGE_SIZE",
    "PAGE_WIDTH",
    "PAGE_HEIGHT",
    "MARGIN_TOP",
    "MARGIN_BOTTOM",
    "MARGIN_LEFT",
    "MARGIN_RIGHT",
    "CONTENT_WIDTH",
    "CONTENT_HEIGHT",
    "SPACE_XXS",
    "SPACE_XS",
    "SPACE_SM",
    "SPACE_MD",
    "SPACE_LG",
    "SPACE_XL",
    "SPACE_XXL",
    "FONT_SIZE_REPORT_TITLE",
    "FONT_SIZE_SECTION_TITLE",
    "FONT_SIZE_HEADING",
    "FONT_SIZE_NORMAL",
    "FONT_SIZE_SMALL",
    "FONT_SIZE_CAPTION",
    "FONT_SIZE_TABLE_HEADER",
    "FONT_SIZE_TABLE_CELL",
    "LEADING_RATIO",
    "BORDER_WIDTH_HAIRLINE",
    "BORDER_WIDTH_THIN",
    "BORDER_WIDTH_STANDARD",
    "BORDER_WIDTH_THICK",
    "TABLE_CELL_PADDING_TOP",
    "TABLE_CELL_PADDING_BOTTOM",
    "TABLE_CELL_PADDING_LEFT",
    "TABLE_CELL_PADDING_RIGHT",
    "SECTION_HEADER_PADDING_TOP",
    "SECTION_HEADER_PADDING_BOTTOM",
    "DATE_DISPLAY_FORMAT",
    "EMPTY_VALUE_PLACEHOLDER",
    "PAGE_NUMBER_TEXT",
]

# ---------------------------------------------------------------------------
# Asset locations
#
# Fonts and the logo are bundled inside the package itself, at
# pdf_engine/assets/ (declared as package data in pyproject.toml's
# [tool.setuptools.package-data], so `pip install` -- editable or
# regular -- carries them along). ASSETS_DIR is therefore always resolved
# relative to this package's own on-disk location (PACKAGE_DIR), never
# relative to a sibling directory or an external, deployment-specific
# path: that is what makes it resolve correctly both when running from
# a source checkout and when installed into site-packages, with no
# environment-specific configuration required.
# ---------------------------------------------------------------------------

#: This package's own directory on disk (the directory ``constants.py``
#: lives in), independent of whether it's a source checkout or an
#: installed site-packages copy.
PACKAGE_DIR: Path = Path(__file__).resolve().parent

#: The bundled assets directory, always ``<pdf_engine package dir>/assets``.
ASSETS_DIR: Path = PACKAGE_DIR / "assets"

#: Font family root directories, keyed by family name. Each family's
#: actual weight files may live directly under this directory or under a
#: nested ``static/`` subdirectory depending on how the font was
#: distributed (see ``theme.py``'s font discovery logic, which checks
#: both locations).
FONT_DIRS: dict[str, Path] = {
    "Montserrat": ASSETS_DIR / "Montserrat",
    "Poppins": ASSETS_DIR / "Poppins",
    "Roboto": ASSETS_DIR / "Roboto",
}

#: Path to the CRIF logo in the existing assets directory.
LOGO_PATH: Path = ASSETS_DIR / "criflogo.png"

# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------

PAGE_SIZE = A4
PAGE_WIDTH, PAGE_HEIGHT = PAGE_SIZE

MARGIN_TOP: float = 16 * mm
MARGIN_BOTTOM: float = 16 * mm
MARGIN_LEFT: float = 14 * mm
MARGIN_RIGHT: float = 14 * mm

#: Usable content width/height inside the page margins.
CONTENT_WIDTH: float = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
CONTENT_HEIGHT: float = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

# ---------------------------------------------------------------------------
# Spacing scale
#
# A small, consistent set of vertical spacing values (points) for use as
# Spacer() heights and ParagraphStyle spaceBefore/spaceAfter, so spacing
# throughout the report stays visually consistent rather than ad hoc.
# ---------------------------------------------------------------------------

SPACE_XXS: float = 2
SPACE_XS: float = 4
SPACE_SM: float = 6
SPACE_MD: float = 10
SPACE_LG: float = 14
SPACE_XL: float = 20
SPACE_XXL: float = 28

# ---------------------------------------------------------------------------
# Typography scale (points)
# ---------------------------------------------------------------------------

FONT_SIZE_REPORT_TITLE: float = 20
FONT_SIZE_SECTION_TITLE: float = 10.5
FONT_SIZE_HEADING: float = 9.5
FONT_SIZE_NORMAL: float = 8.5
FONT_SIZE_SMALL: float = 7.5
FONT_SIZE_CAPTION: float = 6.5
FONT_SIZE_TABLE_HEADER: float = 8
FONT_SIZE_TABLE_CELL: float = 8

#: Default leading (line height) as a multiple of font size.
LEADING_RATIO: float = 1.3

# ---------------------------------------------------------------------------
# Borders and table padding
# ---------------------------------------------------------------------------

BORDER_WIDTH_HAIRLINE: float = 0.25
BORDER_WIDTH_THIN: float = 0.5
BORDER_WIDTH_STANDARD: float = 0.75
BORDER_WIDTH_THICK: float = 1.25

TABLE_CELL_PADDING_TOP: float = 4
TABLE_CELL_PADDING_BOTTOM: float = 4
TABLE_CELL_PADDING_LEFT: float = 5
TABLE_CELL_PADDING_RIGHT: float = 5

#: Padding for a section title bar's single cell, kept slightly larger
#: than a normal data row for visual weight.
SECTION_HEADER_PADDING_TOP: float = 4
SECTION_HEADER_PADDING_BOTTOM: float = 4

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

#: Display format for dates rendered into the PDF, matching CRIF's own
#: DD-MM-YYYY convention.
DATE_DISPLAY_FORMAT: str = "%d-%m-%Y"

#: Placeholder shown for a value that is missing/blank.
EMPTY_VALUE_PLACEHOLDER: str = ""

#: Footer page-number text template. ``{page}`` is substituted by
#: ``helpers.create_page_number``.
PAGE_NUMBER_TEXT: str = "Page {page}"
