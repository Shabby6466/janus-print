"""Document text extraction.

The inline path must be fast and must never raise — a crash here would take down printing
for the building. Everything returns an ExtractionResult; failures are reported in the
result, not as exceptions.

Format chain (PLAN.md §5):
    PDF                -> pypdfium2 text layer
    PostScript / PCL   -> ghostscript to PDF, then as above
    plain text         -> as-is
    anything else      -> unreadable, policy decides

Driverless (`-m everywhere`) queues deliver PDF, which is why the plan insists on them.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"
PS_MAGIC = b"%!PS"
PCL_MAGIC = b"\x1b%-12345X"  # PJL / Universal Exit Language
PCL_ALT = b"\x1bE"


@dataclass
class ExtractionResult:
    pages: list[str] = field(default_factory=list)
    page_count: int = 0
    format: str = "unknown"
    unreadable: bool = False
    encrypted: bool = False
    error: str = ""

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self.pages)

    def pages_without_text(self, minimum: int) -> list[int]:
        """1-indexed pages whose text layer is too thin to trust — OCR candidates."""
        return [i for i, page in enumerate(self.pages, start=1) if len(page.strip()) < minimum]

    @property
    def text(self) -> str:
        return "\n".join(self.pages)


def sniff_format(data: bytes) -> str:
    head = data[:1024]
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(PS_MAGIC):
        return "postscript"
    if head.startswith(PCL_MAGIC) or head.startswith(PCL_ALT):
        return "pcl"
    if b"%PDF" in head:
        return "pdf"
    # A NUL byte means binary. Without this check any undecodable-but-UTF-8-valid blob
    # (control characters are valid UTF-8) would be scanned as if it were plain text,
    # and would silently "pass" inspection.
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def extract(data: bytes) -> ExtractionResult:
    """Best-effort text extraction. Never raises."""
    fmt = sniff_format(data)
    try:
        if fmt == "pdf":
            return _extract_pdf(data)
        if fmt in {"postscript", "pcl"}:
            converted = _to_pdf(data, fmt)
            if converted is None:
                return ExtractionResult(format=fmt, unreadable=True, error="ghostscript failed")
            result = _extract_pdf(converted)
            result.format = fmt
            return result
        if fmt == "text":
            body = data.decode("utf-8", errors="replace")
            return ExtractionResult(pages=[body], page_count=1, format="text")
        return ExtractionResult(format=fmt, unreadable=True, error="unrecognised format")
    except Exception as exc:  # noqa: BLE001 - extraction must not break the print path
        log.exception("extraction failed")
        return ExtractionResult(format=fmt, unreadable=True, error=str(exc))


def _extract_pdf(data: bytes) -> ExtractionResult:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(data, autoclose=False)
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        # A password-protected PDF is readable by the printer but not by us. That is a
        # policy decision, not an error — see PrinterPolicy.on_unreadable.
        encrypted = "password" in message or "encrypt" in message
        return ExtractionResult(format="pdf", unreadable=True, encrypted=encrypted, error=str(exc))

    pages: list[str] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                textpage = page.get_textpage()
                pages.append(textpage.get_text_bounded() or "")
                textpage.close()
            except Exception:  # noqa: BLE001 - one bad page must not lose the others
                pages.append("")
            finally:
                page.close()
    finally:
        document.close()

    return ExtractionResult(pages=pages, page_count=len(pages), format="pdf")


def _to_pdf(data: bytes, fmt: str) -> bytes | None:
    """Convert PostScript/PCL to PDF via ghostscript."""
    gs = shutil.which("gs")
    if gs is None:
        log.error("ghostscript not installed; cannot read %s jobs", fmt)
        return None

    device = "pdfwrite"
    with tempfile.TemporaryDirectory(prefix="janus-extract-") as tmp:
        source = Path(tmp) / "in.spool"
        target = Path(tmp) / "out.pdf"
        source.write_bytes(data)
        command = [
            gs,
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-dQUIET",
            f"-sDEVICE={device}",
            f"-sOutputFile={target}",
            str(source),
        ]
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                check=True,
                timeout=get_settings().inspect_deadline_seconds * 4,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("ghostscript conversion failed: %s", exc)
            return None
        return target.read_bytes() if target.exists() else None


def render_pages_to_png(data: bytes, page_numbers: list[int], scale: float = 2.0) -> dict[int, bytes]:
    """Rasterise selected 1-indexed pages for OCR."""
    import pypdfium2 as pdfium

    rendered: dict[int, bytes] = {}
    document = pdfium.PdfDocument(data, autoclose=False)
    try:
        import io

        for number in page_numbers:
            index = number - 1
            if not 0 <= index < len(document):
                continue
            page = document[index]
            try:
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                rendered[number] = buffer.getvalue()
            finally:
                page.close()
    finally:
        document.close()
    return rendered
