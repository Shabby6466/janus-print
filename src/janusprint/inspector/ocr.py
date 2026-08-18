"""OCR fallback for image-only pages.

Never runs inline. At 2-15 seconds a page it would blow the deadline on the first scanned
document, and a print queue that stalls is a Sev1 (PLAN.md §3). The queue policy decides
what happens to the job while this runs:

    deep_scan_required: true   -> job is already held; OCR either clears it or confirms it
    deep_scan_required: false  -> job already printed; OCR raises a retrospective incident
"""

from __future__ import annotations

import logging
import shutil

from ..config import get_settings
from .extract import render_pages_to_png

log = logging.getLogger(__name__)


def available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_pages(pdf_bytes: bytes, page_numbers: list[int]) -> dict[int, str]:
    """OCR the given 1-indexed pages. Returns page -> text, skipping failures."""
    settings = get_settings()
    if not available():
        log.error("tesseract not installed; OCR tier unavailable")
        return {}

    import pytesseract
    from PIL import Image

    capped = page_numbers[: settings.ocr_max_pages]
    if len(page_numbers) > len(capped):
        log.warning(
            "job has %d image-only pages, OCR capped at %d",
            len(page_numbers),
            settings.ocr_max_pages,
        )

    results: dict[int, str] = {}
    for number, png in render_pages_to_png(
        pdf_bytes, capped, scale=settings.ocr_render_scale
    ).items():
        import io

        try:
            with Image.open(io.BytesIO(png)) as image:
                results[number] = pytesseract.image_to_string(
                    image, timeout=int(settings.ocr_page_timeout_seconds)
                )
        except Exception as exc:  # noqa: BLE001 - a page that won't OCR is not fatal
            log.warning("OCR failed on page %d: %s", number, exc)
    return results
