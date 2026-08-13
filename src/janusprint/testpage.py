"""A minimal one-page PDF, built by hand.

Deliberately no reportlab: that is a dev/test dependency, and a diagnostic that only works
on machines where the test extras happen to be installed is a diagnostic you cannot trust
at 3am. This writes the PDF bytes directly — about 40 lines and no imports.

The wording matters too. A test page travels the same path as a real job, so it must not
contain anything that trips a detection rule; a test that gets held teaches the operator
the printer is broken when it is not.
"""

from __future__ import annotations

from datetime import UTC, datetime


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_test_page(queue: str, actor: str, note: str = "") -> bytes:
    """One A4 page confirming the full path: client -> spooler -> inspector -> device."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        ("janus-print test page", 20, 760),
        (f"Queue: {queue}", 12, 720),
        (f"Requested by: {actor}", 12, 700),
        (f"Printed at: {stamp}", 12, 680),
        ("", 12, 660),
        ("If you are holding this page, the whole chain works:", 12, 640),
        ("the workstation reached the spooler, the inspector", 12, 622),
        ("returned a verdict, and the job reached the device.", 12, 604),
        ("", 12, 586),
        ("This page was inspected like any other document.", 10, 566),
    ]
    if note:
        lines.append((f"Note: {note[:80]}", 10, 546))

    parts = ["BT"]
    for text, size, y in lines:
        if not text:
            continue
        parts.append(f"/F1 {size} Tf")
        parts.append(f"1 0 0 1 60 {y} Tm")
        parts.append(f"({_escape(text)}) Tj")
    parts.append("ET")
    stream = "\n".join(parts).encode("latin-1", "replace")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)
