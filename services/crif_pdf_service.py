"""
services.crif_pdf_service
============================

Django service wrapper around ``pdf_engine``: turns a raw CRIF Highmark
API JSON response into a saved PDF under ``MEDIA_ROOT/crif_reports/`` and
reports back where it landed.

This module only ever calls ``pdf_engine``'s public API
(``from pdf_engine import generate_report``); it has no knowledge of
``pdf_engine``'s internals and does not duplicate any of its behavior --
in particular, it does not create the ``crif_reports/`` directory itself,
since ``generate_report`` already does that.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from pdf_engine import generate_report

logger = logging.getLogger(__name__)

__all__ = ["generate_crif_pdf"]

#: Subdirectory of MEDIA_ROOT that CRIF report PDFs are saved under.
CRIF_REPORTS_SUBDIR = "crif_reports"


def _resolve_output_directory() -> Path:
    """
    Resolves ``MEDIA_ROOT/crif_reports`` from Django settings.

    Reads ``settings.MEDIA_ROOT`` lazily (inside this function, not at
    module import time) since Django settings may not be fully
    configured yet when this module is first imported.

    Raises:
        ImproperlyConfigured: If ``settings.MEDIA_ROOT`` is unset or
            empty, rather than silently writing relative to the current
            working directory.
    """
    media_root = settings.MEDIA_ROOT
    if not media_root:
        raise ImproperlyConfigured(
            "MEDIA_ROOT is not configured. Set MEDIA_ROOT in your Django "
            "settings before generating CRIF report PDFs."
        )
    return Path(media_root) / CRIF_REPORTS_SUBDIR


def generate_crif_pdf(raw_json: dict[str, Any]) -> dict[str, Any]:
    """
    Generates a CRIF Highmark credit report PDF from a raw bureau
    response and saves it under ``MEDIA_ROOT/crif_reports/``.

    Args:
        raw_json: The full decoded CRIF Highmark API response body, as
            accepted by ``pdf_engine.generate_report``.

    Returns:
        A dict with:
            - ``pdf_path``: the absolute filesystem path the PDF was
              saved to.
            - ``filename``: the generated ``<uuid4>.pdf`` filename.
            - ``size``: the saved file's size in bytes.

    Raises:
        ImproperlyConfigured: If ``settings.MEDIA_ROOT`` is not set.
        OSError: If the PDF cannot be written. Propagated from
            ``pdf_engine.generate_report`` rather than caught here --
            deciding how a write failure becomes an HTTP response is the
            caller's responsibility, not this service's.
    """
    output_directory = _resolve_output_directory()
    filename = f"{uuid.uuid4().hex}.pdf"
    output_path = output_directory / filename

    logger.info("Generating CRIF report PDF at %s", output_path)
    result_path = generate_report(raw_json, output_path)
    size = result_path.stat().st_size

    logger.info("CRIF report PDF generated: %s (%d bytes)", result_path, size)

    return {
        "pdf_path": str(result_path),
        "filename": filename,
        "size": size,
    }
