#!/bin/bash
# Drive the lab: print a clean document, a card-data document, and a classified one, then
# show what CUPS did with each.
set -uo pipefail
QUEUE="${1:-office-laser}"
WORK=$(mktemp -d)

python3 - "$WORK" <<'PY'
import sys
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

work = sys.argv[1]

def make(name, lines):
    pdf = canvas.Canvas(f"{work}/{name}", pagesize=A4)
    y = 780
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 18
    pdf.save()

make("clean.pdf", [
    "Quarterly facilities report",
    "Meeting room utilisation is up 12% on last quarter.",
    "No action required before the next review.",
])
make("cardholder.pdf", [
    "Payment reconciliation - October",
    "Cardholder: J Smith",
    "Card 4111 1111 1111 1111  exp 04/28  cvv 123",
    "Amount settled: 1,204.55",
])
make("confidential.pdf", [
    "STRICTLY CONFIDENTIAL",
    "Draft merger agreement - Project Halyard",
    "Not for distribution outside the deal team.",
])
PY

for file in clean.pdf cardholder.pdf confidential.pdf; do
  echo "--- printing ${file} to ${QUEUE}"
  lp -d "${QUEUE}" -t "${file}" "${WORK}/${file}" || echo "    lp reported a failure (expected for blocked jobs)"
  sleep 3
done

echo
echo "=== queue state ==="
lpstat -o "${QUEUE}" -l || true
echo
echo "Held jobs stay listed above. Release them from the console at http://localhost:8080/queue"
