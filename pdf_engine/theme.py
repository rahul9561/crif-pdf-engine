"""
pdf_engine.theme
=================

CRIF Highmark visual identity for the PDF engine: dynamic registration of
the Montserrat, Poppins and Roboto fonts found in this project's existing
``assets/`` directory, and the reusable color palette used by every
paragraph style, table style, and section built on top of this foundation
layer.

Font registration is dynamic and defensive, not a fixed list of hardcoded
paths: for each ``(family, weight)`` pair this module searches a small set
of candidate locations under ``constants.FONT_DIRS`` (fonts are
distributed inconsistently -- some families' weight files sit directly in
the family folder, others under a nested ``static/`` subfolder) and
registers the first match found with ReportLab. A missing or unreadable
font file is logged and the affected logical font name falls back to a
built-in Helvetica variant rather than raising, so an incomplete asset
directory degrades the report's typography instead of preventing it from
rendering at all. No fonts are copied anywhere; this module only ever
reads from the existing ``assets/`` directory.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .constants import FONT_DIRS

logger = logging.getLogger(__name__)

__all__ = [
    "register_fonts",
    "FONTS",
    "PRIMARY_BLUE",
    "PRIMARY_BLUE_DARK",
    "SECONDARY_BLUE",
    "SECONDARY_BLUE_LIGHT",
    "GREEN",
    "GREEN_LIGHT",
    "ORANGE",
    "RED",
    "RED_LIGHT",
    "GRAY_900",
    "GRAY_700",
    "GRAY_500",
    "GRAY_300",
    "GRAY_100",
    "WHITE",
    "BORDER_LIGHT",
    "BORDER_MEDIUM",
    "BORDER_DARK",
    "ZEBRA_STRIPE",
    "COLOR_PALETTE",
]

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

#: Primary brand navy -- section title bars, the report title.
PRIMARY_BLUE: Color = colors.HexColor("#1B3A6B")
PRIMARY_BLUE_DARK: Color = colors.HexColor("#122747")

#: Secondary blue -- accents and table header backgrounds.
SECONDARY_BLUE: Color = colors.HexColor("#4472C4")
SECONDARY_BLUE_LIGHT: Color = colors.HexColor("#E8EDF9")

#: Status green -- e.g. the "Active" account ribbon.
GREEN: Color = colors.HexColor("#2E7D32")
GREEN_LIGHT: Color = colors.HexColor("#DCEFDD")

#: CRIF logo accent orange -- used sparingly for highlights.
ORANGE: Color = colors.HexColor("#F5821F")

#: Status red -- overdue/delinquent/negative indicators.
RED: Color = colors.HexColor("#C62828")
RED_LIGHT: Color = colors.HexColor("#FBE4E4")

#: Neutral grays, darkest to lightest.
GRAY_900: Color = colors.HexColor("#1F2126")
GRAY_700: Color = colors.HexColor("#4A4F57")
GRAY_500: Color = colors.HexColor("#8A8F98")
GRAY_300: Color = colors.HexColor("#D4D7DC")
GRAY_100: Color = colors.HexColor("#F4F5F7")
WHITE: Color = colors.white

#: Border colors for tables and rules.
BORDER_LIGHT: Color = GRAY_300
BORDER_MEDIUM: Color = colors.HexColor("#B7BCC4")
BORDER_DARK: Color = PRIMARY_BLUE

#: Alternate-row background for zebra-striped tables. A soft blue-gray
#: (on-brand with PRIMARY_BLUE/SECONDARY_BLUE) rather than a plain
#: neutral gray, so banding reads clearly without competing with status
#: colors or feeling like a rendering artifact.
ZEBRA_STRIPE: Color = colors.HexColor("#EEF1F6")

#: All palette colors keyed by logical name, for enumeration/testing.
COLOR_PALETTE: dict[str, Color] = {
    "primary_blue": PRIMARY_BLUE,
    "primary_blue_dark": PRIMARY_BLUE_DARK,
    "secondary_blue": SECONDARY_BLUE,
    "secondary_blue_light": SECONDARY_BLUE_LIGHT,
    "green": GREEN,
    "green_light": GREEN_LIGHT,
    "orange": ORANGE,
    "red": RED,
    "red_light": RED_LIGHT,
    "gray_900": GRAY_900,
    "gray_700": GRAY_700,
    "gray_500": GRAY_500,
    "gray_300": GRAY_300,
    "gray_100": GRAY_100,
    "white": WHITE,
    "border_light": BORDER_LIGHT,
    "border_medium": BORDER_MEDIUM,
    "border_dark": BORDER_DARK,
    "zebra_stripe": ZEBRA_STRIPE,
}

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

#: Weights registered per family, matching the file-name suffix used by
#: each family's distribution (e.g. ``Montserrat-SemiBold.ttf``).
_FONT_WEIGHTS: tuple[str, ...] = ("Regular", "Medium", "SemiBold", "Bold")

#: Subdirectories (relative to a family's root directory in
#: ``constants.FONT_DIRS``) searched for weight files, in priority order.
#: An empty string means "directly in the family root directory".
_FONT_SEARCH_SUBDIRS: tuple[str, ...] = ("", "static")

#: Fallback Helvetica variants used when a bundled font file cannot be
#: found/registered, keyed by weight name so the substitution keeps
#: roughly the right visual weight.
_FALLBACK_BY_WEIGHT: dict[str, str] = {
    "Regular": "Helvetica",
    "Medium": "Helvetica",
    "SemiBold": "Helvetica-Bold",
    "Bold": "Helvetica-Bold",
}

#: Maps a logical font name (e.g. ``"Montserrat-SemiBold"``) to the actual
#: font name registered with ReportLab -- identical to the key when
#: registration succeeded, or a Helvetica fallback name when it did not.
#: Populated by :func:`register_fonts`; ``styles.py`` reads from this dict
#: rather than assuming registration always succeeded.
FONTS: dict[str, str] = {}

_fonts_registered: bool = False

#: Guards ``_fonts_registered``/``FONTS`` against concurrent registration.
#: The very first population (triggered by the module-level
#: ``register_fonts()`` call below) is already serialized by Python's own
#: per-module import lock, so this specifically protects the case of
#: application code calling ``register_fonts(force=True)`` again later
#: (e.g. in tests, or a long-lived process picking up changed assets)
#: from multiple threads at once.
_registration_lock = threading.Lock()


def _find_font_file(family: str, weight: str) -> Path | None:
    """
    Searches the candidate locations for one ``(family, weight)`` font
    file and returns the first match.

    Checks, in order, ``<family_dir>/<family>-<weight>.ttf`` and
    ``<family_dir>/static/<family>-<weight>.ttf`` -- the two layouts
    actually observed across this project's bundled font distributions.

    Returns:
        The resolved file path, or ``None`` if no candidate exists.
    """
    family_dir = FONT_DIRS.get(family)
    if family_dir is None:
        return None

    file_name = f"{family}-{weight}.ttf"
    for subdir in _FONT_SEARCH_SUBDIRS:
        candidate = family_dir / subdir / file_name if subdir else family_dir / file_name
        if candidate.is_file():
            return candidate
    return None


def _register_single_font(family: str, weight: str) -> str:
    """
    Locates and registers one ``(family, weight)`` font with ReportLab.

    Returns the logical font name (``"<family>-<weight>"``) on success.
    On any failure -- no matching file found, or the file exists but is
    not a valid/readable font -- logs a warning and returns a Helvetica
    fallback name instead of raising, so one bad or missing asset cannot
    prevent the rest of the report from rendering.
    """
    logical_name = f"{family}-{weight}"
    font_path = _find_font_file(family, weight)

    if font_path is None:
        logger.warning(
            "Font file for %r not found under any of %s; falling back to Helvetica",
            logical_name,
            [str(FONT_DIRS.get(family, "?") / (sub or "."))
             for sub in _FONT_SEARCH_SUBDIRS],
        )
        return _FALLBACK_BY_WEIGHT.get(weight, "Helvetica")

    try:
        pdfmetrics.registerFont(TTFont(logical_name, str(font_path)))
    except Exception:
        logger.exception(
            "Failed to register font %r from %s; falling back to Helvetica",
            logical_name,
            font_path,
        )
        return _FALLBACK_BY_WEIGHT.get(weight, "Helvetica")

    return logical_name


def register_fonts(*, force: bool = False) -> dict[str, str]:
    """
    Discovers and registers the Montserrat, Poppins and Roboto font
    weights found in this project's existing ``assets/`` directory with
    ReportLab, and populates :data:`FONTS`.

    Safe to call multiple times: registration only runs once per process
    unless ``force=True`` is passed (useful for tests, or a long-lived
    process such as a dev server where assets on disk may have changed).

    Args:
        force: Re-run discovery/registration even if it has already
            completed in this process.

    Returns:
        The populated :data:`FONTS` mapping of logical font name to the
        actual name registered with ReportLab (a Helvetica fallback name
        where a font file could not be found or registered).
    """
    global _fonts_registered

    if _fonts_registered and not force:
        return FONTS

    with _registration_lock:
        # Re-check inside the lock: another thread may have already
        # finished registration while this one was waiting to acquire it.
        if _fonts_registered and not force:
            return FONTS

        for family in FONT_DIRS:
            for weight in _FONT_WEIGHTS:
                logical_name = f"{family}-{weight}"
                FONTS[logical_name] = _register_single_font(family, weight)

        _fonts_registered = True

    return FONTS


# Fonts are discovered and registered as soon as the theme is imported, so
# every other module in the foundation layer (styles.py) can rely on
# FONTS being populated without an explicit setup step.
register_fonts()
