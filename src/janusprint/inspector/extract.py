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
import re
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


_WORDLIKE = re.compile(r"[A-Za-z]{3,}")
_VOWELS = set("aeiouAEIOU")


def looks_like_language(text: str) -> bool:
    """Does this look like words, or like glyph codes?

    A PDF produced from macOS/Word PostScript often carries a subset font with a custom
    encoding and no ToUnicode map. Extraction then yields one symbol per glyph — a text
    layer that is present, non-empty, and completely meaningless:

        !"#$%&'(#)*$+'+,+-.*!+--/#"$0*1%)'."23&#"".&'31#"-.

    That is more dangerous than no text at all: the page counts as having a text layer, so
    the OCR fallback never runs, and every rule silently fails to match while the job is
    reported as cleanly inspected. This check is what routes such pages to OCR instead.
    """
    stripped = text.strip()
    if not stripped:
        return False

    words = _WORDLIKE.findall(stripped)
    with_vowels = [w for w in words if _VOWELS & set(w)]
    if len(with_vowels) >= 3:
        return True

    # Fall back to alphabetic density, so a short but genuine line still passes while
    # symbol soup does not.
    letters = sum(1 for c in stripped if c.isalpha())
    return letters / len(stripped) >= 0.30


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
        """1-indexed pages whose text cannot be trusted — OCR candidates.

        Two ways a page qualifies: too little text to be worth reading, or text that is
        present but does not look like language (see looks_like_language).
        """
        candidates: list[int] = []
        for index, page in enumerate(self.pages, start=1):
            if len(page.strip()) < minimum or not looks_like_language(page):
                candidates.append(index)
        return candidates

    def unreadable_text_pages(self, minimum: int) -> list[int]:
        """Pages with plenty of text that still is not language — a mis-encoded font."""
        return [
            index
            for index, page in enumerate(self.pages, start=1)
            if len(page.strip()) >= minimum and not looks_like_language(page)
        ]

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


def extract(data: bytes, budget: float | None = None) -> ExtractionResult:
    """Best-effort text extraction. Never raises.

    `budget` is the seconds remaining in the caller's inline deadline. Conversion is
    capped by it, because overrunning is worse than failing: the CUPS backend gives up
    first, releases the job fail-open, and the verdict this call eventually produces is
    applied to a document that has already printed.
    """
    fmt = sniff_format(data)
    try:
        if fmt == "pdf":
            return _extract_pdf(data)
        if fmt in {"postscript", "pcl"}:
            converted = _to_pdf(data, fmt, budget)
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


def _to_pdf(data: bytes, fmt: str, budget: float | None = None) -> bytes | None:
    """Convert PostScript/PCL to PDF via ghostscript, inside the inline budget."""
    gs = shutil.which("gs")
    if gs is None:
        log.error("ghostscript not installed; cannot read %s jobs", fmt)
        return None

    # Clean up PostScript wrappers common in Windows spool files (PJL, Ctrl-D, EOF)
    if fmt == "postscript":
        ps_idx = data.find(b"%!PS")
        if ps_idx > 0:
            data = data[ps_idx:]
        data = data.rstrip(b"\x04\x00\r\n\x1a\x1b%-12345X ")

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
            "-dPDFSETTINGS=/default",
            f"-sDEVICE={device}",
            f"-sOutputFile={target}",
            str(source),
        ]
        try:
            res = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                check=False,
                timeout=max(1.0, budget if budget is not None else get_settings().inspect_deadline_seconds),
                capture_output=True,
            )
            if res.returncode != 0:
                err_msg = res.stderr.decode("utf-8", errors="replace").strip()
                log.warning("ghostscript non-zero exit %d: %s", res.returncode, err_msg)
        except subprocess.TimeoutExpired as exc:
            log.warning("ghostscript conversion timed out: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("ghostscript conversion failed: %s", exc)
            return None

        if target.exists() and target.stat().st_size > 200:
            return target.read_bytes()
        return None


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
