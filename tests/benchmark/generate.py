#!/usr/bin/env python3
"""Generate a benchmark set of print jobs with known expected verdicts.

The scanned documents are built the realistic way: typeset with reportlab, rendered to an
image at a chosen DPI with the same PDF engine the inspector uses, degraded the way a real
scanner degrades a page (rotation, noise, JPEG artefacts), then wrapped back into an
image-only PDF. Drawing text with PIL directly would produce crisp synthetic glyphs that
flatter OCR and tell you nothing about the real hit rate.

Each document carries an expectation, so scoring is mechanical rather than a judgement
call:

    hold   - a rule must fire; failing to hold is a MISS (the dangerous direction)
    allow  - nothing may fire; holding it is a FALSE POSITIVE (the expensive direction)

Usage:
    python generate.py --out ./bench
    # print every PDF in ./bench through the inspected queue, then:
    python score.py --api http://10.0.1.5:8088 --manifest ./bench/manifest.json
"""

from __future__ import annotations

import argparse
import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

# --- document bodies ---------------------------------------------------------
# Card numbers here are the industry test values: Luhn-valid, never issued.

CLEAN_REPORT = [
    "Quarterly facilities report",
    "",
    "Meeting room utilisation is up twelve percent on last quarter.",
    "The east wing refurbishment completed on schedule and under budget.",
    "No action is required before the next review in October.",
]

INVOICE_DECOYS = [
    "Purchase order summary",
    "",
    "Invoice 4111111111111112 was raised on 3 March for order 1234567890123456.",
    "Reference 9999999999999999 remains outstanding pending delivery.",
    "Serial 1234 5678 9012 3456 is recorded on the chassis plate.",
    "Contact alice@example.com or bob@example.com with questions.",
    "Please treat the contents as confidential where possible.",
]

CARD_DATA = [
    "Payment reconciliation - October",
    "",
    "Cardholder: J Smith",
    "Card 4111 1111 1111 1111  exp 04/28  cvv 123",
    "Amount settled: 1,204.55",
]

CLASSIFIED = [
    "STRICTLY CONFIDENTIAL",
    "",
    "Draft merger agreement - Project Halyard",
    "Not for distribution outside the deal team.",
]

CREDENTIALS = [
    "Deployment runbook",
    "",
    "production database password: hunter2-correct-horse",
    "Connect to the primary before running the migration.",
]

IBAN_DOC = [
    "Supplier payment instruction",
    "",
    "Bank transfer to IBAN GB82WEST12345698765432",
    "Reference: invoice 4471, due 30 days net",
]

MIXED_PAGES = [
    ["Board pack - page one", "", "Agenda and apologies for absence."],
    ["Page two", "", "STRICTLY CONFIDENTIAL", "Valuation summary follows."],
    ["Page three", "", "Any other business."],
]


@dataclass
class Case:
    name: str
    expect: str  # "hold" or "allow"
    why: str
    pages: list[list[str]]
    scan: bool = False
    dpi: int = 300
    rotation: float = 0.0
    noise: int = 0
    jpeg_quality: int = 0
    font_size: int = 12
    expect_rules: list[str] = field(default_factory=list)


