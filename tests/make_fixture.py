# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate tests/fixtures/sample.pdf: 3 pages, searchable text, an outline.

Run from the repository root:  python tests/make_fixture.py
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"

PAGES = [
    ("Chapter One", ["This is the first page of the sample document.",
                     "The word needle appears here once."]),
    ("Chapter Two", ["Second page. Nothing to find on this page.",
                     "Lorem ipsum dolor sit amet."]),
    ("Chapter Three", ["Third page: needle, and again needle.",
                       "End of the sample document."]),
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=A4)
    c.setTitle("pdf/45 sample document")
    width, height = A4
    for i, (title, lines) in enumerate(PAGES):
        key = f"page{i}"
        c.bookmarkPage(key)
        c.addOutlineEntry(title, key, level=0)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(72, height - 90, title)
        c.setFont("Helvetica", 12)
        y = height - 130
        for line in lines:
            c.drawString(72, y, line)
            y -= 18
        c.showPage()
    c.save()
    print("wrote", OUT)


if __name__ == "__main__":
    main()
