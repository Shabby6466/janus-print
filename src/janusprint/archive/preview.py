"""Rendered page previews of archived documents.

An analyst cannot sensibly decide release-or-deny without seeing the page. Forcing a full
download for that is worse on every axis: it puts the original file on the reviewer's
laptop, outside the archive's control, to answer a question a picture would have settled.

So previews are a deliberately weaker, more auditable form of access:

  * **Rasterised, never the original.** The viewer receives PNG page images. No PDF leaves
    the server, so there is no file to forward, re-upload, or leave in a Downloads folder.
  * **Watermarked** with the viewer's username, the job id, and the timestamp. A screenshot
    that circulates is traceable to whoever took it.
  * **Logged individually.** Every page render is its own archive-access record.

Downloading the original still requires the dual-approval grant. Preview is for triage;
export is for evidence.
"""

from __future__ import annotations

import io
import logging

from ..config import get_settings
from ..inspector.extract import extract, render_pages_to_png

log = logging.getLogger(__name__)

MAX_PREVIEW_PAGES = 200


class PreviewUnavailable(RuntimeError):
    """The document cannot be rendered — encrypted, corrupt, or not a supported format."""


def page_count(data: bytes) -> int:
    result = extract(data)
    if result.unreadable:
        raise PreviewUnavailable(result.error or "document cannot be read")
    return result.page_count


def render_page(data: bytes, page: int, watermark: str, scale: float = 1.6) -> bytes:
    """Render one 1-indexed page to a watermarked PNG."""
    if page < 1 or page > MAX_PREVIEW_PAGES:
        raise PreviewUnavailable(f"page {page} is out of range")

    result = extract(data)
    if result.unreadable:
        raise PreviewUnavailable(result.error or "document cannot be read")

    # Only PDFs can be rasterised directly. PostScript and PCL were converted during
    # extraction, but that converted copy is not retained — re-convert for the preview.
    source = data
    if result.format != "pdf":
        from ..inspector.extract import _to_pdf

        converted = _to_pdf(data, result.format)
        if converted is None:
            raise PreviewUnavailable(f"cannot rasterise a {result.format} document")
        source = converted

    rendered = render_pages_to_png(source, [page], scale=scale)
    if page not in rendered:
        raise PreviewUnavailable(f"page {page} could not be rendered")

    return _watermark(rendered[page], watermark)


def _watermark(png: bytes, text: str) -> bytes:
    """Stamp the viewer's identity across the page.

    Deterrent, not a control — it survives a screenshot, which is the realistic way a
    preview leaks.
    """
    from PIL import Image, ImageDraw

    try:
        with Image.open(io.BytesIO(png)) as image:
            base = image.convert("RGBA")

        overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        width, height = base.size
        step = max(240, width // 3)
        for x in range(-width, width * 2, step):
            for y in range(0, height, step):
                draw.text((x, y), text, fill=(200, 60, 60, 60))

        # A solid footer, so identity survives cropping of the tiled overlay.
        footer_height = 22
        draw.rectangle(
            [(0, height - footer_height), (width, height)], fill=(20, 20, 20, 190)
        )
        draw.text((8, height - footer_height + 6), text, fill=(235, 235, 235, 255))

        return _to_png(Image.alpha_composite(base, overlay).convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - never fail a preview over decoration
        log.warning("watermarking failed (%s); serving the unwatermarked render", exc)
        return png


def _to_png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def preview_allowed_without_grant(job_state: str) -> bool:
    """Whether triage may proceed without a dual-approval grant.

    A held job is one an analyst has been asked to decide on; refusing them the page while
    demanding a decision just produces rubber-stamped releases. Everything else — released,
    denied, historical — needs the same approval as a download.
    """
    if not get_settings().allow_preview_for_held_jobs:
        return False
    return job_state in {"held", "inspecting"}