CASES: list[Case] = [
    # --- text layer, the easy tier ------------------------------------------
    Case("01-clean-text", "allow", "ordinary document, nothing sensitive", [CLEAN_REPORT]),
    Case(
        "02-invoice-decoys", "allow",
        "the false-positive canary: Luhn-failing numbers, two emails, a soft 'confidential'",
        [INVOICE_DECOYS],
    ),
    Case("03-card-text", "hold", "payment card in the text layer", [CARD_DATA],
         expect_rules=["pan-spaced"]),
    Case("04-classified-text", "hold", "classification banner", [CLASSIFIED],
         expect_rules=["classification-banner"]),
    Case("05-credentials-text", "hold", "password assignment", [CREDENTIALS],
         expect_rules=["generic-secret-assignment"]),
    Case("06-iban-text", "hold", "IBAN with context", [IBAN_DOC], expect_rules=["iban"]),
    Case("07-multipage", "hold", "sensitive content on page 2 of 3", MIXED_PAGES,
         expect_rules=["classification-banner"]),

    # --- scanned, the OCR tier ----------------------------------------------
    Case("08-card-scan-300", "hold", "card data, clean 300dpi scan", [CARD_DATA],
         scan=True, dpi=300, expect_rules=["pan-spaced", "pan-primary"]),
    Case("09-card-scan-200", "hold", "card data, 200dpi", [CARD_DATA],
         scan=True, dpi=200, expect_rules=["pan-spaced", "pan-primary"]),
    Case("10-card-scan-150", "hold", "card data, 150dpi - the realistic floor", [CARD_DATA],
         scan=True, dpi=150, expect_rules=["pan-spaced", "pan-primary"]),
    Case("11-classified-scan-noisy", "hold", "banner at 300dpi with sensor noise", [CLASSIFIED],
         scan=True, dpi=300, noise=18, expect_rules=["classification-banner"]),
    Case("12-classified-scan-skewed", "hold", "banner at 200dpi, 1.5 degrees off square",
         [CLASSIFIED], scan=True, dpi=200, rotation=1.5,
         expect_rules=["classification-banner"]),
    Case("13-credentials-scan-jpeg", "hold", "password with JPEG compression artefacts",
         [CREDENTIALS], scan=True, dpi=300, jpeg_quality=45,
         expect_rules=["generic-secret-assignment"]),
    Case("14-card-scan-smallfont", "hold", "card data in 8pt, 300dpi", [CARD_DATA],
         scan=True, dpi=300, font_size=8, expect_rules=["pan-spaced", "pan-primary"]),
    Case("15-clean-scan", "allow", "scanned but harmless - OCR must not invent a match",
         [CLEAN_REPORT], scan=True, dpi=300),
]


def build_text_pdf(pages: list[list[str]], font_size: int = 12) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for page in pages:
        y = 760
        for line in page:
            pdf.setFont("Helvetica-Bold" if line.isupper() and line else "Helvetica", font_size)
            pdf.drawString(60, y, line)
            y -= font_size * 1.8
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def degrade(image, rotation: float, noise: int, jpeg_quality: int):
    """Make a crisp render look like something that came off a scanner."""
    from PIL import Image

    if rotation:
        image = image.rotate(rotation, resample=Image.BICUBIC, fillcolor="white", expand=False)

    if noise:
        pixels = image.load()
        width, height = image.size
        random.seed(1234)  # reproducible: the same benchmark twice must be comparable
        for _ in range((width * height) // 40):
            x, y = random.randrange(width), random.randrange(height)
            shift = random.randint(-noise, noise)
            r, g, b = pixels[x, y][:3]
            pixels[x, y] = (
                max(0, min(255, r + shift)),
                max(0, min(255, g + shift)),
                max(0, min(255, b + shift)),
            )

    if jpeg_quality:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=jpeg_quality)
        buffer.seek(0)
        image = Image.open(buffer).convert("RGB")

    return image


def build_scanned_pdf(case: Case) -> bytes:
    """Typeset, render at DPI, degrade, and wrap back up with no text layer."""
    import pypdfium2 as pdfium
    from PIL import Image

    source = build_text_pdf(case.pages, case.font_size)
    document = pdfium.PdfDocument(source, autoclose=False)
    images: list[Image.Image] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=case.dpi / 72.0)
                image = bitmap.to_pil().convert("RGB")
                images.append(degrade(image, case.rotation, case.noise, case.jpeg_quality))
            finally:
                page.close()
    finally:
        document.close()

    buffer = io.BytesIO()
    images[0].save(
        buffer, format="PDF", save_all=True,
        append_images=images[1:], resolution=float(case.dpi),
    )
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="./bench", help="output directory")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = []
    for case in CASES:
        data = build_scanned_pdf(case) if case.scan else build_text_pdf(case.pages, case.font_size)
        path = out / f"{case.name}.pdf"
        path.write_bytes(data)

        manifest.append({
            "name": case.name,
            "file": path.name,
            "expect": case.expect,
            "why": case.why,
            "scanned": case.scan,
            "dpi": case.dpi if case.scan else None,
            "rotation": case.rotation,
            "noise": case.noise,
            "jpeg_quality": case.jpeg_quality,
            "font_size": case.font_size,
            "expect_rules": case.expect_rules,
            "bytes": len(data),
        })
        kind = f"scan {case.dpi}dpi" if case.scan else "text"
        print(f"  {case.name:<28} {case.expect:<5} {kind:<12} {len(data):>8,} bytes")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n{len(CASES)} documents in {out}/  (manifest.json alongside)")
    print("Print them all through the inspected queue, then run score.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
